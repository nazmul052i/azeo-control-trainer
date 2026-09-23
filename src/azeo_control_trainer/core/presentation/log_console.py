"""Live log console — a System Log viewer for the Simulator Manager.

Streams records from every configured logger (see
``config.logging_config.attach_handler_to_all``) into a colour-coded,
filterable text view.

Threading: log records originate from worker threads (engine scan loop, OPC UA
asyncio thread) as well as the GUI thread. A QWidget must never be touched from
a non-GUI thread, so the ``logging.Handler`` only appends formatted records to a
lock-guarded ring buffer; a QTimer running on the GUI thread drains and renders
them. No Qt objects are touched from worker threads.
"""
from __future__ import annotations

import collections
import html  # noqa: F401  (kept for callers that may pre-escape)
import logging
import threading

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from azeo_control_trainer.config import logging_config
from azeo_control_trainer.config.paths import logs_dir
from .hmi_theme import Colors

# Display order for the level selector.
_LEVELS = [
    ("DEBUG", logging.DEBUG),
    ("INFO", logging.INFO),
    ("WARNING", logging.WARNING),
    ("ERROR", logging.ERROR),
]

# Colour per level (ISA-101 friendly). INFO uses the theme text colour.
_LEVEL_COLORS = {
    logging.DEBUG: "#8A8F99",
    logging.INFO: Colors.TEXT_PRIMARY,
    logging.WARNING: "#C77700",
    logging.ERROR: "#C0392B",
    logging.CRITICAL: "#C0392B",
}

_FMT = "%(asctime)s | %(levelname)-7s | %(name)-28s | %(message)s"
_DATE_FMT = "%H:%M:%S"
_MAX_LINES = 5000          # ring-buffer cap on the visible view
_BUFFER_CAP = 20000        # records held between flushes (bounds memory)
_FLUSH_MS = 200            # GUI render cadence
_MAX_PER_FLUSH = 500       # cap render work per flush under flood


class _BufferingLogHandler(logging.Handler):
    """Thread-safe handler that buffers formatted records for GUI draining."""

    def __init__(self):
        super().__init__()
        self.setFormatter(logging.Formatter(_FMT, datefmt=_DATE_FMT))
        self._pending: collections.deque = collections.deque(maxlen=_BUFFER_CAP)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
        except Exception:  # pragma: no cover - never let logging crash a thread
            return
        with self._lock:
            self._pending.append((record.levelno, msg))

    def drain(self) -> list:
        with self._lock:
            items = list(self._pending)
            self._pending.clear()
        return items


class LogConsoleWidget(QWidget):
    """Colour-coded, filterable live view of all simulator logs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._handler = _BufferingLogHandler()
        self._filter_text = ""
        self._autoscroll = True

        self._build_ui()

        # Subscribe to every configured logger at the selected level.
        logging_config.attach_handler_to_all(
            self._handler, level=self._selected_level())

        self._timer = QTimer(self)
        self._timer.setInterval(_FLUSH_MS)
        self._timer.timeout.connect(self._flush)
        self._timer.start()

    # -- UI ---------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Level:"))
        self._level_combo = QComboBox()
        for name, _lvl in _LEVELS:
            self._level_combo.addItem(name)
        self._level_combo.setCurrentText("INFO")
        self._level_combo.currentIndexChanged.connect(self._on_level_changed)
        bar.addWidget(self._level_combo)

        bar.addSpacing(12)
        bar.addWidget(QLabel("Filter:"))
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("substring (logger or message)…")
        self._filter_edit.textChanged.connect(self._on_filter_changed)
        bar.addWidget(self._filter_edit, 1)

        self._pause_chk = QCheckBox("Pause")
        self._pause_chk.toggled.connect(self._on_pause)
        bar.addWidget(self._pause_chk)

        self._scroll_chk = QCheckBox("Auto-scroll")
        self._scroll_chk.setChecked(True)
        self._scroll_chk.toggled.connect(self._on_autoscroll)
        bar.addWidget(self._scroll_chk)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.clicked.connect(lambda: self._view.clear())
        bar.addWidget(self._clear_btn)

        self._folder_btn = QPushButton("Open Logs Folder")
        self._folder_btn.clicked.connect(self._open_logs_folder)
        bar.addWidget(self._folder_btn)

        root.addLayout(bar)

        self._view = QPlainTextEdit()
        self._view.setReadOnly(True)
        self._view.setMaximumBlockCount(_MAX_LINES)
        self._view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._view.setFont(QFont("Consolas", 9))
        self._view.setStyleSheet(
            f"QPlainTextEdit {{ background: {Colors.BG_SECONDARY};"
            f" color: {Colors.TEXT_PRIMARY}; border: 1px solid #C0C7D0; }}")
        root.addWidget(self._view, 1)

    # -- Controls ---------------------------------------------------------

    def _selected_level(self) -> int:
        return dict(_LEVELS)[self._level_combo.currentText()]

    def _on_level_changed(self, _idx):
        # Capture from this level upward going forward.
        self._handler.setLevel(self._selected_level())

    def _on_filter_changed(self, text: str):
        self._filter_text = text.strip().lower()

    def _on_pause(self, paused: bool):
        if paused:
            self._timer.stop()
        elif self.isVisible():
            self._timer.start()

    def _on_autoscroll(self, on: bool):
        self._autoscroll = on

    def _open_logs_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(logs_dir())))

    # -- Render -----------------------------------------------------------

    def _flush(self):
        items = self._handler.drain()
        if not items:
            return
        if len(items) > _MAX_PER_FLUSH:
            items = items[-_MAX_PER_FLUSH:]

        filt = self._filter_text
        cursor = self._view.textCursor()
        cursor.movePosition(QTextCursor.End)
        appended = False
        for levelno, msg in items:
            if filt and filt not in msg.lower():
                continue
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(_LEVEL_COLORS.get(levelno, Colors.TEXT_PRIMARY)))
            cursor.insertText(msg + "\n", fmt)
            appended = True

        if appended and self._autoscroll:
            sb = self._view.verticalScrollBar()
            sb.setValue(sb.maximum())

    # -- Lifecycle: stop capturing/rendering while hidden -----------------

    def showEvent(self, ev):
        super().showEvent(ev)
        self._handler.setLevel(self._selected_level())
        if not self._pause_chk.isChecked() and not self._timer.isActive():
            self._timer.start()

    def hideEvent(self, ev):
        super().hideEvent(ev)
        self._timer.stop()
        # Drop capture cost while not visible (records would just pile up).
        self._handler.setLevel(logging.CRITICAL + 1)

    def shutdown(self):
        """Detach the handler and stop the timer (call on permanent close)."""
        self._timer.stop()
        logging_config.detach_handler_from_all(self._handler)


class LogViewerWindow(QMainWindow):
    """Standalone System Log window wrapping :class:`LogConsoleWidget`."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("System Log — Process Control Training Simulator")
        self.resize(900, 500)
        self._console = LogConsoleWidget(self)
        self.setCentralWidget(self._console)

    def closeEvent(self, ev):
        # The manager reuses (hides) this window, so keep the handler attached;
        # hideEvent already suppresses capture cost. Detach only on real delete.
        super().closeEvent(ev)
