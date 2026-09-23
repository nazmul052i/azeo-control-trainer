#!/usr/bin/env python3
"""Split the draw.io symbol sheet into one standalone SVG per symbol.

``docs/ProcessSymbols.svg`` is a draw.io export holding the whole ISA symbol
set on one page. This turns it into a symbol library: one file per symbol,
cropped to the symbol's own bounding box and cleaned so it renders anywhere.

Two things have to be fixed on the way out, or the symbols are unusable
outside draw.io:

* draw.io writes ``style="fill: var(--ge-adaptive-bg, #ffffff); stroke:
  light-dark(...)"`` for dark-mode support. Nothing but a browser understands
  those functions, so every other renderer falls back to black and the symbol
  becomes a silhouette. The presentation attributes underneath (``fill``,
  ``stroke``) are already correct, so the adaptive ``style`` is dropped.
* placeholder labels ("TI ##") are rendered as ``<switch>``/``<foreignObject>``
  HTML, which most renderers ignore anyway. They are stripped so the symbol is
  clean geometry and the caller supplies its own text.

    python tools/split_symbols.py [--src docs/ProcessSymbols.svg]
                                  [--out docs/symbols] [--sheet]
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]

# shape name (last dotted segment) plus its distinguishing style parameters,
# mapped to the file name the library should use.
NAMES: Dict[Tuple[str, str], str] = {
    ("discInst", "mounting=room"): "instrument_discrete_room",
    ("discInst", "mounting=field"): "instrument_discrete_field",
    ("sharedCont", "mounting=room"): "instrument_shared_control_room",
    ("sharedCont", "mounting=inaccessible"): "instrument_shared_control_inaccessible",
    ("indicator", "indType=inst"): "indicator_instrument",
    ("indicator", "indType=ctrl"): "indicator_control",
    ("indicator", "indType=func"): "indicator_function",
    ("indicator", "indType=plc"): "indicator_plc",
    ("column", "columnType=baffle"): "column_baffle",
    ("column", "columnType=fixed"): "column_fixed",
    ("column", "columnType=common"): "column_common",
    ("valve", "actuator=diaph"): "valve_control_diaphragm",
    ("valve", "actuator=powered"): "valve_motor_operated",
    ("valve", "actuator=balDiaph"): "valve_control_balanced_diaphragm",
    ("valve", ""): "valve_gate",
}

# shapes with only one instance, named straight from the shape
SIMPLE: Dict[str, str] = {
    "air_cooler": "air_cooler",
    "stack": "stack",
    "shell_and_tube_heat_exchanger_1": "shell_and_tube_1",
    "shell_and_tube_heat_exchanger_2": "shell_and_tube_2",
    "shell_and_tube_heat_exchanger_3": "shell_and_tube_3",
    "reboiler": "reboiler",
    "condenser": "condenser",
    "heater": "heater",
    "heat_exchanger_": "heat_exchanger",
    "centrifugal_pump_1": "centrifugal_pump_1",
    "centrifugal_pump_2": "centrifugal_pump_2",
    "centrifugal_pump_3": "centrifugal_pump_3",
    "gas_blower": "gas_blower",
    "pressurized_vessel": "pressurized_vessel",
    "tank": "tank",
    "tank_": "tank_domed",
    "vessel_": "vessel_horizontal",
    "furnace": "furnace",
    "compressors_": "compressor",
    "fan": "fan",
}

KEYS = ("mounting", "indType", "columnType", "actuator")


def parse_cells(svg: str) -> List[dict]:
    """Read the embedded mxfile for every vertex: shape, style, size."""
    m = re.search(r'content="(.*?)"\s', svg, re.S)
    if not m:
        raise SystemExit("no embedded draw.io model found in the source SVG")
    model = html.unescape(m.group(1))

    out: List[dict] = []
    for cid, style, geo in re.findall(
            r'<mxCell id="([^"]+)"[^>]*?style="([^"]*)"[^>]*?vertex="1"[^>]*>'
            r'\s*<mxGeometry([^/]*)/>', model, re.S):
        shape = re.search(r'shape=([a-zA-Z0-9_.]+)', style)
        if not shape:
            continue
        g = dict(re.findall(r'(\w+)="([-\d.]+)"', geo))
        params = {k: v for k, v in
                  (p.split("=", 1) for p in style.split(";") if "=" in p)
                  if k in KEYS}
        out.append({
            "id": cid,
            "shape": shape.group(1).split(".")[-1],
            "full_shape": shape.group(1),
            "params": params,
            "x": float(g.get("x", 0)),
            "y": float(g.get("y", 0)),
            "w": float(g.get("width", 0)),
            "h": float(g.get("height", 0)),
        })
    return out


def page_offset(svg: str, cells: List[dict]) -> Tuple[float, float]:
    """Model coordinates to SVG coordinates.

    draw.io crops the export to its content, so the rendered page is shifted
    from the model page by a constant. Several shapes emit an invisible rect
    the size of the symbol; the shift is read from those and then used for the
    shapes that do not (instruments, valves and columns draw themselves
    directly, with no sizing rect).
    """
    seen: Dict[Tuple[float, float], int] = {}
    for c in cells:
        group = group_for(svg, c["id"])
        if not group:
            continue
        box = rect_origin(group, c["w"], c["h"])
        if box:
            key = (round(box[0] - c["x"], 2), round(box[1] - c["y"], 2))
            seen[key] = seen.get(key, 0) + 1
    if not seen:
        return 0.0, 0.0
    return max(seen.items(), key=lambda kv: kv[1])[0]


def group_for(svg: str, cell_id: str) -> Optional[str]:
    """Pull out <g data-cell-id="..."> ... </g>, counting nesting.

    draw.io emits self-closing ``<g .../>`` elements for empty text groups.
    Counting one of those as an opening tag runs the extraction past the end of
    the symbol and swallows whatever follows it, so they are skipped.
    """
    start = svg.find(f'<g data-cell-id="{cell_id}"')
    if start < 0:
        return None
    depth = 0
    for m in re.finditer(r"<g\b[^>]*?(/?)>|</g>", svg[start:]):
        if m.group(0) == "</g>":
            depth -= 1
        elif m.group(1) == "/":
            continue                      # self-closing, not a nesting level
        else:
            depth += 1
        if depth == 0:
            return svg[start:start + m.end()]
    return None


def rect_origin(group: str, w: float, h: float) -> Optional[Tuple[float, float]]:
    """draw.io emits an invisible rect the size of the shape; use its origin."""
    for m in re.finditer(r'<rect([^>]*)/>', group):
        a = m.group(1)
        if 'fill="none"' in a and 'stroke="none"' in a:
            g = dict(re.findall(r'(x|y|width|height)="([-\d.]+)"', a))
            if (abs(float(g.get("width", -1)) - w) < 1.5
                    and abs(float(g.get("height", -1)) - h) < 1.5):
                return float(g["x"]), float(g["y"])
    return None


def clean(group: str) -> str:
    """Strip draw.io's adaptive CSS and HTML labels."""
    # adaptive colour styles: nothing outside a browser can resolve these
    group = re.sub(r'\s+style="[^"]*(?:var\(|light-dark\()[^"]*"', "", group)
    group = re.sub(r'\s+pointer-events="[^"]*"', "", group)
    # placeholder labels rendered as HTML
    group = re.sub(r"<switch>.*?</switch>", "", group, flags=re.S)
    group = re.sub(r"<foreignObject.*?</foreignObject>", "", group, flags=re.S)
    # empty leftovers
    group = re.sub(r"<g>\s*</g>", "", group)
    return group


def build(group: str, ox: float, oy: float, w: float, h: float,
          name: str, shape: str) -> str:
    body = clean(group)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:g}" height="{h:g}" '
        f'viewBox="0 0 {w:g} {h:g}" preserveAspectRatio="xMidYMid meet" '
        'version="1.1">\n'
        f'  <title>{name}</title>\n'
        f'  <desc>AzeoPlant symbol library. Source shape {shape}, '
        'extracted from docs/ProcessSymbols.svg.</desc>\n'
        f'  <g transform="translate({-ox:g},{-oy:g})">\n'
        f'    {body}\n'
        '  </g>\n'
        '</svg>\n')


def contact_sheet(entries: List[dict], out: Path) -> Path:
    """One sheet showing every extracted symbol, for review."""
    cols, cell_w, cell_h = 6, 220, 190
    rows = (len(entries) + cols - 1) // cols
    W, H = cols * cell_w + 40, rows * cell_h + 90
    parts = [f'<rect x="0" y="0" width="{W}" height="{H}" fill="#ffffff"/>',
             f'<text x="{W/2}" y="42" font-family="Arial" font-size="24" '
             'font-weight="bold" text-anchor="middle">AzeoPlant symbol library'
             f'  ·  {len(entries)} symbols</text>']
    for i, e in enumerate(entries):
        cx = 20 + (i % cols) * cell_w
        cy = 70 + (i // cols) * cell_h
        parts.append(f'<rect x="{cx}" y="{cy}" width="{cell_w - 12}" '
                     f'height="{cell_h - 12}" fill="#fafafa" stroke="#c8c8c8"/>')
        # scale the symbol to fit the tile
        box = min((cell_w - 60) / max(e["w"], 1), (cell_h - 80) / max(e["h"], 1), 1.6)
        sw, sh = e["w"] * box, e["h"] * box
        tx = cx + (cell_w - 12 - sw) / 2
        ty = cy + 18 + (cell_h - 60 - sh) / 2
        parts.append(f'<g transform="translate({tx:g},{ty:g}) scale({box:g}) '
                     f'translate({-e["ox"]:g},{-e["oy"]:g})">{clean(e["group"])}</g>')
        parts.append(f'<text x="{cx + (cell_w - 12)/2}" y="{cy + cell_h - 24}" '
                     'font-family="Arial" font-size="12" text-anchor="middle" '
                     f'fill="#222">{e["name"]}</text>')
        parts.append(f'<text x="{cx + (cell_w - 12)/2}" y="{cy + cell_h - 40}" '
                     'font-family="Arial" font-size="10" text-anchor="middle" '
                     f'fill="#777">{e["w"]:g} x {e["h"]:g}</text>')
    svg = ('<?xml version="1.0" encoding="utf-8"?>\n'
           f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
           f'viewBox="0 0 {W} {H}" version="1.1">\n' + "\n".join(parts) + "\n</svg>\n")
    out.write_text(svg, encoding="utf-8")
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Split the draw.io symbol sheet")
    p.add_argument("--src", default=str(ROOT / "docs" / "ProcessSymbols.svg"))
    p.add_argument("--out", default=str(ROOT / "docs" / "symbols"))
    p.add_argument("--sheet", action="store_true",
                   help="also write a contact sheet of every symbol")
    args = p.parse_args(argv)

    svg = Path(args.src).read_text(encoding="utf-8")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    entries: List[dict] = []
    used: Dict[str, int] = {}
    skipped: List[str] = []

    cells = parse_cells(svg)
    dx, dy = page_offset(svg, cells)
    print(f"model to sheet offset: ({dx:g}, {dy:g})\n")

    for cell in cells:
        shape, params = cell["shape"], cell["params"]
        name = None
        for key in KEYS:
            if key in params:
                name = NAMES.get((shape, f"{key}={params[key]}"))
                if name:
                    break
        if name is None:
            name = NAMES.get((shape, "")) or SIMPLE.get(shape)
        if name is None:
            skipped.append(f'{cell["id"]} {cell["full_shape"]}')
            continue

        group = group_for(svg, cell["id"])
        if group is None:
            skipped.append(f'{cell["id"]} {shape} (no rendered group)')
            continue
        origin = (rect_origin(group, cell["w"], cell["h"])
                  or (cell["x"] + dx, cell["y"] + dy))

        used[name] = used.get(name, 0) + 1
        fname = name if used[name] == 1 else f"{name}_{used[name]}"
        path = out / f"{fname}.svg"
        path.write_text(build(group, origin[0], origin[1], cell["w"], cell["h"],
                              fname, cell["full_shape"]), encoding="utf-8")
        entries.append({"name": fname, "file": path.name, "shape": cell["full_shape"],
                        "w": cell["w"], "h": cell["h"],
                        "ox": origin[0], "oy": origin[1], "group": group})
        print(f'  {fname:44} {cell["w"]:>5g} x {cell["h"]:<5g}  {cell["full_shape"]}')

    manifest = [{k: e[k] for k in ("name", "file", "shape", "w", "h")}
                for e in entries]
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\n{len(entries)} symbols written to {out}")
    if skipped:
        print("skipped: " + "; ".join(skipped))

    # Every file must be well-formed on its own, or it is not a usable symbol.
    import xml.etree.ElementTree as ET
    broken = []
    for e in entries:
        try:
            ET.parse(out / e["file"])
        except ET.ParseError as exc:
            broken.append(f'{e["file"]}: {exc}')
    if broken:
        print("MALFORMED:\n  " + "\n  ".join(broken))
        return 1
    print("all symbol files parse as well-formed SVG")
    if args.sheet:
        print("contact sheet:", contact_sheet(entries, out / "_contact_sheet.svg"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
