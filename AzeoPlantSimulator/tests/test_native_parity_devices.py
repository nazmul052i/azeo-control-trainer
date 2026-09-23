#!/usr/bin/env python3
"""Parity of the native field devices against azeoplant/core/devices.py.

Same construction, same command sequences, same faults injected at the
same instants: the two must agree to round-off on every output, and the
transmitters must agree exactly, because their noise is seeded from the
tag and both sides carry CPython's generator. State captured on one
side is applied on the other and the pair must continue together.

    python tests/test_native_parity_devices.py

Skips when the extension is not built. The Python core is the reference
and is untouched.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import os
os.environ.setdefault("AZEO_NATIVE", "0")   # the Python side of this comparison is the reference
from azeoplant.core import devices as py  # noqa: E402

try:
    from azeoplant import _azeocore as cc  # noqa: E402
except ImportError:
    print("native core not built: skipping device parity")
    sys.exit(0)

failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failures
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")
    if not ok:
        failures += 1


def worst(a, b) -> float:
    return max(abs(x - y) / max(1.0, abs(x), abs(y)) for x, y in zip(a, b))


def commands(seed: int, n: int) -> list[float]:
    r = random.Random(seed)
    out, level = [], 50.0
    for _ in range(n):
        if r.random() < 0.03:
            level = r.uniform(-10.0, 110.0)
        elif r.random() < 0.4:
            level += r.uniform(-3.0, 3.0)
        out.append(level)
    return out


CHARS = ((py.ValveChar.LINEAR, cc.ValveChar.LINEAR),
         (py.ValveChar.EQUAL_PERCENT, cc.ValveChar.EQUAL_PERCENT),
         (py.ValveChar.QUICK_OPENING, cc.ValveChar.QUICK_OPENING))

# ------------------------------------------------------------ valve gain
for pc, cch in CHARS:
    xs = [k / 100.0 for k in range(-5, 106)]
    check(f"valve_gain {pc.value}", worst([py.valve_gain(pc, x) for x in xs], [cc.valve_gain(cch, x) for x in xs]) < 1e-12)

# ---------------------------------------------------------- ControlValve
cmds = commands(11, 6000)
for label, kw in (("plain", {}), ("stiction", {"stiction": 2.0}), ("hysteresis", {"hysteresis": 3.0}),
                  ("both", {"stiction": 1.5, "hysteresis": 2.0}), ("zero shift", {"zero_shift": 4.0}),
                  ("fail open", {"fail_closed": False}), ("leaky", {"leakage_pct": 2.0}),
                  # the positioner loop: servo alone, with overshoot, with a deadband, and
                  # stacked on stiction and backlash the way the poor level does
                  ("positioner", {"positioner_time": 1.5}),
                  ("overshoot", {"positioner_time": 1.0, "positioner_overshoot": 12.0}),
                  ("deadband", {"positioner_deadband": 1.0}),
                  ("poor", {"positioner_time": 2.5, "positioner_overshoot": 15.0, "positioner_deadband": 1.0,
                            "stiction": 1.0, "hysteresis": 1.5})):
    for pc, cch in CHARS:
        a = py.ControlValve("FCV-1001", cv_rated=120.0, char=pc, stroke_time=8.0, **kw)
        b = cc.ControlValve("FCV-1001", cv_rated=120.0, char=cch, stroke_time=8.0, **kw)
        pos_a, pos_b, fl_a, fl_b, gf_a, gf_b = [], [], [], [], [], []
        for k, c in enumerate(cmds):
            air = 2000 <= k < 2300
            if k == 4000 and label == "plain":
                a.stuck = b.stuck = True
            if k == 4500 and label == "plain":
                a.stuck = b.stuck = False
            pos_a.append(a.step(0.1, c, air))
            pos_b.append(b.step(0.1, c, air))
            fl_a.append(a.flow(3.5, 0.8)); fl_b.append(b.flow(3.5, 0.8))
            gf_a.append(a.gas_flow(17.0, 4.0 + (k % 50) * 0.25, 0.65, 300.0))
            gf_b.append(b.gas_flow(17.0, 4.0 + (k % 50) * 0.25, 0.65, 300.0))
        ok = worst(pos_a, pos_b) < 1e-12 and worst(fl_a, fl_b) < 1e-12 and worst(gf_a, gf_b) < 1e-12
        check(f"ControlValve {label:10} {pc.value:13}", ok,
              f"pos {worst(pos_a, pos_b):.1e} flow {worst(fl_a, fl_b):.1e} gas {worst(gf_a, gf_b):.1e}")

a = py.ControlValve("FCV-2001", cv_rated=80.0, char=py.ValveChar.EQUAL_PERCENT, stroke_time=6.0, hysteresis=2.0)
b = cc.ControlValve("FCV-2001", cv_rated=80.0, char=cc.ValveChar.EQUAL_PERCENT, stroke_time=6.0, hysteresis=2.0)
for c in cmds[:700]:
    a.step(0.1, c); b.step(0.1, c)
check("ControlValve capture_state identical", a.capture_state() == b.capture_state())
c2 = cc.ControlValve("FCV-2001", cv_rated=80.0, char=cc.ValveChar.EQUAL_PERCENT, stroke_time=6.0, hysteresis=2.0)
c2.apply_state(a.capture_state())
check("ControlValve state Python -> native continues",
      worst([a.step(0.1, c) for c in cmds[700:1400]], [c2.step(0.1, c) for c in cmds[700:1400]]) < 1e-12)

# --------------------------------------------------------------------- MOV
def drive_mov(v, seq):
    out = []
    for k, (o, c) in enumerate(seq):
        v.command(o, c)
        out.append((round(v.step(0.5), 12), v.state.value if hasattr(v.state, "value") else str(v.state), v.zso, v.zsc))
    return out

r = random.Random(3)
seq = []
o = c = False
for _ in range(1500):
    if r.random() < 0.02:
        o, c = r.choice([(True, False), (False, True), (False, False), (True, True)])
    seq.append((o, c))
for label, faults in (("plain", {}), ("fail to open", {"fail_to_open": True}), ("drifts", {"drifts_closed": True}),
                      ("slow", {"slow_travel_factor": 2.5}), ("faulty limit", {"open_limit_faulty": True})):
    a, b = py.MotorOperatedValve("MOV-1001", travel_time=20.0), cc.MotorOperatedValve("MOV-1001", travel_time=20.0)
    for k, v in faults.items():
        setattr(a, k, v); setattr(b, k, v)
    check(f"MOV {label}", drive_mov(a, seq) == drive_mov(b, seq))
a, b = py.MotorOperatedValve("MOV-1002"), cc.MotorOperatedValve("MOV-1002")
a.torque_trip_at = b.torque_trip_at = 60.0
a.command(True, False); b.command(True, False)
ra, rb = [], []
for k in range(120):
    ra.append((a.step(0.5), a.state.value, a.torque_tripped)); rb.append((b.step(0.5), b.state.value, b.torque_tripped))
    if k == 80:
        a.reset(); b.reset(); a.command(False, True); b.command(False, True)
check("MOV torque trip and reset", ra == rb)
check("MOV capture_state identical", a.capture_state() == b.capture_state())

# ------------------------------------------------------------------ Motor
for vfd in (False, True):
    a, b = py.Motor("P-101A", rated_current=90.0, vfd=vfd), cc.Motor("P-101A", rated_current=90.0, vfd=vfd)
    sa, sb = [], []
    r = random.Random(5)
    load = 1.0
    for k in range(9000):
        if k == 10: a.command(True, False); b.command(True, False)
        if k == 3000: a.command(False, True); b.command(False, True)
        if k == 3200: a.command(True, False); b.command(True, False)
        if k == 6000: a.load_frac = b.load_frac = 1.45      # runout: the thermal image climbs
        if k == 8500: a.reset(); b.reset(); a.command(True, False); b.command(True, False)
        perm = not (5000 <= k < 5100)
        ref = 60.0 + 30.0 * (k % 400) / 400.0
        a.step(0.1, perm, ref); b.step(0.1, perm, ref)
        sa.append((a.running, round(a.speed_pct, 9), round(a.current, 9), round(a.thermal_pct, 9), a.faulted))
        sb.append((b.running, round(b.speed_pct, 9), round(b.current, 9), round(b.thermal_pct, 9), b.faulted))
    same = all(x[0] == y[0] and x[4] == y[4] and abs(x[1] - y[1]) < 1e-9 and abs(x[2] - y[2]) < 1e-9
               and abs(x[3] - y[3]) < 1e-9 for x, y in zip(sa, sb))
    first = next((k for k, (x, y) in enumerate(zip(sa, sb)) if x != y), None)
    check(f"Motor {'VFD' if vfd else 'DOL'} trajectory", same, f"first difference at {first}")
    check(f"Motor {'VFD' if vfd else 'DOL'} state dict", a.state() == b.state(), f"{a.state()} vs {b.state()}")
    c2 = cc.Motor("P-101A", rated_current=90.0, vfd=vfd)
    c2.restore(a.state())
    ya, yb = [], []
    for _ in range(300):
        a.step(0.1, True, 75.0); c2.step(0.1, True, 75.0)
        ya.append(a.speed_pct); yb.append(c2.speed_pct)
    check(f"Motor {'VFD' if vfd else 'DOL'} state Python -> native continues", worst(ya, yb) < 1e-9)

# ------------------------------------------------------------------- Pump
a, b = py.CentrifugalPump("P-101", 280.0, 230.0, 25.0), cc.CentrifugalPump("P-101", 280.0, 230.0, 25.0)
grid = [(q, s) for q in (0.0, 25.0, 100.0, 200.0, 260.0) for s in (0.0, 0.5, 40.0, 100.0, 120.0)]
check("Pump head", worst([a.head(q, s) for q, s in grid], [b.head(q, s) for q, s in grid]) < 1e-12)
a.wear_pct = b.wear_pct = 12.0
check("Pump discharge pressure, worn",
      worst([a.discharge_pressure(2.0, q, s, 780.0) for q, s in grid],
            [b.discharge_pressure(2.0, q, s, 780.0) for q, s in grid]) < 1e-12)
check("Pump cavitation flag", [a.check_cavitation(p, 1.9, True) for p in (1.0, 2.1, 2.3)]
      == [b.check_cavitation(p, 1.9, True) for p in (1.0, 2.1, 2.3)])
check("Pump capture_state identical", a.capture_state() == b.capture_state())

# ---------------------------------------------------------- HeatExchanger
a, b = py.HeatExchanger("E-501", 290.0, 40000.0), cc.HeatExchanger("E-501", 290.0, 40000.0)
res_a, res_b = [], []
r = random.Random(8)
for k in range(2000):
    if k == 1000: a.fouling_pct = b.fouling_pct = 35.0
    d, th, tc, drv = r.uniform(0, 60000), r.uniform(40, 400), r.uniform(20, 300), r.uniform(0, 1.6)
    res_a.append((a.transfer(d, th, tc, drv), a.limited)); res_b.append((b.transfer(d, th, tc, drv), b.limited))
check("HeatExchanger transfer + limited", all(abs(x[0] - y[0]) < 1e-9 * max(1.0, abs(x[0])) and x[1] == y[1]
                                              for x, y in zip(res_a, res_b)))
check("HeatExchanger non-finite guard", a.capacity(float("nan"), 20.0) == b.capacity(float("nan"), 20.0) == 0.0)
check("HeatExchanger capture_state identical", a.capture_state() == b.capture_state())

# ------------------------------------------------------------ Transmitter
def truth(seed: int, n: int, lo: float, hi: float) -> list[float]:
    r = random.Random(seed)
    out, level = [], (lo + hi) / 2
    for _ in range(n):
        level += r.uniform(-0.01, 0.01) * (hi - lo)
        if r.random() < 0.01:
            level = r.uniform(lo - 0.1 * (hi - lo), hi + 0.1 * (hi - lo))
        out.append(level)
    return out

CASES = [
    ("AT-4001 analyser", dict(lo=0, hi=500, tau=20.0, transport=30.0, noise_sigma_pct=1.5, update_period=420.0), 0.1),
    ("FT-1001 flow", dict(lo=0, hi=200, tau=0.6, noise_sigma_pct=0.45), 0.1),
    ("LT-5001 level", dict(lo=0, hi=100, tau=1.2, noise_sigma_pct=0.4), 0.05),
    ("AT-5001 analyser", dict(lo=0, hi=10, tau=25.0, noise_sigma_pct=1.2, update_period=300.0, transport=45.0), 0.25),
    ("TT-3001 quiet", dict(lo=0, hi=550, tau=8.0), 0.1),
    # the instrument model: walk, damping and offset, alone and on an analyser
    ("FT-2001 walk", dict(lo=0, hi=80, tau=0.6, noise_sigma_pct=0.6, walk_sigma_pct=0.5, walk_tau=300.0), 0.1),
    ("PT-7001 damped", dict(lo=0, hi=60, tau=1.0, noise_sigma_pct=0.3, damping=1.0, offset_pct=-0.7), 0.1),
    ("AT-3001 poor", dict(lo=0, hi=21, tau=12.0, noise_sigma_pct=0.8, update_period=20.0, transport=8.0,
                          walk_sigma_pct=0.5, walk_tau=300.0, damping=1.0, offset_pct=1.0), 0.1),
]
for label, kw, dt in CASES:
    tag = label.split()[0]
    a, b = py.Transmitter(tag, **kw), cc.Transmitter(tag, **kw)
    u = truth(hash(tag) % 1000, 8000, kw["lo"], kw["hi"])
    ya, yb, qa, qb = [], [], [], []
    for k, x in enumerate(u):
        if k == 3000: a.drift_pct_per_hour = b.drift_pct_per_hour = 4.0
        if k == 5000: a.extra_lag = b.extra_lag = 3.0
        if k == 6000:      # the settings change the model under a running transmitter
            a.damping = b.damping = 0.8
            a.walk_sigma_pct = b.walk_sigma_pct = 0.2
            if kw.get("transport"):
                a.transport = b.transport = kw["transport"] * 1.5
        ya.append(a.step(dt, x)); yb.append(b.step(dt, x)); qa.append(a.quality); qb.append(b.quality)
    check(f"Transmitter {label:18} bit-exact", ya == yb and qa == qb,
          f"first diff at {next((k for k, (p, q) in enumerate(zip(ya, yb)) if p != q), None)}")

# failure modes
for pf, cf in ((py.TxFailure.FROZEN, cc.TxFailure.FROZEN), (py.TxFailure.FAIL_LOW, cc.TxFailure.FAIL_LOW),
               (py.TxFailure.FAIL_HIGH, cc.TxFailure.FAIL_HIGH), (py.TxFailure.OUT_OF_SERVICE, cc.TxFailure.OUT_OF_SERVICE)):
    a, b = py.Transmitter("PT-7001", 0, 60, tau=1.0, noise_sigma_pct=0.3), cc.Transmitter("PT-7001", 0, 60, tau=1.0, noise_sigma_pct=0.3)
    u = truth(2, 1500, 0, 60)
    ya, yb = [], []
    for k, x in enumerate(u):
        if k == 500: a.failure, b.failure = pf, cf
        if k == 1000: a.clear_faults(); b.clear_faults()
        ya.append((a.step(0.1, x), a.quality)); yb.append((b.step(0.1, x), b.quality))
    check(f"Transmitter failure {pf.value}", ya == yb)

# state round trips both ways, including the pipe and the generator
a, b = py.Transmitter("AT-6001", 0, 10, tau=25.0, noise_sigma_pct=1.2, update_period=300.0, transport=45.0), \
       cc.Transmitter("AT-6001", 0, 10, tau=25.0, noise_sigma_pct=1.2, update_period=300.0, transport=45.0)
u = truth(9, 6000, 0, 10)
for x in u[:3000]:
    a.step(0.1, x); b.step(0.1, x)
sa, sb = a.capture_state(), b.capture_state()
check("Transmitter capture_state identical", sa == sb,
      str([k for k in sa if sa[k] != sb.get(k)]))
c2 = cc.Transmitter("AT-6001", 0, 10, tau=25.0, noise_sigma_pct=1.2, update_period=300.0, transport=45.0)
c2.apply_state(sa)
check("Transmitter state Python -> native continues exactly",
      [a.step(0.1, x) for x in u[3000:4500]] == [c2.step(0.1, x) for x in u[3000:4500]])
d2 = py.Transmitter("AT-6001", 0, 10, tau=25.0, noise_sigma_pct=1.2, update_period=300.0, transport=45.0)
d2.apply_state(b.capture_state())
check("Transmitter state native -> Python continues exactly",
      [b.step(0.1, x) for x in u[3000:4500]] == [d2.step(0.1, x) for x in u[3000:4500]])

print(f"\n{failures} failure(s)")
sys.exit(1 if failures else 0)
