"""Full-project qualification for the APVC lined-up commissioning state.

The isolated one-loop bumpless tests cannot catch a project that downloads
every controller in Auto, or a DEVCTL that starts from STOPPED while its
motor is already running.  This test exercises the actual 160-module project
against its configured embedded provider and proves that the project adopts
the plant before an operator transfers any block to its normal mode.
"""
from __future__ import annotations

import copy
import json
import logging
import math
import sys
import time
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from azeo_control_trainer.connectivity.fieldio.local_virtual_io import (  # noqa: E402
    LocalVirtualIoDriver,
)
from azeo_control_trainer.core.datastore.shared_data_store import (  # noqa: E402
    SharedDataStore,
)
from azeo_control_trainer.core.strategy import blocks as _registered_blocks  # noqa: E402,F401
from azeo_control_trainer.core.strategy.engine.bridge import DataBridge  # noqa: E402
from azeo_control_trainer.core.strategy.engine.compiler import (  # noqa: E402
    compile_strategy,
)
from azeo_control_trainer.core.strategy.engine.runtime import (  # noqa: E402
    StrategyRuntime,
)
from azeo_control_trainer.core.strategy.engine.runtime_context import (  # noqa: E402
    RuntimeContext,
)
from azeo_control_trainer.core.strategy.serialization.strategy_io import (  # noqa: E402
    load_strategy,
)


PROJECT = REPO / "projects" / "AzeoPlantVirtualController"
COOLING_WATER_STARTUP = {
    "TT-0102": 28.0,
    "PT-0105": 2.4,
    "LT-0101": 65.0,
    "AT-0103": 800.0,
}


def _project_document() -> dict:
    return json.loads((PROJECT / "_project.json").read_text(encoding="utf-8"))


def _strategy_documents(project: dict) -> list[tuple[Path, dict]]:
    area = project["areas"][0]
    result = []
    for relative in [*area["strategies"], *area.get("sfc_modules", [])]:
        path = PROJECT / relative
        result.append((path, json.loads(path.read_text(encoding="utf-8"))))
    return result


def _startup_pv_tag(document: dict, pid: dict,
                    regulatory_basis: dict[str, dict]) -> str:
    """Resolve the project-engineered PV used to seed one PID's SP."""
    name = str(pid.get("instance_name") or "")
    basis = regulatory_basis.get(name)
    if basis is not None:
        return str(basis["pv"])
    engineering = document.get("engineering", {})
    mapped = engineering.get("mapped_input_tags") or []
    if len(mapped) == 1:
        return str(mapped[0])
    return str(engineering.get("pv_source_text") or "")


def test_every_control_and_field_output_has_a_lined_up_start_state() -> None:
    """Static project data must express the safe commissioning posture."""
    project = _project_document()
    area = project["areas"][0]
    commissioning = area["virtual_io"].get("commissioning", {})
    assert commissioning == {
        "startup_state": "lined_up_hold",
        "initial_modes": {"PID": "MAN", "AO": "MAN", "DO": "MAN"},
        "pid_setpoint_source": "snapshot_pv_with_engineered_exceptions",
        "engineered_setpoint_exceptions": {
            "AIC-7002": "constraint_design_sp",
            "FIC-5003": "snapshot_pv_x_1.35_headroom",
            "FIC-6003": "snapshot_pv_x_1.35_headroom",
            "PDIC-5001": "constraint_design_sp",
            "PDIC-6001": "constraint_design_sp",
            "PIC-1001": "constraint_design_sp",
            "PIC-2002": "constraint_design_sp",
            "PIC-5001": "commissioned_pressure_sp",
            "PIC-7002": "constraint_design_sp",
            "TIC-2001": "constraint_design_sp",
            "TIC-3004": "constraint_design_sp",
            "TIC-4002": "constraint_design_sp",
        },
        "transfer_policy": "operator_one_loop_at_a_time",
    }

    snapshot = json.loads(
        (PROJECT / area["virtual_io"]["snapshot"]).read_text(encoding="utf-8")
    )["tags"]
    engineering = json.loads(
        (PROJECT / area["virtual_io"]["control_module_basis"]).read_text(
            encoding="utf-8"
        )
    )
    basis = engineering["modules"]
    assert engineering["commissioning"] == commissioning

    pid_count = 0
    output_count = 0
    pid_configs: dict[str, dict] = {}
    engineered_sp = {
        "PIC-1001": 24.0,
        "PIC-2002": 56.0,
        "TIC-2001": 185.0,
        "TIC-3004": 600.0,
        "TIC-4002": 392.0,
        "PIC-5001": 8.5,
        "PDIC-5001": 340.0,
        "PDIC-6001": 330.0,
        "AIC-7002": 400.0,
        "PIC-7002": 37.5,
        "FIC-5003": float(snapshot["FT-5006"]) * 1.35,
        "FIC-6003": float(snapshot["FT-6006"]) * 1.35,
    }
    for _path, document in _strategy_documents(project):
        for block in document.get("blocks", []):
            kind = block.get("block_type")
            if kind not in {"PID", "AO", "DO"}:
                continue
            config = block.get("config", {})
            assert config.get("mode") == "MAN", (
                document.get("name"), block.get("instance_name"), config
            )
            assert config.get("normal_mode") in {"AUTO", "CAS", "MAN"}
            if kind != "PID":
                output_count += 1
                continue
            pid_count += 1
            name = str(block.get("instance_name") or "")
            pid_configs[name] = config
            pv_tag = _startup_pv_tag(document, block, basis)
            assert pv_tag in snapshot or pv_tag in COOLING_WATER_STARTUP, (
                document.get("name"), block.get("instance_name"), pv_tag
            )
            startup_pv = float(snapshot.get(
                pv_tag, COOLING_WATER_STARTUP.get(pv_tag, math.nan)))
            if name in engineered_sp:
                assert config.get("sp_pv_track_man") is False
                assert float(config["sp_init"]) == pytest.approx(
                    engineered_sp[name], abs=1e-12
                )
            else:
                assert config.get("sp_pv_track_man") is True
                assert float(config["sp_init"]) == pytest.approx(
                    startup_pv, abs=1e-12
                )
            assert math.isfinite(float(config["out_init"]))

    assert pid_count == 80
    assert output_count == 157
    # Split-range controllers must reconstruct the lined-up physical legs;
    # the wrapper's former default 50% would open PIC-3001 and close the
    # active AIC-8001 caustic leg on first transfer.
    assert float(pid_configs["PIC-3001"]["out_init"]) == pytest.approx(0.0)
    assert float(pid_configs["AIC-8001"]["out_init"]) == pytest.approx(80.0)
    assert float(pid_configs["PIC-5001"]["out_init"]) == pytest.approx(50.0)
    topology_doc = (PROJECT / "CONTROL_TOPOLOGY.md").read_text(
        encoding="utf-8"
    ).lower()
    assert "pic-5001" in topology_doc
    assert "non-bumpless commissioning deviation" in topology_doc
    assert "pcv-5001=100%" in topology_doc


def _stop_exchange_thread(driver: LocalVirtualIoDriver) -> None:
    """Leave the configured provider alive but make exchange deterministic."""
    driver._stop.set()
    thread = driver._thread
    if thread is not None:
        thread.join(timeout=2.0)
        assert not thread.is_alive()
    driver._thread = None


def _is_bad(sample) -> bool:
    quality = str(getattr(sample, "quality", "GOOD")).upper()
    return bool(getattr(sample, "stale", False)) or "BAD" in quality


def test_all_modules_hold_lined_up_plant_for_thirty_simulated_seconds() -> None:
    """Boot the real project/provider and prove multi-scan no-bump behavior."""
    project = _project_document()
    area = project["areas"][0]
    config = copy.deepcopy(area["virtual_io"])
    ownership = config["output_ownership"]["routes"]
    held_outputs = {
        tag for tag, record in ownership.items()
        if record["owner"] in {"strategy", "reserved_inactive"}
    }
    simulator_sis_outputs = {
        tag for tag, record in ownership.items()
        if record["owner"] == "simulator_sis"
    }
    # The production configuration is real-time.  Qualification advances the
    # same provider deterministically so 30 simulated seconds costs no sleep.
    config["provider"]["options"]["autorun"] = False

    store = SharedDataStore()
    driver = LocalVirtualIoDriver(store, config, PROJECT)
    prior_disable = logging.root.manager.disable
    # Scan-level PID DEBUG is useful interactively but would make a full-area
    # qualification spend most of its time formatting captured diagnostics.
    logging.disable(logging.WARNING)
    if not driver.start():
        logging.disable(prior_disable)
        pytest.skip(f"configured embedded provider is unavailable: {driver.last_error}")
    runtimes: list[StrategyRuntime] = []
    try:
        _stop_exchange_thread(driver)
        product = driver._session.product
        step = getattr(product, "step", None)
        if not callable(step):
            pytest.skip("configured embedded provider has no deterministic step API")

        initial_outputs = {
            route.store_tag: driver._read(route.signal, time.time()).value
            for route in driver.write_routes.values()
        }
        context = RuntimeContext(store=store, plugin_id=project["project_id"])
        due_every: list[tuple[int, StrategyRuntime]] = []
        for relative in [*area["strategies"], *area.get("sfc_modules", [])]:
            graph, _comments = load_strategy(PROJECT / relative)
            runtime = StrategyRuntime()
            runtime.load(compile_strategy(graph), DataBridge(store))
            runtime.set_context(context)
            assert runtime.go_online(), relative
            runtimes.append(runtime)
            period_ticks = max(1, round(graph.scan_ms / 100.0))
            due_every.append((period_ticks, runtime))

        assert len(runtimes) == 160
        max_analog_bump = 0.0
        changed_held_discretes: set[str] = set()
        independently_owned_changes: set[str] = set()
        # Project timing declares a 100 ms integration/exchange cadence.
        for tick in range(300):
            driver.scan_once()
            for period_ticks, runtime in due_every:
                if tick % period_ticks == 0:
                    runtime.execute_scan(period_ticks * 0.1)
            driver.scan_once()

            for route in driver.write_routes.values():
                before = initial_outputs[route.store_tag]
                after = driver._read(route.signal, time.time()).value
                if isinstance(before, bool):
                    changed = bool(after) != before
                    if changed and route.store_tag in held_outputs:
                        changed_held_discretes.add(route.store_tag)
                    elif changed:
                        independently_owned_changes.add(route.store_tag)
                elif route.store_tag in held_outputs:
                    max_analog_bump = max(
                        max_analog_bump, abs(float(after) - float(before))
                    )
                elif abs(float(after) - float(before)) > 1e-9:
                    independently_owned_changes.add(route.store_tag)
            step(1)

        bad_inputs = []
        non_finite = []
        samples = store.get_samples()
        for route in driver.read_routes:
            sample = samples.get(route.store_tag)
            if sample is None or _is_bad(sample):
                bad_inputs.append(route.store_tag)
                continue
            value = sample.value
            if not isinstance(value, bool) and not math.isfinite(float(value)):
                non_finite.append(route.store_tag)

        assert not changed_held_discretes and max_analog_bump <= 1e-9, {
            "changed_held_discretes": sorted(changed_held_discretes),
            "max_analog_bump": max_analog_bump,
        }
        assert independently_owned_changes <= simulator_sis_outputs, {
            "independently_owned_changes": sorted(independently_owned_changes),
            "declared_simulator_sis_outputs": sorted(simulator_sis_outputs),
        }
        assert not non_finite
        assert not bad_inputs
        assert sum(runtime.get_status()["error_count"] for runtime in runtimes) == 0
        assert not product.engine.stats.frozen_by_error
        assert product.engine._consecutive_errors == 0
    finally:
        for runtime in runtimes:
            runtime.go_offline()
        driver.stop()
        logging.disable(prior_disable)
