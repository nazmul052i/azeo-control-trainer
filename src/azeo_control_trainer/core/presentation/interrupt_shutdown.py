"""Graceful console-interrupt handling for Qt product entry points.

Without an explicit handler Python raises ``KeyboardInterrupt`` inside the
arbitrary Qt callback active when Windows delivers Ctrl+C. Qt reports that
callback failure and keeps its event loop alive, so every additional Ctrl+C
only interrupts another timer. This module turns SIGINT into one idempotent
event-loop exit; the ordinary code after ``app.exec()`` still owns runtime,
provider, server, audit, and logging cleanup.
"""
from __future__ import annotations

import logging
import signal
from collections.abc import Callable

from PySide6.QtCore import QTimer

log = logging.getLogger("azeo.shutdown")


class InterruptShutdown:
    """Translate repeated console interrupts into one graceful exit request."""

    def __init__(self, request_exit: Callable[[], None]):
        self._request_exit = request_exit
        self.requested = False

    def handle(self, _signum=None, _frame=None) -> None:
        if self.requested:
            return
        self.requested = True
        log.warning("Ctrl+C received — stopping Azeo cleanly")
        self._request_exit()


def install_interrupt_shutdown(app) -> InterruptShutdown:
    """Install and retain the process SIGINT bridge for ``app``.

    A small timer gives the Python interpreter regular checkpoints while Qt's
    native event loop is idle. It performs no work and stops with the app.
    """
    existing = getattr(app, "_azeo_interrupt_shutdown", None)
    if isinstance(existing, InterruptShutdown):
        return existing

    controller = InterruptShutdown(lambda: app.exit(130))
    signal.signal(signal.SIGINT, controller.handle)

    pulse = QTimer(app)
    pulse.setInterval(100)
    pulse.timeout.connect(lambda: None)
    pulse.start()
    app.aboutToQuit.connect(pulse.stop)

    app._azeo_interrupt_shutdown = controller
    app._azeo_interrupt_pulse = pulse
    return controller
