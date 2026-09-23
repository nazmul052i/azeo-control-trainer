"""Scene adapter for the Qt-free professional pipe router.

Both Studio and Station call :func:`route_scene_pipes`; keeping obstacle and
port extraction here prevents a published display from taking a different
path from the one the engineer approved.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF

from .routing import (
    Box, DEFAULT_CLEARANCE, Obstacle, OrthogonalRouter, Point, PortAnchor,
    RouteRequest, Segment, infer_port_normal,
)


def _item_ident(item) -> str:
    pvm_id = getattr(getattr(item, "pvm", None), "id", "")
    data = getattr(item, "data", {})
    return str(pvm_id or (data.get("id", "")
                          if isinstance(data, dict) else ""))


def _valid_port_name(item, name: str) -> bool:
    if name in item.anchor_sides():
        return True
    if str(name).startswith("outline:"):
        try:
            prefix, normal, fx, fy = str(name).split(":", 3)
            fx, fy = float(fx), float(fy)
        except (TypeError, ValueError):
            return False
        return (prefix == "outline" and normal in ("n", "e", "s", "w")
                and math.isfinite(fx) and math.isfinite(fy)
                and 0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0)
    try:
        prefix, normal, fraction = name.split(":", 2)
        fraction = float(fraction)
    except (AttributeError, TypeError, ValueError):
        return False
    return (prefix == "edge" and normal in ("n", "e", "s", "w")
            and math.isfinite(fraction) and 0.0 <= fraction <= 1.0)


def valid_port_name(item, name: str) -> bool:
    """Public verifier companion for the renderer's port vocabulary."""
    return _valid_port_name(item, name)


def _loose_point(pipe, end: str) -> QPointF | None:
    raw = pipe.data.get(f"{end}_point")
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return None
    try:
        point = QPointF(float(raw[0]), float(raw[1]))
    except (TypeError, ValueError):
        return None
    return point if math.isfinite(point.x()) and math.isfinite(point.y()) \
        else None


def _endpoint_centre(item, loose: QPointF | None) -> QPointF:
    if item is not None:
        return item.mapToScene(item.rect().center())
    return QPointF(loose) if loose is not None else QPointF()


def _facing_normals(a: QPointF, b: QPointF) -> tuple[str, str]:
    dx, dy = b.x() - a.x(), b.y() - a.y()
    if abs(dx) >= abs(dy):
        return ("e", "w") if dx >= 0 else ("w", "e")
    return ("s", "n") if dy >= 0 else ("n", "s")


def junction_side(item, selected: str, toward: QPointF | None) -> str:
    """Choose a new branch direction at a dot with coincident ports.

    Call only while authoring an endpoint, never while rerouting saved pipes.
    Equipment nozzles remain the engineer's selection; a junction dot has no
    separate nozzle for the magnetic hit test to distinguish.
    """
    data = getattr(item, "data", None)
    if toward is None or not isinstance(data, dict) or not data.get("pipe_junction"):
        return selected
    center = item.anchor(selected)
    if (toward - center).manhattanLength() < .01:
        return selected
    normal = _facing_normals(center, toward)[0]
    return next((side for side in ("n", "e", "s", "w")
                 if port_anchor(item, side).normal == normal), selected)


def port_anchor(item, name: str) -> PortAnchor:
    """Return an exact scene endpoint and its outward cardinal normal."""
    point = item.anchor(name)
    if callable(getattr(item, "connection_normal", None)):
        return PortAnchor(Point(point.x(), point.y()), item.connection_normal(name), name)
    data = getattr(item, "data", {})
    data = data if isinstance(data, dict) else {}
    # Semantic process ports share the same direction contract as cardinal
    # ports.  Inferring ``outlet`` from the item's bounding box is wrong for
    # asymmetric symbols such as centrifugal pumps, whose discharge is high
    # on the east side rather than at the rectangle midpoint.
    normal = {
        "n": "n", "top": "n",
        "e": "e", "right": "e", "outlet": "e",
        "s": "s", "bottom": "s",
        "w": "w", "left": "w", "inlet": "w",
    }.get(str(name).lower(), "")
    # Free perimeter attachments encode their owning edge. Keep that normal
    # rather than inferring it against the item's broader bounding rectangle;
    # a symbol's visible SVG content may be inset inside that rectangle.
    if str(name).startswith(("edge:", "outline:")):
        parts = str(name).split(":", 2)
        if len(parts) >= 2 and parts[1] in ("n", "e", "s", "w"):
            normal = parts[1]
            # Static symbols may rotate as QGraphicsItems. Translate the
            # stored local outline normal into scene axes so an orthogonal
            # route still departs outward after rotation.
            local = item.mapFromScene(point)
            dx, dy = {"n": (0.0, -1.0), "e": (1.0, 0.0),
                      "s": (0.0, 1.0), "w": (-1.0, 0.0)}[normal]
            mapped = item.mapToScene(QPointF(local.x() + dx,
                                              local.y() + dy))
            vx, vy = mapped.x() - point.x(), mapped.y() - point.y()
            normal = ("e" if vx >= 0 else "w") if abs(vx) >= abs(vy) \
                else ("s" if vy >= 0 else "n")
    if not normal:
        raw = data.get("ports", []) if isinstance(data, dict) else []
        port = next((entry for entry in raw
                     if str(entry.get("name", "")) == name), None)
        if port and port.get("normal") in ("n", "e", "s", "w"):
            normal = str(port["normal"])
        else:
            local = item.mapFromScene(point)
            rect = item.rect()
            x = (local.x() - rect.left()) / max(rect.width(), 1e-9)
            y = (local.y() - rect.top()) / max(rect.height(), 1e-9)
            normal = infer_port_normal(x, y)
    if data.get("kind") == "symbol" \
            and not str(name).startswith(("edge:", "outline:")):
        # A named nozzle is stored in the symbol's untransformed SVG space.
        # Its point already mirrors in StaticItem.anchor(); its departure
        # normal must undergo the same transform or a mirrored exchanger
        # routes immediately back through its own body.
        if data.get("mx"):
            normal = {"e": "w", "w": "e"}.get(normal, normal)
        if data.get("my"):
            normal = {"n": "s", "s": "n"}.get(normal, normal)
        local = item.mapFromScene(point)
        dx, dy = {"n": (0.0, -1.0), "e": (1.0, 0.0),
                  "s": (0.0, 1.0), "w": (-1.0, 0.0)}[normal]
        mapped = item.mapToScene(QPointF(local.x() + dx,
                                          local.y() + dy))
        vx, vy = mapped.x() - point.x(), mapped.y() - point.y()
        normal = ("e" if vx >= 0 else "w") if abs(vx) >= abs(vy) \
            else ("s" if vy >= 0 else "n")
    if data.get("kind") == "stream_connector":
        # Its semantic port is stored in untransformed item coordinates.
        # Mirror and QGraphics rotation must change the route's departure
        # normal along with the painted continuation symbol.
        if data.get("mx"):
            normal = {"e": "w", "w": "e"}.get(normal, normal)
        if data.get("my"):
            normal = {"n": "s", "s": "n"}.get(normal, normal)
        local = item.mapFromScene(point)
        dx, dy = {"n": (0.0, -1.0), "e": (1.0, 0.0),
                  "s": (0.0, 1.0), "w": (-1.0, 0.0)}[normal]
        mapped = item.mapToScene(QPointF(local.x() + dx,
                                          local.y() + dy))
        vx, vy = mapped.x() - point.x(), mapped.y() - point.y()
        normal = ("e" if vx >= 0 else "w") if abs(vx) >= abs(vy) \
            else ("s" if vy >= 0 else "n")
    return PortAnchor(Point(point.x(), point.y()), normal, name)


def scene_bounds(scene) -> Box | None:
    if scene is None:
        return None
    display = getattr(scene, "display", None)
    if display is None:
        return None
    width = float(getattr(display, "width", 0) or 0)
    height = float(getattr(display, "height", 0) or 0)
    return Box(0, 0, width, height) if width > 0 and height > 0 else None


def scene_obstacles(scene, excluded=()) -> tuple[Obstacle, ...]:
    """Solid display objects, excluding strokes and the connected objects."""
    from .items import PvmItem, PipeItem, StaticItem
    excluded = set(excluded)
    if scene is None:
        return ()
    obstacles = []
    for item in scene.items():
        if item in excluded or isinstance(item, PipeItem) or not item.isVisible() \
                or getattr(item, "_authoring_preview", False):
            continue
        if not isinstance(item, (PvmItem, StaticItem)):
            continue
        data = getattr(item, "data", {})
        data = data if isinstance(data, dict) else {}
        if data.get("routing_obstacle") is False:
            # A process-panel backdrop surrounds the equipment; treating its
            # entire fill as equipment leaves every port inside a blocked box.
            continue
        if isinstance(item, StaticItem) and data.get("kind") in (
                "line", "polyline", "freehand", "arc"):
            continue
        local = item.routing_rect()
        if local.isEmpty():
            continue
        rect = item.mapRectToScene(local)
        ident = _item_ident(item) or "object"
        obstacles.append(Obstacle(
            ident, Box(rect.left(), rect.top(), rect.right(), rect.bottom())))
    return tuple(sorted(obstacles, key=lambda row: row.ident))


def existing_segments(scene, excluded=()) -> tuple[Segment, ...]:
    from .crossing import CROSSOVER_KINDS, stroke_points
    excluded = set(excluded)
    if scene is None:
        return ()
    result = []
    for item in scene.items():
        data = getattr(item, "data", {})
        data = data if isinstance(data, dict) else {}
        if item in excluded or data.get("kind") not in CROSSOVER_KINDS:
            continue
        points = [item.mapToScene(point) for point in stroke_points(item)]
        ident = str(data.get("id", ""))
        result.extend(Segment(Point(a.x(), a.y()), Point(b.x(), b.y()), ident)
                      for a, b in zip(points, points[1:]))
    return tuple(result)


def route_pipe(scene, pipe, endpoint_a, endpoint_b, *, obstacles=None,
               segments=None, bounds=None):
    """Route one pipe and apply the derived result to its graphics item."""
    loose_a, loose_b = _loose_point(pipe, "a"), _loose_point(pipe, "b")
    centre_a = _endpoint_centre(endpoint_a, loose_a)
    centre_b = _endpoint_centre(endpoint_b, loose_b)
    facing_a, facing_b = _facing_normals(centre_a, centre_b)
    a_side = str(pipe.data.get("a_side", ""))
    b_side = str(pipe.data.get("b_side", ""))
    # The preview and persisted connector both name the ports the engineer
    # selected. Former `auto: true` cardinal pipes replaced a bottom port with
    # whichever rectangle side faced the other PVM, making the committed pipe
    # jump away from its preview and run down the PVM's outer edge. Preserve a
    # valid authored side; geometric facing is only a legacy-data fallback.
    if endpoint_a is not None and not _valid_port_name(endpoint_a, a_side):
        a_side = facing_a
    if endpoint_b is not None and not _valid_port_name(endpoint_b, b_side):
        b_side = facing_b
    if endpoint_a is None:
        a_side = facing_a
    if endpoint_b is None:
        b_side = facing_b
    if (endpoint_a is None and loose_a is None) \
            or (endpoint_b is None and loose_b is None):
        return None
    mode = str(pipe.data.get("route_mode", "auto"))
    vias = tuple(Point.from_value(value)
                 for value in pipe.data.get("route_points", ())) \
        if mode == "manual" else ()
    anchor_a = port_anchor(endpoint_a, a_side) if endpoint_a is not None \
        else PortAnchor(Point(loose_a.x(), loose_a.y()), a_side)
    anchor_b = port_anchor(endpoint_b, b_side) if endpoint_b is not None \
        else PortAnchor(Point(loose_b.x(), loose_b.y()), b_side)
    connected = tuple(item for item in (endpoint_a, endpoint_b)
                      if item is not None)
    connected_ids = {_item_ident(item) for item in connected}
    request = RouteRequest(
        anchor_a, anchor_b,
        obstacles=scene_obstacles(scene, connected)
        if obstacles is None else tuple(
            obstacle for obstacle in obstacles
            if obstacle.ident not in connected_ids),
        vias=vias, bounds=scene_bounds(scene) if bounds is None else bounds,
        existing_segments=existing_segments(scene, (pipe,))
        if segments is None else tuple(
            segment for segment in segments
            if segment.owner != str(pipe.data.get("id", ""))),
        clearance=float(pipe.data.get("clearance", DEFAULT_CLEARANCE)),
        line_width=float(pipe.data.get("width", 2.0)),
    )
    result = OrthogonalRouter().route(request)
    pipe.apply_route(result, a_side=a_side, b_side=b_side)
    return result


def route_scene_pipes(scene, *, only_for=None, only_ids=None, preview=False) -> None:
    """Shared deterministic pass, optionally limited to moved endpoints."""
    from .items import PvmItem, PipeItem, StaticItem
    index, pipes = {}, []
    for item in scene.items():
        if getattr(item, "_authoring_preview", False):
            continue
        if isinstance(item, PipeItem):
            pipes.append(item)
        elif isinstance(item, PvmItem):
            index[item.pvm.id] = item
        elif isinstance(item, StaticItem):
            index[str(item.data.get("id", ""))] = item
    affected = None if only_ids is None else set(only_ids)
    if only_for is not None:
        ident = _item_ident(only_for)
        if ident:
            affected = (affected or set()) | {ident}
    obstacles = () if preview else scene_obstacles(scene)
    previous = getattr(scene, "_pipe_obstacle_bounds", {})
    if not preview:
        # Values only: retaining QGraphicsItems here creates stale wrappers
        # after a display is rebuilt. Preview must not overwrite old bounds.
        scene._pipe_obstacle_bounds = {row.ident: row.bounds for row in obstacles}
    if affected is not None:
        # A selection drag routes the union once. Routing every other train
        # again made one pointer move cost 126 ms on a 60-pipe display.
        changed = [box for ident in affected
                   for box in (previous.get(ident),
                               scene._pipe_obstacle_bounds.get(ident)
                               if not preview else None)
                   if box is not None]

        def needs_route(pipe):
            if str(pipe.data.get("id", "")) in affected:
                return True
            if pipe.data.get("a") in affected or pipe.data.get("b") in affected:
                return True
            if preview:
                return False
            if affected.intersection(pipe.route_collisions):
                return True
            rect = pipe.sceneBoundingRect()
            margin = float(pipe.data.get("clearance", DEFAULT_CLEARANCE))
            return any(not (rect.right() < box.left - margin
                            or rect.left() > box.right + margin
                            or rect.bottom() < box.top - margin
                            or rect.top() > box.bottom + margin)
                       for box in changed)

        pipes = [pipe for pipe in pipes if needs_route(pipe)]
    if not pipes:
        return
    # While the pointer moves, show exact authored ports and manual bends
    # immediately. Global obstacle/crossing search runs at release; doing it
    # for every intermediate coordinate stalled a 1000-object canvas.
    segments = () if preview else existing_segments(scene)
    bounds = scene_bounds(scene)
    for pipe in sorted(pipes, key=lambda row: str(row.data.get("id", ""))):
        a = index.get(str(pipe.data.get("a", "")))
        b = index.get(str(pipe.data.get("b", "")))
        a_valid = a is not None or _loose_point(pipe, "a") is not None
        b_valid = b is not None or _loose_point(pipe, "b") is not None
        if a_valid and b_valid:
            route_pipe(scene, pipe, a, b, obstacles=obstacles,
                       segments=segments, bounds=bounds)


def qpoints(points) -> list[QPointF]:
    return [QPointF(point.x, point.y) for point in points]


__all__ = [
    "existing_segments", "port_anchor", "qpoints", "route_pipe",
    "route_scene_pipes", "scene_bounds", "scene_obstacles",
    "valid_port_name",
]
