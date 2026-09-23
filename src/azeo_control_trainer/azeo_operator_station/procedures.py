"""Advisory procedures live beside the existing operating tools and faceplates."""
from __future__ import annotations

import logging
from pathlib import Path
import uuid

import yaml
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHeaderView, QLabel, QLineEdit, QPushButton, QSizePolicy, QTabWidget,
    QTextBrowser, QVBoxLayout, QWidget, QInputDialog,
)

from azeo_control_trainer.core.pa_designer.core.yaml_loader import load_bounded_yaml_file
from azeo_control_trainer.core.pa_designer.reports import build_run_report, export_json_report, export_markdown_report
from azeo_control_trainer.core.pa_designer.training.run_compare import compare_runs, format_run_comparison
from azeo_control_trainer.core.presentation.authoring_style import AUTHORING_CHROME_QSS
from azeo_control_trainer.core.presentation.dialog_layout import guarded_action, scrolling_body
from azeo_control_trainer.core.presentation.flow_layout import FlowLayout
from azeo_control_trainer.core.presentation.headless import is_headless
from azeo_control_trainer.core.procedures.model import load_definition, loop_verification
from azeo_control_trainer.core.procedures.authoring import library_documents
from azeo_control_trainer.core.procedures.reports import format_report

from .training import fill, loops, table

log = logging.getLogger("operator.procedures")


class ProceduresDialog(QDialog):
    def __init__(self, station):
        super().__init__(station)
        self.station = station
        self.library = Path(station.simulation_service.project_dir) / "procedures"
        self.session = station.procedure_session()
        self.definition = None
        self._observation = None
        self._context = None
        self._runtime_identity = None
        self._prompt = None
        self._closed = False
        self._last_finished = ""
        self.setWindowTitle("Procedures — Azeo Operator Station")
        self.resize(1000, 760)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setStyleSheet(AUTHORING_CHROME_QSS)
        root = scrolling_body(self)
        intro = QLabel("Advisory procedures · Operate through the faceplate; each required condition is verified from controller readback.")
        intro.setWordWrap(True)
        root.addWidget(intro)
        self.status = QLabel("Select a procedure and wait for completed controller scans.")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.clock = QLabel()
        self.clock.setWordWrap(True)
        root.addWidget(self.clock)
        self.proposal = QLabel()
        self.proposal.setWordWrap(True)
        self.proposal.hide()
        root.addWidget(self.proposal)
        self.active_summary = QLabel("No active procedures")
        root.addWidget(self.active_summary)
        self.active_picker = QComboBox()
        self.active_picker.setMinimumContentsLength(16)
        self.active_picker.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.active_picker.setAccessibleName("Select an active procedure run")
        self.active_picker.hide()
        root.addWidget(self.active_picker)
        self._active_rows = ()
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)
        execution = QWidget()
        layout = QVBoxLayout(execution)
        self.tabs.addTab(execution, "Procedure")
        self.selection = QWidget()
        selection = QFormLayout(self.selection)
        selection.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.procedure = QComboBox()
        selection.addRow("Procedure", self.procedure)
        self.template = QWidget()
        form = QFormLayout(self.template)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        form.setContentsMargins(0, 0, 0, 0)
        self.loop = QComboBox()
        self.loop.addItems(loops(station))
        form.addRow("Control loop", self.loop)
        self.target = self.number(-1e9, 1e9, 0, " EU")
        self.tolerance = self.number(0.001, 1e9, 1, " EU")
        self.dwell = self.number(0, 86400, 10, " simulation seconds")
        form.addRow("Target setpoint", self.target)
        form.addRow("PV tolerance", self.tolerance)
        form.addRow("Observed dwell", self.dwell)
        selection.addRow(self.template)
        layout.addWidget(self.selection)
        self.library_buttons = QWidget()
        library_actions = FlowLayout(self.library_buttons)
        self.button(library_actions, "Refresh library", self.refresh_library)
        self.save_template = self.button(library_actions, "Save template to project", self.save_to_project)
        layout.addWidget(self.library_buttons)
        self.steps = table(("Step", "Instruction", "State"))
        self.steps.setMinimumHeight(150)
        self.steps.setWordWrap(True)
        self.steps.horizontalHeader().setStretchLastSection(False)
        self.steps.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.steps.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        layout.addWidget(self.steps, 1)
        self.instruction = QLabel()
        self.instruction.setWordWrap(True)
        layout.addWidget(self.instruction)
        self.prompt_panel = QWidget()
        prompt_layout = QVBoxLayout(self.prompt_panel)
        self.prompt_text = QLabel()
        self.prompt_text.setWordWrap(True)
        prompt_layout.addWidget(self.prompt_text)
        self.answer = QLineEdit()
        self.answer.setPlaceholderText("Operator response")
        prompt_layout.addWidget(self.answer)
        self.choices = QComboBox()
        prompt_layout.addWidget(self.choices)
        response_actions = FlowLayout()
        self.submit = self.button(response_actions, "Confirm", self.submit_answer)
        self.skip_button = self.button(response_actions, "Skip step with reason", self.skip_step)
        self.skip_button.hide()
        self.decline = self.button(response_actions, "Decline / abort", self.abort)
        prompt_layout.addLayout(response_actions)
        layout.addWidget(self.prompt_panel)
        self.prompt_panel.hide()
        actions = FlowLayout()
        self.start_button = self.button(actions, "Start procedure", self.start_run)
        self.pause_button = self.button(actions, "Pause procedure", self.toggle_pause)
        self.break_button = self.button(actions, "Break with reason", self.break_run)
        self.abort_button = self.button(actions, "Abort procedure", self.abort)
        layout.addLayout(actions)
        operating = FlowLayout()
        self.equipment = QComboBox()
        self.equipment.setToolTip("Equipment referenced by the selected procedure")
        operating.addWidget(self.equipment)
        self.faceplate_button = self.button(operating, "Open faceplate", self.open_faceplate)
        if station.training_session is not None:
            self.button(operating, "Training sessions", station.open_training)
        layout.addLayout(operating)
        self.values = table(("Logical tag", "Controller parameter", "Value", "Quality"))
        live = QWidget()
        live_layout = QVBoxLayout(live)
        live_layout.addWidget(self.values)
        self.tabs.addTab(live, "Live values")
        from .procedure_workflow import ProcedureWorkflow
        self.workflow = ProcedureWorkflow(station, self.session)
        self.tabs.addTab(self.workflow, "Workflow")
        history = QWidget()
        history_layout = QVBoxLayout(history)
        self.tabs.addTab(history, "Run history")
        self.history = table(("Started (UTC)", "Procedure", "Status", "Run ID"))
        self.history.setSelectionMode(QAbstractItemView.ExtendedSelection)
        history_layout.addWidget(self.history)
        history_actions = FlowLayout()
        self.button(history_actions, "Refresh history", self.refresh_history)
        self.button(history_actions, "Review selected run", self.review)
        self.button(history_actions, "Compare two selected runs", self.compare)
        self.button(history_actions, "Export selected run", self.export)
        history_layout.addLayout(history_actions)
        self.report = QTextBrowser()
        self.report.setOpenExternalLinks(False)
        history_layout.addWidget(self.report, 1)
        for control in (self.procedure, self.loop, self.equipment):
            control.setMinimumContentsLength(12)
            control.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.procedure.currentIndexChanged.connect(guarded_action(self.select_procedure, self.status))
        self.active_picker.currentIndexChanged.connect(guarded_action(self.focus_active_run, self.status))
        self.loop.currentTextChanged.connect(guarded_action(self.select_loop, self.status))
        for control in (self.target, self.tolerance, self.dwell):
            control.valueChanged.connect(guarded_action(self.select_procedure, self.status))
        self.session.events.connect(self.consume_events)
        self.refresh_library()
        self.select_loop()
        self.refresh_history()
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        # Opening a faceplate hides the procedure tab. Its execution still
        # needs observations; workspace paint timers normally stop when hidden.
        self.timer.setProperty("keepRunningWhenHidden", True)
        self.timer.timeout.connect(lambda: self.poll(observe=False))
        self.timer.start()
        self.poll()

    @property
    def run(self):
        return self.session.run

    @property
    def observations(self):
        return self.session.observations

    @staticmethod
    def number(low, high, value, suffix):
        widget = QDoubleSpinBox()
        widget.setDecimals(3)
        widget.setRange(low, high)
        widget.setValue(value)
        widget.setSuffix(suffix)
        return widget

    def button(self, layout, text, callback):
        button = QPushButton(text)
        button.clicked.connect(guarded_action(callback, self.status))
        layout.addWidget(button)
        return button

    def refresh_library(self):
        selected_path = self.procedure.currentData()
        self.procedure.blockSignals(True)
        self.procedure.clear()
        self.procedure.addItem("Built-in: verify a flow loop", None)
        self.procedure.setItemData(0, "built-in", Qt.UserRole + 1)
        errors = []
        for path in library_documents(self.library):
            try:
                payload = load_bounded_yaml_file(path, label="Procedure library")
                if isinstance(payload, dict) and "mappings" in payload:
                    continue
                definition = load_definition(path, self.library)
                from azeo_control_trainer.core.procedures.governance import read_governance
                state = read_governance(path)["status"]
                self.procedure.addItem(
                    f"{definition.procedure.name} · {state.upper()} · {path.relative_to(self.library)}",
                    str(path),
                )
                self.procedure.setItemData(self.procedure.count() - 1, state, Qt.UserRole + 1)
            except Exception as error:
                errors.append(f"{path.name}: {error}")
        self.procedure.blockSignals(False)
        if selected_path is not None:
            index = self.procedure.findData(selected_path)
            if index >= 0:
                self.procedure.setCurrentIndex(index)
        self.select_procedure()
        if errors:
            self.status.setText("Library documents unavailable: " + " | ".join(errors))

    def select_loop(self):
        row = self.station.live_source.read(self.loop.currentText() + "/SP")
        if isinstance(row.value, (int, float)):
            self.target.setValue(row.value)
        self.select_procedure()

    def select_procedure(self):
        self.definition = None
        path = self.procedure.currentData()
        ref = str(Path(path).relative_to(self.library)).replace("\\", "/") if path else ""
        self.template.setVisible(path is None)
        self.save_template.setEnabled(path is None)
        try:
            running = self.session._contexts.get(ref)
            self.definition = (running.definition if running and running.run.active else
                load_definition(Path(path), self.library) if path else
                loop_verification(self.loop.currentText(), self.target.value(),
                                  self.tolerance.value(), self.dwell.value()))
        except ValueError as error:
            self.status.setText(str(error))
            self.steps.setRowCount(0)
            return
        self.session.select(self.definition, ref)
        self.definition = self.session.definition
        self._prompt = None
        self.prompt_panel.hide()
        fill(self.steps, [(step.id, step.description or step.type, "Pending")
                          for step in self.definition.procedure.steps])
        self.equipment.clear()
        self.equipment.addItems(sorted({"/".join(path.split("/")[:2])
                                       for path in self.definition.bindings.values()}))
        state = self.procedure.currentData(Qt.UserRole + 1) or "draft"
        prefix = "Built-in" if state == "built-in" else state.title()
        self.status.setText(
            f"{prefix} procedure selected. Start after reviewing the current operating condition."
        )

    def save_to_project(self):
        if self.definition is None or self.run.active:
            raise ValueError("Select a valid template first")
        # A unique revision directory publishes the mapping before the procedure
        # becomes discoverable and never overwrites an authored document.
        folder = self.library / ("loop-verification-" + uuid.uuid4().hex[:12])
        folder.mkdir(parents=True)
        payload = self.definition.procedure.model_dump(mode="json", exclude_defaults=True)
        payload["connectivity"] = {"mapping_path": "mapping.yaml"}
        mapping = {"mappings": [{"logical_tag": tag, "connector_tag": path}
                                for tag, path in self.definition.bindings.items()]}
        (folder / "mapping.yaml").write_text(yaml.safe_dump(mapping, sort_keys=False), encoding="utf-8")
        path = folder / "procedure.yaml"
        temporary = folder / "procedure.tmp"
        temporary.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
        temporary.replace(path)
        self.refresh_library()
        self.procedure.setCurrentIndex(self.procedure.findData(str(path)))
        self.status.setText(f"Saved {path}")
        return path

    def poll(self, observe=True):
        if self._closed:
            return
        try:
            self._poll(observe)
        except Exception as error:  # Never let a live-source failure escape a Qt timer.
            log.exception("Procedure observation failed")
            self.run.control.stop(f"Station observation failed: {error}")
            self.start_button.setEnabled(False)
            self.status.setText(str(error))
            self.consume_events()

    def _poll(self, observe=True):
        if observe:
            self.session.poll()
        self.sync_active_runs()
        self.definition = self.session.definition
        self.workflow.load(self.definition)
        self.workflow.update_state()
        observation = self.session.observation
        self._observation = observation
        if self.definition is not None and observation is not None:
            fill(self.values, [(tag, self.definition.bindings[tag], sample.value, sample.quality)
                               for tag, sample in observation.samples.items()])
            self.clock.setText(f"Simulation {observation.sim_time:.1f} s · {observation.detail}")
        if self.steps.rowCount() != len(self.definition.procedure.steps if self.definition else ()):
            fill(self.steps, [(step.id, step.description or step.type,
                              self.session.step_states.get(step.id, "Pending"))
                             for step in self.definition.procedure.steps])
        if self.session.prompt is not None and self._prompt != self.session.prompt:
            self.consume_events([self.session.prompt])
        elif self.session.prompt is None:
            self._prompt = None
            self.prompt_panel.hide()
        self._last_finished = self.session.last_finished
        active = self.run.active
        self.selection.setVisible(True)
        self.library_buttons.setVisible(True)
        self.selection.setEnabled(True)
        self.library_buttons.setEnabled(True)
        self.save_template.setEnabled(self.procedure.currentData() is None and not active)
        self.start_button.setEnabled(bool(self.definition and self._observation and
                                         self._observation.ready and self.session.can_start(self.session.ref)
                                         and self.station.settings.write_authority))
        self.start_button.setToolTip(self.session._start_error(self.session.ref))
        self.pause_button.setEnabled(active and self.station.settings.write_authority)
        self.break_button.setEnabled(active and not self.run.control.is_paused
                                     and self.station.settings.write_authority)
        self.pause_button.setText("Resume procedure" if self.run.control.is_paused else "Pause procedure")
        self.abort_button.setEnabled(active and self.station.settings.write_authority)
        self.submit.setEnabled(bool(self.session.prompt and self._observation and self._observation.ready
                                    and not self.run.control.is_paused and self.station.settings.write_authority))
        self.skip_button.setEnabled(self.submit.isEnabled())
        self.decline.setEnabled(active and self.station.settings.write_authority)
        self.faceplate_button.setEnabled(bool(
            self.equipment.count() and self.station.current_binding_engine() is not None))
        self.consume_events()

    def sync_active_runs(self):
        rows = tuple((ref, context.run.run_id, context.status, context.definition.procedure.name)
                     for ref, context in self.session.active_runs)
        if rows == self._active_rows:
            return
        self._active_rows = rows
        self.active_summary.setText(f"Active procedures: {len(rows)}" if rows else "No active procedures")
        selected_ref = self.active_picker.currentData()
        self.active_picker.blockSignals(True)
        self.active_picker.clear()
        if rows:
            self.active_picker.addItem("Select active procedure…", None)
        for ref, run_id, status, name in rows:
            self.active_picker.addItem(f"{name} · {status} · {run_id[:8]}", ref)
        index = self.active_picker.findData(selected_ref) if selected_ref is not None else -1
        self.active_picker.setCurrentIndex(index if index >= 0 else 0)
        self.active_picker.blockSignals(False)
        self.active_picker.setVisible(bool(rows))

    def focus_active_run(self):
        ref = self.active_picker.currentData()
        if ref is None:
            return
        path = str(self.library / ref) if ref else None
        index = self.procedure.findData(path)
        if index >= 0:
            if index == self.procedure.currentIndex():
                self.session.focus(ref)
                self.select_procedure()
            else:
                self.procedure.setCurrentIndex(index)
        else:
            self.session.focus(ref)
            self.definition = self.session.definition
        self.poll(observe=False)

    def start_run(self):
        self.session.start()
        self.poll()

    def show_workflow(self, ref=None):
        if ref:
            path = str(self.library / ref)
            index = self.procedure.findData(path)
            if index >= 0:
                self.procedure.setCurrentIndex(index)
            else:
                self.session.focus(ref)
        definition = self.session.prepare(ref) if ref else self.session.definition
        self.workflow.load(definition)
        self.tabs.setCurrentWidget(self.workflow)
        self.workflow.locate_current()

    def consume_events(self, events=()):
        if self._closed:
            return
        self.definition = self.session.definition
        for event in events:
            if event.get("run_id") != self.run.run_id:
                continue
            kind = event["kind"]
            if kind == "started":
                self.status.setText(f"Running {event['name']} · {event['run_id'][:8]}")
            elif kind == "step":
                for index, step in enumerate(self.definition.procedure.steps):
                    if step.id == event["step"]:
                        if self.steps.item(index, 2) is None:
                            fill(self.steps, [(s.id, s.description or s.type, "Pending") for s in self.definition.procedure.steps])
                        self.steps.item(index, 2).setText(event["status"].title())
                        if event["status"] == "ACTIVE":
                            self.steps.selectRow(index)
                            self.instruction.setText(step.description or step.type)
                            self.instruction.show()
                            if step.type in {"write_tag", "ramp_tag"}:
                                request = (
                                    f"{step.tag} = {step.value}"
                                    if step.type == "write_tag"
                                    else f"{step.tag}: {step.start} to {step.end} at {step.rate_per_sec}/s"
                                )
                                self.proposal.setText(
                                    f"Governed output request: {request}. "
                                    "Authorization, checked write and actual feedback are required."
                                )
                                self.proposal.show()
                            else:
                                self.proposal.hide()
            elif kind == "progress":
                self.status.setText(f"{event['step']}: {event['elapsed']:.1f} simulation seconds elapsed · "
                                    f"observed stable {event['stable']:.1f} / {event['dwell']:g} s")
            elif kind == "prompt":
                self._prompt = event
                self._prompt_token = self.session.token()
                self.instruction.hide()
                text = event["prompt"]
                if event["prompt_kind"] == "output":
                    schedule = event.get("schedule")
                    detail = (
                        f"{event['path']}: {schedule['start']} to {schedule['end']} "
                        f"at {schedule['rate_per_sec']}/s"
                        if schedule else f"{event['path']} = {event.get('value')!r}"
                    )
                    text += f"\n\nChecked output: {detail}"
                elif event["prompt_kind"] == "hmi_window":
                    text += f"\n\nOpen: {event['target']}"
                if event.get("response_timeout_sec") is not None:
                    text += f"\n\nResponse timeout: {event['response_timeout_sec']:g} simulation seconds"
                self.prompt_text.setText(text)
                self.answer.clear()
                if event["prompt_kind"] == "input":
                    initial = event["step"].get("value", "")
                    self.answer.setText(str(initial).lower() if type(initial) is bool else str(initial))
                self.choices.clear()
                choices = event["step"].get("choices", [])
                self.choices.addItems([str(value) for value in choices])
                confirmation = event["prompt_kind"] in {"confirm", "output", "hmi_window"}
                skip_gate = event["prompt_kind"] == "skip_gate"
                self.choices.setVisible(bool(choices) and not confirmation)
                self.answer.setVisible(not choices and not confirmation and not skip_gate)
                self.skip_button.setVisible(skip_gate)
                self.submit.setText("Continue step" if skip_gate else "Confirm" if confirmation else "Submit response")
                self.prompt_panel.show()
            elif kind == "message":
                self.instruction.setText(str(event["message"].get("message", "")))
            elif kind == "finished":
                self._last_finished = event["status"]
                self.status.setText(f"{event['status'].title()}: {event['message']}")
                self._prompt = None
                self.prompt_panel.hide()
                self.refresh_history()
        # Session events also arrive from its own timer, between dialog polls.
        # Enable a newly displayed prompt in the same callback that paints it.
        observation = self.session.observation
        self.submit.setEnabled(bool(self.session.prompt and observation and observation.ready
                                    and not self.run.control.is_paused and self.station.settings.write_authority))
        self.skip_button.setEnabled(self.submit.isEnabled())
        self.workflow.update_state()
    def submit_answer(self):
        if self._prompt is None:
            return
        if self.run.control.is_paused or self.run.control.host_paused:
            raise ValueError("Resume the procedure and simulation before responding")
        event = self._prompt
        if event["prompt_kind"] == "comment" and not self.answer.text().strip():
            raise ValueError("Enter an observation before submitting the run comment")
        if event["prompt_kind"] == "hmi_window":
            self.session.open_hmi_target(event.get("target", ""), self.session.ref)
        value = ({"decision": "continue"} if event["prompt_kind"] == "skip_gate" else
                 True if event["prompt_kind"] in {"confirm", "output", "hmi_window"} else
                 self.choices.currentText() if event["step"].get("choices") else self.answer.text())
        self.session.execute("respond", self._prompt_token, value)
        self._prompt = None
        self.prompt_panel.hide()

    def skip_step(self):
        if self._prompt is None or self._prompt.get("prompt_kind") != "skip_gate":
            raise ValueError("No skippable step is awaiting an operator decision")
        if is_headless():
            return
        reason, accepted = QInputDialog.getText(self, "Skip step", "Reason for skipping this step")
        if not accepted:
            return
        self.session.execute("respond", self._prompt_token,
                             {"decision": "skip", "reason": reason})
        self._prompt = None
        self.prompt_panel.hide()

    def toggle_pause(self):
        self.session.execute("resume" if self.run.control.is_paused else "pause", self.session.token())
        self.poll()

    def break_run(self):
        if is_headless():
            return
        reason, accepted = QInputDialog.getText(self, "Break procedure", "Reason for the break")
        if accepted:
            self.session.execute("break", self.session.token(), reason)
            self.poll()

    def abort(self):
        self.session.execute("abort", self.session.token())
        self.status.setText("Aborting procedure…")

    def open_faceplate(self):
        from azeo_control_trainer.core.hmi.pvms.base import registry
        path = self.equipment.currentText()
        module, _, block_name = path.partition("/")
        graph = self.station.graphs_provider().get(module)
        block = next((block for block in graph.blocks.values() if block.instance_name == block_name), None) if graph else None
        cls = registry.get(block.block_type, "faceplate") if block else None
        if cls is None:
            raise ValueError(f"No installed faceplate for {path}")
        active = self.run.active
        if active:
            self.run.control.suspend_for_presentation()
            self.run.emit("presentation_paused", equipment=path)
        try:
            self.station.open_faceplate(cls().place("procedure-equipment", path=path))
        finally:
            if active:
                self.observations.wait_for_scan()
                self.poll()

    def refresh_history(self):
        rows = self.run.store.recent_runs(100)
        fill(self.history, [(row["started_at"], row["name"], row["status"], row["run_id"])
                            for row in rows])

    def selected_runs(self, count=1):
        rows = sorted({index.row() for index in self.history.selectedIndexes()})
        if len(rows) != count:
            raise ValueError(f"Select {count} run{'s' if count != 1 else ''} in the history table")
        return [self.history.item(row, 3).text() for row in rows]

    def review(self):
        run_id, = self.selected_runs()
        self.report.setPlainText(format_report(build_run_report(self.run.store, run_id)))

    def compare(self):
        left, right = self.selected_runs(2)
        self.report.setPlainText(format_run_comparison(compare_runs(self.run.store, left, right)))

    def export(self, path=None):
        run_id, = self.selected_runs()
        if path is None:
            if is_headless():
                raise ValueError("Specify a report path when running headless")
            path, _ = QFileDialog.getSaveFileName(self, "Export procedure run", f"procedure-{run_id[:8]}.json",
                                                "JSON report (*.json);;Markdown report (*.md)")
        if not path:
            return None
        export = export_markdown_report if Path(path).suffix.lower() == ".md" else export_json_report
        result = export(self.run.store, run_id, path)
        self.status.setText(f"Exported {result}")
        return result

    def shutdown(self):
        # This is a presentation. Only station shutdown owns the worker/audit.
        self.timer.stop()
        if not self._closed:
            self.session.events.disconnect(self.consume_events)
        self._closed = True
        return True

    def reject(self):
        if self.shutdown():
            super().reject()

    def closeEvent(self, event):  # noqa: N802
        if self.shutdown():
            super().closeEvent(event)
        else:
            event.ignore()
