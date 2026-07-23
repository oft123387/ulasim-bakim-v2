"""Excel içe/dışa aktarma yardımcıları (araçlar ve bakım ağacı)."""

from __future__ import annotations

import io
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
    durum kendi sayfasına ayrılır; tek durum varsa (ör. yalnızca tamamlanan
    Bakım Geçmişi dışa aktarımı) tek sayfa yeterlidir. Durum sütunu her
    zaman rengiyle vurgulanır."""
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

    def write_sheet(sheet_title: str, rows: list) -> None:
        ws = wb.create_sheet(_safe_sheet_title(sheet_title, "Bakımlar"))
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
        write_sheet(title, maintenances)
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
                lower_pct=50.0,
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
