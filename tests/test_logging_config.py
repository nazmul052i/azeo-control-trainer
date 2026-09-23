"""Persistent system, application, error, audit and Qt log contracts."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from azeo_control_trainer.config.logging_config import (
    APPLICATIONS,
    application_logger,
    audit_event,
    install_qt_message_logging,
    setup_logging,
    shutdown_logging,
)
from azeo_control_trainer.config import logging_config


def _flush() -> None:
    for handler in logging.getLogger().handlers:
        handler.flush()


def test_every_application_and_system_event_has_a_rotating_file(tmp_path) -> None:
    files = setup_logging(
        "control_designer", log_directory=tmp_path, console=False,
        capture_exceptions=False, crash_diagnostics=False,
    )
    try:
        logging.getLogger("strategy.runtime").debug("scan diagnostic")
        logging.getLogger("azeo.graphics_designer.canvas").info(
            "display opened")
        logging.getLogger("azeo.operator_station").warning("station warning")
        application_logger("explorer", "network").error(
            "controller unavailable")
        audit_event("pvm_configurator", "class.save", pvm_class="PID")
        try:
            raise RuntimeError("test exception")
        except RuntimeError:
            application_logger("graphics_designer", "publish").exception(
                "publish failed")
        _flush()

        assert set(files.applications) == set(APPLICATIONS)
        assert all(path.exists() for path in files.applications.values())
        system = files.system.read_text(encoding="utf-8")
        assert "scan diagnostic" in system
        assert "display opened" in system
        assert "station warning" in system
        assert "app=graphics_designer" in system
        assert "pid=" in system and "thread=MainThread" in system

        graphics = files.applications["graphics_designer"].read_text(
            encoding="utf-8")
        assert "display opened" in graphics and "publish failed" in graphics
        assert "station warning" not in graphics
        assert "station warning" in files.applications["operator_station"].read_text(
            encoding="utf-8")
        assert "controller unavailable" in files.applications[
            "explorer"].read_text(encoding="utf-8")

        errors = files.errors.read_text(encoding="utf-8")
        assert "controller unavailable" in errors
        assert "publish failed" in errors and "RuntimeError: test exception" in errors
        assert "station warning" not in errors
        audit = files.audit.read_text(encoding="utf-8")
        assert "event='class.save'" in audit and "pvm_class='PID'" in audit
    finally:
        shutdown_logging()


def test_setup_is_idempotent_and_does_not_duplicate_records(tmp_path) -> None:
    setup_logging(
        "launcher", log_directory=tmp_path, console=False,
        capture_exceptions=False, crash_diagnostics=False,
    )
    files = setup_logging(
        "launcher", log_directory=tmp_path, console=False,
        capture_exceptions=False, crash_diagnostics=False,
    )
    try:
        application_logger("launcher", "test").info("one-record-marker")
        _flush()
        text = files.applications["launcher"].read_text(encoding="utf-8")
        assert text.count("one-record-marker") == 1
    finally:
        shutdown_logging()


def test_rotation_is_safe_while_another_handler_targets_the_journal(
        tmp_path, monkeypatch, capsys) -> None:
    """Two Azeo processes must not race an open Windows log handle."""
    monkeypatch.setattr(logging_config, "MAX_BYTES", 300)
    path = tmp_path / "applications" / "control_designer.log"
    path.parent.mkdir(parents=True)
    formatter = logging.Formatter("%(message)s")
    first = logging_config._rotating(path, formatter)
    second = logging_config._rotating(path, formatter)
    try:
        record = logging.LogRecord(
            "strategy.test", logging.INFO, __file__, 1, "x" * 240, (), None)
        first.emit(record)
        # The second write crosses the size boundary while the first handler
        # owns an open Windows handle. It must keep appending without a noisy
        # logging traceback, then roll normally after the peer closes.
        second.emit(record)
        assert "Logging error" not in capsys.readouterr().err
        assert path.exists() and path.stat().st_size >= 480

        first.close()
        second._rollover_retry_at = 0.0
        second.emit(record)
        assert path.with_suffix(".log.1").exists()
    finally:
        first.close()
        second.close()


def test_qt_diagnostics_are_persisted_instead_of_lost_on_stderr(tmp_path) -> None:
    from PySide6.QtCore import qWarning

    files = setup_logging(
        "graphics_designer", log_directory=tmp_path, console=False,
        capture_exceptions=False, crash_diagnostics=False,
    )
    try:
        assert install_qt_message_logging()
        qWarning("qt-warning-marker")
        _flush()
        assert "Qt: qt-warning-marker" in files.system.read_text(
            encoding="utf-8")
        assert "Qt: qt-warning-marker" in files.applications[
            "graphics_designer"].read_text(encoding="utf-8")
    finally:
        shutdown_logging()


def test_setup_temporarily_reopens_a_globally_disabled_logging_session(
        tmp_path) -> None:
    prior = logging.root.manager.disable
    logging.disable(logging.WARNING)
    files = setup_logging(
        "graphics_designer", log_directory=tmp_path, console=False,
        capture_exceptions=False, crash_diagnostics=False,
    )
    try:
        logging.getLogger("azeo.graphics_designer.canvas").info(
            "reopened-session-marker")
        _flush()
        assert "reopened-session-marker" in files.system.read_text(
            encoding="utf-8")
    finally:
        shutdown_logging()
        assert logging.root.manager.disable == logging.WARNING
        logging.disable(prior)
