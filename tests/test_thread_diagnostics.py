"""Diagnostic probes keep attribution and the wrapped operation's semantics."""
from pathlib import Path
import sys
import threading

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from thread_diagnostics import ObservedLock, ThreadDiagnostics  # noqa: E402


def test_attribution_keeps_threads_separate_and_bounds_samples():
    recorder = ThreadDiagnostics(limit=2)
    recorder.record("same", .001, .0001)

    def work():
        for _ in range(5):
            recorder.record("same", .002, .001)
    worker = threading.Thread(target=work, name="diagnostic-test")
    worker.start()
    worker.join()
    rows = recorder.export()["rows"]
    assert len(rows) == 2
    main, background = rows
    assert main["thread_id"] != background["thread_id"]
    assert main["count"] == 1 and background["count"] == 5
    assert len(background["samples"]) == 2
    assert background["wall_total_ms"] == 10
    assert background["cpu_total_ms"] == 5


def test_probe_restores_method_and_preserves_exceptions():
    class Operation:
        def work(self):
            raise ValueError("same exception")
    original = Operation.work
    recorder = ThreadDiagnostics()
    recorder.measure(Operation, "work")
    try:
        with pytest.raises(ValueError, match="same exception"):
            Operation().work()
        assert recorder.export()["rows"][0]["count"] == 1
    finally:
        recorder.close()
    assert Operation.work is original


def test_observed_lock_preserves_recursion_exceptions_and_failed_acquisition():
    recorder = ThreadDiagnostics()
    lock = ObservedLock(threading.RLock(), recorder)
    rejected = []
    with pytest.raises(ValueError):
        with lock:
            with lock:
                worker = threading.Thread(target=lambda: rejected.append(lock.acquire(False)))
                worker.start()
                worker.join()
                raise ValueError()
    assert rejected == [False]
    rows = recorder.export()["rows"]
    assert {r["operation"]: r["count"] for r in rows} == {
        "provider:lock_wait": 1, "provider:lock_hold": 1}
    assert lock.acquire(False)
    lock.release()
