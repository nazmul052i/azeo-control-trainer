"""Build the two maintained ACT-FND-01 PDFs; --audit renders every page for review."""
from __future__ import annotations

import argparse
import hashlib
from html import escape
import json
from pathlib import Path
import textwrap

from markdown_it import MarkdownIt
from PIL import Image, ImageDraw
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import PageBreak, Paragraph, Spacer, XPreformatted
from reportlab.platypus.tableofcontents import TableOfContents

from build_user_manual import (
    BLUE, BORDER, INK, MARGIN_X, MUTED, NAVY, PAGE_H, PAGE_W, WIDTH,
    ManualDoc, inline, register_fonts, slug, styles, table,
)

ROOT = Path(__file__).resolve().parents[1]
COURSE = ROOT / "docs" / "training"
OUTPUT = ROOT / "output" / "pdf"
EDITIONS = (
    ("LEARNER_WORKBOOK.md", "Learner Workbook", "ACT-FND-01-L",
     "Azeo_Process_Control_Learner_Workbook.pdf"),
    ("INSTRUCTOR_GUIDE.md", "Instructor Guide", "ACT-FND-01-I",
     "Azeo_Process_Control_Instructor_Guide.pdf"),
)


class CourseDoc(ManualDoc):
    def __init__(self, output, *, subtitle, document_id, manifest):
        self.subtitle = subtitle
        self.document_id = document_id
        super().__init__(output, edition=manifest["edition"], issued=manifest["issued"],
                         baseline=manifest["application_baseline"])
        self.title = f"Process-Control Engineering Fundamentals - {subtitle}"
        self.subject = "ACT-FND-01: five-day instructor-led pilot course"

    def beforeDocument(self):
        self.chapter = "Contents"
        self.heading_count = 0

    def page_start(self, canvas, doc):
        if doc.page != 1:
            return
        canvas.saveState()
        canvas.setFillColor(NAVY)
        canvas.rect(0, 292, PAGE_W, PAGE_H - 292, fill=1, stroke=0)
        canvas.setFillColor(BLUE)
        canvas.rect(0, 284, PAGE_W, 8, fill=1, stroke=0)
        canvas.setFont("ManualBold", 10)
        canvas.setFillColor(colors.HexColor("#B8D8F2"))
        canvas.drawString(MARGIN_X, PAGE_H - 71, "AZEO  /  ENGINEERING EDUCATION")
        canvas.setFillColor(colors.white)
        canvas.setFont("ManualBold", 32)
        for i, line in enumerate(("Process-Control", "Engineering", "Fundamentals")):
            canvas.drawString(MARGIN_X, PAGE_H - 149 - 41 * i, line)
        canvas.setFont("Manual", 23)
        canvas.drawString(MARGIN_X, PAGE_H - 298, self.subtitle)
        canvas.setFont("Manual", 11)
        canvas.setFillColor(colors.HexColor("#CFE3F3"))
        canvas.drawString(MARGIN_X, PAGE_H - 336, "Five days  /  40 contact hours  /  Instructor-led pilot")
        canvas.setStrokeColor(colors.HexColor("#507394"))
        canvas.line(MARGIN_X, 433, PAGE_W - MARGIN_X, 433)
        for i, label in enumerate(("Trace signals and control behavior",
                                   "Diagnose with modes, quality and history",
                                   "Build, verify and release operator graphics")):
            canvas.setFillColor(colors.HexColor("#72B4E8"))
            canvas.circle(MARGIN_X + 3, 403 - i * 27, 2.5, fill=1, stroke=0)
            canvas.setFillColor(colors.white)
            canvas.drawString(MARGIN_X + 15, 399 - i * 27, label)
        canvas.setFillColor(INK)
        canvas.setFont("ManualBold", 12)
        canvas.drawString(MARGIN_X, 245, "FROM OBSERVATION TO ENGINEERING EVIDENCE")
        canvas.setFont("Manual", 10)
        lines = (
            "Learner edition: 18 guided labs, capstone and evidence worksheets."
            if self.document_id.endswith("-L") else
            "Instructor edition: delivery notes, fault cards, answers and rubrics.",
            "Use qualified starting conditions and the allocated training runtime.",
            "Pilot course: instructor lab qualification is required before delivery.",
        )
        for i, line in enumerate(lines):
            canvas.drawString(MARGIN_X, 216 - i * 22, line)
        canvas.setFillColor(MUTED)
        canvas.setFont("Manual", 9)
        canvas.drawString(MARGIN_X, 103,
                          f"Edition {self.edition}  |  {self.issued}  |  Baseline {self.baseline}")
        canvas.drawString(MARGIN_X, 82, f"{self.document_id}  |  Azeo Control Trainer")
        canvas.restoreState()

    def page_end(self, canvas, doc):
        if doc.page == 1:
            return
        canvas.saveState()
        canvas.setStrokeColor(BORDER)
        canvas.line(MARGIN_X, PAGE_H - 37, PAGE_W - MARGIN_X, PAGE_H - 37)
        canvas.setFont("ManualBold", 8)
        canvas.setFillColor(BLUE)
        canvas.drawString(MARGIN_X, PAGE_H - 26, f"AZEO  /  {self.subtitle.upper()}")
        canvas.setFont("Manual", 7.4)
        canvas.setFillColor(MUTED)
        from reportlab.pdfbase import pdfmetrics
        label = doc.chapter
        while pdfmetrics.stringWidth(label, "Manual", 7.4) > WIDTH - 180:
            label = label[:-4] + "..."
        canvas.drawRightString(PAGE_W - MARGIN_X, PAGE_H - 26, label)
        canvas.line(MARGIN_X, 34, PAGE_W - MARGIN_X, 34)
        canvas.setFont("Manual", 8)
        canvas.drawString(MARGIN_X, 21, f"{self.document_id}  |  Edition {self.edition}  |  Pilot")
        canvas.drawRightString(PAGE_W - MARGIN_X, 21, str(doc.page))
        canvas.restoreState()

    def afterFlowable(self, item):
        if not hasattr(item, "manual_heading"):
            return
        level, title = item.manual_heading
        self.heading_count += 1
        # Repeated lesson subheadings need distinct destinations in PDF bookmarks.
        key = f"section-{self.heading_count}-{slug(title)}"
        self.canv.bookmarkPage(key)
        self.canv.addOutlineEntry(title, key, level=level, closed=level == 0)
        if level == 0:
            self.chapter = title
            self.notify("TOCEntry", (0, title, self.page, key))


def build(source, output, subtitle, document_id, manifest):
    sty = styles()
    tokens = MarkdownIt("commonmark").enable("table").parse(source.read_text(encoding="utf-8"))
    story = [Spacer(1, 1), PageBreak(), Paragraph("Contents", sty["chapter"])]
    story.append(Paragraph(
        "Use the linked contents or PDF bookmarks to open a session. "
        "The maintained Markdown source contains links to the detailed product guides.", sty["body"]))
    toc = TableOfContents()
    toc.levelStyles = [ParagraphStyle("CourseTOC", fontName="Manual", fontSize=10.5,
                                     leading=24, textColor=BLUE, rightIndent=24)]
    story.extend([toc, Spacer(1, 24), Paragraph(
        f"<b>Course record</b><br/>{manifest['course_id']} / "
        f"Edition {manifest['edition']} / {manifest['contact_minutes'] // 60} contact hours.<br/>"
        "This pilot edition requires instructor qualification of the live exercises. "
        "Printed worksheet spaces are for learner/instructor entries; they are not "
        "prevalidated process values.", sty["note"])])
    started, quote = False, 0
    lists, rows, row = [], None, None
    index, chapters = 0, 0
    while index < len(tokens):
        token = tokens[index]
        if token.type == "heading_open":
            title = tokens[index + 1]
            if token.tag == "h2":
                started = True
                chapters += 1
                story.append(PageBreak())
                heading = Paragraph(inline(title), sty["chapter"])
                heading.manual_heading = (0, title.content)
                story.append(heading)
            elif started and token.tag == "h3":
                heading = Paragraph(inline(title), sty["section"])
                heading.manual_heading = (1, title.content)
                story.append(heading)
            index += 3
            continue
        if not started:
            index += 1
            continue
        if token.type in ("ordered_list_open", "bullet_list_open"):
            lists.append([token.type == "ordered_list_open", int(token.attrGet("start") or 1)])
        elif token.type in ("ordered_list_close", "bullet_list_close"):
            lists.pop()
        elif token.type == "blockquote_open":
            quote += 1
        elif token.type == "blockquote_close":
            quote -= 1
        elif token.type == "table_open":
            rows = []
        elif token.type == "tr_open":
            row = []
        elif token.type in ("td_open", "th_open"):
            row.append(inline(tokens[index + 1], links=False))
            index += 2
        elif token.type == "tr_close":
            rows.append(row)
        elif token.type == "table_close":
            story.append(table(rows, sty))
            rows = None
        elif token.type == "paragraph_open":
            content = inline(tokens[index + 1])
            bullet = None
            style = sty["note"] if quote else sty["body"]
            if lists:
                ordered, number = lists[-1]
                bullet = f"{number}." if ordered else "\u2022"
                lists[-1][1] += 1
                style = sty["bullet"]
            story.append(Paragraph(content, style, bulletText=bullet))
            index += 2
        elif token.type == "fence":
            wrapped = "\n".join(textwrap.fill(line, width=88, subsequent_indent="  ",
                                             break_long_words=False, break_on_hyphens=False)
                                for line in token.content.rstrip().splitlines())
            story.append(XPreformatted(escape(wrapped), sty["code"]))
        elif token.type == "html_block" and "course-page-break" in token.content:
            story.append(PageBreak())
        index += 1
    output.parent.mkdir(parents=True, exist_ok=True)
    CourseDoc(output, subtitle=subtitle, document_id=document_id, manifest=manifest).multiBuild(story)
    from pypdf import PdfReader
    reader = PdfReader(output)
    if any(not (page.extract_text() or "").strip() for page in reader.pages):
        raise ValueError(f"Blank page in {output}")
    report = {"output": str(output), "source": str(source), "pages": len(reader.pages),
              "chapters": chapters, "words": len(source.read_text(encoding="utf-8").split()),
              "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "pdf_sha256": hashlib.sha256(output.read_bytes()).hexdigest()}
    print(json.dumps(report))
    return report


def audit(output, report):
    import fitz
    scratch = ROOT / "tmp" / "pdfs" / output.stem
    scratch.mkdir(parents=True, exist_ok=True)
    issues, page_counts = [], []
    with fitz.open(output) as document:
        for number, page in enumerate(document, 1):
            page_counts.append({"page": number, "words": len(page.get_text().split())})
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        x0, y0, x1, y1 = span["bbox"]
                        if (x0 < 15 or x1 > PAGE_W - 15 or y0 < 8 or y1 > PAGE_H - 8
                                or "\ufffd" in span["text"] or "\u25a0" in span["text"]):
                            issues.append({"page": number, "text": span["text"], "bbox": span["bbox"]})
            page.get_pixmap(matrix=fitz.Matrix(1.25, 1.25)).save(scratch / f"page-{number:03}.png")
        for start in range(0, len(document), 12):
            sheet = Image.new("RGB", (1040, 1170), "#D5DEE7")
            draw = ImageDraw.Draw(sheet)
            for slot, number in enumerate(range(start + 1, min(start + 13, len(document) + 1))):
                with Image.open(scratch / f"page-{number:03}.png") as page_image:
                    page_image.thumbnail((244, 352))
                    x, y = (slot % 4) * 260 + 8, (slot // 4) * 390 + 8
                    sheet.paste(page_image, (x, y))
                    draw.text((x, y + 357), f"Page {number}", fill="black")
            sheet.save(scratch / f"contact-{start // 12 + 1:02}.png")
    report.update({"geometry_issues": issues, "page_counts": page_counts,
                   "render_directory": str(scratch)})
    (scratch / "audit.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if issues:
        raise ValueError(f"PDF geometry/glyph failures: {issues[:5]}")
    print(f"Rendered and checked {len(page_counts)} pages: {output.name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", action="store_true")
    args = parser.parse_args()
    manifest = json.loads((COURSE / "course_manifest.json").read_text(encoding="utf-8"))
    register_fonts()
    for filename, subtitle, document_id, pdf in EDITIONS:
        output = OUTPUT / pdf
        report = build(COURSE / filename, output, subtitle, document_id, manifest)
        if args.audit:
            audit(output, report)


if __name__ == "__main__":
    main()
