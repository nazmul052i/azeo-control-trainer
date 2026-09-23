"""The host probe must not block the GUI callback it measures."""
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from performance_host import BackgroundHostSampler


def test_host_samples_are_owned_by_worker_and_close_joins_it():
    ready = threading.Event()
    threads = []

    class Sampler:
        def sample(self):
            threads.append(threading.get_ident())
            ready.set()
            return {"value": len(threads)}

    worker = BackgroundHostSampler(sampler_factory=Sampler)
    assert ready.wait(2)
    worker.close()
    assert not worker._thread.is_alive()
    assert threads and threading.get_ident() not in threads
    assert worker.drain() == [{"value": 1}]
    assert worker.drain() == []


def test_host_failure_is_visible_without_a_callback_after_close():
    class Sampler:
        def sample(self):
            raise OSError("counter unavailable")

    worker = BackgroundHostSampler(sampler_factory=Sampler)
    worker._thread.join(2)
    worker.close()
    assert worker.error == "counter unavailable"
    assert worker.drain() == []


def test_soak_deadline_finishes_without_claiming_two_hour_qualification(tmp_path):
    from types import SimpleNamespace
    from PySide6.QtCore import QObject
    from PySide6.QtTest import QSignalSpy
    from PySide6.QtWidgets import QApplication
    from operator_soak import OperatorSoak

    app = QApplication.instance() or QApplication([])

    class Run(QObject):
        def __init__(self):
            super().__init__()
            self.host_samples, self.requests, self.metadata = [], [], {}
            self.station = SimpleNamespace(historian=SimpleNamespace(available_points=lambda: {}))
            self.output = tmp_path
            self.finishes = 0

        def inventory(self, _name):
            pass

        def request(self, target):
            self.requests.append(target)

        def finish(self):
            self.finishes += 1

    run = Run()
    soak = OperatorSoak(run, .01)
    soak.start()
    assert QSignalSpy(soak.deadline.timeout).wait(2000)
    assert run.finishes == 1 and soak.finished and not soak.active
    assert not run.metadata["soak"]["duration_and_lifecycle_passed"]
    soak.start()
    assert len(run.requests) == 1
    soak.stop()
    run.deleteLater()
    app.processEvents()
