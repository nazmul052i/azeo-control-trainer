"""Current-module hierarchy view for Control Designer.

The project tree answers *where is the module?*; this pane answers *what is
inside the module I am editing?*.  Keeping those questions separate mirrors
the Explorer/Control Designer boundary and makes composite drill-down usable on
large algorithms.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

_PARAMETER_ACCESS_LABEL = {
    "input": "Input",
    "output": "Output",
    "internal_read": "Internal read",
    "internal_write": "Internal write",
}


class ModuleHierarchyPanel(QWidget):
    blockActivated = Signal(str)

    ROLE_BLOCK_ID = Qt.UserRole

    def __init__(self, parent=None):
        super().__init__(parent)
        self._graph = None
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)
        row = QHBoxLayout()
        title = QLabel("MODULE HIERARCHY")
        title.setStyleSheet(f"font-weight: 700; color: {UI.blue};")
        row.addWidget(title)
        row.addStretch(1)
        root.addLayout(row)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter blocks, composites, parameters…")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._apply_filter)
        root.addWidget(self.filter)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Object", "Type / value"])
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setColumnWidth(0, 165)
        self.tree.itemDoubleClicked.connect(self._activate)
        root.addWidget(self.tree, 1)

    def set_graph(self, graph) -> None:
        self._graph = graph
        self.rebuild()

    def rebuild(self) -> None:
        selected_id = None
        current = self.tree.currentItem()
        if current is not None:
            selected_id = current.data(0, self.ROLE_BLOCK_ID)
        self.tree.clear()
        graph = self._graph
        if graph is None:
            return
        module = QTreeWidgetItem([graph.name, "Control Module"])
        module.setExpanded(True)
        self.tree.addTopLevelItem(module)

        parameters = graph.module_parameters()
        if parameters:
            params = QTreeWidgetItem(["Module Parameters", str(len(parameters))])
            module.addChild(params)
            for name, spec in sorted(parameters.items()):
                access_id = str(spec.get("access", ""))
                access = _PARAMETER_ACCESS_LABEL.get(
                    access_id, access_id.replace("_", " ").title())
                params.addChild(QTreeWidgetItem([
                    name, f"{spec.get('value')}  [{access}]"
                ]))

        by_category: dict[str, list] = {}
        for block in graph.blocks.values():
            category = getattr(getattr(block, "category", None), "value", None) \
                or str(getattr(block, "category", "Other"))
            by_category.setdefault(str(category), []).append(block)
        for category, blocks in sorted(by_category.items()):
            group = QTreeWidgetItem([category, str(len(blocks))])
            group.setExpanded(True)
            module.addChild(group)
            for block in sorted(blocks, key=lambda b: (
                    getattr(b, "_exec_order", -1), b.instance_name.lower())):
                type_name = getattr(block, "block_type", type(block).__name__)
                order = getattr(block, "_exec_order", -1)
                detail = type_name if order < 0 else f"{type_name}  •  #{order + 1}"
                item = QTreeWidgetItem([block.instance_name, detail])
                item.setData(0, self.ROLE_BLOCK_ID, block.id)
                group.addChild(item)
                # A composite's boundary is the useful at-a-glance hierarchy;
                # its full interior opens on drill-down rather than producing a
                # thousand-row tree here.
                inner = getattr(block, "inner_graph", None)
                if inner is not None:
                    item.addChild(QTreeWidgetItem([
                        "Interior", f"{len(inner.blocks)} blocks / "
                        f"{len(inner.wires)} wires"
                    ]))
                for terminal in block.inputs.values():
                    if terminal.connected or not terminal.hidden:
                        item.addChild(QTreeWidgetItem([
                            f"IN  {terminal.name}", terminal.data_type.value
                        ]))
                for terminal in block.outputs.values():
                    if terminal.connected or not terminal.hidden:
                        item.addChild(QTreeWidgetItem([
                            f"OUT {terminal.name}", terminal.data_type.value
                        ]))
                if block.id == selected_id:
                    self.tree.setCurrentItem(item)
        self._apply_filter(self.filter.text())

    def select_block(self, block_id: str | None) -> None:
        if not block_id:
            self.tree.clearSelection()
            return
        iterator = self.tree.invisibleRootItem()
        stack = [iterator.child(i) for i in range(iterator.childCount())]
        while stack:
            item = stack.pop()
            if item.data(0, self.ROLE_BLOCK_ID) == block_id:
                self.tree.setCurrentItem(item)
                self.tree.scrollToItem(item)
                return
            stack.extend(item.child(i) for i in range(item.childCount()))

    def _activate(self, item: QTreeWidgetItem, _column: int) -> None:
        block_id = item.data(0, self.ROLE_BLOCK_ID)
        if block_id:
            self.blockActivated.emit(str(block_id))

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()

        def visit(item: QTreeWidgetItem) -> bool:
            child_match = False
            for index in range(item.childCount()):
                child_match = visit(item.child(index)) or child_match
            own = needle in (item.text(0) + " " + item.text(1)).lower()
            visible = not needle or own or child_match
            item.setHidden(not visible)
            if needle and child_match:
                item.setExpanded(True)
            return visible

        root = self.tree.invisibleRootItem()
        for index in range(root.childCount()):
            visit(root.child(index))
