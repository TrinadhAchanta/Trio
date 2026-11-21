"""Turn Markdown (from chat replies, research reports or generated documents) into office files.

Markdown is parsed once into a small block model (headings, paragraphs with bold/italic/code runs,
lists, code blocks, tables, quotes, rules) and rendered to:
  - DOCX (python-docx), PDF (reportlab), PPTX (python-pptx: one slide per H1/H2 section),
  - XLSX (openpyxl: every Markdown table becomes a sheet; without tables, one row per block),
  - Markdown (unchanged).
"""

import io
import os
import re
from dataclasses import dataclass, field

from markdown_it import MarkdownIt

FORMATS = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "md": "text/markdown; charset=utf-8",
}


@dataclass
class Run:
    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False


@dataclass
class Block:
    kind: str  # heading | para | bullet | ordered | code | table | quote | hr
    level: int = 0
    runs: list[Run] = field(default_factory=list)
    items: list[list[Run]] = field(default_factory=list)
    text: str = ""
    rows: list[list[str]] = field(default_factory=list)

    def plain(self) -> str:
        return "".join(r.text for r in self.runs)


def _inline(tok) -> list[Run]:
    runs: list[Run] = []
    bold = italic = False
    for c in tok.children or []:
        if c.type == "strong_open":
            bold = True
        elif c.type == "strong_close":
            bold = False
        elif c.type == "em_open":
            italic = True
        elif c.type == "em_close":
            italic = False
        elif c.type == "code_inline":
            runs.append(Run(c.content, bold, italic, True))
        elif c.type in ("text", "html_inline"):
            runs.append(Run(c.content, bold, italic))
        elif c.type in ("softbreak", "hardbreak"):
            runs.append(Run(" " if c.type == "softbreak" else "\n", bold, italic))
        elif c.type == "image":
            runs.append(Run(f"[image: {c.content or c.attrs.get('src', '')}]", bold, italic))
    return runs


def parse(md: str) -> list[Block]:
    tokens = MarkdownIt("commonmark").enable("table").parse(md)
    blocks: list[Block] = []
    i = 0
    list_stack: list[Block] = []
    quote = 0
    while i < len(tokens):
        t = tokens[i]
        if t.type == "heading_open":
            blocks.append(Block("heading", level=int(t.tag[1]), runs=_inline(tokens[i + 1])))
            i += 3
            continue
        if t.type in ("bullet_list_open", "ordered_list_open"):
            b = Block("bullet" if t.type == "bullet_list_open" else "ordered", level=len(list_stack))
            list_stack.append(b)
            blocks.append(b)
        elif t.type in ("bullet_list_close", "ordered_list_close"):
            list_stack.pop()
        elif t.type == "inline" and list_stack:
            list_stack[-1].items.append(_inline(t))
        elif t.type == "blockquote_open":
            quote += 1
        elif t.type == "blockquote_close":
            quote -= 1
        elif t.type == "inline":
            blocks.append(Block("quote" if quote else "para", runs=_inline(t)))
        elif t.type in ("fence", "code_block"):
            blocks.append(Block("code", text=t.content.rstrip("\n"), level=0))
        elif t.type == "hr":
            blocks.append(Block("hr"))
        elif t.type == "table_open":
            rows: list[list[str]] = []
            j = i + 1
            row: list[str] = []
            while tokens[j].type != "table_close":
                if tokens[j].type == "tr_open":
                    row = []
                elif tokens[j].type == "tr_close":
                    rows.append(row)
                elif tokens[j].type == "inline":
                    row.append(tokens[j].content)
                j += 1
            blocks.append(Block("table", rows=rows))
            i = j
        i += 1
    return blocks


def title_from(md: str, fallback: str = "Document") -> str:
    m = re.search(r"^\s*#{1,2}\s+(.+)$", md, re.M)
    return (m.group(1).strip() if m else fallback)[:120]


def safe_filename(title: str, ext: str) -> str:
    base = re.sub(r"[^A-Za-z0-9 _.-]+", "", title).strip().replace(" ", "-")[:60] or "document"
    return f"{base}.{ext}"


# ---------- DOCX ----------
def to_docx(blocks: list[Block], title: str) -> bytes:
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    doc.core_properties.title = title
    doc.core_properties.author = "Trio"

    def add_runs(p, runs: list[Run]):
        for r in runs:
            run = p.add_run(r.text)
            run.bold, run.italic = r.bold or None, r.italic or None
            if r.code:
                run.font.name = "Courier New"

    for b in blocks:
        if b.kind == "heading":
            add_runs(doc.add_heading(level=min(b.level, 4)), b.runs)
        elif b.kind in ("para", "quote"):
            add_runs(doc.add_paragraph(style="Intense Quote" if b.kind == "quote" else None), b.runs)
        elif b.kind in ("bullet", "ordered"):
            style = "List Bullet" if b.kind == "bullet" else "List Number"
            if b.level:
                style += f" {min(b.level + 1, 3)}"
            for item in b.items:
                add_runs(doc.add_paragraph(style=style), item)
        elif b.kind == "code":
            p = doc.add_paragraph()
            run = p.add_run(b.text)
            run.font.name, run.font.size = "Courier New", Pt(9)
        elif b.kind == "table" and b.rows:
            cols = max(len(r) for r in b.rows)
            table = doc.add_table(rows=len(b.rows), cols=cols)
            table.style = "Table Grid"
            for ri, row in enumerate(b.rows):
                for ci, cell in enumerate(row):
                    table.cell(ri, ci).text = cell
                    if ri == 0:
                        for r in table.cell(ri, ci).paragraphs[0].runs:
                            r.bold = True
        elif b.kind == "hr":
            doc.add_paragraph("_" * 40)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------- PDF ----------
_FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", "/usr/local/share/fonts"]
_fonts: tuple[str, str, str] | None = None


def _pdf_fonts() -> tuple[str, str, str]:
    """Use DejaVu (wide Unicode coverage) when installed; otherwise the built-in Latin-1 fonts."""
    global _fonts
    if _fonts:
        return _fonts
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for d in _FONT_DIRS:
        reg, bold, mono = (os.path.join(d, f) for f in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSansMono.ttf"))
        if all(os.path.exists(p) for p in (reg, bold, mono)):
            pdfmetrics.registerFont(TTFont("TrioSans", reg))
            pdfmetrics.registerFont(TTFont("TrioSans-Bold", bold))
            pdfmetrics.registerFont(TTFont("TrioMono", mono))
            pdfmetrics.registerFontFamily("TrioSans", normal="TrioSans", bold="TrioSans-Bold", italic="TrioSans",
                                          boldItalic="TrioSans-Bold")
            _fonts = ("TrioSans", "TrioSans-Bold", "TrioMono")
            return _fonts
    _fonts = ("Helvetica", "Helvetica-Bold", "Courier")
    return _fonts


def _xml(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _markup(runs: list[Run], mono: str) -> str:
    out = []
    for r in runs:
        t = _xml(r.text).replace("\n", "<br/>")
        if r.code:
            t = f'<font face="{mono}">{t}</font>'
        if r.italic:
            t = f"<i>{t}</i>"
        if r.bold:
            t = f"<b>{t}</b>"
        out.append(t)
    return "".join(out)


def to_pdf(blocks: list[Block], title: str) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import HRFlowable, ListFlowable, ListItem, Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table

    sans, bold, mono = _pdf_fonts()
    ss = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=ss["BodyText"], fontName=sans, fontSize=10.5, leading=14)
    heads = {lvl: ParagraphStyle(f"h{lvl}", parent=ss[f"Heading{min(lvl, 4)}"], fontName=bold) for lvl in range(1, 7)}
    quote = ParagraphStyle("quote", parent=body, leftIndent=12, textColor=colors.HexColor("#555555"))
    code = ParagraphStyle("code", parent=body, fontName=mono, fontSize=8.5, leading=11, backColor=colors.HexColor("#f4f4f4"))
    story = []
    for b in blocks:
        if b.kind == "heading":
            story.append(Paragraph(_markup(b.runs, mono), heads[b.level]))
        elif b.kind in ("para", "quote"):
            story.append(Paragraph(_markup(b.runs, mono), quote if b.kind == "quote" else body))
        elif b.kind in ("bullet", "ordered"):
            items = [ListItem(Paragraph(_markup(it, mono), body)) for it in b.items]
            story.append(ListFlowable(items, bulletType="bullet" if b.kind == "bullet" else "1",
                                      leftIndent=12 + 12 * b.level, bulletFontName=sans))
        elif b.kind == "code":
            story.append(Preformatted(b.text, code))
        elif b.kind == "table" and b.rows:
            cols = max(len(r) for r in b.rows)
            data = [[Paragraph(_xml(c), body) for c in r] + [""] * (cols - len(r)) for r in b.rows]
            t = Table(data, repeatRows=1)
            t.setStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")), ("VALIGN", (0, 0), (-1, -1), "TOP")])
            story.append(t)
        elif b.kind == "hr":
            story.append(HRFlowable(width="100%", color=colors.lightgrey))
        story.append(Spacer(1, 4))
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, title=title, author="Trio", leftMargin=20 * mm, rightMargin=20 * mm,
                      topMargin=18 * mm, bottomMargin=18 * mm).build(story or [Paragraph(_xml(title), body)])
    return buf.getvalue()


# ---------- PPTX ----------
def to_pptx(blocks: list[Block], title: str) -> bytes:
    from pptx import Presentation
    from pptx.util import Pt

    prs = Presentation()
    slides: list[tuple[str, list[str]]] = []
    for b in blocks:
        if b.kind == "heading" and b.level <= 2:
            slides.append((b.plain(), []))
            continue
        if not slides:
            slides.append((title, []))
        lines = slides[-1][1]
        if b.kind == "heading":
            lines.append(b.plain())
        elif b.kind in ("para", "quote"):
            lines.append(b.plain())
        elif b.kind in ("bullet", "ordered"):
            lines += ["".join(r.text for r in it) for it in b.items]
        elif b.kind == "table":
            lines += [" | ".join(r) for r in b.rows]
        elif b.kind == "code":
            lines += b.text.splitlines()[:12]
    if not slides:
        slides = [(title, [])]
    for i, (head, lines) in enumerate(slides):
        layout = prs.slide_layouts[0 if i == 0 and not lines else 1]
        s = prs.slides.add_slide(layout)
        s.shapes.title.text = head
        if lines and len(s.placeholders) > 1:
            tf = s.placeholders[1].text_frame
            tf.text = lines[0][:300]
            for line in lines[1:14]:
                p = tf.add_paragraph()
                p.text = line[:300]
            for p in tf.paragraphs:
                for r in p.runs:
                    r.font.size = Pt(18 if len(lines) <= 6 else 14)
    prs.core_properties.title = title
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


# ---------- XLSX ----------
def to_xlsx(blocks: list[Block], title: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    tables = [b for b in blocks if b.kind == "table" and b.rows]
    if tables:
        wb.remove(wb.active)
        for n, t in enumerate(tables, 1):
            ws = wb.create_sheet(f"Table {n}")
            for row in t.rows:
                ws.append([_cell_value(c) for c in row])
            for c in ws[1]:
                c.font = Font(bold=True)
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = min(60, max(10, *(len(str(c.value or "")) for c in col)))
    else:
        ws = wb.active
        ws.title = "Document"
        for b in blocks:
            if b.kind in ("bullet", "ordered"):
                for it in b.items:
                    ws.append(["".join(r.text for r in it)])
            elif b.kind == "code":
                for line in b.text.splitlines():
                    ws.append([line])
            elif b.kind != "hr":
                ws.append([b.plain()])
                if b.kind == "heading":
                    ws.cell(ws.max_row, 1).font = Font(bold=True)
        ws.column_dimensions["A"].width = 100
    wb.properties.title = title
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_NUM = re.compile(r"^-?\d+(?:[.,]\d+)?$")


def _cell_value(s: str):
    t = s.strip()
    if t.startswith("="):
        return "'" + t  # never write formulas from model output
    if _NUM.match(t):
        try:
            return float(t.replace(",", ".")) if any(ch in t for ch in ".,") else int(t)
        except ValueError:
            return t
    return t


def render(md: str, fmt: str, title: str | None = None) -> tuple[bytes, str, str]:
    """Returns (content, media_type, filename)."""
    if fmt not in FORMATS:
        raise ValueError(f"Unsupported format {fmt}")
    title = title or title_from(md)
    if fmt == "md":
        data = md.encode()
    else:
        blocks = parse(md)
        data = {"docx": to_docx, "pdf": to_pdf, "pptx": to_pptx, "xlsx": to_xlsx}[fmt](blocks, title)
    return data, FORMATS[fmt], safe_filename(title, fmt)
