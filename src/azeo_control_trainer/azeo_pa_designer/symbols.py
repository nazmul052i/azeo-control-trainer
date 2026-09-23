"""PA binding selection reuses the engineering Tag DB browser."""
from copy import deepcopy
from dataclasses import replace
import re
from types import SimpleNamespace

from PySide6.QtCore import Qt, QStringListModel
from PySide6.QtWidgets import (
    QCompleter, QDialog, QDialogButtonBox, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)
from shiboken6 import isValid

from azeo_control_trainer.core.presentation.configuration_chrome import icon
from azeo_control_trainer.core.presentation.engineering_dialog import polish_dialog
from azeo_control_trainer.core.procedures.authoring import ProcedureDraft
from azeo_control_trainer.core.presentation.memory_tag_dialog import MemoryDialog
from azeo_control_trainer.core.strategy.tagdb import EntryKind


class SymbolPicker(QDialog):
    def __init__(self, draft, database, parent=None, *, memory_only=False, tags_only=False):
        super().__init__(parent)
        self.setWindowTitle("Select a tag or memory tag")
        self.resize(860, 640)
        self.draft = ProcedureDraft(deepcopy(draft.data), dict(draft.bindings), draft.source)
        self.reference = ""
        self.database = database
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        layout.addWidget(self.tabs, 1)
        self.browser = None
        self.selected_path = ""
        if not memory_only:
            from azeo_control_trainer.core.presentation.tagdb_browser import TagDatabaseBrowser
            self.browser = TagDatabaseBrowser(store=SimpleNamespace(tagdb=database), parent=self)
            self.browser.pathActivated.connect(self.choose_tag)
            self.browser.selectedPathChanged.connect(self.select_tag)
            self.tabs.addTab(self.browser, "Project Tag DB")
        self.memory = QListWidget()
        if not tags_only:
            panel = QWidget()
            form = QVBoxLayout(panel)
            self.memory_search = QLineEdit()
            self.memory_search.setPlaceholderText("Search memory name or description…")
            self.memory_search.textChanged.connect(self.filter_memory)
            form.addWidget(self.memory_search)
            form.addWidget(QLabel("Project memory tags are shared with Control Designer. Save creates a Tag DB entry immediately.", wordWrap=True))
            form.addWidget(self.memory, 1)
            self.new_memory = QPushButton(icon("new"), "New memory tag…")
            self.new_memory.clicked.connect(self.create_memory)
            form.addWidget(self.new_memory)
            self.tabs.addTab(panel, "Memory tags")
            self.memory.itemDoubleClicked.connect(lambda *_: self.choose_memory())
        self.note = QLabel("Select a terminal/configuration parameter, shared memory tag or local variable.", wordWrap=True)
        layout.addWidget(self.note)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Insert selected")
        self.buttons.accepted.connect(self.choose)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.populate_memory()
        polish_dialog(
            self,
            title="Select a tag or memory tag",
            subtitle="Choose a typed project value for this procedure reference.",
            mark="search",
        )

    def populate_memory(self):
        self.memory.clear()
        linked = {v.get("tag_path") for v in self.draft.data.get("variables", [])}
        values = list(self.draft.data.get("variables", []))
        current = self.database.memory.read_many() if self.database.memory else {}
        if self.database.memory:
            values += [dict(spec.model_dump(), tag_path=spec.path) for spec in self.database.memory.definitions() if spec.path not in linked]
        for value in values:
            path = value.get("tag_path", "")
            shown = f"current {current.get(path, 'unavailable')}" if path else f"initial {value['value']}"
            row = QListWidgetItem(f"{value['name']} · {value.get('data_type', 'any')} · {shown}\n{path or 'Local variable'} · {value.get('description', '')}")
            row.setData(Qt.UserRole, value.get("tag_path") or value["name"])
            self.memory.addItem(row)

    def filter_memory(self, text):
        for i in range(self.memory.count()):
            row = self.memory.item(i)
            row.setHidden(text.casefold() not in row.text().casefold())

    def create_memory(self):
        self.memory_dialog = MemoryDialog(self.database.memory, self)
        self.memory_dialog.accepted.connect(self.add_memory)
        self.memory_dialog.open()

    def add_memory(self):
        self.database.reload_memory()
        self.draft.use_memory(self.memory_dialog.result_value)
        self.populate_memory()
        self.memory_search.clear()
        path = "MEMORY/" + self.memory_dialog.result_value["name"] + "/VALUE"
        for index in range(self.memory.count()):
            if self.memory.item(index).data(Qt.UserRole) == path:
                self.memory.setCurrentRow(index)
                break

    def select_tag(self, path):
        self.selected_path = path

    def choose_tag(self, path):
        entry = self.database.lookup(path)
        if entry is None or entry.kind not in {EntryKind.TERMINAL, EntryKind.PARAMETER, EntryKind.MEMORY}:
            self.note.setText("Select a terminal or configuration parameter. Modules and raw field inventory are not scalar PA bindings.")
            return
        self.reference = path
        self.accept()

    def choose_memory(self):
        item = self.memory.currentItem()
        if item and not item.isHidden():
            self.reference = item.data(Qt.UserRole)
            self.accept()

    def choose(self):
        if self.browser and self.tabs.currentWidget() is self.browser:
            self.choose_tag(self.selected_path)
        else:
            self.choose_memory()


def resolve_symbol(draft, database, reference, *, variable=True):
    if reference in {v["name"] for v in draft.data.get("variables", [])} or reference in draft.bindings:
        return reference
    entry = database.lookup(reference)
    if entry is None or entry.kind not in {EntryKind.TERMINAL, EntryKind.PARAMETER, EntryKind.MEMORY}:
        raise ValueError("Select a configured Tag DB parameter")
    if entry.kind == EntryKind.MEMORY and variable:
        spec = next(spec for spec in database.memory.definitions() if spec.path == reference)
        return draft.use_memory(spec.model_dump())
    if entry.name == "MODE" and entry.kind == EntryKind.TERMINAL:
        entry = replace(entry, path=entry.path + ".ACTUAL", data_type="str")
    return draft.bind_parameter(entry)


def attach_symbols(field, key, host):
    """An expression keeps its surrounding text when a picked symbol is inserted."""
    memory_only, tags_only = key == "variable", key == "tag"
    def symbols():
        values = [] if tags_only else [v["name"] for v in host.draft.data.get("variables", [])]
        if not tags_only and host.tag_database.memory:
            values += [spec.path for spec in host.tag_database.memory.definitions()]
        if not memory_only:
            values += list(host.draft.bindings)
            values += [f"{e.path} · {e.description}" for e in host.tag_database
                       if e.kind in {EntryKind.TERMINAL, EntryKind.PARAMETER, EntryKind.MEMORY}]
        return values

    # A worksheet can contain dozens of expressions over 30,000 project
    # parameters. Share the catalog model instead of copying it into every cell.
    if not hasattr(host, "_symbol_models"):
        host._symbol_models = {}
    model_key = (memory_only, tags_only)
    if model_key not in host._symbol_models:
        host._symbol_models[model_key] = (None, QStringListModel(host))

    def refresh_model():
        stamp = (id(host.tag_database), tuple(v["name"] for v in host.draft.data.get("variables", [])),
                 tuple(host.draft.bindings))
        previous, model = host._symbol_models[model_key]
        if stamp != previous:
            model.setStringList(symbols())
            host._symbol_models[model_key] = (stamp, model)
        return model

    model = refresh_model()
    completer = QCompleter(model, field)
    completer.setWidget(field)
    completer.setCaseSensitivity(Qt.CaseInsensitive)
    completer.setFilterMode(Qt.MatchContains)
    completer.setCompletionMode(QCompleter.PopupCompletion)
    field._symbol_model, field._symbol_completer = model, completer

    def insert(reference, draft=None):
        if not isValid(field):
            return
        host.insert_symbol(field, key, reference.split(" · ", 1)[0], draft)

    def complete(_text):
        refresh_model()
        prefix = re.search(r"[A-Za-z_][\w./-]*$", field.text()[:field.cursorPosition()])
        if prefix and len(prefix.group()) >= 2:
            completer.setCompletionPrefix(prefix.group())
            completer.complete()

    def browse():
        host.symbol_picker = SymbolPicker(host.draft, host.tag_database, host,
                                          memory_only=memory_only, tags_only=tags_only)
        picker = host.symbol_picker
        picker.accepted.connect(lambda: insert(picker.reference, picker.draft))
        picker.open()

    field.textEdited.connect(complete)
    completer.activated[str].connect(insert)
    action = field.addAction(icon("search"), QLineEdit.TrailingPosition)
    action.setToolTip("Search Tag DB / PA memory")
    action.triggered.connect(browse)
    field._browse_symbol = browse


def insert_reference(field, key, token):
    if key in {"tag", "variable"}:
        field.setText(token)
    else:
        prefix = re.search(r"[A-Za-z_][\w./-]*$", field.text()[:field.cursorPosition()])
        if not field.hasSelectedText() and prefix:
            field.setSelection(prefix.start(), len(prefix.group()))
        field.insert(token)
