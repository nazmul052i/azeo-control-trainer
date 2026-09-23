"""Application icon.

One place that knows where the icon lives, plus the piece of Windows
housekeeping that makes the taskbar use it.

A Python application on Windows is hosted by ``python.exe``, and the shell
groups taskbar buttons by an Application User Model ID that it infers from the
host executable. Without our own ID the taskbar button belongs to the Python
interpreter and shows the Python icon no matter what ``setWindowIcon`` says.
Claiming an explicit ID before the first window is created fixes it.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

ASSETS = Path(__file__).resolve().parent / "assets"

#: Multi-resolution Windows icon, rebuilt by ``tools/make_icon.py``.
APP_ICON = ASSETS / "azeoplant.ico"

#: 256 px render, for anywhere an ``.ico`` is awkward.
APP_ICON_PNG = ASSETS / "icon_256.png"

APP_USER_MODEL_ID = "Azeo.PlantSimulator.OpenLoop.1"


def claim_taskbar_identity(app_id: str = APP_USER_MODEL_ID) -> bool:
    """Tell Windows we are our own application, not the Python interpreter.

    Safe to call anywhere: it does nothing off Windows, and a failure here is
    cosmetic, so it never propagates.
    """
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        return True
    except Exception:
        log.debug("Could not set the taskbar application id", exc_info=True)
        return False


def available() -> bool:
    return APP_ICON.exists()


def glyph_icon(kind: str, size: int = 20):
    """Small vector-drawn toolbar icons; delegates to the application
    glyph set in :mod:`azeoplant.ui.icons`."""
    from .icons import make_icon

    return make_icon(kind, size)
