"""Native callback fields must be visible to Python's cycle collector."""
import gc
from pathlib import Path
import sys
import weakref

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
import azeoplant  # noqa: F401
from azeoplant.core import native
from azeoplant.core.tags import TagDatabase
from azeoplant.control.strategy import ControlSystem
from azeoplant.control.native_scan import compile_scanner
from azeoplant.models.flowsheet import Flowsheet
from azeoplant.io import VirtualIOBus

pytestmark = pytest.mark.skipif(not native.active(), reason="Requires the native bridge")


def test_default_compiled_controllers_are_collectable():
    def create():
        db = TagDatabase()
        Flowsheet(db, dt=.1)
        controller = ControlSystem(db, VirtualIOBus(db))
        controller._native_scanner = compile_scanner(controller, native.module())
        return weakref.ref(controller)
    refs = [create() for _ in range(4)]
    gc.collect()
    try:
        assert all(ref() is None for ref in refs)
    finally:
        # Keep a failing regression from retaining the native plant at exit.
        for ref in refs:
            if ref() is not None:
                ref()._native_scanner = None
        gc.collect()


def test_loop_spec_callback_cycle_is_collectable():
    def create():
        spec = native.module().LoopSpec()
        spec.pv_fn = lambda *_: spec
        return weakref.ref(spec)
    ref = create()
    gc.collect()
    try:
        assert ref() is None
    finally:
        if ref() is not None:
            ref().pv_fn = None
        gc.collect()
