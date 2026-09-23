"""Template Manager dialog — browse, preview, and delete block templates."""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI

import json
import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QTextEdit, QVBoxLayout,
)

log = logging.getLogger("strategy.template_manager")

# Template storage directory
from azeo_control_trainer.config.paths import data_dir
TEMPLATE_DIR = data_dir() / "templates"


def list_templates() -> list[Path]:
    """List all saved template files."""
    if not TEMPLATE_DIR.exists():
        return []
    return sorted(TEMPLATE_DIR.glob("*.json"))


def load_template_data(path: Path) -> dict:
    """Load template JSON data."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def delete_template(path: Path) -> None:
    """Delete a template file."""
    path.unlink()


class TemplateManagerDialog(QDialog):
    """Dialog for browsing, previewing, and deleting block templates."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Template Manager")
        self.setMinimumSize(550, 400)
        self.resize(650, 480)

        self._selected_path: Path | None = None
        self._build_ui()
        self._refresh_list()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        header = QLabel("Saved Block Templates")
        header.setStyleSheet(
            f"font-size: 9pt; font-weight: bold; color: {UI.text_secondary}; padding: 4px;")
        layout.addWidget(header)

        # Main area: list + preview
        body = QHBoxLayout()

        # Template list
        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.setStyleSheet(
            "QListWidget { background: #fff; border: 1px solid #c0c7d0; "
            "font-size: 9pt; }"
            "QListWidget::item { padding: 4px 8px; }"
            f"QListWidget::item:selected {{ background: {UI.blue}; color: #fff; }}")
        self._list.currentItemChanged.connect(self._on_selection_changed)
        body.addWidget(self._list, 1)

        # Preview pane
        preview_layout = QVBoxLayout()
        preview_label = QLabel("Preview")
        preview_label.setStyleSheet(
            f"font-size: 9pt; font-weight: bold; color: {UI.text_muted}; padding: 2px;")
        preview_layout.addWidget(preview_label)

        self._preview = QTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setStyleSheet(
            "QTextEdit { background: #fafafa; border: 1px solid #c0c7d0; "
            "font-family: 'Consolas', 'Courier New', monospace; font-size: 9pt; "
            f"color: {UI.text_secondary}; }}")
        preview_layout.addWidget(self._preview, 1)
        body.addLayout(preview_layout, 1)

        layout.addLayout(body, 1)

        # Buttons
        btn_row = QHBoxLayout()

        self._btn_place = QPushButton("Place Template")
        self._btn_place.setEnabled(False)
        self._btn_place.setStyleSheet(
            "QPushButton { background: #2e7d32; color: #fff; "
            "border: none; border-radius: 3px; padding: 6px 16px; "
            "font-weight: bold; font-size: 9pt; }"
            "QPushButton:hover { background: #388e3c; }"
            f"QPushButton:disabled {{ background: {UI.border}; }}")
        self._btn_place.clicked.connect(self._place)
        btn_row.addWidget(self._btn_place)

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
        self._preview.clear()
        self._btn_place.setEnabled(False)
        self._btn_delete.setEnabled(False)

        templates = list_templates()
        for tp in templates:
            try:
                data = load_template_data(tp)
                name = data.get("name", tp.stem)
                n_blocks = len(data.get("blocks", []))
                n_wires = len(data.get("wires", []))
                item = QListWidgetItem(f"{name}  ({n_blocks} blocks, {n_wires} wires)")
                item.setData(Qt.UserRole, str(tp))
                self._list.addItem(item)
            except Exception:
                item = QListWidgetItem(f"{tp.stem}  (error reading)")
                item.setData(Qt.UserRole, str(tp))
                self._list.addItem(item)

    def _on_selection_changed(self, current: QListWidgetItem | None,
                              previous: QListWidgetItem | None):
        has_sel = current is not None
        self._btn_place.setEnabled(has_sel)
        self._btn_delete.setEnabled(has_sel)

        if has_sel:
            self._selected_path = Path(current.data(Qt.UserRole))
            self._show_preview(self._selected_path)
        else:
            self._selected_path = None
            self._preview.clear()

    def _show_preview(self, path: Path):
        try:
            data = load_template_data(path)
            lines = []
            lines.append(f"Template: {data.get('name', '?')}")
            lines.append("")

            blocks = data.get("blocks", [])
            lines.append(f"Blocks ({len(blocks)}):")
            for b in blocks:
                btype = b.get("block_type", "?")
                bname = b.get("instance_name", "?")
                lines.append(f"  {btype}: {bname}")

            wires = data.get("wires", [])
            lines.append(f"\nWires ({len(wires)}):")
            # Build id->name map
            id_names = {b.get("id", ""): b.get("instance_name", "?") for b in blocks}
            for w in wires:
                src = id_names.get(w.get("src_block_id", ""), "?")
                dst = id_names.get(w.get("dst_block_id", ""), "?")
                lines.append(
                    f"  {src}.{w.get('src_terminal', '?')} -> "
                    f"{dst}.{w.get('dst_terminal', '?')}")

            self._preview.setPlainText("\n".join(lines))
        except Exception as e:
            self._preview.setPlainText(f"Error loading template: {e}")

    def _place(self):
        if self._selected_path:
            self.accept()

    def _delete(self):
        if not self._selected_path:
            return
        reply = QMessageBox.question(
            self, "Delete Template",
            f"Delete template?\n\n{self._selected_path.stem}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        try:
            delete_template(self._selected_path)
            log.info("Deleted template: %s", self._selected_path)
            self._selected_path = None
            self._refresh_list()
        except OSError as e:
            QMessageBox.critical(self, "Error", f"Failed to delete: {e}")

    def get_selected_path(self) -> Path | None:
        """Return the selected template path for placement, or None."""
        return self._selected_path
