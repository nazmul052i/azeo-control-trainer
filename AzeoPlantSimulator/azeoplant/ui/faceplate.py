"""Tag faceplate.

Opened by clicking anything on the P&ID or double-clicking a row in the tag
browser. Read-only for ``AI`` and ``DI``, because those are model outputs and
forcing them would hide the physics the trainee is meant to see.

For ``AO`` and ``DO`` the faceplate offers a **local override**. The simulator is
open loop, so with no DCS attached nothing would ever move a valve. The override
makes the simulator usable standalone and is deliberately conspicuous: an
overridden tag is drawn in violet everywhere it appears, and the OPC UA value
continues to show what the DCS actually wrote, so the two never get confused.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QDialogButtonBox,
                               QDoubleSpinBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QPushButton, QSlider,
                               QVBoxLayout)

from ..core.tags import Quality, Tag, TagDatabase, TagKind
from . import theme
from .live_dialog import LiveDialog

log = logging.getLogger(__name__)


class FaceplateDialog(LiveDialog):
    def __init__(self, db: TagDatabase, tag_name: str, parent=None) -> None:
        super().__init__(parent)
        self.db = db
        self.tag: Tag = db[tag_name]
        self.setWindowTitle(f"{self.tag.name}  ·  {self.tag.desc}")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)

        info = QGroupBox("Signal")
        form = QFormLayout(info)
        self.value_label = QLabel("-")
        self.value_label.setFont(theme.font(16, True, mono=True))
        self.quality_label = QLabel("-")
        form.addRow("Value", self.value_label)
        form.addRow("Quality", self.quality_label)
        form.addRow("Type", QLabel(f"{self.tag.kind.value}  ({self.tag.unit})"))
        if self.tag.kind.analogue:
            form.addRow("Range", QLabel(f"{self.tag.lo} to {self.tag.hi} {self.tag.eu}"))
        node = QLabel(f"ns=2;s={self.tag.name}")
        node.setFont(theme.font(9, mono=True))
        node.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow("NodeId", node)
        layout.addWidget(info)

        if self.tag.kind.dcs_writable:
            layout.addWidget(self._build_override())
        else:
            note = QLabel("This is a model output. It cannot be forced from here, "
                          "because doing so would mask the process behaviour it "
                          "exists to show.")
            note.setWordWrap(True)
            note.setStyleSheet(f"color: {theme.MUTED_TEXT.name()};")
            layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(250)
        self._refresh()

    def _build_override(self) -> QGroupBox:
        box = QGroupBox("Local override")
        layout = QVBoxLayout(box)

        note = QLabel("With no DCS connected, use this to drive the model by hand. "
                      "The value the DCS wrote is still shown above.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {theme.MUTED_TEXT.name()};")
        layout.addWidget(note)

        self.enable = QCheckBox("Override active")
        self.enable.setChecked(self.tag.override)
        self.enable.toggled.connect(self._set_override)
        layout.addWidget(self.enable)

        if self.tag.kind is TagKind.AO:
            row = QHBoxLayout()
            self.slider = QSlider(Qt.Horizontal)
            self.slider.setRange(int(self.tag.lo), int(self.tag.hi))
            self.slider.setValue(int(float(self.tag.override_value)))
            self.spin = QDoubleSpinBox()
            self.spin.setRange(self.tag.lo, self.tag.hi)
            self.spin.setDecimals(2)
            self.spin.setSuffix(f"  {self.tag.eu}")
            self.spin.setValue(float(self.tag.override_value))
            self.slider.valueChanged.connect(
                lambda v: self.spin.setValue(float(v)))
            self.spin.valueChanged.connect(self._set_value)
            row.addWidget(self.slider, 1)
            row.addWidget(self.spin)
            layout.addLayout(row)
        else:
            row = QHBoxLayout()
            on = QPushButton(self.tag.state1)
            off = QPushButton(self.tag.state0)
            on.clicked.connect(lambda: self._set_value(True))
            off.clicked.connect(lambda: self._set_value(False))
            row.addWidget(off)
            row.addWidget(on)
            layout.addLayout(row)
        return box

    def _set_override(self, active: bool) -> None:
        with self.db.lock:
            self.tag.override = bool(active)
        log.info("Local override %s on %s", "enabled" if active else "cleared",
                 self.tag.name)

    def _set_value(self, value) -> None:
        with self.db.lock:
            self.tag.override_value = (float(value) if self.tag.kind.analogue
                                       else bool(value))
            if not self.tag.override:
                self.tag.override = True
                self.enable.setChecked(True)
        if hasattr(self, "slider") and self.tag.kind.analogue:
            self.slider.blockSignals(True)
            self.slider.setValue(int(float(value)))
            self.slider.blockSignals(False)

    def _refresh(self) -> None:
        with self.db.lock:
            value, quality, override = self.tag.value, self.tag.quality, self.tag.override
        text = (self.tag.state1 if value else self.tag.state0) \
            if isinstance(value, bool) else f"{float(value):.3f} {self.tag.eu}".strip()
        self.value_label.setText(text)
        self.value_label.setStyleSheet(
            f"color: {theme.quality_colour(int(quality)).name()};")
        label = Quality(int(quality)).label
        if override:
            label += "   ·   LOCAL OVERRIDE ACTIVE"
        self.quality_label.setText(label)
        self.quality_label.setStyleSheet(
            f"color: {(theme.OVERRIDE if override else theme.quality_colour(int(quality))).name()};"
            f" font-weight: {'bold' if override or quality else 'normal'};")

    def closeEvent(self, event) -> None:
        self._timer.stop()
        super().closeEvent(event)
