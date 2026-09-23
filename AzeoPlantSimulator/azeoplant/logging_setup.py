"""Logging setup.

Three sinks:

* a rotating file under ``logs/`` at DEBUG, which is what you send to whoever is
  debugging;
* the console at INFO;
* an in-memory ring buffer that the Log view reads, so the operator can see what
  the simulator is complaining about without leaving the application.

Logger names follow the package layout, so ``azeoplant.opc`` can be turned up to
DEBUG on its own when the OPC UA session is misbehaving without drowning in
model output.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
import threading
from collections import deque
from pathlib import Path
from typing import Deque, List, Tuple

FORMAT = "%(asctime)s %(levelname)-8s %(name)-22s %(message)s"
DATEFMT = "%Y-%m-%d %H:%M:%S"


class RingBufferHandler(logging.Handler):
    """Thread-safe bounded buffer of formatted records for the UI Log view."""

    def __init__(self, capacity: int = 5000) -> None:
        super().__init__()
        self._buf: Deque[Tuple[int, str]] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._seq = 0
        self.setFormatter(logging.Formatter(FORMAT, DATEFMT))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = self.format(record)
        except Exception:
            return
        with self._lock:
            self._seq += 1
            self._buf.append((record.levelno, text))

    def since(self, marker: int) -> Tuple[int, List[Tuple[int, str]]]:
        """Return records appended since ``marker`` plus the new marker."""
        with self._lock:
            new = self._seq
            if marker >= new:
                return new, []
            count = min(new - marker, len(self._buf))
            return new, list(self._buf)[-count:]

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()


ring_handler = RingBufferHandler()


def setup_logging(log_dir: Path | str = "logs", console_level: int = logging.INFO,
                  file_level: int = logging.DEBUG) -> Path:
    """Configure the root logger. Safe to call more than once."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for h in list(root.handlers):
        root.removeHandler(h)

    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "azeoplant.log"

    file_handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=4_000_000, backupCount=5, encoding="utf-8")
    file_handler.setLevel(file_level)
    file_handler.setFormatter(logging.Formatter(FORMAT, DATEFMT))
    root.addHandler(file_handler)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(console_level)
    console.setFormatter(logging.Formatter("%(levelname)-8s %(name)-22s %(message)s"))
    root.addHandler(console)

    ring_handler.setLevel(logging.DEBUG)
    root.addHandler(ring_handler)

    # asyncua is extremely chatty at INFO and drowns everything else.
    logging.getLogger("asyncua").setLevel(logging.WARNING)
    logging.getLogger("asyncua.server.address_space").setLevel(logging.ERROR)

    logging.getLogger(__name__).info("Logging to %s", path.resolve())
    return path
