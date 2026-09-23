"""Shared check-in and recovery UI; editors continue using their existing models."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
import weakref

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QPlainTextEdit, QTabWidget, QVBoxLayout,
)

from ..configuration.client import ConfigurationClient, read_profile
from ..configuration.documents import ConfigurationError
from ..configuration.workspace import DraftWorkspace
from .configuration_catalog import _table, _workers, _shutdown
from .headless import is_headless
from .configuration_chrome import CONFIGURATION_QSS, command_bar, heading, polish_page, progress
from .configuration_chrome import ConfigurationButton as QPushButton, ConfigurationToolButton


class _Command(QThread):
    ready = Signal(object)
    failed = Signal(object)

    def __init__(self, operation):
        super().__init__(QApplication.instance())
        self.operation = operation

    def run(self):
        try:
            self.ready.emit(self.operation())
        except Exception as error:
            self.failed.emit(error)
        finally:
            self.operation = None


class BackgroundDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window)
        self.worker = None
        self._dialogs = []
        self.setStyleSheet(CONFIGURATION_QSS)
        self._chrome_ready = False
        self._progress = progress(self)
        app = QApplication.instance()
        if not getattr(app, "_configuration_catalog_shutdown", False):
            app.aboutToQuit.connect(_shutdown)
            app._configuration_catalog_shutdown = True

    def showEvent(self, event):  # noqa: N802
        if not self._chrome_ready and self.layout():
            self._chrome_ready = True
            polish_page(self)
            title = self.property("configurationPageTitle") or self.windowTitle().split(" — ")[0]
            self.layout().removeWidget(self.status)
            self.layout().insertWidget(0, heading(self, title, mark=None, status=self.status))
            self.layout().addWidget(self._progress)
        super().showEvent(event)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key_Escape and self.property("configurationEmbedded"):
            event.accept()
            return
        super().keyPressEvent(event)

    def run(self, operation, completed):
        if self.worker is not None:
            return
        worker = _Command(operation)
        self.worker = worker
        self._progress.show()
        self.status.setProperty("configurationState", "working")
        _workers.add(worker)
        self._busy_actions = []
        if self.property("configurationEmbedded"):
            # Keep the catalog and tables readable and scrollable during refresh.
            # Review dialogs still lock their form while a reviewed command runs.
            for button in self.findChildren(QPushButton) + self.findChildren(ConfigurationToolButton):
                owner = button.parentWidget()
                while owner is not None and owner is not self and not isinstance(owner, QDialog):
                    owner = owner.parentWidget()
                if owner is self:
                    button.set_request_busy(True)
                    self._busy_actions.append(button)
        else:
            self.setEnabled(False)
        worker.ready.connect(completed)
        worker.failed.connect(self.failed)
        worker.finished.connect(self._finished)
        def release():
            _workers.discard(worker)
            worker.deleteLater()
        worker.finished.connect(release)
        worker.start()

    def _finished(self):
        self.worker = None
        self._progress.hide()
        self.setEnabled(True)
        for button in self._busy_actions:
            button.set_request_busy(False)
        self._busy_actions.clear()

    def reject(self):
        if self.worker is None:
            super().reject()

    def closeEvent(self, event):  # noqa: N802
        if self.worker is not None:
            event.ignore()
            return
        super().closeEvent(event)

    def failed(self, error):
        self.status.setText(str(error))
        self.status.setProperty("configurationState", "error")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)


def launch_workspace(directory):
    import os
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[3])
    subprocess.Popen([sys.executable, "-m", "azeo_control_trainer.azeo_explorer.configuration_workspace",
                      str(directory)], env=env,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


class EditingLauncher(BackgroundDialog):
    def __init__(self, project, parent=None, *, profile=None):
        super().__init__(parent)
        from ...config.paths import data_dir
        self.project = project
        self.profile = profile or read_profile()
        self.client = ConfigurationClient(**self.profile)
        self.directory = data_dir() / "configuration/workspaces"
        self.setWindowTitle("Shared engineering — " + project["name"])
        self.resize(760, 470)
        layout = QVBoxLayout(self)
        self.status = QLabel("Each session has a recoverable working draft. Check-in creates the shared revision.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.table, self.model = _table([("name", "Recoverable draft"), ("session", "Session")], "Working drafts")
        layout.addWidget(self.table)
        row = QHBoxLayout()
        self.create_pilot = QPushButton("Create isolated editing pilot…")
        self.create_pilot.clicked.connect(self.fork)
        self.create_pilot.setVisible(project["mode"] != "repository")
        self.new_session = QPushButton("Start new editing session")
        self.new_session.setProperty("configurationPrimary", True)
        self.new_session.setEnabled(project["mode"] == "repository")
        self.new_session.clicked.connect(self.create)
        self.resume = QPushButton("Resume selected draft")
        self.resume.setEnabled(False)
        self.resume.clicked.connect(self.resume_selected)
        self.table.selectionModel().currentRowChanged.connect(lambda current, previous: self.resume.setEnabled(current.isValid()))
        row.addWidget(self.create_pilot)
        row.addWidget(self.new_session)
        row.addWidget(self.resume)
        layout.addLayout(row)
        QTimer.singleShot(0, self.load_sessions)

    def load_sessions(self):
        def read():
            rows = []
            if self.directory.exists():
                for path in self.directory.glob("*/workspace.json"):
                    try:
                        workspace = DraftWorkspace(path.parent, profile=self.profile)
                        if workspace.project == str(self.project["id"]):
                            rows.append({"name": workspace.meta["name"], "session": workspace.session[:8],
                                         "directory": str(path.parent)})
                    except (ConfigurationError, OSError, ValueError):
                        continue
            return rows
        self.run(read, self.model.set_rows)

    def fork(self, name=None):
        if isinstance(name, bool):
            name = None
        if name is None:
            if is_headless():
                return
            name, accepted = QInputDialog.getText(self, "Editing pilot", "New repository project name:",
                                                 text=self.project["name"] + " — Editing")
            if not accepted:
                return
        def create():
            receipt = self.client.request(f"/v1/projects/{self.project['id']}/editing/fork",
                                          {"name": name, "command_id": str(uuid4())})
            return next(p for p in self.client.request("/v1/projects") if p["id"] == receipt["project_id"])
        def ready(project):
            self.project = project
            self.create_pilot.hide()
            self.new_session.setEnabled(True)
            self.status.setText(f"{project['name']} is ready. Start a session to edit its working draft.")
            from .configuration_workspace import workspace_for
            host = workspace_for(self)
            if host:
                host.browser._selected = project["id"]
                host._initial = "changes"
                host.browser.refresh()
        self.run(create, ready)

    def create(self):
        destination = self.directory / str(uuid4())
        self.run(lambda: DraftWorkspace.create(destination, str(self.project["id"]), profile=self.profile),
                 lambda workspace: launch_workspace(workspace.directory))

    def resume_selected(self):
        selected = self.table.currentIndex()
        if selected.isValid():
            launch_workspace(self.model.rows[selected.row()]["directory"])


def comparison_rows(comparison):
    def flatten(content):
        if content is None:
            return {"Document": "Absent"}
        raw = base64.b64decode(content)
        try:
            value = json.loads(raw.decode("utf-8-sig"))
        except (ValueError, UnicodeError):
            import hashlib
            return {"Content SHA-256": hashlib.sha256(raw).hexdigest()}
        result = {}
        def walk(item, path):
            if isinstance(item, dict) and item:
                for key, child in item.items():
                    walk(child, f"{path}/{key}".strip("/"))
            elif isinstance(item, list) and item:
                for i, child in enumerate(item):
                    walk(child, f"{path}/{i}")
            else:
                result[path] = json.dumps(item, ensure_ascii=False)
        walk(value, "")
        return result
    base, draft, current = (flatten(comparison[k]) for k in ("base", "draft", "current"))
    return [{"field": path, "base": base.get(path, "Absent"), "draft": draft.get(path, "Absent"),
             "current": current.get(path, "Absent")} for path in sorted(base.keys() | draft.keys() | current.keys())
            if base.get(path) != draft.get(path) or base.get(path) != current.get(path)]


class EngineeringChangesDialog(BackgroundDialog):
    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.workspace = workspace
        self.state = None
        self.editors = []
        self._closed_editors = []
        self.setWindowTitle("Shared changes — " + workspace.meta["name"])
        self.resize(1190, 740)
        layout = QVBoxLayout(self)
        self.status = QLabel("Loading revision and edit ownership information…")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        explanation = QLabel("Save preserves the working draft. Check-in commits engineering configuration. Running control is separate.")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a module, graphic or configuration file…")
        self.search.textChanged.connect(self.populate)
        row.addWidget(self.search, 1)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        row.addWidget(refresh)
        layout.addLayout(row)
        self.table, self.model = _table([("path", "Document"), ("kind", "Kind"), ("base", "Draft base"),
                                        ("revision", "Committed"), ("state", "Changes"), ("owner", "Editing")], "Engineering changes")
        self.table.doubleClicked.connect(lambda _: self.open_selected())
        layout.addWidget(self.table, 1)
        self.commands = command_bar(self, [
            ("Open in Studio", self.open_selected, "open", False),
            ("Save drafts", self.save_editors, "save", False),
            ("Review and check in…", self.review, "compare", True),
            ("Compare / resolve…", self.compare, "compare", False),
        ], more=[("Rename module…", self.rename, "exec_edit"),
                 ("Release reservations", self.release, "disconnect"),
                 ("Retry interrupted check-in", self.retry, "restore"),
                 ("Release Manager…", self.open_releases, "download")])
        layout.insertWidget(2, self.commands)
        self.table.selectionModel().currentRowChanged.connect(self._selection_changed)
        self._selection_changed()
        self.timer = QTimer(self)
        self.timer.setInterval(20000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        QTimer.singleShot(0, self.refresh)

    def selected(self):
        index = self.table.currentIndex()
        return self.model.rows[index.row()] if index.isValid() else None

    def open_releases(self):
        from .configuration_workspace import workspace_for
        host = workspace_for(self)
        if host:
            return host.open_page("releases")
        from .configuration_releases import ReleaseManager
        if self.state is not None:
            dialog = ReleaseManager(self.state["project"], self, profile=self.workspace.profile)
            self._dialogs.append(dialog)
            dialog.show()

    def refresh(self):
        self.run(lambda: (self.workspace.heartbeat(), self.workspace.changes()), self.loaded)

    def loaded(self, result):
        self.state, self.edits = result
        self.populate()
        self.status.setText(f"{len(self.edits)} saved draft changes · revision set {self.state['project']['generation']} · {self.state['identity']}")
        self.status.setToolTip("Reservations renew while connected and expire after 90 seconds. Save keeps private work; check-in creates a shared revision.")
        self._selection_changed()

    def _selection_changed(self, *_):
        selected = self.selected() is not None
        self.commands.buttons["Open in Studio"].setEnabled(selected)
        self.commands.buttons["Compare / resolve…"].setEnabled(selected)

    def populate(self, *_):
        if self.state is None:
            return
        bases = {r["path"]: r for r in self.workspace.base["objects"]}
        remote = {r["path"]: r for r in self.state["objects"]}
        owners = {r["path"]: r["actor"] + (" (this session)" if str(r["session"]) == self.workspace.session else "")
                  for r in self.state["leases"]}
        dirty = {e["path"] for e in self.edits}
        words = self.search.text().casefold().split()
        rows = []
        for path in sorted(bases.keys() | remote.keys() | dirty):
            if any(part in {"revisions", "versions"} for part in path.split("/")) or not all(w in path.casefold() for w in words):
                continue
            old, now = bases.get(path, {}), remote.get(path, {})
            newer = old.get("revision") != now.get("revision")
            state = "Conflict: compare" if newer and path in dirty else "New committed revision" if newer else (
                "Local draft" if path in dirty else "In sync")
            rows.append({**(now or old), "path": path, "base": old.get("revision", "—"),
                         "revision": now.get("revision", "Removed"), "state": state, "owner": owners.get(path, "Available")})
        self.model.set_rows(rows)

    def failed(self, error):
        super().failed(error)
        for editor in self.editors:
            editor.setEnabled(True)

    def open_selected(self):
        row = self.selected()
        if row:
            self.run(lambda: self.workspace.reserve([row["path"]]), lambda _: self.open_editor(row))

    def open_editor(self, row):
        self._prune_editors()
        path = self.workspace.root / row["path"]
        if not path.exists():
            self.status.setText("This document is new or renamed remotely. Use Compare / resolve to load its revision.")
            return
        # One Studio per session shares its existing tabs. Separate windows
        # would load stale copies of the same writable local document.
        key = row["kind"] if row["kind"] in {"module", "display"} else row["path"]
        for editor in self.editors:
            if editor._draft_editor_key != key:
                continue
            if row["kind"] == "module":
                editor.designer._load(str(path))
            elif row["kind"] == "display":
                editor.open_display(path.parent.name)
            editor.show()
            editor.raise_()
            editor.activateWindow()
            return
        if row["kind"] == "module":
            from ...app import AreaContext
            from ...azeo_control_designer.designer_window import StrategyDesignerWindow
            from ..datastore.shared_data_store import SharedDataStore
            editor = StrategyDesignerWindow(store=SharedDataStore(), plugin=AreaContext(self.workspace.root))
            changes = weakref.ref(self)
            def shared_rename(path):
                dialog = changes()
                if dialog is not None:
                    dialog.search.setText(Path(path).relative_to(dialog.workspace.root).as_posix())
                    dialog.table.selectRow(0)
                    dialog.show()
                    dialog.raise_()
                    QTimer.singleShot(0, dialog.rename)
            editor._configuration_rename = shared_rename
            editor.designer._load(str(path))
            editor.statusBar().showMessage("Working draft · Save locally, then check in through Shared changes")
        elif row["kind"] == "display":
            from ...azeo_graphics_designer.window import HmiStudioWindow
            editor = HmiStudioWindow(lambda: {}, self.workspace.root / "displays/pvm",
                                     area_name=self.workspace.meta["name"], configuration_root=self.workspace.root / "displays/pvm")
            editor.open_display(path.parent.name)
        else:
            editor = QDialog(self, Qt.Window)
            editor.resize(800, 600)
            box = QVBoxLayout(editor)
            box.addWidget(QLabel("Advanced configuration document · changes are saved to this working draft"))
            text = QPlainTextEdit()
            try:
                text.setPlainText(path.read_text(encoding="utf-8-sig"))
            except (UnicodeError, OSError):
                self.status.setText("Binary assets can be replaced in the working draft and reviewed before check-in.")
                return
            box.addWidget(text)
            def save():
                content = text.toPlainText()
                if path.suffix == ".json":
                    json.loads(content)
                path.write_text(content, encoding="utf-8")
            editor._draft_save = save
            button = QPushButton("Save draft")
            button.clicked.connect(self.save_editors)
            box.addWidget(button)
        editor.setWindowTitle(editor.windowTitle() + " — WORKING DRAFT")
        editor._draft_editor_key = key
        self.editors.append(editor)
        editor.show()

    def _prune_editors(self):
        # A manually closed Studio has already handled Save/Discard and torn
        # down its scene. Do not save its old graph when the session closes.
        closed = [editor for editor in self.editors if not editor.isVisible()]
        self._closed_editors.extend(closed)
        self.editors = [editor for editor in self.editors if editor.isVisible()]

    def save_editors(self):
        self._prune_editors()
        try:
            for editor in self.editors:
                if hasattr(editor, "designer"):
                    designer = editor.designer
                    for i in range(designer._canvas_tabs.count()):
                        canvas = designer._canvas_tabs.widget(i)
                        if getattr(canvas, "dirty", False):
                            designer._save(canvas)
                            if canvas.dirty:
                                raise ConfigurationError("A control draft could not be saved; check its editor message")
                elif hasattr(editor, "studios"):
                    for studio in editor.studios():
                        if studio.unsaved and not studio.save_draft():
                            raise ConfigurationError("A graphics draft could not be saved; check its editor message")
                elif hasattr(editor, "_draft_save"):
                    editor._draft_save()
            self.status.setText("Working drafts saved. Review changes to check them in.")
            return True
        except Exception as error:
            self.failed(error)
            return False

    def close_editors(self):
        if not self.save_editors():
            return False
        for editor in self.editors:
            if not editor.close():
                return False
        self._closed_editors.extend(self.editors)
        self.editors = []
        return True

    def review(self):
        if not self.close_editors():
            return
        self.run(self.workspace.preview, self.show_review)

    def show_review(self, reviewed):
        dialog = QDialog(self, Qt.Window)
        dialog.setWindowTitle("Review engineering change set")
        dialog.resize(950, 650)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"{len(reviewed['edits'])} documents will be checked in together."))
        table, model = _table([("path", "Document"), ("expected_revision", "Base revision")], "Check-in documents")
        model.set_rows(reviewed["edits"])
        tabs = QTabWidget()
        tabs.addTab(table, "Documents")
        fields, field_model = _table([("document", "Document"), ("field", "Field"),
                                     ("base", "Before"), ("draft", "After")], "Changed fields")
        fields.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        base_files = {f["path"]: f["content"] for f in self.workspace.base["files"]}
        differences = []
        for edit in reviewed["edits"]:
            previous = base_files.get(edit["path"])
            differences.extend({**row, "document": edit["path"]} for row in comparison_rows(
                {"base": previous, "draft": edit["content"], "current": previous}))
        field_model.set_rows(differences)
        tabs.addTab(fields, f"Changed fields ({len(differences)})")
        layout.addWidget(tabs, 1)
        coverage = QPlainTextEdit()
        coverage.setReadOnly(True)
        coverage.setPlainText("Unresolved/dynamic references:\n" + "\n".join(
            f"{e['source']} → {e['target']} ({e['status']})" for e in reviewed["review"]["unresolved"]) +
            "\n\n" + "\n".join(i["message"] for i in reviewed["review"]["issues"]))
        layout.addWidget(coverage, 1)
        reason = QLineEdit()
        reason.setMaxLength(2000)
        reason.setPlaceholderText("Describe why you are making this change")
        layout.addWidget(reason)
        accepted = QCheckBox("I reviewed the changed documents and the reference findings")
        layout.addWidget(accepted)
        button = QPushButton("Check in change set")
        button.setProperty("configurationPrimary", True)
        button.setEnabled(False)
        accepted.toggled.connect(lambda _: button.setEnabled(accepted.isChecked() and bool(reason.text().strip())))
        reason.textChanged.connect(lambda _: button.setEnabled(accepted.isChecked() and bool(reason.text().strip())))
        def commit():
            message = reason.text()
            dialog.close()
            self.run(lambda: self.workspace.checkin(reviewed, message), self.committed)
        button.clicked.connect(commit)
        layout.addWidget(button)
        self._dialogs.append(dialog)
        polish_page(dialog)
        dialog.show()

    def committed(self, receipt):
        self.status.setText(f"Checked in revision set {receipt['generation']}: {receipt['changed']} changed documents. "
                            "Running configuration has not been changed.")
        self._refresh_after = True

    def _finished(self):
        super()._finished()
        if getattr(self, "_refresh_after", False):
            self._refresh_after = False
            self.refresh()

    def compare(self):
        row = self.selected()
        if row and self.close_editors():
            self.run(lambda: self.workspace.comparison(row["path"]), self.show_comparison)

    def show_comparison(self, comparison):
        dialog = QDialog(self, Qt.Window)
        dialog.setWindowTitle("Resolve draft — " + comparison["path"])
        dialog.resize(1180, 670)
        layout = QVBoxLayout(dialog)
        table, model = _table([("field", "Field"), ("base", "Draft base"), ("draft", "Your draft"),
                              ("current", "Current committed")], "Conflict comparison")
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        model.set_rows(comparison_rows(comparison))
        layout.addWidget(table)
        accepted = QCheckBox("I reviewed these differences; preserve my current draft as a recovery copy")
        layout.addWidget(accepted)
        row = QHBoxLayout()
        for text, keep in [("Reload current revision", False), ("Keep reviewed draft against current revision", True)]:
            button = QPushButton(text)
            button.setEnabled(False)
            accepted.toggled.connect(button.setEnabled)
            def resolve(_=False, keep=keep):
                dialog.close()
                self.run(lambda: self.workspace.resolve(comparison, keep_draft=keep),
                         lambda _: self.status.setText("Draft resolved. Reopen the editor or review changes to check in."))
            button.clicked.connect(resolve)
            row.addWidget(button)
        layout.addLayout(row)
        self._dialogs.append(dialog)
        polish_page(dialog)
        dialog.show()

    def rename(self, new_name=None):
        row = self.selected()
        if not row or row["kind"] != "module" or not self.close_editors():
            self.status.setText("Select a module to review its rename impact.")
            return
        if isinstance(new_name, bool):
            new_name = None
        if new_name is None:
            if is_headless():
                return
            new_name, accepted = QInputDialog.getText(self, "Rename module", "New module name:")
            if not accepted:
                return
        self.run(lambda: self.workspace.rename_preview(row["name"], new_name), self.show_rename)

    def show_rename(self, plan):
        dialog = QDialog(self, Qt.Window)
        dialog.setWindowTitle(f"Rename {plan['module']} to {plan['new_name']}")
        dialog.resize(980, 660)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(plan["note"]))
        tabs = QTabWidget()
        table, model = _table([("location", "Reference"), ("before", "Before"), ("after", "After")], "Rename impact")
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        model.set_rows(plan["fields"])
        tabs.addTab(table, "Known changes")
        unknown, unknown_model = _table([("location", "Location"), ("value", "Unchanged text"), ("reason", "Review finding")], "Unknown rename dependencies")
        unknown_model.set_rows(plan["unknown"])
        tabs.addTab(unknown, f"Review manually ({len(plan['unknown'])})")
        layout.addWidget(tabs)
        accepted = QCheckBox("I reviewed the known consumers and the unchanged references requiring manual review")
        layout.addWidget(accepted)
        reason = QLineEdit()
        reason.setMaxLength(2000)
        reason.setPlaceholderText("Reason for rename")
        layout.addWidget(reason)
        button = QPushButton("Check in reviewed rename")
        button.setProperty("configurationPrimary", True)
        button.setEnabled(False)
        accepted.toggled.connect(lambda _: button.setEnabled(accepted.isChecked() and bool(reason.text().strip())))
        reason.textChanged.connect(lambda _: button.setEnabled(accepted.isChecked() and bool(reason.text().strip())))
        def commit():
            message = reason.text()
            dialog.close()
            self.run(lambda: self.workspace.rename_checkin(plan, message), self.committed)
        button.clicked.connect(commit)
        layout.addWidget(button)
        self._dialogs.append(dialog)
        polish_page(dialog)
        dialog.show()

    def release(self):
        self.run(self.workspace.release, lambda _: self.status.setText("Reservations released; working drafts retained."))

    def retry(self):
        if self.close_editors():
            self.run(self.workspace.retry, self.committed)

    def closeEvent(self, event):  # noqa: N802
        if self.worker is not None or not self.close_editors():
            event.ignore()
            return
        self.timer.stop()
        worker = _Command(self.workspace.release_on_close)
        _workers.add(worker)
        def release():
            _workers.discard(worker)
            worker.deleteLater()
        worker.finished.connect(release)
        worker.start()
        super().closeEvent(event)
