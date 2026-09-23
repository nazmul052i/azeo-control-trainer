"""Public application boundary for Azeo Simulation Workbench."""

from __future__ import annotations

from typing import Any

__all__ = [
    "SimulationWorkbenchDialog",
    "SimulationWorkbenchWindow",
    "create_window",
    "main",
]


def _window_type():
    from .window import SimulationWorkbenchDialog

    return SimulationWorkbenchDialog


def create_window(*args: Any, **kwargs: Any):
    """Construct the workbench over a configured simulation provider."""

    return _window_type()(*args, **kwargs)


def main() -> int:
    """Launch Azeo Simulation Workbench for the selected/default project."""

    from ..app import main as launch

    return launch(surface="simulation")


def __getattr__(name: str):
    if name in {"SimulationWorkbenchDialog", "SimulationWorkbenchWindow"}:
        return _window_type()
    raise AttributeError(name)
