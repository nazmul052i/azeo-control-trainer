"""The per-unit parity harness for the native core (phase 3).

A Python unit on a Python tag database and process bus, a native unit on
native ones, driven with the same scripted DCS outputs, bus inputs and
malfunction events, must publish identical tags and bus values every
step, capture identical snapshots, restore from the committed snapshot
identically, and behave identically inside the whole plant with the
native unit swapped in. Each unit's test declares its scenario and calls
``run``.
"""

from __future__ import annotations

import json
import logging
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import os
os.environ.setdefault("AZEO_NATIVE", "0")   # the Python side of this comparison is the reference

logging.disable(logging.CRITICAL)


@dataclass
class Scenario:
    code: str                                   # unit code, e.g. "U100"
    python_cls: type                            # the Python unit class
    native_name: str                            # attribute name on the native module
    analog_ao: dict[str, tuple[float, float]]   # AO tag -> (lo, hi) of the scripted moves
    discrete_do: list[str]                      # DO tags toggled at random
    bus_inputs: dict[str, tuple[float, float]]  # signal -> (lo, hi) of the scripted swings
    bus_outputs: tuple[str, ...]                # signals the unit writes
    events: list[tuple[int, str, bool, float | None]] = field(default_factory=list)
    steps: int = 4000
    dt: float = 0.1
    flowsheet_index: int | None = None          # position in UNIT_CLASSES for the mixed run
    p_move: float = 0.02
    p_toggle: float = 0.01
    p_swing: float = 0.05
    setup: Callable | None = None               # setup(db, bus, unit) before driving, both sides


def _native():
    from azeoplant.core import native
    cc = native.module()
    if cc is None:
        print("native core not built: skipping")
        sys.exit(0)
    return cc


def _native_specs(cc):
    from azeoplant.models.flowsheet import BUS_SIGNALS
    return [cc.BusSignalSpec(s.name, s.default, s.eu, s.producer, tuple(s.consumers), s.lo, s.hi, s.tear, s.description)
            for s in BUS_SIGNALS]


def build_pair(sc: Scenario, cc):
    from azeoplant.core.tags import TagDatabase
    from azeoplant.models.accountability import ProcessBus
    from azeoplant.models.flowsheet import BUS_SIGNALS
    dbp, busp = TagDatabase(), ProcessBus(BUS_SIGNALS)
    dbn, busn = cc.TagDatabase(), cc.ProcessBus(_native_specs(cc))
    up = sc.python_cls(dbp, busp, dt=sc.dt)
    un = getattr(cc, sc.native_name)(dbn, busn, sc.dt)
    return (dbp, busp, up), (dbn, busn, un)


def scenario_frames(sc: Scenario, seed: int, n: int, db):
    r = random.Random(seed)
    ao = {tag: db[tag].value for tag in sc.analog_ao}
    do = {tag: bool(db[tag].value) for tag in sc.discrete_do}
    bus = {name: None for name in sc.bus_inputs}
    frames = []
    for _ in range(n):
        if sc.analog_ao and r.random() < sc.p_move:
            tag = r.choice(list(sc.analog_ao))
            lo, hi = sc.analog_ao[tag]
            ao[tag] = r.uniform(lo, hi)
        if sc.discrete_do and r.random() < sc.p_toggle:
            tag = r.choice(sc.discrete_do)
            do[tag] = not do[tag]
        if sc.bus_inputs and r.random() < sc.p_swing:
            for name, (lo, hi) in sc.bus_inputs.items():
                bus[name] = r.uniform(lo, hi)
        frames.append((dict(ao), dict(do), {k: v for k, v in bus.items() if v is not None}))
    return frames


def drive(sc: Scenario, side, frames, events):
    db, bus, unit = side
    out = []
    mfs = {mf.mf_id: mf for mf in unit.malfunctions}
    names = sorted(unit.tags)
    for k, (ao, do, inputs) in enumerate(frames):
        for tag, v in ao.items():
            db[tag].value = v
        for tag, v in do.items():
            db[tag].value = v
        for name, v in inputs.items():
            bus.restore(name, v)
        for step, mf_id, active, value in events:
            if step == k:
                mfs[mf_id].set(active, value)
        with bus.source(sc.code):
            unit.step(sc.dt)
        # values AND quality: a transmitter's quality lagging one step behind
        # its value (an argument-evaluation-order slip in a port) is invisible
        # to a value-only comparison and sheds a cascade in the closed loop
        out.append(tuple(db[t].value for t in names) + tuple(int(db[t].quality) for t in names)
                   + tuple(bus[name] for name in sc.bus_outputs))
    return out, names


def run(sc: Scenario) -> int:
    cc = _native()
    failures = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")
        if not ok:
            failures += 1

    py_side, cc_side = build_pair(sc, cc)
    if sc.setup:
        sc.setup(*py_side)
        sc.setup(*cc_side)
    up, un = py_side[2], cc_side[2]
    check("same tags in the same order", list(up.tags) == list(un.tags), f"{len(up.tags)} vs {len(un.tags)}")
    meta = lambda m: (m.mf_id, m.target, m.description, m.category, m.param_label, m.param_min, m.param_max)  # noqa: E731
    check("same malfunctions", [meta(m) for m in up.malfunctions] == [meta(m) for m in un.malfunctions])
    pmeta = lambda p: (p.unit, p.name, p.value, p.eu, p.lo, p.hi, p.documented)  # noqa: E731
    check("same parameters", sorted(map(pmeta, up.model_parameters())) == sorted(map(pmeta, un.model_parameters())),
          str(sorted((p.name, p.value) for p in un.model_parameters())))
    check("initial tag values identical", py_side[0].save_state() == cc_side[0].save_state())
    cp, cn = up.capture(), un.capture()
    check("initial capture identical", cp == cn,
          str([k for k in cp.get("_dyn", {}) if cp["_dyn"][k] != cn.get("_dyn", {}).get(k)] + [k for k in cp if k != "_dyn" and cp[k] != cn.get(k)]))

    frames = scenario_frames(sc, 7, sc.steps, py_side[0])
    ya, names = drive(sc, py_side, frames, sc.events)
    yb, _ = drive(sc, cc_side, frames, sc.events)
    first = next((k for k, (a, b) in enumerate(zip(ya, yb)) if a != b), None)
    diffs = []
    if first is not None:
        cols = names + list(sc.bus_outputs)
        diffs = [(n, a, b) for n, a, b in zip(cols, ya[first], yb[first]) if a != b][:4]
    check(f"{sc.steps} steps with DCS moves, bus swings and malfunctions: identical every step", first is None,
          f"first difference at step {first}: {diffs}")
    check("capture identical after the run", up.capture() == un.capture())

    snap = json.loads((ROOT / "snapshots" / "lined_up.json").read_text(encoding="utf-8"))
    state = snap["units"].get(sc.code)
    if state:
        for db, bus, unit in (py_side, cc_side):
            for name, v in snap["tags"].items():
                if name in unit.tags:
                    db[name].value = v
            unit.apply(state)
        check("snapshot state applied identically", up.capture() == un.capture())
        frames2 = scenario_frames(sc, 11, max(sc.steps // 3, 500), py_side[0])
        za, _ = drive(sc, py_side, frames2, [])
        zb, _ = drive(sc, cc_side, frames2, [])
        first2 = next((k for k, (a, b) in enumerate(zip(za, zb)) if a != b), None)
        check("continues identically from the committed snapshot", first2 is None, f"first difference at step {first2}")

    py2, cc2 = build_pair(sc, cc)
    py2[2].apply(un.capture())
    cc2[2].apply(up.capture())
    check("captures cross-apply", py2[2].capture() == cc2[2].capture() == up.capture())

    if sc.flowsheet_index is not None:
        from azeoplant.core.engine import SimulationEngine
        import azeoplant.models.flowsheet as flowsheet_mod
        original = list(flowsheet_mod.UNIT_CLASSES)
        saved = (flowsheet_mod.TagDatabase, flowsheet_mod.ProcessBus, flowsheet_mod.BUS_SIGNALS)
        runs = []
        try:
            for swap in (False, True):
                classes = list(original)
                if swap:
                    classes[sc.flowsheet_index] = getattr(cc, sc.native_name)
                flowsheet_mod.UNIT_CLASSES = classes
                flowsheet_mod.TagDatabase = cc.TagDatabase
                flowsheet_mod.ProcessBus = cc.ProcessBus
                flowsheet_mod.BUS_SIGNALS = tuple(_native_specs(cc))
                db = cc.TagDatabase()
                fs = flowsheet_mod.Flowsheet(db, dt=sc.dt)
                eng = SimulationEngine(db, fs, dt=sc.dt)
                for _ in range(3000):
                    eng._execute_step()
                runs.append((db.save_state(), dict(fs.bus), eng.stats.errors))
        finally:
            flowsheet_mod.UNIT_CLASSES = original
            flowsheet_mod.TagDatabase, flowsheet_mod.ProcessBus, flowsheet_mod.BUS_SIGNALS = saved
        diff = [k for k in runs[0][0] if runs[0][0][k] != runs[1][0].get(k)]
        check(f"whole plant, 300 s, Python {sc.code} vs native {sc.code}: identical tags",
              runs[0][0] == runs[1][0] and runs[0][2] == runs[1][2] == 0, f"{len(diff)} differ: {diff[:6]}")
        check("identical bus", runs[0][1] == runs[1][1])

    print(f"\n{failures} failure(s)")
    return 1 if failures else 0
