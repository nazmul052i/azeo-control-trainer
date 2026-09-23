"""Publication discovery never moves acceptance or blocks the GUI on a worker."""
from concurrent.futures import Future
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from azeo_control_trainer.azeo_operator_station.release_catalog import ReleaseCatalogWorker  # noqa: E402


def test_slow_reader_runs_off_owner_and_keeps_only_one_request():
    started, release = threading.Event(), threading.Event()
    threads = []
    def read(root):
        threads.append(threading.get_ident())
        started.set()
        assert release.wait(5)
        return {root: []}
    worker = ReleaseCatalogWorker(read)
    try:
        identity = ("project", "CON-01")
        worker.seed(identity, {"Held": [{"rev": 1}]})
        assert "Held" in worker.poll(identity)
        assert started.wait(5)
        future = worker._future
        for _ in range(20):
            assert "Held" in worker.poll(identity)
            assert worker._future is future
        assert threads == [threads[0]] and threads[0] != threading.get_ident()
        worker.close()
        assert worker.closed and worker.poll(identity) is None
    finally:
        release.set()
        worker.close()
        if worker._future is not None:
            worker._future.result(timeout=5)


class ManualPool:
    def __init__(self):
        self.requests = []
    def submit(self, *args):
        future = Future()
        self.requests.append((args, future))
        return future
    def shutdown(self, **kwargs):
        pass


def controlled_worker():
    worker = ReleaseCatalogWorker()
    worker._pool.shutdown()
    worker._pool = ManualPool()
    return worker


def test_project_and_explicit_refresh_discard_late_results():
    worker = controlled_worker()
    try:
        first, second = ("A", "CON-01"), ("B", "CON-01")
        worker.poll(first)
        stale = worker._future
        worker.seed(first, {"Current": [{"rev": 2}]})
        stale.set_result({"Obsolete": [{"rev": 1}]})
        assert worker.poll(first) == {"Current": [{"rev": 2}]}
        stale = worker._future
        assert worker.poll(second) is None
        stale.set_result({"A": []})
        assert worker.poll(second) is None
        worker._future.set_result({"B": []})
        assert worker.poll(second) == {"B": []}
    finally:
        worker.close()


def test_read_failure_retains_catalog_and_retries_without_stale_error(caplog):
    worker = controlled_worker()
    identity = ("A", "CON-01")
    try:
        worker.seed(identity, {"Held": []})
        worker.poll(identity)
        worker._future.set_exception(OSError("share unavailable"))
        assert worker.poll(identity) == {"Held": []}
        assert worker.error == "share unavailable"
        assert "retaining last catalog" in caplog.text
        worker._future.set_result({"Recovered": []})
        assert worker.poll(identity) == {"Recovered": []}
        assert worker.error == ""
    finally:
        worker.close()


def test_complete_background_catalog_does_not_read_deleted_held_display(tmp_path, monkeypatch):
    from azeo_control_trainer.azeo_operator_station.deployment import PvmDeployment
    from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore
    deployment = PvmDeployment(DisplayStore(tmp_path))
    worker = controlled_worker()
    deployment._catalog_worker = worker
    deployment._accepted["Deleted"] = 1
    worker.seed(deployment._catalog_identity(), {})

    def unexpected(_):
        raise AssertionError("deleted display caused GUI file I/O")
    monkeypatch.setattr(deployment.store, "history", unexpected)
    try:
        with deployment.background_catalog_snapshot():
            assert deployment.displays() == ()
            assert not deployment.pending()
        assert deployment._catalog_history is None
    finally:
        deployment.close()


def test_background_catalog_scope_is_released_on_invalid_publication(tmp_path):
    from azeo_control_trainer.azeo_operator_station.deployment import PvmDeployment
    from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore
    import pytest
    deployment = PvmDeployment(DisplayStore(tmp_path))
    worker = controlled_worker()
    deployment._catalog_worker = worker
    worker.seed(deployment._catalog_identity(), {"Invalid": [{}]})
    try:
        with pytest.raises(KeyError):
            with deployment.background_catalog_snapshot():
                pass
        assert deployment._catalog_history is None and not deployment._catalog_complete
    finally:
        deployment.close()


def test_missing_catalog_is_a_read_failure_and_does_not_create_files(tmp_path):
    from azeo_control_trainer.azeo_operator_station.release_catalog import read_catalog
    import pytest
    root = tmp_path / "disconnected"
    with pytest.raises(FileNotFoundError):
        read_catalog(str(root))
    assert not root.exists()


def test_repeated_close_releases_all_owned_worker_threads():
    threads = []
    for _ in range(10):
        worker = ReleaseCatalogWorker(lambda _: {})
        worker.poll(("project", "CON-01"))
        worker._future.result(timeout=5)
        threads.extend(worker._pool._threads)
        worker.close()
        worker.close()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
