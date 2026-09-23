"""The control-data browser — modules, blocks and addressable parameters.

This is the engineering-data half of Graphics Designer. Block rows drag a
compatible PVM; terminal and configuration rows drag a Data Link. The visual
classes themselves remain in the Palette — configured control objects and
reusable drawing classes are deliberately not the same tree.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.menu_style import retain_menu

import json

from PySide6.QtCore import QMimeData, Qt
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QHeaderView, QTreeWidget, QTreeWidgetItem

from azeo_control_trainer.core.hmi.pvms.rendering.chrome import MIME_BLOCK, MIME_PARAMETER

BLOCK_ROLE = Qt.UserRole
PARAMETER_ROLE = Qt.UserRole + 1
KIND_ROLE = Qt.UserRole + 2
SEARCH_ROLE = Qt.UserRole + 3

# Compatibility aliases for code and tests written before the browser became
# the shared selector model.  New callers should use the public names above.
_PARAM_ROLE = PARAMETER_ROLE
_KIND_ROLE = KIND_ROLE


class ControlBrowser(QTreeWidget):
    """Configured modules, blocks, terminals and configuration values."""

    def __init__(self, graphs_provider, parent=None):
        super().__init__(parent)
        self._graphs = graphs_provider
        self.setHeaderLabels(["Control object", "Type", "Value"])
        self.setDragEnabled(True)
        self.setAlternatingRowColors(True)
        self.setUniformRowHeights(True)
        self.setRootIsDecorated(True)
        self.setIndentation(18)
        header = self.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setMinimumSectionSize(72)
        self.reload()

    @staticmethod
    def item_kind(item) -> str:
        return str(item.data(0, KIND_ROLE) or "") if item else ""

    @staticmethod
    def block_payload(item) -> tuple[str, str] | None:
        payload = item.data(0, BLOCK_ROLE) if item else None
        return payload if isinstance(payload, tuple) and len(payload) == 2 \
            else None

    @staticmethod
    def parameter_payload(item) -> dict | None:
        payload = item.data(0, PARAMETER_ROLE) if item else None
        return payload if isinstance(payload, dict) else None

    @classmethod
    def item_path(cls, item) -> str:
        parameter = cls.parameter_payload(item)
        if parameter:
            return str(parameter.get("path", ""))
        block = cls.block_payload(item)
        if block:
            return str(block[0])
        if item is None:
            return ""
        kind = cls.item_kind(item)
        if kind == "module":
            return item.text(0)
        if kind == "folder" and item.parent() is not None:
            parent = cls.block_payload(item.parent())
            if parent:
                suffix = "CONFIG" if item.text(0) == "Configuration" \
                    else item.text(0)
                return f"{parent[0]}/{suffix}"
        return item.text(0)

    @classmethod
    def search_text(cls, item) -> str:
        """Text indexed by both the dock and modal parameter browsers."""
        if item is None:
            return ""
        stored = str(item.data(0, SEARCH_ROLE) or "")
        columns = " ".join(item.text(column)
                           for column in range(item.columnCount()))
        return f"{stored} {columns}".strip().lower()

    def reload(self) -> None:
        # Build detached subtrees so each parameter's data/tooltip does not
        # notify the live view and invalidate its contents-sized columns.
        # Large projects otherwise block the Studio launch for seconds.
        from ..component_icons import block_icon
        from azeo_control_trainer.core.presentation.studio_icons import studio_icon

        modules = []
        module_icon = studio_icon("templates", 20)
        for module, graph in sorted(self._graphs().items()):
            module_item = QTreeWidgetItem(
                [module, "MODULE", f"{len(graph.blocks)} blocks"])
            module_item.setData(0, KIND_ROLE, "module")
            module_item.setIcon(0, module_icon)
            description = str(getattr(graph, "description", "") or "")
            module_item.setData(0, SEARCH_ROLE, f"{module} {description}")
            module_item.setToolTip(0, f"Control module {module}")
            modules.append(module_item)
            for block in sorted(graph.blocks.values(),
                                key=lambda one: one.instance_name):
                child = QTreeWidgetItem(
                    [block.instance_name, block.block_type, ""])
                child.setData(0, BLOCK_ROLE,
                              (f"{module}/{block.instance_name}",
                               block.block_type))
                child.setData(0, KIND_ROLE, "block")
                child.setIcon(0, block_icon(block.block_type))
                child.setData(
                    0, SEARCH_ROLE,
                    f"{module}/{block.instance_name} {block.block_type} block {description} "
                    f"{getattr(block, 'description', '')}")
                child.setToolTip(
                    0, f"Drag {block.instance_name} to place a compatible "
                    "function-block PVM")
                module_item.addChild(child)

                base = f"{module}/{block.instance_name}"
                for label, terminals in (("Inputs", block.inputs),
                                         ("Outputs", block.outputs)):
                    folder = QTreeWidgetItem(
                        [label, "PARAMETERS", f"{len(terminals)}"])
                    folder.setData(0, KIND_ROLE, "folder")
                    folder.setData(
                        0, SEARCH_ROLE,
                        f"{base}/{label} {block.block_type} {label}")
                    child.addChild(folder)
                    for name, terminal in sorted(terminals.items()):
                        if getattr(terminal, "hidden", False):
                            continue
                        data_type = getattr(
                            getattr(terminal, "data_type", None),
                            "value", "")
                        value = getattr(terminal, "value", "")
                        row = QTreeWidgetItem(
                            [name, str(data_type), str(value)])
                        direction = label.lower().rstrip("s")
                        row.setData(0, PARAMETER_ROLE, {
                            "path": f"{base}/{name}",
                            "data_type": str(data_type),
                            "kind": "terminal",
                            "direction": direction,
                            "block_type": block.block_type,
                            "value": value,
                            "units": getattr(terminal, "units", ""),
                            "range": getattr(terminal, "eu_range", None),
                            "quality": getattr(getattr(terminal, "status", None), "name", "Unknown"),
                        })
                        row.setData(0, KIND_ROLE, "parameter")
                        row.setData(
                            0, SEARCH_ROLE,
                            f"{base}/{name} {module} {block.instance_name} "
                            f"{block.block_type} {direction} {data_type} {value} {description} "
                            f"{getattr(terminal, 'units', '')}")
                        row.setToolTip(
                            0, f"{base}/{name}\n{label[:-1]} parameter "
                            f"\u00b7 {data_type} \u00b7 current value {value}\n"
                            "Drag to create a Data Link")
                        folder.addChild(row)

                config = dict(getattr(block.config, "params", {}) or {})
                schema = getattr(block, "get_config_schema", lambda: {})()
                names = sorted(set(config) | set(schema))
                if names:
                    folder = QTreeWidgetItem(
                        ["Configuration", "CONFIG", f"{len(names)}"])
                    folder.setData(0, KIND_ROLE, "folder")
                    folder.setData(
                        0, SEARCH_ROLE,
                        f"{base}/CONFIG {block.block_type} configuration")
                    child.addChild(folder)
                    for name in names:
                        value = config.get(name)
                        if value is None and name in schema:
                            definition = schema[name]
                            if isinstance(definition, (tuple, list)) \
                                    and len(definition) > 1:
                                value = definition[1]
                        row = QTreeWidgetItem(
                            [name, "CONFIG", "" if value is None
                             else str(value)])
                        row.setData(0, PARAMETER_ROLE, {
                            "path": f"{base}/CONFIG/{name}",
                            "data_type": type(value).__name__.upper(),
                            "kind": "config",
                            "direction": "configuration",
                            "block_type": block.block_type,
                            "value": value,
                        })
                        row.setData(0, KIND_ROLE, "parameter")
                        row.setData(
                            0, SEARCH_ROLE,
                            f"{base}/CONFIG/{name} {module} "
                            f"{block.instance_name} {block.block_type} "
                            f"configuration config {value}")
                        row.setToolTip(
                            0, f"Drag {base}/CONFIG/{name} to create "
                            "a Data Link")
                        folder.addChild(row)
        self.clear()
        self.addTopLevelItems(modules)
        for module_item in modules:
            module_item.setExpanded(True)

    def startDrag(self, actions) -> None:           # noqa: N802
        item = self.currentItem()
        block_payload = self.block_payload(item)
        parameter_payload = self.parameter_payload(item)
        if not block_payload and not parameter_payload:
            return
        mime = QMimeData()
        if parameter_payload:
            mime.setData(MIME_PARAMETER,
                         json.dumps(parameter_payload).encode())
        else:
            path, block_type = block_payload
            mime.setData(MIME_BLOCK, json.dumps(
                {"path": path, "block_type": block_type}).encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)

    def contextMenuEvent(self, event) -> None:      # noqa: N802
        """Sheet 8.4 ctx.block — the control browser's block menu."""
        item = self.itemAt(event.pos())
        block_payload = self.block_payload(item)
        parameter_payload = self.parameter_payload(item)
        if not block_payload and not parameter_payload:
            return
        from azeo_control_trainer.core.presentation.menu_style \
            import studio_menu
        if parameter_payload:
            path = parameter_payload["path"]
            title = path.rsplit("/", 1)[-1]
            menu = studio_menu(title, "Control parameter")
            place = menu.addAction("Create Data Link")
        else:
            path, block_type = block_payload
            menu = studio_menu(f"{block_type}  {path.split('/')[-1]}",
                               "Control module block")
            place = menu.addAction("Place Compatible PVM")
        font = place.font()
        font.setBold(True)
        place.setFont(font)
        window = self.window()
        if parameter_payload and hasattr(window, "place_parameter_link"):
            place.triggered.connect(
                lambda: window.place_parameter_link(path))
        elif block_payload and hasattr(window, "place_from_browser"):
            place.triggered.connect(
                lambda: window.place_from_browser(path, block_type))
        menu.addSeparator()
        copy_path = menu.addAction("Copy path")
        copy_path.triggered.connect(
            lambda: QApplicationClipboard(path))
        copy_node = menu.addAction("Copy UA NodeId")
        node_path = path if parameter_payload else f"{path}/PV"
        copy_node.triggered.connect(
            lambda: QApplicationClipboard(f"ns=2;s={node_path}"))
        retain_menu(self, menu, "_context_menu")
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not is_headless():
            menu.exec_transient(event.globalPos())


def QApplicationClipboard(text: str) -> None:
    from PySide6.QtWidgets import QApplication
    QApplication.clipboard().setText(text)
