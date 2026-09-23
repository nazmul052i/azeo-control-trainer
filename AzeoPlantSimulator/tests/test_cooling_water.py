#!/usr/bin/env python3
"""Cooling-tower and plant-wide cooling-water integration.

The checks are deliberately behavioural: CT1 must reject heat toward wet bulb,
inventory and dissolved solids must remain finite, its physical pump trip must
remove header pressure, and the five real users must lose flow and heat sink.
Control is checked only at the external DCS boundary; none belongs in U010.

    python tests/test_cooling_water.py
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["AZEO_NATIVE"] = "0"

from azeoplant.core.engine import SimulationEngine  # noqa: E402
from azeoplant.core.tags import TagDatabase  # noqa: E402
from azeoplant.models.accountability import ProcessBus  # noqa: E402
from azeoplant.models.flowsheet import BUS_SIGNALS, Flowsheet  # noqa: E402
from azeoplant.models.u010_fuel_gas import FuelGasHeader  # noqa: E402


FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(name)


NOMINAL_LOADS = {
    "u200_cw_flow": 230.0, "u200_cw_duty": 1800.0,
    "u400_cw_flow": 250.0, "u400_cw_duty": 8000.0,
    "t1_cw_flow": 650.0, "t1_cw_duty": 6500.0,
    "t2_cw_flow": 500.0, "t2_cw_duty": 4200.0,
    "u800_cw_flow": 190.0, "u800_cw_duty": 1000.0,
    "ambient_dry_bulb": 30.0, "ambient_wet_bulb": 22.0,
    "d3_offgas_available": 900.0, "h1_burner_pressure_bara": 4.0,
    "b1_fuel_demand": 1500.0, "r1_conversion": 60.0,
}


def build_tower() -> tuple[TagDatabase, ProcessBus, FuelGasHeader]:
    db = TagDatabase()
    bus = ProcessBus(BUS_SIGNALS)
    unit = FuelGasHeader(db, bus, dt=0.1)
    for name, value in NOMINAL_LOADS.items():
        bus.restore(name, value)
    return db, bus, unit


def advance_tower(bus: ProcessBus, unit: FuelGasHeader, steps: int) -> None:
    for _ in range(steps):
        with bus.source("U010"):
            unit.step(0.1)


print("cooling-water contract")
specs = {s.name: s for s in BUS_SIGNALS}
users = {"U200", "U400", "U500", "U600", "U800"}
check("CT1 owns the common supply temperature",
      specs["cooling_water_temperature"].producer == "U010")
check("CT1 owns the common supply pressure",
      specs["cooling_water_dp_bar"].producer == "U010")
check("all five cooler users consume both common headers",
      users == set(specs["cooling_water_temperature"].consumers)
      == set(specs["cooling_water_dp_bar"].consumers))
return_signals = {f"{stem}_cw_{quantity}"
                  for stem in ("u200", "u400", "t1", "t2", "u800")
                  for quantity in ("flow", "duty")}
check("every user returns flow and heat duty to CT1",
      all(specs[name].consumers == ("U010",) for name in return_signals))


print("\nnormal tower operation")
db, bus, tower = build_tower()
advance_tower(bus, tower, 6000)
values = {tag: float(db[tag].value) for tag in
          ("TT-0102", "TT-0103", "PT-0105", "FT-0103", "LT-0101",
           "AT-0103", "FT-0104", "FT-0105")}
check("all CT1 measurements remain finite",
      all(math.isfinite(v) for v in values.values()), str(values))
check("tower cools the return water toward wet bulb",
      22.0 <= values["TT-0102"] < values["TT-0103"],
      f"CWS {values['TT-0102']:.2f}, CWR {values['TT-0103']:.2f} degC")
check("circulation header is established",
      values["PT-0105"] > 1.2 and values["FT-0103"] > 1500.0,
      f"{values['PT-0105']:.2f} barg, {values['FT-0103']:.0f} m3/h")
check("basin chemistry and inventory are dynamic but healthy",
      40.0 < values["LT-0101"] < 90.0
      and 300.0 < values["AT-0103"] < 1600.0
      and values["FT-0104"] > values["FT-0105"] > 0.0,
      f"level {values['LT-0101']:.1f} %, conductivity {values['AT-0103']:.0f} uS/cm")
check("fans and duty pump obey their start commands",
      bool(db["XS-CT011A-RUN"].value) and bool(db["XS-CT011B-RUN"].value)
      and bool(db["XS-P011A-RUN"].value))


print("\nweather and air-side failures")
base_supply = values["TT-0102"]
next(m for m in tower.malfunctions if m.mf_id == "MF-045").set(True, 10.0)
advance_tower(bus, tower, 6000)
hot_supply = float(db["TT-0102"].value)
check("high wet bulb raises cooling-water supply temperature",
      hot_supply > base_supply + 5.0,
      f"{base_supply:.2f} -> {hot_supply:.2f} degC")

db_fan, bus_fan, tower_fan = build_tower()
advance_tower(bus_fan, tower_fan, 6000)
fan_base = float(db_fan["TT-0102"].value)
for stem in ("CT011A", "CT011B"):
    db_fan[f"XY-{stem}-STR"].value = False
    db_fan[f"XY-{stem}-STP"].value = True
advance_tower(bus_fan, tower_fan, 6000)
fan_loss = float(db_fan["TT-0102"].value)
check("loss of both cells removes tower heat rejection",
      fan_loss > fan_base + 15.0,
      f"{fan_base:.2f} -> {fan_loss:.2f} degC")


print("\nplant-wide pump-trip consequence")
plant_db = TagDatabase()
flowsheet = Flowsheet(plant_db, dt=0.1)
engine = SimulationEngine(plant_db, flowsheet, dt=0.1)
snapshot = ROOT / "snapshots" / "lined_up.json"
if snapshot.exists():
    engine.load_snapshot(snapshot)
for _ in range(3000):
    engine._execute_step()
flow_names = ("u200_cw_flow", "u400_cw_flow", "t1_cw_flow",
              "t2_cw_flow", "u800_cw_flow")
flow_before = {name: float(flowsheet.bus[name]) for name in flow_names}
p_before = (float(plant_db["PT-5001"].value),
            float(plant_db["PT-6001"].value))
plant_db["XY-P011A-STR"].value = False
plant_db["XY-P011A-STP"].value = True
plant_db["XY-P011B-STR"].value = False
plant_db["XY-P011B-STP"].value = True
for _ in range(6000):
    engine._execute_step()
flow_after = {name: float(flowsheet.bus[name]) for name in flow_names}
p_after = (float(plant_db["PT-5001"].value),
           float(plant_db["PT-6001"].value))
check("both stopped pumps collapse the common header",
      float(plant_db["PT-0105"].value) < 0.1,
      f"PT-0105={float(plant_db['PT-0105'].value):.4f} barg")
check("every connected exchanger loses cooling-water flow",
      all(flow_after[n] < max(flow_before[n] * 0.01, 0.1) for n in flow_names),
      str({n: (round(flow_before[n], 1), round(flow_after[n], 3))
           for n in flow_names}))
check("both overhead systems pressurise when condenser cooling is lost",
      all(after > before + 2.0 for before, after in zip(p_before, p_after)),
      f"T1 {p_before[0]:.2f}->{p_after[0]:.2f}, T2 {p_before[1]:.2f}->{p_after[1]:.2f} barg")
check("pump trip produces no model execution errors", engine.stats.errors == 0,
      str(engine.stats.errors))


print("\nDCS boundary")
check("CT1 exposes commands for the host controller",
      all(tag in plant_db and plant_db[tag].kind.value == "AO"
          for tag in ("SC-0101", "SC-0102", "LCV-0101", "FCV-0102")))
check("CT1 process model has no embedded controllers",
      not hasattr(flowsheet.unit("U010"), "loops"))

print(f"\n{len(FAILURES)} failure(s)")
raise SystemExit(1 if FAILURES else 0)
