"""Stroke vocabulary — dash patterns, caps and arrowheads, in Qt.

Ported from the HMI builder's `runtime.js` so a line drawn there and a
line drawn here read the same on paper.

Two things here are worth stating because they are easy to get wrong:

- **Dash lengths are proportional to the stroke width**, not fixed. A
  2 px dashed line and a 10 px dashed line with the same absolute dash
  array do not look like the same pattern — the thick one reads almost
  solid. The builder computes each pattern from the width with a
  floor; Qt's dash arrays are already expressed in width multiples, so
  the port divides through.
- **An arrowhead is drawn in the line's colour and scales with its
  width**, like SVG's `markerUnits="strokeWidth"`. A fixed-size head on
  a heavy line looks broken off.

Every head is authored on the builder's 12x12 marker grid with the tip
at x=12, y=6, so the shapes are the same geometry, just re-expressed.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPen, QPolygonF

#: Pattern -> dash array in WIDTH MULTIPLES, with the builder's floors
#: applied at build time against the real width.
DASH_PATTERNS: dict[str, tuple] = {
    "solid": (),
    "dash": ((4.0, 6.0), (2.5, 4.0)),
    "long_dash": ((8.0, 12.0), (3.0, 5.0)),
    "dot": ((1.0, 1.0), (2.5, 4.0)),
    "dash_dot": ((4.0, 7.0), (2.0, 3.0), (1.0, 1.0), (2.0, 3.0)),
    "dash_dot_dot": ((5.0, 8.0), (2.0, 3.0), (1.0, 1.0), (2.0, 3.0),
                     (1.0, 1.0), (2.0, 3.0)),
}

DASH_TITLES = {
    "solid": "Solid", "dash": "Dashed", "long_dash": "Long dash",
    "dot": "Dotted", "dash_dot": "Dash-dot",
    "dash_dot_dot": "Dash-dot-dot",
}

#: Legacy names kept working — module JSON written before the port
#: uses these and must keep loading (item 4: never rename, alias).
DASH_ALIASES = {"dashdot": "dash_dot", "longdash": "long_dash",
                "dashdotdot": "dash_dot_dot"}

CAP_STYLES = {"butt": Qt.FlatCap, "round": Qt.RoundCap,
              "square": Qt.SquareCap}

#: The builder's fourteen heads, plus "none".
ARROW_HEADS = ("none", "filled_arrow", "open_arrow", "filled_triangle",
               "open_triangle", "stealth", "filled_diamond",
               "open_diamond", "filled_circle", "open_circle",
               "filled_square", "open_square", "bar", "crow_foot",
               "double_arrow")

ARROW_TITLES = {
    "none": "None", "filled_arrow": "Filled arrow",
    "open_arrow": "Open arrow", "filled_triangle": "Filled triangle",
    "open_triangle": "Open triangle", "stealth": "Stealth",
    "filled_diamond": "Filled diamond",
    "open_diamond": "Open diamond", "filled_circle": "Filled circle",
    "open_circle": "Open circle", "filled_square": "Filled square",
    "open_square": "Open square", "bar": "Bar",
    "crow_foot": "Crow's foot", "double_arrow": "Double arrow",
}

#: Legacy arrow vocabulary — `arrow: end|start|both` with one implicit
#: head — maps onto the new per-end fields.
LEGACY_ARROW = {"end": ("none", "filled_arrow"),
                "start": ("filled_arrow", "none"),
                "both": ("filled_arrow", "filled_arrow"),
                "none": ("none", "none")}

#: Head outlines on the 12x12 marker grid, tip at (12, 6). Closed
#: polygons; the `filled` flag decides brush vs pen.
_HEAD_POLYGONS: dict[str, tuple] = {
    "filled_arrow": ((0, 0), (12, 6), (0, 12), (3.4, 6)),
    "open_arrow": ((0, 0), (12, 6), (0, 12), (3.4, 6)),
    "filled_triangle": ((0, 0), (12, 6), (0, 12)),
    "open_triangle": ((0, 0), (12, 6), (0, 12)),
    "stealth": ((0, 0), (12, 6), (0, 12), (5.2, 6)),
    "filled_diamond": ((0, 6), (6, 1.4), (12, 6), (6, 10.6)),
    "open_diamond": ((0, 6), (6, 1.4), (12, 6), (6, 10.6)),
    "filled_square": ((3, 2.2), (11.2, 2.2), (11.2, 9.8), (3, 9.8)),
    "open_square": ((3, 2.2), (11.2, 2.2), (11.2, 9.8), (3, 9.8)),
}

#: Heads that are outlines rather than solids.
_OPEN_HEADS = ("open_arrow", "open_triangle", "open_diamond",
               "open_circle", "open_square")


def dash_lengths(name: str, width: float) -> list:
    """The pattern in ABSOLUTE pixels — the builder's own numbers.

    Proportional to the stroke width with a floor: a 2 px dashed line
    and a 12 px one need different dash lengths to read as the same
    pattern, and a hairline needs the floor or its dots close up into
    a solid line.
    """
    name = DASH_ALIASES.get(name, name)
    spec = DASH_PATTERNS.get(name)
    if not spec:
        return []
    width = max(float(width), 0.1)
    return [max(floor, width * factor) for factor, floor in spec]


def dash_pattern(name: str, width: float) -> list:
    """The same pattern as a Qt dash array, which is expressed in
    width multiples — Qt re-multiplies by the pen width itself, so
    the absolute lengths are divided back out here."""
    width = max(float(width), 0.1)
    return [length / width for length in dash_lengths(name, width)]


def build_pen(colour, width: float, style: str = "solid",
              cap: str = "round") -> QPen:
    """One pen builder for every stroke in the editor."""
    width = max(float(width), 0.1)
    pen = QPen(QColor(colour), width)
    pen.setCapStyle(CAP_STYLES.get(cap, Qt.RoundCap))
    pen.setJoinStyle(Qt.RoundJoin)
    pattern = dash_pattern(style, width)
    if pattern:
        pen.setStyle(Qt.CustomDashLine)
        pen.setDashPattern(pattern)
    else:
        pen.setStyle(Qt.SolidLine)
    return pen


def resolve_arrows(data: dict) -> tuple:
    """(start, end) head names for an item, accepting both the new
    per-end fields and the legacy `arrow` vocabulary."""
    start = data.get("arrow_start")
    end = data.get("arrow_end")
    if start is None and end is None:
        legacy = LEGACY_ARROW.get(data.get("arrow", "none"),
                                  ("none", "none"))
        return legacy
    return (start or "none", end or "none")


def draw_arrow_head(painter, name: str, tip: QPointF, tail: QPointF,
                    colour, width: float, size: str = "medium") -> None:
    """One head at `tip`, pointing away from `tail`.

    Scales with the stroke width the way `markerUnits="strokeWidth"`
    does, times the item's own small/medium/large choice.
    """
    if not name or name == "none":
        return
    scale = {"small": 0.62, "medium": 1.0,
             "large": 1.45}.get(size, 1.0)
    unit = max(float(width), 1.0) * 0.75 * scale
    angle = math.atan2(tip.y() - tail.y(), tip.x() - tail.x())
    colour = QColor(colour)
    painter.save()
    painter.translate(tip)
    painter.rotate(math.degrees(angle))
    filled = name not in _OPEN_HEADS
    pen = QPen(colour, max(width * 0.55, 0.8))
    pen.setJoinStyle(Qt.MiterJoin)
    painter.setPen(pen)
    painter.setBrush(colour if filled else Qt.NoBrush)

    def at(point) -> QPointF:
        # Grid tip (12, 6) becomes the origin, pointing +x.
        return QPointF((point[0] - 12.0) * unit,
                       (point[1] - 6.0) * unit)

    polygon = _HEAD_POLYGONS.get(name)
    if polygon is not None:
        painter.drawPolygon(QPolygonF([at(p) for p in polygon]))
    elif name in ("filled_circle", "open_circle"):
        painter.drawEllipse(at((6.6, 6.0)), 4.6 * unit, 4.6 * unit)
    elif name == "bar":
        painter.setPen(QPen(colour, max(width * 0.9, 1.2)))
        painter.drawLine(at((11.0, 0.6)), at((11.0, 11.4)))
    elif name == "crow_foot":
        painter.setPen(QPen(colour, max(width * 0.7, 1.0)))
        for end in ((0.5, 0.5), (0.5, 11.5)):
            painter.drawLine(at((12.0, 6.0)), at(end))
        painter.drawLine(at((12.0, 6.0)), at((0.5, 6.0)))
    elif name == "double_arrow":
        head = _HEAD_POLYGONS["filled_arrow"]
        painter.drawPolygon(QPolygonF([at(p) for p in head]))
        painter.translate(-6.6 * unit, 0)
        painter.drawPolygon(QPolygonF([at(p) for p in head]))
    painter.restore()
