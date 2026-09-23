#!/usr/bin/env python3
"""Parity of the native tag database and buses against their Python twins.

Three layers. The tag: value, quality, excursions, DCS writes, overrides,
formatting, enums handed back as the Python enums. The buses: the I/O
bus's queue, holder, ownership, forces, stale timers and subscriptions;
the process bus's contract, statistics and topology. Then the plant: the
same Python flowsheet built on a Python and on a native database, stepped
the same number of times, must publish identical tags - the models are
the same code and every transmitter seeds its noise from its tag.

    python tests/test_native_parity_tags.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
logging.disable(logging.CRITICAL)

import os
os.environ.setdefault("AZEO_NATIVE", "0")   # the Python side of this comparison is the reference
from azeoplant.core import native  # noqa: E402

cc = native.module()
if cc is None:
    print("native core not built: skipping tag/bus parity")
    sys.exit(0)

from azeoplant.core.tags import Quality, Tag, TagDatabase, TagKind  # noqa: E402
from azeoplant.io.bus import VirtualIOBus  # noqa: E402
from azeoplant.io.ownership import OwnershipViolation  # noqa: E402
from azeoplant.models.accountability import (BusContractError, BusSignalSpec,  # noqa: E402
                                             ProcessBus)

failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failures
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")
    if not ok:
        failures += 1


def build(dbcls):
    db = dbcls()
    db.analog("FT-1", "AI", "U1", "flow", "m3/h", 0, 200)
    db.analog("AO-1", "AO", "U1", "valve", "%", 0, 100)
    db.discrete("DI-1", "DI", "U1", "switch", "Closed", "Open")
    db.discrete("DO-1", "DO", "U1", "command", "Idle", "Cmd")
    db.analog("PT-2", "AI", "U2", "pressure", "barg", -1, 60, 5.0)
    return db


# ---------------------------------------------------------------- tags
a, b = build(TagDatabase), build(cc.TagDatabase)
check("tag enums are the Python enums", b["FT-1"].kind is TagKind.AI and b["FT-1"].quality is Quality.GOOD
      and b["DO-1"].kind.dcs_writable and not b["FT-1"].kind.dcs_writable)
for v in (10.0, 250.0, -7.0, float("nan"), 199.0):
    a["FT-1"].set(v); b["FT-1"].set(v)
check("tag set: value, quality, excursions, worst",
      (a["FT-1"].value, a["FT-1"].quality, a["FT-1"].excursions, a["FT-1"].worst)
      == (b["FT-1"].value, b["FT-1"].quality, b["FT-1"].excursions, b["FT-1"].worst))
a["FT-1"].set(float("nan")); b["FT-1"].set(float("nan"))
check("tag set: NaN freezes and flags bad", a["FT-1"].quality is Quality.BAD and b["FT-1"].quality is Quality.BAD
      and a["FT-1"].value == b["FT-1"].value)
check("set_from_dcs clamps and reports", a["AO-1"].set_from_dcs(140.0) == b["AO-1"].set_from_dcs(140.0) == (100.0, True))
for db in (a, b):
    try:
        db["FT-1"].set_from_dcs(1.0)
        ok = False
    except PermissionError:
        ok = True
    check(f"set_from_dcs refuses a simulator-owned tag ({type(db).__module__})", ok)
try:
    b["AO-1"].set_from_dcs(float("inf")); ok = False
except ValueError:
    ok = True
check("set_from_dcs rejects a non-finite write", ok)
a["DO-1"].set(True); b["DO-1"].set(True)
a["AO-1"].override, a["AO-1"].override_value = True, 33.0
b["AO-1"].override, b["AO-1"].override_value = True, 33.0
check("effective honours the override on AO", a["AO-1"].effective == b["AO-1"].effective == 33.0)
a["FT-1"].override, a["FT-1"].override_value = True, 1.0
b["FT-1"].override, b["FT-1"].override_value = True, 1.0
check("effective ignores an override on AI", a["FT-1"].effective == b["FT-1"].effective == a["FT-1"].value)
check("discrete values are bools", b["DO-1"].value is True and b["DI-1"].value is False)
check("format", all(a[n].format() == b[n].format() for n in ("FT-1", "AO-1", "DI-1", "DO-1", "PT-2")),
      f"{[b[n].format() for n in ('FT-1','AO-1','DI-1','DO-1','PT-2')]}")
check("pct", all(abs(a[n].pct - b[n].pct) < 1e-12 for n in ("FT-1", "AO-1", "DI-1", "DO-1", "PT-2")))
check("node_id", a["PT-2"].node_id == b["PT-2"].node_id)
check("span", a["PT-2"].span == b["PT-2"].span)
check("counts / units / by_kind / by_unit",
      a.counts() == b.counts() and a.units() == b.units()
      and [t.name for t in a.by_kind(TagKind.AI, TagKind.DI)] == [t.name for t in b.by_kind(TagKind.AI, TagKind.DI)]
      and [t.name for t in a.by_unit("U1")] == [t.name for t in b.by_unit("U1")])
check("excursions list", a.excursions() == b.excursions(), f"{b.excursions()}")
check("contains / get / len", ("FT-1" in b) and ("nope" not in b) and b.get("nope") is None and len(b) == 5)
try:
    b["nope"]; ok = False
except KeyError:
    ok = True
check("missing tag raises KeyError", ok)
try:
    b.analog("FT-1", "AI", "U9", "dup", "x", 0, 1); ok = False
except KeyError:
    ok = True
check("duplicate tag raises KeyError", ok)
sa, sb = a.save_state(), b.save_state()
check("save_state identical", sa == sb, str(sb))
check("load_state applies", a.load_state(sb) == b.load_state(sa) == 5 and a["FT-1"].quality is Quality.GOOD)
check("snapshot shape", set(b.snapshot()) == set(a.snapshot()) and b.snapshot(["FT-1"])["FT-1"][:2] == (b["FT-1"].value, 0))
with b.lock:
    with b.lock:
        pass
check("lock is re-entrant", True)
t = cc.Tag("X-1", "AI", "U1", "desc", eu="kg", lo=0, hi=10, value=4.0)
check("Tag constructor by keyword", t.value == 4.0 and t.kind is TagKind.AI)

# ------------------------------------------------------------ I/O bus
events_a, events_b = [], []
a, b = build(TagDatabase), build(cc.TagDatabase)
ba, bb = VirtualIOBus(a, stale_timeout_s=0.05), cc.VirtualIOBus(b, stale_timeout_s=0.05)
ba.subscribe("AO-1", lambda tag: events_a.append(tag)); bb.subscribe("AO-1", lambda tag: events_b.append(tag))
check("write_from_dcs applies and notifies", ba.write_from_dcs("AO-1", 42.0) is True and bb.write_from_dcs("AO-1", 42.0) is True
      and a["AO-1"].value == b["AO-1"].value == 42.0 and events_a == events_b == ["AO-1"])
for bus in (ba, bb):
    try:
        bus.write_from_dcs("FT-1", 1.0); ok = False
    except OwnershipViolation as e:
        ok = e.tag == "FT-1"
    check(f"write_from_dcs raises OwnershipViolation ({type(bus).__module__})", ok)
check("holder arbitration", ba.register_dcs("internal") and bb.register_dcs("internal")
      and not ba.register_dcs("other") and not bb.register_dcs("other")
      and ba.write_from_dcs("AO-1", 5.0, "other") is False and bb.write_from_dcs("AO-1", 5.0, "other") is False
      and ba.write_from_dcs("AO-1", 7.0, "sis") is True and bb.write_from_dcs("AO-1", 7.0, "sis") is True)
ba.release_dcs("internal"); bb.release_dcs("internal")
for bus in (ba, bb):
    bus.queue_write("AO-1", 10.0, "ext"); bus.queue_write("AO-1", 20.0, "ext"); bus.queue_write("DO-1", True, "ext")
    bus.queue_write("FT-1", 3.0, "ext"); bus.queue_write("AO-1", 250.0, "ext2")
check("queue coalesces and drains with ownership and clamping",
      ba.drain_writes() == bb.drain_writes() == 3 and a["AO-1"].value == b["AO-1"].value == 100.0
      and a["DO-1"].value is True and b["DO-1"].value is True
      and ba.stats.coalesced_writes == bb.stats.coalesced_writes == 1
      and ba.stats.rejected_ownership == bb.stats.rejected_ownership
      and ba.stats.adjusted_writes == bb.stats.adjusted_writes == 1
      and ba.queue_health()["peak"] == bb.queue_health()["peak"])
for bus in (ba, bb):
    try:
        bus.force("FT-1", 99.0, "test", "")
        ok = False
    except ValueError:
        ok = True
    check(f"force needs a reason ({type(bus).__module__})", ok)
ba.force("FT-1", 99.0, "test", "parity"); bb.force("FT-1", 99.0, "test", "parity")
ba.force("AO-1", 12.0, "test", "parity"); bb.force("AO-1", 12.0, "test", "parity")
a["FT-1"].set(50.0); b["FT-1"].set(50.0)
ba.tick(); bb.tick()
check("forces: simulator side rides over physics, DCS side via override",
      a["FT-1"].value == b["FT-1"].value == 99.0 and a["AO-1"].effective == b["AO-1"].effective == 12.0
      and ba.stats.forces_applied == bb.stats.forces_applied == 1
      and [(f.tag, f.value, f.true_value) for f in ba.active_forces()] == [(f.tag, f.value, f.true_value) for f in bb.active_forces()])
check("bus capture_state identical apart from time",
      {k: v for f in ba.capture_state()["forces"] for k, v in f.items() if k != "t"}
      == {k: v for f in bb.capture_state()["forces"] for k, v in f.items() if k != "t"})
sb_state = bb.capture_state()
ba.release_all(); bb.release_all()
check("release_all clears overrides", not a["AO-1"].override and not b["AO-1"].override and not bb.active_forces())
ba.apply_state(sb_state); bb.apply_state(sb_state)
check("apply_state restores forces on both", [f.tag for f in ba.active_forces()] == [f.tag for f in bb.active_forces()] == ["FT-1", "AO-1"])
ba.release_all(); bb.release_all()
import time  # noqa: E402
ba.queue_write("AO-1", 1.0, "ext"); bb.queue_write("AO-1", 1.0, "ext"); ba.drain_writes(); bb.drain_writes()
time.sleep(0.08)
ba.tick(); bb.tick()
check("stale timer marks the tag uncertain", a["AO-1"].quality is Quality.UNCERTAIN and b["AO-1"].quality is Quality.UNCERTAIN
      and ba.stale.is_stale("AO-1") and bb.stale.is_stale("AO-1"))
check("samples carry the same metadata", {k: (v.value, v.quality, v.kind, v.eu, v.lo, v.hi) for k, v in ba.samples().items()}
      == {k: (v.value, v.quality, v.kind, v.eu, v.lo, v.hi) for k, v in bb.samples().items()})
check("authorize_dcs_write", ba.authorize_dcs_write("AO-1", "x") == bb.authorize_dcs_write("AO-1", "x") is True
      and ba.authorize_dcs_write("FT-1", "x") == bb.authorize_dcs_write("FT-1", "x") is False)

# --------------------------------------------------------- process bus
SPECS = [("charge_flow", 0.0, "m3/h", "U100", ("U300", "U400"), 0, 500, False),
         ("t1_bottoms_recycle", 0.0, "m3/h", "U500", ("U100",), 0, 200, True),
         ("d1_level", 55.0, "%", "U100", (), -20, 120, False)]


def make_pb(cls, speccls):
    return cls([speccls(n, d, eu, p, c, lo, hi, tear) for n, d, eu, p, c, lo, hi, tear in SPECS])


pa, pb = make_pb(ProcessBus, BusSignalSpec), make_pb(cc.ProcessBus, cc.BusSignalSpec)
check("process bus defaults and dict()", dict(pa) == dict(pb) == {"charge_flow": 0.0, "t1_bottoms_recycle": 0.0, "d1_level": 55.0})
with pa.source("U100"), pb.source("U100"):
    pa["charge_flow"] = 90.0; pb["charge_flow"] = 90.0
    for bus in (pa, pb):
        try:
            bus["t1_bottoms_recycle"] = 1.0; ok = False
        except BusContractError:
            ok = True
        check(f"producer violation ({type(bus).__module__})", ok)
with pa.source("U300"), pb.source("U300"):
    check("consumer read", pa["charge_flow"] == pb["charge_flow"] == 90.0)
    for bus in (pa, pb):
        try:
            bus["d1_level"]; ok = False
        except BusContractError:
            ok = True
        check(f"consumer violation ({type(bus).__module__})", ok)
for bus in (pa, pb):
    try:
        bus["nope"]; ok = False
    except BusContractError:
        ok = True
    check(f"unknown signal ({type(bus).__module__})", ok)
    try:
        bus["charge_flow"] = float("nan"); ok = False
    except BusContractError:
        ok = True
    check(f"non-finite rejected ({type(bus).__module__})", ok)
pa["charge_flow"] = 600.0; pb["charge_flow"] = 600.0
check("range excursion counted", pa.stats.range_excursions == pb.stats.range_excursions == 1)
check("current_range_issues", pa.current_range_issues() == pb.current_range_issues(), f"{pb.current_range_issues()}")
pa.restore("charge_flow", 80.0); pb.restore("charge_flow", 80.0)
check("restore bypasses ownership", pa["charge_flow"] == pb["charge_flow"] == 80.0)
order = ["U100", "U300", "U400", "U500"]
check("validate_topology", pa.validate_topology(order) == pb.validate_topology(order) == [])
bad = ["U500", "U100", "U300", "U400"]
check("validate_topology flags a backwards dependency", pa.validate_topology(bad) == pb.validate_topology(bad))
# ``dict(bus)`` copies the Python dict subclass without touching
# __getitem__, so it counts no reads; the native mapping counts each item.
# Every other statistic is a semantic one and must agree.
check("stats agree (reads excepted: dict() copies count natively)",
      {k: v for k, v in pa.stats_snapshot().__dict__.items() if k != "reads"}
      == {k: v for k, v in pb.stats_snapshot().as_dict().items() if k != "reads"})
check("contract_rows", pa.contract_rows() == pb.contract_rows())
try:
    cc.BusSignalSpec("x", 1.0, "", "U1", (), 5.0, 1.0); ok = False
except ValueError:
    ok = True
check("BusSignalSpec validates", ok)

# ----------------------------------------------------------- the plant
from azeoplant.core.engine import SimulationEngine  # noqa: E402
from azeoplant.models.flowsheet import Flowsheet  # noqa: E402

runs = []
for dbcls in (TagDatabase, cc.TagDatabase):
    db = dbcls()
    fs = Flowsheet(db, dt=0.1)
    eng = SimulationEngine(db, fs, dt=0.1)
    for _ in range(3000):
        eng._execute_step()
    runs.append((db.save_state(), {t.name: int(t.quality) for t in db.all()}, dict(fs.bus), eng.stats.errors))
same_tags = runs[0][0] == runs[1][0]
diff = [k for k in runs[0][0] if runs[0][0][k] != runs[1][0].get(k)]
check("Python flowsheet, 300 s on both databases: identical tags", same_tags and runs[0][3] == runs[1][3] == 0,
      f"{len(diff)} differ: {diff[:6]}")
check("identical qualities and bus", runs[0][1] == runs[1][1] and runs[0][2] == runs[1][2])

print(f"\n{failures} failure(s)")
sys.exit(1 if failures else 0)
