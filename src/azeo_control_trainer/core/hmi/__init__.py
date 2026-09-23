"""Azeo HMI — the operator-graphics half of the trainer.

``pvms/`` is the one shipping display stack: Graphics Designer authors
``PvmDisplay`` documents and the station renders published revisions of
that same format. ``console/``, ``model/``, ``theme/``, ``history/`` and
``assets/`` are shared core services. The former DynaLive stack is archived
outside ``src`` and is not an alternate runtime or import path.
"""
from __future__ import annotations

from importlib import import_module
from importlib.abc import Loader, MetaPathFinder
from importlib.util import find_spec, spec_from_loader
import sys

from .compatibility import LEGACY_PVM_COLLECTION_KEY


class _PvmAliasLoader(Loader):
    """Return the canonical module object for a pre-rename import."""

    def __init__(self, target: str):
        self.target = target

    def create_module(self, spec):
        return import_module(self.target)

    def exec_module(self, module) -> None:
        return None


class _PvmAliasFinder(MetaPathFinder):
    """Keep the previous package importable without loading code twice."""

    current = __name__ + ".pvms"
    previous = __name__ + "." + LEGACY_PVM_COLLECTION_KEY

    def find_spec(self, fullname, path=None, target=None):
        if fullname != self.previous and not fullname.startswith(self.previous + "."):
            return None
        canonical = self.current + fullname[len(self.previous):]
        canonical_spec = find_spec(canonical)
        if canonical_spec is None:
            return None
        is_package = canonical_spec.submodule_search_locations is not None
        return spec_from_loader(fullname, _PvmAliasLoader(canonical), is_package=is_package)


if not any(isinstance(finder, _PvmAliasFinder) for finder in sys.meta_path):
    sys.meta_path.insert(0, _PvmAliasFinder())
