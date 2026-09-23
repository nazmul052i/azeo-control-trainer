"""Training sessions and loop investigation reached from Operator Live tools."""
from __future__ import annotations

from datetime import datetime
import json
import logging
import math
from pathlib import Path
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHeaderView, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
    QTextBrowser, QVBoxLayout, QWidget,
)

from azeo_control_trainer.core.presentation.authoring_style import AUTHORING_CHROME_QSS
from azeo_control_trainer.core.presentation.dialog_layout import guarded_action, scrolling_body
from azeo_control_trainer.core.presentation.flow_layout import FlowLayout
from azeo_control_trainer.core.hmi.history.analysis import response_metrics
from azeo_control_trainer.core.simulation.training import Exercise

log = logging.getLogger("operator.training")


def table(headers):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setSelectionBehavior(QTableWidget.SelectRows)
    widget.setEditTriggers(QTableWidget.NoEditTriggers)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    widget.horizontalHeader().setStretchLastSection(True)
    widget.verticalHeader().hide()
    return widget


def fill(widget, rows):
    widget.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            text = f"{value:.6g}" if isinstance(value, float) else str(value)
            widget.setItem(r, c, QTableWidgetItem(text))


def loops(station):
    return [f"{name}/{block.instance_name}"
            for name, graph in station.graphs_provider().items()
            for block in graph.blocks.values() if block.block_type == "PID"]


class TrainingDialog(QDialog):
    def __init__(self, station):
        super().__init__(station)
        self.station, self.session = station, station.training_session
        self.setWindowTitle("Training sessions — Azeo Operator Station")
        self.resize(1120, 740)
        self.setStyleSheet(AUTHORING_CHROME_QSS)
        self._baseline = ""
        self._configuration = {}
        self._inherited_paths = []
        self._review_id = ""
        root = scrolling_body(self)
        self.status = QLabel("Choose a loop, capture a starting condition, then start an exercise.")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.presentation = QComboBox()
        self.presentation.addItems(("Trainee workspace", "Instructor setup and review"))
        self.presentation.setToolTip("Presentation choice for this shared training seat; operating authority is unchanged")
        root.addWidget(self.presentation)
        self.learner = QWidget()
        learner_root = QVBoxLayout(self.learner)
        self.learner_title = QLabel()
        self.learner_title.setWordWrap(True)
        learner_root.addWidget(self.learner_title)
        self.learner_objectives = table(("Objective", "Progress"))
        self.learner_objectives.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        learner_root.addWidget(self.learner_objectives, 1)
        learner_actions = FlowLayout()
        for title, callback in (
                ("Investigate loop", lambda: station.open_loop_diagnostics(
                    self.session.exercise.loop if self.session.exercise else "")),
                ("Open trend", lambda: station.open_process_history(
                    self.session.exercise.loop.partition("/")[0] if self.session.exercise else ""))):
            button = QPushButton(title)
            button.clicked.connect(guarded_action(callback, self.status))
            learner_actions.addWidget(button)
        learner_root.addLayout(learner_actions)
        root.addWidget(self.learner, 1)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.presentation.currentIndexChanged.connect(self._presentation_changed)
        self._presentation_changed()
        exercise = QWidget()
        self.tabs.addTab(exercise, "Exercise")
        layout = QVBoxLayout(exercise)
        form = QFormLayout()
        self.saved = QComboBox()
        self.saved.addItem("New exercise", None)
        for entry in self.session.exercises():
            self.saved.addItem(entry.name, entry)
        self.saved.currentIndexChanged.connect(guarded_action(self.load_exercise, self.status))
        form.addRow("Saved exercise", self.saved)
        self.name = QLineEdit("Flow-loop troubleshooting")
        self.loop = QComboBox()
        self.loop.addItems(loops(station))
        self.input = QComboBox()
        for row in self.session.workbench.io_rows():
            if row["type"] in {"AI", "DI"}:
                path = f"{row['module']}/{row['block']}"
                self.input.addItem(path, path)
        form.addRow("Exercise name", self.name)
        form.addRow("Control loop", self.loop)
        form.addRow("Fault input", self.input)
        self.objectives = QPlainTextEdit("\n".join(Exercise().objectives))
        self.objectives.setMaximumHeight(110)
        form.addRow("Objectives (one per line)", self.objectives)
        self.baseline_label = QLabel("No starting snapshot selected")
        self.baseline_label.setWordWrap(True)
        form.addRow("Starting condition", self.baseline_label)
        layout.addLayout(form)
        row = FlowLayout()
        for name, callback in (("Capture starting condition", self.capture),
                               ("Load inherited baseline…", self.inherited_baseline),
                               ("Save exercise", self.save_exercise),
                               ("Start", self.start), ("Restart", self.restart),
                               ("Pause", self.pause), ("Resume", self.resume)):
            self.button(row, name, callback)
        layout.addLayout(row)
        fault = FlowLayout()
        fault.addWidget(QLabel("Input substitution"))
        self.value = QDoubleSpinBox()
        self.value.setRange(-1e9, 1e9)
        self.value.setDecimals(3)
        self.quality = QComboBox()
        self.quality.addItems(("GOOD", "BAD"))
        fault.addWidget(self.value)
        fault.addWidget(self.quality)
        self.button(fault, "Introduce fault", self.inject)
        self.button(fault, "Clear faults", self.session.clear_faults)
        self.button(fault, "Investigate loop", lambda: station.open_loop_diagnostics(self.loop.currentText()))
        layout.addLayout(fault)
        layout.addWidget(QLabel("Objective review — select a row and record the evidence."))
        self.review = table(("Objective", "Review", "Evidence"))
        layout.addWidget(self.review, 1)
        evidence = FlowLayout()
        self.evidence = QLineEdit()
        self.evidence.setPlaceholderText("Observation or corrective action supporting this objective")
        evidence.addWidget(self.evidence, 1)
        self.button(evidence, "Mark complete", self.complete)
        self.button(evidence, "Reopen objective", self.reopen)
        self.button(evidence, "Finish session", self.finish)
        layout.addLayout(evidence)

        timeline = QWidget()
        self.tabs.addTab(timeline, "Timeline and recorded values")
        tl = QVBoxLayout(timeline)
        tl.addWidget(QLabel("Select an event to inspect the recorded values at that simulation time."))
        split = QSplitter(Qt.Vertical)
        from azeo_control_trainer.core.pid.charts.historian_trend import HistorianTrendWidget
        self.recorded_trend = HistorianTrendWidget("Recorded session — simulation time")
        split.addWidget(self.recorded_trend)
        self.events = table(("Seconds", "Event", "Target", "Evidence"))
        self.values = table(("Parameter", "Value", "Quality", "Target mode", "Actual mode"))
        split.addWidget(self.events)
        split.addWidget(self.values)
        tl.addWidget(split)
        self.events.itemSelectionChanged.connect(guarded_action(self.inspect_event, self.status))
        reports = QWidget()
        self.tabs.addTab(reports, "Saved sessions and report")
        rl = QVBoxLayout(reports)
        self.sessions = QComboBox()
        self.sessions.currentIndexChanged.connect(guarded_action(self.review_session, self.status))
        rl.addWidget(self.sessions)
        self.report_view = QTextBrowser()
        rl.addWidget(self.report_view, 1)
        rr = FlowLayout()
        self.button(rr, "Refresh sessions", self.reload_sessions)
        self.button(rr, "Export report and data", self.export)
        rl.addLayout(rr)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(guarded_action(self.refresh, self.status))
        self._timer.start()
        self.reload_sessions()
        if self.session.exercise is not None:
            entry = self.session.exercise
            self.name.setText(entry.name)
            self.loop.setCurrentText(entry.loop)
            self.input.setCurrentText(entry.input_path)
            self.objectives.setPlainText("\n".join(entry.objectives))
            self._baseline = entry.snapshot
            self._configuration = dict(entry.configuration)
            self._inherited_paths = list(entry.paths)
            self.baseline_label.setText(Path(entry.snapshot).name)
            self._review_rows()

        from azeo_control_trainer.core.hmi.theme.widgets import bind_operator_theme
        bind_operator_theme(self, tool_controls=True)

    def apply_operator_theme(self, theme):
        self.recorded_trend.apply_operator_theme(theme)

    def button(self, layout, label, callback):
        button = QPushButton(label)
        button.clicked.connect(lambda _=False: self.run(callback))
        layout.addWidget(button)
        return button

    def _presentation_changed(self, *_):
        instructor = self.presentation.currentIndex() == 1
        self.status.setText(
            "Configure the exercise, then review the trainee's observations." if instructor else
            "Follow the objectives and use the process display, faceplates and trends to investigate.")
        self.tabs.setVisible(instructor)
        self.learner.setVisible(not instructor)
        if not instructor:
            self._refresh_learner()

    def _refresh_learner(self):
        exercise = self.session.exercise
        if exercise is None:
            self.learner_title.setText("Your instructor will select a starting condition and start the exercise.")
            return
        caps = self.session.workbench.process_capabilities()
        state = "Paused" if caps.get("paused") else "Running"
        if not self.session.active:
            state = "Finished"
        self.learner_title.setText(f"{exercise.name}\n{state} · {self.session.elapsed():.0f} s · {exercise.loop}")
        values = [(title, "Reviewed complete" if self.session.metadata.get("objectives", {}).get(str(i), {}).get("complete")
                   else "In progress") for i, title in enumerate(exercise.objectives)]
        if values != getattr(self, "_learner_rows", None):
            fill(self.learner_objectives, values)
            self._learner_rows = values
    def run(self, callback):
        try:
            result = callback()
            self.status.setText(str(result or "Ready"))
            self.refresh()
            return result
        except Exception as error:  # noqa: BLE001 - Qt event boundary
            log.exception("Training action failed")
            self.status.setText(str(error))
            return None

    def require_authority(self):
        if not self.station.settings.write_authority:
            raise ValueError("This station is view-only; operating authority is required")

    def definition(self):
        loop = self.loop.currentText()
        return Exercise(name=self.name.text().strip(), loop=loop, configuration=dict(self._configuration),
                        input_path=self.input.currentText(), snapshot=self._baseline,
                        objectives=[s.strip() for s in self.objectives.toPlainText().splitlines() if s.strip()],
                        paths=(list(self._inherited_paths) if self._configuration else [f"{loop}/CONFIG/{key}" for key in
                               ("GAIN", "RESET", "RATE", "out_lo", "out_hi")]))

    def load_exercise(self):
        exercise = self.saved.currentData()
        if exercise is None:
            self._configuration = {}
            return
        self.name.setText(exercise.name)
        self.loop.setCurrentText(exercise.loop)
        self.input.setCurrentText(exercise.input_path)
        self.objectives.setPlainText("\n".join(exercise.objectives))
        self._baseline = exercise.snapshot
        self._configuration = dict(exercise.configuration)
        self._inherited_paths = list(exercise.paths)
        self.baseline_label.setText(Path(self._baseline).name)

    def capture(self):
        self._baseline = str(self.session.capture_baseline())
        self._configuration = {}
        self.baseline_label.setText("Captured " + datetime.now().strftime("%H:%M:%S"))
        return "Starting condition captured"

    def inherited_baseline(self):
        from azeo_control_trainer.core.presentation.configuration_training import load_inherited_exercise
        load_inherited_exercise(self)

    def save_exercise(self):
        exercise = self.definition()
        self.session.save_exercise(exercise)
        self.saved.addItem(exercise.name, exercise)
        return "Exercise saved"

    def start(self):
        self.require_authority()
        self.session.start(self.definition())
        self._review_id = self.session.identity
        self._review_rows()
        return "Exercise running"

    def restart(self):
        self.require_authority()
        self.session.restart()
        self._review_id = self.session.identity
        self._review_rows()
        return "Restarted from the same starting condition"

    def pause(self):
        self.require_authority()
        self.session.workbench.pause()
        self.session.event("instructor_paused")

    def resume(self):
        self.require_authority()
        self.session.workbench.resume()
        self.session.event("instructor_resumed")

    def inject(self):
        self.require_authority()
        self.session.inject_input(self.input.currentText(), self.value.value(), self.quality.currentText())
        return "Fault introduced through input simulation"

    def _review_rows(self):
        if self.session.exercise is None:
            return
        current = self.review.currentRow()
        data = self.session.metadata.get("objectives", {})
        fill(self.review, [(name, "Complete" if data.get(str(i), {}).get("complete") else "Pending",
                            data.get(str(i), {}).get("note", ""))
                           for i, name in enumerate(self.session.exercise.objectives)])
        if current >= 0:
            self.review.selectRow(current)

    def complete(self):
        if not self.evidence.text().strip():
            raise ValueError("Record evidence before marking this objective complete")
        self.session.objective(self.review.currentRow(), True, self.evidence.text())
        self._review_rows()

    def reopen(self):
        self.session.objective(self.review.currentRow(), False, self.evidence.text())
        self._review_rows()

    def finish(self):
        self.session.finish(self.evidence.text())
        self.reload_sessions()
        self.tabs.setCurrentIndex(2)
        return "Session saved; introduced input faults have been cleared"

    def refresh(self):
        if not self.isVisible():
            return
        self._refresh_learner()
        if self.station.training_error:
            self.status.setText("Recording issue: " + self.station.training_error)
        if self._review_id and self.tabs.isVisible() and self.tabs.currentIndex() == 1:
            data = self.session.archive.read(self._review_id)
            selected = self.events.currentRow()
            self.events.blockSignals(True)
            fill(self.events, [(f"{r['time']:.2f}", r["action"], r.get("target", ""),
                                str(r.get("detail", ""))) for r in data["events"][-500:]])
            self.events.selectRow(min(max(selected, 0), self.events.rowCount() - 1))
            self.events.blockSignals(False)
            self.plot_recording(data)

    def plot_recording(self, data):
        from azeo_control_trainer.core.pid.charts.historian_trend import TrendPen
        from azeo_control_trainer.core.hmi.history.historian import PEN_COLOURS
        import numpy as np
        paths = [f"{data['exercise']['loop']}/{suffix}" for suffix in ("PV", "SP", "OUT")]
        if getattr(self, "_plotted_id", "") != self._review_id:
            self.recorded_trend.remove_all_pens()
            for i, path in enumerate(paths):
                values = [row["values"].get(path, {}).get("value") for row in data["samples"]]
                finite = [float(v) for v in values if isinstance(v, (int, float)) and math.isfinite(v)]
                lo, hi = (min(finite), max(finite)) if finite else (0, 100)
                pad = max(1, (hi - lo) * .1)
                units = next((row["values"].get(path, {}).get("units", "") for row in data["samples"]), "")
                label = f"{path.rsplit('/', 1)[-1]} · {data['exercise']['loop'].split('/')[0]}"
                self.recorded_trend.add_pen(TrendPen(path, label, units, PEN_COLOURS[i], lo - pad, hi + pad,
                                                   axis="right" if i == 2 else "left"))
            self._plotted_id = self._review_id
            self.recorded_trend.set_time_window(1)
        for path in paths:
            values = []
            for row in data["samples"]:
                point = row["values"].get(path, {})
                value = point.get("value")
                values.append(float(value) if isinstance(value, (int, float)) and point.get("quality") == "GOOD" else math.nan)
            self.recorded_trend.update_data(path, np.array([r["time"] / 60 for r in data["samples"]]), np.array(values))
        if self.recorded_trend.is_live():
            self.recorded_trend.follow_latest()

    def inspect_event(self):
        item = self.events.item(self.events.currentRow(), 0)
        if item is None or not self._review_id:
            return
        stamp = float(item.text())
        self.recorded_trend.focus_time(stamp / 60)
        data = self.session.archive.read(self._review_id)
        earlier = [row for row in data["samples"] if row["time"] <= stamp + 0.01]
        values = earlier[-1]["values"] if earlier else {}
        fill(self.values, [(path, row["value"], row["quality"], row["mode_target"], row["mode_actual"])
                           for path, row in values.items()])

    def reload_sessions(self):
        self.sessions.blockSignals(True)
        self.sessions.clear()
        for row in self.session.archive.sessions():
            stamp = datetime.fromtimestamp(row["started"]).strftime("%Y-%m-%d %H:%M")
            self.sessions.addItem(f"{stamp} — {row['title']}", row["id"])
        self.sessions.blockSignals(False)
        self.review_session()

    def review_session(self):
        identity = self.sessions.currentData()
        if identity:
            self._review_id = identity
            self.report_view.setHtml(self.session.report(identity))

    def export(self, path=None):
        identity = self.sessions.currentData() or self.session.identity
        if not identity:
            raise ValueError("Select a recorded session")
        if path is None:
            from azeo_control_trainer.core.presentation.headless import is_headless
            if is_headless():
                return None
            path, _ = QFileDialog.getSaveFileName(self, "Export training report", "training-report.html", "HTML (*.html)")
        if not path:
            return None
        target = Path(path)
        target.write_text(self.session.report(identity), encoding="utf-8")
        target.with_suffix(".json").write_text(json.dumps(self.session.archive.read(identity), indent=2), encoding="utf-8")
        return f"Saved {target.name} and recorded data"


class LoopDiagnosticsDialog(QDialog):
    def __init__(self, station, path=""):
        super().__init__(station)
        self.station = station
        self.setWindowTitle("Loop diagnosis — Azeo Operator Station")
        self.resize(1150, 760)
        self.setStyleSheet(AUTHORING_CHROME_QSS)
        self.note = QLabel("Select a response window after its setpoint change; measurements use engineering units.")
        root = QVBoxLayout(self)
        bar = FlowLayout()
        self.loop = QComboBox()
        self.loop.addItems(loops(station))
        self.loop.setCurrentText(path)
        bar.addWidget(QLabel("Loop"))
        bar.addWidget(self.loop)
        self.feedback = QLineEdit()
        self.feedback.setPlaceholderText("Optional valve feedback: MODULE/BLOCK/PARAMETER")
        bar.addWidget(self.feedback, 1)
        state_button = QPushButton("Loop state")
        state_button.setCheckable(True)
        state_button.toggled.connect(lambda checked: self.state.setVisible(checked))
        bar.addWidget(state_button)
        root.addLayout(bar)
        self.state = table(("Signal or condition", "Observed value", "Quality / meaning"))
        self.state.setMaximumHeight(150)
        self.state.hide()
        root.addWidget(self.state)
        from azeo_control_trainer.core.hmi.history.view import ProcessHistoryView
        self.trend = ProcessHistoryView(station.historian, parent=self)
        self.trend.setWindowFlags(Qt.Widget)
        root.addWidget(self.trend, 1)
        controls = FlowLayout()
        self.tolerance = QDoubleSpinBox()
        self.tolerance.setRange(0.001, 1e6)
        self.tolerance.setDecimals(3)
        self.tolerance.setValue(1)
        self.tolerance.setSuffix(" EU tolerance")
        controls.addWidget(self.tolerance)
        for label, callback in (("Begin baseline", lambda: self.begin("baseline")),
                                ("Begin trial", lambda: self.begin("trial")),
                                ("End measurement", self.end),
                                ("Compare measured runs", self.compare),
                                ("Open faceplate", self.faceplate)):
            button = QPushButton(label)
            button.clicked.connect(guarded_action(callback, self.note))
            controls.addWidget(button)
        root.addLayout(controls)
        self.metrics = table(("Measurement", "Baseline", "Trial"))
        self.metrics.setMaximumHeight(120)
        root.addWidget(self.metrics)
        self.note.setWordWrap(True)
        root.addWidget(self.note)
        self._recording = ""
        self._windows = {}
        self._last_time = None
        self.loop.currentTextChanged.connect(guarded_action(self.select_loop, self.note))
        self.feedback.editingFinished.connect(guarded_action(self.select_loop, self.note))
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(guarded_action(self.refresh, self.note))
        self._timer.start()
        self.select_loop()

        from azeo_control_trainer.core.hmi.theme.widgets import bind_operator_theme
        bind_operator_theme(self, tool_controls=True)

    def select_loop(self):
        self._recording = ""
        self._windows = {}
        self.metrics.setRowCount(0)
        paths = [f"{self.loop.currentText()}/{suffix}" for suffix in ("PV", "SP", "OUT")]
        if self.feedback.text().strip():
            paths.append(self.feedback.text().strip())
        for path in paths:
            self.station.historian.add_point(path)
        self.trend.set_pens(paths)
        self.refresh()

    def refresh(self):
        base = self.loop.currentText()
        source = self.station.live_source
        rows, results = [], {}
        for label, path in (("PV", base + "/PV"), ("SP", base + "/SP"), ("OUT", base + "/OUT"),
                            ("Output low limit", base + "/CONFIG/out_lo"),
                            ("Output high limit", base + "/CONFIG/out_hi"),
                            ("Tracking input", base + "/TRK_IN_D"),
                            ("Back calculation", base + "/BKCAL_IN"),
                            ("Configured output limits enabled", base + "/CONFIG/use_out_limits"),
                            ("Valve feedback", self.feedback.text().strip())):
            result = source.read(path) if path else None
            results[label] = result
            quality = getattr(getattr(result, "quality", None), "name", "BAD")
            value = getattr(result, "value", None)
            rows.append((label, value if quality == "GOOD" and value is not None else "—", quality if path else "No feedback selected"))
        primary = results["PV"]
        rows.append(("Reported output limit", getattr(getattr(results["OUT"], "limit", None), "name", "—"), "Runtime status"))
        rows.insert(0, ("Mode: target / actual / normal",
                        " / ".join(getattr(primary, key, "") or "—" for key in
                                   ("mode_target", "mode_actual", "mode_normal")), "Reported by the loop"))
        fill(self.state, rows)
        if self._recording:
            training = self.station.training_session
            stamp = training.workbench._sim_time() if training else time.monotonic()
            if self._last_time is None or stamp > self._last_time:
                self._windows[self._recording].append({"time": stamp,
                    **{key.lower(): getattr(results[key], "value", None) for key in ("PV", "SP", "OUT")},
                    "quality": "GOOD" if all(getattr(getattr(results[key], "quality", None), "name", "") == "GOOD"
                                                for key in ("PV", "SP", "OUT")) else "BAD"})
                self._last_time = stamp

    def begin(self, name):
        self._recording = name
        self._windows[name] = []
        self._last_time = None
        self.note.setText(f"Recording {name}; end the measurement after the response has settled.")

    def end(self):
        self.refresh()
        self._recording = ""
        measured = {name: response_metrics(rows, tolerance=self.tolerance.value())
                    for name, rows in self._windows.items()}
        def value(name, key):
            raw = measured.get(name, {}).get(key)
            return "—" if raw is None else f"{raw:.3f}" if isinstance(raw, float) and math.isfinite(raw) else str(raw)
        fill(self.metrics, [(label, value("baseline", key), value("trial", key)) for label, key in
                            (("Observed seconds", "observed_seconds"), ("Accumulated error (EU·s)", "iae"),
                             ("Overshoot (EU)", "overshoot"), ("Settling time (s)", "settling_seconds"))])
        self.note.setText(" · ".join(f"{name}: {result['note']}" for name, result in measured.items()))
        if self.station.training_session:
            self.station.training_session.event("loop_measurement", self.loop.currentText(), measured)

    def faceplate(self):
        from azeo_control_trainer.core.hmi.pvms.base import registry
        cls = registry.get("PID", "faceplate")
        if cls:
            self.station.open_faceplate(cls().place("training-loop", path=self.loop.currentText()))

    def compare(self):
        if not self._windows:
            self.note.setText("Record a baseline or trial before comparing measured runs")
            return
        from azeo_control_trainer.core.hmi.history.workspace_tools import RunComparisonDialog
        dialog = RunComparisonDialog(self.station.historian, self.trend)
        dialog.set_windows(self._windows, self.loop.currentText())
        self.trend._comparison_dialogs.append(dialog)
        dialog.show()

    def closeEvent(self, event):  # noqa: N802
        self._timer.stop()
        self.trend.close()
        super().closeEvent(event)


class AlarmInvestigationDialog(QDialog):
    """Live alarm conditions, observed sequence, and authored response guidance."""

    def __init__(self, station):
        super().__init__(station)
        self.station = station
        self.setWindowTitle("Alarm investigation — Azeo Operator Station")
        self.resize(1150, 800)
        self.setStyleSheet(AUTHORING_CHROME_QSS)
        root = QVBoxLayout(self)
        from .dialogs import AlarmListDialog
        self.summary = AlarmListDialog(station.alarm_state, station.alarm_filter,
                                       station.open_alarm_source, self,
                                       can_operate=lambda: station.settings.write_authority)
        self.summary.setWindowFlags(Qt.Widget)
        self.summary.close_button.hide()
        root.addWidget(self.summary, 2)
        self.context = QLabel("Select an alarm. Open Source reaches its existing control display and condition faceplates.")
        self.context.setWordWrap(True)
        root.addWidget(self.context)
        self.detail_tabs = QTabWidget()
        response = QWidget()
        details = QVBoxLayout(response)
        self.detail_tabs.addTab(response, "Response and conditions")
        root.addWidget(self.detail_tabs, 2)
        row = FlowLayout()
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("Reason for temporary shelving")
        self.duration = QDoubleSpinBox()
        self.duration.setRange(1, 1440)
        self.duration.setValue(10)
        self.duration.setSuffix(" min")
        row.addWidget(self.reason, 1)
        row.addWidget(self.duration)
        for label, fn in (("Shelve", self.shelve), ("Unshelve", self.unshelve)):
            button = QPushButton(label)
            button.clicked.connect(guarded_action(fn, self.context))
            row.addWidget(button)
        details.addLayout(row)
        self.timeline = table(("Observed time", "Transition", "Source", "Condition"))
        self.detail_tabs.addTab(self.timeline, "Observed events")
        condition_row = FlowLayout()
        self.conditions = QComboBox()
        condition_row.addWidget(self.conditions, 1)
        condition_button = QPushButton("Conditions")
        condition_button.clicked.connect(guarded_action(self.open_conditions, self.context))
        condition_row.addWidget(condition_button)
        trend_button = QPushButton("Trend")
        trend_button.clicked.connect(guarded_action(self.open_trend, self.context))
        condition_row.addWidget(trend_button)
        details.addLayout(condition_row)
        self.first_out = QLabel()
        self.first_out.setWordWrap(True)
        details.addWidget(self.first_out)
        guidance_label = QLabel("Response guidance: cause, consequence and corrective action")
        guidance_label.setWordWrap(True)
        details.addWidget(guidance_label)
        self.guidance = QPlainTextEdit()
        self.guidance.setMaximumHeight(100)
        details.addWidget(self.guidance, 1)
        save = QPushButton("Save response guidance")
        save.clicked.connect(guarded_action(self.save_guidance, self.context))
        details.addWidget(save)
        self._key = ""
        self._guidance = {}
        self._path = Path(station.config_root) / "_alarm_guidance.json" if station.config_root else None
        if self._path and self._path.exists():
            try:
                loaded = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._guidance = loaded
            except (ValueError, OSError):
                log.exception("Could not load alarm response guidance")
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(guarded_action(self.refresh, self.context))
        self._timer.start()
        self.summary.table.itemSelectionChanged.connect(guarded_action(self.refresh, self.context))

        from azeo_control_trainer.core.hmi.theme.widgets import bind_operator_theme
        bind_operator_theme(self, tool_controls=True)

    def refresh(self):
        record = self.summary.selected_record()
        if record is None:
            self._key = ""
            self.guidance.clear()
            self.conditions.clear()
            self.first_out.clear()
            self.timeline.setRowCount(0)
            self.context.setText("Select an alarm to investigate its source, trend and response guidance.")
            return
        if self._key != record.key:
            self._key = record.key
            self.guidance.setPlainText(str(self._guidance.get(record.key, "")))
            from azeo_control_trainer.core.hmi.pvms.base import registry
            self.conditions.clear()
            graph = self.station.graphs_provider().get(record.module)
            for block in graph.blocks.values() if graph else ():
                if any(name.startswith("FIRST_OUT") for name in block.outputs) or block.block_type in {"MOTOR_INTERLOCK", "DEVCTL", "CND"}:
                    if registry.get(block.block_type, "faceplate"):
                        self.conditions.addItem(block.instance_name, (record.module, block.instance_name, block.block_type))
        shelf = (f"Shelved for {max(0, record.shelved_until - self.station.alarm_state.clock()):.0f} s more: "
                 f"{record.shelf_reason}" if record.shelved_until else "No timed shelf")
        self.context.setText(f"{record.key} · {shelf}. Events are ordered by observation; simultaneous scan events do not establish a causal first-out.")
        rows = [row for row in self.station.alarm_state.events if row["module"] == record.module][-100:]
        fill(self.timeline, [(datetime.fromtimestamp(row["time"]).strftime("%H:%M:%S.%f")[:-3],
                              row["action"], f"{row['module']}/{row['block']}", row["condition"])
                             for row in rows])
        graph = self.station.graphs_provider().get(record.module)
        first = []
        for block in graph.blocks.values() if graph else ():
            for name in block.outputs:
                if name.startswith("FIRST_OUT"):
                    value = self.station.live_source.read(f"{record.module}/{block.instance_name}/{name}")
                    if getattr(value.quality, "name", "") == "GOOD":
                        first.append(f"{block.instance_name}.{name} = {value.value}")
        self.first_out.setText("Controller first-out: " + ("; ".join(first) or "No first-out output available in this module"))

    def open_conditions(self):
        selected = self.conditions.currentData()
        if selected is None:
            return
        from azeo_control_trainer.core.hmi.pvms.base import registry
        module, block, kind = selected
        cls = registry.get(kind, "faceplate")
        self.station.open_faceplate(cls().place("alarm-condition", path=f"{module}/{block}"))

    def open_trend(self):
        record = self.summary.selected_record()
        if record is not None:
            return self.station.open_process_history(record.module)

    def shelve(self):
        if not self.station.settings.write_authority:
            self.context.setText("This station is view-only")
            return False
        record = self.summary.selected_record()
        if record is None:
            return False
        try:
            self.station.alarm_state.shelve(record.key, self.duration.value() * 60, self.reason.text())
        except ValueError as error:
            self.context.setText(str(error))
            return False
        self.refresh()
        return True

    def unshelve(self):
        if not self.station.settings.write_authority:
            self.context.setText("This station is view-only")
            return False
        record = self.summary.selected_record()
        if record:
            self.station.alarm_state.suppress(record.key, False)
        self.refresh()

    def save_guidance(self):
        if not self._key or self._path is None:
            self.context.setText("Select an alarm in a project before saving guidance")
            return False
        try:
            from azeo_control_trainer.core.hmi.pvms.json_io import atomic_write_json
            self._guidance[self._key] = self.guidance.toPlainText().strip()
            atomic_write_json(self._path, self._guidance)
        except (OSError, ValueError) as error:
            self.context.setText(str(error))
            return False
        self.context.setText("Response guidance saved")
        return True
