"""Large controller inventories must yield to queued operator input."""
import os
from pathlib import Path
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from azeo_control_trainer.azeo_control_designer import executive as module


@pytest.fixture
def rig(monkeypatch):
    app = QApplication.instance() or QApplication([])
    clock, scanned, input_seen, writes = [100.0], [], [], []

    class Runtime:
        is_online = True
        is_debug_paused = False

        def __init__(self, number):
            self.number = number
            self.compiled = SimpleNamespace(graph=SimpleNamespace(scan_ms=500))

        def execute_scan(self, dt):
            scanned.append((self.number, dt))
            clock[0] += 0.020
            if self.number == 0:
                QTimer.singleShot(0, lambda: input_seen.append(len(scanned)))
            return True

    runtimes = [Runtime(i) for i in range(8)]
    store = SimpleNamespace(get_strategy_runtimes=lambda: runtimes,
                            drain_writes=lambda: writes.append(1) or [])
    monkeypatch.setattr(module.time, "perf_counter", lambda: clock[0])
    executive = module.ControllerExecutive(store)
    yield SimpleNamespace(app=app, clock=clock, scanned=scanned, input=input_seen,
                          writes=writes, executive=executive, runtimes=runtimes)
    executive.stop()
    executive.deleteLater()
    app.processEvents()


def test_timer_scan_yields_to_operator_input_between_modules(rig):
    rig.executive.start()
    rig.executive._timer.timeout.emit()
    QTest.qWait(50)
    assert rig.input == [1], "Operator input waited behind the entire module inventory"
    assert [number for number, _ in rig.scanned] == list(range(8))
    assert all(dt == 0.5 for _, dt in rig.scanned)
    assert rig.executive.scan_count == 1
    assert rig.writes == [1]


def test_explicit_scan_remains_synchronous(rig):
    assert rig.executive.scan_once() == 8
    assert [number for number, _ in rig.scanned] == list(range(8))
    assert rig.executive.scan_count == 1
    assert rig.executive.last_work_ms == pytest.approx(160)


def test_pause_cancels_pending_scan_and_resume_keeps_completed_deadlines(rig):
    rig.executive.start()
    rig.executive._timer.timeout.emit()
    rig.executive.stop()
    completed = len(rig.scanned)
    QTest.qWait(30)
    assert completed == 1 and len(rig.scanned) == completed
    assert not rig.executive.is_running
    rig.executive.start()
    rig.executive._timer.timeout.emit()
    QTest.qWait(50)
    assert [number for number, _ in rig.scanned] == list(range(8))


def test_removed_runtime_is_not_scanned_by_a_pending_pass(rig):
    rig.executive.start()
    rig.executive._timer.timeout.emit()
    rig.runtimes[1].is_online = False
    QTest.qWait(50)
    assert [number for number, _ in rig.scanned] == [0, 2, 3, 4, 5, 6, 7]
