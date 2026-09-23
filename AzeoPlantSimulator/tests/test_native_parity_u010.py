#!/usr/bin/env python3
"""Parity of the native U010 (fuel gas header) against its Python twin.

A Python unit on a Python tag database and process bus, a native unit on
native ones. Both are driven with the same DCS outputs, the same bus
inputs and the same malfunctions at the same steps, and must publish
identical tags and bus values every step: the devices and the noise are
the same on both sides, so the only room for difference is the unit's
own arithmetic. Snapshots move both ways.

    python tests/test_native_parity_u010.py
"""

from __future__ import annotations

import json
import logging
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
logging.disable(logging.CRITICAL)

os.environ.setdefault("AZEO_NATIVE", "0")   # the Python side of this comparison is the reference
from azeoplant.core import native  # noqa: E402

cc = native.module()
if cc is None:
    print("native core not built: skipping U010 parity")
    sys.exit(0)

from azeoplant.core.tags import TagDatabase  # noqa: E402
from azeoplant.models.accountability import ProcessBus  # noqa: E402
from azeoplant.models.flowsheet import BUS_SIGNALS  # noqa: E402
from azeoplant.models.u010_fuel_gas import FuelGasHeader  # noqa: E402

failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failures
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")
    if not ok:
        failures += 1


def native_specs():
    return [cc.BusSignalSpec(s.name, s.default, s.eu, s.producer, tuple(s.consumers), s.lo, s.hi, s.tear, s.description)
            for s in BUS_SIGNALS]


def build_pair():
    dbp, busp = TagDatabase(), ProcessBus(BUS_SIGNALS)
    dbn, busn = cc.TagDatabase(), cc.ProcessBus(native_specs())
    up = FuelGasHeader(dbp, busp, dt=0.1)
    un = cc.FuelGasHeader(dbn, busn, 0.1)
    return (dbp, busp, up), (dbn, busn, un)


INPUTS = (
    "d3_offgas_available", "h1_burner_pressure_bara", "b1_fuel_demand", "r1_conversion",
    "ambient_dry_bulb", "ambient_wet_bulb",
    "u200_cw_flow", "u200_cw_duty", "u400_cw_flow", "u400_cw_duty",
    "t1_cw_flow", "t1_cw_duty", "t2_cw_flow", "t2_cw_duty",
    "u800_cw_flow", "u800_cw_duty",
)
OUTPUTS = (
    "fg_header_pressure_bara", "b1_supply_pressure_bara", "fg_to_h1_flow", "fg_lhv",
    "cooling_water_temperature", "cooling_water_dp_bar",
)
AOS = (
    "PCV-0101", "PCV-0102", "PCV-0103", "PCV-0104", "FCV-0101",
    "SC-0101", "SC-0102", "LCV-0101", "FCV-0102",
    "XY-MOV0101-OPN", "XY-MOV0101-CLS", "XY-XV0101-OPN",
)


def scenario(seed: int, n: int):
    """Scripted DCS outputs and bus inputs, plus malfunction events."""
    r = random.Random(seed)
    ao = {"PCV-0101": 46.0, "PCV-0102": 0.0, "PCV-0103": 40.0, "PCV-0104": 30.0, "FCV-0101": 60.0,
          "SC-0101": 100.0, "SC-0102": 45.0, "LCV-0101": 46.0, "FCV-0102": 15.0,
          "XY-MOV0101-OPN": True, "XY-MOV0101-CLS": False, "XY-XV0101-OPN": True}
    bus = {"d3_offgas_available": 900.0, "h1_burner_pressure_bara": 4.0, "b1_fuel_demand": 1500.0,
           "r1_conversion": 60.0, "ambient_dry_bulb": 30.0, "ambient_wet_bulb": 22.0,
           "u200_cw_flow": 210.0, "u200_cw_duty": 1400.0,
           "u400_cw_flow": 260.0, "u400_cw_duty": 1800.0,
           "t1_cw_flow": 580.0, "t1_cw_duty": 4200.0,
           "t2_cw_flow": 430.0, "t2_cw_duty": 3100.0,
           "u800_cw_flow": 190.0, "u800_cw_duty": 1000.0}
    frames = []
    for k in range(n):
        if r.random() < 0.02:
            tag = r.choice(["PCV-0101", "PCV-0102", "PCV-0103", "PCV-0104", "FCV-0101",
                            "SC-0101", "SC-0102", "LCV-0101", "FCV-0102"])
            ao[tag] = r.uniform(0.0, 100.0)
        if r.random() < 0.01:
            ao["XY-MOV0101-OPN"], ao["XY-MOV0101-CLS"] = r.choice([(True, False), (False, True), (False, False)])
        if r.random() < 0.005:
            ao["XY-XV0101-OPN"] = not ao["XY-XV0101-OPN"]
        if r.random() < 0.05:
            bus["d3_offgas_available"] = r.uniform(0.0, 2500.0)
            bus["h1_burner_pressure_bara"] = r.uniform(1.5, 6.0)
            bus["b1_fuel_demand"] = r.uniform(500.0, 3000.0)
            bus["r1_conversion"] = r.uniform(40.0, 99.0)
            bus["ambient_dry_bulb"] = r.uniform(15.0, 45.0)
            bus["ambient_wet_bulb"] = r.uniform(10.0, 35.0)
            for stem, flow_hi, duty_hi in (("u200", 350.0, 3000.0), ("u400", 450.0, 4000.0),
                                            ("t1", 900.0, 7000.0), ("t2", 750.0, 6000.0),
                                            ("u800", 350.0, 3000.0)):
                bus[f"{stem}_cw_flow"] = r.uniform(0.0, flow_hi)
                bus[f"{stem}_cw_duty"] = r.uniform(0.0, duty_hi)
        frames.append((dict(ao), dict(bus)))
    return frames


def drive(side, frames, events):
    db, bus, unit = side
    out = []
    mfs = {mf.mf_id: mf for mf in unit.malfunctions}
    for k, (ao, inputs) in enumerate(frames):
        for tag, v in ao.items():
            db[tag].value = v
        for name, v in inputs.items():
            bus.restore(name, v)
        for step, mf_id, active, value in events:
            if step == k:
                mfs[mf_id].set(active, value)
        with bus.source("U010"):
            unit.step(0.1)
        row = tuple(db[t].value for t in sorted(unit.tags)) + tuple(bus[name] for name in OUTPUTS)
        out.append(row)
    return out


EVENTS = [(500, "MF-028", True, 33.0), (1500, "MF-028", False, None), (900, "MF-029", True, 1.0),
          (1300, "MF-029", False, None), (1750, "MF-044", True, 45.0),
          (2100, "MF-045", True, 8.0), (2400, "MF-046", True, 35.0),
          (2700, "MF-047", True, 45.0), (3000, "MF-044", False, None),
          (3050, "MF-045", False, None), (3100, "MF-046", False, None),
          (3150, "MF-047", False, None), (3300, "MF-041", True, 1.0),
          (3800, "MF-041", False, None)]

py_side, cc_side = build_pair()
check("same tags in the same order", list(py_side[2].tags) == list(cc_side[2].tags),
      f"{len(py_side[2].tags)} vs {len(cc_side[2].tags)}")
check("same malfunctions",
      [(m.mf_id, m.target, m.description, m.category, m.param_label, m.param_min, m.param_max) for m in py_side[2].malfunctions]
      == [(m.mf_id, m.target, m.description, m.category, m.param_label, m.param_min, m.param_max) for m in cc_side[2].malfunctions])
check("same parameters",
      [(p.unit, p.name, p.value, p.eu, p.lo, p.hi, p.documented) for p in py_side[2].model_parameters()]
      == [(p.unit, p.name, p.value, p.eu, p.lo, p.hi, p.documented) for p in cc_side[2].model_parameters()],
      str([(p.name, p.value) for p in cc_side[2].model_parameters()]))
check("initial tag values identical", py_side[0].save_state() == cc_side[0].save_state())
check("initial capture identical", py_side[2].capture() == cc_side[2].capture(),
      str([k for k in py_side[2].capture().get("_dyn", {}) if py_side[2].capture()["_dyn"][k] != cc_side[2].capture()["_dyn"].get(k)]))

frames = scenario(7, 4000)
ya = drive(py_side, frames, EVENTS)
yb = drive(cc_side, frames, EVENTS)
first = next((k for k, (a, b) in enumerate(zip(ya, yb)) if a != b), None)
if first is not None:
    names = sorted(py_side[2].tags) + list(OUTPUTS)
    diffs = [(n, a, b) for n, a, b in zip(names, ya[first], yb[first]) if a != b][:4]
else:
    diffs = []
check("4000 steps with DCS moves, bus swings and malfunctions: identical every step", first is None,
      f"first difference at step {first}: {diffs}")
check("capture identical after the run", py_side[2].capture() == cc_side[2].capture())
check("air_failed agrees", py_side[2].air_failed == cc_side[2].air_failed)

# snapshot: the committed plant's U010 state into both, then continue
snap = json.loads((ROOT / "snapshots" / "lined_up.json").read_text(encoding="utf-8"))
state = snap["units"]["U010"]
for db, bus, unit in (py_side, cc_side):
    for name, v in snap["tags"].items():
        if name in unit.tags:
            db[name].value = v
    unit.apply(state)
check("snapshot state applied identically", py_side[2].capture() == cc_side[2].capture())
frames2 = scenario(11, 1500)
za = drive(py_side, frames2, [])
zb = drive(cc_side, frames2, [])
first2 = next((k for k, (a, b) in enumerate(zip(za, zb)) if a != b), None)
check("continues identically from the committed snapshot", first2 is None, f"first difference at step {first2}")

# the native capture applied to the Python unit and vice versa
py2, cc2 = build_pair()
py2[2].apply(cc_side[2].capture())
cc2[2].apply(py_side[2].capture())
check("captures cross-apply", py2[2].capture() == cc2[2].capture() == py_side[2].capture())

# ------------------------------------------- the whole plant, mixed
# The Python flowsheet on the native database and bus, once with the
# Python U010 and once with the native one; the other nine units are
# Python both times. Every tag must agree every step, because the only
# thing that changed is which implementation of U010 ran.
from azeoplant.core.engine import SimulationEngine  # noqa: E402
import azeoplant.models.flowsheet as flowsheet_mod  # noqa: E402

runs = []
original = list(flowsheet_mod.UNIT_CLASSES)
for swap in (False, True):
    classes = list(original)
    if swap:
        classes[0] = cc.FuelGasHeader
    flowsheet_mod.UNIT_CLASSES = classes
    flowsheet_mod.TagDatabase = cc.TagDatabase
    flowsheet_mod.ProcessBus = cc.ProcessBus
    flowsheet_mod.BUS_SIGNALS = tuple(native_specs())
    db = cc.TagDatabase()
    fs = flowsheet_mod.Flowsheet(db, dt=0.1)
    eng = SimulationEngine(db, fs, dt=0.1)
    for _ in range(3000):
        eng._execute_step()
    runs.append((db.save_state(), dict(fs.bus), eng.stats.errors, type(fs.units[0]).__name__))
flowsheet_mod.UNIT_CLASSES = original
same = runs[0][0] == runs[1][0]
diff = [k for k in runs[0][0] if runs[0][0][k] != runs[1][0].get(k)]
check(f"whole plant, 300 s, Python U010 ({runs[0][3]}) vs native U010 ({runs[1][3]}): identical tags",
      same and runs[0][2] == runs[1][2] == 0, f"{len(diff)} differ: {diff[:6]}")
check("identical bus", runs[0][1] == runs[1][1])

print(f"\n{failures} failure(s)")
sys.exit(1 if failures else 0)
