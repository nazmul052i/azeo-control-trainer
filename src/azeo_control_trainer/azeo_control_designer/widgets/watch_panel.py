r"""Watch panel — DCS-standard pinned parameter monitor.

A floating widget that lists "pinned" terminals from blocks across one
or more strategy graphs. Updated at 5 Hz from the block's live state,
so operators can keep critical parameters in sight while editing or
debugging a strategy.

Each row shows:

    Block.terminal  |  Dir  |  Type  |  Value  |  Forced  |  Actions

Actions:
    Force...    — opens the existing ForceValueDialog
    Remove      — drop the row from the watch list

Pinning workflow:
  * Right-click any block on the canvas -> *Add to Watch* -> submenu
    listing every terminal -> click adds the row.
  * Right-click a forced terminal in the BlockDiagnosticsDialog ->
    *Add to Watch* (future)
  * Drag-drop from the block palette / canvas (future)

The panel is a singleton — :func:`open_watch_panel()` returns the same
instance across calls so one watch list survives across blocks.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI
from azeo_control_trainer.core.presentation.authoring_style import AUTHORING_CHROME_QSS
from azeo_control_trainer.core.presentation.headless import is_headless

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)


@dataclass
class WatchEntry:
    """One pinned parameter."""
    block: object           # FunctionBlock (kept by reference)
    terminal: str
    direction: str          # "IN" or "OUT"

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.block.id, self.terminal, self.direction)

    def value(self):
        terms = self.block.inputs if self.direction == "IN" else self.block.outputs
        t = terms.get(self.terminal)
        return None if t is None else t.value

    def is_forced(self) -> bool:
        terms = self.block.inputs if self.direction == "IN" else self.block.outputs
        t = terms.get(self.terminal)
        return bool(t and t.forced)

    def data_type(self) -> str:
        terms = self.block.inputs if self.direction == "IN" else self.block.outputs
        t = terms.get(self.terminal)
        return t.data_type.value if t else ""


class WatchPanel(QWidget):
    """Floating list of pinned parameters across one or more blocks."""

    entryAdded   = Signal(str, str, str)  # block_id, terminal, dir
    entryRemoved = Signal(str, str, str)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("Watch")
        self.setStyleSheet(AUTHORING_CHROME_QSS)
        self.resize(620, 380)
        self._entries: list[WatchEntry] = []
        self._build()
        self._timer = QTimer(self)
        self._timer.setInterval(200)   # 5 Hz refresh
        self._timer.timeout.connect(self._refresh_values)

    def showEvent(self, ev):
        super().showEvent(ev)
        # Closing a retained tool hides it; restarting only in __init__ left
        # reopened watches displaying yesterday's value under a live heading.
        self._refresh_values()
        self._timer.start()

    def hideEvent(self, ev):
        self._timer.stop()
        super().hideEvent(ev)

    # ─────────────────────────────────────────────────────────────
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        header = QLabel("Pinned parameters — updated 5×/s")
        header.setStyleSheet(
            f"color: {UI.blue}; font-weight: bold; padding: 4px 8px;"
            f" background: {UI.hover}; border: 1px solid {UI.border};")
        root.addWidget(header)

        self._table = QTableWidget(0, 7)
        self._table.setAccessibleName("Watched parameters")
        self._table.setHorizontalHeaderLabels(
            ["Block", "Terminal", "Dir", "Type", "Value", "Forced", "Actions"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setStyleSheet(
            f"QTableWidget {{ background: white; border: 1px solid {UI.border};"
            " font-family: Consolas; font-size: 9pt;"
            " alternate-background-color: #FAFBFD; }"
            f" QTableWidget::item:hover {{ background: {UI.selection}; }}"
            f" QTableWidget::item:selected {{ background: {UI.blue}; color: white; }}")
        self._table.setAlternatingRowColors(True)
        hh = self._table.horizontalHeader()
        for c in (0, 1, 4):
            hh.setSectionResizeMode(c, QHeaderView.Stretch)
        for c in (2, 3, 5, 6):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        root.addWidget(self._table, 1)

        # Footer actions
        ftr = QHBoxLayout()
        ftr.addStretch(1)
        self._btn_clear = QPushButton("Clear all")
        self._btn_clear.clicked.connect(self.clear)
        ftr.addWidget(self._btn_clear)
        root.addLayout(ftr)

    # ─────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────
    def add(self, block, terminal: str, direction: str) -> bool:
        """Pin a terminal. Returns True if newly added, False if duplicate."""
        direction = direction.upper()
        if direction not in ("IN", "OUT"):
            return False
        e = WatchEntry(block=block, terminal=terminal, direction=direction)
        if any(x.key == e.key for x in self._entries):
            return False
        self._entries.append(e)
        self._rebuild_table()
        self.entryAdded.emit(block.id, terminal, direction)
        return True

    def remove(self, block_id: str, terminal: str, direction: str) -> bool:
        before = len(self._entries)
        self._entries = [e for e in self._entries
                          if e.key != (block_id, terminal, direction)]
        removed = len(self._entries) < before
        if removed:
            self._rebuild_table()
            self.entryRemoved.emit(block_id, terminal, direction)
        return removed

    def clear(self):
        self._entries.clear()
        self._rebuild_table()

    def entries(self) -> list[WatchEntry]:
        return list(self._entries)

    # ─────────────────────────────────────────────────────────────
    def _rebuild_table(self):
        self._table.setRowCount(len(self._entries))
        for r, e in enumerate(self._entries):
            self._table.setItem(r, 0, QTableWidgetItem(e.block.instance_name))
            self._table.setItem(r, 1, QTableWidgetItem(e.terminal))
            self._table.setItem(r, 2, QTableWidgetItem(e.direction))
            self._table.setItem(r, 3, QTableWidgetItem(e.data_type()))
            # Value + Forced filled by _refresh_values()
            # Actions cell
            act_cell = QWidget()
            ah = QHBoxLayout(act_cell)
            ah.setContentsMargins(2, 0, 2, 0)
            ah.setSpacing(2)
            btn_force = QPushButton("Force...")
            btn_force.setAccessibleName(f"Force {e.block.instance_name} {e.terminal}")
            btn_force.setStyleSheet(
                "QPushButton { background: #FAFBFD; color: #B22222;"
                f" border: 1px solid {UI.border}; border-radius: 2px;"
                " padding: 1px 6px; }"
                " QPushButton:hover { background: #FFE7E7; }")
            btn_force.clicked.connect(
                lambda _checked=False, ee=e: self._open_force(ee))
            ah.addWidget(btn_force)
            btn_remove = QPushButton("✕")
            btn_remove.setToolTip("Remove from watch")
            btn_remove.setAccessibleName(f"Remove {e.block.instance_name} {e.terminal} from watch")
            btn_remove.setStyleSheet(
                "QPushButton { background: #FAFBFD; color: #555;"
                f" border: 1px solid {UI.border}; border-radius: 2px;"
                " padding: 1px 6px; font-weight: bold; }"
                f" QPushButton:hover {{ background: {UI.border_light}; }}")
            btn_remove.clicked.connect(
                lambda _checked=False, ee=e: self.remove(
                    ee.block.id, ee.terminal, ee.direction))
            ah.addWidget(btn_remove)
            ah.addStretch(1)
            self._table.setCellWidget(r, 6, act_cell)
        self._refresh_values()

    def _refresh_values(self):
        for r, e in enumerate(self._entries):
            val = e.value()
            item = self._table.item(r, 4)
            if item is None:
                item = QTableWidgetItem()
                self._table.setItem(r, 4, item)
            text = "Unavailable" if val is None else str(val)
            if item.text() != text:
                item.setText(text)
            item.setToolTip("The pinned terminal has no current value." if val is None else "")
            actions = self._table.cellWidget(r, 6)
            if actions:
                actions.findChildren(QPushButton)[0].setEnabled(val is not None)
            forced = e.is_forced()
            f_item = self._table.item(r, 5)
            if f_item is None:
                f_item = QTableWidgetItem()
                self._table.setItem(r, 5, f_item)
            text = "●" if forced else ""
            if f_item.text() != text:
                f_item.setText(text)
            if forced:
                f_item.setForeground(QColor("#C62828"))

    def _open_force(self, e: WatchEntry):
        from ..dialogs.force_value_dialog import ForceValueDialog
        dlg = ForceValueDialog(e.block, parent=self)
        # Pre-select the entry's terminal in the combo
        target = f"  ·  {e.terminal}"
        for i in range(dlg._combo.count()):
            if dlg._combo.itemText(i).endswith(target) or e.terminal in dlg._combo.itemText(i):
                # Check direction too
                d = dlg._combo.itemData(i)
                if d and d[0] == ("in" if e.direction == "IN" else "out") \
                        and d[1] == e.terminal:
                    dlg._combo.setCurrentIndex(i)
                    dlg._on_terminal_pick(i)
                    break
        if not is_headless():
            dlg.exec()


# ───────────────────────────────────────────────────────────────────────
# Singleton helper
# ───────────────────────────────────────────────────────────────────────

def open_watch_panel(parent=None) -> WatchPanel:
    """Return (or create) the global watch-panel instance, raise + focus."""
    inst = getattr(open_watch_panel, "_instance", None)
    if inst is None:
        inst = WatchPanel(parent)
        open_watch_panel._instance = inst
        inst.destroyed.connect(
            lambda *_: setattr(open_watch_panel, "_instance", None))
    inst.show()
    inst.raise_()
    inst.activateWindow()
    return inst


def watch_panel_instance() -> Optional[WatchPanel]:
    """Return the singleton if it exists; None otherwise. Doesn't create."""
    return getattr(open_watch_panel, "_instance", None)
