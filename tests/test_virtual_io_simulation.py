"""Provider-neutral Virtual I/O simulation and scenario qualification."""
from __future__ import annotations

import json
import os
import sys
import time
import types
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "AzeoPlantSimulator"))

from azeo_control_trainer.connectivity.fieldio.local_virtual_io import (  # noqa: E402
    LocalVirtualIoDriver,
)
from azeo_control_trainer.connectivity.fieldio.simulation import (  # noqa: E402
    SignalSimulation, read_profile,
)
from azeo_control_trainer.core.datastore.shared_data_store import (  # noqa: E402
    SharedDataStore,
)
from azeo_control_trainer.azeo_explorer.virtual_io_simulator import (  # noqa: E402
    VirtualIoSimulatorDialog,
)
from azeoplant.core.tags import Quality as PlantQuality  # noqa: E402
from azeoplant.core.tags import TagDatabase  # noqa: E402
from azeoplant.io.bus import VirtualIOBus  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


class _SimulatingProvider:
    def __init__(self):
        self.samples = {
            "AI-1": (25.0, "GOOD", time.time()),
            "DI-1": (False, "GOOD", time.time()),
            "AO-1": (40.0, "GOOD", time.time()),
        }
        self.simulated = {}

    def start(self):
        pass

    def stop(self):
        pass

    def claim(self, _source):
        return True

    def release(self, _source):
        pass

    def read(self, tag):
        return self.samples[tag][0]

    def read_samples(self, tags):
        return {tag: self.samples[tag] for tag in tags}

    def write(self, tag, value):
        self.samples[tag] = (value, "GOOD", time.time())

    def set_simulated_input(self, tag, value, quality):
        self.simulated[tag] = (value, quality)
        self.samples[tag] = (value, quality, time.time())

    def clear_simulated_input(self, tag):
        self.simulated.pop(tag, None)


def _driver(tmp_path, monkeypatch):
    module = types.ModuleType("qualified_simulating_provider")
    made = []

    def create(**_options):
        product = _SimulatingProvider()
        made.append(product)
        return product

    module.create = create
    monkeypatch.setitem(sys.modules, module.__name__, module)
    config = {
        "type": "local_virtual_io",
        "source": "TEST-CTRL",
        "period_ms": 60_000,
        "input_stale_timeout_s": 2.0,
        "claim_outputs": True,
        "provider_manages_claim": False,
        "provider": {"factory": f"{module.__name__}:create"},
        "signals": {
            "FIELD.AI": {"signal": "AI-1", "direction": "read",
                         "kind": "AI", "plant_unit": "U100",
                         "range": [0, 100]},
            "FIELD.DI": {"signal": "DI-1", "direction": "read",
                         "kind": "DI", "plant_unit": "U100"},
            "FIELD.AO": {"signal": "AO-1", "direction": "write",
                         "kind": "AO", "plant_unit": "U100",
                         "range": [0, 100]},
        },
    }
    driver = LocalVirtualIoDriver(SharedDataStore(), config, tmp_path)
    assert driver.start()
    return driver, made[0]


def test_signal_profiles_are_typed_and_deterministic() -> None:
    profile = SignalSimulation(
        "AI", mode="sawtooth", low=10, high=20, period_s=4,
        started_at=100,
    )
    assert profile.sample(101) == pytest.approx(12.5)
    assert profile.sample(103) == pytest.approx(17.5)
    discrete = SignalSimulation(
        "DI", mode="square", period_s=2, started_at=10)
    assert discrete.sample(10.25, discrete=True) is False
    assert discrete.sample(11.25, discrete=True) is True


def test_embedded_bus_reapplies_value_and_quality_over_live_physics() -> None:
    db = TagDatabase()
    ai = db.analog("AI-1", "AI", "U100", "Measurement", "bar", 0, 100, 20)
    db.analog("AO-1", "AO", "U100", "Demand", "%", 0, 100, 50)
    bus = VirtualIOBus(db)
    bus.simulate_input("AI-1", 72.0, "UNCERTAIN")
    ai.set(31.0)  # the model continues computing underneath the override
    bus.tick()
    assert ai.value == pytest.approx(72.0)
    assert ai.quality is PlantQuality.UNCERTAIN
    assert bus.capture_state()["force_qualities"]["AI-1"] == 1
    with pytest.raises(PermissionError, match="only AI and DI"):
        bus.simulate_input("AO-1", 10.0, "GOOD")
    bus.clear_simulated_input("AI-1")
    assert not bus.active_forces()


def test_driver_simulates_inputs_but_never_controller_outputs(
        tmp_path, monkeypatch) -> None:
    driver, provider = _driver(tmp_path, monkeypatch)
    try:
        profile = driver.configure_signal_simulation(
            "FIELD.AI", mode="static", value=73.5, quality="UNCERTAIN")
        assert profile.store_tag == "FIELD.AI"
        driver.scan_once()
        assert provider.simulated["AI-1"] == (73.5, "UNCERTAIN")
        assert driver.store.get("FIELD.AI") == pytest.approx(73.5)
        assert driver.simulation_capabilities()["active"] == 1
        row = next(row for row in driver.signal_simulation_snapshot()
                   if row["store_tag"] == "FIELD.AI")
        assert row["simulation"]["mode"] == "static"
        with pytest.raises(ValueError, match="not a configured input"):
            driver.configure_signal_simulation("FIELD.AO", value=99)
        assert driver.clear_signal_simulation("FIELD.AI")
        assert "AI-1" not in provider.simulated
    finally:
        driver.stop()


def test_scenarios_are_atomic_validated_and_repeatable(
        tmp_path, monkeypatch) -> None:
    driver, provider = _driver(tmp_path, monkeypatch)
    try:
        driver.configure_signal_simulation(
            "FIELD.AI", mode="sine", low=20, high=80, period_s=12)
        driver.configure_signal_simulation(
            "FIELD.DI", mode="square", low=0, high=1, period_s=4,
            quality="BAD")
        path = driver.save_simulation_profile(tmp_path / "scenario.json")
        document = json.loads(path.read_text(encoding="utf-8"))
        assert document["type"] == "azeo.virtual_io_scenario"
        assert len(document["signals"]) == 2
        assert not list(tmp_path.glob(".*.tmp"))

        driver.clear_all_signal_simulations()
        assert driver.load_simulation_profile(path) == 2
        assert set(provider.simulated) == {"AI-1", "DI-1"}

        invalid = tmp_path / "invalid.json"
        invalid.write_text(json.dumps({
            "schema_version": 1,
            "type": "azeo.virtual_io_scenario",
            "signals": [{"store_tag": "FIELD.AO", "mode": "static"}],
        }), encoding="utf-8")
        with pytest.raises(ValueError, match="not a configured input"):
            driver.load_simulation_profile(invalid)
        assert len(read_profile(path)) == 2
    finally:
        driver.stop()


def test_virtual_io_dialog_exposes_hierarchy_and_typed_configuration(
        tmp_path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    driver, _provider = _driver(tmp_path, monkeypatch)
    try:
        dialog = VirtualIoSimulatorDialog(driver, tmp_path)
        assert dialog.units.topLevelItemCount() == 1
        assert dialog.units.topLevelItem(0).childCount() == 2  # All + U100
        ai_row = next(index for index in range(dialog.table.rowCount())
                      if dialog.table.item(index, 0).text() == "FIELD.AI")
        dialog.table.setCurrentCell(ai_row, 0)
        assert dialog.configure_selected({
            "mode": "static", "value": 55.0, "low": 0.0,
            "high": 100.0, "period_s": 5.0, "quality": "BAD",
        })
        assert driver.simulation_capabilities()["active"] == 1
        dialog.close()
        app.processEvents()
    finally:
        driver.stop()


def test_shipped_embedded_plant_implements_simulation_contract(tmp_path) -> None:
    project = REPO / "projects" / "AzeoPlantVirtualController"
    document = json.loads((project / "_project.json").read_text(
        encoding="utf-8"))
    config = document["areas"][0]["virtual_io"]
    driver = LocalVirtualIoDriver(SharedDataStore(), config, project)
    try:
        assert driver.start()
        assert driver.simulation_capabilities()["available"]
        process = driver.process_simulation_capabilities()
        assert process["available"]
        assert process["snapshot_supported"]
        driver.pause_process_simulation()
        before_time = driver.process_simulation_capabilities()["sim_time"]
        time.sleep(process["dt"] * 2)
        assert driver.process_simulation_capabilities()["sim_time"] \
            == pytest.approx(before_time)
        driver.step_process_simulation()
        assert driver.process_simulation_capabilities()["sim_time"] \
            == pytest.approx(before_time + process["dt"])
        assert driver.set_process_simulation_speed(2.0) == pytest.approx(2.0)
        snapshot = driver.save_process_simulation_snapshot(
            tmp_path / "embedded-process.json")
        assert snapshot.is_file()
        assert driver.load_process_simulation_snapshot(snapshot)["tags"] > 0
        driver.resume_process_simulation()
        route = next(route for route in driver.read_routes
                     if route.kind == "AI" and route.eu_range is not None)
        low, high = map(float, route.eu_range)
        expected = low + (high - low) * 0.61
        driver.configure_signal_simulation(
            route.store_tag, value=expected, quality="BAD")
        driver.scan_once()
        sample = driver.store.get_sample(route.store_tag)
        assert sample is not None
        assert sample.value == pytest.approx(expected)
        assert getattr(sample.quality, "name", sample.quality) == "BAD"
        assert driver.clear_signal_simulation(route.store_tag)
    finally:
        driver.stop()
