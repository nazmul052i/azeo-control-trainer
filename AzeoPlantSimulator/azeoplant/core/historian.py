"""Tag historian.

An open loop simulator has no HMI of its own: the DCS owns the graphics. What
it must provide is the ability to see every variable and to look at how one
moved. That means history, and history has to outlive the process, so it goes
to SQLite rather than a deque in memory.

Recording policy
----------------
Sampling every tag on every engine step would write five thousand rows a
second and tell you almost nothing new, because a lined-out plant barely
moves. The historian samples on a slow cycle and then applies the usual
historian rule: store a point when the value has actually changed by more than
a deadband, when its quality changed, or when the heartbeat expires. A steady
tag costs two rows a minute; a moving one is recorded faithfully.

Threading
---------
The writer owns its own connection on its own thread. Readers get a separate
connection per thread, which SQLite in WAL mode allows to run concurrently
with the writer. Nothing here touches the engine except a snapshot of the tag
database, taken once per sample period.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .tags import TagDatabase, TagKind

log = logging.getLogger(__name__)

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS tag (
    id          INTEGER PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    unit        TEXT,
    kind        TEXT,
    description TEXT,
    eu          TEXT,
    lo          REAL,
    hi          REAL
);
CREATE TABLE IF NOT EXISTS sample (
    tag_id  INTEGER NOT NULL,
    ts      REAL    NOT NULL,
    value   REAL    NOT NULL,
    quality INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS sample_tag_ts ON sample (tag_id, ts);
CREATE TABLE IF NOT EXISTS run (
    id      INTEGER PRIMARY KEY,
    started REAL NOT NULL,
    note    TEXT
);
"""


@dataclass
class HistorianStats:
    running: bool = False
    rows_written: int = 0
    samples_taken: int = 0
    last_write_ms: float = 0.0
    pruned: int = 0
    last_error: str = ""
    path: str = ""


class Historian:
    """Records tag values to SQLite and answers trend queries."""

    def __init__(self, db: TagDatabase, path: Path | str,
                 period: float = 1.0, deadband_pct: float = 0.25,
                 heartbeat: float = 30.0, retention_hours: float = 48.0) -> None:
        self.db = db
        self.path = Path(path)
        self.period = max(float(period), 0.05)
        self.deadband_pct = max(float(deadband_pct), 0.0)
        self.heartbeat = max(float(heartbeat), 1.0)
        self.retention = max(float(retention_hours), 0.0) * 3600.0
        self.stats = HistorianStats(path=str(self.path))

        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._local = threading.local()
        self._ids: Dict[str, int] = {}
        self._last: Dict[str, Tuple[float, int, float]] = {}   # value, quality, ts

    # ------------------------------------------------------------ connections
    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.path), timeout=5.0)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=4000")
        return con

    def _reader(self) -> sqlite3.Connection:
        """One connection per calling thread. WAL lets readers run while writing."""
        con = getattr(self._local, "con", None)
        if con is None:
            con = self._local.con = self._connect()
        return con

    # -------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = self._connect()
        try:
            con.executescript(SCHEMA)
            self._register(con)
            con.execute("INSERT INTO run (started, note) VALUES (?, ?)",
                        (time.time(), "simulator start"))
            con.commit()
        finally:
            con.close()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="historian",
                                        daemon=True)
        self._thread.start()
        log.info("Historian recording to %s every %.1f s", self.path, self.period)

    def stop(self, timeout: float = 4.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                log.warning("Historian did not stop within %.1f s", timeout)
        self.stats.running = False
        log.info("Historian stopped after %d rows", self.stats.rows_written)

    # ----------------------------------------------------------- registration
    def _register(self, con: sqlite3.Connection) -> None:
        """Insert or refresh the tag dictionary, and cache the ids."""
        with self.db.lock:
            rows = [(t.name, t.unit, t.kind.value, t.desc, t.eu,
                     float(t.lo), float(t.hi)) for t in self.db.all()]
        con.executemany(
            "INSERT INTO tag (name, unit, kind, description, eu, lo, hi) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
            "unit=excluded.unit, kind=excluded.kind, description=excluded.description,"
            "eu=excluded.eu, lo=excluded.lo, hi=excluded.hi", rows)
        con.commit()
        self._ids = {name: tid for tid, name in
                     con.execute("SELECT id, name FROM tag").fetchall()}

    # ------------------------------------------------------------------- loop
    def _loop(self) -> None:
        con = self._connect()
        self.stats.running = True
        next_prune = time.monotonic() + 300.0
        try:
            while not self._stop.is_set():
                t0 = time.perf_counter()
                try:
                    self._record(con)
                except Exception as exc:
                    self.stats.last_error = f"{type(exc).__name__}: {exc}"
                    log.exception("Historian write failed")
                self.stats.last_write_ms = (time.perf_counter() - t0) * 1000.0

                if self.retention and time.monotonic() >= next_prune:
                    next_prune = time.monotonic() + 300.0
                    try:
                        self._prune(con)
                    except Exception:
                        log.exception("Historian prune failed")
                self._stop.wait(self.period)
        finally:
            try:
                con.commit()
            finally:
                con.close()
            self.stats.running = False

    def _record(self, con: sqlite3.Connection) -> None:
        snap = self.db.snapshot()
        now = time.time()
        self.stats.samples_taken += 1

        with self.db.lock:
            meta = {t.name: (t.span, t.kind) for t in self.db.all()}

        batch: List[Tuple[int, float, float, int]] = []
        for name, (value, quality, _ts) in snap.items():
            tid = self._ids.get(name)
            if tid is None:
                continue
            v = 1.0 if value is True else 0.0 if value is False else float(value)
            span, kind = meta.get(name, (1.0, TagKind.AI))
            prev = self._last.get(name)
            if prev is not None:
                pv, pq, pts = prev
                if kind in (TagKind.DI, TagKind.DO):
                    moved = v != pv
                else:
                    moved = abs(v - pv) > (self.deadband_pct / 100.0) * abs(span)
                if not moved and quality == pq and (now - pts) < self.heartbeat:
                    continue
            batch.append((tid, now, v, int(quality)))
            self._last[name] = (v, int(quality), now)

        if batch:
            con.executemany(
                "INSERT INTO sample (tag_id, ts, value, quality) VALUES (?,?,?,?)",
                batch)
            con.commit()
            self.stats.rows_written += len(batch)

    def _prune(self, con: sqlite3.Connection) -> None:
        cutoff = time.time() - self.retention
        cur = con.execute("DELETE FROM sample WHERE ts < ?", (cutoff,))
        con.commit()
        if cur.rowcount and cur.rowcount > 0:
            self.stats.pruned += cur.rowcount
            log.info("Historian pruned %d rows older than %.0f h",
                     cur.rowcount, self.retention / 3600.0)

    # ---------------------------------------------------------------- queries
    def query(self, tag: str, t0: float, t1: float,
              max_points: int = 3000) -> List[Tuple[float, float]]:
        """Points for one tag in a window, plus the last point before it.

        The leading point matters: with deadband recording a steady tag may
        have no sample inside the window at all, and without it the trend would
        draw nothing for a variable that is simply not moving.
        """
        con = self._reader()
        tid = self._ids.get(tag)
        if tid is None:
            row = con.execute("SELECT id FROM tag WHERE name=?", (tag,)).fetchone()
            if row is None:
                return []
            tid = self._ids[tag] = row[0]
        rows = con.execute(
            "SELECT ts, value FROM sample WHERE tag_id=? AND ts<? "
            "ORDER BY ts DESC LIMIT 1", (tid, t0)).fetchall()
        rows.reverse()
        rows += con.execute(
            "SELECT ts, value FROM sample WHERE tag_id=? AND ts>=? AND ts<=? "
            "ORDER BY ts", (tid, t0, t1)).fetchall()
        if len(rows) > max_points:                    # decimate, keep the ends
            step = len(rows) / float(max_points)
            keep = [rows[int(i * step)] for i in range(max_points - 1)]
            keep.append(rows[-1])
            rows = keep
        return [(float(a), float(b)) for a, b in rows]

    def span_recorded(self) -> Tuple[float, float]:
        con = self._reader()
        row = con.execute("SELECT MIN(ts), MAX(ts) FROM sample").fetchone()
        if not row or row[0] is None:
            now = time.time()
            return now, now
        return float(row[0]), float(row[1])

    def row_count(self) -> int:
        return int(self._reader().execute(
            "SELECT COUNT(*) FROM sample").fetchone()[0])

    def file_size_mb(self) -> float:
        try:
            return self.path.stat().st_size / (1024 ** 2)
        except OSError:
            return 0.0
