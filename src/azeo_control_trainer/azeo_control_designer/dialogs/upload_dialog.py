"""Load/Upload Dialog — Honeywell-style bidirectional sync.

Implements two workflows:
  - Load (Project -> Controller): Save strategy, compile, download to runtime
  - Upload (Controller -> Project): Capture running state back to project file

Honeywell Control Builder distinguishes these as separate operations.
Our implementation captures runtime parameters (tuning, SP, mode) that may
have been changed during online operation and writes them back to the
strategy JSON file.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI

import logging
from pathlib import Path
from azeo_control_trainer.core.strategy.serialization.tuning import (
    ParameterChange, values_differ, parameter_category, runtime_config_snapshot,
    matching_block, apply_parameter_changes, save_parameter_changes, effective_configuration,
)

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QFrame, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QMessageBox as _QtMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from azeo_control_trainer.core.presentation.headless import is_headless

log = logging.getLogger("strategy.upload")


class _HeadlessSafeMessageBox(_QtMessageBox):
    """Upload status must never open a native modal in offscreen runs."""

    @staticmethod
    def information(parent, title, text, *args):
        if is_headless():
            log.info("%s: %s", title, text)
            return _QtMessageBox.Ok
        return _QtMessageBox.information(parent, title, text, *args)

    @staticmethod
    def critical(parent, title, text, *args):
        if is_headless():
            log.error("%s: %s", title, text)
            return _QtMessageBox.Ok
        return _QtMessageBox.critical(parent, title, text, *args)


QMessageBox = _HeadlessSafeMessageBox


def apply_changes_to_open_canvas(designer_tab, path: Path | str,
                                 changes: list[ParameterChange]) -> int:
    """Synchronize a successful upload into its matching open document."""
    target = Path(path).resolve()
    tabs = getattr(designer_tab, "_canvas_tabs", None)
    if tabs is None:
        return 0
    canvas = next((
        tabs.widget(index)
        for index in range(tabs.count())
        if getattr(tabs.widget(index), "file_path", None)
        and Path(tabs.widget(index).file_path).resolve() == target
    ), None)
    if canvas is None:
        return 0

    # Deliberately do not emit strategyModified or reset document/controller
    # flags: Upload already persisted these values, while unrelated unsaved
    # edits and the last-download relationship remain exactly as they were.
    updated = apply_parameter_changes(canvas.scene.graph, changes)
    for change in changes:
        block = matching_block(
            canvas.scene.graph, change.block_id,
            change.instance_name, change.block_type)
        item = (canvas.scene.get_block_item(block.id)
                if block is not None else None)
        if item is not None:
            item.refresh()
    return updated


def save_and_sync_parameter_changes(designer_tab, path: Path | str,
                                    changes: list[ParameterChange]) -> int:
    """Persist an upload, then keep an open editor from reverting it."""
    updated = save_parameter_changes(path, changes)
    if updated:
        apply_changes_to_open_canvas(designer_tab, path, changes)
    return updated

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
    margin-top: 10px;
    padding-top: 16px;
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

_BTN_UPLOAD = (
    "QPushButton { background: #E65100; color: #FFFFFF; "
    "border: 1px solid #BF360C; border-radius: 3px; "
    "font-size: 9pt; padding: 5px 16px; font-weight: bold; }"
    "QPushButton:hover { background: #EF6C00; }"
    "QPushButton:disabled { background: #78909C; color: #CCC; }"
)


class UploadDialog(QDialog):
    """Upload (Controller -> Project) dialog.

    Captures the running state of online strategies and writes the
    modified parameters back to the project file.
    """

    def __init__(self, designer_tab, store, parent=None):
        super().__init__(parent)
        self._designer_tab = designer_tab
        self._store = store
        self.setWindowTitle("Upload — Controller to Project")
        self.setMinimumSize(800, 500)
        self.resize(900, 600)
        self.setStyleSheet(_DLG_STYLE)
        self._build_ui()
        self._scan_changes()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Header
        hdr = QFrame()
        hdr.setObjectName("headerBar")
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(8, 4, 8, 4)
        title = QLabel("Upload Controller State to Project")
        title.setObjectName("dlgTitle")
        hdr_lay.addWidget(title)
        hdr_lay.addStretch()
        layout.addWidget(hdr)

        # Explanation
        info = QLabel(
            "This captures runtime parameter changes (tuning, setpoints, modes) "
            "made while online and writes them back to the strategy file on disk.\n"
            "Select which parameters to upload.")
        info.setWordWrap(True)
        info.setStyleSheet(f"font-size: 9pt; color: {UI.text_secondary}; padding: 4px 8px;")
        layout.addWidget(info)

        # Options
        opt_grp = QGroupBox("Upload Options")
        opt_lay = QHBoxLayout(opt_grp)
        self._chk_tuning = QCheckBox("PID Tuning (Kp, Ti, Td)")
        self._chk_tuning.setChecked(True)
        self._chk_tuning.setStyleSheet(f"font-size: 9pt; color: {UI.blue};")
        opt_lay.addWidget(self._chk_tuning)

        self._chk_sp = QCheckBox("Setpoints")
        self._chk_sp.setChecked(True)
        self._chk_sp.setStyleSheet(f"font-size: 9pt; color: {UI.blue};")
        opt_lay.addWidget(self._chk_sp)

        self._chk_mode = QCheckBox("Controller Modes")
        self._chk_mode.setChecked(False)
        self._chk_mode.setStyleSheet(f"font-size: 9pt; color: {UI.blue};")
        opt_lay.addWidget(self._chk_mode)

        self._chk_limits = QCheckBox("Output/SP Limits")
        self._chk_limits.setChecked(False)
        self._chk_limits.setStyleSheet(f"font-size: 9pt; color: {UI.blue};")
        opt_lay.addWidget(self._chk_limits)

        self._chk_configuration = QCheckBox("Other block configuration")
        self._chk_configuration.setChecked(True)
        self._chk_configuration.setStyleSheet(
            f"font-size: 9pt; color: {UI.blue};")
        opt_lay.addWidget(self._chk_configuration)

        opt_lay.addStretch()
        layout.addWidget(opt_grp)

        # Changes table
        changes_grp = QGroupBox("Detected Changes")
        changes_lay = QVBoxLayout(changes_grp)

        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels([
            "Module", "Block", "Parameter", "File Value",
            "Runtime Value", "Include",
        ])
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(24)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.Interactive)
        hh.setSectionResizeMode(4, QHeaderView.Interactive)
        hh.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        hh.resizeSection(3, 110)
        hh.resizeSection(4, 110)
        changes_lay.addWidget(self._table, 1)

        self._lbl_summary = QLabel("")
        self._lbl_summary.setStyleSheet(
            f"font-size: 9pt; color: {UI.text_secondary}; padding: 2px 6px;")
        changes_lay.addWidget(self._lbl_summary)

        layout.addWidget(changes_grp, 1)

        # Buttons
        btn_row = QHBoxLayout()

        btn_sel_all = QPushButton("Select All")
        btn_sel_all.setStyleSheet(_BTN_STYLE)
        btn_sel_all.clicked.connect(self._select_all)
        btn_row.addWidget(btn_sel_all)

        btn_sel_none = QPushButton("Select None")
        btn_sel_none.setStyleSheet(_BTN_STYLE)
        btn_sel_none.clicked.connect(self._select_none)
        btn_row.addWidget(btn_sel_none)

        btn_rescan = QPushButton("Rescan")
        btn_rescan.setStyleSheet(_BTN_STYLE)
        btn_rescan.clicked.connect(self._scan_changes)
        btn_row.addWidget(btn_rescan)

        btn_row.addStretch()

        self._btn_upload = QPushButton("Upload Selected")
        self._btn_upload.setStyleSheet(_BTN_UPLOAD)
        self._btn_upload.clicked.connect(self._do_upload)
        btn_row.addWidget(self._btn_upload)

        btn_close = QPushButton("Cancel")
        btn_close.setStyleSheet(_BTN_STYLE)
        btn_close.clicked.connect(self.close)
        btn_row.addWidget(btn_close)

        layout.addLayout(btn_row)

    def _scan_changes(self):
        """Scan all online modules for parameter differences vs file."""
        self._changes: list[ParameterChange] = []
        tab = self._designer_tab
        if not tab:
            return

        for i in range(tab._canvas_tabs.count()):
            canvas = tab._canvas_tabs.widget(i)
            runtime = getattr(canvas, "runtime", None)
            if runtime is None or not runtime.is_online:
                continue
            file_path = getattr(canvas, "file_path", None)
            if not file_path:
                continue

            module_name = tab._canvas_tabs.tabText(i).replace(" *", "").strip()
            runtime_graph = (runtime.compiled.graph
                             if runtime.compiled else None)
            if runtime_graph is None:
                continue

            try:
                from azeo_control_trainer.core.strategy.serialization.strategy_io import (
                    load_strategy,
                )
                file_graph, _comments = load_strategy(
                    file_path, remember=False)
            except (OSError, ValueError, KeyError) as exc:
                log.warning("Cannot inspect %s for upload: %s", file_path, exc)
                continue

            for block in runtime_graph.blocks.values():
                file_block = matching_block(
                    file_graph, block.id, block.instance_name,
                    block.block_type)
                if file_block is None:
                    continue
                file_config = effective_configuration(file_block)
                runtime_config = runtime_config_snapshot(block, self._store)
                for key in block.get_config_schema():
                    if key not in file_config or key not in runtime_config:
                        continue
                    file_value = file_config[key]
                    runtime_value = runtime_config[key]
                    if values_differ(file_value, runtime_value):
                        self._changes.append(ParameterChange(
                            module=module_name,
                            file_path=Path(file_path),
                            block_id=block.id,
                            instance_name=block.instance_name,
                            block_type=block.block_type,
                            parameter=key,
                            file_value=file_value,
                            runtime_value=runtime_value,
                            category=parameter_category(key),
                        ))

        self._populate_table()

    def _values_differ(self, a, b) -> bool:
        """Compare two values with tolerance for floats."""
        return values_differ(a, b)

    def _populate_table(self):
        """Fill the table with detected changes."""
        self._table.setRowCount(len(self._changes))
        for r, change in enumerate(self._changes):
            self._table.setItem(r, 0, QTableWidgetItem(change.module))
            self._table.setItem(r, 1, QTableWidgetItem(change.instance_name))
            self._table.setItem(r, 2, QTableWidgetItem(change.parameter))
            self._table.setItem(
                r, 3, QTableWidgetItem(self._fmt(change.file_value)))
            self._table.setItem(
                r, 4, QTableWidgetItem(self._fmt(change.runtime_value)))

            # Checkbox in last column
            chk = QTableWidgetItem()
            chk.setFlags(chk.flags() | Qt.ItemIsUserCheckable)
            chk.setCheckState(Qt.Checked)
            chk.setData(Qt.UserRole, change.category)
            self._table.setItem(r, 5, chk)

            # Color rows
            for c in range(5):
                item = self._table.item(r, c)
                if item:
                    item.setBackground(QColor("#FFEBEE"))

        n = len(self._changes)
        self._lbl_summary.setText(
            f"{n} parameter change{'s' if n != 1 else ''} detected"
            if n > 0 else "No changes detected — file matches runtime")

    def _select_all(self):
        for r in range(self._table.rowCount()):
            item = self._table.item(r, 5)
            if item:
                item.setCheckState(Qt.Checked)

    def _select_none(self):
        for r in range(self._table.rowCount()):
            item = self._table.item(r, 5)
            if item:
                item.setCheckState(Qt.Unchecked)

    def _do_upload(self):
        """Write selected runtime values back to strategy files."""
        file_updates: dict[Path, list[ParameterChange]] = {}

        for r in range(self._table.rowCount()):
            chk = self._table.item(r, 5)
            if not chk or chk.checkState() != Qt.Checked:
                continue
            category = chk.data(Qt.UserRole)

            # Filter by options
            if category == "tuning" and not self._chk_tuning.isChecked():
                continue
            if category == "sp" and not self._chk_sp.isChecked():
                continue
            if category == "mode" and not self._chk_mode.isChecked():
                continue
            if category == "limits" and not self._chk_limits.isChecked():
                continue
            if (category == "configuration"
                    and not self._chk_configuration.isChecked()):
                continue

            change = self._changes[r]
            file_updates.setdefault(change.file_path, []).append(change)

        if not file_updates:
            QMessageBox.information(
                self, "Upload", "No parameters selected for upload.")
            return

        # Apply updates to files
        total_updated = 0
        for file_path, updates in file_updates.items():
            try:
                count = save_and_sync_parameter_changes(
                    self._designer_tab, file_path, updates)
                total_updated += count
                log.info("Uploaded %d parameters to %s",
                         count, file_path)
            except Exception as e:
                log.exception("Upload failed for %s", file_path)
                QMessageBox.critical(
                    self, "Upload Error",
                    f"Failed to update {file_path}:\n{e}")
                return

        QMessageBox.information(
            self, "Upload Complete",
            f"Updated {total_updated} parameter(s) in "
            f"{len(file_updates)} file(s).\n"
            "The strategy file(s) now reflect the runtime state.")
        self.accept()

    @staticmethod
    def _fmt(val) -> str:
        if val is None:
            return "<none>"
        if isinstance(val, float):
            return f"{val:.4f}"
        return str(val)
