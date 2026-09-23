#!/usr/bin/env python3
"""Parity of the native U900 (safety instrumented system).

    python tests/test_native_parity_u900.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import os
os.environ.setdefault("AZEO_NATIVE", "0")   # the Python side of this comparison is the reference
from native_unit_parity import Scenario, run  # noqa: E402
from azeoplant.models.u800_u900 import SafetySystem  # noqa: E402

EVENTS = [
    (300, "MF-901", True, 1.0), (900, "MF-901", False, None),
    (600, "MF-904", True, 1.0), (1400, "MF-904", False, None),
    (1200, "MF-902", True, 1.0), (1700, "MF-902", False, None),
    (1500, "MF-903", True, 1.0), (2100, "MF-903", False, None),
    (2000, "MF-905", True, 1.0), (2500, "MF-905", False, None),
    (2800, "MF-906", True, 1.0), (3300, "MF-906", False, None),
]

sc = Scenario(
    code="U900", python_cls=SafetySystem, native_name="SafetySystem",
    analog_ao={},
    discrete_do=["XY-9001", "XY-9002", "XY-9003", "XY-9004", "XY-9005", "XY-9006"],
    bus_inputs={},
    bus_outputs=("esd_total", "esd_reaction", "esd_fractionation", "esd_boiler", "esd_depressure",
                 "esd_u100", "esd_u200", "esd_u300", "esd_u400", "esd_u500", "esd_u700"),
    events=EVENTS, steps=4000, flowsheet_index=9, p_toggle=0.01,
)

sys.exit(run(sc))
