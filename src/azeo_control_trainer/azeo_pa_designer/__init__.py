"""Public, lazy application boundary for Azeo PA Designer."""
from __future__ import annotations

__all__ = ["PADesignerWindow", "create_window", "main"]


def create_window(*args, **kwargs):
    from .window import PADesignerWindow

    return PADesignerWindow(*args, **kwargs)


def main():
    from ..app import main as launch

    return launch(surface="procedures")


def __getattr__(name):
    if name == "PADesignerWindow":
        from .window import PADesignerWindow

        return PADesignerWindow
    raise AttributeError(name)
