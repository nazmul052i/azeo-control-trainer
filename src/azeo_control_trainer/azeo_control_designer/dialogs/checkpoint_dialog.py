"""Checkpoint diagnostics with a deliberately narrow restore contract.

Allows users to:
  - Capture named diagnostic snapshots of online controller modules
  - View all saved checkpoints with metadata
  - Replay only the writable configuration recorded in ``restore_writes``
  - Delete old checkpoints
  - Auto-checkpoint on demand

Readbacks, output terminals, strategy data, and scan statistics are retained
for diagnosis; they are never presented as a restorable process image.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFrame, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox as _QtMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout,
)

from azeo_control_trainer.core.strategy.serialization.checkpoint_io import (
    delete_checkpoint, list_checkpoints, load_checkpoint,
    restore_checkpoint, save_checkpoint,
)
from azeo_control_trainer.core.presentation.headless import is_headless

log = logging.getLogger("strategy.checkpoint_dlg")


class _HeadlessSafeMessageBox(_QtMessageBox):
    """Checkpoint notices are silent and confirmations refuse headlessly."""

    @staticmethod
    def information(parent, title, text, *args):
        if is_headless():
            log.info("%s: %s", title, text)
            return _QtMessageBox.Ok
        return _QtMessageBox.information(parent, title, text, *args)

    @staticmethod
    def warning(parent, title, text, *args):
        if is_headless():
            log.warning("%s: %s", title, text)
            return _QtMessageBox.No
        return _QtMessageBox.warning(parent, title, text, *args)

    @staticmethod
    def critical(parent, title, text, *args):
        if is_headless():
            log.error("%s: %s", title, text)
            return _QtMessageBox.Ok
        return _QtMessageBox.critical(parent, title, text, *args)

    @staticmethod
    def question(parent, title, text, *args):
        if is_headless():
            log.info("%s refused headlessly: %s", title, text)
            return _QtMessageBox.No
        return _QtMessageBox.question(parent, title, text, *args)


QMessageBox = _HeadlessSafeMessageBox

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
QGroupBox {{
    font-size: 9pt;
    font-weight: bold;
    color: {UI.blue};
    border: 1px solid {UI.border};
    border-radius: 3px;
    margin-top: 8px;
    padding-top: 14px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
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
QTableWidget::item {{ padding: 3px 6px; }}
QHeaderView::section {{
    background: {UI.chrome};
    color: {UI.blue};
    font-size: 9pt;
    font-weight: bold;
    border: 1px solid {UI.border};
    padding: 4px 6px;
}}
"""

_BTN_STYLE = (
    f"QPushButton {{ background: {UI.chrome}; color: {UI.blue}; "
    f"border: 1px solid {UI.border}; border-radius: 3px; "
    "font-size: 9pt; padding: 5px 16px; font-weight: bold; }"
    f"QPushButton:hover {{ background: {UI.chrome_alt}; }}"
    f"QPushButton:disabled {{ background: {UI.border_light}; color: #999; }}"
)

_BTN_PRIMARY = (
    "QPushButton { background: #1565C0; color: #FFFFFF; "
    "border: 1px solid #0D47A1; border-radius: 3px; "
    "font-size: 9pt; padding: 5px 16px; font-weight: bold; }"
    "QPushButton:hover { background: #1976D2; }"
    "QPushButton:disabled { background: #78909C; color: #CCC; }"
)

_BTN_DANGER = (
    "QPushButton { background: #C62828; color: #FFFFFF; "
    "border: 1px solid #B71C1C; border-radius: 3px; "
    "font-size: 9pt; padding: 5px 16px; font-weight: bold; }"
    "QPushButton:hover { background: #D32F2F; }"
    "QPushButton:disabled { background: #78909C; color: #CCC; }"
)


class CheckpointDialog(QDialog):
    """Dialog for managing strategy state checkpoints."""

    def __init__(self, designer_tab, store, parent=None):
        super().__init__(parent)
        self._designer_tab = designer_tab
        self._store = store
        self.setWindowTitle("Checkpoint Manager")
        self.setMinimumSize(800, 500)
        self.resize(900, 550)
        self.setStyleSheet(_DLG_STYLE)
        self._build_ui()
        self._refresh_list()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Header
        hdr = QFrame()
        hdr.setObjectName("headerBar")
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(8, 4, 8, 4)
        title = QLabel("Checkpoint Manager")
        title.setObjectName("dlgTitle")
        hdr_lay.addWidget(title)
        hdr_lay.addStretch()
        subtitle = QLabel(
            "Capture diagnostics; restore explicit writable configuration")
        subtitle.setObjectName("dlgSubtitle")
        hdr_lay.addWidget(subtitle)
        layout.addWidget(hdr)

        # Save new checkpoint section
        save_grp = QGroupBox("Create Checkpoint")
        save_lay = QHBoxLayout(save_grp)
        save_lay.addWidget(QLabel("Name:"))
        self._txt_name = QLineEdit()
        self._txt_name.setPlaceholderText("Enter checkpoint name (e.g. Pre-tuning, Baseline)")
        self._txt_name.setStyleSheet(
            f"QLineEdit {{ background: #FFF; border: 1px solid {UI.border}; "
            "border-radius: 3px; padding: 4px 8px; font-size: 9pt; }")
        save_lay.addWidget(self._txt_name, 1)

        btn_save = QPushButton("Save Checkpoint")
        btn_save.setStyleSheet(_BTN_PRIMARY)
        btn_save.clicked.connect(self._save_checkpoint)
        save_lay.addWidget(btn_save)

        layout.addWidget(save_grp)

        # Checkpoint list
        list_grp = QGroupBox("Saved Checkpoints")
        list_lay = QVBoxLayout(list_grp)

        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels([
            "Name", "Date/Time", "Type", "Modules", "Status", "Path",
        ])
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(24)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, 6):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.selectionModel().selectionChanged.connect(
            self._on_selection_changed)
        list_lay.addWidget(self._table, 1)

        # Action buttons
        btn_row = QHBoxLayout()
        self._btn_restore = QPushButton("Restore Selected")
        self._btn_restore.setStyleSheet(_BTN_PRIMARY)
        self._btn_restore.setEnabled(False)
        self._btn_restore.clicked.connect(self._restore_checkpoint)
        btn_row.addWidget(self._btn_restore)

        self._btn_view = QPushButton("View Details")
        self._btn_view.setStyleSheet(_BTN_STYLE)
        self._btn_view.setEnabled(False)
        self._btn_view.clicked.connect(self._view_details)
        btn_row.addWidget(self._btn_view)

        btn_row.addStretch()

        self._btn_delete = QPushButton("Delete")
        self._btn_delete.setStyleSheet(_BTN_DANGER)
        self._btn_delete.setEnabled(False)
        self._btn_delete.clicked.connect(self._delete_checkpoint)
        btn_row.addWidget(self._btn_delete)

        btn_refresh = QPushButton("Refresh")
        btn_refresh.setStyleSheet(_BTN_STYLE)
        btn_refresh.clicked.connect(self._refresh_list)
        btn_row.addWidget(btn_refresh)

        btn_close = QPushButton("Close")
        btn_close.setStyleSheet(_BTN_STYLE)
        btn_close.clicked.connect(self.close)
        btn_row.addWidget(btn_close)

        list_lay.addLayout(btn_row)
        layout.addWidget(list_grp, 1)

    def _refresh_list(self):
        """Reload the checkpoint list from disk."""
        checkpoints = list_checkpoints()
        self._table.setRowCount(len(checkpoints))
        for r, cp in enumerate(checkpoints):
            self._table.setItem(r, 0, QTableWidgetItem(cp["name"]))
            self._table.setItem(r, 1, QTableWidgetItem(cp["timestamp"]))
            type_str = "Auto" if cp["auto"] else "Manual"
            item = QTableWidgetItem(type_str)
            if cp["auto"]:
                item.setForeground(QColor("#78909C"))
            else:
                item.setForeground(QColor("#1565C0"))
            self._table.setItem(r, 2, item)
            self._table.setItem(r, 3, QTableWidgetItem(
                str(cp["module_count"])))
            self._table.setItem(r, 4, QTableWidgetItem("OK"))
            self._table.setItem(r, 5, QTableWidgetItem(
                str(cp["path"])))

    def _on_selection_changed(self):
        has_sel = len(self._table.selectionModel().selectedRows()) > 0
        self._btn_restore.setEnabled(has_sel)
        self._btn_view.setEnabled(has_sel)
        self._btn_delete.setEnabled(has_sel)

    def _get_canvases(self) -> list:
        """Gather (tab_name, canvas) tuples from designer."""
        from ..designer_tab import StrategyCanvas
        tab = self._designer_tab
        result = []
        for i in range(tab._canvas_tabs.count()):
            canvas = tab._canvas_tabs.widget(i)
            if isinstance(canvas, StrategyCanvas):
                name = tab._canvas_tabs.tabText(i).replace(" *", "").strip()
                result.append((name, canvas))
        return result

    def _save_checkpoint(self):
        """Save a new checkpoint."""
        name = self._txt_name.text().strip()
        if not name:
            QMessageBox.warning(self, "Checkpoint",
                                "Please enter a checkpoint name.")
            return

        canvases = self._get_canvases()
        online = [(n, c) for n, c in canvases
                  if c.runtime and c.runtime.is_online]
        if not online:
            QMessageBox.warning(self, "Checkpoint",
                                "No modules are online. Cannot save checkpoint.")
            return

        path = save_checkpoint(name, online, self._store, auto=False)
        self._txt_name.clear()
        self._refresh_list()
        QMessageBox.information(
            self, "Checkpoint Saved",
            f"Checkpoint '{name}' saved with {len(online)} module(s).\n"
            f"File: {path.name}")

    def _restore_checkpoint(self):
        """Restore the selected checkpoint."""
        row = self._table.currentRow()
        if row < 0:
            return
        path_item = self._table.item(row, 5)
        name_item = self._table.item(row, 0)
        if not path_item:
            return

        name = name_item.text() if name_item else "checkpoint"
        reply = QMessageBox.question(
            self, "Restore Checkpoint",
            f"Restore checkpoint '{name}'?\n\n"
            "Only the writable configuration explicitly recorded in this\n"
            "checkpoint will be queued. Readbacks and outputs are not restored.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        try:
            data = load_checkpoint(path_item.text())
            results = restore_checkpoint(data, self._store)
            summary = "\n".join(
                f"  {mod}: {count} parameters"
                for mod, count in results.items())
            QMessageBox.information(
                self, "Checkpoint Restored",
                f"Checkpoint '{name}' restored:\n{summary}")
        except Exception as e:
            QMessageBox.critical(
                self, "Restore Failed",
                f"Failed to restore checkpoint: {e}")

    def _view_details(self):
        """Show checkpoint details in a popup."""
        row = self._table.currentRow()
        if row < 0:
            return
        path_item = self._table.item(row, 5)
        if not path_item:
            return

        try:
            data = load_checkpoint(path_item.text())
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load: {e}")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Checkpoint: {data.get('name', '?')}")
        dlg.resize(600, 400)
        dlg.setStyleSheet(_DLG_STYLE)
        lay = QVBoxLayout(dlg)

        browser = QTextBrowser()
        browser.setStyleSheet(
            "QTextBrowser { background: #FFF; font-family: Consolas, monospace; "
            f"font-size: 9pt; border: 1px solid {UI.border}; }}")

        html = f"<h3>{data.get('name', 'Unnamed')}</h3>"
        html += f"<p>Created: {data.get('timestamp', '?')}</p>"
        html += f"<p>Type: {'Auto' if data.get('auto') else 'Manual'}</p>"

        for module in data.get("modules", []):
            html += f"<h4>Module: {module.get('tab_name', '?')}</h4>"
            status = module.get("runtime_status", {})
            html += (f"<p>Scans: {status.get('scan_count', 0)} | "
                     f"Blocks: {status.get('block_count', 0)} | "
                     f"Avg scan: {status.get('avg_scan_ms', 0):.2f} ms</p>")
            html += "<table border='1' cellpadding='3' cellspacing='0'>"
            html += "<tr><th>Block</th><th>Parameter</th><th>Value</th></tr>"
            for iname, params in module.get("parameters", {}).items():
                for key, val in params.items():
                    html += f"<tr><td>{iname}</td><td>{key}</td><td>{val}</td></tr>"
            html += "</table>"

        browser.setHtml(html)
        lay.addWidget(browser, 1)

        btn_close = QPushButton("Close")
        btn_close.setStyleSheet(_BTN_STYLE)
        btn_close.clicked.connect(dlg.close)
        lay.addWidget(btn_close, alignment=Qt.AlignRight)

        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.show()

    def _delete_checkpoint(self):
        """Delete the selected checkpoint."""
        row = self._table.currentRow()
        if row < 0:
            return
        path_item = self._table.item(row, 5)
        name_item = self._table.item(row, 0)
        if not path_item:
            return

        name = name_item.text() if name_item else "checkpoint"
        reply = QMessageBox.question(
            self, "Delete Checkpoint",
            f"Delete checkpoint '{name}'?\nThis cannot be undone.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        delete_checkpoint(path_item.text())
        self._refresh_list()
