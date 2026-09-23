#!/usr/bin/env python3
"""Focused contract for the provider-neutral in-process plant factory."""

from __future__ import annotations

import sys
import tempfile
from dataclasses import FrozenInstanceError
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from azeoplant.embedding import (  # noqa: E402
    EmbeddedPlantConfig,
    create_embedded_plant,
)
from azeoplant.core.tags import Quality  # noqa: E402
from azeoplant.io.adapters import LocalAdapter  # noqa: E402


def test_config_and_factory() -> None:
    with tempfile.TemporaryDirectory() as directory:
        config_path = Path(directory) / "provider.yaml"
        config_path.write_text(
            "\n".join((
                "embedding:",
                f'  model_root: "{ROOT.as_posix()}"',
                '  source: "TEST-VCTRL-1"',
                "  dt: 0.1",
                '  snapshot: "snapshots/lined_up.json"',
                '  catalog: "data/opcua_tag_catalog.json"',
                "  stale_timeout_s: 3.0",
                "  speed_factor: 1.25",
                "  autorun: false",
            )), encoding="utf-8")
        config = EmbeddedPlantConfig.from_file(config_path)

    runtime = create_embedded_plant(config)
    # The cooling-water open-loop model adds 54 process and command signals.
    # Keep this count tied to the matching catalog and lined-up snapshot so a
    # partial simulator sync fails at the provider boundary.
    assert len(runtime.db.all()) == 612
    assert len(runtime.catalog) == 612
    assert runtime.engine.dt == 0.1
    assert runtime.flowsheet.dt == 0.1
    assert runtime.engine.speed_factor == 1.25
    assert runtime.engine.stats.sim_time > 1000.0
    assert not runtime.controller.enabled
    assert runtime.bus.holder is None
    assert runtime.controller.step in runtime.engine.post_step_hooks

    try:
        runtime.write("FCV-1001", 41.5)
        raise AssertionError("write before start was accepted")
    except RuntimeError:
        pass
    runtime.start()
    runtime.start()
    assert runtime.started
    assert runtime.bus.holder == "TEST-VCTRL-1"
    assert runtime.health().connected
    assert runtime.health().detail["tag_count"] == 612
    assert runtime.health().detail["bpcs_enabled"] is False
    assert runtime.health().detail["sis_active"] is True

    other = LocalAdapter(runtime.bus, source="OTHER-VCTRL")
    try:
        other.start()
        raise AssertionError("a second holder was accepted")
    except RuntimeError:
        pass
    duplicate = LocalAdapter(runtime.bus, source="TEST-VCTRL-1")
    try:
        duplicate.start()
        raise AssertionError("a duplicate source borrowed the holder lease")
    except RuntimeError:
        pass

    sample = runtime.read_sample("FT-1001")
    assert sample.name == "FT-1001"
    assert sample.good
    assert sample.timestamp > 0.0
    try:
        sample.value = 0.0
        raise AssertionError("SignalSample is mutable")
    except FrozenInstanceError:
        pass
    assert len(runtime.read_samples(("FT-1001", "FCV-1001"))) == 2

    runtime.write("FCV-1001", 41.5)
    runtime.step()
    assert abs(float(runtime.db["FCV-1001"].value) - 41.5) < 1e-9

    output = runtime.db["FCV-1001"]
    output.quality = Quality.UNCERTAIN
    old_timestamp = output.ts
    runtime.write("FCV-1001", output.hi + 25.0)
    runtime.step()
    assert float(output.value) == output.hi
    assert output.quality is Quality.GOOD
    assert output.ts >= old_timestamp
    assert runtime.bus.stats.adjusted_writes == 1

    before = float(output.value)
    runtime.write("FCV-1001", float("nan"))
    runtime.step()
    assert float(output.value) == before
    assert runtime.bus.stats.rejected_value == 1
    assert runtime.health().rejected >= 1

    # The BPCS stays passive, but the independent safety scan still has its
    # unconditional write path through holder arbitration.
    for _ in range(11):
        with runtime.db.lock:
            runtime.db["HS-9002"].set(True)
            runtime.controller.step(0.2)
    assert bool(runtime.db["XY-9001"].value)
    assert not runtime.controller.enabled

    runtime.stop()
    runtime.stop()
    assert not runtime.started
    assert runtime.bus.holder is None
    assert not runtime.health().connected


def test_factory_call_forms_and_validation() -> None:
    options = {
        "source": "CALL-FORM",
        "dt": 0.1,
        "model_root": ROOT,
        "catalog": "data/opcua_tag_catalog.json",
        "autorun": False,
    }
    runtime = create_embedded_plant(options)
    assert runtime.config.source == "CALL-FORM"
    assert len(runtime.catalog) == 612

    runtime = create_embedded_plant(**options)
    assert runtime.config.source == "CALL-FORM"

    try:
        create_embedded_plant(source="X", dt=0.1, endpont="typo")
        raise AssertionError("unknown configuration field was accepted")
    except ValueError as exc:
        assert "endpont" in str(exc)


def main() -> int:
    test_config_and_factory()
    test_factory_call_forms_and_validation()
    print("PASS: embedded LocalAdapter provider contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
