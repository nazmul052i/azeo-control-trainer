"""The panes around the canvas.

- **_Snippet** — hover a PVM, read the whole point without clicking.
- **_WatchArea** — a drop target that needs no configuration.
- **_AlarmBanner** — the strip along the canvas bottom (sheet 01).
- **_PathField** / **_ConfigPane** — the Assembler's property pane:
  bind by path, pick a class, choose a standard and optionally override
  the PVM's ordinary fill and line colours.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from azeo_control_trainer.core.presentation.authoring_controls import AuthoringComboBox
from azeo_control_trainer.core.presentation.authoring_style import AUTHORING_FIELDS_QSS
from azeo_control_trainer.core.presentation.authoring_dialog import (
    add_authoring_dialog_header,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QMessageBox, QPushButton, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from azeo_control_trainer.core.hmi.pvms.base import registry
from azeo_control_trainer.core.hmi.pvms.shapes import SHAPE_KINDS
from azeo_control_trainer.core.hmi.pvms.rendering.chrome import WF
from azeo_control_trainer.core.hmi.pvms.rendering.items import (
    PvmItem, item_document_data,
)

if TYPE_CHECKING:                       # pragma: no cover
    # The window builds the panes, so a real import would be
    # circular; `from __future__ import annotations` keeps the
    # annotation a string.
    from .assembler import PvmStudio


log = logging.getLogger("azeo.graphics_designer.properties")


_SECTION_STYLE = (
    f"background: {WF['chrome']}; color: {WF['tx2']};"
    f"border: none; border-bottom: 1px solid {WF['bd_lt']};"
    "font-size: 9pt; font-weight: 600;"
    "padding: 4px 6px; margin-top: 6px;")

# Fields inherit the pane's shared sheet. Parsing a complete field stylesheet
# separately on every inspector editor made selection changes unnecessarily slow.
_FIELD_STYLE = ""


def _fill_symbol_combo(combo, current: str, *, blank_label: str = "") -> None:
    """List the whole symbol catalog on one combo, grouped by category.

    Vendored and project-imported artwork share the list because they
    share the catalog: an engineer hunting for the symbol they imported
    should not have to know it came in by a different door. Signals are
    blocked while filling so populating a form is never an edit.
    """
    from azeo_control_trainer.core.hmi.pvms.symbols import CATALOG, USER_SYMBOLS

    combo.blockSignals(True)
    try:
        combo.clear()
        if blank_label:
            combo.addItem(blank_label, "")
        for name, (_rel, title, caption) in sorted(
                CATALOG.items(), key=lambda kv: (kv[1][2], kv[1][1])):
            suffix = "  ·  imported" if name in USER_SYMBOLS else ""
            combo.addItem(f"{str(caption).title()}  ·  {title}{suffix}",
                          name)
        index = combo.findData(current)
        if index < 0 and current:
            # A display may name a symbol this project does not have.
            # Showing it as missing keeps the engineer's choice visible
            # instead of silently retargeting the item on first open.
            combo.addItem(f"Missing  ·  {current}", current)
            index = combo.count() - 1
        combo.setCurrentIndex(max(index, 0))
    finally:
        combo.blockSignals(False)


def _polish_form(form: QFormLayout) -> None:
    """Give every inspector group one aligned, breathable row rhythm."""
    form.setContentsMargins(0, 2, 0, 2)
    form.setHorizontalSpacing(9)
    form.setVerticalSpacing(7)
    form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    # The inspector is 320–360 px wide. A label beside a styled selector can
    # exceed its viewport even with horizontal scrolling disabled. Put labels
    # above full-width fields consistently, including hidden/dynamic groups.
    form.setRowWrapPolicy(QFormLayout.WrapAllRows)


def _wrap_property_label(label: QLabel) -> None:
    """Long names must wrap inside the inspector, never set its minimum width."""
    label.setWordWrap(True)
    label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    label.setMinimumWidth(0)


class _DescriptionField(QPlainTextEdit):
    """Wrap prose while keeping one undo checkpoint per completed edit."""
    editingFinished = Signal()

    def __init__(self):
        super().__init__()
        self.setTabChangesFocus(True)
        self.setFixedHeight(76)

    def focusOutEvent(self, event):  # noqa: N802
        super().focusOutEvent(event)
        self.editingFinished.emit()


class _ColorField(QLineEdit):
    def __init__(self):
        super().__init__()
        from PySide6.QtGui import QAction
        self._swatch = QAction("Choose color", self)
        self._swatch.triggered.connect(self._choose)
        self.addAction(self._swatch, QLineEdit.TrailingPosition)
        self.textChanged.connect(self._update_swatch)
        self._update_swatch()

    def _update_swatch(self):
        from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
        pixmap = QPixmap(18, 18)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        try:
            painter.setPen(QColor(WF["bd"]))
            colour = QColor(self.text())
            painter.setBrush(colour if colour.isValid() else QColor(WF["pane"]))
            painter.drawRoundedRect(1, 1, 15, 15, 3, 3)
        finally:
            painter.end()
        self._swatch.setIcon(QIcon(pixmap))

    def _choose(self):
        from azeo_control_trainer.core.presentation.headless import is_headless
        if is_headless():
            return
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QColorDialog
        color = QColorDialog.getColor(QColor(self.text() or "#526176"), self, "Choose color")
        if color.isValid():
            self.setText(color.name())
            self.editingFinished.emit()


class _Snippet(QWidget):
    """Azeo Operator Station's snippet: hover a PVM, read the whole point without
    leaving the display — name, mode, PV/OUT, alarm limits, Bad/Forced.
    """

    def __init__(self, parent=None):
        super().__init__(parent, Qt.ToolTip
                         | Qt.FramelessWindowHint)
        self.setStyleSheet(
            f"background: {WF['pane']}; border: 1px solid "
            f"{WF['lapis']}; color: {WF['tx']};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(9, 6, 9, 6)
        lay.setSpacing(2)
        self.title = QLabel("")
        self.title.setFont(QFont("Segoe UI", 9, QFont.Bold))
        self.title.setStyleSheet("border: none;")
        lay.addWidget(self.title)
        self.body = QLabel("")
        self.body.setFont(QFont("Consolas", 8))
        self.body.setStyleSheet(f"border: none; color: {WF['tx2']};")
        lay.addWidget(self.body)
        self.flags = QLabel("")
        self.flags.setFont(QFont("Segoe UI", 8, QFont.Bold))
        self.flags.setStyleSheet("border: none;")
        lay.addWidget(self.flags)

    def show_for(self, item: "PvmItem", screen_pos) -> None:
        pvm = item.pvm
        tag = "/".join(str(v) for v in pvm.params.values())
        friendly = pvm.label
        self.title.setText(f"{friendly}  ·  {tag}" if friendly
                           else tag)
        result = item.binding.result if item.binding else None
        lines = []
        mode = item.mode_binding.result.value \
            if item.mode_binding else ""
        if mode:
            lines.append(f"MODE  {mode}")
        if result is not None and result.value is not None:
            unit = f" {result.units}" if result.units else ""
            try:
                lines.append(f"PV    {float(result.value):g}{unit}")
            except (TypeError, ValueError):
                lines.append(f"PV    {result.value}")
        out = item.rows.get("OUT")
        if out is not None and out.result.value is not None:
            try:
                lines.append(f"OUT   {float(out.result.value):g}")
            except (TypeError, ValueError):
                pass
        limits = []
        for name in ("HH", "H", "L", "LL"):
            binding = item.rows.get(name)
            if binding is not None \
                    and binding.result.value is not None:
                limits.append(f"{name} {binding.result.value:g}")
        if limits:
            lines.append("LIMS  " + " / ".join(limits))
        self.body.setText("\n".join(lines) or "no live data")
        flags = []
        if result is not None and result.quality.name == "BAD":
            flags.append(("BAD I/O", WF["crit"]))
        if result is not None and result.forced:
            flags.append(("FORCED", WF["crit"]))
        if result is not None and result.alarm_active:
            flags.append((result.alarm_condition or "ALARM",
                          WF["crit"]))
        self.flags.setText("   ".join(f for f, _ in flags))
        self.flags.setStyleSheet(
            f"border: none; color: {WF['crit']};")
        self.flags.setVisible(bool(flags))
        self.adjustSize()
        self.move(int(screen_pos.x()) + 14, int(screen_pos.y()) + 10)
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not is_headless():
            self.show()


class _WatchArea(QWidget):
    """The runtime Watch Area: an ad-hoc list of points the operator
    assembles for an upset or a shift — no configuration, no
    engineering change. Rows read by friendly name, which is what makes
    the list readable to the next operator."""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Tool)
        self.setWindowTitle("Watch Area")
        self.setStyleSheet(f"background: {WF['pane']}; color: {WF['tx']};")
        self.resize(260, 220)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        from PySide6.QtWidgets import QTableWidget
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Point", "Value"])
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnWidth(0, 140)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        lay.addWidget(self.table)
        hint = QLabel("Right-click a PVM → Add to Watch Area")
        hint.setStyleSheet(f"color: {WF['tx3']}; font-size: 9pt;")
        lay.addWidget(hint)
        self.rows: list = []            # (label, binding)

    def add_row(self, label: str, binding) -> None:
        self.rows.append((label, binding))
        self.refresh()

    def refresh(self) -> None:
        from PySide6.QtWidgets import QTableWidgetItem
        self.table.setRowCount(len(self.rows))
        for i, (label, binding) in enumerate(self.rows):
            result = binding.result
            if result.quality.name == "BAD":
                text = "– – –"
            elif result.value is None:
                text = ""
            else:
                unit = f" {result.units}" if result.units else ""
                try:
                    text = f"{float(result.value):g}{unit}"
                except (TypeError, ValueError):
                    text = str(result.value)
            for column, value in ((0, label), (1, text)):
                cell = self.table.item(i, column)
                if cell is None:
                    self.table.setItem(i, column, QTableWidgetItem(value))
                elif cell.text() != value:
                    cell.setText(value)


class _AlarmBanner(QWidget):
    """One readable alert summary; the full live list remains one click away."""

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QSizePolicy
        self.setStyleSheet("background: #FFF8EB; color: #805516; border: none;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 3, 6, 3)
        self.summary = QLabel()
        self.summary.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.summary.setMinimumWidth(0)
        lay.addWidget(self.summary, 1)
        self.details_button = QPushButton("Details…")
        self.details_button.setAccessibleName("Show all display alerts")
        self.details_button.clicked.connect(self.show_details)
        lay.addWidget(self.details_button)
        self.entries = []
        self._dialog = None
        self.hide()

    def set_alarms(self, entries: list) -> None:
        changed = entries != self.entries
        self.entries = list(entries)
        self.setVisible(bool(entries))
        if entries:
            priority, tag, condition, _since = entries[0]
            if all(str(entry[2]).upper() == "BAD QUALITY" for entry in entries):
                message = f"{len(entries)} PVMs have bad data quality · Check bindings and controller status"
            else:
                message = f"{priority} · {tag or 'Unbound PVM'} · {condition}"
                if len(entries) > 1:
                    message += f" · {len(entries) - 1} more alerts"
            self.summary.setText(message)
            self.summary.setToolTip(message)
        if changed and self._dialog is not None:
            self._refresh_details()

    def show_details(self):
        from PySide6.QtWidgets import QDialog, QHeaderView, QTableWidget
        if self._dialog is None:
            self._dialog = QDialog(self)
            self._dialog.setWindowTitle("Display alerts — Graphics Designer")
            self._dialog.resize(720, 340)
            layout = QVBoxLayout(self._dialog)
            add_authoring_dialog_header(
                self._dialog,
                layout,
                "Display alerts",
                "Active display conditions ordered by priority and onset time.",
            )
            self._table = QTableWidget(0, 4)
            self._table.setHorizontalHeaderLabels(["Priority", "Control tag", "Condition", "Since"])
            self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            self._table.verticalHeader().hide()
            self._table.setEditTriggers(QTableWidget.NoEditTriggers)
            layout.addWidget(self._table)
        self._refresh_details()
        self._dialog.show()
        self._dialog.raise_()
        return self._dialog

    def _refresh_details(self):
        from PySide6.QtWidgets import QTableWidgetItem
        self._table.setRowCount(len(self.entries))
        for row, entry in enumerate(self.entries):
            for column, value in enumerate(entry):
                self._table.setItem(row, column, QTableWidgetItem(str(value)))


# ───────────────────────────────────────────────── configuration pane
class _PathField(QWidget):
    """The Control Tag field, Azeo Operator Station style (pp.29–30): type it
    or browse it (the ellipsis opens the parameter browser); Enter
    verifies — blue solid underline when the path resolves, red when
    it does not, with the two documented causes in the tooltip."""

    def __init__(self, pane, parent=None, parameter="path"):
        super().__init__(parent)
        from PySide6.QtWidgets import QLineEdit
        self.pane = pane
        self.parameter = parameter
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.edit = QLineEdit()
        self.edit.setFont(QFont("Consolas", 8))
        self.edit.editingFinished.connect(self._apply)
        lay.addWidget(self.edit, 1)
        self.browse_btn = QPushButton("…")
        self.browse_btn.setFixedSize(22, 20)
        self.browse_btn.setToolTip(
            "Browse Azeo control parameters")
        self.browse_btn.clicked.connect(self._browse)
        lay.addWidget(self.browse_btn)
        self._style(True)

    # ------------------------------------------------------ plumbing
    def _studio(self):
        item = getattr(self.pane, "_current_item", None)
        scene = item.scene() if item is not None else None
        return getattr(scene, "studio", None) if scene else None

    def setText(self, text: str) -> None:           # noqa: N802
        self.edit.blockSignals(True)
        self.edit.setText(text)
        self.edit.blockSignals(False)
        studio = self._studio()
        self._style(self._resolves(studio, text)
                    if studio is not None else True)

    def _resolves(self, studio, path):
        item = getattr(self.pane, "_current_item", None)
        cls = registry.get(item.pvm.block_type, item.pvm.role, item.pvm.variant) if item else None
        expected = getattr(cls, "PARAM_TYPES", {}).get(self.parameter, "")
        return studio.path_resolves(path, expected) if expected else studio.path_resolves(path)

    def text(self) -> str:
        return self.edit.text()

    def _style(self, resolved: bool) -> None:
        if resolved:
            self.edit.setStyleSheet(
                f"QLineEdit {{ background: {WF['chrome']};"
                "border: 1px solid transparent;"
                f"border-bottom: 2px solid {WF['lapis']};"
                f"color: {WF['lapis']}; padding: 1px 5px; }}")
            self.edit.setToolTip("Resolved against the loaded "
                                 "configuration.")
        else:
            self.edit.setStyleSheet(
                f"QLineEdit {{ background: {WF['chrome']};"
                "border: 1px solid transparent;"
                f"border-bottom: 2px dotted {WF['crit']};"
                f"color: {WF['crit']}; padding: 1px 5px; }}")
            self.edit.setToolTip(
                "The path could not be resolved: a typo, or the "
                "module is not present in the configuration.")

    # ------------------------------------------------------- actions
    def _apply(self) -> None:
        studio = self._studio()
        item = getattr(self.pane, "_current_item", None)
        if studio is None or item is None:
            return
        path = self.edit.text().strip()
        resolved = self._resolves(studio, path)
        self._style(resolved)
        if (not resolved and path) or path == item.pvm.params.get(self.parameter, ""):
            return
        try:
            replacement = studio.set_pvm_path(item, path, self.parameter)
            replacement.setSelected(True)
            self.pane._current_item = replacement
        except Exception as error:                  # noqa: BLE001
            log.exception("Could not update PVM path")
            studio.uiError.emit(f"Could not update PVM path: {error}")

    def _browse(self) -> None:
        from azeo_control_trainer.core.presentation.headless import is_headless
        from ..param_browser import ParameterBrowserDialog
        studio = self._studio()
        if studio is None:
            return
        provider = studio.engine._source._graphs
        dialog = ParameterBrowserDialog(
            provider, self.edit.text(), parent=self, selection="block")
        self.pane._param_browser = dialog
        if is_headless():
            return
        if dialog.exec() == dialog.DialogCode.Accepted \
                and dialog.selected_path:
            self.edit.setText(dialog.selected_path)
            self._apply()


class _ConfigPane(QWidget):
    """Assembler properties: binding truth plus authored appearance."""

    #: (section, [(key, label, locked)]) — the wireframe's exact pane.
    _SECTIONS = (
        ("BINDING", (("path", "Path", False),
                     ("block_type", "Block type", False),
                     ("pvm_class", "PVM class", False),
                     ("status", "Status", False))),
        ("FROM TYPE", (("units", "Units", True),
                       ("eu_range", "EU range", True),
                       ("mode_set", "Mode set", True),
                       ("alarm_lims", "Alarm lims", True))),
        ("APPEARANCE", (("standard", "Standard", False),
                        ("fill", "Fill Color", False),
                        ("line", "Line Color", False),
                        ("symbol", "Artwork", False),
                        ("size_role", "Size", False))),
        ("PLACEMENT", (("placement", "X · Y", False),
                       ("wh", "W · H", False),
                       ("layer", "Layer", False))),
    )

    @staticmethod
    def _scroll_page(page: QWidget) -> QWidget:
        """Bound the inspector stack to the available application height.

        A bare QStackedWidget takes the minimum-size hint of its LARGEST
        hidden page. The drawing-element inspector is about 1,500 px tall;
        after the first selection invalidated layout, that hint made a
        maximized 1080 px window grow below the desktop and hid the status
        bar. One outer viewport owns vertical scrolling for whichever page is
        current, so hidden forms can remain detailed without changing the
        main-window frame or multiplying native scroll-area lifecycles.
        """
        # Keep these widgets local to the inspector construction. Importing
        # the ``studio`` package is also part of the lightweight faceplate
        # runtime path; it must not eagerly expand that path's Qt surface.
        from PySide6.QtWidgets import QScrollArea, QSizePolicy

        scroll = QScrollArea()
        scroll.setObjectName("property_scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        scroll.setWidget(page)
        return scroll

    def __init__(self, studio: PvmStudio):
        super().__init__()
        self.setObjectName("graphics_configuration")
        self.studio = studio
        self.setStyleSheet(
            f"QWidget#graphics_configuration {{ background: {WF['pane']}; color: {WF['tx']}; }}"
            + AUTHORING_FIELDS_QSS)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(4)
        self.title = QLabel("Properties")
        self.title.setObjectName("config_pane_title")
        self.title.setFont(QFont("Segoe UI", 9, QFont.Bold))
        self.title.setStyleSheet(f"color: {WF['navy']};")
        self.title.setToolTip(
            "Right-click for the selected element or display commands")
        self.title.setContextMenuPolicy(Qt.CustomContextMenu)
        self.title.customContextMenuRequested.connect(
            self._configuration_context_menu)
        outer.addWidget(self.title)
        search_button = QPushButton("Find a property…")
        search_button.clicked.connect(self.open_property_search)
        outer.addWidget(search_button)

        # Azeo Operator Station: with nothing selected the pane shows the
        # DISPLAY's own properties (Basics and Interaction).
        from PySide6.QtWidgets import QStackedWidget
        self._stack = QStackedWidget()
        display_scroll = self._scroll_page(self._build_display_page())
        self._stack.addWidget(display_scroll)

        pvm_page = QWidget()
        lay = QVBoxLayout(pvm_page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        pvm_scroll = self._scroll_page(pvm_page)
        item_scroll = self._scroll_page(self._build_item_page())
        self._stack.addWidget(pvm_scroll)
        self._stack.addWidget(item_scroll)
        # Each page keeps one stable viewport. Scrolling the stack itself made
        # a page change relayout the 1,500 px hidden drawing form inside the
        # same native viewport; on Windows/PySide 6.9 that could corrupt the
        # heap. Page-local viewports also isolate every hidden size hint from
        # the main-window geometry.
        self._stack_scroll = display_scroll
        outer.addWidget(self._stack, 1)
        self.rows: dict[str, QLabel] = {}
        #: PVM placement editors that are not text rows (the artwork
        #: combo). Kept apart so `rows` stays a dict of `.text()` widgets.
        self.pvm_fields: dict = {}
        for section, fields in self._SECTIONS:
            head_row = QHBoxLayout()
            head = QLabel(section)
            head.setStyleSheet(_SECTION_STYLE)
            head_row.addWidget(head)
            if section == "BINDING":
                self.selected_label = QLabel("")
                self.selected_label.setStyleSheet(
                    f"color: {WF['tx3']}; font-size: 9pt;"
                    "padding-top: 6px;")
                head_row.addStretch(1)
                head_row.addWidget(self.selected_label)
            if section == "FROM TYPE":
                ro = QLabel("read-only")
                ro.setStyleSheet(f"color: {WF['tx3']}; font-size: 9pt;"
                                 "padding-top: 6px;")
                head_row.addStretch(1)
                head_row.addWidget(ro)
            lay.addLayout(head_row)
            form = QFormLayout()
            _polish_form(form)
            for key, label, locked in fields:
                if key == "path":
                    # The Control Tag, Azeo Operator Station style (pp.29–30):
                    # editable, an ellipsis to the parameter browser,
                    # and verification on Enter — blue solid when the
                    # path resolves, red when it does not.
                    value = _PathField(self)
                elif key in ("fill", "line"):
                    value = _ColorField()
                    value.setPlaceholderText("Theme default")
                    value.setToolTip(
                        "Leave blank to follow the active display theme")
                    value.editingFinished.connect(
                        lambda k=key: self._apply_pvm_colour(k))
                elif key == "symbol":
                    # Artwork sits with fill and line because it is the
                    # same kind of decision: what this placement looks
                    # like. Ranges, modes, alarms and the faceplate stay
                    # class-owned, so I7 is not touched.
                    value = AuthoringComboBox()
                    value.setToolTip(
                        "The equipment drawing this PVM paints. Blank "
                        "follows the PVM class. Available only where the "
                        "class already draws equipment.")
                    value.currentIndexChanged.connect(
                        lambda _index: self._apply_pvm_symbol())
                else:
                    value = QLabel("—")
                    _wrap_property_label(value)
                    value.setFont(QFont("Consolas", 8))
                    value.setStyleSheet(
                        f"background: "
                        f"{WF['fld_ro'] if locked else WF['pane']};"
                        f"border: 1px solid {WF['bd']};"
                        "border-radius: 3px; min-height: 18px;"
                        f"padding: 3px 6px; color: {WF['tx']};")
                if key == "symbol":
                    # `rows` is a dict of text-bearing widgets and callers
                    # iterate it calling .text(). A combo belongs beside
                    # them on the form, not inside that contract.
                    self.pvm_fields[key] = value
                else:
                    self.rows[key] = value
                name = QLabel(label)
                if locked:
                    name.setToolTip("Inherited from the control type; read-only here")
                name.setStyleSheet(f"color: {WF['tx2']};"
                                   "font-size: 9pt;")
                form.addRow(name, value)
            lay.addLayout(form)
            if section == "FROM TYPE":
                note = QLabel(
                    "These come from the OPC UA type. Change them in "
                    "Control Designer and every display using this block "
                    "follows. They are not display properties.")
                note.setWordWrap(True)
                note.setStyleSheet(
                    f"background: {WF['sel']}; color: {WF['tx2']};"
                    f"border-left: 3px solid {WF['lapis']};"
                    "font-size: 9pt; padding: 5px 7px;")
                lay.addWidget(note)
        # CONFIGURATION — the class's Selection properties, when an
        # Author wrote a configuration document. The engineer picks
        # a NAMED option ("Left", never 270°); Presence decides which
        # selections even appear.
        self.choices_head = QLabel("CONFIGURATION")
        self.choices_head.setStyleSheet(_SECTION_STYLE)
        self.choices_head.setVisible(False)
        lay.addWidget(self.choices_head)
        self.choices_holder = QWidget()
        self.choices_form = QFormLayout(self.choices_holder)
        _polish_form(self.choices_form)
        self.choices_holder.setVisible(False)
        lay.addWidget(self.choices_holder)
        lay.addStretch(1)

    def open_property_search(self):
        from ..quick_access import PropertySearch, present_search
        self._property_search = present_search(PropertySearch(self))
        return self._property_search

    def _configuration_context_menu(self, pos) -> None:
        """Route the inspector header to the selected object's real menu.

        This keeps canvas, Selection pane and inspector commands identical.
        In particular, an inspector must not grow a second implementation of
        Delete, Duplicate, Watch or Display Properties.
        """
        global_pos = self.title.mapToGlobal(pos)
        page = self._stack.currentIndex()
        if page == 1:
            item = getattr(self, "_current_item", None)
            if item is not None and item.scene() is not None:
                item.setSelected(True)
                self.studio.pvm_context_menu(item, global_pos)
                return
        elif page == 2:
            item = getattr(self, "_current_static", None)
            if item is not None and item.scene() is not None:
                item.setSelected(True)
                self.studio.drawing_context_menu(item, global_pos)
                return
        scene_pos = self.studio.canvas.mapToScene(
            self.studio.canvas.viewport().rect().center())
        self.studio.canvas_context_menu(global_pos, scene_pos)

    def _rebuild_choices(self, item) -> None:
        while self.choices_form.count():
            entry = self.choices_form.takeAt(0)
            if entry.widget():
                widget = entry.widget()
                # Keep the holder as the C++ owner until DeferredDelete runs.
                # Detaching first transfers ownership to the Python wrapper;
                # when that local wrapper is collected and Qt later handles
                # deleteLater(), Windows reports heap corruption (0xC0000374).
                widget.deleteLater()
        studio = getattr(item.scene(), "studio", None)
        pvm_cls = registry.get(item.pvm.block_type, item.pvm.role,
                               item.pvm.variant)
        config = studio._pvm_config(pvm_cls, item.pvm.class_revision) \
            if studio is not None and pvm_cls is not None else None
        selections = []
        if config is not None:
            selections = [
                prop for prop in config.public_properties()
                if prop.ptype == "Selection" and prop.options
                and config.is_present(prop.name, item.pvm.choices)]
        extra_params = [param for param in getattr(pvm_cls, "PARAMS", ()) if param != "path"]
        self.choices_head.setVisible(bool(selections or extra_params))
        self.choices_holder.setVisible(bool(selections or extra_params))
        for param in extra_params:
            field = _PathField(self, parameter=param)
            field.setText(item.pvm.params.get(param, ""))
            label = getattr(pvm_cls, "PARAM_LABELS", {}).get(param, param)
            self.choices_form.addRow(label, field)
        for prop in selections:
            combo = AuthoringComboBox()
            combo.addItems([o.name for o in prop.options])
            combo.setCurrentText(
                item.pvm.choices.get(prop.name, prop.default))
            combo.currentTextChanged.connect(
                lambda option, it=item, name=prop.name, s=studio:
                self._apply_choice(s, it, name, option))
            label = QLabel(prop.title or prop.name)
            _wrap_property_label(label)
            label.setStyleSheet(f"color: {WF['tx2']};"
                                "font-size: 9pt;")
            holder = QWidget()
            choice_layout = QVBoxLayout(holder)
            choice_layout.setContentsMargins(0, 0, 0, 0)
            choice_layout.setSpacing(3)
            choice_layout.addWidget(combo)
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            choice_layout.addLayout(row)
            inherited = prop.name not in item.pvm.choices
            state = QLabel("Inherited" if inherited else "Instance")
            state.setToolTip("Class default" if inherited else "This instance selects a different class option")
            row.addWidget(state)
            row.addStretch(1)
            reset = QPushButton("Reset")
            reset.setEnabled(not inherited)
            reset.setToolTip(f"Restore class default: {prop.default}")
            reset.clicked.connect(lambda _=False, it=item, name=prop.name, default=prop.default, s=studio:
                                  self._apply_choice(s, it, name, default))
            row.addWidget(reset)
            self.choices_form.addRow(label, holder)
        if config is not None \
                and config.property("Typography") is not None \
                and studio is not None and pvm_cls is not None:
            edit_class = QPushButton("Edit class typography…")
            edit_class.setToolTip(
                "Edit exact typefaces in the shared PVM class profile")

            def open_typography(_checked=False, *, name=pvm_cls.__name__):
                opener = getattr(studio.window(), "open_pvm_config", None)
                if callable(opener):
                    opener(name)

            edit_class.clicked.connect(open_typography)
            self.choices_form.addRow(edit_class)

    def _apply_choice(self, studio, item, name, option) -> None:
        try:
            studio.enter_edit()
            replacement = studio.set_pvm_choice(item, name, option)
            replacement.setSelected(True)
        except Exception as error:                  # noqa: BLE001
            log.exception("Could not update PVM property")
            studio.uiError.emit(f"Could not update PVM property: {error}")

    def _apply_pvm_colour(self, key: str) -> None:
        """Persist one explicit PVM appearance value as an undoable edit."""
        from dataclasses import replace
        from PySide6.QtGui import QColor

        item = getattr(self, "_current_item", None)
        if item is None or key not in ("fill", "line"):
            return
        field = self.rows[key]
        value = field.text().strip()
        if value and not QColor(value).isValid():
            self.studio.uiError.emit(
                f"Invalid {key} color {value!r}; use a color name or #RRGGBB")
            return
        if value == getattr(item.pvm, key):
            return
        try:
            self.studio.enter_edit()
            self.studio.checkpoint()
            item.pvm = replace(item.pvm, **{key: value})
            item.set_palette(self.studio.palette_roles)
            item.update()
            self.studio.mark_unsaved()
        except Exception as error:                  # noqa: BLE001
            log.exception("Could not update PVM appearance")
            self.studio.uiError.emit(
                f"Could not update PVM {key} color: {error}")

    def _apply_pvm_symbol(self) -> None:
        """Persist the placement's artwork override as one undo step."""
        from dataclasses import replace

        item = getattr(self, "_current_item", None)
        if item is None:
            return
        value = str(self.pvm_fields["symbol"].currentData() or "")
        if value == item.pvm.symbol:
            return
        try:
            self.studio.enter_edit()
            self.studio.checkpoint()
            item.pvm = replace(item.pvm, symbol=value)
            item.set_palette(self.studio.palette_roles)
            item.update()
            # Connectors anchor on the artwork's visible ink, so new
            # artwork means new anchor geometry for anything glued to it.
            self.studio.reroute_pipes()
            self.studio.mark_unsaved()
        except Exception as error:                  # noqa: BLE001
            log.exception("Could not update PVM artwork")
            self.studio.uiError.emit(
                f"Could not update PVM artwork: {error}")

    def _build_item_page(self) -> QWidget:
        """The drawing-item property table — select a shape, its
        properties appear (the paper's Rectangle1 walkthrough, p.27):
        geometry, appearance, state. Every field applies on Enter and
        is one undo step."""
        from PySide6.QtWidgets import (
            QCheckBox, QDoubleSpinBox, QLineEdit, QSpinBox,
        )

        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.item_title = QLabel("\u2014")
        self.item_title.setObjectName("config_item_title")
        _wrap_property_label(self.item_title)
        lay.addWidget(self.item_title)
        from PySide6.QtWidgets import QTabWidget
        self.item_tabs = QTabWidget()
        self.item_tabs.setObjectName("inspector_tabs")
        self.item_tabs.setDocumentMode(True)
        lay.addWidget(self.item_tabs, 1)
        pages = []
        for caption in ("Design", "Data", "Behavior"):
            content = QWidget()
            content_layout = QVBoxLayout(content)
            content_layout.setContentsMargins(0, 6, 3, 0)
            content_layout.setSpacing(6)
            self.item_tabs.addTab(self._scroll_page(content), caption)
            pages.append(content_layout)
        design_lay, data_lay, behavior_lay = pages
        lay = data_lay

        def head(text):
            label = QLabel(text)
            label.setStyleSheet(_SECTION_STYLE)
            lay.addWidget(label)

        form_style = _FIELD_STYLE
        self.item_fields: dict = {}

        def row(section_form, key, label_text, widget):
            widget.setStyleSheet(form_style)
            self.item_fields[key] = widget
            name = QLabel(label_text)
            name.setBuddy(widget)
            widget.setAccessibleName(label_text)
            name.setStyleSheet(f"color: {WF['tx2']};"
                               "font-size: 9pt;")
            section_form.addRow(name, widget)

        # Data elements use the same Basics pane as shapes, with their
        # Azeo-specific configuration first. Hidden groups disappear
        # completely for ordinary drawing items; an empty property group
        # reads as a broken feature rather than an inapplicable one.
        self.data_head = QLabel("DATA")
        self.data_head.setStyleSheet(_SECTION_STYLE)
        lay.addWidget(self.data_head)

        self.datalink_holder = QWidget()
        datalink_form = QFormLayout(self.datalink_holder)
        _polish_form(datalink_form)
        dtype = AuthoringComboBox()
        from azeo_control_trainer.core.hmi.pvms.elements import DATALINK_TITLES
        for value, title_text in DATALINK_TITLES.items():
            dtype.addItem(title_text, value)
        dtype.currentIndexChanged.connect(
            lambda _i: self._apply_item_field("datalink_type"))
        row(datalink_form, "datalink_type", "Type", dtype)
        path_box = QWidget()
        path_lay = QHBoxLayout(path_box)
        path_lay.setContentsMargins(0, 0, 0, 0)
        path_lay.setSpacing(2)
        data_path = QLineEdit()
        data_path.setPlaceholderText("MODULE/BLOCK/PARAMETER")
        data_path.setStyleSheet(form_style)
        data_path.editingFinished.connect(
            lambda: self._apply_item_field("data_path"))
        browse = QPushButton("…")
        browse.setFixedWidth(26)
        browse.clicked.connect(self._browse_datalink)
        path_lay.addWidget(data_path, 1)
        path_lay.addWidget(browse)
        self.item_fields["data_path"] = data_path
        datalink_form.addRow(QLabel("Parameter"), path_box)
        decimals = QSpinBox()
        decimals.setRange(0, 8)
        decimals.valueChanged.connect(
            lambda _v: self._apply_item_field("decimals"))
        row(datalink_form, "decimals", "Decimals", decimals)
        units = QCheckBox("Show engineering units")
        units.toggled.connect(
            lambda _on: self._apply_item_field("units"))
        self.item_fields["units"] = units
        datalink_form.addRow("", units)
        self.data_validation = QLabel("—")
        self.data_validation.setWordWrap(True)
        datalink_form.addRow(QLabel("Status"), self.data_validation)
        lay.addWidget(self.datalink_holder)

        self.display_link_holder = QWidget()
        display_link_form = QFormLayout(self.display_link_holder)
        _polish_form(display_link_form)
        link_target = QLineEdit()
        link_target.setPlaceholderText("Display name")
        link_target.editingFinished.connect(
            lambda: self._apply_item_field("link_target"))
        row(display_link_form, "link_target", "Target", link_target)
        link_text = QLineEdit()
        link_text.editingFinished.connect(
            lambda: self._apply_item_field("link_text"))
        row(display_link_form, "link_text", "Label", link_text)
        lay.addWidget(self.display_link_holder)

        self.icon_holder = QWidget()
        icon_form = QFormLayout(self.icon_holder)
        _polish_form(icon_form)
        icon = AuthoringComboBox()
        from azeo_control_trainer.core.hmi.pvms.faceplate_icons import ICON_TITLES
        for value, title_text in ICON_TITLES.items():
            icon.addItem(title_text, value)
        icon.currentIndexChanged.connect(
            lambda _i: self._apply_item_field("icon"))
        row(icon_form, "icon", "Faceplate icon", icon)
        icon_note = QLabel(
            "Assign an Interaction action to make this a button. "
            "Without an action it remains a non-interactive status icon.")
        icon_note.setWordWrap(True)
        icon_form.addRow("", icon_note)
        lay.addWidget(self.icon_holder)

        # Parented and hidden, never laid out: a parentless holder is owned by
        # its Python wrapper, and its editors' signal lambdas then pin the whole
        # closed window through C++ (one inspector page leaked per session).
        self._legacy_user_entry_holder = QWidget(page)
        self._legacy_user_entry_holder.hide()
        user_form = QFormLayout(self._legacy_user_entry_holder)
        _polish_form(user_form)
        entry_type = AuthoringComboBox()
        from azeo_control_trainer.core.hmi.pvms.elements import USER_ENTRY_TITLES
        for value, title_text in USER_ENTRY_TITLES.items():
            entry_type.addItem(title_text, value)
        entry_type.currentIndexChanged.connect(
            lambda _i: self._apply_item_field("entry_type"))
        row(user_form, "entry_type", "Type", entry_type)
        entry_path = QLineEdit()
        entry_path.setPlaceholderText("MODULE/BLOCK/PARAMETER")
        entry_path.editingFinished.connect(
            lambda: self._apply_item_field("entry_path"))
        row(user_form, "entry_path", "Write destination", entry_path)
        entry_label = QLineEdit()
        entry_label.editingFinished.connect(
            lambda: self._apply_item_field("entry_label"))
        row(user_form, "entry_label", "Label", entry_label)
        entry_value = QLineEdit()
        entry_value.editingFinished.connect(
            lambda: self._apply_item_field("entry_value"))
        row(user_form, "entry_value", "Button value", entry_value)
        entry_options = QLineEdit()
        entry_options.setPlaceholderText("0=Off; 1=On")
        entry_options.editingFinished.connect(
            lambda: self._apply_item_field("entry_options"))
        row(user_form, "entry_options", "Options", entry_options)
        for key, title_text, default in (
                ("entry_lo", "Range minimum", 0.0),
                ("entry_hi", "Range maximum", 100.0)):
            field = QDoubleSpinBox()
            field.setRange(-1_000_000.0, 1_000_000.0)
            field.setDecimals(3)
            field.setValue(default)
            field.valueChanged.connect(
                lambda _v, k=key: self._apply_item_field(k))
            row(user_form, key, title_text, field)
        entry_disabled = QLineEdit()
        entry_disabled.setPlaceholderText("Empty = enabled")
        entry_disabled.editingFinished.connect(
            lambda: self._apply_item_field("entry_disabled"))
        row(user_form, "entry_disabled", "Disabled reason",
            entry_disabled)
        self.entry_validation = QLabel("—")
        self.entry_validation.setWordWrap(True)
        user_form.addRow(QLabel("Status"), self.entry_validation)
        self.user_entry_holder = QWidget()
        user_entry_lay = QVBoxLayout(self.user_entry_holder)
        user_entry_lay.setContentsMargins(0, 0, 0, 0)
        self.entry_summary = QLabel("No entry configuration")
        self.entry_summary.setWordWrap(True)
        user_entry_lay.addWidget(self.entry_summary)
        configure_entry = QPushButton("Configure User Entry…")
        configure_entry.clicked.connect(self._configure_user_entry)
        user_entry_lay.addWidget(configure_entry)
        user_entry_lay.addWidget(self.entry_validation)
        lay.addWidget(self.user_entry_holder)

        self.compound_holder = QWidget()
        compound_lay = QVBoxLayout(self.compound_holder)
        compound_lay.setContentsMargins(0, 0, 0, 0)
        self.compound_caption = QLabel(
            "Configure live series, rows, axes, tabs, or equipment ports.")
        self.compound_caption.setWordWrap(True)
        compound_lay.addWidget(self.compound_caption)
        self.compound_json = QPlainTextEdit()
        self.compound_json.hide()
        self.compound_json.setMaximumHeight(150)
        self.compound_json.setStyleSheet(form_style)
        compound_lay.addWidget(self.compound_json)
        self.apply_compound = QPushButton("Configure Data Element…")
        self.apply_compound.clicked.connect(self._configure_data_element)
        compound_lay.addWidget(self.apply_compound)
        self.compound_validation = QLabel("—")
        self.compound_validation.setWordWrap(True)
        compound_lay.addWidget(self.compound_validation)
        lay.addWidget(self.compound_holder)
        self.data_head.setVisible(False)
        self.datalink_holder.setVisible(False)
        self.display_link_holder.setVisible(False)
        self.icon_holder.setVisible(False)
        self.user_entry_holder.setVisible(False)
        self.compound_holder.setVisible(False)

        lay = behavior_lay
        head("Actions")
        self.actions_json = QPlainTextEdit()
        self.actions_json.hide()
        self.actions_json.setMaximumHeight(100)
        self.actions_json.setPlaceholderText(
            '[{"event":"click","kind":"open_display","target":"Overview"}]')
        self.actions_json.setStyleSheet(form_style)
        lay.addWidget(self.actions_json)
        apply_actions = QPushButton("Configure Actions…")
        apply_actions.clicked.connect(self._configure_actions)
        lay.addWidget(apply_actions)
        edit_script = QPushButton("Edit TypeScript…")
        edit_script.setToolTip(
            "Open Script Assistant for this element's click event")
        edit_script.clicked.connect(self._edit_item_script)
        lay.addWidget(edit_script)
        self.actions_validation = QLabel("No actions")
        lay.addWidget(self.actions_validation)

        # The group names and their order are Graphics Designer's own
        # (Web Browser / Display / Button property topics all share
        # them): Information, the element's own group, Fill, Line,
        # Geometry, Visibility. Matching the vocabulary is most of what
        # makes this trainer transfer to a real Graphics Designer — an
        # engineer looks for "Line Thickness" under Line, not for
        # "Line width" under Appearance.
        lay = design_lay
        head("Position and size")
        geometry = QFormLayout()
        _polish_form(geometry)
        from PySide6.QtWidgets import QGridLayout
        positions = QWidget()
        position_grid = QGridLayout(positions)
        position_grid.setContentsMargins(0, 0, 0, 0)
        position_grid.setSpacing(6)
        position_grid.setColumnStretch(1, 1)
        position_grid.setColumnStretch(3, 1)
        lay.addWidget(positions)
        for key, label in (("x", "X position"),
                           ("y", "Y position"),
                           ("w", "Width"), ("h", "Height"),
                           ("rot", "Rotation")):
            field = QLineEdit()
            field.editingFinished.connect(
                lambda k=key: self._apply_item_field(k))
            if key == "rot":
                row(geometry, key, label, field)
            else:
                self.item_fields[key] = field
                field.setStyleSheet(form_style)
                field.setAccessibleName(label)
                row_index = 0 if key in ("x", "y") else 1
                column = 0 if key in ("x", "w") else 2
                name = QLabel({"x": "X", "y": "Y", "w": "W", "h": "H"}[key])
                name.setToolTip(label)
                name.setBuddy(field)
                position_grid.addWidget(name, row_index, column)
                position_grid.addWidget(field, row_index, column + 1)
        lay.addLayout(geometry)

        # Shape geometry — live parameters for the adjustable
        # primitives (sides, star inset, taper, slant, notch).
        self.geometry_head = QLabel("SHAPE GEOMETRY")
        self.geometry_head.setStyleSheet(_SECTION_STYLE)
        lay.addWidget(self.geometry_head)
        self.geometry_holder = QWidget()
        geometry_form = QFormLayout(self.geometry_holder)
        _polish_form(geometry_form)
        sides = QSpinBox()
        sides.setRange(3, 64)
        sides.valueChanged.connect(
            lambda _v: self._apply_item_field("sides"))
        self.item_fields["sides"] = sides
        sides.setStyleSheet(form_style)
        self.sides_label = QLabel("Sides")
        self.sides_label.setStyleSheet(f"color: {WF['tx2']};"
                                       "font-size: 9pt;")
        geometry_form.addRow(self.sides_label, sides)
        adjust = QSpinBox()
        adjust.setRange(0, 100)
        adjust.valueChanged.connect(
            lambda _v: self._apply_item_field("adjust"))
        self.item_fields["adjust"] = adjust
        adjust.setStyleSheet(form_style)
        self.adjust_label = QLabel("Adjustment")
        self.adjust_label.setStyleSheet(f"color: {WF['tx2']};"
                                        "font-size: 9pt;")
        geometry_form.addRow(self.adjust_label, adjust)
        radius = QSpinBox()
        radius.setRange(0, 200)
        radius.valueChanged.connect(
            lambda _v: self._apply_item_field("radius"))
        self.item_fields["radius"] = radius
        radius.setStyleSheet(form_style)
        self.radius_label = QLabel("Corner radius")
        self.radius_label.setStyleSheet(f"color: {WF['tx2']};"
                                        "font-size: 9pt;")
        geometry_form.addRow(self.radius_label, radius)
        for key, label_text, low, high, default in (
                ("start", "Start Angle", -360, 360, 0),
                ("span", "Sweep Angle", -360, 360, 180)):
            field = QSpinBox()
            field.setRange(low, high)
            field.setValue(default)
            field.valueChanged.connect(
                lambda _v, k=key: self._apply_item_field(k))
            field.setStyleSheet(form_style)
            label = QLabel(label_text)
            label.setStyleSheet(f"color: {WF['tx2']}; font-size: 9pt;")
            self.item_fields[key] = field
            setattr(self, f"{key}_label", label)
            geometry_form.addRow(label, field)
        lay.addWidget(self.geometry_holder)

        # Equipment artwork — which drawing this symbol paints. Swapping
        # it here keeps the item's identity, ports, group and attached
        # pipes, so a connector that landed on the vessel stays landed.
        # Deleting and re-placing to change artwork loses all four.
        self.artwork_head = QLabel("EQUIPMENT ARTWORK")
        self.artwork_head.setStyleSheet(_SECTION_STYLE)
        lay.addWidget(self.artwork_head)
        self.artwork_holder = QWidget()
        artwork_form = QFormLayout(self.artwork_holder)
        _polish_form(artwork_form)
        artwork = AuthoringComboBox()
        artwork.setStyleSheet(form_style)
        artwork.setAccessibleName("Equipment artwork")
        artwork.setToolTip(
            "The drawing this equipment paints, from the project's symbol "
            "library. Imported SVGs appear beside the built-in artwork.")
        artwork.currentIndexChanged.connect(
            lambda _i: self._apply_item_field("symbol"))
        self.item_fields["symbol"] = artwork
        artwork_form.addRow(QLabel("Artwork"), artwork)
        lay.addWidget(self.artwork_holder)

        lay = design_lay
        head("Fill")
        fill_group = QFormLayout()
        _polish_form(fill_group)
        fill_field = _ColorField()
        fill_field.setPlaceholderText("Default")
        fill_field.editingFinished.connect(
            lambda: self._apply_item_field("fill"))
        row(fill_group, "fill", "Fill Color", fill_field)
        def theme_role(form, key, title, choices):
            combo = AuthoringComboBox()
            combo.addItem("Default / custom color", "")
            for role in choices:
                combo.addItem(role.replace("_", " ").title(), role)
            combo.setToolTip("Follows the display theme. Choosing a role clears the custom color.")
            combo.currentIndexChanged.connect(lambda _index, k=key: self._apply_item_field(k))
            row(form, key, title, combo)

        theme_role(fill_group, "fill_role", "Fill Theme Role", (
            "SURFACE_BG", "SURFACE_PANEL", "SURFACE_PANEL_ALT", "SURFACE_SUNK",
            "SURFACE_FIELD", "EQUIPMENT_FILL", "LIQUID"))
        fill_pct = QSpinBox()
        fill_pct.setRange(0, 100)
        fill_pct.setValue(100)
        fill_pct.valueChanged.connect(
            lambda _v: self._apply_item_field("fill_pct"))
        row(fill_group, "fill_pct", "Fill Percent", fill_pct)
        fill_direction = AuthoringComboBox()
        fill_direction.addItems(["bottom", "top", "left", "right"])
        fill_direction.currentTextChanged.connect(
            lambda _text: self._apply_item_field("fill_direction"))
        row(fill_group, "fill_direction", "Fill Direction", fill_direction)
        lay.addLayout(fill_group)

        head("Stroke")
        appearance = QFormLayout()
        self._stroke_form = appearance
        _polish_form(appearance)
        line_field = _ColorField()
        line_field.setPlaceholderText("Default")
        line_field.editingFinished.connect(
            lambda: self._apply_item_field("line"))
        row(appearance, "line", "Line Color", line_field)
        theme_role(appearance, "line_role", "Line Theme Role", (
            "EQUIPMENT", "LINE", "LINE_SOFT", "TEXT", "HEADING"))
        width = QSpinBox()
        width.setRange(1, 8)
        width.valueChanged.connect(
            lambda _v: self._apply_item_field("width"))
        row(appearance, "width", "Line Thickness", width)
        style = AuthoringComboBox()
        from azeo_control_trainer.core.hmi.pvms.strokes import ARROW_HEADS, ARROW_TITLES, DASH_PATTERNS
        style.addItems(list(DASH_PATTERNS))
        style.currentTextChanged.connect(
            lambda _t: self._apply_item_field("style"))
        row(appearance, "style", "Line Style", style)
        cap = AuthoringComboBox()
        cap.addItems(["round", "butt", "square"])
        cap.currentTextChanged.connect(
            lambda _t: self._apply_item_field("cap"))
        row(appearance, "cap", "Line Cap", cap)
        crossover = AuthoringComboBox()
        crossover.addItem("None (continuous)", "")
        crossover.addItem("Break at crossings", "gap")
        crossover.addItem("Jump over crossings", "jump")
        crossover.currentTextChanged.connect(
            lambda _t: self._apply_item_field("crossover"))
        row(appearance, "crossover", "Crossover Effect", crossover)
        self.crossover_status = QLabel()
        self.crossover_status.setWordWrap(True)
        self.crossover_status.setStyleSheet(
            f"color: {WF['tx3']}; font-size: 9pt;")
        appearance.addRow("", self.crossover_status)
        # Independent start and end heads, from the fourteen — the
        # builder's set, because a P&ID's flow direction and its
        # instrument links are different marks.
        for key, label_text in (("arrow_start", "Start head"),
                                ("arrow_end", "End head")):
            head_combo = AuthoringComboBox()
            for name in ARROW_HEADS:
                head_combo.addItem(ARROW_TITLES.get(name, name), name)
            head_combo.currentIndexChanged.connect(
                lambda _i, k=key: self._apply_item_field(k))
            row(appearance, key, label_text, head_combo)
        arrow_size = AuthoringComboBox()
        arrow_size.addItems(["small", "medium", "large"])
        arrow_size.currentTextChanged.connect(
            lambda _t: self._apply_item_field("arrow_size"))
        row(appearance, "arrow_size", "Head Size", arrow_size)
        lay.addLayout(appearance)

        head("Information")
        information = QFormLayout()
        _polish_form(information)
        for key, label in (("title", "Title"),
                           ("description", "Description")):
            field = QLineEdit()
            field.editingFinished.connect(
                lambda k=key: self._apply_item_field(k))
            row(information, key, label, field)
        lay.addLayout(information)

        # Azeo gives Text its own tab. Keep it in this scroll pane, but
        # preserve the same property vocabulary and hide the whole section
        # for non-text elements.
        self.text_head = QLabel("TEXT")
        self.text_head.setStyleSheet(_SECTION_STYLE)
        lay.addWidget(self.text_head)
        self.text_holder = QWidget()
        text_form = QFormLayout(self.text_holder)
        _polish_form(text_form)
        text_value = QLineEdit()
        text_value.editingFinished.connect(
            lambda: self._apply_item_field("text"))
        row(text_form, "text", "Text", text_value)
        text_colour = QLineEdit()
        text_colour.setPlaceholderText("#RRGGBB / empty = theme")
        text_colour.editingFinished.connect(
            lambda: self._apply_item_field("text_color"))
        row(text_form, "text_color", "Text Color", text_colour)
        theme_role(text_form, "text_role", "Text Theme Role", (
            "TEXT", "TEXT_DIM", "TEXT_FAINT", "HEADING", "ACTION",
            "ALARM_P1_TEXT", "ALARM_P2_TEXT", "ALARM_P3_TEXT"))
        family = QLineEdit()
        family.setPlaceholderText("Segoe UI")
        family.editingFinished.connect(
            lambda: self._apply_item_field("font_family"))
        row(text_form, "font_family", "Font Family", family)
        size = QDoubleSpinBox()
        size.setRange(1.0, 200.0)
        size.setDecimals(1)
        size.valueChanged.connect(
            lambda _v: self._apply_item_field("font_size"))
        row(text_form, "font_size", "Font Size", size)
        for key, title_text in (("font_bold", "Bold"),
                                ("font_italic", "Italic"),
                                ("font_underline", "Underline"),
                                ("text_wrap", "Wrap")):
            field = QCheckBox(title_text)
            field.toggled.connect(
                lambda _on, k=key: self._apply_item_field(k))
            self.item_fields[key] = field
            text_form.addRow("", field)
        for key, title_text, choices in (
                ("text_halign", "Horizontal Alignment",
                 ("left", "center", "right")),
                ("text_valign", "Vertical Alignment",
                 ("top", "middle", "bottom"))):
            field = AuthoringComboBox()
            field.addItems(choices)
            field.currentTextChanged.connect(
                lambda _text, k=key: self._apply_item_field(k))
            row(text_form, key, title_text, field)
        lay.addWidget(self.text_holder)

        head("Visibility")
        state = QFormLayout()
        _polish_form(state)
        from PySide6.QtWidgets import QCheckBox as _QCheck
        locked = _QCheck("Locked")
        locked.toggled.connect(
            lambda _on: self._apply_item_field("locked"))
        self.item_fields["locked"] = locked
        state.addRow("", locked)
        # Visibility is a property (the paper's own trio: name,
        # geometry, visibility). Hidden ghosts in EDIT, vanishes
        # everywhere else.
        visible = _QCheck("Visible")
        visible.setChecked(True)
        visible.toggled.connect(
            lambda _on: self._apply_item_field("visible"))
        self.item_fields["visible"] = visible
        state.addRow("", visible)
        obstacle = _QCheck("Block automatic pipe routes")
        obstacle.setToolTip("Clear for background panels that pipes should cross.")
        obstacle.setChecked(True)
        obstacle.toggled.connect(lambda _on: self._apply_item_field("routing_obstacle"))
        self.item_fields["routing_obstacle"] = obstacle
        state.addRow("", obstacle)
        opacity = QSpinBox()
        opacity.setRange(0, 100)
        opacity.setSuffix(" %")
        opacity.setValue(100)
        opacity.valueChanged.connect(
            lambda _value: self._apply_item_field("opacity"))
        row(state, "opacity", "Opacity", opacity)
        aspect = _QCheck("Lock aspect ratio")
        aspect.toggled.connect(
            lambda _on: self._apply_item_field("lock_aspect"))
        self.item_fields["lock_aspect"] = aspect
        state.addRow("", aspect)
        self.item_group = QLabel("\u2014")
        self.item_group.setStyleSheet(f"color: {WF['tx3']};"
                                      "font-size: 9pt;")
        state.addRow(QLabel("Group"), self.item_group)
        lay.addLayout(state)

        lay = behavior_lay
        head("Animation")
        anim = QFormLayout()
        _polish_form(anim)

        def anim_row(label_text, widget):
            widget.setStyleSheet(form_style)
            name = QLabel(label_text)
            name.setStyleSheet(f"color: {WF['tx2']};"
                               "font-size: 9pt;")
            anim.addRow(name, widget)
            return name

        self.anim_prop = AuthoringComboBox()
        self.anim_prop.addItems([
            "fill", "line", "fill_pct", "rot", "visible", "text",
            "width", "radius", "start", "span", "style", "opacity",
            "text_color", "font_family", "font_size", "font_bold",
            "font_italic", "font_underline", "icon", "tooltip", "enabled",
        ])
        self.anim_prop.currentTextChanged.connect(
            lambda _t: self._load_anim_row())
        anim_row("Property", self.anim_prop)
        self.anim_kind = AuthoringComboBox()
        self.anim_kind.addItem("Static Value", "static")
        self.anim_kind.addItem("Animation", "animation")
        self.anim_kind.addItem("Blink Animation", "blink")
        self.anim_kind.addItem("Expression", "expression")
        self.anim_kind.addItem("Standard", "standard")
        self.anim_kind.addItem("Variable", "variable")
        self.anim_kind.addItem("PVM Config Property", "pvm")
        self.anim_kind.currentIndexChanged.connect(
            lambda _i: self._animation_kind_changed())
        anim_row("Source", self.anim_kind)
        from PySide6.QtWidgets import QLineEdit as _QLine
        self.anim_path = _QLine()
        self.anim_path.setPlaceholderText("Parameter, expression, or reference")
        self.anim_path.editingFinished.connect(self._apply_anim)
        self.anim_browse = QPushButton("Bind…")
        self.anim_browse.setToolTip(
            "Browse tags and typed PVM properties, or build an expression")
        self.anim_browse.clicked.connect(self._open_binding_editor)
        reference_holder = QWidget()
        reference_layout = QHBoxLayout(reference_holder)
        reference_layout.setContentsMargins(0, 0, 0, 0)
        reference_layout.setSpacing(3)
        reference_layout.addWidget(self.anim_path, 1)
        reference_layout.addWidget(self.anim_browse)
        self.anim_reference_label = QLabel("Reference")
        self.anim_reference_label.setStyleSheet(
            f"color: {WF['tx2']}; font-size: 9pt;")
        anim.addRow(self.anim_reference_label, reference_holder)
        self.anim_fn = AuthoringComboBox()
        self.anim_fn.currentTextChanged.connect(
            lambda _t: self._apply_anim())
        self.anim_fn_label = anim_row("Function", self.anim_fn)
        self.anim_default = _QLine()
        self.anim_default.setPlaceholderText("Optional fallback")
        self.anim_default.editingFinished.connect(self._apply_anim)
        self.anim_default_label = anim_row("Default", self.anim_default)
        self.anim_on = _QLine()
        self.anim_on.setPlaceholderText("Blink on value")
        self.anim_on.editingFinished.connect(self._apply_anim)
        self.anim_on_label = anim_row("On", self.anim_on)
        self.anim_off = _QLine()
        self.anim_off.setPlaceholderText("Blink off value")
        self.anim_off.editingFinished.connect(self._apply_anim)
        self.anim_off_label = anim_row("Off", self.anim_off)
        hint = QLabel("The source is the property's diamond menu: one model "
                      "for animation, blink, expression, Standard, Variable "
                      "and PVM configuration references.")
        hint.setStyleSheet(f"color: {WF['tx3']}; font-size: 9pt;")
        hint.setWordWrap(True)
        anim.addRow("", hint)
        lay.addLayout(anim)
        for layout in pages:
            layout.addStretch(1)
        return page

    def _anim_widgets(self):
        return (self.anim_prop, self.anim_kind, self.anim_path, self.anim_fn,
                self.anim_default, self.anim_on, self.anim_off,
                self.anim_browse)

    @staticmethod
    def _binding_value_type(prop: str):
        """The inspector's paint properties mapped to the shared type model."""
        from .binding_editor import ValueType
        if prop in {"fill", "line", "text_color"}:
            return ValueType.COLOR
        if prop in {"fill_pct", "rot", "width", "radius", "start",
                    "span", "opacity", "font_size"}:
            return ValueType.NUMBER
        if prop in {"visible", "font_bold", "font_italic",
                    "font_underline", "enabled"}:
            return ValueType.BOOLEAN
        return ValueType.STRING

    def _binding_catalog(self):
        """Sources valid in this document, shared by every property row."""
        from .binding_editor import BindingCatalog
        catalog = BindingCatalog()
        try:
            catalog.add_graphs(self.studio.graphs_provider())
        except (AttributeError, TypeError):
            pass
        class_name = self.studio.edited_user_class_name()
        if class_name:
            config = self.studio._config_for_name(class_name)
            if config is not None:
                catalog.add_configuration(config)
        return catalog

    def _initial_binding_result(self, spec: dict | None):
        """Adapt the persisted property grammar to the unified editor."""
        if not spec:
            return None
        from .binding_editor import (
            BindingKind, BindingTarget, ExpressionReference,
            make_binding_result,
        )
        prop = self.anim_prop.currentText()
        target = BindingTarget(
            prop, self._binding_value_type(prop),
            str(item_document_data(self._current_static).get("name")
                or item_document_data(self._current_static).get("id")
                or "Selection"))
        kind = spec.get("kind")
        if kind == "animation":
            return make_binding_result(
                target, BindingKind.INDIRECT_TAG
                if spec.get("indirect") else BindingKind.DIRECT_TAG,
                source=str(spec.get("path", "")), catalog=self._binding_catalog())
        if kind == "pvm":
            return make_binding_result(
                target, BindingKind.CLASS_PROPERTY,
                source=str(spec.get("ref", "")), catalog=self._binding_catalog())
        if kind == "expression" and isinstance(spec.get("refs"), dict):
            references = tuple(ExpressionReference(str(name), str(path))
                               for name, path in spec["refs"].items())
            return make_binding_result(
                target, BindingKind.EXPRESSION,
                expression=str(spec.get("authored_expr")
                               or spec.get("expr", "")),
                references=references, catalog=self._binding_catalog())
        return None

    def _apply_binding_result(self, result) -> bool:
        """Apply one typed result as exactly one undoable authoring act."""
        item = getattr(self, "_current_static", None)
        if item is None or result is None or not result.valid:
            return False
        from .binding_editor import BindingKind
        if result.kind is BindingKind.COMPONENT_PROPERTY:
            return False
        if result.kind in (BindingKind.CLASS_PROPERTY,
                           BindingKind.INDIRECT_TAG) \
                and not self.studio.edited_user_class_name():
            return False
        from azeo_control_trainer.core.hmi.pvms.properties import descriptor_for, set_descriptor
        prop = self.anim_prop.currentText()
        previous = descriptor_for(item.data, prop) or {}
        descriptor = result.descriptor()
        if result.kind is BindingKind.EXPRESSION:
            descriptor["authored_expr"] = result.expression
        for key in ("default", "fn"):
            if key in previous and key not in descriptor:
                descriptor[key] = previous[key]
        if previous == descriptor:
            return False
        self.studio.checkpoint()
        if prop not in (item.data.get("props") or {}) \
                and prop in item.data and not item.data.get(
                    prop + "_animated"):
            item.data.setdefault(prop + "_static", item.data[prop])
        set_descriptor(item.data, prop, descriptor)
        legacy = item.data.get("anim")
        if isinstance(legacy, dict):
            legacy.pop(prop, None)
            if not legacy:
                item.data.pop("anim", None)
        item.update()
        self.studio.mark_unsaved()
        self._load_anim_row()
        return True

    def _open_binding_editor(self) -> None:
        """Open the one typed source browser used by drawing properties."""
        item = getattr(self, "_current_static", None)
        if item is None:
            return
        from azeo_control_trainer.core.presentation.headless import is_headless
        if is_headless():
            return
        from azeo_control_trainer.core.hmi.pvms.properties import descriptor_for
        from .binding_editor import (
            BindingTarget, UnifiedBindingEditor,
        )
        prop = self.anim_prop.currentText()
        target = BindingTarget(
            prop, self._binding_value_type(prop),
            str(item.data.get("name") or item.data.get("id") or "Selection"))
        catalog = self._binding_catalog()
        initial = self._initial_binding_result(descriptor_for(item.data, prop))
        dialog = UnifiedBindingEditor(target, catalog, initial, parent=self)
        self._binding_dialog = dialog
        if dialog.exec() == dialog.DialogCode.Accepted:
            self._apply_binding_result(dialog.binding_result())

    def _animation_kind_changed(self) -> None:
        if getattr(self, "_loading_item", False):
            return
        self._sync_animation_fields()
        self._apply_anim()

    def _sync_animation_fields(self) -> None:
        kind = self.anim_kind.currentData() or "static"
        labels = {"animation": "Parameter", "blink": "Condition",
                  "expression": "Expression", "standard": "Standard",
                  "variable": "Variable", "pvm": "PVM Property",
                  "static": "Reference"}
        self.anim_reference_label.setText(labels.get(kind, "Reference"))
        self.anim_path.setEnabled(kind != "static")
        self.anim_fn.setVisible(kind == "animation")
        self.anim_fn_label.setVisible(kind == "animation")
        self.anim_default.setVisible(kind not in ("static",))
        self.anim_default_label.setVisible(kind not in ("static",))
        blink = kind == "blink"
        self.anim_on.setVisible(blink)
        self.anim_off.setVisible(blink)
        self.anim_on_label.setVisible(blink)
        self.anim_off_label.setVisible(blink)

    def _load_anim_row(self) -> None:
        """Reflect the selected property's animation spec \u2014 never
        writes; `_loading_item` guards the round trip."""
        item = getattr(self, "_current_static", None)
        if item is None:
            return
        was_loading = getattr(self, "_loading_item", False)
        self._loading_item = True
        try:
            from azeo_control_trainer.core.hmi.pvms.properties import descriptor_for
            spec = descriptor_for(item.data,
                                  self.anim_prop.currentText()) or {}
            kind = spec.get("kind", "static") if spec else "static"
            index = self.anim_kind.findData(kind)
            self.anim_kind.setCurrentIndex(max(index, 0))
            reference = {"animation": spec.get("path", ""),
                         "blink": spec.get("condition", ""),
                         "expression": spec.get("expr", ""),
                         }.get(kind, spec.get("ref", ""))
            self.anim_path.setText(str(reference or ""))
            fn = spec.get("fn", "")
            index = self.anim_fn.findText(fn)
            self.anim_fn.setCurrentIndex(max(index, 0))
            self.anim_default.setText(
                "" if spec.get("default") is None else str(spec["default"]))
            self.anim_on.setText(
                "" if spec.get("on") is None else str(spec["on"]))
            self.anim_off.setText(
                "" if spec.get("off") is None else str(spec["off"]))
            self._sync_animation_fields()
        finally:
            self._loading_item = was_loading

    def _apply_anim(self) -> None:
        """One property follows one parameter through one conversion
        function. An empty parameter removes the animation AND its
        live override \u2014 a cleared animation must not freeze the last
        driven colour onto the shape."""
        item = getattr(self, "_current_static", None)
        if item is None or getattr(self, "_loading_item", False):
            return
        studio = getattr(item.scene(), "studio", None) \
            if item.scene() else None
        if studio is None:
            return
        from azeo_control_trainer.core.hmi.pvms.properties import (
            ANIMATION, BLINK, EXPRESSION, PVM_PROPERTY, STANDARD, STATIC,
            VARIABLE,
        )
        prop = self.anim_prop.currentText()
        kind = self.anim_kind.currentData() or STATIC
        path = self.anim_path.text().strip()
        fn = self.anim_fn.currentText().strip()
        props = dict(item.data.get("props") or {})
        legacy = dict(item.data.get("anim") or {})
        previous = props.get(prop) or legacy.get(prop)
        if kind == STATIC or not path:
            if previous is None:
                return
            studio.checkpoint()
            props.pop(prop, None)
            legacy.pop(prop, None)
            if item.data.pop(prop + "_animated", None) is not None:
                base = item.data.pop(prop + "_static", None)
                if base is None:
                    item.data.pop(prop, None)
                else:
                    item.data[prop] = base
        else:
            spec = {"kind": kind}
            if kind == ANIMATION:
                spec["path"] = path
            elif kind == BLINK:
                spec["condition"] = path
                if self.anim_on.text().strip():
                    spec["on"] = self._entry_scalar(
                        self.anim_on.text().strip())
                if self.anim_off.text().strip():
                    spec["off"] = self._entry_scalar(
                        self.anim_off.text().strip())
            elif kind == EXPRESSION:
                spec["expr"] = path
            elif kind in (STANDARD, VARIABLE, PVM_PROPERTY):
                spec["ref"] = path
            if kind == ANIMATION and fn:
                spec["fn"] = fn
            if self.anim_default.text().strip():
                spec["default"] = self._entry_scalar(
                    self.anim_default.text().strip())
            if props.get(prop) == spec and prop not in legacy:
                return
            studio.checkpoint()
            if prop not in props and prop in item.data \
                    and not item.data.get(prop + "_animated"):
                item.data[prop + "_static"] = item.data[prop]
            props[prop] = spec
            legacy.pop(prop, None)
        if props:
            item.data["props"] = props
        else:
            item.data.pop("props", None)
        if legacy:
            item.data["anim"] = legacy
        else:
            item.data.pop("anim", None)
        item.update()
        studio.mark_unsaved()

    def _browse_datalink(self) -> None:
        """Browse to an exact addressable parameter for this Data Link."""
        from ..param_browser import ParameterBrowserDialog

        current = self.item_fields["data_path"].text().strip()
        chosen = ParameterBrowserDialog.browse(
            self.studio.graphs_provider, current, parent=self,
            selection="parameter")
        if chosen is None:
            return
        path, _data_type = chosen
        self.item_fields["data_path"].setText(path)
        self._apply_item_field("data_path")

    def _commit_structured_item(self, candidate: dict) -> None:
        """Apply one validated editor result as one undoable operation."""
        item = getattr(self, "_current_static", None)
        studio = self.studio
        if item is None or studio is None or candidate == item.data:
            return
        studio.checkpoint()
        item.data.clear()
        item.data.update(candidate)
        studio.rebind_static(item)
        studio.mark_unsaved()
        item.update()
        self.show_item(item)

    def _configure_data_element(self) -> None:
        """Open the primary row-oriented compound-data workflow."""
        if getattr(self, "_loading_item", False):
            return
        item = getattr(self, "_current_static", None)
        if item is None:
            return
        from PySide6.QtWidgets import QDialog
        from .structured_editors import DataElementDialog

        dialog = DataElementDialog(item.data, self._binding_catalog(), self)
        if dialog.exec() != QDialog.Accepted:
            return
        candidate = dialog.result_data()
        if candidate is not None:
            self._commit_structured_item(candidate)

    def _configure_user_entry(self) -> None:
        """Configure a writable element without manual option-string syntax."""
        if getattr(self, "_loading_item", False):
            return
        item = getattr(self, "_current_static", None)
        if item is None or item.data.get("kind") != "user_entry":
            return
        from copy import deepcopy
        from PySide6.QtWidgets import QDialog
        from .structured_editors import UserEntryDialog

        dialog = UserEntryDialog(
            item.data.get("entry", {}), self._binding_catalog(), self)
        if dialog.exec() != QDialog.Accepted:
            return
        entry = dialog.result_entry()
        if entry is None:
            return
        candidate = deepcopy(item.data)
        candidate["entry"] = dialog.result_entry_data()
        self._commit_structured_item(candidate)

    def _configure_actions(self) -> None:
        """Configure interactions while excluding controls runtime cannot run."""
        if getattr(self, "_loading_item", False):
            return
        item = getattr(self, "_current_static", None)
        if item is None:
            return
        from copy import deepcopy
        from PySide6.QtWidgets import QDialog
        from .structured_editors import ActionListDialog

        dialog = ActionListDialog(
            list(item.data.get("actions", ())), self._binding_catalog(), self,
            operator_target=True)
        if dialog.exec() != QDialog.Accepted:
            return
        actions = dialog.result_actions()
        if actions is None:
            return
        candidate = deepcopy(item.data)
        if actions:
            candidate["actions"] = actions
        else:
            candidate.pop("actions", None)
        self._commit_structured_item(candidate)

    def _apply_compound_data(self) -> None:
        if getattr(self, "_loading_item", False):
            return
        item = getattr(self, "_current_static", None)
        if item is None:
            return
        import json
        from azeo_control_trainer.core.hmi.pvms.elements import validate_data_element

        try:
            data = json.loads(self.compound_json.toPlainText() or "{}")
            if not isinstance(data, dict):
                raise ValueError("configuration must be a JSON object")
        except (ValueError, TypeError) as error:
            self.compound_validation.setText(str(error))
            self.compound_validation.setStyleSheet(
                f"color: {WF['crit']}; font-size: 9pt;")
            return
        kind = item.data.get("kind")
        allowed = {
            "chart": ("pens", "lo", "hi", "window_seconds"),
            "alarm_list": ("priority_min", "path_prefix", "max_rows"),
            "multi_point": ("parameters",),
            "radar_plot": ("parameters",),
            "tab": ("tabs", "active_tab"),
            "date_time": ("timezone", "format", "culture"),
            "table": ("columns", "rows", "row_height", "header_height"),
            "symbol": ("ports",),
        }.get(kind, ())
        studio = self.studio
        if studio is not None:
            studio.checkpoint()
        for name in allowed:
            if name in data:
                item.data[name] = data[name]
        problem = validate_data_element(item.data)
        if studio is not None:
            studio.rebind_static(item)
            studio.mark_unsaved()
        self.compound_validation.setText(problem or "Configured")
        self.compound_validation.setStyleSheet(
            f"color: {WF['crit'] if problem else WF['ok']}; font-size: 9pt;")
        item.update()

    def _apply_actions(self) -> None:
        if getattr(self, "_loading_item", False):
            return
        item = getattr(self, "_current_static", None)
        if item is None:
            return
        import json
        from azeo_control_trainer.core.hmi.pvms.elements import ACTION_KINDS, MOUSE_EVENTS, Action

        try:
            rows = json.loads(self.actions_json.toPlainText() or "[]")
            if not isinstance(rows, list):
                raise ValueError("actions must be a JSON list")
            actions = [Action.from_dict(row) for row in rows
                       if isinstance(row, dict)]
            if len(actions) != len(rows):
                raise ValueError("each action must be an object")
            invalid = next((action for action in actions
                            if action.event not in MOUSE_EVENTS
                            or action.kind not in ACTION_KINDS), None)
            if invalid is not None:
                raise ValueError(
                    f"unsupported action {invalid.event}/{invalid.kind}")
        except (ValueError, TypeError) as error:
            self.actions_validation.setText(str(error))
            self.actions_validation.setStyleSheet(
                f"color: {WF['crit']}; font-size: 9pt;")
            return
        self.studio.checkpoint()
        if actions:
            item.data["actions"] = [action.to_dict() for action in actions]
        else:
            item.data.pop("actions", None)
        self.studio.rebind_static(item)
        self.studio.mark_unsaved()
        self.actions_validation.setText(
            f"{len(actions)} action(s)" if actions else "No actions")
        self.actions_validation.setStyleSheet(
            f"color: {WF['ok']}; font-size: 9pt;")

    def show_item(self, item) -> None:
        """Populate the drawing-item page from one selected item."""
        self._current_static = item
        self._stack.setCurrentIndex(2)
        data = item.data
        kind = data.get("kind", "item")
        title = kind.title()
        if data.get("symbol"):
            from azeo_control_trainer.core.hmi.pvms.symbols import CATALOG
            record = CATALOG.get(data["symbol"])
            title = record[1] if record else str(data["symbol"]).replace("_", " ").title()
        elif kind == "stream_connector":
            title = f"{str(data.get('stream_direction', 'process')).title()}" \
                " Stream  \u00b7  off-page connector"
        if data.get("user_pvm"):
            title += f"   [PVM {data['user_pvm']}]"
        self.item_title.setText(title)
        self._loading_item = True
        try:
            import json
            actions = list(data.get("actions", ()))
            self.actions_json.setPlainText(json.dumps(
                actions, indent=2, ensure_ascii=False))
            from azeo_control_trainer.core.hmi.pvms.elements import Action
            from .editor_models import action_issues
            action_problems = [
                issue for raw in actions
                for issue in action_issues(
                    Action.from_dict(raw), operator_target=True)
                if issue.severity == "error"
            ]
            self.actions_validation.setText(
                f"{len(actions)} action(s) · {len(action_problems)} issue(s)"
                if actions else "No actions")
            self.actions_validation.setStyleSheet(
                f"color: {WF['crit'] if action_problems else WF['ok']};"
                "font-size: 9pt;")
            is_datalink = kind == "datalink"
            is_display_link = kind == "display_link"
            is_user_entry = kind == "user_entry"
            is_icon = kind in ("icon_button", "special_symbol")
            from azeo_control_trainer.core.hmi.pvms.elements import DATA_ELEMENT_KINDS, validate_data_element
            # Equipment symbols expose named process ports as placement
            # configuration. Live vessel instrumentation is a registered PVM,
            # so Studio never grows a second drawing-specific implementation.
            is_compound = kind in DATA_ELEMENT_KINDS \
                or kind in ("symbol", "stream_connector")
            self.data_head.setVisible(
                is_datalink or is_display_link or is_user_entry
                or is_compound or is_icon)
            self.datalink_holder.setVisible(is_datalink)
            self.display_link_holder.setVisible(is_display_link)
            self.user_entry_holder.setVisible(is_user_entry)
            self.icon_holder.setVisible(is_icon)
            self.compound_holder.setVisible(is_compound)
            is_symbol = kind == "symbol"
            self.artwork_head.setVisible(is_symbol)
            self.artwork_holder.setVisible(is_symbol)
            if is_symbol:
                _fill_symbol_combo(self.item_fields["symbol"],
                                   str(data.get("symbol", "")))
            if is_compound:
                import json
                if kind in ("symbol", "stream_connector"):
                    self.compound_caption.setText(
                        "PROCESS CONNECTION PORTS\n"
                        "Named ports use normalized x/y positions and remain "
                        "attached when equipment is resized.")
                    self.apply_compound.setText(
                        "Configure Equipment Ports…")
                else:
                    self.compound_caption.setText(
                        "Structured series, rows, axes and tab configuration. "
                        "Advanced JSON remains available inside the editor.")
                    self.apply_compound.setText("Configure Data Element…")
                keys = {
                    "chart": ("pens", "lo", "hi", "window_seconds"),
                    "alarm_list": ("priority_min", "path_prefix", "max_rows"),
                    "multi_point": ("parameters",),
                    "radar_plot": ("parameters",),
                    "tab": ("tabs", "active_tab"),
                    "date_time": ("timezone", "format", "culture"),
                    "table": ("columns", "rows", "row_height",
                              "header_height"),
                    "symbol": ("ports",),
                    "stream_connector": ("ports", "stream_direction"),
                }.get(kind, ())
                payload = {key: data[key] for key in keys if key in data}
                self.compound_json.setPlainText(json.dumps(
                    payload, indent=2, ensure_ascii=False))
                problem = item.binding_error or validate_data_element(data)
                self.compound_validation.setText(problem or "Configured")
                self.compound_validation.setStyleSheet(
                    f"color: {WF['crit'] if problem else WF['ok']};"
                    "font-size: 9pt;")
            if is_datalink:
                combo = self.item_fields["datalink_type"]
                index = combo.findData(
                    data.get("datalink_type", "numeric"))
                combo.setCurrentIndex(max(index, 0))
                self.item_fields["data_path"].setText(
                    str(data.get("path", "") or ""))
                try:
                    decimals = int(data.get("decimals", 1) or 0)
                except (TypeError, ValueError):
                    decimals = 1
                self.item_fields["decimals"].setValue(
                    decimals)
                self.item_fields["units"].setChecked(
                    bool(data.get("units", False)))
                from azeo_control_trainer.core.hmi.binding.result import UNRESOLVED
                if item.binding_error:
                    status = item.binding_error
                    colour = WF["crit"]
                elif item.binding is None \
                        or item.binding.result is UNRESOLVED:
                    status = "Unresolved — shows @@@@@@@@ online"
                    colour = WF["crit"]
                elif item.binding.result.quality.name == "BAD":
                    status = "Bad status — shows ????????? online"
                    colour = WF["warn"]
                else:
                    status = "Live"
                    colour = WF["ok"]
                self.data_validation.setText(status)
                self.data_validation.setStyleSheet(
                    f"color: {colour}; font-size: 9pt;")
            if is_display_link:
                self.item_fields["link_target"].setText(
                    str(data.get("target", "") or ""))
                self.item_fields["link_text"].setText(
                    str(data.get("text", "") or ""))
            if is_icon:
                combo = self.item_fields["icon"]
                combo.setCurrentIndex(max(combo.findData(
                    data.get("icon", "module_detail")), 0))
            if is_user_entry:
                from azeo_control_trainer.core.hmi.pvms.elements import USER_ENTRY_TITLES, UserEntry
                entry = UserEntry.from_dict(data.get("entry", {}))
                destination = entry.path or "action-only"
                self.entry_summary.setText(
                    f"{USER_ENTRY_TITLES.get(entry.kind, entry.kind)} · "
                    f"{destination}")
                combo = self.item_fields["entry_type"]
                combo.setCurrentIndex(max(combo.findData(entry.kind), 0))
                self.item_fields["entry_path"].setText(entry.path)
                self.item_fields["entry_label"].setText(entry.label)
                self.item_fields["entry_value"].setText(
                    str(entry.value))
                self.item_fields["entry_options"].setText("; ".join(
                    f"{value}={caption}"
                    for value, caption in entry.options))
                self.item_fields["entry_lo"].setValue(entry.lo)
                self.item_fields["entry_hi"].setValue(entry.hi)
                self.item_fields["entry_disabled"].setText(
                    entry.disabled_reason)
                if item.binding_error:
                    status, colour = item.binding_error, WF["crit"]
                elif item.write_error:
                    status, colour = item.write_error, WF["warn"]
                elif item.write_allowed:
                    status, colour = "Writable online", WF["ok"]
                else:
                    status, colour = "Write unavailable", WF["crit"]
                self.entry_validation.setText(status)
                self.entry_validation.setStyleSheet(
                    f"color: {colour}; font-size: 9pt;")
            rect = item.rect()
            values = {"x": item.pos().x(), "y": item.pos().y(),
                      "w": rect.width(), "h": rect.height(),
                      "rot": data.get("rot", 0.0)}
            for key in ("x", "y", "w", "h", "rot"):
                self.item_fields[key].setText(
                    f"{float(values[key]):g}")
            for key in ("fill", "line"):
                self.item_fields[key].setText(
                    str(data.get(key) or ""))
            for key in ("fill_role", "line_role", "text_role"):
                combo = self.item_fields[key]
                value = data.get(key, "")
                if value and combo.findData(value) < 0:
                    # Older/custom definitions may use another valid role.
                    # Selecting the item must not mislabel it as a default.
                    combo.addItem(str(value).replace("_", " ").title(), value)
                combo.setCurrentIndex(max(0, combo.findData(value)))
            self.item_fields["width"].setValue(
                int(float(data.get("width", 2) or 2)))
            self.item_fields["style"].setCurrentText(
                data.get("style", "solid") or "solid")
            crossover = self.item_fields["crossover"]
            crossover.setCurrentIndex(max(
                crossover.findData(data.get("crossover") or ""), 0))
            self.item_fields["locked"].setChecked(
                bool(data.get("locked")))
            self.item_group.setText(data.get("group") or "\u2014")
            from azeo_control_trainer.core.hmi.pvms.rendering.crossing import CROSSOVER_KINDS, crossing_count
            crossover_capable = kind in CROSSOVER_KINDS
            self.item_fields["crossover"].setEnabled(crossover_capable)
            self.crossover_status.setVisible(crossover_capable)
            if crossover_capable:
                total = crossing_count(item)
                self.crossover_status.setText(
                    f"{total} crossing{'s' if total != 1 else ''} detected "
                    "live · this stroke controls Break/Jump")
            self.item_fields["fill_pct"].setValue(
                int(float(data.get("fill_pct", 100) or 100)))
            self.item_fields["fill_direction"].setCurrentText(
                data.get("fill_direction", "bottom") or "bottom")
            self.item_fields["fill_pct"].setEnabled(
                kind in ("rect", "square", "ellipse", "round_rect")
                or kind in SHAPE_KINDS)
            from azeo_control_trainer.core.hmi.pvms.shapes import (
                ADJUSTMENT_LIMITS, GEOMETRY_DEFAULTS, adjustment_amount,
                is_adjustable, vertex_maximum, vertex_total,
            )
            from azeo_control_trainer.core.hmi.pvms.strokes import resolve_arrows
            # Heads apply to every open stroke, not only pipes.
            open_stroke = kind in ("pipe", "line", "polyline",
                                   "freehand", "arc")
            start_head, end_head = resolve_arrows(data)
            for key, value in (("arrow_start", start_head),
                               ("arrow_end", end_head)):
                combo = self.item_fields[key]
                index = combo.findData(value)
                combo.setCurrentIndex(max(index, 0))
                combo.setEnabled(open_stroke)
            self.item_fields["arrow_size"].setCurrentText(
                data.get("arrow_size") or "medium")
            self.item_fields["arrow_size"].setEnabled(open_stroke)
            self.item_fields["cap"].setCurrentText(
                data.get("cap") or "round")
            self.item_fields["cap"].setEnabled(open_stroke)
            # Irrelevant stroke controls previously consumed half the panel
            # for equipment symbols. Keep the property vocabulary contextual.
            for key in ("cap", "arrow_start", "arrow_end", "arrow_size", "crossover"):
                field = self.item_fields[key]
                self._stroke_form.setRowVisible(
                    field, crossover_capable if key == "crossover" else open_stroke)
            # Shape geometry: only the kinds that have parameters
            # show the section at all — an empty form under a heading
            # reads as broken.
            adjustable = is_adjustable(kind)
            rounded = kind == "round_rect"
            angle_shape = kind in ("arc", "chord", "pie")
            self.geometry_head.setVisible(
                adjustable or rounded or angle_shape)
            self.geometry_holder.setVisible(
                adjustable or rounded or angle_shape)
            vertexed = kind in ("polygon", "star")
            for widget in (self.sides_label,
                           self.item_fields["sides"]):
                widget.setVisible(vertexed)
            for widget in (self.adjust_label,
                           self.item_fields["adjust"]):
                widget.setVisible(adjustable and kind != "polygon")
            for widget in (self.radius_label,
                           self.item_fields["radius"]):
                widget.setVisible(rounded)
            for key in ("start", "span"):
                getattr(self, f"{key}_label").setVisible(angle_shape)
                self.item_fields[key].setVisible(angle_shape)
            if vertexed:
                self.item_fields["sides"].setRange(
                    4 if kind == "star" else 3, vertex_maximum(kind))
                self.item_fields["sides"].setValue(
                    vertex_total(data, kind))
                self.sides_label.setText(
                    "Points" if kind == "star" else "Sides")
            if adjustable and kind != "polygon":
                low, high = ADJUSTMENT_LIMITS.get(kind, (0, 100))
                self.item_fields["adjust"].setRange(int(low),
                                                    int(high))
                self.item_fields["adjust"].setValue(
                    int(adjustment_amount(data, kind)))
                self.adjust_label.setText(
                    {"star": "Inner radius",
                     "trapezoid": "Top inset",
                     "parallelogram": "Slant",
                     "chevron": "Notch depth"}.get(kind,
                                                   "Adjustment"))
            if rounded:
                self.item_fields["radius"].setValue(
                    int(float(data.get("radius", 12) or 12)))
            if angle_shape:
                self.item_fields["start"].setValue(
                    int(float(data.get("start", 0) or 0)))
                self.item_fields["span"].setValue(
                    int(float(data.get("span", 180) or 180)))
            _ = GEOMETRY_DEFAULTS
            # A point-kind resizes by its points, so the numeric
            # width/height fields would fight the geometry.
            for key in ("w", "h"):
                self.item_fields[key].setEnabled(
                    kind not in ("pipe", "polyline", "freehand"))
            self.item_fields["visible"].setChecked(
                bool(data.get("visible", True)))
            self.item_fields["routing_obstacle"].setChecked(bool(data.get("routing_obstacle", True)))
            self.item_fields["routing_obstacle"].setEnabled(kind != "pipe")
            self.item_fields["opacity"].setValue(
                int(float(data.get("opacity", 100) or 0)))
            self.item_fields["lock_aspect"].setChecked(
                getattr(item, "aspect_locked", lambda: False)())
            # Equipment owns optional authored text as well. Keeping that
            # label on the equipment object means move/copy/group operations
            # cannot leave a separate tag behind on the canvas.
            is_text = kind in ("text", "stream_connector", "symbol")
            self.text_head.setVisible(is_text)
            self.text_holder.setVisible(is_text)
            if is_text:
                for key, fallback in (("text", ""),
                                      ("text_color", ""),
                                      ("font_family", "Segoe UI")):
                    self.item_fields[key].setText(
                        str(data.get(key, fallback) or fallback))
                self.item_fields["font_size"].setValue(
                    float(data.get("font_size", 9.0) or 9.0))
                for key in ("font_bold", "font_italic",
                            "font_underline", "text_wrap"):
                    self.item_fields[key].setChecked(bool(data.get(key)))
                default_halign = "center" if kind in (
                    "stream_connector", "symbol") else "left"
                self.item_fields["text_halign"].setCurrentText(
                    data.get("text_halign", default_halign)
                    or default_halign)
                self.item_fields["text_valign"].setCurrentText(
                    data.get("text_valign", "middle") or "middle")
            # Function list is live — authored in the Library
            # Explorer between two selections.
            studio = getattr(item.scene(), "studio", None) \
                if item.scene() else None
            names = []
            if studio is not None:
                from azeo_control_trainer.core.hmi.pvms.functions import FunctionStore
                names = FunctionStore(studio.store.root).names()
            self.anim_fn.clear()
            self.anim_fn.addItems([""] + names)
            for widget in self._anim_widgets():
                widget.setEnabled(kind != "pipe")
            self._load_anim_row()
            for key in ("x", "y", "w", "h", "rot"):
                self.item_fields[key].setEnabled(kind != "pipe")
        finally:
            self._loading_item = False

    def begin_item_format(self, item):
        """Expose and focus the most useful Format field for *item*.

        Double-click deliberately reuses this inspector instead of opening a
        second property dialog whose values could drift from the document.
        """
        self.show_item(item)
        kind = str(item.data.get("kind", ""))
        key = {
            "text": "text",
            "stream_connector": "text",
            "symbol": "text",
            "display_link": "link_text",
            "datalink": "data_path",
            "user_entry": "entry_label",
        }.get(kind)
        field = self.item_fields.get(key) if key else None
        if field is None:
            return None
        scroll = self._stack.currentWidget()
        if hasattr(scroll, "ensureWidgetVisible"):
            scroll.ensureWidgetVisible(field, 12, 12)
        field.setFocus(Qt.MouseFocusReason)
        if hasattr(field, "selectAll"):
            field.selectAll()
        return field

    def _apply_item_field(self, key: str) -> None:
        item = getattr(self, "_current_static", None)
        if item is None or getattr(self, "_loading_item", False):
            return
        studio = getattr(item.scene(), "studio", None) \
            if item.scene() else None
        if studio is None:
            return
        try:
            studio.enter_edit()
        except Exception:                           # noqa: BLE001
            return
        studio.checkpoint()
        field = self.item_fields[key]
        if key in ("x", "y", "w", "h", "rot"):
            try:
                value = float(field.text())
            except (TypeError, ValueError):
                return
            if key in ("x", "y"):
                item.data[key] = value
                item.setPos(item.data.get("x", 0),
                            item.data.get("y", 0))
            elif key in ("w", "h"):
                item.data[key] = value
                item.prepareGeometryChange()
                item.setRect(0, 0, item.data.get("w", 10),
                             item.data.get("h", 10))
            else:
                item.data["rot"] = value
                if hasattr(item, "apply_rotation"):
                    item.apply_rotation()
        elif key in ("fill_role", "line_role", "text_role"):
            value = field.currentData()
            if value:
                item.data[key] = value
                literal = {"fill_role": "fill", "line_role": "line",
                           "text_role": "text_color"}[key]
                item.data.pop(literal, None)
                self.item_fields[literal].setText("")
            else:
                item.data.pop(key, None)
        elif key == "symbol":
            value = field.currentData()
            if not value or value == item.data.get("symbol"):
                return
            from azeo_control_trainer.core.hmi.pvms.symbols import aspect
            item.data["symbol"] = value
            # Placement sets the rect from the glyph's own aspect so the
            # selection box hugs what is actually drawn. A swap must do
            # the same, or the new artwork renders stretched inside the
            # outline the previous one left behind.
            item.data["h"] = float(
                item.data.get("w", 60) or 60) * aspect(value)
            item.prepareGeometryChange()
            item.setRect(0, 0, item.data.get("w", 10),
                         item.data.get("h", 10))
        elif key == "width":
            item.data["width"] = int(field.value())
        elif key in ("arrow_start", "arrow_end"):
            value = field.currentData() or "none"
            # Writing either head retires the legacy `arrow` field —
            # two vocabularies for one property is how a display ends
            # up drawing a head nobody chose.
            item.data.pop("arrow", None)
            other = "arrow_end" if key == "arrow_start" \
                else "arrow_start"
            if other not in item.data:
                item.data[other] = (self.item_fields[other]
                                    .currentData() or "none")
            item.data[key] = value
            if item.data.get("arrow_start", "none") == "none" \
                    and item.data.get("arrow_end", "none") == "none":
                item.data.pop("arrow_start", None)
                item.data.pop("arrow_end", None)
        elif key in ("sides", "adjust", "radius", "start", "span"):
            item.data[key] = int(field.value())
        elif key == "crossover":
            value = field.currentData() or ""
            if value:
                item.data[key] = value
            else:
                item.data.pop(key, None)
        elif key in ("style", "arrow", "arrow_size", "cap"):
            value = field.currentText()
            if (key == "arrow" and value == "none") \
                    or (key == "arrow_size" and value == "medium") \
                    or (key == "cap" and value == "round") \
                    or (key == "style" and value == "solid"):
                item.data.pop(key, None)
            else:
                item.data[key] = value
        elif key == "fill_pct":
            value = int(field.value())
            if value >= 100:
                item.data.pop("fill_pct", None)
            else:
                item.data["fill_pct"] = value
        elif key == "fill_direction":
            value = field.currentText()
            if value == "bottom":
                item.data.pop("fill_direction", None)
            else:
                item.data["fill_direction"] = value
        elif key == "opacity":
            value = int(field.value())
            if value >= 100:
                item.data.pop("opacity", None)
            else:
                item.data["opacity"] = value
        elif key == "datalink_type":
            item.data["datalink_type"] = field.currentData() \
                or "numeric"
            studio.rebind_static(item)
        elif key == "data_path":
            item.data["path"] = field.text().strip()
            studio.rebind_static(item)
        elif key == "decimals":
            item.data["decimals"] = int(field.value())
        elif key == "units":
            if field.isChecked():
                item.data["units"] = True
            else:
                item.data.pop("units", None)
        elif key == "link_target":
            item.data["target"] = field.text().strip()
            studio.rebind_static(item)
        elif key == "link_text":
            item.data["text"] = field.text().strip() or "Display Link"
        elif key == "icon":
            item.data["icon"] = field.currentData() or "module_detail"
        elif key.startswith("entry_"):
            from azeo_control_trainer.core.hmi.pvms.elements import UserEntry
            entry = UserEntry.from_dict(item.data.get("entry", {}))
            if key == "entry_type":
                entry.kind = field.currentData() or "button"
            elif key == "entry_path":
                entry.path = field.text().strip()
            elif key == "entry_label":
                entry.label = field.text().strip()
            elif key == "entry_value":
                entry.value = self._entry_scalar(field.text())
            elif key == "entry_options":
                entry.options = self._entry_options(field.text())
            elif key == "entry_lo":
                entry.lo = float(field.value())
            elif key == "entry_hi":
                entry.hi = float(field.value())
            elif key == "entry_disabled":
                entry.disabled_reason = field.text().strip()
            item.data["entry"] = entry.to_dict()
            studio.rebind_static(item)
        elif key in ("fill", "line", "text", "text_color",
                     "font_family"):
            text = field.text().strip()
            if text:
                item.data[key] = text
                role_key = {"fill": "fill_role", "line": "line_role",
                            "text_color": "text_role"}.get(key)
                if role_key:
                    item.data.pop(role_key, None)
                    combo = self.item_fields[role_key]
                    blocked = combo.blockSignals(True)
                    combo.setCurrentIndex(0)
                    combo.blockSignals(blocked)
            else:
                item.data.pop(key, None)
        elif key == "font_size":
            item.data[key] = float(field.value())
        elif key in ("font_bold", "font_italic", "font_underline",
                     "text_wrap", "lock_aspect"):
            value = bool(field.isChecked())
            if key != "lock_aspect" and not value:
                item.data.pop(key, None)
            else:
                item.data[key] = value
        elif key in ("text_halign", "text_valign"):
            value = field.currentText()
            default = "left" if key == "text_halign" else "middle"
            if value == default:
                item.data.pop(key, None)
            else:
                item.data[key] = value
        elif key == "locked":
            item.data["locked"] = bool(field.isChecked())
        elif key == "routing_obstacle":
            if field.isChecked():
                item.data.pop("routing_obstacle", None)
            else:
                item.data["routing_obstacle"] = False
        elif key == "visible":
            # Absent means visible — shipped displays stay
            # byte-identical.
            if field.isChecked():
                item.data.pop("visible", None)
            else:
                item.data["visible"] = False
        item.update()
        studio.reroute_pipes()
        studio.mark_unsaved()
        if key in ("datalink_type", "data_path", "decimals", "units",
                   "link_target", "link_text", "icon") \
                or key.startswith("entry_"):
            self.show_item(item)

    @staticmethod
    def _entry_scalar(text: str):
        """Parse an option value without making every value a string."""
        value = str(text or "").strip()
        if value.lower() in ("true", "false"):
            return value.lower() == "true"
        try:
            return int(value)
        except ValueError:
            try:
                return float(value)
            except ValueError:
                return value

    @classmethod
    def _entry_options(cls, text: str) -> tuple:
        options = []
        for token in str(text or "").split(";"):
            value, separator, caption = token.strip().partition("=")
            if not value:
                continue
            options.append((cls._entry_scalar(value),
                            caption.strip() if separator else value))
        return tuple(options)

    def _build_display_page(self) -> QWidget:
        """Separate everyday display properties from lifecycle actions."""
        from PySide6.QtWidgets import QTabBar

        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        header = QHBoxLayout()
        self.display_title = QLabel(self.studio.display.name)
        _wrap_property_label(self.display_title)
        self.display_title.setObjectName("config_item_title")
        self.display_title.setFont(QFont("Segoe UI", 12, QFont.Bold))
        header.addWidget(self.display_title, 1)
        self.display_kind_chip = QLabel("DISPLAY")
        self.display_kind_chip.setStyleSheet(
            f"border: 1px solid {WF['bd']}; color: {WF['tx2']};"
            "font-size: 9pt; padding: 1px 8px; font-family: Consolas;")
        header.addWidget(self.display_kind_chip)
        lay.addLayout(header)
        self.display_tabs = QTabBar()
        self.display_tabs.setExpanding(True)
        self.display_tabs.setUsesScrollButtons(False)
        self.display_tabs.setElideMode(Qt.ElideNone)
        self.display_tabs.setDrawBase(False)
        for name in ("Basics", "Interaction"):
            self.display_tabs.addTab(name)
        self.display_tabs.setStyleSheet(
            "QTabBar::tab { padding: 3px 8px; font-size: 9pt;"
            f"color: {WF['tx2']}; background: transparent;"
            "border: none; }"
            "QTabBar::tab:selected {"
            f"color: {WF['navy']}; font-weight: 600;"
            f"border-bottom: 2px solid {WF['lapis']}; }}")
        lay.addWidget(self.display_tabs)
        self.display_basics = QWidget(page)
        self.display_interaction = QWidget(page)
        lay.addWidget(self.display_basics)
        lay.addWidget(self.display_interaction)
        interaction_lay = QVBoxLayout(self.display_interaction)
        interaction_lay.setContentsMargins(0, 0, 0, 0)
        lay = QVBoxLayout(self.display_basics)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.display_tabs.currentChanged.connect(self._show_display_tab)
        self._show_display_tab(0)

        self.class_interface_panel = QWidget()
        class_lay = QVBoxLayout(self.class_interface_panel)
        class_lay.setContentsMargins(0, 0, 0, 2)
        class_lay.setSpacing(4)
        class_head = QLabel("CLASS INTERFACE")
        class_head.setStyleSheet(_SECTION_STYLE)
        class_lay.addWidget(class_head)
        self.class_interface_summary = QLabel()
        self.class_interface_summary.setWordWrap(True)
        self.class_interface_summary.setTextInteractionFlags(
            Qt.TextSelectableByMouse)
        self.class_interface_summary.setStyleSheet(
            f"background: {WF['chrome']}; border-left: 3px solid "
            f"{WF['lapis']}; color: {WF['tx2']}; font-size: 9pt; "
            "padding: 6px 7px;")
        class_lay.addWidget(self.class_interface_summary)
        edit_interface = QPushButton("Edit Class Interface…")
        edit_interface.clicked.connect(self._edit_class_interface)
        class_lay.addWidget(edit_interface)
        validate_interface = QPushButton("Validate Reusable Class")
        validate_interface.clicked.connect(self._validate_class_interface)
        class_lay.addWidget(validate_interface)
        self.class_interface_panel.hide()
        lay.addWidget(self.class_interface_panel)

        self._display_only_widgets = []

        events_head = QLabel("DISPLAY OPEN / CLOSE EVENTS")
        events_head.setStyleSheet(_SECTION_STYLE)
        interaction_lay.addWidget(events_head)
        self._display_only_widgets.append(events_head)
        self.display_events_json = QPlainTextEdit()
        self.display_events_json.setMaximumHeight(90)
        self.display_events_json.setPlaceholderText(
            '{"open": [], "close": []}')
        interaction_lay.addWidget(self.display_events_json)
        self._display_only_widgets.append(self.display_events_json)
        apply_events = QPushButton("Apply Display Events")
        apply_events.clicked.connect(self._apply_display_events)
        interaction_lay.addWidget(apply_events)
        self._display_only_widgets.append(apply_events)
        display_script = QPushButton("Edit Display TypeScript…")
        display_script.clicked.connect(self._edit_display_script)
        interaction_lay.addWidget(display_script)
        interaction_lay.addStretch(1)
        self._display_only_widgets.append(display_script)

        # ISA-101 hierarchy: the level decides which frame and which
        # navigation slot the display lands in — configuration, not
        # scripting.
        hier = QLabel("HIERARCHY")
        hier.setStyleSheet(_SECTION_STYLE)
        lay.addWidget(hier)
        self._display_only_widgets.append(hier)
        hierarchy_holder = QWidget()
        hier_form = QFormLayout(hierarchy_holder)
        _polish_form(hier_form)
        self.level_combo = AuthoringComboBox()
        for level, name in ((1, "L1 · Plant overview"),
                            (2, "L2 · Unit control"),
                            (3, "L3 · Equipment detail"),
                            (4, "L4 · Diagnostics / support")):
            self.level_combo.addItem(name, level)
        self.level_combo.setCurrentIndex(self.studio.display.level - 1)
        self.level_combo.currentIndexChanged.connect(
            lambda i: (setattr(self.studio.display, "level",
                               self.level_combo.currentData()),
                       self.studio.mark_unsaved()))
        hier_form.addRow(QLabel("Level"), self.level_combo)
        self.parent_combo = AuthoringComboBox()
        self.parent_combo.setEditable(True)
        self._parent_guard = False

        def parent_changed(text: str) -> None:
            if self._parent_guard:
                return
            text = text.strip()
            if text != self.studio.display.parent:
                self.studio.display.parent = text
                self.studio.mark_unsaved()
        self.parent_combo.currentTextChanged.connect(parent_changed)
        hier_form.addRow(QLabel("Parent"), self.parent_combo)
        lay.addWidget(hierarchy_holder)
        self._display_only_widgets.append(hierarchy_holder)

        self._loading_display = False
        self.display_rows: dict[str, QWidget] = {}
        for section, fields in (
                ("Information", (("title", "Title"),
                                 ("description", "Description"))),
                ("Size", (("width", "Width"), ("height", "Height"),
                          ("safe_margin", "Safe Margin"),
                          ("fit", "Fit"))),
                ("Background", (("color", "Color"),)),
                ("View", (("view_type", "View Type"),))):
            head = QLabel(section)
            head.setStyleSheet(_SECTION_STYLE)
            lay.addWidget(head)
            form = QFormLayout()
            _polish_form(form)
            for key, label in fields:
                if key == "title":
                    value = QLabel("—")
                    _wrap_property_label(value)
                    value.setFont(QFont("Consolas", 8))
                elif key in ("description", "color"):
                    value = _DescriptionField() if key == "description" else QLineEdit()
                    if key == "color":
                        value.setPlaceholderText(
                            "#RRGGBB / empty = theme background")
                    value.editingFinished.connect(
                        lambda k=key: self._apply_display_property(k))
                elif key in ("width", "height", "safe_margin"):
                    value = QSpinBox()
                    value.setRange(0, 16384 if key != "safe_margin" else 512)
                    if key != "safe_margin":
                        value.setSpecialValueText("Auto")
                    value.setSuffix(" px")
                    value.editingFinished.connect(
                        lambda k=key: self._apply_display_property(k))
                else:
                    value = AuthoringComboBox()
                    options = (
                        (("Fit to Display Frame", "fit_to_frame"),
                         ("Fit to Content", "fit_to_content"))
                        if key == "fit" else
                        (("Scale to Display Frame", "scale_to_frame"),
                         ("Actual Size", "actual_size")))
                    for title, stored in options:
                        value.addItem(title, stored)
                    value.currentIndexChanged.connect(
                        lambda _index, k=key:
                        self._apply_display_property(k))
                value.setStyleSheet(
                    _FIELD_STYLE)
                self.display_rows[key] = value
                name = QLabel(label)
                name.setStyleSheet(f"color: {WF['tx2']};"
                                   "font-size: 9pt;")
                form.addRow(name, value)
            lay.addLayout(form)
        lay.addStretch(1)
        return page

    def _show_display_tab(self, index: int) -> None:
        self.display_basics.setVisible(index == 0)
        self.display_interaction.setVisible(index == 1)

    def _refresh_display_page(self) -> None:
        display = self.studio.display
        class_name = self.studio.edited_user_class_name()
        is_class = bool(class_name)
        self.display_kind_chip.setText(
            "FACEPLATE CLASS" if is_class and self._class_kind(
                class_name) == "faceplate" else
            "PVM CLASS" if is_class else "DISPLAY")
        self.display_tabs.setVisible(not is_class)
        if is_class:
            self.display_tabs.setCurrentIndex(0)
        self._show_display_tab(self.display_tabs.currentIndex())
        self.class_interface_panel.setVisible(is_class)
        for widget in self._display_only_widgets:
            widget.setVisible(not is_class)
        if is_class:
            self._refresh_class_interface(class_name)
        import json
        self.display_events_json.setPlainText(json.dumps(
            display.events, indent=2, ensure_ascii=False))
        self.display_title.setText(class_name or display.name)
        self.level_combo.setCurrentIndex(
            max(0, min(3, display.level - 1)))
        self._parent_guard = True
        try:
            self.parent_combo.clear()
            self.parent_combo.addItem("")
            store = self.studio.store
            if store.root.exists():
                for path in sorted(store.root.glob("*")):
                    if (path / "draft.json").exists() \
                            and path.name != display.name \
                            and not path.name.startswith(
                                self.studio.PVM_EDIT_PREFIX):
                        self.parent_combo.addItem(path.name)
            self.parent_combo.setCurrentText(display.parent)
        finally:
            self._parent_guard = False
        rows = self.display_rows
        self._loading_display = True
        try:
            rows["title"].setText(class_name or display.name)
            rows["description"].setPlainText(display.description)
            rows["width"].setValue(display.width)
            rows["height"].setValue(display.height)
            rows["safe_margin"].setValue(display.safe_margin)
            rows["color"].setText(display.background)
            for key in ("fit", "view_type"):
                combo = rows[key]
                index = combo.findData(getattr(display, key))
                combo.setCurrentIndex(max(index, 0))
        finally:
            self._loading_display = False

    def _class_kind(self, class_name: str) -> str:
        entry = self.studio.user_library().entries.get(class_name, {})
        return str(entry.get("definition_kind", "pvm"))

    def _refresh_class_interface(self, class_name: str) -> None:
        config = self.studio._config_for_name(class_name)
        if config is None:
            self.class_interface_summary.setText(
                "No saved interface document. Define typed public inputs "
                "before placing this class from control data.")
            return
        public = config.public_properties()
        internal = config.internal_properties()
        target = config.primary_drop_target()
        target_text = "None"
        if target is not None:
            accepted = ", ".join(target.accepted_block_types) or "none"
            target_text = f"{target.name}  ←  {accepted}"
        required = [prop.name for prop in public if prop.required]
        lines = [
            f"Public parameters: {len(public)}",
            f"Internal properties: {len(internal)}",
            "Required: " + (", ".join(required) or "none"),
            f"Primary drop target: {target_text}",
        ]
        issues = config.issues()
        lines.append("Preflight: ready" if not issues else
                     f"Preflight: {len(issues)} issue(s) block use")
        self.class_interface_summary.setText("\n".join(lines))

    def _edit_class_interface(self) -> None:
        class_name = self.studio.edited_user_class_name()
        opener = getattr(self.studio.window(), "open_pvm_config", None)
        if class_name and callable(opener):
            opener(class_name)

    def _validate_class_interface(self) -> None:
        class_name = self.studio.edited_user_class_name()
        if not class_name:
            return
        config = self.studio._config_for_name(class_name)
        issues = config.issues() if config is not None else ()
        self._refresh_class_interface(class_name)
        from azeo_control_trainer.core.presentation.headless import is_headless
        if is_headless():
            return
        if config is None:
            text = "No saved class interface exists."
        elif issues:
            text = "\n".join(str(issue) for issue in issues)
        else:
            text = "The reusable class interface is valid and placeable."
        QMessageBox.information(self, "Reusable class validation", text)

    def _apply_display_property(self, key: str) -> None:
        if self._loading_display:
            return
        display = self.studio.display
        widget = self.display_rows[key]
        if key in ("description", "color"):
            value = (widget.toPlainText() if key == "description" else widget.text()).strip()
            target = "background" if key == "color" else key
        elif key in ("width", "height", "safe_margin"):
            value, target = int(widget.value()), key
        else:
            value, target = widget.currentData(), key
        if getattr(display, target) == value:
            return
        self.studio.checkpoint()
        setattr(display, target, value)
        self.studio.apply_display_frame()
        self.studio.mark_unsaved()

    def _apply_display_events(self) -> None:
        import json
        from azeo_control_trainer.core.hmi.pvms.elements import ACTION_KINDS, Action

        try:
            payload = json.loads(
                self.display_events_json.toPlainText() or "{}")
            if not isinstance(payload, dict) \
                    or any(name not in ("open", "close") for name in payload):
                raise ValueError("display events contain only open and close")
            events = {}
            for name, rows in payload.items():
                if not isinstance(rows, list):
                    raise ValueError(f"{name} actions must be a list")
                actions = [Action.from_dict(row) for row in rows
                           if isinstance(row, dict)]
                if len(actions) != len(rows) \
                        or any(action.kind not in ACTION_KINDS
                               for action in actions):
                    raise ValueError(f"{name} contains an unsupported action")
                if actions:
                    events[name] = [action.to_dict() for action in actions]
        except (ValueError, TypeError) as error:
            self.display_events_json.setToolTip(str(error))
            return
        self.studio.display.events = events
        self.studio.mark_unsaved()
        self.display_events_json.setToolTip("Configured")

    def _edit_item_script(self) -> None:
        item = getattr(self, "_current_static", None)
        if item is None:
            return
        from ..script_editor import edit_script

        actions = list(item.data.get("actions", ()))
        action = next((row for row in actions
                       if row.get("kind") == "script"), None)
        edited = edit_script(
            str((action or {}).get("source", "")),
            runner=lambda source: self.studio.run_script(
                source, item, allow_writes=False),
            parent=self)
        if edited is None:
            return
        self.studio.checkpoint()
        if action is None:
            actions.append(
                {"event": "click", "kind": "script", "source": edited})
        else:
            action["source"] = edited
        item.data["actions"] = actions
        self.studio.rebind_static(item)
        self.studio.mark_unsaved()
        self.show_item(item)

    def _edit_display_script(self) -> None:
        from ..script_editor import edit_script

        events = dict(self.studio.display.events)
        actions = list(events.get("open", ()))
        action = next((row for row in actions
                       if row.get("kind") == "script"), None)
        edited = edit_script(
            str((action or {}).get("source", "")),
            runner=lambda source: self.studio.run_script(
                source, allow_writes=False), parent=self)
        if edited is None:
            return
        self.studio.checkpoint()
        if action is None:
            actions.append(
                {"event": "open", "kind": "script", "source": edited})
        else:
            action["source"] = edited
        events["open"] = actions
        self.studio.display.events = events
        self.studio.mark_unsaved()
        self._refresh_display_page()

    def show_pvm(self, item: PvmItem | None) -> None:
        if item is None:
            self._current_item = None
            for key, value in self.rows.items():
                value.setText("" if key in ("fill", "line") else "—")
            artwork = self.pvm_fields.get("symbol")
            if artwork is not None:
                _fill_symbol_combo(artwork, "",
                                   blank_label="Class default")
            self.selected_label.setText("")
            self._refresh_display_page()
            self._stack.setCurrentIndex(0)
            return
        self._stack.setCurrentIndex(1)
        self.selected_label.setText("1 selected")
        self._current_item = item
        self._rebuild_choices(item)
        pvm = item.pvm
        result = item.binding.result if item.binding else None
        self.rows["path"].setText(
            "/".join(str(v) for v in pvm.params.values()))
        self.rows["block_type"].setText(pvm.block_type)
        self.rows["pvm_class"].setText(f"{pvm.block_type} / {pvm.role}")
        if result is not None:
            self.rows["status"].setText(
                f"{result.quality.name.title()} · "
                f"{result.limit.name.replace('_', ' ').title()}")
            self.rows["units"].setText(result.units or "—")
            self.rows["eu_range"].setText(
                f"{result.eu_range[0]:g} … {result.eu_range[1]:g}"
                if result.eu_range else "—")
        self.rows["mode_set"].setText(
            "CAS AUT MAN" if item.mode_binding is not None else "—")
        self.rows["alarm_lims"].setText(
            "4 configured" if pvm.block_type == "AI" else "—")
        self.rows["size_role"].setText(
            pvm.role.replace("dynamo_", "").title()
            + (" / M" if pvm.role == "dynamo_inline" else " / S"))
        self.rows["standard"].setText(pvm.standard or "—")
        self.rows["fill"].setText(pvm.fill)
        self.rows["line"].setText(pvm.line)
        # Offered only where the class already draws equipment. Letting it
        # turn a value card into a silhouette would delete the PV/SP/OUT
        # rows the operator reads, and a control that looks live and does
        # nothing is worse than one that is plainly unavailable.
        artwork = self.pvm_fields["symbol"]
        draws_equipment = item.built_in_symbol_name() is not None
        artwork.setEnabled(draws_equipment)
        _fill_symbol_combo(artwork, pvm.symbol if draws_equipment else "",
                           blank_label="Class default")
        artwork.setToolTip(
            "The equipment drawing this PVM paints. Blank follows the PVM "
            "class. Appearance only: bindings, mode, alarm box and "
            "faceplate are unchanged."
            if draws_equipment else
            "This PVM class draws a value card rather than equipment, so "
            "it has no artwork to replace.")
        self.rows["placement"].setText(f"{pvm.x:.0f} · {pvm.y:.0f}")
        self.rows["wh"].setText(f"{pvm.w:.0f} · {pvm.h:.0f}")
        from .layers import layer_title
        self.rows["layer"].setText(layer_title(pvm.layer))
