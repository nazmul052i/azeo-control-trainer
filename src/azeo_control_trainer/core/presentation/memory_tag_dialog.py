"""One typed memory-tag editor for Control Designer and PA Designer."""
import json

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout

from .authoring_controls import AuthoringComboBox
from .engineering_dialog import ENGINEERING_QSS, polish_dialog
from ..datastore.memory_tags import MemoryTag


class MemoryDialog(QDialog):
    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.result_value = None
        self.setWindowTitle("New project memory tag")
        self.setStyleSheet(ENGINEERING_QSS)
        self.resize(460, 340)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Save creates a shared Tag DB entry immediately. Its last value is retained across PA runs and application restarts.", wordWrap=True))
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. shutdown_limit")
        self.dtype = AuthoringComboBox()
        self.dtype.addItems(["float", "int", "bool", "str"])
        self.value = QLineEdit("0")
        self.dtype.currentTextChanged.connect(lambda kind: self.value.setText({"float": "0", "int": "0", "bool": "false", "str": ""}[kind]))
        self.minimum, self.maximum, self.description = QLineEdit(), QLineEdit(), QLineEdit()
        for label, field in (("Name", self.name), ("Type", self.dtype), ("Initial value", self.value),
                             ("Minimum (optional)", self.minimum), ("Maximum (optional)", self.maximum),
                             ("Description", self.description)):
            form.addRow(label, field)
        layout.addLayout(form)
        self.error = QLabel("", wordWrap=True)
        layout.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.apply)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        polish_dialog(
            self,
            title="New project memory tag",
            subtitle="Create one typed value shared by Control Designer and PA Designer.",
            mark="new",
        )

    def apply(self):
        try:
            value = self.value.text() if self.dtype.currentText() == "str" else json.loads(self.value.text())
            row = MemoryTag(name=self.name.text().strip(), data_type=self.dtype.currentText(), value=value,
                            min_value=float(self.minimum.text()) if self.minimum.text().strip() else None,
                            max_value=float(self.maximum.text()) if self.maximum.text().strip() else None,
                            description=self.description.text())
            if self.store is None:
                raise ValueError("Open an editable project to create shared memory tags")
            self.store.create(row)
            self.result_value = row.model_dump()
            self.accept()
        except Exception as error:
            self.error.setText(str(error))


def attach_memory_picker(field, changed):
    """Bind a Control Designer memory block through the existing Tag DB view."""
    from types import SimpleNamespace
    from PySide6.QtWidgets import QCompleter
    from PySide6.QtCore import Qt
    from .configuration_chrome import icon
    from ..datastore.memory_tags import MemoryTagStore
    from ..strategy.serialization import strategy_io
    from ..strategy.tagdb import TagDatabase
    from .tagdb_browser import TagDatabaseBrowser

    database = TagDatabase()
    database.memory = MemoryTagStore(strategy_io.STRATEGY_DIR)
    database.reload_memory()
    completer = QCompleter([spec.path for spec in database.memory.definitions()], field)
    completer.setCaseSensitivity(Qt.CaseInsensitive)
    field.setCompleter(completer)

    def browse():
        dialog = QDialog(field)
        dialog.setWindowTitle("Select project memory tag")
        dialog.resize(760, 620)
        dialog.setStyleSheet(ENGINEERING_QSS)
        layout = QVBoxLayout(dialog)
        dialog.browser = TagDatabaseBrowser(store=SimpleNamespace(tagdb=database), parent=dialog)
        layout.addWidget(dialog.browser)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Use selected")
        layout.addWidget(buttons)
        dialog.selected_path = ""
        dialog.browser.selectedPathChanged.connect(lambda path: setattr(dialog, "selected_path", path))
        field._memory_picker = dialog
        def select(path):
            entry = dialog.browser.database.lookup(path)
            if entry and entry.kind == "memory":
                field.setText(path)
                changed(path)
                dialog.accept()
        dialog.browser.pathActivated.connect(select)
        buttons.accepted.connect(lambda: select(dialog.selected_path))
        buttons.rejected.connect(dialog.reject)
        dialog.open()
    action = field.addAction(icon("search"), QLineEdit.TrailingPosition)
    action.setToolTip("Select or create a shared memory tag in the project Tag DB")
    action.triggered.connect(browse)
