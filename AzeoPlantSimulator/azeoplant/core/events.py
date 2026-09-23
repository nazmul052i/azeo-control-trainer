# -*- coding: utf-8 -*-
"""The event journal: a persistent chronology of everything that annunciates.

Alarm activations and returns, acknowledgements, shelves, ESD trips - anything
an operator or an instructor would later ask "when did that happen" about goes
through :meth:`EventJournal.log` and lands in one SQLite table next to the
historian. The journal is an audit record, so it is append-only: nothing in
the application deletes rows except the retention purge.

Writes come from the engine thread (the alarm scan runs as a post-step hook)
and queries from the UI thread, so one connection is shared under a lock.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    t        REAL NOT NULL,
    category TEXT NOT NULL,
    source   TEXT NOT NULL DEFAULT '',
    tag      TEXT NOT NULL DEFAULT '',
    priority TEXT NOT NULL DEFAULT '',
    message  TEXT NOT NULL DEFAULT '',
    value    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_t ON events (t);
CREATE INDEX IF NOT EXISTS idx_events_tag ON events (tag);
"""


@dataclass
class Event:
    t: float
    category: str
    source: str
    tag: str
    priority: str
    message: str
    value: str


class EventJournal:
    """Append-only journal in ``history/events.sqlite``.

    Categories in use: ``ALARM`` (activation), ``RTN`` (return to normal),
    ``ACK``, ``SHELVE``, ``UNSHELVE``, ``TRIP`` and ``TRIP_RESET`` from the
    SIS, ``OPERATOR`` for operator actions, ``SYSTEM`` for everything else.
    """

    def __init__(self, path: Path | str,
                 retention_hours: float = 24 * 14) -> None:
        self.path = Path(path)
        self.retention_s = retention_hours * 3600.0
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.path), check_same_thread=False)
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.executescript(_SCHEMA)
        self._con.commit()
        self._last_purge = 0.0
        log.info("Event journal at %s", self.path)

    # ------------------------------------------------------------------ write
    def log(self, category: str, message: str, *, source: str = "",
            tag: str = "", priority: str = "", value: str = "") -> None:
        now = time.time()
        try:
            with self._lock:
                self._con.execute(
                    "INSERT INTO events VALUES (?,?,?,?,?,?,?)",
                    (now, category, source, tag, priority, message,
                     str(value)))
                self._con.commit()
        except sqlite3.Error:
            # the journal must never take the engine down with it
            log.exception("Event journal write failed")
            return
        if now - self._last_purge > 3600.0:
            self._last_purge = now
            self._purge(now - self.retention_s)

    def _purge(self, older_than: float) -> None:
        try:
            with self._lock:
                self._con.execute("DELETE FROM events WHERE t < ?",
                                  (older_than,))
                self._con.commit()
        except sqlite3.Error:
            log.exception("Event journal purge failed")

    # ------------------------------------------------------------------- read
    def query(self, t0: Optional[float] = None, t1: Optional[float] = None,
              category: Optional[str] = None, like: str = "",
              limit: int = 500) -> List[Event]:
        """Newest first. ``like`` matches tag or message, case-insensitive."""
        sql = "SELECT * FROM events WHERE 1=1"
        args: list = []
        if t0 is not None:
            sql += " AND t >= ?"; args.append(t0)
        if t1 is not None:
            sql += " AND t <= ?"; args.append(t1)
        if category:
            sql += " AND category = ?"; args.append(category)
        if like:
            sql += " AND (tag LIKE ? OR message LIKE ?)"
            args += [f"%{like}%", f"%{like}%"]
        sql += " ORDER BY t DESC LIMIT ?"
        args.append(int(limit))
        try:
            with self._lock:
                rows = self._con.execute(sql, args).fetchall()
        except sqlite3.Error:
            log.exception("Event journal query failed")
            return []
        return [Event(*row) for row in rows]

    def stop(self) -> None:
        with self._lock:
            try:
                self._con.commit()
                self._con.close()
            except sqlite3.Error:
                pass
