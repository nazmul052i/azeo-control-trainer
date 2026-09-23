"""Searchable control-object and parameter selector for Graphics Designer.

The docked control browser and this dialog deliberately share
``ControlBrowser``.  The dialog adds the work an engineer needs at selection
time: scoped full-path search, an unambiguous selection preview, and separate
block/parameter contracts so a Data Link can never accidentally receive only
its parent block.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.authoring_controls import AuthoringComboBox
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QSplitter, QVBoxLayout,
)

from azeo_control_trainer.core.presentation.headless import is_headless
from azeo_control_trainer.core.presentation.brand import AUTHORING_BLUE
from azeo_control_trainer.core.presentation.authoring_style import AUTHORING_CONTROLS_QSS
from azeo_control_trainer.core.presentation.authoring_dialog import (
    add_authoring_dialog_header,
    apply_authoring_dialog,
    style_dialog_buttons,
)


class ParameterBrowserDialog(QDialog):
    """Select a configured control block or one addressable parameter.

    ``selection`` is ``"block"`` for PVM Control Tags, ``"parameter"`` for
    Data Links/bindings, or ``"any"`` for diagnostic callers. Keeping the
    contract explicit prevents the old behavior where a parameter field
    browsed only to a block and guessed ``OUT`` afterwards.
    """

    _SCOPES = (
        ("All control data", "all"),
        ("Blocks", "block"),
        ("Input parameters", "input"),
        ("Output parameters", "output"),
        ("Configuration", "configuration"),
    )

    def __init__(self, graphs_provider, current: str = "", parent=None,
                 *, selection: str = "block", block_types=None):
        super().__init__(parent)
        if selection not in {"block", "parameter", "any"}:
            raise ValueError(f"unknown browser selection mode: {selection}")
        self.selection_mode = selection
        self.block_types = None if block_types is None else frozenset(block_types)
        self._graphs_provider = graphs_provider
        noun = "parameter" if selection == "parameter" else "control object"
        self.setWindowTitle(f"Select Azeo {noun}")
        self.resize(760, 590)
        self.setMinimumSize(620, 460)
        self.selected_path = ""
        self.selected_type = ""
        self.selected_kind = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        browser_title = (
            "Control parameter browser" if selection == "parameter"
            else "Control object browser"
        )
        instruction = (
            "Search by module, block, parameter, data type, current value, "
            "or full path. Use spaces to combine terms."
        )
        add_authoring_dialog_header(self, root, browser_title, instruction)

        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setObjectName("parameter_search")
        self.search.setPlaceholderText(
            "Search, for example: U400 TIC PV  or  CONFIG HI_LIM")
        self.search.setClearButtonEnabled(True)
        self.scope = AuthoringComboBox()
        self.scope.setObjectName("parameter_scope")
        for label, value in self._SCOPES:
            self.scope.addItem(label, value)
        if selection == "block":
            self.scope.setCurrentIndex(self.scope.findData("block"))
        search_row.addWidget(self.search, 1)
        search_row.addWidget(self.scope)
        root.addLayout(search_row)

        from .studio.browser import ControlBrowser
        self.browser = ControlBrowser(graphs_provider)
        self.browser.setObjectName("parameter_tree")
        self.browser.setSelectionMode(QAbstractItemView.SingleSelection)

        details = QFrame()
        details.setObjectName("parameter_details")
        details.setMinimumWidth(220)
        detail_layout = QVBoxLayout(details)
        detail_layout.setContentsMargins(14, 12, 14, 12)
        detail_layout.setSpacing(6)
        detail_layout.addWidget(QLabel("SELECTION"))
        self.path_label = QLabel("Select a row in the browser")
        self.path_label.setObjectName("parameter_path")
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        detail_layout.addWidget(self.path_label)
        self.kind_label = QLabel("")
        self.kind_label.setObjectName("parameter_kind")
        self.kind_label.setWordWrap(True)
        detail_layout.addWidget(self.kind_label)
        self.value_label = QLabel("")
        self.value_label.setObjectName("parameter_value")
        self.value_label.setWordWrap(True)
        detail_layout.addWidget(self.value_label)
        detail_layout.addStretch(1)
        self.copy_button = QPushButton("Copy full path")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self._copy_path)
        detail_layout.addWidget(self.copy_button)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.browser)
        split.addWidget(details)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 2)
        split.setSizes([510, 230])
        root.addWidget(split, 1)

        footer = QHBoxLayout()
        self.result_count = QLabel()
        self.result_count.setObjectName("parameter_result_count")
        footer.addWidget(self.result_count, 1)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok_button = self.buttons.button(QDialogButtonBox.Ok)
        self.ok_button.setText(
            "Use Parameter" if selection == "parameter" else "Use Object")
        self.ok_button.setEnabled(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        style_dialog_buttons(self.buttons, QDialogButtonBox.Ok)
        footer.addWidget(self.buttons)
        root.addLayout(footer)

        self.browser.itemDoubleClicked.connect(self._choose)
        self.browser.currentItemChanged.connect(
            lambda item, _previous: self._picked(item))
        self.search.textChanged.connect(self._filter)
        self.scope.currentIndexChanged.connect(
            lambda _index: self._filter(self.search.text()))

        self.setStyleSheet(f"""
            QLabel#browser_title {{ color: {AUTHORING_BLUE}; font-weight: 700; }}
            QLabel#browser_instruction, QLabel#parameter_result_count,
            QLabel#parameter_kind, QLabel#parameter_value {{ color: #5B6975; }}
            QFrame#parameter_details {{
                background: #F5F7F9; border: 1px solid #CAD3DB;
            }}
            QLabel#parameter_path {{
                color: {AUTHORING_BLUE}; font-weight: 600; padding: 6px 0;
            }}
            QTreeWidget#parameter_tree {{ border: 1px solid #B9C5CF; }}
            QLineEdit#parameter_search, QComboBox#parameter_scope {{
                min-height: 27px;
            }}
        """ + AUTHORING_CONTROLS_QSS)
        apply_authoring_dialog(self)

        self._filter("")
        if current:
            self._select_path(current)
        self.search.setFocus()

    # ------------------------------------------------------------ picking
    def _selection_payload(self, item):
        from .studio.browser import ControlBrowser

        parameter = ControlBrowser.parameter_payload(item)
        block = ControlBrowser.block_payload(item)
        family = block[1] if block else (parameter or {}).get("block_type")
        if self.block_types is not None and family not in self.block_types:
            return None
        if parameter and self.selection_mode in {"parameter", "any"}:
            return (str(parameter.get("path", "")),
                    str(parameter.get("data_type", "")), "parameter")
        if block and self.selection_mode in {"block", "any"}:
            return str(block[0]), str(block[1]), "block"
        return None

    def _picked(self, item, _col=0) -> None:
        from .studio.browser import ControlBrowser

        self.selected_path = ""
        self.selected_type = ""
        self.selected_kind = ""
        payload = self._selection_payload(item)
        parameter = ControlBrowser.parameter_payload(item)
        block = ControlBrowser.block_payload(item)
        path = ControlBrowser.item_path(item)
        kind = ControlBrowser.item_kind(item)
        self.path_label.setText(path or "Select a row in the browser")

        if parameter:
            direction = str(parameter.get("direction", "parameter")).title()
            dtype = str(parameter.get("data_type", "")) or "Unspecified"
            block_type = str(parameter.get("block_type", ""))
            self.kind_label.setText(
                f"{direction} parameter  \u00b7  {dtype}"
                + (f"  \u00b7  {block_type} block" if block_type else ""))
            self.value_label.setText(
                f"Current value: {parameter.get('value', '')}\n"
                f"Units: {parameter.get('units') or 'Unspecified'}\n"
                f"Range: {parameter.get('range') or 'Unspecified'}\n"
                f"Quality: {parameter.get('quality', 'Unknown')}")
            from azeo_control_trainer.core.hmi.binding.source import LiveGraphSource
            access = LiveGraphSource(self._graphs_provider).can_write(path)
            self.value_label.setText(self.value_label.text() + "\nSource write capability: "
                                     + ("Available" if access.success else "Read only · " + access.error)
                                     + "\nOperator authorization is checked at runtime.")
        elif block:
            self.kind_label.setText(f"Control block  \u00b7  {block[1]}")
            graph = self._graphs_provider().get(block[0].split("/", 1)[0])
            self.value_label.setText(str(getattr(graph, "description", "") or "")
                                     + "\nExpand the block to inspect parameter units, ranges and quality.")
        else:
            self.kind_label.setText(kind.title() if kind else "")
            self.value_label.setText("")

        if payload:
            self.selected_path, self.selected_type, self.selected_kind = payload
        self.ok_button.setEnabled(bool(payload and self.selected_path))
        family = block[1] if block else (parameter or {}).get("block_type")
        if self.block_types is not None and (block or parameter) and family not in self.block_types:
            self.value_label.setText("Requires: " + ", ".join(sorted(self.block_types)))
        self.copy_button.setEnabled(bool(path))

    def _choose(self, item, _col=0) -> None:
        self._picked(item)
        if self.selected_path:
            self.accept()

    def accept(self) -> None:
        if self.selected_path:
            super().accept()

    def _copy_path(self) -> None:
        from PySide6.QtWidgets import QApplication

        text = self.selected_path or self.path_label.text()
        if text and text != "Select a row in the browser":
            QApplication.clipboard().setText(text)

    @staticmethod
    def _walk(item):
        yield item
        for index in range(item.childCount()):
            yield from ParameterBrowserDialog._walk(item.child(index))

    def _items(self):
        for index in range(self.browser.topLevelItemCount()):
            yield from self._walk(self.browser.topLevelItem(index))

    def _select_path(self, path: str) -> None:
        from .studio.browser import ControlBrowser

        wanted = path.strip().strip("/").lower()
        for item in self._items():
            if ControlBrowser.item_path(item).strip("/").lower() == wanted:
                parent = item.parent()
                while parent is not None:
                    parent.setExpanded(True)
                    parent = parent.parent()
                self.browser.setCurrentItem(item)
                self.browser.scrollToItem(item)
                self._picked(item)
                return

    def _scope_match(self, item) -> bool:
        from .studio.browser import ControlBrowser

        scope = str(self.scope.currentData() or "all")
        if self.block_types is not None:
            block = ControlBrowser.block_payload(item)
            parameter = ControlBrowser.parameter_payload(item)
            family = block[1] if block else (parameter or {}).get("block_type")
            if family not in self.block_types:
                return False
        if scope == "all":
            return True
        if scope == "block":
            return ControlBrowser.block_payload(item) is not None
        parameter = ControlBrowser.parameter_payload(item)
        if parameter is None:
            return False
        direction = str(parameter.get("direction", ""))
        return direction == scope

    def _filter(self, text: str) -> None:
        from .studio.browser import ControlBrowser

        terms = tuple(part.lower() for part in text.strip().split() if part)

        def own_match(item) -> bool:
            if not self._scope_match(item):
                return False
            blob = ControlBrowser.search_text(item)
            return all(term in blob for term in terms)

        matches = 0

        def walk(item) -> bool:
            nonlocal matches
            child_hit = False
            for index in range(item.childCount()):
                child_hit = walk(item.child(index)) or child_hit
            hit = (not terms and self._scope_match(item)) or own_match(item)
            visible = hit or child_hit
            item.setHidden(not visible)
            if terms and item.childCount():
                item.setExpanded(child_hit)
            if hit and self._selection_payload(item):
                matches += 1
            return visible

        for index in range(self.browser.topLevelItemCount()):
            top = self.browser.topLevelItem(index)
            walk(top)
            if not terms:
                top.setExpanded(True)

        noun = "parameter" if self.selection_mode == "parameter" else "object"
        self.result_count.setText(
            f"{matches} matching {noun}{'' if matches == 1 else 's'}")
        current = self.browser.currentItem()
        # A filtered-out tag must not remain the hidden target of Use Object.
        self._picked(current if current is not None and not current.isHidden() else None)

    @classmethod
    def browse(cls, graphs_provider, current: str = "", parent=None,
               *, selection: str = "block", block_types=None) -> tuple[str, str] | None:
        """Open modally and return ``(full path, type)``; None on cancel."""
        if is_headless():
            return None
        dialog = cls(graphs_provider, current, parent, selection=selection, block_types=block_types)
        try:
            if dialog.exec() != QDialog.Accepted or not dialog.selected_path:
                return None
            return dialog.selected_path, dialog.selected_type
        finally:
            dialog.deleteLater()
