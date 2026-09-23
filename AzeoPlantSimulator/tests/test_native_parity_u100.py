#!/usr/bin/env python3
"""Parity of the native U100 (feed surge drum and charge pumps).

    python tests/test_native_parity_u100.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import os
os.environ.setdefault("AZEO_NATIVE", "0")   # the Python side of this comparison is the reference
from native_unit_parity import Scenario, run  # noqa: E402
from azeoplant.models.u100_feed import FeedSection  # noqa: E402

EVENTS = [
    (300, "MF-001", True, 2.0), (1200, "MF-001", False, None),
    (600, "MF-002", True, 3.0), (1500, "MF-002", False, None),
    (800, "MF-012", True, 4.0), (2000, "MF-012", False, None),
    (900, "MF-013", True, 2.0), (2100, "MF-013", False, None),
    (1000, "MF-014", True, 1.0), (1600, "MF-014", False, None),
    (1700, "MF-022", True, 20.0), (2600, "MF-022", False, None),
    (2200, "MF-031", True, 70.0), (3200, "MF-031", False, None),
    (2400, "MF-011", True, 1.0), (3000, "MF-011", False, None),
    (2800, "MF-007", True, 1.0), (3300, "MF-007", False, None),
    (3400, "MF-021", True, 1.0), (3700, "MF-021", False, None),
    (3500, "MF-023", True, 1.0), (3800, "MF-023", False, None),
]


def setup(db, bus, unit):
    # the duty pump running, as the plant starts
    db["XY-P101A-STR"].value = True
    unit.motor_a.running = True
    unit.motor_a.speed_pct = 100.0


sc = Scenario(
    code="U100", python_cls=FeedSection, native_name="FeedSection",
    analog_ao={"FCV-1001": (20.0, 100.0), "FCV-1002": (0.0, 60.0), "LCV-1001": (0.0, 40.0), "SC-1001": (60.0, 100.0)},
    discrete_do=["XY-P101A-STR", "XY-P101A-STP", "XY-P101B-STR", "XY-P101B-STP", "XY-MOV1001A-OPN", "XY-MOV1001A-CLS",
                 "XY-MOV1001B-OPN", "XY-MOV1001B-CLS", "XY-XV1001-OPN", "XY-MOV1002-OPN", "XY-MOV1002-CLS"],
    bus_inputs={"air_failure": (0.0, 0.0), "esd_u100": (0.0, 0.0), "h1_inlet_pressure": (6.0, 14.0),
                "t1_bottoms_recycle": (0.0, 80.0), "t1_bottoms_temperature": (150.0, 260.0)},
    bus_outputs=("charge_flow", "charge_temperature", "d1_level"),
    events=EVENTS, steps=4000, flowsheet_index=1, setup=setup,
)

sys.exit(run(sc))
