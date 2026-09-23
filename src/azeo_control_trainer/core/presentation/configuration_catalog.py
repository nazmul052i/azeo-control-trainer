"""The same captured-configuration browser and picker in every engineering app."""
from __future__ import annotations

import json
import copy
from os import PathLike
from html import escape
from datetime import datetime, timezone

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QDialog, QDialogButtonBox, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QSplitter, QTableView, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from ..configuration.catalog_client import CatalogSession, project_root
from .brand import UI
from .configuration_chrome import CONFIGURATION_QSS, icon, mark_for, polish_page, progress, state_color
from .configuration_chrome import ConfigurationComboBox as QComboBox, heading, style_button

_workers = set()


def _capture_time(value, *, compact=False):
    try:
        captured = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if captured.tzinfo is None:
            captured = captured.replace(tzinfo=timezone.utc)
        if compact:
            return captured.astimezone().strftime("%d %b %Y, %H:%M")
        minutes = max(0, int((datetime.now(timezone.utc) - captured).total_seconds() / 60))
        age = (f"{minutes // 1440}d {(minutes % 1440) // 60}h" if minutes >= 1440 else
               f"{minutes // 60}h {minutes % 60}m" if minutes >= 60 else f"{minutes}m")
        return captured.astimezone().strftime("%d %b %Y %H:%M %Z") + f" ({age} ago)"
    except (TypeError, ValueError):
        return str(value)


def _shutdown():
    # Workers belong to the application, not to a dialog which may close mid-request.
    for worker in tuple(_workers):
        worker.wait()


class _Request(QThread):
    result = Signal(object)
    failure = Signal(str)

    def __init__(self, session, selected):
        super().__init__(QApplication.instance())
        self.session, self.selected = session, selected

    def run(self):
        try:
            self.result.emit(self.session.load(self.selected, self.result.emit))
        except Exception as error:
            self.failure.emit(str(error))
        finally:
            self.session = None


class _Rows(QAbstractTableModel):
    def __init__(self, columns, parent=None):
        super().__init__(parent)
        self.columns, self.rows = columns, []

    def set_rows(self, rows):
        if rows == self.rows:
            return
        view = self.parent()
        current = view.currentIndex()
        key = self.columns[0][0]
        def identity(row):
            return row.get("target_id"), row.get("id", row.get(key))
        selected = identity(self.rows[current.row()]) if current.isValid() else None
        scroll = view.verticalScrollBar().value()
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()
        # Live evidence refreshes must not move the engineer away from their object.
        if selected is not None:
            for index, row in enumerate(rows):
                if identity(row) == selected:
                    view.selectRow(index)
                    break
        view.verticalScrollBar().setValue(scroll)

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        if role == Qt.DecorationRole and index.column() == 0:
            return icon(mark_for(str(row.get("kind", row.get("path", self.parent().accessibleName())))))
        if role == Qt.ForegroundRole and self.columns[index.column()][0] in {"state", "status", "origin"}:
            return state_color(row.get(self.columns[index.column()][0], ""))
        if role == Qt.DisplayRole:
            value = row.get(self.columns[index.column()][0], "")
            if value and self.columns[index.column()][0] in {"created", "occurred", "completed", "modified"}:
                try:
                    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone().strftime("%d %b %Y %H:%M")
                except ValueError:
                    pass
            return str(value) if value is not None else ""
        if role == Qt.ToolTipRole:
            value = row.get(self.columns[index.column()][0], "")
            return str(value) if value is not None else ""
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.columns[section][1]
        return None


def _table(columns, name):
    view = QTableView()
    view.setAccessibleName(name)
    view.setSelectionBehavior(QAbstractItemView.SelectRows)
    view.setSelectionMode(QAbstractItemView.SingleSelection)
    view.setEditTriggers(QAbstractItemView.NoEditTriggers)
    view.setAlternatingRowColors(True)
    view.setWordWrap(False)
    view.setShowGrid(False)
    view.verticalHeader().setDefaultSectionSize(30)
    view.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
    view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    view.verticalHeader().hide()
    model = _Rows(columns, view)
    view.setModel(model)
    view.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    view.horizontalHeader().setStretchLastSection(True)
    view.setColumnWidth(0, 310)
    for column, (key, _) in enumerate(columns):
        if key in {"created", "occurred", "completed", "modified"}:
            view.setColumnWidth(column, 160)
    return view, model


def context_root(widget=None, explicit=None):
    if explicit is not None:
        return project_root(explicit)
    while widget is not None:
        for attribute in ("_configuration_root", "root", "standards_root"):
            value = getattr(widget, attribute, None)
            if isinstance(value, (str, PathLike)):
                return project_root(value)
        widget = widget.parentWidget()
    return project_root()


class ConfigurationCatalogWidget(QWidget):
    tagChosen = Signal(dict)
    contextChanged = Signal(object)

    def __init__(self, root=None, parent=None, *, pick="", session=None, hosted=False):
        super().__init__(parent)
        self.session = session or CatalogSession(root)
        self.pick = pick
        self.hosted = hosted
        self.index = None
        self._request = None
        self._started = False
        self._offset = 0
        self._path = ""
        self._history = []
        self._selected = ""
        self._previews = []
        self._inspection_key = None
        self._inspection_worker = None
        self._inspection_timer = QTimer(self)
        self._inspection_timer.setSingleShot(True)
        self._inspection_timer.setInterval(180)
        self._inspection_timer.timeout.connect(self._load_object_history)
        self.setStyleSheet(CONFIGURATION_QSS)
        layout = QVBoxLayout(self)
        title = QLabel("Engineering catalog")
        title.setStyleSheet(f"font-size: 14pt; font-weight: 600; color: {UI.blue};")
        layout.addWidget(title)
        title.setVisible(not hosted)
        self.status = QLabel("Engineering configuration · running values are separate")
        self.status.setWordWrap(True)
        self.status.setAccessibleName("Catalog snapshot status")
        if hosted:
            layout.addWidget(heading(self, "Catalog", mark=None, status=self.status))
        else:
            layout.addWidget(self.status)
        row = QHBoxLayout()
        self.projects = QComboBox()
        self.projects.setAccessibleName("Captured project")
        self.projects.addItem("Select captured project…", "")
        self.projects.activated.connect(self._project_changed)
        row.addWidget(self.projects, 1)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        row.addWidget(self.refresh_button)
        self.editing_button = QPushButton("Shared editing…")
        self.editing_button.setEnabled(False)
        self.editing_button.clicked.connect(self.open_editing)
        row.addWidget(self.editing_button)
        self.releases_button = QPushButton("Release Manager…")
        self.releases_button.setEnabled(False)
        self.releases_button.clicked.connect(self.open_releases)
        row.addWidget(self.releases_button)
        self.libraries_button = QPushButton("Library Manager…")
        self.libraries_button.setEnabled(False)
        self.libraries_button.clicked.connect(self.open_libraries)
        row.addWidget(self.libraries_button)
        layout.addLayout(row)
        if hosted:
            for button in (self.editing_button, self.releases_button, self.libraries_button):
                button.hide()
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search tag, module, I/O, PVM or display…")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Engineering catalog search")
        self.search.textChanged.connect(self._search_changed)
        row.addWidget(self.search, 1)
        self.kind = QComboBox()
        for label, kinds in [("All configuration", ()), ("Tags", ("terminal", "parameter", "field")),
                             ("Modules", ("module",)), ("I/O", ("io",)),
                             ("PVMs and faceplates", ("class", "faceplate", "pvm", "detail", "class_configuration")),
                             ("Graphics", ("display", "pvm_instance"))]:
            self.kind.addItem(label, kinds)
        self.kind.currentIndexChanged.connect(self._search_changed)
        self.kind.setVisible(not pick)
        row.addWidget(self.kind)
        if hosted:
            row.addWidget(self.refresh_button)
        self.training_button = QPushButton("Training and recovery…")
        self.training_button.setEnabled(False)
        self.training_button.clicked.connect(self.open_training)
        row.addWidget(self.training_button)
        self.training_button.setVisible(not hosted and not pick)
        layout.addLayout(row)
        split = QSplitter(Qt.Horizontal)
        split.setHandleWidth(12)
        split.setChildrenCollapsible(False)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.results, self.result_model = _table(
            [("path", "Tag / object"), ("kind", "Kind"), ("data_type", "Type"),
             ("unit", "Units"), ("description", "Description")], "Catalog results")
        self.results.selectionModel().currentRowChanged.connect(self._inspect_selection)
        for column, width in ((1, 100), (2, 85), (3, 65)):
            self.results.setColumnWidth(column, width)
        self.results.setColumnWidth(0, 280)
        self.results.doubleClicked.connect(lambda _: self.choose() if self.pick else self.inspect_loop())
        left_layout.addWidget(self.results, 1)
        paging = QHBoxLayout()
        self.previous = QPushButton("Previous")
        self.next = QPushButton("Next")
        self.previous.clicked.connect(lambda: self._page(-200))
        self.next.clicked.connect(lambda: self._page(200))
        self.count = QLabel()
        paging.addWidget(self.previous)
        paging.addWidget(self.count, 1)
        paging.addWidget(self.next)
        left_layout.addLayout(paging)
        split.addWidget(left)
        right = QFrame(objectName="configurationInspector")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 12, 12, 12)
        self.heading = QLabel("Select an object", objectName="configurationObjectTitle")
        self.heading.setWordWrap(True)
        self.heading.setTextFormat(Qt.PlainText)
        right_layout.addWidget(self.heading)
        self.object_kind = QLabel("Configuration and references", objectName="configurationSubtitle")
        right_layout.addWidget(self.object_kind)
        actions = QHBoxLayout()
        self.back = QPushButton("Back")
        self.back.clicked.connect(self.go_back)
        actions.addWidget(self.back)
        self.loop_button = QPushButton("Inspect loop")
        self.loop_button.clicked.connect(self.inspect_loop)
        actions.addWidget(self.loop_button)
        self.preview_button = QPushButton("Preview")
        self.preview_button.setEnabled(False)
        self.preview_button.clicked.connect(self.preview)
        actions.addWidget(self.preview_button)
        copy = QPushButton("Copy path")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self._path))
        actions.addWidget(copy)
        actions.setSpacing(2)
        actions.addStretch()
        for button, mark in ((self.back, "undo"), (self.loop_button, "module_props"),
                             (self.preview_button, "show"), (copy, "copy")):
            style_button(button, mark, quiet=True)
        right_layout.addLayout(actions)
        self.tabs = QTabWidget()
        self.overview = QTextBrowser(objectName="configurationProperties")
        self.tabs.addTab(self.overview, "Properties")
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.parameters, self.parameter_model = _table(
            [("path", "Parameter"), ("value", "Captured value"), ("unit", "Units")], "Loop parameters")
        self.parameters.doubleClicked.connect(lambda i: self.inspect(self.parameter_model.rows[i.row()]["path"]))
        self.tabs.addTab(self.parameters, "Parameters")
        self.references, self.reference_model = _table(
            [("source", "Used by / source"), ("kind", "Relationship"), ("target", "Target"),
             ("status", "Resolution"), ("location", "Location")], "Configuration references")
        self.references.doubleClicked.connect(self._follow_reference)
        references_page = QWidget()
        references_layout = QVBoxLayout(references_page)
        self.where_used = QCheckBox("Where used only")
        self.where_used.toggled.connect(lambda _: self.inspect(self._path, remember=False))
        references_layout.addWidget(self.where_used)
        references_layout.addWidget(QLabel("Double-click Source or Target to follow that reference."))
        references_layout.addWidget(self.references, 1)
        self.tabs.addTab(references_page, "References")
        self.coverage = QPlainTextEdit()
        self.coverage.setReadOnly(True)
        self.tabs.addTab(self.coverage, "Coverage")
        self.tabs.addTab(self.details, "Metadata")
        self.object_history = QPlainTextEdit()
        self.object_history.setReadOnly(True)
        self.tabs.addTab(self.object_history, "History")
        self.revisions, self.revision_model = _table(
            [("number", "Revision"), ("generation", "Generation"), ("actor", "Engineer"),
             ("occurred", "Checked in"), ("reason", "Reason"), ("path", "Path at revision")], "Object revisions")
        for column, width in enumerate((65, 85, 120, 175, 240, 260)):
            self.revisions.setColumnWidth(column, width)
        self.revisions.doubleClicked.connect(self.show_revision)
        self.tabs.addTab(self.revisions, "Revisions")
        self.tabs.setUsesScrollButtons(True)
        right_layout.addWidget(self.tabs, 1)
        split.addWidget(right)
        split.setSizes([620, 500])
        layout.addWidget(split, 1)
        self._progress = progress(self)
        layout.addWidget(self._progress)
        self.timer = QTimer(self)
        self.timer.setInterval(30000)
        self.timer.timeout.connect(self.refresh)
        app = QApplication.instance()
        if not getattr(app, "_configuration_catalog_shutdown", False):
            app.aboutToQuit.connect(_shutdown)
            app._configuration_catalog_shutdown = True
        polish_page(self)

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self.timer.start()
        if not self._started:
            self._started = True
            self.refresh()

    def hideEvent(self, event):  # noqa: N802
        self.timer.stop()
        super().hideEvent(event)

    def _project_changed(self, index):
        if self._request is not None:
            return
        self._selected = self.projects.itemData(index) or ""
        self.index = None
        self._path = ""
        self._history.clear()
        self._search_changed()
        self.contextChanged.emit({"index": None, "state": "Loading selected project"})
        self.refresh()

    def open_editing(self):
        if self.index is None:
            return
        from .configuration_workspace import workspace_for
        host = workspace_for(self)
        if host:
            return host.open_page("changes")
        from .configuration_editing import EditingLauncher
        dialog = EditingLauncher(self.index.project, self, profile=self.session.profile)
        self._previews.append(dialog)
        dialog.show()

    def open_releases(self):
        if self.index is None:
            return
        from .configuration_workspace import workspace_for
        host = workspace_for(self)
        if host:
            return host.open_page("releases")
        from .configuration_releases import ReleaseManager
        dialog = ReleaseManager(self.index.project, self, profile=self.session.profile)
        self._previews.append(dialog)
        dialog.show()

    def open_libraries(self):
        if self.index is None:
            return
        from .configuration_workspace import workspace_for
        host = workspace_for(self)
        if host:
            return host.open_page("libraries")
        from .configuration_libraries import LibraryManager
        dialog = LibraryManager(self.index.project, self, profile=self.session.profile)
        self._previews.append(dialog)
        dialog.show()

    def open_training(self):
        if self.index is None:
            return
        from .configuration_workspace import workspace_for
        host = workspace_for(self)
        if host:
            return host.open_page("training")
        from .configuration_training import TrainingManager
        dialog = TrainingManager(self.index.project, self, profile=self.session.profile)
        self._previews.append(dialog)
        dialog.show()
        return dialog

    def refresh(self):
        if self._request is not None:
            return
        if self.index is None:
            self.status.setText("Loading captured configuration in the background…")
        self.refresh_button.setEnabled(False)
        self.projects.setEnabled(False)
        self._progress.show()
        worker = _Request(self.session, self._selected)
        self._request = worker
        _workers.add(worker)
        worker.result.connect(self._loaded)
        worker.failure.connect(self._failed)
        worker.finished.connect(self._finished)
        def release():
            _workers.discard(worker)
            worker.deleteLater()
        worker.finished.connect(release)
        worker.start()

    def _finished(self):
        self._request = None
        self._progress.hide()
        self.refresh_button.setEnabled(True)
        self.projects.setEnabled(True)
        if self.hosted:
            from .configuration_workspace import workspace_for
            host = workspace_for(self)
            if host:
                host._update_busy()

    def _failed(self, message):
        self.index = None
        self.editing_button.setEnabled(False)
        self.releases_button.setEnabled(False)
        self.libraries_button.setEnabled(False)
        self.training_button.setEnabled(False)
        self.status.setText(message)
        self.result_model.set_rows([])
        self.parameter_model.set_rows([])
        self.reference_model.set_rows([])
        self.details.clear()
        self.overview.clear()
        self.coverage.clear()
        self.heading.setText("Catalog unavailable")
        self._path = ""
        self._inspection_key = None
        self.object_history.clear()
        self.revision_model.set_rows([])
        self.contextChanged.emit({"index": None, "state": message, "failed": True})

    def _loaded(self, result):
        self.index = result["index"]
        self.editing_button.setEnabled(self.index is not None)
        self.releases_button.setEnabled(self.index is not None and self.index.project.get("mode") == "repository")
        self.libraries_button.setEnabled(self.index is not None and self.index.project.get("mode") == "repository")
        self.training_button.setEnabled(self.index is not None)
        self._selected = result["selected"]
        choices = result["projects"]
        if choices:
            self.projects.blockSignals(True)
            self.projects.clear()
            self.projects.addItem("Select captured project…", "")
            for project in choices:
                self.projects.addItem(project["name"], project["id"])
            self.projects.setCurrentIndex(max(0, self.projects.findData(self._selected)))
            self.projects.blockSignals(False)
        if self.index:
            p = self.index.project
            repository = p.get("mode") == "repository"
            label, verb = ("revision set", "Checked in") if repository else ("snapshot", "Captured")
            self.status.setText(f"{result['state']} · {p['name']} · {label} {p['generation']}\n"
                                f"{verb} {_capture_time(p['modified'])} · {result['local']} · running revision unknown")
            self.status.setToolTip(self.status.text())
            if self.hosted:
                prefix = "" if result["state"].startswith("Connected") else result["state"] + " · "
                local = "Local files differ" if result["local"].startswith("STALE") else result["local"]
                self.status.setText(f"{prefix}{verb} {_capture_time(p['modified'], compact=True)} · {local}")
            self.coverage.setPlainText(self.index.catalog["coverage"] + "\n\n" + "\n".join(
                f"{e['status']}: {e['source']} → {e['target']} ({e['location']})"
                for e in self.index.catalog["edges"] if e["status"] != "resolved") + "\n\n" + "\n".join(
                f"{i['source']} {i['location']}: {i['message']}" for i in self.index.catalog["issues"]))
        else:
            self.status.setText(result["state"])
        self._show_results()
        if self._path:
            self.inspect(self._path, remember=False)
        self.contextChanged.emit(result)

    def _search_changed(self, *_):
        self._offset = 0
        self._show_results()

    def _page(self, amount):
        self._offset = max(0, self._offset + amount)
        self._show_results()

    def _show_results(self):
        kinds = ("io", "field") if self.pick == "field" else (
            ("terminal", "parameter") if self.pick else self.kind.currentData())
        rows, total = self.index.search(self.search.text(), kinds=kinds, offset=self._offset) \
            if self.index else ([], 0)
        self.result_model.set_rows(rows)
        self.count.setText(f"{self._offset + 1 if total else 0}–{self._offset + len(rows)} of {total:,}")
        self.previous.setEnabled(self._offset > 0)
        self.next.setEnabled(self._offset + len(rows) < total)

    def _inspect_selection(self, current, _previous):
        if current.isValid():
            self.inspect(self.result_model.rows[current.row()]["path"])

    def inspect(self, path, *, remember=True):
        if not self.index:
            return
        if remember and self._path and self._path != path:
            self._history.append(self._path)
        self._path = path
        data = self.index.entries.get(path)
        self.heading.setText(path if data else path + " — unresolved in captured configuration")
        self.object_kind.setText(str(data.get("kind", "Configuration")).replace("_", " ").title() if data else "Unresolved reference")
        self.details.setPlainText(json.dumps(data, indent=2, ensure_ascii=False) if data else "No target in this snapshot.")
        if data:
            fields = [("Name", data.get("name", path)), ("Kind", data.get("kind")),
                      ("Description", data.get("description")), ("Module", data.get("module")),
                      ("Block type", data.get("block_type")), ("Data type", data.get("data_type")),
                      ("Units", data.get("unit")), ("Engineering range", data.get("eu_range")),
                      ("Captured value", data.get("value")), ("Field tag", data.get("io_tag")),
                      ("Direction", data.get("direction")), ("Source file", data.get("source")),
                      ("Object revision", data.get("object_revision")),
                      ("Running revision", "Unknown")]
            self.overview.setHtml("<table width='100%' cellspacing='0' cellpadding='7'>" + "".join(
                f"<tr><td width='34%' valign='top' style='color:{UI.text_secondary}'>{escape(label)}</td>"
                f"<td valign='top'>{escape(str(value))}</td></tr>"
                for label, value in fields if value is not None and value != "") + "</table>"
                f"<p style='color:{UI.text_muted}'>Captured engineering configuration. "
                "Running values are available in Live / open modules. Stable object and point identifiers are under Metadata.</p>")
        else:
            self.overview.setPlainText("This target is absent from the captured catalog. "
                                       "References identifies its consumers; Coverage lists unresolved and dynamic dependencies.")
        parameters = [t for t in self.index.bundle["tags"] if t["path"].startswith(path + "/")
                      and t["kind"] in {"parameter", "terminal", "field"}]
        self.parameter_model.set_rows(parameters)
        refs = self.index.references(path, incoming=self.where_used.isChecked())
        self.reference_model.set_rows(refs)
        self.tabs.setTabToolTip(1, f"{len(parameters)} captured parameters")
        self.tabs.setTabToolTip(2, f"{len(refs)} used-by and other references")
        key = (str(self.index.project["id"]), str((data or {}).get("object_id", "")),
               (data or {}).get("object_revision"))
        if key != self._inspection_key:
            self._inspection_key = key
            self.revision_model.set_rows([])
            self.object_history.setPlainText("Loading revision evidence…" if key[1] else "Installed class: versioned with the application build.")
            self._inspection_timer.start()
        self.back.setEnabled(bool(self._history))
        self.loop_button.setEnabled(self.index.loop(path) != path)
        self.preview_button.setEnabled(bool(data and (data.get("kind") in {"display", "pvm_instance"}
                                                      or data.get("origin") == "installed")))

    def _load_object_history(self):
        key = self._inspection_key
        if not key or not key[1] or self._inspection_worker is not None:
            return
        from .configuration_editing import _Command
        def load():
            rows = self.session.client.request(f"/v1/projects/{key[0]}/objects/{key[1]}/revisions?limit=500")
            required = {"number", "occurred", "actor", "path"}
            if not isinstance(rows, list) or any(not isinstance(row, dict) or required - row.keys() for row in rows):
                raise ValueError("Service returned invalid revision evidence")
            return rows
        worker = _Command(load)
        self._inspection_worker = worker
        _workers.add(worker)
        def ready(rows):
            if key == self._inspection_key:
                self.revision_model.set_rows(rows)
                self.object_history.setPlainText("Latest 500 revisions; double-click a revision to inspect its immutable document.\n\n" + "\n\n".join(
                    f"r{row['number']} · {row['occurred']} · {row['actor']}\n{row.get('reason') or row.get('action', '')}\n{row['path']}" for row in rows))
        def failed(error):
            if key == self._inspection_key:
                self.object_history.setPlainText("Revision history unavailable: " + str(error))
        def finished():
            self._inspection_worker = None
            _workers.discard(worker)
            worker.deleteLater()
            if key != self._inspection_key:
                self._inspection_timer.start()
        worker.ready.connect(ready)
        worker.failed.connect(failed)
        worker.finished.connect(finished)
        worker.start()

    def show_revision(self, index):
        if not self._inspection_key or not index.isValid():
            return
        from .configuration_editing import BackgroundDialog
        project, identity, _ = self._inspection_key
        number = self.revision_model.rows[index.row()]["number"]
        dialog = BackgroundDialog(self)
        dialog.setWindowTitle(f"Immutable object revision {number}")
        dialog.resize(860, 640)
        layout = QVBoxLayout(dialog)
        dialog.status = QLabel("Loading recorded document…")
        layout.addWidget(dialog.status)
        body = QPlainTextEdit()
        body.setReadOnly(True)
        layout.addWidget(body)
        def ready(row):
            dialog.status.setText(f"r{row['number']} · {row['path']} · {row['digest']}")
            body.setPlainText(json.dumps(row.get("document"), ensure_ascii=False, indent=2))
        dialog.run(lambda: self.session.client.request(f"/v1/projects/{project}/objects/{identity}?revision={number}"), ready)
        self._previews.append(dialog)
        dialog.show()

    def preview(self):
        if not self.index:
            return None
        row = self.index.entries.get(self._path, {})
        document = row.get("document") if row.get("kind") == "display" else None
        if row.get("kind") == "pvm_instance":
            document = self.index.entries.get(row.get("display"), {}).get("document")
        faceplate = row.get("role") in {"faceplate", "detail"}
        if document is None and row.get("origin") == "installed":
            block = next((self.index.entries[p] for p in reversed(self._history)
                          if p in self.index.entries and self.index.entries[p].get("block_type") == row["block_type"]
                          and self.index.entries[p].get("block_id")), None)
            params = {key: (block["path"] if block else "") for key in row.get("parameters", ["path"])}
            document = {"display": row["name"], "pvms": [{"id": "catalog_preview",
                        "class": f"{row['block_type']}/{row['role']}", "variant": row.get("variant", ""),
                        "params": params, "x": 0, "y": 0}]}
        if document is None:
            return None
        if any(item.get("class_revision") for item in document.get("items", []) + document.get("pvms", [])):
            return self._pinned_preview(copy.deepcopy(document))
        from ..hmi.pvms.rendering.viewer import PvmDisplayView
        dialog = QDialog(self, Qt.Window)
        dialog.setWindowTitle("Configuration preview — " + document.get("display", ""))
        layout = QVBoxLayout(dialog)
        label = QLabel("Captured layout · values are not live · interactions disabled")
        label.setWordWrap(True)
        layout.addWidget(label)
        try:
            if faceplate:
                from ..hmi.binding import BindingEngine, LiveGraphSource
                from ..hmi.pvms import registry
                from ..hmi.pvms.render import PvmFaceplateWidget
                cls = registry.get(row["block_type"], row["role"], row.get("variant", ""))
                view = PvmFaceplateWidget(cls, params, BindingEngine(LiveGraphSource(lambda: {})),
                                          parent=dialog, live=False)
                view.setWindowFlags(Qt.Widget)
                view.context_title.hide()
                view.set_available_actions(set())
                view.setAttribute(Qt.WA_TransparentForMouseEvents, True)
                dialog.faceplate = view
            else:
                view = PvmDisplayView(copy.deepcopy(document), lambda: {}, live=False, parent=dialog)
                view.setInteractive(False)
        except Exception as error:
            dialog.deleteLater()
            self.status.setText(f"Cannot preview this captured layout: {error}")
            return None
        layout.addWidget(view, 0 if faceplate else 1, Qt.AlignHCenter if faceplate else Qt.Alignment())
        dialog.viewer = view
        dialog.finished.connect(view.close)
        if faceplate:
            label.setMaximumWidth(view.sizeHint().width())
            layout.setAlignment(Qt.AlignTop)
            dialog.adjustSize()
        else:
            dialog.resize(1000, 650)
        self._previews.append(dialog)
        dialog.show()
        if not faceplate:
            view.fit_display()
        return dialog

    def _pinned_preview(self, document):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from .configuration_editing import BackgroundDialog
        from ..configuration.documents import export_project
        from ..hmi.pvms.rendering.viewer import PvmDisplayView
        project, generation = self.index.project["id"], self.index.project["generation"]
        dialog = BackgroundDialog(self)
        dialog.setWindowTitle("Captured display and retained classes — " + document.get("display", ""))
        dialog.resize(1000, 650)
        layout = QVBoxLayout(dialog)
        dialog.status = QLabel("Loading the captured generation and its retained class files…")
        layout.addWidget(dialog.status)
        dialog.files = TemporaryDirectory(prefix="azeo-catalog-preview-")
        def load():
            bundle = self.session.client.request(f"/v1/projects/{project}/export?generation={generation}")
            return export_project(bundle, Path(dialog.files.name) / "project")
        def ready(root):
            try:
                viewer = PvmDisplayView(document, lambda: {}, live=False, config_root=root / "displays/pvm", parent=dialog)
                viewer.setInteractive(False)
                dialog.viewer = viewer
                layout.addWidget(viewer)
                dialog.finished.connect(viewer.close)
                dialog.status.setText("Captured generation and retained class versions · values are not live · interactions disabled")
                viewer.fit_display()
            except Exception as error:
                dialog.status.setText("Cannot preview this captured display: " + str(error))
        self._previews.append(dialog)
        dialog.run(load, ready)
        dialog.show()
        return dialog

    def _follow_reference(self, index):
        ref = self.reference_model.rows[index.row()]
        target = ref["source"] if index.column() == 0 else ref["target"]
        self.inspect(target)

    def go_back(self):
        if self._history:
            self.inspect(self._history.pop(), remember=False)

    def inspect_loop(self):
        if self.index and self._path:
            self.inspect(self.index.loop(self._path))

    def choose(self):
        if self.index and self._path:
            row = self.index.entries.get(self._path, {})
            if (self.pick == "field" and row.get("io_tag")) or (
                    self.pick == "tag" and row.get("kind") in {"terminal", "parameter"}):
                self.tagChosen.emit(row)


class ConfigurationCatalogDialog(QDialog):
    def __init__(self, root=None, parent=None, *, pick="", session=None, initial_page="catalog", draft=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("Engineering Catalog — Azeo" if pick else "Configuration — Azeo")
        self.setWindowIcon(icon("datalog"))
        # This dialog contains the complete cross-product authoring shell
        # (menu bar, navigation rail and workspace) instead of the compact
        # Graphics Designer workflow header.
        self.setProperty("authoringDialog", True)
        self.setProperty("embeddedAuthoringShell", True)
        available = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1340, available.width() - 40), min(820, available.height() - 60))
        self.setStyleSheet(CONFIGURATION_QSS)
        self.selected_tag = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.workspace = None
        if pick:
            self.browser = ConfigurationCatalogWidget(root, self, pick=pick, session=session)
            layout.addWidget(self.browser)
        else:
            from .configuration_workspace import ConfigurationWorkspace
            self.workspace = ConfigurationWorkspace(root, self, session=session, initial_page=initial_page, draft=draft)
            self.browser = self.workspace.browser
            layout.addWidget(self.workspace)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        if pick:
            select = buttons.addButton("Use selected tag", QDialogButtonBox.AcceptRole)
            select.clicked.connect(self.browser.choose)
            self.browser.tagChosen.connect(self._chosen)
        if pick:
            layout.addWidget(buttons)
        else:
            buttons.hide()

    @property
    def source_project(self):
        return self.workspace.source_project if self.workspace else self.browser.session.root

    def set_source_project(self, root):
        if self.workspace:
            self.workspace.set_source_project(root)

    def closeEvent(self, event):  # noqa: N802
        if self.workspace and not self.workspace.close_pages():
            event.ignore()
            return
        self._closing = True
        try:
            super().closeEvent(event)
        finally:
            self._closing = False

    def reject(self):
        if not getattr(self, "_closing", False) and self.workspace and not self.workspace.close_pages():
            return
        super().reject()

    def _chosen(self, tag):
        self.selected_tag = tag
        self.accept()
