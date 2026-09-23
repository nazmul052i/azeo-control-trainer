"""Library Explorer — wireframe sheet 7.2, plus the sheet-03 inspector.

Roles are columns because a block type usually needs several, and a
missing cell is a visible gap rather than a discovery at drop time. The
inspector shows a class the way an Author reads it: parameters (1 of 1,
or the counted exception), the binding table with its templates, and
coverage. "Edit source" opens the class's module in the system editor —
classes are Python written by Authors, not forms filled in a dialog.

The tree above it is the Azeo Operator Station Library Explorer. It carries
the library's own context menu:
New ▸ with the nine standard types plus Folder, Publish, clipboard,
Delete and Rename. Those act on a real user-standards store
(``_standards.json`` beside the displays) — a menu that does nothing
is a screen lying about itself. The built-in hphmi.* standards are
locked so shipped standards cannot be changed accidentally.
"""
from __future__ import annotations

from azeo_control_trainer.core.hmi.compatibility import PVM_SCOPE_PREFIXES

from azeo_control_trainer.core.presentation.menu_style import retain_menu

import inspect
import os
from pathlib import Path

import logging
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSplitter,
    QTableWidget, QTableWidgetItem, QTabWidget, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from azeo_control_trainer.core.presentation.headless import is_headless
from azeo_control_trainer.core.presentation.brand import AUTHORING_BLUE, AUTHORING_SELECTION
from azeo_control_trainer.core.presentation.authoring_dialog import apply_authoring_dialog
from azeo_control_trainer.core.hmi.pvms.base import ROLES, registry
from azeo_control_trainer.core.hmi.pvms.standards import StandardsStore

log = logging.getLogger(__name__)

_ROLE_HEADS = {"dynamo_compact": "cmp", "dynamo_inline": "inl",
               "faceplate": "face", "detail": "detail"}

#: Figure 10's New ▸ menu: the nine standard types, with a default a
#: fresh entry can carry until it is edited.
STANDARD_TYPES = (
    ("Color", "▮▮", "#4472C4"),
    ("Font", "AB", "Segoe UI 12"),
    ("Boolean", "01", "False"),
    ("Image", "🖼", ""),
    ("String", "[ ]", ""),
    ("Private String", "🔒", ""),
    ("Multi-language String", "🌐", "en:"),
    ("Measurement", "↔", "0 px"),
    ("Number", "#", "0"),
)
_TYPE_GLYPHS = {name: glyph for name, glyph, _d in STANDARD_TYPES}

#: Shipped, referenced by classes — locked like the OOB library.
BUILTIN_STANDARDS = ("hphmi.controller", "hphmi.indicator",
                     "hphmi.equipment")


class LibraryExplorer(QWidget):
    """The registry as a grid — gaps visible — with a class inspector."""

    def __init__(self, instance_counter=None, standards_root=None,
                 parent=None, *, configuration_root=None,
                 library_opener=None):
        super().__init__(parent)
        self._instance_counter = instance_counter or (lambda bt: 0)
        self._library_opener = library_opener
        self.library_root = Path(standards_root) \
            if standards_root is not None else None
        self.standards = StandardsStore(standards_root)
        from azeo_control_trainer.core.hmi.pvms.library_catalog import LibraryCatalog
        self.catalog = LibraryCatalog(self.library_root) \
            if self.library_root is not None else None
        from azeo_control_trainer.core.hmi.pvms.configuration import ConfigurationLibraryStore
        self.configuration_libraries = ConfigurationLibraryStore(
            configuration_root or standards_root) \
            if standards_root is not None else None
        from azeo_control_trainer.core.hmi.pvms.functions import FunctionStore
        from azeo_control_trainer.core.hmi.pvms.instances import TemplateStore
        self.functions = FunctionStore(standards_root) \
            if standards_root is not None else None
        self.templates = TemplateStore(standards_root) \
            if standards_root is not None else None
        self._std_clipboard: dict | None = None
        layout = QVBoxLayout(self)

        self.search = QLineEdit()
        self.search.setPlaceholderText(
            "Filter classes, symbols, status or usage…")
        self.search.setClearButtonEnabled(True)
        self.search.setToolTip(
            "Filter the engineering library without changing its folders")
        self.search.textChanged.connect(self._filter_tree)
        layout.addWidget(self.search)

        self.purpose = QLabel(
            "Reusable project assets. Select an item to preview, place, "
            "configure or edit it.")
        self.purpose.setObjectName("library_purpose")
        self.purpose.setWordWrap(True)
        self.purpose.setToolTip(
            "Library = reusable definitions; Palette = fast placement; "
            "Graphics Explorer = project displays and layouts")
        layout.addWidget(self.purpose)

        quick = QHBoxLayout()
        quick.setSpacing(3)
        self.quick_new_pvm = QPushButton("New PVM")
        self.quick_new_pvm.setObjectName("library_quick_action")
        self.quick_new_pvm.setToolTip(
            "Create a reusable Process Visualization Module (PVM), then draw its layout")
        self.quick_new_pvm.clicked.connect(
            lambda: self._new_user_class("pvm"))
        quick.addWidget(self.quick_new_pvm)
        self.quick_new_faceplate = QPushButton("New Pair")
        self.quick_new_faceplate.setObjectName("library_quick_action")
        self.quick_new_faceplate.setToolTip(
            "Create a paired compact PVM and full operator faceplate")
        self.quick_new_faceplate.clicked.connect(
            self._new_faceplate_blueprint)
        quick.addWidget(self.quick_new_faceplate)
        self.quick_import_svg = QPushButton("Import")
        self.quick_import_svg.setObjectName("library_quick_action")
        self.quick_import_svg.setToolTip(
            "Import scalable project artwork into Special Symbols")
        self.quick_import_svg.clicked.connect(self._import_svg)
        quick.addWidget(self.quick_import_svg)
        layout.addLayout(quick)

        # The Azeo Operator Station Library Explorer tree (white-paper figure):
        # system root over Languages, Themes, and Library holding PVM
        # Classes (foldered), Templates, Standards and Functions.
        self.tree = QTreeWidget()
        self.tree.setObjectName("library_tree")
        self.tree.setHeaderLabels(["Library item", "State", "Used"])
        self.tree.setColumnWidth(0, 150)
        self.tree.setColumnWidth(1, 52)
        self.tree.setColumnWidth(2, 32)
        self.tree.header().setStretchLastSection(False)
        # State and usage remain in the action card and coverage view. The
        # browser gives the asset name the full sidebar width.
        from PySide6.QtWidgets import QHeaderView
        self.tree.setColumnHidden(1, True)
        self.tree.setColumnHidden(2, True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.setStyleSheet("font-size: 9pt;")
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        self._build_tree()

        self.tree.itemClicked.connect(
            lambda item, _column: self._select_tree_item(item))
        # Double-click a class = pop the inspector out where there is
        # ROOM. The bottom half of a narrow column was the hardest
        # real estate in the window to actually use.
        self.tree.itemDoubleClicked.connect(self._tree_double_clicked)

        summary_row = QHBoxLayout()
        self.summary = QLabel("")
        summary_row.addWidget(self.summary, 1)
        self.pop_out = QPushButton("⧉")
        self.pop_out.setFixedSize(22, 20)
        self.pop_out.setToolTip(
            "Open the class inspector in its own window "
            "(double-clicking a class does this too)")
        self.pop_out.clicked.connect(self.undock_inspector)
        summary_row.addWidget(self.pop_out)

        # The whole panel is ONE draggable splitter: tree above,
        # grid + inspector below — the user decides the split, and
        # the bottom can be pulled up instead of being crushed.
        self.split = QSplitter(Qt.Vertical)
        self.split.addWidget(self.tree)
        bottom = QWidget()
        layout_bottom = QVBoxLayout(bottom)
        layout_bottom.setContentsMargins(0, 0, 0, 0)
        layout_bottom.setSpacing(2)
        layout_bottom.addLayout(summary_row)
        self._bottom_panel = bottom

        self.bottom_tabs = QTabWidget()
        self.bottom_tabs.setObjectName("library_detail_tabs")
        self.bottom_tabs.setDocumentMode(True)

        actions = QWidget()
        action_layout = QVBoxLayout(actions)
        action_layout.setContentsMargins(7, 6, 7, 6)
        action_layout.setSpacing(5)
        title_row = QHBoxLayout()
        self.item_title = QLabel("Library")
        self.item_title.setWordWrap(True)
        self.item_title.setFont(QFont("Segoe UI", 10, QFont.Bold))
        title_row.addWidget(self.item_title, 1)
        self.item_state = QLabel("READY")
        self.item_state.setStyleSheet(
            "QLabel { background: #E5F2E7; color: #2E6A3B; "
            "border: 1px solid #B8D8BE; padding: 2px 5px; "
            "font-size: 9pt; font-weight: 600; }")
        title_row.addWidget(self.item_state)
        action_layout.addLayout(title_row)
        self.item_preview = QLabel("LIBRARY")
        self.item_preview.setObjectName("library_preview")
        self.item_preview.setAlignment(Qt.AlignCenter)
        self.item_preview.setFixedHeight(92)
        action_layout.addWidget(self.item_preview)
        self.item_description = QLabel("")
        self.item_description.setWordWrap(True)
        self.item_description.setStyleSheet(
            "color: #455867; font-size: 9pt; border: none;")
        action_layout.addWidget(self.item_description)
        action_layout.addStretch(1)
        action_row = QHBoxLayout()
        action_row.setSpacing(4)
        self.item_primary = QPushButton("New PVM")
        self.item_primary.setObjectName("library_primary_action")
        self.item_primary.clicked.connect(self._run_primary_action)
        action_row.addWidget(self.item_primary, 1)
        self.item_secondary = QPushButton("PVM + Faceplate")
        self.item_secondary.clicked.connect(self._run_secondary_action)
        action_row.addWidget(self.item_secondary, 1)
        self.item_more = QPushButton("⋯")
        self.item_more.setFixedWidth(28)
        self.item_more.setToolTip("More actions for the selected library item")
        self.item_more.clicked.connect(self._show_selected_menu)
        action_row.addWidget(self.item_more)
        action_layout.addLayout(action_row)
        self.bottom_tabs.addTab(actions, "Actions")
        self.actions_tab = actions

        self.grid = QTableWidget(0, len(ROLES) + 2)
        self.grid.setHorizontalHeaderLabels(
            ["Block type"] + [_ROLE_HEADS[r] for r in ROLES] + ["inst"])
        self.grid.verticalHeader().setVisible(False)
        self.grid.setEditTriggers(QTableWidget.NoEditTriggers)
        self.grid.setSelectionBehavior(QTableWidget.SelectRows)
        self.grid.itemSelectionChanged.connect(self._on_select)
        self.bottom_tabs.addTab(self.grid, "Coverage")

        inspector = QWidget()
        ilay = QVBoxLayout(inspector)
        self.class_title = QLabel("Class inspector")
        self.class_title.setFont(QFont("Segoe UI", 9, QFont.Bold))
        ilay.addWidget(self.class_title)
        self.params_label = QLabel("")
        ilay.addWidget(self.params_label)
        self.bindings = QTableWidget(0, 2)
        self.bindings.setHorizontalHeaderLabels(["Target", "Binding"])
        self.bindings.verticalHeader().setVisible(False)
        self.bindings.setEditTriggers(QTableWidget.NoEditTriggers)
        self.bindings.setColumnWidth(0, 110)
        ilay.addWidget(self.bindings, 1)
        row = QHBoxLayout()
        self.coverage_label = QLabel("")
        self.coverage_label.setWordWrap(True)
        row.addWidget(self.coverage_label, 1)
        # Compact labels that survive a narrow column; the tooltip
        # carries the full name — a clipped button reads as broken.
        self.pvm_config = QPushButton("Configure…")
        self.pvm_config.setToolTip("PVM Configuration Designer")
        self.pvm_config.clicked.connect(self._open_config)
        row.addWidget(self.pvm_config)
        self.edit_layout = QPushButton("Layout ↗")
        self.edit_layout.setToolTip(
            "Edit this authored class on the Graphics Designer canvas")
        self.edit_layout.clicked.connect(self._open_layout)
        self.edit_layout.hide()
        row.addWidget(self.edit_layout)
        self.edit_source = QPushButton("Source ↗")
        self.edit_source.setToolTip("Edit the class's Python source")
        self.edit_source.clicked.connect(self._open_source)
        row.addWidget(self.edit_source)
        ilay.addLayout(row)
        self.details_tab = inspector
        self.bottom_tabs.insertTab(1, inspector, "Details")
        self._inner_split = self.bottom_tabs
        layout_bottom.addWidget(self.bottom_tabs, 1)
        self.split.addWidget(bottom)
        self.split.setSizes([340, 320])
        self.split.setCollapsible(0, False)
        layout.addWidget(self.split, 1)

        self._current_cls: type | None = None
        self._current_user_class = ""
        self._current_symbol = ""
        self._current_symbol_user = False
        self._selected_tree_item = None
        self._primary_callback = None
        self._secondary_callback = None
        self._inspector_dialog = None
        self.reload()
        self._show_library_welcome()

    # --------------------------------------------------- guided workflow
    def _set_action_card(self, title: str, state: str, description: str,
                         *, preview=None, preview_text: str = "",
                         primary: tuple[str, object] | None = None,
                         secondary: tuple[str, object] | None = None) -> None:
        """Make the selected asset's next useful operation unambiguous."""
        self.item_title.setText(title)
        self.item_state.setText((state or "READY").upper())
        colour = {
            "ERROR": ("#FDECEC", "#A32920", "#E0AAA6"),
            "REVIEW": ("#FFF3DD", "#8A5A00", "#DFC38E"),
            "PROJECT": (AUTHORING_SELECTION, AUTHORING_BLUE, AUTHORING_BLUE),
            "INSTALLED": ("#EEF0F2", "#53616C", "#CCD1D5"),
            "BUILT-IN": ("#EEF0F2", "#53616C", "#CCD1D5"),
        }.get((state or "").upper(),
              ("#E5F2E7", "#2E6A3B", "#B8D8BE"))
        self.item_state.setStyleSheet(
            "QLabel { background: %s; color: %s; border: 1px solid %s; "
            "padding: 2px 5px; font-size: 9pt; font-weight: 600; }"
            % colour)
        self.item_description.setText(description)
        self.item_preview.clear()
        if preview is not None and not preview.isNull():
            self.item_preview.setPixmap(preview.scaled(
                200, 88, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.item_preview.setText(preview_text or title.upper())

        self._primary_callback = primary[1] if primary else None
        self.item_primary.setText(primary[0] if primary else "No action")
        self.item_primary.setEnabled(primary is not None)
        self.item_primary.setVisible(primary is not None)
        self._secondary_callback = secondary[1] if secondary else None
        self.item_secondary.setText(secondary[0] if secondary else "")
        self.item_secondary.setEnabled(secondary is not None)
        self.item_secondary.setVisible(secondary is not None)
        payload = self._selected_tree_item.data(0, Qt.UserRole) \
            if self._selected_tree_item is not None else None
        self.item_more.setVisible(bool(payload))
        self.bottom_tabs.setCurrentWidget(self.actions_tab)

    def _show_library_welcome(self) -> None:
        self._selected_tree_item = None
        self._set_action_card(
            "Reusable Engineering Library", "Ready",
            "Use the Palette for quick placement. Use this Library to "
            "create, inspect and govern assets shared by many displays.",
            preview_text="SELECT AN ITEM\nOR CREATE A REUSABLE ASSET",
            primary=("New PVM", lambda: self._new_user_class("pvm")),
            secondary=("PVM + Faceplate", self._new_faceplate_blueprint))

    def _run_primary_action(self) -> None:
        if callable(self._primary_callback):
            self._primary_callback()

    def _run_secondary_action(self) -> None:
        if callable(self._secondary_callback):
            self._secondary_callback()

    def _show_details(self) -> None:
        self.bottom_tabs.setCurrentWidget(self.details_tab)

    def _show_selected_menu(self) -> None:
        item = self._selected_tree_item
        if item is None:
            return
        rect = self.tree.visualItemRect(item)
        self._tree_menu(rect.center())

    def _import_svg(self) -> None:
        importer = getattr(self.window(), "_import_svg", None)
        if callable(importer):
            importer()

    def _place_current_class(self) -> None:
        cls = self._current_cls
        placer = getattr(self.window(), "_palette_place", None)
        if cls is not None and callable(placer):
            placer(cls.block_type, cls.role, getattr(cls, "variant", ""))

    def _place_current_user_class(self) -> None:
        placer = getattr(self.window(), "_place_user_pvm", None)
        if self._current_user_class and callable(placer):
            placer(self._current_user_class)

    def _place_current_symbol(self) -> None:
        if not self._current_symbol:
            return
        if self._current_symbol_user:
            self._place_user_symbol(self._current_symbol)
        else:
            self._place_special_symbol(self._current_symbol)

    def _select_tree_item(self, item) -> None:
        """Selection explains first; opening or placing remains explicit."""
        self._selected_tree_item = item
        payload = item.data(0, Qt.UserRole)
        if not payload:
            self._show_library_welcome()
            return
        kind, name = payload
        if kind == "class":
            self.show_class(registry.get(*name))
        elif kind == "user_class":
            self.show_user_class(name)
        elif kind in ("special_symbol", "user_symbol"):
            self.show_symbol(name, kind == "user_symbol")
        elif kind == "config_library":
            self._set_action_card(
                str(name), "Project",
                "A named graphics configuration containing reusable "
                "classes, standards, templates and functions.",
                preview_text="CONFIGURATION\nLIBRARY",
                primary=("Open Library", lambda: self._library_opener(name))
                if self._library_opener is not None else None)
        else:
            self._show_subject_card(kind, name, item.text(0))

    def _show_subject_card(self, kind: str, name, label: str) -> None:
        from azeo_control_trainer.core.hmi.pvms.library_catalog import FACEPLATE_CLASS, PVM_CLASS

        if kind == "user_class_folder":
            if name == PVM_CLASS:
                self._set_action_card(
                    "PVM Classes", "Project",
                    "Reusable process graphics. Draw once, configure its "
                    "properties, then place linked instances on displays.",
                    preview_text="PVM", primary=(
                        "New PVM Class", lambda: self._new_user_class(
                            PVM_CLASS)))
            elif name == FACEPLATE_CLASS:
                self._set_action_card(
                    "Faceplate Classes", "Project",
                    "Reusable operator popups paired with a compact PVM. "
                    "The blueprint creates both sides of the workflow.",
                    preview_text="PVM  →  FACEPLATE", primary=(
                        "New PVM + Faceplate", self._new_faceplate_blueprint))
            else:
                self._set_action_card(
                    "Detail Display Classes", "Installed",
                    "Deep limits, diagnostics and tuning surfaces supplied "
                    "by the function-block package.",
                    preview_text="DETAIL DISPLAY")
        elif kind in ("special_symbols", "user_symbols"):
            self._set_action_card(
                "Special Symbols", "Project",
                "Scalable marks and SVG artwork. Place a symbol, then bind "
                "visibility or interaction in Graphics Configuration.",
                preview_text="SVG  /  SYMBOL", primary=(
                    "Import SVG", self._import_svg))
        elif kind in ("templates", "tpl_folder"):
            self._set_action_card(
                "Templates", "Ready",
                "Protected starting points for displays and layouts. "
                "Instances are detached copies, so projects remain editable.",
                preview_text="TEMPLATE", primary=(
                    "New Template", lambda: self.new_template(
                        kind=name if kind == "tpl_folder" else "display")))
        elif kind == "tpl":
            template = self.templates.entries.get(name) if self.templates is not None else None
            preview = None
            if template is not None and template.kind == "display":
                try:
                    from .template_session import template_preview
                    preview = template_preview(template.document, 200, 88, dpr=self.devicePixelRatioF())
                except Exception:  # noqa: BLE001 - a preview must never block the Library
                    logging.getLogger(__name__).exception("Template preview failed")
            edited = self.templates is not None and self.templates.is_overridden(name)
            self._set_action_card(
                str(name),
                ("Built-in · edited" if edited else "Built-in")
                if self.templates is not None and self.templates.is_builtin(name) else "Project",
                "Starting point for new displays. Edit Template opens it on the "
                "canvas; Save writes the template. A built-in is edited as this "
                "project's override and Reset to built-in restores it.",
                preview=preview, preview_text="TEMPLATE",
                primary=("Edit Template", lambda: self._window_call("edit_template", name)),
                secondary=("New Display from Template",
                           lambda: self._window_call("new_from_template", name)))
        elif kind in ("standards", "std_folder"):
            self._set_action_card(
                "Standards", "Ready",
                "Shared typed values for colors, fonts, measurements and "
                "text. Change one standard and every reference follows.",
                preview_text="STANDARD", primary=(
                    "New Color Standard", lambda: self.new_standard(
                        "Color", name if kind == "std_folder" and name
                        else "Common")))
        elif kind in ("std", "std_builtin"):
            entry = self.standards.get(name) if kind == "std" else None
            value = entry.get("value", "") if entry else str(name)
            self._set_action_card(
                str(name), "Built-in" if kind == "std_builtin" else "Project",
                "Shared standard value: %s" % value,
                preview_text=value or "STANDARD",
                primary=("Edit Value", lambda: self.edit_value(name))
                if kind == "std" else None,
                secondary=("Details", self._show_details))
        elif kind == "functions":
            self._set_action_card(
                "Functions", "Ready",
                "Reusable threshold, scale and typed conversions used by "
                "bindings and animations.",
                preview_text="f(x)", primary=(
                    "New Function", self.new_formula_fn))
        elif kind == "fn":
            entry = self.functions.entries.get(name, {}) \
                if self.functions is not None else {}
            self._set_action_card(
                str(name), "Project",
                "Reusable %s conversion function." % entry.get(
                    "type", "typed"), preview_text="f(x)",
                primary=("Copy Name", lambda: self._clipboard(name)))
        elif kind == "theme":
            self._set_action_card(
                str(name), "Installed",
                "A complete role-based HMI theme. Themes change semantic "
                "roles, not individual object colors.", preview_text="THEME")
        else:
            self._set_action_card(
                label, "Ready",
                "Select a child item or use More actions to create and "
                "manage this part of the reusable library.",
                preview_text=label.upper())

    # ---------------------------------------------------- undocking
    def undock_inspector(self) -> "QWidget":
        """Float the grid + inspector as its own window; closing it
        re-docks. Same widgets either way, so nothing re-implements
        and the state travels with them."""
        from PySide6.QtWidgets import QDialog, QVBoxLayout as _QVBL

        from azeo_control_trainer.core.presentation.headless import is_headless
        if self._inspector_dialog is not None:
            self._inspector_dialog.raise_()
            self._inspector_dialog.activateWindow()
            return self._inspector_dialog
        dialog = QDialog(self.window())
        dialog.setWindowTitle("Library — Class Inspector")
        apply_authoring_dialog(dialog)
        dialog.setProperty("embeddedAuthoringShell", True)
        dialog.resize(560, 620)
        lay = _QVBL(dialog)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(self._bottom_panel)

        def redock():
            self._bottom_panel.setParent(None)
            self.split.addWidget(self._bottom_panel)
            self._inspector_dialog = None
        dialog.finished.connect(lambda _r: redock())
        self._inspector_dialog = dialog
        if not is_headless():
            dialog.show()
        return dialog

    # -------------------------------------------------------------- tree
    def _node(self, parent, text, payload=None, bold=False, *,
              state: str = "", used: str = "", tooltip: str = ""):
        # Tree icons carry identity independently of the workstation's emoji
        # font. Keep names searchable and free of font-dependent prefixes.
        text = text.lstrip("📄📁🏅◇◆▣▤▥ ").replace(" 🔒", "")
        item = QTreeWidgetItem(parent, [text, state, used])
        if payload is not None:
            item.setData(0, Qt.UserRole, payload)
        from azeo_control_trainer.core.presentation.studio_icons import studio_icon
        from .component_icons import pvm_icon, special_preview, symbol_preview
        from PySide6.QtGui import QIcon

        kind, value = payload if payload is not None else ("folder", None)
        if kind == "class":
            cls = registry.all_classes().get(tuple(value))
            if cls is not None:
                item.setIcon(0, pvm_icon(cls))
        elif kind == "special_symbol":
            item.setIcon(0, QIcon(special_preview(value, 20, 20)))
        elif kind == "user_symbol":
            item.setIcon(0, QIcon(symbol_preview(value, 20, 20)))
        else:
            icon_name = {
                "user_class": "templates", "theme": "presets", "tpl": "new",
                "std": "properties", "std_builtin": "properties", "fn": "exec_edit",
            }.get(kind, "open")
            item.setIcon(0, studio_icon(icon_name, 20))
        if bold:
            font = QFont(self.tree.font())
            font.setBold(True)
            item.setFont(0, font)
        if tooltip:
            for column in range(3):
                item.setToolTip(column, tooltip)
        colour = {"Ready": "#2E7D32", "Review": "#A26400",
                  "Error": "#B3261E"}.get(state)
        if colour:
            item.setForeground(1, QBrush(QColor(colour)))
        return item

    def _build_tree(self) -> None:
        self.tree.clear()
        node = self._node
        root = node(self.tree, "Azeo System", bold=True)
        node(root, "Languages")
        themes = node(root, "Themes")
        try:
            from azeo_control_trainer.core.hmi.theme.tokens import THEMES
            for theme_name in THEMES:
                node(themes, theme_name, payload=("theme", theme_name))
        except Exception:                           # noqa: BLE001
            log.warning("Unable to load installed graphics themes",
                        exc_info=True)
        if self.configuration_libraries is not None:
            libraries = node(root, "▣ Configuration Libraries", bold=True)
            active = self.configuration_libraries.active
            for library_name in self.configuration_libraries.names():
                marker = " ●" if library_name == active else ""
                node(libraries, f"▣ {library_name}{marker}",
                     payload=("config_library", library_name))
            libraries.setExpanded(True)
        library = node(root, "Library", bold=True)

        from azeo_control_trainer.core.hmi.pvms.library_catalog import (
            DETAIL_CLASS, FACEPLATE_CLASS, PVM_CLASS,
        )

        records = self.catalog.records() if self.catalog is not None else ()

        def class_folder(title: str, artifact_kind: str,
                         roles: tuple[str, ...]):
            artifact = node(
                library, title,
                payload=("user_class_folder", artifact_kind), bold=True,
                tooltip=f"Reusable {title.lower()} owned by this library")
            installed = node(
                artifact, "Installed · read-only", state="Installed",
                tooltip="Packaged classes are verified with the application")
            modules: dict[str, QTreeWidgetItem] = {}
            for (block_type, role, variant), cls in sorted(
                    registry.all_classes().items()):
                if role not in roles:
                    continue
                module_name = cls.__module__.rsplit(".", 1)[-1]
                folder = modules.get(module_name)
                if folder is None:
                    folder = node(installed, module_name)
                    modules[module_name] = folder
                label = cls.__name__ + (f" · {variant}" if variant else "")
                node(folder, label,
                     payload=("class", (block_type, role, variant)),
                     state="Ready", tooltip=(
                         f"{block_type} / {role}"
                         + (f" / {variant}" if variant else "")))

            authored = node(artifact, "Project · authored")
            matching = [record for record in records
                        if record.kind == artifact_kind]
            by_folder: dict[str, QTreeWidgetItem] = {}
            for record in matching:
                folder = by_folder.get(record.folder)
                if folder is None:
                    folder = node(authored, record.folder)
                    by_folder[record.folder] = folder
                issue_text = "\n".join(record.issues) or "Validated"
                node(folder, f"◇ {record.name}",
                     payload=("user_class", record.name),
                     state=record.status, used=str(record.usage_count),
                     tooltip=(
                         f"{record.kind_title}\n"
                         f"{record.item_count} elements · "
                         f"{record.width:g}×{record.height:g} px\n"
                         f"Paired: {', '.join(record.paired_with) or 'none'}\n"
                         f"{issue_text}"))
            if not matching:
                node(authored, "No authored classes", state="—")
            installed.setExpanded(False)
            authored.setExpanded(True)
            artifact.setExpanded(True)
            return artifact

        pvm_classes = class_folder(
            "▣ PVM Classes", PVM_CLASS,
            ("dynamo_compact", "dynamo_inline"))
        faceplate_classes = class_folder(
            "▤ Faceplate Classes", FACEPLATE_CLASS, ("faceplate",))
        detail_classes = class_folder(
            "▥ Detail Display Classes", DETAIL_CLASS, ("detail",))

        special = node(library, "◆ Special Symbols", bold=True,
                       payload=("special_symbols", None),
                       tooltip="Scalable faceplate marks and project SVGs")
        from azeo_control_trainer.core.hmi.pvms.faceplate_icons import ICON_TITLES, SPECIAL_SYMBOL_GROUPS
        for group_name, names in SPECIAL_SYMBOL_GROUPS.items():
            group = node(special, group_name, state="Built-in")
            for name in names:
                node(group, ICON_TITLES[name],
                     payload=("special_symbol", name), state="Ready",
                     tooltip=(f"Special Symbol: {name}\n"
                              "Place, resize, bind and group like any "
                              "drawing element"))
        user_symbols = node(special, "Project SVG Symbols",
                            payload=("user_symbols", None))
        if self.library_root is not None:
            from azeo_control_trainer.core.hmi.pvms.symbols import USER_SYMBOLS, load_user_symbols
            load_user_symbols(self.library_root / "_library" / "svg")
            for name in sorted(USER_SYMBOLS):
                node(user_symbols, name, payload=("user_symbol", name),
                     state="Ready")
            if not USER_SYMBOLS:
                node(user_symbols, "No imported SVG symbols", state="—")
        special.setExpanded(True)

        # Permanent library services follow the reusable engineering
        # artifacts.  Their order is stable so muscle memory survives a
        # project growing from ten classes to ten thousand.
        templates = node(library, "📄 Templates",
                         payload=("templates", None))
        for kind, label in (("display", "Display templates"),
                            ("contextual_display",
                             "Contextual display templates"),
                            ("layout", "Layout templates")):
            folder = node(templates, f"📁 {label}",
                          payload=("tpl_folder", kind))
            if self.templates is not None:
                for template_name in self.templates.names(kind):
                    node(folder, f"📄 {template_name}",
                         payload=("tpl", template_name),
                         state=("Built-in · edited" if self.templates.is_overridden(template_name)
                                else "Built-in" if self.templates.is_builtin(template_name)
                                else "Project"))
        self.standards_node = node(library, "🏅 Standards",
                                   payload=("standards", None))
        builtin = node(self.standards_node, "📁 Azeo 🔒",
                       payload=("std_folder", None))
        for standard in BUILTIN_STANDARDS:
            node(builtin, f"▮▮ {standard} 🔒",
                 payload=("std_builtin", standard))
        for folder_name in self.standards.folders:
            folder_item = node(self.standards_node,
                               f"📁 {folder_name}",
                               payload=("std_folder", folder_name))
            for entry in self.standards.entries:
                if entry["folder"] != folder_name:
                    continue
                glyph = _TYPE_GLYPHS.get(entry["type"], "[ ]")
                node(folder_item,
                     f"{glyph} {entry['name']}  ·  {entry['value']}",
                     payload=("std", entry["name"]))
            folder_item.setExpanded(True)
        functions = node(library, "ƒ(x) Functions",
                         payload=("functions", ""))
        # Authored conversion functions first — the animation
        # vocabulary; the expression builtins are read-only reference.
        if self.functions is not None:
            for fn_name in self.functions.names():
                entry = self.functions.entries[fn_name]
                node(functions,
                     f"ƒ {fn_name}  ·  {entry['type']}",
                     payload=("fn", fn_name))
        builtins = node(functions, "expression built-ins 🔒")
        for fn in ("abs", "min", "max", "round",
                   "param('BLOCK/TERM')", "tag('KEY'[, default])"):
            node(builtins, fn)
        root.setExpanded(True)
        library.setExpanded(True)
        pvm_classes.setExpanded(True)
        faceplate_classes.setExpanded(True)
        detail_classes.setExpanded(True)
        self.standards_node.setExpanded(True)
        self._filter_tree(self.search.text() if hasattr(self, "search") else "")

    def _filter_tree(self, text: str) -> None:
        """Filter leaves while retaining the ancestry that explains them."""
        query = str(text).strip().casefold()

        def visit(item) -> bool:
            own = any(query in item.text(column).casefold()
                      for column in range(3))
            # Visit every sibling before reducing. ``any(generator)`` stops at
            # the first match and leaves the remaining branch in its previous
            # visibility state, producing apparently random search results.
            child_results = [visit(item.child(index))
                             for index in range(item.childCount())]
            child_match = any(child_results)
            visible = not query or own or child_match
            item.setHidden(not visible)
            if query and child_match:
                item.setExpanded(True)
            return visible

        for index in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(index))

    def _tree_double_clicked(self, item, _column) -> None:
        payload = item.data(0, Qt.UserRole) or ("", None)
        kind, _name = payload
        self._select_tree_item(item)
        if kind in ("class", "user_class", "special_symbol",
                    "user_symbol", "config_library"):
            self._run_primary_action()

    # ---------------------------------------------- Figure 10's menu
    def _tree_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        payload = item.data(0, Qt.UserRole) if item else None
        if not payload:
            return
        from azeo_control_trainer.core.presentation.menu_style \
            import studio_menu
        kind, name = payload
        if kind == "class":
            cls = registry.get(*name)
            menu = studio_menu(f"CLASS  {cls.__name__}",
                               "PVM class")
            if cls.role in ("dynamo_compact", "dynamo_inline"):
                place = menu.addAction("Place on Display")
                font = place.font()
                font.setBold(True)
                place.setFont(font)
                place.triggered.connect(
                    lambda: (self.show_class(cls),
                             self._place_current_class()))
            config = menu.addAction("Configuration…")
            font = config.font()
            font.setBold(True)
            config.setFont(font)
            config.triggered.connect(
                lambda: (self.show_class(cls), self._open_config()))
            menu.addAction("Edit source ↗").triggered.connect(
                lambda: (self.show_class(cls), self._open_source()))
            menu.addSeparator()
            menu.addAction("Copy class name").triggered.connect(
                lambda: self._clipboard(cls.__name__))
        elif kind == "user_class_folder":
            from azeo_control_trainer.core.hmi.pvms.library_catalog import FACEPLATE_CLASS, PVM_CLASS
            title = {
                PVM_CLASS: "PVM CLASSES",
                FACEPLATE_CLASS: "FACEPLATE CLASSES",
            }.get(name, "DETAIL DISPLAY CLASSES")
            menu = studio_menu(title, "Project engineering artifacts")
            if name == PVM_CLASS:
                menu.addAction("New PVM Class…").triggered.connect(
                    lambda: self._new_user_class(PVM_CLASS))
            elif name == FACEPLATE_CLASS:
                action = menu.addAction(
                    "New Paired PVM + Faceplate Blueprint…")
                action.triggered.connect(self._new_faceplate_blueprint)
            else:
                note = menu.addAction(
                    "Detail classes are supplied by function-block packages")
                note.setEnabled(False)
        elif kind == "user_class":
            record = self.catalog.record(name) if self.catalog else None
            menu = studio_menu(
                f"{record.kind_title.upper() if record else 'CLASS'}  {name}",
                record.status if record else "Authored class")
            if record is not None and record.kind == "pvm":
                place = menu.addAction("Place on Display")
                font = place.font()
                font.setBold(True)
                place.setFont(font)
                place.triggered.connect(
                    lambda: (self.show_user_class(name),
                             self._place_current_user_class()))
            layout = menu.addAction("Edit Layout…")
            font = layout.font()
            font.setBold(True)
            layout.setFont(font)
            layout.triggered.connect(
                lambda: (self.show_user_class(name), self._open_layout()))
            menu.addAction("Configure Properties…").triggered.connect(
                lambda: (self.show_user_class(name), self._open_config()))
            if record is not None and record.kind == "pvm":
                menu.addAction("Pair with Faceplate…").triggered.connect(
                    lambda: self._pair_user_class(name))
            if record is not None and record.paired_with:
                menu.addAction("Open Paired Class").triggered.connect(
                    lambda: self.select_user_class(record.paired_with[0]))
            menu.addSeparator()
            menu.addAction("Validate Class").triggered.connect(
                lambda: self.validate_user_class(name, show=True))
            menu.addAction("Find Usages…").triggered.connect(
                lambda: self.find_user_class_usages(name, show=True))
            menu.addAction("Copy Class Name").triggered.connect(
                lambda: self._clipboard(name))
        elif kind in ("special_symbols", "user_symbols"):
            menu = studio_menu("SPECIAL SYMBOLS", "Drawing library")
            action = menu.addAction("Import SVG Symbol…")
            action.triggered.connect(
                lambda: getattr(self.window(), "_import_svg", lambda: None)())
        elif kind == "special_symbol":
            menu = studio_menu(f"SPECIAL SYMBOL  {name}",
                               "Scalable faceplate artwork")
            place = menu.addAction("Place on Display")
            font = place.font()
            font.setBold(True)
            place.setFont(font)
            place.triggered.connect(lambda: self._place_special_symbol(name))
            menu.addAction("Copy Symbol Name").triggered.connect(
                lambda: self._clipboard(name))
        elif kind == "user_symbol":
            menu = studio_menu(f"SVG SYMBOL  {name}", "Project symbol")
            menu.addAction("Place on Display").triggered.connect(
                lambda: self._place_user_symbol(name))
            menu.addAction("Copy Symbol Name").triggered.connect(
                lambda: self._clipboard(name))
        elif kind in ("standards", "std_folder", "std",
                      "std_builtin"):
            menu = self._standards_menu(kind, name)
        elif kind == "theme":
            menu = studio_menu(f"THEME  {name}", "Library theme")
            menu.addAction("Copy name").triggered.connect(
                lambda: self._clipboard(name))
        elif kind == "config_library" \
                and self._library_opener is not None:
            menu = studio_menu(f"LIBRARY  {name}",
                               "Graphics configuration")
            menu.addAction("Open").triggered.connect(
                lambda: self._library_opener(name))
        elif kind == "functions" and self.functions is not None:
            menu = studio_menu("FUNCTIONS", "Conversion functions")
            menu.addAction("New Threshold Function…") \
                .triggered.connect(lambda: self.new_threshold_fn())
            menu.addAction("New Scale Function…") \
                .triggered.connect(lambda: self.new_scale_fn())
            menu.addAction("New Typed Function…") \
                .triggered.connect(lambda: self.new_formula_fn())
        elif kind == "fn" and self.functions is not None:
            menu = studio_menu(f"FUNCTION  {name}",
                               "Conversion function")
            menu.addAction("Copy name").triggered.connect(
                lambda: self._clipboard(name))
            menu.addSeparator()
            menu.addAction("Delete").triggered.connect(
                lambda: self.delete_fn(name))
        elif kind in ("templates", "tpl_folder") \
                and self.templates is not None:
            template_kind = name if kind == "tpl_folder" else "display"
            menu = studio_menu("TEMPLATES", "Copied starting points")
            menu.addAction("New Template…").triggered.connect(
                lambda: self.new_template(kind=template_kind))
        elif kind == "tpl" and self.templates is not None:
            builtin = self.templates.is_builtin(name)
            overridden = self.templates.is_overridden(name)
            menu = studio_menu(
                f"TEMPLATE  {name}",
                ("Built-in, edited in this project" if overridden else "Built-in")
                if builtin else "Project starting point",
            )
            menu.addAction("Edit Template\u2026").triggered.connect(
                lambda: self._window_call("edit_template", name))
            menu.addAction("New Display from Template\u2026").triggered.connect(
                lambda: self._window_call("new_from_template", name))
            menu.addAction("Duplicate\u2026").triggered.connect(
                lambda: self.duplicate_template(name))
            menu.addSeparator()
            if builtin:
                reset = menu.addAction("Reset to built-in")
                reset.setEnabled(overridden)
                reset.triggered.connect(
                    lambda: self._window_call("reset_template", name))
            else:
                menu.addAction("Delete").triggered.connect(
                    lambda: self.delete_template(name))
        else:
            return
        retain_menu(self, menu, "_context_menu")
        if not is_headless():
            menu.exec_transient(self.tree.mapToGlobal(pos))

    def _standards_menu(self, kind: str, name):
        from azeo_control_trainer.core.presentation.menu_style \
            import studio_menu
        locked = kind == "std_builtin" or (kind == "std_folder"
                                           and name is None)
        title = name or "Standards"
        menu = studio_menu(f"STANDARDS  {title}",
                           "Built-in — locked" if locked
                           else "Library standards")
        folder = name if kind == "std_folder" and name else \
            (self.standards.get(name) or {}).get("folder", "Common") \
            if kind == "std" else "Common"
        # Construct the submenu explicitly and addMenu(menu) — the
        # addMenu(title) overload's QMenu gets GC-deleted (see the
        # editor-direct-manipulation note; same fix as studio.py).
        from PySide6.QtWidgets import QMenu as _QMenu
        new_menu = _QMenu("New", menu)
        for std_type, glyph, _default in STANDARD_TYPES:
            new_menu.addAction(f"{glyph}  {std_type}") \
                .triggered.connect(
                    lambda _=False, t=std_type, f=folder:
                    self.new_standard(t, f))
        new_menu.addSeparator()
        new_menu.addAction("📁  Folder").triggered.connect(
            self.new_folder)
        menu.addMenu(new_menu)
        menu.addAction("Publish").triggered.connect(
            self.publish_standards)
        menu.addSeparator()
        cut = menu.addAction("Cut\tCtrl+X")
        copy = menu.addAction("Copy\tCtrl+C")
        paste = menu.addAction("Paste\tCtrl+V")
        paste.setEnabled(self._std_clipboard is not None)
        paste.triggered.connect(
            lambda _=False, f=folder: self.paste_standard(f))
        menu.addSeparator()
        delete = menu.addAction("Delete\tDel")
        rename = menu.addAction("Rename\tF2")
        if kind == "std":
            copy.triggered.connect(
                lambda _=False, n=name: self.copy_standard(n))
            cut.triggered.connect(
                lambda _=False, n=name:
                (self.copy_standard(n), self.delete_standard(n)))
            delete.triggered.connect(
                lambda _=False, n=name: self.delete_standard(n))
            rename.triggered.connect(
                lambda _=False, n=name: self.rename_standard(n))
            edit = menu.addAction("Edit value…")
            edit.triggered.connect(
                lambda _=False, n=name: self.edit_value(n))
        else:
            for action in (cut, copy, delete, rename):
                action.setEnabled(False)
        if locked:
            for action in menu.actions():
                if action.text().split("\t")[0] not in ("Copy",
                                                        "Publish"):
                    action.setEnabled(False)
            copy.setEnabled(kind == "std_builtin")
            if kind == "std_builtin":
                copy.triggered.connect(
                    lambda _=False, n=name: self._clipboard(n))
        return menu

    # --------------------------------------------- standards actions
    def new_standard(self, std_type: str,
                     folder: str = "Common") -> dict:
        entry = self.standards.add(std_type, folder)
        self.standards.save()
        self._build_tree()
        return entry

    def new_folder(self, name: str = "") -> None:
        if not name:
            name = f"Folder{len(self.standards.folders) + 1}"
            if not is_headless():
                name, ok = QInputDialog.getText(
                    self, "New folder", "Folder name:", text=name)
                if not ok or not name:
                    return
        if name not in self.standards.folders:
            self.standards.folders.append(name)
            self.standards.save()
            self._build_tree()

    def rename_standard(self, old: str, new: str = "") -> bool:
        if not new:
            if is_headless():
                new = old + "_r"
            else:
                new, ok = QInputDialog.getText(
                    self, "Rename", "New name:", text=old)
                if not ok or not new:
                    return False
        renamed = self.standards.rename(old, new)
        if renamed:
            self.standards.save()
            self._build_tree()
        return renamed

    def delete_standard(self, name: str) -> bool:
        deleted = self.standards.delete(name)
        if deleted:
            self.standards.save()
            self._build_tree()
        return deleted

    def edit_value(self, name: str, value: str | None = None) -> None:
        entry = self.standards.get(name)
        if entry is None:
            return
        if value is None:
            if is_headless():
                return
            if entry["type"] == "Color":
                from PySide6.QtWidgets import QColorDialog
                from PySide6.QtGui import QColor
                colour = QColorDialog.getColor(
                    QColor(entry["value"] or "#FFFFFF"), self)
                if not colour.isValid():
                    return
                value = colour.name()
            else:
                value, ok = QInputDialog.getText(
                    self, f"{name} — value", "Value:",
                    text=entry["value"])
                if not ok:
                    return
        entry["value"] = value
        self.standards.save()
        self._build_tree()

    def copy_standard(self, name: str) -> None:
        entry = self.standards.get(name)
        if entry is not None:
            self._std_clipboard = dict(entry)

    def paste_standard(self, folder: str = "Common") -> dict | None:
        if self._std_clipboard is None:
            return None
        source = self._std_clipboard
        entry = self.standards.add(source["type"], folder)
        entry["value"] = source["value"]
        self.standards.save()
        self._build_tree()
        return entry

    def publish_standards(self) -> None:
        """A standard publishes itself — never the displays that
        reference it (the republish asymmetry the paper documents)."""
        self.standards.save()

    # ------------------------------------------- conversion functions
    def new_threshold_fn(self, name: str = "",
                         spec: str = "") -> dict | None:
        """The colour-table lookup. Interactive spec grammar:
        ``80: #E4572E; 60: #F0A202; else #8A9BA8`` — highest limit
        met wins, `else` is the default."""
        if self.functions is None:
            return None
        if not name:
            if is_headless():
                return None
            name, ok = QInputDialog.getText(
                self, "New Threshold Function", "Function name:")
            if not ok or not name:
                return None
            spec, ok = QInputDialog.getText(
                self, name,
                "Rows — limit: output; …; else default:",
                text="80: #E4572E; 60: #F0A202; else #8A9BA8")
            if not ok:
                return None
        rows, default = [], None
        try:
            for part in filter(None,
                               (p.strip() for p in spec.split(";"))):
                if part.lower().startswith("else"):
                    default = part[4:].lstrip(": ").strip()
                else:
                    limit, output = part.split(":", 1)
                    rows.append((float(limit), output.strip()))
        except (ValueError, TypeError):
            return None
        if not rows:
            return None
        entry = self.functions.add_threshold(name, rows, default)
        self._build_tree()
        return entry

    def new_scale_fn(self, name: str = "",
                     spec: str = "") -> dict | None:
        """Linear in→out, clamped. Spec: ``0, 2000 -> 0, 100``."""
        if self.functions is None:
            return None
        if not name:
            if is_headless():
                return None
            name, ok = QInputDialog.getText(
                self, "New Scale Function", "Function name:")
            if not ok or not name:
                return None
            spec, ok = QInputDialog.getText(
                self, name, "in_lo, in_hi -> out_lo, out_hi:",
                text="0, 2000 -> 0, 100")
            if not ok:
                return None
        try:
            in_part, out_part = spec.split("->")
            in_lo, in_hi = (float(v) for v in in_part.split(","))
            out_lo, out_hi = (float(v) for v in out_part.split(","))
        except (ValueError, TypeError):
            return None
        entry = self.functions.add_scale(name, in_lo, in_hi,
                                         out_lo, out_hi)
        self._build_tree()
        return entry

    def delete_fn(self, name: str) -> bool:
        if self.functions is None:
            return False
        removed = self.functions.remove(name)
        if removed:
            self._build_tree()
        return removed

    def new_formula_fn(self, name: str = "", specification=None):
        """Create the five-input rule/value form from a JSON definition."""
        if self.functions is None:
            return None
        import json
        if not name:
            if is_headless():
                return None
            name, ok = QInputDialog.getText(
                self, "New Typed Function", "Function name:")
            if not ok or not name:
                return None
            starter = {
                "logic": "value",
                "inputs": [{"name": "Input1", "type": "Number",
                            "default": 0}],
                "calculations": [],
                "expression": "Input1",
                "return_type": "Number",
            }
            text, ok = QInputDialog.getMultiLineText(
                self, name, "Typed function definition:",
                json.dumps(starter, indent=2))
            if not ok:
                return None
            try:
                specification = json.loads(text)
            except ValueError:
                return None
        spec = dict(specification or {})
        try:
            entry = self.functions.add_formula(
                name, spec.get("inputs", ()),
                calculations=spec.get("calculations", ()),
                expression=spec.get("expression", ""),
                rules=spec.get("rules", ()), default=spec.get("default"),
                logic=spec.get("logic", "value"),
                return_type=spec.get("return_type", "Number"))
        except (TypeError, ValueError):
            return None
        self._build_tree()
        return entry

    def _clipboard(self, text: str) -> None:
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(text)

    # --------------------------------------- authored engineering classes
    def _new_user_class(self, definition_kind: str, name: str = ""):
        host = self.window()
        creator = getattr(host, "new_user_pvm_class", None)
        if not callable(creator):
            return None
        created = creator(name=name)
        self.rebuild()
        return created

    def _new_faceplate_blueprint(self, name: str = ""):
        host = self.window()
        creator = getattr(host, "new_faceplate_blueprint", None)
        if not callable(creator):
            return None
        created = creator(name=name)
        self.rebuild()
        return created

    def _pair_user_class(self, pvm_name: str,
                         faceplate_name: str = "") -> bool:
        if self.library_root is None:
            return False
        from azeo_control_trainer.core.hmi.pvms.user_library import UserPvmLibrary
        library = UserPvmLibrary(self.library_root)
        choices = library.names(definition_kind="faceplate")
        if not choices:
            return False
        if not faceplate_name:
            faceplate_name = choices[0]
            if not is_headless():
                faceplate_name, ok = QInputDialog.getItem(
                    self, "Pair PVM with Faceplate", "Faceplate class:",
                    choices, 0, False)
                if not ok:
                    return False
        paired = library.pair_faceplate(pvm_name, faceplate_name)
        if paired:
            refresher = getattr(self.window(), "refresh_user_pvms", None)
            if callable(refresher):
                refresher()
            self.rebuild()
            self.select_user_class(pvm_name)
        return paired

    def validate_user_class(self, name: str, *, show: bool = False):
        record = self.catalog.record(name) if self.catalog else None
        if show and not is_headless():
            if record is None:
                text = "Class is not present in the active library."
            elif record.issues:
                text = "\n".join(f"• {issue}" for issue in record.issues)
            else:
                text = "No structural or configuration issues were found."
            QMessageBox.information(
                self, f"Validate — {name}", text)
        return record

    def find_user_class_usages(self, name: str, *,
                               show: bool = False) -> tuple[str, ...]:
        record = self.catalog.record(name) if self.catalog else None
        usages = record.used_by if record is not None else ()
        if show and not is_headless():
            QMessageBox.information(
                self, f"Usages — {name}",
                "\n".join(usages) if usages else
                "This class has no project references.")
        return usages

    def select_user_class(self, name: str) -> bool:
        def visit(item) -> QTreeWidgetItem | None:
            if item.data(0, Qt.UserRole) == ("user_class", name):
                return item
            for index in range(item.childCount()):
                found = visit(item.child(index))
                if found is not None:
                    return found
            return None

        for index in range(self.tree.topLevelItemCount()):
            found = visit(self.tree.topLevelItem(index))
            if found is not None:
                self.tree.setCurrentItem(found)
                parent = found.parent()
                while parent is not None:
                    parent.setExpanded(True)
                    parent = parent.parent()
                self.show_user_class(name)
                return True
        return False

    def _place_special_symbol(self, name: str) -> None:
        handler = getattr(self.window(), "_arm_special_symbol", None)
        if callable(handler):
            handler(name)

    def _place_user_symbol(self, name: str) -> None:
        handler = getattr(self.window(), "_arm_symbol", None)
        if callable(handler):
            handler(name)

    def new_template(self, name: str = "", kind: str = "display",
                     document=None):
        if self.templates is None:
            return None
        if not name:
            if is_headless():
                return None
            name, ok = QInputDialog.getText(
                self, "New Template", "Template name:")
            if not ok or not name:
                return None
        if document is None:
            document = ({"layout": name, "screens": []}
                        if kind == "layout"
                        else {"display": name, "pvms": []})
        template = self.templates.add(name, kind, document)
        self._build_tree()
        return template

    def delete_template(self, name: str) -> bool:
        if self.templates is None:
            return False
        removed = self.templates.remove(name)
        if removed:
            self._build_tree()
        return removed

    def duplicate_template(self, name: str, new_name: str = ""):
        """A project copy of any template, under a new name."""
        template = self.templates.entries.get(name) if self.templates is not None else None
        if template is None:
            return None
        if not new_name:
            if is_headless():
                return None
            new_name, ok = QInputDialog.getText(
                self, "Duplicate template", "New template name:", text=f"{name} copy")
            if not ok or not new_name.strip():
                return None
        if self.templates.is_builtin(new_name.strip()):
            return None
        return self.new_template(new_name.strip(), template.kind, dict(template.document))

    def _window_call(self, method: str, *args):
        handler = getattr(self.window(), method, None)
        return handler(*args) if callable(handler) else None

    # -------------------------------------------------------------- grid
    def rebuild(self) -> None:
        """Refresh both library documents and the registered-class grid."""
        self._build_tree()
        self.reload()

    def reload(self) -> None:
        classes = registry.all_classes()
        authored = self.catalog.records() if self.catalog is not None else ()
        by_type: dict[str, dict] = {}
        for (block_type, role, variant), cls in classes.items():
            by_type.setdefault(block_type, {})[role] = cls
        self.summary.setText(
            f"{len(classes)} installed · {len(authored)} authored · "
            f"{sum(record.status == 'Error' for record in authored)} errors")
        rows = sorted(by_type)
        self.grid.setRowCount(len(rows))
        for i, block_type in enumerate(rows):
            self.grid.setItem(i, 0, QTableWidgetItem(block_type))
            for j, role in enumerate(ROLES, start=1):
                mark = "✓" if role in by_type[block_type] else "–"
                item = QTableWidgetItem(mark)
                item.setTextAlignment(Qt.AlignCenter)
                self.grid.setItem(i, j, item)
            count = self._instance_counter(block_type)
            item = QTableWidgetItem(str(count))
            item.setTextAlignment(Qt.AlignCenter)
            self.grid.setItem(i, len(ROLES) + 1, item)

    # --------------------------------------------------------- inspector
    def _on_select(self) -> None:
        items = self.grid.selectedItems()
        if not items:
            return
        self._selected_tree_item = None
        block_type = self.grid.item(items[0].row(), 0).text()
        cls = registry.get(block_type, "faceplate") \
            or next(iter(registry.for_block_type(block_type).values()),
                    None)
        self.show_class(cls)

    def show_class(self, cls: type | None) -> None:
        self._current_cls = cls
        self._current_user_class = ""
        self._current_symbol = ""
        self._current_symbol_user = False
        self.pvm_config.setVisible(cls is not None)
        self.edit_layout.hide()
        self.edit_source.setVisible(cls is not None)
        if cls is None:
            self.class_title.setText("Class inspector")
            return
        self.class_title.setText(
            f"{cls.block_type} / {cls.role}"
            + (f" · {cls.variant}" if cls.variant else ""))
        n = len(cls.PARAMS)
        if cls.EXCEPTION:
            self.params_label.setText(
                f"Parameters  {', '.join(cls.PARAMS)} — counted "
                f"exception: {cls.EXCEPTION[:60]}…")
        else:
            self.params_label.setText(
                f"Parameters  {cls.PARAMS[0]} — {n} of 1 ✓")
        self.bindings.setRowCount(len(cls.bindings))
        for i, spec in enumerate(cls.bindings):
            self.bindings.setItem(i, 0, QTableWidgetItem(spec.key))
            if spec.expr:
                text = f"expr: {spec.expr}"
            elif spec.prop:
                text = f"{spec.path} → {spec.prop}"
            else:
                text = spec.path + (" · writable" if spec.writable
                                    else "") \
                    + (" · persists" if spec.persists else "")
            self.bindings.setItem(i, 1, QTableWidgetItem(text))
        count = self._instance_counter(cls.block_type)
        self.coverage_label.setText(f"Covers {count} instance(s) · "
                                    "overrides 0")
        preview = None
        try:
            from .configurator.designer import _coded_class_preview
            preview = _coded_class_preview(cls, width=200, height=88)
        except Exception:                           # noqa: BLE001
            log.warning("Unable to render the PVM class preview for %s",
                        cls.__name__, exc_info=True)
        placeable = cls.role in ("dynamo_compact", "dynamo_inline")
        role_name = cls.role.replace("dynamo_", "").replace("_", " ")
        primary = ("Place on Display", self._place_current_class) \
            if placeable else ("Configure Class", self._open_config)
        self._set_action_card(
            cls.__name__, "Installed",
            f"{cls.block_type} {role_name} class with "
            f"{len(cls.bindings)} live binding(s). "
            + ("Place an instance, then assign its Control Tag."
               if placeable else
               "This contextual surface opens from its paired PVM."),
            preview=preview, primary=primary,
            secondary=("Details", self._show_details))

    def show_user_class(self, name: str) -> None:
        """Inspect a persisted drawing class, not a synthetic Python class."""
        self._current_cls = None
        self._current_user_class = name
        self._current_symbol = ""
        self._current_symbol_user = False
        self.pvm_config.show()
        self.edit_layout.show()
        self.edit_source.hide()
        record = self.catalog.record(name) if self.catalog else None
        if record is None or self.library_root is None:
            self.class_title.setText("Class inspector")
            return
        from azeo_control_trainer.core.hmi.pvms.user_library import UserPvmLibrary
        entry = UserPvmLibrary(self.library_root).entries.get(name, {})
        self.class_title.setText(f"{name}  ·  {record.kind_title}")
        contract = "typed contract" if record.configured \
            else "configuration required"
        self.params_label.setText(
            f"{record.item_count} elements  ·  "
            f"{record.width:g}×{record.height:g} px  ·  {contract}")

        refs: list[tuple[str, str]] = []

        def walk(value, location: str) -> None:
            if isinstance(value, str) and value.startswith(
                    (*PVM_SCOPE_PREFIXES, "Standard.")):
                refs.append((location, value))
            elif isinstance(value, dict):
                for key, child in value.items():
                    walk(child, f"{location}.{key}" if location else key)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    walk(child, f"{location}[{index}]")

        for index, item in enumerate(entry.get("items", ())):
            walk(item, str(item.get("id") or f"element[{index}]"))
        self.bindings.setRowCount(len(refs))
        for row, (target, reference) in enumerate(refs):
            self.bindings.setItem(row, 0, QTableWidgetItem(target))
            self.bindings.setItem(row, 1, QTableWidgetItem(reference))

        pair = ", ".join(record.paired_with) or "none"
        usages = ", ".join(record.used_by) or "none"
        issue = "; ".join(record.issues) or "validated"
        self.coverage_label.setText(
            f"{record.status}  ·  Pair: {pair}\n"
            f"Used by: {usages}\n{issue}")
        placeable = record.kind == "pvm"
        primary = ("Place on Display", self._place_current_user_class) \
            if placeable else ("Edit Layout", self._open_layout)
        self._set_action_card(
            name, record.status,
            f"Project-authored {record.kind_title.lower()} with "
            f"{record.item_count} elements; used by "
            f"{record.usage_count} display(s). "
            + ("Place a linked instance or edit the reusable layout."
               if placeable else
               "Edit the operator surface or its typed properties."),
            preview_text=(f"{record.width:g} x {record.height:g} px\n"
                          f"{record.item_count} ELEMENTS"),
            primary=primary,
            secondary=("Configure", self._open_config))

    def show_symbol(self, name: str, user: bool = False) -> None:
        self._current_cls = None
        self._current_user_class = ""
        self._current_symbol = name
        self._current_symbol_user = user
        self.class_title.setText(f"{name}  ·  Special Symbol")
        self.params_label.setText(
            "Project SVG · scalable vector" if user else
            "Installed faceplate artwork · scalable vector")
        self.bindings.setRowCount(0)
        self.coverage_label.setText(
            "Place on a display, then bind Visibility or assign an "
            "Interaction action in Graphics Configuration.")
        self.pvm_config.hide()
        self.edit_layout.hide()
        self.edit_source.hide()
        preview = None
        try:
            if user:
                from azeo_control_trainer.core.hmi.pvms.symbols import preview_pixmap
                preview = preview_pixmap(name, 160, 80)
            else:
                from azeo_control_trainer.core.hmi.pvms.faceplate_icons import special_symbol_pixmap
                preview = special_symbol_pixmap(name, 160, 80)
        except Exception:                           # noqa: BLE001
            log.warning("Unable to render the symbol preview for %s", name,
                        exc_info=True)
        self._set_action_card(
            name, "Project" if user else "Installed",
            "Scalable vector artwork. Place it on the active display, "
            "then bind Visibility or Interaction in Graphics Configuration.",
            preview=preview, preview_text="SPECIAL SYMBOL",
            primary=("Place on Display", self._place_current_symbol),
            secondary=("Details", self._show_details))

    def _open_config(self) -> None:
        """The PVM Configuration Designer, for the inspected class."""
        window = self.window()
        if hasattr(window, "open_pvm_config"):
            name = self._current_user_class or (
                self._current_cls.__name__
                if self._current_cls is not None else "")
            window.open_pvm_config(name)

    def _open_layout(self) -> None:
        if not self._current_user_class:
            return
        opener = getattr(self.window(), "edit_user_pvm_layout", None)
        if callable(opener):
            opener(self._current_user_class)

    def _open_source(self) -> None:
        if self._current_cls is None:
            return
        try:
            path = inspect.getsourcefile(self._current_cls)
            if path:
                os.startfile(path)                  # noqa: S606
        except Exception:                           # noqa: BLE001
            log.exception("Unable to open source for %s",
                          self._current_cls.__name__)
