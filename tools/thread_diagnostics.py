"""Opt-in, bounded thread attribution for the disposable Operator verifier.

Wall spans include descendant calls. Thread CPU separates execution from time
not scheduled; that difference alone cannot distinguish GIL and lock waits.
"""
from __future__ import annotations

from collections import deque
from functools import wraps
from pathlib import Path
import sys
import threading
import time


class ThreadDiagnostics:
    def __init__(self, enabled=lambda: True, limit=4096):
        self.enabled = enabled
        self.limit = limit
        self.rows = {}
        self.installed = []
        self.pending = {}

    def record(self, name, wall, cpu=0.0):
        if not self.enabled():
            return
        key = (threading.get_ident(), threading.current_thread().name, name)
        row = self.rows.setdefault(key, {
            "count": 0, "wall_total_ms": 0., "cpu_total_ms": 0.,
            "wall_max_ms": 0., "samples": deque(maxlen=self.limit)})
        wall, cpu = wall * 1000, cpu * 1000
        row["count"] += 1
        row["wall_total_ms"] += wall
        row["cpu_total_ms"] += cpu
        row["wall_max_ms"] = max(row["wall_max_ms"], wall)
        row["samples"].append((wall, cpu))

    def patch(self, kind, name, replacement):
        original = getattr(kind, name)
        self.installed.append((kind, name, original))
        setattr(kind, name, replacement(original))

    def measure(self, kind, name, label=None):
        def decorate(original):
            @wraps(original)
            def call(obj, *args, **kwargs):
                if not self.enabled():
                    return original(obj, *args, **kwargs)
                key = label(obj) if label else f"{kind.__name__}.{name}"
                wall, cpu = time.perf_counter(), time.thread_time()
                try:
                    return original(obj, *args, **kwargs)
                finally:
                    self.record(key, time.perf_counter() - wall, time.thread_time() - cpu)
            return call
        self.patch(kind, name, decorate)

    def install(self):
        from azeo_control_trainer.azeo_control_designer.executive import ControllerExecutive
        from azeo_control_trainer.core.strategy.engine.runtime import StrategyRuntime
        from azeo_control_trainer.connectivity.fieldio.local_virtual_io import LocalVirtualIoDriver
        from azeo_control_trainer.core.hmi.pvms.render import PvmFaceplateWidget
        from azeo_control_trainer.core.hmi.history.archive import HistoryArchive
        from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore

        self.measure(StrategyRuntime, "execute_scan",
                     lambda obj: f"module:{obj.compiled.graph.name}")
        for kind, names in (
            (ControllerExecutive, ("_advance_scan",)),
            (LocalVirtualIoDriver, ("scan_once", "_read_many", "_drain_store_writes")),
            (PvmFaceplateWidget, ("refresh", "_refresh_write_permissions")),
            (HistoryArchive, ("_drain_writes",)),
            (DisplayStore, ("history",)),
        ):
            for name in names:
                self.measure(kind, name)

        def continuation(original):
            @wraps(original)
            def call(obj, *args, **kwargs):
                queued = self.pending.pop(id(obj), None)
                if queued is not None:
                    self.record("controller:continuation_wait", time.perf_counter() - queued)
                try:
                    return original(obj, *args, **kwargs)
                finally:
                    if obj._continuation.isActive():
                        self.pending[id(obj)] = time.perf_counter()
            return call

        def cancel(original):
            @wraps(original)
            def call(obj, *args, **kwargs):
                self.pending.pop(id(obj), None)
                return original(obj, *args, **kwargs)
            return call

        self.patch(ControllerExecutive, "_continue_scan", continuation)
        self.patch(ControllerExecutive, "_cancel_pending_scan", cancel)
        # The verifier explicitly uses the bundled APVC fixture. Product code
        # continues to reach its configured provider only through field I/O.
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "AzeoPlantSimulator"))
        from azeoplant.core.engine import SimulationEngine
        from azeoplant.core.tags import TagDatabase
        self.measure(SimulationEngine, "_execute_step")

        def database(original):
            @wraps(original)
            def call(obj, *args, **kwargs):
                original(obj, *args, **kwargs)
                obj.lock = ObservedLock(obj.lock, self)
            return call
        self.patch(TagDatabase, "__init__", database)

    def close(self):
        for kind, name, original in reversed(self.installed):
            setattr(kind, name, original)
        self.installed.clear()
        self.pending.clear()

    def export(self):
        # Called after disabling recording; one thread owns each row.
        return {"scope": "inclusive wall and current-thread CPU; last bounded samples per key",
                "sample_limit": self.limit,
                "rows": [{"thread_id": tid, "thread_name": thread, "operation": operation,
                          **{k: list(v) if k == "samples" else v for k, v in row.items()}}
                         for (tid, thread, operation), row in list(self.rows.items())]}


class ObservedLock:
    """Keep the actual RLock and report outer acquisition wait and hold time."""
    def __init__(self, lock, recorder):
        self.lock = lock
        self.recorder = recorder
        self.local = threading.local()

    def acquire(self, *args, **kwargs):
        depth = getattr(self.local, "depth", 0)
        observed = not depth and self.recorder.enabled()
        if observed:
            started, cpu = time.perf_counter(), time.thread_time()
        acquired = self.lock.acquire(*args, **kwargs)
        if acquired:
            self.local.depth = depth + 1
            if not depth:
                self.local.hold = None
                if observed:
                    finished, end_cpu = time.perf_counter(), time.thread_time()
                    self.recorder.record("provider:lock_wait", finished - started, end_cpu - cpu)
                    self.local.hold = (finished, end_cpu)
        return acquired

    def release(self):
        depth = getattr(self.local, "depth", 0)
        hold = getattr(self.local, "hold", None) if depth == 1 else None
        finished, cpu = (time.perf_counter(), time.thread_time()) if hold else (0., 0.)
        self.lock.release()
        self.local.depth = depth - 1
        if hold:
            self.local.hold = None
            self.recorder.record("provider:lock_hold", finished - hold[0], cpu - hold[1])

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        self.release()

    def __getattr__(self, name):
        return getattr(self.lock, name)
