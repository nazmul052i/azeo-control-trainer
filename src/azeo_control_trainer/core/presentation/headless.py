"""Detect a display-less Qt session.

Modal dialogs (`QDialog.exec`, `QMessageBox.question`) block until someone
answers them. Under the offscreen platform — smoke tests, CI, screenshot
capture — nobody can, so the process hangs until it is killed. Several
close-time prompts did exactly that.

Guard any modal prompt that can fire without direct user action::

    if not is_headless():
        reply = QMessageBox.question(...)
"""
from __future__ import annotations

from PySide6.QtWidgets import QApplication

#: Qt platform plugins that have no display attached.
_HEADLESS_PLATFORMS = ("offscreen", "minimal", "vnc")


def is_headless() -> bool:
    """True when Qt is running without a real display."""
    app = QApplication.instance()
    if app is None:
        return True          # no event loop at all — nothing can answer
    try:
        return app.platformName().lower() in _HEADLESS_PLATFORMS
    except Exception:
        return False
