#!/usr/bin/env python3
"""Parity of the native U500 (distillation column T1).

    python tests/test_native_parity_u500.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import os
os.environ.setdefault("AZEO_NATIVE", "0")   # the Python side of this comparison is the reference
from native_unit_parity import Scenario, run  # noqa: E402
from azeoplant.models.u500_u600_columns import ColumnT1  # noqa: E402

EVENTS = [
    (400, "MF-501", True, 3.0), (1400, "MF-501", False, None),
    (700, "MF-503", True, 25.0), (2000, "MF-503", False, None),
    (1000, "MF-504", True, 30.0), (2300, "MF-504", False, None),
    (1300, "MF-506", True, 40.0), (2600, "MF-506", False, None),
    (1800, "MF-502", True, 1.0), (2400, "MF-502", False, None),
    (2800, "MF-505", True, 1.0), (3400, "MF-505", False, None),
    (3000, "MF-503", True, 55.0), (3700, "MF-503", False, None),
]


def setup(db, bus, unit):
    # the column fed and heated, as the plant starts
    bus.restore("d3_liquid_to_t1", 60.0)
    bus.restore("d3_liquid_lightfrac", 0.46)
    bus.restore("mp_steam_pressure_bara", 37.0)
    bus.restore("mp_steam_temperature", 373.0)


sc = Scenario(
    code="U500", python_cls=ColumnT1, native_name="ColumnT1",
    analog_ao={"FCV-5001": (10.0, 90.0), "FCV-5002": (0.0, 80.0), "FCV-5003": (0.0, 80.0), "FCV-5004": (0.0, 100.0),
               "FCV-5005": (10.0, 100.0), "PCV-5001": (0.0, 60.0), "LCV-5001": (0.0, 40.0), "PCV-5002": (0.0, 50.0),
               "FCV-5006": (0.0, 40.0), "FCV-5007": (0.0, 40.0)},
    discrete_do=["XY-XV5001-OPN", "XY-P501A-STR", "XY-P501A-STP", "XY-P501B-STR", "XY-P501B-STP",
                 "XY-P502A-STR", "XY-P502A-STP", "XY-P502B-STR", "XY-P502B-STP",
                 "XY-MOV5001A-OPN", "XY-MOV5001A-CLS", "XY-MOV5001B-OPN", "XY-MOV5001B-CLS",
                 "XY-MOV5002A-OPN", "XY-MOV5002A-CLS", "XY-MOV5002B-OPN", "XY-MOV5002B-CLS"],
    bus_inputs={"air_failure": (0.0, 0.0), "esd_u500": (0.0, 0.0), "d3_liquid_to_t1": (20.0, 120.0),
                "d3_liquid_temperature": (40.0, 120.0), "d3_liquid_lightfrac": (0.3, 0.7),
                "mp_steam_pressure_bara": (28.0, 40.0), "mp_steam_temperature": (300.0, 400.0),
                "cooling_water_temperature": (15.0, 38.0)},
    bus_outputs=("t1_steam_demand", "t1_pressure_bara", "t1_distillate", "t1_distillate_to_t2",
                 "t1_distillate_to_storage", "t1_distillate_temperature", "t1_bottoms_recycle",
                 "t1_bottoms_temperature"),
    events=EVENTS, steps=4000, flowsheet_index=5, setup=setup, p_toggle=0.002,
)

sys.exit(run(sc))
