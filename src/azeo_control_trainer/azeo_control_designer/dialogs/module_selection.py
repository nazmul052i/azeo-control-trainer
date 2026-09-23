"""Download Module dialog — Strategy module download to controller.

Mimics the ISA 'Download' workflow: select modules, compile, and
download to the controller (i.e. bring online).
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

log = logging.getLogger("strategy.download")

# ── ISA-101 Silver styling ──

_DLG_STYLE = f"""
QDialog {{
    background: {UI.chrome};
}}
QLabel#dlgTitle {{
    color: {UI.blue};
    font-size: 11pt;
    font-weight: bold;
    padding: 4px 0;
}}
QLabel#dlgSubtitle {{
    color: {UI.text_secondary};
    font-size: 9pt;
    padding: 0 0 6px 0;
}}
QFrame#headerBar {{
    background: {UI.chrome};
    border: 1px solid {UI.border};
    border-radius: 2px;
    padding: 4px 8px;
}}
QFrame#headerBar QLabel {{
    color: {UI.blue};
    font-size: 9pt;
    font-weight: bold;
}}
QTableWidget {{
    background: #FFFFFF;
    alternate-background-color: {UI.pane};
    color: {UI.text};
    font-size: 9pt;
    border: 1px solid {UI.border};
    gridline-color: {UI.border_light};
    selection-background-color: {UI.selection};
    selection-color: {UI.blue};
}}
QTableWidget::item {{
    padding: 3px 6px;
}}
QHeaderView::section {{
    background: {UI.chrome};
    color: {UI.blue};
    font-size: 9pt;
    font-weight: bold;
    border: 1px solid {UI.border};
    padding: 4px 8px;
}}
QPushButton#btnDownload {{
    background: qlineargradient(y1:0, y2:1, stop:0 #4A8BC2, stop:1 #2E6BA4);
    color: #FFFFFF;
    font-size: 9pt;
    font-weight: bold;
    border: 1px solid #1A4F7A;
    border-radius: 3px;
    padding: 6px 20px;
    min-width: 90px;
}}
QPushButton#btnDownload:hover {{
    background: qlineargradient(y1:0, y2:1, stop:0 #5A9BD2, stop:1 #3E7BB4);
}}
QPushButton#btnDownload:pressed {{
    background: #1A4F7A;
}}
QPushButton#btnDownload:disabled {{
    background: #B0B8CC;
    color: #808899;
    border-color: #A0A8BC;
}}
QPushButton#btnCancel {{
    background: qlineargradient(y1:0, y2:1, stop:0 #F0F0F4, stop:1 {UI.chrome_alt});
    color: #37474F;
    font-size: 9pt;
    border: 1px solid {UI.border};
    border-radius: 3px;
    padding: 6px 20px;
    min-width: 70px;
}}
QPushButton#btnCancel:hover {{
    background: qlineargradient(y1:0, y2:1, stop:0 #FFFFFF, stop:1 #E8EAF0);
}}
QPushButton#btnSelectAll, QPushButton#btnSelectNone {{
    background: transparent;
    color: #2E6BA4;
    font-size: 9pt;
    border: none;
    padding: 2px 6px;
    text-decoration: underline;
}}
QPushButton#btnSelectAll:hover, QPushButton#btnSelectNone:hover {{
    color: #1A4F7A;
}}
QCheckBox {{
    spacing: 0px;
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
}}
"""

# Module states
_STATE_ACTIVE = "Active"
_STATE_INACTIVE = "Inactive"
_STATE_NOT_DOWNLOADED = "Not Downloaded"

_STATE_COLORS = {
    _STATE_ACTIVE: "#2E7D32",       # green
    _STATE_INACTIVE: "#78909C",     # blue-grey
    _STATE_NOT_DOWNLOADED: "#9E9E9E",  # grey
}


def _status_icon(color_hex: str, size: int = 12) -> QIcon:
    """Create a small colored circle icon for module status."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(color_hex))
    p.setPen(QColor(color_hex).darker(130))
    p.drawEllipse(1, 1, size - 2, size - 2)
    p.end()
    return QIcon(pm)


class DownloadDialog(QDialog):
    """Strategy Download dialog for selecting modules to compile
    and download (bring online) to the controller."""

    impactRequested = Signal()

    # Column indices
    COL_SELECT = 0
    COL_MODULE = 1
    COL_STATE = 2
    COL_CONTROLLER = 3
    COL_BLOCKS = 4
    COL_RISK = 5
    COL_IMPACT = 6

    def __init__(self, canvas_info: list[dict], parent=None):
        """
        Parameters
        ----------
        canvas_info : list[dict]
            Each dict has keys:
            - ``index`` (int): tab index
            - ``label`` (str): tab label / strategy name
            - ``online`` (bool): whether this canvas is already online
            - ``block_count`` (int): number of blocks in the strategy
        """
        super().__init__(parent)
        self.setWindowTitle("Download")
        self.setMinimumSize(520, 360)
        self.resize(580, 420)
        self.setStyleSheet(_DLG_STYLE)

        self._canvas_info = canvas_info
        self._checkboxes: list[tuple[int, QCheckBox]] = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # ── Title bar ──
        title = QLabel("Download Assignment")
        title.setObjectName("dlgTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            "Select the control modules to compile and download to the "
            "controller. Active modules will continue running."
        )
        subtitle.setObjectName("dlgSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        # ── Header info bar ──
        hbar = QFrame()
        hbar.setObjectName("headerBar")
        hbar_layout = QHBoxLayout(hbar)
        hbar_layout.setContentsMargins(8, 4, 8, 4)
        hbar_layout.setSpacing(20)

        n_total = len(self._canvas_info)
        n_active = sum(1 for c in self._canvas_info if c.get("online"))
        hbar_layout.addWidget(QLabel(f"Modules: {n_total}"))
        hbar_layout.addWidget(QLabel(f"Active: {n_active}"))
        hbar_layout.addWidget(
            QLabel(f"Inactive: {n_total - n_active}"))
        hbar_layout.addStretch()
        layout.addWidget(hbar)

        # ── Select All / None links ──
        sel_row = QHBoxLayout()
        sel_row.setSpacing(4)
        btn_all = QPushButton("Select All")
        btn_all.setObjectName("btnSelectAll")
        btn_all.setCursor(Qt.PointingHandCursor)
        btn_all.clicked.connect(self._select_all)
        sel_row.addWidget(btn_all)
        sel_row.addWidget(QLabel("|"))
        btn_none = QPushButton("Select None")
        btn_none.setObjectName("btnSelectNone")
        btn_none.setCursor(Qt.PointingHandCursor)
        btn_none.clicked.connect(self._select_none)
        sel_row.addWidget(btn_none)
        sel_row.addStretch()
        layout.addLayout(sel_row)

        # ── Module table ──
        self._table = QTableWidget()
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels([
            "", "Module", "State", "Controller", "Blocks", "Risk", "Impact"
        ])
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.NoSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(True)

        # Column sizing
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(self.COL_SELECT, QHeaderView.Fixed)
        hdr.setSectionResizeMode(self.COL_MODULE, QHeaderView.Stretch)
        hdr.setSectionResizeMode(self.COL_STATE, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(self.COL_CONTROLLER, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(self.COL_BLOCKS, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(self.COL_RISK, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(self.COL_IMPACT, QHeaderView.Stretch)
        self._table.setColumnWidth(self.COL_SELECT, 30)

        self._populate_table()
        layout.addWidget(self._table, 1)

        # ── Button row ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()

        btn_impact = QPushButton("Review Impact...")
        btn_impact.setObjectName("btnCancel")
        btn_impact.clicked.connect(self.impactRequested.emit)
        btn_row.addWidget(btn_impact)

        self._btn_download = QPushButton("Download")
        self._btn_download.setObjectName("btnDownload")
        self._btn_download.clicked.connect(self.accept)
        btn_row.addWidget(self._btn_download)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.setObjectName("btnCancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        layout.addLayout(btn_row)

    def _populate_table(self):
        self._table.setRowCount(len(self._canvas_info))
        for row, info in enumerate(self._canvas_info):
            is_online = info.get("online", False)
            block_count = info.get("block_count", 0)

            # Checkbox
            cb = QCheckBox()
            cb.setChecked(not is_online)  # pre-check inactive modules
            cb_widget = QWidget()
            cb_layout = QHBoxLayout(cb_widget)
            cb_layout.setContentsMargins(0, 0, 0, 0)
            cb_layout.setAlignment(Qt.AlignCenter)
            cb_layout.addWidget(cb)
            self._table.setCellWidget(row, self.COL_SELECT, cb_widget)
            self._checkboxes.append((info["index"], cb))

            # Module name
            name_item = QTableWidgetItem(info["label"])
            name_item.setFlags(Qt.ItemIsEnabled)
            name_font = QFont()
            name_font.setPointSize(8)
            name_font.setBold(True)
            name_item.setFont(name_font)
            self._table.setItem(row, self.COL_MODULE, name_item)

            # State with colored icon
            if is_online:
                state = _STATE_ACTIVE
            else:
                state = _STATE_NOT_DOWNLOADED
            state_item = QTableWidgetItem(state)
            state_item.setFlags(Qt.ItemIsEnabled)
            state_item.setIcon(_status_icon(_STATE_COLORS[state]))
            state_item.setForeground(QColor(_STATE_COLORS[state]))
            state_font = QFont()
            state_font.setPointSize(8)
            state_item.setFont(state_font)
            self._table.setItem(row, self.COL_STATE, state_item)

            # Controller (simulated — always CTRL1)
            ctrl_item = QTableWidgetItem("CTRL1")
            ctrl_item.setFlags(Qt.ItemIsEnabled)
            ctrl_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row, self.COL_CONTROLLER, ctrl_item)

            # Block count
            bc_item = QTableWidgetItem(str(block_count))
            bc_item.setFlags(Qt.ItemIsEnabled)
            bc_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row, self.COL_BLOCKS, bc_item)

            risk = str(info.get("risk") or "NONE")
            risk_item = QTableWidgetItem(risk)
            risk_item.setFlags(Qt.ItemIsEnabled)
            risk_item.setTextAlignment(Qt.AlignCenter)
            risk_item.setForeground(QColor({
                "HIGH": "#B71C1C", "MEDIUM": "#A65A00",
                "LOW": "#5B677A", "NONE": "#2E7D32",
            }.get(risk, "#5B677A")))
            self._table.setItem(row, self.COL_RISK, risk_item)

            impact_item = QTableWidgetItem(str(
                info.get("impact") or "No baseline differences"))
            impact_item.setFlags(Qt.ItemIsEnabled)
            impact_item.setToolTip(impact_item.text())
            self._table.setItem(row, self.COL_IMPACT, impact_item)

        self._table.resizeRowsToContents()

    def _select_all(self):
        for _, cb in self._checkboxes:
            cb.setChecked(True)

    def _select_none(self):
        for _, cb in self._checkboxes:
            cb.setChecked(False)

    def selected_indices(self) -> list[int]:
        """Return the tab indices that were checked for download."""
        return [idx for idx, cb in self._checkboxes if cb.isChecked()]


# Backward-compatible alias
ModuleSelectionDialog = DownloadDialog
