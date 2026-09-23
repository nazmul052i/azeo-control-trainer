#!/usr/bin/env python3
"""The virtual I/O bus contract, checked the way the other gates check.

Ownership both directions, holder arbitration, the drain executive,
audited forcing over live physics, stale detection, and snapshot
round-trip of forces.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

logging.disable(logging.CRITICAL)

from azeoplant.core.engine import SimulationEngine          # noqa: E402
from azeoplant.core.tags import Quality                      # noqa: E402
from azeoplant.models.flowsheet import Flowsheet, TagDatabase  # noqa: E402
from azeoplant.io import OwnershipViolation, VirtualIOBus    # noqa: E402
from azeoplant.control.strategy import ControlSystem         # noqa: E402

FAILURES = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global FAILURES
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" +
          (f"  {detail}" if detail else ""))
    if not ok:
        FAILURES += 1


def main() -> int:
    db = TagDatabase()
    fs = Flowsheet(db)
    eng = SimulationEngine(db, fs, dt=0.1)
    bus = VirtualIOBus(db)
    eng.bus = bus
    cs = ControlSystem(db, bus)
    eng.controller = cs
    eng.post_step_hooks.append(bus.tick)
    eng.post_step_hooks.append(cs.step)
    snap = ROOT / "snapshots" / "lined_up.json"
    restored = {"control": False}
    if snap.exists():
        restored = eng.load_snapshot(snap)
    if not restored["control"]:
        cs.seed_from_plant()

    def run(steps: int) -> None:
        for _ in range(steps):
            eng._execute_step()

    g = lambda n: float(db[n].value)   # noqa: E731

    run(600)
    check("closed loop runs through the bus",
          bus.stats.dcs_writes > 1000 and bus.holder == "internal",
          f"{bus.stats.dcs_writes} writes, holder {bus.holder}")

    # ---------------------------------------------------------- ownership
    try:
        bus.write_from_dcs("PT-0101", 1.0, source="internal")
        check("DCS write to a simulator-owned tag raises", False)
    except OwnershipViolation:
        check("DCS write to a simulator-owned tag raises", True)

    before = bus.stats.rejected_ownership
    bus.queue_write("LT-1001", 99.0, source="opcua")
    run(3)
    check("queued external write to an AI is rejected at the drain",
          bus.stats.rejected_ownership == before + 1
          and abs(g("LT-1001") - 99.0) > 1.0)

    # --------------------------------------------------- holder arbitration
    v0 = g("FCV-3001")
    bus.queue_write("FCV-3001", 5.0, source="opcua")
    run(3)
    check("external output write loses to the internal holder",
          abs(g("FCV-3001") - 5.0) > 1.0 and bus.stats.rejected_holder >= 1,
          f"FCV-3001 {g('FCV-3001'):.1f}, was {v0:.1f}")

    cs.open_loop()
    bus.queue_write("FCV-3001", 55.5, source="opcua")
    run(3)
    check("external output write lands once the loop is open",
          abs(g("FCV-3001") - 55.5) < 1e-9)
    check("open loop releases the holder", bus.holder is None)

    # Local and internal writes use the Tag contract just like OPC: values are
    # clamped, timestamped and non-finite analogue commands are refused.
    out = db["FCV-3001"]
    bus.queue_write("FCV-3001", out.hi + 50.0, source="opcua")
    run(1)
    check("queued output above range is clamped",
          float(out.value) == out.hi and bus.stats.adjusted_writes == 1)
    before = float(out.value)
    bus.queue_write("FCV-3001", float("nan"), source="opcua")
    run(1)
    check("queued non-finite output is rejected",
          float(out.value) == before and bus.stats.rejected_value == 1)
    adjusted = bus.stats.adjusted_writes
    accepted = bus.write_from_dcs("FCV-3001", out.hi + 50.0,
                                  source="external")
    check("immediate output uses the same clamp contract",
          accepted and float(out.value) == out.hi
          and bus.stats.adjusted_writes == adjusted + 1)
    rejected = bus.stats.rejected_value
    accepted = bus.write_from_dcs("FCV-3001", float("nan"),
                                  source="external")
    check("immediate non-finite output is rejected",
          not accepted and float(out.value) == out.hi
          and bus.stats.rejected_value == rejected + 1)
    # Put the process valve back where this ownership test found it.  The
    # clamp contract is the subject here; leaving H1 fuel at 100 % would turn
    # the later stale-detection check into an unrelated fired-heater upset.
    bus.write_from_dcs("FCV-3001", v0, source="external")

    # ------------------------------------------------------------- forcing
    try:
        bus.force("PT-0101", 3.0, source="test", reason="")
        check("a force without a reason is refused", False)
    except ValueError:
        check("a force without a reason is refused", True)

    bus.force("PT-0101", 3.33, source="test", reason="unit test")
    run(20)
    rec = bus.active_forces()[0]
    check("forced AI publishes the force while physics continues",
          abs(g("PT-0101") - 3.33) < 1e-9 and abs(rec.true_value - 3.33) > 1.0,
          f"true {rec.true_value:.2f}")
    st = cs.capture_state()
    bus.release("PT-0101")
    run(20)
    check("release returns the physics value",
          abs(g("PT-0101") - 3.33) > 1.0)
    cs.apply_state(st)
    check("forces survive a snapshot round-trip",
          any(r.tag == "PT-0101" for r in bus.active_forces()))
    bus.release_all()

    # --------------------------------------------------------------- stale
    time.sleep(bus.stale.timeout_s + 0.3)
    run(3)
    check("an armed output goes UNCERTAIN after silence",
          db["FCV-3001"].quality is Quality.UNCERTAIN)

    # ----------------------------------------------------------- reclosure
    cs.close_loop()
    check("closing the loop reclaims the output side",
          bus.holder == "internal" and cs.enabled)
    # Two minutes of settle lets the one-second AI filters and the output
    # holder complete their bumpless re-entry.
    run(1200)
    dev = abs(g("TT-3001") - cs.loops["TIC-3001"].pid.sp_wrk)
    check("plant back under control through the bus", dev < 6.0,
          f"TT-3001 dev {dev:.2f}")

    print(f"\n{FAILURES} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
