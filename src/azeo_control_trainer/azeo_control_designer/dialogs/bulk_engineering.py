"""Preview-first bulk module generation and configuration editing."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QLabel, QMessageBox, QPlainTextEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
    QHeaderView,
)

from azeo_control_trainer.core.presentation.brand import UI
from azeo_control_trainer.core.presentation.engineering_dialog import (
    ENGINEERING_QSS, polish_dialog,
)
from azeo_control_trainer.core.presentation.headless import is_headless
from azeo_control_trainer.core.strategy.bulk_engineering import (
    parse_edit_csv, parse_generation_csv, prepare_edits, prepare_generation,
)
from azeo_control_trainer.core.strategy.serialization import strategy_io


class BulkEngineeringDialog(QDialog):
    """Generate or edit many modules without bypassing validation."""

    operationApplied = Signal(object)  # list[Path]

    def __init__(self, project_tree, canvases, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Bulk Module Engineering")
        self.resize(980, 720)
        self.setMinimumSize(780, 580)
        self.setStyleSheet(ENGINEERING_QSS)
        self._project_tree = project_tree
        self._canvases = list(canvases)
        self._prepared = None
        self._prepared_mode = ""
        self._build_ui()
        self._refresh_sources()
        polish_dialog(self)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        title = QLabel("Bulk Module Engineering")
        title.setObjectName("dlgTitle")
        layout.addWidget(title)
        note = QLabel(
            "Every proposed module is schema-checked and compiled before Apply. "
            "Apply commits the complete set or restores every original file.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {UI.text_secondary};")
        layout.addWidget(note)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._generation_page(), "Generate from Module Template")
        self._tabs.addTab(self._edit_page(), "Controlled Bulk Edit")
        self._tabs.currentChanged.connect(self._invalidate)
        layout.addWidget(self._tabs, 1)

        self._status = QLabel("Enter rows, then Preview.")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(
            f"background: {UI.chrome_alt}; border: 1px solid {UI.border}; "
            f"padding: 6px; color: {UI.text_secondary};")
        layout.addWidget(self._status)

        self._preview = QTableWidget(0, 4)
        self._preview.setHorizontalHeaderLabels(
            ["Module", "Target", "Before", "After"])
        self._preview.setAlternatingRowColors(True)
        self._preview.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._preview.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._preview.verticalHeader().setVisible(False)
        header = self._preview.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        layout.addWidget(self._preview, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self._preview_button = QPushButton("Preview and Validate")
        self._preview_button.clicked.connect(self._prepare)
        buttons.addButton(self._preview_button, QDialogButtonBox.ActionRole)
        self._apply_button = QPushButton("Apply")
        self._apply_button.setEnabled(False)
        self._apply_button.clicked.connect(self._apply)
        buttons.addButton(self._apply_button, QDialogButtonBox.AcceptRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _generation_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self._template = QComboBox()
        self._template.currentIndexChanged.connect(self._invalidate)
        form.addRow("Source module:", self._template)
        self._area = QComboBox()
        for area in self._project_tree.areas():
            self._area.addItem(
                str(area.get("name") or "Area"), str(area.get("area_id") or ""))
        form.addRow("Project area:", self._area)
        layout.addLayout(form)
        help_text = QLabel(
            "Use {{MODULE}} and {{PREFIX}} inside string properties of the "
            "source module. The generated module receives new deterministic "
            "block identities. CSV columns: module,prefix")
        help_text.setWordWrap(True)
        help_text.setStyleSheet(f"color: {UI.text_secondary};")
        layout.addWidget(help_text)
        self._generation_csv = QPlainTextEdit()
        self._generation_csv.setPlaceholderText(
            "module,prefix\nFIC-201,F201\nFIC-202,F202")
        self._generation_csv.textChanged.connect(self._invalidate)
        layout.addWidget(self._generation_csv, 1)
        return page

    def _edit_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        help_text = QLabel(
            "CSV columns: module,block,parameter,value. Block may be an "
            "instance name or UUID. Use @module with description or scan_ms. "
            "Unknown blocks, properties, types, choices, or non-compiling "
            "results are refused.")
        help_text.setWordWrap(True)
        help_text.setStyleSheet(f"color: {UI.text_secondary};")
        layout.addWidget(help_text)
        self._edit_csv = QPlainTextEdit()
        self._edit_csv.setPlaceholderText(
            "module,block,parameter,value\n"
            "FIC-101,PID-101,GAIN,1.5\n"
            "FIC-102,@module,scan_ms,250")
        self._edit_csv.textChanged.connect(self._invalidate)
        layout.addWidget(self._edit_csv, 1)
        return page

    def _refresh_sources(self):
        self._template.clear()
        seen: set[Path] = set()
        for canvas in self._canvases:
            path = getattr(canvas, "file_path", None)
            if not path:
                continue
            resolved = Path(path).resolve()
            seen.add(resolved)
            self._template.addItem(
                canvas.scene.graph.name, (resolved, canvas.scene.graph.to_dict()))
        for _folder, paths in strategy_io.list_strategy_folders().items():
            for path in paths:
                resolved = Path(path).resolve()
                if resolved in seen:
                    continue
                try:
                    document = json.loads(resolved.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                self._template.addItem(
                    str(document.get("name") or resolved.stem),
                    (resolved, document))

    def _invalidate(self, *_args):
        self._prepared = None
        self._prepared_mode = ""
        self._apply_button.setEnabled(False)
        self._preview.setRowCount(0)
        self._status.setText("Input changed. Preview and validate again.")

    def _module_documents(self) -> dict[Path, dict]:
        result: dict[Path, dict] = {}
        for _folder, paths in strategy_io.list_strategy_folders().items():
            for path in paths:
                resolved = Path(path).resolve()
                try:
                    result[resolved] = json.loads(
                        resolved.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
        for canvas in self._canvases:
            if canvas.file_path:
                result[Path(canvas.file_path).resolve()] = \
                    canvas.scene.graph.to_dict()
        return result

    def _prepare(self):
        try:
            if self._tabs.currentIndex() == 0:
                source = self._template.currentData()
                if not source:
                    raise ValueError("Select a source module")
                source_path, document = source
                rows = parse_generation_csv(self._generation_csv.toPlainText())
                operation = prepare_generation(
                    document, Path(source_path).parent, rows)
                mode = "generate"
            else:
                rows = parse_edit_csv(self._edit_csv.toPlainText())
                operation = prepare_edits(self._module_documents(), rows)
                self._ensure_open_targets_safe(operation.documents)
                mode = "edit"
        except Exception as exc:  # user-authored batch; show the exact refusal
            self._prepared = None
            self._apply_button.setEnabled(False)
            self._status.setText(f"Preview refused: {exc}")
            if not is_headless():
                QMessageBox.warning(self, "Bulk Preview Refused", str(exc))
            return None

        self._prepared = operation
        self._prepared_mode = mode
        self._render_preview(operation)
        self._apply_button.setEnabled(True)
        warning = (" " + " ".join(operation.warnings)) if operation.warnings else ""
        self._status.setText(
            f"Validated {len(operation.documents)} module(s) and "
            f"{len(operation.changes)} change(s). No files have changed.{warning}")
        return operation

    def _ensure_open_targets_safe(self, documents):
        targets = {Path(path).resolve() for path in documents}
        for canvas in self._canvases:
            if not canvas.file_path or Path(canvas.file_path).resolve() not in targets:
                continue
            if canvas.runtime and canvas.runtime.is_online:
                raise ValueError(
                    f"{canvas.scene.graph.name} is on scan; take it offline first")
            if canvas.dirty:
                raise ValueError(
                    f"{canvas.scene.graph.name} has unsaved edits; save or undo them first")

    def _render_preview(self, operation):
        self._preview.setRowCount(len(operation.changes))
        for row, change in enumerate(operation.changes):
            values = (change.module, change.target,
                      self._value(change.before), self._value(change.after))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self._preview.setItem(row, column, item)

    @staticmethod
    def _value(value) -> str:
        if value is None:
            return "—"
        text = json.dumps(value, ensure_ascii=False) \
            if isinstance(value, (dict, list)) else str(value)
        return text if len(text) < 180 else text[:177] + "..."

    def _apply(self):
        operation = self._prepared
        if operation is None:
            return
        try:
            self._ensure_open_targets_safe(operation.documents)
            paths = operation.apply()
            if self._prepared_mode == "generate":
                try:
                    self._project_tree.register_control_modules(
                        paths, self._area.currentData() or None)
                except Exception:
                    # A generated file that never enters the project index is
                    # not a successful all-or-nothing generation operation.
                    operation.rollback()
                    raise
        except Exception as exc:
            self._status.setText(f"Apply failed; prior files restored: {exc}")
            if not is_headless():
                QMessageBox.critical(self, "Bulk Apply Failed", str(exc))
            return
        try:
            if self._prepared_mode == "edit":
                self._reload_open_modules(paths)
            self._project_tree.refresh()
        except Exception as exc:
            # The validated documents are already durable here. Never claim a
            # rollback occurred when only the view failed to refresh.
            self._status.setText(
                f"Files applied, but the project view could not refresh: {exc}")
            if not is_headless():
                QMessageBox.warning(
                    self, "Bulk Apply Completed", self._status.text())
            self.operationApplied.emit(paths)
            return
        self._apply_button.setEnabled(False)
        self._status.setText(
            f"Applied {len(operation.changes)} change(s) to "
            f"{len(paths)} module(s).")
        self.operationApplied.emit(paths)

    def _reload_open_modules(self, paths):
        targets = {Path(path).resolve() for path in paths}
        for canvas in self._canvases:
            if not canvas.file_path or Path(canvas.file_path).resolve() not in targets:
                continue
            graph, comments = strategy_io.load_strategy(
                canvas.file_path, remember=False)
            canvas.scene.load_graph(graph, comments=comments)
            canvas.dirty = False
            canvas.modified_since_download = canvas.download_snapshot is not None
