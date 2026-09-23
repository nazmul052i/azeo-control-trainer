"""Shape geometry — the drawing tool's primitive vocabulary, in Qt.

Ported from the HMI builder's `primitives.js` so the two tools draw the
*same* shapes: every closed primitive is authored on a normalized
100x100 grid and stretched into the item's rectangle, exactly as the
builder's `viewBox="0 0 100 100" preserveAspectRatio="none"` does. A
shape drawn there and a shape drawn here are then the same shape, which
is the point — the builder is where artwork is authored and this is
where it is operated.

Two families:

- **fixed** — one authored outline (triangle, diamond, cloud, …).
- **adjustable** — generated from parameters the author edits live:
  regular polygons of 3..64 sides, 4..12-point stars with an inset
  ratio, a trapezoid's taper, a parallelogram's slant, a chevron's
  notch depth. `GEOMETRY_DEFAULTS` and `ADJUSTMENT_LIMITS` carry the
  builder's own numbers rather than re-invented ones.

Qt-free apart from `QPainterPath`, and every function is total: an
unknown kind returns None so the caller falls back rather than drawing
a wrong shape.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QPainterPath

#: Authored outlines, normalized to the 100x100 grid. Point lists
#: rather than path strings — the builder's `d` attributes, parsed
#: once here so there is no path parser in the paint loop.
FIXED_SHAPES: dict[str, list[tuple[float, float]]] = {
    "triangle": [(50, 4), (96, 96), (4, 96)],
    "pentagon": [(50, 3), (97, 39), (79, 96), (21, 96), (3, 39)],
    "hexagon": [(25, 4), (75, 4), (98, 50), (75, 96), (25, 96),
                (2, 50)],
    "octagon": [(30, 3), (70, 3), (97, 30), (97, 70), (70, 97),
                (30, 97), (3, 70), (3, 30)],
    "diamond": [(50, 3), (97, 50), (50, 97), (3, 50)],
    "trapezoid": [(20, 4), (80, 4), (98, 96), (2, 96)],
    "parallelogram": [(24, 4), (98, 4), (76, 96), (2, 96)],
    "chevron": [(3, 4), (55, 4), (97, 50), (55, 96), (3, 96),
                (45, 50)],
    "arrow_shape": [(3, 32), (62, 32), (62, 8), (98, 50), (62, 92),
                    (62, 68), (3, 68)],
    "star": [(50, 3), (61, 36), (96, 36), (68, 57), (79, 92),
             (50, 71), (21, 92), (32, 57), (4, 36), (39, 36)],
}

#: The cloud is the one outline that is genuinely curved; kept as its
#: cubic segments (start, then (c1, c2, end) triples).
CLOUD_START = (23.0, 79.0)
CLOUD_CURVES = [
    ((8, 79), (3, 66), (10, 55)),
    ((3, 40), (17, 27), (31, 33)),
    ((37, 13), (63, 10), (73, 29)),
    ((91, 26), (101, 45), (92, 59)),
    ((101, 72), (88, 84), (73, 80)),
]

#: Kinds whose outline is generated from parameters.
ADJUSTABLE_SHAPES = ("polygon", "star", "trapezoid", "parallelogram",
                     "chevron")

#: The builder's defaults: (vertices, adjustment).
GEOMETRY_DEFAULTS: dict[str, tuple[int, float]] = {
    "polygon": (6, 50.0),
    "trapezoid": (4, 20.0),
    "parallelogram": (4, 22.0),
    "chevron": (6, 45.0),
    "star": (5, 45.0),
}

#: Adjustment ranges, per kind — outside these the outline degenerates.
ADJUSTMENT_LIMITS: dict[str, tuple[float, float]] = {
    "polygon": (0.0, 100.0),
    "trapezoid": (0.0, 45.0),
    "parallelogram": (0.0, 45.0),
    "chevron": (10.0, 55.0),
    "star": (15.0, 80.0),
}

MAX_POLYGON_SIDES = 64
MAX_STAR_POINTS = 12

#: Every closed primitive this module can draw — the palette's
#: Shapes section is built from this, so adding one here offers it.
SHAPE_KINDS = ("chord", "pie", "triangle", "pentagon", "hexagon", "octagon",
               "diamond", "trapezoid", "parallelogram", "chevron",
               "arrow_shape", "star", "cloud", "polygon")

#: Human titles for the palette and the property pane.
SHAPE_TITLES = {
    "chord": "Chord", "pie": "Pie",
    "triangle": "Triangle", "pentagon": "Pentagon",
    "hexagon": "Hexagon", "octagon": "Octagon",
    "diamond": "Diamond", "trapezoid": "Trapezoid",
    "parallelogram": "Parallelogram", "chevron": "Chevron",
    "arrow_shape": "Block Arrow", "star": "Star", "cloud": "Cloud",
    "polygon": "Regular Polygon",
}


def _bounded(value, fallback: float, minimum: float,
             maximum: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    if not math.isfinite(numeric):
        return float(fallback)
    return min(maximum, max(minimum, numeric))


def vertex_maximum(kind: str) -> int:
    return MAX_POLYGON_SIDES if kind == "polygon" else MAX_STAR_POINTS


def vertex_total(data: dict, kind: str) -> int:
    """Sides (polygon) or points (star), clamped to what the outline
    can actually be drawn with."""
    default = GEOMETRY_DEFAULTS.get(kind, (5, 25.0))[0]
    minimum = 4 if kind == "star" else 3
    return int(round(_bounded(data.get("sides"), default, minimum,
                              vertex_maximum(kind))))


def adjustment_amount(data: dict, kind: str) -> float:
    """The kind's one shape parameter: star inset, trapezoid taper,
    parallelogram slant, chevron notch."""
    default = GEOMETRY_DEFAULTS.get(kind, (5, 25.0))[1]
    low, high = ADJUSTMENT_LIMITS.get(kind, (0.0, 100.0))
    return _bounded(data.get("adjust"), default, low, high)


def is_adjustable(kind: str) -> bool:
    return kind in ADJUSTABLE_SHAPES


def _radial_points(vertices: int,
                   inner_ratio: float | None = None) -> list:
    """A regular polygon, or a star when an inner ratio is given —
    the builder's own radius of 47 about (50, 50), first point up."""
    count = vertices if inner_ratio is None else vertices * 2
    points = []
    for index in range(count):
        angle = -math.pi / 2 + index * math.pi * 2 / count
        radius = 47.0 * inner_ratio \
            if inner_ratio is not None and index % 2 == 1 else 47.0
        points.append((50 + math.cos(angle) * radius,
                       50 + math.sin(angle) * radius))
    return points


def normalized_points(kind: str, data: dict | None = None) -> list:
    """The outline on the 100x100 grid, or [] for a curved/unknown
    kind. `data` supplies `sides` / `adjust` for adjustable kinds."""
    data = data or {}
    if kind == "polygon":
        return _radial_points(vertex_total(data, kind))
    if kind == "star":
        return _radial_points(vertex_total(data, kind),
                              adjustment_amount(data, kind) / 100.0)
    if kind == "trapezoid":
        inset = adjustment_amount(data, kind)
        return [(inset, 4), (100 - inset, 4), (98, 96), (2, 96)]
    if kind == "parallelogram":
        slant = adjustment_amount(data, kind)
        return [(slant, 4), (98, 4), (98 - slant, 96), (2, 96)]
    if kind == "chevron":
        notch = adjustment_amount(data, kind)
        return [(3, 4), (55, 4), (97, 50), (55, 96), (3, 96),
                (notch, 50)]
    return list(FIXED_SHAPES.get(kind, []))


def shape_path(kind: str, rect: QRectF,
               data: dict | None = None) -> QPainterPath | None:
    """The outline as a closed QPainterPath filling `rect`.

    Returns None for a kind this module does not draw, so a caller can
    fall back to its own geometry instead of painting something wrong.
    """
    if rect.width() <= 0 or rect.height() <= 0:
        return None
    sx, sy = rect.width() / 100.0, rect.height() / 100.0

    def at(point) -> QPointF:
        return QPointF(rect.left() + point[0] * sx,
                       rect.top() + point[1] * sy)

    if kind in ("chord", "pie"):
        data = data or {}
        try:
            start = float(data.get("start", 0.0))
            span = float(data.get("span", 180.0))
        except (TypeError, ValueError):
            start, span = 0.0, 180.0
        path = QPainterPath()
        if kind == "pie":
            path.moveTo(rect.center())
            arc_start = QPainterPath()
            arc_start.arcMoveTo(rect, start)
            path.lineTo(arc_start.currentPosition())
        else:
            path.arcMoveTo(rect, start)
        path.arcTo(rect, start, span)
        path.closeSubpath()
        return path

    if kind == "cloud":
        path = QPainterPath(at(CLOUD_START))
        for c1, c2, end in CLOUD_CURVES:
            path.cubicTo(at(c1), at(c2), at(end))
        path.closeSubpath()
        return path
    points = normalized_points(kind, data)
    if not points:
        return None
    path = QPainterPath(at(points[0]))
    for point in points[1:]:
        path.lineTo(at(point))
    path.closeSubpath()
    return path


def rounded_rect_path(rect: QRectF, radius: float) -> QPainterPath:
    """A rectangle with a corner radius, clamped so the radius can
    never exceed half the shorter side (which would invert it)."""
    limit = min(rect.width(), rect.height()) / 2.0
    radius = max(0.0, min(float(radius), limit))
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


# ------------------------------------------------------------ freehand
def point_segment_distance(point, start, end) -> float:
    px, py = point
    ax, ay = start
    bx, by = end
    dx, dy = bx - ax, by - ay
    if abs(dx) < 1e-12 and abs(dy) < 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy)
                     / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def simplify_points(points: list, tolerance: float = 2.0) -> list:
    """Radial thinning then Ramer–Douglas–Peucker, the builder's two
    passes in order: drop samples closer than 0.65*tolerance to the
    last kept one, then drop points that lie within tolerance of the
    chord they sit on. A freehand stroke is hundreds of samples; kept
    raw it is slow to draw and impossible to edit."""
    if len(points) <= 2:
        return list(points)
    radial = [points[0]]
    for point in points[1:-1]:
        previous = radial[-1]
        if math.hypot(point[0] - previous[0],
                      point[1] - previous[1]) >= tolerance * 0.65:
            radial.append(point)
    radial.append(points[-1])
    if len(radial) <= 2:
        return radial
    keep = {0, len(radial) - 1}
    ranges = [(0, len(radial) - 1)]
    while ranges:
        start_index, end_index = ranges.pop()
        maximum = tolerance
        split_index = -1
        for index in range(start_index + 1, end_index):
            distance = point_segment_distance(
                radial[index], radial[start_index], radial[end_index])
            if distance > maximum:
                maximum = distance
                split_index = index
        if split_index > 0:
            keep.add(split_index)
            ranges.append((start_index, split_index))
            ranges.append((split_index, end_index))
    return [point for index, point in enumerate(radial)
            if index in keep]


def smoothed_path(points: list) -> QPainterPath:
    """The builder's freehand smoothing: quadratic segments through
    the midpoints, so the stroke curves rather than showing every
    sampled corner."""
    path = QPainterPath()
    if not points:
        return path
    path.moveTo(QPointF(*points[0]))
    if len(points) == 1:
        return path
    for index in range(1, len(points) - 1):
        current = points[index]
        following = points[index + 1]
        midpoint = QPointF((current[0] + following[0]) / 2.0,
                           (current[1] + following[1]) / 2.0)
        path.quadTo(QPointF(*current), midpoint)
    path.lineTo(QPointF(*points[-1]))
    return path
