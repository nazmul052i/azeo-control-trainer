#!/usr/bin/env python3
"""Parity of the native U600 (distillation column T2).

    python tests/test_native_parity_u600.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import os
os.environ.setdefault("AZEO_NATIVE", "0")   # the Python side of this comparison is the reference
from native_unit_parity import Scenario, run  # noqa: E402
from azeoplant.models.u500_u600_columns import ColumnT2  # noqa: E402

EVENTS = [
    (400, "MF-601", True, 3.0), (1400, "MF-601", False, None),
    (700, "MF-603", True, 25.0), (2000, "MF-603", False, None),
    (1000, "MF-604", True, 30.0), (2300, "MF-604", False, None),
    (1300, "MF-606", True, 40.0), (2600, "MF-606", False, None),
    (1800, "MF-602", True, 1.0), (2400, "MF-602", False, None),
    (2800, "MF-605", True, 1.0), (3400, "MF-605", False, None),
    (3000, "MF-603", True, 55.0), (3700, "MF-603", False, None),
]


def setup(db, bus, unit):
    # the column fed from T1 and heated, as the plant starts
    bus.restore("t1_distillate_to_t2", 40.0)
    bus.restore("t1_distillate_temperature", 48.0)
    bus.restore("mp_steam_pressure_bara", 37.0)
    bus.restore("mp_steam_temperature", 373.0)


sc = Scenario(
    code="U600", python_cls=ColumnT2, native_name="ColumnT2",
    analog_ao={"FCV-6001": (10.0, 90.0), "FCV-6002": (0.0, 80.0), "FCV-6003": (0.0, 80.0), "FCV-6004": (0.0, 100.0),
               "FCV-6005": (10.0, 100.0), "PCV-6001": (0.0, 60.0), "FCV-6006": (0.0, 40.0), "FCV-6007": (0.0, 40.0)},
    discrete_do=["XY-XV6001-OPN", "XY-P601A-STR", "XY-P601A-STP", "XY-P601B-STR", "XY-P601B-STP",
                 "XY-P602A-STR", "XY-P602A-STP", "XY-P602B-STR", "XY-P602B-STP",
                 "XY-MOV6001A-OPN", "XY-MOV6001A-CLS", "XY-MOV6001B-OPN", "XY-MOV6001B-CLS",
                 "XY-MOV6002A-OPN", "XY-MOV6002A-CLS", "XY-MOV6002B-OPN", "XY-MOV6002B-CLS"],
    bus_inputs={"air_failure": (0.0, 0.0), "esd_u500": (0.0, 0.0), "t1_distillate_to_t2": (10.0, 90.0),
                "t1_distillate_temperature": (30.0, 90.0), "mp_steam_pressure_bara": (28.0, 40.0),
                "mp_steam_temperature": (300.0, 400.0), "cooling_water_temperature": (15.0, 38.0)},
    bus_outputs=("t2_steam_demand", "t2_pressure_bara", "r2_product", "t2_heavy_rundown"),
    events=EVENTS, steps=4000, flowsheet_index=6, setup=setup, p_toggle=0.002,
)

sys.exit(run(sc))
