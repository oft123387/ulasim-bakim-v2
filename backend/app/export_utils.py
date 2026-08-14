"""Çoklu format dışa aktarma yardımcıları: PDF, CSV ve Word (Excel için
mevcut excel_utils.py kullanılır). Bakımlar ve İşlem Kayıtları (Audit Log)
sayfalarındaki 'Dışa Aktar' butonu bu modül üzerinden 4 formatı da üretir.
"""

from __future__ import annotations

import csv
import io
import os
from datetime import datetime

import reportlab
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.shared import Cm, Pt

from . import excel_utils

_STATUS_TR = {
    "pending": "Bekliyor",
    "in_progress": "Devam Ediyor",
    "completed": "Tamamlandı",
}
_ACTION_TR = {
    "create": "Oluşturma", "update": "Güncelleme", "delete": "Silme",
    "complete": "Tamamlama", "unlock": "Kilit Açma", "import": "İçe Aktarma",
    "export": "Dışa Aktarma", "login": "Giriş", "backup": "Yedekleme",
}
_ENTITY_TR = {
    "vehicle": "Araç", "plan": "Bakım Planı", "user": "Kullanıcı",
    "tree_node": "Ağaç Maddesi", "maintenance": "Bakım", "item": "Kontrol Maddesi",
    "attachment": "Ek", "model3d": "3D Model", "permission": "Yetki",
    "system": "Sistem", "part": "Parça", "part_attachment": "Parça Eki",
    "alarm": "Alarm",
}

MEDIA_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

# ---------------------------------------------------------------- Türkçe karakter destekli PDF fontu
# reportlab kendi paketiyle birlikte Bitstream Vera fontunu getirir; bu font
# ş/ğ/ı/İ/ç/ö/ü gibi Türkçe karakterleri de kapsadığı için ekstra bir font
# dosyası projeye eklemeye gerek kalmadan doğrudan kullanılabilir.
_FONT_DIR = os.path.join(os.path.dirname(reportlab.__file__), "fonts")
_FONT_REGULAR = "Helvetica"
_FONT_BOLD = "Helvetica-Bold"
_fonts_ready = False


def _ensure_fonts() -> None:
    global _fonts_ready, _FONT_REGULAR, _FONT_BOLD
    if _fonts_ready:
        return
    try:
        pdfmetrics.registerFont(TTFont("UlasimBakim", os.path.join(_FONT_DIR, "Vera.ttf")))
        pdfmetrics.registerFont(TTFont("UlasimBakim-Bold", os.path.join(_FONT_DIR, "VeraBd.ttf")))
        _FONT_REGULAR = "UlasimBakim"
        _FONT_BOLD = "UlasimBakim-Bold"
    except Exception:
        pass  # font kayıt edilemezse Helvetica ile devam edilir (Türkçe karakterler bozulabilir)
    _fonts_ready = True


def _dt(v) -> str:
    return v.strftime("%d.%m.%Y %H:%M") if v else ""


# ================================================================= Satır çıkarma (Bakımlar / Audit)
def maintenance_rows(maintenances: list) -> tuple[list[str], list[list[str]]]:
    # döngüsel importu önlemek için burada
    from . import services

    headers = [
        "İş Emri", "Araç Kodu", "Araç Adı", "Periyot", "Hedef", "Açılış KM",
        "Tamamlanan KM", "Sapma %",
        "Durum", "İlerleme", "Atanan", "Oluşturma", "Başlangıç", "Bitiş",
    ]
    rows = []
    for m in maintenances:
        done, total = excel_utils._count_leaves(list(m.items))
        # Süre bazlı bakımlarda target_km dahili bir sentinel değerdir (bkz.
        # models.Maintenance) — dışa aktarımda her zaman gerçek hedefi (km ya
        # da tarih) gösterilir.
        hedef = m.target_date.strftime("%d.%m.%Y") if m.target_date else str(m.target_km)
        deviation_pct = services.compute_km_deviation_pct(
            m.completed_km,
            m.target_km,
            m.plan.interval_km if m.plan else None,
            m.plan.plan_type if m.plan else None,
        )
        rows.append([
            str(m.id),
            m.vehicle.code if m.vehicle else "",
            m.vehicle.name if m.vehicle else "",
            m.plan.name if m.plan else "",
            hedef,
            str(m.km_at_open),
            str(m.completed_km) if m.completed_km is not None else "",
            f"%{deviation_pct:g}" if deviation_pct is not None else "",
            _STATUS_TR.get(m.status, m.status),
            f"{done}/{total}",
            m.assigned_to.full_name if m.assigned_to else "",
            _dt(m.created_at),
            _dt(m.started_at),
            _dt(m.completed_at),
        ])
    return headers, rows


def item_completion_rows(
    maintenance_id: int, vehicle_code: str, plan_name: str, items: list,
) -> tuple[list[str], list[list[str]]]:
    """Tek bir iş emrinin kontrol maddelerini (yalnızca en alt seviye/yaprak
    maddeler — üst başlıklar tek tek işaretlenmez, alt maddeler bitince
    otomatik kilitlenir) kimin/ne zaman tamamladığını ve varsa eklediği
    notu düz bir tabloya çevirir. `items`, `main._build_item_tree` çıktısı
    (iç içe ItemOut listesi) — hiyerarşideki üst başlıklar "Yol" sütununda
    (ör. "ARAÇ İÇİ > Aydınlatma Sistemi") görünür, kendileri ayrı satır
    olarak eklenmez."""
    headers = [
        "İş Emri", "Araç", "Periyot", "Yol", "Madde", "Kart Kodu",
        "Durum", "Tamamlayan", "Tamamlanma Tarihi/Saati",
        "Bulgu/Not", "Notu Ekleyen", "Not Tarihi/Saati",
    ]
    rows: list[list[str]] = []

    def walk(nodes, path: list[str]):
        for it in nodes:
            children = list(it.children)
            if children:
                walk(children, path + [it.name])
                continue
            rows.append([
                str(maintenance_id),
                vehicle_code,
                plan_name,
                " > ".join(path),
                it.name,
                it.card_code or "",
                "Tamamlandı" if it.is_completed else "Bekliyor",
                it.completed_by.full_name if it.completed_by else "",
                _dt(it.completed_at),
                it.exec_note or "",
                it.exec_note_by.full_name if it.exec_note_by else "",
                _dt(it.exec_note_at),
            ])

    walk(items, [])
    return headers, rows


def build_item_completion_export(
    maintenance_id: int, vehicle_code: str, plan_name: str, items: list, fmt: str, title: str,
) -> bytes:
    headers, rows = item_completion_rows(maintenance_id, vehicle_code, plan_name, items)
    if fmt == "xlsx":
        import openpyxl
        from openpyxl.utils import get_column_letter

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Madde Tamamlama"
        ws.append(headers)
        for c in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=c)
            cell.font = excel_utils._HEADER_FONT
            cell.fill = excel_utils._HEADER_FILL
        ws.freeze_panes = "A2"
        for row in rows:
            ws.append(row)
        widths = [10, 12, 16, 34, 30, 14, 14, 20, 20, 34, 20, 20]
        for i, w in enumerate(widths[: len(headers)], start=1):
            ws.column_dimensions[get_column_letter(i)].width = w
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()
    if fmt == "csv":
        return build_csv(headers, rows)
    if fmt == "pdf":
        return build_pdf(headers, rows, title)
    if fmt == "docx":
        return build_docx(headers, rows, title)
    raise ValueError(f"Desteklenmeyen format: {fmt}")


def audit_rows(logs: list) -> tuple[list[str], list[list[str]]]:
    headers = [
        "Tarih", "Kullanıcı", "Aksiyon", "Varlık", "Özet",
        "Eski Değer", "Yeni Değer", "IP", "Tarayıcı",
    ]
    rows = []
    for a in logs:
        rows.append([
            _dt(a.created_at),
            a.actor_name or "",
            _ACTION_TR.get(a.action, a.action),
            _ENTITY_TR.get(a.entity_type, a.entity_type),
            a.summary or "",
            a.old_value or "",
            a.new_value or "",
            a.ip_address or "",
            a.user_agent or "",
        ])
    return headers, rows


# ================================================================= CSV
def build_csv(headers: list[str], rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    # Türkiye'de Excel varsayılan olarak ; ayraçlı CSV bekler
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(headers)
    writer.writerows(rows)
    # Excel'in Türkçe karakterleri doğru göstermesi için UTF-8 BOM eklenir
    return ("﻿" + buf.getvalue()).encode("utf-8")


# ================================================================= PDF
def build_pdf(headers: list[str], rows: list[list[str]], title: str) -> bytes:
    _ensure_fonts()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=12 * mm,
        title=title,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TitleTR", parent=styles["Title"], fontName=_FONT_BOLD, fontSize=16, spaceAfter=4,
    )
    meta_style = ParagraphStyle(
        "MetaTR", parent=styles["Normal"], fontName=_FONT_REGULAR, fontSize=8.5,
        textColor=colors.HexColor("#5B6178"), spaceAfter=12,
    )
    header_style = ParagraphStyle(
        "HeaderTR", fontName=_FONT_BOLD, fontSize=8, leading=10, textColor=colors.white,
    )
    cell_style = ParagraphStyle("CellTR", fontName=_FONT_REGULAR, fontSize=7.5, leading=9.5)

    elements = [
        Paragraph(title, title_style),
        Paragraph(f"Oluşturma: {_dt(datetime.now())} &mdash; {len(rows)} kayıt", meta_style),
    ]

    table_data = [[Paragraph(h, header_style) for h in headers]]
    for row in rows:
        table_data.append([Paragraph(str(c) if c else "-", cell_style) for c in row])

    n = max(len(headers), 1)
    avail_width = landscape(A4)[0] - 24 * mm
    table = Table(table_data, colWidths=[avail_width / n] * n, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1B4DA0")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D8DEE9")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F6F7FB")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    elements.append(table)
    doc.build(elements)
    return buf.getvalue()


# ================================================================= Word (DOCX)
def build_docx(headers: list[str], rows: list[list[str]], title: str) -> bytes:
    doc = Document()
    section = doc.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width, section.page_height = section.page_height, section.page_width
    section.left_margin = Cm(1.2)
    section.right_margin = Cm(1.2)
    section.top_margin = Cm(1.5)
    section.bottom_margin = Cm(1.5)

    doc.add_heading(title, level=1)
    meta = doc.add_paragraph(f"Oluşturma: {_dt(datetime.now())} — {len(rows)} kayıt")
    if meta.runs:
        meta.runs[0].font.size = Pt(9)

    table = doc.add_table(rows=1, cols=max(len(headers), 1))
    table.style = "Light Grid Accent 1"
    hdr_cells = table.rows[0].cells
    for i, htext in enumerate(headers):
        hdr_cells[i].text = htext
        for p in hdr_cells[i].paragraphs:
            for r in p.runs:
                r.font.bold = True
                r.font.size = Pt(9)

    for row in rows:
        cells = table.add_row().cells
        for i, val in enumerate(row):
            cells[i].text = str(val) if val else "-"
            for p in cells[i].paragraphs:
                for r in p.runs:
                    r.font.size = Pt(8.5)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ================================================================= Üst düzey dispatch
def build_maintenances_export(maintenances: list, fmt: str, title: str) -> bytes:
    if fmt == "xlsx":
        return excel_utils.build_maintenances_workbook(maintenances, title=title)
    headers, rows = maintenance_rows(maintenances)
    if fmt == "csv":
        return build_csv(headers, rows)
    if fmt == "pdf":
        return build_pdf(headers, rows, title)
    if fmt == "docx":
        return build_docx(headers, rows, title)
    raise ValueError(f"Desteklenmeyen format: {fmt}")


def build_audit_export(logs: list, fmt: str, title: str = "İşlem Kayıtları") -> bytes:
    headers, rows = audit_rows(logs)
    if fmt == "xlsx":
        import openpyxl
        from openpyxl.utils import get_column_letter

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "İşlem Kayıtları"
        ws.append(headers)
        for c in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=c)
            cell.font = excel_utils._HEADER_FONT
            cell.fill = excel_utils._HEADER_FILL
        ws.freeze_panes = "A2"
        for row in rows:
            ws.append(row)
        widths = [18, 18, 14, 14, 34, 24, 24, 16, 30]
        for i, w in enumerate(widths[: len(headers)], start=1):
            ws.column_dimensions[get_column_letter(i)].width = w
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()
    if fmt == "csv":
        return build_csv(headers, rows)
    if fmt == "pdf":
        return build_pdf(headers, rows, title)
    if fmt == "docx":
        return build_docx(headers, rows, title)
    raise ValueError(f"Desteklenmeyen format: {fmt}")


def filename_for(base: str, fmt: str) -> str:
    return f"{base}.{fmt}"


# ================================================================= Faz 18: KPI raporu (Excel)
def build_kpi_workbook(report: dict, title: str = "KPI Raporu") -> bytes:
    """services.compute_kpi_report()'ün döndürdüğü sözlükten çok sayfalı bir
    Excel raporu üretir: Özet, Araç Modeli, Araçlar, Personel, Vardiya."""
    import openpyxl
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()

    def _header_row(ws, headers: list[str]) -> None:
        ws.append(headers)
        for c in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=c)
            cell.font = excel_utils._HEADER_FONT
            cell.fill = excel_utils._HEADER_FILL
        ws.freeze_panes = "A2"

    def _widths(ws, widths: list[int]) -> None:
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w

    date_from = report["date_from"].strftime("%d.%m.%Y") if report["date_from"] else "Tüm zamanlar"
    date_to = report["date_to"].strftime("%d.%m.%Y") if report["date_to"] else "Tüm zamanlar"

    # ---- Özet ----
    ws = wb.active
    ws.title = "Özet"
    ws.append([title])
    ws.append([f"Tarih Aralığı: {date_from} - {date_to}"])
    ws.append([])
    c = report["completion"]
    ws.append(["Tamamlanma Oranı ve Süresi"])
    ws.append(["Aralıkta Açılan İş Emri", c["opened_in_range"]])
    ws.append(["Bunlardan Tamamlanan", c["completed_of_opened"]])
    ws.append(["Tamamlanma Oranı (%)", c["completion_rate_pct"]])
    ws.append(["Aralıkta Tamamlanan (toplam)", c["completed_in_range"]])
    ws.append(["Ortalama Tamamlanma Süresi (gün)", c["avg_completion_days"] if c["avg_completion_days"] is not None else "-"])
    ws.append(["Toleransta Tamamlanan Bakım", c["on_time_count"]])
    ws.append(["Değerlendirilen Tamamlanan Bakım (periyodu belirsiz olanlar hariç)", c["on_time_evaluated"]])
    ws.append(["Zamanında Bakım Oranı (%)", c["on_time_rate_pct"] if c["on_time_rate_pct"] is not None else "-"])
    ws.append([])
    cr = report["critical"]
    ws.append(["Geciken/Kritik Bakımlar (güncel durum, tarih aralığından bağımsız)"])
    ws.append(["Açık İş Emri (toplam)", cr["open_total"]])
    ws.append(["Kritik", cr["urgent_count"]])
    ws.append(["Çok Kritik", cr["very_critical_count"]])
    ws.append(["Gecikmiş (hedefi aşmış)", cr["overdue_count"]])
    for row in (4, 14):
        ws.cell(row=row, column=1).font = excel_utils._HEADER_FONT
    ws.cell(row=1, column=1).font = excel_utils._HEADER_FONT
    _widths(ws, [38, 16])

    # ---- Araç Modeli ----
    ws2 = wb.create_sheet("Araç Modeli")
    _header_row(ws2, ["Araç Modeli", "Açılan", "Tamamlanan", "Şu An Açık"])
    for row in report["by_vehicle_model"]:
        ws2.append([row["vehicle_model"], row["opened"], row["completed"], row["open_now"]])
    _widths(ws2, [26, 12, 12, 12])

    # ---- Araçlar ----
    ws3 = wb.create_sheet("Araçlar")
    _header_row(ws3, ["Araç Kodu", "Araç Adı", "Model", "Açılan", "Tamamlanan", "Şu An Açık"])
    for row in report["vehicles"]:
        v = row["vehicle"]
        ws3.append([v.code, v.name, v.model, row["opened"], row["completed"], row["open_now"]])
    _widths(ws3, [14, 22, 20, 12, 12, 12])

    # ---- Personel ----
    ws4 = wb.create_sheet("Personel")
    _header_row(
        ws4,
        [
            "Ad Soyad", "Rol", "Vardiya", "Tamamlanan Madde Sayısı",
            "Tamamlanan İş Emri Sayısı", "Ortalama Tamamlanma Süresi (gün)",
        ],
    )
    for row in report["by_person"]:
        u = row["user"]
        ws4.append([
            u.full_name, u.role, row["shift_name"] or "-",
            row["items_completed_in_range"], row["maintenances_completed_in_range"],
            row["avg_completion_days"] if row["avg_completion_days"] is not None else "-",
        ])
    _widths(ws4, [22, 14, 16, 20, 20, 26])

    # ---- Vardiya ----
    ws5 = wb.create_sheet("Vardiya")
    _header_row(ws5, ["Vardiya", "Tamamlanan Madde Sayısı"])
    for row in report["by_shift"]:
        ws5.append([row["shift_name"], row["items_completed_in_range"]])
    _widths(ws5, [20, 20])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ================================================================= Bakım Akışı (Bakımlar sayfası) — çok sayfalı Excel
# Durum -> sekme adı ve öncelik sırası (en acil önce) — Bakımlar sayfasındaki
# MaintenancesPage.tsx FLOW_STATUS_PRIORITY ile birebir aynı sıra.
_FLOW_STATUS_TR = {
    "gecikti": "Bakım Gecikti",
    "planlandi": "Planlandı",
    "yaklasiyor": "Yaklaşıyor",
    "planlanacak": "Planlanmayı Bekleyen",
}
_FLOW_STATUS_ORDER = ["gecikti", "planlandi", "yaklasiyor", "planlanacak"]


def build_flow_export(items: list[dict], title: str = "Bakım Akışı") -> bytes:
    """Bakımlar sayfasındaki 'Bakım Akışı' listesini (bkz. main.py
    _compute_upcoming_merged / GET /vehicles/alerts — Başlatılanlar VE
    Başlatılmayı Bekleyenler sekmelerinin ortak veri kaynağı) çok sayfalı
    bir Excel dosyasına aktarır. Kullanıcı isteği: "önemli olanları da
    sheet sheet ayır" — her durum kategorisi (Bakım Gecikti/Planlandı/
    Yaklaşıyor/Planlanmayı Bekleyen) KENDİ sayfasında, en acil olan en
    önce; boş kategoriler için sayfa oluşturulmaz. `items` çağıran tarafça
    (bkz. main.py export_vehicle_alerts) sayfadaki aktif periyot/araç
    tipi/sekme filtreleriyle ÖNCEDEN filtrelenmiş olarak gelir."""
    import openpyxl
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    headers = ["Araç Kodu", "Araç Adı", "Model", "Periyot", "Hedef", "Kalan", "İş Emri", "Vardiya"]

    def _fmt_target(item: dict) -> str:
        if item.get("target_date"):
            d = item["target_date"]
            return d.strftime("%d.%m.%Y") if hasattr(d, "strftime") else str(d)
        km = item.get("target_km") or 0
        return f"{km:,}".replace(",", ".") + " km"

    def _fmt_remaining(item: dict) -> str:
        r = item.get("remaining_km") or 0
        unit = "gün" if item.get("target_date") else "km"
        abs_r = f"{abs(r):,}".replace(",", ".") if unit == "km" else str(abs(r))
        return f"{abs_r} {unit} geçti" if r < 0 else f"{abs_r} {unit} kaldı"

    grouped: dict[str, list[dict]] = {}
    for it in items:
        key = it.get("status") or "diger"
        grouped.setdefault(key, []).append(it)

    sheet_keys = [k for k in _FLOW_STATUS_ORDER if grouped.get(k)]
    if grouped.get("diger"):
        sheet_keys.append("diger")
    if not sheet_keys:
        sheet_keys = [_FLOW_STATUS_ORDER[0]]  # hiç kayıt yoksa bile en az bir (boş) sayfa gerekir

    first = True
    for key in sheet_keys:
        sheet_title = _FLOW_STATUS_TR.get(key, "Diğer")
        if first:
            ws = wb.active
            ws.title = sheet_title
            first = False
        else:
            ws = wb.create_sheet(sheet_title)
        ws.append(headers)
        for c in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=c)
            cell.font = excel_utils._HEADER_FONT
            cell.fill = excel_utils._HEADER_FILL
        ws.freeze_panes = "A2"
        for it in grouped.get(key, []):
            v = it["vehicle"]
            ws.append([
                v.code, v.name, v.model or "-",
                it.get("plan_name", ""), _fmt_target(it), _fmt_remaining(it),
                f"#{it['maintenance_id']}" if it.get("maintenance_id") else "Henüz yok",
                it.get("assigned_shift_name") or "-",
            ])
        for i, w in enumerate([12, 22, 18, 22, 16, 16, 12, 16], start=1):
            ws.column_dimensions[get_column_letter(i)].width = w

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
