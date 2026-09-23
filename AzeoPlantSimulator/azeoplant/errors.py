"""Exception types and the global exception hook.

The rule this project follows: an unexpected exception must never silently kill a
thread and must never take the whole application down without telling anyone.
The engine catches per-unit failures and degrades to a safe frozen state; the Qt
hook reports anything that escapes.
"""

from __future__ import annotations

import logging
import sys
import traceback
from types import TracebackType
from typing import Callable, Optional, Type

log = logging.getLogger(__name__)


class PctuError(Exception):
    """Base class for every error this application raises deliberately."""


class ConfigError(PctuError):
    """Malformed or missing configuration."""


class ModelError(PctuError):
    """A process model failed to build or step."""

    def __init__(self, unit: str, message: str) -> None:
        super().__init__(f"[{unit}] {message}")
        self.unit = unit


class OpcServerError(PctuError):
    """The OPC UA server could not start or lost its address space."""


class EngineStalled(PctuError):
    """The simulation engine exceeded its consecutive-error budget and froze."""


def install_excepthook(notify: Optional[Callable[[str, str], None]] = None) -> None:
    """Route uncaught exceptions to the log and, optionally, to the UI.

    ``notify(title, detail)`` is called on the thread that raised, so a Qt
    implementation must marshal to the GUI thread itself.
    """

    previous = sys.excepthook

    def _hook(exc_type: Type[BaseException], exc: BaseException,
              tb: Optional[TracebackType]) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            previous(exc_type, exc, tb)
            return
        detail = "".join(traceback.format_exception(exc_type, exc, tb))
        log.critical("Unhandled exception:\n%s", detail)
        if notify is not None:
            try:
                notify(f"{exc_type.__name__}: {exc}", detail)
            except Exception:            # notification must never mask the error
                log.exception("Failed to report unhandled exception to the UI")

    sys.excepthook = _hook

    def _thread_hook(args) -> None:      # threading.excepthook signature
        _hook(args.exc_type, args.exc_value, args.exc_traceback)

    import threading
    threading.excepthook = _thread_hook
