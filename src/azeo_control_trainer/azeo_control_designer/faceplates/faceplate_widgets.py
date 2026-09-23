"""Small widgets and the palette the device faceplates are assembled from.

Kept apart from the faceplates themselves so a new one — the interlock
summary, the discrete input/output pair — starts from the same lamps, bands
and rows rather than restyling its own. The colours come from the shared
ISA-101 Silver theme; only the *roles* are named here.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QSizePolicy, QWidget,
)

from azeo_control_trainer.core.pid.theme.colors import (
    BG_FACEPLATE, BG_RAISED, TEXT_PRIMARY, TEXT_SECONDARY,
    ALARM_CRITICAL, ALARM_WARN,
)

# ── palette roles ────────────────────────────────────────────────────
BODY_BG = BG_FACEPLATE
TAG_COLOR = TEXT_SECONDARY
VALUE_COLOR = TEXT_PRIMARY
BORDER = "#9BADC6"

OK = "#2E7D32"          # healthy / running
BAD = ALARM_CRITICAL    # asserted / tripped
WARN = ALARM_WARN       # bypassed / transitional
IDLE = "#6B7A90"        # stopped, and nothing wrong with that


class Lamp(QLabel):
    """Round status indicator."""

    def __init__(self, colour: str = IDLE, diameter: int = 11, parent=None):
        super().__init__(parent)
        self._d = diameter
        self.setFixedSize(diameter, diameter)
        self.set_colour(colour)

    def set_colour(self, colour: str) -> None:
        self.setStyleSheet(
            f"QLabel {{ background-color: {colour};"
            f" border-radius: {self._d // 2}px;"
            " border: 1px solid rgba(0,0,0,0.25); }"
        )


def band(title: str) -> tuple[QFrame, QLabel]:
    """A section header bar. Returns the frame and its right-hand status label."""
    fr = QFrame()
    fr.setFixedHeight(20)
    fr.setStyleSheet(
        f"QFrame {{ background-color: {BG_RAISED}; border: 1px solid {BORDER};"
        " border-radius: 2px; }"
    )
    lay = QHBoxLayout(fr)
    lay.setContentsMargins(6, 0, 6, 0)
    lbl = QLabel(title)
    lbl.setStyleSheet(
        f"QLabel {{ font-size: 7pt; font-weight: bold; color: {TAG_COLOR};"
        " background: transparent; border: none; letter-spacing: 0.5px; }"
    )
    lay.addWidget(lbl)
    lay.addStretch()
    status = QLabel("")
    status.setStyleSheet(
        "QLabel { font-size: 7pt; font-weight: bold; background: transparent;"
        " border: none; }"
    )
    lay.addWidget(status)
    return fr, status


def set_band_status(label: QLabel, text: str, ok: bool) -> None:
    """Write a band's right-hand status, coloured by whether it is healthy."""
    label.setText(text)
    label.setStyleSheet(
        f"QLabel {{ font-size: 7pt; font-weight: bold;"
        f" color: {OK if ok else BAD}; background: transparent;"
        " border: none; }"
    )


class FieldRow(QWidget):
    """``label   VALUE``, with an optional lamp in front of the value."""

    def __init__(self, label: str, lamp: bool = False, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(6)
        cap = QLabel(label)
        cap.setFixedWidth(74)
        cap.setStyleSheet(f"QLabel {{ font-size: 8pt; color: {TAG_COLOR}; }}")
        lay.addWidget(cap)
        self.lamp = Lamp() if lamp else None
        if self.lamp is not None:
            lay.addWidget(self.lamp)
        self.value = QLabel("—")
        self.value.setStyleSheet(
            f"QLabel {{ font-size: 9pt; font-weight: bold; color: {VALUE_COLOR};"
            " font-family: Consolas; }"
        )
        lay.addWidget(self.value)
        lay.addStretch()

    def set(self, text: str, colour: str | None = None,
            lamp_colour: str | None = None) -> None:
        self.value.setText(text)
        self.value.setStyleSheet(
            f"QLabel {{ font-size: 9pt; font-weight: bold;"
            f" color: {colour or VALUE_COLOR}; font-family: Consolas; }}"
        )
        if self.lamp is not None and lamp_colour is not None:
            self.lamp.set_colour(lamp_colour)


class ConditionRow(QWidget):
    """One interlock channel: health lamp, number, description, bypass badge."""

    def __init__(self, index: int, description: str, parent=None):
        super().__init__(parent)
        self.index = index
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 1, 2, 1)
        lay.setSpacing(5)
        self._lamp = Lamp(IDLE, 9)
        lay.addWidget(self._lamp)
        num = QLabel(str(index))
        num.setFixedWidth(12)
        num.setStyleSheet(f"QLabel {{ font-size: 7pt; color: {TAG_COLOR}; }}")
        lay.addWidget(num)
        self._desc = QLabel(description)
        self._desc.setStyleSheet(
            f"QLabel {{ font-size: 8pt; color: {VALUE_COLOR}; }}")
        self._desc.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        lay.addWidget(self._desc, 1)
        self._byp = QLabel("BYP")
        self._byp.setStyleSheet(
            f"QLabel {{ font-size: 6pt; font-weight: bold; color: white;"
            f" background-color: {WARN}; border-radius: 2px; padding: 0 3px; }}"
        )
        self._byp.setVisible(False)
        lay.addWidget(self._byp)

    def set_state(self, healthy: bool, bypassed: bool = False) -> None:
        # A bypassed channel reads healthy — which is exactly why the banner
        # above it has to exist.
        self._lamp.set_colour(WARN if bypassed else (OK if healthy else BAD))
        self._byp.setVisible(bypassed)
        self._desc.setStyleSheet(
            f"QLabel {{ font-size: 8pt; color: "
            f"{VALUE_COLOR if healthy or bypassed else BAD}; }}"
        )

    def set_description(self, text: str) -> None:
        self._desc.setText(text)

    def description(self) -> str:
        return self._desc.text()
