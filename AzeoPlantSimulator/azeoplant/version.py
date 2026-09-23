# -*- coding: utf-8 -*-
"""One place that knows what build this is."""

from __future__ import annotations

import subprocess
from pathlib import Path

__version__ = "1.0.0"

_ROOT = Path(__file__).resolve().parents[1]


def build_info() -> str:
    """``1.0.0 (abc1234)`` when git is at hand, plain version otherwise."""
    try:
        rev = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=_ROOT,
            capture_output=True, text=True, timeout=3).stdout.strip()
        return f"{__version__} ({rev})" if rev else __version__
    except (OSError, subprocess.SubprocessError):
        return __version__
