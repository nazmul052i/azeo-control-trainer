"""Deterministic qualification of the assembled virtual-controller project.

This is intentionally a system test, not another unit test of AO or PID.
It loads the checked-in engineering project, resolves its configured provider,
brings every assigned module online against one SharedDataStore, and advances
the exact project-declared read/scan/write/plant sequence without wall-clock
timers.  The provider checkout is optional for ordinary trainer development;
when it is present this test is the executable LocalAdapter acceptance check.
"""
from __future__ import annotations

import copy
import json
import math
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import azeo_control_trainer.core.strategy.blocks  # noqa: E402,F401
from azeo_control_trainer.app import _field_io_config  # noqa: E402
from azeo_control_trainer.connectivity.fieldio.local_virtual_io import (  # noqa: E402
    LocalVirtualIoDriver,
)
from azeo_control_trainer.core.datastore.shared_data_store import (  # noqa: E402
    SharedDataStore,
)
from azeo_control_trainer.core.strategy.engine.bridge import DataBridge  # noqa: E402
from azeo_control_trainer.core.strategy.engine.compiler import (  # noqa: E402
    compile_strategy,
)
from azeo_control_trainer.core.strategy.engine.runtime import StrategyRuntime  # noqa: E402
from azeo_control_trainer.core.strategy.engine.runtime_context import (  # noqa: E402
    RuntimeContext,
)
from azeo_control_trainer.core.strategy.engine.validator import (  # noqa: E402
    validate_strategy,
)
from azeo_control_trainer.core.strategy.serialization.strategy_io import (  # noqa: E402
    load_strategy,
)


PROJECT = ROOT / "projects" / "AzeoPlantVirtualController"


def _provider_is_available(config: dict) -> bool:
    provider = config.get("provider") or {}
    for raw in provider.get("search_paths") or ():
        expanded = str(raw).replace("${PROJECT_DIR}", str(PROJECT))
        if "${" in expanded or "%" in expanded:
            continue
        path = Path(expanded)
        candidate = path if path.is_absolute() else PROJECT / path
        if candidate.resolve().is_dir():
            return True
    return False


def _deterministic_config() -> dict:
    """Use the real config while disabling only its wall-clock executives."""
    config = copy.deepcopy(_field_io_config(PROJECT))
    if not _provider_is_available(config):
        pytest.skip("configured AzeoPlantSimulator provider is not present")
    options = config["provider"]["options"]
    options["autorun"] = False

    # LocalVirtualIoDriver owns a production polling thread.  A long, matching
    # exchange period keeps that thread asleep while this test explicitly
    # performs each exchange.  stop() sets its Event and wakes it immediately,
    # so this does not lengthen teardown.
    config["period_ms"] = 60_000
    config["timing"]["exchange_period_ms"] = 60_000
    return config


def _load_assigned_runtimes(store: SharedDataStore):
    document = json.loads(
        (PROJECT / "_project.json").read_text(encoding="utf-8")
    )
    area = document["areas"][0]
    declared = list(area["strategies"]) + list(area["sfc_modules"])
    assignments = document["assignments"]
    assert set(assignments) == set(declared)

    runtimes = []
    output_blocks = {}
    for relative in assignments:
        graph, _comments = load_strategy(PROJECT / relative)
        errors = [finding for finding in validate_strategy(graph)
                  if finding.severity == "ERROR"]
        assert not errors, (relative, errors)
        runtime = StrategyRuntime()
        runtime.set_context(RuntimeContext(
            store=store,
            plugin_id="AzeoPlantVirtualController",
        ))
        runtime.load(compile_strategy(graph), DataBridge(store))
        runtimes.append(runtime)
        for block in graph.blocks.values():
            if block.block_type not in {"AO", "DO"}:
                continue
            tag = str(block.config.params.get("tag") or "")
            assert tag and tag not in output_blocks, (relative, tag)
            output_blocks[tag] = block
    return runtimes, output_blocks, assignments


def _field_value(block):
    if block.block_type == "AO":
        return float(block.get_output("OUT"))
    return bool(block.get_output("OUT_D"))


def test_real_virtual_controller_is_bumpless_across_deterministic_startup(
        ) -> None:
    """Qualify the full 160-module controller over ten 100 ms plant steps."""
    config = _deterministic_config()
    store = SharedDataStore()
    runtimes, output_blocks, assignments = _load_assigned_runtimes(store)
    driver = LocalVirtualIoDriver(store, config, PROJECT)
    store.field_io_driver = driver
    assert driver.start(), driver.status()
    product = driver._session.product

    try:
        assert len(runtimes) == len(assignments) == 160
        assert driver.outputs_seeded == len(driver.write_routes) == 173
        assert set(output_blocks) <= set(driver.write_routes)

        seeded = {tag: store.get(tag) for tag in driver.write_routes}
        assert all(value is not None for value in seeded.values())

        for runtime in runtimes:
            assert runtime.go_online()

        # Going online initializes each field block from its actual readback;
        # no scan or queued write is needed to make this statement true.
        for tag, block in output_blocks.items():
            expected = seeded[tag]
            actual = _field_value(block)
            if block.block_type == "AO":
                assert actual == pytest.approx(float(expected), abs=1e-9), tag
            else:
                assert actual is bool(expected), tag
        assert not store.drain_writes()

        base_step_s = float(config["dt"])
        first_cycle = None
        analog_step_peak = 0.0
        previous = dict(seeded)
        for cycle in range(10):
            # Project contract: read inputs, scan due modules, write outputs,
            # then step the plant.  Period arithmetic is integer milliseconds
            # so this result cannot depend on scheduler jitter.
            driver.scan_once()
            elapsed_ms = cycle * int(round(base_step_s * 1000.0))
            for runtime in runtimes:
                period_ms = runtime.compiled.graph.scan_ms
                if elapsed_ms % period_ms == 0:
                    runtime.execute_scan(period_ms / 1000.0)
            driver.scan_once()

            current = {tag: store.get(tag) for tag in driver.write_routes}
            if first_cycle is None:
                first_cycle = dict(current)
            for tag, value in current.items():
                if isinstance(value, bool):
                    assert isinstance(previous[tag], bool), tag
                else:
                    number = float(value)
                    assert math.isfinite(number), tag
                    analog_step_peak = max(
                        analog_step_peak,
                        abs(number - float(previous[tag])),
                    )
            previous = current
            product.step(1)

        # The first scheduled scan is the transfer boundary.  Every controlled
        # AO/DO must still equal the live readback it adopted at go-online.
        assert first_cycle is not None
        startup_changes = {}
        for tag in output_blocks:
            value = first_cycle[tag]
            expected = seeded[tag]
            if isinstance(expected, bool):
                if value is not bool(expected):
                    startup_changes[tag] = (expected, value)
            elif not math.isclose(
                    float(value), float(expected), abs_tol=1e-9,
                    rel_tol=0.0):
                startup_changes[tag] = (expected, value)
        assert not startup_changes, startup_changes

        # Ten 100 ms ticks include t=0 through t=900 ms.  The CT1 thermal
        # performance monitor intentionally scans at 1 s, while regulation
        # scans at 200 ms; qualify each module against its own schedule.
        assert all(
            runtime.scan_count >= 1 + 900 // runtime.compiled.graph.scan_ms
            for runtime in runtimes
        )
        assert product.engine.stats.heartbeat == 10
        assert product.engine.stats.errors == 0
        assert driver.write_failures == 0
        assert driver.read_failures == 0
        # Reported in a failure so a future tightening has concrete evidence,
        # while finiteness and startup equality remain the safety contract.
        assert math.isfinite(analog_step_peak), analog_step_peak
    finally:
        for runtime in runtimes:
            runtime.go_offline()
        driver.stop()
