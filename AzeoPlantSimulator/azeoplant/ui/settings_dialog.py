# -*- coding: utf-8 -*-
"""Application settings: the few knobs that are real.

Everything here takes effect immediately on OK and persists through
QSettings. Anything that belongs to the plant (dt, historian period,
endpoint) stays on the command line where it always was.
"""

from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFormLayout, QSpinBox)

REFRESH_CHOICES = [("Fast (100 ms)", 100), ("Normal (200 ms)", 200),
                   ("Relaxed (500 ms)", 500)]
HORN_CHOICES = [("Critical and High", "CRITICAL,HIGH"),
                ("Critical only", "CRITICAL")]

DEFAULTS = {"ui_refresh_ms": 200, "banner_slots": 5,
            "horn_scope": "CRITICAL,HIGH", "confirm_exit": True}


class SettingsDialog(QDialog):
    def __init__(self, prefs: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")

        self.refresh_box = QComboBox()
        for label, ms in REFRESH_CHOICES:
            self.refresh_box.addItem(label, ms)
        ix = next((i for i, (_l, ms) in enumerate(REFRESH_CHOICES)
                   if ms == prefs["ui_refresh_ms"]), 1)
        self.refresh_box.setCurrentIndex(ix)

        self.slots_spin = QSpinBox()
        self.slots_spin.setRange(1, 5)
        self.slots_spin.setValue(int(prefs["banner_slots"]))

        self.horn_box = QComboBox()
        for label, scope in HORN_CHOICES:
            self.horn_box.addItem(label, scope)
        ix = next((i for i, (_l, sc) in enumerate(HORN_CHOICES)
                   if sc == prefs["horn_scope"]), 0)
        self.horn_box.setCurrentIndex(ix)

        self.confirm_chk = QCheckBox()
        self.confirm_chk.setChecked(bool(prefs["confirm_exit"]))

        form = QFormLayout(self)
        form.addRow("Display refresh", self.refresh_box)
        form.addRow("Alarm banner entries", self.slots_spin)
        form.addRow("Audible horn on", self.horn_box)
        form.addRow("Confirm before exit", self.confirm_chk)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                   | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def prefs(self) -> dict:
        return {"ui_refresh_ms": int(self.refresh_box.currentData()),
                "banner_slots": int(self.slots_spin.value()),
                "horn_scope": str(self.horn_box.currentData()),
                "confirm_exit": self.confirm_chk.isChecked()}
