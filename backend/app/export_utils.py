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
    headers = [
        "İş Emri", "Araç Kodu", "Araç Adı", "Periyot", "Hedef KM", "Açılış KM",
        "Durum", "İlerleme", "Atanan", "Oluşturma", "Başlangıç", "Bitiş",
    ]
    rows = []
    for m in maintenances:
        done, total = excel_utils._count_leaves(list(m.items))
        rows.append([
            str(m.id),
            m.vehicle.code if m.vehicle else "",
            m.vehicle.name if m.vehicle else "",
            m.plan.name if m.plan else "",
            str(m.target_km),
            str(m.km_at_open),
            _STATUS_TR.get(m.status, m.status),
            f"{done}/{total}",
            m.assigned_to.full_name if m.assigned_to else "",
            _dt(m.created_at),
            _dt(m.started_at),
            _dt(m.completed_at),
        ])
    return headers, rows


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
