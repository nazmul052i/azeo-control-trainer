"""A sequence editor and clock over Studio's isolated TEST data overlay."""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import time
from datetime import datetime, timezone

from azeo_control_trainer.core.presentation.authoring_controls import AuthoringComboBox
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QHBoxLayout, QHeaderView, QLabel, QLineEdit, QTableWidget, QTableWidgetItem

from azeo_control_trainer.core.hmi.binding.result import UNRESOLVED
from azeo_control_trainer.core.hmi.pvms.engineering import document_digest
from .engineering_tools import EngineeringDialog
from .studio.sequences import STATES, alarm_sequence, sequence_values, validate_steps
from .studio.test_data import TestDataPane


class SequenceDialog(EngineeringDialog):
    def run(self, fn):
        try:
            return fn()
        except Exception as error:  # noqa: BLE001 - a failed timer must stop, not log forever
            self.stop()
            logging.getLogger("graphics.sequences").exception("Visual sequence failed")
            self.status.setText(str(error))
            return None

    def __init__(self, studio):
        super().__init__(studio, "Visual state sequences")
        self._restore = None
        self.elapsed = 0.0
        self.steps = []
        self.evidence = []
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(lambda: self.run(self.tick))
        studio.modeChanged.connect(self.mode_changed)
        top = QHBoxLayout()
        self.saved = AuthoringComboBox()
        self.saved.addItem("Choose saved sequence…")
        self.saved.addItems([one["name"] for one in studio.display.test_sequences])
        self.saved.activated.connect(lambda *_: self.run(self.load))
        top.addWidget(self.saved)
        self.name = QLineEdit("Alarm lifecycle")
        top.addWidget(self.name)
        self.button(top, "Save sequence", self.save, primary=True)
        self.root.addLayout(top)
        row = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setPlaceholderText("MODULE/BLOCK/PARAMETER")
        row.addWidget(self.path, 1)
        self.button(row, "Use selection", self.use_selected)
        self.button(row, "Add alarm lifecycle", self.add_lifecycle)
        self.button(row, "Add step", self.add_step)
        self.button(row, "Remove selected", self.remove_step)
        self.root.addLayout(row)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(("At (s)", "Parameter", "Value", "Quality", "State", "Ramp to value", "Progress"))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.setColumnWidth(1, 245)
        self.table.setColumnWidth(4, 155)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.root.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.button(row, "Play", self.play)
        self.button(row, "Pause", self.pause)
        self.button(row, "Next step", self.step)
        self.button(row, "Stop / restore", self.stop)
        self.button(row, "Capture visual evidence", self.capture)
        self.root.addLayout(row)
        self.note = QLineEdit()
        self.note.setPlaceholderText("Review note for the captured state")
        self.root.addWidget(self.note)
        self.capture_label = QLabel()
        self.capture_label.setMaximumHeight(150)
        self.root.addWidget(self.capture_label)
        self.status.setText("Run state changes and PV ramps on this canvas in TEST. Stop restores previous overrides. Captures are review evidence, not an automatic pass.")

    def use_selected(self):
        for item in self.studio.selection.snapshot().items:
            binding = getattr(item, "binding", None)
            path = str(getattr(binding, "path", ""))
            if path:
                self.path.setText(path)
                return path

    def add_lifecycle(self):
        self.stop()
        for step in alarm_sequence(self.path.text().strip()):
            self.add_step(step)

    def add_step(self, step=None):
        self.stop()
        step = step or dict(at=self.table.rowCount() * 2, path=self.path.text(), value=50)
        row = self.table.rowCount()
        self.table.insertRow(row)
        for column, value in enumerate((step.get("at", 0), step.get("path", ""), step.get("value", 50))):
            self.table.setItem(row, column, QTableWidgetItem(str(value)))
        for column, choices, value in ((3, ("GOOD", "UNCERTAIN", "BAD"), step.get("quality", "GOOD")),
                                        (4, STATES, step.get("state", "Normal")), (5, ("Step", "Ramp"), "Ramp" if step.get("ramp") else "Step")):
            box = AuthoringComboBox()
            box.addItems(choices)
            box.setCurrentText(value)
            self.table.setCellWidget(row, column, box)
        cell = QTableWidgetItem("Not run")
        cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(row, 6, cell)

    def remove_step(self):
        self.stop()
        for row in sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(row)

    def read_steps(self):
        steps = []
        for row in range(self.table.rowCount()):
            steps.append(dict(at=float(self.table.item(row, 0).text()), path=self.table.item(row, 1).text(),
                              value=TestDataPane._scalar(self.table.item(row, 2).text()),
                              quality=self.table.cellWidget(row, 3).currentText(), state=self.table.cellWidget(row, 4).currentText(),
                              ramp=self.table.cellWidget(row, 5).currentText() == "Ramp"))
        return validate_steps(steps)

    def save(self):
        self.stop()
        name = self.name.text().strip()
        if not name:
            raise ValueError("Enter a sequence name")
        steps = self.read_steps()
        self.editable()
        self.studio.checkpoint()
        sequence = dict(name=name, steps=steps, evidence=copy.deepcopy(self.evidence))
        self.studio.display.test_sequences = [one for one in self.studio.display.test_sequences if one["name"] != name] + [sequence]
        self.studio.mark_unsaved()
        if self.saved.findText(name) < 0:
            self.saved.addItem(name)
        self.status.setText("Sequence stored in the display. Save the display to retain it on disk.")

    def load(self):
        self.stop()
        sequence = next((one for one in self.studio.display.test_sequences if one["name"] == self.saved.currentText()), None)
        if sequence:
            self.name.setText(sequence["name"])
            self.table.setRowCount(0)
            for step in sequence["steps"]:
                self.add_step(step)
            self.evidence = copy.deepcopy(sequence.get("evidence", []))

    def begin(self):
        if self._restore is not None:
            return
        steps = self.read_steps()
        for path in {step["path"] for step in steps}:
            if self.studio.preview_source.source.read(path) is UNRESOLVED:
                raise ValueError(f"Unresolved source: {path}")
        self.steps = steps
        self._restore = (self.studio.test_mode, copy.deepcopy(self.studio.preview_source.overrides))
        self.studio.set_test_mode(True)
        self.table.setEnabled(False)
        self.elapsed = 0.0
        self.apply_time(0)

    def play(self):
        self.begin()
        self._started = time.monotonic() - self.elapsed
        self.timer.start()

    def pause(self):
        self.timer.stop()

    def tick(self):
        if not self.studio.test_mode:
            self.stop()
            return
        self.apply_time(min(time.monotonic() - self._started, self.steps[-1]["at"]))
        if self.elapsed >= self.steps[-1]["at"]:
            self.pause()
            self.status.setText("Sequence complete. Inspect/capture the final state, then Stop to restore.")

    def apply_time(self, elapsed):
        self.elapsed = elapsed
        values = sequence_values(self.steps, elapsed)
        original = self._restore[1]
        self.studio.preview_source.overrides = {**copy.deepcopy(original), **values}
        self.studio.engine.poll()
        self.studio.canvas.viewport().update()
        for row in range(self.table.rowCount()):
            at = float(self.table.item(row, 0).text())
            self.table.item(row, 6).setText("Applied" if at <= elapsed else "Pending")
        self.status.setText(f"TEST · {elapsed:.2f} / {self.steps[-1]['at']:g} s · {len(values)} preview tags")

    def step(self):
        self.pause()
        fresh = self._restore is None
        self.begin()
        if not fresh:
            self.apply_time(next((step["at"] for step in self.steps if step["at"] > self.elapsed), self.steps[-1]["at"]))

    def mode_changed(self, _mode):
        if self._restore is not None and not self.studio.test_mode:
            self.stop(restore_mode=False)

    def stop(self, *, restore_mode=True):
        self.timer.stop()
        if self._restore is not None:
            was_test, overrides = self._restore
            self._restore = None
            self.studio.preview_source.overrides = overrides
            if restore_mode:
                self.studio.set_test_mode(was_test)
            self.studio.engine.poll()
        self.table.setEnabled(True)

    def capture(self):
        if self._restore is None:
            raise ValueError("Preview a sequence step before capturing evidence")
        path = self.studio.store.root / self.studio.display.name / "test_evidence"
        path.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        screenshot = self.studio.canvas.viewport().grab()
        filename = path / f"{stamp}.png"
        if not screenshot.save(str(filename)):
            raise OSError("Could not save the preview image")
        digest = hashlib.sha256(json.dumps(self.steps, sort_keys=True).encode()).hexdigest()
        self.evidence.append(dict(at=self.elapsed, image=str(filename.relative_to(self.studio.store.root)),
                                  note=self.note.text(), document_digest=document_digest(self.studio._document()), steps_digest=digest))
        self.capture_label.setPixmap(screenshot.scaled(480, 145, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.status.setText("Captured the current canvas. Save sequence to retain its note and fingerprint.")

    def closeEvent(self, event):  # noqa: N802
        self.stop()
        super().closeEvent(event)

    def done(self, result):
        self.stop()
        super().done(result)
