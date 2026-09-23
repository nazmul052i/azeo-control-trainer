"""Disk history owned by the shared historian, with I/O off the Qt thread."""
from __future__ import annotations

from collections import deque
from concurrent.futures import CancelledError, ThreadPoolExecutor
from contextlib import closing, contextmanager
import json
import logging
import math
from pathlib import Path
import sqlite3
import threading
import time

import numpy as np

from .plot_data import plot_indices

log = logging.getLogger("hmi.historian.archive")


class HistoryArchive:
    """One ordered writer and one bounded reader; raw samples stay on disk."""

    def __init__(self, path, retention_days=1):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="history-io")
        self._read_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="history-query")
        self._read_tokens = {}
        self._pending = deque()
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._write_future = None
        self._write_points = {}
        self._write_samples = []
        self._write_events = []
        self._write_contexts = []
        self.error = ""
        self.closed = False
        self._last_prune = 0.0
        with self.connect() as db:
            db.execute("PRAGMA auto_vacuum=INCREMENTAL")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS history_meta (key TEXT PRIMARY KEY, data TEXT);
                CREATE TABLE IF NOT EXISTS history_points (path TEXT PRIMARY KEY, data TEXT);
                CREATE TABLE IF NOT EXISTS history_samples (
                    path TEXT, time REAL, value REAL, quality TEXT,
                    wall_time REAL, sim_time REAL, run TEXT,
                    PRIMARY KEY(path, time));
                CREATE INDEX IF NOT EXISTS history_wall_time ON history_samples(wall_time);
                CREATE TABLE IF NOT EXISTS history_events (
                    id TEXT PRIMARY KEY, time REAL, wall_time REAL, sim_time REAL,
                    category TEXT, action TEXT, target TEXT, detail TEXT, run TEXT);
                CREATE INDEX IF NOT EXISTS history_event_time ON history_events(time);
                CREATE TABLE IF NOT EXISTS history_workspaces (name TEXT PRIMARY KEY, data TEXT);
                CREATE TABLE IF NOT EXISTS history_sample_context (
                    path TEXT, time REAL, point_id TEXT NOT NULL, configuration TEXT NOT NULL,
                    PRIMARY KEY(path, time));
                CREATE INDEX IF NOT EXISTS history_context_identity ON history_sample_context(point_id,time);
            """)
            db.execute("INSERT OR IGNORE INTO history_meta VALUES ('origin', ?)", (json.dumps(time.time()),))
            self.origin = float(json.loads(db.execute(
                "SELECT data FROM history_meta WHERE key='origin'").fetchone()[0]))
            row = db.execute("SELECT data FROM history_meta WHERE key='retention_days'").fetchone()
            self.retention_days = int(json.loads(row[0])) if row else int(retention_days)
            row = db.execute("SELECT data FROM history_meta WHERE key='storage_limit_mb'").fetchone()
            self.storage_limit_mb = int(json.loads(row[0])) if row else 1024
            self.available_start, self.available_end = db.execute(
                "SELECT (SELECT min(wall_time) FROM history_samples), (SELECT max(wall_time) FROM history_samples)").fetchone()
            self.points = {row[0]: json.loads(row[1]) for row in db.execute("SELECT * FROM history_points")}
            self.workspaces = {row[0]: json.loads(row[1]) for row in db.execute("SELECT * FROM history_workspaces")}

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            with db:
                yield db

    def _submit(self, function, *args, executor=None):
        if self.closed:
            raise RuntimeError("History archive is closed")
        with self._lock:
            self._pending = deque(future for future in self._pending if not future.done())
            if len(self._pending) >= 128:
                self.error = "History disk queue is full; operation was not queued"
                raise RuntimeError(self.error)
            future = (executor or self._executor).submit(function, *args)
            self._pending.append(future)
        future.add_done_callback(self._completed)
        return future

    def _completed(self, future):
        if future.cancelled():
            return
        error = future.exception()
        if error is not None and not isinstance(error, CancelledError):
            self.error = str(error)
            log.error("History archive operation failed: %s", error)

    def append(self, points, samples, events, *, contexts=()):
        with self._write_lock:
            if len(self._write_samples) + len(samples) > 250000:
                self.error = "History disk writer cannot keep up; live history remains available"
                raise RuntimeError(self.error)
            self._write_points.update(points)
            self._write_samples.extend(samples)
            self._write_events.extend(events)
            self._write_contexts.extend(contexts)
            if self._write_future is None:
                self._write_future = self._submit(self._drain_writes)
            return self._write_future

    def _drain_writes(self):
        try:
            while True:
                with self._write_lock:
                    if not (self._write_points or self._write_samples or self._write_events or self._write_contexts):
                        self._write_future = None
                        return
                    points, samples, events = self._write_points, self._write_samples, self._write_events
                    contexts = self._write_contexts
                    self._write_points, self._write_samples, self._write_events = {}, [], []
                    self._write_contexts = []
                self._append(points, samples, events, contexts)
        except Exception:
            with self._write_lock:
                self._write_future = None
            raise

    def _append(self, points, samples, events, contexts=()):
        with self.connect() as db:
            db.executemany("INSERT OR REPLACE INTO history_points VALUES (?, ?)",
                           [(path, json.dumps(data)) for path, data in points.items()])
            db.executemany("INSERT OR REPLACE INTO history_samples VALUES (?, ?, ?, ?, ?, ?, ?)", samples)
            # The release context travels with each queued sample, not with
            # the latest point metadata; a download can occur before disk I/O.
            db.executemany("INSERT OR REPLACE INTO history_sample_context VALUES (?, ?, ?, ?)", contexts)
            db.executemany("INSERT OR IGNORE INTO history_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           [(r['id'], r['time'], r['wall_time'], r.get('sim_time'), r['category'],
                             r['action'], r.get('target', ''), json.dumps(r.get('detail', ''), default=str),
                             r.get('run', '')) for r in events])
            now = time.time()
            if now - self._last_prune >= 300:
                cutoff = now - self.retention_days * 86400
                # A station may reopen after millions of samples expire. One
                # unbounded DELETE kept the ordered writer busy through the
                # entire Qt shutdown and defeated the boot watchdog.
                db.execute("DELETE FROM history_samples WHERE rowid IN ("
                           "SELECT rowid FROM history_samples WHERE wall_time < ? "
                           "ORDER BY wall_time LIMIT 5000)", (cutoff,))
                db.execute("DELETE FROM history_events WHERE wall_time < ?", (cutoff,))
                pages = db.execute("PRAGMA page_count").fetchone()[0]
                free = db.execute("PRAGMA freelist_count").fetchone()[0]
                page_size = db.execute("PRAGMA page_size").fetchone()[0]
                over_limit = (pages - free) * page_size > self.storage_limit_mb * 1024 * 1024
                if over_limit:
                    db.execute("DELETE FROM history_samples WHERE rowid IN ("
                               "SELECT rowid FROM history_samples ORDER BY wall_time LIMIT 5000)")
                db.execute("PRAGMA incremental_vacuum(1000)")
                db.execute("DELETE FROM history_sample_context WHERE rowid IN ("
                           "SELECT c.rowid FROM history_sample_context c WHERE NOT EXISTS ("
                           "SELECT 1 FROM history_samples s WHERE s.path=c.path AND s.time=c.time) "
                           "LIMIT 5000)")
                pending_expired = db.execute(
                    "SELECT 1 FROM history_samples WHERE wall_time < ? LIMIT 1", (cutoff,)
                ).fetchone() is not None
                # Continue bounded cleanup on later samples without turning
                # one archive append into an unbounded retention job.
                self._last_prune = now - 299 if pending_expired or over_limit else now
            self.available_start, self.available_end = db.execute(
                "SELECT (SELECT min(wall_time) FROM history_samples), (SELECT max(wall_time) FROM history_samples)").fetchone()

    def query(self, paths, start, end, max_points=12000, transform=None, *, metadata=None):
        # A read observes all writes accepted before it, then uses its own WAL
        # transaction. Long review/preview work no longer holds up collection.
        barrier = self._submit(lambda: None)
        token = threading.Event()
        future = self._submit(self._query, tuple(paths), float(start), float(end),
                              max_points, transform, barrier, token, metadata,
                              executor=self._read_executor)
        with self._lock:
            self._read_tokens[future] = token
        future.add_done_callback(self._forget_query)
        return future

    def _forget_query(self, future):
        with self._lock:
            self._read_tokens.pop(future, None)

    def cancel_query(self, future):
        with self._lock:
            token = self._read_tokens.get(future)
        if token is not None:
            token.set()
        return future.cancel()

    def _query(self, paths, start, end, max_points, transform, barrier, token, metadata):
        barrier.result()
        if token.is_set():
            raise CancelledError()
        result = self.read(paths, start, end, max_points, cancel_event=token, metadata=metadata)
        if token.is_set():
            raise CancelledError()
        result = transform(result) if transform else result
        if token.is_set():
            raise CancelledError()
        return result

    def read(self, paths, start, end, max_points=12000, *, cancel_event=None, metadata=None):
        try:
            return self._read(paths, start, end, max_points, cancel_event, metadata)
        except sqlite3.OperationalError:
            if cancel_event is not None and cancel_event.is_set():
                raise CancelledError() from None
            raise

    def _read(self, paths, start, end, max_points, cancel_event, captured_metadata):
        """Return bounded envelopes in time order, preserving extrema and bad samples.

        Reduction affects rendering only. Exports request raw samples explicitly.
        """
        data, statistics, reduced, provenance = {}, {}, False, {}
        with self.connect() as db:
            if cancel_event is not None:
                db.set_progress_handler(lambda: int(cancel_event.is_set()), 1000)
            db.execute("BEGIN")
            for path in paths:
                if cancel_event is not None and cancel_event.is_set():
                    raise CancelledError()
                if captured_metadata is not None and path in captured_metadata:
                    metadata = captured_metadata[path]
                else:
                    metadata = db.execute("SELECT data FROM history_points WHERE path=?", (path,)).fetchone()
                    metadata = json.loads(metadata[0]) if metadata else {}
                point_id = metadata.get("point_id", "")
                source = "history_samples s LEFT JOIN history_sample_context c ON c.path=s.path AND c.time=s.time"
                # Unidentified legacy samples stay readable by their original
                # address. Never infer that they belonged to a new repository ID.
                predicate = "c.point_id=?" if point_id else "s.path=? AND c.point_id IS NULL"
                args = (point_id or metadata.get("legacy_path") or path, start, end)
                where = " WHERE " + predicate + " AND s.time BETWEEN ? AND ?"
                count = db.execute("SELECT count(*) FROM " + source + where, args).fetchone()[0]
                chunk_size = max(1, math.ceil(count / max_points)) if max_points else 1
                reduced |= chunk_size > 1
                cursor = db.execute("SELECT s.time,s.value,s.quality,s.wall_time,s.sim_time,s.run FROM "
                                    + source + where + " ORDER BY s.time", args)
                provenance[path] = [dict(path=r[0], start=r[1], end=r[2], point_id=point_id,
                                         configuration=json.loads(r[3])) for r in db.execute(
                    "SELECT s.path,min(s.time),max(s.time),c.configuration FROM " + source + where
                    + " AND c.point_id IS NOT NULL GROUP BY s.path,c.configuration ORDER BY min(s.time)", args)] if point_id else []
                rows = []
                good_count, total, low, high, first, last = 0, 0.0, math.inf, -math.inf, None, None
                current = math.nan
                # Envelope buckets can contain only a few rows. Fetching each
                # one separately made a ten-pen day require 108,000 SQLite/Python
                # crossings; reduce complete buckets inside a bounded batch.
                fetch_size = max(chunk_size, (8192 // chunk_size) * chunk_size)
                while batch := cursor.fetchmany(fetch_size):
                    if cancel_event is not None and cancel_event.is_set():
                        raise CancelledError()
                    current = batch[-1][1] if batch[-1][1] is not None else math.nan
                    values = np.fromiter((r[1] if r[1] is not None else math.nan for r in batch),
                                         dtype=float, count=len(batch))
                    qualities = np.asarray([r[2] for r in batch], dtype=str)
                    good = values[np.isfinite(values) & (qualities == "GOOD")]
                    if good.size:
                        good_count += int(good.size)
                        total += float(good.sum())
                        low, high = min(low, float(good.min())), max(high, float(good.max()))
                        first = float(good[0]) if first is None else first
                        last = float(good[-1])
                    if len(batch) < 3 or chunk_size == 1:
                        rows.extend(batch)
                        continue
                    selected = plot_indices(values, qualities, 4 * math.ceil(len(batch) / chunk_size))
                    run_changes = [i for i in range(1, len(batch)) if batch[i][5] != batch[i - 1][5]]
                    if run_changes:
                        selected = np.union1d(selected, [j for i in run_changes for j in (i - 1, i)])
                    rows.extend(batch[int(i)] for i in selected)
                data[path] = rows
                statistics[path] = dict(current=current, count=good_count,
                                        minimum=low if good_count else math.nan,
                                        maximum=high if good_count else math.nan,
                                        average=total / good_count if good_count else math.nan,
                                        delta=last - first if good_count else math.nan)
            events = [dict(id=r[0], time=r[1], wall_time=r[2], sim_time=r[3], category=r[4],
                           action=r[5], target=r[6], detail=json.loads(r[7]), run=r[8])
                      for r in db.execute("SELECT * FROM history_events WHERE time BETWEEN ? AND ? ORDER BY time, id", (start, end))]
        return {"samples": data, "statistics": statistics, "events": events,
                "reduced": reduced, "start": start, "end": end, "provenance": provenance}

    def save_workspace(self, name, state):
        encoded = json.dumps(state, allow_nan=False)
        self.workspaces[name] = json.loads(encoded)
        return self._submit(self._save_workspace, name, encoded)

    def _save_workspace(self, name, encoded):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO history_workspaces VALUES (?, ?)", (name, encoded))

    def set_retention(self, days, storage_limit_mb=None):
        days = int(days)
        if not 1 <= days <= 365:
            raise ValueError("History retention must be between 1 and 365 days")
        self.retention_days = days
        if storage_limit_mb is not None:
            if not 64 <= int(storage_limit_mb) <= 16384:
                raise ValueError("History disk budget must be between 64 and 16384 MB")
            self.storage_limit_mb = int(storage_limit_mb)
        return self._submit(self._save_retention, days)

    def _save_retention(self, days):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO history_meta VALUES ('retention_days', ?)", (json.dumps(days),))
            db.execute("INSERT OR REPLACE INTO history_meta VALUES ('storage_limit_mb', ?)", (json.dumps(self.storage_limit_mb),))
        self._last_prune = 0

    def flush(self, timeout=10):
        if not self.closed:
            self._submit(lambda: None).result(timeout=timeout)

    def close(self):
        if not self.closed:
            self.closed = True
            with self._lock:
                tokens = list(self._read_tokens.values())
            for token in tokens:
                token.set()
            self._read_executor.shutdown(wait=True, cancel_futures=True)
            self._executor.shutdown(wait=True)
