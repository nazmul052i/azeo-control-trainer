"""ControlOverviewDialog -- multi-controller overview panel.

Shows all online PID controllers discovered from SharedDataStore in a
compact table with Tag, PV, SP, OUT%, Mode, and Alarm columns.
Updated at 1 Hz via QTimer. Double-click a row to open a faceplate.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from azeo_control_trainer.core.presentation.hmi_theme import Colors

if TYPE_CHECKING:
    from azeo_control_trainer.core.datastore.shared_data_store import SharedDataStore

log = logging.getLogger(__name__)

# Mode -> (display text, background color, text color)
_MODE_STYLES: dict[str, tuple[str, str, str]] = {
    "AUTO":    ("AUTO",  Colors.MODE_AUTO,    "#000000"),
    "MANUAL":  ("MAN",   Colors.MODE_MANUAL,  "#FFFFFF"),
    "CASCADE": ("CAS",   Colors.MODE_CASCADE, "#FFFFFF"),
    "RCAS":    ("RCAS",  Colors.MODE_CASCADE, "#FFFFFF"),
    "ROUT":    ("ROUT",  "#009999",           "#FFFFFF"),
    "IMAN":    ("IMAN",  "#003A6B",           "#FFFFFF"),
    "OOS":     ("OOS",   "#8B0000",           "#FFFFFF"),
    "LO":      ("LO",    "#808080",           "#FFFFFF"),
}

# Alarm severity -> (label, background, text)
_ALARM_BADGES: dict[str, tuple[str, str, str]] = {
    "CRITICAL": ("CRIT", Colors.ALARM_CRITICAL, "#FFFFFF"),
    "HIGH":     ("HIGH", Colors.ALARM_HIGH,     "#FFFFFF"),
    "WARN":     ("WARN", Colors.ALARM_MEDIUM,   "#000000"),
    "OK":       ("OK",   Colors.STATE_RUNNING,  "#FFFFFF"),
}


class ControlOverviewDialog(QDialog):
    """Compact table overview of all online PID controllers.

    Columns: Tag | PV | SP | OUT% | Mode | Alarm
    Rows auto-discovered from ``ctrl.*.PV`` keys in the SharedDataStore.

    Signals:
        faceplate_requested(str): emitted with the tag when a row is
            double-clicked, so the caller can open a faceplate.
    """

    faceplate_requested = Signal(str)

    def __init__(self, store: SharedDataStore, parent=None) -> None:
        super().__init__(parent)
        self._store = store
        self._tags: list[str] = []

        self.setWindowTitle("Control Overview")
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowStaysOnTopHint
            | Qt.WindowMinMaxButtonsHint
        )
        self.setMinimumSize(560, 300)
        self.resize(640, 420)

        self._build_ui()

        # 1 Hz refresh timer
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

        # Initial refresh
        self._refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        # Title
        title = QLabel("PID Controller Overview")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            f"font-size: 11pt; font-weight: bold; color: {Colors.TEXT_PRIMARY};"
        )
        root.addWidget(title)

        # Table
        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(
            ["Tag", "PV", "SP", "OUT%", "Mode", "Alarm"])
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setSelectionMode(QTableWidget.SingleSelection)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setStyleSheet(
            f"QTableWidget {{ gridline-color: {Colors.PLOT_GRID_MAJOR}; "
            f"font-family: Consolas; font-size: 9pt; }}"
            f"QTableWidget::item {{ padding: 2px 6px; }}"
        )

        header = self._table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for col in range(1, 4):
            header.setSectionResizeMode(col, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)

        self._table.doubleClicked.connect(self._on_double_click)
        root.addWidget(self._table, 1)

        # Footer
        footer = QHBoxLayout()
        footer.addStretch()
        btn_close = QPushButton("Close")
        btn_close.setFixedSize(80, 28)
        btn_close.clicked.connect(self.close)
        footer.addWidget(btn_close)
        root.addLayout(footer)

    # ------------------------------------------------------------------ refresh

    def _discover_tags(self, data: dict) -> list[str]:
        """Find all PID controller tags from ``ctrl.*.PV`` keys."""
        pattern = re.compile(r'^ctrl\.(.+?)\.PV$')
        tags = []
        for key in data:
            m = pattern.match(key)
            if m:
                tags.append(m.group(1))
        tags.sort()
        return tags

    def _refresh(self) -> None:
        """Refresh the table from current store data."""
        if self._store is None:
            return
        data = self._store.get_all()

        tags = self._discover_tags(data)

        # If the tag set has changed, rebuild the table rows
        if tags != self._tags:
            self._tags = tags
            self._table.setRowCount(len(tags))
            for row, tag in enumerate(tags):
                item = QTableWidgetItem(tag)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self._table.setItem(row, 0, item)

        # Update values for each row
        for row, tag in enumerate(self._tags):
            pv = data.get(f'ctrl.{tag}.PV', 0.0)
            sp = data.get(f'ctrl.{tag}.SP', 0.0)
            out_raw = data.get(f'ctrl.{tag}.OUT', 0.0)
            out_pct = float(out_raw) if out_raw is not None else 0.0
            mode_str = str(data.get(f'ctrl.{tag}.Mode', 'AUTO'))

            # PV
            pv_item = QTableWidgetItem(f"{float(pv or 0):.2f}")
            pv_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._table.setItem(row, 1, pv_item)

            # SP
            sp_item = QTableWidgetItem(f"{float(sp or 0):.2f}")
            sp_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._table.setItem(row, 2, sp_item)

            # OUT%
            out_item = QTableWidgetItem(f"{out_pct:.1f}")
            out_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._table.setItem(row, 3, out_item)

            # Mode (color-coded)
            mode_info = _MODE_STYLES.get(mode_str, ("???", "#808080", "#FFFFFF"))
            mode_item = QTableWidgetItem(mode_info[0])
            mode_item.setTextAlignment(Qt.AlignCenter)
            mode_item.setBackground(QColor(mode_info[1]))
            mode_item.setForeground(QColor(mode_info[2]))
            self._table.setItem(row, 4, mode_item)

            # Alarm severity
            alarm_sev = self._compute_alarm_severity(data, tag)
            alm_info = _ALARM_BADGES.get(alarm_sev, ("OK", Colors.STATE_RUNNING, "#FFFFFF"))
            alm_item = QTableWidgetItem(alm_info[0])
            alm_item.setTextAlignment(Qt.AlignCenter)
            alm_item.setBackground(QColor(alm_info[1]))
            alm_item.setForeground(QColor(alm_info[2]))
            self._table.setItem(row, 5, alm_item)

    def _compute_alarm_severity(self, data: dict, tag: str) -> str:
        """Determine the highest active alarm severity for a controller."""
        alarm_dict = data.get(f'ctrl.{tag}.alarm_state')
        if isinstance(alarm_dict, dict):
            if alarm_dict.get('hi_hi_act') or alarm_dict.get('lo_lo_act'):
                return "CRITICAL"
            if (alarm_dict.get('hi_act') or alarm_dict.get('lo_act')
                    or alarm_dict.get('dv_hi_act') or alarm_dict.get('dv_lo_act')):
                return "WARN"

        # Check block errors
        err_dict = data.get(f'ctrl.{tag}.block_err')
        if isinstance(err_dict, dict) and err_dict.get('any_active'):
            return "CRITICAL"

        return "OK"

    # ------------------------------------------------------------------ events

    def _on_double_click(self, index) -> None:
        """Emit faceplate_requested when a row is double-clicked."""
        row = index.row()
        if 0 <= row < len(self._tags):
            self.faceplate_requested.emit(self._tags[row])

    def closeEvent(self, event) -> None:
        self._timer.stop()
        super().closeEvent(event)
