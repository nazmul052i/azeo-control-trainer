#!/usr/bin/env python3
"""Parity of the native U400 (reactor R1 and separator D3).

    python tests/test_native_parity_u400.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import os
os.environ.setdefault("AZEO_NATIVE", "0")   # the Python side of this comparison is the reference
from native_unit_parity import Scenario, run  # noqa: E402
from azeoplant.models.u400_reactor import ReactorSection  # noqa: E402

EVENTS = [
    (500, "MF-017", True, 8.0), (1500, "MF-017", False, None),
    (900, "MF-020", True, 1.0), (1700, "MF-020", False, None),
    (1200, "MF-034", True, 30.0), (2600, "MF-034", False, None),
    (2000, "MF-032", True, 60.0), (3000, "MF-032", False, None),
    (2800, "MF-033", True, 1.0), (3100, "MF-033", False, None),
]


def setup(db, bus, unit):
    # beds at operating temperature, as the plant starts
    unit.bed1.reset(380.0)
    unit.bed2.reset(385.0)
    unit.conversion.reset(95.0)
    bus.restore("charge_flow", 90.0)
    bus.restore("h1_outlet_temperature", 349.0)
    bus.restore("recycle_gas_to_h1", 15000.0)


sc = Scenario(
    code="U400", python_cls=ReactorSection, native_name="ReactorSection",
    analog_ao={"FCV-4001": (0.0, 100.0), "FCV-4002": (0.0, 100.0), "LCV-4001": (10.0, 90.0),
               "LCV-4002": (0.0, 60.0), "PCV-4001": (0.0, 80.0), "FCV-4003": (0.0, 100.0)},
    discrete_do=["XY-XV4001-OPN", "XY-XV4002-OPN", "XY-BDV4001-OPN"],
    bus_inputs={"air_failure": (0.0, 0.0), "esd_u400": (0.0, 0.0), "esd_depressure": (0.0, 0.0),
                "charge_flow": (40.0, 150.0), "h1_outlet_temperature": (300.0, 400.0),
                "recycle_gas_to_h1": (5000.0, 25000.0), "c1_discharge_pressure": (10.0, 20.0),
                "d3_offgas_total": (500.0, 3000.0), "t1_pressure_bara": (8.0, 11.0)},
    bus_outputs=("quench_and_makeup_demand", "r1_effluent_temperature", "d3_offgas_available", "d3_offgas_to_c1",
                 "d3_offgas_total", "r1_h2_consumption", "d3_liquid_lightfrac", "d3_liquid_to_t1",
                 "d3_liquid_temperature", "r1_bed_temperature", "r1_conversion"),
    events=EVENTS, steps=4000, flowsheet_index=4, setup=setup, p_toggle=0.003,
)

sys.exit(run(sc))
