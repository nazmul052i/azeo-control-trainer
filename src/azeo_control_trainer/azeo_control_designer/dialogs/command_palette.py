"""Keyboard-first fuzzy command and block palette for Control Designer."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from azeo_control_trainer.core.presentation.brand import UI
from azeo_control_trainer.core.presentation.authoring_dialog import apply_compact_authoring_dialog


@dataclass(frozen=True)
class PaletteCommand:
    label: str
    category: str
    callback: Callable[[], object]
    keywords: str = ""
    enabled: bool = True

    @property
    def search_text(self) -> str:
        return f"{self.label} {self.category} {self.keywords}".lower()


def fuzzy_score(query: str, text: str) -> int | None:
    """Small deterministic subsequence scorer; lower is a better match."""
    query = "".join(query.lower().split())
    text = text.lower()
    if not query:
        return 0
    direct = text.find(query)
    if direct >= 0:
        return direct
    cursor = -1
    gaps = 0
    for char in query:
        found = text.find(char, cursor + 1)
        if found < 0:
            return None
        if cursor >= 0:
            gaps += found - cursor - 1
        cursor = found
    return 100 + gaps


class CommandPaletteDialog(QDialog):
    """Modeless palette so users can keep the canvas context visible."""

    def __init__(self, commands: list[PaletteCommand], parent=None):
        super().__init__(parent)
        self._commands = list(commands)
        self.setWindowTitle("Command Palette")
        apply_compact_authoring_dialog(self)
        self.setModal(False)
        self.resize(620, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        title = QLabel("COMMAND PALETTE  ·  Ctrl+Shift+P")
        title.setStyleSheet(f"font-weight: bold; color: {UI.blue};")
        layout.addWidget(title)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Run a command or insert a block…")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.results = QListWidget()
        self.results.setAlternatingRowColors(True)
        layout.addWidget(self.results, 1)

        self.search.textChanged.connect(self._refill)
        self.search.returnPressed.connect(self._run_current)
        self.results.itemActivated.connect(self._run_item)
        self._refill("")
        self.search.setFocus()

    def _refill(self, query: str):
        ranked = []
        for index, command in enumerate(self._commands):
            score = fuzzy_score(query, command.search_text)
            if score is not None:
                ranked.append((score, index, command))
        self.results.clear()
        for _score, _index, command in sorted(ranked, key=lambda row: (row[0], row[1])):
            item = QListWidgetItem(f"{command.label}\n{command.category}")
            item.setData(Qt.UserRole, command)
            item.setFlags(item.flags() | Qt.ItemIsEnabled if command.enabled
                          else item.flags() & ~Qt.ItemIsEnabled)
            self.results.addItem(item)
        if self.results.count():
            self.results.setCurrentRow(0)

    def _run_current(self):
        self._run_item(self.results.currentItem())

    def _run_item(self, item):
        if item is None:
            return
        command = item.data(Qt.UserRole)
        if not command.enabled:
            return
        self.accept()
        command.callback()
