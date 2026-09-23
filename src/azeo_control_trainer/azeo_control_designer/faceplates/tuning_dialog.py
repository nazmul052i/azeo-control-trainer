"""PID Tuning parameter editor dialog.

Reads current Kp/Ti/Td and advanced parameters from SharedDataStore,
lets the operator edit them, and writes changes back via queue_write.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QPushButton, QVBoxLayout,
)


class TuningDialog(QDialog):
    """Modal dialog for editing PID tuning parameters."""

    def __init__(self, tag: str, store=None, parent=None):
        super().__init__(parent)
        self._tag = tag
        self._store = store

        self.setWindowTitle(f"Tuning — {tag}")
        self.setMinimumWidth(350)
        self.setModal(True)

        layout = QVBoxLayout(self)

        # PID Gains
        gb_gains = QGroupBox("PID Gains")
        gains_form = QFormLayout(gb_gains)

        self._spin_kp = self._make_spin(0, 100, 4, 0.001)
        gains_form.addRow("Kp:", self._spin_kp)

        self._spin_ti = self._make_spin(0, 10000, 1, 1.0)
        self._spin_ti.setSuffix(" s")
        gains_form.addRow("Ti:", self._spin_ti)

        self._spin_td = self._make_spin(0, 1000, 2, 0.1)
        self._spin_td.setSuffix(" s")
        gains_form.addRow("Td:", self._spin_td)

        layout.addWidget(gb_gains)

        # Advanced
        gb_adv = QGroupBox("Advanced")
        adv_form = QFormLayout(gb_adv)

        self._spin_beta = self._make_spin(0, 2, 2, 0.1)
        adv_form.addRow("Beta (SP weight):", self._spin_beta)

        self._spin_gamma = self._make_spin(0, 2, 2, 0.1)
        adv_form.addRow("Gamma (D weight):", self._spin_gamma)

        self._spin_alpha = self._make_spin(0, 1, 2, 0.01)
        adv_form.addRow("Alpha (D filter):", self._spin_alpha)

        self._spin_rate = self._make_spin(0, 1, 3, 0.01)
        adv_form.addRow("Rate Limit:", self._spin_rate)

        layout.addWidget(gb_adv)

        # Limits
        gb_lim = QGroupBox("Limits")
        lim_form = QFormLayout(gb_lim)

        self._spin_sp_min = self._make_spin(-1e9, 1e9, 2, 1.0)
        lim_form.addRow("SP Min:", self._spin_sp_min)

        self._spin_sp_max = self._make_spin(-1e9, 1e9, 2, 1.0)
        lim_form.addRow("SP Max:", self._spin_sp_max)

        self._spin_op_min = self._make_spin(-1, 1, 3, 0.01)
        lim_form.addRow("OP Min:", self._spin_op_min)

        self._spin_op_max = self._make_spin(-1, 2, 3, 0.01)
        lim_form.addRow("OP Max:", self._spin_op_max)

        layout.addWidget(gb_lim)

        # Status
        self._lbl_status = QLabel("")
        self._lbl_status.setStyleSheet("color: #2E8B2E; font-style: italic;")
        layout.addWidget(self._lbl_status)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_apply = QPushButton("Apply")
        self._btn_apply.setStyleSheet(
            "QPushButton { background: #4169E1; color: white; font-weight: bold; "
            "border: 1px solid #3159C1; padding: 4px 16px; }"
            "QPushButton:hover { background: #5179F1; }")
        self._btn_apply.clicked.connect(self._on_apply)
        btn_row.addWidget(self._btn_apply)

        self._btn_close = QPushButton("Close")
        self._btn_close.clicked.connect(self.close)
        btn_row.addWidget(self._btn_close)
        layout.addLayout(btn_row)

        self._load_values()

    def _make_spin(self, lo, hi, decimals, step):
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(decimals)
        s.setSingleStep(step)
        s.setMinimumWidth(100)
        return s

    def _load_values(self):
        if not self._store:
            return
        prefix = f'ctrl.{self._tag}'
        kp = self._store.get(f'{prefix}.GAIN', self._store.get(f'{prefix}.Kp', 0.0))
        ti = self._store.get(f'{prefix}.RESET', self._store.get(f'{prefix}.Ti', 0.0))
        td = self._store.get(f'{prefix}.RATE', self._store.get(f'{prefix}.Td', 0.0))
        beta = self._store.get(f'{prefix}.Beta', 1.0)
        gamma = self._store.get(f'{prefix}.Gamma', 0.0)
        alpha = self._store.get(f'{prefix}.Alpha', 0.1)
        rate = self._store.get(f'{prefix}.RateLimit', 0.0)
        sp_min = self._store.get(f'{prefix}.SP_Min', 0.0)
        sp_max = self._store.get(f'{prefix}.SP_Max', 100.0)
        op_min = self._store.get(f'{prefix}.OP_Min', 0.0)
        op_max = self._store.get(f'{prefix}.OP_Max', 1.0)

        self._spin_kp.setValue(float(kp) if kp else 0.0)
        self._spin_ti.setValue(float(ti) if ti else 0.0)
        self._spin_td.setValue(float(td) if td else 0.0)
        self._spin_beta.setValue(float(beta) if beta else 1.0)
        self._spin_gamma.setValue(float(gamma) if gamma else 0.0)
        self._spin_alpha.setValue(float(alpha) if alpha else 0.1)
        self._spin_rate.setValue(float(rate) if rate else 0.0)
        self._spin_sp_min.setValue(float(sp_min) if sp_min else 0.0)
        self._spin_sp_max.setValue(float(sp_max) if sp_max else 100.0)
        self._spin_op_min.setValue(float(op_min) if op_min else 0.0)
        self._spin_op_max.setValue(float(op_max) if op_max else 1.0)

    def _on_apply(self):
        if not self._store:
            self._lbl_status.setText("No store connected")
            return

        prefix = f'ctrl.{self._tag}'
        writes = {
            f'{prefix}.GAIN': self._spin_kp.value(),
            f'{prefix}.RESET': self._spin_ti.value(),
            f'{prefix}.RATE': self._spin_td.value(),
            f'{prefix}.Beta': self._spin_beta.value(),
            f'{prefix}.Gamma': self._spin_gamma.value(),
            f'{prefix}.Alpha': self._spin_alpha.value(),
            f'{prefix}.RateLimit': self._spin_rate.value(),
            f'{prefix}.SP_Min': self._spin_sp_min.value(),
            f'{prefix}.SP_Max': self._spin_sp_max.value(),
            f'{prefix}.OP_Min': self._spin_op_min.value(),
            f'{prefix}.OP_Max': self._spin_op_max.value(),
        }
        for key, val in writes.items():
            self._store.queue_write(key, val)

        self._lbl_status.setText(f"Applied {len(writes)} parameters")
