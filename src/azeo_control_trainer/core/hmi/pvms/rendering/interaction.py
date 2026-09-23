"""Shared screen-space interaction rules for every canvas item.

Drawing objects and PVMs have different process semantics, but a pointer must
not feel different when it crosses between them.  This module owns the common
gesture decisions: constant-size hit targets, rectangular transform handles,
and the click-versus-drag ambiguity at a connectable outline.

It deliberately knows nothing about document types or painters.  An item only
needs ``scene()``, selection state, and the small connector attributes already
used by the retained-mode canvas.  That boundary lets future tools replace the
item implementation without inventing another set of mouse rules.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF


CONNECT_DRAG_PIXELS = 5.0


def scene_radius(item, pixels: float) -> float:
    """Convert a screen-space affordance size into local scene units."""
    scene = item.scene()
    studio = getattr(scene, "studio", None) if scene is not None else None
    scale = abs(studio.canvas.transform().m11()) \
        if studio is not None else 1.0
    return float(pixels) / max(scale, 0.1)


def rect_handle_points(rect: QRectF) -> dict[str, QPointF]:
    """The shared eight-handle geometry used by PVMs and drawing objects."""
    mid_x, mid_y = rect.center().x(), rect.center().y()
    return {
        "nw": QPointF(rect.left(), rect.top()),
        "n": QPointF(mid_x, rect.top()),
        "ne": QPointF(rect.right(), rect.top()),
        "w": QPointF(rect.left(), mid_y),
        "e": QPointF(rect.right(), mid_y),
        "sw": QPointF(rect.left(), rect.bottom()),
        "s": QPointF(mid_x, rect.bottom()),
        "se": QPointF(rect.right(), rect.bottom()),
    }


def hit_rect_handle(item, pos, *, pixels: float = 9.0) -> str | None:
    """Return the rectangular transform handle under ``pos``."""
    hit = scene_radius(item, pixels)
    for name, centre in rect_handle_points(item.rect()).items():
        if abs(pos.x() - centre.x()) <= hit \
                and abs(pos.y() - centre.y()) <= hit:
            return name
    return None


def visible_connector_sides(item, studio) -> tuple[str, ...]:
    """Return only connector points relevant to the current gesture.

    Selection and connection are distinct editing states.  Showing every
    magnetic port merely because an item is selected covers compact valves
    with dots and makes their transform frame unusable.  Explicit Connect
    mode (and connection-point editing) exposes all ports; ordinary hover and
    routing expose only the exact point currently under the pointer.
    """
    if studio is None:
        return ()
    if bool(getattr(studio, "connect_armed", False)) \
            or bool(getattr(item, "_cp_edit", False)):
        return tuple(item.anchor_sides())
    sides: list[str] = []
    for attribute in ("_connect_hot_anchor", "_hover_anchor"):
        side = getattr(item, attribute, None)
        if side and side not in sides:
            sides.append(str(side))
    return tuple(sides)


class ConnectorGestureKernel:
    """One connection gesture state machine shared by all item families.

    A press on the edge of an unselected, small symbol is ambiguous.  Starting
    a pipe immediately consumes the click needed to select and resize it.
    Commercial editors resolve that ambiguity with motion: release selects;
    dragging beyond a few screen pixels connects.  Explicit Connect mode is
    unambiguous and starts immediately.  Selection itself is not connection
    intent; otherwise a compact selected valve becomes a field of connector
    targets instead of a movable, resizable object.
    """

    @staticmethod
    def press(item, studio, side: str, scene_pos) -> bool:
        """Stage or start a connector; return whether the press is consumed."""
        explicit = bool(getattr(studio, "connect_armed", False))
        if explicit:
            studio.start_anchor_drag(item, side)
            item._anchor_dragging = True
            item._pending_anchor_drag = None
            item._pending_anchor_origin = None
            return True
        item._pending_anchor_drag = side
        item._pending_anchor_origin = QPointF(scene_pos)
        return False

    @staticmethod
    def move(item, studio, scene_pos) -> bool:
        """Advance a staged/active connector; return whether move is owned."""
        if bool(getattr(item, "_anchor_dragging", False)):
            studio.drag_anchor_to(scene_pos)
            return True
        pending = getattr(item, "_pending_anchor_drag", None)
        if pending is None:
            return False
        origin = getattr(item, "_pending_anchor_origin", scene_pos)
        delta = scene_pos - origin
        if abs(delta.x()) + abs(delta.y()) \
                >= scene_radius(item, CONNECT_DRAG_PIXELS):
            item._pending_anchor_drag = None
            studio.start_anchor_drag(item, pending)
            item._anchor_dragging = True
            studio.drag_anchor_to(scene_pos)
        return True

    @staticmethod
    def release(item, studio, scene_pos) -> bool:
        """Commit an active connector or turn a staged press into a click."""
        active = bool(getattr(item, "_anchor_dragging", False))
        item._pending_anchor_drag = None
        item._pending_anchor_origin = None
        if not active:
            return False
        item._anchor_dragging = False
        studio.finish_anchor_drag(scene_pos, exclude=item)
        studio.end_gesture()
        return True

    @staticmethod
    def cancel(item) -> None:
        item._anchor_dragging = False
        item._pending_anchor_drag = None
        item._pending_anchor_origin = None
