"""Strategy Diff / Compare dialog — compare two strategy JSON files side by side.

Shows added, removed, and changed blocks and wires with color-coded rows.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI

import json
import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

log = logging.getLogger("strategy.diff")

# Row highlight colors
_GREEN = QColor("#d4edda")   # added
_RED = QColor("#f8d7da")     # removed
_YELLOW = QColor("#fff3cd")  # changed


def _load_strategy_data(path: str | Path) -> dict:
    """Load raw strategy JSON data from file."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _blocks_by_name(data: dict) -> dict[str, dict]:
    """Index blocks by instance_name for comparison."""
    result: dict[str, dict] = {}
    for b in data.get("blocks", []):
        name = b.get("instance_name", b.get("id", "unknown"))
        result[name] = b
    return result


def _wires_key(w: dict) -> str:
    """Create a unique key for a wire based on its connections."""
    src = w.get("src_block_id", "?")
    src_t = w.get("src_terminal", "?")
    dst = w.get("dst_block_id", "?")
    dst_t = w.get("dst_terminal", "?")
    bkcal = w.get("is_bkcal", False)
    return f"{src}.{src_t} -> {dst}.{dst_t} (bkcal={bkcal})"


def _wire_display(w: dict, block_names: dict[str, str]) -> str:
    """Human-readable wire description using block names where possible."""
    src_id = w.get("src_block_id", "?")
    dst_id = w.get("dst_block_id", "?")
    src_name = block_names.get(src_id, src_id[:8])
    dst_name = block_names.get(dst_id, dst_id[:8])
    src_t = w.get("src_terminal", "?")
    dst_t = w.get("dst_terminal", "?")
    return f"{src_name}.{src_t} -> {dst_name}.{dst_t}"


def _block_id_to_name(data: dict) -> dict[str, str]:
    """Map block id -> instance_name."""
    return {b.get("id", ""): b.get("instance_name", b.get("id", ""))
            for b in data.get("blocks", [])}


def _compare_dicts(old: dict, new: dict, skip_keys: set[str] | None = None
                   ) -> list[tuple[str, str, str]]:
    """Compare two dicts and return list of (key, old_value, new_value) for differences."""
    skip = skip_keys or set()
    diffs: list[tuple[str, str, str]] = []
    all_keys = sorted(set(old.keys()) | set(new.keys()))
    for k in all_keys:
        if k in skip:
            continue
        ov = old.get(k)
        nv = new.get(k)
        if ov != nv:
            diffs.append((k, repr(ov), repr(nv)))
    return diffs


class StrategyDiffDialog(QDialog):
    """Dialog to compare two strategy JSON files."""

    def __init__(self, parent=None, file_a: str = "", file_b: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Compare Strategies")
        self.setMinimumSize(700, 500)
        self.resize(850, 600)

        self._file_a = file_a
        self._file_b = file_b

        self._build_ui()

        if file_a and file_b:
            self._path_a.setText(file_a)
            self._path_b.setText(file_b)
            self._run_diff()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        # File selection row A
        row_a = QHBoxLayout()
        row_a.addWidget(QLabel("File A (base):"))
        self._path_a = QLabel(self._file_a or "(none)")
        self._path_a.setStyleSheet(
            f"QLabel {{ color: {UI.text_secondary}; background: #f5f5f5; "
            "border: 1px solid #c0c7d0; border-radius: 3px; padding: 3px 6px; }")
        self._path_a.setMinimumWidth(350)
        row_a.addWidget(self._path_a, 1)
        btn_a = QPushButton("Browse...")
        btn_a.setStyleSheet(self._btn_style())
        btn_a.clicked.connect(lambda: self._browse("a"))
        row_a.addWidget(btn_a)
        layout.addLayout(row_a)

        # File selection row B
        row_b = QHBoxLayout()
        row_b.addWidget(QLabel("File B (new):"))
        self._path_b = QLabel(self._file_b or "(none)")
        self._path_b.setStyleSheet(
            f"QLabel {{ color: {UI.text_secondary}; background: #f5f5f5; "
            "border: 1px solid #c0c7d0; border-radius: 3px; padding: 3px 6px; }")
        self._path_b.setMinimumWidth(350)
        row_b.addWidget(self._path_b, 1)
        btn_b = QPushButton("Browse...")
        btn_b.setStyleSheet(self._btn_style())
        btn_b.clicked.connect(lambda: self._browse("b"))
        row_b.addWidget(btn_b)
        layout.addLayout(row_b)

        # Compare button
        btn_row = QHBoxLayout()
        self._btn_compare = QPushButton("Compare")
        self._btn_compare.setStyleSheet(
            f"QPushButton {{ background: {UI.blue}; color: #fff; "
            "border: none; border-radius: 3px; padding: 6px 20px; "
            "font-weight: bold; font-size: 9pt; }"
            f"QPushButton:hover {{ background: {UI.blue}; }}"
            f"QPushButton:disabled {{ background: {UI.border}; }}")
        self._btn_compare.clicked.connect(self._run_diff)
        btn_row.addStretch()
        btn_row.addWidget(self._btn_compare)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Summary label
        self._summary = QLabel("")
        self._summary.setStyleSheet(f"color: {UI.text_muted}; font-size: 9pt; padding: 4px;")
        layout.addWidget(self._summary)

        # Results tree
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Item", "Detail", "File A (base)", "File B (new)"])
        self._tree.setAlternatingRowColors(True)
        self._tree.setRootIsDecorated(True)
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self._tree.setStyleSheet(
            "QTreeWidget { background: #fff; border: 1px solid #c0c7d0; "
            "font-size: 9pt; }"
            "QTreeWidget::item { padding: 2px; }"
            f"QHeaderView::section {{ background: #f5f6f8; color: {UI.text_secondary}; "
            "border: 1px solid #c0c7d0; padding: 4px; font-size: 9pt; }")
        layout.addWidget(self._tree, 1)

        # Close button
        btn_close = QPushButton("Close")
        btn_close.setStyleSheet(self._btn_style())
        btn_close.clicked.connect(self.close)
        close_row = QHBoxLayout()
        close_row.addStretch()
        close_row.addWidget(btn_close)
        layout.addLayout(close_row)

    def _btn_style(self) -> str:
        return (
            "QPushButton { background: #ffffff; color: #4A5068; "
            "border: 1px solid #c0c7d0; border-radius: 3px; "
            "padding: 4px 12px; font-size: 9pt; }"
            "QPushButton:hover { background: #D2DFEF; border-color: #90A8C8; }")

    def _browse(self, which: str):
        from azeo_control_trainer.core.strategy.serialization.strategy_io import STRATEGY_DIR
        path, _ = QFileDialog.getOpenFileName(
            self, f"Select Strategy File ({which.upper()})",
            str(STRATEGY_DIR),
            "Strategy Files (*.json);;All Files (*)")
        if path:
            if which == "a":
                self._file_a = path
                self._path_a.setText(path)
            else:
                self._file_b = path
                self._path_b.setText(path)

    def _run_diff(self):
        self._tree.clear()
        self._summary.setText("")

        if not self._file_a or not self._file_b:
            self._summary.setText("Please select both files to compare.")
            return

        try:
            data_a = _load_strategy_data(self._file_a)
            data_b = _load_strategy_data(self._file_b)
        except Exception as e:
            self._summary.setText(f"Error loading files: {e}")
            return

        blocks_a = _blocks_by_name(data_a)
        blocks_b = _blocks_by_name(data_b)
        names_a = _block_id_to_name(data_a)
        names_b = _block_id_to_name(data_b)

        all_names = sorted(set(blocks_a.keys()) | set(blocks_b.keys()))

        added_count = 0
        removed_count = 0
        changed_count = 0

        # -- Blocks section --
        blocks_root = QTreeWidgetItem(self._tree, ["Blocks", "", "", ""])
        blocks_root.setExpanded(True)

        skip_keys = {"id", "x", "y", "_ui_width", "_ui_height"}

        for name in all_names:
            in_a = name in blocks_a
            in_b = name in blocks_b

            if in_a and not in_b:
                # Removed
                item = QTreeWidgetItem(blocks_root,
                                       [name, "REMOVED", blocks_a[name].get("block_type", ""), ""])
                self._color_row(item, _RED)
                removed_count += 1

            elif in_b and not in_a:
                # Added
                item = QTreeWidgetItem(blocks_root,
                                       [name, "ADDED", "", blocks_b[name].get("block_type", "")])
                self._color_row(item, _GREEN)
                added_count += 1

            else:
                # Both exist — compare parameters
                diffs = _compare_dicts(blocks_a[name], blocks_b[name], skip_keys)
                if diffs:
                    item = QTreeWidgetItem(
                        blocks_root,
                        [name, f"CHANGED ({len(diffs)} params)", "", ""])
                    self._color_row(item, _YELLOW)
                    changed_count += 1
                    for key, old_v, new_v in diffs:
                        child = QTreeWidgetItem(item, ["", key, old_v, new_v])
                        self._color_row(child, _YELLOW)

        # -- Wires section --
        wires_root = QTreeWidgetItem(self._tree, ["Wires", "", "", ""])
        wires_root.setExpanded(True)

        # Build wire sets by key
        wires_a_by_key: dict[str, dict] = {}
        for w in data_a.get("wires", []):
            wires_a_by_key[_wires_key(w)] = w
        wires_b_by_key: dict[str, dict] = {}
        for w in data_b.get("wires", []):
            wires_b_by_key[_wires_key(w)] = w

        all_wire_keys = sorted(set(wires_a_by_key.keys()) | set(wires_b_by_key.keys()))
        wire_added = 0
        wire_removed = 0

        for wk in all_wire_keys:
            in_a = wk in wires_a_by_key
            in_b = wk in wires_b_by_key

            if in_a and not in_b:
                display = _wire_display(wires_a_by_key[wk], names_a)
                item = QTreeWidgetItem(wires_root,
                                       ["", "REMOVED", display, ""])
                self._color_row(item, _RED)
                wire_removed += 1
            elif in_b and not in_a:
                display = _wire_display(wires_b_by_key[wk], names_b)
                item = QTreeWidgetItem(wires_root,
                                       ["", "ADDED", "", display])
                self._color_row(item, _GREEN)
                wire_added += 1

        # Summary
        total = added_count + removed_count + changed_count + wire_added + wire_removed
        if total == 0:
            self._summary.setText("No differences found between the two strategies.")
        else:
            parts = []
            if added_count:
                parts.append(f"{added_count} block(s) added")
            if removed_count:
                parts.append(f"{removed_count} block(s) removed")
            if changed_count:
                parts.append(f"{changed_count} block(s) changed")
            if wire_added:
                parts.append(f"{wire_added} wire(s) added")
            if wire_removed:
                parts.append(f"{wire_removed} wire(s) removed")
            self._summary.setText("Differences: " + ", ".join(parts))

    @staticmethod
    def _color_row(item: QTreeWidgetItem, color: QColor):
        for col in range(4):
            item.setBackground(col, color)
