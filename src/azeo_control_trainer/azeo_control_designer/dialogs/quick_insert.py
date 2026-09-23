"""Searchable compatible-block picker for a wire dropped on empty canvas."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from azeo_control_trainer.core.presentation.brand import UI
from azeo_control_trainer.core.presentation.authoring_dialog import (
    apply_compact_authoring_dialog,
)
from azeo_control_trainer.core.strategy.model.block_registry import registry
from azeo_control_trainer.core.strategy.model.strategy_graph import types_compatible
from azeo_control_trainer.core.strategy.model.terminal import (
    DataType,
    TerminalDirection,
)


@dataclass(frozen=True)
class QuickInsertCandidate:
    """One block pin that can complete a connection from the source pin."""

    block_type: str
    display_name: str
    category: str
    terminal_name: str
    terminal_type: DataType
    terminal_direction: TerminalDirection

    @property
    def search_text(self) -> str:
        return " ".join((
            self.block_type,
            self.display_name,
            self.category,
            self.terminal_name,
            self.terminal_type.value,
        )).lower()


@dataclass(frozen=True)
class WireInsertCandidate:
    """Block input/output pair that can be spliced into an existing wire."""

    block_type: str
    display_name: str
    category: str
    input_name: str
    output_name: str
    terminal_type: DataType

    @property
    def terminal_name(self) -> str:
        return f"{self.input_name} → {self.output_name}"

    @property
    def search_text(self) -> str:
        return " ".join((self.block_type, self.display_name, self.category,
                         self.input_name, self.output_name,
                         self.terminal_type.value)).lower()


def compatible_wire_insert_candidates(graph, wire) -> list[WireInsertCandidate]:
    source = graph.blocks[wire.src_block_id].outputs[wire.src_terminal]
    destination = graph.blocks[wire.dst_block_id].inputs[wire.dst_terminal]
    candidates: list[WireInsertCandidate] = []
    for block_type, block_cls in sorted(registry.all_types().items()):
        if getattr(block_cls, "is_special_palette_item", False):
            continue
        try:
            block = block_cls(instance_name=f"__wire_insert_{block_type}")
        except Exception:
            continue
        for input_name, input_terminal in block.inputs.items():
            if input_terminal.hidden or not types_compatible(
                    source.data_type, input_terminal.data_type):
                continue
            for output_name, output_terminal in block.outputs.items():
                if output_terminal.hidden or not types_compatible(
                        output_terminal.data_type, destination.data_type):
                    continue
                candidates.append(WireInsertCandidate(
                    block_type=block_type,
                    display_name=str(getattr(block_cls, "display_name", block_type)),
                    category=str(getattr(block_cls, "category").value),
                    input_name=input_name,
                    output_name=output_name,
                    terminal_type=output_terminal.data_type,
                ))
    return candidates


def compatible_insert_candidates(source_terminal) -> list[QuickInsertCandidate]:
    """Return every registered pin that can legally face ``source_terminal``.

    This is type compatibility only.  The candidate is a new block, so its
    input is necessarily free; final graph validation still runs when chosen.
    """
    source_direction = source_terminal.direction
    source_type = source_terminal.data_type
    candidates: list[QuickInsertCandidate] = []
    for block_type, block_cls in sorted(registry.all_types().items()):
        if getattr(block_cls, "is_special_palette_item", False):
            continue
        try:
            block = block_cls(instance_name=f"__quick_insert_{block_type}")
        except Exception:  # An unavailable optional block must not break wiring.
            continue
        terminals = (block.inputs if source_direction == TerminalDirection.OUTPUT
                     else block.outputs)
        for name, terminal in terminals.items():
            if terminal.hidden:
                continue
            compatible = (
                types_compatible(source_type, terminal.data_type)
                if source_direction == TerminalDirection.OUTPUT
                else types_compatible(terminal.data_type, source_type)
            )
            if not compatible:
                continue
            candidates.append(QuickInsertCandidate(
                block_type=block_type,
                display_name=str(getattr(block_cls, "display_name", block_type)),
                category=str(getattr(block_cls, "category").value),
                terminal_name=name,
                terminal_type=terminal.data_type,
                terminal_direction=terminal.direction,
            ))
    return candidates


class QuickInsertDialog(QDialog):
    """Modeless keyboard-first chooser used at the end of a loose wire."""

    candidateChosen = Signal(object)

    def __init__(self, source_terminal=None, parent=None, *, candidates=None,
                 prompt: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Quick Insert Compatible Block")
        apply_compact_authoring_dialog(self)
        self.setModal(False)
        self.resize(520, 420)
        self._all = (list(candidates) if candidates is not None
                     else compatible_insert_candidates(source_terminal))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        prompt_text = prompt or (
            f"Complete {source_terminal.name} · {source_terminal.data_type.value}"
            " with a compatible block pin"
        )
        prompt_label = QLabel(prompt_text)
        prompt_label.setStyleSheet(f"color: {UI.blue}; font-weight: bold;")
        layout.addWidget(prompt_label)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Type a block, category, terminal, or data type…")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)

        self.results = QListWidget()
        self.results.setAlternatingRowColors(True)
        layout.addWidget(self.results, 1)

        self.search.textChanged.connect(self._refill)
        self.search.returnPressed.connect(self._choose_current)
        self.results.itemActivated.connect(self._choose_item)
        self._refill("")
        self.search.setFocus()

    def _refill(self, query: str):
        words = [word for word in query.lower().split() if word]
        selected = self.results.currentItem()
        previous = selected.data(Qt.UserRole) if selected else None
        self.results.clear()
        first = None
        for candidate in self._all:
            if not all(word in candidate.search_text for word in words):
                continue
            item = QListWidgetItem(
                f"{candidate.display_name}  [{candidate.block_type}]"
                f"\n{candidate.category} · {candidate.terminal_name}"
                f" · {candidate.terminal_type.value}"
            )
            item.setData(Qt.UserRole, candidate)
            item.setToolTip(
                f"Insert {candidate.block_type} and connect its "
                f"{candidate.terminal_name} pin"
            )
            self.results.addItem(item)
            first = first or item
            if candidate == previous:
                self.results.setCurrentItem(item)
        if self.results.currentItem() is None and first is not None:
            self.results.setCurrentItem(first)

    def _choose_current(self):
        self._choose_item(self.results.currentItem())

    def _choose_item(self, item):
        if item is None:
            return
        candidate = item.data(Qt.UserRole)
        self.candidateChosen.emit(candidate)
        self.accept()
