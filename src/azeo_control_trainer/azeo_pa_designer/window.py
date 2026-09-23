"""Procedure authoring with the suite's library, selection and inspector pattern."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QStringListModel, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QCompleter, QDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QStyledItemDelegate, QTableWidget, QTableWidgetItem, QTabWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)
from azeo_control_trainer.core.pa_designer.core.validator import validate_procedure_contract
from azeo_control_trainer.core.pa_designer.core.yaml_loader import load_bounded_yaml_file

from azeo_control_trainer.config.applications import application
from azeo_control_trainer.core.presentation.authoring_controls import AuthoringComboBox
from azeo_control_trainer.core.presentation.configuration_chrome import command_bar, icon
from azeo_control_trainer.core.presentation.dialog_layout import fit_dialog_to_screen
from azeo_control_trainer.core.presentation.engineering_dialog import ENGINEERING_QSS
from azeo_control_trainer.core.presentation.headless import is_headless
from azeo_control_trainer.core.presentation.menu_style import studio_menu
from azeo_control_trainer.core.presentation.property_group import PropertyGroup
from azeo_control_trainer.core.procedures.authoring import ProcedureDraft, library_documents
from azeo_control_trainer.core.procedures.library import (
    block_for_step,
    block_for_token,
    block_library,
    core_block_library,
    parameter_specs,
)
from azeo_control_trainer.core.strategy.tagdb import EntryKind, TagDatabase
from .canvas import BlockPalette, ProcedureCanvas, START, END
from .editing import BlockEditing, guarded
from .status import ProcedureStatusBar
from .problems import ProblemsPanel, collect_findings


STEP_TYPES = {block.step_type: block.label for block in core_block_library()}


def scalar(text):
    try:
        return json.loads(text)
    except ValueError:
        return text


def value_text(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


class MappingDelegate(QStyledItemDelegate):
    """Complete against the existing engineering catalog without copying its browser."""
    def __init__(self, parent, paths):
        super().__init__(parent)
        self.paths = paths

    def createEditor(self, parent, option, index):  # noqa: N802
        editor = QLineEdit(parent)
        completer = QCompleter(self.paths, editor)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        editor.setCompleter(completer)
        editor._completer = completer
        return editor


class PADesignerWindow(QMainWindow, BlockEditing):
    def __init__(self, project_dir, parent=None, *, graphs_provider=None):
        super().__init__(parent)
        from azeo_control_trainer.core.presentation.app_icon import get_app_icon
        self.setWindowIcon(get_app_icon(application_id="pa_designer"))
        self.setWindowFlag(Qt.Window, True)
        self.project_dir = Path(project_dir).resolve()
        self.library = self.project_dir / "procedures"
        self.graphs_provider = graphs_provider
        self.draft = ProcedureDraft.new()
        self._saved = self.snapshot()
        self._undo, self._redo = [], []
        self._loading = False
        self._step_row = 0
        self._catalog_paths = None
        self.tag_database = TagDatabase(self.project_dir.name)
        self._coalesce_edit = False
        self.path_model = QStringListModel(self)
        self.fields = {}
        self.resize(1240, 800)
        self.setStyleSheet(ENGINEERING_QSS)

        body = QWidget(self)
        self.setCentralWidget(body)
        root = QVBoxLayout(body)
        root.setContentsMargins(6, 0, 6, 0)
        root.setSpacing(4)
        self.status = ProcedureStatusBar(self)
        self.setStatusBar(self.status)
        self.review = QPlainTextEdit()
        self.review.setReadOnly(True)
        self.problems = ProblemsPanel()
        self.problems.finding_activated.connect(self.navigate_to_finding)
        self.split = QSplitter()
        library_panel = QWidget()
        library_layout = QVBoxLayout(library_panel)
        library_layout.setContentsMargins(4, 4, 4, 0)
        library_heading = QHBoxLayout()
        project_label = QLabel("Project procedures")
        project_label.setTextFormat(Qt.PlainText)
        project_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        project_label.setToolTip(str(self.library))
        library_heading.addWidget(project_label, 1)
        from azeo_control_trainer.core.presentation.ribbon import SmallRibbonButton
        self.refresh_library_button = SmallRibbonButton("Refresh", "restore", "Refresh project procedures (F5 also refreshes tags)")
        self.refresh_library_button.clicked.connect(self.refresh_library)
        library_heading.addWidget(self.refresh_library_button)
        library_layout.addLayout(library_heading)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a procedure…")
        self.search.textChanged.connect(self.filter_library)
        library_layout.addWidget(self.search)
        self.library_list = QListWidget()
        self.library_list.setAccessibleName("Saved project procedures")
        self.library_list.setMinimumWidth(160)
        self.library_list.setWordWrap(True)
        self.library_list.setSpacing(4)
        self.library_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.library_list.itemDoubleClicked.connect(self.open_selected)
        library_layout.addWidget(self.library_list, 1)
        self.library_empty = QLabel("", wordWrap=True)
        self.library_empty.setTextFormat(Qt.PlainText)
        self.library_empty.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.library_empty.setContentsMargins(8, 12, 8, 0)
        library_layout.addWidget(self.library_empty, 1)
        self.library_summary = QLabel("")
        self._library_errors = []
        library_layout.addWidget(self.library_summary)
        self.open_button = QPushButton("Open selected")
        self.open_button.clicked.connect(self.open_selected)
        self.open_button.setEnabled(False)
        self.library_list.itemSelectionChanged.connect(self.update_library_actions)
        library_layout.addWidget(self.open_button)
        self.library_tabs = QTabWidget()
        self.library_tabs.setMinimumWidth(200)
        self.library_tabs.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.library_tabs.setDocumentMode(True)
        self.library_tabs.addTab(library_panel, "Procedures")
        self._build_block_library()
        self.split.addWidget(self.library_tabs)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        for tabs in (self.tabs, self.library_tabs):
            tabs.setStyleSheet("QTabBar::tab { padding: 4px 10px; }")
        self.split.addWidget(self.tabs)
        self._build_steps()
        self._build_properties()
        self._build_tags()
        self._build_variables()
        self.tabs.addTab(self.review, "Review")
        self.problems_index = self.tabs.addTab(self.problems, "Problems")
        self.split.setStretchFactor(0, 0)
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([225, 1000])
        root.addWidget(self.split, 1)
        self._build_menus()
        from .ribbon import ProcedureRibbon
        self.ribbon = ProcedureRibbon(self)
        root.insertWidget(0, self.ribbon)
        self.workflow_views.currentChanged.connect(self._sync_steps)
        self._library_refresh = QTimer(self)
        self._library_refresh.setSingleShot(True)
        self._library_refresh.timeout.connect(self.refresh_library)
        self._problems_refresh = QTimer(self)
        self._problems_refresh.setSingleShot(True)
        self._problems_refresh.setInterval(220)
        self._problems_refresh.timeout.connect(self.update_problems)
        self.render()
        self.status.setText("New advisory procedure · Edit the steps, then validate and save a revision.")
        self.refresh()

    def _build_block_library(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.block_search = QLineEdit()
        self.block_search.setPlaceholderText("Find a block…")
        self.block_search.setAccessibleName("Find a procedure block")
        self.block_search.textChanged.connect(self.filter_blocks)
        layout.addWidget(self.block_search)
        self.block_tree = BlockPalette()
        self.block_tree.setHeaderHidden(True)
        self.block_tree.setAccessibleName("Procedure block library")
        self.block_tree.setUniformRowHeights(True)
        self.block_tree.setIndentation(14)
        self.block_tree.setColumnCount(1)
        categories = {}
        for block in block_library():
            category = categories.get(block.category)
            if category is None:
                category = QTreeWidgetItem(self.block_tree, [block.category])
                categories[block.category] = category
            item = QTreeWidgetItem(category, [block.label])
            item.setData(0, Qt.UserRole, block.block_id)
            item.setIcon(0, icon(block.icon))
            item.setToolTip(0, f"{block.description}\n{block.block_id} · {block.version}")
        self.block_tree.expandAll()
        self.block_tree.currentItemChanged.connect(self.select_block)
        self.block_tree.itemDoubleClicked.connect(self.add_selected_block)
        layout.addWidget(self.block_tree, 1)
        self.block_description = QLabel("Select a block to see its behavior.", wordWrap=True)
        self.block_description.setTextFormat(Qt.PlainText)
        layout.addWidget(self.block_description)
        self.add_block_button = QPushButton(icon("new"), "Add selected block")
        self.add_block_button.setEnabled(False)
        self.add_block_button.clicked.connect(self.add_selected_block)
        layout.addWidget(self.add_block_button)
        self.library_tabs.addTab(panel, "Blocks")

    @guarded
    def filter_blocks(self, *_):
        query = self.block_search.text().casefold()
        for row in range(self.block_tree.topLevelItemCount()):
            category = self.block_tree.topLevelItem(row)
            for index in range(category.childCount()):
                item = category.child(index)
                block = block_for_token(item.data(0, Qt.UserRole))
                searchable = (
                    f"{block.label} {block.category} {block.description} "
                    f"{block.step_type} {block.block_id} {block.source_id}"
                )
                item.setHidden(query not in searchable.casefold())
            category.setHidden(all(category.child(i).isHidden() for i in range(category.childCount())))
        self.select_block(self.block_tree.currentItem())

    @guarded
    def select_block(self, item, *_):
        token = item.data(0, Qt.UserRole) if item and not item.isHidden() else None
        self.add_block_button.setEnabled(bool(token))
        self.block_description.setText(block_for_token(token).description if token else "Select a block to see its behavior.")

    @guarded
    def add_selected_block(self, *_):
        item = self.block_tree.currentItem()
        if item and not item.isHidden() and item.data(0, Qt.UserRole):
            self.add_type.setCurrentIndex(self.add_type.findData(item.data(0, Qt.UserRole)))
            self.add_step()
            self.tabs.setCurrentIndex(0)

    def _build_menus(self):
        self.menuBar().setNativeMenuBar(False)
        menus = {}
        self.menu_actions = {}
        for title in ("File", "Edit", "Procedure", "View", "Help"):
            menu = studio_menu(parent=self)
            menu.setTitle("&" + title)
            self.menuBar().addMenu(menu)
            menus[title] = menu
        for title, label, callback, mark, shortcut in (
            ("File", "New procedure", self.new_procedure, "new", "Ctrl+N"),
            ("File", "Open selected procedure", self.open_selected, "open", "Ctrl+O"),
            ("File", "Save revision", self.save_revision, "save", "Ctrl+S"),
            ("File", "Export procedure document", self.export_document, "datalog", "Ctrl+Shift+E"),
            ("File", "Close", self.close, "deactivate", "Ctrl+W"),
            ("Edit", "Undo", self.undo, "undo", "Ctrl+Z"),
            ("Edit", "Redo", self.redo, "redo", "Ctrl+Y"),
            ("Procedure", "Rename symbol / Find usages", self.open_refactor, "properties", "Ctrl+Shift+R"),
            ("Procedure", "Extract reusable procedure", self.extract_reusable, "templates", "Ctrl+Shift+X"),
            ("Procedure", "Compare revisions", self.compare_revisions, "compare", "Ctrl+Shift+D"),
            ("Procedure", "Add engineering annotation", self.add_annotation, "comment", "Ctrl+Shift+N"),
            ("Procedure", "Review and release", self.review_and_release, "compile", "Ctrl+Shift+G"),
            ("View", "Run isolated trial", self.run_isolated_trial, "activate", "F6"),
            ("View", "Validate procedure", self.validate_document, "compile", "F7"),
            ("View", "Refresh library and tags", self.refresh, "restore", "F5"),
            ("Help", "Procedure authoring help", self.show_help, "comment", "F1"),
        ):
            action = QAction(icon(mark), label, self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
            menus[title].addAction(action)
            self.menu_actions[label] = action
            if label == "Undo":
                self.undo_action = action
            elif label == "Redo":
                self.redo_action = action
        self.install_block_commands(menus["Edit"])
        from azeo_control_trainer.core.presentation.product_help import add_help_action
        add_help_action(menus["Help"], self)
        self.duplicate_action = self.block_actions["duplicate"]
        for key, title in (("getting_started", "Getting started"), ("workflow", "Visual workflow"),
                           ("properties", "Block properties"), ("tags", "Tag mappings and variables"),
                           ("engineering", "Engineering productivity and release"),
                           ("timing", "Timing and conditions"), ("validation", "Validation and revisions"),
                           ("running", "Operator workflow"), ("blocks", "Block reference"),
                           ("troubleshooting", "Troubleshooting"), ("shortcuts", "Keyboard shortcuts")):
            menus["Help"].addAction(title).triggered.connect(
                lambda _checked=False, k=key: self.open_help_topic(k))

    @staticmethod
    def _table(labels):
        table = QTableWidget(0, len(labels))
        table.setHorizontalHeaderLabels(labels)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(34)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        return table

    @staticmethod
    def _scroll(widget):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        return scroll

    def _build_steps(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 4, 0, 0)
        self.add_type = AuthoringComboBox()
        for block in block_library():
            self.add_type.addItem(icon(block.icon), block.label, block.block_id)
        self.step_buttons = {}
        self.step_split = QSplitter()
        self.workflow_views = QTabWidget()
        self.workflow_views.setStyleSheet("QTabBar::tab { padding: 4px 10px; }")
        canvas_panel = QWidget()
        canvas_layout = QVBoxLayout(canvas_panel)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        self.canvas = ProcedureCanvas()
        self.align_action = QAction("Align selected left", self)
        self.align_action.triggered.connect(self.canvas.align_selected)
        self.align_action.setEnabled(False)
        self.canvas.scene.selectionChanged.connect(self.update_canvas_actions)
        canvas_layout.addWidget(self.canvas, 1)
        self.canvas.setWhatsThis("Drag blocks onto the canvas or a wire. Connect output → input to reorder a linear procedure or add an advanced path. Configure its condition/outcome in Connections. Ctrl+wheel zooms; middle-drag pans. F1 opens block help.")
        self.canvas.zoom_changed.connect(self.status.set_zoom)
        self.status.zoom.clicked.connect(self.canvas.zoom_reset)
        self.status.fit.clicked.connect(self.canvas.zoom_fit)
        self.workflow_views.addTab(canvas_panel, "Workflow")
        self.steps = self._table(["Step", "Action", "Operator instruction"])
        self.steps.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.steps.setColumnWidth(0, 90)
        self.steps.setColumnWidth(1, 150)
        self.steps.currentCellChanged.connect(self.select_step)
        self.workflow_views.addTab(self.steps, "Step list")
        from .flow_editor import WorkflowEditor
        self.flow_editor = WorkflowEditor(self)
        self.workflow_views.addTab(self.flow_editor, "Connections")
        self.step_split.addWidget(self.workflow_views)
        self.canvas.step_selected.connect(self.select_canvas_step)
        self.canvas.layout_changed.connect(self.save_canvas_layout)
        self.canvas.block_drop_requested.connect(self.drop_canvas_block)
        self.canvas.connection_requested.connect(self.connect_canvas_steps)
        self.canvas.connection_route_changed.connect(self.save_canvas_route)
        self.canvas.connection_edit_requested.connect(self.reset_canvas_route)
        self.canvas.step_command_requested.connect(self.canvas_step_command)
        self.canvas.ui_error.connect(self.canvas_error)
        self.canvas.annotation_geometry_changed.connect(self.update_annotation_geometry)
        self.canvas.annotation_edit_requested.connect(self.edit_annotation)
        self._canvas_recovery = QTimer(self)
        self._canvas_recovery.setSingleShot(True)
        self._canvas_recovery.timeout.connect(self.recover_canvas)
        self.inspector = self._scroll(QWidget())
        self.inspector.setMinimumWidth(240)
        self.step_split.addWidget(self.inspector)
        self.step_split.setStretchFactor(0, 1)
        self.step_split.setStretchFactor(1, 0)
        self.step_split.setSizes([850, 320])
        layout.addWidget(self.step_split, 1)
        self.tabs.addTab(panel, "Steps")

    @guarded
    def focus_block_library(self, *_):
        self.tabs.setCurrentIndex(0)
        self.library_tabs.setCurrentIndex(1)
        self.block_search.setFocus()

    @guarded
    def focus_library_block(self, kind):
        """Reveal an Explorer selection without replacing the current draft."""
        block = block_for_token(kind)
        self.focus_block_library()
        self.block_search.clear()
        for row in range(self.block_tree.topLevelItemCount()):
            category = self.block_tree.topLevelItem(row)
            for index in range(category.childCount()):
                item = category.child(index)
                if item.data(0, Qt.UserRole) == block.block_id:
                    category.setExpanded(True)
                    self.block_tree.setCurrentItem(item)
                    self.block_tree.scrollToItem(item)
                    self.block_tree.setFocus()
                    return True
        return False

    @guarded
    def show_workflow_view(self, index):
        self.tabs.setCurrentIndex(0)
        self.workflow_views.setCurrentIndex(index)

    def _build_properties(self):
        panel = QWidget()
        form = QFormLayout(panel)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.properties = {}
        for key, label in (("procedure_id", "Procedure ID"), ("name", "Name"), ("unit", "Unit / equipment"),
                           ("description", "Description"), ("version", "Version"), ("author", "Author"),
                           ("owner", "Owner"), ("revision_note", "Revision note")):
            field = QLineEdit()
            field.setAccessibleName(label)
            self.properties[key] = field
            form.addRow(label, field)
            field.textChanged.connect(lambda value, k=key: self.edit_property(k, value))
        note = QLabel("Manual start · Linear steps · Advisory operation\nSaved changes create a new draft revision in this project's library.")
        note.setWordWrap(True)
        form.addRow(note)
        self.tabs.addTab(self._scroll(panel), "Procedure")

    def _build_tags(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        note = QLabel("Declare logical tags used by steps. In Project parameter, type part of a module or parameter name and select a match.")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = command_bar(panel, (("Add tag", self.add_tag, "new", False),
                                      ("Remove tag", self.remove_tag, "delete", False)))
        self.remove_tag_button = buttons.buttons["Remove tag"]
        self.remove_tag_button.setEnabled(False)
        layout.addWidget(buttons)
        self.tags = self._table(["Logical tag", "Type", "Access", "Project parameter", "Description"])
        self.tags.setColumnWidth(0, 140)
        self.tags.setColumnWidth(1, 120)
        self.tags.setColumnWidth(2, 140)
        self.tags.setColumnWidth(3, 270)
        self.mapping_delegate = MappingDelegate(self.tags, self.path_model)
        self.tags.setItemDelegateForColumn(3, self.mapping_delegate)
        self.tags.cellChanged.connect(self.edit_tag)
        self.tags.itemSelectionChanged.connect(lambda: self.remove_tag_button.setEnabled(self.tags.currentRow() >= 0))
        layout.addWidget(self.tags, 1)
        self.catalog_status = QLabel()
        self.catalog_status.setWordWrap(True)
        layout.addWidget(self.catalog_status)
        self.tabs.addTab(panel, "Tag mappings")

    def _build_variables(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        note = QLabel("Memory tags are saved immediately in the project Tag DB and shared with Control Designer. Removing a PA link does not delete the tag. Local variables reset on each run.")
        note.setWordWrap(True)
        note.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(note)
        commands = command_bar(panel, (("New memory…", self.new_memory, "new", False),
                                       ("Remove variable", self.remove_variable, "delete", False)),
                               more=(("Add local variable", self.add_variable, "new"),))
        self.remove_variable_button = commands.buttons["Remove variable"]
        self.remove_variable_button.setEnabled(False)
        layout.addWidget(commands)
        self.variables = self._table(["Name", "Initial value", "Description", "Type", "Minimum", "Maximum", "Tag DB / scope", "Unit", "Operator tuning"])
        self.variables.cellChanged.connect(self.edit_variable)
        self.variables.itemSelectionChanged.connect(
            lambda: self.remove_variable_button.setEnabled(self.variables.currentRow() >= 0))
        layout.addWidget(self.variables, 1)
        self.tabs.addTab(panel, "Memory")

    def snapshot(self):
        return deepcopy((self.draft.data, self.draft.bindings))

    @property
    def dirty(self):
        return (self.draft.data, self.draft.bindings) != self._saved

    def checkpoint(self):
        self._undo.append((self.snapshot(), self._step_row))
        self._undo = self._undo[-100:]
        self._redo.clear()

    def changed(self):
        self.setWindowTitle(f"{self.draft.data.get('name') or 'Untitled'}{' *' if self.dirty else ''} — {application('pa_designer').title} · {self.project_dir.name}")
        self.undo_action.setEnabled(bool(self._undo))
        self.redo_action.setEnabled(bool(self._redo))
        self._sync_steps()
        self.status.document.setText("Modified" if self.dirty else "Saved" if self.draft.source else "Not saved")
        self.status.steps.setText(f"{len(self.draft.data['steps'])} blocks")
        self.status.setText("Unsaved changes · Validate before saving a new revision." if self.dirty else
                            (f"Saved · {self.draft.source}" if self.draft.source else "New procedure · Not yet saved."))
        self.review.setPlainText("The document has changed. Validate to review the current procedure.")
        if hasattr(self, "_problems_refresh"):
            self._problems_refresh.start()

    @guarded
    def update_problems(self):
        findings = collect_findings(self.draft, self._catalog_paths)
        self.problems.set_findings(findings)
        errors = sum(row.severity == "error" for row in findings)
        warnings = sum(row.severity == "warning" for row in findings)
        self.tabs.setTabText(self.problems_index, f"Problems ({errors + warnings})" if findings else "Problems")
        return findings

    @guarded
    def navigate_to_finding(self, finding):
        if finding.location_kind == "step":
            self.tabs.setCurrentIndex(0)
            self.workflow_views.setCurrentIndex(0)
            self.select_canvas_step(finding.location_id)
            self.canvas.select_step(finding.location_id, reveal=True)
            if finding.property_name in self.fields:
                self.fields[finding.property_name].setFocus()
        elif finding.location_kind == "flow":
            self.tabs.setCurrentIndex(0)
            self.flow_editor.locate_node(finding.location_id)
        elif finding.location_kind == "edge" and "->" in finding.location_id:
            self.tabs.setCurrentIndex(0)
            self.flow_editor.locate_edge(*finding.location_id.split("->", 1))
        elif finding.location_kind == "tag":
            self.tabs.setCurrentIndex(2)
            for row, tag in enumerate(self.draft.data.get("tags", [])):
                if tag.get("tag") == finding.location_id:
                    self.tags.selectRow(row)
                    self.tags.scrollToItem(self.tags.item(row, 0))
                    break
        else:
            self.tabs.setCurrentIndex(1)

    @guarded
    def undo(self, *_):
        if self._undo:
            self._redo.append((self.snapshot(), self._step_row))
            (self.draft.data, self.draft.bindings), self._step_row = self._undo.pop()
            self.render()

    @guarded
    def redo(self, *_):
        if self._redo:
            self._undo.append((self.snapshot(), self._step_row))
            (self.draft.data, self.draft.bindings), self._step_row = self._redo.pop()
            self.render()

    @guarded
    def render(self):
        self._loading = True
        try:
            from .flow_editor import reconcile_actions
            reconcile_actions(self.draft.data)
            for key, field in self.properties.items():
                container = self.draft.data if key in {"procedure_id", "name", "unit", "description"} else self.draft.data.get("metadata", {})
                field.setText(str(container.get(key, "")))
            self.steps.setRowCount(len(self.draft.data["steps"]))
            for row, step in enumerate(self.draft.data["steps"]):
                self._fill_step_row(row, step)
            self._step_row = min(max(0, self._step_row), len(self.draft.data["steps"]) - 1)
            self.steps.selectRow(self._step_row)
            self.tags.setRowCount(len(self.draft.data.get("tags", [])))
            for row, tag in enumerate(self.draft.data.get("tags", [])):
                for col, value in enumerate((tag["tag"], tag.get("data_type", "any"), tag.get("access", "read_write"),
                                             self.draft.bindings.get(tag["tag"], ""), tag.get("description", ""))):
                    self.tags.setItem(row, col, QTableWidgetItem(str(value)))
                for col, key, options in ((1, "data_type", ("float", "int", "bool", "str", "any")),
                                          (2, "access", ("read", "read_write", "write"))):
                    combo = AuthoringComboBox()
                    combo.addItems(options)
                    combo.setCurrentText(tag.get(key, options[0]))
                    combo.currentTextChanged.connect(lambda value, r=row, k=key: self.edit_tag_choice(r, k, value))
                    self.tags.setCellWidget(row, col, combo)
            self.variables.setRowCount(len(self.draft.data.get("variables", [])))
            for row, variable in enumerate(self.draft.data.get("variables", [])):
                for col, value in enumerate((variable["name"], value_text(variable["value"]), variable.get("description", ""),
                        variable.get("data_type", "any"),
                        "" if variable.get("min_value") is None else str(variable["min_value"]),
                        "" if variable.get("max_value") is None else str(variable["max_value"]),
                        variable.get("tag_path") or "Local variable", variable.get("engineering_units", ""))):
                    item = QTableWidgetItem(value)
                    if (variable.get("tag_path") and col < 7) or col == 6:
                        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    self.variables.setItem(row, col, item)
                dtype = AuthoringComboBox()
                dtype.addItems(["any", "float", "int", "bool", "str"])
                dtype.setCurrentText(variable.get("data_type", "any"))
                dtype.setEnabled(not variable.get("tag_path"))
                dtype.currentTextChanged.connect(lambda value, r=row: self.edit_memory_type(r, value))
                self.variables.setCellWidget(row, 3, dtype)
                tuning = AuthoringComboBox()
                for label, key in (("Not exposed", "none"), ("Next run", "next_run"), ("Live / next run", "live")):
                    tuning.addItem(label, key)
                tuning.setCurrentIndex(tuning.findData(variable.get("operator_tuning", "none")))
                tuning.currentIndexChanged.connect(lambda _index, r=row, box=tuning: self.edit_tuning_access(r, box.currentData()))
                self.variables.setCellWidget(row, 8, tuning)
            self.render_inspector()
            self.canvas.sync(self.draft.data["steps"], self.draft.data.get("metadata", {}), self.draft.data.get("flow"))
            self.flow_editor.sync()
            if self._step_row >= 0:
                self.canvas.select_step(self.draft.data["steps"][self._step_row]["id"], reveal=True)
        finally:
            self._loading = False
        self.changed()

    def _fill_step_row(self, row, step):
        for col, value in enumerate((step.get("id", ""), block_for_step(step).label,
                                     step.get("description") or step.get("comment_prompt") or step.get("condition", ""))):
            self.steps.setItem(row, col, QTableWidgetItem(str(value)))

    def _sync_steps(self):
        selected = bool(self.selected_step_ids())
        for label in ("Delete Block", "Duplicate"):
            self.step_buttons[label].setEnabled(selected)
        self.duplicate_action.setEnabled(selected)
        self.step_buttons["Move up"].setEnabled(self._step_row > 0)
        self.step_buttons["Move down"].setEnabled(selected and self._step_row < len(self.draft.data["steps"]) - 1)
        self.update_block_actions()
        if hasattr(self, "menu_actions"):
            self.menu_actions["Extract reusable procedure"].setEnabled(
                selected and not bool(self.draft.data.get("flow"))
            )
        self.status.set_selection(self.selected_step_ids())

    @guarded
    def select_step(self, row, *_):
        if self._loading or row < 0:
            return
        self._step_row = row
        self.render_inspector()
        self.canvas.select_step(self.draft.data["steps"][row]["id"])
        self._sync_steps()

    @guarded
    def select_canvas_step(self, step_id):
        row = next((i for i, step in enumerate(self.draft.data["steps"]) if step["id"] == step_id), None)
        if row is None:
            self._step_row = -1
            self.steps.clearSelection()
            self.render_inspector()
            self._sync_steps()
            return
        if row is not None and row != self._step_row:
            self._loading = True
            self.steps.selectRow(row)
            self._loading = False
            self._step_row = row
            self.render_inspector()
            self._sync_steps()

    def update_canvas_actions(self):
        if getattr(self, "_closing", False):
            return
        self.align_action.setEnabled(sum(hasattr(item, "node_id") for item in self.canvas.scene.selectedItems()) > 1)
        self.update_block_actions()
        self.status.set_selection(self.selected_step_ids())

    @guarded
    def save_canvas_layout(self, rows):
        if self.draft.data.get("metadata", {}).get("canvas_layout") == rows:
            return
        self.checkpoint()
        self.draft.data.setdefault("metadata", {})["canvas_layout"] = rows
        self.changed()

    @guarded
    def save_canvas_route(self, source, target, points):
        self.checkpoint()
        metadata = self.draft.data.setdefault("metadata", {})
        rows = [row for row in metadata.get("canvas_routes", []) if (row["source"], row["target"]) != (source, target)]
        if points:
            rows.append({"source": source, "target": target, "points": points})
        metadata["canvas_routes"] = rows
        self.changed()

    @guarded
    def reset_canvas_route(self, source, target):
        self.save_canvas_route(source, target, [])
        self.canvas.edges[(source, target)].reset_route()
        self.status.setText("Connection returned to automatic routing.")

    @guarded
    def drop_canvas_block(self, kind, point, pair):
        # Accept pre-catalog drag payloads that contained only the primitive
        # step type, then normalize to the versioned combo identity.
        kind = block_for_token(kind).block_id
        steps = self.draft.data["steps"]
        flow = self.draft.data.get("flow")
        if flow:
            self._step_row = max(-1, len(steps) - 2)
        elif pair and pair[0] != START:
            self._step_row = next(i for i, step in enumerate(steps) if self.canvas.key(step["id"]) == pair[0])
        elif pair:
            self._step_row = -1
        self.add_type.setCurrentIndex(self.add_type.findData(kind))
        self.add_step()
        if flow and pair:
            source, target = (self.canvas.flow_ids.get(key, key) for key in pair)
            edge = next((e for e in flow["edges"] if (e["source"], e["target"]) == (source, target)), None)
            if edge:
                identity = self.canvas.key(steps[self._step_row]["id"])
                edge["target"] = identity
                flow["edges"].append({"source": identity, "target": target})
                self.canvas.sync(steps, self.draft.data.get("metadata", {}), flow)
                self.flow_editor.sync()
        node = self.canvas.nodes[self.canvas.key(steps[self._step_row]["id"])]
        node.setPos(point.x() - node.rect().width() / 2, point.y() - node.rect().height() / 2)
        # Insertion and its drop position are one undoable document command.
        self.draft.data.setdefault("metadata", {})["canvas_layout"] = self.canvas.current_layout()
        self.canvas._node_position_changed = False
        self.changed()
        self.workflow_views.setCurrentIndex(0)

    @guarded
    def connect_canvas_steps(self, source, target):
        if self.draft.data.get("flow"):
            self.flow_editor.add_edge(source, target)
            return
        steps = self.draft.data["steps"]
        keys = [self.canvas.key(step["id"]) for step in steps]
        if target == END:
            if source == keys[-1]:
                return
            raise ValueError("End follows the final step. Connect to a block input to change execution order.")
        if target not in keys or source not in [START, *keys]:
            raise ValueError("Select two existing procedure ports")
        index = keys.index(target)
        if steps[index]["type"] == "complete" and source != ([START, *keys][index]):
            raise ValueError("Complete stays last. Reorder the preceding actions instead.")
        if source != START and steps[keys.index(source)]["type"] == "complete":
            raise ValueError("Complete must remain the final step")
        reordered = list(steps)
        step = reordered.pop(index)
        position = 0 if source == START else next(i + 1 for i, item in enumerate(reordered) if self.canvas.key(item["id"]) == source)
        reordered.insert(position, step)
        if reordered == steps:
            return
        self.checkpoint()
        self.draft.data["steps"] = reordered
        self._step_row = position
        self._prune_canvas_metadata()
        self.render()
        self.status.setText("Execution order updated. Validate before saving the revision.")

    def _prune_canvas_metadata(self):
        if self.draft.data.get("flow"):
            from .flow_editor import reconcile_actions
            reconcile_actions(self.draft.data)
            return
        ids = {step["id"] for step in self.draft.data["steps"]}
        metadata = self.draft.data.setdefault("metadata", {})
        if "canvas_layout" in metadata:
            metadata["canvas_layout"] = [row for row in metadata["canvas_layout"] if row["step_id"] in ids | {START, END}]
        keys = [START, *(self.canvas.key(step["id"]) for step in self.draft.data["steps"]), END]
        pairs = set(zip(keys, keys[1:]))
        if "canvas_routes" in metadata:
            metadata["canvas_routes"] = [row for row in metadata["canvas_routes"] if (row["source"], row["target"]) in pairs]

    @guarded
    def canvas_step_command(self, command, step_id):
        if command == "open":
            self.select_canvas_step(step_id)
            self.block_command("open")
        elif command == "delete":
            self.delete_blocks(set(step_id.splitlines()))

    def canvas_error(self, message):
        # Restore the last document geometry after the native callback unwinds.
        self._canvas_recovery.start(0)
        self.status.setText("Canvas action cancelled: " + message)

    @guarded
    def recover_canvas(self):
        self.canvas.sync(self.draft.data["steps"], self.draft.data.get("metadata", {}), self.draft.data.get("flow"))

    def render_inspector(self):
        filter_text = self.property_filter.text() if hasattr(self, "property_filter") else ""
        self._property_group_collapsed = getattr(self, "_property_group_collapsed", {"Documentation": True})
        old = self.inspector.takeWidget()
        if old:
            old.deleteLater()
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setAlignment(Qt.AlignTop)
        self.fields = {}
        self.property_groups = {}
        self.property_rows = {}
        self.property_filter = QLineEdit(filter_text)
        self.property_filter.setPlaceholderText("Filter parameters…")
        self.property_filter.setAccessibleName("Filter block parameters")
        self.property_filter.setClearButtonEnabled(True)
        if self._step_row < 0:
            layout.addWidget(QLabel("Add a step to begin."))
            self.inspector.setWidget(panel)
            return
        step = self.draft.data["steps"][self._step_row]
        kind = step["type"]
        block = block_for_step(step)
        title = QLabel(f"Properties · {block.label}")
        title.setTextFormat(Qt.PlainText)
        title.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(self.property_filter)

        def group_form(name):
            if name not in self.property_groups:
                group = PropertyGroup(name)
                group.set_collapsed(self._property_group_collapsed.get(name, False))
                group._btn.clicked.connect(lambda _checked=False, n=name, g=group:
                    self._property_group_collapsed.update({n: g.is_collapsed()}))
                form = group.form()
                form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
                form.setRowWrapPolicy(QFormLayout.WrapAllRows)
                form.setLabelAlignment(Qt.AlignLeft)
                layout.addWidget(group)
                self.property_groups[name] = group
            return self.property_groups[name].form()

        form = group_form("Quick Configuration")
        self.block_identity = QLineEdit()
        self.block_identity.setReadOnly(True)
        self.block_identity.setText(
            f"{step['library_block_id']} · {step.get('library_block_version') or 'version unspecified'}"
            if step.get("library_block_id") else "Custom / legacy step")
        self.block_identity.setCursorPosition(0)
        self.block_identity.setToolTip(self.block_identity.text())
        self.block_identity.setAccessibleName("Block identity and version")
        form.addRow("Library block", self.block_identity)
        self.property_rows["library_block"] = ("Quick Configuration", form, 0, "library block identity version")
        if kind == "write_tag":
            note = QLabel(block.description, wordWrap=True)
            form.addRow(note)
        specs = parameter_specs(kind, block)
        for key, label, editor_type, default in specs:
            value = step.get(key, default)
            if isinstance(editor_type, tuple):
                field = AuthoringComboBox()
                field.addItems(editor_type)
                field.setCurrentText(str(value))
                signal = field.currentTextChanged
            elif editor_type == "bool":
                field = QCheckBox()
                field.setChecked(bool(value))
                signal = field.toggled
            elif editor_type in {"multiline", "choices"}:
                field = QPlainTextEdit()
                field.setMaximumHeight(85)
                field.setPlainText("\n".join(value_text(item) for item in value) if editor_type == "choices" else str(value or ""))
                signal = field.textChanged
            else:
                field = QLineEdit("" if value is None else value_text(value))
                if key == "condition":
                    field.setPlaceholderText("e.g. LOOP.PV >= 40 and LOOP.PV <= 60")
                signal = field.textChanged
            field.setAccessibleName(label)
            type_label = ("choice" if isinstance(editor_type, tuple) else
                          {"number": "float", "optional_number": "optional float", "choices": "list",
                           "scalar": "value"}.get(editor_type, "text"))
            field.setToolTip(f"{label}\nParameter: {key} · Type: {type_label}")
            self.fields[key] = field
            if key in {"tag", "variable", "condition", "expression"}:
                derived = (key == "condition" and step.get("condition_rows") or
                           key in {"variable", "expression"} and kind == "calculate" and step.get("calculation_rows"))
                if derived:
                    field.setReadOnly(True)
                    field.setToolTip("Derived from the logic worksheet. Use Edit block logic to change its rows.")
                else:
                    from .symbols import attach_symbols
                    attach_symbols(field, key, self)
            group_name = ("Quick Configuration" if key in {"id", "description", "display_text", "operator_guidance"}
                          else "Timing" if key in {"timeout_sec", "poll_sec", "stable_for_sec", "delay_sec", "timer_tuning"}
                          else "Failure Handling" if key in {"on_failure", "on_timeout"}
                          else "Documentation" if key in {"section", "equipment", "notes"}
                          else "Action")
            form = group_form(group_name)
            row = form.rowCount()
            form.addRow(label, field)
            form.labelForField(field).setBuddy(field)
            form.labelForField(field).setToolTip(field.toolTip())
            self.property_rows[key] = (group_name, form, row, f"{label} {key} {type_label}".casefold())
            signal.connect(lambda *_args, k=key, t=editor_type, f=field: self.edit_step(k, t, f))
        if kind in {"wait_until", "check", "permissive", "watchdog", "calculate"}:
            form = group_form("Action")
            row = form.rowCount()
            self.logic_button = QPushButton(icon("params"), "Edit block logic…")
            self.logic_button.setToolTip("Multiple conditions, row hold timers, grouped AND/OR and calculation rows")
            self.logic_button.clicked.connect(self.open_logic_editor)
            form.addRow(self.logic_button)
            self.property_rows["logic_rows"] = ("Action", form, row, "conditions calculations rows logic hold timer")
        if kind == "subprocedure":
            choose = AuthoringComboBox()
            choose.addItem("Select a saved reusable revision…", "")
            for index in range(self.library_list.count()):
                item = self.library_list.item(index)
                path = Path(item.data(Qt.UserRole))
                choose.addItem(item.text(), path.relative_to(self.library).as_posix())
            choose.currentIndexChanged.connect(lambda _: self.fields["subprocedure_path"].setText(choose.currentData()) if choose.currentData() else None)
            group_form("Action").addRow("Choose revision", choose)
        self.inspector.setWidget(panel)
        self.property_filter.textChanged.connect(self.filter_properties)
        self.filter_properties()

    @guarded
    def filter_properties(self, *_):
        query = self.property_filter.text().strip().casefold()
        visible_groups = set()
        for name, form, row, search in self.property_rows.values():
            visible = not query or query in search
            form.setRowVisible(row, visible)
            if visible:
                visible_groups.add(name)
        for name, group in self.property_groups.items():
            group.setVisible(name in visible_groups)
            group.set_collapsed(False if query else self._property_group_collapsed.get(name, False))

    @guarded
    def edit_step(self, key, kind, field):
        if self._loading:
            return
        if kind == "bool":
            value = field.isChecked()
        elif isinstance(kind, tuple):
            value = field.currentText()
        else:
            value = field.toPlainText() if isinstance(field, QPlainTextEdit) else field.text()
            if kind in {"number", "optional_number"}:
                # Retain incomplete typing for validation; never replace it with a default.
                try:
                    value = float(value) if value.strip() else None
                except ValueError:
                    pass
            elif kind == "scalar":
                value = scalar(value)
            elif kind == "choices":
                value = [scalar(line) for line in value.splitlines() if line.strip()]
        if not self._coalesce_edit:
            self.checkpoint()
        old_id = self.draft.data["steps"][self._step_row]["id"]
        self.draft.data["steps"][self._step_row][key] = value
        if key == "id":
            for node in (self.draft.data.get("flow") or {}).get("nodes", []):
                if node.get("step_id") == old_id:
                    node["step_id"] = value
            metadata = self.draft.data.setdefault("metadata", {})
            for row in metadata.get("canvas_layout", []):
                if row["step_id"] == old_id:
                    row["step_id"] = value
            for row in metadata.get("canvas_routes", []):
                for end in ("source", "target"):
                    if row[end] == self.canvas.key(old_id):
                        row[end] = self.canvas.key(value)
            self.canvas.sync(self.draft.data["steps"], metadata, self.draft.data.get("flow"))
        else:
            self.canvas.update_step(self.draft.data["steps"][self._step_row], self._step_row)
        self._fill_step_row(self._step_row, self.draft.data["steps"][self._step_row])
        self.changed()

    @guarded
    def edit_property(self, key, value):
        if self._loading:
            return
        self.checkpoint()
        container = self.draft.data if key in {"procedure_id", "name", "unit", "description"} else self.draft.data.setdefault("metadata", {})
        container[key] = value
        self.changed()

    def unique_step_id(self, prefix):
        ids = {step["id"] for step in self.draft.data["steps"]}
        index = 1
        while f"{prefix}_{index}" in ids:
            index += 1
        return f"{prefix}_{index}"

    @guarded
    def add_step(self, *_):
        block = block_for_token(self.add_type.currentData())
        kind = block.step_type
        step = block.instantiate(self.unique_step_id(kind))
        self.checkpoint()
        # New actions normally precede the final completion marker.
        steps = self.draft.data["steps"]
        position = min(self._step_row + 1, len(steps) - (bool(steps) and steps[-1]["type"] == "complete"))
        position = len(steps) if kind == "complete" else max(0, position)
        steps.insert(position, step)
        self._prune_canvas_metadata()
        self._step_row = position
        self.render()

    @guarded
    def duplicate_step(self, *_):
        self.block_command("duplicate")

    @guarded
    def move_step(self, delta):
        target = self._step_row + delta
        steps = self.draft.data["steps"]
        if 0 <= target < len(steps):
            self.checkpoint()
            steps.insert(target, steps.pop(self._step_row))
            self._prune_canvas_metadata()
            self._step_row = target
            self.render()

    @guarded
    def remove_step(self, *_):
        self.block_command("delete")

    @guarded
    def add_tag(self, *_):
        self.checkpoint()
        tags = self.draft.data.setdefault("tags", [])
        number = 1
        while f"TAG{number}.PV" in {tag["tag"] for tag in tags}:
            number += 1
        tag = f"TAG{number}.PV"
        tags.append({"tag": tag, "data_type": "float", "access": "read"})
        self.draft.bindings[tag] = ""
        self.render()
        self.tags.selectRow(len(tags) - 1)

    @guarded
    def remove_tag(self, *_):
        row = self.tags.currentRow()
        if row >= 0:
            self.checkpoint()
            tag = self.draft.data["tags"].pop(row)
            self.draft.bindings.pop(tag["tag"], None)
            self.render()

    @guarded
    def edit_tag(self, row, column):
        if self._loading or column in {1, 2}:
            return
        value = self.tags.item(row, column).text()
        tag = self.draft.data["tags"][row]
        if column == 0 and value != tag["tag"]:
            old = tag["tag"]
            from .refactoring import rename_symbol
            try:
                data, bindings, usages = rename_symbol(self.draft.data, self.draft.bindings, "tag", old, value)
            except ValueError:
                self._loading = True
                self.tags.item(row, column).setText(old)
                self._loading = False
                raise
            self.checkpoint()
            self.draft.data, self.draft.bindings = data, bindings
            self.render()
            self.tags.selectRow(row)
            self.status.setText(f"Renamed tag {old} to {value} in {len(usages)} semantic usages.")
            return
        self.checkpoint()
        if column == 3:
            self.draft.bindings[tag["tag"]] = value
        elif column == 4:
            tag["description"] = value
        self.changed()

    @guarded
    def edit_tag_choice(self, row, key, value):
        if not self._loading:
            self.checkpoint()
            self.draft.data["tags"][row][key] = value
            self.changed()

    @guarded
    def add_variable(self, *_):
        self.checkpoint()
        variables = self.draft.data.setdefault("variables", [])
        number = 1
        while f"value_{number}" in {item["name"] for item in variables}:
            number += 1
        variables.append({"name": f"value_{number}", "value": 0})
        self.render()

    @guarded
    def remove_variable(self, *_):
        row = self.variables.currentRow()
        if row >= 0:
            self.checkpoint()
            self.draft.data["variables"].pop(row)
            self.render()

    @guarded
    def edit_variable(self, row, column):
        if not self._loading:
            value = self.variables.item(row, column).text()
            variable = self.draft.data["variables"][row]
            if column == 0 and value != variable["name"]:
                old = variable["name"]
                from .refactoring import rename_symbol
                try:
                    data, bindings, usages = rename_symbol(
                        self.draft.data, self.draft.bindings, "variable", old, value
                    )
                except ValueError:
                    self._loading = True
                    self.variables.item(row, column).setText(old)
                    self._loading = False
                    raise
                self.checkpoint()
                self.draft.data, self.draft.bindings = data, bindings
                self.render()
                self.variables.selectRow(row)
                self.status.setText(
                    f"Renamed variable {old} to {value} in {len(usages)} semantic usages."
                )
                return
            self.checkpoint()
            if column in {4, 5}:
                value = scalar(value) if value.strip() else None
            elif column == 1:
                value = scalar(value)
            variable[("name", "value", "description", "data_type", "min_value", "max_value", "tag_path", "engineering_units")[column]] = value
            self.changed()

    @guarded
    def edit_memory_type(self, row, value):
        if not self._loading:
            self.checkpoint()
            self.draft.data["variables"][row]["data_type"] = value
            self.changed()

    @guarded
    def edit_tuning_access(self, row, value):
        if not self._loading:
            self.checkpoint()
            self.draft.data["variables"][row]["operator_tuning"] = value
            self.changed()

    @guarded
    def new_memory(self, *_):
        from .symbols import MemoryDialog
        self.memory_dialog = MemoryDialog(self.tag_database.memory, self)
        self.memory_dialog.accepted.connect(self.accept_memory)
        self.memory_dialog.open()

    @guarded
    def accept_memory(self):
        self.checkpoint()
        self.tag_database.reload_memory()
        self.draft.use_memory(self.memory_dialog.result_value)
        self.render()

    @guarded
    def open_logic_editor(self, *_):
        from .logic_editor import LogicEditor
        self.logic_editor = LogicEditor(self)
        self.logic_editor.accepted.connect(self.accept_logic)
        self.logic_editor.open()

    @guarded
    def accept_logic(self):
        self.checkpoint()
        self.draft.data, self.draft.bindings = self.logic_editor.draft.data, self.logic_editor.draft.bindings
        self.render()

    @guarded
    def insert_symbol(self, field, key, reference, draft=None):
        from .symbols import insert_reference, resolve_symbol
        self.checkpoint()
        if draft:
            self.draft.data["variables"] = draft.data.get("variables", [])
        token = resolve_symbol(self.draft, self.tag_database, reference, variable=key != "tag")
        step = self.draft.data["steps"][self._step_row]
        if key == "variable" and step["type"] == "operator_input":
            memory = next(v for v in self.draft.data["variables"] if v["name"] == token)
            if memory.get("data_type", "any") != "any":
                step["input_type"] = memory["data_type"]
        if key == "tag" and step["type"] == "write_tag":
            next(v for v in self.draft.data["tags"] if v["tag"] == token)["access"] = "read_write"
        self._coalesce_edit = True
        try:
            insert_reference(field, key, token)
        finally:
            self._coalesce_edit = False
        self.render()

    @guarded
    def refresh_library(self, *_):
        selected = self.library_list.currentItem()
        selected_path = selected.data(Qt.UserRole) if selected else None
        self.library_list.clear()
        errors = []
        for path in library_documents(self.library):
            try:
                data = load_bounded_yaml_file(path, label="Procedure library")
                if not isinstance(data, dict) or "procedure_id" not in data or "mappings" in data:
                    continue
                try:
                    from azeo_control_trainer.core.procedures.governance import read_governance
                    state = read_governance(path)["status"].upper()
                except ValueError:
                    state = "INTEGRITY ERROR"
                item = QListWidgetItem(f"{data.get('name', path.stem)}\n{path.parent.name} · {state}")
                item.setData(Qt.UserRole, str(path))
                item.setToolTip(f"{data.get('name', path.stem)}\n{path}")
                self.library_list.addItem(item)
                if str(path) == selected_path:
                    self.library_list.setCurrentItem(item)
            except Exception as error:
                errors.append(f"{path.relative_to(self.library)}: {error}")
        self._library_errors = errors
        self.filter_library()
        if errors:
            self.status.setText(f"{len(errors)} library documents could not be read. See Review for details.")
            self.review.setPlainText("\n".join(errors))

    @guarded
    def refresh(self, *_):
        self.refresh_library()
        try:
            db = TagDatabase.from_area(self.project_dir)
            if self.graphs_provider:
                for graph in self.graphs_provider():
                    db.add_module(graph)
            self.tag_database = db
            self._catalog_paths = {entry.path for entry in db if entry.kind in {EntryKind.TERMINAL, EntryKind.PARAMETER, EntryKind.MEMORY}}
            completions = self._catalog_paths | {entry.path + suffix for entry in db
                if entry.kind == EntryKind.TERMINAL and entry.name == "MODE" for suffix in (".ACTUAL", ".TARGET")}
            self.path_model.setStringList(sorted(completions))
            self.catalog_status.setText(f"{len(self._catalog_paths):,} configured parameters · Values and live readiness are checked in Operator Station.")
        except Exception:
            self._catalog_paths = None
            self.path_model.setStringList([])
            self.catalog_status.setText("Project parameter catalog unavailable. Refresh to retry.")
            raise

    def filter_library(self, *_):
        text = self.search.text().casefold()
        for index in range(self.library_list.count()):
            item = self.library_list.item(index)
            item.setHidden(text not in item.text().casefold())
        total = self.library_list.count()
        visible = sum(not self.library_list.item(i).isHidden() for i in range(total))
        self.library_list.setVisible(bool(visible))
        self.library_empty.setVisible(not visible)
        self.library_empty.setText(
            "No matching procedures.\n\nClear or change the search to see saved revisions." if total else
            "No readable procedures.\n\nSee Review for the file errors, then Refresh to retry." if self._library_errors else
            "No saved procedures in this project.\n\nThe new procedure is a draft. Use Save revision to add it here.\n\nUse the Blocks tab to build the workflow.")
        self.library_summary.setText(f"{visible} of {total} revisions" + (
            f" · {len(self._library_errors)} unreadable" if self._library_errors else ""))
        self.library_summary.setToolTip("\n".join(self._library_errors) or str(self.library))
        self.update_library_actions()

    def update_library_actions(self):
        item = self.library_list.currentItem()
        enabled = bool(item and not item.isHidden())
        self.open_button.setEnabled(enabled)
        if hasattr(self, "menu_actions"):
            self.menu_actions["Open selected procedure"].setEnabled(enabled)

    def allow_discard(self):
        if not self.dirty:
            return True
        if is_headless():
            return False
        from .dialogs import unsaved_procedure_dialog
        result = unsaved_procedure_dialog(self).exec()
        return bool(self.save_revision()) if result == QMessageBox.Save else result == QMessageBox.Discard

    def saved_revision_paths(self, *, procedure_id=None):
        paths = []
        for path in library_documents(self.library):
            try:
                data = load_bounded_yaml_file(path, label="Procedure library")
            except Exception:
                continue
            if not isinstance(data, dict) or "procedure_id" not in data or "mappings" in data:
                continue
            if procedure_id is None or data.get("procedure_id") == procedure_id:
                paths.append(path)
        return paths

    @guarded
    def new_procedure(self, *_):
        if not self.allow_discard():
            return False
        if is_headless():
            draft = ProcedureDraft.new()
        else:
            from .new_procedure import NewProcedureDialog
            dialog = NewProcedureDialog(self)
            if dialog.exec() != QDialog.Accepted:
                return False
            draft = dialog.result_draft()
        self.draft = draft
        self._saved = self.snapshot()
        self._undo.clear()
        self._redo.clear()
        self._step_row = 0
        self.render()
        return True

    @guarded
    def open_refactor(self, *_args, kind=None, symbol=None):
        if kind is None:
            if self.tabs.currentIndex() == 2 and self.tags.currentRow() >= 0:
                kind, symbol = "tag", self.draft.data["tags"][self.tags.currentRow()]["tag"]
            elif self.tabs.currentIndex() == 3 and self.variables.currentRow() >= 0:
                kind, symbol = "variable", self.draft.data["variables"][self.variables.currentRow()]["name"]
            elif self.selected_step_ids():
                kind, symbol = "step", self.selected_step_ids()[0]
            else:
                kind, symbol = "tag", ""
        from .engineering_tools import RefactorDialog
        dialog = RefactorDialog(self.draft.data, self.draft.bindings, self, kind=kind, symbol=symbol or "")
        if is_headless():
            return dialog
        if dialog.exec() == QDialog.Accepted:
            return self.apply_symbol_rename(dialog.kind.currentText(), dialog.old.text(), dialog.new.text())
        return False

    @guarded
    def apply_symbol_rename(self, kind, old, new):
        from .refactoring import rename_symbol
        data, bindings, usages = rename_symbol(self.draft.data, self.draft.bindings, kind, old, new)
        self.checkpoint()
        self.draft.data, self.draft.bindings = data, bindings
        self.render()
        self.status.setText(f"Renamed {kind} {old} to {new} in {len(usages)} semantic usages.")
        return usages

    @guarded
    def compare_revisions(self, *_):
        from .engineering_tools import RevisionCompareDialog
        paths = self.saved_revision_paths(procedure_id=self.draft.data.get("procedure_id"))
        dialog = RevisionCompareDialog(paths, self.draft, self)
        if is_headless():
            return dialog
        dialog.exec()
        return dialog

    @guarded
    def review_and_release(self, *_):
        from .engineering_tools import GovernanceDialog
        dialog = GovernanceDialog(self.saved_revision_paths(), self)
        if is_headless():
            return dialog
        dialog.exec()
        self.refresh_library()
        return dialog

    @guarded
    def extract_reusable(self, *_args, **values):
        selected = set(self.selected_step_ids())
        if not values:
            if is_headless():
                raise ValueError("Provide reusable procedure identity when running headless")
            from .engineering_tools import ExtractProcedureDialog
            dialog = ExtractProcedureDialog(len(selected), self)
            if dialog.exec() != QDialog.Accepted:
                return False
            values = dialog.values()
        before = (self.snapshot(), self._step_row)
        from .extraction import extract_selection
        child, path, updated, call_id = extract_selection(self.draft, selected, self.project_dir, **values)
        self._undo.append(before)
        self._undo = self._undo[-100:]
        self._redo.clear()
        self.draft.data["steps"] = updated
        self._step_row = next(index for index, step in enumerate(updated) if step["id"] == call_id)
        self.render()
        self.refresh_library()
        self.status.setText(f"Created reusable revision {path.parent.name} and replaced {len(selected)} blocks with {call_id}.")
        return child, path

    @guarded
    def add_annotation(self, data=None, point=None):
        if isinstance(data, bool):
            data = None
        if data is None:
            if is_headless():
                data = {"kind": "note", "text": "Engineering note", "width": 300, "height": 120, "color": "#607D8B"}
            else:
                from .engineering_tools import AnnotationDialog
                dialog = AnnotationDialog(self)
                if dialog.exec() != QDialog.Accepted:
                    return False
                data = dialog.value()
        if point is None:
            point = self.canvas.mapToScene(self.canvas.viewport().rect().center())
        rows = self.draft.data.setdefault("metadata", {}).setdefault("annotations", [])
        used = {row["id"] for row in rows}
        number, identity = 1, "annotation_1"
        while identity in used:
            number += 1
            identity = f"annotation_{number}"
        from azeo_control_trainer.core.pa_designer.core.procedure_model import EngineeringAnnotation
        annotation = EngineeringAnnotation.model_validate(
            dict(data, id=identity, x=round(point.x()), y=round(point.y()))
        ).model_dump(mode="json")
        self.checkpoint()
        rows.append(annotation)
        self.render()
        self.canvas.annotations[identity].setSelected(True)
        return identity

    @guarded
    def update_annotation_geometry(self, identity, geometry):
        row = next(row for row in self.draft.data.get("metadata", {}).get("annotations", []) if row["id"] == identity)
        if all(row.get(key) == value for key, value in geometry.items()):
            return
        from azeo_control_trainer.core.pa_designer.core.procedure_model import EngineeringAnnotation
        updated = EngineeringAnnotation.model_validate(dict(row, **geometry)).model_dump(mode="json")
        self.checkpoint()
        row.update(updated)
        self.changed()

    @guarded
    def edit_annotation(self, identity, values=None):
        row = next(row for row in self.draft.data.get("metadata", {}).get("annotations", []) if row["id"] == identity)
        if values is None:
            if is_headless():
                return row
            from .engineering_tools import AnnotationDialog
            dialog = AnnotationDialog(self, row)
            if dialog.exec() != QDialog.Accepted:
                return False
            values = dialog.value()
        from azeo_control_trainer.core.pa_designer.core.procedure_model import EngineeringAnnotation
        updated = EngineeringAnnotation.model_validate(dict(row, **values)).model_dump(mode="json")
        self.checkpoint()
        row.update(updated)
        self.render()
        return row

    @guarded
    def remove_annotation(self, identity):
        rows = self.draft.data.get("metadata", {}).get("annotations", [])
        if not any(row["id"] == identity for row in rows):
            return False
        self.checkpoint()
        self.draft.data["metadata"]["annotations"] = [row for row in rows if row["id"] != identity]
        self.render()
        return True

    @guarded
    def open_selected(self, *_):
        item = self.library_list.currentItem()
        if item and not item.isHidden() and self.allow_discard():
            draft = ProcedureDraft.load(Path(item.data(Qt.UserRole)), self.library)
            self.draft = draft
            self._saved = self.snapshot()
            self._undo.clear()
            self._redo.clear()
            self._step_row = 0
            self.render()

    def _definition(self):
        self.draft.library = self.library
        if self._catalog_paths is None:
            raise ValueError("Project parameter catalog is unavailable. Refresh before validating or saving.")
        self.tag_database.reload_memory()
        self._catalog_paths = {p for p in self._catalog_paths if not p.startswith("MEMORY/")}
        self._catalog_paths.update(entry.path for entry in self.tag_database if entry.kind == EntryKind.MEMORY)
        definition = self.draft.definition(self._catalog_paths)
        self.draft.validate_memory(self.project_dir, definition)
        return definition

    @guarded
    def validate_document(self, *_):
        definition = self._definition()
        warnings = validate_procedure_contract(definition.procedure).warnings
        self.update_problems()
        lines = [f"{definition.procedure.name} · {definition.procedure.metadata.version}",
                 "Valid for the supervised advisory runner. Live readiness is checked at run time.", ""]
        for index, step in enumerate(definition.procedure.steps, 1):
            lines.extend((f"{index}. {step.id} — {block_for_step(step).label}",
                          step.description or step.comment_prompt or step.display_text))
            if step.condition:
                lines.append(f"Condition: {step.condition}")
            if step.stable_for_sec:
                lines.append(f"Continuous observed dwell: {step.stable_for_sec:g} simulation seconds")
        lines += ["", "Tag mappings"] + [f"{tag} → {path}" for tag, path in definition.bindings.items()]
        lines += ["", "Warnings"] + (warnings or ["None"])
        self.review.setPlainText("\n".join(lines))
        self.tabs.setCurrentWidget(self.review)
        self.status.setText(f"Validation passed · {len(definition.procedure.steps)} steps · {len(warnings)} warnings · Live readiness not tested.")
        return True

    @guarded
    def save_revision(self, *_):
        self._definition()
        path = self.draft.save_revision(self.project_dir, self._catalog_paths)
        self._saved = self.snapshot()
        self.refresh()
        self.changed()
        self.status.setText(f"Saved {path.relative_to(self.project_dir)} · In Operator Station, open Tools > Procedures > Refresh library.")
        return path

    @guarded
    def export_document(self, path=None):
        definition = self._definition()
        if isinstance(path, bool):
            path = None
        if path is None:
            if is_headless():
                raise ValueError("Specify a documentation path when running headless")
            from .dialogs import get_export_procedure_path
            path = get_export_procedure_path(
                self, f"{definition.procedure.procedure_id}-sop.html")
            if not path:
                return None
        from azeo_control_trainer.core.procedures.documentation import export_sop
        result = export_sop(definition, path)
        self.status.setText(f"Exported procedure document to {result}")
        return result

    @guarded
    def run_isolated_trial(self, inputs=None, history_path=None):
        definition = self._definition()
        if isinstance(inputs, bool):
            inputs = None
        if inputs is None:
            defaults = {tag.tag: tag.initial_value for tag in definition.procedure.tags
                        if tag.initial_value is not None}
            if is_headless():
                inputs = defaults
            else:
                from .dialogs import TrialInputsDialog
                dialog = TrialInputsDialog(json.dumps(defaults, indent=2), self)
                if dialog.exec() != TrialInputsDialog.Accepted:
                    return None
                inputs = json.loads(dialog.text_value())
        if not isinstance(inputs, dict):
            raise ValueError("Trial inputs must be a JSON object keyed by logical tag")
        from azeo_control_trainer.core.procedures.trial import run_isolated_trial
        target = Path(history_path) if history_path else self.library / "runtime" / "trials.sqlite"
        result = run_isolated_trial(definition, inputs, target)
        lines = [
            f"Isolated trial: {result.execution.status.value}",
            result.execution.message,
            "",
            "No controller or SharedDataStore output path was attached.",
            "",
            "Final logical tag values",
            *[f"{key} = {value!r}" for key, value in sorted(result.final_values.items())],
            "",
            "Internal output journal",
            *[f"{row['tag']} = {row['value']!r}" for row in result.writes],
        ]
        self.review.setPlainText("\n".join(lines))
        self.tabs.setCurrentWidget(self.review)
        self.status.setText(
            f"Isolated trial {result.execution.status.value.lower()} · "
            f"{len(result.writes)} internal outputs · no live writes"
        )
        return result

    @guarded
    def open_help_topic(self, key="getting_started"):
        from .help import ProcedureHelpCenter
        if not hasattr(self, "help_center"):
            self.help_center = ProcedureHelpCenter(self)
        self.help_center.show_topic(key)
        self.help_center.show()
        self.help_center.raise_()
        self.help_center.activateWindow()

    @guarded
    def show_help(self, *_):
        focus = self.focusWidget()
        if focus and (focus is self.block_tree or self.block_tree.isAncestorOf(focus)):
            item = self.block_tree.currentItem()
            token = item.data(0, Qt.UserRole) if item else None
            if token:
                return self.open_help_topic(block_for_token(token).help_key)
        if self.tabs.currentIndex() == 0 and self._step_row >= 0:
            return self.open_help_topic(
                block_for_step(self.draft.data["steps"][self._step_row]).help_key
            )
        self.open_help_topic({1: "properties", 2: "tags", 3: "tags", 4: "validation", 5: "validation"}.get(
            self.tabs.currentIndex(), "getting_started"))

    def showEvent(self, event):  # noqa: N802
        self._closing = False
        self.canvas._syncing = False
        if not self._clipboard_connected:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().dataChanged.connect(self.update_block_actions)
            self._clipboard_connected = True
        super().showEvent(event)
        fit_dialog_to_screen(self)
        self._library_refresh.start(0)

    def changeEvent(self, event):  # noqa: N802
        super().changeEvent(event)
        # Explorer retains the editor: procedures saved elsewhere after launch
        # must appear on return without rebuilding tags or touching the draft.
        if event.type() == QEvent.ActivationChange and self.isActiveWindow() and hasattr(self, "_library_refresh"):
            self._library_refresh.start(0)

    def closeEvent(self, event):  # noqa: N802
        try:
            if not self.allow_discard():
                event.ignore()
                return
        except Exception as error:
            self.status.setText(str(error))
            event.ignore()
            return
        self._closing = True
        self.canvas._syncing = True
        self._canvas_recovery.stop()
        self._library_refresh.stop()
        if hasattr(self, "help_center"):
            self.help_center.close()
        from PySide6.QtWidgets import QApplication
        if self._clipboard_connected:
            QApplication.clipboard().dataChanged.disconnect(self.update_block_actions)
            self._clipboard_connected = False
        super().closeEvent(event)
