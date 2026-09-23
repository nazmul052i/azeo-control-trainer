#!/usr/bin/env python3
"""Parity of the native U700 (steam boiler B1 and the MP steam header).

    python tests/test_native_parity_u700.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import os
os.environ.setdefault("AZEO_NATIVE", "0")   # the Python side of this comparison is the reference
from native_unit_parity import Scenario, run  # noqa: E402
from azeoplant.models.u700_boiler import SteamBoiler  # noqa: E402

EVENTS = [
    (600, "MF-037", True, 2.0), (1800, "MF-037", False, None),
    (900, "MF-038", True, 10.0), (2600, "MF-038", False, None),
    (1200, "MF-027", True, 1.0), (2000, "MF-027", False, None),
    (3000, "MF-037", True, 4.5), (3600, "MF-037", False, None),
]


def setup(db, bus, unit):
    # the fan and the duty feedwater pump commanded, the burner lit off the
    # header, both reboilers drawing steam: the boiler as the plant starts
    db["XY-FD701-STR"].value = True
    db["XY-P701A-STR"].value = True
    bus.restore("b1_supply_pressure_bara", 6.0)
    bus.restore("t1_steam_demand", 8.0)
    bus.restore("t2_steam_demand", 6.0)


sc = Scenario(
    code="U700", python_cls=SteamBoiler, native_name="SteamBoiler",
    analog_ao={"FCV-7001": (20.0, 80.0), "FCV-7002": (0.0, 100.0), "FCV-7003": (0.0, 100.0),
               "FCV-7004": (0.0, 60.0), "FCV-7005": (0.0, 40.0), "SC-7001": (30.0, 100.0),
               "SC-7002": (60.0, 100.0), "PCV-7001": (0.0, 50.0), "TCV-7001": (0.0, 60.0),
               "LCV-7001": (0.0, 80.0)},
    discrete_do=["XY-7010", "XY-7011", "XY-XV7001-OPN", "XY-P701A-STR", "XY-P701A-STP", "XY-P701B-STR",
                 "XY-P701B-STP", "XY-FD701-STR", "XY-FD701-STP", "XY-MOV7001A-OPN", "XY-MOV7001A-CLS",
                 "XY-MOV7001B-OPN", "XY-MOV7001B-CLS"],
    bus_inputs={"air_failure": (0.0, 0.0), "esd_u700": (0.0, 0.0), "b1_supply_pressure_bara": (3.0, 8.0),
                "fg_lhv": (30.0, 45.0), "t1_steam_demand": (0.0, 15.0), "t2_steam_demand": (0.0, 15.0)},
    bus_outputs=("b1_fuel_demand", "mp_steam_pressure_bara", "mp_steam_temperature", "mp_extra_demand"),
    events=EVENTS, steps=4000, flowsheet_index=7, setup=setup, p_toggle=0.002,
)

sys.exit(run(sc))
