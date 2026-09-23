"""The native tag database under the threads the application runs.

The engine steps on its own thread, the OPC UA publisher reads every input
under ``with db.lock:`` on another, and the operator window calls
``db.snapshot()`` from the GUI thread. On 2026-09-06 the window froze on
exactly that: a thread inside ``with db.lock:`` gives the GIL up at the
interpreter's switch interval, the GUI thread took it and blocked in
``snapshot()`` waiting for the database lock WITH the GIL held, and the
lock's holder could never get the GIL back to release it. The rule in the
binding is that no native code waits for the database lock while holding
the GIL (``acquire_without_gil`` in ``cpp/src/py_tags.cpp``).

This test runs the three threads for 30 simulated-wall seconds and fails if
any two of them stop making progress. A deadlock here holds the GIL, so the
verdict is written from a watchdog that escapes with ``os._exit``.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.disable(logging.INFO)

import azeoplant  # noqa: E402  (activates the native core when built)
from azeoplant.control.strategy import ControlSystem  # noqa: E402
from azeoplant.core import native, units as _units  # noqa: E402
from azeoplant.core.engine import SimulationEngine  # noqa: E402
from azeoplant.core.tags import TagDatabase, TagKind  # noqa: E402
from azeoplant.io import VirtualIOBus  # noqa: E402
from azeoplant.models.flowsheet import Flowsheet  # noqa: E402

SECONDS = 30.0
TICK = 5.0


def main() -> int:
    if not native.active():
        print("  SKIP  native core not built; the Python database uses an RLock and cannot deadlock this way")
        return 0
    db = TagDatabase()
    fs = Flowsheet(db, dt=0.1)
    eng = SimulationEngine(db, fs, dt=0.1)
    bus = VirtualIOBus(db)
    cs = ControlSystem(db, bus)
    eng.bus, eng.controller = bus, cs
    eng.post_step_hooks.extend((bus.tick, cs.step))
    snap = Path(__file__).resolve().parents[1] / "snapshots" / "lined_up.json"
    if snap.exists():
        eng.load_snapshot(snap)

    stop = threading.Event()
    counts = {"engine": 0, "opc": 0, "gui": 0}

    def engine() -> None:
        while not stop.is_set():
            eng._execute_step()
            counts["engine"] += 1

    def opc() -> None:            # the publisher's read, verbatim in shape
        while not stop.is_set():
            with db.lock:
                _ = [(t, _units.value(t) if t.kind.analogue else t.value, t.quality, t.pct, t.ts)
                     for t in db.by_kind(TagKind.AI, TagKind.DI)]
            counts["opc"] += 1
            time.sleep(0.02)

    def gui() -> None:            # the window's refresh
        while not stop.is_set():
            db.snapshot()
            counts["gui"] += 1
            time.sleep(0.01)

    for name, fn in (("engine", engine), ("opc", opc), ("gui", gui)):
        threading.Thread(target=fn, name=name, daemon=True).start()

    # The watchdog must not need the GIL to report: it is armed before the
    # threads can deadlock and fires from the C side of time.sleep.
    def watchdog() -> None:
        time.sleep(SECONDS + 2 * TICK)
        sys.stderr.write("  FAIL  three threads on the native database: watchdog fired, the process is deadlocked\n")
        sys.stderr.flush()
        os._exit(2)
    threading.Thread(target=watchdog, daemon=True).start()

    last = dict(counts)
    t0 = time.monotonic()
    ok = True
    while time.monotonic() - t0 < SECONDS:
        time.sleep(TICK)
        now = dict(counts)
        stalled = [k for k in now if now[k] == last[k]]
        print(f"  {time.monotonic() - t0:4.0f} s  engine {now['engine']:6d}  opc {now['opc']:5d}  gui {now['gui']:5d}"
              + (f"  stalled: {', '.join(stalled)}" if stalled else ""), flush=True)
        if len(stalled) >= 2:
            ok = False
            break
        last = now
    stop.set()
    verdict = "PASS" if ok else "FAIL"
    print(f"  {verdict}  engine, OPC publisher and GUI refresh all make progress on the native database for {SECONDS:.0f} s")
    print(f"\n{0 if ok else 1} failure(s)")
    sys.stdout.flush()            # os._exit skips the interpreter's own flush
    os._exit(0 if ok else 1)


if __name__ == "__main__":
    sys.exit(main())
