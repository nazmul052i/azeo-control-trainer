#!/usr/bin/env python3
"""Line the plant up and save the result as an initial condition.

Why this exists
---------------
The simulator is open loop by design: there is not a single controller in the
model code. That is the point, but it has a consequence that is easy to miss.
Levels and header pressures are integrators, so **no fixed set of valve positions
holds a ten-unit plant steady**. Any hand-tuned initial condition drifts to a rail
sooner or later, and that is correct physics rather than a defect.

So the initial condition is produced the same way a real plant reaches steady
state: by closing the loops. The controllers below live *here*, in a commissioning
script, and write to the ``AO`` tags exactly as a DCS would over OPC UA. They are
never imported by the application. Run this once, and ``run.py --snapshot`` starts
from a plant that is lined out and hands the trainee a stable starting point.

    python tools/lineup.py [--hours 3] [--out snapshots/lined_up.json]

If you change model sizing, re-run this. If a loop below will not settle, that is
usually the model telling you something is mis-sized rather than the controller
needing a retune.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from azeoplant.core.engine import SimulationEngine
from azeoplant.core.tags import TagDatabase
from azeoplant.logging_setup import setup_logging
from azeoplant.models.flowsheet import Flowsheet

log = logging.getLogger("lineup")


@dataclass
class Loop:
    """A plain PI controller. Deliberately simple: this is commissioning, not training."""

    pv: str
    mv: str
    setpoint: float
    gain: float
    ti: float = 300.0
    reverse: bool = False          # True when more output lowers the PV
    lo: float = 0.0
    hi: float = 100.0
    integral: float = 50.0
    sp_tag: str = ""          # track another tag instead of a fixed setpoint
    sp_factor: float = 1.0

    def step(self, db: TagDatabase, dt: float) -> None:
        pv = float(db[self.pv].value)
        target = (self.setpoint if not self.sp_tag
                  else float(db[self.sp_tag].value) * self.sp_factor)
        error = target - pv
        if self.reverse:
            error = -error
        self.integral += self.gain / max(self.ti, 1e-3) * error * dt
        self.integral = min(max(self.integral, self.lo), self.hi)
        out = min(max(self.integral + self.gain * error, self.lo), self.hi)
        db[self.mv].value = out


# Setpoints describe the design operating point in the tag list.
LOOPS = [
    # U010 fuel gas header. Import and the reducing valves are inflows, so more
    # output means more pressure: direct acting.
    Loop("PT-0101", "PCV-0101", 16.0, 6.0, 240),
    Loop("PT-0102", "PCV-0103", 3.2, 12.0, 120),
    Loop("PT-0103", "PCV-0104", 6.0, 8.0, 150),

    # U100. Level held on an outflow valve, so more output lowers the PV.
    Loop("LT-1001", "FCV-1001", 55.0, 1.6, 700, reverse=True),

    # U200. More speed pulls the suction down; the drain is an outflow.
    Loop("PT-2001", "SC-2001", 9.0, 5.0, 200, reverse=True),
    # Anti-surge on margin. With no loop here the fail-open valve recycles the
    # machine's full stonewall forever, which a load-honest ammeter rightly
    # calls a motor overload. A lined-out compressor holds its margin with the
    # valve closed, opening only as the margin falls toward the set point.
    Loop("UY-2001", "FCV-2001", 15.0, 1.2, 240, integral=0.0),
    Loop("LT-2001", "LCV-2001", 40.0, 3.0, 240, reverse=True),

    # U300. Fuel raises temperature and air raises oxygen: both direct.
    Loop("TT-3001", "FCV-3001", 350.0, 0.30, 420),
    Loop("AT-3001", "FCV-3004", 3.2, 5.0, 220),

    # U400. Quench cools, the level and pressure valves are outflows.
    Loop("TT-4002", "FCV-4001", 360.0, 1.0, 320, reverse=True),
    Loop("LT-4001", "LCV-4001", 50.0, 2.2, 420, reverse=True),
    Loop("LT-4002", "LCV-4002", 30.0, 1.6, 420, reverse=True),
    Loop("PT-4002", "PCV-4001", 39.0, 3.0, 320, reverse=True),

    # U500 column T1. Drum level is held on reflux and the distillate is set as a
    # fraction of feed. Holding level on distillate instead lets the split drift
    # above the light key content of the feed, and then no amount of reflux can
    # make product: the material balance caps the purity, not the separation.
    Loop("LT-5001", "FCV-5001", 50.0, 3.0, 320, reverse=True),
    # The draw fraction must sit below the light key in the feed (z ~ 0.46
    # with the anti-surge closed) or the material balance caps purity, but not
    # far below it either: what is not drawn overhead accumulates in the
    # reflux drum once the loops let go, and the drum has to survive an hour
    # of open loop drift. The draw also drifts UP as the drum fills (more
    # suction head on a frozen valve), eating the balance margin, so the set
    # point splits the difference: 0.40 drains the drum gently and keeps six
    # points of margin under z. Open loop, the column still drifts off spec in
    # the end; holding it there is the DCS operator's job, not this script's.
    Loop("FT-5003", "FCV-5002", 0.0, 1.6, 240, sp_tag="FT-5001", sp_factor=0.40),
    Loop("LT-5002", "FCV-5003", 50.0, 2.2, 340, reverse=True),
    Loop("PT-5001", "PCV-5001", 8.0, 4.0, 260, reverse=True),
    # Reboiler steam is lined out on FLOW, not on a tray temperature. A
    # temperature setpoint encodes the column pressure it was chosen at: the
    # same clean bottoms read 20 degrees hotter when the column runs at its
    # design pressure than at the old plant's 5.6 barg, so a temperature loop
    # here either latches the steam shut or holds the bottoms dirty. Flow
    # cannot latch, and the draw split loop owns purity. Cascading a tray
    # temperature onto this flow is a DCS exercise, deliberately left to it.
    Loop("FT-5005", "FCV-5004", 14.0, 1.2, 240),
    Loop("TT-5007", "FCV-5005", 48.0, 2.5, 320, reverse=True),
    # The reflux drum water boot draws on interface level, the same arrangement
    # as D3. Without it the boot simply fills: water entering with the feed has
    # nowhere to go, and a column that is slowly making a water layer is not
    # lined out. T2 has no boot, so this loop has no U600 counterpart.
    Loop("LT-5003", "LCV-5001", 30.0, 1.6, 420, reverse=True),

    # U600 column T2, same arrangement.
    Loop("LT-6001", "FCV-6001", 50.0, 3.0, 320, reverse=True),
    Loop("FT-6003", "FCV-6002", 0.0, 1.6, 240, sp_tag="FT-6001", sp_factor=0.46),
    Loop("LT-6002", "FCV-6003", 50.0, 2.2, 340, reverse=True),
    Loop("PT-6001", "PCV-6001", 5.5, 4.0, 260, reverse=True),
    Loop("FT-6005", "FCV-6004", 17.0, 1.2, 240),
    Loop("TT-6007", "FCV-6005", 48.0, 2.5, 320, reverse=True),

    # U700. Feedwater and makeup are inflows; fuel raises header pressure.
    Loop("LT-7001", "FCV-7001", 0.0, 0.22, 240),
    Loop("PT-7002", "FCV-7002", 36.0, 2.0, 320),
    Loop("AT-7001", "FCV-7003", 3.0, 4.5, 240),
    Loop("LT-7002", "LCV-7001", 55.0, 2.0, 320),
    Loop("TT-7001", "TCV-7001", 385.0, 0.6, 320, reverse=True),

    # U800. Caustic raises pH; discharge is an outflow.
    Loop("AT-8002", "FCV-8002", 7.2, 12.0, 220),
    Loop("LT-8001", "FCV-8004", 55.0, 2.5, 320, reverse=True),
]

# Discrete line-up: what an operator does before any loop can hold anything.
STARTUP_COMMANDS = [
    "XY-MOV1001A-OPN", "XY-P101A-STR",          # charge pump
    "XY-P201-STR", "XY-C1-STR",                 # lube oil then compressor
    "XY-3010", "XY-ID301-STR",                  # heater pilot and draft fan
    "XY-MOV5001A-OPN", "XY-P501A-STR",          # T1 reflux
    "XY-MOV5002A-OPN", "XY-P502A-STR",          # T1 bottoms
    "XY-MOV6001A-OPN", "XY-P601A-STR",          # T2 reflux
    "XY-MOV6002A-OPN", "XY-P602A-STR",          # T2 bottoms
    "XY-MOV7001A-OPN", "XY-P701A-STR",          # boiler feedwater
    "XY-FD701-STR", "XY-7010",                  # boiler fan and igniter
    "XY-MOV8001A-OPN", "XY-P801A-STR", "XY-M801-STR",   # effluent
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=float, default=3.0)
    ap.add_argument("--out", default=str(ROOT / "snapshots" / "lined_up.json"))
    ap.add_argument("--dt", type=float, default=0.1)
    ap.add_argument("--report", action="store_true", help="print the final state")
    args = ap.parse_args(argv)

    setup_logging(ROOT / "logs", console_level=logging.WARNING)
    db = TagDatabase()
    flowsheet = Flowsheet(db)
    engine = SimulationEngine(db, flowsheet, dt=args.dt)

    missing = [t for t in STARTUP_COMMANDS if t not in db]
    if missing:
        log.error("Startup commands reference tags that do not exist: %s", missing)
        return 2
    for tag in STARTUP_COMMANDS:
        db[tag].value = True

    bad_loops = [l for l in LOOPS if l.pv not in db or l.mv not in db]
    if bad_loops:
        log.error("Loops reference missing tags: %s",
                  [(l.pv, l.mv) for l in bad_loops])
        return 2

    steps = int(args.hours * 3600 / args.dt)
    print(f"Lining out {len(LOOPS)} loops over {args.hours:g} simulated hours "
          f"({steps} steps)...")
    for i in range(steps):
        engine._execute_step()
        if i % 5 == 0:                       # controllers run on a half-second scan
            for loop in LOOPS:
                loop.step(db, args.dt * 5)
        if i and i % (steps // 6) == 0:
            print(f"  {i * args.dt / 3600:.2f} h  "
                  f"D1 {float(db['LT-1001'].value):5.1f} %  "
                  f"H1 {float(db['TT-3001'].value):6.1f} C  "
                  f"R1 {float(db['TT-4002'].value):6.1f} C  "
                  f"MP {float(db['PT-7002'].value):5.1f} barg")

    out = Path(args.out)
    engine.save_snapshot(out)
    print(f"\nSnapshot written to {out}")

    if args.report:
        print("\nFinal state:")
        for name in ["PT-0101", "TT-3001", "AT-3001", "TT-4002", "XI-4001",
                     "LT-4001", "PT-2002", "UY-2001", "PT-5001", "LT-5001",
                     "AT-5001", "PT-6001", "LT-6001", "AT-6001", "LT-7001",
                     "PT-7002", "FT-7001", "AT-8002", "LT-1001", "FT-1001"]:
            tag = db[name]
            print(f"  {name:10s} {float(tag.value):9.2f} {tag.eu}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
