"""Public application boundary for Azeo Explorer."""

from __future__ import annotations

from typing import Any

__all__ = ["ExplorerWindow", "create_window", "create_signal_simulator", "main"]


def create_window(*args: Any, **kwargs: Any):
    """Construct the Explorer shell while keeping its implementation private."""

    from .window import ExplorerWindow

    return ExplorerWindow(*args, **kwargs)


def main() -> int:
    """Launch Azeo Explorer as the primary application."""

    from ..app import main as launch

    return launch(surface="explorer")


def create_signal_simulator(*args: Any, **kwargs: Any):
    """Share the existing input-simulation tool with the process workbench."""
    from .virtual_io_simulator import VirtualIoSimulatorDialog

    return VirtualIoSimulatorDialog(*args, **kwargs)


def __getattr__(name: str):
    if name == "ExplorerWindow":
        from .window import ExplorerWindow

        return ExplorerWindow
    raise AttributeError(name)
