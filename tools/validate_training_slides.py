"""Validate the native teaching deck and create private contact sheets for visual QA."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import shutil
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from PIL import Image, ImageDraw
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "tmp" / "training_slides"
NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
      "c": "http://schemas.openxmlformats.org/drawingml/2006/chart"}


def validate(deliver=False):
    deck = json.loads((BUILD / "deck.json").read_text(encoding="utf-8"))
    audit = json.loads((BUILD / "powerpoint_audit.json").read_text(encoding="utf-8-sig"))
    assert not audit["geometry"], audit["geometry"]
    candidate = Path(audit["candidate"])
    assert candidate.is_file()
    count = len(deck["slides"])
    notes_words = 0
    native_tables, native_charts, image_slides = [], [], []
    with ZipFile(candidate) as archive:
        assert archive.testzip() is None
        slide_names = [n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
        assert len(slide_names) == count
        root = ET.fromstring(archive.read("ppt/presentation.xml"))
        size = root.find("p:sldSz", NS)
        assert (int(size.attrib["cx"]), int(size.attrib["cy"])) == (12192000, 6858000)
        for number, spec in enumerate(deck["slides"], 1):
            slide = ET.fromstring(archive.read(f"ppt/slides/slide{number}.xml"))
            text = "\n".join(node.text or "" for node in slide.findall(".//a:t", NS))
            for title_line in spec["title"].splitlines():
                assert title_line in text, (number, title_line)
            normalized_text = " ".join(text.split())
            for key in ("lines", "left", "right", "leftTitle", "rightTitle", "caption",
                        "setup", "evidence", "pass", "cleanup", "prompt", "subtitle"):
                value = spec.get(key, [])
                for item in ([value] if isinstance(value, str) else value):
                    assert " ".join(item.split()) in normalized_text, (number, key, item)
            note = ET.fromstring(archive.read(f"ppt/notesSlides/notesSlide{number}.xml"))
            note_text = " ".join(node.text or "" for node in note.findall(".//a:t", NS))
            assert "Sources:" in note_text and "Teaching context:" in note_text, number
            notes_words += len(note_text.split())
            tables = slide.findall(".//a:tbl", NS)
            charts = slide.findall(".//c:chart", NS)
            pictures = slide.findall(".//p:pic", NS)
            if spec["type"] == "table":
                assert len(tables) == 1, number
                native_tables.append(number)
                rows = tables[0].findall("a:tr", NS)
                expected = [spec["columns"], *spec["rows"]]
                assert len(rows) == len(expected), number
                for row, expected_cells in zip(rows, expected):
                    cells = row.findall("a:tc", NS)
                    assert len(cells) == len(expected_cells), number
                    for cell, expected_text in zip(cells, expected_cells):
                        actual_text = "".join(t.text or "" for t in cell.findall(".//a:t", NS))
                        assert actual_text == expected_text, (number, actual_text, expected_text)
            if spec["type"] == "chart":
                assert len(charts) == 1, number
                native_charts.append(number)
            if spec["type"] == "image":
                assert pictures, number
                image_slides.append(number)
        chart_parts = [n for n in archive.namelist() if re.fullmatch(r"ppt/charts/chart\d+\.xml", n)]
        assert len(chart_parts) == len(native_charts) == 1
        chart_xml = ET.fromstring(archive.read(chart_parts[0]))
        expected_chart = next(s for s in deck["slides"] if s["type"] == "chart")
        series = chart_xml.findall(".//c:ser", NS)
        assert len(series) == len(expected_chart["series"])
        for actual, expected in zip(series, expected_chart["series"]):
            values = [float(v.text) for v in actual.findall("c:val/c:numRef/c:numCache/c:pt/c:v", NS)]
            assert values == expected["values"], (values, expected)
            categories = [float(v.text) for v in actual.findall("c:cat/c:numRef/c:numCache/c:pt/c:v", NS)]
            assert categories == [float(v) for v in expected_chart["categories"]]
            assert actual.find("c:tx/c:strRef/c:strCache/c:pt/c:v", NS).text == expected["name"]
        embeddings = [n for n in archive.namelist() if n.startswith("ppt/embeddings/") and n.endswith(".xlsx")]
        assert len(embeddings) == 1
        book = load_workbook(BytesIO(archive.read(embeddings[0])), read_only=True, data_only=True)
        rows = list(book["Sheet1"].values)
        assert rows[0] == ("Time (s)", *[s["name"] for s in expected_chart["series"]])
        expected_rows = [(float(time), *[s["values"][i] for s in expected_chart["series"]])
                         for i, time in enumerate(expected_chart["categories"])]
        assert rows[1:] == expected_rows
        book.close()
    for lab in [f"L{i:02}" for i in range(1, 19)]:
        entries = [s for s in deck["slides"] if s["title"].startswith(lab + "  ")]
        assert len(entries) == 2, lab
        assert {s["type"] for s in entries} == {"lab", "debrief"}, lab
    notes = "\n".join(s["notes"] for s in deck["slides"] if s["type"] == "questions")
    for n in range(1, 21):
        assert f"Q{n:02}:" in notes
    render_dir = Path(audit["renders"])
    for number in range(1, count + 1):
        with Image.open(render_dir / f"slide-{number:03}.png") as image:
            assert image.size == (1280, 720), number
    for start in range(0, count, 12):
        sheet = Image.new("RGB", (1280, 804), "#D5DEE7")
        draw = ImageDraw.Draw(sheet)
        for slot, number in enumerate(range(start + 1, min(start + 13, count + 1))):
            with Image.open(render_dir / f"slide-{number:03}.png") as frame:
                frame.thumbnail((412, 232))
                x, y = (slot % 3) * 426 + 7, (slot // 3) * 201 + 4
                # QA sheets are thumbnails only. Review original PNGs for fit.
                frame.thumbnail((348, 196))
                sheet.paste(frame, (x, y))
                draw.text((x + 354, y + 8), f"{number:03}", fill="black")
        sheet.save(render_dir / f"contact-{start // 12 + 1:02}.png")
    report = {"slides": count, "notes_words": notes_words,
              "per_day": dict(Counter(str(s["day"]) for s in deck["slides"])),
              "guided_labs": 18, "lab_and_debrief_slides": 36,
              "knowledge_answers": 20, "editable_table_slides": native_tables,
              "editable_chart_slides": native_charts, "reference_image_slides": image_slides,
              "candidate_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest()}
    if deliver:
        destination = Path(deck["output"])
        if destination.exists():
            raise FileExistsError(f"Choose a new final filename: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(candidate, destination)
        report["output"] = str(destination)
    (BUILD / "content_validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deliver", action="store_true")
    args = parser.parse_args()
    validate(args.deliver)
