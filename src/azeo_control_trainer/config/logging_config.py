"""Production logging for every Azeo engineering application.

One process can host Control Designer, Explorer, Graphics Designer, the PVM
Configurator, Operator Station, and Simulation Workbench at the same time.
Every record therefore goes
to the system journal and, based on its logger/application identity, to one
application journal. Errors and audit events also get dedicated rollups.

The files are UTF-8, append-only between rotations and safe to initialize more
than once. Native crash diagnostics use a separate ``crash.log`` because
Python's rotating handlers cannot run after the interpreter has faulted.
"""
from __future__ import annotations

import faulthandler
from collections import deque
import copy
import logging
import logging.handlers
import os
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from azeo_control_trainer.config.applications import APPLICATIONS as PRODUCT_APPLICATIONS
from azeo_control_trainer.config.paths import logs_dir

MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 10

APPLICATIONS: Mapping[str, str] = MappingProxyType({
    "launcher": "Azeo Launcher",
    "pvm_configurator": "PVM Configuration Designer",
    **{product.log_name: product.title for product in PRODUCT_APPLICATIONS},
})

_APPLICATION_PREFIXES = (
    ("azeo_control_trainer.azeo_pa_designer", "pa_designer"),
    ("azeo.application.pa_designer", "pa_designer"),
    ("azeo.application.launcher", "launcher"),
    ("azeo.application.control_designer", "control_designer"),
    ("azeo.application.explorer", "explorer"),
    ("azeo.explorer", "explorer"),
    ("azeo.application.graphics_designer", "graphics_designer"),
    ("azeo.graphics_designer", "graphics_designer"),
    ("azeo.pvms.scripting", "graphics_designer"),
    ("azeo.application.pvm_configurator", "pvm_configurator"),
    ("azeo.pvm_configurator", "pvm_configurator"),
    ("azeo.application.operator_station", "operator_station"),
    ("azeo.application.live_station", "operator_station"),
    ("azeo.operator_station", "operator_station"),
    ("azeo.console", "operator_station"),
    ("hmi.history", "operator_station"),
    ("azeo.application.simulation_workbench", "simulation_workbench"),
    ("azeo.simulation", "simulation_workbench"),
    ("strategy", "control_designer"),
    ("fieldio", "control_designer"),
    ("opcua", "control_designer"),
    ("plant", "control_designer"),
    ("PID", "control_designer"),
)

_FORMAT = (
    "%(asctime)s | %(levelname)-8s | app=%(application)-18s | "
    "pid=%(process)d | thread=%(threadName)s | %(name)s | %(message)s"
)
_MANAGED = "_azeo_managed_handler"
_LOCK = threading.RLock()
_PRIMARY_APPLICATION = "launcher"
_CURRENT_FILES: "LogFiles | None" = None
_ORIGINAL_SYS_EXCEPTHOOK = None
_ORIGINAL_THREAD_EXCEPTHOOK = None
_QT_PREVIOUS_HANDLER = None
_QT_HANDLER_INSTALLED = False
_CRASH_STREAM = None
_OWNS_FAULTHANDLER = False
_ORIGINAL_DISABLE_LEVEL: int | None = None


class _JournalWriter(logging.Handler):
    """Keep disk writes/rotation out of controller, plant and Qt callbacks.

    Ordinary diagnostics have a bounded backlog. Audit records and errors
    are retained even during a disk stall; any skipped diagnostics are counted
    and reported when the writer catches up. Only explicit flush/shutdown waits.
    """

    def __init__(self, handlers, capacity=20000):
        super().__init__(logging.DEBUG)
        setattr(self, _MANAGED, True)
        self._handlers = tuple(handlers)
        self._capacity = capacity
        self._pending = deque()
        self._condition = threading.Condition()
        self._accepting = True
        self._active = False
        self.dropped_diagnostics = 0
        self._unreported_drops = 0
        self._thread = threading.Thread(target=self._write, name="journal-writer", daemon=True)
        self._thread.start()

    def emit(self, record):
        captured = copy.copy(record)
        captured.application = _application_for(record)
        # Snapshot arguments now: a scan may mutate them before the writer runs.
        captured.msg, captured.args = record.getMessage(), ()
        if captured.exc_info:
            captured.exc_text = logging.Formatter().formatException(captured.exc_info)
            captured.exc_info = None
        with self._condition:
            if not self._accepting:
                return
            essential = captured.levelno >= logging.ERROR or _AuditFilter().filter(captured)
            if len(self._pending) >= self._capacity and not essential:
                self.dropped_diagnostics += 1
                self._unreported_drops += 1
            else:
                self._pending.append(captured)
            self._condition.notify()

    def _dispatch(self, record):
        for handler in self._handlers:
            if record.levelno >= handler.level:
                try:
                    handler.handle(record)
                except Exception:  # A failed sink must not strand queued audit records.
                    self.handleError(record)

    def _write(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._pending or not self._accepting)
                if not self._pending:
                    return
                record = self._pending.popleft()
                dropped, self._unreported_drops = self._unreported_drops, 0
                self._active = True
            try:
                if dropped:
                    notice = logging.LogRecord("azeo.logging", logging.WARNING, __file__, 0,
                        "Journal backlog: skipped %d diagnostic records; audit and errors retained",
                        (dropped,), None)
                    notice.application = record.application
                    self._dispatch(notice)
                self._dispatch(record)
            finally:
                with self._condition:
                    self._active = False
                    self._condition.notify_all()

    def flush(self):
        if threading.current_thread() is self._thread:
            return
        with self._condition:
            self._condition.wait_for(lambda: not self._pending and not self._active)

    def close(self):
        with self._condition:
            self._accepting = False
            self._condition.notify_all()
        if threading.current_thread() is not self._thread:
            self._thread.join()
        for handler in self._handlers:
            handler.close()
        super().close()


@dataclass(frozen=True)
class LogFiles:
    """Absolute paths comprising one logging session's persistent contract."""

    root: Path
    system: Path
    errors: Path
    audit: Path
    crash: Path
    applications: Mapping[str, Path]


class _IsoFormatter(logging.Formatter):
    """Local ISO-8601 timestamps with milliseconds and UTC offset."""

    def formatTime(self, record, datefmt=None):  # noqa: N802
        return datetime.fromtimestamp(record.created).astimezone().isoformat(
            timespec="milliseconds")

    def format(self, record: logging.LogRecord) -> str:
        if not getattr(record, "application", None):
            record.application = _application_for(record)
        return super().format(record)


class _ApplicationFilter(logging.Filter):
    def __init__(self, application: str):
        super().__init__()
        self.application = application

    def filter(self, record: logging.LogRecord) -> bool:
        return _application_for(record) == self.application


class _AuditFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return bool(getattr(record, "audit", False)) \
            or record.name.startswith("azeo.audit")


class _SharedRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """Fast rotation that tolerates another Azeo process on Windows.

    Explorer, Control Designer, Graphics Designer, and Station can legitimately
    be open together and append to the same system/application journals.
    Windows will not rename a journal while another process has it open, so a
    normal rotating handler prints ``WinError 32`` for every subsequent log
    record.  Defer that maintenance operation while the peer is alive; normal
    append logging continues, and this handler retries after a short cooldown.
    Once the competing process closes, the ordinary numbered rollover runs.
    """

    _RETRY_SECONDS = 30.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._rollover_retry_at = 0.0

    def shouldRollover(self, record: logging.LogRecord) -> bool:  # noqa: N802
        if time.monotonic() < self._rollover_retry_at:
            return False
        return super().shouldRollover(record)

    def doRollover(self) -> None:  # noqa: N802
        try:
            super().doRollover()
        except PermissionError:
            # RotatingFileHandler closes its own stream before the failed
            # rename. Reopen it so the record that triggered maintenance is
            # still persisted; retry later instead of flooding stderr.
            if self.stream is None:
                self.stream = self._open()
            self._rollover_retry_at = time.monotonic() + self._RETRY_SECONDS


def _application_for(record: logging.LogRecord) -> str:
    explicit = str(getattr(record, "application", "") or "")
    if explicit in APPLICATIONS:
        return explicit
    name = record.name
    for prefix, application in _APPLICATION_PREFIXES:
        if name == prefix or name.startswith(prefix + "."):
            return application
    return _PRIMARY_APPLICATION


def _rotating(path: Path, formatter: logging.Formatter, *,
              level: int = logging.DEBUG,
              filter_: logging.Filter | None = None) -> logging.Handler:
    handler = _SharedRotatingFileHandler(
        path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT,
        encoding="utf-8", delay=False,
    )
    setattr(handler, _MANAGED, True)
    handler.setLevel(level)
    handler.setFormatter(formatter)
    if filter_ is not None:
        handler.addFilter(filter_)
    return handler


def _remove_managed_handlers() -> None:
    root = logging.getLogger()
    for handler in tuple(root.handlers):
        if getattr(handler, _MANAGED, False):
            root.removeHandler(handler)
            handler.close()


def _install_exception_hooks() -> None:
    global _ORIGINAL_SYS_EXCEPTHOOK, _ORIGINAL_THREAD_EXCEPTHOOK
    if _ORIGINAL_SYS_EXCEPTHOOK is None:
        _ORIGINAL_SYS_EXCEPTHOOK = sys.excepthook

        def exception_hook(exc_type, value, traceback) -> None:
            logging.getLogger("azeo.unhandled").critical(
                "Uncaught exception on the main thread",
                exc_info=(exc_type, value, traceback),
            )
            _ORIGINAL_SYS_EXCEPTHOOK(exc_type, value, traceback)

        sys.excepthook = exception_hook

    if hasattr(threading, "excepthook") and _ORIGINAL_THREAD_EXCEPTHOOK is None:
        _ORIGINAL_THREAD_EXCEPTHOOK = threading.excepthook

        def thread_hook(args) -> None:
            logging.getLogger("azeo.unhandled.thread").critical(
                "Uncaught exception on thread %s", args.thread.name,
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
            _ORIGINAL_THREAD_EXCEPTHOOK(args)

        threading.excepthook = thread_hook


def _install_crash_diagnostics(path: Path) -> None:
    global _CRASH_STREAM, _OWNS_FAULTHANDLER
    if faulthandler.is_enabled() or _CRASH_STREAM is not None:
        return
    _CRASH_STREAM = path.open("a", encoding="utf-8", buffering=1)
    _CRASH_STREAM.write(
        f"\n--- session {datetime.now().astimezone().isoformat()} "
        f"pid={os.getpid()} ---\n")
    faulthandler.enable(file=_CRASH_STREAM, all_threads=True)
    _OWNS_FAULTHANDLER = True


def setup_logging(application: str = "control_designer", *,
                  log_directory: str | Path | None = None,
                  console: bool = True,
                  capture_exceptions: bool = True,
                  crash_diagnostics: bool = True) -> LogFiles:
    """Install the product's rotating journals and return their paths.

    Repeated calls replace only Azeo-owned handlers, so opening one product
    surface from another never duplicates a record and test/tool handlers are
    preserved. ``log_directory`` exists for diagnostics and isolated tests;
    normal callers use :func:`azeo_control_trainer.config.paths.logs_dir`.
    """
    global _PRIMARY_APPLICATION, _CURRENT_FILES, _ORIGINAL_DISABLE_LEVEL
    if application not in APPLICATIONS:
        choices = ", ".join(APPLICATIONS)
        raise ValueError(f"Unknown Azeo application {application!r}; {choices}")

    with _LOCK:
        # ``logging.disable`` is process-global and is commonly used by a
        # host, test harness, or an earlier embedded surface to suppress
        # noise.  A product logging session must explicitly reopen its own
        # journals or INFO/DEBUG records silently disappear even though all
        # handlers and levels look correct.  Preserve the host state and put
        # it back at shutdown rather than changing it permanently.
        if _ORIGINAL_DISABLE_LEVEL is None:
            _ORIGINAL_DISABLE_LEVEL = logging.root.manager.disable
        logging.disable(logging.NOTSET)
        _PRIMARY_APPLICATION = application
        root_path = Path(log_directory) if log_directory else logs_dir()
        application_dir = root_path / "applications"
        system_dir = root_path / "system"
        application_dir.mkdir(parents=True, exist_ok=True)
        system_dir.mkdir(parents=True, exist_ok=True)

        files = LogFiles(
            root=root_path.resolve(),
            system=(system_dir / "system.log").resolve(),
            errors=(system_dir / "errors.log").resolve(),
            audit=(system_dir / "audit.log").resolve(),
            crash=(system_dir / "crash.log").resolve(),
            applications=MappingProxyType({
                name: (application_dir / f"{name}.log").resolve()
                for name in APPLICATIONS
            }),
        )

        _remove_managed_handlers()
        formatter = _IsoFormatter(_FORMAT)
        root = logging.getLogger()
        root.setLevel(logging.DEBUG)
        journals = [_rotating(files.system, formatter),
                    _rotating(files.errors, formatter, level=logging.ERROR),
                    _rotating(files.audit, formatter, filter_=_AuditFilter())]
        for name, path in files.applications.items():
            journals.append(_rotating(
                path, formatter, filter_=_ApplicationFilter(name)))

        if console:
            console_handler = logging.StreamHandler(sys.stderr)
            setattr(console_handler, _MANAGED, True)
            console_handler.setLevel(logging.WARNING)
            console_handler.setFormatter(formatter)
            journals.append(console_handler)

        root.addHandler(_JournalWriter(journals))

        # Current product loggers all propagate into the one handler graph.
        # Explicitly undo the old configuration's propagate=False state when
        # setup_logging is called twice in a long-lived development process.
        for name in set(prefix for prefix, _app in _APPLICATION_PREFIXES):
            logger = logging.getLogger(name)
            logger.setLevel(logging.DEBUG)
            logger.propagate = True
            for handler in tuple(logger.handlers):
                if getattr(handler, _MANAGED, False):
                    logger.removeHandler(handler)
                    handler.close()
        logging.getLogger("asyncua").setLevel(logging.WARNING)
        logging.getLogger("werkzeug").setLevel(logging.WARNING)

        if capture_exceptions:
            _install_exception_hooks()
        if crash_diagnostics:
            _install_crash_diagnostics(files.crash)

        _CURRENT_FILES = files
        logging.getLogger("azeo.logging").info(
            "Logging initialized: root=%s files=%d rotation=%dMB x %d",
            files.root, len(files.applications) + 4,
            MAX_BYTES // (1024 * 1024), BACKUP_COUNT,
            extra={"application": application},
        )
        for name, title in APPLICATIONS.items():
            logging.getLogger(f"azeo.application.{name}").debug(
                "%s application journal ready: %s", title,
                files.applications[name], extra={"application": name})
        return files


def install_qt_message_logging() -> bool:
    """Route Qt diagnostics into the rotating system/application journals."""
    global _QT_PREVIOUS_HANDLER, _QT_HANDLER_INSTALLED
    if _QT_HANDLER_INSTALLED:
        return False
    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler
    except ImportError:                                  # pragma: no cover
        return False

    levels = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }

    def qt_handler(kind, context, message) -> None:
        source = ""
        if getattr(context, "file", None):
            source = f" ({context.file}:{context.line})"
        logging.getLogger("azeo.qt").log(
            levels.get(kind, logging.INFO), "Qt: %s%s", message, source,
            extra={"application": _PRIMARY_APPLICATION},
        )

    _QT_PREVIOUS_HANDLER = qInstallMessageHandler(qt_handler)
    _QT_HANDLER_INSTALLED = True
    return True


def application_logger(application: str,
                       component: str = "") -> logging.LoggerAdapter:
    """Return a logger that is unambiguously routed to one application."""
    if application not in APPLICATIONS:
        raise ValueError(f"Unknown Azeo application {application!r}")
    suffix = f".{component}" if component else ""
    logger = logging.getLogger(f"azeo.application.{application}{suffix}")
    return logging.LoggerAdapter(logger, {"application": application})


def audit_event(application: str, event: str, *, outcome: str = "success",
                **fields) -> None:
    """Write a structured engineering/operator action to ``audit.log``."""
    details = " ".join(
        f"{key}={str(value)!r}" for key, value in sorted(fields.items()))
    message = f"event={event!r} outcome={outcome!r}"
    if details:
        message += " " + details
    logging.getLogger("azeo.audit").info(
        message, extra={"application": application, "audit": True})


def current_log_files() -> LogFiles | None:
    return _CURRENT_FILES


def attach_handler_to_all(handler: logging.Handler, level=None) -> None:
    """Attach a live viewer once at root; product loggers all propagate."""
    if level is not None:
        handler.setLevel(level)
    root = logging.getLogger()
    if handler not in root.handlers:
        root.addHandler(handler)


def detach_handler_from_all(handler: logging.Handler) -> None:
    root = logging.getLogger()
    if handler in root.handlers:
        root.removeHandler(handler)


def shutdown_logging() -> None:
    """Close only Azeo-owned resources, preserving host/test handlers."""
    global _CURRENT_FILES, _CRASH_STREAM, _OWNS_FAULTHANDLER
    global _ORIGINAL_SYS_EXCEPTHOOK, _ORIGINAL_THREAD_EXCEPTHOOK
    global _QT_PREVIOUS_HANDLER, _QT_HANDLER_INSTALLED
    global _ORIGINAL_DISABLE_LEVEL
    with _LOCK:
        if _QT_HANDLER_INSTALLED:
            try:
                from PySide6.QtCore import qInstallMessageHandler
                qInstallMessageHandler(_QT_PREVIOUS_HANDLER)
            except ImportError:                          # pragma: no cover
                pass
            _QT_PREVIOUS_HANDLER = None
            _QT_HANDLER_INSTALLED = False
        if _ORIGINAL_SYS_EXCEPTHOOK is not None:
            sys.excepthook = _ORIGINAL_SYS_EXCEPTHOOK
            _ORIGINAL_SYS_EXCEPTHOOK = None
        if _ORIGINAL_THREAD_EXCEPTHOOK is not None:
            threading.excepthook = _ORIGINAL_THREAD_EXCEPTHOOK
            _ORIGINAL_THREAD_EXCEPTHOOK = None
        if _OWNS_FAULTHANDLER:
            faulthandler.disable()
            _OWNS_FAULTHANDLER = False
        if _CRASH_STREAM is not None:
            _CRASH_STREAM.close()
            _CRASH_STREAM = None
        _remove_managed_handlers()
        _CURRENT_FILES = None
        if _ORIGINAL_DISABLE_LEVEL is not None:
            logging.disable(_ORIGINAL_DISABLE_LEVEL)
            _ORIGINAL_DISABLE_LEVEL = None
