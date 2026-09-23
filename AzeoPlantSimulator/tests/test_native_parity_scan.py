#!/usr/bin/env python3
"""Parity of the native strategy scan against the Python strategy.

Two complete closed-loop stacks are built from the commissioned snapshot
with run.py's wiring: one on the Python core, one on the native core
with the scan routed through the native scanner. Both are stepped
together while the same operator actions land on both: column scheme
changes, the loop-shaping menus, the compressor MV and boiler mode
menus, level compensation, the ASC formulations, and the open-loop
switch with a hand move while passive. Every tenth step, every tag and
every block's OUT, working SP, PV and mode must be identical.

    python tests/test_native_parity_scan.py [--minutes 30]

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


# (step, action): the same call on both controllers
EVENTS = [
    (600, lambda cs, db: cs.set_algorithm("AIC-8001", "error_squared", mix_c=0.8)),
    (1200, lambda cs, db: cs.set_compensator("AIC-5001", "smith", k=1.2, theta=45.0, tau=300.0)),
    (1800, lambda cs, db: cs.set_column_scheme("material", column=6)),
    (2400, lambda cs, db: cs.set_output_conditioning("FIC-1001", 0.3)),
    (3000, lambda cs, db: cs.set_level_compensation(6.0)),
    (3600, lambda cs, db: cs.set_compressor_mv("suction")),
    (4200, lambda cs, db: cs.set_column_scheme("ryskamp", column=5)),
    (4800, lambda cs, db: cs.open_loop()),
    (5000, lambda cs, db: db["FCV-5004"].__setattr__("value", 55.0)),
    (5400, lambda cs, db: cs.close_loop()),
    (6000, lambda cs, db: cs.set_boiler_mode("baseload")),
    (6600, lambda cs, db: cs.set_asc_formulation("flow_speed")),
    (7200, lambda cs, db: cs.set_algorithm("AIC-8001", "slope", source="u800_titration", kp_target=1.0)),
    (7800, lambda cs, db: cs.set_compressor_mv("speed")),
    (8400, lambda cs, db: cs.set_column_scheme("energy", column=6)),
    (9000, lambda cs, db: cs.set_compensator("AIC-5001", "none")),
    (9600, lambda cs, db: cs.set_algorithm("AIC-8001", "linear")),
    (10200, lambda cs, db: cs.set_asc_formulation("margin")),
    (10800, lambda cs, db: cs.set_boiler_mode("pressure")),
    (11400, lambda cs, db: cs.set_level_compensation(0.0)),
    (12000, lambda cs, db: cs.set_column_scheme("energy", column=5)),
]


def main() -> int:
    from azeoplant.core import native
    if native.module() is None:
        print("native core not built: skipping")
        return 0
    minutes = 30.0
    if "--minutes" in sys.argv:
        minutes = float(sys.argv[sys.argv.index("--minutes") + 1])
    print("strategy scan parity")
    work = Path(tempfile.mkdtemp(prefix="azeo_scan_parity_"))
    P = build(False, work)
    N = build(True, work)
    check("native stack runs the native scanner", getattr(N[3], "_native_scan_installed", False))
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
                fn(_cs, _db)
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
        check(f"identical every scan for {minutes:g} minutes with {applied} operator actions", True,
              f"{steps} steps, {time.perf_counter() - t0:.0f} s wall")
    check("both stacks stepped without model errors", P[2].stats.errors == 0 and N[2].stats.errors == 0)
    check("captured control state identical", P[3].capture_state() == N[3].capture_state())
    print(f"\n{len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
