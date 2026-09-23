"""A row worksheet for one block, committed as one undoable document edit."""
from copy import deepcopy

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)
from PySide6.QtCore import Qt

from azeo_control_trainer.core.presentation.authoring_controls import AuthoringComboBox
from azeo_control_trainer.core.presentation.configuration_chrome import command_bar
from azeo_control_trainer.core.presentation.engineering_dialog import polish_dialog
from azeo_control_trainer.core.procedures.authoring import ProcedureDraft
from azeo_control_trainer.core.procedures.conditions import condition_clauses
from azeo_control_trainer.core.procedures.logic import validate_expression
from azeo_control_trainer.core.procedures.model import AdvisoryStep, AdvisoryProcedure
from .symbols import attach_symbols, insert_reference, resolve_symbol


class LogicEditor(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.setWindowTitle("Block logic — conditions and calculations")
        self.resize(1020, 660)
        self.draft = ProcedureDraft(deepcopy(window.draft.data), dict(window.draft.bindings), window.draft.source)
        self.tag_database = window.tag_database
        self.step_row = window._step_row
        self.step = self.draft.data["steps"][self.step_row]
        self.kind = self.step["type"]
        layout = QVBoxLayout(self)
        note = QLabel("Calculations run top to bottom. Wait blocks recalculate on each captured scan, then evaluate every condition. "
                      "A row's hold time must finish before the combined group hold begins.", wordWrap=True)
        layout.addWidget(note)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.conditions = self.table(["ID", "Condition expression", "Hold continuously (s)"])
        self.calculations = self.table(["Result memory", "Calculation expression"])
        self.match = AuthoringComboBox()
        self.match.addItems(["ALL", "ANY"])
        self.match.setCurrentText(self.step.get("condition_match", "ALL"))
        self.logic = QLineEdit(self.step.get("condition_logic", ""))
        self.logic.setPlaceholderText("Optional: C1 and (C2 or C3). Blank uses ALL / ANY.")
        self.dwell = QLineEdit(str(self.step.get("stable_for_sec", 0)))
        self.timeout = QLineEdit(str(self.step.get("timeout_sec", 60)))
        self.timer_tuning = AuthoringComboBox()
        for label, key in (("Not exposed", "none"), ("Next run", "next_run"), ("Live / next run", "live")):
            self.timer_tuning.addItem(label, key)
        self.timer_tuning.setCurrentIndex(self.timer_tuning.findData(self.step.get("timer_tuning", "none")))
        if self.kind != "calculate":
            panel = self.page(self.conditions, self.add_condition, self.remove_condition)
            form = QFormLayout()
            form.addRow("Combine rows", self.match)
            form.addRow("Grouped logic", self.logic)
            if self.kind == "wait_until":
                form.addRow("Combined hold (simulation s)", self.dwell)
                form.addRow("Overall timeout (simulation s)", self.timeout)
                form.addRow("Operator timer tuning", self.timer_tuning)
            else:
                self.conditions.hideColumn(2)
            panel.layout().addLayout(form)
            self.tabs.addTab(panel, "Conditions")
            rows = self.step.get("condition_rows", [])
            if not rows:
                match, clauses = condition_clauses(self.step.get("condition") or "False")
                self.match.setCurrentText(match)
                rows = [dict(id=f"C{i+1}", expression=clause, stable_for_sec=0) for i, clause in enumerate(clauses)]
            for row in rows:
                self.add_condition(row)
        if self.kind in {"wait_until", "calculate"}:
            self.tabs.addTab(self.page(self.calculations, self.add_calculation, self.remove_calculation), "Calculations")
            rows = self.step.get("calculation_rows", [])
            if not rows and self.kind == "calculate":
                rows = [dict(variable=self.step.get("variable") or "", expression=self.step.get("expression") or "0")]
            for row in rows:
                self.add_calculation(row)
        self.error = QLabel("", wordWrap=True)
        self.error.setTextFormat(Qt.PlainText)
        layout.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Save).setText("Apply to block")
        self.buttons.accepted.connect(self.apply)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        polish_dialog(
            self,
            title="Block logic",
            subtitle="Configure conditions, continuous hold time and ordered calculations.",
            mark="params",
        )

    @staticmethod
    def table(headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setStretchLastSection(True)
        table.verticalHeader().setDefaultSectionSize(38)
        return table

    def page(self, table, add, remove):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.addWidget(command_bar(panel, (("Add row", lambda: add(), "new", False),
                                             ("Remove row", remove, "delete", False))))
        if table is self.calculations:
            order = QHBoxLayout()
            for caption, delta in (("Move up", -1), ("Move down", 1)):
                button = QPushButton(caption)
                button.clicked.connect(lambda _checked=False, d=delta: self.move_calculation(d))
                order.addWidget(button)
            order.addStretch(1)
            layout.addLayout(order)
        layout.addWidget(table, 1)
        return panel

    def add_condition(self, value=None):
        ids = {self.conditions.item(i, 0).text() for i in range(self.conditions.rowCount())}
        number = 1
        while f"C{number}" in ids:
            number += 1
        value = value or dict(id=f"C{number}", expression="False", stable_for_sec=0)
        row = self.conditions.rowCount()
        self.conditions.insertRow(row)
        self.conditions.setItem(row, 0, QTableWidgetItem(value["id"]))
        field = QLineEdit(value["expression"])
        attach_symbols(field, "condition", self)
        self.conditions.setCellWidget(row, 1, field)
        self.conditions.setItem(row, 2, QTableWidgetItem(str(value.get("stable_for_sec", 0))))
        self.conditions.setColumnWidth(0, 90)
        self.conditions.setColumnWidth(1, 635)
        self.conditions.selectRow(row)

    def remove_condition(self):
        if self.conditions.currentRow() >= 0:
            self.conditions.removeRow(self.conditions.currentRow())

    def add_calculation(self, value=None):
        value = value or dict(variable="", expression="0")
        row = self.calculations.rowCount()
        self.calculations.insertRow(row)
        for col, key in enumerate(("variable", "expression")):
            field = QLineEdit(value[key])
            attach_symbols(field, key, self)
            self.calculations.setCellWidget(row, col, field)
        self.calculations.setColumnWidth(0, 230)
        self.calculations.selectRow(row)

    def remove_calculation(self):
        if self.calculations.currentRow() >= 0:
            self.calculations.removeRow(self.calculations.currentRow())

    def move_calculation(self, delta):
        index = self.calculations.currentRow()
        target = index + delta
        if index >= 0 and 0 <= target < self.calculations.rowCount():
            for col in range(2):
                a, b = self.calculations.cellWidget(index, col), self.calculations.cellWidget(target, col)
                old = a.text()
                a.setText(b.text())
                b.setText(old)
            self.calculations.selectRow(target)

    def insert_symbol(self, field, key, reference, draft=None):
        try:
            if draft:
                self.draft.data["variables"] = draft.data.get("variables", [])
            token = resolve_symbol(self.draft, self.tag_database, reference)
            insert_reference(field, key, token)
        except Exception as error:
            self.error.setText(str(error))

    def apply(self):
        try:
            data = dict(self.step)
            if self.kind != "calculate":
                data["condition_rows"] = [dict(id=self.conditions.item(i, 0).text().strip(),
                    expression=self.conditions.cellWidget(i, 1).text().strip(),
                    stable_for_sec=float(self.conditions.item(i, 2).text()) if self.kind == "wait_until" else 0)
                    for i in range(self.conditions.rowCount())]
                if not data["condition_rows"]:
                    raise ValueError("Add at least one condition")
                data.update(condition_match=self.match.currentText(), condition_logic=self.logic.text().strip())
            if self.kind == "wait_until":
                data.update(stable_for_sec=float(self.dwell.text()), timeout_sec=float(self.timeout.text()),
                            timer_tuning=self.timer_tuning.currentData())
            data["calculation_rows"] = [dict(variable=self.calculations.cellWidget(i, 0).text().strip(),
                expression=self.calculations.cellWidget(i, 1).text().strip()) for i in range(self.calculations.rowCount())]
            if self.kind == "calculate" and not data["calculation_rows"]:
                raise ValueError("Add at least one calculation")
            step = AdvisoryStep.model_validate(data)
            # Validate this block independently; incomplete edits in a different
            # block must not prevent the engineer from configuring this one.
            procedure = AdvisoryProcedure.model_validate(dict(procedure_id="logic_review", name="Logic review", mode="advisory",
                variables=self.draft.data.get("variables", []), tags=self.draft.data.get("tags", []), steps=[data]))
            memory = {v.name for v in procedure.variables}
            for row in step.condition_rows:
                validate_expression(row.expression, memory, procedure.declared_tag_map())
            for row in step.calculation_rows:
                if row.variable not in memory:
                    raise ValueError(f"Create or select result memory: {row.variable}")
                validate_expression(row.expression, memory, procedure.declared_tag_map())
            data.update(condition=step.condition, expression=step.expression, variable=step.variable)
            self.draft.data["steps"][self.step_row] = data
            self.accept()
        except Exception as error:
            self.error.setText(str(error))
