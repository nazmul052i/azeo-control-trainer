"""Scrolling must never edit a value.

The mouse wheel over a spinbox or combo steps the value in stock Qt — so
scrolling a tall properties list edits whatever parameter the cursor
happens to cross. In an engineering tool for a control system that is not
a quirk, it is an accidental write: a setpoint limit or a MODE changed by
a gesture that meant "move the page". User rule, absolute: the wheel
changes no field, focused or not.

The blocked gesture is not swallowed — it is re-sent to the field's
parent, so the scroll area underneath keeps scrolling smoothly instead of
dead-zoning every time the cursor passes a field. Typing, arrow keys and
Page Up/Down remain the ways to change a value.

Installed once on the QApplication (an application-level filter sees every
widget's events), so every present and future field is covered without
per-widget wiring.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QComboBox

_INSTALLED_FLAG = "_azeo_wheel_guard"


class WheelGuard(QObject):
    """Block wheel edits on value fields; pass the scroll to the page."""

    def eventFilter(self, obj, event):              # noqa: N802
        if event.type() != QEvent.Wheel:
            return False
        if not isinstance(obj, (QAbstractSpinBox, QComboBox)):
            return False
        if isinstance(obj, QComboBox) and obj.view() is not None \
                and obj.view().isVisible():
            # The open popup list is deliberate navigation, not a stray
            # scroll — leave it alone.
            return False
        parent = obj.parentWidget()
        if parent is not None:
            forwarded = QWheelEvent(
                parent.mapFromGlobal(event.globalPosition().toPoint()),
                event.globalPosition(), event.pixelDelta(),
                event.angleDelta(), event.buttons(), event.modifiers(),
                event.phase(), event.inverted())
            QApplication.sendEvent(parent, forwarded)
        return True


def install_wheel_guard() -> None:
    """Idempotent: one guard per application, wherever it is asked from."""
    app = QApplication.instance()
    if app is None or getattr(app, _INSTALLED_FLAG, None) is not None:
        return
    guard = WheelGuard(app)
    app.installEventFilter(guard)
    setattr(app, _INSTALLED_FLAG, guard)
