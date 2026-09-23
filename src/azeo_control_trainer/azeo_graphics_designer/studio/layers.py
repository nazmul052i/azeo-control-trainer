"""Named authoring layers over the single PvmDisplay document.

Layers are organizational metadata on each placement, not a second scene
model.  The operator renderer therefore keeps consuming the same items while
the author gets bulk selection, visibility, locking and reassignment.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.menu_style import retain_menu

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QWidget

from azeo_control_trainer.core.hmi.compatibility import normalize_pvm_layer
from azeo_control_trainer.core.hmi.pvms.rendering.chrome import WF
from azeo_control_trainer.core.hmi.pvms.rendering.items import item_document_data

STANDARD_LAYERS = (
    "background", "pipes", "equipment", "pvms", "annotation", "navigation",
)


def layer_title(layer: str) -> str:
    """Return a product-facing label without changing saved layer keys."""
    key = str(layer or "").strip().casefold().replace(" ", "_")
    if normalize_pvm_layer(key) == "pvms":
        return "PVMs"
    return key.replace("_", " ").title()


def item_layer(item) -> str:
    pvm = getattr(item, "pvm", None)
    if pvm is not None:
        layer = str(pvm.layer or "pvms")
        return "pvms" if normalize_pvm_layer(layer) == "pvms" else layer
    data = item_document_data(item)
    explicit = str(data.get("layer", "") or "").strip()
    if explicit:
        return "pvms" if normalize_pvm_layer(explicit) == "pvms" else explicit
    kind = data.get("kind", "")
    if kind == "pipe":
        return "pipes"
    if kind == "symbol":
        return "equipment"
    if kind in ("text", "date_time"):
        return "annotation"
    if kind in ("display_link", "user_entry"):
        return "navigation"
    return "background"


def set_item_layer(item, layer: str) -> None:
    layer = str(layer or "background").strip().casefold().replace(" ", "_")
    if normalize_pvm_layer(layer) == "pvms":
        layer = "pvms"
    pvm = getattr(item, "pvm", None)
    if pvm is not None:
        item.pvm = replace(pvm, layer=layer)
    else:
        item.data["layer"] = layer


class LayersPane(QTreeWidget):
    """Layer hierarchy with bulk authoring commands."""

    def __init__(self, studio, parent: QWidget | None = None):
        super().__init__(parent)
        self.studio = studio
        self.setHeaderLabels(("Layer / element", "Count"))
        self.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.setStyleSheet(
            f"background: {WF['pane']}; border: 1px solid {WF['bd_lt']};"
            "font-size: 8.25pt;")
        self.itemDoubleClicked.connect(self._activate)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.reload()

    def _items(self) -> list:
        return self.studio._items() + self.studio._static_items() \
            + self.studio._pipe_items()

    @staticmethod
    def _label(item) -> str:
        pvm = getattr(item, "pvm", None)
        if pvm is not None:
            path = next(iter(pvm.params.values()), "")
            return f"{pvm.block_type}  {path}".strip()
        data = item.data
        return str(data.get("tag") or data.get("text")
                   or data.get("symbol") or data.get("kind") or "element")

    def reload(self) -> None:
        self.clear()
        buckets: dict[str, list] = {}
        for item in self._items():
            buckets.setdefault(item_layer(item), []).append(item)
        names = list(STANDARD_LAYERS)
        names.extend(sorted(set(buckets) - set(names)))
        for name in names:
            parent = QTreeWidgetItem(self)
            parent.setText(0, layer_title(name))
            parent.setText(1, str(len(buckets.get(name, ()))))
            parent.setData(0, Qt.UserRole, ("layer", name))
            for item in buckets.get(name, ()):
                child = QTreeWidgetItem(parent)
                child.setText(0, self._label(item))
                child.setData(0, Qt.UserRole, ("item", item))
                child.setSelected(item.isSelected())
        self.expandAll()
        self.resizeColumnToContents(0)

    def _chosen_items(self, row=None) -> list:
        rows = [row] if row is not None else self.selectedItems()
        chosen = []
        for current in rows:
            if current is None:
                continue
            kind, value = current.data(0, Qt.UserRole) or ("", None)
            if kind == "item":
                chosen.append(value)
            elif kind == "layer":
                chosen.extend(item for item in self._items()
                              if item_layer(item) == value)
        return list(dict.fromkeys(chosen))

    def _activate(self, row, _column) -> None:
        chosen = self._chosen_items(row)
        if not chosen:
            return
        scene = self.studio.canvas.scene()
        scene.clearSelection()
        for item in chosen:
            item.setSelected(True)
        self.studio.canvas.centerOn(chosen[0])

    def assign_selected(self, layer: str) -> int:
        chosen = [item for item in self._items() if item.isSelected()]
        if not chosen:
            return 0
        self.studio.checkpoint()
        for item in chosen:
            set_item_layer(item, layer)
        self.studio.mark_unsaved()
        self.reload()
        return len(chosen)

    def _set_state(self, field: str, value: bool, items: list) -> None:
        if not items:
            return
        self.studio.checkpoint()
        for item in items:
            pvm = getattr(item, "pvm", None)
            if field == "visible":
                if pvm is not None:
                    item.pvm_visible = value
                    item.pvm = replace(item.pvm, visible=value)
                else:
                    item.data["visible"] = value
            else:
                if pvm is not None:
                    item.pvm_locked = value
                    item.pvm = replace(item.pvm, locked=value)
                else:
                    item.data["locked"] = value
        # Let the one mode gate apply movability.  Directly unlocking here
        # made pipes movable and could make an object movable in TEST mode.
        self.studio._apply_mode()
        self.studio.apply_authoring_visibility()
        self.studio.mark_unsaved()
        self.reload()

    def isolate(self, layer: str) -> None:
        self.studio.isolate_layer(layer)
        self.reload()

    def _menu(self, pos) -> None:
        row = self.itemAt(pos)
        payload = row.data(0, Qt.UserRole) if row is not None else None
        from azeo_control_trainer.core.presentation.menu_style import \
            studio_menu
        menu = studio_menu("LAYERS", "Organize the active display")
        move = menu.addMenu("Move selected to layer")
        for layer in STANDARD_LAYERS:
            move.addAction(layer_title(layer)).triggered.connect(
                lambda _checked=False, name=layer: self.assign_selected(name))
        if payload and payload[0] == "layer":
            layer = payload[1]
            items = self._chosen_items(row)
            menu.addSeparator()
            menu.addAction("Select Layer").triggered.connect(
                lambda: self._activate(row, 0))
            menu.addAction("Isolate Layer").triggered.connect(
                lambda: self.isolate(layer))
            menu.addAction("Show Layer").triggered.connect(
                lambda: self._set_state("visible", True, items))
            menu.addAction("Hide Layer").triggered.connect(
                lambda: self._set_state("visible", False, items))
            menu.addAction("Lock Layer").triggered.connect(
                lambda: self._set_state("locked", True, items))
            menu.addAction("Unlock Layer").triggered.connect(
                lambda: self._set_state("locked", False, items))
        menu.addSeparator()
        menu.addAction("Refresh").triggered.connect(self.reload)
        retain_menu(self, menu, "_context_menu")
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not is_headless():
            menu.exec_transient(self.viewport().mapToGlobal(pos))
