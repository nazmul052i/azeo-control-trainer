"""Shared chrome and command routing for controller and field-I/O tools."""
from dataclasses import dataclass
from html import escape
import logging
from weakref import ref

from PySide6.QtCore import QEvent, QItemSelectionModel, QObject, QPersistentModelIndex, Qt
from PySide6.QtGui import QAction, QContextMenuEvent, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QDialogButtonBox, QFormLayout,
    QLineEdit, QMenuBar, QPushButton, QTableWidget, QTabWidget,
    QTextBrowser, QVBoxLayout,
)
from shiboken6 import isValid

from .brand import UI
from .authoring_controls import name_form_fields
from .dialog_layout import fit_dialog_to_screen
from .configuration_chrome import CONFIGURATION_QSS, heading, icon, style_button
from .menu_style import studio_menu


ENGINEERING_QSS = CONFIGURATION_QSS + f"""
QDialog {{ background: {UI.chrome}; }}
QFrame#headerBar, QWidget#vioHeader {{ background: {UI.pane};
    border: 1px solid {UI.border_light}; border-radius: 6px; }}
QLabel#dlgTitle, QLabel#vioTitle {{ font-size: 14pt; font-weight: 600; color: {UI.text}; }}
QLabel#dlgSubtitle, QLabel#vioSubtitle {{ color: {UI.text_secondary}; }}
QFrame#metricCard {{ background: {UI.pane}; border: 1px solid {UI.border_light}; border-radius: 6px; }}
QLabel#metricValue {{ font-size: 18pt; font-weight: 600; color: {UI.blue}; }}
QLabel#metricLabel {{ color: {UI.text_secondary}; }}
QGroupBox {{ background: {UI.pane}; border: 1px solid {UI.border_light};
    border-radius: 6px; margin-top: 12px; padding: 16px 10px 10px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 5px; }}
QFrame#warningBar {{ background: #FFF5DE; border: 1px solid #E6D2A1; border-radius: 6px; }}
QFrame#warningBar QLabel {{ background: transparent; color: #785600; }}
QLabel#stateGood {{ color: {UI.success}; font-weight: 600; }}
QLabel#stateBad {{ color: {UI.error}; font-weight: 600; }}
QProgressBar {{ background: {UI.pane}; border: 1px solid {UI.border};
    border-radius: 5px; text-align: center; }}
QProgressBar::chunk {{ background: {UI.selection}; border-radius: 4px; }}
"""

_BUTTON_MARKS = {
    "Compile": "compile", "Run": "connect", "Stop": "disconnect", "Single Step": "exec_order",
    "Reset Simulator": "restore", "Run Step Test": "simulator", "Apply Injected Values": "params",
    "Discover": "search", "Add as Decommissioned": "new", "Add && Commission": "connect",
    "Controller Properties…": "io_config", "Offline All": "disconnect", "Close": "deactivate",
    "Cancel": "deactivate", "Warm switchover drill": "simulator", "Cold restart drill": "simulator",
    "Pause": "pause", "Step": "step_block", "Start Recording": "datalog", "Stop Recording": "datalog",
    "Apply Next Event": "step_block", "Restart Playback": "restore", "Apply Selected Row": "params",
}


def polish_dialog(dialog, *, title="", subtitle="", mark="properties"):
    """Use shared controls while preserving status labels and typed editors."""
    dialog.setStyleSheet(ENGINEERING_QSS)
    name_form_fields(dialog)
    layout = dialog.layout()
    layout.setContentsMargins(16, 12, 16, 12)
    layout.setSpacing(10)
    if title:
        header = heading(dialog, title, subtitle, mark)
        if isinstance(layout, QFormLayout):
            layout.insertRow(0, header)
        else:
            layout.insertWidget(0, header)
    for button in dialog.findChildren(QPushButton):
        button.setStyleSheet("")
        style_button(button, button.property("configurationIconName") or _BUTTON_MARKS.get(button.text()),
                     quiet=button.text() in {"Close", "Cancel"},
                     primary=button.property("configurationPrimary") is True or button.objectName() == "primary")
    for tabs in dialog.findChildren(QTabWidget):
        tabs.setStyleSheet("")
        tabs.setDocumentMode(True)
    for field in dialog.findChildren(QLineEdit):
        field.setStyleSheet("")
    for table in dialog.findChildren(QTableWidget):
        table.setShowGrid(False)
        table.setAlternatingRowColors(True)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(34)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        if table.selectionMode() == QAbstractItemView.NoSelection:
            table.setSelectionMode(QAbstractItemView.SingleSelection)
    for buttons in dialog.findChildren(QDialogButtonBox):
        for role in (QDialogButtonBox.Save, QDialogButtonBox.Ok):
            button = buttons.button(role)
            if button:
                style_button(button, "save" if role == QDialogButtonBox.Save else "connect", primary=True)


@dataclass
class Command:
    text: str
    callback: object
    mark: str = ""
    enabled: object = lambda: True
    shortcut: str = ""
    source: object = None
    command_id: str = ""


def button_command(button, *, text=None, mark="", shortcut="", command_id=""):
    target = ref(button)

    def available():
        value = target()
        return value is not None and isValid(value) and value.isEnabled()

    return Command(text or button.text(), lambda: target().click(),
                   mark or button.property("configurationIconName") or "properties",
                   available, shortcut, target,
                   command_id or button.property("commandId") or button.objectName())


class EngineeringMenus(QObject):
    """Menus invoke the same guarded commands as the visible controls."""
    def __init__(self, dialog, *, refresh=None, search=None, help_text=""):
        super().__init__(dialog)
        self._dialog = ref(dialog)
        self._tables = {}
        self._identities = {}
        self._button_actions = {}
        self.context_menu = None
        dialog.installEventFilter(self)
        self.bar = QMenuBar(dialog)
        self.bar.setNativeMenuBar(False)
        dialog.layout().setMenuBar(self.bar)
        self.actions = []
        self.file = self.menu("&File", [Command("Close window", dialog.close, "deactivate", shortcut="Ctrl+W", command_id="window.close")])
        self.view = self.menu("&View", [])
        if refresh:
            self.add(self.view, Command("Refresh", refresh, "restore", shortcut="F5", command_id="view.refresh"))
        if search:
            def find():
                search.setFocus()
                search.selectAll()
            self.add(self.view, Command("Find", find, "search", shortcut="Ctrl+F", command_id="view.find"))
        for tabs in dialog.findChildren(QTabWidget):
            for i in range(tabs.count()):
                self.add(self.view, Command(tabs.tabText(i), lambda t=tabs, n=i: t.setCurrentIndex(n), "properties"))
        self.help_text = help_text
        self.help_dialog = None
        self.help_menu = self.menu("&Help", [Command("Using this window", self.show_help, "comment", shortcut="F1", command_id="help.context")])

    def menu(self, title, commands):
        menu = studio_menu(parent=self.bar)
        menu.setTitle(title)
        if hasattr(self, "help_menu"):
            self.bar.insertMenu(self.help_menu.menuAction(), menu)
        else:
            self.bar.addMenu(menu)
        for command in commands:
            self.add(menu, command)
        return menu

    def dynamic_menu(self, title, commands):
        menu = self.menu(title, [])

        def populate():
            menu.clear()
            for command in commands():
                self.add(menu, command, shortcuts=False)
        menu.aboutToShow.connect(populate)
        return menu

    def add(self, menu, command, *, guard=lambda: True, prepare=lambda: None, shortcuts=True):
        action = QAction(command.text, menu)
        action.setObjectName(command.command_id)
        if command.mark:
            action.setIcon(icon(command.mark))

        def available():
            return guard() and command.enabled()

        def trigger():
            if guard():
                prepare()
                if command.enabled():
                    command.callback()

        action.setEnabled(available())
        action.triggered.connect(trigger)
        if shortcuts:
            menu.aboutToShow.connect(lambda: action.setEnabled(available()))
            if command.source:
                button = command.source()
                self._button_actions.setdefault(button, []).append((action, available))
                button.installEventFilter(self)
        if command.shortcut and shortcuts:
            action.setShortcut(QKeySequence(command.shortcut))
            action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
            self._dialog().addAction(action)
        menu.addAction(action)
        if shortcuts:
            self.actions.append(action)
        return action

    def table(self, table, commands=lambda row: (), *, title="Selection", identity_columns=(0,)):
        self._tables[table.viewport()] = (table, commands, title)
        self._identities[table] = identity_columns
        table.viewport().installEventFilter(self)

    def eventFilter(self, watched, event):  # noqa: N802
        if event.type() == QEvent.Show and watched is self._dialog():
            fit_dialog_to_screen(watched)
            name_form_fields(watched)
        if event.type() == QEvent.EnabledChange and watched in self._button_actions:
            for action, available in self._button_actions[watched]:
                action.setEnabled(available())
        if event.type() == QEvent.ContextMenu and watched in self._tables:
            try:
                self._context_event(watched, event)
            except Exception:
                logging.getLogger(__name__).exception("Could not open engineering context menu")
            return True
        return super().eventFilter(watched, event)

    def _context_event(self, watched, event):
        table, commands, title = self._tables[watched]
        keyboard = event.reason() == QContextMenuEvent.Keyboard
        index = table.currentIndex() if keyboard else table.indexAt(event.pos())
        if not index.isValid():
            return
        selection = table.selectionModel()
        flags = QItemSelectionModel.NoUpdate
        if not selection.isSelected(index):
            flags = QItemSelectionModel.ClearAndSelect
            if table.selectionBehavior() == QAbstractItemView.SelectRows:
                flags |= QItemSelectionModel.Rows
        # Re-selecting the same row reset pending disturbance edits; selecting
        # an already selected member also used to discard a Ctrl-selection.
        selection.setCurrentIndex(index, flags)
        self.open_context(table, index, commands(index.row()), title,
                          table.viewport().mapToGlobal(table.visualRect(index).center())
                          if keyboard else event.globalPos())

    def open_context(self, table, index, commands, title, position):
        if self.context_menu:
            self.context_menu.close()
            self.context_menu.deleteLater()
        anchor = QPersistentModelIndex(index.siblingAtColumn(0))
        identity = anchor.data(Qt.DisplayRole)
        columns = self._identities.get(table, (0,))
        token = tuple(table.model().index(index.row(), c).data() for c in columns)
        menu = studio_menu(escape(str(identity)), escape(title), self._dialog())
        self.context_menu = menu

        def guard():
            # Polling may replace rows while a popup is open. Never apply an
            # input override or module command to the new occupant of that row.
            return isValid(table) and table.isEnabled() and anchor.isValid() and not table.isRowHidden(anchor.row()) and token == tuple(
                table.model().index(anchor.row(), c).data() for c in columns)

        def prepare():
            table.selectionModel().setCurrentIndex(
                table.model().index(anchor.row(), index.column()), QItemSelectionModel.NoUpdate)

        def cell_text(column):
            value = table.model().index(anchor.row(), column).data()
            return "" if value is None else str(value)

        for command in commands:
            self.add(menu, command, guard=guard, prepare=prepare, shortcuts=False)
        if commands:
            menu.addSeparator()
        self.add(menu, Command("Copy cell", lambda: QApplication.clipboard().setText(
            cell_text(index.column())), "copy"),
            guard=guard, shortcuts=False)
        self.add(menu, Command("Copy row", lambda: QApplication.clipboard().setText("\t".join(
            cell_text(c) for c in range(table.columnCount()))), "copy"),
            guard=guard, shortcuts=False)
        selected = [QPersistentModelIndex(i) for i in table.selectionModel().selectedRows()]
        if len(selected) > 1:
            def selected_cell(index, column):
                return table.model().index(index.row(), column).data()
            tokens = [tuple(selected_cell(i, c) for c in columns) for i in selected]
            def selection_valid():
                return guard() and all(i.isValid() and not table.isRowHidden(i.row()) and
                    token == tuple(selected_cell(i, c) for c in columns)
                    for i, token in zip(selected, tokens))
            self.add(menu, Command("Copy selected rows", lambda: QApplication.clipboard().setText(
                "\n".join("\t".join("" if (value := selected_cell(i, c)) is None else str(value)
                                    for c in range(table.columnCount())) for i in selected)), "copy"),
                guard=selection_valid, shortcuts=False)
        menu.popup(position)
        return menu

    def show_help(self):
        if self.help_dialog is None:
            self.help_dialog = QDialog(self._dialog())
            self.help_dialog.setWindowTitle("Engineering tool help")
            layout = QVBoxLayout(self.help_dialog)
            browser = QTextBrowser()
            browser.setPlainText(self.help_text + "\n\nF1: Help    F5: Refresh (where available)\n"
                                 "Ctrl+W: Close window    Shift+F10: Selected row menu\n"
                                 "Right-click a row for its available commands and copy actions.")
            layout.addWidget(browser)
            buttons = QDialogButtonBox(QDialogButtonBox.Close)
            buttons.rejected.connect(self.help_dialog.close)
            layout.addWidget(buttons)
            polish_dialog(self.help_dialog)
            self.help_dialog.resize(540, 350)
        self.help_dialog.show()
        fit_dialog_to_screen(self.help_dialog)
        self.help_dialog.raise_()
