"""Centralized PID mode string-to-enum mappings.

Single source of truth for mode conversions used by pid_block.py,
bridge.py, and io_blocks.py.  Import from here instead of defining
local dicts.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from azeo_control_trainer.core.pid.core import Mode

# Lazy-initialized caches (avoid importing PySide6 at module level)
_STR_TO_MODE: dict[str, Mode] | None = None
_MODE_TO_STR: dict[Mode, str] | None = None


def _ensure_maps():
    global _STR_TO_MODE, _MODE_TO_STR
    if _STR_TO_MODE is not None:
        return
    from azeo_control_trainer.core.pid.core import Mode

    _STR_TO_MODE = {
        "OOS":     Mode.OOS,
        "IMAN":    Mode.IMan,
        "LO":      Mode.LO,
        "MANUAL":  Mode.Man,
        "MAN":     Mode.Man,
        "AUTO":    Mode.Auto,
        "CASCADE": Mode.Cas,
        "CAS":     Mode.Cas,
        "RCAS":    Mode.RCas,
        "ROUT":    Mode.ROut,
    }
    _MODE_TO_STR = {
        Mode.OOS:  "OOS",
        Mode.IMan: "IMAN",
        Mode.LO:   "LO",
        Mode.Man:  "MAN",
        Mode.Auto: "AUTO",
        Mode.Cas:  "CAS",
        Mode.RCas: "RCAS",
        Mode.ROut: "ROUT",
    }


def str_to_mode(mode_str: str) -> Mode | None:
    """Convert a mode string (e.g. 'AUTO', 'MAN') to a Mode enum, or None."""
    _ensure_maps()
    return _STR_TO_MODE.get(mode_str.upper())


def mode_to_str(mode_enum: Mode) -> str:
    """Convert a Mode enum to its canonical string representation."""
    _ensure_maps()
    return _MODE_TO_STR.get(mode_enum, "AUTO")
