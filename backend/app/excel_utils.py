"""Excel içe/dışa aktarma yardımcıları (araçlar ve bakım ağacı)."""

from __future__ import annotations

import io
import re
from datetime import datetime

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import models

_TR_MAP = str.maketrans("çÇğĞıİöÖşŞüÜ", "cCgGiIoOsSuU")

# ---------------------------------------------------------------- Excel stilleri
# Uygulamanın kendi renk paletiyle (Badge tone'ları) tutarlı dolgu/yazı renkleri.
_HEADER_FILL = PatternFill("solid", fgColor="1B4DA0")
_HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
_THIN_BORDER = Border(bottom=Side(style="thin", color="D8DEE9"))

_STATUS_FILLS = {
    "pending": PatternFill("solid", fgColor="FCE8B2"),       # amber — Bekliyor
    "in_progress": PatternFill("solid", fgColor="D6E4FF"),   # mavi — Devam Ediyor
    "completed": PatternFill("solid", fgColor="D9F2E3"),     # yeşil — Tamamlandı
}
_STATUS_FONTS = {
    "pending": Font(color="92600D", bold=True),
    "in_progress": Font(color="1B4DA0", bold=True),
    "completed": Font(color="1E7A46", bold=True),
}
_ACTIVE_FILL = PatternFill("solid", fgColor="D9F2E3")
_ACTIVE_FONT = Font(color="1E7A46", bold=True)
_INACTIVE_FILL = PatternFill("solid", fgColor="E5E7EB")
_INACTIVE_FONT = Font(color="6B7280", bold=True)


def _style_header_row(ws, ncols: int) -> None:
    ws.freeze_panes = "A2"
    for c in range(1, ncols + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 20


def _autofilter(ws, ncols: int, nrows: int) -> None:
    ws.auto_filter.ref = f"A1:{get_column_letter(ncols)}{max(nrows, 1) + 1}"

# Kullanıcı isteğiyle sistemden kalıcı olarak kaldırılan bakım periyotları.
# "400.000 bakım diye bir şey yok onu sil" — kaynak Excel dosyasında bu
# sütun bloğu bulunsa bile (seed.py veya self-servis yükleme fark etmez)
# içe aktarma sırasında atlanır.
_EXCLUDED_INTERVALS: set[int] = {400_000}


# --------------------------------------------------------------------------
# Gerçek/temel bakım periyotları (araç modeline göre).
#
# Bu kümede OLMAYAN bir km periyodu aslında AYRI bir bakım DEĞİLDİR; bir temel
# periyodun katıdır (ör. 1.200.000 = 200.000'in 6 katı, 400.000 = 200.000'in
# 2 katı). Kaynak listede böyle "kat" periyotlar için ayrı satırlar bulunsa
# bile, bunlar için ayrı bir MaintenancePlan OLUŞTURULMAZ: içerikleri, onları
# tam bölen EN BÜYÜK temel periyodun planına (ör. "200.000 km Bakımı")
# katlanır. Böylece "Bakım Planları" sayfasında 400.000 / 1.200.000 gibi
# sahte planlar görünmez; o kilometreye gelindiğinde iş emri yine açılır ama
# temel bakımın (ör. 200.000 km Bakımı) adıyla görünür.
#
# Kullanıcı beyanı (KB): SİRİO araçlarında temel periyotlar 10/30/60/100/200/
# 500 bin; diğer tüm modellerde (Bozankaya vb.) 25/50/100/200/300/500 bin.
_BASE_INTERVALS_SIRIO: set[int] = {10_000, 30_000, 60_000, 100_000, 200_000, 500_000}
_BASE_INTERVALS_DEFAULT: set[int] = {25_000, 50_000, 100_000, 200_000, 300_000, 500_000}


def _base_intervals_for(vehicle_model: str | None) -> set[int]:
    """Araç modeline göre geçerli (temel) km periyot kümesini döndürür.
    Model adı 'sirio' içeriyorsa (büyük/küçük harf ve Türkçe karakter
    duyarsız) SİRİO kümesi, aksi halde varsayılan (Bozankaya vb.) küme."""
    if vehicle_model and "sirio" in _norm(vehicle_model):
        return _BASE_INTERVALS_SIRIO
    return _BASE_INTERVALS_DEFAULT


def canonical_base_interval(vehicle_model: str | None, km: int) -> int | None:
    """Bir km periyodunu 'temel' (canonical) periyoduna eşler.

    - km zaten bir temel periyot ise kendisini döndürür.
    - km temel değilse (bir katıysa) onu TAM BÖLEN en büyük temel periyodu
      döndürür (ör. SİRİO için 1.200.000 -> 200.000, 400.000 -> 200.000).
    - Hiçbir temel periyoda tam bölünmüyorsa None döndürür — bu durumda
      periyot 'tanınmayan' sayılır ve (veri kaybı olmaması için) olduğu gibi
      korunur, kendi planı oluşturulur.
    """
    if km <= 0:
        return None
    bases = _base_intervals_for(vehicle_model)
    if km in bases:
        return km
    divisors = [b for b in bases if b > 0 and km % b == 0]
    return max(divisors) if divisors else None


def purge_nonbase_plans(db, vehicle_model: str, *, commit: bool = False) -> list[dict]:
    """Verilen araç modeli için, interval_km'i temel periyot kümesinde OLMAYAN
    ama bir temel periyodu TAM BÖLEN (yani onun katı olan) km planlarını
    temizler — ör. eskiden oluşmuş "1.200.000 km Bakımı" / "400.000 km Bakımı".

    Kural (veri güvenliği önce):
      * Tamamlanmış (geçmiş) iş emirleri, o periyodu bölen EN BÜYÜK temel
        planın (ör. 200.000 km Bakımı) altına TAŞINIR — böylece bakım geçmişi
        kaybolmaz, sadece doğru (temel) bakım adıyla görünür. (uq_once çakışması
        olursa — temel planda aynı araç+hedef km zaten varsa — kat plandaki
        kopya silinir.)
      * Henüz tamamlanmamış (Bekliyor/Devam Ediyor) iş emirleri silinir; km
        güncellemesinde temel plandan yeniden açılırlar.
      * Plana ait ağaç (TreeNode) ve 3D parça eşleşmeleri (PartHotspot) silinir.
      * Taşınacak temel plan yoksa ve planda tamamlanmış geçmiş varsa, plan
        SİLİNMEZ; yalnızca pasife alınır (is_active=False) — geçmiş korunur.
        Aksi halde plan tamamen silinir.

    Yalnızca bir temel periyoda tam bölünen (canon != None) planlara dokunulur;
    hiçbir temele bölünmeyen (tanınmayan) periyotlar olduğu gibi bırakılır.

    `commit=False` (varsayılan) yalnızca db.flush yapar — çağıran taraf commit
    eder; `commit=True` ise sonda kendisi commit eder (standalone script için).

    Dönüş: yapılan işlemlerin özet listesi."""
    actions: list[dict] = []
    bases = _base_intervals_for(vehicle_model)

    plans = (
        db.query(models.MaintenancePlan)
        .filter(models.MaintenancePlan.vehicle_model == vehicle_model)
        .all()
    )
    for p in plans:
        ptype = getattr(p, "plan_type", None) or "km"
        if ptype != "km" or not p.interval_km or p.interval_km <= 0:
            continue  # süre/belirsiz planlara dokunma
        if p.interval_km in bases:
            continue  # zaten temel periyot
        canon = canonical_base_interval(vehicle_model, p.interval_km)
        if canon is None:
            continue  # bir temele bölünmüyor — tanınmayan, olduğu gibi bırak

        base_plan = (
            db.query(models.MaintenancePlan)
            .filter_by(
                interval_km=canon,
                interval_months=getattr(p, "interval_months", None),
                vehicle_model=vehicle_model,
            )
            .filter(
                (models.MaintenancePlan.plan_type == ptype)
                | (models.MaintenancePlan.plan_type.is_(None))
            )
            .first()
        )

        moved = 0
        removed_open = 0
        maint_list = db.query(models.Maintenance).filter_by(plan_id=p.id).all()
        for m in maint_list:
            if m.status == "completed" and base_plan is not None:
                clash = (
                    db.query(models.Maintenance)
                    .filter_by(vehicle_id=m.vehicle_id, plan_id=base_plan.id, target_km=m.target_km)
                    .first()
                )
                if clash is not None:
                    db.delete(m)  # temel planda aynı kayıt zaten var
                else:
                    m.plan_id = base_plan.id
                    moved += 1
            elif m.status == "completed" and base_plan is None:
                # taşınacak temel plan yok — geçmişi koru, planı pasife alacağız
                pass
            else:
                db.delete(m)  # açık (pending/in_progress) iş emri
                removed_open += 1
        db.flush()

        # Ağaç ve 3D eşleşmeleri temizle
        roots = db.query(models.TreeNode).filter_by(parent_id=None, plan_id=p.id).all()
        for r in roots:
            db.delete(r)
        db.query(models.PartHotspot).filter_by(plan_id=p.id).delete()
        db.flush()

        remaining = db.query(models.Maintenance).filter_by(plan_id=p.id).count()
        if remaining > 0:
            p.is_active = False
            action = "deactivated"
        else:
            db.delete(p)
            action = "deleted"
        db.flush()

        actions.append({
            "plan_id": p.id,
            "name": p.name,
            "interval_km": p.interval_km,
            "canonical_km": canon,
            "vehicle_model": vehicle_model,
            "action": action,
            "moved_completed": moved,
            "removed_open": removed_open,
        })

    if commit:
        db.commit()
    return actions


def merge_vehicle_model(db, from_model: str, to_model: str, *, commit: bool = False) -> dict:
    """Bir araç modelini bir diğerine "birleştirir" — yanlış yazılmış/eski bir
    model adının (ör. yükleme sırasında oluşmuş "BOZANKAYA") gerçek filo
    modeliyle (ör. "Bozankaya Tip-1") tek elde toplanmasını sağlar. İki adımda
    çalışır:

    1) Araçlar: Vehicle.model == from_model olan TÜM araçların modeli
       to_model olarak güncellenir.
    2) Planlar: vehicle_model == from_model olan her MaintenancePlan için:
       - to_model altında AYNI periyoda (plan_type, interval_km,
         interval_months) sahip bir plan YOKSA: plan olduğu gibi (kendi
         ağacıyla birlikte) to_model'e "yeniden adlandırılır" (basit rename,
         veri kaybı yok).
       - VARSA (çakışma): tamamlanmış iş emirleri hedef plana TAŞINIR (aynı
         araç+hedef zaten varsa kopya silinir), açık (Bekliyor/Devam Ediyor)
         iş emirleri silinir (hedef planın güncel ağacından yeniden açılırlar),
         kaynak planın ağacı/3D eşleşmeleri silinir, plan kendisi de silinir
         (tamamlanmış geçmişi hedefe taşındıktan sonra artık gereksizdir).

    `from_model` ve `to_model` boş olamaz ve birbirinden farklı olmalıdır
    (çağıran taraf — bkz. main.py — bunu doğrular).

    Dönüş: {"vehicles_moved": N, "plans_renamed": [...], "plans_merged": [...]}."""
    vehicles_moved = (
        db.query(models.Vehicle)
        .filter(models.Vehicle.model == from_model)
        .update({"model": to_model}, synchronize_session=False)
    )

    plans_renamed: list[dict] = []
    plans_merged: list[dict] = []

    source_plans = (
        db.query(models.MaintenancePlan)
        .filter(models.MaintenancePlan.vehicle_model == from_model)
        .all()
    )
    for p in source_plans:
        target = (
            db.query(models.MaintenancePlan)
            .filter_by(
                vehicle_model=to_model,
                plan_type=p.plan_type,
                interval_km=p.interval_km,
                interval_months=p.interval_months,
            )
            .first()
        )
        if target is None:
            # Çakışma yok — planı olduğu gibi hedef modele taşı (basit rename).
            p.vehicle_model = to_model
            db.flush()
            plans_renamed.append({"plan_id": p.id, "name": p.name, "interval_km": p.interval_km})
            continue

        # Çakışma: tamamlanmış geçmişi hedef plana taşı, açık iş emirlerini sil.
        moved = 0
        removed_open = 0
        for m in db.query(models.Maintenance).filter_by(plan_id=p.id).all():
            if m.status == "completed":
                clash = (
                    db.query(models.Maintenance)
                    .filter_by(vehicle_id=m.vehicle_id, plan_id=target.id, target_km=m.target_km)
                    .first()
                )
                if clash is not None:
                    db.delete(m)
                else:
                    m.plan_id = target.id
                    moved += 1
            else:
                db.delete(m)
                removed_open += 1
        db.flush()

        roots = db.query(models.TreeNode).filter_by(parent_id=None, plan_id=p.id).all()
        for r in roots:
            db.delete(r)
        db.query(models.PartHotspot).filter_by(plan_id=p.id).delete()
        db.flush()

        remaining = db.query(models.Maintenance).filter_by(plan_id=p.id).count()
        if remaining > 0:
            p.is_active = False
            action = "deactivated"
        else:
            db.delete(p)
            action = "deleted"
        db.flush()

        plans_merged.append({
            "plan_id": p.id,
            "name": p.name,
            "interval_km": p.interval_km,
            "merged_into_plan_id": target.id,
            "action": action,
            "moved_completed": moved,
            "removed_open": removed_open,
        })

    if commit:
        db.commit()
    return {
        "vehicles_moved": vehicles_moved,
        "plans_renamed": plans_renamed,
        "plans_merged": plans_merged,
    }


def _norm(s: str) -> str:
    return s.translate(_TR_MAP).strip().lower().replace(" ", "").replace("_", "")


# Başlık eşleme: sistemdeki alan -> kullanıcının kullanabileceği olası başlıklar
_VEHICLE_HEADER_ALIASES: dict[str, list[str]] = {
    "code": ["kod", "araçkodu", "aracno", "araçno", "envanterno", "no"],
    "name": ["ad", "araçadı", "isim", "araçismi"],
    "current_km": ["km", "kilometre", "anlıkkm", "güncelkm", "mevcutkm"],
    "manufacturer": ["üretici", "marka"],
    "model": ["model"],
    "chassis_no": ["şasino", "şaseno", "serino", "şasi"],
    "notes": ["not", "açıklama", "notlar"],
}


def _build_header_index(header_row: list) -> dict[str, int]:
    """Excel başlık satırından {alan_adı: sütun_index} eşlemesi çıkarır."""
    field_by_norm_alias: dict[str, str] = {}
    for field, aliases in _VEHICLE_HEADER_ALIASES.items():
        for alias in aliases:
            field_by_norm_alias[_norm(alias)] = field

    index: dict[str, int] = {}
    for col_idx, header in enumerate(header_row):
        if not header:
            continue
        norm = _norm(str(header))
        field = field_by_norm_alias.get(norm)
        if field and field not in index:
            index[field] = col_idx
    return index


def _find_multiblock_header_row(rows: list[tuple]) -> int | None:
    """Şirketin 'GÜNCEL KM'LER' dosyası gibi, aynı satırda yan yana birden
    fazla (Araç No | KM) bloğu içeren tabloları tanır — örn. A/C, E/G, I/K,
    M/O sütun çiftleri, her blokta ayrı bir 'ARAÇ NO' başlığı ve başlıksız
    bir km sütunu (iki sütun sağında). İlk birkaç satırda 'Araç No' (veya
    eşdeğeri) en az 2 kez tekrar eden satırı döndürür."""
    code_aliases = {_norm(a) for a in _VEHICLE_HEADER_ALIASES["code"]}
    for i, row in enumerate(rows[:6]):
        if not row:
            continue
        hits = sum(1 for v in row if v and _norm(str(v)) in code_aliases)
        if hits >= 2:
            return i
    return None


def _parse_multiblock_vehicle_grid(rows: list[tuple], header_row_idx: int) -> list[dict]:
    """_find_multiblock_header_row ile bulunan çoklu blok tablosunu
    (code, current_km) satırlarına açar. Her blokta km sütunu, kod
    sütunundan 2 sütun sağdadır (aradaki sütun boş ayraçtır)."""
    header_row = rows[header_row_idx]
    code_aliases = {_norm(a) for a in _VEHICLE_HEADER_ALIASES["code"]}
    block_cols = [
        idx for idx, v in enumerate(header_row)
        if v and _norm(str(v)) in code_aliases
    ]

    out: list[dict] = []
    seen_codes: set[str] = set()
    for code_col in block_cols:
        km_col = code_col + 2
        for row in rows[header_row_idx + 1:]:
            if row is None or code_col >= len(row):
                continue
            code_val = row[code_col]
            if code_val is None or (isinstance(code_val, str) and not code_val.strip()):
                continue
            code = str(int(code_val)) if isinstance(code_val, float) and code_val.is_integer() else str(code_val).strip()
            if not code or code in seen_codes:
                continue
            seen_codes.add(code)

            km_val = row[km_col] if km_col < len(row) else None
            try:
                km = int(km_val) if km_val is not None else None
            except (TypeError, ValueError):
                km = None

            out.append(
                {
                    "code": code,
                    "name": None,
                    "current_km": km,
                    "manufacturer": None,
                    "model": None,
                    "chassis_no": None,
                    "notes": None,
                }
            )
    return out


# Şirketin araç kodu -> model eşleme kuralı (Excel'de bu bilgi genelde
# bulunmadığı için kod aralığına göre otomatik belirlenir):
#   3801-3838 (dahil)  -> Sirio
#   3839-3871 (dahil)  -> Bozankaya Tip-1
#   geri kalan (sayısal)-> Bozankaya Tip-2
def _auto_vehicle_model(code: str) -> str:
    try:
        n = int(code)
    except (TypeError, ValueError):
        return ""
    if 3801 <= n <= 3838:
        return "Sirio"
    if 3839 <= n <= 3871:
        return "Bozankaya Tip-1"
    return "Bozankaya Tip-2"


def _fill_vehicle_defaults(rows: list[dict]) -> list[dict]:
    """Araç adı ve modeli Excel'de yoksa (veya şirketin standart adlandırma/
    model kuralına göre) otomatik doldurur: ad 'Tramvay {kod}', model kod
    aralığına göre (bkz. _auto_vehicle_model)."""
    for row in rows:
        code = row.get("code")
        if not code:
            continue
        if not row.get("name"):
            row["name"] = f"Tramvay {code}"
        if not row.get("model"):
            model = _auto_vehicle_model(code)
            if model:
                row["model"] = model
    return rows


def parse_vehicles_workbook(content: bytes) -> list[dict]:
    """Yüklenen xlsx dosyasını satır satır ayrıştırır.

    İlk satır başlık olarak kabul edilir. Sütun sırası önemli değildir,
    başlık metinleri eşleştirilir (Türkçe karakter ve boşluk duyarsız).
    'code' (araç kodu) zorunludur; diğer alanlar boş bırakılabilir.

    Ayrıca şirketin standart 'GÜNCEL KM'LER' dışa aktarım formatını da
    (aynı satırda yan yana birden çok Araç No/KM bloğu) otomatik tanır —
    bu formatta ayrı bir 'KM' başlığı yoktur, kod sütununun 2 sağındaki
    sütun km olarak kabul edilir.
    """
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []

    multiblock_header_idx = _find_multiblock_header_row(rows)
    if multiblock_header_idx is not None:
        multiblock_rows = _parse_multiblock_vehicle_grid(rows, multiblock_header_idx)
        if multiblock_rows:
            return _fill_vehicle_defaults(multiblock_rows)

    header_index = _build_header_index(list(rows[0]))
    if "code" not in header_index:
        raise ValueError(
            "Başlık satırında araç kodu sütunu bulunamadı "
            "(örn. 'Kod', 'Araç Kodu', 'Araç No')"
        )

    out = []
    for row in rows[1:]:
        if row is None or all(v is None for v in row):
            continue

        def get(field):
            idx = header_index.get(field)
            if idx is None or idx >= len(row):
                return None
            v = row[idx]
            if isinstance(v, str):
                v = v.strip()
            return v if v != "" else None

        code = get("code")
        if not code:
            continue
        km = get("current_km")
        try:
            km = int(km) if km is not None else None
        except (TypeError, ValueError):
            km = None
        out.append(
            {
                "code": str(code).strip(),
                "name": get("name"),
                "current_km": km,
                "manufacturer": get("manufacturer"),
                "model": get("model"),
                "chassis_no": get("chassis_no"),
                "notes": get("notes"),
            }
        )
    return _fill_vehicle_defaults(out)


def _safe_sheet_title(name: str, fallback: str) -> str:
    name = (name or "").strip() or fallback
    for ch in "[]:*?/\\":
        name = name.replace(ch, "-")
    return name[:31]


def build_vehicles_workbook(vehicles: list[models.Vehicle]) -> bytes:
    """Araç listesini xlsx olarak üretir (dışa aktarma).

    Filoda birden fazla araç modeli varsa (bkz. Bakım Ağacı sayfasındaki
    model bazlı Excel yükleme) her model kendi sayfasına ayrılır (model
    girilmemiş araçlar "Genel" sayfasına düşer); tek model/model bilgisi
    yoksa tek sayfa yeterlidir. Durum sütunu Aktif/Pasif'e göre renklendirilir.
    """
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    headers = [
        "Araç Kodu",
        "Araç Adı",
        "Anlık KM",
        "Üretici",
        "Model",
        "Şasi No",
        "Not",
        "Durum",
        "Son KM Güncelleme",
    ]
    widths = [14, 20, 12, 16, 20, 16, 24, 10, 18]

    groups: dict[str, list[models.Vehicle]] = {}
    for v in vehicles:
        key = (v.model or "").strip() or "Genel"
        groups.setdefault(key, []).append(v)
    group_names = sorted(groups.keys(), key=lambda k: (k != "Genel", k))
    multi = len(group_names) > 1

    for gname in group_names:
        ws = wb.create_sheet(_safe_sheet_title(gname, "Araçlar") if multi else "Araçlar")
        ws.append(headers)
        _style_header_row(ws, len(headers))
        for v in groups[gname]:
            ws.append(
                [
                    v.code,
                    v.name,
                    v.current_km,
                    v.manufacturer,
                    v.model,
                    v.chassis_no,
                    v.notes,
                    "Aktif" if v.is_active else "Pasif",
                    v.last_km_update_at.strftime("%d.%m.%Y %H:%M")
                    if v.last_km_update_at
                    else "",
                ]
            )
            r = ws.max_row
            status_cell = ws.cell(row=r, column=8)
            status_cell.fill = _ACTIVE_FILL if v.is_active else _INACTIVE_FILL
            status_cell.font = _ACTIVE_FONT if v.is_active else _INACTIVE_FONT
            status_cell.alignment = Alignment(horizontal="center")
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w
        _autofilter(ws, len(headers), len(groups[gname]))

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ================================================================= Bakım/rapor dışa aktarma (Faz 16)
_STATUS_TR = {
    "pending": "Bekliyor",
    "in_progress": "Devam Ediyor",
    "completed": "Tamamlandı",
}


def _count_leaves(items: list) -> tuple[int, int]:
    """Bir bakımın en alt seviye (yaprak) madde sayısı ve tamamlananları."""
    child_ids = {it.parent_id for it in items if it.parent_id is not None}
    done = total = 0
    for it in items:
        if it.id not in child_ids:  # çocuğu yok → yaprak
            total += 1
            if it.is_completed:
                done += 1
    return done, total


def build_maintenances_workbook(maintenances: list, title: str = "Bakımlar") -> bytes:
    """Bakım/iş emri listesini (geçmiş, bekleyen, tamamlanan veya filtrelenmiş
    sonuç) xlsx olarak üretir.

    Sonuçta birden fazla durum (Bekliyor/Devam Ediyor/Tamamlandı) varsa her
    durum kendi sayfasına ayrılır (bkz. `statuses_present`).

    Tek durum varsa (ör. yalnızca tamamlanan Bakım Geçmişi dışa aktarımı —
    en yaygın kullanım): bu kez PERİYODA (bakım planına, ör. "200.000 km
    Bakımı" / "500.000 km Bakımı") göre ayrı sayfalara bölünür — böylece
    farklı periyotların bakım geçmişi tek kalabalık sayfada karışmaz, her
    periyot kendi sayfasında ayrı ayrı incelenebilir/filtrelenebilir. Tek bir
    periyot varsa (tüm sonuçlar aynı plana ait) gereksiz bölünme yapılmaz,
    tek sayfa yeterlidir.

    Durum sütunu her zaman rengiyle vurgulanır."""
    headers = [
        "İş Emri",
        "Araç Kodu",
        "Araç Adı",
        "Periyot",
        "Hedef KM",
        "Açılış KM",
        "Durum",
        "İlerleme",
        "Atanan",
        "Oluşturma",
        "Başlangıç",
        "Bitiş",
    ]
    widths = [10, 12, 22, 18, 12, 12, 14, 12, 20, 18, 18, 18]

    def dt(v):
        return v.strftime("%d.%m.%Y %H:%M") if v else ""

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    _used_titles: set[str] = set()

    def write_sheet(sheet_title: str, rows: list) -> None:
        safe = _safe_sheet_title(sheet_title, "Bakımlar")
        # Farklı plan adları 31 karaktere kısaltılınca çakışabilir — aynı
        # sayfa adı ikinci kez kullanılıyorsa openpyxl hata vermesin diye
        # kısa bir ayraç eklenir.
        final = safe
        n = 2
        while final in _used_titles:
            final = _safe_sheet_title(f"{safe[:28]} ({n})", "Bakımlar")
            n += 1
        _used_titles.add(final)
        ws = wb.create_sheet(final)
        ws.append(headers)
        _style_header_row(ws, len(headers))
        for m in rows:
            done, total = _count_leaves(list(m.items))
            ws.append(
                [
                    m.id,
                    m.vehicle.code if m.vehicle else "",
                    m.vehicle.name if m.vehicle else "",
                    m.plan.name if m.plan else "",
                    m.target_km,
                    m.km_at_open,
                    _STATUS_TR.get(m.status, m.status),
                    f"{done}/{total}",
                    m.assigned_to.full_name if m.assigned_to else "",
                    dt(m.created_at),
                    dt(m.started_at),
                    dt(m.completed_at),
                ]
            )
            r = ws.max_row
            status_cell = ws.cell(row=r, column=7)
            fill = _STATUS_FILLS.get(m.status)
            font = _STATUS_FONTS.get(m.status)
            if fill:
                status_cell.fill = fill
                status_cell.font = font
                status_cell.alignment = Alignment(horizontal="center")
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w
        _autofilter(ws, len(headers), len(rows))

    statuses_present = [
        key for key in ("pending", "in_progress", "completed")
        if any(m.status == key for m in maintenances)
    ]

    if len(statuses_present) <= 1:
        # Tek durum: periyoda (plana) göre böl — birden fazla farklı periyot
        # varsa. Aynı (interval_km, interval_months, name) periyodu tek grup
        # sayılır; sıralama küçükten büyüğe km/ay bazındadır.
        groups: dict[tuple, list] = {}
        for m in maintenances:
            p = m.plan
            key = (p.interval_km, p.interval_months or 0, p.name) if p else (0, 0, "Bilinmeyen Plan")
            groups.setdefault(key, []).append(m)

        if len(groups) <= 1:
            write_sheet(title, maintenances)
        else:
            for (_, __, plan_name), rows in sorted(groups.items()):
                write_sheet(plan_name, rows)
    else:
        for key in statuses_present:
            write_sheet(_STATUS_TR[key], [m for m in maintenances if m.status == key])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ================================================================= Bakım ağacı içe aktarma
def import_maintenance_tree(db, workbook_source: str | bytes, vehicle_model: str = "") -> dict:
    """'Koruyucu Bakım Liste Çalışması' formatındaki xlsx'i okuyup her bakım
    periyodu (km) için ayrı bir MaintenancePlan + o plana özel bakım ağacı
    (TreeNode) oluşturur. Zaten var olan aynı (interval_km, vehicle_model)
    çiftine sahip planın ağacı varsa önce silinip yeniden oluşturulur
    (idempotent) — yani aynı modeli tekrar yüklemek o modelin ağacını
    tamamen günceller, farklı modellerin planlarına dokunmaz.

    `workbook_source` bir dosya yolu (str, ör. seed.py'den) veya yüklenen
    dosyanın ham baytları (bytes, ör. Bakım Ağacı sayfasındaki self-servis
    Excel yükleme uç noktasından) olabilir.

    `vehicle_model`: boş bırakılırsa ("") bu Excel'den oluşan planlar
    "genel" sayılır ve modelinden bağımsız TÜM araçlara uygulanır (mevcut
    filo/varsayılan davranış). Doldurulursa (ör. "CAF Urbos 100") yalnızca
    Vehicle.model alanı bu değerle birebir eşleşen araçlara uygulanır —
    böylece farklı araç modelleri için ayrı Excel'ler yükleyip birbirinden
    bağımsız bakım periyotları/ağaçları tanımlanabilir.

    Beklenen format: ilk satırda km periyot sütun başlıkları, 2/3/4.
    satırlarda kategori/alt kategori/bileşen başlıkları (soldan sağa
    doldurularak), 5. satırdan itibaren her satır bir checklist maddesi.
    Sayfa adı "Sayfa1" ise o kullanılır, yoksa ilk sayfa kullanılır.

    _EXCLUDED_INTERVALS içindeki periyotlar (ör. 400.000 km) kaynak
    dosyada bulunsa bile atlanır ve varsa daha önce oluşturulmuş
    plan+ağacı da temizlenir.

    Ayrıca bu kapsamdaki (genel veya belirli bir araç modeli) HENÜZ
    TAMAMLANMAMIŞ (Bekliyor/Devam Ediyor) tüm iş emirleri silinir, böylece
    güncellenmiş ağaçtan baştan açılırlar; Tamamlanmış iş emirleri (Bakım
    Geçmişi) korunur.

    Dönüş: {"plans": [...özet...], "plans_obj": [MaintenancePlan...],
    "nodes_created": N, "maintenances_reset": N}
    """
    vehicle_model = (vehicle_model or "").strip()
    if isinstance(workbook_source, (bytes, bytearray)):
        wb = openpyxl.load_workbook(io.BytesIO(workbook_source), data_only=True)
    else:
        wb = openpyxl.load_workbook(workbook_source, data_only=True)
    ws = wb["Sayfa1"] if "Sayfa1" in wb.sheetnames else wb.worksheets[0]
    max_col = ws.max_column
    max_row = ws.max_row

    def cell(r, c):
        v = ws.cell(row=r, column=c).value
        if isinstance(v, str):
            v = v.strip()
            if v == "":
                return None
        return v

    km_cols = [(c, cell(1, c)) for c in range(1, max_col + 1) if cell(1, c) is not None]
    if not km_cols:
        raise ValueError(
            "1. satırda km periyodu bulunamadı (her sütun bloğunun başında "
            "25000, 50000 gibi bir km değeri olmalı)"
        )

    periods = []
    for i, (c, km) in enumerate(km_cols):
        end = (km_cols[i + 1][0] - 1) if i + 1 < len(km_cols) else max_col
        try:
            km_int = int(km)
        except (TypeError, ValueError):
            continue
        if km_int in _EXCLUDED_INTERVALS:
            continue
        periods.append((km_int, c, end))

    for excluded_km in _EXCLUDED_INTERVALS:
        stale = (
            db.query(models.MaintenancePlan)
            .filter_by(interval_km=excluded_km, vehicle_model=vehicle_model)
            .first()
        )
        if stale:
            if db.query(models.Maintenance).filter_by(plan_id=stale.id).first():
                stale.is_active = False
            else:
                roots = db.query(models.TreeNode).filter_by(parent_id=None, plan_id=stale.id).all()
                for r in roots:
                    db.delete(r)
                db.query(models.PartHotspot).filter_by(plan_id=stale.id).delete()
                db.delete(stale)
            db.flush()

    # Excel'den bakım ağacı (yeniden) yüklendiğinde, aynı kapsamdaki (genel
    # veya belirli bir araç modeli) HENÜZ TAMAMLANMAMIŞ iş emirleri silinir;
    # böylece bir sonraki km kontrolünde güncellenmiş ağaçtan baştan
    # (temiz) açılırlar. Tamamlanmış (Bakım Geçmişi) iş emirlerine
    # dokunulmaz — geçmiş kaydı olarak kalır.
    open_q = (
        db.query(models.Maintenance)
        .join(models.Vehicle)
        .filter(models.Maintenance.status.in_(["pending", "in_progress"]))
    )
    if vehicle_model:
        open_q = open_q.filter(models.Vehicle.model == vehicle_model)
    reset_count = 0
    for m in open_q.all():
        db.delete(m)
        reset_count += 1
    db.flush()

    plans_touched: list[models.MaintenancePlan] = []
    total_nodes = 0

    for km, start, end in periods:
        plan = (
            db.query(models.MaintenancePlan)
            .filter_by(interval_km=km, vehicle_model=vehicle_model)
            .first()
        )
        if not plan:
            plan = models.MaintenancePlan(
                name=f"{km:,}".replace(",", ".") + " km Bakımı",
                interval_km=km,
                lower_pct=5.0,
                upper_pct=10.0,
                vehicle_model=vehicle_model,
            )
            db.add(plan)
            db.flush()
        plans_touched.append(plan)

        # Bu plana ait önceki ağacı temizle (yeniden içe aktarma güvenli olsun)
        db.query(models.TreeNode).filter_by(parent_id=None, plan_id=plan.id).delete()
        db.flush()

        # sütunları gezerek kategori > alt kategori > bileşen > checklist maddeleri ağacını kur
        cat_nodes: dict[str, models.TreeNode] = {}
        sub_nodes: dict[tuple[str, str], models.TreeNode] = {}
        cat_order = 0
        sub_order: dict[str, int] = {}
        comp_order: dict[tuple[str, str], int] = {}

        cat = None
        sub = None
        for c in range(start, end + 1):
            v2, v3, v4 = cell(2, c), cell(3, c), cell(4, c)
            if v2:
                cat = v2
            if v3:
                sub = v3
            if v4 is None or cat is None or sub is None:
                continue

            if cat not in cat_nodes:
                cat_node = models.TreeNode(
                    plan_id=plan.id, parent_id=None, name=cat, order_index=cat_order
                )
                db.add(cat_node)
                db.flush()
                cat_nodes[cat] = cat_node
                cat_order += 1
                total_nodes += 1
                sub_order[cat] = 0

            sub_key = (cat, sub)
            if sub_key not in sub_nodes:
                sub_node = models.TreeNode(
                    plan_id=None,
                    parent_id=cat_nodes[cat].id,
                    name=sub,
                    order_index=sub_order[cat],
                )
                db.add(sub_node)
                db.flush()
                sub_nodes[sub_key] = sub_node
                sub_order[cat] += 1
                total_nodes += 1
                comp_order[sub_key] = 0

            comp_node = models.TreeNode(
                plan_id=None,
                parent_id=sub_nodes[sub_key].id,
                name=v4,
                order_index=comp_order[sub_key],
            )
            db.add(comp_node)
            db.flush()
            comp_order[sub_key] += 1
            total_nodes += 1

            item_order = 0
            for r in range(5, max_row + 1):
                item_name = cell(r, c)
                if not item_name:
                    continue
                db.add(
                    models.TreeNode(
                        plan_id=None,
                        parent_id=comp_node.id,
                        name=item_name,
                        order_index=item_order,
                    )
                )
                item_order += 1
                total_nodes += 1
            db.flush()

    db.commit()
    for p in plans_touched:
        db.refresh(p)
    return {
        "plans": [{"id": p.id, "name": p.name, "interval_km": p.interval_km} for p in plans_touched],
        "plans_obj": plans_touched,
        "nodes_created": total_nodes,
        "maintenances_reset": reset_count,
    }


# ================================================================= Toplu bakım kataloğu içe aktarma
# "ARAÇ | BÖLGE | SİSTEM | EKİPMAN | BAKIM | BAKIM KALEMİ | KART KODU"
# formatındaki düz (flat) tabloyu okur — üretici/entegratörden tek dosyada
# birden fazla araç modelinin TÜM bakım kataloğu geldiğinde kullanılır
# (bkz. main.py POST /api/tree/import-bulk-catalog). import_maintenance_tree
# ("Koruyucu Bakım Liste Çalışması" — sütun bloklu, TEK araç modeli) ile
# KARIŞTIRILMAMALI; bu format satır bazlıdır ve ARAÇ sütunundan araç
# modelini kendisi okur.
_BULK_CATALOG_HEADERS = ["ARAÇ", "BÖLGE", "SİSTEM", "EKİPMAN", "BAKIM", "BAKIM KALEMİ", "KART KODU"]

# Kaynak dosyada büyük km periyotları bazen açık sayı yerine kısaltmalı bir
# kodla yazılır — bunlar SÜRE (ay/yıl) DEĞİL, km katlarıdır: "1M" -> 10.000
# km ("M" = 10.000 km birimi, yaklaşık 1 ay eşdeğeri filo kullanımı). "Y"
# birimi ("yıl") bu ayın 12 katıdır: 1Y = 12 × 10.000 = 120.000 km. Kullanıcı
# beyanı (KB): "1Y" = 120.000 km, "2Y" = 240.000 km, vb. — ESKİDEN burada
# yanlışlıkla "Y" = 100.000 km kullanılıyordu, düzeltildi.
_KM_CODE_RE = re.compile(r"^\s*(\d+)\s*([MYmy])\s*$")
_KM_CODE_UNIT = {"M": 10_000, "Y": 120_000}


def _parse_bakim_value(raw) -> tuple[str, int, int | None] | None:
    """BAKIM hücresini ("25000" veya "1M"/"3M"/"1Y" gibi kısaltmalı km
    kodunu) yorumlar. Dönüş: (plan_type, interval_km, interval_months) —
    plan_type burada her zaman "km"dir (interval_months her zaman None) —
    ya da yorumlanamıyorsa None (ör. '?' veya boş)."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        n = int(raw)
        return ("km", n, None) if n > 0 else None
    if isinstance(raw, str):
        s = raw.strip().upper()
        if not s:
            return None
        if s.isdigit():
            n = int(s)
            return ("km", n, None) if n > 0 else None
        m = _KM_CODE_RE.match(s)
        if m:
            n = int(m.group(1))
            unit = m.group(2).upper()
            km = n * _KM_CODE_UNIT.get(unit, 0)
            return ("km", km, None) if km > 0 else None
    return None


def import_bulk_catalog(
    db, workbook_source: str | bytes, vehicle_model_override: str = "", dry_run: bool = False,
) -> dict:
    """KB TOPLU LİSTE formatındaki toplu bakım kataloğunu içe aktarır.

    `vehicle_model_override` doldurulursa (Bakım Ağacı sayfasındaki yükleme
    formunda "Araç Modeli" seçilirse): dosyadaki ARAÇ sütunu TAMAMEN göz ardı
    edilir, TÜM satırlar bu modele aitmiş gibi işlenir — bu sayede ARAÇ
    sütunu olmayan (tek modele özel, daha basit) dosyalar da yüklenebilir.
    Boş bırakılırsa (varsayılan) her satırın modeli, ARAÇ sütunundaki
    değerden otomatik okunur (eskisi gibi).

    `dry_run=True` verilirse: dosya BİREBİR AYNI kod yolundan işlenir (aynı
    sayaçlar/raporlar üretilir) ama sonda veritabanına HİÇBİR ŞEY YAZILMAZ
    (işlem sonunda commit yerine rollback yapılır) — "önizleme" için.
    Admin, gerçekten kaydetmeden önce kaç plan/madde oluşacağını, hangi
    satırların atlanacağını/belirsiz kalacağını görebilir.

    Her satır: ARAÇ (araç modeli, ör. "BOZANKAYA"/"SİRİO" — bu satır o
    modeldeki araçlara otomatik atanır, diğer modellere dokunmaz), BÖLGE
    (ör. ARAÇ İÇİ/ÜSTÜ/ALTI — kök düğüm), SİSTEM (2. seviye), EKİPMAN (3.
    seviye), BAKIM (periyot — km sayısı, ya da "1M"/"3M"/"1Y" gibi kısaltmalı
    km kodu: "M"=10.000 km, "Y"=100.000 km birimi — bkz. _parse_bakim_value;
    bunlar SÜRE değil km katıdır), BAKIM KALEMİ (checklist maddesi — yaprak
    düğüm), KART KODU (yaprağa iliştirilen rozet, opsiyonel).

    Her benzersiz (araç modeli, periyot) çifti için bir MaintenancePlan
    bulunur/oluşturulur (plan adı otomatik, bkz. services.default_plan_name);
    o plana ait ağaç TAMAMEN yeniden kurulur (idempotent — aynı dosyayı
    tekrar yüklemek o modelin ağacını günceller/üzerine yazar, diğer
    modellere dokunmaz, çift madde oluşturmaz) ve o modeldeki henüz
    TAMAMLANMAMIŞ iş emirleri sıfırlanır (import_maintenance_tree ile aynı
    kural). Baştan/sondan boşluklu hücreler (ör. "ARAÇ İÇİ ") temizlenir.

    ARAÇ'ı boş olan satırlar ve BAKIM hücresi tamamen BOŞ olan satırlar
    (sayfa sonundaki boş dolgu satırları) atlanır, `skipped` listesinde
    raporlanır. BAKIM hücresinde bir değer olup da yorumlanamayan satırlar
    (ör. '?') ise ATLANMAZ: "Periyodu Belirsiz (?)" adlı ayrı bir plan
    altında ağaca eklenir, ham metin madde notunda korunur (kaynak
    netleşene kadar periyot bilgisi soru işaretli kalır) ve interval_km=0/
    interval_months=None olduğu için otomatik açma motorlarından doğal
    olarak hariç tutulur; bu satırlar `unresolved_period` listesinde
    raporlanır.

    Kaynak dosyada görünen HER periyot (ör. 800.000 km gibi başka bir
    periyodun tam katı olsa bile) kendi başına GERÇEK, Bakım Planları
    sayfasında da görünen bir plan olarak açılır — hiçbiri gizlenmez/temel
    periyoda katlanmaz. (`catalog_only_periods` alanı geriye dönük uyumluluk
    için dönüş sözlüğünde hâlâ var ama artık her zaman boştur.)

    Dönüş: {"vehicle_models": [...], "plans_created": N, "plans_updated": N,
    "nodes_created": N, "leaves_created": N, "skipped": [...],
    "unresolved_period": [...], "catalog_only_periods": [...], "dry_run": bool}."""
    from . import services  # main.py -> excel_utils -> services döngüsünü önlemek için burada import edilir

    if isinstance(workbook_source, (bytes, bytearray)):
        wb = openpyxl.load_workbook(io.BytesIO(workbook_source), data_only=True)
    else:
        wb = openpyxl.load_workbook(workbook_source, data_only=True)

    def norm_cell(v):
        if isinstance(v, str):
            v = v.strip()
            return v if v else None
        return v

    # Satırları (plan_type, interval_km, interval_months, vehicle_model)
    # anahtarı altında grupla — aynı plana ait tüm maddeler tek geçişte
    # ağaca eklensin.
    rows_by_plan: dict[tuple, list[dict]] = {}
    skipped: list[dict] = []
    unresolved: list[dict] = []
    collapsed: list[dict] = []  # temel bir periyodun katı olan (is_catalog_only) planlar
    vehicle_models: set[str] = set()

    # ---- Başlık (kolon) eşleme: kolon SIRASI değil BAŞLIK ADI önemli --------
    # Böylece kaynak dosyadaki kolon sırası/adı değişse de tüm veriler
    # eksiksiz okunur. İki yaygın düzen desteklenir:
    #   Format A (KB Toplu Liste): ARAÇ | BÖLGE | SİSTEM | EKİPMAN | BAKIM |
    #                              BAKIM KALEMİ | KART KODU
    #   Format B (hiyerarşik): ARAÇ | BAKIM | BAKIM KONUMU | EKİPMAN |
    #             ALT EKİPMAN | BAKIM KALEMİ 1 | BAKIM KALEMİ 2 | ... (çok kalem)
    # Format B'de bir satırda birden çok "BAKIM KALEMİ n" sütunu bulunur;
    # HER dolu kalem sütunu ayrı bir yaprak (madde) olarak alınır — eskiden
    # yalnızca ilk kalem okunduğu için maddelerin çoğu eksik kalıyordu.
    _H_ARAC = {_norm(x) for x in ("ARAÇ", "ARAC", "ARAÇ MODELİ", "MODEL")}
    _H_BAKIM = {_norm(x) for x in ("BAKIM", "PERİYOT", "PERIYOT", "BAKIM PERİYODU", "BAKIM PERIYODU")}
    _H_BOLGE = {_norm(x) for x in ("BÖLGE", "BOLGE", "BAKIM KONUMU", "KONUM")}
    _H_SISTEM = {_norm(x) for x in ("SİSTEM", "SISTEM")}
    _H_EKIPMAN = {_norm(x) for x in ("EKİPMAN", "EKIPMAN")}
    _H_ALT_EKIPMAN = {_norm(x) for x in ("ALT EKİPMAN", "ALT EKIPMAN")}
    _H_KART = {_norm(x) for x in ("KART KODU", "KART", "KARTKODU", "KART NO")}
    _ITEM_PREFIX = _norm("BAKIM KALEMİ")  # "bakimkalemi" -> "BAKIM KALEMİ", "BAKIM KALEMİ 1"..

    def _build_catalog_map(header_row: list) -> tuple[dict, list[int]] | None:
        """Başlık satırından {alan: kolon_index} + kalem kolonları listesini
        çıkarır. Katalog sayfası değilse (BAKIM başlığı yoksa, ya da
        vehicle_model_override YOKSA ve ARAÇ başlığı da yoksa) None döner —
        override verilmişse ARAÇ sütunu hiç aranmaz/gerekmez."""
        norm_h = [_norm(str(v)) if v not in (None, "") else "" for v in header_row]
        has_alt = any(h in _H_ALT_EKIPMAN for h in norm_h)
        idx: dict[str, int] = {}
        item_cols: list[int] = []
        for c, h in enumerate(norm_h):
            if not h:
                continue
            if h.startswith(_ITEM_PREFIX):
                item_cols.append(c)
            elif h in _H_ARAC and "arac" not in idx:
                idx["arac"] = c
            elif h in _H_BAKIM and "bakim" not in idx:
                idx["bakim"] = c
            elif h in _H_BOLGE and "bolge" not in idx:
                idx["bolge"] = c
            elif h in _H_KART and "kart" not in idx:
                idx["kart"] = c
            elif has_alt:  # Format B: EKİPMAN=2.seviye, ALT EKİPMAN=3.seviye
                if h in _H_EKIPMAN and "sistem" not in idx:
                    idx["sistem"] = c
                elif h in _H_ALT_EKIPMAN and "ekipman" not in idx:
                    idx["ekipman"] = c
            else:  # Format A: SİSTEM=2.seviye, EKİPMAN=3.seviye
                if h in _H_SISTEM and "sistem" not in idx:
                    idx["sistem"] = c
                elif h in _H_EKIPMAN and "ekipman" not in idx:
                    idx["ekipman"] = c
        if (not vehicle_model_override and "arac" not in idx) or "bakim" not in idx or not item_cols:
            return None
        return idx, item_cols

    def _find_header_row(ws) -> tuple[int, list] | None:
        """Sayfanın ilk ~12 satırında BAKIM (ve override yoksa ayrıca ARAÇ)
        başlıklarını içeren satırı (başlık satırı) bulur."""
        for r in range(1, min(ws.max_row, 12) + 1):
            row = [ws.cell(row=r, column=c).value for c in range(1, ws.max_column + 1)]
            norm_set = {_norm(str(v)) for v in row if v not in (None, "")}
            if (norm_set & _H_BAKIM) and (vehicle_model_override or (norm_set & _H_ARAC)):
                return r, row
        return None

    # Kaynak dosyadaki ARAÇ değeri (ör. "BOZANKAYA", "SİRİO") filodaki gerçek
    # Vehicle.model değerleriyle (ör. "Bozankaya Tip-1", "Bozankaya Tip-2",
    # "Sirio") birebir aynı yazılmayabilir — büyük/küçük harf, Türkçe karakter
    # ve "Tip-1/Tip-2" gibi ek ayrımlar farklı olabilir. Plan eşleştirmesi
    # (check_and_open_maintenances) TAM eşitlik kullandığından, ARAÇ token'ı
    # normalize edilip (_norm) filodaki gerçek modellerle karşılaştırılır; eşleşen
    # TÜM gerçek modeller için ayrı ayrı plan/ağaç oluşturulur (ör. "BOZANKAYA"
    # hem "Bozankaya Tip-1" hem "Bozankaya Tip-2"ye uygulanır). Hiç eşleşme
    # yoksa (filoda henüz o modelden araç yoksa) token olduğu gibi kullanılır.
    _real_models_cache: list[str] | None = None

    def _resolve_target_models(arac_token: str) -> list[str]:
        nonlocal _real_models_cache
        if _real_models_cache is None:
            _real_models_cache = [
                m for (m,) in db.query(models.Vehicle.model).distinct().all() if m
            ]
        tok = _norm(arac_token)
        if not tok:
            return [arac_token.strip()]
        matches = sorted({m for m in _real_models_cache if tok in _norm(m) or _norm(m) in tok})
        return matches or [arac_token.strip()]

    def _emit(vehicle_model, bakim, bolge, sistem, ekipman, kalem, kart, sheet, row_no):
        """Tek bir (madde) satırını rows_by_plan'a ekler.

        NOT: bir periyot (ör. 800.000 km) bir temel periyodun (ör. 200.000
        km) tam katıysa, İÇERİĞİ o temel periyodun ağacına KATILMAZ/
        BİRLEŞTİRİLMEZ — kendi periyoduyla, kendi ayrı planında/ağacında
        kalır (aksi halde 800.000'e özel maddeler yanlışlıkla HER 200.000
        km'de tekrar isteniyor gibi görünürdü). Yalnızca bu planın
        `is_catalog_only` olarak işaretlenip işaretlenmeyeceği (Bakım
        Planları'nda gizlenip gizlenmeyeceği) burada `collapsed`
        listesinde raporlanır — bkz. aşağıdaki plan oluşturma döngüsü."""
        parsed = _parse_bakim_value(bakim)
        note = ""
        if parsed is None:
            raw_bakim = str(bakim).strip()
            plan_type, interval_km, interval_months = "unknown", 0, None
            note = f"Bakım periyodu kaynak dosyada {raw_bakim!r} olarak verilmiş — netleşince Excel güncellenip yeniden yüklenmeli."
            unresolved.append({
                "row": row_no,
                "reason": f"[{sheet}] Periyot belirsiz ({raw_bakim!r}) — ağaca eklendi, periyot bilgisi soru işaretli bırakıldı.",
                "data": [vehicle_model, str(bakim), bolge, sistem, ekipman, kalem],
            })
        else:
            plan_type, interval_km, interval_months = parsed

        key = (plan_type, interval_km, interval_months, vehicle_model)
        rows_by_plan.setdefault(key, []).append({
            "bolge": bolge,
            "sistem": sistem,
            "ekipman": ekipman,
            "kalem": kalem,
            "kart": kart,
            "note": note,
        })

    processed_any = False
    # TÜM sayfaları tara — yalnızca ilk sayfa değil. Katalog başlığı taşımayan
    # sayfalar (arama/sözlük sayfaları vb.) otomatik atlanır.
    for ws in wb.worksheets:
        found = _find_header_row(ws)
        if found is None:
            continue
        header_idx, header_row = found
        mapping = _build_catalog_map(header_row)
        if mapping is None:
            continue
        idx, item_cols = mapping
        # Hiyerarşi kolonları eksikse bu sayfa katalog değildir
        if not all(k in idx for k in ("bolge", "sistem", "ekipman")):
            continue
        processed_any = True

        # Birleştirilmiş/boş bırakılmış üst kolonları taşıyabilmek için
        # (ARAÇ/BAKIM/BÖLGE/SİSTEM/EKİPMAN) son dolu değeri hatırla (ileri
        # doldurma) — böylece grup başında bir kez yazılıp altta boş bırakılan
        # satırlar da eksiksiz okunur.
        last = {"arac": None, "bakim": None, "bolge": None, "sistem": None, "ekipman": None}

        for r in range(header_idx + 1, ws.max_row + 1):
            row = [ws.cell(row=rr, column=c).value for rr in (r,) for c in range(1, ws.max_column + 1)]

            def get(field):
                c = idx.get(field)
                if c is None or c >= len(row):
                    return None
                return norm_cell(row[c])

            arac = get("arac"); bakim = get("bakim")
            bolge = get("bolge"); sistem = get("sistem"); ekipman = get("ekipman")
            kart = get("kart")
            items = [norm_cell(row[c]) for c in item_cols if c < len(row)]
            items = [i for i in items if i not in (None, "")]

            # Tamamen boş satır (dolgu) — sessizce atla, ileri doldurmayı bozma
            if arac is None and bakim is None and bolge is None and sistem is None and ekipman is None and not items:
                continue

            # İleri doldurma (yalnızca üst/gruplama kolonları)
            if arac is not None: last["arac"] = arac
            if bakim is not None: last["bakim"] = bakim
            if bolge is not None: last["bolge"] = bolge
            if sistem is not None: last["sistem"] = sistem
            if ekipman is not None: last["ekipman"] = ekipman
            arac, bakim = last["arac"], last["bakim"]
            bolge, sistem, ekipman = last["bolge"], last["sistem"], last["ekipman"]
            if vehicle_model_override:
                arac = vehicle_model_override

            data_preview = [str(arac or ""), str(bakim or ""), str(bolge or ""), str(sistem or ""), str(ekipman or "")]
            if not arac:
                skipped.append({"row": r, "reason": f"[{ws.title}] ARAÇ (model) boş", "data": data_preview})
                continue
            if bakim is None or (isinstance(bakim, str) and not str(bakim).strip()):
                skipped.append({"row": r, "reason": f"[{ws.title}] BAKIM (periyot) boş", "data": data_preview})
                continue
            if not (bolge and sistem and ekipman):
                skipped.append({"row": r, "reason": f"[{ws.title}] BÖLGE/SİSTEM/EKİPMAN alanlarından biri boş", "data": data_preview})
                continue
            if not items:
                skipped.append({"row": r, "reason": f"[{ws.title}] Hiç BAKIM KALEMİ yok", "data": data_preview})
                continue

            kart_str = str(kart).strip() if kart else ""
            target_models = _resolve_target_models(str(arac).strip())
            for vehicle_model in target_models:
                vehicle_models.add(vehicle_model)
                # Bir satırdaki HER dolu kalem sütunu ayrı bir madde (yaprak) olur.
                for kalem in items:
                    _emit(
                        vehicle_model, bakim,
                        str(bolge).strip(), str(sistem).strip(), str(ekipman).strip(),
                        str(kalem).strip(), kart_str, ws.title, r,
                    )

    # Hiçbir sayfada başlık bulunamadıysa (başlıksız/eski konumsal dosya):
    # geriye dönük uyumluluk için ilk sayfayı ARAÇ|BÖLGE|SİSTEM|EKİPMAN|BAKIM|
    # BAKIM KALEMİ|KART KODU sırasıyla konumsal oku.
    if not processed_any:
        ws = wb.worksheets[0]
        for r in range(2, ws.max_row + 1):
            cells = [norm_cell(ws.cell(row=r, column=c).value) for c in range(1, 8)]
            cells += [None] * (7 - len(cells))
            arac, bolge, sistem, ekipman, bakim, kalem, kart = cells[:7]
            if arac is None and bolge is None and sistem is None and ekipman is None and bakim is None and kalem is None:
                continue
            if vehicle_model_override:
                arac = vehicle_model_override
            if not arac:
                skipped.append({"row": r, "reason": "ARAÇ (model) boş", "data": [str(c or "") for c in cells]})
                continue
            if not (bolge and sistem and ekipman and kalem):
                skipped.append({"row": r, "reason": "BÖLGE/SİSTEM/EKİPMAN/BAKIM KALEMİ alanlarından biri boş", "data": [str(c or "") for c in cells]})
                continue
            if bakim is None or (isinstance(bakim, str) and not str(bakim).strip()):
                skipped.append({"row": r, "reason": "BAKIM (periyot) boş", "data": [str(c or "") for c in cells]})
                continue
            target_models = _resolve_target_models(str(arac).strip())
            for vehicle_model in target_models:
                vehicle_models.add(vehicle_model)
                _emit(
                    vehicle_model, bakim,
                    str(bolge).strip(), str(sistem).strip(), str(ekipman).strip(),
                    str(kalem).strip(), str(kart).strip() if kart else "", ws.title, r,
                )

    plans_created = 0
    plans_updated = 0
    nodes_created = 0
    leaves_created = 0
    models_reset: set[str] = set()

    for (plan_type, interval_km, interval_months, vehicle_model), items in rows_by_plan.items():
        # Kullanıcı talebi: kaynak dosyada görünen HER periyot (ör. 800.000 km
        # gibi başka bir periyodun tam katı olsa bile) kendi başına GERÇEK,
        # görünür bir MaintenancePlan olarak açılır — artık hiçbiri
        # "is_catalog_only" olarak gizlenmez/temel periyoda katlanmaz. Zaten
        # var olan bir plan yeniden yüklendiğinde de is_catalog_only=False'a
        # çekilerek Bakım Planları'nda görünür hale getirilir.
        is_catalog_only = False

        plan = (
            db.query(models.MaintenancePlan)
            .filter_by(
                plan_type=plan_type,
                interval_km=interval_km,
                interval_months=interval_months,
                vehicle_model=vehicle_model,
            )
            .first()
        )
        if plan is None:
            plan = models.MaintenancePlan(
                name=services.default_plan_name(plan_type, interval_km, interval_months),
                interval_km=interval_km,
                interval_months=interval_months,
                plan_type=plan_type,
                lower_pct=5.0,
                upper_pct=10.0,
                vehicle_model=vehicle_model,
                is_catalog_only=is_catalog_only,
            )
            db.add(plan)
            db.flush()
            plans_created += 1
        else:
            plan.is_catalog_only = is_catalog_only  # yeniden yüklemede güncel duruma göre tazele
            plans_updated += 1

        # Bu plana ait önceki ağacı temizle (yeniden içe aktarma güvenli olsun)
        db.query(models.TreeNode).filter_by(parent_id=None, plan_id=plan.id).delete()
        db.flush()

        bolge_nodes: dict[str, models.TreeNode] = {}
        sistem_nodes: dict[tuple[str, str], models.TreeNode] = {}
        ekipman_nodes: dict[tuple[str, str, str], models.TreeNode] = {}
        bolge_order = 0
        sistem_order: dict[str, int] = {}
        ekipman_order: dict[tuple[str, str], int] = {}
        kalem_order: dict[tuple[str, str, str], int] = {}
        # Aynı madde birden çok periyottan (temel + ona katlanan katları)
        # gelebileceğinden, aynı yoldaki (bölge/sistem/ekipman/kalem) yaprağı
        # yalnızca bir kez ekle — kat periyotların katlanması çift madde
        # oluşturmasın.
        seen_leaves: set[tuple[str, str, str, str]] = set()

        for it in items:
            bolge, sistem, ekipman, kalem, kart = it["bolge"], it["sistem"], it["ekipman"], it["kalem"], it["kart"]
            leaf_note = it.get("note", "")

            leaf_key = (bolge, sistem, ekipman, kalem)
            if leaf_key in seen_leaves:
                continue  # aynı madde daha önce (başka bir kattan) eklendi
            seen_leaves.add(leaf_key)

            if bolge not in bolge_nodes:
                node = models.TreeNode(plan_id=plan.id, parent_id=None, name=bolge, order_index=bolge_order)
                db.add(node)
                db.flush()
                bolge_nodes[bolge] = node
                bolge_order += 1
                nodes_created += 1
                sistem_order[bolge] = 0

            sk = (bolge, sistem)
            if sk not in sistem_nodes:
                node = models.TreeNode(
                    plan_id=None, parent_id=bolge_nodes[bolge].id, name=sistem, order_index=sistem_order[bolge]
                )
                db.add(node)
                db.flush()
                sistem_nodes[sk] = node
                sistem_order[bolge] += 1
                nodes_created += 1
                ekipman_order[sk] = 0

            ek = (bolge, sistem, ekipman)
            if ek not in ekipman_nodes:
                node = models.TreeNode(
                    plan_id=None, parent_id=sistem_nodes[sk].id, name=ekipman, order_index=ekipman_order[sk]
                )
                db.add(node)
                db.flush()
                ekipman_nodes[ek] = node
                ekipman_order[sk] += 1
                nodes_created += 1
                kalem_order[ek] = 0

            db.add(
                models.TreeNode(
                    plan_id=None,
                    parent_id=ekipman_nodes[ek].id,
                    name=kalem,
                    order_index=kalem_order[ek],
                    card_code=kart,
                    note=leaf_note,
                )
            )
            kalem_order[ek] += 1
            nodes_created += 1
            leaves_created += 1
        db.flush()
        models_reset.add(vehicle_model)

    # Ağacı yenilenen her araç modeli için henüz TAMAMLANMAMIŞ iş emirlerini
    # sıfırla (import_maintenance_tree ile aynı idempotent kural) — böylece
    # güncellenmiş ağaçtan baştan açılırlar. Tamamlanmış bakımlar korunur.
    for vm in models_reset:
        (
            db.query(models.Maintenance)
            .filter(
                models.Maintenance.status.in_(["pending", "in_progress"]),
                models.Maintenance.vehicle_id.in_(
                    db.query(models.Vehicle.id).filter(models.Vehicle.model == vm)
                ),
            )
            .delete(synchronize_session=False)
        )

    if dry_run:
        # Önizleme: hiçbir şey kalıcı olmasın — yukarıdaki tüm db.add/flush
        # çağrıları yalnızca bu (henüz commit edilmemiş) işlem içinde
        # geçicidir; geri alınınca veritabanı hiç dokunulmamış gibi kalır.
        # Sayaçlar (plans_created/nodes_created/...) YİNE DE doğrudur, çünkü
        # gerçek yükleme ile BİREBİR AYNI kod yolundan geçilmiştir.
        db.rollback()
    else:
        db.commit()
    return {
        "vehicle_models": sorted(vehicle_models),
        "plans_created": plans_created,
        "plans_updated": plans_updated,
        "nodes_created": nodes_created,
        "leaves_created": leaves_created,
        "skipped": skipped,
        "unresolved_period": unresolved,
        "catalog_only_periods": collapsed,
        "dry_run": dry_run,
    }
