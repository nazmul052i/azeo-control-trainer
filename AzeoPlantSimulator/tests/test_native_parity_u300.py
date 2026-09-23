#!/usr/bin/env python3
"""Parity of the native U300 (fired heater H1).

    python tests/test_native_parity_u300.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import os
os.environ.setdefault("AZEO_NATIVE", "0")   # the Python side of this comparison is the reference
from native_unit_parity import Scenario, run  # noqa: E402
from azeoplant.models.u300_heater import FiredHeater  # noqa: E402

EVENTS = [
    (400, "MF-003", True, 6.0), (1400, "MF-003", False, None),
    (900, "MF-019", True, 60.0), (2200, "MF-019", False, None),
    (1200, "MF-030", True, 12.0), (2600, "MF-030", False, None),
    (1800, "MF-016", True, 1.0), (2300, "MF-016", False, None),
    (2900, "MF-018", True, 1.0), (3400, "MF-018", False, None),
]


def setup(db, bus, unit):
    # a lit heater with the burner header fed, as the plant starts
    db["XY-3010"].value = True
    unit.lit = True
    unit.burner_pressure.reset(3.0 + 1.01325)
    bus.restore("fg_to_h1_flow", 1200.0)


sc = Scenario(
    code="U300", python_cls=FiredHeater, native_name="FiredHeater",
    analog_ao={"FCV-3001": (30.0, 100.0), "FCV-3002": (0.0, 100.0), "FCV-3003": (0.0, 100.0),
               "FCV-3004": (20.0, 100.0), "TCV-3002": (0.0, 100.0), "FCV-3005": (10.0, 100.0),
               "FCV-3006": (10.0, 100.0), "SC-3001": (30.0, 100.0)},
    discrete_do=["XY-XV3001-OPN", "XY-3010", "XY-3011", "XY-ID301-STR", "XY-ID301-STP", "XY-XV3002-OPN"],
    bus_inputs={"air_failure": (0.0, 0.0), "esd_u300": (0.0, 0.0), "fg_header_pressure_bara": (14.0, 18.0),
                "fg_to_h1_flow": (200.0, 2200.0), "fg_lhv": (30.0, 45.0), "charge_flow": (40.0, 150.0),
                "charge_temperature": (30.0, 90.0), "r1_effluent_temperature": (300.0, 420.0),
                "recycle_gas_to_h1": (5000.0, 25000.0), "recycle_gas_mw": (8.0, 16.0)},
    bus_outputs=("h1_burner_pressure_bara", "h1_inlet_pressure", "h1_outlet_temperature", "h1_duty_mw"),
    events=EVENTS, steps=4000, flowsheet_index=3, setup=setup, p_toggle=0.004,
)

sys.exit(run(sc))
