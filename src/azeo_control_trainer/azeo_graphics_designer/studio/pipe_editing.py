"""Explicit branch junctions reuse ordinary display items and pipe endpoints."""
from __future__ import annotations

import copy
import uuid

from PySide6.QtCore import QPointF

from azeo_control_trainer.core.hmi.pvms.rendering.items import PipeItem, StaticItem
from azeo_control_trainer.core.hmi.pvms.rendering.chrome import MODE_EDIT
from azeo_control_trainer.core.hmi.pvms.rendering.routing import Point, simplify_orthogonal
from .selection import is_locked


def insert_junction(studio, pipe, scene_pos):
    """Split a run at the pointer, retaining its route, ports and one Undo."""
    if studio.mode != MODE_EDIT or is_locked(pipe) or pipe.scene() is not studio.canvas.scene():
        return None
    index = pipe.segment_at(scene_pos)
    if index is None:
        return None
    points = [QPointF(p) for p in pipe._points]
    a, b = points[index:index + 2]
    delta = b - a
    length = delta.x() ** 2 + delta.y() ** 2
    t = max(0.0, min(1.0, ((scene_pos - a).x() * delta.x()
                            + (scene_pos - a).y() * delta.y()) / length))
    center = a + delta * t
    if min((center - points[0]).manhattanLength(),
           (center - points[-1]).manhattanLength()) < 12:
        return None
    ident = "junction_" + uuid.uuid4().hex[:10]
    junction_data = dict(kind="ellipse", id=ident, x=center.x() - 4, y=center.y() - 4,
                         w=8, h=8, pipe_junction=True, routing_obstacle=False,
                         fill_role="EQUIPMENT", line_role="EQUIPMENT", width=1, z=1)
    if pipe.data.get("group"):
        junction_data["group"] = pipe.data["group"]
    first, second = copy.deepcopy(pipe.data), copy.deepcopy(pipe.data)
    second["id"] = "pipe_" + uuid.uuid4().hex[:10]
    direction = ("e" if delta.x() > 0 else "w") if abs(delta.x()) > abs(delta.y()) else ("s" if delta.y() > 0 else "n")
    opposite = {"e": "w", "w": "e", "n": "s", "s": "n"}[direction]
    for data, end, normal, half in (
            (first, "b", opposite, [*points[:index + 1], center]),
            (second, "a", direction, [center, *points[index + 1:]])):
        for key in tuple(data):
            if key == end or key.startswith(end + "_"):
                data.pop(key)
        data[end], data[end + "_side"] = ident, normal
        data["auto"] = False
        data["route_mode"] = "manual"
        data["route_points"] = [p.to_list() for p in simplify_orthogonal(
            Point(p.x(), p.y()) for p in half)[1:-1]]
    # Preserve flow arrows at the original outer ends, never at the new dot.
    first["arrow_end"], second["arrow_start"] = "none", "none"
    studio.checkpoint()
    pipe.data.clear()
    pipe.data.update(first)
    junction = StaticItem(junction_data, studio.palette_roles)
    tail = PipeItem(second, studio.palette_roles)
    studio.canvas.scene().addItem(junction)
    studio.canvas.scene().addItem(tail)
    studio.reroute_pipes()
    studio.selection.replace((junction,), primary=junction)
    studio.mark_unsaved()
    return junction
