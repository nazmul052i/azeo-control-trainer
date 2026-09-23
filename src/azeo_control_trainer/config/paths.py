"""Frozen-aware path resolution for PyInstaller / standalone builds.

When running from source, paths are resolved relative to the repository root
(the directory containing ``src/``, ``data/``, ``ccode/``, etc.).

When running as a PyInstaller frozen executable, ``sys._MEIPASS`` points to the
temporary bundle directory containing all packaged data files, and the "project
root" is redirected there.

Usage::

    from azeo_control_trainer.config.paths import project_root, data_dir, strategies_dir

    layout = data_dir() / "te_pid_layout.json"
    dll    = ccode_dir() / "standalone" / "te_process.dll"
"""
from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path


def is_frozen() -> bool:
    """Return True when running inside a PyInstaller bundle."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


@lru_cache(maxsize=1)
def project_root() -> Path:
    """Return the absolute path to the project root directory.

    * **Source mode** — three levels up from this file
      (``src/azeo_control_trainer/config/paths.py`` → repository root).
    * **Frozen mode** — ``sys._MEIPASS`` (PyInstaller bundle directory).
    """
    if is_frozen():
        return Path(sys._MEIPASS)
    # ``config`` is nested below the package root: parents[3] is the source
    # checkout.  Keeping this explicit prevents a package move from silently
    # redirecting projects, logs, and generated data under ``src``.
    return Path(__file__).resolve().parents[3]


def data_dir() -> Path:
    """``<root>/data/`` — runtime data (layouts, historian DBs, settings)."""
    return workspace_root() / "data"


def strategies_dir() -> Path:
    """``<root>/src/strategies/`` — strategy JSON files."""
    return workspace_root() / "src" / "strategies"


def workspace_root() -> Path:
    """Installed data lives outside program files; source/portable keep their layout."""
    override = os.environ.get("AZEO_WORKSPACE_DIR", "").strip()
    return Path(override).expanduser().resolve() if override else project_root()


def ccode_dir() -> Path:
    """``<root>/ccode/`` — compiled C/C++ artefacts (DLLs)."""
    return project_root() / "ccode"


def logs_dir() -> Path:
    """Writable root for system and per-application log journals.

    Source checkouts keep logs beside the repository for developer access.
    A frozen application's bundle directory is temporary and may be read-only,
    so production builds use the operating system's per-user application-data
    location. ``AZEO_LOG_DIR`` is the supported service/test override.
    """
    override = os.environ.get("AZEO_LOG_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if not is_frozen():
        return workspace_root() / "logs"
    if sys.platform == "win32":
        base = Path(os.environ.get(
            "LOCALAPPDATA", Path.home() / "AppData/Local"))
        return base / "Azeo" / "ControlTrainer" / "Logs"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "Azeo" / "ControlTrainer"
    base = Path(os.environ.get(
        "XDG_STATE_HOME", Path.home() / ".local/state"))
    return base / "azeo" / "control-trainer" / "logs"


def src_dir() -> Path:
    """``<root>/src/`` — Python source tree."""
    return project_root() / "src"


def package_dir() -> Path:
    """Return the installed or source-tree ``azeo_control_trainer`` package."""

    # ``__file__`` is retained inside PyInstaller's extraction directory, so
    # this remains the one reliable answer in source and frozen deployments.
    return Path(__file__).resolve().parent.parent
