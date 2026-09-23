"""Version History dialog — browse and restore strategy auto-versions."""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI

import logging
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QVBoxLayout,
)

log = logging.getLogger("strategy.version_history")


class VersionHistoryDialog(QDialog):
    """Dialog showing saved strategy versions with restore/delete options."""

    def __init__(self, strategy_path: Path | str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Version History")
        self.setMinimumSize(450, 350)
        self.resize(500, 420)

        self._strategy_path = Path(strategy_path)
        self._versions_dir = self._strategy_path.parent / "versions"
        self._selected_path: Path | None = None

        self._build_ui()
        self._refresh_list()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        # Header
        header = QLabel(f"Versions of: {self._strategy_path.name}")
        header.setStyleSheet(
            f"font-size: 9pt; font-weight: bold; color: {UI.text_secondary}; padding: 4px;")
        layout.addWidget(header)

        # Version list
        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.setStyleSheet(
            "QListWidget { background: #fff; border: 1px solid #c0c7d0; "
            "font-size: 9pt; }"
            "QListWidget::item { padding: 4px 8px; }"
            f"QListWidget::item:selected {{ background: {UI.blue}; color: #fff; }}")
        self._list.currentItemChanged.connect(self._on_selection_changed)
        layout.addWidget(self._list, 1)

        # Info label
        self._info = QLabel("")
        self._info.setStyleSheet(f"color: {UI.text_muted}; font-size: 9pt; padding: 2px;")
        layout.addWidget(self._info)

        # Buttons
        btn_row = QHBoxLayout()
        self._btn_restore = QPushButton("Restore")
        self._btn_restore.setEnabled(False)
        self._btn_restore.setStyleSheet(
            "QPushButton { background: #2e7d32; color: #fff; "
            "border: none; border-radius: 3px; padding: 6px 16px; "
            "font-weight: bold; font-size: 9pt; }"
            "QPushButton:hover { background: #388e3c; }"
            f"QPushButton:disabled {{ background: {UI.border}; }}")
        self._btn_restore.clicked.connect(self._restore)
        btn_row.addWidget(self._btn_restore)

        self._btn_delete = QPushButton("Delete")
        self._btn_delete.setEnabled(False)
        self._btn_delete.setStyleSheet(
            "QPushButton { background: #c62828; color: #fff; "
            "border: none; border-radius: 3px; padding: 6px 16px; "
            "font-weight: bold; font-size: 9pt; }"
            "QPushButton:hover { background: #d32f2f; }"
            f"QPushButton:disabled {{ background: {UI.border}; }}")
        self._btn_delete.clicked.connect(self._delete)
        btn_row.addWidget(self._btn_delete)

        btn_row.addStretch()

        btn_close = QPushButton("Close")
        btn_close.setStyleSheet(
            f"QPushButton {{ background: #ffffff; color: {UI.text_secondary}; "
            "border: 1px solid #c0c7d0; border-radius: 3px; "
            "padding: 6px 16px; font-size: 9pt; }"
            "QPushButton:hover { background: #D2DFEF; }")
        btn_close.clicked.connect(self.close)
        btn_row.addWidget(btn_close)

        layout.addLayout(btn_row)

    def _refresh_list(self):
        self._list.clear()
        self._btn_restore.setEnabled(False)
        self._btn_delete.setEnabled(False)
        self._info.setText("")

        if not self._versions_dir.exists():
            self._info.setText("No version history found.")
            return

        # Get strategy stem to filter matching versions
        stem = self._strategy_path.stem
        versions = sorted(
            self._versions_dir.glob(f"{stem}_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        if not versions:
            self._info.setText("No saved versions found.")
            return

        for vp in versions:
            # Extract timestamp from filename
            suffix = vp.stem[len(stem) + 1:]  # e.g. "2024-01-15_143022"
            try:
                ts = datetime.strptime(suffix, "%Y-%m-%d_%H%M%S")
                display = ts.strftime("%Y-%m-%d  %H:%M:%S")
            except ValueError:
                display = suffix

            size_kb = vp.stat().st_size / 1024
            item = QListWidgetItem(f"{display}   ({size_kb:.1f} KB)")
            item.setData(Qt.UserRole, str(vp))
            self._list.addItem(item)

        self._info.setText(f"{len(versions)} version(s) found.")

    def _on_selection_changed(self, current: QListWidgetItem | None,
                              previous: QListWidgetItem | None):
        has_sel = current is not None
        self._btn_restore.setEnabled(has_sel)
        self._btn_delete.setEnabled(has_sel)
        if has_sel:
            self._selected_path = Path(current.data(Qt.UserRole))
        else:
            self._selected_path = None

    def _restore(self):
        if not self._selected_path or not self._selected_path.exists():
            return

        reply = QMessageBox.question(
            self, "Restore Version",
            f"Restore this version?\n\n{self._selected_path.name}\n\n"
            "The current strategy will be overwritten.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        self._restore_path = self._selected_path
        self.accept()

    def _delete(self):
        if not self._selected_path or not self._selected_path.exists():
            return

        reply = QMessageBox.question(
            self, "Delete Version",
            f"Delete this version?\n\n{self._selected_path.name}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        try:
            self._selected_path.unlink()
            log.info("Deleted version: %s", self._selected_path)
            self._refresh_list()
        except OSError as e:
            QMessageBox.critical(self, "Error", f"Failed to delete: {e}")

    def get_restore_path(self) -> Path | None:
        """Return the path selected for restore, or None if cancelled."""
        return getattr(self, "_restore_path", None)
