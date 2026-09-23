"""Canonical Graphics Designer selection and mixed-property aggregation."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import QObject, QRectF, Signal


class AggregateKind(Enum):
    UNIFORM = "uniform"
    MIXED = "mixed"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Aggregate:
    kind: AggregateKind
    value: object = None


@dataclass(frozen=True)
class SelectionSnapshot:
    items: tuple
    primary: object | None
    bounds: QRectF

    @property
    def count(self) -> int:
        return len(self.items)


def is_locked(item) -> bool:
    pvm = getattr(item, "pvm", None)
    if pvm is not None:
        return bool(pvm.locked)
    metadata = getattr(item, "data", None)
    return bool(metadata.get("locked", False)) \
        if isinstance(metadata, dict) else False


def visual_scene_rect(item) -> QRectF:
    """Return the visible authoring geometry in scene coordinates.

    ``sceneBoundingRect()`` is intentionally *not* an alignment primitive.
    PVM and drawing items widen it for resize handles, rotation knobs and Qt
    damage tracking. Aligning those invisible margins is why unlike process
    symbols could report as aligned while their actual equipment was not.
    """
    local_rect = None

    symbol_name = getattr(item, "_symbol_name", None)
    symbol_view_rect = getattr(item, "_symbol_view_rect", None)
    if callable(symbol_name) and callable(symbol_view_rect):
        symbol = symbol_name()
        if symbol:
            target = symbol_view_rect(symbol)
            from azeo_control_trainer.core.hmi.pvms.symbols import content_box
            fx, fy, fw, fh = content_box(symbol)
            local_rect = QRectF(target.x() + target.width() * fx,
                                target.y() + target.height() * fy,
                                target.width() * fw,
                                target.height() * fh)

    content_rect = getattr(item, "_content_rect", None)
    if local_rect is None and callable(content_rect):
        local_rect = content_rect()
    if local_rect is None and hasattr(item, "rect"):
        local_rect = item.rect()
    if local_rect is None:
        return QRectF(item.sceneBoundingRect())
    return QRectF(item.mapRectToScene(local_rect))


class SelectionController(QObject):
    """One ordered selection model shared by canvas, pane and inspector."""

    changed = Signal(object)

    def __init__(self, scene, parent=None):
        super().__init__(parent)
        self.scene = scene
        self._primary = None
        self._order: list = []
        self._syncing = False
        scene.selectionChanged.connect(self.sync_from_scene)

    def snapshot(self) -> SelectionSnapshot:
        # Hidden rows remain a logical selection when chosen through the
        # Selection pane even though Qt cannot visually select an invisible
        # graphics item. Explicit scene clear still reaches sync_from_scene.
        selected = [item for item in self._order if item.scene() is self.scene]
        for item in self.scene.selectedItems():
            if item not in selected:
                selected.append(item)
        primary = self._primary if self._primary in selected \
            else (selected[-1] if selected else None)
        bounds = None
        for item in selected:
            visible = visual_scene_rect(item)
            bounds = bounds.united(visible) \
                if bounds is not None else QRectF(visible)
        return SelectionSnapshot(
            tuple(selected), primary, bounds if bounds is not None else QRectF())

    def _apply(self, items, primary=None) -> SelectionSnapshot:
        unique = list(dict.fromkeys(item for item in items
                                    if item is not None and item.scene() is self.scene))
        target = primary if primary in unique else (unique[-1] if unique else None)
        current = self.snapshot()
        if tuple(unique) == current.items and target is current.primary:
            return current
        self._syncing = True
        try:
            self.scene.clearSelection()
            for item in unique:
                item.setSelected(True)
        finally:
            self._syncing = False
        self._order = unique
        self._primary = target
        snapshot = self.snapshot()
        self.changed.emit(snapshot)
        return snapshot

    def replace(self, items, primary=None) -> SelectionSnapshot:
        return self._apply(items, primary)

    def extend(self, items, primary=None) -> SelectionSnapshot:
        current = list(self.snapshot().items)
        for item in items:
            if item not in current:
                current.append(item)
        return self._apply(current, primary or (current[-1] if current else None))

    def toggle(self, item) -> SelectionSnapshot:
        current = list(self.snapshot().items)
        if item in current:
            current.remove(item)
            primary = self._primary if self._primary in current \
                else (current[-1] if current else None)
        else:
            current.append(item)
            primary = item
        return self._apply(current, primary)

    def set_primary(self, item) -> None:
        if item in self.snapshot().items:
            self._primary = item
            self.changed.emit(self.snapshot())

    def clear(self) -> SelectionSnapshot:
        return self._apply(())

    def sync_from_scene(self) -> None:
        if self._syncing:
            return
        selected = list(self.scene.selectedItems())
        retained = [item for item in self._order if item in selected]
        retained.extend(item for item in selected if item not in retained)
        self._order = retained
        if self._primary not in retained:
            self._primary = retained[-1] if retained else None
        self.changed.emit(self.snapshot())

    def editable_items(self, *, include_locked=False, include_pipes=False):
        from azeo_control_trainer.core.hmi.pvms.rendering.items import PipeItem
        return tuple(item for item in self.snapshot().items
                     if (include_pipes or not isinstance(item, PipeItem))
                     and (include_locked or not is_locked(item)))


COMMON_PROPERTIES = ("x", "y", "w", "h", "rot", "visible", "locked",
                     "opacity", "layer")


def read_property(item, key: str):
    pvm = getattr(item, "pvm", None)
    if pvm is not None:
        return getattr(pvm, key)
    data = getattr(item, "data", None)
    if not isinstance(data, dict):
        raise KeyError(key)
    defaults = {"x": item.pos().x(), "y": item.pos().y(),
                "w": item.rect().width(), "h": item.rect().height(),
                "rot": 0.0, "visible": True, "locked": False,
                "opacity": 1.0, "layer": "background"}
    return data.get(key, defaults[key])


def aggregate(items, key: str) -> Aggregate:
    if key not in COMMON_PROPERTIES or not items:
        return Aggregate(AggregateKind.UNAVAILABLE)
    try:
        values = [read_property(item, key) for item in items]
    except (AttributeError, KeyError):
        return Aggregate(AggregateKind.UNAVAILABLE)
    first = values[0]
    return Aggregate(AggregateKind.UNIFORM, first) \
        if all(value == first for value in values[1:]) \
        else Aggregate(AggregateKind.MIXED)
