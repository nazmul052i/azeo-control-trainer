#!/usr/bin/env python3
"""Parity of the native U200 (recycle gas compressor C1).

    python tests/test_native_parity_u200.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import os
os.environ.setdefault("AZEO_NATIVE", "0")   # the Python side of this comparison is the reference
from native_unit_parity import Scenario, run  # noqa: E402
from azeoplant.models.u200_compressor import RecycleCompressor  # noqa: E402

EVENTS = [
    (600, "MF-006", True, 10.0), (1600, "MF-006", False, None),
    (1000, "MF-025", True, 12.0), (2400, "MF-025", False, None),
    (1900, "MF-043", True, 12.0), (2900, "MF-043", False, None),
    (3000, "MF-026", True, 1.0), (3300, "MF-026", False, None),
]


def setup(db, bus, unit):
    # lube oil on and the machine commanded, as the plant starts
    db["XY-P201-STR"].value = True
    db["XY-C1-STR"].value = True
    db["SC-2001"].value = 85.0
    db["FCV-2001"].value = 30.0
    db["PCV-2003"].value = 15.0
    bus.restore("d3_offgas_to_c1", 8000.0)
    bus.restore("quench_and_makeup_demand", 4000.0)


sc = Scenario(
    code="U200", python_cls=RecycleCompressor, native_name="RecycleCompressor",
    analog_ao={"SC-2001": (40.0, 100.0), "FCV-2001": (0.0, 100.0), "PCV-2003": (0.0, 60.0), "GV-2001": (0.0, 40.0),
               "FCV-2004": (50.0, 100.0), "FCV-2002": (50.0, 100.0), "FCV-2003": (0.0, 100.0), "LCV-2001": (0.0, 60.0)},
    discrete_do=["XY-C1-STR", "XY-C1-STP", "XY-P201-STR", "XY-P201-STP", "XY-P202-STR", "XY-P202-STP", "XY-XV2001-OPN"],
    bus_inputs={"air_failure": (0.0, 0.0), "esd_u200": (0.0, 0.0), "d3_offgas_to_c1": (2000.0, 12000.0),
                "quench_and_makeup_demand": (1000.0, 8000.0), "d3_liquid_temperature": (40.0, 120.0),
                "r1_conversion": (50.0, 99.0)},
    bus_outputs=("c1_discharge_pressure", "c1_recycle_gas_flow", "c1_running", "recycle_gas_to_h1", "recycle_gas_mw"),
    events=EVENTS, steps=4000, flowsheet_index=2, setup=setup, p_toggle=0.002,
)

sys.exit(run(sc))
