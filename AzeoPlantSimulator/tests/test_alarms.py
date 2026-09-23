#!/usr/bin/env python3
"""The plant-wide alarm system, checked the way the other gates check.

On-delay, deadband hysteresis, the ISA-18.2 ack cycle, shelving, PV_BAD,
DISCRETE and DEV conditions, SIS trip annunciation with first-out, banner
ordering, and the event journal round trip.
"""

from __future__ import annotations

import logging
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

logging.disable(logging.CRITICAL)

from azeoplant.core.engine import SimulationEngine             # noqa: E402
from azeoplant.core.tags import Quality                        # noqa: E402
from azeoplant.models.flowsheet import Flowsheet, TagDatabase  # noqa: E402
from azeoplant.control.strategy import ControlSystem           # noqa: E402
from azeoplant.control.alarms import (ACKED, NORMAL, RTN_UNACK,   # noqa: E402
                                      UNACK, AlarmPoint, AlarmSystem)
from azeoplant.control.pid import Mode                         # noqa: E402
from azeoplant.core.events import EventJournal                 # noqa: E402

FAILURES = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global FAILURES
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" +
          (f"  {detail}" if detail else ""))
    if not ok:
        FAILURES += 1


def scan(alarms: AlarmSystem, seconds: float) -> None:
    steps = int(round(seconds / 0.2))
    for _ in range(steps):
        alarms.step(0.2)


def point(alarms: AlarmSystem, tag: str, atype: str):
    return next(p for p in alarms.points
                if p.tag == tag and p.atype == atype)


def main() -> int:
    db = TagDatabase()
    fs = Flowsheet(db)
    eng = SimulationEngine(db, fs, dt=0.1)
    snap = ROOT / "snapshots" / "lined_up.json"
    if snap.exists():
        eng.load_snapshot(snap)
        # the snapshot's tag dump can be staler than its unit state (the
        # reactor pressure is capped on restore); a few steps re-derive
        # every instrument from the restored physics
        for _ in range(20):
            for unit in fs.units:
                unit.step(0.1)
            fs.after_step(0.1)
    cs = ControlSystem(db)
    journal = EventJournal(Path(tempfile.mkdtemp()) / "events.sqlite",
                           retention_hours=1)
    alarms = AlarmSystem(db, journal, cs)

    check("schedule scanned into points", len(alarms.points) > 100,
          f"{len(alarms.points)} points")
    # The lined-up snapshot commissions the minimum-flow recycles at
    # setpoint, so the short-delay schedule is quiet at start. (The 60 s
    # quality alarms AT-5002/AT-6001/CT-7001 stand later by plant truth;
    # this 12 s scan deliberately sits under their delays.)
    scan(alarms, 12.0)
    baseline = {p.key for p in alarms.points if p.active}
    check("lined-up plant annunciates nothing within the short delays",
          not baseline, str(sorted(baseline))[:120])
    # Drive the FT-1003 LO condition for the out-of-service cycle: the
    # models are not stepping, so the value holds where it is put.
    ft = point(alarms, "FT-1003", "LO")
    db["FT-1003"].set(5.0)
    scan(alarms, 10.4)
    check("low recycle annunciates", ft.active)
    alarms.remove_from_service(ft)
    scan(alarms, 0.4)
    check("out of service clears and withholds", not ft.active
          and ft not in alarms.standing() and ft in alarms.oos_points())
    alarms.return_to_service(ft)
    scan(alarms, 12.0)
    check("returned to service re-annunciates", ft.active)
    db["FT-1003"].set(24.0)             # protection satisfied again
    scan(alarms, 0.4)
    alarms.ack_tag("FT-1003")

    # ------------------------------------------------ on-delay and activation
    lo = point(alarms, "LT-1001", "LO")          # sp 20, dead 2, delay 3
    db["LT-1001"].set(15.0)
    scan(alarms, 2.8)
    check("on-delay holds annunciation", not lo.active)
    scan(alarms, 0.6)
    check("LO annunciates after its delay", lo.active and lo.state == UNACK)
    check("priority mapped from the sheet", lo.priority == "HIGH")
    check("annunciation counted for the horn",
          alarms.take_annunciations() >= 1)

    # ------------------------------------------------- deadband and ack cycle
    db["LT-1001"].set(21.0)                      # above sp, inside deadband
    scan(alarms, 1.0)
    check("deadband holds the alarm in", lo.active)
    db["LT-1001"].set(22.5)
    scan(alarms, 0.4)
    check("clear leaves RTN unacked", not lo.active and lo.state == RTN_UNACK)
    check("cleared-unacked still standing", lo in alarms.standing())
    alarms.ack(lo)
    check("ack of a returned alarm ends it", lo.state == NORMAL
          and lo not in alarms.standing())

    db["LT-1001"].set(15.0)
    scan(alarms, 3.4)
    alarms.ack(lo)
    check("ack while active", lo.state == ACKED and lo in alarms.standing())
    check("acked alarm off the banner",
          lo not in alarms.banner())
    db["LT-1001"].set(30.0)
    scan(alarms, 0.4)
    check("acked alarm clears to normal", lo.state == NORMAL)

    # -------------------------------------------------------- banner ordering
    db["LT-1001"].set(5.0)                       # LO_LO sp 8 CRITICAL + LO
    scan(alarms, 3.4)
    lolo = point(alarms, "LT-1001", "LO_LO")
    banner = alarms.banner()
    check("critical leads the banner", banner and banner[0] is lolo)
    check("counts by priority", alarms.counts()["CRITICAL"] >= 1)
    n = alarms.ack_tag("LT-1001")
    check("ack by tag takes both", n >= 2 and lolo.state == ACKED)
    db["LT-1001"].set(50.0)
    scan(alarms, 0.4)

    # --------------------------------------------------------------- DISCRETE
    disc = point(alarms, "BS-3001", "DISCRETE")  # abnormal state 0, delay 1
    db["BS-3001"].set(False)
    scan(alarms, 1.4)
    check("discrete annunciates on abnormal state",
          disc.active and disc.priority == "CRITICAL")
    db["BS-3001"].set(True)
    scan(alarms, 0.4)
    alarms.ack_tag("BS-3001")

    # ----------------------------------------------------------------- PV_BAD
    bad = point(alarms, "LT-1001", "PV_BAD")
    db["LT-1001"].set(50.0, Quality.BAD)
    scan(alarms, 2.4)
    check("bad transmitter annunciates", bad.active)
    db["LT-1001"].set(50.0, Quality.GOOD)
    scan(alarms, 0.4)
    alarms.ack_tag("LT-1001")

    # -------------------------------------------------------------------- DEV
    # The sheet's DEV rows name monitor-only points (AT-0101 has no loop,
    # UIC-2001 is not a tag), so they are dropped at build; exercise the
    # deviation machinery on a real loop instead.
    check("unresolvable DEV rows dropped at build",
          not any(p.atype == "DEV" for p in alarms.points))
    loop = cs.loops["FIC-0101"]
    t = db[loop.pv_tag]
    dv = AlarmPoint(tag=loop.pv_tag, atype="DEV", setpoint=50.0,
                    priority="HIGH", deadband=10.0, delay_s=1.0,
                    desc=t.desc, eu=t.eu, unit=t.unit)
    dv.condition = alarms._condition_for(dv, t, {loop.pv_tag: loop})
    alarms.points.append(dv)
    loop.pid._actual = Mode.AUTO
    loop.pid.sp = float(t.value) + 150.0
    scan(alarms, 1.6)
    check("deviation annunciates in AUTO", dv.active, dv.key)
    loop.pid._actual = Mode.MAN
    scan(alarms, 0.4)
    check("deviation suppressed in MAN", not dv.active)
    alarms.ack(dv)

    # ---------------------------------------------------------------- shelving
    db["LT-1001"].set(15.0)
    scan(alarms, 3.4)
    high_before = alarms.counts()["HIGH"]
    alarms.shelve(lo, minutes=0.002)             # 120 ms shelf
    check("shelved alarm withheld", lo not in alarms.standing()
          and lo not in alarms.banner() and lo in alarms.shelved_points())
    check("shelved excluded from counts",
          alarms.counts()["HIGH"] == high_before - 1)
    time.sleep(0.15)
    scan(alarms, 0.4)
    check("shelf expires on its own", not lo.shelved
          and lo in alarms.standing())
    alarms.ack_tag("LT-1001")
    db["LT-1001"].set(50.0)
    scan(alarms, 0.4)

    # ---------------------------------------------------------------- SIS trip
    trip = next(p for p in alarms.points if p.atype == "TRIP")
    cause = alarms._esd_cause_of[trip.key]
    cs.esd.latched[cause] = True
    cs.esd.first_out = cause
    scan(alarms, 0.4)
    check("SIS latch annunciates critical",
          trip.active and trip.priority == "CRITICAL")
    check("first-out marked", trip.first_out)
    cs.esd.latched[cause] = False
    cs.esd.first_out = None
    scan(alarms, 0.4)
    alarms.ack(trip)

    # ----------------------------------------------------------------- journal
    rows = journal.query(limit=1000)
    cats = {e.category for e in rows}
    check("journal recorded the life cycle",
          {"ALARM", "RTN", "ACK", "SHELVE", "UNSHELVE", "OOS", "RTS"} <= cats,
          f"categories {sorted(cats)}")
    check("journal recorded the trip", "TRIP" in cats)
    acks = journal.query(category="ACK")
    check("journal filters by category",
          acks and all(e.category == "ACK" for e in acks))
    named = journal.query(like="LT-1001")
    check("journal filters by text",
          named and all("LT-1001" in (e.tag + e.message) for e in named))
    journal.stop()

    print(f"\n{FAILURES} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
