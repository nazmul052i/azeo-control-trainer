"""Ctrl+C requests one graceful Qt-loop exit instead of escaping callbacks."""
from __future__ import annotations

import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from azeo_control_trainer.core.presentation.interrupt_shutdown import (  # noqa: E402
    InterruptShutdown,
    install_interrupt_shutdown,
)


def test_repeated_interrupts_request_only_one_exit():
    exits = []
    controller = InterruptShutdown(lambda: exits.append(130))

    controller.handle()
    controller.handle()

    assert controller.requested
    assert exits == [130]


def test_qt_interrupt_bridge_is_retained_and_has_a_python_pulse(monkeypatch):
    app = QApplication.instance() or QApplication([])
    installed = []
    monkeypatch.setattr(
        "azeo_control_trainer.core.presentation.interrupt_shutdown.signal.signal",
        lambda signum, handler: installed.append((signum, handler)),
    )
    if hasattr(app, "_azeo_interrupt_shutdown"):
        del app._azeo_interrupt_shutdown
    if hasattr(app, "_azeo_interrupt_pulse"):
        del app._azeo_interrupt_pulse

    controller = install_interrupt_shutdown(app)

    assert installed and installed[0][1] == controller.handle
    assert app._azeo_interrupt_pulse.isActive()
    assert install_interrupt_shutdown(app) is controller
    app._azeo_interrupt_pulse.stop()
