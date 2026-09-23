#!/usr/bin/env python3
"""Parity of the native U800 (effluent treatment, the pH loop).

    python tests/test_native_parity_u800.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import os
os.environ.setdefault("AZEO_NATIVE", "0")   # the Python side of this comparison is the reference
from native_unit_parity import Scenario, run  # noqa: E402
from azeoplant.models.u800_u900 import EffluentTreatment  # noqa: E402

EVENTS = [
    (500, "MF-039", True, 2.0), (1500, "MF-039", False, None),
    (900, "MF-042", True, 120.0), (2400, "MF-042", False, None),
    (2600, "MF-039", True, 6.5), (3400, "MF-039", False, None),
]


def setup(db, bus, unit):
    # the agitator commanded on, as the plant starts
    db["XY-M801-STR"].value = True
    bus.restore("sour_water_flow", 6.0)


sc = Scenario(
    code="U800", python_cls=EffluentTreatment, native_name="EffluentTreatment",
    analog_ao={"FCV-8001": (0.0, 100.0), "FCV-8002": (0.0, 100.0), "FCV-8003": (0.0, 60.0),
               "FCV-8004": (10.0, 90.0), "TCV-8001": (0.0, 100.0)},
    discrete_do=["XY-M801-STR", "XY-M801-STP", "XY-P801A-STR", "XY-P801A-STP", "XY-P801B-STR", "XY-P801B-STP",
                 "XY-MOV8001A-OPN", "XY-MOV8001A-CLS", "XY-MOV8001B-OPN", "XY-MOV8001B-CLS"],
    bus_inputs={"air_failure": (0.0, 0.0), "sour_water_flow": (0.0, 60.0), "d3_liquid_temperature": (40.0, 120.0)},
    bus_outputs=(),
    events=EVENTS, steps=4000, flowsheet_index=8, setup=setup, p_toggle=0.002,
)

sys.exit(run(sc))
