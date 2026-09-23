#!/usr/bin/env python3
"""Parity of the native PID block against azeoplant.control.pid.PID.

Both blocks are built from the same keyword arguments and driven through
the same scripted scan: PV walks with noise, bad-quality spells, mode
changes through the eight modes, setpoint steps, cascade and external
reset feedback, feedforward, the structures, the limits, and the alarm
enable and shelve maps. Every scan's OUT, working SP, PV, actual mode,
BKCAL and alarm flags must be identical; the captured state must be
identical; and a state applied across the two must continue identically.

    python tests/test_native_parity_pid.py
"""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import os
os.environ.setdefault("AZEO_NATIVE", "0")   # the Python side of this comparison is the reference
from azeoplant.control.pid import PID, Mode, Structure   # noqa: E402
from azeoplant.core import native                        # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(name)


def state(p) -> tuple:
    return (p.out, p.sp_wrk, p.sp, p.pv, p.pv_good, p.actual_mode, p.bkcal_out, p.bkcal_limit,
            p.alarms.hi_hi, p.alarms.hi, p.alarms.lo, p.alarms.lo_lo, p.alarms.dv_hi, p.alarms.dv_lo,
            p.field_value, p.field_good)


MODES = [Mode.AUTO, Mode.MAN, Mode.CAS, Mode.RCAS, Mode.ROUT, Mode.LO, Mode.IMAN, Mode.OOS, Mode.AUTO]

CONFIGS = [
    dict(gain=0.8, reset=20.0),
    dict(gain=1.4, reset=35.0, rate=4.0, structure=Structure.PID_ON_ERROR),
    dict(gain=0.6, reset=12.0, rate=2.0, structure=Structure.I_ERROR_PD_PV, direct_acting=True),
    dict(gain=2.0, reset=0.0, structure=Structure.PD_ON_ERROR, rate=3.0),
    dict(gain=1.0, reset=math.inf, structure=Structure.P_ERROR_D_PV, rate=1.5),
    dict(gain=0.9, reset=15.0, structure=Structure.ID_ON_ERROR, rate=2.5),
    dict(gain=0.7, reset=18.0, structure=Structure.I_ERROR_D_PV, rate=1.0),
    dict(gain=1.1, reset=25.0, structure=Structure.TWO_DEGREES, beta=0.6, gamma=0.3, rate=2.0),
    dict(gain=1.2, reset=30.0, pv_ftime=1.5, sp_ftime=3.0, sp_rate_up=2.0, sp_rate_dn=1.0),
    dict(gain=0.5, reset=40.0, ff_enable=True, ff_gain=0.35, use_pv_for_bkcal=True,
         sp_hi_lim=90.0, sp_lo_lim=10.0, out_hi_lim=85.0, out_lo_lim=5.0, arw_hi_lim=85.0, arw_lo_lim=5.0),
    dict(gain=1.6, reset=8.0, hi_hi_lim=95.0, hi_lim=80.0, lo_lim=20.0, lo_lo_lim=5.0,
         dv_hi_lim=12.0, dv_lo_lim=-12.0, alarm_hys=1.5, sp_pv_track_in_man=False),
]


def build(cc, kw):
    kw = dict(name="TIC-0001", description="parity", pv_eu0=0.0, pv_eu100=100.0, **kw)
    return PID(**kw), cc.PID(**kw)


def drive(a, b, seed: int, steps: int, cascade: bool) -> str | None:
    r = random.Random(seed)
    pv = 40.0
    good = True
    sp = 50.0
    a.set_mode(Mode.AUTO); b.set_mode(Mode.AUTO)
    a.sp = b.sp = sp
    for i in range(steps):
        pv += r.uniform(-1.5, 1.5) + 0.02 * (a.out - 50.0)
        pv = min(max(pv, -5.0), 105.0)
        if r.random() < 0.01:
            good = not good
        if r.random() < 0.01:
            m = r.choice(MODES)
            a.set_mode(m); b.set_mode(m)
        if r.random() < 0.01:
            sp = r.uniform(5.0, 95.0)
            a.sp = b.sp = sp
            if r.random() < 0.5:
                a.note_sp_change(30.0); b.note_sp_change(30.0)
        if r.random() < 0.003:
            key = r.choice(["hi_hi", "hi", "lo", "lo_lo", "dv_hi", "dv_lo"])
            v = r.random() < 0.5
            a.alarm_enab[key] = v; b.alarm_enab[key] = v
            a.alarm_shelved[key] = not v; b.alarm_shelved[key] = not v
        if r.random() < 0.005:
            g = r.uniform(0.3, 2.0); rs = r.choice([10.0, 25.0, 60.0])
            a.gain = b.gain = g; a.reset = b.reset = rs
        if r.random() < 0.002:
            a.simulate_enable = b.simulate_enable = not a.simulate_enable
            a.simulate_value = b.simulate_value = r.uniform(20.0, 80.0)
        cas = r.uniform(20.0, 80.0) if cascade and r.random() < 0.7 else None
        bk = r.uniform(0.0, 100.0) if r.random() < 0.4 else None
        lim = r.choice(["", "high", "low"])
        ff = r.uniform(-20.0, 20.0)
        oa = a.step(0.1, pv, good, cas_in=cas, bkcal_in=bk, bkcal_in_limit=lim, ff_val=ff)
        ob = b.step(0.1, pv, good, cas_in=cas, bkcal_in=bk, bkcal_in_limit=lim, ff_val=ff)
        if oa != ob or state(a) != state(b):
            return f"step {i}: python {state(a)} native {state(b)}"
    return None


def main() -> int:
    cc = native.module()
    if cc is None:
        print("native core not built: skipping")
        return 0
    print("PID block parity")
    for n, kw in enumerate(CONFIGS):
        a, b = build(cc, kw)
        check(f"config {n}: initial state identical", state(a) == state(b), str(state(b)))
        check(f"config {n}: capture identical", a.capture_state() == b.capture_state())
        diff = drive(a, b, seed=100 + n, steps=6000, cascade=(n % 2 == 0))
        check(f"config {n}: 6000 scans identical", diff is None, diff or "")
        check(f"config {n}: capture identical after the run", a.capture_state() == b.capture_state(),
              str(b.capture_state()) if a.capture_state() != b.capture_state() else "")
        # cross-apply and continue
        a2, b2 = build(cc, kw)
        a2.apply_state(b.capture_state()); b2.apply_state(a.capture_state())
        check(f"config {n}: cross-applied state identical", a2.capture_state() == b2.capture_state())
        diff = drive(a2, b2, seed=200 + n, steps=2000, cascade=True)
        check(f"config {n}: continues identically after apply", diff is None, diff or "")

    # the enum identity the strategy relies on
    a, b = build(cc, CONFIGS[0])
    b.set_mode(Mode.CAS)
    check("native block hands back the Python Mode members", b.target_mode is Mode.CAS and b.actual_mode is Mode.MAN)
    check("structure round-trips as the Python enum", b.structure is Structure.PI_ERROR_D_PV)
    b.alarm_shelved = {"hi": True}
    check("alarm maps behave as dicts", dict(b.alarm_shelved) == {"hi": True} and b.alarm_enab.get("hi", True) is True
          and "hi_hi" in b.alarm_enab and len(b.alarm_enab) == 6)
    b.alarm_priority = {"hi": "WARNING"}
    check("arbitrary attributes can be hung on the block", b.alarm_priority["hi"] == "WARNING")

    # the alarm table's on-delay, honoured by the block since 8 September
    # 2026: the condition stands for its delay before the indication shows
    a, b = build(cc, dict(gain=1.0, reset=20.0, hi_lim=80.0, dv_hi_lim=10.0, alarm_hys=0.5))
    a.alarm_delay = {"hi": 2.0, "dv_hi": 1.0}
    b.alarm_delay = {"hi": 2.0, "dv_hi": 1.0}
    check("the native block hands the delays back as a dict", dict(b.alarm_delay) == {"hi": 2.0, "dv_hi": 1.0},
          str(dict(b.alarm_delay)))
    a.set_mode(Mode.AUTO); b.set_mode(Mode.AUTO)      # in MAN the SP tracks the PV
    a.sp = b.sp = 50.0
    seen: list[tuple] = []
    for i in range(40):
        a.step(0.1, 85.0); b.step(0.1, 85.0)
        seen.append((a.alarms.hi, a.alarms.dv_hi))
        if state(a) != state(b) or a._alarm_timer != b._alarm_timer:
            check(f"on-delay scan {i} identical on both cores", False,
                  f"python {state(a)} {a._alarm_timer} native {state(b)} {b._alarm_timer}")
            break
    else:
        check("on-delay scans identical on both cores", True)
    first_hi = next((i for i, (h, _d) in enumerate(seen) if h), None)
    first_dv = next((i for i, (_h, d) in enumerate(seen) if d), None)
    # a tenth-of-a-second sum lands a scan either side of the delay
    check("HI shows after its 2 s delay, not at once", first_hi in (19, 20), f"scan {first_hi}")
    check("the deviation alarm after its own 1 s", first_dv in (9, 10), f"scan {first_dv}")
    a.step(0.1, 50.0); b.step(0.1, 50.0)
    check("and both clear at once when the condition drops",
          not a.alarms.hi and not b.alarms.hi and a._alarm_timer == b._alarm_timer == {k: 0.0 for k in a._alarm_timer})
    check("the delays and timers survive capture and apply on both cores",
          a.capture_state()["adly"] == b.capture_state()["adly"] == {"hi": 2.0, "dv_hi": 1.0}
          and "atmr" in b.capture_state())

    print(f"\n{len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
