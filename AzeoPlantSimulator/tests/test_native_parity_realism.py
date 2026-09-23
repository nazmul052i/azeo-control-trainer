#!/usr/bin/env python3
"""Parity of the realism layer: instrument model, positioner loop and scan
classes, on two complete closed-loop stacks.

Two plants are built from the commissioned snapshot with run.py's wiring,
one on the Python core and one on the native core with the native
scanner. Both get the same realism level (every transmitter's walk,
damping and offset; every valve's positioner), the same per-instrument
overrides, and the same scan classes set mid-run, and are stepped
together: every tenth step every tag and every block's OUT, working SP,
PV and mode must be identical, because the walk is seeded per tag from
the same integer generator on both sides. A snapshot taken on one side
is restored on the other and both continue identically.

    python tests/test_native_parity_realism.py [--minutes 12]

The test pins AZEO_NATIVE=0 for its Python stack and activates the
native core itself for the second one.
"""

from __future__ import annotations

import logging
import math
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import os
os.environ.setdefault("AZEO_NATIVE", "0")   # the Python side of this comparison is the reference

logging.disable(logging.INFO)

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(name)


def build(native: bool, workdir: Path):
    if native:
        from azeoplant.core import native as nat
        nat.activate()
    from azeoplant.core import tags as tags_mod
    from azeoplant.models import flowsheet as fs_mod
    from azeoplant.control import strategy as st_mod, alarms as al_mod
    from azeoplant.core.engine import SimulationEngine
    from azeoplant.core.events import EventJournal
    import azeoplant.io as io_mod
    db = tags_mod.TagDatabase()
    fs = fs_mod.Flowsheet(db, dt=0.1)
    eng = SimulationEngine(db, fs, dt=0.1)
    bus = io_mod.VirtualIOBus(db)
    cs = st_mod.ControlSystem(db, bus)
    eng.bus, eng.controller = bus, cs
    al = al_mod.AlarmSystem(db, EventJournal(workdir / f"events_{int(native)}.sqlite"), cs)
    eng.alarms = al
    eng.post_step_hooks.extend((bus.tick, cs.step, al.step))
    eng.load_snapshot(ROOT / "snapshots" / "lined_up.json")
    return db, fs, eng, cs


OVERRIDES = {"FT-1001": {"damping": 2.0, "walk_sigma_pct": 0.4},
             "AT-5001": {"update_period": 240.0, "transport": 60.0},
             "FCV-3001": {"positioner_time": 2.0, "positioner_overshoot": 20.0},
             "LT-7001": {"offset_pct": -1.5}}

# (step, action) applied to both stacks: fs, cs, db
EVENTS = [
    (300, lambda fs, cs, db: __import__("azeoplant.core.instruments", fromlist=["apply"]).apply(fs, "typical")),
    (900, lambda fs, cs, db: cs.set_scan_class("TIC-3001", 1.0)),
    (1200, lambda fs, cs, db: cs.set_scan_class("FIC-1001", 0.1)),
    (1500, lambda fs, cs, db: cs.set_scan_class("LIC-1001", 2.0)),
    (2100, lambda fs, cs, db: __import__("azeoplant.core.instruments", fromlist=["apply"]).apply(fs, "poor", OVERRIDES)),
    (2700, lambda fs, cs, db: cs.set_column_scheme("material", column=6)),
    (3300, lambda fs, cs, db: cs.set_scan_class("TIC-3001", 0.5)),
    (3900, lambda fs, cs, db: cs.set_algorithm("AIC-8001", "error_squared", mix_c=0.8)),
    (4500, lambda fs, cs, db: cs.open_loop()),
    (4800, lambda fs, cs, db: cs.close_loop()),
    (5400, lambda fs, cs, db: __import__("azeoplant.core.instruments", fromlist=["apply"]).apply(fs, "typical", OVERRIDES)),
]


def main() -> int:
    from azeoplant.core import native
    if native.module() is None:
        print("native core not built: skipping")
        return 0
    minutes = 12.0
    if "--minutes" in sys.argv:
        minutes = float(sys.argv[sys.argv.index("--minutes") + 1])
    print("realism parity: instrument model, positioner, scan classes")
    work = Path(tempfile.mkdtemp(prefix="azeo_realism_parity_"))
    P = build(False, work)
    N = build(True, work)
    check("native stack runs the native scanner", getattr(N[3], "_native_scan_installed", False))
    from azeoplant.core import instruments
    rp, rn = instruments.describe(P[1]), instruments.describe(N[1])
    check("both cores enumerate the same instruments",
          [(r["unit"], r["tag"], r["kind"], r["attr"]) for r in rp] == [(r["unit"], r["tag"], r["kind"], r["attr"]) for r in rn],
          f"{len(rp)} vs {len(rn)}")
    check("both cores describe the same parameters", rp == rn)
    names = [t.name for t in P[0].all()]
    loops = sorted(P[3].loops)

    def tags(db):
        return {n: float(db[n].value) for n in names}

    def blocks(cs):
        return {m: (cs.loops[m].pid.out, cs.loops[m].pid.sp_wrk, cs.loops[m].pid.pv,
                    cs.loops[m].pid.actual_mode.value) for m in loops}

    events = {s: fn for s, fn in EVENTS}
    steps = int(minutes * 600)
    first = None
    applied = 0
    t0 = time.perf_counter()
    for step in range(1, steps + 1):
        fn = events.get(step)
        if fn is not None:
            for _db, _fs, _eng, _cs in (P, N):
                fn(_fs, _cs, _db)
            applied += 1
        P[2]._execute_step()
        N[2]._execute_step()
        if step % 10 == 0 or step < 50 or fn is not None:
            a, b = tags(P[0]), tags(N[0])
            dt_ = [(n, a[n], b[n]) for n in names
                   if a[n] != b[n] and not (math.isnan(a[n]) and math.isnan(b[n]))]
            pa, pb = blocks(P[3]), blocks(N[3])
            dp = [(m, pa[m], pb[m]) for m in loops if pa[m] != pb[m]]
            if (dt_ or dp) and first is None:
                first = step
                detail = "; ".join([f"{n}: {x!r} vs {y!r}" for n, x, y in dt_[:4]]
                                   + [f"{m}: {x} vs {y}" for m, x, y in dp[:4]])
                check(f"identical every scan for {minutes:g} minutes", False,
                      f"first divergence at step {step} ({step * 0.1:.1f} s): {detail}")
                break
    else:
        check(f"identical every scan for {minutes:g} minutes with {applied} realism and scan-class actions", True,
              f"{steps} steps, {time.perf_counter() - t0:.0f} s wall")
    check("both stacks stepped without model errors", P[2].stats.errors == 0 and N[2].stats.errors == 0)
    check("scan classes agree", P[3].scan_classes() == N[3].scan_classes(), str(N[3].scan_classes()))
    check("captured control state identical (scan accumulators included)",
          P[3].capture_state() == N[3].capture_state())
    check("the level reached every instrument on both cores",
          all(r["damping"] == instruments.LEVELS["typical"].damping for r in instruments.describe(N[1])
              if r["kind"] == "transmitter" and r["tag"] not in OVERRIDES))

    # a snapshot from the Python stack restored on the native one, and back
    snap_p = P[2].save_snapshot(work / "p.json")
    snap_n = N[2].save_snapshot(work / "n.json")
    P2 = build(False, work)
    N2 = build(True, work)
    for _db, _fs, _eng, _cs in (P2, N2):
        instruments.apply(_fs, "typical", OVERRIDES)
    P2[2].load_snapshot(snap_n)
    N2[2].load_snapshot(snap_p)
    diverged = None
    for step in range(1, 1201):
        P2[2]._execute_step()
        N2[2]._execute_step()
        if step % 10 == 0:
            a, b = tags(P2[0]), tags(N2[0])
            bad = [n for n in names if a[n] != b[n] and not (math.isnan(a[n]) and math.isnan(b[n]))]
            if bad:
                diverged = (step, bad[:3], a[bad[0]], b[bad[0]])
                break
    check("snapshots cross the cores and continue identically for 2 minutes", diverged is None, str(diverged))
    print(f"\n{len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
