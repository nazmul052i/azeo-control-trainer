"""Baseline and trainee workflows reached from the shared engineering catalog."""
from dataclasses import asdict
import json
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QSplitter, QTextBrowser, QVBoxLayout, QWidget)

from ..configuration.client import ConfigurationClient, read_profile
from ..configuration.training import export_exercise
from .configuration_catalog import _table
from .configuration_editing import BackgroundDialog
from .configuration_releases import send_command, retry_pending
from .configuration_chrome import command_bar, ConfigurationComboBox as QComboBox
from .configuration_chrome import ConfigurationButton as QPushButton, detail_document


class BaselineReview(BackgroundDialog):
    def __init__(self, project, profile, releases, parent=None):
        super().__init__(parent)
        self.project, self.profile = project, profile
        self.command = str(uuid4())
        self.exercise, self.snapshot = None, None
        self.setWindowTitle("Create immutable training baseline")
        self.resize(760, 400)
        layout = QVBoxLayout(self)
        self.status = QLabel("Select a complete validated release. An exercise may also retain its captured process starting condition.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        form = QFormLayout()
        self.release = QComboBox()
        for row in releases:
            self.release.addItem(f"Release {row['number']} · {row['reason']}", row["id"])
        self.name, self.reason = QLineEdit(), QLineEdit()
        self.name.setMaxLength(160)
        self.reason.setMaxLength(160)
        form.addRow("Released configuration", self.release)
        form.addRow("Baseline name", self.name)
        form.addRow("Reason", self.reason)
        layout.addLayout(form)
        self.exercise_label = QLabel("Configuration baseline · no process reset is implied")
        self.exercise_label.setWordWrap(True)
        layout.addWidget(self.exercise_label)
        attach = QPushButton("Attach saved exercise and its snapshot…")
        attach.clicked.connect(self.choose_exercise)
        layout.addWidget(attach)
        self.create_button = QPushButton("Create immutable baseline")
        self.create_button.setProperty("configurationPrimary", True)
        self.create_button.clicked.connect(self.create)
        layout.addWidget(self.create_button)
        for field in (self.name, self.reason):
            field.textChanged.connect(self.gate)
        self.release.currentIndexChanged.connect(self.gate)
        self.gate()

    def gate(self):
        self.create_button.setEnabled(bool(self.release.currentData() and self.name.text().strip() and self.reason.text().strip()))

    def choose_exercise(self):
        path, _ = QFileDialog.getOpenFileName(self, "Saved Azeo exercise", "", "Exercise (*.json)")
        if path:
            self.run(lambda: self.load_exercise(path), self.attach)

    @staticmethod
    def load_exercise(path):
        from ..simulation.training import Exercise
        from ..simulation.workbench import SimulationWorkbenchService
        exercise = Exercise(**json.loads(Path(path).read_text(encoding="utf-8")))
        exercise.validate()
        snapshot = SimulationWorkbenchService.load_snapshot_document(exercise.snapshot)
        return asdict(exercise), snapshot

    def attach(self, result):
        self.exercise, self.snapshot = result
        self.exercise_label.setText("Exercise: " + self.exercise["name"] + " · " + self.exercise["loop"])

    def create(self):
        if not self.release.currentData() or not self.name.text().strip() or not self.reason.text().strip():
            self.status.setText("Choose a release and enter the baseline name and reason")
            return
        payload = {"release": self.release.currentData(), "name": self.name.text(), "reason": self.reason.text(),
                   "command_id": self.command, "exercise": self.exercise, "snapshot": self.snapshot}
        self.run(lambda: send_command(self.profile, self.project, f"/v1/projects/{self.project}/baselines", payload),
                 lambda row: (self.status.setText("Baseline retained: " + row["id"]), self.accept()))


class TrainingManager(BackgroundDialog):
    def __init__(self, project, parent=None, *, profile=None):
        super().__init__(parent)
        self.project, self.profile = project, profile or read_profile()
        self.client = ConfigurationClient(**self.profile, timeout=150)
        self.command = str(uuid4())
        self.setWindowTitle("Training baselines — " + project["name"])
        self.resize(1050, 650)
        layout = QVBoxLayout(self)
        self.status = QLabel("Loading baseline and release evidence…")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addWidget(command_bar(self, [
            ("Refresh", self.refresh, "restore", False),
            ("New baseline…", self.new_baseline, "checkpoint", True),
        ], more=[("Database backup and recovery…", self.recovery, "restore"),
                 ("Retry pending baseline or clone command", self.retry, "restore")]))
        self.table, self.model = _table([("name", "Baseline"), ("kind", "Contents"), ("created", "Captured"),
                                         ("actor", "Engineer"), ("reason", "Reason")], "Training baselines")
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.addWidget(self.table)
        inspector = QWidget()
        inspector_layout = QVBoxLayout(inspector)
        inspector_layout.setContentsMargins(0, 0, 0, 0)
        self.details = QTextBrowser()
        self.details.setPlainText("Select a baseline to inspect its released configuration and create a trainee copy.")
        inspector_layout.addWidget(self.details, 1)
        row = QHBoxLayout()
        self.trainee_name = QLineEdit()
        self.trainee_name.setPlaceholderText("New trainee project name")
        row.addWidget(self.trainee_name, 1)
        inspector_layout.addLayout(row)
        self.clone_button = QPushButton("Create isolated trainee copy")
        self.clone_button.clicked.connect(self.clone)
        self.clone_button.setEnabled(False)
        self.trainee_name.textChanged.connect(self.describe)
        inspector_layout.addWidget(self.clone_button)
        split.addWidget(inspector)
        split.setSizes([700, 360])
        layout.addWidget(split, 1)
        self.releases = []
        self.table.selectionModel().currentRowChanged.connect(self.describe)
        QTimer.singleShot(0, self.refresh)

    def refresh(self):
        project = self.project["id"]
        def load():
            return self.client.request(f"/v1/projects/{project}/baselines"), self.client.request(f"/v1/projects/{project}/releases/state")
        def ready(result):
            rows, state = result
            self.model.set_rows(rows)
            self.releases = state["releases"]
            self.status.setText(f"{len(rows)} immutable baselines · trainee copies have separate project and object identities")
        self.run(load, ready)

    def selected(self):
        index = self.table.currentIndex()
        return self.model.rows[index.row()] if index.isValid() else None

    def describe(self, *_):
        row = self.selected()
        self.clone_button.setEnabled(bool(row and self.trainee_name.text().strip()))
        if row:
            self.details.setHtml(detail_document(
                row["name"], row.get("reason", ""),
                "Create a trainee copy below, deploy it from Releases, then load its inherited exercise in Operator Live.\n\n"
                "Cloning retains engineering configuration. Process physics and operator commands are not reset or replayed by cloning.",
                f"Baseline identity\n{row['id']}\n\nRelease identity\n{row['release_id']}",
            ))
        else:
            self.details.setPlainText("Select a baseline to inspect its released configuration and create a trainee copy.")

    def new_baseline(self):
        dialog = BaselineReview(self.project["id"], self.profile, self.releases, self)
        self._dialogs.append(dialog)
        dialog.accepted.connect(lambda: QTimer.singleShot(0, self.refresh))
        dialog.show()
        return dialog

    def clone(self):
        row = self.selected()
        if not row or not self.trainee_name.text().strip():
            self.status.setText("Select a baseline and name the new trainee project")
            return
        path = f"/v1/projects/{self.project['id']}/baselines/{row['id']}/clone"
        payload = {"name": self.trainee_name.text().strip(), "command_id": self.command}
        def ready(result):
            self.command = str(uuid4())
            self.status.setText("Trainee project created: " + result["project_id"] + " · select it in the Engineering Catalog")
        self.run(lambda: send_command(self.profile, self.project["id"], path, payload), ready)

    def recovery(self):
        from .configuration_workspace import workspace_for
        host = workspace_for(self)
        if host:
            return host.open_page("recovery")
        from .configuration_recovery import RecoveryManager
        dialog = RecoveryManager(self, profile=self.profile)
        self._dialogs.append(dialog)
        dialog.show()
        return dialog

    def retry(self):
        def ready(message):
            self.command = str(uuid4())
            self.status.setText(message + " Refresh to inspect the baseline and project lists.")
        self.run(lambda: retry_pending(self.profile, self.project["id"]), ready)


def load_inherited_exercise(training_dialog):
    session = training_dialog.session
    projects = {row["project_id"] for row in session.configuration_context().values()}
    if len(projects) != 1:
        training_dialog.status.setText("Load one released trainee project before selecting its inherited exercise")
        return
    project = next(iter(projects))
    dialog = BackgroundDialog(training_dialog)
    dialog.setWindowTitle("Load inherited baseline exercise")
    layout = QVBoxLayout(dialog)
    dialog.status = QLabel("Reading the baseline retained by the loaded trainee project…")
    dialog.status.setWordWrap(True)
    layout.addWidget(dialog.status)
    profile = read_profile()
    def load():
        context = ConfigurationClient(**profile, timeout=150).request(f"/v1/projects/{project}/training-context")
        path = export_exercise(context, session.root)
        from ..simulation.training import Exercise
        return Exercise(**json.loads(path.read_text(encoding="utf-8")))
    def ready(exercise):
        training_dialog.saved.addItem(exercise.name, exercise)
        training_dialog.saved.setCurrentIndex(training_dialog.saved.count() - 1)
        training_dialog.status.setText("Inherited baseline selected. Start explicitly restores its process starting condition.")
        dialog.accept()
    training_dialog._repository_dialog = dialog
    dialog.run(load, ready)
    dialog.show()
