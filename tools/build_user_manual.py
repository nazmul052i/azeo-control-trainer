"""Build the user manual's searchable PDF from its maintained Markdown source.

Usage: <configured-python> tools/build_user_manual.py
Dependencies: reportlab, markdown-it-py, Pillow and pypdf. PyMuPDF is used only
for the optional visual/geometry audit (--audit). No application is launched.
"""
from __future__ import annotations

import argparse
from html import escape
import json
from pathlib import Path
import re
import textwrap

from markdown_it import MarkdownIt
from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, Image, KeepTogether, PageBreak,
    PageTemplate, Paragraph, Spacer, Table, TableStyle, XPreformatted,
)
from reportlab.platypus.tableofcontents import TableOfContents

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "USER_MANUAL.md"
DEFAULT_OUTPUT = ROOT / "output" / "pdf" / "Azeo_Control_Trainer_User_Manual.pdf"
PAGE_W, PAGE_H = A4
MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = 49, 55, 48
WIDTH = PAGE_W - 2 * MARGIN_X
BLUE = colors.HexColor("#004487")
NAVY = colors.HexColor("#102C49")
INK = colors.HexColor("#263746")
MUTED = colors.HexColor("#586A79")
PALE = colors.HexColor("#EFF5FA")
BORDER = colors.HexColor("#D5E0E9")


def register_fonts():
    candidates = [
        (Path("C:/Windows/Fonts"),
         ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf", "segoeuiz.ttf")),
        (Path("/usr/share/fonts/truetype/dejavu"),
         ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf", "DejaVuSans-BoldOblique.ttf")),
    ]
    for directory, filenames in candidates:
        if all((directory / filename).is_file() for filename in filenames):
            for name, filename in zip(("Manual", "ManualBold", "ManualItalic", "ManualBoldItalic"), filenames):
                pdfmetrics.registerFont(TTFont(name, str(directory / filename)))
            pdfmetrics.registerFontFamily("Manual", normal="Manual", bold="ManualBold",
                                         italic="ManualItalic", boldItalic="ManualBoldItalic")
            break
    else:
        raise RuntimeError("Install Segoe UI or DejaVu Sans for reproducible manual typography")
    mono = Path("C:/Windows/Fonts/consola.ttf")
    if not mono.exists():
        mono = Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")
    pdfmetrics.registerFont(TTFont("ManualMono", str(mono)))


def styles():
    base = dict(fontName="Manual", fontSize=10, leading=13.7, textColor=INK,
                spaceAfter=5, splitLongWords=True, allowWidows=0, allowOrphans=0)
    body = ParagraphStyle("Body", **base)
    return {
        "body": body,
        "chapter": ParagraphStyle("Chapter", parent=body, fontName="ManualBold",
                                  fontSize=23, leading=28, textColor=BLUE,
                                  spaceBefore=3, spaceAfter=17, keepWithNext=True),
        "section": ParagraphStyle("Section", parent=body, fontName="ManualBold",
                                  fontSize=13, leading=17, textColor=BLUE,
                                  spaceBefore=10, spaceAfter=6, keepWithNext=True),
        "subsection": ParagraphStyle("Subsection", parent=body, fontName="ManualBold",
                                     fontSize=10.5, leading=15, spaceBefore=8,
                                     keepWithNext=True),
        "bullet": ParagraphStyle("Bullet", parent=body, leftIndent=17, firstLineIndent=0,
                                 bulletIndent=1, bulletFontName="Manual",
                                 bulletFontSize=9.5, spaceAfter=3),
        "table": ParagraphStyle("Cell", parent=body, fontSize=9, leading=12.2, spaceAfter=0),
        "tablehead": ParagraphStyle("CellHead", parent=body, fontName="ManualBold",
                                    fontSize=9, leading=12.2, textColor=colors.white, spaceAfter=0),
        "caption": ParagraphStyle("Caption", parent=body, fontSize=8.5, leading=12,
                                   textColor=MUTED, spaceBefore=7, spaceAfter=12),
        "note": ParagraphStyle("Note", parent=body, fontSize=9.5, leading=14,
                                borderPadding=10, backColor=PALE, borderColor=BORDER,
                                borderWidth=.6, spaceBefore=6, spaceAfter=12),
        "code": ParagraphStyle("Code", parent=body, fontName="ManualMono",
                                fontSize=8.2, leading=11.4, borderPadding=10,
                                backColor=PALE, spaceBefore=3, spaceAfter=12),
    }


def slug(text):
    return re.sub(r"[^\w\s-]", "", text.lower()).strip().replace(" ", "-")


class ManualDoc(BaseDocTemplate):
    def __init__(self, path, *, edition, issued, baseline, product_title=None,
                 document_id="AZEO-UM-001"):
        self.edition, self.issued, self.baseline = edition, issued, baseline
        self.product_title, self.document_id = product_title, document_id
        super().__init__(str(path), pagesize=A4, leftMargin=MARGIN_X,
                         rightMargin=MARGIN_X, topMargin=MARGIN_TOP,
                         bottomMargin=MARGIN_BOTTOM,
                         title=f"{product_title or 'Azeo Control Trainer'} - User Manual",
                         author="Azeo Control Trainer",
                         subject=f"Engineering and operator training - Edition {edition}",
                         pageCompression=1)
        self.chapter = "Contents"
        self.addPageTemplates(PageTemplate(
            id="manual", frames=Frame(MARGIN_X, MARGIN_BOTTOM, WIDTH,
                                       PAGE_H - MARGIN_TOP - MARGIN_BOTTOM,
                                       leftPadding=0, rightPadding=0,
                                       topPadding=0, bottomPadding=0),
            onPage=self.page_start, onPageEnd=self.page_end))

    def page_start(self, canvas, doc):
        if doc.page == 2:
            doc.chapter = "Contents"
        if doc.page != 1:
            return
        if self.product_title:
            self.product_cover(canvas)
            return
        canvas.setFillColor(NAVY)
        canvas.rect(0, 285, PAGE_W, PAGE_H - 285, fill=1, stroke=0)
        canvas.setFillColor(BLUE)
        canvas.rect(0, 277, PAGE_W, 8, fill=1, stroke=0)
        canvas.setFillColor(colors.HexColor("#A9CDEE"))
        canvas.setFont("ManualBold", 11)
        canvas.drawString(MARGIN_X, PAGE_H - 74, "AZEO  /  PRODUCT DOCUMENTATION")
        canvas.setFillColor(colors.white)
        canvas.setFont("ManualBold", 35)
        canvas.drawString(MARGIN_X, PAGE_H - 162, "Azeo Control")
        canvas.drawString(MARGIN_X, PAGE_H - 205, "Trainer")
        canvas.setFont("Manual", 26)
        canvas.drawString(MARGIN_X, PAGE_H - 271, "User Manual")
        canvas.setFont("Manual", 12)
        canvas.setFillColor(colors.HexColor("#D1E4F4"))
        canvas.drawString(MARGIN_X, PAGE_H - 310, "Engineering and operator training")
        canvas.setStrokeColor(colors.HexColor("#507394"))
        canvas.line(MARGIN_X, 377, PAGE_W - MARGIN_X, 377)
        canvas.setFont("Manual", 10.5)
        canvas.drawString(MARGIN_X, 352, "Project setup  /  Control engineering  /  HMI authoring")
        canvas.drawString(MARGIN_X, 330, "Live operation  /  Simulation  /  Instructor review")
        canvas.setFillColor(INK)
        canvas.setFont("ManualBold", 10.5)
        canvas.drawString(MARGIN_X, 238, "SIX APPLICATIONS. ONE CONNECTED WORKFLOW.")
        canvas.setFont("Manual", 10.5)
        for i, label in enumerate(("Azeo Explorer", "Azeo Control Designer", "Azeo Graphics Designer",
                                   "Azeo Operator Station", "Azeo Simulation Workbench",
                                   "Azeo PA Designer")):
            x, y = MARGIN_X + (i % 2) * 250, 207 - (i // 2) * 24
            canvas.setFillColor(BLUE)
            canvas.circle(x + 3, y + 3, 2.4, fill=1, stroke=0)
            canvas.setFillColor(INK)
            canvas.drawString(x + 15, y, label)
        canvas.setFillColor(MUTED)
        canvas.setFont("Manual", 9)
        canvas.drawString(MARGIN_X, 97,
                          f"Edition {self.edition}  |  {self.issued}  |  Baseline {self.baseline}")
        canvas.drawString(MARGIN_X, 78, "AZEO-UM-001  |  Illustrated procedures and reference")

    def product_cover(self, canvas):
        canvas.setFillColor(NAVY)
        canvas.rect(0, 255, PAGE_W, PAGE_H - 255, fill=1, stroke=0)
        canvas.setFillColor(BLUE)
        canvas.rect(0, 247, PAGE_W, 8, fill=1, stroke=0)
        canvas.setFont("ManualBold", 11)
        canvas.setFillColor(colors.HexColor("#A9CDEE"))
        canvas.drawString(MARGIN_X, PAGE_H - 74, "AZEO  /  PRODUCT DOCUMENTATION")
        canvas.setFillColor(colors.white)
        canvas.setFont("ManualBold", 27)
        brand, application = self.product_title.split(" ", 1)
        canvas.drawString(MARGIN_X, PAGE_H - 155, brand)
        canvas.drawString(MARGIN_X, PAGE_H - 197, application)
        canvas.setFont("Manual", 22)
        canvas.drawString(MARGIN_X, PAGE_H - 266, "User Manual")
        canvas.setFont("Manual", 11)
        canvas.setFillColor(colors.HexColor("#D1E4F4"))
        canvas.drawString(MARGIN_X, PAGE_H - 309,
                          "Illustrated procedures, checks and reference")
        canvas.setFillColor(INK)
        canvas.setFont("ManualBold", 11)
        canvas.drawString(MARGIN_X, 205, "START WITH THE TASK YOU NEED")
        canvas.setFont("Manual", 10)
        canvas.drawString(MARGIN_X, 178, "Use the contents and searchable bookmarks to find a workflow.")
        canvas.drawString(MARGIN_X, 156, "Screenshots show Azeo widgets in a training context.")
        canvas.setFillColor(MUTED)
        canvas.setFont("Manual", 9)
        canvas.drawString(MARGIN_X, 96,
                          f"Edition {self.edition}  |  {self.issued}  |  Baseline {self.baseline}")
        canvas.drawString(MARGIN_X, 76, self.document_id)

    def page_end(self, canvas, doc):
        if doc.page == 1:
            return
        canvas.saveState()
        canvas.setStrokeColor(BORDER)
        canvas.setLineWidth(.6)
        canvas.line(MARGIN_X, PAGE_H - 37, PAGE_W - MARGIN_X, PAGE_H - 37)
        canvas.setFont("ManualBold", 8)
        canvas.setFillColor(BLUE)
        canvas.drawString(MARGIN_X, PAGE_H - 26, "AZEO  /  USER MANUAL")
        canvas.setFont("Manual", 7.6)
        canvas.setFillColor(MUTED)
        label = doc.chapter
        while pdfmetrics.stringWidth(label, "Manual", 7.6) > WIDTH - 155:
            label = label[:-4] + "..."
        canvas.drawRightString(PAGE_W - MARGIN_X, PAGE_H - 26, label)
        canvas.line(MARGIN_X, 34, PAGE_W - MARGIN_X, 34)
        canvas.setFont("Manual", 8)
        canvas.drawString(MARGIN_X, 21,
                          f"{self.document_id}  |  Edition {self.edition}  |  {self.issued}")
        canvas.drawRightString(PAGE_W - MARGIN_X, 21, str(doc.page))
        canvas.restoreState()

    def afterFlowable(self, item):
        if not hasattr(item, "manual_heading"):
            return
        level, text = item.manual_heading
        key = slug(text)
        self.canv.bookmarkPage(key)
        self.canv.addOutlineEntry(text, key, level=level, closed=level == 0)
        if level == 0:
            self.chapter = text
            self.notify("TOCEntry", (0, text, self.page, key))


def inline(token, *, links=True):
    if token.children is None:
        return escape(token.content)
    out = []
    for child in token.children:
        if child.type == "text":
            out.append(escape(child.content))
        elif child.type == "code_inline":
            out.append(f'<font name="ManualMono" size="8.8">{escape(child.content)}</font>')
        elif child.type == "strong_open":
            out.append("<b>")
        elif child.type == "strong_close":
            out.append("</b>")
        elif child.type == "em_open":
            out.append("<i>")
        elif child.type == "em_close":
            out.append("</i>")
        elif child.type in {"softbreak", "hardbreak"}:
            out.append(" " if child.type == "softbreak" else "<br/>")
        elif child.type == "link_open" and links:
            target = child.attrGet("href")
            if target.startswith("#") or target.startswith(("https://", "http://")):
                out.append(f'<link href="{escape(target, quote=True)}" color="#004487">')
            else:
                out.append('<font color="#004487">')
        elif child.type == "link_close" and links:
            # Local Markdown guides are named references in print, not broken
            # file links tied to the builder's workstation directory.
            opening = next((text for text in reversed(out) if text.startswith(("<link ", '<font color='))), "")
            out.append("</link>" if opening.startswith("<link ") else "</font>")
    return "".join(out)


def figure(path, caption, sty):
    with PILImage.open(path) as image:
        w, h = image.size
    scale = min(WIDTH / w, 420 / h)
    image = Image(str(path), width=w * scale, height=h * scale)
    image.hAlign = "CENTER"
    return KeepTogether([Spacer(1, 4), image, Paragraph(escape(caption), sty["caption"])])


def table(rows, sty):
    count = len(rows[0])
    ratios = [0.30, 0.70] if count == 2 else [0.25, 0.36, 0.39]
    if count not in (2, 3):
        ratios = [1 / count] * count
    content = [[Paragraph(cell, sty["tablehead"] if i == 0 else sty["table"])
                for cell in row] for i, row in enumerate(rows)]
    result = Table(content, colWidths=[WIDTH * x for x in ratios], repeatRows=1,
                   hAlign="LEFT", spaceBefore=4, spaceAfter=12)
    result.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLUE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 1), (-1, -1), .35, BORDER),
    ]))
    return result


def build(output, *, source=SOURCE, product_title=None, document_id="AZEO-UM-001"):
    register_fonts()
    sty = styles()
    document = source.read_text(encoding="utf-8")
    # The Markdown edition used to advance while the PDF cover/footer stayed old.
    edition_line = re.search(r"(?m)^Edition ([0-9.]+) - ([^\n\\]+)\\?$", document)
    baseline_line = re.search(r"(?m)^Application baseline: `([^`]+)`\\?$", document)
    if not edition_line or not baseline_line:
        raise ValueError("Manual needs an edition/date and application baseline in its header")
    edition, issued = edition_line.groups()
    baseline = baseline_line[1]
    if product_title is None and (f"| Edition | {edition} |" not in document or f"| Application baseline | {baseline}" not in document):
        raise ValueError("The edition record must agree with the manual header")
    parser = MarkdownIt("commonmark").enable("table")
    tokens = parser.parse(document)
    story = [Spacer(1, 1), PageBreak()]
    story.append(Paragraph("Contents", sty["chapter"]))
    story.append(Paragraph(
        "Select a chapter to jump to it. The PDF bookmarks contain every numbered section. "
        "Use your PDF reader's search to find a command, tag or topic.", sty["body"]))
    toc = TableOfContents()
    toc.levelStyles = [ParagraphStyle("TOC", fontName="Manual", fontSize=11,
                                     leading=23, textColor=BLUE, leftIndent=0,
                                     rightIndent=25, alignment=TA_LEFT)]
    story.append(toc)
    story.append(Spacer(1, 18))
    route = ("<b>Reading routes</b><br/>Use Start with a task first, then the "
             "numbered procedures. Numbers match the complete suite manual."
             if product_title else
             "<b>Reading routes</b><br/>First session: 2-5. Graphics engineering: 6-9. "
             "Operating: 10-12. Instructor preparation: 13-15. "
             "Troubleshooting and reference: 16-19.")
    story.append(Paragraph(route, sty["note"]))
    body_started = False
    lists = []
    in_item = False
    quote_depth = 0
    table_rows = None
    row = None
    index = 0
    chapters = []
    figures = []
    while index < len(tokens):
        token = tokens[index]
        if token.type == "heading_open":
            title_token = tokens[index + 1]
            title = title_token.content
            if token.tag == "h2":
                body_started = True
                story.append(PageBreak())
                p = Paragraph(inline(title_token), sty["chapter"])
                p.manual_heading = (0, title)
                story.append(p)
                chapters.append(title)
            elif body_started and token.tag in {"h3", "h4"}:
                p = Paragraph(inline(title_token), sty["section"] if token.tag == "h3" else sty["subsection"])
                p.manual_heading = (1 if token.tag == "h3" else 2, title)
                story.append(p)
            index += 3
            continue
        if not body_started:
            index += 1
            continue
        if token.type in {"ordered_list_open", "bullet_list_open"}:
            lists.append([token.type == "ordered_list_open", int(token.attrGet("start") or 1)])
        elif token.type in {"ordered_list_close", "bullet_list_close"}:
            lists.pop()
        elif token.type == "list_item_open":
            in_item = True
        elif token.type == "list_item_close":
            in_item = False
        elif token.type == "blockquote_open":
            quote_depth += 1
        elif token.type == "blockquote_close":
            quote_depth -= 1
        elif token.type == "table_open":
            table_rows = []
        elif token.type == "tr_open":
            row = []
        elif token.type in {"td_open", "th_open"}:
            row.append(inline(tokens[index + 1], links=False))
            index += 2
        elif token.type == "tr_close":
            table_rows.append(row)
        elif token.type == "table_close":
            story.append(table(table_rows, sty))
            table_rows = None
        elif token.type == "paragraph_open":
            item = tokens[index + 1]
            image_token = next((child for child in item.children or [] if child.type == "image"), None)
            if image_token is not None:
                filename = image_token.attrGet("src")
                path = source.parent / filename
                if not path.exists():
                    raise FileNotFoundError(path)
                figures.append(filename)
                graphic = figure(path, image_token.content, sty)
                if story and hasattr(story[-1], "manual_heading"):
                    # A nested KeepTogether otherwise lets its preceding
                    # heading remain alone at the foot of a page.
                    graphic = KeepTogether([story.pop(), *graphic._content])
                story.append(graphic)
            else:
                text = inline(item)
                style = sty["note"] if quote_depth else sty["body"]
                bullet = None
                if lists and in_item:
                    ordered, number = lists[-1]
                    bullet = f"{number}." if ordered else "\u2022"
                    lists[-1][1] += 1
                    style = sty["bullet"]
                p = Paragraph(text, style, bulletText=bullet)
                if re.fullmatch(r"<b>[^<]+</b>", text):
                    p.keepWithNext = True
                story.append(p)
            index += 2
        elif token.type == "fence":
            # Wrap at spaces while preserving a copyable full command in the
            # Markdown; the PDF is a reading reference, not a shell script.
            wrapped = "\n".join(textwrap.fill(line, width=89, subsequent_indent="  ",
                                             break_long_words=False, break_on_hyphens=False)
                                for line in token.content.rstrip().splitlines())
            story.append(XPreformatted(escape(wrapped), sty["code"]))
        elif token.type == "hr":
            story.append(Spacer(1, 10))
        elif token.type == "html_block" and "manual-page-break" in token.content:
            story.append(PageBreak())
        index += 1
    output.parent.mkdir(parents=True, exist_ok=True)
    doc = ManualDoc(output, edition=edition, issued=issued, baseline=baseline,
                    product_title=product_title, document_id=document_id)
    doc.multiBuild(story)
    from pypdf import PdfReader
    reader = PdfReader(output)
    texts = [page.extract_text() or "" for page in reader.pages]
    # Additional verified UI captures should not block a release manual build.
    invalid_inventory = ((len(chapters) != 19 or len(figures) < 19)
                         if product_title is None else (len(chapters) < 2 or not figures))
    if invalid_inventory:
        raise AssertionError((len(chapters), len(figures)))
    if any(not text.strip() for text in texts):
        raise AssertionError("Blank PDF page")
    report = {"output": str(output), "pages": len(reader.pages),
              "edition": edition, "issued": issued, "baseline": baseline,
              "chapters": len(chapters), "figures": len(figures),
              "source_words": len(document.split()), "searchable": True,
              "outline_entries": sum(1 for t in tokens if t.type == "heading_open" and t.tag in {"h2", "h3"}),
              "embedded_images": len(figures)}
    from document_release import record
    record(source, output)
    print(json.dumps(report, indent=2))
    return report


def audit(output, report):
    import fitz
    scratch = ROOT / "tmp" / "pdfs" / output.stem
    scratch.mkdir(parents=True, exist_ok=True)
    document = fitz.open(output)
    issues, per_page = [], []
    for number, page in enumerate(document, 1):
        blocks = page.get_text("dict")["blocks"]
        count = 0
        for block in blocks:
            if block["type"] != 0:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    text = span["text"]
                    if not text.strip():
                        continue
                    count += len(text.split())
                    x0, y0, x1, y1 = span["bbox"]
                    if x0 < 15 or x1 > PAGE_W - 15 or y0 < 8 or y1 > PAGE_H - 8:
                        issues.append({"page": number, "text": text, "bbox": span["bbox"]})
                    if "\ufffd" in text or "\u25a0" in text:
                        issues.append({"page": number, "glyph": text})
        per_page.append({"page": number, "words": count, "images": len(page.get_images()),
                         "text": page.get_text()[:180]})
        page.get_pixmap(matrix=fitz.Matrix(1.15, 1.15)).save(str(scratch / f"page-{number:03}.png"))
    # Contact sheets are verification artifacts generated from the PDF pages.
    for start in range(0, len(document), 12):
        sheet = PILImage.new("RGB", (4 * 260, 3 * 390), "#D5DEE7")
        from PIL import ImageDraw
        draw = ImageDraw.Draw(sheet)
        for slot, number in enumerate(range(start + 1, min(start + 13, len(document) + 1))):
            with PILImage.open(scratch / f"page-{number:03}.png") as page:
                page.thumbnail((244, 352))
                x, y = (slot % 4) * 260 + 8, (slot // 4) * 390 + 8
                sheet.paste(page, (x, y))
                draw.text((x, y + 357), f"Page {number}", fill="black")
        sheet.save(scratch / f"contact-{start // 12 + 1:02}.png")
    report.update({"geometry_issues": issues, "page_audit": per_page,
                   "render_directory": str(scratch)})
    (scratch / "audit.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if issues:
        raise AssertionError(f"PDF geometry/glyph issues: {issues[:5]}")
    count = len(document)
    document.close()
    print(f"Rendered {count} pages; geometry/glyph checks passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--audit", action="store_true")
    args = parser.parse_args()
    from document_release import build_release
    build_release(SOURCE, args.output, build, audit if args.audit else None)
