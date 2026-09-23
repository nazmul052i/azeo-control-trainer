"""A provider read must not hold Python's GIL while waiting for a snapshot."""
from pathlib import Path
import os
import subprocess
import sys
import textwrap

import pytest
import psutil


@pytest.mark.parametrize("method", ["read_sample", "read_samples"])
def test_native_reads_wait_without_blocking_the_lock_owner(method):
    root = Path(__file__).resolve().parents[1]
    source = textwrap.dedent('''
        import sys
        import threading
        sys.path.insert(0, "AzeoPlantSimulator")
        from azeoplant.embedding import create_embedded_plant
        runtime = create_embedded_plant({"autorun": False, "source": "READ-LOCK-TEST", "dt": .1})
        runtime.start()
        attempted = threading.Event()
        done = threading.Event()
        def read():
            attempted.set()
            if sys.argv[1] == "read_sample":
                runtime.read_sample("FT-1001")
            else:
                runtime.read_samples(["FT-1001"])
            done.set()
        worker = threading.Thread(target=read, daemon=True)
        with runtime.db.lock:
            worker.start()
            assert attempted.wait(2)
            # A snapshot file read releases the GIL just like this wait.
            # The reader must let this owner wake and release the database.
            assert not done.wait(.15)
        assert done.wait(2)
        worker.join()
        runtime.stop()
        print("Read and snapshot lock both completed")
    ''')
    process = subprocess.Popen([sys.executable, "-c", source, method], cwd=root,
                               env=dict(os.environ, AZEO_NATIVE="1"),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stdout, stderr = process.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        # Windows' venv executable has a child interpreter. Killing only the
        # launcher leaves that child holding the captured pipes indefinitely.
        for child in psutil.Process(process.pid).children(recursive=True):
            child.kill()
        process.kill()
        process.communicate(timeout=5)
        pytest.fail("Native provider read held the GIL while waiting for the snapshot lock")
    assert process.returncode == 0, stderr
    assert "both completed" in stdout
