"""Composite boundary-port editor and selected-pin exposure workflow."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from azeo_control_trainer.core.presentation.brand import UI
from azeo_control_trainer.core.presentation.authoring_dialog import apply_compact_authoring_dialog
from azeo_control_trainer.core.strategy.blocks.composite_blocks import (
    InportBlock,
    OutportBlock,
)
from azeo_control_trainer.core.strategy.model.terminal import DataType


@dataclass(frozen=True)
class ExposableTerminal:
    block_id: str
    block_name: str
    terminal_name: str
    direction: str
    data_type: DataType


def exposable_selected_terminals(scene) -> list[ExposableTerminal]:
    from ..items.block_item import BlockItem

    selected = [item for item in scene.selectedItems()
                if isinstance(item, BlockItem)
                and item.block.block_type not in {"INPORT", "OUTPORT"}]
    if len(selected) != 1:
        return []
    block = selected[0].block
    connected_inputs = {
        wire.dst_terminal for wire in scene.graph.wires.values()
        if wire.dst_block_id == block.id
    }
    connected_outputs = {
        wire.src_terminal for wire in scene.graph.wires.values()
        if wire.src_block_id == block.id
    }
    result = [
        ExposableTerminal(block.id, block.instance_name, name, "input", terminal.data_type)
        for name, terminal in block.inputs.items()
        if not terminal.hidden and name not in connected_inputs
    ]
    result.extend(
        ExposableTerminal(block.id, block.instance_name, name, "output", terminal.data_type)
        for name, terminal in block.outputs.items()
        if not terminal.hidden and name not in connected_outputs
    )
    return result


class CompositePortsDialog(QDialog):
    """Add standalone ports or expose an unconnected selected inner pin."""

    def __init__(self, composite, inner_scene, parent_scene, parent=None):
        super().__init__(parent)
        self._composite = composite
        self._scene = inner_scene
        self._parent_scene = parent_scene
        self.setWindowTitle(f"Composite Ports — {composite.instance_name}")
        apply_compact_authoring_dialog(self)
        self.setModal(False)
        self.resize(590, 470)
        layout = QVBoxLayout(self)
        title = QLabel("COMPOSITE BOUNDARY PORTS")
        title.setStyleSheet(f"font-weight: bold; color: {UI.blue};")
        layout.addWidget(title)
        self.ports = QListWidget()
        layout.addWidget(self.ports, 1)

        add_row = QHBoxLayout()
        self.direction = QComboBox()
        self.direction.addItems(["Input", "Output"])
        self.name = QLineEdit()
        self.name.setPlaceholderText("Port name")
        self.data_type = QComboBox()
        self.data_type.addItems([member.value for member in DataType])
        add = QPushButton("Add Port")
        add.clicked.connect(self._add_port)
        add_row.addWidget(self.direction)
        add_row.addWidget(self.name, 1)
        add_row.addWidget(self.data_type)
        add_row.addWidget(add)
        layout.addLayout(add_row)

        expose_row = QHBoxLayout()
        self.exposable = QComboBox()
        expose = QPushButton("Expose Selected Pin")
        expose.clicked.connect(self._expose)
        expose_row.addWidget(self.exposable, 1)
        expose_row.addWidget(expose)
        layout.addLayout(expose_row)
        hint = QLabel(
            "Select one inner block to expose an unconnected pin. "
            "Every addition is undoable in this composite tab."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {UI.text_muted};")
        layout.addWidget(hint)
        self._refresh()

    def _refresh(self):
        self.ports.clear()
        for block in self._scene.graph.blocks.values():
            if block.block_type not in {"INPORT", "OUTPORT"}:
                continue
            direction = "IN" if block.block_type == "INPORT" else "OUT"
            name = str(block.config.params.get("port_name", block.instance_name))
            dtype = str(block.config.params.get("data_type", "FLOAT"))
            item = QListWidgetItem(f"{direction:3}  {name}  ·  {dtype}")
            item.setData(Qt.UserRole, block.id)
            self.ports.addItem(item)
        self.exposable.clear()
        for terminal in exposable_selected_terminals(self._scene):
            self.exposable.addItem(
                f"{terminal.direction.upper()}  {terminal.block_name}."
                f"{terminal.terminal_name}  ·  {terminal.data_type.value}",
                terminal,
            )

    def _unique_name(self, requested: str) -> str:
        requested = requested.strip() or "PORT"
        used = {name.casefold() for name in self._composite.inputs}
        used.update(name.casefold() for name in self._composite.outputs)
        candidate = requested
        number = 2
        while candidate.casefold() in used:
            candidate = f"{requested}_{number}"
            number += 1
        return candidate

    def _make_port(self, direction: str, name: str, dtype: DataType):
        input_port = direction == "input"
        block = (InportBlock(name) if input_port else OutportBlock(name))
        block.config.params.update(port_name=name, data_type=dtype.value)
        block._apply_config()
        count = sum(existing.block_type == block.block_type
                    for existing in self._scene.graph.blocks.values())
        x = -900 if input_port else 1500
        return self._scene.add_block(block, QPointF(x, -400 + count * 100))

    def _add_port(self):
        direction = "input" if self.direction.currentIndex() == 0 else "output"
        name = self._unique_name(self.name.text())
        dtype = DataType(self.data_type.currentText())
        self._make_port(direction, name, dtype)
        self.name.clear()
        self._refresh()

    def _expose(self):
        terminal = self.exposable.currentData()
        if terminal is None:
            return
        name = self._unique_name(terminal.terminal_name)
        self._scene.undo_stack.beginMacro(f"Expose {terminal.block_name}.{terminal.terminal_name}")
        try:
            port_item = self._make_port(terminal.direction, name, terminal.data_type)
            if port_item is None:
                return
            if terminal.direction == "input":
                self._scene.add_wire(
                    port_item.block.id, "OUT", terminal.block_id, terminal.terminal_name)
            else:
                self._scene.add_wire(
                    terminal.block_id, terminal.terminal_name, port_item.block.id, "IN")
        finally:
            self._scene.undo_stack.endMacro()
        self._refresh()
