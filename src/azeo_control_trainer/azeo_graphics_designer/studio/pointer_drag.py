"""One canvas drag moves or copies a selection without distorting its layout."""
from __future__ import annotations

import copy
import weakref
from bisect import bisect_left

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtWidgets import QApplication

from azeo_control_trainer.core.hmi.pvms.rendering.items import (
    PvmItem, StaticItem, item_group_id,
)
from .selection import is_locked, visual_scene_rect


class PointerDrag:
    """Keep a transient gesture over the existing selection and undo models."""

    def __init__(self, canvas):
        self.canvas = weakref.proxy(canvas)
        self.reset()

    def reset(self):
        self.start = None
        self.started = False
        self.copying = False
        self.toggle_on_click = ()
        self.origins = {}
        self.routes = []
        self.endpoint_ids = set()
        self.bounds = QRectF()
        self.targets = ([], [])
        self.snap_latches = [None, None]
        self.port_targets = ([], [])
        self.port_latches = [None, None]
        self.last_delta = None

    @property
    def active(self):
        return self.start is not None

    def press(self, item, position, modifiers):
        studio = self.canvas.studio
        if studio.interaction_mode != "select":
            return False
        while item is not None and not isinstance(item, (PvmItem, StaticItem)):
            item = item.parentItem()
        if item is None or is_locked(item) or getattr(item, "_cp_edit", False):
            return False
        local = item.mapFromScene(position)
        if not item.rect().contains(local):
            return False
        # Resize/rotation/vertex handles retain their own geometry gestures.
        if item.isSelected() and (item.handle_at(local) is not None
                or isinstance(item, StaticItem) and item.is_point_kind()
                and item.vertex_at(local) is not None):
            return False
        members = [item]
        group = item_group_id(item)
        if group and not modifiers & Qt.AltModifier:
            members = [one for one in studio._groupable_items()
                       if item_group_id(one) == group and one.isVisible()]
        self.reset()
        selected = item in studio.selection.snapshot().items
        self.copying = bool(modifiers & Qt.ControlModifier)
        self.toggle_on_click = tuple(members) if self.copying and selected else ()
        if modifiers & (Qt.ControlModifier | Qt.ShiftModifier):
            studio.selection.extend(members, primary=item)
        elif not selected or modifiers & Qt.AltModifier:
            studio.selection.replace(members, primary=item)
        else:
            studio.selection.set_primary(item)
        self.start = QPointF(position)
        return True

    def _prepare(self):
        studio = self.canvas.studio
        studio.gesture_checkpoint()
        if self.copying:
            studio.paste_payload(studio.copy_selection_payload(),
                                 dx=0, dy=0, as_gesture=True)
        items = studio.selection.editable_items()
        self.origins = {item: QPointF(item.pos()) for item in items}
        bounds = None
        for item in items:
            rect = visual_scene_rect(item)
            bounds = rect if bounds is None else bounds.united(rect)
        self.bounds = bounds or QRectF()
        endpoint_ids = {studio._endpoint_id(item) for item in items}
        self.endpoint_ids = endpoint_ids
        self.routes = [
            (pipe, copy.deepcopy(pipe.data["route_points"]))
            for pipe in studio._pipe_items()
            if pipe.data.get("a") in endpoint_ids
            and pipe.data.get("b") in endpoint_ids
            and pipe.data.get("route_points")
        ]
        xs, ys = [], []
        page = self.canvas.page_rect()
        if not page.isEmpty():
            margin = max(0.0, float(studio.display.safe_margin))
            safe = page.adjusted(margin, margin, -margin, -margin)
            xs.extend((safe.left(), safe.center().x(), safe.right()))
            ys.extend((safe.top(), safe.center().y(), safe.bottom()))
        for axis, coordinate in studio.authoring_guides:
            (xs if axis == "x" else ys).append(float(coordinate))
        for other in studio._items() + studio._static_items():
            if other in self.origins or not other.isVisible():
                continue
            rect = visual_scene_rect(other)
            xs.extend((rect.left(), rect.center().x(), rect.right()))
            ys.extend((rect.top(), rect.center().y(), rect.bottom()))
        self.targets = (sorted(set(xs)), sorted(set(ys)))
        self._prepare_port_targets()
        self.started = True

    def _prepare_port_targets(self):
        from azeo_control_trainer.core.hmi.pvms.rendering.pipe_scene import port_anchor

        studio = self.canvas.studio
        xs, ys = [], []
        pvms, statics, pipes = studio._scene_buckets()
        endpoints = studio._index_endpoints(pvms, statics)
        for pipe in pipes:
            a_id, b_id = pipe.data.get("a"), pipe.data.get("b")
            if (a_id in self.endpoint_ids) == (b_id in self.endpoint_ids):
                continue
            moving_end, fixed_end = ("a", "b") if a_id in self.endpoint_ids else ("b", "a")
            moving = endpoints.get(pipe.data.get(moving_end))
            fixed = endpoints.get(pipe.data.get(fixed_end))
            if moving is None or fixed is None or not fixed.isVisible():
                continue
            source = port_anchor(moving, str(pipe.data.get(moving_end + "_side", "e")))
            target = port_anchor(fixed, str(pipe.data.get(fixed_end + "_side", "w")))
            vias = pipe.data.get("route_points", ()) if pipe.data.get("route_mode") == "manual" else ()
            if vias:
                # A hand-routed pipe follows its adjacent bend. Aligning its
                # remote equipment instead would still leave a nozzle jog.
                from azeo_control_trainer.core.hmi.pvms.rendering.routing import Point
                try:
                    point = Point.from_value(vias[0 if moving_end == "a" else -1])
                except (ValueError, TypeError, KeyError, IndexError):
                    continue
                if not point.finite:
                    continue
            elif (source.normal, target.normal) in (
                    ("e", "w"), ("w", "e"), ("n", "s"), ("s", "n")):
                point = target.point
            else:
                continue
            # Cache numeric deltas once per gesture. Per-frame scene scans
            # or alpha-outline searches make a dense process display stall.
            if source.normal in ("e", "w"):
                ys.append((point.y - source.point.y, point.y))
            else:
                xs.append((point.x - source.point.x, point.x))
        self.port_targets = (sorted(set(xs)), sorted(set(ys)))

    def _snap_delta(self, delta, modifiers):
        studio = self.canvas.studio
        horizontal = abs(delta.x()) >= abs(delta.y())
        constrain = bool(modifiers & Qt.ShiftModifier)
        if constrain:
            delta = QPointF(delta.x(), 0) if horizontal else QPointF(0, delta.y())
        self.canvas._smart_guides = []
        if modifiers & Qt.AltModifier:
            self.snap_latches = [None, None]
            self.port_latches = [None, None]
            return delta
        rect = self.bounds
        dx, dy = delta.x(), delta.y()
        if studio.snap_enabled:
            # Snap one anchor and use the same delta for every member. Rounding
            # each item's position separately changed equipment spacing.
            if not constrain or horizontal:
                dx = round((rect.left() + dx) / 8) * 8 - rect.left()
            if not constrain or not horizontal:
                dy = round((rect.top() + dy) / 8) * 8 - rect.top()
        if studio.smart_guides_enabled:
            tolerance = 6.0 / max(abs(self.canvas.transform().m11()), 0.1)

            def nearest(axis, amount, edges, targets):
                port = self.port_latches[axis]
                if port is None or abs(amount - port[1]) > tolerance * 1.75:
                    port = min(((abs(amount - adjustment), adjustment, coordinate)
                                for adjustment, coordinate in self.port_targets[axis]), default=None)
                    port = port if port and port[0] <= tolerance else None
                self.port_latches[axis] = port
                if port is not None:
                    # The attached process nozzle takes priority over a box
                    # edge or label center near it. The entire selection uses
                    # one delta; no endpoint is changed by the router.
                    self.snap_latches[axis] = None
                    return port
                held = self.snap_latches[axis]
                # Keep the chosen edge until the pointer leaves a wider band.
                # Competing centers/edges previously made a slow drag chatter.
                if held is not None and abs(amount - held[1]) <= tolerance * 1.75:
                    return held
                candidates = []
                for edge in edges:
                    index = bisect_left(targets, edge + amount)
                    for target in targets[max(0, index - 1):index + 1]:
                        candidates.append((abs(edge + amount - target), target - edge, target))
                best = min(candidates, default=None)
                self.snap_latches[axis] = best if best and best[0] <= tolerance else None
                return self.snap_latches[axis]

            match_x = nearest(0, delta.x(), (rect.left(), rect.center().x(), rect.right()),
                              self.targets[0]) if not constrain or horizontal else None
            match_y = nearest(1, delta.y(), (rect.top(), rect.center().y(), rect.bottom()),
                              self.targets[1]) if not constrain or not horizontal else None
            if constrain:
                self.snap_latches[1 if horizontal else 0] = None
                self.port_latches[1 if horizontal else 0] = None
            if match_x:
                dx = match_x[1]
                self.canvas._smart_guides.append(("x", match_x[2]))
            if match_y:
                dy = match_y[1]
                self.canvas._smart_guides.append(("y", match_y[2]))
        else:
            self.snap_latches = [None, None]
            self.port_latches = [None, None]
        return QPointF(dx, dy)

    def move(self, position, modifiers):
        if not self.active:
            return False
        delta = position - self.start
        if not self.started:
            distance = delta.manhattanLength() * abs(self.canvas.transform().m11())
            if distance < QApplication.startDragDistance():
                return True
            self._prepare()
        delta = self._snap_delta(delta, modifiers)
        if delta == self.last_delta:
            return True
        self.last_delta = QPointF(delta)
        studio = self.canvas.studio
        # Intermediate itemChange callbacks must not route pipes against a
        # half-moved assembly or serialize its intermediate geometry.
        studio._moving_selection = True
        try:
            for item, origin in self.origins.items():
                item.setPos(origin + delta)
            for pipe, points in self.routes:
                pipe.data["route_points"] = [
                    [x + delta.x(), y + delta.y()] for x, y in points]
        finally:
            studio._moving_selection = False
        studio.reroute_pipes(only_ids=self.endpoint_ids, preview=True)
        studio.mark_unsaved(sync_document=False)
        self.canvas.viewport().update()
        self.canvas.update_rulers()
        return True

    def release(self):
        if not self.active:
            return False
        studio = self.canvas.studio
        if self.started:
            studio.reroute_pipes(only_ids=self.endpoint_ids)
            studio.mark_unsaved()
            studio.end_gesture()
        elif self.toggle_on_click:
            studio.selection.replace(
                [item for item in studio.selection.snapshot().items
                 if item not in self.toggle_on_click])
        self.reset()
        self.canvas.clear_smart_guides()
        return True
