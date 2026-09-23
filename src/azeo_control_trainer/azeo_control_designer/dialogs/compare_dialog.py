"""Compare Parameters Dialog — Honeywell-style project vs runtime diff.

Shows a side-by-side comparison of:
  - Left: parameter values saved in the project strategy JSON file
  - Right: current live runtime values from SharedDataStore
  - Color-coded differences (red = mismatch, green = match)
  - Accept (apply file value to runtime) or Revert (update file from runtime)

Inspired by Honeywell Control Builder's "Compare Parameters" function.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI

import logging
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)
from .upload_dialog import (
    ParameterChange, matching_block, runtime_config_snapshot,
    save_parameter_changes, values_differ,
)

log = logging.getLogger("strategy.compare")


@dataclass(frozen=True)
class ParameterDiff:
    change: ParameterChange
    status: str
    file_present: bool = True


def apply_project_values_to_runtime(graph, diffs: list[ParameterDiff],
                                    store=None) -> int:
    """Apply project-side mismatches to any configurable runtime block.

    The operation rolls every touched block back if one block rejects its
    configuration. PID setpoint/mode also use their live write-back channel;
    all other parameters are applied through the block model itself.
    """
    originals: dict[str, dict] = {}
    count = 0
    try:
        for diff in diffs:
            change = diff.change
            if diff.status != "MISMATCH" or not diff.file_present:
                continue
            block = matching_block(
                graph, change.block_id, change.instance_name,
                change.block_type)
            if block is None:
                continue
            originals.setdefault(block.id, dict(block.config.params))
            block.config.params[change.parameter] = change.file_value
            block.normalize_config()
            block._apply_config()
            if store is not None and block.block_type == "PID":
                dynamic = {"sp_init": "SP", "mode": "Mode"}.get(
                    change.parameter)
                if dynamic:
                    store.queue_write(
                        f"ctrl.{block.instance_name}.wb.{dynamic}",
                        change.file_value)
            count += 1
    except Exception:
        for block_id, config in originals.items():
            block = graph.blocks.get(block_id)
            if block is not None:
                block.config.params = config
                block._apply_config()
        raise
    return count

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
QFrame#summaryBar {{
    background: {UI.pane};
    border: 1px solid {UI.border};
    border-radius: 2px;
    padding: 6px 10px;
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

_BTN_ACCEPT = (
    "QPushButton { background: #2E7D32; color: #FFFFFF; "
    "border: 1px solid #1B5E20; border-radius: 3px; "
    "font-size: 9pt; padding: 5px 16px; font-weight: bold; }"
    "QPushButton:hover { background: #388E3C; }"
    "QPushButton:disabled { background: #78909C; color: #CCC; }"
)

_BTN_REVERT = (
    "QPushButton { background: #E65100; color: #FFFFFF; "
    "border: 1px solid #BF360C; border-radius: 3px; "
    "font-size: 9pt; padding: 5px 16px; font-weight: bold; }"
    "QPushButton:hover { background: #EF6C00; }"
    "QPushButton:disabled { background: #78909C; color: #CCC; }"
)

_COLOR_MATCH = "#E8F5E9"
_COLOR_MISMATCH = "#FFEBEE"
_COLOR_MISSING = "#FFF3E0"


class CompareParametersDialog(QDialog):
    """Side-by-side comparison of project file vs runtime parameters."""

    def __init__(self, canvas, store, parent=None):
        super().__init__(parent)
        self._canvas = canvas
        self._store = store
        self._diffs = []  # list of diff tuples

        self.setWindowTitle("Compare Parameters — Project vs Controller")
        self.setMinimumSize(950, 550)
        self.resize(1050, 650)
        self.setStyleSheet(_DLG_STYLE)
        self._build_ui()
        self._run_comparison()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Header
        hdr = QFrame()
        hdr.setObjectName("headerBar")
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(8, 4, 8, 4)
        title = QLabel("Compare Parameters")
        title.setObjectName("dlgTitle")
        hdr_lay.addWidget(title)
        hdr_lay.addStretch()

        file_name = Path(self._canvas.file_path).name if self._canvas.file_path else "Unsaved"
        self._lbl_file = QLabel(f"File: {file_name}")
        self._lbl_file.setStyleSheet(f"color: {UI.text_secondary}; font-size: 9pt;")
        hdr_lay.addWidget(self._lbl_file)
        layout.addWidget(hdr)

        # Summary bar
        self._summary = QFrame()
        self._summary.setObjectName("summaryBar")
        sum_lay = QHBoxLayout(self._summary)
        sum_lay.setContentsMargins(8, 4, 8, 4)
        self._lbl_summary = QLabel("")
        self._lbl_summary.setStyleSheet(
            f"font-size: 9pt; font-weight: bold; color: {UI.blue};")
        sum_lay.addWidget(self._lbl_summary)
        sum_lay.addStretch()
        self._lbl_counts = QLabel("")
        self._lbl_counts.setStyleSheet(f"font-size: 9pt; color: {UI.text_secondary};")
        sum_lay.addWidget(self._lbl_counts)
        layout.addWidget(self._summary)

        # Table
        self._table = QTableWidget()
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels([
            "Block", "Type", "Parameter",
            "Project Value", "Runtime Value", "Status", "Action",
        ])
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(24)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Interactive)
        hh.setSectionResizeMode(3, QHeaderView.Interactive)
        hh.setSectionResizeMode(4, QHeaderView.Interactive)
        hh.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        hh.resizeSection(0, 120)
        hh.resizeSection(2, 100)
        hh.resizeSection(3, 120)
        hh.resizeSection(4, 120)
        layout.addWidget(self._table, 1)

        # Buttons
        btn_row = QHBoxLayout()

        btn_accept_all = QPushButton("Accept All Mismatches (File -> Controller)")
        btn_accept_all.setStyleSheet(_BTN_ACCEPT)
        btn_accept_all.clicked.connect(self._accept_all)
        btn_row.addWidget(btn_accept_all)

        btn_revert_all = QPushButton("Revert All (Controller -> File)")
        btn_revert_all.setStyleSheet(_BTN_REVERT)
        btn_revert_all.clicked.connect(self._revert_all)
        btn_row.addWidget(btn_revert_all)

        btn_row.addStretch()

        btn_refresh = QPushButton("Re-Compare")
        btn_refresh.setStyleSheet(_BTN_STYLE)
        btn_refresh.clicked.connect(self._run_comparison)
        btn_row.addWidget(btn_refresh)

        btn_close = QPushButton("Close")
        btn_close.setStyleSheet(_BTN_STYLE)
        btn_close.clicked.connect(self.close)
        btn_row.addWidget(btn_close)

        layout.addLayout(btn_row)

    def _run_comparison(self):
        """Run the comparison between file and runtime."""
        self._diffs.clear()

        canvas = self._canvas
        if not canvas.file_path:
            self._lbl_summary.setText("No file path — cannot compare")
            return

        try:
            from azeo_control_trainer.core.strategy.serialization.strategy_io import (
                load_strategy,
            )
            file_graph, _comments = load_strategy(
                canvas.file_path, remember=False)
        except (OSError, ValueError, KeyError) as e:
            self._lbl_summary.setText(f"Cannot read file: {e}")
            return

        # Compare with runtime blocks
        graph = (canvas.runtime.compiled.graph
                 if canvas.runtime and canvas.runtime.compiled else None)
        if graph is None:
            self._lbl_summary.setText("Strategy not compiled — cannot compare runtime")
            return

        store = self._store
        n_match = n_mismatch = n_missing = 0

        for block in graph.blocks.values():
            file_block = matching_block(
                file_graph, block.id, block.instance_name, block.block_type)
            file_config = (file_block.config.params
                           if file_block is not None else {})
            runtime_config = runtime_config_snapshot(block, store)
            for parameter in block.get_config_schema():
                file_present = parameter in file_config
                runtime_present = parameter in runtime_config
                if not file_present and not runtime_present:
                    continue
                file_value = file_config.get(parameter)
                runtime_value = runtime_config.get(parameter)
                if not file_present:
                    status = "NEW (runtime only)"
                    n_missing += 1
                elif not runtime_present:
                    status = "MISSING (runtime)"
                    n_missing += 1
                elif values_differ(file_value, runtime_value):
                    status = "MISMATCH"
                    n_mismatch += 1
                else:
                    status = "MATCH"
                    n_match += 1
                self._diffs.append(ParameterDiff(
                    change=ParameterChange(
                        module=graph.name,
                        file_path=Path(canvas.file_path),
                        block_id=block.id,
                        instance_name=block.instance_name,
                        block_type=block.block_type,
                        parameter=parameter,
                        file_value=file_value,
                        runtime_value=runtime_value,
                        category="configuration",
                    ),
                    status=status,
                    file_present=file_present,
                ))

        # Populate table
        self._table.setRowCount(len(self._diffs))
        for r, diff in enumerate(self._diffs):
            change = diff.change
            status = diff.status
            self._table.setItem(
                r, 0, QTableWidgetItem(change.instance_name))
            self._table.setItem(r, 1, QTableWidgetItem(change.block_type))
            self._table.setItem(r, 2, QTableWidgetItem(change.parameter))
            self._table.setItem(
                r, 3, QTableWidgetItem(self._fmt(change.file_value)))
            self._table.setItem(
                r, 4, QTableWidgetItem(self._fmt(change.runtime_value)))

            status_item = QTableWidgetItem(status)
            status_item.setTextAlignment(Qt.AlignCenter)
            if status == "MATCH":
                bg = _COLOR_MATCH
            elif status == "MISMATCH":
                bg = _COLOR_MISMATCH
            else:
                bg = _COLOR_MISSING
            for c in range(7):
                item = self._table.item(r, c)
                if item:
                    item.setBackground(QColor(bg))
            status_item.setBackground(QColor(bg))
            font = QFont()
            font.setBold(True)
            status_item.setFont(font)
            self._table.setItem(r, 5, status_item)

            # Action column — only for mismatches
            if status == "MISMATCH":
                action_item = QTableWidgetItem("Accept / Revert")
                action_item.setTextAlignment(Qt.AlignCenter)
                action_item.setForeground(QColor("#1565C0"))
                action_item.setBackground(QColor(bg))
                self._table.setItem(r, 6, action_item)
            else:
                empty = QTableWidgetItem("")
                empty.setBackground(QColor(bg))
                self._table.setItem(r, 6, empty)

        # Summary
        total = n_match + n_mismatch + n_missing
        self._lbl_summary.setText(
            "ALL MATCH" if n_mismatch == 0 else
            f"{n_mismatch} MISMATCH{'ES' if n_mismatch > 1 else ''} FOUND")
        self._lbl_counts.setText(
            f"{total} parameters | {n_match} match | "
            f"{n_mismatch} mismatch | {n_missing} new")

    def _accept_all(self):
        """Apply all project values to the compiled runtime graph."""
        canvas = self._canvas
        graph = (canvas.runtime.compiled.graph
                 if canvas.runtime and canvas.runtime.compiled else None)
        if graph is None:
            return
        try:
            count = apply_project_values_to_runtime(
                graph, self._diffs, self._store)
        except Exception as exc:
            QMessageBox.critical(
                self, "Accept All", f"Runtime update was rolled back:\n{exc}")
            return
        QMessageBox.information(
            self, "Accept All",
            f"Applied {count} file values to runtime.\n"
            "Click Re-Compare to verify.")

    def _revert_all(self):
        """Update the file with current runtime values."""
        canvas = self._canvas
        if not canvas.file_path or not canvas.runtime:
            return

        reply = QMessageBox.question(
            self, "Revert All",
            "Overwrite the strategy file with current runtime values?\n"
            "This updates the file on disk.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        changes = [diff.change for diff in self._diffs
                   if diff.status != "MATCH"
                   and diff.change.runtime_value is not None]
        try:
            count = save_parameter_changes(canvas.file_path, changes)
        except Exception as exc:
            log.exception("Controller-to-file compare update failed")
            QMessageBox.critical(
                self, "Revert All", f"Strategy file was not changed:\n{exc}")
            return

        QMessageBox.information(
            self, "Revert All",
            f"Strategy file updated with {count} runtime values.\n"
            "Click Re-Compare to verify.")
        self._run_comparison()

    @staticmethod
    def _fmt(val) -> str:
        if val is None:
            return "<none>"
        if isinstance(val, float):
            return f"{val:.4f}"
        return str(val)
