"""Update an existing native PowerPoint chart and its embedded teaching data."""
from __future__ import annotations

import argparse
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from lxml import etree as ET
from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
NS = {"c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "p": "http://schemas.openxmlformats.org/presentationml/2006/main"}


def child(parent, prefix, tag, text=None, **attrs):
    item = ET.SubElement(parent, f"{{{NS[prefix]}}}{tag}", **{k: str(v) for k, v in attrs.items()})
    if text is not None:
        item.text = str(text)
    return item


def apply_chart_font(root):
    # Chart-level defaults otherwise inherit the host Office theme during editing.
    for properties in root.findall(".//c:txPr/a:p/a:pPr/a:defRPr", NS):
        for script in ("latin", "ea", "cs"):
            font = properties.find(f"a:{script}", NS)
            if font is None:
                font = child(properties, "a", script)
            font.set("typeface", "Segoe UI")


def prepare(source, output, spec_path):
    deck = json.loads(spec_path.read_text(encoding="utf-8"))
    spec = next(s for s in deck["slides"] if s["type"] == "chart")
    with ZipFile(source) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    chart_name = next(n for n in parts if n.startswith("ppt/charts/chart") and n.endswith(".xml"))
    root = ET.fromstring(parts[chart_name])
    apply_chart_font(root)
    series = root.findall(".//c:ser", NS)
    if len(series) == 4:
        series[0].getparent().remove(series[0])
        series = series[1:]
    assert len(series) == len(spec["series"]) == 3
    palette = ["8A96A1", "004487", "00858A"]
    for i, (node, data) in enumerate(zip(series, spec["series"])):
        node.find("c:idx", NS).set("val", str(i))
        node.find("c:order", NS).set("val", str(i))
        col = chr(ord("B") + i)
        node.find("c:tx/c:strRef/c:f", NS).text = f"Sheet1!${col}$1"
        node.find("c:tx/c:strRef/c:strCache/c:pt/c:v", NS).text = data["name"]
        shape = node.find("c:spPr", NS)
        if shape is None:
            shape = ET.Element(f"{{{NS['c']}}}spPr")
            node.insert(node.index(node.find("c:tx", NS)) + 1, shape)
        line = shape.find("a:ln", NS)
        if line is None:
            line = child(shape, "a", "ln", w="31750")
        for fill in list(line):
            if ET.QName(fill).localname.endswith("Fill"):
                line.remove(fill)
        child(child(line, "a", "solidFill"), "a", "srgbClr", val=palette[i])
        old_category = node.find("c:cat", NS)
        if old_category is not None:
            node.remove(old_category)
        category = ET.Element(f"{{{NS['c']}}}cat")
        reference = child(category, "c", "numRef")
        child(reference, "c", "f", f"Sheet1!$A$2:$A${len(spec['categories']) + 1}")
        cache = child(reference, "c", "numCache")
        child(cache, "c", "formatCode", "0")
        child(cache, "c", "ptCount", val=len(spec["categories"]))
        for point, value in enumerate(spec["categories"]):
            child(child(cache, "c", "pt", idx=point), "c", "v", value)
        value_node = node.find("c:val", NS)
        node.insert(node.index(value_node), category)
        ref = value_node.find("c:numRef", NS)
        ref.find("c:f", NS).text = f"Sheet1!${col}$2:${col}${len(data['values']) + 1}"
        ref.remove(ref.find("c:numCache", NS))
        cache = child(ref, "c", "numCache")
        child(cache, "c", "formatCode", "0.0")
        child(cache, "c", "ptCount", val=len(data["values"]))
        for point, value in enumerate(data["values"]):
            child(child(cache, "c", "pt", idx=point), "c", "v", value)
    for axis in root.findall(".//c:valAx", NS):
        formatting = axis.find("c:numFmt", NS)
        if formatting is not None:
            formatting.set("formatCode", "0")
            formatting.set("sourceLinked", "0")
    parts[chart_name] = ET.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    book = Workbook()
    sheet = book.active
    sheet.title = "Sheet1"
    sheet.append(["Time (s)", *[s["name"] for s in spec["series"]]])
    for index, time in enumerate(spec["categories"]):
        sheet.append([float(time), *[s["values"][index] for s in spec["series"]]])
    data = BytesIO()
    book.save(data)
    embedding = next(n for n in parts if n.startswith("ppt/embeddings/") and n.endswith(".xlsx"))
    parts[embedding] = data.getvalue()
    slide = ET.fromstring(parts["ppt/slides/slide1.xml"])
    heights = {"Title": 82, "ChartDisclosure": 35, "CourseFooter": 18, "SlideNumber": 18}
    for shape in slide.findall(".//p:sp", NS):
        name = shape.find("p:nvSpPr/p:cNvPr", NS).get("name")
        if name in heights:
            shape.find("p:spPr/a:xfrm/a:ext", NS).set("cy", str(heights[name] * 12700))
    parts["ppt/slides/slide1.xml"] = ET.tostring(slide, xml_declaration=True, encoding="UTF-8", standalone=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    print(f"Prepared native chart with {len(spec['categories'])} time points and 3 series: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "docs/training/assets/response_chart_template.pptx")
    parser.add_argument("--output", type=Path, default=ROOT / "tmp/training_slides/native_chart.pptx")
    parser.add_argument("--spec", type=Path, default=ROOT / "tmp/training_slides/deck.json")
    args = parser.parse_args()
    prepare(args.source, args.output, args.spec)
