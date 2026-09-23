"""Passing provider assertions must also leave a clean native process exit."""
import os
from pathlib import Path
import subprocess
import sys


def test_native_provider_process_exits_after_snapshot_and_logging_use():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([
        sys.executable, "-X", "faulthandler", "-m", "pytest", "-q",
        "AzeoPlantSimulator/tests/test_native_embedding.py",
        "AzeoPlantSimulator/tests/test_embedding.py",
        "AzeoPlantSimulator/tests/test_write_queue_backpressure.py",
    ], cwd=root, env=dict(os.environ, AZEO_NATIVE="1", QT_QPA_PLATFORM="offscreen"),
        capture_output=True, text=True, errors="replace", timeout=90)
    # The original run printed ten passes, then faulted in native teardown.
    # Checking pytest's summary alone would incorrectly certify that build.
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert "passed" in result.stdout
