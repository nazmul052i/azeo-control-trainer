"""Guided creation of useful, valid procedure starting points."""
from __future__ import annotations

import re

from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QPlainTextEdit, QVBoxLayout

from azeo_control_trainer.core.presentation.engineering_dialog import polish_dialog
from azeo_control_trainer.core.procedures.authoring import ProcedureDraft
from azeo_control_trainer.core.procedures.library import block_for_type
from azeo_control_trainer.core.procedures.flow import linear_flow


TEMPLATES = {
    "blank": ("Blank", "One instruction and a controlled completion point."),
    "guided": ("Guided operation", "Prepare, perform, verify and record an operator-guided task."),
    "startup": ("Startup", "Readiness, lineup, start, stabilization and handover."),
    "shutdown": ("Shutdown", "Scope review, controlled stop, isolation verification and handover."),
    "changeover": ("Equipment changeover", "Verify standby equipment, transfer duty and confirm the result."),
    "loop_verification": ("Loop verification", "Guide an operator through loop mode, target and observation checks."),
    "advanced": ("Advanced workflow", "A ready-to-edit decision path with explicit default routing."),
}


def _step(kind, identity, description=""):
    row = dict(block_for_type(kind).instantiate(identity))
    if kind in {"check", "permissive", "watchdog", "wait_until"}:
        # Templates must start valid; the Problems panel then reminds the
        # engineer to replace this explicit placeholder with plant evidence.
        row["condition"] = "True"
    if description:
        row["description"] = description
    return row


def procedure_from_template(template: str, procedure_id: str, name: str, *, version="1.0.0", author="", revision_note="") -> ProcedureDraft:
    if template not in TEMPLATES:
        raise ValueError("Unknown procedure template")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{1,79}", procedure_id):
        raise ValueError("Procedure ID must start with a letter and use letters, numbers, underscore or hyphen")
    sequences = {
        "blank": [("instruction", "instruction", "Describe the operator action."), ("complete", "complete", "Procedure complete.")],
        "guided": [("instruction", "prepare", "Review prerequisites and confirm readiness."), ("instruction", "perform", "Perform the controlled operation."), ("check", "verify", "Verify the expected condition."), ("operator_comment", "record", "Record observations and deviations."), ("complete", "complete", "Procedure complete.")],
        "startup": [("permissive", "readiness", "Confirm startup permissives."), ("instruction", "lineup", "Establish the approved equipment lineup."), ("instruction", "start", "Start the equipment using its operator controls."), ("wait_until", "stabilize", "Wait for stable operating conditions."), ("operator_comment", "handover", "Record startup handover."), ("complete", "complete", "Startup complete.")],
        "shutdown": [("instruction", "scope", "Review shutdown scope and process conditions."), ("instruction", "stop", "Stop the equipment using its operator controls."), ("check", "isolated", "Verify the approved isolated state."), ("operator_comment", "handover", "Record shutdown handover."), ("complete", "complete", "Shutdown complete.")],
        "changeover": [("permissive", "standby_ready", "Confirm standby equipment is available."), ("instruction", "start_standby", "Start the standby equipment."), ("instruction", "transfer", "Transfer duty using operator controls."), ("check", "verify_transfer", "Verify duty transferred and process remains stable."), ("instruction", "stop_previous", "Stop the previous duty equipment if authorized."), ("complete", "complete", "Changeover complete.")],
        "loop_verification": [("instruction", "review", "Review loop scope and current operating condition."), ("instruction", "set_mode", "Select the required loop mode from the faceplate."), ("wait_until", "verify_mode", "Verify actual loop mode feedback."), ("operator_comment", "record", "Record response and observations."), ("complete", "complete", "Loop verification complete.")],
        "advanced": [("instruction", "prepare", "Confirm the procedure is ready to branch."), ("instruction", "normal_path", "Perform the normal path."), ("warning", "alternate_path", "Review the alternate path."), ("complete", "complete", "Advanced workflow complete.")],
    }
    steps = [_step(*row) for row in sequences[template]]
    data = {"procedure_id": procedure_id, "name": name.strip() or TEMPLATES[template][0], "mode": "advisory",
            "metadata": {"version": version.strip() or "1.0.0", "author": author.strip(), "revision_note": revision_note.strip(), "safety_class": "advisory"},
            "tags": [], "variables": [], "steps": steps}
    if template == "advanced":
        data["flow"] = linear_flow(steps)
        flow = data["flow"]
        normal = next(node for node in flow["nodes"] if node.get("step_id") == "normal_path")
        alternate = next(node for node in flow["nodes"] if node.get("step_id") == "alternate_path")
        incoming = next(edge for edge in flow["edges"] if edge["target"] == normal["id"])
        complete = next(node for node in flow["nodes"] if node.get("step_id") == "complete")
        choice = {"id": "route", "kind": "choice", "label": "Choose route"}
        incoming["target"] = "route"
        flow["nodes"].append(choice)
        flow["edges"] = [edge for edge in flow["edges"] if not (
            edge["source"] == normal["id"] and edge["target"] == alternate["id"]
        )]
        flow["edges"].extend([{"source": "route", "target": normal["id"], "condition": "True", "priority": 10},
                              {"source": "route", "target": alternate["id"], "is_default": True, "priority": 100},
                              {"source": normal["id"], "target": complete["id"]}])
    return ProcedureDraft(data, {})


class NewProcedureDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New procedure")
        self.resize(680, 470)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.template = QComboBox()
        for key, (label, description) in TEMPLATES.items():
            self.template.addItem(label, key)
            self.template.setItemData(self.template.count() - 1, description, 3)
        self.description = QLabel(wordWrap=True)
        self.template.currentIndexChanged.connect(self._describe)
        self.procedure_id = QLineEdit("new_procedure")
        self.name = QLineEdit("New procedure")
        self.version = QLineEdit("1.0.0")
        self.author = QLineEdit()
        self.note = QPlainTextEdit()
        self.note.setMaximumHeight(75)
        form.addRow("Starting point", self.template)
        form.addRow("", self.description)
        form.addRow("Procedure ID", self.procedure_id)
        form.addRow("Name", self.name)
        form.addRow("Version", self.version)
        form.addRow("Author", self.author)
        form.addRow("Revision note", self.note)
        layout.addLayout(form)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Create procedure")
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.error = QLabel()
        self.error.setStyleSheet("color: #b71c1c;")
        layout.insertWidget(layout.count() - 1, self.error)
        polish_dialog(self, title="New procedure", subtitle="Choose a tested starting structure, then tailor it to the plant task.", mark="procedure")
        self._describe()

    def _describe(self, *_):
        self.description.setText(TEMPLATES[self.template.currentData()][1])

    def _accept(self):
        try:
            self.result_draft()
        except ValueError as error:
            self.error.setText(str(error))
            return
        self.accept()

    def result_draft(self):
        return procedure_from_template(self.template.currentData(), self.procedure_id.text(), self.name.text(),
                                       version=self.version.text(), author=self.author.text(), revision_note=self.note.toPlainText())
