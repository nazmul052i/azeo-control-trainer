"""A visible edge is not sufficient: process ports must hit the flow axis."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from azeo_control_trainer.core.hmi.pvms.symbols import connection_ports  # noqa: E402


@pytest.mark.parametrize("name", ["control_valve", "mov", "actuated_valve"])
def test_actuated_valve_ports_follow_the_bow_tie_center_not_the_actuator_frame(name):
    # In the source paths the two triangles share the point at (54.5, 74.5)
    # after extraction. The 108 px frame also contains the actuator above it.
    ports = connection_ports(name)
    assert ports["w"] == pytest.approx((4.5 / 108, 74.5 / 108), abs=1e-6)
    assert ports["e"] == pytest.approx((104.5 / 108, 74.5 / 108), abs=1e-6)


@pytest.mark.parametrize("name,side,x", [("pump", "w", 5.1), ("pump_3", "e", 73.9)])
def test_centrifugal_suction_hits_the_center_of_the_mouth(name, side, x):
    # The casing lips end at y=29.5 and y=39.5 in the 78 by 75 viewBox.
    assert connection_ports(name)[side] == pytest.approx((x / 78, 34.5 / 75), abs=1e-6)


def test_condenser_connections_share_exact_horizontal_and_vertical_axes():
    ports = connection_ports("condenser")
    assert ports["e"][1] == ports["w"][1]
    assert ports["n"][0] == ports["s"][0]


def test_kettle_reboiler_ports_hit_head_and_shell_midpoints():
    ports = connection_ports("reboiler")
    # The tube head spans y=19.5..34.5; the shell end spans y=4.5..34.5.
    assert ports["w"] == pytest.approx((4.5 / 99, 27 / 41), abs=1e-6)
    assert ports["e"] == pytest.approx((95.5 / 99, 19.5 / 41), abs=1e-6)


@pytest.mark.parametrize(
    "name,side,point,size",
    [
        ("compressor", "w", (4.6, 35.345), (78, 78)),
        ("compressor", "e", (74, 14.45), (78, 78)),
        ("blower", "w", (4, 13.875), (80, 75)),
        ("pump_2", "s", (34.455, 73.9), (70, 85)),
        ("air_separator", "w", (4.5, 87.5), (73.5, 114)),
        ("air_separator", "e", (70, 27.5), (73.5, 114)),
        ("furnace", "w", (4.5, 59), (88, 107)),
        ("furnace", "e", (84.5, 84), (88, 107)),
        ("angle_valve", "e", (104, 34), (108, 88)),
        ("angle_valve", "s", (54, 84), (108, 88)),
        ("three_way_valve", "e", (104, 34), (108, 88)),
        ("block_bleed_valve", "e", (104, 33.9), (108, 138)),
        ("dome_vessel", "n", (66.93, 4.43), (103, 63)),
        ("pit_vessel", "s", (37.06, 59.5), (103, 63)),
        ("forced_flow_air_cooler", "w", (4, 11.5), (78, 38)),
    ],
)
def test_asymmetric_equipment_hits_its_nozzle_instead_of_casing_or_support(name, side, point, size):
    # These coordinates come from the source path's mouth/flange/coil ends,
    # independently of the generator's outline projection.
    assert connection_ports(name)[side] == pytest.approx(
        (point[0] / size[0], point[1] / size[1]), abs=1e-6
    )


def test_valve_cardinal_connections_stay_on_the_stem_and_body_axes():
    from azeo_control_trainer.core.hmi.pvms import symbols

    for name in symbols.CATALOG:
        if "gate_valve" not in name and name not in (
            "valve",
            "valve_closed",
            "control_valve",
            "mov",
            "actuated_valve",
        ):
            continue
        ports = connection_ports(name)
        center_x = (ports["w"][0] + ports["e"][0]) / 2
        assert ports["w"][1] == pytest.approx(ports["e"][1], abs=1e-6), name
        assert ports["s"][0] == pytest.approx(center_x, abs=1e-6), name
