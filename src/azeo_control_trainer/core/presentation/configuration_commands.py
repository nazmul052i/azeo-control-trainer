"""Configuration menus route to the same guarded commands as the visible controls."""
from html import escape
from pathlib import Path
import weakref

from PySide6.QtCore import QEvent, QItemSelectionModel, QModelIndex, QObject, QPersistentModelIndex, Qt, QUrl
from PySide6.QtGui import QAction, QContextMenuEvent, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QDialog, QLineEdit, QMenuBar, QPlainTextEdit, QPushButton,
    QMenu, QTableView, QTextEdit, QToolButton, QVBoxLayout,
)
from shiboken6 import isValid

from .menu_style import studio_menu
from .studio_icons import studio_icon


def pending(page):
    return any(getattr(page, key, None) is not None for key in ("worker", "_request", "_inspection_worker"))


def owned_controls(page, cls):
    for control in page.findChildren(cls):
        owner = control.parentWidget()
        while owner is not None and owner is not page and not isinstance(owner, QDialog):
            owner = owner.parentWidget()
        if owner is page and control.isVisibleTo(page):
            yield control


def add_command(menu, label, callback, *, mark=None, available=lambda: True):
    action = menu.addAction(studio_icon(mark, 16), label) if mark else menu.addAction(label)
    action.setEnabled(bool(available()))
    action.triggered.connect(lambda _=False: callback() if available() else None)
    return action


def copy_rows(table, *, cell=False):
    current = table.currentIndex()
    if not current.isValid():
        return
    if cell:
        text = display_text(current.data())
    else:
        model = table.model()
        rows = sorted({i.row() for i in table.selectionModel().selectedIndexes()}) or [current.row()]
        text = "\n".join("\t".join(display_text(model.index(row, column).data())
                                   for column in range(model.columnCount())) for row in rows)
    QApplication.clipboard().setText(text)


def display_text(value):
    return "" if value is None else str(value)


class ConfigurationMenuBar(QMenuBar):
    @property
    def host(self):
        return self._host_ref()

    def __init__(self, workspace):
        super().__init__(workspace)
        self._host_ref = weakref.ref(workspace)
        self.setNativeMenuBar(False)
        self.setAccessibleName("Configuration menu bar")
        self.actions_by_name = {}
        self.menus = {}
        self._focus = None
        self._help = None
        self._guide_path = self._guide()
        for name in ("File", "Edit", "View", "Object", "Tools", "Help"):
            menu = studio_menu(parent=self)
            menu.setTitle("&" + name)
            menu.aboutToShow.connect(self._remember_focus)
            self.addMenu(menu)
            self.menus[name] = menu
        self.menus["Object"].aboutToShow.connect(self._object_commands)
        self._add("File", "Connection…", workspace.connection_button.click, "connect",
                  enabled=lambda: not workspace.busy() and not workspace.has_review() and workspace.draft is None)
        self._add("File", "Select project", self._select_project, "open",
                  enabled=lambda: workspace.projects.isEnabled())
        self.menus["File"].addSeparator()
        self._add("File", "Import / export…", lambda: workspace.open_page("capture"), "upload")
        self.menus["File"].addSeparator()
        self._add("File", "Close window", lambda: workspace.window().close(), "deactivate", "Ctrl+W",
                  enabled=lambda: not workspace.busy())
        self._add("Edit", "Copy", self._copy, "copy", "Ctrl+C", enabled=self._can_copy)
        self._add("Edit", "Copy object path", self._copy_path, "copy", "Ctrl+Shift+C",
                  enabled=lambda: workspace.stack.currentWidget() is workspace.browser and bool(workspace.browser._path))
        self._add("Edit", "Select all", self._select_all, "select_all", "Ctrl+A", enabled=self._can_select_all)
        self.menus["Edit"].addSeparator()
        self._add("Edit", "Find…", self._find, "search", "Ctrl+F", enabled=lambda: self._search() is not None)
        self._add("View", "Refresh", self._refresh, "restore", "F5", enabled=self._can_refresh)
        nav = self._add("View", "Navigation pane", self._toggle_navigation, "show", "Ctrl+B")
        nav.setCheckable(True)
        nav.setChecked(True)
        self.menus["View"].addSeparator()
        self.page_actions = {}
        for number, (key, title, mark) in enumerate(workspace.page_definitions, 1):
            action = self._add("View", title, lambda key=key: workspace.open_page(key), mark,
                               f"Ctrl+Alt+{number}", enabled=lambda number=number: bool(
                                   workspace.navigation.item(number - 1).flags() & Qt.ItemIsEnabled))
            action.setCheckable(True)
            self.page_actions[key] = action
        for key, title, mark in workspace.page_definitions[1:]:
            self._add("Tools", title + "…", lambda key=key: workspace.open_page(key), mark,
                      enabled=lambda key=key: self.page_actions[key].isEnabled())
        self._add("Help", "Configuration help", self._show_help, "comment", "F1")
        self._add("Help", "User guide", self._open_guide, "open", enabled=lambda: self._guide_path is not None)
        self.menus["Edit"].aboutToShow.connect(self.sync)
        self.menus["File"].aboutToShow.connect(self.sync)
        self.menus["View"].aboutToShow.connect(self.sync)
        self.menus["Tools"].aboutToShow.connect(self.sync)
        QApplication.instance().focusChanged.connect(self._focus_changed)

    def _focus_changed(self, _previous, current):
        if self.host is None or not isValid(self.host):
            return
        if current is not None and self.host.isAncestorOf(current) and not isinstance(current, (QMenu, QMenuBar)):
            self._focus = current
        self.sync()

    def _add(self, menu, label, callback, mark, shortcut=None, *, enabled=lambda: True):
        action = QAction(studio_icon(mark, 16), label, self.host)
        # Limit shortcuts to this workspace; a hidden Tag Database tab must not
        # compete with Control Designer's existing F5, Ctrl+F or clipboard keys.
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
            action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
            self.host.addAction(action)
        action.triggered.connect(lambda _=False: callback() if enabled() else None)
        self.menus[menu].addAction(action)
        self.actions_by_name[label] = (action, enabled)
        return action

    def sync(self):
        if self.host is None or not isValid(self.host.stack):
            return
        for action, enabled in self.actions_by_name.values():
            action.setEnabled(bool(enabled()))
        for key, action in self.page_actions.items():
            action.setChecked(self.host.current_page_key() == key)

    def _remember_focus(self):
        focus = QApplication.focusWidget()
        if focus and self.host.isAncestorOf(focus) and not isinstance(focus, (QMenu, QMenuBar)):
            self._focus = focus

    def _editor(self):
        focus = QApplication.focusWidget()
        for candidate in (focus, self._focus):
            if candidate is not None and isValid(candidate) and self.host.isAncestorOf(candidate) and candidate.isVisible():
                while candidate is not self.host:
                    if isinstance(candidate, (QLineEdit, QTextEdit, QPlainTextEdit, QTableView)):
                        return candidate
                    candidate = candidate.parentWidget()
                    if candidate is None:
                        break
        return None

    def _can_copy(self):
        editor = self._editor()
        if isinstance(editor, QTableView):
            return editor.currentIndex().isValid()
        if isinstance(editor, QLineEdit):
            return editor.hasSelectedText() and editor.echoMode() == QLineEdit.Normal
        return bool(editor and editor.textCursor().hasSelection())

    def _copy(self):
        editor = self._editor()
        if isinstance(editor, QTableView):
            copy_rows(editor)
        elif editor is not None:
            editor.copy()

    def _copy_path(self):
        QApplication.clipboard().setText(self.host.browser._path)

    def _select_project(self):
        self.host.projects.setFocus()
        self.host.projects.showPopup()

    def _select_all(self):
        editor = self._editor()
        if editor is not None:
            editor.selectAll()

    def _can_select_all(self):
        editor = self._editor()
        if isinstance(editor, QTableView):
            return editor.selectionMode() in (QTableView.MultiSelection, QTableView.ExtendedSelection)
        return editor is not None

    def _search(self):
        page = self.host.stack.currentWidget()
        return next((field for field in (getattr(page, "search", None), getattr(page, "comparison_search", None))
                     if field is not None and field.isVisibleTo(page)), None)

    def _find(self):
        field = self._search()
        if field is not None:
            field.setFocus()
            field.selectAll()

    def _can_refresh(self):
        page = self.host.stack.currentWidget()
        return not pending(page) and any(callable(getattr(page, key, None)) for key in ("refresh", "load_sessions", "load_page"))

    def _refresh(self):
        page = self.host.stack.currentWidget()
        callback = next(getattr(page, key) for key in ("refresh", "load_sessions", "load_page") if callable(getattr(page, key, None)))
        callback()

    def _toggle_navigation(self):
        self.host.navigation.setVisible(self.actions_by_name["Navigation pane"][0].isChecked())

    def _object_commands(self):
        menu = self.menus["Object"]
        menu.clear()
        page = self.host.stack.currentWidget()
        if page is self.host.browser:
            for label, callback, mark, enabled in catalog_commands(page):
                add_command(menu, label, callback, mark=mark, available=enabled)
        else:
            for button in owned_controls(page, QPushButton):
                if button.text() not in {"Refresh", "Previous", "Next"}:
                    add_command(menu, button.text(), button.click, mark=button.property("configurationIconName"),
                                available=lambda b=button: isValid(b) and b.isEnabled() and not pending(page))
            for button in owned_controls(page, QToolButton):
                if button.menu():
                    menu.addSeparator()
                    for source in button.menu().actions():
                        if not source.isSeparator():
                            action = add_command(menu, source.text(), source.trigger, available=lambda a=source, b=button: (
                                isValid(a) and isValid(b) and a.isEnabled() and b.isEnabled() and not pending(page)))
                            action.setIcon(source.icon())

    @staticmethod
    def _guide():
        root = Path(__file__).resolve().parents[4]
        return next((p for p in (root / "output/pdf/Azeo_Control_Trainer_User_Manual_Updated.pdf",
                                 root / "output/pdf/Azeo_Control_Trainer_User_Manual.pdf", root / "docs/USER_MANUAL.md") if p.is_file()), None)

    def _open_guide(self):
        path = self._guide()
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _show_help(self):
        if self._help is None:
            from .configuration_chrome import CONFIGURATION_QSS
            self._help = QDialog(self.host, Qt.Window)
            self._help.setProperty("configurationReadOnly", True)
            self._help.setWindowTitle("Configuration help — Azeo")
            self._help.setStyleSheet(CONFIGURATION_QSS)
            self._help.resize(680, 560)
            layout = QVBoxLayout(self._help)
            body = QTextEdit()
            body.setReadOnly(True)
            body.setHtml("<h2>Configuration workspace</h2><p>Select a project in the header. "
                         "Use View or the navigation pane to switch pages; your selections and filters are retained.</p>"
                         "<p><b>Catalog:</b> inspect captured properties, parameters and references. Right-click an object "
                         "for its available commands. Configuration values are separate from live process values.</p>"
                         "<p><b>Changes:</b> work in recoverable drafts and review before check-in. "
                         "<b>Releases:</b> include modules and graphics, validate, then review deployment. "
                         "<b>Libraries:</b> select instances before reviewing pins or adoption.</p>"
                         "<p><b>Training:</b> retain a validated baseline and create trainee copies. "
                         "<b>Recovery:</b> back up configuration and rehearse an isolated restore. "
                         "<b>Import / export:</b> review local files before capturing a shared snapshot.</p>"
                         "<h3>Keyboard</h3><p>F5 — refresh this page<br>Ctrl+F — find on this page<br>"
                         "Ctrl+C — copy selected text or table rows<br>Ctrl+Shift+C — copy the catalog object path<br>"
                         "Ctrl+Alt+1…7 — switch workspace pages<br>Ctrl+B — show/hide navigation<br>"
                         "Shift+F10 — context menu for the focused row<br>Ctrl+W — close this window</p>")
            layout.addWidget(body)
        self._help.show()
        self._help.raise_()
        self._help.activateWindow()


def catalog_commands(page):
    def selected():
        return bool(page.index and page._path in page.index.entries)
    commands = [("Properties", lambda: page.tabs.setCurrentIndex(0), "properties", selected)]
    for button in (page.loop_button, page.preview_button):
        commands.append((button.text(), button.click, button.property("configurationIconName"), lambda button=button: selected() and button.isEnabled()))
    for label, index, mark in (("Parameters", 1, "params"), ("References", 2, "xref"), ("Revision history", 6, "history")):
        commands.append((label, lambda index=index: page.tabs.setCurrentIndex(index), mark, selected))
    commands.append(("Copy object path", lambda: QApplication.clipboard().setText(page._path), "copy", selected))
    return commands


class ConfigurationTableMenu(QObject):
    @property
    def page(self):
        return self._page_ref()

    def __init__(self, page, table):
        super().__init__(table)
        self._page_ref = weakref.ref(page)
        self.table, self.menu = table, None
        table.installEventFilter(self)
        table.viewport().installEventFilter(self)
        table.model().modelAboutToBeReset.connect(self.close)

    def close(self, *_):
        if self.menu is not None:
            self.menu.close()

    def eventFilter(self, watched, event):  # noqa: N802
        if event.type() != QEvent.ContextMenu:
            return False
        if event.reason() == QContextMenuEvent.Keyboard:
            index = self.table.currentIndex()
            position = self.table.viewport().mapToGlobal(self.table.visualRect(index).center())
        else:
            point = event.pos() if watched is self.table.viewport() else self.table.viewport().mapFrom(self.table, event.pos())
            index = self.table.indexAt(point)
            position = event.globalPos()
        self.build(index).popup(position)
        event.accept()
        return True

    def build(self, index):
        if self.menu is not None:
            self.menu.close()
            self.menu.deleteLater()
        table, page = self.table, self.page
        persistent = QPersistentModelIndex(index)
        def row_values():
            return tuple(display_text(table.model().index(persistent.row(), col).data()) for col in range(table.model().columnCount()))
        token = row_values() if index.isValid() else ()
        def valid():
            return persistent.isValid() and row_values() == token
        def select():
            if valid():
                table.selectionModel().setCurrentIndex(QModelIndex(persistent), QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        select()
        identity = next((v for v in token if v), table.accessibleName())
        self.menu = menu = studio_menu(escape(identity[:100]), escape(table.accessibleName()), table)
        def command(label, callback, mark=None, available=lambda: True):
            def run():
                select()
                callback()
            return add_command(menu, label, run, mark=mark, available=lambda: valid() and available())
        if index.isValid():
            first = table.model().index(index.row(), 0)
            if first.data(Qt.CheckStateRole) is not None and first.flags() & Qt.ItemIsUserCheckable:
                include = command("Include in review", lambda: table.model().setData(
                    table.model().index(persistent.row(), 0), Qt.Checked.value if include.isChecked() else Qt.Unchecked.value, Qt.CheckStateRole),
                    available=lambda: not pending(page))
                include.setCheckable(True)
                include.setChecked(first.data(Qt.CheckStateRole) in (Qt.Checked, Qt.Checked.value))
            self._specific_commands(command, persistent)
            menu.addSeparator()
            command("Copy cell", lambda: copy_rows(table, cell=True), "copy")
            command("Copy row", lambda: copy_rows(table), "copy")
        if callable(getattr(page, "refresh", None)):
            add_command(menu, "Refresh", page.refresh, mark="restore", available=lambda: not pending(page))
        return menu

    def _specific_commands(self, command, index):
        page, table = self.page, self.table
        if table is getattr(page, "results", None) and hasattr(page, "inspect_loop"):
            # Returning from a followed reference leaves the results' current row
            # unchanged; selecting that row again does not emit currentRowChanged.
            page.inspect(page.result_model.rows[index.row()]["path"])
            for label, callback, mark, enabled in catalog_commands(page):
                command(label, callback, mark, enabled)
        elif table is getattr(page, "parameters", None) and hasattr(page, "inspect"):
            path = page.parameter_model.rows[index.row()]["path"]
            command("Open parameter properties", lambda: page.inspect(path), "properties")
        elif table is getattr(page, "references", None) and hasattr(page, "inspect"):
            row = page.reference_model.rows[index.row()]
            for field in ("source", "target"):
                path = row[field]
                command("Follow " + field, lambda path=path: page.inspect(path), "xref",
                        lambda path=path: bool(page.index and path in page.index.entries))
        elif table is getattr(page, "revisions", None) and hasattr(page, "show_revision"):
            command("Open recorded revision…", lambda: page.show_revision(QModelIndex(index)), "history", lambda: not pending(page))
        elif table is getattr(page, "backups", None):
            command("Rehearse selected restore", page.rehearse, "restore", lambda: not pending(page))
            command("Export selected backup…", page.export, "save_as", lambda: not pending(page))
        elif table is getattr(page, "jobs", None):
            row = page.job_model.rows[index.row()]
            for label, callback, mark in (("Retry failed deployment", page.retry_job, "restore"), ("Cancel failed deployment", page.cancel_job, "delete")):
                command(label, callback, mark, lambda: not pending(page) and row["state"] == "failed")
        else:
            buttons = []
            if table is getattr(page, "objects", None) and hasattr(page, "review_button"):
                buttons = [page.review_button]
            elif table is getattr(page, "releases", None) and hasattr(page, "deploy_button"):
                buttons = [page.deploy_button]
            elif table is getattr(page, "comparison", None) and hasattr(page, "upload_button"):
                buttons = [page.upload_button]
            elif table is getattr(page, "instances", None):
                buttons = [b for b in owned_controls(page, QPushButton) if b.text().startswith("Review")]
            elif table is getattr(page, "table", None):
                if hasattr(page, "resume"):
                    buttons = [page.resume]
                elif hasattr(page, "clone_button"):
                    command("Name trainee copy…", page.trainee_name.setFocus, "new", lambda: not pending(page))
                    buttons = [page.clone_button]
                elif hasattr(page, "open_selected"):
                    buttons = [page.commands.buttons[label] for label in ("Open in Studio", "Compare / resolve…")]
                elif hasattr(page, "_object_selected"):
                    command("Show revision history", page._object_selected, "history", lambda: not pending(page))
            for button in buttons:
                command(button.text(), button.click, button.property("configurationIconName"),
                        available=lambda b=button: b.isEnabled() and not pending(page))


def install_table_menus(page):
    controllers = getattr(page, "_configuration_table_menus", {})
    for table in page.findChildren(QTableView):
        owner = table.parentWidget()
        while owner is not None and owner is not page and not isinstance(owner, QDialog):
            owner = owner.parentWidget()
        if owner is page and table not in controllers:
            controllers[table] = ConfigurationTableMenu(page, table)
    page._configuration_table_menus = controllers
