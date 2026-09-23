"""Repository snapshot browser, reached through the existing Project Administrator."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

from azeo_control_trainer.core.configuration.client import ConfigurationClient, read_profile
from azeo_control_trainer.core.configuration.documents import export_project, read_project
from azeo_control_trainer.core.presentation.configuration_chrome import (
    CONFIGURATION_QSS, heading, polish_page, progress, ConfigurationComboBox as QComboBox,
)
from azeo_control_trainer.core.presentation.headless import is_headless


class _Request(QThread):
    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            self.ready.emit(self.operation())
        except Exception as error:
            self.failed.emit(str(error))
        finally:
            self.operation = None


class ConfigurationDatabaseDialog(QDialog):
    """All I/O is asynchronous, including local import scanning and export writes."""
    def __init__(self, project: Path, parent=None, *, profile=None):
        super().__init__(parent)
        self.source_project = Path(project)
        self.client = None
        self._request = None
        self._preview = None
        self._pending_command = None
        self._offset = 0
        self._audit_after = 0
        self._audit_cursors = {0: 0}
        self.selected_project_id = None
        self.setWindowTitle("Azeo Configuration Database")
        available = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1120, available.width() - 40), min(760, available.height() - 60))
        self.setStyleSheet(CONFIGURATION_QSS)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.addWidget(heading(self, "Import / export", "Review a local project before capturing a shared snapshot.", "upload"))
        profile = profile or read_profile()
        connection = QHBoxLayout()
        self.address = QLineEdit(profile["url"])
        self.address.setPlaceholderText("Configuration service address")
        self.token = QLineEdit(profile["token"])
        self.token.setEchoMode(QLineEdit.Password)
        self.token.setPlaceholderText("Your service access token")
        self.connect_button = QPushButton("Connect")
        self.connect_button.setObjectName("primary")
        self.connect_button.setProperty("configurationPrimary", True)
        self.connect_button.clicked.connect(self.connect_service)
        connection.addWidget(self.address, 2)
        connection.addWidget(self.token, 1)
        connection.addWidget(self.connect_button)
        root.addLayout(connection)
        toolbar = QHBoxLayout()
        self.projects = QComboBox()
        self.projects.setMinimumWidth(240)
        self.projects.currentIndexChanged.connect(self._project_changed)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search configured objects or tags…")
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self._search_changed)
        self.search.textChanged.connect(lambda: self._debounce.start())
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.connect_service)
        self.export_button = QPushButton("Export snapshot…")
        self.export_button.clicked.connect(lambda: self.export_snapshot())
        toolbar.addWidget(self.projects)
        toolbar.addWidget(self.search, 1)
        toolbar.addWidget(self.refresh_button)
        toolbar.addWidget(self.export_button)
        root.addLayout(toolbar)
        self.tabs = QTabWidget()
        self.table = QTableWidget()
        self.tags = QTableWidget()
        self.audit = QTableWidget()
        for label, table in (("Objects", self.table), ("Tags", self.tags), ("Audit", self.audit)):
            table.setSelectionBehavior(QTableWidget.SelectRows)
            table.setSelectionMode(QTableWidget.SingleSelection)
            table.setEditTriggers(QTableWidget.NoEditTriggers)
            table.setWordWrap(False)
            table.verticalHeader().hide()
            self.tabs.addTab(table, label)
        self.tabs.currentChanged.connect(self._search_changed)
        self.table.itemSelectionChanged.connect(self._object_selected)
        self.tags.itemSelectionChanged.connect(self._tag_selected)
        self.audit.itemSelectionChanged.connect(self._audit_selected)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setPlaceholderText("Select an object to inspect its immutable revisions.")
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.addWidget(self.tabs)
        split.addWidget(self.details)
        split.setSizes([650, 310])
        root.addWidget(split, 1)
        pages = QHBoxLayout()
        self.page_label = QLabel()
        self.previous = QPushButton("Previous")
        self.next = QPushButton("Next")
        self.previous.clicked.connect(lambda: self._page(-1))
        self.next.clicked.connect(lambda: self._page(1))
        pages.addWidget(self.page_label, 1)
        pages.addWidget(self.previous)
        pages.addWidget(self.next)
        root.addLayout(pages)
        capture = QWidget()
        capture_layout = QHBoxLayout(capture)
        capture_layout.setContentsMargins(0, 8, 0, 0)
        self.import_name = QLineEdit(self.source_project.name)
        self.import_name.setPlaceholderText("Repository project name")
        self.import_name.textChanged.connect(self._invalidate_preview)
        self.preview_button = QPushButton("Preview local project")
        self.preview_button.setToolTip(str(self.source_project))
        self.preview_button.clicked.connect(self.preview_import)
        self.import_button = QPushButton("Import reviewed snapshot")
        self.import_button.setObjectName("primary")
        self.import_button.setProperty("configurationPrimary", True)
        self.import_button.clicked.connect(self.import_snapshot)
        capture_layout.addWidget(QLabel("Capture as"))
        capture_layout.addWidget(self.import_name, 1)
        capture_layout.addWidget(self.preview_button)
        capture_layout.addWidget(self.import_button)
        root.addWidget(capture)
        self.source_label = QLabel(f"Local source: {self.source_project}")
        self.source_label.setWordWrap(True)
        self.source_label.setTextFormat(Qt.PlainText)
        root.addWidget(self.source_label)
        self.status = QLabel("Connect to browse snapshots.")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.status)
        self._progress = progress(self)
        root.addWidget(self._progress)
        polish_page(self)
        self._update_enabled()
        QApplication.instance().aboutToQuit.connect(self._finish_before_shutdown)

    def set_source_project(self, project: Path):
        if self._request is not None:
            self.status.setText("Finish the current request before changing the local source project.")
            return
        self.source_project = Path(project)
        self.source_label.setText(f"Local source: {self.source_project}")
        self.preview_button.setToolTip(str(self.source_project))
        self.import_name.setText(self.source_project.name)
        self._invalidate_preview()

    def _finish_before_shutdown(self):
        # Process teardown must not destroy a QThread while its HTTP transaction
        # is finishing. Ordinary navigation and close attempts never wait here.
        if self._request is not None:
            self._request.wait()

    def _update_enabled(self):
        busy, connected = self._request is not None, self.client is not None
        self._progress.setVisible(busy)
        for widget in (self.address, self.token, self.connect_button):
            widget.setEnabled(not busy)
        for widget in (self.projects, self.search, self.refresh_button, self.preview_button,
                       self.import_name, self.tabs):
            widget.setEnabled(connected and not busy)
        self.export_button.setEnabled(connected and not busy and self.projects.currentData() is not None)
        self.import_button.setEnabled(connected and not busy and self._preview is not None)
        self.previous.setEnabled(connected and not busy and self._offset > 0)
        self.next.setEnabled(connected and not busy and getattr(self, "_page_full", False))

    def _run(self, message, operation, complete):
        if self._request is not None:
            return
        self.status.setText(message)
        worker = _Request(operation, self)
        self._request = worker
        outcome = {}
        worker.ready.connect(lambda value: outcome.update(value=value))
        worker.failed.connect(lambda error: outcome.update(error=error))

        def finished():
            self._request = None
            worker.deleteLater()
            self._update_enabled()
            if "error" in outcome:
                self.status.setText(outcome["error"])
            else:
                try:
                    complete(outcome.get("value"))
                except Exception as error:
                    self.status.setText(f"Could not display configuration result: {error}")
            self._update_enabled()

        worker.finished.connect(finished)
        self._update_enabled()
        worker.start()

    def connect_service(self):
        try:
            candidate = ConfigurationClient(self.address.text(), self.token.text())
        except ValueError as error:
            self.status.setText(str(error))
            return
        self._invalidate_preview()
        selected = self.projects.currentData()

        def connected(rows):
            self.client = candidate
            self.projects.blockSignals(True)
            self.projects.clear()
            for row in rows:
                self.projects.addItem(f"{row['name']} · snapshot {row['generation']}", row)
            if self.selected_project_id is not None:
                self.projects.setCurrentIndex(next((i for i, row in enumerate(rows)
                    if row["id"] == self.selected_project_id), -1))
            elif selected:
                for index, row in enumerate(rows):
                    if row["id"] == selected["id"]:
                        self.projects.setCurrentIndex(index)
                        break
            self.projects.blockSignals(False)
            self._project_changed()

        self._run("Connecting to configuration service…",
                  lambda: candidate.request("/v1/projects"), connected)

    def select_project(self, project_id):
        self.selected_project_id = project_id
        index = next((i for i in range(self.projects.count())
                      if self.projects.itemData(i)["id"] == project_id), -1)
        if index != self.projects.currentIndex():
            self.projects.setCurrentIndex(index)

    def _project_changed(self):
        self.details.clear()
        for table in (self.table, self.tags, self.audit):
            table.setRowCount(0)
        self._search_changed()

    def _search_changed(self):
        self._offset, self._audit_after = 0, 0
        self._audit_cursors = {0: 0}
        self.details.clear()
        self.load_page()

    def _page(self, direction):
        self._offset = max(0, self._offset + direction * 200)
        self.load_page()

    @staticmethod
    def _fill(table, headers, rows):
        # ResizeToContents otherwise remeasures the visible page for every cell
        # insertion, turning a 200-row tag result into a minute-long native freeze.
        table.setUpdatesEnabled(False)
        table.blockSignals(True)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setRowCount(len(rows))
        for index, (values, data) in enumerate(rows):
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                if column == 0:
                    item.setData(Qt.UserRole, data)
                table.setItem(index, column, item)
        header = table.horizontalHeader()
        if len(headers) == 5:
            # A long description must not squeeze the address down to one word.
            header.resizeSection(0, max(280, min(520, int(table.viewport().width() * 0.45))))
            for column in (1, 2, 3):
                header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(4, QHeaderView.Stretch)
        else:
            header.setSectionResizeMode(QHeaderView.ResizeToContents)
            header.setSectionResizeMode(0, QHeaderView.Stretch)
        table.blockSignals(False)
        table.setUpdatesEnabled(True)

    def load_page(self):
        project = self.projects.currentData()
        if not project or not self.client:
            self.status.setText("Connected. Preview a local project to capture the first snapshot.")
            return
        tab, query, offset = self.tabs.currentIndex(), self.search.text(), self._offset
        endpoint = ("objects", "tags", "audit")[tab]
        params = {"q": query, "offset": offset, "limit": 200}
        if tab == 2:
            # Audit uses a durable sequence cursor, not assumed contiguous IDs.
            params = {"after": self._audit_cursors.get(offset, 0), "limit": 200}
        path = f"/v1/projects/{project['id']}/{endpoint}?{urlencode(params)}"

        def loaded(rows):
            self._page_full = len(rows) == 200
            if tab == 0:
                self._fill(self.table, ["Object", "Kind", "Revision", "SHA-256"], [
                    ([r["path"], r["kind"], r["revision"], r["digest"][:12]], r) for r in rows])
            elif tab == 1:
                self._fill(self.tags, ["Configured tag", "Kind", "Type", "Unit", "Description"], [
                    ([r["path"], r["data"]["kind"], r["data"]["data_type"], r["data"]["unit"],
                      r["data"]["description"]], r) for r in rows])
            else:
                self._audit_after = rows[-1]["id"] if rows else self._audit_after
                self._audit_cursors[offset + 200] = self._audit_after
                self._fill(self.audit, ["Action", "Actor", "Snapshot", "Time (UTC)"], [
                    ([r["action"], r["actor"], r["generation"], r["occurred"]], r) for r in rows])
            self.page_label.setText(f"Showing {len(rows)} {endpoint} · page {offset // 200 + 1}")
            self.status.setText("File-authoritative snapshot. Refresh and preview again after local edits.")

        self._run("Loading configured metadata…", lambda: self.client.request(path), loaded)

    def _object_selected(self):
        row = self.table.item(self.table.currentRow(), 0)
        project = self.projects.currentData()
        if row is None or project is None:
            return
        obj = row.data(Qt.UserRole)
        path = f"/v1/projects/{project['id']}/objects/{obj['id']}/revisions"
        self._run("Loading revision history…", lambda: self.client.request(path),
                  lambda rows: self._show_revisions(obj, rows))

    def _show_revisions(self, obj, rows):
        self.details.setPlainText(
                      f"{obj['path']}\nStable repository ID: {obj['id']}\n\n" + "\n".join(
                          f"Revision {r['number']} · {r['actor']} · {r['occurred']}\n"
                          f"SHA-256: {r['digest']}" for r in rows))
        self.status.setText("Revision history loaded. Source files remain authoritative.")

    def _tag_selected(self):
        item = self.tags.item(self.tags.currentRow(), 0)
        if item is None:
            return
        tag = item.data(Qt.UserRole)
        data = tag["data"]
        self.details.setPlainText(
            f"{tag['path']}\n{data['description']}\n\n"
            f"Kind: {data['kind']}    Type: {data['data_type']}    Unit: {data['unit']}\n"
            f"Configured value / schema default: {json.dumps(data.get('value'), ensure_ascii=False)}\n"
            f"Source: {data['source']}\n"
            "This is configuration metadata. Live values are available in Control Designer and Operator Live.")

    def _audit_selected(self):
        item = self.audit.item(self.audit.currentRow(), 0)
        if item is None:
            return
        row = item.data(Qt.UserRole)
        result = row["result"]
        self.details.setPlainText(
            f"Snapshot {row['generation']} imported by {row['actor']}\n{row['occurred']}\n\n"
            f"{result['files']} captured files · {result['tags']} module tags\n"
            f"{result['changed']} changed files · {result['removed']} removed files\n"
            f"Command: {row['command_id']}\nSource SHA-256: {result['digest']}")

    def _invalidate_preview(self):
        self._preview, self._pending_command = None, None
        self.import_button.setEnabled(False)

    def preview_import(self):
        self._invalidate_preview()
        name = self.import_name.text().strip()

        def preview():
            bundle = read_project(self.source_project)
            payload = {"name": name, "files": bundle["files"]}
            result = self.client.request("/v1/imports/preview", payload)
            return payload, result, bundle["excluded"]

        def prepared(value):
            payload, result, excluded = value
            self._preview = payload
            self._pending_command = {**payload, "expected_generation": result["expected_generation"],
                                     "command_id": str(uuid4())}
            lines = [f"Snapshot preview — {name}",
                     f"{result['files']} files · {result['tags']} module tags", ""]
            for title, key in (("Added", "added"), ("Changed", "updated"), ("Removed", "removed")):
                lines.extend([f"{title} ({len(result[key])})", *(result[key] or ["None"]), ""])
            lines.extend([f"Excluded runtime files ({len(excluded)})",
                          *(f"{item['path']} — {item['reason']}" for item in excluded), "",
                          "Index coverage", *result["warnings"], "", f"Source SHA-256: {result['digest']}"])
            self.details.setPlainText("\n".join(lines))
            self.status.setText(f"Preview: {result['files']} files, {result['tags']} module tags; "
                                f"{len(result['added'])} added, {len(result['updated'])} changed, "
                                f"{len(result['removed'])} removed. Import captures these reviewed bytes.")

        self._run("Reading and validating the selected local project…", preview, prepared)

    def import_snapshot(self):
        if not self._pending_command:
            return
        command = self._pending_command

        def imported(result):
            self._invalidate_preview()
            self.details.setPlainText(json.dumps(result, indent=2))
            from azeo_control_trainer.core.presentation.configuration_workspace import workspace_for
            host = workspace_for(self)
            if host:
                host.browser._selected = result["project_id"]
                self.selected_project_id = result["project_id"]
                host.browser.refresh()
            self.connect_service()

        # Retain the command ID on a timeout so a retry cannot double-commit.
        self._run("Committing the reviewed snapshot…",
                  lambda: self.client.request("/v1/imports", command), imported)

    def export_snapshot(self, destination: Path | None = None):
        project = self.projects.currentData()
        if project is None:
            return
        if destination is None:
            if is_headless():
                return
            selected = QFileDialog.getExistingDirectory(self, "Choose a parent for the new export folder")
            if not selected:
                return
            destination = Path(selected) / f"configuration-{project['id']}-snapshot-{project['generation']}"
        path = f"/v1/projects/{project['id']}/export"
        self._run("Exporting and verifying snapshot hashes…",
                  lambda: export_project(self.client.request(path), destination),
                  lambda result: self.status.setText(f"Verified snapshot exported to {result}"))

    def closeEvent(self, event):
        if self._request is not None:
            self.status.setText("Finishing the current request before closing…")
            event.ignore()
            return
        super().closeEvent(event)
