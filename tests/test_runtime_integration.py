"""End-to-end acceptance for blocks in the 97-entry primary catalog."""
from __future__ import annotations

from azeo_control_trainer.core.datastore.shared_data_store import SharedDataStore
from azeo_control_trainer.core.strategy import blocks as _blocks  # noqa: F401
from azeo_control_trainer.core.strategy.engine.bridge import DataBridge
from azeo_control_trainer.core.strategy.engine.compiler import compile_strategy
from azeo_control_trainer.core.strategy.engine.runtime import StrategyRuntime
from azeo_control_trainer.core.strategy.model.block_registry import registry
from azeo_control_trainer.core.strategy.model.block_base import BlockStatus
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
from azeo_control_trainer.core.strategy.model.terminal import Quality
from azeo_control_trainer.core.strategy.serialization.strategy_io import (
    load_strategy,
    save_strategy,
)


def _new(block_type: str, name: str):
    block = registry.create(block_type, name)
    assert block is not None
    return block


def _by_name(graph: StrategyGraph, name: str):
    return next(
        block for block in graph.blocks.values() if block.instance_name == name
    )


def test_primary_blocks_round_trip_scan_force_and_debug_as_one_module(tmp_path) -> None:
    graph = StrategyGraph("PRIMARY-NATIVE-RUNTIME")

    pid = _new("PID", "PID1")
    alarm = _new("ALARM", "ALARM1")
    alarm.config.params.update({"HI_LIM": 80.0, "HI_HI_LIM": 95.0})
    assert alarm.force_terminal("PV", 85.0)

    alarm_det = _new("ALARM_DET", "ALARM_DET1")
    alarm_det.config.params.update({"HI_LIM": 80.0, "DEFAULT": 0.0})
    assert alarm_det.force_terminal("IN", 85.0)

    tag_blocks = []
    for block_type, name, tag in (
        ("TAGAI", "TAGAI1", "PLC.AI"),
        ("TAGAO", "TAGAO1", "PLC.AO"),
        ("TAGDI", "TAGDI1", "PLC.DI"),
        ("TAGDO", "TAGDO1", "PLC.DO"),
    ):
        block = _new(block_type, name)
        block.config.params["tag"] = tag
        block._apply_config()
        tag_blocks.append(block)

    selector = _new("ECTLSL", "ECTLSL1")
    selector.config.params.update({"NOF_TOTAL_SEL": 2, "NOF_USED_SEL": 2})
    selector._apply_config()
    assert selector.force_terminal("SEL_1", 33.0)
    selector.inputs["SEL_2"].value = 12.0

    ramp = _new("ERAMP", "ERAMP1")
    ramp.config.params.update({
        "REF_MODE": "PID1/MODE",
        "REF_RAMP_AUTO": "PID1/SP",
        "ERAMP_IN_MODE": "AUTO",
        "ERAMP_RATE": 10.0,
        "TIME_UNIT": "SECONDS",
    })
    ramp.inputs["ERAMP_END_VALUE"].value = 100.0
    assert ramp.force_terminal("ERAMP_ENABLE", True)

    for block in (pid, alarm, alarm_det, *tag_blocks, selector, ramp):
        graph.add_block(block)

    path = tmp_path / "primary_native_runtime.json"
    save_strategy(graph, path=path)
    restored, comments = load_strategy(path, remember=False)
    assert comments == []
    assert [block.block_type for block in restored.blocks.values()] == [
        "PID", "ALARM", "ALARM_DET", "TAGAI", "TAGAO", "TAGDI",
        "TAGDO", "ECTLSL", "ERAMP",
    ]
    assert _by_name(restored, "ALARM1").forced_terminals() == ["PV"]
    assert _by_name(restored, "ALARM_DET1").forced_terminals() == ["IN"]
    assert _by_name(restored, "ECTLSL1").forced_terminals() == ["SEL_1"]
    assert _by_name(restored, "ERAMP1").forced_terminals() == [
        "ERAMP_ENABLE",
    ]

    store = SharedDataStore()
    store.set_many({
        "PLC.AI.Val": 42.5,
        "PLC.AO.Val_CVOut": 61.0,
        "PLC.DI.Sts": True,
        "PLC.DO.Out": False,
    })
    compiled = compile_strategy(restored)
    runtime = StrategyRuntime()
    runtime.load(compiled, DataBridge(store))
    assert runtime.go_online()

    ramp_restored = _by_name(restored, "ERAMP1")
    # Ordinary unconnected input values are commissioning state, not build
    # configuration, so save/load correctly resets this one to its default.
    ramp_restored.inputs["ERAMP_END_VALUE"].value = 100.0
    assert runtime.set_breakpoint(ramp_restored.id) == ramp_restored.id
    # Every preceding block executes; the runtime then stops exactly before
    # ERAMP rather than losing the breakpoint because the type is new.
    assert runtime.execute_scan(1.0) is False
    status = runtime.get_debug_status()
    assert status["pause_reason"] == "breakpoint"
    assert status["next_block"]["type"] == "ERAMP"
    ramp_target_before = float(_by_name(restored, "PID1").inputs["SP"].value)
    assert runtime.debug_step_block() == ramp_restored.id
    assert runtime.scan_count == 1
    assert runtime.get_status()["error_count"] == 0

    assert _by_name(restored, "ALARM1").get_output("HI") is True
    assert _by_name(restored, "ALARM_DET1").get_output("HI_ACT") is True
    assert _by_name(restored, "ECTLSL1").get_output("OUT") == 33.0
    assert _by_name(restored, "TAGAI1").get_output("OUT") == 42.5
    assert _by_name(restored, "TAGAO1").get_output("OUT") == 61.0
    assert _by_name(restored, "TAGDI1").get_output("OUT_D") is True
    assert _by_name(restored, "TAGDO1").get_output("OUT_D") is False
    # The breakpoint step is not just type-dispatch coverage: ERAMP must
    # actively advance the configured target and write it through the
    # same-module reference during the retained one-second debug scan.
    assert ramp_restored.get_output("ERAMP_STATE") == "RAMPING"
    assert ramp_restored.get_output("ERAMP_ACTIVE") is True
    assert ramp_restored.get_output("OUT") == ramp_target_before + 10.0
    assert ramp_restored.get_output("REF_RAMP_VAL") == ramp_target_before + 10.0
    assert (
        _by_name(restored, "PID1").inputs["SP"].value
        == ramp_target_before + 10.0
    )
    assert all(block.status is BlockStatus.GOOD for block in (
        _by_name(restored, "TAGAI1"),
        _by_name(restored, "TAGAO1"),
        _by_name(restored, "TAGDI1"),
        _by_name(restored, "TAGDO1"),
    ))
    assert all(
        block.outputs[block.primary_terminal].status is Quality.GOOD
        for block in (
            _by_name(restored, "TAGAI1"),
            _by_name(restored, "TAGAO1"),
            _by_name(restored, "TAGDI1"),
            _by_name(restored, "TAGDO1"),
        )
    )

    # Only block inputs are forceable. A read-only PLC monitor output cannot
    # accidentally become a field command through the debugger.
    assert _by_name(restored, "TAGAO1").force_terminal("OUT", 99.0) is False
    assert ramp_restored.release_terminal("ERAMP_ENABLE") is True
    assert ramp_restored.forced_terminals() == []
    assert _by_name(restored, "ALARM_DET1").release_terminal("IN") is True
    assert _by_name(restored, "ALARM_DET1").forced_terminals() == []
