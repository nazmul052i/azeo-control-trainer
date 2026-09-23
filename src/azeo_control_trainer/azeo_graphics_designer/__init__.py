"""Public application boundary for Azeo Graphics Designer."""

from __future__ import annotations

from typing import Any

__all__ = ["GraphicsDesignerWindow", "HmiStudioWindow", "create_window", "main"]


def _window_type():
    from .window import HmiStudioWindow

    return HmiStudioWindow


def create_window(*args: Any, **kwargs: Any):
    """Construct Graphics Designer over the shared display/PVM model."""

    return _window_type()(*args, **kwargs)


def main() -> int:
    """Launch Azeo Graphics Designer for the selected/default project."""

    from ..app import main as launch

    return launch(surface="graphics")


def __getattr__(name: str):
    if name in {"GraphicsDesignerWindow", "HmiStudioWindow"}:
        return _window_type()
    raise AttributeError(name)
