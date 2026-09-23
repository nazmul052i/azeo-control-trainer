"""One retained navigation shell over the existing configuration workflows."""
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QSplitter, QStackedWidget, QVBoxLayout, QWidget,
)

from .configuration_catalog import ConfigurationCatalogWidget, _workers
from .configuration_chrome import CONFIGURATION_QSS, heading, icon, style_button


PAGES = (
    ("catalog", "Catalog", "search"), ("changes", "Changes", "exec_edit"),
    ("releases", "Releases", "download"), ("libraries", "Libraries", "templates"),
    ("training", "Training", "simulator"), ("recovery", "Recovery", "restore"),
    ("capture", "Import / export", "upload"),
)


class ConfigurationWorkspace(QWidget):
    page_definitions = PAGES

    def __init__(self, root=None, parent=None, *, session=None, initial_page="catalog", draft=None):
        super().__init__(parent)
        self.setObjectName("configurationWorkspace")
        self.setStyleSheet(CONFIGURATION_QSS)
        self.source_project = Path(root).resolve() if root else None
        self.draft = draft
        self.pages = {}
        self._project = None
        self._identity_worker = None
        self._authenticated = False
        self._initial = initial_page
        self._connection_dialog = None
        self.browser = ConfigurationCatalogWidget(root, self, session=session, hosted=True)
        if draft:
            self.browser._selected = draft.project
        self.browser.contextChanged.connect(self._context_changed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = heading(self, "Configuration", mark="datalog")
        header.setObjectName("configurationAppHeader")
        header.layout().setContentsMargins(18, 12, 18, 12)
        header.layout().setSpacing(14)
        self.connection = QLabel("Connecting…", objectName="configurationBadge")
        self.connection.setTextFormat(Qt.PlainText)
        self.identity = QLabel("Identity pending", objectName="configurationSubtitle")
        self.identity.setTextFormat(Qt.PlainText)
        self.identity.setMaximumWidth(245)
        self.identity.setWordWrap(True)
        header.layout().addWidget(self.connection)
        header.layout().addWidget(self.identity)
        layout.addWidget(header)
        context_frame = QFrame(self, objectName="configurationContext")
        context = QHBoxLayout(context_frame)
        context.setContentsMargins(18, 8, 18, 10)
        context.setSpacing(12)
        context.addWidget(QLabel("PROJECT", objectName="configurationFieldLabel"))
        # Reuse the catalog's selector, so every page has exactly one project owner.
        self.projects = self.browser.projects
        self.projects.setMinimumWidth(0)
        self.projects.setMaximumWidth(390)
        context.addWidget(self.projects, 1)
        self.revision = QLabel("Select a project", objectName="configurationRevision")
        context.addWidget(self.revision)
        context.addStretch()
        from PySide6.QtWidgets import QPushButton
        self.connection_button = style_button(QPushButton("Connection…"), "connect", quiet=True)
        self.connection_button.clicked.connect(self.connection_settings)
        self.connection_button.setEnabled(draft is None)
        context.addWidget(self.connection_button)
        layout.addWidget(context_frame)
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        self.navigation = QListWidget(objectName="configurationNavigation")
        self.navigation.setAccessibleName("Configuration workspace navigation")
        self.navigation.setMinimumWidth(150)
        self.navigation.setMaximumWidth(180)
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for key, title, mark in PAGES:
            item = QListWidgetItem(icon(mark), title)
            item.setData(Qt.UserRole, key)
            self.navigation.addItem(item)
        split.addWidget(self.navigation)
        self.stack = QStackedWidget()
        self.stack.setMinimumWidth(0)
        self.stack.addWidget(self.browser)
        split.addWidget(self.stack)
        split.setStretchFactor(1, 1)
        split.setHandleWidth(1)
        split.setSizes([164, 1050])
        layout.addWidget(split, 1)
        from .configuration_commands import ConfigurationMenuBar
        self.menu_bar = ConfigurationMenuBar(self)
        layout.insertWidget(0, self.menu_bar)
        self.navigation.currentRowChanged.connect(self._navigate)
        self.navigation.setCurrentRow(0)
        self._update_navigation(False)
        self._busy_timer = QTimer(self)
        self._busy_timer.setInterval(200)
        self._busy_timer.timeout.connect(self._update_busy)
        self._busy_timer.start()
        QTimer.singleShot(0, self._load_identity)

    def busy(self):
        return (self.browser._request is not None or self.browser._inspection_worker is not None
                or self._identity_worker is not None or any(
                    getattr(page, "worker", None) is not None or getattr(page, "_request", None) is not None
                    for page in self.findChildren(QDialog)))

    def has_review(self):
        return any(dialog.isVisible() and dialog.isWindow() and dialog is not self._connection_dialog
                   and not dialog.property("configurationReadOnly")
                   for dialog in self.findChildren(QDialog))

    def _update_busy(self):
        busy = self.busy()
        self.projects.setEnabled(not busy and not self.has_review() and self.draft is None)
        self.connection_button.setEnabled(not busy and not self.has_review() and self.draft is None)
        self.menu_bar.sync()

    def current_page_key(self):
        return PAGES[max(0, self.navigation.currentRow())][0]

    def _load_identity(self):
        from .configuration_editing import _Command
        if self._identity_worker is not None:
            return
        worker = _Command(lambda: self.browser.session.client.request("/v1/status"))
        self._identity_worker = worker
        _workers.add(worker)
        worker.ready.connect(self._identity_ready)
        worker.failed.connect(lambda _: self.identity.setText("Identity unavailable"))
        def finished():
            self._identity_worker = None
            _workers.discard(worker)
            worker.deleteLater()
        worker.finished.connect(finished)
        worker.start()

    def _identity_ready(self, result):
        self._authenticated = True
        actor = result.get("identity", {})
        self.identity.setText(actor.get("name", "Connected identity") +
                              (" · Administrator" if actor.get("administrator") else ""))
        self._update_navigation(self._project is not None and self.browser.index is not None)

    def _update_navigation(self, connected):
        for i, (key, _, _) in enumerate(PAGES):
            allowed = key in {"catalog", "capture"} or (key == "recovery" and self._authenticated) or (
                connected and self._project is not None and (
                    key == "changes" or self._project.get("mode") == "repository"))
            item = self.navigation.item(i)
            item.setFlags((Qt.ItemIsEnabled | Qt.ItemIsSelectable) if allowed else Qt.NoItemFlags)
            item.setToolTip("" if allowed else "Connect and select a repository project to use this workspace.")

    def _context_changed(self, result):
        index = result.get("index")
        project = index.project if index else None
        old_id = self._project.get("id") if self._project else None
        self._project = project
        state = result.get("state", "Unavailable")
        connected = state.startswith("Connected")
        if connected:
            self._authenticated = True
        elif state not in {"Loading", "Connecting"}:
            self._authenticated = False
        endpoint = urlsplit(self.browser.session.profile.get("url", ""))
        host = endpoint.hostname or "Service"
        if endpoint.port:
            host += f":{endpoint.port}"
        self.connection.setText(("Connected" if connected else "Cached / offline" if index else "Not connected") + " · " + host)
        self.connection.setToolTip(state)
        self.revision.setText((f"Revision set {project['generation']}" if project.get("mode") == "repository"
                               else f"Captured snapshot {project['generation']}") if project else "Select a project")
        self._update_navigation(connected)
        capture = self.pages.get(("service", "capture"))
        if capture is not None:
            capture.select_project(project["id"] if project else "")
        if old_id != (project.get("id") if project else None) or not index:
            self.open_page("catalog")
        if self._initial and (index or self._initial in {"capture", "recovery"}):
            initial, self._initial = self._initial, None
            self.open_page(initial)
        self._update_busy()

    def open_page(self, key):
        row = next(i for i, item in enumerate(PAGES) if item[0] == key)
        if not self.navigation.item(row).flags() & Qt.ItemIsEnabled:
            return None
        if self.navigation.currentRow() != row:
            self.navigation.setCurrentRow(row)
        else:
            self._navigate(row)
        return self.stack.currentWidget()

    def _navigate(self, row):
        if row < 0:
            return
        key = PAGES[row][0]
        if key == "catalog":
            self.stack.setCurrentWidget(self.browser)
            return
        project_id = self._project["id"] if self._project else ""
        cache_key = (project_id if key not in {"capture", "recovery"} else "service", key)
        if cache_key not in self.pages:
            page = self._create_page(key)
            if page is None:
                self.open_page("catalog")
                return
            page.setProperty("configurationEmbedded", True)
            page.setProperty("configurationPageTitle", next(title for code, title, _ in PAGES if code == key))
            page.setParent(self.stack, Qt.Widget)
            page.setMinimumSize(0, 0)
            self.pages[cache_key] = page
            self.stack.addWidget(page)
        self.stack.setCurrentWidget(self.pages[cache_key])

    def _create_page(self, key):
        profile = self.browser.session.profile
        if key == "recovery":
            from .configuration_recovery import RecoveryManager
            return RecoveryManager(self, profile=profile)
        if key == "capture":
            from ...azeo_explorer.configuration_database import ConfigurationDatabaseDialog
            from .configuration_catalog import context_root
            page = ConfigurationDatabaseDialog(self.source_project or context_root() or Path.cwd(), self, profile=profile)
            page.address.setReadOnly(True)
            page.token.setReadOnly(True)
            page.token.hide()
            page.address.setToolTip("Use Connection in the workspace header to change service identity.")
            page.connect_button.hide()
            page.address.hide()
            page.projects.hide()
            page.selected_project_id = self._project["id"] if self._project else ""
            QTimer.singleShot(0, page.connect_service)
            return page
        if not self._project:
            return None
        if key == "changes":
            from .configuration_editing import EditingLauncher, EngineeringChangesDialog
            return EngineeringChangesDialog(self.draft, self) if self.draft else EditingLauncher(self._project, self, profile=profile)
        if key == "releases":
            from .configuration_releases import ReleaseManager
            return ReleaseManager(self._project, self, profile=profile)
        if key == "libraries":
            from .configuration_libraries import LibraryManager
            return LibraryManager(self._project, self, profile=profile)
        if key == "training":
            from .configuration_training import TrainingManager
            return TrainingManager(self._project, self, profile=profile)
        return None

    def connection_settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Configuration connection")
        dialog.setStyleSheet(CONFIGURATION_QSS)
        dialog.resize(540, 220)
        form = QFormLayout(dialog)
        address = QLineEdit(self.browser.session.profile["url"])
        token = QLineEdit(self.browser.session.profile["token"])
        token.setEchoMode(QLineEdit.Password)
        form.addRow("Service address", address)
        form.addRow("Access token", token)
        form.addRow(QLabel("This connection applies to this workspace. Credentials are not saved here."))
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        connect = buttons.addButton("Connect", QDialogButtonBox.AcceptRole)
        style_button(connect, "connect", primary=True)
        form.addRow(buttons)
        error = QLabel()
        error.setWordWrap(True)
        form.addRow(error)
        def apply():
            try:
                self.switch_connection({"url": address.text().strip(), "token": token.text()})
            except ValueError as problem:
                error.setText(str(problem))
                return
            dialog.accept()
        connect.clicked.connect(apply)
        buttons.rejected.connect(dialog.reject)
        self._connection_dialog = dialog
        dialog.show()

    def switch_connection(self, profile):
        from ..configuration.catalog_client import CatalogSession
        if self.busy() or self.has_review() or self.draft is not None:
            raise ValueError("Finish the active request before changing connection. A draft keeps its original service identity.")
        # Validate the port before replacing any retained page or its credentials.
        urlsplit(profile["url"]).port
        session = CatalogSession(self.source_project, profile=profile)
        for page in self.pages.values():
            page.close()
            self.stack.removeWidget(page)
            page.deleteLater()
        self.pages.clear()
        self.browser.session = session
        self.browser._selected = ""
        self.browser._failed("Connecting to the selected service…")
        self.browser.projects.clear()
        self.browser._history.clear()
        self.identity.setText("Identity pending")
        self._authenticated = False
        self.browser.refresh()
        self._load_identity()

    def set_source_project(self, root):
        if self.busy() or self.has_review() or self.draft is not None:
            self.stack.currentWidget().status.setText(
                "Finish the active request or review before changing the local source project. A draft keeps its own source.")
            return False
        from ..configuration.catalog_client import CatalogSession
        self.source_project = Path(root).resolve()
        self.browser.session = CatalogSession(root, profile=self.browser.session.profile)
        self.browser._selected = ""
        self.browser.refresh()
        page = self.pages.get(("service", "capture"))
        if page:
            page.set_source_project(root)
        return True

    def close_pages(self):
        for page in self.findChildren(QDialog):
            if getattr(page, "worker", None) is not None or getattr(page, "_request", None) is not None:
                return False
        for dialog in self.findChildren(QDialog):
            if dialog.isWindow() and dialog.isVisible() and not dialog.close():
                return False
        if self.draft:
            for page in self.pages.values():
                if getattr(page, "workspace", None) is self.draft and not page.close():
                    return False
        self.browser.timer.stop()
        return True


def workspace_for(widget):
    while widget is not None:
        if isinstance(widget, ConfigurationWorkspace):
            return widget
        widget = widget.parentWidget()
    return None
