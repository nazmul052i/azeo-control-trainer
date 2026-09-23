"""Checked entry editor opened by an authored PA parameter row."""
import json
import logging

from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QPushButton, QStackedWidget, QVBoxLayout

from azeo_control_trainer.core.hmi.theme.widgets import bind_operator_theme
from azeo_control_trainer.core.procedures.parameters import parameters


class ParameterEditor(QDialog):
    def __init__(self, station, session, context, identity=""):
        super().__init__(station)
        self.station, self.session, self.context = station, session, context
        self.setWindowTitle("Procedure parameter")
        self.resize(520, 360)
        self.specs = parameters(session.prepare(context["ref"]).procedure)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)
        self.parameter = QComboBox()
        for spec in self.specs.values():
            self.parameter.addItem(spec.label, spec.id)
        if identity:
            self.parameter.setCurrentIndex(self.parameter.findData(identity))
        form.addRow("Parameter", self.parameter)
        self.current = QLabel()
        self.current.setWordWrap(True)
        form.addRow("Current / limits", self.current)
        self.value = QLineEdit()
        self.boolean = QComboBox()
        self.boolean.addItem("True", True)
        self.boolean.addItem("False", False)
        self.editor = QStackedWidget()
        self.editor.addWidget(self.value)
        self.editor.addWidget(self.boolean)
        form.addRow("Proposed value", self.editor)
        self.effective = QComboBox()
        form.addRow("Apply", self.effective)
        self.effect = QLabel()
        self.effect.setWordWrap(True)
        layout.addWidget(self.effect)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.cancel_pending = QPushButton("Cancel queued change")
        layout.addWidget(self.cancel_pending)
        self.cancel_pending.clicked.connect(lambda: self.apply("cancel"))
        buttons = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Close)
        self.apply_button = buttons.button(QDialogButtonBox.Apply)
        self.apply_button.clicked.connect(self.apply)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self.parameter.currentIndexChanged.connect(self.refresh_parameter)
        self.effective.currentIndexChanged.connect(self.refresh_effect)
        self.refresh_parameter()
        self._hmi_theme_extra = "QLineEdit, QComboBox { padding: 5px; } QPushButton { padding: 6px; }"
        bind_operator_theme(self)

    def refresh_parameter(self, *_):
        try:
            self._refresh_parameter()
            self.apply_button.setEnabled(True)
        except Exception as error:
            self.apply_button.setEnabled(False)
            self.context["token"] = ("", "", -1)
            self.status.setText("Parameter unavailable: " + str(error))
            logging.getLogger(__name__).exception("Procedure parameter refresh failed")

    def _refresh_parameter(self):
        identity = self.parameter.currentData()
        spec = self.specs.get(identity)
        if spec is None:
            return
        self.context["token"] = self.session.token()
        row = next(row for row in self.session.snapshot(self.context["ref"])["PARAMETERS"] if row["id"] == identity)
        self.current.setText(f"{row['value']} {spec.unit} · {spec.data_type} · {row['limits']}\nQueued: {row['pending']}\n{spec.description}")
        value = row["value"] if row["value"] is not None else spec.value
        self.value.setText(value if spec.data_type == "str" else json.dumps(value))
        self.editor.setCurrentWidget(self.boolean if spec.data_type == "bool" else self.value)
        if spec.data_type == "bool":
            self.boolean.setCurrentIndex(0 if value else 1)
        self.effective.clear()
        self.effective.addItem("Next run (once)", "next_run")
        if spec.access == "live" and self.context["ref"] == self.session.ref and self.session.run.active:
            self.effective.addItem("Live · restart condition qualification", "live")
        self.cancel_pending.setEnabled(identity in self.session.pending(self.context["ref"]))
        self.refresh_effect()

    def refresh_effect(self, *_):
        live = self.effective.currentData() == "live"
        self.effect.setText("Applied at the next observation boundary. All condition hold evidence restarts; the overall timeout continues." if live else
                            "Saved in signed history for the next run of this revision. The active run and shared memory are unchanged until that run starts.")

    def apply(self, mode=None):
        try:
            spec = self.specs[self.parameter.currentData()]
            effective = mode if mode == "cancel" else self.effective.currentData()
            value = (None if effective == "cancel" else
                     self.boolean.currentData() if spec.data_type == "bool" else
                     self.value.text() if spec.data_type == "str" else json.loads(self.value.text()))
            self.session.tune(self.context["ref"], self.context["token"], spec.id, value, effective)
            self.status.setText("Queued change cancelled" if effective == "cancel" else
                                "Change requested; see the parameter table and history for its applied result" if effective == "live" else
                                "Saved for the next run of this revision")
            self.refresh_parameter()
        except (ValueError, KeyError) as error:
            self.status.setText(str(error))
        except Exception as error:
            logging.getLogger(__name__).exception("Procedure parameter change failed")
            self.status.setText("Change failed: " + str(error))
