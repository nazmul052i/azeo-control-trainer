"""Host features retained across the native plant cutover; run on both cores."""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from azeoplant.core import native  # noqa: E402
from azeoplant.core.tags import Quality  # noqa: E402
from azeoplant.embedding import create_embedded_plant  # noqa: E402


@pytest.fixture
def runtime():
    instance = create_embedded_plant({
        "source": "NATIVE-INTEGRATION-TEST", "dt": .1, "autorun": False,
        "snapshot": "snapshots/lined_up.json", "catalog": "data/opcua_tag_catalog.json",
    })
    instance.start()
    try:
        yield instance
    finally:
        instance.stop()


def test_requested_core_reaches_all_units_and_preserves_output_ownership(runtime):
    expected = "cpp" if native.requested() else "python"
    assert runtime.health().detail["core"] == expected
    if expected == "cpp":
        assert runtime.health().detail["native_units"] == 10
        assert type(runtime.db).__module__ == "azeoplant._azeocore"
        assert type(runtime.bus).__module__ == "azeoplant._azeocore"
        assert all(type(unit).__module__ == "azeoplant._azeocore"
                   for unit in runtime.flowsheet.units)
    assert len(runtime.catalog) == 612
    for record in runtime.catalog:
        tag = runtime.db[record["name"]]
        assert record["kind"] == tag.kind.name
        if tag.kind.analogue:
            assert (record["lo"], record["hi"], record["eu"]) == (tag.lo, tag.hi, tag.eu)
    assert not runtime.controller.enabled
    assert runtime.bus.holder == "NATIVE-INTEGRATION-TEST"
    before = runtime.read_sample("FCV-1001").value
    demand = 40.0 if before != 40.0 else 45.0
    runtime.write("FCV-1001", demand)
    assert runtime.read_sample("FCV-1001").value == before
    runtime.step(3)
    assert runtime.read_sample("FCV-1001").value == demand
    assert runtime.engine.stats.errors == 0
    assert not runtime.controller.enabled


@pytest.mark.parametrize("quality", ["GOOD", "UNCERTAIN", "BAD"])
def test_engineering_input_value_and_quality_survive_steps_and_snapshots(runtime, tmp_path, quality):
    runtime.set_simulated_input("FT-1001", 71.25, quality)
    runtime.step(3)
    sample = runtime.read_sample("FT-1001")
    assert sample.value == 71.25
    assert sample.quality is Quality[quality]
    assert runtime.bus.active_forces()[0].reason == "Virtual I/O signal simulation"
    snapshot = runtime.save_simulation_snapshot(tmp_path / "forced.json")
    runtime.clear_simulated_input("FT-1001")
    runtime.step(2)
    assert not runtime.bus.active_forces()
    runtime.load_simulation_snapshot(snapshot)
    runtime.step(2)
    sample = runtime.read_sample("FT-1001")
    assert sample.value == 71.25
    assert sample.quality is Quality[quality]
    assert not runtime.controller.enabled
    assert runtime.bus.holder == "NATIVE-INTEGRATION-TEST"


def test_input_simulation_cannot_take_an_output(runtime):
    with pytest.raises(PermissionError):
        runtime.set_simulated_input("FCV-1001", 20.0, "GOOD")
    assert not runtime.bus.active_forces()


def test_cached_status_remains_private_and_updates_after_a_single_step(runtime):
    catalog = runtime.simulation_model_catalog()
    catalog["units"][0]["name"] = "changed by a reader"
    assert runtime.simulation_model_catalog()["units"][0]["name"] != "changed by a reader"
    before = runtime.simulation_model_state()
    before["units"].clear()
    assert len(runtime.simulation_model_state()["units"]) == 10
    runtime.set_simulated_input("FT-1001", 71.25, "BAD")
    runtime.step(1)
    after = runtime.simulation_model_state()
    assert after["sampled_at"] > before["sampled_at"]
    assert next(unit for unit in after["units"] if unit["unit"] == "U100")["bad_signals"] >= 1


def test_restoring_the_standalone_lineup_keeps_host_control_ownership(runtime):
    runtime.load_simulation_snapshot(ROOT / "snapshots/lined_up.json")
    assert not runtime.controller.enabled
    assert runtime.bus.holder == "NATIVE-INTEGRATION-TEST"
    runtime.step(3)
    assert not runtime.controller.enabled
    assert runtime.engine.stats.errors == 0


def test_missing_requested_core_is_a_visible_start_failure(monkeypatch):
    monkeypatch.setattr(native, "requested", lambda: True)
    monkeypatch.setattr(native, "active", lambda: {})
    with pytest.raises(RuntimeError, match="tools/build_plant_core.py"):
        create_embedded_plant({"source": "TEST", "dt": .1})


def test_model_diagnostics_and_disturbances_are_plain_data_and_keep_external_control(runtime, tmp_path):
    import json
    catalog = runtime.simulation_model_catalog()
    json.dumps(catalog, allow_nan=False)
    assert len(catalog["units"]) == 10
    assert sum(row["signals"] for row in catalog["units"]) == 612
    assert catalog["parameters"]
    row = next(row for row in catalog["disturbances"] if row["parameter"])
    value = (row["minimum"] + row["maximum"]) / 2
    runtime.set_simulation_disturbance(row["id"], True, value)
    runtime.step(2)
    state = runtime.simulation_model_state()
    fault = next(fault for fault in state["disturbances"] if fault["id"] == row["id"])
    assert fault == {"id": row["id"], "active": True, "value": value}
    snapshot = runtime.save_simulation_snapshot(tmp_path / "disturbed.json")
    runtime.set_simulation_disturbance(row["id"], False)
    runtime.load_simulation_snapshot(snapshot)
    assert next(fault for fault in runtime.simulation_model_state()["disturbances"]
                if fault["id"] == row["id"])["active"]
    for bad in (float("nan"), float("inf"), row["maximum"] + 1):
        with pytest.raises(ValueError):
            runtime.set_simulation_disturbance(row["id"], True, bad)
    with pytest.raises(ValueError, match="Unknown"):
        runtime.set_simulation_disturbance("not-a-fault", True)
    assert runtime.bus.holder == "NATIVE-INTEGRATION-TEST"
    assert not runtime.controller.enabled
    assert runtime.engine.stats.errors == 0
