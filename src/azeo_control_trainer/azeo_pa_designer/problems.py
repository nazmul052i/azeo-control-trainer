"""Live, navigable PA Designer diagnostics."""
from __future__ import annotations

from dataclasses import dataclass
import re

from pydantic import ValidationError
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from azeo_control_trainer.core.procedures.model import AdvisoryProcedure
from azeo_control_trainer.core.pa_designer.core.validator import validate_procedure_contract


@dataclass(frozen=True)
class ProcedureFinding:
    severity: str
    message: str
    location_kind: str = "procedure"
    location_id: str = ""
    property_name: str = ""

    @property
    def location(self):
        return (self.location_kind.title() + (f" {self.location_id}" if self.location_id else "")).strip()


def _finding(severity: str, message: str) -> ProcedureFinding:
    patterns = (
        (r"^Step ([^ ]+)", "step"),
        (r"^Flow node ([^ ]+)", "flow"),
        (r"^Flow edge ([^ ]+)", "edge"),
        (r"(?:tag is not declared|tag): ([A-Za-z0-9_.-]+)", "tag"),
    )
    for pattern, kind in patterns:
        match = re.search(pattern, message)
        if match:
            return ProcedureFinding(severity, message, kind, match.group(1))
    return ProcedureFinding(severity, message)


def collect_findings(draft, available_paths=None) -> list[ProcedureFinding]:
    findings: list[ProcedureFinding] = []
    try:
        procedure = AdvisoryProcedure.model_validate(draft.data)
    except ValidationError as error:
        for row in error.errors(include_url=False):
            loc = row.get("loc", ())
            kind, identity = "procedure", ""
            if len(loc) >= 2 and loc[0] == "steps" and isinstance(loc[1], int):
                kind = "step"
                steps = draft.data.get("steps", [])
                identity = steps[loc[1]].get("id", str(loc[1] + 1)) if loc[1] < len(steps) else str(loc[1] + 1)
            property_name = str(loc[-1]) if loc else ""
            findings.append(ProcedureFinding("error", row["msg"], kind, identity, property_name))
        return findings
    report = validate_procedure_contract(procedure)
    findings.extend(_finding("error", message) for message in report.errors)
    findings.extend(_finding("warning", message) for message in report.warnings)
    declared = {row.get("tag") for row in draft.data.get("tags", [])}
    for tag in sorted(declared - set(draft.bindings)):
        findings.append(ProcedureFinding("error", "Declared tag has no project parameter mapping.", "tag", tag, "binding"))
    if available_paths is not None:
        from azeo_control_trainer.core.procedures.authoring import catalog_resolves
        for tag, path in sorted(draft.bindings.items()):
            if not catalog_resolves(path, available_paths):
                findings.append(ProcedureFinding("error", f"Project parameter is missing: {path}", "tag", tag, "binding"))
    try:
        draft.definition(available_paths)
    except (ValueError, OSError) as error:
        message = str(error).splitlines()[0]
        if message and all(message not in finding.message for finding in findings):
            findings.append(_finding("error", message))
    return findings


class ProblemsPanel(QWidget):
    finding_activated = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.summary = QLabel("No diagnostics")
        bar.addWidget(self.summary, 1)
        self.filter = QComboBox()
        self.filter.addItems(["Errors and warnings", "Errors only", "Warnings only"])
        self.filter.currentIndexChanged.connect(self._render)
        bar.addWidget(self.filter)
        layout.addLayout(bar)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Severity", "Location", "Message"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 90)
        self.table.setColumnWidth(1, 180)
        self.table.cellDoubleClicked.connect(self._activate)
        layout.addWidget(self.table, 1)
        self.findings = []

    def set_findings(self, findings):
        self.findings = list(findings)
        errors = sum(row.severity == "error" for row in self.findings)
        warnings = sum(row.severity == "warning" for row in self.findings)
        self.summary.setText(f"{errors} errors · {warnings} warnings")
        self._render()

    def _visible(self):
        mode = self.filter.currentIndex()
        return [row for row in self.findings if mode == 0 or (mode == 1 and row.severity == "error") or (mode == 2 and row.severity == "warning")]

    def _render(self, *_):
        rows = self._visible()
        self.table.setRowCount(len(rows))
        for index, finding in enumerate(rows):
            severity = QTableWidgetItem(finding.severity.title())
            severity.setForeground(QColor("#C62828" if finding.severity == "error" else "#9A6700"))
            severity.setData(Qt.UserRole, finding)
            self.table.setItem(index, 0, severity)
            self.table.setItem(index, 1, QTableWidgetItem(finding.location))
            self.table.setItem(index, 2, QTableWidgetItem(finding.message))

    def _activate(self, row, _column):
        item = self.table.item(row, 0)
        if item:
            self.finding_activated.emit(item.data(Qt.UserRole))
