"""Public application boundary for Azeo Control Designer."""

from __future__ import annotations

from typing import Any

__all__ = [
    "ControlDesignerWindow",
    "ControllerExecutive",
    "StrategyCanvas",
    "StrategyDesignerWindow",
    "create_window",
    "main",
]


def _window_type():
    from .designer_window import StrategyDesignerWindow

    return StrategyDesignerWindow


def create_window(*args: Any, **kwargs: Any):
    """Construct the Control Designer shell over the shared control runtime."""

    return _window_type()(*args, **kwargs)


def main() -> int:
    """Launch Azeo Control Designer as the primary application."""

    from ..app import main as launch

    return launch(surface="control")


def __getattr__(name: str):
    if name in {"ControlDesignerWindow", "StrategyDesignerWindow"}:
        return _window_type()
    # Explorer hosts the shared controller executive and shows a loaded module
    # on a canvas; both are part of this boundary, not private modules.
    if name == "ControllerExecutive":
        from .executive import ControllerExecutive

        return ControllerExecutive
    if name == "StrategyCanvas":
        from .designer_tab import StrategyCanvas

        return StrategyCanvas
    raise AttributeError(name)
