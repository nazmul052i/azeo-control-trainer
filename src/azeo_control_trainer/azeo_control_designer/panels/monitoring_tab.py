"""Monitoring Tab — Honeywell-style live view of loaded strategy modules.

Shows a real-time, read-only view of what's actually running in the controller:
  - All online strategy modules with their status
  - Live PV / SP / OUT / Mode for every PID block
  - Block execution status with color coding
  - Alarm states with priority colors
  - Auto-refresh at 1 Hz

Inspired by Honeywell Control Builder's Monitoring Tab which shows the
runtime state of loaded modules independently from the engineering/project view.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI

import logging
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QPushButton, QSizePolicy,
    QSplitter, QTableWidget, QTableWidgetItem, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

log = logging.getLogger("strategy.monitoring")

# ── ISA-101 Silver styling ──

_PANEL_STYLE = f"""
QWidget#monitorPanel {{
    background: {UI.chrome};
}}
QLabel#sectionTitle {{
    color: {UI.blue};
    font-size: 10pt;
    font-weight: bold;
    padding: 4px 0;
}}
QLabel#sectionSubtitle {{
    color: {UI.text_secondary};
    font-size: 9pt;
}}
QFrame#statusBar {{
    background: qlineargradient(y1:0, y2:1, stop:0 {UI.chrome_alt}, stop:1 #B0B8CC);
    border: 1px solid {UI.border};
    border-radius: 2px;
    padding: 4px 8px;
}}
QFrame#statusBar QLabel {{
    color: {UI.blue};
    font-size: 9pt;
    font-weight: bold;
}}
"""

_TABLE_STYLE = f"""
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
    background: qlineargradient(y1:0, y2:1, stop:0 {UI.chrome}, stop:1 {UI.chrome_alt});
    color: {UI.blue};
    font-size: 9pt;
    font-weight: bold;
    border: 1px solid {UI.border};
    padding: 4px 6px;
}}
"""

_TREE_STYLE = f"""
QTreeWidget {{
    background: #FFFFFF;
    color: {UI.text};
    border: 1px solid {UI.border};
    font-size: 9pt;
    outline: none;
}}
QTreeWidget::item {{
    padding: 2px 4px;
    border: none;
}}
QTreeWidget::item:hover {{
    background: {UI.hover};
}}
QTreeWidget::item:selected {{
    background: {UI.blue};
    color: #FFFFFF;
}}
"""

_BTN_STYLE = (
    f"QPushButton {{ background: {UI.chrome}; color: {UI.blue}; "
    f"border: 1px solid {UI.border}; border-radius: 3px; "
    "font-size: 9pt; padding: 4px 12px; font-weight: bold; }"
    f"QPushButton:hover {{ background: {UI.chrome_alt}; }}"
    f"QPushButton:disabled {{ background: {UI.border_light}; color: #999; }}"
)

_SEARCH_STYLE = (
    f"QLineEdit {{ background: #FFFFFF; color: {UI.text}; "
    f"border: 1px solid {UI.border}; border-radius: 3px; "
    "padding: 4px 8px; font-size: 9pt; }"
    f"QLineEdit:focus {{ border-color: {UI.blue}; }}"
)

# ── State colors ──
_COLOR_ONLINE = "#2E7D32"
_COLOR_OFFLINE = "#78909C"
_COLOR_ERROR = "#C62828"
_COLOR_WARN = "#F9A825"

# Mode colors (Azeo convention)
_MODE_COLORS = {
    "AUTO": "#2E7D32",
    "CAS": "#1565C0",
    "RCAS": "#6A1B9A",
    "MAN": "#E65100",
    "IMAN": "#C62828",
    "LO": "#78909C",
    "ROUT": "#4527A0",
}

# Block status colors
_STATUS_COLORS = {
    "GOOD": "#2E7D32",
    "BAD": "#C62828",
    "UNCERTAIN": "#F9A825",
    "OOS": "#78909C",
}


def _make_status_icon(color: str, size: int = 12) -> QIcon:
    """Create a small colored circle icon."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(Qt.NoPen)
    p.drawEllipse(1, 1, size - 2, size - 2)
    p.end()
    return QIcon(pm)


class MonitoringTab(QWidget):
    """Honeywell-style Monitoring Tab for live strategy parameter viewing.

    Shows real-time block parameters for all online strategy modules.
    Read-only — no editing, just monitoring.
    """

    faceplateRequested = Signal(str)  # tag name

    def __init__(self, store=None, parent=None):
        super().__init__(parent)
        self._store = store
        self.setObjectName("monitorPanel")
        self._designer_tab = None  # set externally
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh)
        self._filter_text = ""
        self._show_io_blocks = True
        self._show_pid_blocks = True
        self._show_signal_blocks = True
        self._build_ui()

    def set_designer_tab(self, designer_tab):
        """Connect to the designer tab to access canvases/runtimes."""
        self._designer_tab = designer_tab

    def _build_ui(self):
        self.setStyleSheet(_PANEL_STYLE)
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        # ── Header bar ──
        hdr = QFrame()
        hdr.setObjectName("statusBar")
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(8, 4, 8, 4)

        title = QLabel("MONITORING")
        title.setObjectName("sectionTitle")
        hdr_lay.addWidget(title)

        self._lbl_status = QLabel("No modules online")
        self._lbl_status.setObjectName("sectionSubtitle")
        hdr_lay.addWidget(self._lbl_status)
        hdr_lay.addStretch()

        # Filter
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter blocks...")
        self._search.setClearButtonEnabled(True)
        self._search.setFixedWidth(200)
        self._search.setStyleSheet(_SEARCH_STYLE)
        self._search.textChanged.connect(self._on_filter_changed)
        hdr_lay.addWidget(self._search)

        # Filter checkboxes
        self._chk_io = QCheckBox("I/O")
        self._chk_io.setChecked(True)
        self._chk_io.setStyleSheet(f"color: {UI.blue}; font-size: 9pt;")
        self._chk_io.toggled.connect(lambda c: self._set_filter_flag("io", c))
        hdr_lay.addWidget(self._chk_io)

        self._chk_pid = QCheckBox("PID")
        self._chk_pid.setChecked(True)
        self._chk_pid.setStyleSheet(f"color: {UI.blue}; font-size: 9pt;")
        self._chk_pid.toggled.connect(lambda c: self._set_filter_flag("pid", c))
        hdr_lay.addWidget(self._chk_pid)

        self._chk_signal = QCheckBox("Signal")
        self._chk_signal.setChecked(True)
        self._chk_signal.setStyleSheet(f"color: {UI.blue}; font-size: 9pt;")
        self._chk_signal.toggled.connect(lambda c: self._set_filter_flag("signal", c))
        hdr_lay.addWidget(self._chk_signal)

        # Refresh rate selector
        hdr_lay.addWidget(QLabel("Refresh:"))
        self._cmb_rate = QComboBox()
        self._cmb_rate.addItems(["1s", "2s", "5s", "10s"])
        self._cmb_rate.setCurrentIndex(0)
        self._cmb_rate.setFixedWidth(60)
        self._cmb_rate.setStyleSheet(
            "QComboBox { font-size: 9pt; padding: 2px 4px; }")
        self._cmb_rate.currentTextChanged.connect(self._on_rate_changed)
        hdr_lay.addWidget(self._cmb_rate)

        root.addWidget(hdr)

        # ── Main content: module tree (left) + block table (right) ──
        splitter = QSplitter(Qt.Horizontal)

        # Module tree (left)
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(2)

        left_hdr = QLabel("Online Modules")
        left_hdr.setStyleSheet(
            f"font-size: 9pt; font-weight: bold; color: {UI.blue}; padding: 2px 4px;")
        left_lay.addWidget(left_hdr)

        self._module_tree = QTreeWidget()
        self._module_tree.setHeaderLabels(["Module", "Status", "Scans"])
        self._module_tree.setColumnCount(3)
        self._module_tree.setRootIsDecorated(False)
        self._module_tree.setAlternatingRowColors(True)
        self._module_tree.setStyleSheet(_TREE_STYLE)
        self._module_tree.header().setStretchLastSection(False)
        self._module_tree.header().setSectionResizeMode(
            0, QHeaderView.Stretch)
        self._module_tree.header().setSectionResizeMode(
            1, QHeaderView.ResizeToContents)
        self._module_tree.header().setSectionResizeMode(
            2, QHeaderView.ResizeToContents)
        self._module_tree.currentItemChanged.connect(self._on_module_selected)
        left_lay.addWidget(self._module_tree, 1)

        splitter.addWidget(left)

        # Block detail table (right)
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(2)

        self._lbl_detail = QLabel("Select a module to view parameters")
        self._lbl_detail.setStyleSheet(
            f"font-size: 9pt; font-weight: bold; color: {UI.blue}; padding: 2px 4px;")
        right_lay.addWidget(self._lbl_detail)

        self._block_table = QTableWidget()
        self._block_table.setColumnCount(10)
        self._block_table.setHorizontalHeaderLabels([
            "Block", "Type", "Status", "PV", "SP", "OUT",
            "Mode", "Error", "Tag", "Detail",
        ])
        self._block_table.setAlternatingRowColors(True)
        self._block_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._block_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._block_table.setSortingEnabled(True)
        self._block_table.setStyleSheet(_TABLE_STYLE)
        self._block_table.verticalHeader().setVisible(False)
        self._block_table.verticalHeader().setDefaultSectionSize(22)
        hh = self._block_table.horizontalHeader()
        hh.setStretchLastSection(True)
        for i in range(9):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self._block_table.doubleClicked.connect(self._on_block_double_click)
        right_lay.addWidget(self._block_table, 1)

        # Summary bar
        self._lbl_summary = QLabel("")
        self._lbl_summary.setStyleSheet(
            f"font-size: 9pt; color: {UI.border}; padding: 2px 6px;")
        right_lay.addWidget(self._lbl_summary)

        splitter.addWidget(right)
        splitter.setSizes([250, 700])
        root.addWidget(splitter, 1)

    def _set_filter_flag(self, kind: str, checked: bool):
        if kind == "io":
            self._show_io_blocks = checked
        elif kind == "pid":
            self._show_pid_blocks = checked
        else:
            self._show_signal_blocks = checked
        self._refresh_block_table()

    def _on_filter_changed(self, text: str):
        self._filter_text = text.lower()
        self._refresh_block_table()

    def _on_rate_changed(self, text: str):
        ms = {"1s": 1000, "2s": 2000, "5s": 5000, "10s": 10000}.get(text, 1000)
        if self._refresh_timer.isActive():
            self._refresh_timer.start(ms)

    # ── Lifecycle ──

    def start(self):
        """Start monitoring refresh."""
        rate = {"1s": 1000, "2s": 2000, "5s": 5000, "10s": 10000}.get(
            self._cmb_rate.currentText(), 1000)
        self._refresh_timer.start(rate)
        self._refresh()

    def stop(self):
        """Stop monitoring refresh."""
        self._refresh_timer.stop()

    # ── Refresh logic ──

    def _refresh(self):
        """Refresh the module list and block detail table."""
        if self._designer_tab is None:
            return

        tab = self._designer_tab
        tabs_widget = tab._canvas_tabs

        # Gather online canvases
        online_modules = []
        for i in range(tabs_widget.count()):
            from ..designer_tab import StrategyCanvas
            canvas = tabs_widget.widget(i)
            if not isinstance(canvas, StrategyCanvas):
                continue
            if canvas.runtime and canvas.runtime.is_online:
                name = tabs_widget.tabText(i).replace(" *", "").strip()
                status = canvas.runtime.get_status()
                online_modules.append((i, name, canvas, status))

        # Update module tree
        self._module_tree.blockSignals(True)
        current_row = self._module_tree.currentIndex().row()
        self._module_tree.clear()
        for idx, name, canvas, status in online_modules:
            item = QTreeWidgetItem([
                name,
                "ACTIVE" if status["online"] else "INACTIVE",
                str(status["scan_count"]),
            ])
            item.setData(0, Qt.UserRole, idx)  # canvas tab index
            item.setIcon(0, _make_status_icon(_COLOR_ONLINE))
            if status.get("error_count", 0) > 0:
                item.setForeground(2, QColor(_COLOR_ERROR))
            self._module_tree.addTopLevelItem(item)

        # Restore selection
        if 0 <= current_row < self._module_tree.topLevelItemCount():
            self._module_tree.setCurrentItem(
                self._module_tree.topLevelItem(current_row))
        elif self._module_tree.topLevelItemCount() > 0:
            self._module_tree.setCurrentItem(
                self._module_tree.topLevelItem(0))
        self._module_tree.blockSignals(False)

        # Status label
        n = len(online_modules)
        self._lbl_status.setText(
            f"{n} module{'s' if n != 1 else ''} online"
            if n > 0 else "No modules online")

        # Refresh block table for selected module
        self._refresh_block_table()

    def _on_module_selected(self, current, previous):
        self._refresh_block_table()

    def _refresh_block_table(self):
        """Refresh the block parameter table for the selected module."""
        item = self._module_tree.currentItem()
        if item is None or self._designer_tab is None:
            self._block_table.setRowCount(0)
            self._lbl_detail.setText("Select a module to view parameters")
            self._lbl_summary.setText("")
            return

        tab_idx = item.data(0, Qt.UserRole)
        from ..designer_tab import StrategyCanvas
        canvas = self._designer_tab._canvas_tabs.widget(tab_idx)
        if not isinstance(canvas, StrategyCanvas) or not canvas.runtime:
            return

        name = item.text(0)
        self._lbl_detail.setText(f"Parameters: {name}")

        graph = canvas.runtime.compiled.graph if canvas.runtime.compiled else None
        if graph is None:
            self._block_table.setRowCount(0)
            return

        store = self._store

        # Gather block data
        rows = []
        for block_id in (canvas.runtime.compiled.exec_order
                         if canvas.runtime.compiled else []):
            block = graph.blocks.get(block_id)
            if block is None:
                continue

            bt = block.block_type
            cat = getattr(block, 'category', None)
            cat_name = cat.value if cat else ""

            # Filter by category
            if bt in ("AI", "AO", "DI", "DO") and not self._show_io_blocks:
                continue
            if bt == "PID" and not self._show_pid_blocks:
                continue
            if bt not in ("AI", "AO", "DI", "DO", "PID") and not self._show_signal_blocks:
                continue

            # Filter by text
            if self._filter_text:
                search_str = f"{block.instance_name} {bt} {cat_name}".lower()
                tag = block.config.params.get("tag", "")
                search_str += f" {tag}"
                if self._filter_text not in search_str:
                    continue

            # Read live values from store
            pv = sp = out = mode = tag = detail = ""
            err = ""
            status_str = block.status.value if hasattr(block, 'status') else "GOOD"

            if bt == "PID" and store:
                iname = block.instance_name
                pv = self._fmt(store.get(f"ctrl.{iname}.PV"))
                sp = self._fmt(store.get(f"ctrl.{iname}.SP"))
                out = self._fmt(store.get(f"ctrl.{iname}.OUT"))
                mode_val = store.get(f"ctrl.{iname}.MODE")
                mode = str(mode_val) if mode_val is not None else ""
                tag = block.config.params.get("tag", "")
                # Error info
                block_err = store.get(f"ctrl.{iname}.block_err")
                if isinstance(block_err, dict) and any(block_err.values()):
                    err_items = [k for k, v in block_err.items() if v]
                    err = ", ".join(err_items[:3])
                    status_str = "BAD"
                # Detail: gain/reset
                gain = store.get(f"ctrl.{iname}.GAIN")
                reset = store.get(f"ctrl.{iname}.RESET")
                parts = []
                if gain is not None:
                    parts.append(f"Kp={gain:.3f}")
                if reset is not None:
                    parts.append(f"Ti={reset:.1f}s")
                detail = ", ".join(parts)

            elif bt == "AI" and store:
                tag = block.config.params.get("tag", "")
                if tag:
                    val = store.get(tag)
                    pv = self._fmt(val)
                    io_status = store.get(f"io.{block.instance_name}.status")
                    if io_status:
                        status_str = str(io_status)
                detail = block.config.params.get("eng_units", "")

            elif bt == "AO" and store:
                tag = block.config.params.get("tag", "")
                if tag:
                    val = store.get(tag)
                    out = self._fmt(val)
                    sp_val = store.get(f"io.{block.instance_name}.SP")
                    sp = self._fmt(sp_val)
                mode_val = store.get(f"io.{block.instance_name}.mode")
                mode = str(mode_val) if mode_val else ""
                detail = block.config.params.get("eng_units", "")

            elif bt == "SCALER":
                in_val = block.outputs.get("OUT")
                if in_val:
                    out = self._fmt(in_val.value)
                in_lo = block.config.params.get("in_lo", 0)
                in_hi = block.config.params.get("in_hi", 1)
                out_lo = block.config.params.get("out_lo", 0)
                out_hi = block.config.params.get("out_hi", 100)
                detail = f"[{in_lo},{in_hi}] -> [{out_lo},{out_hi}]"

            else:
                # Generic: show first output value
                for tname, term in block.outputs.items():
                    if tname not in ("BKCAL_OUT", "MODE"):
                        out = self._fmt(term.value)
                        break
                tag = block.config.params.get("tag", "")

            rows.append((block.instance_name, bt, status_str,
                         pv, sp, out, mode, err, tag, detail))

        # Populate table
        self._block_table.setSortingEnabled(False)
        self._block_table.setRowCount(len(rows))
        for r, (bname, btype, bstatus, pv, sp, out, mode, err, tag, detail) in enumerate(rows):
            self._set_cell(r, 0, bname)
            self._set_cell(r, 1, btype)
            self._set_status_cell(r, 2, bstatus)
            self._set_cell(r, 3, pv, align=Qt.AlignRight | Qt.AlignVCenter)
            self._set_cell(r, 4, sp, align=Qt.AlignRight | Qt.AlignVCenter)
            self._set_cell(r, 5, out, align=Qt.AlignRight | Qt.AlignVCenter)
            self._set_mode_cell(r, 6, mode)
            self._set_cell(r, 7, err, fg=_COLOR_ERROR if err else None)
            self._set_cell(r, 8, tag)
            self._set_cell(r, 9, detail)

        self._block_table.setSortingEnabled(True)

        # Summary
        n_pid = sum(1 for r in rows if r[1] == "PID")
        n_io = sum(1 for r in rows if r[1] in ("AI", "AO", "DI", "DO"))
        n_other = len(rows) - n_pid - n_io
        n_err = sum(1 for r in rows if r[7])
        parts = [f"{len(rows)} blocks"]
        if n_pid:
            parts.append(f"{n_pid} PID")
        if n_io:
            parts.append(f"{n_io} I/O")
        if n_other:
            parts.append(f"{n_other} signal/logic")
        if n_err:
            parts.append(f"{n_err} errors")
        self._lbl_summary.setText(" | ".join(parts))

    def _set_cell(self, row, col, text, align=None, fg=None):
        item = QTableWidgetItem(str(text))
        if align:
            item.setTextAlignment(align)
        if fg:
            item.setForeground(QColor(fg))
        self._block_table.setItem(row, col, item)

    def _set_status_cell(self, row, col, status_str):
        item = QTableWidgetItem(status_str)
        item.setTextAlignment(Qt.AlignCenter)
        color = _STATUS_COLORS.get(status_str, "#78909C")
        item.setForeground(QColor("#FFFFFF"))
        item.setBackground(QColor(color))
        font = QFont()
        font.setBold(True)
        font.setPointSize(7)
        item.setFont(font)
        self._block_table.setItem(row, col, item)

    def _set_mode_cell(self, row, col, mode_str):
        item = QTableWidgetItem(mode_str)
        item.setTextAlignment(Qt.AlignCenter)
        color = _MODE_COLORS.get(mode_str, "#78909C")
        item.setForeground(QColor(color))
        font = QFont()
        font.setBold(True)
        font.setPointSize(7)
        item.setFont(font)
        self._block_table.setItem(row, col, item)

    def _on_block_double_click(self, index):
        """Double-click a PID row to open its faceplate."""
        row = index.row()
        type_item = self._block_table.item(row, 1)
        name_item = self._block_table.item(row, 0)
        if type_item and name_item and type_item.text() == "PID":
            self.faceplateRequested.emit(name_item.text())

    @staticmethod
    def _fmt(val) -> str:
        """Format a value for display."""
        if val is None:
            return ""
        if isinstance(val, float):
            if abs(val) < 0.01 and val != 0:
                return f"{val:.4f}"
            return f"{val:.2f}"
        return str(val)
