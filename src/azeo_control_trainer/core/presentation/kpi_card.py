"""Reusable ISA-101 styled KPI display card."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

from .hmi_theme import Colors

# Category -> (background tint, border color)
_COLOR_MAP = {
    'primary':   (Colors.STATE_MANUAL, Colors.STATE_MANUAL_BORDER),
    'danger':    (Colors.STATE_FAULT, Colors.STATE_FAULT_BORDER),
    'warning':   (Colors.ALARM_MEDIUM, Colors.ALARM_MEDIUM_BORDER),
    'info':      (Colors.ALARM_LOW, Colors.ALARM_LOW_BORDER),
    'success':   (Colors.STATE_RUNNING, Colors.STATE_RUNNING_BORDER),
    'secondary': (Colors.STATE_STOPPED, Colors.STATE_STOPPED_BORDER),
    'light':     (Colors.BG_SECONDARY, Colors.EQUIP_OUTLINE),
}


class KpiCard(QFrame):
    """Compact card showing a title, value, and optional unit."""

    def __init__(self, title: str, unit: str = '', color: str = 'primary',
                 parent=None):
        super().__init__(parent)
        self._unit = unit
        self._fmt = '.1f'

        bg, border = _COLOR_MAP.get(color, _COLOR_MAP['primary'])
        self._default_style = (
            f"KpiCard {{ background: {Colors.BG_SECONDARY}; "
            f"border: 2px solid {border}; border-radius: 4px; }}")

        self.setStyleSheet(self._default_style)
        self.setMinimumWidth(100)
        self.setMaximumHeight(70)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(1)

        self._title_lbl = QLabel(title)
        self._title_lbl.setAlignment(Qt.AlignCenter)
        self._title_lbl.setStyleSheet(
            f"font-size: 8pt; color: {Colors.TEXT_SECONDARY}; "
            f"background: transparent; font-weight: bold;")
        layout.addWidget(self._title_lbl)

        self._value_lbl = QLabel("--")
        self._value_lbl.setAlignment(Qt.AlignCenter)
        self._value_lbl.setStyleSheet(
            f"font-size: 12pt; font-weight: bold; font-family: Consolas, monospace; "
            f"color: {bg}; background: transparent;")
        layout.addWidget(self._value_lbl)

    def set_value(self, value, fmt: str | None = None):
        """Update displayed value. *value* can be float or str."""
        if isinstance(value, str):
            text = value
        else:
            f = fmt or self._fmt
            text = f"{value:{f}}"
            if self._unit:
                text += f" {self._unit}"
        self._value_lbl.setText(text)

    def set_format(self, fmt: str):
        self._fmt = fmt

    def set_alert(self, alert: bool):
        """Highlight border red when in alarm, reset when clear."""
        if alert:
            self.setStyleSheet(
                f"KpiCard {{ background: {Colors.BG_SECONDARY}; "
                f"border: 3px solid {Colors.ALARM_CRITICAL}; border-radius: 4px; }}")
        else:
            self.setStyleSheet(self._default_style)
