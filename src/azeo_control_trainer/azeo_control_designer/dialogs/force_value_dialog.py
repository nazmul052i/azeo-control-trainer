r"""Force value dialog — DCS-standard "parameter pulse" / "force" UI.

Opens from any block's right-click menu (or by ``Ctrl+F`` over a
selected block). Lets the operator:

  * Pick an input terminal on the block
  * Enter a target value (typed to the terminal's data type)
  * Optionally set an auto-release timer in seconds
  * See and release every currently-forced terminal on the block

Behaves like Azeo's *Force*: the runtime applies the forced value to
the input after upstream wires propagate and before the block executes.
Outputs remain the algorithm's observable result and cannot be forced.

Public API
----------
``ForceValueDialog(block, parent=None)`` — dialog wrapped around a
:class:`FunctionBlock`. Reads/writes the block's terminals directly,
so changes are reflected on the next runtime scan without any extra
plumbing.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI

from azeo_control_trainer.core.presentation.authoring_controls import AuthoringComboBox
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)


class ForceValueDialog(QDialog):
    """Modal force / release dialog for a FunctionBlock."""

    # Emitted when the operator presses Apply (or releases something);
    # canvas / properties panel use it to refresh the BlockItem.
    forcesChanged = Signal(str)   # block_id

    def __init__(self, block, parent=None):
        super().__init__(parent)
        self._block = block
        self.setWindowTitle(
            f"Force value — {block.instance_name}  ({block.block_type})")
        self.setMinimumWidth(520)
        self._build()
        self._refresh_active()

    # ─────────────────────────────────────────────────────────────
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # ── Header ─────────────────────────────────────────────────
        header = QLabel(
            f"<b>{self._block.instance_name}</b> "
            f"&nbsp;·&nbsp; <span style='color:#555;'>"
            f"{self._block.block_type}</span><br>"
            "<span style='color:#888; font-size:9pt;'>Forced terminals "
            "override their computed or wire-propagated value every scan.</span>")
        header.setStyleSheet(
            f"background: {UI.hover}; padding: 6px 10px;"
            f" border: 1px solid {UI.border};")
        root.addWidget(header)

        # ── New force section ──────────────────────────────────────
        new_grp = QGroupBox("Apply a force")
        new_grp.setStyleSheet(
            f"QGroupBox {{ background: #FAFBFD; border: 1px solid {UI.border};"
            " border-radius: 3px; margin-top: 8px; padding: 8px; }"
            " QGroupBox::title { subcontrol-origin: margin; left: 8px;"
            f" color: {UI.blue}; font-weight: bold; }}")
        nl = QVBoxLayout(new_grp)
        nl.setSpacing(6)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Terminal:"))
        self._combo = AuthoringComboBox()
        self._combo.setMinimumWidth(220)
        self._populate_terminal_combo()
        self._combo.currentIndexChanged.connect(self._on_terminal_pick)
        row1.addWidget(self._combo, 1)
        nl.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Value:"))
        # We'll show different value editors per data type — for now use a
        # QLineEdit + a QDoubleSpinBox stacked. Bool terminals get a combo.
        self._val_line = QLineEdit()
        self._val_line.setPlaceholderText("Number, True/False, or text")
        self._val_line.setStyleSheet(
            "QLineEdit { padding: 4px; font-family: Consolas; }")
        row2.addWidget(self._val_line, 1)
        nl.addLayout(row2)

        row3 = QHBoxLayout()
        self._auto_chk = QCheckBox("Auto-release after")
        self._auto_chk.setChecked(False)
        row3.addWidget(self._auto_chk)
        self._auto_spin = QDoubleSpinBox()
        self._auto_spin.setRange(0.1, 3600.0)
        self._auto_spin.setSingleStep(0.5)
        self._auto_spin.setValue(5.0)
        self._auto_spin.setSuffix(" s")
        self._auto_spin.setDecimals(1)
        row3.addWidget(self._auto_spin)
        row3.addStretch(1)
        btn_apply = QPushButton("Apply force")
        btn_apply.setStyleSheet(
            "QPushButton { background: #B22222; color: white; padding: 4px 14px;"
            " border-radius: 3px; font-weight: bold; }"
            " QPushButton:hover { background: #d32f2f; }")
        btn_apply.clicked.connect(self._on_apply)
        row3.addWidget(btn_apply)
        nl.addLayout(row3)
        root.addWidget(new_grp)

        # ── Active forces section ──────────────────────────────────
        act_grp = QGroupBox("Active forces")
        act_grp.setStyleSheet(new_grp.styleSheet())
        al = QVBoxLayout(act_grp)
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(
            ["Terminal", "Direction", "Forced value", ""])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setStyleSheet(
            f"QTableWidget {{ background: white; border: 1px solid {UI.border};"
            " font-family: Consolas; font-size: 9pt; }")
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        al.addWidget(self._table)
        row_btns = QHBoxLayout()
        row_btns.addStretch(1)
        btn_rel_all = QPushButton("Release all")
        btn_rel_all.setStyleSheet(
            "QPushButton { background: #555; color: white; padding: 4px 14px;"
            " border-radius: 3px; }"
            " QPushButton:hover { background: #333; }")
        btn_rel_all.clicked.connect(self._on_release_all)
        row_btns.addWidget(btn_rel_all)
        al.addLayout(row_btns)
        root.addWidget(act_grp)

        # ── Buttons ────────────────────────────────────────────────
        bbox = QDialogButtonBox(QDialogButtonBox.Close)
        bbox.rejected.connect(self.reject)
        bbox.accepted.connect(self.accept)
        root.addWidget(bbox)

    def _populate_terminal_combo(self):
        self._combo.clear()
        for name, t in self._block.inputs.items():
            self._combo.addItem(f"IN  ·  {name}  ({t.data_type.value})",
                                  ("in", name))

    def _on_terminal_pick(self, _idx: int):
        # Seed the value field with the terminal's current value
        data = self._combo.currentData()
        if not data:
            return
        _direction, name = data
        t = self._block.inputs.get(name)
        if t is not None:
            self._val_line.setText(str(t.value))

    # ─────────────────────────────────────────────────────────────
    def _parse_value(self, text: str, data_type) -> tuple[bool, object]:
        """Coerce ``text`` to the terminal's data type. Returns (ok, value)."""
        from azeo_control_trainer.core.strategy.model.terminal import DataType
        s = text.strip()
        if data_type == DataType.BOOL:
            return True, s.lower() in ("1", "true", "t", "yes", "y", "on")
        if data_type == DataType.INT:
            try:
                return True, int(float(s))
            except (TypeError, ValueError):
                return False, 0
        if data_type == DataType.FLOAT:
            try:
                return True, float(s)
            except (TypeError, ValueError):
                return False, 0.0
        return True, s

    def _on_apply(self):
        data = self._combo.currentData()
        if not data:
            return
        _direction, name = data
        t = self._block.inputs.get(name)
        if t is None:
            return
        ok, val = self._parse_value(self._val_line.text(), t.data_type)
        if not ok:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Invalid value",
                f"Cannot interpret {self._val_line.text()!r} as {t.data_type.value}")
            return
        timer = self._auto_spin.value() if self._auto_chk.isChecked() else None
        self._block.force_terminal(name, val, release_after_s=timer)
        self.forcesChanged.emit(self._block.id)
        self._refresh_active()

    def _on_release_all(self):
        n = self._block.release_all_forces()
        if n:
            self.forcesChanged.emit(self._block.id)
        self._refresh_active()

    def _on_release_one(self, name: str):
        self._block.release_terminal(name)
        self.forcesChanged.emit(self._block.id)
        self._refresh_active()

    def _refresh_active(self):
        rows: list[tuple[str, str, object]] = []
        for n, t in self._block.inputs.items():
            if t.forced:
                rows.append((n, "IN", t.forced_value))
        self._table.setRowCount(len(rows))
        for r, (name, direction, val) in enumerate(rows):
            self._table.setItem(r, 0, QTableWidgetItem(name))
            self._table.setItem(r, 1, QTableWidgetItem(direction))
            self._table.setItem(r, 2, QTableWidgetItem(str(val)))
            btn = QPushButton("Release")
            btn.setStyleSheet(
                "QPushButton { padding: 2px 10px; border-radius: 2px;"
                f" border: 1px solid {UI.border}; background: #FAFBFD; }}"
                f" QPushButton:hover {{ background: {UI.hover}; }}")
            btn.clicked.connect(
                lambda _checked=False, n=name: self._on_release_one(n))
            self._table.setCellWidget(r, 3, btn)
