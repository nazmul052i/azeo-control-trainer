"""Professional dialogs for refactoring, comparison, annotations and review."""
from __future__ import annotations

from pathlib import Path
import re

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from azeo_control_trainer.core.presentation.engineering_dialog import polish_dialog
from azeo_control_trainer.core.procedures.governance import read_governance, transition_revision
from .refactoring import find_usages, rename_symbol
from .revisions import compare_revision


def _table(headers):
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.verticalHeader().hide()
    table.horizontalHeader().setStretchLastSection(True)
    return table


class RefactorDialog(QDialog):
    def __init__(self, data, bindings, parent=None, *, kind="tag", symbol=""):
        super().__init__(parent)
        self.data, self.bindings = data, bindings
        self.setWindowTitle("Rename symbol and find usages")
        self.resize(820, 520)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.kind = QComboBox()
        self.kind.addItems(["tag", "variable", "step"])
        self.kind.setCurrentText(kind)
        self.old = QLineEdit(symbol)
        self.new = QLineEdit()
        form.addRow("Symbol type", self.kind)
        form.addRow("Current name", self.old)
        form.addRow("New name", self.new)
        layout.addLayout(form)
        self.usages = _table(["Location", "Property", "Current value"])
        layout.addWidget(self.usages, 1)
        self.summary = QLabel(wordWrap=True)
        layout.addWidget(self.summary)
        self.error = QLabel(wordWrap=True)
        self.error.setStyleSheet("color: #b71c1c;")
        layout.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Rename all usages")
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.kind.currentTextChanged.connect(self.refresh)
        self.old.textChanged.connect(self.refresh)
        self.new.textChanged.connect(self.refresh)
        polish_dialog(self, title="Safe rename", subtitle="Preview every semantic reference before applying one undoable refactor.", mark="properties")
        self.refresh()

    def refresh(self, *_):
        rows = find_usages(self.data, self.bindings, self.kind.currentText(), self.old.text().strip()) if self.old.text().strip() else []
        self.usages.setRowCount(len(rows))
        for index, row in enumerate(rows):
            for col, value in enumerate((row.location, row.field, row.preview)):
                self.usages.setItem(index, col, QTableWidgetItem(str(value)))
        self.summary.setText(f"{len(rows)} semantic usages will be updated." if rows else "No usages found.")
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(bool(rows and self.new.text().strip()))

    def _accept(self):
        try:
            rename_symbol(self.data, self.bindings, self.kind.currentText(), self.old.text(), self.new.text())
        except ValueError as error:
            self.error.setText(str(error))
            return
        self.accept()


class RevisionCompareDialog(QDialog):
    def __init__(self, revision_paths, draft, parent=None):
        super().__init__(parent)
        self.draft = draft
        self.setWindowTitle("Compare procedure revisions")
        self.resize(1050, 640)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Baseline saved revision"))
        self.baseline = QComboBox()
        for path in revision_paths:
            self.baseline.addItem(path.parent.name, str(path))
        row.addWidget(self.baseline, 1)
        layout.addLayout(row)
        self.summary = QLabel(wordWrap=True)
        layout.addWidget(self.summary)
        self.changes = _table(["Area", "Item", "Change", "Before", "After", "Impact"])
        layout.addWidget(self.changes, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
        self.baseline.currentIndexChanged.connect(self.refresh)
        polish_dialog(self, title="Revision compare and impact", subtitle="Compare a saved immutable baseline with the current working draft.", mark="compare")
        self.refresh()

    def refresh(self, *_):
        if self.baseline.currentIndex() < 0:
            self.summary.setText("Save a revision before comparing changes.")
            self.changes.setRowCount(0)
            return
        rows = compare_revision(Path(self.baseline.currentData()), self.draft)
        self.summary.setText(f"{len(rows)} semantic changes · current working draft is the target")
        self.changes.setRowCount(len(rows))
        for index, change in enumerate(rows):
            for col, value in enumerate((change.area, change.identity, change.change, change.before, change.after, change.impact)):
                self.changes.setItem(index, col, QTableWidgetItem(value))


class AnnotationDialog(QDialog):
    def __init__(self, parent=None, annotation=None):
        super().__init__(parent)
        annotation = annotation or {}
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.kind = QComboBox()
        self.kind.addItems(["note", "phase", "rectangle", "swimlane"])
        self.kind.setCurrentText(annotation.get("kind", "note"))
        self.text = QPlainTextEdit(annotation.get("text", ""))
        self.text.setMaximumHeight(120)
        self.width = QLineEdit(str(annotation.get("width", 300)))
        self.height = QLineEdit(str(annotation.get("height", 120)))
        self.color = QLineEdit(annotation.get("color", "#607D8B"))
        for label, field in (("Type", self.kind), ("Text", self.text), ("Width", self.width), ("Height", self.height), ("Color", self.color)):
            form.addRow(label, field)
        layout.addLayout(form)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Apply annotation")
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.error = QLabel(wordWrap=True)
        self.error.setStyleSheet("color: #b71c1c;")
        layout.insertWidget(layout.count() - 1, self.error)
        polish_dialog(self, title="Engineering annotation", subtitle="Annotations provide design context and never execute.", mark="comment")

    def _accept(self):
        try:
            self.value()
        except (ValueError, TypeError) as error:
            self.error.setText(str(error))
            return
        self.accept()

    def value(self):
        width, height = int(self.width.text()), int(self.height.text())
        if not 80 <= width <= 4000 or not 40 <= height <= 4000:
            raise ValueError("Annotation size is outside the supported range")
        color = self.color.text().strip()
        if len(color) != 7 or not color.startswith("#"):
            raise ValueError("Enter color as #RRGGBB")
        int(color[1:], 16)
        return {"kind": self.kind.currentText(), "text": self.text.toPlainText().strip(),
                "width": width, "height": height, "color": color.upper()}


class ExtractProcedureDialog(QDialog):
    def __init__(self, selected_count, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"{selected_count} contiguous blocks will be replaced by one pinned reusable-procedure call.",
            wordWrap=True,
        ))
        form = QFormLayout()
        self.procedure_id = QLineEdit("reusable_sequence")
        self.name = QLineEdit("Reusable sequence")
        self.version = QLineEdit("1.0.0")
        self.author = QLineEdit()
        self.note = QPlainTextEdit("Extracted from a parent procedure.")
        self.note.setMaximumHeight(75)
        for label, field in (("Procedure ID", self.procedure_id), ("Name", self.name), ("Version", self.version),
                             ("Author", self.author), ("Revision note", self.note)):
            form.addRow(label, field)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Create reusable revision")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.error = QLabel(wordWrap=True)
        self.error.setStyleSheet("color: #b71c1c;")
        layout.insertWidget(layout.count() - 1, self.error)
        polish_dialog(
            self,
            title="Extract reusable procedure",
            subtitle="The child is saved first; the parent remains an editable draft.",
            mark="templates",
        )

    def _accept(self):
        values = self.values()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{1,79}", values["procedure_id"].strip()):
            self.error.setText("Procedure ID must start with a letter and use letters, numbers, underscore or hyphen")
            return
        if not values["name"].strip():
            self.error.setText("Enter a reusable procedure name")
            return
        self.accept()

    def values(self):
        return {
            "procedure_id": self.procedure_id.text(),
            "name": self.name.text(),
            "version": self.version.text(),
            "author": self.author.text(),
            "revision_note": self.note.toPlainText(),
        }


class GovernanceDialog(QDialog):
    def __init__(self, revision_paths, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Review and release")
        self.resize(820, 560)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.revision = QComboBox()
        for path in revision_paths:
            self.revision.addItem(path.parent.name, str(path))
        self.status = QLineEdit()
        self.status.setReadOnly(True)
        self.actor = QLineEdit()
        self.role = QLineEdit()
        self.reason = QPlainTextEdit()
        self.reason.setMaximumHeight(80)
        form.addRow("Saved revision", self.revision)
        form.addRow("Controlled state", self.status)
        form.addRow("Actor", self.actor)
        form.addRow("Role", self.role)
        form.addRow("Review evidence / reason", self.reason)
        layout.addLayout(form)
        self.history = _table(["Transition", "Actor", "Role", "Time", "Reason"])
        layout.addWidget(self.history, 1)
        bar = QHBoxLayout()
        self.transition = QPushButton("Advance controlled state")
        self.transition.clicked.connect(self.advance)
        bar.addWidget(self.transition)
        bar.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        bar.addWidget(close)
        layout.addLayout(bar)
        self.message = QLabel(wordWrap=True)
        layout.insertWidget(layout.count() - 1, self.message)
        self.revision.currentIndexChanged.connect(self.refresh)
        polish_dialog(self, title="Review and release", subtitle="Evidence is hash-chained beside immutable revision content.", mark="compile")
        self.refresh()

    def refresh(self, *_):
        if self.revision.currentIndex() < 0:
            self.status.setText("No saved revision")
            self.transition.setEnabled(False)
            return
        try:
            record = read_governance(Path(self.revision.currentData()))
        except ValueError as error:
            self.status.setText("Integrity error")
            self.message.setText(str(error))
            self.transition.setEnabled(False)
            return
        self.status.setText(record["status"].title())
        target = {"draft": "review", "review": "approved", "approved": "released"}.get(record["status"])
        self.transition.setText(f"Mark as {target}" if target else "Released")
        self.transition.setEnabled(bool(target))
        self.history.setRowCount(len(record["events"]))
        for index, event in enumerate(record["events"]):
            values = (f"{event['from_status']} → {event['to_status']}", event["actor"], event["role"], event["occurred_at"], event["reason"])
            for col, value in enumerate(values):
                self.history.setItem(index, col, QTableWidgetItem(value))

    def advance(self):
        record = read_governance(Path(self.revision.currentData()))
        target = {"draft": "review", "review": "approved", "approved": "released"}.get(record["status"])
        try:
            transition_revision(Path(self.revision.currentData()), target, actor=self.actor.text(), role=self.role.text(), reason=self.reason.toPlainText())
        except ValueError as error:
            self.message.setText(str(error))
            return
        self.message.setText(f"Controlled state advanced to {target}.")
        self.reason.clear()
        self.refresh()
