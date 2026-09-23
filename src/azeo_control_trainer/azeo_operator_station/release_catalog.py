"""Read-only release discovery, isolated from Qt and operator acceptance."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
from pathlib import Path

log = logging.getLogger("azeo.pvms.deployment")


def read_catalog(root: str) -> dict:
    from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore
    store = DisplayStore(root)
    directory = Path(root)
    return {path.name: store.history(path.name)
            for path in sorted(directory.iterdir()) if path.is_dir()
            and not path.name.startswith("_")}


class ReleaseCatalogWorker:
    """One pending read, immutable request identity, no GUI callbacks or writes.

    Only the owning GUI thread calls poll/seed/close. The worker receives a
    path string and returns a private JSON tree; it never sees a live station,
    DeploymentRules, accepted revisions, widgets or controller graphs.
    """
    def __init__(self, reader=read_catalog):
        self._reader = reader
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="release-catalog")
        self._future = None
        self._request = None
        self._identity = None
        self._generation = 0
        self._catalog = None
        self.closed = False
        self.error = ""

    def seed(self, identity, catalog):
        # Explicit retrieval supersedes any older in-flight read, even if it
        # completes after the operator accepts the current publication.
        self._identity = identity
        self._generation += 1
        self._catalog = catalog
        self.error = ""

    def invalidate(self):
        self._generation += 1
        self._catalog = None

    def poll(self, identity):
        if self.closed:
            return None
        if identity != self._identity:
            self.seed(identity, None)
        future = self._future
        if future is not None and future.done():
            request, self._request = self._request, None
            self._future = None
            try:
                catalog = future.result()  # done() above; never waits for I/O
            except Exception as error:  # noqa: BLE001 - retry without blocking the station
                if request == (identity, self._generation):
                    self.error = str(error)
                    log.warning("Release discovery failed; retaining last catalog: %s", error)
            else:
                if request == (identity, self._generation):
                    self._catalog = catalog
                    self.error = ""
        if self._future is None:
            self._request = (identity, self._generation)
            self._future = self._pool.submit(self._reader, identity[0])
        return self._catalog

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.invalidate()
        if self._future is not None:
            self._future.cancel()
        # Running file I/O owns no Qt objects. Let it finish without holding
        # the close event or delivering a callback into a deleted station.
        self._pool.shutdown(wait=False, cancel_futures=True)
