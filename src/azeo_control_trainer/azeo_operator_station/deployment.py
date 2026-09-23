"""The PVM display store, bound to the shared deployment rules.

`DeploymentRules` in `console/deployment.py` owns *what a console
does with a published revision* — publish marks available, uncached
adopts silently, cached holds until Refresh, an offline station refuses.
This module answers the four store questions for
`pvms.publishing.DisplayStore` and adds nothing else.

That split is deliberate and was learned the hard way: a second
deployment model grew over this store with the rules restated, and the
two would have drifted. The rules live in one place; a store adapts to
them.

**Where the acceptance is recorded.** It lives in `_accepted.json` under
the store root, per workstation — a station that
forgets what it accepted offers the same update again after a restart,
which trains an operator to dismiss the indicator.
"""
from __future__ import annotations

from azeo_control_trainer.core.hmi.compatibility import display_root

import json
import logging
from contextlib import contextmanager
from pathlib import Path

from .shell.deployment import DeploymentRules

log = logging.getLogger("azeo.pvms.deployment")

ACCEPTED_FILE = "_accepted.json"


class PvmDeployment(DeploymentRules):
    """One workstation's view of a `DisplayStore`."""

    def __init__(self, store, workstation: str = "CON-01"):
        self.store = store
        self.workstation = workstation
        self._accepted: dict = {}
        self._catalog_history: dict | None = None
        self._catalog_displays: tuple | None = None
        self._catalog_worker = None
        self._catalog_complete = False
        self._load()

    @contextmanager
    def catalog_snapshot(self):
        """Reuse release reads within one synchronous station refresh only."""
        if self._catalog_history is not None:
            yield
            return
        self._catalog_history = {}
        self._catalog_displays = None
        try:
            yield
            if self._catalog_displays is not None:
                self._worker().seed(self._catalog_identity(), dict(self._catalog_history))
        finally:
            # Synchronous calls always see the next publication. The worker
            # owns a separate completed catalog for recurring timer checks.
            self._catalog_history = self._catalog_displays = None

    def _catalog_identity(self):
        return (str(Path(self.store.root).absolute()), self.workstation)

    def _worker(self):
        if self._catalog_worker is None:
            from .release_catalog import ReleaseCatalogWorker
            self._catalog_worker = ReleaseCatalogWorker()
        return self._catalog_worker

    @contextmanager
    def background_catalog_snapshot(self):
        """Recurring discovery uses completed worker results; explicit reads stay fresh."""
        if self._catalog_history is not None:
            yield
            return
        catalog = self._worker().poll(self._catalog_identity())
        if catalog is None:
            # Initial station setup establishes the first complete catalog.
            # Later ticks retain it while one private file read is pending.
            with self.catalog_snapshot():
                yield
            return
        self._catalog_history = dict(catalog)
        self._catalog_complete = True
        try:
            self._catalog_displays = tuple(name for name in sorted(catalog)
                                           if self._latest(name) is not None)
            yield
        finally:
            self._catalog_history = self._catalog_displays = None
            self._catalog_complete = False

    def invalidate_background_catalog(self):
        if self._catalog_worker is not None:
            self._catalog_worker.invalidate()

    def close(self):
        if self._catalog_worker is not None:
            self._catalog_worker.close()

    def _history(self, display_id: str):
        cache = self._catalog_history
        if cache is None:
            return self.store.history(display_id)
        if self._catalog_complete:
            return cache.get(display_id, [])
        if display_id not in cache:
            cache[display_id] = self.store.history(display_id)
        return cache[display_id]

    # -------------------------------------------- the four questions
    def _latest(self, display_id: str):
        """The newest revision published TO THIS workstation.

        `published_document` already honours per-workstation targeting;
        this needs the number, so it walks the same history.
        """
        for entry in reversed(self._history(display_id)):
            stations = entry.get("workstations")
            if not stations or self.workstation in stations:
                return entry["rev"]
        return None

    def _held(self, display_id: str):
        return self._accepted.get(display_id)

    def _hold(self, display_id: str, revision: int) -> None:
        self._accepted[display_id] = revision
        self._persist()

    def _known(self) -> tuple:
        return tuple(self._accepted)

    # --------------------------------------------------------- documents
    def document(self, display_id: str) -> dict | None:
        """The document this station should be SHOWING.

        Goes through `open()`, so the cached/uncached rule decides which
        revision — not "whatever is newest", which is the shortcut that
        makes a publish redraw a screen under someone's hands.
        """
        revision = self.open(display_id)
        if revision is None:
            return None
        return self.store.revision_document(display_id, revision)

    def monitor_document(self, display_id: str) -> dict | None:
        """Revision used for background alarm monitoring, without opening it.

        An unopened display is not added to ``_accepted`` merely because its
        descendants feed a navigation rollup. Otherwise loading the console
        would make every display look operator-held and the Refresh badge
        would count updates the operator never opened.
        """
        revision = self._held(display_id) or self._latest(display_id)
        if revision is None:
            return None
        return self.store.revision_document(display_id, revision)

    def configuration_root(self, display_id: str):
        """Resolve class/asset files from the revision the operator actually holds."""
        revision = self._held(display_id) or self._latest(display_id)
        entry = next((row for row in self._history(display_id) if row["rev"] == revision), {})
        digest = entry.get("repository_release", {}).get("package_hash", "")
        if digest:
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise ValueError("Invalid published configuration package identity")
            root = display_root(Path(self.store.root) / "_release_packages" / digest)
            if not root.is_dir():
                raise FileNotFoundError("The accepted display's pinned class/asset package is missing")
            return root
        return self.store.root

    def displays(self) -> tuple:
        """Every display published to this station, newest first.

        Reads the store rather than the filesystem: a display with a
        draft but no release is not deployed and must not appear on a
        console, however finished it looks in the studio.
        """
        if self._catalog_displays is not None:
            return self._catalog_displays
        root = Path(self.store.root)
        names = []
        for path in sorted(root.iterdir()) if root.is_dir() else ():
            if not path.is_dir() or path.name.startswith("_"):
                continue
            if self._latest(path.name) is not None:
                names.append(path.name)
        result = tuple(names)
        if self._catalog_history is not None:
            self._catalog_displays = result
        return result

    # ------------------------------------------------------ persistence
    @property
    def _path(self) -> Path:
        return Path(self.store.root) / ACCEPTED_FILE

    def _load(self) -> None:
        path = self._path
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:              # noqa: BLE001
            # A corrupt acceptance record must not take the console
            # down. Starting with nothing accepted means every display
            # adopts on first open — the safe direction, and it says so
            # in the log rather than silently.
            log.error("could not read %s (%s) — nothing accepted",
                      path, error)
            return
        self._accepted = dict(data.get(self.workstation, {}))

    def _persist(self) -> None:
        path = self._path
        data = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
        data[self.workstation] = dict(self._accepted)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            from azeo_control_trainer.core.hmi.pvms.publishing import atomic_write_json
            atomic_write_json(path, data)
        except OSError as error:                            # noqa: BLE001
            log.error("could not record acceptance in %s: %s", path, error)
