"""Blocked diagnostics must not block an operator's event loop."""
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from azeo_control_trainer.config import logging_config


def test_slow_journal_does_not_block_the_logging_caller(tmp_path, monkeypatch):
    entered, release, returned = (threading.Event() for _ in range(3))
    original = logging_config._SharedRotatingFileHandler.emit

    def slow_emit(self, record):
        if record.getMessage() == "slow-disk-marker":
            entered.set()
            assert release.wait(5)
        original(self, record)

    logging_config.setup_logging("simulation_workbench", log_directory=tmp_path,
                                console=False, capture_exceptions=False,
                                crash_diagnostics=False)
    monkeypatch.setattr(logging_config._SharedRotatingFileHandler, "emit", slow_emit)
    def produce():
        logging.getLogger("strategy.executive").warning("slow-disk-marker")
        returned.set()
    caller = threading.Thread(target=produce)
    try:
        caller.start()
        assert entered.wait(2)
        assert returned.wait(.2), "Disk logging blocked the controller/UI caller"
    finally:
        release.set()
        caller.join(5)
        logging_config.shutdown_logging()
    assert "slow-disk-marker" in (tmp_path / "system/system.log").read_text()


def test_journal_backlog_is_bounded_and_preserves_audit_and_errors():
    entered, release = threading.Event(), threading.Event()
    messages = []
    class SlowSink(logging.Handler):
        def emit(self, record):
            if record.msg == "hold":
                entered.set()
                assert release.wait(5)
            messages.append(record.getMessage())
    writer = logging_config._JournalWriter([SlowSink()], capacity=2)
    def send(text, level=logging.INFO, **fields):
        record = logging.LogRecord("strategy.test", level, __file__, 1, text, (), None)
        record.__dict__.update(fields)
        writer.handle(record)
    try:
        send("hold")
        assert entered.wait(2)
        for index in range(20):
            send(f"diagnostic-{index}")
        send("audit-marker", audit=True)
        send("error-marker", logging.ERROR)
        assert len(writer._pending) == 4
        assert writer.dropped_diagnostics == 18
    finally:
        release.set()
        writer.close()
    assert "audit-marker" in messages and "error-marker" in messages
    assert any("skipped 18 diagnostic records" in message for message in messages)


def test_model_status_does_not_wait_for_the_simulation_lock():
    # The timeout lives outside the interpreter: a native GIL/mutex deadlock
    # cannot be interrupted by another Python timer in that interpreter.
    source = '''
import sys, threading
sys.path.insert(0, "AzeoPlantSimulator")
from azeoplant.embedding import create_embedded_plant
runtime = create_embedded_plant({"autorun": False, "source": "STATUS-TEST", "dt": .1})
runtime.start()
owned, release, completed = (threading.Event() for _ in range(3))
def hold():
    with runtime.db.lock:
        owned.set()
        release.wait(5)
owner = threading.Thread(target=hold)
owner.start()
assert owned.wait(2)
def read():
    assert len(runtime.simulation_model_state()["units"]) == 10
    assert len(runtime.simulation_model_catalog()["units"]) == 10
    completed.set()
reader = threading.Thread(target=read)
try:
    reader.start()
    assert completed.wait(.2), "Status refresh waited for the running plant lock"
finally:
    release.set()
    reader.join(5)
    owner.join(5)
    runtime.stop()
'''
    result = subprocess.run([sys.executable, "-c", source],
                            cwd=Path(__file__).resolve().parents[1],
                            env=dict(os.environ, AZEO_NATIVE="1"),
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
