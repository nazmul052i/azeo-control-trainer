"""Public application boundary for Azeo Operator Station."""

from __future__ import annotations

from typing import Any

__all__ = [
    "AlarmFilter",
    "ConsoleSettings",
    "PvmDeployment",
    "LiveStation",
    "OperatorStationWindow",
    "UNAVAILABLE",
    "create_window",
    "main",
]


def _station_api():
    from .console import LiveStation, UNAVAILABLE
    from .deployment import PvmDeployment
    from .dialogs import AlarmFilter
    from .shell.chrome import ConsoleSettings

    return {
        "AlarmFilter": AlarmFilter,
        "ConsoleSettings": ConsoleSettings,
        "PvmDeployment": PvmDeployment,
        "LiveStation": LiveStation,
        "OperatorStationWindow": LiveStation,
        "UNAVAILABLE": UNAVAILABLE,
    }


def create_window(*args: Any, **kwargs: Any):
    """Construct the published-display operator runtime."""

    return _station_api()["OperatorStationWindow"](*args, **kwargs)


def main() -> int:
    """Launch Azeo Operator Station as the primary application."""

    from ..app import main as launch

    return launch(surface="station")


def __getattr__(name: str):
    try:
        return _station_api()[name]
    except KeyError as error:
        raise AttributeError(name) from error
