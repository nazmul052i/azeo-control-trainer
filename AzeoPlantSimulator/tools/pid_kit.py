"""Drawing kit for ISA-5.1 piping and instrumentation diagrams.

This is the sheet furniture and the symbol vocabulary. The unit drawings live
in ``make_pids.py`` and use nothing but what is here, so every sheet in the set
comes out with the same line weights, the same bubble geometry and the same
title block.

What is drawn natively and what comes from the library
------------------------------------------------------
Equipment, valves and drivers are placed from ``docs/symbols`` so they are the
issued ISA shapes rather than something approximated here. Instrument bubbles,
signal lines, line numbers, connectors and the sheet furniture are drawn
natively, because they carry text that has to sit exactly right and because
ISA-5.1 distinguishes them by line style rather than by shape.

ISA conventions honoured
------------------------
* **Bubble by location.** Plain circle is field mounted. A solid horizontal
  line through it is a primary location, normally accessible to the operator.
  A dashed line is an auxiliary or field panel. A circle inscribed in a square
  is shared display / shared control, which is what a DCS function is.
* **Tag inside the bubble.** Functional letters above the dividing line, loop
  number below.
* **Line style carries meaning.** Process piping is a heavy solid line;
  electrical signal is dashed; a software or data link is a line with small
  circles on it; utility piping is lighter than process.
* **Valve fail position is annotated** (FC, FO, FL) next to the valve, because
  it is what an operator needs during a trip and it is not visible in the
  symbol.
* **Off-page connectors** name the sheet they continue on, so the set reads as
  a set rather than ten unrelated pictures.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
SYMBOLS_DIR = ROOT / "docs" / "symbols"

# ------------------------------------------------------------------ sheet size
# A2 landscape at 100 px per 25.4 mm, near enough for a screen-read drawing that
# also prints sensibly.
SHEET_W, SHEET_H = 1700, 1200

# ----------------------------------------------------------------- ink and paper
PAPER = "#FFFFFF"
INK = "#111111"
LINE_PROCESS = "#111111"
LINE_UTILITY = "#4A4A4A"
LINE_SIGNAL = "#111111"
EQUIP_FILL = "#FFFFFF"
EQUIP_EDGE = "#111111"
HATCH = "#6E6E6E"
MUTED = "#5A5A5A"

W_PROCESS = 2.6          # major process piping
W_SECONDARY = 1.8        # utility and minor piping
W_SIGNAL = 1.1           # instrument signal
W_FRAME = 1.4
W_SYMBOL = 1.4           # library symbols are normalised to this

FONT = "Arial, Helvetica, sans-serif"

_SYMBOL_CACHE: Dict[str, Tuple[float, float, str]] = {}


def symbol(name: str) -> Tuple[float, float, str]:
    """Native size and geometry of one library symbol."""
    if name not in _SYMBOL_CACHE:
        path = SYMBOLS_DIR / f"{name}.svg"
        if not path.exists():
            raise SystemExit(f"symbol {name!r} not in {SYMBOLS_DIR}")
        txt = path.read_text(encoding="utf-8")
        w = float(re.search(r'\swidth="([\d.]+)"', txt).group(1))
        h = float(re.search(r'\sheight="([\d.]+)"', txt).group(1))
        body = txt[txt.index("<g transform="):txt.rindex("</svg>")].strip()
        _SYMBOL_CACHE[name] = (w, h, body)
    return _SYMBOL_CACHE[name]


class Placed:
    """Where a symbol actually landed, in sheet coordinates.

    ``x0..x1`` and ``y0..y1`` are the **drawn** extent, not the symbol's
    bounding box, so a pipe routed to ``left`` or ``right`` meets the ink
    rather than stopping short of it. ``cy`` is the connection axis: for a
    valve that is the centreline of the body, which is not the middle of the
    symbol once the actuator is included.
    """

    __slots__ = ("cx", "cy", "ax", "w", "h", "x0", "x1", "y0", "y1",
                 "_g", "_s", "_left", "_top")

    def __init__(self, cx, cy, ax, w, h, x0, x1, y0, y1,
                 g=None, s=1.0, left=0.0, top=0.0):
        self.cx, self.cy, self.ax = cx, cy, ax
        self.w, self.h = w, h
        self.x0, self.x1, self.y0, self.y1 = x0, x1, y0, y1
        self._g, self._s, self._left, self._top = g, s, left, top

    def span_at(self, y: float) -> Tuple[float, float]:
        """Left and right edge of the drawn symbol at sheet height ``y``.

        A furnace is wider at its base and a vessel narrows into its heads, so
        the bounding box is the wrong thing to route a pipe to. This reads the
        measured ink profile at the height the line actually arrives at, and
        falls back to the nearest painted row when ``y`` is off the symbol.
        """
        g = self._g
        if not g or not g.get("_rows"):
            return (self.x0, self.x1)
        rows = g["_rows"]
        n = len(rows)
        i = int((y - self._top) / self._s / g["_h"] * n)
        i = max(0, min(n - 1, i))
        for d in range(n):
            for j in (i - d, i + d):
                if 0 <= j < n and rows[j] is not None:
                    a, b = rows[j]
                    return (self._left + a * self._s, self._left + b * self._s)
        return (self.x0, self.x1)

    def left_at(self, y: float) -> Tuple[float, float]:
        """Connection point on the left face, at this height."""
        return (self.span_at(y)[0], y)

    def right_at(self, y: float) -> Tuple[float, float]:
        """Connection point on the right face, at this height."""
        return (self.span_at(y)[1], y)

    @property
    def left(self):
        return (self.x0, self.cy)

    @property
    def right(self):
        return (self.x1, self.cy)

    @property
    def top(self):
        """Vertical connection, on the nozzle rather than the box centre."""
        return (self.ax, self.y0)

    @property
    def bottom(self):
        return (self.ax, self.y1)


_GEOM_CACHE: Dict[str, Dict[str, float]] = {}


def geometry(name: str) -> Dict[str, float]:
    """Ink extent and connection axes of a symbol, in its own viewBox units.

    Measured by rasterising the symbol and looking at which pixels are
    actually painted. Doing it by measurement rather than from a hand-kept
    table means a symbol can be swapped for a different one and every drawing
    that uses it still connects correctly.

    The horizontal axis is taken from the middle of the ink in the leftmost
    painted column, which for a valve is the tip of the body and therefore the
    line the pipe runs along, not the centre of the symbol with its actuator.
    """
    if name in _GEOM_CACHE:
        return _GEOM_CACHE[name]

    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    w, h, _ = symbol(name)
    n = 256
    scale = n / max(w, h)
    iw, ih = max(int(round(w * scale)), 1), max(int(round(h * scale)), 1)
    img = QImage(iw, ih, QImage.Format_ARGB32)
    img.fill(0)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.Antialiasing, False)
    QSvgRenderer(str(SYMBOLS_DIR / f"{name}.svg")).render(painter, QRectF(0, 0, iw, ih))
    painter.end()

    cols = [[] for _ in range(iw)]
    rows = [[] for _ in range(ih)]
    for y in range(ih):
        for x in range(iw):
            if (img.pixel(x, y) >> 24) & 0xFF > 40:
                cols[x].append(y)
                rows[y].append(x)

    painted_cols = [x for x in range(iw) if cols[x]]
    painted_rows = [y for y in range(ih) if rows[y]]
    if not painted_cols:
        g = {"x0": 0.0, "x1": w, "y0": 0.0, "y1": h,
             "axis_y": h / 2.0, "axis_x": w / 2.0,
             "_h": h, "_rows": []}
        _GEOM_CACHE[name] = g
        return g

    x0, x1 = painted_cols[0], painted_cols[-1]
    y0, y1 = painted_rows[0], painted_rows[-1]
    band = max(int(round(iw * 0.02)), 1)
    left_rows = sorted({y for x in painted_cols[:band] for y in cols[x]})
    top_cols = sorted({x for y in painted_rows[:band] for x in rows[y]})
    axis_y = (left_rows[0] + left_rows[-1]) / 2.0 if left_rows else ih / 2.0
    axis_x = (top_cols[0] + top_cols[-1]) / 2.0 if top_cols else iw / 2.0

    g = {"x0": x0 / scale, "x1": (x1 + 1) / scale,
         "y0": y0 / scale, "y1": (y1 + 1) / scale,
         "axis_y": axis_y / scale, "axis_x": axis_x / scale,
         "_h": h,
         "_rows": [(rows[y][0] / scale, (rows[y][-1] + 1) / scale)
                   if rows[y] else None for y in range(ih)]}
    _GEOM_CACHE[name] = g
    return g


def _fit(text: str, size: float, avail: float, floor: float = 6.6) -> float:
    """Point size that keeps ``text`` inside ``avail``, to a readable floor."""
    est = 0.60 * size * len(text)
    if est <= avail or not text:
        return size
    return max(floor, size * avail / est)


def esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


class Sheet:
    """One P&ID sheet: frame, title block, and everything drawn on it."""

    def __init__(self, drawing_no: str, title: str, unit: str,
                 sheet: str = "1 of 1", rev: str = "A",
                 w: int = SHEET_W, h: int = SHEET_H) -> None:
        self.w, self.h = w, h
        self.drawing_no = drawing_no
        self.title = title
        self.unit = unit
        self.sheet = sheet
        self.rev = rev
        self.parts: List[str] = []
        self._notes: List[str] = []

        # usable drawing area, inside the frame and above the bottom strip
        self.area = (54.0, 58.0, w - 54.0, h - 196.0)

    # ------------------------------------------------------------- primitives
    def add(self, s: str) -> None:
        self.parts.append("  " + s)

    def rect(self, x, y, w, h, fill="none", stroke=INK, sw=1.2, rx=0) -> None:
        self.add(f'<rect x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" '
                 f'rx="{rx:g}" ry="{rx:g}" fill="{fill}" stroke="{stroke}" '
                 f'stroke-width="{sw:g}"/>')

    def circle(self, cx, cy, r, fill=EQUIP_FILL, stroke=INK, sw=1.4, dash="") -> None:
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<circle cx="{cx:g}" cy="{cy:g}" r="{r:g}" fill="{fill}" '
                 f'stroke="{stroke}" stroke-width="{sw:g}"{d}/>')

    def seg(self, x1, y1, x2, y2, stroke=INK, sw=1.2, dash="") -> None:
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<line x1="{x1:g}" y1="{y1:g}" x2="{x2:g}" y2="{y2:g}" '
                 f'stroke="{stroke}" stroke-width="{sw:g}"{d}/>')

    def path(self, d, fill="none", stroke=INK, sw=1.2, dash="") -> None:
        da = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<path d="{d}" fill="{fill}" stroke="{stroke}" '
                 f'stroke-width="{sw:g}"{da}/>')

    def poly(self, pts, fill=EQUIP_FILL, stroke=INK, sw=1.2) -> None:
        p = " ".join(f"{x:g},{y:g}" for x, y in pts)
        self.add(f'<polygon points="{p}" fill="{fill}" stroke="{stroke}" '
                 f'stroke-width="{sw:g}"/>')

    def text(self, x, y, s, size=11, bold=False, fill=INK, anchor="middle",
             italic=False) -> None:
        w = ' font-weight="bold"' if bold else ""
        i = ' font-style="italic"' if italic else ""
        self.add(f'<text x="{x:g}" y="{y:g}" font-family="{FONT}" '
                 f'font-size="{size:g}"{w}{i} fill="{fill}" '
                 f'text-anchor="{anchor}">{esc(s)}</text>')

    # ---------------------------------------------------------------- symbols
    def place(self, name: str, cx: float, cy: float, height: float = None,
              width: float = None, rotate: float = 0.0,
              on_axis: bool = False) -> Placed:
        """Drop a library symbol and report where its ink actually landed.

        With ``on_axis`` the symbol is positioned so its connection axis, not
        the middle of its bounding box, sits on ``cy``. That is what puts a
        control valve body on the pipe instead of hanging it below the line.

        Stroke width is divided back out by the scale so a symbol placed small
        still draws with the sheet's line weight instead of a hairline.
        """
        w, h, body = symbol(name)
        g = geometry(name)
        s = height / h if height else (width / w if width else 1.0)

        cy_draw = cy + (h / 2.0 - g["axis_y"]) * s if on_axis else cy
        rot = f" rotate({rotate:g})" if rotate else ""
        self.add(f'<g transform="translate({cx:g},{cy_draw:g}){rot} scale({s:g}) '
                 f'translate({-w / 2:g},{-h / 2:g})" '
                 f'stroke-width="{W_SYMBOL / s:g}">{body}</g>')

        left = cx - w * s / 2.0
        top = cy_draw - h * s / 2.0
        return Placed(cx, cy if on_axis else top + g["axis_y"] * s,
                      left + g["axis_x"] * s, w * s, h * s,
                      left + g["x0"] * s, left + g["x1"] * s,
                      top + g["y0"] * s, top + g["y1"] * s,
                      g, s, left, top)

    def equipment(self, name: str, cx: float, cy: float, tag: str,
                  service: str = "", height: float = None, width: float = None,
                  label: str = "above", rotate: float = 0.0,
                  on_axis: bool = False) -> Placed:
        """Symbol plus its equipment tag and service description."""
        p = self.place(name, cx, cy, height, width, rotate, on_axis)
        if label == "above":
            self.text(cx, p.y0 - 22, tag, 15, True)
            if service:
                self.text(cx, p.y0 - 8, service, 10, False, MUTED)
        elif label == "below":
            self.text(cx, p.y1 + 20, tag, 15, True)
            if service:
                self.text(cx, p.y1 + 34, service, 10, False, MUTED)
        elif label == "left":
            self.text(p.x0 - 12, p.cy - 2, tag, 15, True, anchor="end")
            if service:
                self.text(p.x0 - 12, p.cy + 12, service, 10, False, MUTED,
                          anchor="end")
        elif label == "right":
            self.text(p.x1 + 12, p.cy - 2, tag, 15, True, anchor="start")
            if service:
                self.text(p.x1 + 12, p.cy + 12, service, 10, False, MUTED,
                          anchor="start")
        return p

    # ------------------------------------------------------------- instruments
    def bubble(self, cx: float, cy: float, tag: str, mount: str = "field",
               r: float = 21.0, leader: Optional[Tuple[float, float]] = None,
               leader_dash: str = "6,4") -> None:
        """ISA instrument balloon.

        ``mount`` selects the location convention: ``field`` plain circle,
        ``room`` primary location with a solid dividing line, ``aux``
        auxiliary location with a dashed line, ``dcs`` shared display and
        shared control drawn as a circle inscribed in a square.
        """
        if leader:
            self.seg(cx, cy, leader[0], leader[1], LINE_SIGNAL, W_SIGNAL,
                     dash=leader_dash)
        if mount == "dcs":
            self.rect(cx - r, cy - r, 2 * r, 2 * r, EQUIP_FILL, INK, 1.4)
        self.circle(cx, cy, r, EQUIP_FILL, INK, 1.4)
        if mount in ("room", "dcs"):
            self.seg(cx - r, cy, cx + r, cy, INK, 1.2)
        elif mount == "aux":
            self.seg(cx - r, cy, cx + r, cy, INK, 1.2, dash="5,3")

        letters, _, number = tag.partition("-")
        if mount == "field":
            self.text(cx, cy - 2, letters, 11, True)
            self.text(cx, cy + 11, number, 10)
        else:
            self.text(cx, cy - 5, letters, 11, True)
            self.text(cx, cy + 14, number, 10)

    def valve(self, symbol_name: str, cx: float, cy: float, tag: str,
              width: float = 56.0, label: str = "above", fail: str = "",
              rotate: float = 0.0) -> Placed:
        """Valve seated on the line, with its tag and fail position.

        ``cy`` is the pipe centreline. The symbol is aligned to its body axis,
        so an actuator on top does not push the body off the line.
        """
        p = self.equipment(symbol_name, cx, cy, tag, "", width=width,
                           label=label, rotate=rotate, on_axis=True)
        if fail:
            # A bottom-labelled valve is one the DCS drives from above, so the
            # fail position goes under the tag and leaves the stem clear for
            # the signal to land on.
            if label == "below":
                self.text(cx, p.y1 + 36, fail, 9, True)
            else:
                self.text(cx, p.y1 + 15, fail, 9, True)
        return p

    def strainer(self, cx: float, cy: float, tag: str = "",
                 orient: str = "h") -> None:
        """In-line strainer: body with the screen shown as a diagonal.

        ``orient`` follows the run it sits in, so a strainer in a vertical
        suction line is drawn along that line rather than across it.
        """
        w, h = (34.0, 20.0) if orient == "h" else (20.0, 34.0)
        self.rect(cx - w / 2, cy - h / 2, w, h, EQUIP_FILL, INK, 1.3)
        self.seg(cx - w / 2, cy + h / 2, cx + w / 2, cy - h / 2, INK, 1.2)
        if tag:
            if orient == "h":
                self.text(cx, cy + h / 2 + 15, tag, 9.5, True)
            else:
                self.text(cx + w / 2 + 10, cy + 4, tag, 9.5, True, anchor="start")

    def signal(self, pts: Sequence[Tuple[float, float]], kind: str = "electric") -> None:
        """Instrument signal run. ``electric`` dashed, ``software`` beaded."""
        d = f"M {pts[0][0]:g} {pts[0][1]:g}" + "".join(
            f" L {x:g} {y:g}" for x, y in pts[1:])
        if kind == "software":
            self.path(d, stroke=LINE_SIGNAL, sw=W_SIGNAL)
            total = _length(pts)
            n = max(int(total // 26), 1)
            for i in range(1, n + 1):
                x, y = _along(pts, i / (n + 1))
                self.circle(x, y, 3.0, PAPER, LINE_SIGNAL, 1.0)
        else:
            self.path(d, stroke=LINE_SIGNAL, sw=W_SIGNAL, dash="7,4")

    # ------------------------------------------------------------------- pipes
    def pipe(self, pts: Sequence[Tuple[float, float]], kind: str = "process",
             arrow_at: Optional[float] = None, number: str = "",
             number_at: float = 0.5, number_dy: float = -8.0) -> None:
        """Process or utility piping, optionally carrying its line number."""
        stroke = LINE_PROCESS if kind == "process" else LINE_UTILITY
        sw = W_PROCESS if kind == "process" else W_SECONDARY
        d = f"M {pts[0][0]:g} {pts[0][1]:g}" + "".join(
            f" L {x:g} {y:g}" for x, y in pts[1:])
        self.path(d, stroke=stroke, sw=sw)
        if arrow_at is not None:
            x, y = _along(pts, arrow_at)
            dx, dy = _direction(pts, arrow_at)
            self._arrow(x, y, dx, dy, stroke)
        if number:
            x, y = _along(pts, number_at)
            dx, dy = _direction(pts, number_at)
            if abs(dx) >= abs(dy):
                self.text(x, y + number_dy, number, 9, False, INK)
            else:
                self.text(x + 6, y, number, 9, False, INK, anchor="start")

    def _arrow(self, x, y, dx, dy, colour) -> None:
        a = math.atan2(dy, dx)
        c, s = math.cos(a), math.sin(a)
        self.poly([(x + 9 * c, y + 9 * s),
                   (x - 4 * c + 5 * s, y - 4 * s - 5 * c),
                   (x - 4 * c - 5 * s, y - 4 * s + 5 * c)], colour, colour, 1.0)

    def connector(self, x: float, y: float, text: str, sheet_ref: str,
                  direction: str = "right") -> None:
        """Off-page continuation flag: where the line goes and on which sheet."""
        w, h = 132.0, 26.0
        tip = 15.0
        if direction == "right":
            pts = [(x, y - h / 2), (x + w - tip, y - h / 2), (x + w, y),
                   (x + w - tip, y + h / 2), (x, y + h / 2)]
            tx, anchor = x + 8, "start"
        else:
            pts = [(x + w, y - h / 2), (x + tip, y - h / 2), (x, y),
                   (x + tip, y + h / 2), (x + w, y + h / 2)]
            tx, anchor = x + w - 8, "end"
        self.poly(pts, PAPER, INK, 1.3)
        # A destination that will not fit is set smaller rather than allowed to
        # run out through the point of the flag.
        avail = w - tip - 16.0
        self.text(tx, y - 1, text, _fit(text, 9.5, avail), True, INK,
                  anchor=anchor)
        self.text(tx, y + 10, sheet_ref, _fit(sheet_ref, 8.5, avail), False,
                  MUTED, anchor=anchor)

    def spec_break(self, x: float, y: float, label: str = "") -> None:
        """Line spec change, drawn as the usual paired ticks."""
        self.seg(x, y - 9, x, y + 9, INK, 1.2)
        self.seg(x + 5, y - 9, x + 5, y + 9, INK, 1.2)
        if label:
            self.text(x + 2.5, y - 13, label, 8, False, MUTED)

    # ------------------------------------------------------------- sheet frame
    def frame(self) -> None:
        self.add(f'<rect x="0" y="0" width="{self.w}" height="{self.h}" fill="{PAPER}"/>')
        self.rect(14, 14, self.w - 28, self.h - 28, "none", INK, 2.0)
        self.rect(38, 38, self.w - 76, self.h - 76, "none", INK, W_FRAME)

        # zone markers, the way a drawing office numbers a sheet for reference
        cols, rows = 8, 4
        inner_w, inner_h = self.w - 76, self.h - 76
        for i in range(cols):
            x = 38 + inner_w * (i + 0.5) / cols
            self.text(x, 32, str(i + 1), 10, True)
            self.text(x, self.h - 22, str(i + 1), 10, True)
            if i:
                gx = 38 + inner_w * i / cols
                self.seg(gx, 14, gx, 38, INK, 1.0)
                self.seg(gx, self.h - 38, gx, self.h - 14, INK, 1.0)
        for j in range(rows):
            y = 38 + inner_h * (j + 0.5) / rows
            self.text(26, y + 4, chr(ord("A") + j), 10, True)
            self.text(self.w - 26, y + 4, chr(ord("A") + j), 10, True)
            if j:
                gy = 38 + inner_h * j / rows
                self.seg(14, gy, 38, gy, INK, 1.0)
                self.seg(self.w - 38, gy, self.w - 14, gy, INK, 1.0)

    def notes(self, lines: Iterable[str]) -> None:
        self._notes = list(lines)

    def bottom_strip(self, legend: Sequence[Tuple[str, str]]) -> None:
        """Notes, legend and title block along the foot of the sheet."""
        top = self.h - 186
        bot = self.h - 42
        h = bot - top

        # ---- notes
        nx, nw = 42, 470
        self.rect(nx, top, nw, h, "none", INK, W_FRAME)
        self.text(nx + 10, top + 18, "NOTES", 11, True, INK, anchor="start")
        self.seg(nx, top + 26, nx + nw, top + 26, INK, 1.0)
        for i, line in enumerate(self._notes[:9]):
            self.text(nx + 10, top + 42 + i * 12.5, line, 8.6, False, INK,
                      anchor="start")

        # ---- legend
        lx, lw = nx + nw + 8, 540
        self.rect(lx, top, lw, h, "none", INK, W_FRAME)
        self.text(lx + 10, top + 18, "LEGEND", 11, True, INK, anchor="start")
        self.seg(lx, top + 26, lx + lw, top + 26, INK, 1.0)
        for i, (kind, label) in enumerate(legend[:8]):
            col, row = divmod(i, 4)
            ex = lx + 12 + col * 268
            ey = top + 44 + row * 25
            self._legend_mark(kind, ex, ey)
            self.text(ex + 62, ey + 4, label, 8.8, False, INK, anchor="start")

        # ---- title block
        tx = lx + lw + 8
        tw = self.w - 42 - tx
        self.rect(tx, top, tw, h, "none", INK, W_FRAME)
        self.text(tx + tw / 2, top + 22, "AZEOPLANT PROCESS TRAINING UNIT", 12, True)
        self.text(tx + tw / 2, top + 38, "Open loop dynamic simulator", 8.6, False, MUTED)
        self.seg(tx, top + 46, tx + tw, top + 46, INK, 1.0)
        self.text(tx + tw / 2, top + 66, self.title.upper(), 12.5, True)
        self.text(tx + tw / 2, top + 82, f"UNIT {self.unit}", 9.5, False, MUTED)
        self.seg(tx, top + 92, tx + tw, top + 92, INK, 1.0)

        cells = [("DRAWING No.", self.drawing_no), ("REV", self.rev),
                 ("SCALE", "NONE"), ("SHEET", self.sheet)]
        cw = tw / 4
        for i, (k, v) in enumerate(cells):
            cx = tx + i * cw
            if i:
                self.seg(cx, top + 92, cx, top + 130, INK, 1.0)
            self.text(cx + cw / 2, top + 106, k, 7.5, False, MUTED)
            self.text(cx + cw / 2, top + 124, v, 10.5, True)
        self.seg(tx, top + 130, tx + tw, top + 130, INK, 1.0)
        self.text(tx + 8, top + 142, "ISSUED FOR TRAINING  ·  NOT FOR CONSTRUCTION",
                  8, True, INK, anchor="start")

    def _legend_mark(self, kind: str, x: float, y: float) -> None:
        if kind == "process":
            self.seg(x, y, x + 48, y, LINE_PROCESS, W_PROCESS)
        elif kind == "utility":
            self.seg(x, y, x + 48, y, LINE_UTILITY, W_SECONDARY)
        elif kind == "signal":
            self.seg(x, y, x + 48, y, LINE_SIGNAL, W_SIGNAL, dash="7,4")
        elif kind == "software":
            self.seg(x, y, x + 48, y, LINE_SIGNAL, W_SIGNAL)
            self.circle(x + 24, y, 3.0, PAPER, LINE_SIGNAL, 1.0)
        elif kind == "field":
            self.circle(x + 24, y, 11, EQUIP_FILL, INK, 1.3)
        elif kind == "room":
            self.circle(x + 24, y, 11, EQUIP_FILL, INK, 1.3)
            self.seg(x + 13, y, x + 35, y, INK, 1.1)
        elif kind == "dcs":
            self.rect(x + 13, y - 11, 22, 22, EQUIP_FILL, INK, 1.3)
            self.circle(x + 24, y, 11, EQUIP_FILL, INK, 1.3)
            self.seg(x + 13, y, x + 35, y, INK, 1.1)
        elif kind == "connector":
            self.poly([(x, y - 9), (x + 36, y - 9), (x + 48, y),
                       (x + 36, y + 9), (x, y + 9)], PAPER, INK, 1.2)

    # ------------------------------------------------------------------ output
    def svg(self) -> str:
        return ('<?xml version="1.0" encoding="utf-8"?>\n'
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" '
                f'height="{self.h}" viewBox="0 0 {self.w} {self.h}" '
                'preserveAspectRatio="xMidYMid meet" version="1.1">\n'
                + "\n".join(self.parts) + "\n</svg>\n")

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.svg(), encoding="utf-8")
        return path


# --------------------------------------------------------------------- helpers
def _length(pts: Sequence[Tuple[float, float]]) -> float:
    return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def _along(pts: Sequence[Tuple[float, float]], f: float) -> Tuple[float, float]:
    target, walked = _length(pts) * f, 0.0
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        seg = math.dist(a, b)
        if walked + seg >= target and seg > 1e-9:
            t = (target - walked) / seg
            return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
        walked += seg
    return pts[-1]


def _direction(pts: Sequence[Tuple[float, float]], f: float) -> Tuple[float, float]:
    target, walked = _length(pts) * f, 0.0
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        seg = math.dist(a, b)
        if walked + seg >= target and seg > 1e-9:
            return (b[0] - a[0]) / seg, (b[1] - a[1]) / seg
        walked += seg
    a, b = pts[-2], pts[-1]
    seg = max(math.dist(a, b), 1e-9)
    return (b[0] - a[0]) / seg, (b[1] - a[1]) / seg


STANDARD_LEGEND: List[Tuple[str, str]] = [
    ("process", "Major process line"),
    ("utility", "Utility / secondary line"),
    ("signal", "Electrical signal"),
    ("software", "Software / data link"),
    ("connector", "Off-page connector"),
    ("field", "Field mounted instrument"),
    ("room", "Primary location, accessible"),
    ("dcs", "Shared display / shared control"),
]
