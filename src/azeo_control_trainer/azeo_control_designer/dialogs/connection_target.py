"""Searchable existing-pin picker for connection endpoint refactoring."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from azeo_control_trainer.core.presentation.brand import UI
from azeo_control_trainer.core.presentation.authoring_dialog import apply_compact_authoring_dialog
from azeo_control_trainer.core.strategy.model.strategy_graph import types_compatible


@dataclass(frozen=True)
class ConnectionTarget:
    block_id: str
    terminal_name: str
    label: str
    data_type: str


def reconnect_targets(graph, wire, endpoint: str) -> list[ConnectionTarget]:
    """List legal alternative sources or destinations for ``wire``."""
    targets = []
    if endpoint == "source":
        destination = graph.blocks[wire.dst_block_id].inputs[wire.dst_terminal]
        for block in graph.blocks.values():
            for name, terminal in block.outputs.items():
                if terminal.hidden or not types_compatible(
                        terminal.data_type, destination.data_type):
                    continue
                targets.append(ConnectionTarget(
                    block.id, name, f"{block.instance_name}.{name}",
                    terminal.data_type.value))
    else:
        source = graph.blocks[wire.src_block_id].outputs[wire.src_terminal]
        occupied = {
            (other.dst_block_id, other.dst_terminal)
            for other in graph.wires.values() if other.id != wire.id
        }
        for block in graph.blocks.values():
            for name, terminal in block.inputs.items():
                if terminal.hidden or (block.id, name) in occupied:
                    continue
                if not types_compatible(source.data_type, terminal.data_type):
                    continue
                targets.append(ConnectionTarget(
                    block.id, name, f"{block.instance_name}.{name}",
                    terminal.data_type.value))
    return sorted(targets, key=lambda target: target.label.casefold())


class ConnectionTargetDialog(QDialog):
    targetChosen = Signal(object)

    def __init__(self, targets: list[ConnectionTarget], endpoint: str, parent=None):
        super().__init__(parent)
        self._targets = list(targets)
        self.setWindowTitle(f"Reconnect {endpoint.title()}")
        apply_compact_authoring_dialog(self)
        self.setModal(False)
        self.resize(480, 420)
        layout = QVBoxLayout(self)
        title = QLabel(f"SELECT A NEW {endpoint.upper()} PIN")
        title.setStyleSheet(f"font-weight: bold; color: {UI.blue};")
        layout.addWidget(title)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter block or terminal…")
        layout.addWidget(self.search)
        self.results = QListWidget()
        layout.addWidget(self.results, 1)
        self.search.textChanged.connect(self._refill)
        self.search.returnPressed.connect(self._choose_current)
        self.results.itemActivated.connect(self._choose)
        self._refill("")
        self.search.setFocus()

    def _refill(self, query: str):
        query = query.casefold().strip()
        self.results.clear()
        for target in self._targets:
            if query and query not in f"{target.label} {target.data_type}".casefold():
                continue
            item = QListWidgetItem(f"{target.label}  ·  {target.data_type}")
            item.setData(Qt.UserRole, target)
            self.results.addItem(item)
        if self.results.count():
            self.results.setCurrentRow(0)

    def _choose_current(self):
        self._choose(self.results.currentItem())

    def _choose(self, item):
        if item is None:
            return
        self.targetChosen.emit(item.data(Qt.UserRole))
        self.accept()
