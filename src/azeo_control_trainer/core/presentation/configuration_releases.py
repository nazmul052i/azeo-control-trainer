"""Shared release review, deployment evidence and selective online tuning upload."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QPlainTextEdit, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from ..configuration.client import ConfigurationClient, ServiceUnavailable, read_profile
from ..configuration.documents import ConfigurationError, Conflict
from ..configuration.release_manifest import deployable
from ..strategy.serialization.strategy_io import write_json_transactional
from .configuration_catalog import _table
from .configuration_editing import BackgroundDialog
from .headless import is_headless
from .configuration_chrome import command_bar, ConfigurationComboBox as QComboBox
from .configuration_chrome import ConfigurationButton as QPushButton


def _pending_root():
    from ...config.paths import data_dir
    return data_dir() / "configuration/release_commands"


def _scope(profile):
    return hashlib.sha256((profile["url"] + "\0" + profile["token"]).encode()).hexdigest()


def send_command(profile, project, path, payload, *, timeout=150):
    record = _pending_root() / (payload["command_id"] + ".json")
    document = {"scope": _scope(profile), "project": project, "path": path, "payload": payload}
    if record.exists() and json.loads(record.read_text(encoding="utf-8")) != document:
        raise Conflict("This command has an unconfirmed receipt. Retry the original command before changing its review.")
    write_json_transactional(record, document)
    try:
        result = ConfigurationClient(**profile, timeout=timeout).request(path, payload)
    except ServiceUnavailable:
        raise
    except ConfigurationError:
        record.unlink(missing_ok=True)
        raise
    record.unlink(missing_ok=True)
    return result


def retry_pending(profile, project):
    count = 0
    for path in sorted(_pending_root().glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record["scope"] == _scope(profile) and record["project"] == project:
            send_command(profile, project, record["path"], record["payload"], timeout=600 if project == "recovery" else 150)
            count += 1
    return f"Recovered {count} pending command receipt(s)."


def start_runtime(directory):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[3])
    directory = Path(directory)
    with (directory / "runtime.log").open("ab") as log:
        subprocess.Popen([sys.executable, "-u", "-X", "faulthandler", "-m",
                          "azeo_control_trainer.azeo_explorer.configuration_runtime", str(directory)],
                         env=env, stdout=log, stderr=log,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


class ReviewDialog(BackgroundDialog):
    def __init__(self, project, profile, preview, parent=None):
        super().__init__(parent)
        self.project, self.profile, self.preview = project, profile, preview
        self.command = str(uuid4())
        self.setWindowTitle("Review validated release")
        self.resize(940, 670)
        layout = QVBoxLayout(self)
        manifest = preview["manifest"]
        self.status = QLabel(f"Generation {manifest['generation']} • {len(manifest['selected'])} deployment objects • "
                             f"{len(manifest['objects'])} exact file revisions pinned")
        layout.addWidget(self.status)
        details = QPlainTextEdit()
        details.setReadOnly(True)
        added = set(manifest["selected"]) - set(manifest["requested"])
        lines = ["SELECTED OBJECTS"] + [path + ("  (required control dependency)" if path in added else "") for path in manifest["selected"]]
        lines += ["", "VERIFICATION"] + [f"{f['severity']} • {f['path']}: {f['message']}" for f in manifest["findings"]]
        if not manifest["findings"]:
            lines += ["No verification findings."]
        if manifest["external_navigation"]:
            lines += ["", "NAVIGATION OUTSIDE THIS RELEASE — publish these displays separately if required:"]
            lines += [edge["target"] for edge in manifest["external_navigation"]]
        lines += ["", "PINNED FILE REVISIONS (modules, graphics, classes, assets and project context)"]
        lines += [f"r{obj['revision']} • {obj['path']} • {obj['digest'][:16]}" for obj in manifest["objects"]]
        details.setPlainText("\n".join(lines))
        layout.addWidget(details)
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("Release reason / training exercise")
        self.reason.setMaxLength(1500)
        layout.addWidget(self.reason)
        self.reviewed = QCheckBox("I reviewed the selected objects, dependencies and verification findings")
        layout.addWidget(self.reviewed)
        self.commit_button = QPushButton("Create immutable release")
        self.commit_button.setEnabled(False)
        self.commit_button.clicked.connect(self.commit)
        self.reviewed.toggled.connect(self.gate)
        self.reason.textChanged.connect(self.gate)
        layout.addWidget(self.commit_button)

    def gate(self):
        self.commit_button.setEnabled(bool(self.reason.text().strip()) and self.reviewed.isChecked()
                                      and not any(f["severity"] == "ERROR" for f in self.preview["manifest"]["findings"]))

    def commit(self):
        if not self.commit_button.isEnabled():
            return
        payload = {"preview": self.preview["id"], "reason": self.reason.text().strip(), "command_id": self.command}
        self.run(lambda: send_command(self.profile, self.project, f"/v1/projects/{self.project}/releases", payload),
                 lambda receipt: self.accept())


class UploadDialog(BackgroundDialog):
    def __init__(self, project, profile, preview, parent=None):
        super().__init__(parent)
        self.project, self.profile, self.preview = project, profile, preview
        self.command = str(uuid4())
        self.setWindowTitle("Review online tuning upload")
        self.resize(1080, 620)
        layout = QVBoxLayout(self)
        self.status = QLabel("Choose individual fields. Upload creates a new engineering revision; the running controller is unchanged. "
                             + "Observed: " + str(preview.get("observed_at", "")))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.table = QTableWidget(len(preview["rows"]), 6)
        self.table.setHorizontalHeaderLabels(["Upload", "Module / block", "Parameter", "Configured", "Online", "Category"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for column, width in [(0, 65), (2, 150), (3, 125), (4, 125), (5, 120)]:
            self.table.setColumnWidth(column, width)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        for row, item in enumerate(preview["rows"]):
            check = QTableWidgetItem()
            check.setCheckState(Qt.Unchecked)
            self.table.setItem(row, 0, check)
            for column, value in enumerate([item["module"] + "/" + item["instance_name"], item["parameter"],
                                            item["file_value"], item["runtime_value"], item["category"]], 1):
                self.table.setItem(row, column, QTableWidgetItem(str(value)))
        layout.addWidget(self.table)
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("Reason for keeping the selected online tuning")
        self.reason.setMaxLength(1400)
        layout.addWidget(self.reason)
        button = QPushButton("Check in selected tuning")
        button.clicked.connect(self.commit)
        layout.addWidget(button)

    def commit(self):
        selected = [row["id"] for index, row in enumerate(self.preview["rows"]) if self.table.item(index, 0).checkState() == Qt.Checked]
        if not selected or not self.reason.text().strip():
            self.status.setText("Select at least one field and enter a reason.")
            return
        payload = {"preview": self.preview["id"], "selected": selected,
                   "reason": self.reason.text().strip(), "command_id": self.command}
        self.run(lambda: send_command(self.profile, self.project, f"/v1/projects/{self.project}/uploads", payload),
                 lambda receipt: self.accept())


class ReleaseManager(BackgroundDialog):
    def __init__(self, project, parent=None, *, profile=None):
        super().__init__(parent)
        self.project = str(project["id"])
        self.profile = profile or read_profile()
        self.client = ConfigurationClient(**self.profile, timeout=150)
        self.state = None
        self.setWindowTitle("Release Manager — " + project["name"])
        self.resize(1180, 770)
        layout = QVBoxLayout(self)
        self.status = QLabel("Loading checked-in configuration and runtime evidence…")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        commands = command_bar(self, [
            ("New runtime…", self.create_target, "new", False),
            ("Open runtime", self.resume_target, "open", False),
            ("Refresh", self.refresh, "restore", False),
        ], more=[("Retry pending command", self.retry_commands, "restore")])
        self.commands = commands
        self.target = QComboBox()
        self.target.setAccessibleName("Deployment runtime target")
        self.target.setMaximumWidth(350)
        self.target.setPlaceholderText("Select a runtime")
        self.target.currentIndexChanged.connect(lambda: self.commands.buttons["Open runtime"].setEnabled(bool(self.target.currentData())))
        self.commands.buttons["Open runtime"].setEnabled(False)
        commands.layout().insertWidget(0, QLabel("RUNTIME", objectName="configurationFieldLabel"))
        commands.layout().insertWidget(1, self.target, 1)
        layout.addWidget(commands)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        build = QWidget()
        build_layout = QVBoxLayout(build)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter modules and graphics…")
        self.search.textChanged.connect(self.filter_objects)
        build_layout.addWidget(self.search)
        self.objects = QTableWidget(0, 3)
        self.objects.setHorizontalHeaderLabels(["Include", "Module / graphic", "Revision"])
        self.objects.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.objects.setColumnWidth(0, 64)
        self.objects.setColumnWidth(2, 85)
        self.objects.horizontalHeader().setStretchLastSection(False)
        self.objects.setSelectionBehavior(QTableWidget.SelectRows)
        self.objects.setEditTriggers(QTableWidget.NoEditTriggers)
        build_layout.addWidget(self.objects)
        review = QPushButton("Validate and review…")
        self.review_button = review
        review.setToolTip("Compile the selected modules, verify graphics, then review the release and its dependencies.")
        review.setProperty("configurationPrimary", True)
        review.clicked.connect(self.preview)
        review_row = QHBoxLayout()
        self.selection_count = QLabel("Select modules or graphics to include")
        review_row.addWidget(self.selection_count)
        review_row.addStretch()
        review_row.addWidget(review)
        build_layout.addLayout(review_row)
        self.objects.itemChanged.connect(self._selection_changed)
        self._selection_changed()
        self.tabs.addTab(build, "Build release")
        deploy = QWidget()
        deploy_layout = QVBoxLayout(deploy)
        self.releases, self.release_model = _table([("number", "Release"), ("reason", "Reason"), ("actor", "Engineer"), ("created", "Created")], "Immutable releases")
        deploy_layout.addWidget(self.releases)
        controls = QHBoxLayout()
        self.controller = QCheckBox("Download control modules")
        self.controller.setChecked(True)
        self.station = QCheckBox("Publish graphics to station")
        self.station.setChecked(True)
        controls.addWidget(self.controller)
        controls.addWidget(self.station)
        button = QPushButton("Deploy selected release")
        self.deploy_button = button
        button.setProperty("configurationPrimary", True)
        button.clicked.connect(self.deploy)
        controls.addWidget(button)
        deploy_layout.addLayout(controls)
        deploy_layout.addWidget(QLabel("A publication becomes available. The operator keeps an opened revision until Refresh."))
        self.jobs, self.job_model = _table([("state", "Delivery"), ("attempt", "Attempt"), ("target_name", "Target"),
                                          ("release_number", "Release"), ("message", "Evidence / failure")], "Deployment jobs")
        deploy_layout.addWidget(self.jobs)
        self.job_details = QPlainTextEdit()
        self.job_details.setReadOnly(True)
        self.job_details.setPlaceholderText("Select a delivery to inspect its result and recovery evidence.")
        self.job_details.setMaximumHeight(125)
        deploy_layout.addWidget(self.job_details)
        self.jobs.selectionModel().currentRowChanged.connect(self._describe_job)
        deploy_layout.addWidget(command_bar(self, [], more=[
            ("Retry selected failed deployment", self.retry_job, "restore"),
            ("Cancel selected failed deployment", self.cancel_job, "delete")]))
        self.tabs.addTab(deploy, "Deploy / recover")
        compare = QWidget()
        compare_layout = QVBoxLayout(compare)
        filters = QHBoxLayout()
        self.comparison_search = QLineEdit()
        self.comparison_search.setPlaceholderText("Filter object, target or comparison status…")
        self.comparison_search.textChanged.connect(self.filter_comparison)
        filters.addWidget(self.comparison_search)
        self.reported_only = QCheckBox("Reported objects only")
        self.reported_only.setChecked(True)
        self.reported_only.toggled.connect(self.filter_comparison)
        filters.addWidget(self.reported_only)
        compare_layout.addLayout(filters)
        self.comparison, self.comparison_model = _table([("target", "Target"), ("path", "Object"), ("configured", "Configured"),
                                                        ("running", "Running / accepted"), ("status", "Comparison")], "Configured versus running")
        self.comparison.horizontalHeader().setStretchLastSection(False)
        self.comparison.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for column, width in [(0, 200), (2, 105), (3, 155), (4, 145)]:
            self.comparison.setColumnWidth(column, width)
        compare_layout.addWidget(self.comparison)
        upload = QPushButton("Compare selected runtime's online tuning…")
        self.upload_button = upload
        upload.clicked.connect(self.upload)
        compare_layout.addWidget(upload)
        self.tabs.addTab(compare, "Configured vs running")
        self.events, self.event_model = _table([("occurred", "Time"), ("event", "Event"), ("actor", "Reported by"), ("evidence", "Evidence")], "Deployment audit")
        self.tabs.addTab(self.events, "Audit")
        self.timer = QTimer(self)
        self.timer.setInterval(10000)
        self.timer.timeout.connect(lambda: self.refresh() if self.isVisible() and not self.worker else None)
        self.timer.start()
        QTimer.singleShot(0, self.refresh)

    def refresh(self):
        self.run(lambda: self.client.request(f"/v1/projects/{self.project}/releases/state"), self.loaded)

    def loaded(self, state):
        selected = {self.objects.item(row, 1).text() for row in range(self.objects.rowCount()) if self.objects.item(row, 0).checkState() == Qt.Checked}
        self.state = state
        self.status.setText(f"Revision set {state['project']['generation']} · {len(state['releases'])} releases · {len(state['targets'])} runtime targets")
        self.status.setToolTip("Runtime evidence expires after 30 seconds without a target heartbeat. Delivered does not imply a scan or operator acceptance.")
        objects = [obj for obj in state["objects"] if deployable(obj)]
        self.objects.blockSignals(True)
        self.objects.setRowCount(len(objects))
        for row, obj in enumerate(objects):
            check = QTableWidgetItem()
            check.setCheckState(Qt.Checked if obj["path"] in selected else Qt.Unchecked)
            self.objects.setItem(row, 0, check)
            self.objects.setItem(row, 1, QTableWidgetItem(obj["path"]))
            self.objects.setItem(row, 2, QTableWidgetItem(str(obj["revision"])))
        self.objects.blockSignals(False)
        self._selection_changed()
        self.filter_objects()
        previous = self.target.currentData()
        self.target.clear()
        for target in state["targets"]:
            self.target.addItem(target["name"] + (" • connected" if target["fresh"] else " • unknown/offline"), target["id"])
        if self.target.count():
            self.target.setCurrentIndex(max(0, self.target.findData(previous)))
        targets = {row["id"]: row["name"] for row in state["targets"]}
        releases = {row["id"]: row["number"] for row in state["releases"]}
        self.release_model.set_rows(state["releases"])
        self.job_model.set_rows([{**row, "target_name": targets.get(row["target_id"]), "release_number": releases.get(row["release_id"]),
                                 "message": (row["receipt"].get("error", row["receipt"].get("message", "Select for delivery details"))
                                             if isinstance(row["receipt"], dict) else str(row["receipt"]))} for row in state["jobs"]])
        self.filter_comparison()
        self.event_model.set_rows(state["events"])

    def _describe_job(self, current, *_):
        row = self.job_model.rows[current.row()] if current.isValid() else None
        self.job_details.setPlainText(json.dumps(row.get("receipt", {}), indent=2, ensure_ascii=False) if row else "")

    def filter_comparison(self):
        if self.state is None:
            return
        query = self.comparison_search.text().casefold().strip()
        # Preserve visibility of previously reported objects when a heartbeat
        # expires, while keeping unrelated, never-loaded modules out of the way.
        reported = {(str(target["id"]), str(obj.get("object_id")))
                    for target in self.state["targets"]
                    for obj in target.get("report", {}).get("objects", [])}
        self.comparison_model.set_rows([
            row for row in self.state["comparison"]
            if (not self.reported_only.isChecked() or (str(row["target_id"]), str(row["id"])) in reported)
            and query in " ".join(str(row.get(key, "")) for key in ("target", "path", "status")).casefold()])

    def filter_objects(self):
        query = self.search.text().casefold()
        for row in range(self.objects.rowCount()):
            self.objects.setRowHidden(row, query not in self.objects.item(row, 1).text().casefold())

    def _selection_changed(self, *_):
        count = sum(item is not None and item.checkState() == Qt.Checked
                    for item in (self.objects.item(row, 0) for row in range(self.objects.rowCount())))
        self.selection_count.setText(f"{count} selected for release" if count else "Select modules or graphics to include")
        self.review_button.setEnabled(count > 0)

    def show_review(self, preview):
        dialog = ReviewDialog(self.project, self.profile, preview, self)
        self._dialogs.append(dialog)
        dialog.accepted.connect(self.refresh)
        dialog.show()

    def preview(self):
        paths = [self.objects.item(row, 1).text() for row in range(self.objects.rowCount()) if self.objects.item(row, 0).checkState() == Qt.Checked]
        if not paths:
            self.status.setText("Select modules or graphics to validate.")
            return
        self.status.setText("Compiling control dependencies and running the Graphics Designer verifier…")
        self.run(lambda: self.client.request(f"/v1/projects/{self.project}/releases/preview", {"paths": paths}), self.show_review)

    def create_target(self, name=None):
        if isinstance(name, bool):
            name = None
        if name is None:
            if is_headless():
                return
            name, accepted = QInputDialog.getText(self, "Isolated runtime", "Runtime name:", text="Training runtime")
            if not accepted:
                return
        def create():
            from ...config.paths import data_dir
            target = self.client.request(f"/v1/projects/{self.project}/runtime-targets", {"name": name})
            directory = data_dir() / "configuration/runtime_nodes" / target["id"]
            write_json_transactional(directory / "target.json", {**target, "url": self.profile["url"]})
            start_runtime(directory)
            return self.client.request(f"/v1/projects/{self.project}/releases/state")
        self.run(create, self.loaded)

    def resume_target(self):
        from ...config.paths import data_dir
        target = self.target.currentData()
        if not target:
            return
        directory = data_dir() / "configuration/runtime_nodes" / target
        def launch():
            if not (directory / "target.json").exists():
                raise ConfigurationError("This runtime's private profile is on the computer where it was created")
            start_runtime(directory)
            return "Runtime launch requested. Its process lock prevents a duplicate station."
        self.run(launch, self.status.setText)

    def deploy(self):
        index = self.releases.currentIndex()
        target = self.target.currentData()
        if not index.isValid() or not target:
            self.status.setText("Select an immutable release and a runtime target.")
            return
        release = self.release_model.rows[index.row()]
        components = [name for name, checked in [("controller", self.controller.isChecked()), ("station", self.station.isChecked())] if checked]
        payload = {"release": release["id"], "target": target, "components": components, "command_id": str(uuid4())}
        self.run(lambda: send_command(self.profile, self.project, f"/v1/projects/{self.project}/deployments", payload),
                 lambda receipt: self.status.setText("Deployment queued. Refresh evidence to follow target acknowledgments."))

    def retry_job(self):
        index = self.jobs.currentIndex()
        if index.isValid():
            job = self.job_model.rows[index.row()]["id"]
            self.run(lambda: self.client.request(f"/v1/projects/{self.project}/deployments/{job}/retry", {}),
                     lambda result: self.status.setText("Retry queued with the same release and job identity."))

    def retry_commands(self):
        self.run(lambda: retry_pending(self.profile, self.project), self.status.setText)

    def cancel_job(self):
        index = self.jobs.currentIndex()
        if index.isValid():
            job = self.job_model.rows[index.row()]["id"]
            self.run(lambda: self.client.request(f"/v1/projects/{self.project}/deployments/{job}/cancel", {}),
                     lambda result: self.status.setText("Failed delivery cancelled. Existing target changes remain visible; the next job may proceed."))

    def upload(self):
        target = self.target.currentData()
        if not target:
            return
        def ready(preview):
            dialog = UploadDialog(self.project, self.profile, preview, self)
            self._dialogs.append(dialog)
            dialog.accepted.connect(self.refresh)
            dialog.show()
        self.run(lambda: self.client.request(f"/v1/projects/{self.project}/runtime-targets/{target}/upload-preview", {}), ready)
