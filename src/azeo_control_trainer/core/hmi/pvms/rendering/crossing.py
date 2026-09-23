"""The Crossover Effect — where one line crosses another, break the
stroke or hop over it (graphics paper pp.27-28).

Detected live against the scene rather than authored, so moving
either line moves the effect. The segment list is cached against the
studio's geometry generation because this runs once per item
PAINTED: recomputing it walked the whole scene for every crossover
line on the display.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF


#: Open strokes which participate in the smart crossover engine.  Keeping
#: this vocabulary here means the collector, painters, property pane and
#: ribbon cannot quietly disagree about which elements support Break/Jump.
CROSSOVER_KINDS = frozenset(("line", "polyline", "freehand", "arc",
                             "pipe"))


def stroke_points(item) -> list[QPointF]:
    """Return an open stroke as editable/local polyline points.

    Crossovers are a segment operation.  Lines and connectors already are
    segmented, while arcs and smoothed freeforms need a faithful sampled
    representation.  The sampling lives outside ``paint()``'s item-specific
    branches so the segment cache and the rendered Break/Jump use exactly the
    same geometry.
    """
    data = getattr(item, "data", None)
    data = data if isinstance(data, dict) else {}
    kind = data.get("kind")
    if kind == "pipe":
        return [QPointF(point) for point in getattr(item, "_points", ())]
    rect = item.rect()
    if kind == "line":
        middle = rect.center().y()
        return [QPointF(rect.left(), middle),
                QPointF(rect.right(), middle)]
    raw = data.get("points") or []
    if kind == "polyline":
        if not raw:
            raw = [[0, rect.height()],
                   [rect.width() * 0.4, rect.height() * 0.3],
                   [rect.width(), rect.height() * 0.6]]
        return [QPointF(float(point[0]), float(point[1]))
                for point in raw]
    if kind == "freehand":
        if len(raw) < 2:
            return []
        # A raw freeform is painted as quadratic curves.  Sampling the same
        # path avoids the old lie where a crossing was detected against the
        # control polygon but the visible curve missed it.
        from azeo_control_trainer.core.hmi.pvms.shapes import smoothed_path
        path = smoothed_path([(float(p[0]), float(p[1])) for p in raw])
        samples = max(12, min(192, len(raw) * 6))
        return [path.pointAtPercent(index / samples)
                for index in range(samples + 1)]
    if kind == "arc":
        try:
            start = float(data.get("start", 0.0))
            span = float(data.get("span", 180.0))
        except (TypeError, ValueError):
            start, span = 0.0, 180.0
        steps = max(12, min(180, int(math.ceil(abs(span) / 4.0))))
        centre = rect.center()
        rx, ry = rect.width() / 2.0, rect.height() / 2.0
        points = []
        for index in range(steps + 1):
            angle = math.radians(start + span * index / steps)
            # Qt's positive arc angle is counter-clockwise in a y-down
            # coordinate system, hence the minus on sine.
            points.append(QPointF(centre.x() + rx * math.cos(angle),
                                  centre.y() - ry * math.sin(angle)))
        return points
    return []


def _seg_cross(a1, a2, b1, b2):
    """Proper crossing of two segments (never at shared endpoints)."""
    d1x, d1y = a2.x() - a1.x(), a2.y() - a1.y()
    d2x, d2y = b2.x() - b1.x(), b2.y() - b1.y()
    denom = d1x * d2y - d1y * d2x
    if abs(denom) < 1e-9:
        return None
    bx, by = b1.x() - a1.x(), b1.y() - a1.y()
    t = (bx * d2y - by * d2x) / denom
    u = (bx * d1y - by * d1x) / denom
    eps = 1e-4
    if eps < t < 1 - eps and eps < u < 1 - eps:
        return QPointF(a1.x() + t * d1x, a1.y() + t * d1y)
    return None


def _collect_segments(scene) -> list:
    """(owner, start, end) for every line-ish segment on the display,
    in scene coordinates."""
    # Deferred: `items` imports `draw_crossed_polyline` from here, so
    # a module-level import would close the loop.
    from .items import PipeItem, StaticItem
    owned = []
    for stack, item in enumerate(scene.items()):
        if not isinstance(item, (PipeItem, StaticItem)) \
                or item.data.get("kind") not in CROSSOVER_KINDS:
            continue
        # A hidden runtime stroke is not physical ink and must not punch
        # unexplained gaps into a visible line.  EDIT intentionally retains
        # the ghosted stroke so an author can see and configure its effect.
        if not item.isVisible() or isinstance(item, StaticItem) and item._effectively_hidden():
            continue
        item._crossing_stack_rank = -stack
        points = [item.mapToScene(point)
                  for point in stroke_points(item)]
        owned.extend((item, start, end)
                     for start, end in zip(points, points[1:]))
    return owned


def _scene_segments(scene, exclude) -> list:
    """Every line-ish segment except `exclude`'s own — what a
    crossover has to check against.

    Cached against the studio's geometry generation. This runs once
    per item PAINTED, so recomputing it walked the whole scene for
    every crossover line on the display: 150 ms a frame at 120
    objects against 44 ms with the effect off. The studio bumps
    `crossover_generation` on every geometry change, so a moved line
    still moves its crossover marks.
    """
    studio = getattr(scene, "studio", None)
    generation = getattr(studio, "crossover_generation", None) \
        if studio is not None else None
    cache = getattr(scene, "_segment_cache", None)
    if generation is None or cache is None or cache[0] != generation:
        owned = _collect_segments(scene)
        if generation is not None:
            scene._segment_cache = (generation, owned)
    else:
        owned = cache[1]
    return [(start, end) for owner, start, end in owned
            if owner is not exclude]


def _scene_owned_segments(scene, exclude) -> list:
    """Ownership-preserving companion; the legacy helper returns pairs."""
    studio = getattr(scene, "studio", None)
    generation = getattr(studio, "crossover_generation", None) \
        if studio is not None else None
    cache = getattr(scene, "_segment_cache", None)
    if generation is None or cache is None or cache[0] != generation:
        owned = _collect_segments(scene)
        if generation is not None:
            scene._segment_cache = (generation, owned)
    else:
        owned = cache[1]
    return [(owner, start, end) for owner, start, end in owned
            if owner is not exclude]


def _owns_crossing(item, other) -> bool:
    """Exactly one explicit stroke owns a crossing bridge or break."""
    from .routing import CrossoverCandidate, crossover_owner
    first_data = getattr(item, "data", None)
    first_data = first_data if isinstance(first_data, dict) else {}
    other_data = getattr(other, "data", None)
    other_data = other_data if isinstance(other_data, dict) else {}
    first_id = str(first_data.get("id", "")) or f"item-{id(item)}"
    other_id = str(other_data.get("id", "")) or f"item-{id(other)}"
    owner = crossover_owner(
        CrossoverCandidate(first_id, str(first_data.get("crossover", "")),
                           item.zValue(), getattr(item, "_crossing_stack_rank", 0)),
        CrossoverCandidate(other_id, str(other_data.get("crossover", "")),
                           other.zValue(), getattr(other, "_crossing_stack_rank", 0)))
    return owner == first_id


def draw_crossed_polyline(painter, item, points_local,
                          mode: str, width: float) -> None:
    """The graphics paper's Crossover Effect (pp.27–28): where this
    line crosses another, break the stroke (gap) or hop over it
    (jump) — detected live, so moving either line moves the effect."""
    scene = item.scene()
    others = _scene_owned_segments(scene, item) if scene else []
    radius = max(5.0, width + 3.0)
    for p1, p2 in zip(points_local, points_local[1:]):
        s1, s2 = item.mapToScene(p1), item.mapToScene(p2)
        scene_length = math.hypot(s2.x() - s1.x(), s2.y() - s1.y())
        length = math.hypot(p2.x() - p1.x(), p2.y() - p1.y())
        if min(length, scene_length) < 1e-6:
            continue
        crossings = []
        for other, b1, b2 in others:
            if not _owns_crossing(item, other):
                continue
            hit = _seg_cross(s1, s2, b1, b2)
            if hit is not None:
                t = math.hypot(hit.x() - s1.x(),
                               hit.y() - s1.y()) / scene_length
                crossings.append((t, item.mapFromScene(hit)))
        crossings.sort(key=lambda c: c[0])
        # A freeform/arc is sampled into many short segments.  At a joint,
        # numerical noise can report the same physical crossing twice.  One
        # bridge per place is the engineering meaning and prevents a pair of
        # overlapping semicircles from becoming an unreadable loop.
        unique = []
        tolerance = radius / max(length, 1e-6) * 0.35
        for crossing in crossings:
            if not unique or abs(crossing[0] - unique[-1][0]) > tolerance:
                unique.append(crossing)
        crossings = unique
        direction = QPointF((p2.x() - p1.x()) / length,
                            (p2.y() - p1.y()) / length)
        intervals = []
        for t, local in crossings:
            center = t * length
            # Keep the mark inside its segment, even next to a nozzle. Two
            # nearby bridges merge rather than overlap into unreadable loops.
            span = min(radius, center, length - center)
            low, high = center - span, center + span
            if intervals and low <= intervals[-1][1]:
                intervals[-1] = (intervals[-1][0], max(high, intervals[-1][1]))
            else:
                intervals.append((low, high))
        def at(distance):
            return p1 + direction * distance
        cursor = 0.0
        for low, high in intervals:
            if low - cursor > .01:
                painter.drawLine(at(cursor), at(low))
            if mode == "jump":
                local, span = at((low + high) / 2), (high - low) / 2
                # Orient the entire ellipse, not only its start angle. A
                # merged bridge is wider than it is high; angle-only rotation
                # leaves its ends detached from a vertical or diagonal run.
                painter.save()
                painter.translate(local.x(), local.y())
                painter.rotate(math.degrees(math.atan2(direction.y(), direction.x())))
                painter.drawArc(QRectF(-span, -min(radius, span),
                                      span * 2, min(radius, span) * 2), 0, 180 * 16)
                painter.restore()
            cursor = high
        if length - cursor > .01:
            painter.drawLine(at(cursor), p2)


def crossing_count(item) -> int:
    """How many proper crossings currently affect ``item``.

    This is intentionally derived, never persisted.  Moving either stroke
    changes the answer immediately and the property pane can report what the
    smart line engine actually sees rather than a stale authored count.
    """
    scene = item.scene()
    if scene is None:
        return 0
    others = _scene_segments(scene, item)
    total = 0
    points = stroke_points(item)
    for start, end in zip(points, points[1:]):
        scene_start, scene_end = item.mapToScene(start), item.mapToScene(end)
        total += sum(_seg_cross(scene_start, scene_end, other_start,
                                other_end) is not None
                     for other_start, other_end in others)
    return total


def _contact_kind(a, b, c, d):
    """Classify ambiguous overlap/touch geometry, not ordinary crossings."""
    dx, dy, ex, ey = b.x() - a.x(), b.y() - a.y(), d.x() - c.x(), d.y() - c.y()
    determinant = dx * ey - dy * ex
    bx, by = c.x() - a.x(), c.y() - a.y()
    if abs(determinant) < 1e-7:
        length2 = dx * dx + dy * dy
        if length2 < 1e-9 or abs(bx * dy - by * dx) > 1e-5:
            return ""
        t = (bx * dx + by * dy) / length2
        u = ((d.x() - a.x()) * dx + (d.y() - a.y()) * dy) / length2
        overlap = min(1, max(t, u)) - max(0, min(t, u))
        return "overlap" if overlap * math.sqrt(length2) > .5 else ""
    t, u = (bx * ey - by * ex) / determinant, (bx * dy - by * dx) / determinant
    if -1e-6 <= t <= 1 + 1e-6 and -1e-6 <= u <= 1 + 1e-6:
        if not (1e-6 < t < 1 - 1e-6 and 1e-6 < u < 1 - 1e-6):
            return "touch"
    return ""


def iter_connection_issues(scene):
    """Cooperative sweep; one actionable advisory for each ambiguous pair."""
    if scene is None:
        return ()
    segments = sorted(((min(a.x(), b.x()), max(a.x(), b.x()),
                        min(a.y(), b.y()), max(a.y(), b.y()), owner, a, b)
                       for owner, a, b in _collect_segments(scene)), key=lambda r: r[:4])
    found, reported = [], set()
    for i, first in enumerate(segments):
        for j in range(i + 1, len(segments)):
            second = segments[j]
            yield None
            if second[0] > first[1] + 1e-6:
                break
            one, two = first[4], second[4]
            if one is two or second[2] > first[3] + 1e-6 or second[3] < first[2] - 1e-6:
                continue
            ids = tuple(sorted((str(one.data.get("id", "")), str(two.data.get("id", "")))))
            if ids in reported:
                continue
            contact = _contact_kind(first[5], first[6], second[5], second[6])
            if not contact:
                continue
            shared = {one.data.get(end) for end in ("a", "b")} & {two.data.get(end) for end in ("a", "b")}
            shared.discard(None)
            shared.discard("")
            if contact == "touch" and shared:
                continue
            reported.add(ids)
            message = ("Overlapping pipe/line runs" if contact == "overlap" else "Pipe/line contact without an attached junction")
            found.append((ids[0], f"{message}: {ids[0]} and {ids[1]}. Move a segment or insert an explicit branch junction."))
    return tuple(found)
