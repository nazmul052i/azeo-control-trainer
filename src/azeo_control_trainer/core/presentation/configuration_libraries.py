"""Selective library adoption is an engineering change with a reviewable receipt."""
from uuid import uuid4

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPlainTextEdit, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from ..configuration.client import ConfigurationClient, read_profile
from .configuration_catalog import _table
from .configuration_editing import BackgroundDialog
from .configuration_chrome import ConfigurationComboBox as QComboBox, ConfigurationButton as QPushButton
from .configuration_releases import send_command


class AdoptionReview(BackgroundDialog):
    def __init__(self, project, profile, preview, parent=None):
        super().__init__(parent)
        self.project, self.profile, self.preview = project, profile, preview
        self.command = str(uuid4())
        self.setWindowTitle("Review class adoption")
        self.resize(1100, 720)
        layout = QVBoxLayout(self)
        self.status = QLabel(f"Current project generation {preview['generation']} · class source generation {preview['target_generation']}. "
                             "Only the selected instances change. Release Manager deploys the checked-in result.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        table, model = _table([("instance", "Instance"), ("before", "Current version"),
                               ("after", "Proposed version"), ("action", "Change")], "Reviewed class adoption")
        model.set_rows(preview["rows"])
        tabs = QTabWidget()
        tabs.addTab(table, "Selected instances")
        comparison = QPlainTextEdit()
        comparison.setReadOnly(True)
        comparison.setPlainText("\n\n".join(row["instance"] + "\n" + row["text"] for row in preview.get("diffs", [])))
        tabs.addTab(comparison, "Class changes")
        layout.addWidget(tabs)
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("Engineering change reason")
        self.reason.setMaxLength(1500)
        layout.addWidget(self.reason)
        self.reviewed = QCheckBox("I reviewed the selected instances and retained overrides")
        layout.addWidget(self.reviewed)
        self.commit_button = QPushButton("Check in reviewed adoption")
        self.commit_button.setEnabled(False)
        self.commit_button.clicked.connect(self.commit)
        self.reason.textChanged.connect(self.gate)
        self.reviewed.toggled.connect(self.gate)
        layout.addWidget(self.commit_button)

    def gate(self):
        self.commit_button.setEnabled(bool(self.reason.text().strip()) and self.reviewed.isChecked())

    def commit(self):
        if not self.commit_button.isEnabled():
            return
        payload = {"preview": self.preview["id"], "reason": self.reason.text().strip(), "command_id": self.command}
        self.run(lambda: send_command(self.profile, self.project, f"/v1/projects/{self.project}/libraries/adopt", payload),
                 lambda _: self.accept())


class LibraryManager(BackgroundDialog):
    def __init__(self, project, parent=None, *, profile=None):
        super().__init__(parent)
        self.project = str(project["id"])
        self.profile = profile or read_profile()
        self.client = ConfigurationClient(**self.profile, timeout=150)
        self.state = None
        self.rows = []
        self.setWindowTitle("Library Manager — " + project["name"])
        self.resize(1180, 740)
        layout = QVBoxLayout(self)
        self.status = QLabel("Loading shared classes and their checked-in instances…")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter class, instance or display…")
        self.search.textChanged.connect(self.filter_instances)
        toolbar.addWidget(self.search, 1)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        toolbar.addWidget(refresh)
        layout.addLayout(toolbar)
        self.classes = QComboBox()
        self.classes.setAccessibleName("Shared library class")
        self.classes.setMaximumWidth(280)
        self.classes.currentIndexChanged.connect(self.filter_instances)
        toolbar.insertWidget(1, self.classes)
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        self.instances = QTableWidget(0, 4)
        self.instances.setHorizontalHeaderLabels(["Select", "Instance / Used by", "State", "Version"])
        self.instances.setEditTriggers(QTableWidget.NoEditTriggers)
        self.instances.setSelectionBehavior(QTableWidget.SelectRows)
        self.instances.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.instances.setColumnWidth(0, 55)
        self.instances.setColumnWidth(2, 120)
        self.instances.setColumnWidth(3, 100)
        self.instances.currentCellChanged.connect(self.show_properties)
        split.addWidget(self.instances)
        properties = QWidget()
        properties_layout = QVBoxLayout(properties)
        note = QLabel("Instance properties · inherited and overridden values")
        note.setWordWrap(True)
        properties_layout.addWidget(note)
        self.properties, self.property_model = _table([("name", "Property"), ("inherited", "Class default"),
                                                     ("value", "Effective value"), ("origin", "Origin")], "Inherited and overridden properties")
        self.properties.horizontalHeader().setStretchLastSection(False)
        for column, width in [(0, 110), (1, 110), (3, 90)]:
            self.properties.setColumnWidth(column, width)
        self.properties.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        properties_layout.addWidget(self.properties)
        split.addWidget(properties)
        split.setSizes([660, 420])
        layout.addWidget(split, 1)
        row = QHBoxLayout()
        self.generation = QSpinBox()
        self.generation.setRange(0, 0)
        self.generation.setSpecialValueText("Latest checked-in")
        self.generation.setAccessibleName("Source revision set")
        self.generation.setMaximumWidth(190)
        row.addWidget(QLabel("Source revision set"))
        row.addWidget(self.generation)
        row.addStretch()
        pin = QPushButton("Review initial pins…")
        pin.clicked.connect(lambda: self.preview(pin=True))
        row.addWidget(pin)
        adopt = QPushButton("Review selected adoption…")
        adopt.setProperty("configurationPrimary", True)
        adopt.clicked.connect(lambda: self.preview(pin=False))
        row.addWidget(adopt)
        layout.addLayout(row)
        QTimer.singleShot(0, self.refresh)

    def refresh(self):
        self.run(lambda: self.client.request(f"/v1/projects/{self.project}/libraries"), self.loaded)

    def loaded(self, state):
        self.state = state
        self.classes.blockSignals(True)
        selected = self.classes.currentData()
        self.classes.clear()
        self.classes.addItem("All shared classes", "")
        for cls in sorted(state["classes"], key=lambda row: (row["kind"], row["name"])):
            self.classes.addItem(cls["name"] + " · " + cls["kind"], cls["id"])
        self.classes.setCurrentIndex(max(0, self.classes.findData(selected)))
        self.classes.blockSignals(False)
        self.generation.setMaximum(state["project"]["generation"])
        self.status.setText(f"Revision set {state['project']['generation']} · {len(state['classes'])} classes · {len(state['instances'])} instances")
        self.status.setToolTip("Pin existing graphics before changing a shared class; review adoption for the selected instances.")
        self.filter_instances()

    def filter_instances(self):
        if not self.state:
            return
        old_rows = getattr(self, "rows", [])
        checked = {row["id"] for i, row in enumerate(old_rows)
                   if self.instances.item(i, 0) is not None and self.instances.item(i, 0).checkState() == Qt.Checked}
        current = self.instances.currentRow()
        focused = old_rows[current]["id"] if 0 <= current < len(old_rows) else None
        selected = self.classes.currentData()
        needle = self.search.text().casefold()
        self.rows = [row for row in self.state["instances"] if (not selected or row["class_id"] == selected)
                     and needle in (row["name"] + " " + row["class_id"]).casefold()]
        self.instances.blockSignals(True)
        self.instances.setRowCount(0)
        self.instances.setRowCount(len(self.rows))
        for index, row in enumerate(self.rows):
            check = QTableWidgetItem()
            check.setCheckState(Qt.Checked if row["id"] in checked else Qt.Unchecked)
            self.instances.setItem(index, 0, check)
            for column, value in enumerate([row["name"], row["status"], str(row["revision"] or "Unpinned")], 1):
                item = QTableWidgetItem(value[:14] + "…" if column == 3 and len(value) > 20 else value)
                item.setToolTip(value)
                self.instances.setItem(index, column, item)
            if row["id"] == focused:
                self.instances.setCurrentCell(index, 1)
        self.instances.blockSignals(False)
        self.show_properties(self.instances.currentRow())

    def show_properties(self, row, *_):
        self.property_model.set_rows(self.rows[row]["properties"] if 0 <= row < len(self.rows) else [])

    def preview(self, *, pin):
        selected = [row["id"] for index, row in enumerate(self.rows)
                    if self.instances.item(index, 0).checkState() == Qt.Checked]
        if not selected:
            self.status.setText("Select at least one instance to review.")
            return
        body = {"selected": selected, "pin": pin, "generation": None if pin else self.generation.value() or None}
        self.run(lambda: self.client.request(f"/v1/projects/{self.project}/libraries/preview", body), self.show_review)

    def show_review(self, preview):
        dialog = AdoptionReview(self.project, self.profile, preview, self)
        self._dialogs.append(dialog)
        dialog.accepted.connect(self.refresh)
        dialog.show()
