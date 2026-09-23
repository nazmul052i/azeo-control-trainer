from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from azeo_control_trainer.core.datastore.shared_data_store import SharedDataStore
from azeo_control_trainer.core.strategy.blocks.dv_analog2_blocks import (
    LIM_CONST,
    LIM_HIGH,
    LIM_LOW,
    ST_BAD,
    ST_INIT_REQ,
    ST_NOT_INVITED,
    ST_NOT_SELECTED,
)
from azeo_control_trainer.core.strategy.blocks.dv_enhanced_analog_blocks import (
    ST_INIT_ACK,
    EnhancedControlSelectorBlock,
    EnhancedRampBlock,
)
from azeo_control_trainer.core.strategy.engine.bridge import DataBridge
from azeo_control_trainer.core.strategy.engine.compiler import CompiledStrategy
from azeo_control_trainer.core.strategy.engine.runtime import StrategyRuntime
from azeo_control_trainer.core.strategy.model.block_base import (
    BlockCategory,
    BlockStatus,
    DataType,
    FunctionBlock,
)
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
from azeo_control_trainer.core.strategy.model.terminal import LimitStatus, Quality


class _RampTarget(FunctionBlock):
    """Small same-module target exposing all ERAMP reference shapes."""

    block_type = "TEST_RAMP_TARGET"
    category = BlockCategory.CONTROL

    def _define_terminals(self) -> None:
        self.add_input("SP", default=0.0)
        self.add_input("TRK_VAL", default=0.0)
        self.add_input("RCAS_IN", default=0.0)
        self.add_input("ROUT_IN", default=0.0)
        self.add_output("OUT", default=0.0)
        self.add_output("MODE", DataType.STRING, "AUTO")


def _selector(values: tuple[float, ...] = (30.0, 10.0, 20.0)):
    block = EnhancedControlSelectorBlock("ECTLSL1")
    block.config.params.update(
        {
            "NOF_TOTAL_SEL": len(values),
            "NOF_USED_SEL": len(values),
        }
    )
    for index, value in enumerate(values, 1):
        block.inputs[f"SEL_{index}"].value = value
    block._apply_config()
    return block


def _ramp_target(*, mode: str = "AUTO", value: float = 0.0):
    graph = StrategyGraph("ERAMP-TEST")
    target = _RampTarget("PID1")
    target.outputs["MODE"].value = mode
    target.inputs["SP"].value = value
    target.outputs["OUT"].value = value
    ramp = EnhancedRampBlock("ERAMP1")
    ramp.config.params.update(
        {
            "REF_MODE": "PID1/MODE",
            "REF_RAMP_AUTO": "PID1/SP",
            "REF_RAMP_MAN": "PID1/OUT",
            "ERAMP_RATE": 10.0,
            "TIME_UNIT": "SECONDS",
        }
    )
    graph.add_block(target)
    graph.add_block(ramp)
    ramp._module_graph = graph
    return graph, target, ramp


def test_ectlsl_has_stable_sixteen_path_schema_and_round_trips():
    block = EnhancedControlSelectorBlock("ECTLSL16")

    assert {f"SEL_{index}" for index in range(1, 17)} <= block.inputs.keys()
    assert {f"BKCAL_SEL{index}" for index in range(1, 17)} <= block.outputs.keys()
    assert {"OP_SELECTION", "BKCAL_IN", "BKCAL_IN_ST", "BKCAL_IN_LIM"} <= (
        block.inputs.keys()
    )
    assert {
        "OUT",
        "SELECTED",
        "MODE_ACT",
        "BLOCK_ERR",
        "BAD_ACTIVE",
        "ABNORM_ACTIVE",
    } <= block.outputs.keys()
    assert block.inputs["SEL_16"].hidden is True

    block.config.params.update(
        {
            "NOF_TOTAL_SEL": 16,
            "NOF_USED_SEL": 16,
            "SHOW_STATUS_PINS": True,
            "OP_SELECTION": 16,
        }
    )
    block._apply_config()
    assert block.inputs["SEL_16"].hidden is False
    assert block.inputs["SEL_16_ST"].hidden is False
    assert block.outputs["BKCAL_SEL16_LIM"].hidden is False

    restored = EnhancedControlSelectorBlock.from_dict(block.to_dict())
    assert restored.config.params == block.config.params
    assert restored.inputs["SEL_16"].hidden is False
    assert restored.outputs["BKCAL_SEL16_ST"].hidden is False


def test_ectlsl_auto_algorithms_and_direct_selection_back_calculation():
    block = _selector()

    # The product-contract default is High.
    block.execute(0.1)
    assert block.get_output("OUT") == 30.0
    assert block.get_output("SELECTED") == 1
    assert block.get_output("BKCAL_SEL2_ST") == ST_NOT_SELECTED
    assert block.get_output("BKCAL_SEL2_LIM") == LIM_LOW

    block.config.params["SEL_TYPE"] = "LOW"
    block.execute(0.1)
    assert block.get_output("OUT") == 10.0
    assert block.get_output("SELECTED") == 2
    assert block.get_output("BKCAL_SEL1_LIM") == LIM_HIGH

    block.config.params["SEL_TYPE"] = "MIDDLE"
    block.execute(0.1)
    assert block.get_output("OUT") == 20.0
    assert block.get_output("SELECTED") == 3
    assert block.get_output("BKCAL_SEL2_LIM") == LIM_LOW
    assert block.get_output("BKCAL_SEL1_LIM") == LIM_HIGH

    block.config.params["OP_SELECTION"] = 2
    block.execute(0.1)
    assert block.get_output("OUT") == 10.0
    assert block.get_output("SELECTED") == 2
    assert block.get_output("BKCAL_SEL1_ST") == ST_NOT_INVITED
    assert block.get_output("BKCAL_SEL3_ST") == ST_NOT_INVITED

    # A wired OP_SELECTION overrides the configured named-set value.
    block.inputs["OP_SELECTION"].connected = True
    block.inputs["OP_SELECTION"].value = 1
    block.execute(0.1)
    assert block.get_output("SELECTED") == 1


def test_ectlsl_manual_initializing_manual_oos_shed_and_reset():
    block = _selector()
    block.config.params.update({"MODE": "MAN", "OUT_MAN": 42.0})
    block.execute(0.1)
    assert block.get_output("OUT") == 42.0
    assert block.get_output("SELECTED") == 0
    assert block.get_output("MODE_ACT") == "MAN"
    assert block.get_output("OUT_LIM") == LIM_CONST
    assert block.get_output("BKCAL_SEL1_ST") == ST_NOT_INVITED

    block.config.params["MODE"] = "AUTO"
    block.inputs["BKCAL_IN"].value = 17.0
    block.inputs["BKCAL_IN_ST"].value = ST_INIT_REQ
    block.inputs["BKCAL_IN_LIM"].value = LIM_HIGH
    block.execute(0.1)
    assert block.get_output("MODE_ACT") == "IMAN"
    assert block.get_output("OUT") == 17.0
    assert block.get_output("OUT_ST") == ST_INIT_ACK
    assert block.get_output("BKCAL_SEL3") == 17.0
    assert block.get_output("BKCAL_SEL3_ST") == ST_INIT_REQ

    block.inputs["BKCAL_IN_ST"].value = 0
    block.config.params["MODE"] = "OOS"
    block.config.params["BAD_MASK_OOS"] = False
    block.execute(0.1)
    assert block.status is BlockStatus.OOS
    assert block.outputs["OUT"].status is Quality.BAD
    assert block.outputs["SELECTED"].status is Quality.BAD
    assert block.get_output("BLOCK_ERR") == 1
    assert block.get_output("BAD_ACTIVE") is False
    assert block.get_output("ABNORM_ACTIVE") is True

    block.config.params.update(
        {
            "MODE": "AUTO",
            "OPT_SHED_TO_MAN_ON_BAD": True,
        }
    )
    block.inputs["SEL_2"].connected = True
    block.inputs["SEL_2_ST"].value = ST_BAD
    held = float(block.get_output("OUT"))
    block.execute(0.1)
    assert block.get_output("MODE_ACT") == "MAN"
    assert block.get_output("OUT") == held

    block.reset()
    assert block.get_output("OUT") == 0.0
    assert block.get_output("SELECTED") == 0
    assert block.get_output("MODE_ACT") == "AUTO"
    assert block.outputs["OUT"].status is Quality.GOOD


def test_ectlsl_output_limits_selected_limit_and_downstream_limit_feedback():
    block = _selector((150.0, 50.0))
    block.config.params.update(
        {
            "SEL_TYPE": "HIGH",
            "OUT_SCALE_LO": 0.0,
            "OUT_SCALE_HI": 100.0,
            "OUT_LO_LIM": -999.0,
            "OUT_HI_LIM": 999.0,
        }
    )
    block.execute(0.1)
    assert block.get_output("OUT") == 110.0
    assert block.get_output("OUT_LIM") == LIM_HIGH
    assert block.outputs["OUT"].limit is LimitStatus.HIGH_LIMITED
    # A limited OUT feeds the selected input's unclamped value upstream.
    assert block.get_output("BKCAL_SEL1") == 150.0
    assert block.get_output("BKCAL_SEL1_LIM") == LIM_HIGH

    block.inputs["SEL_1"].value = 20.0
    block.inputs["SEL_2"].value = 10.0
    block.inputs["SEL_1"].limit = LimitStatus.LOW_LIMITED
    block.execute(0.1)
    assert block.get_output("OUT") == 20.0
    assert block.get_output("OUT_LIM") == LIM_LOW

    block.inputs["SEL_1"].limit = LimitStatus.NOT_LIMITED
    block.inputs["BKCAL_IN"].value = 12.0
    block.inputs["BKCAL_IN"].limit = LimitStatus.HIGH_LIMITED
    block.execute(0.1)
    assert block.get_output("OUT") == 20.0
    assert block.get_output("BKCAL_SEL1") == 12.0
    assert block.get_output("BKCAL_SEL1_LIM") == LIM_HIGH


def test_eramp_rate_execution_writes_reference_completes_and_resets():
    _graph, target, ramp = _ramp_target(value=10.0)
    ramp.inputs["ERAMP_END_VALUE"].value = 40.0
    ramp.inputs["ERAMP_ENABLE"].value = True

    ramp.execute(1.0)
    assert ramp.get_output("ERAMP_STATE") == "RAMPING"
    assert ramp.get_output("ERAMP_ACTIVE") is True
    assert ramp.get_output("OUT") == 20.0
    assert ramp.get_output("TIME_REMAIN") == 2.0
    assert target.inputs["SP"].value == 20.0
    assert ramp.get_output("REF_RAMP_PATH") == "PID1/SP"

    ramp.execute(2.0)
    assert ramp.get_output("ERAMP_STATE") == "COMPLETE"
    assert ramp.get_output("COMPLETE") is True
    assert ramp.get_output("ERAMP_ACTIVE") is False
    assert ramp.get_output("TIME_REMAIN") == 0.0
    assert target.inputs["SP"].value == 40.0
    # The block clears an unwired enable after completion.
    assert ramp.get_input("ERAMP_ENABLE") is False

    ramp.reset()
    assert ramp.get_output("OUT") == 0.0
    assert ramp.get_output("ERAMP_STATE") == "STOPPED"
    assert ramp.get_output("COMPLETE") is False
    assert target.inputs["SP"].value == 40.0


def test_eramp_time_pause_resume_mode_gating_and_mode_specific_path():
    _graph, target, ramp = _ramp_target(mode="MAN", value=0.0)
    ramp.config.params.update(
        {
            "ERAMP_IN_MODE": "MAN",
            "ERAMP_TYPE": "USE_TIME",
            "ERAMP_TIME": 4.0,
            "OPT_PAUSE_IF_MODE_CHANGES": True,
        }
    )
    ramp.inputs["ERAMP_END_VALUE"].value = 100.0
    ramp.inputs["ERAMP_ENABLE"].connected = True
    ramp.inputs["ERAMP_ENABLE"].value = True

    ramp.execute(1.0)
    assert ramp.get_output("OUT") == 25.0
    assert target.outputs["OUT"].value == 25.0
    assert ramp.get_output("REF_RAMP_PATH") == "PID1/OUT"

    ramp.inputs["PAUSE"].value = True
    ramp.execute(1.0)
    assert ramp.get_output("ERAMP_STATE") == "PAUSED"
    assert ramp.get_output("OUT") == 25.0
    assert ramp.get_output("TIME_REMAIN") == 3.0

    ramp.inputs["PAUSE"].value = False
    ramp.execute(1.0)
    assert ramp.get_output("ERAMP_STATE") == "RAMPING"
    assert ramp.get_output("OUT") == 50.0

    target.outputs["MODE"].value = "AUTO"
    ramp.execute(1.0)
    assert ramp.get_output("ERAMP_STATE") == "PAUSED"
    assert ramp.get_output("OUT") == 50.0

    target.outputs["MODE"].value = "MAN"
    ramp.execute(1.0)
    assert ramp.get_output("ERAMP_STATE") == "PAUSED"
    ramp.inputs["RESUME"].value = True
    ramp.execute(1.0)
    assert ramp.get_output("ERAMP_STATE") == "RAMPING"
    assert ramp.get_output("OUT") == 75.0


def test_eramp_recalculates_on_rate_change_obeys_limits_and_propagates_status():
    _graph, target, ramp = _ramp_target(value=0.0)
    ramp.inputs["ERAMP_END_VALUE"].value = 100.0
    ramp.inputs["ERAMP_ENABLE"].connected = True
    ramp.inputs["ERAMP_ENABLE"].value = True
    ramp.execute(1.0)
    assert ramp.get_output("OUT") == 10.0
    assert ramp.get_output("TIME_REMAIN") == 9.0

    ramp.config.params["ERAMP_RATE"] = 20.0
    ramp.execute(1.0)
    assert ramp.get_output("OUT") == 30.0
    assert ramp.get_output("TIME_REMAIN") == 3.5

    ramp.reset()
    target.inputs["SP"].value = 0.0
    ramp.config.params.update(
        {
            "OPT_OBEY_LIMITS": True,
            "LO_LIM": 0.0,
            "HI_LIM": 80.0,
        }
    )
    ramp.inputs["ERAMP_END_VALUE"].value = 120.0
    ramp.inputs["ERAMP_ENABLE"].value = True
    ramp.execute(1.0)
    assert ramp.get_output("OUT") == 80.0
    assert ramp.get_output("ERAMP_STATE") == "COMPLETE"
    assert target.inputs["SP"].value == 80.0

    ramp.inputs["IN"].status = Quality.BAD
    ramp.execute(0.1)
    assert ramp.outputs["OUT"].status is Quality.BAD
    assert ramp.outputs["ERAMP_STATE"].status is Quality.BAD
    assert ramp.status is BlockStatus.BAD


def test_eramp_missing_reference_is_bad_and_schema_round_trip_is_primitive():
    ramp = EnhancedRampBlock("ERAMP1")
    ramp.config.params.update(
        {
            "REF_MODE": "MISSING/MODE",
            "REF_RAMP_AUTO": "MISSING/SP",
            "ERAMP_TYPE": "USE_TIME",
            "ERAMP_TIME": 15.0,
        }
    )
    ramp.inputs["ERAMP_ENABLE"].value = True
    ramp.execute(1.0)
    assert ramp.status is BlockStatus.BAD
    assert ramp.get_output("ERAMP_STATE") == "STOPPED"
    assert ramp.outputs["OUT"].status is Quality.BAD

    restored = EnhancedRampBlock.from_dict(ramp.to_dict())
    assert restored.config.params == ramp.config.params
    assert restored.get_output("ERAMP_STATE") == "STOPPED"
    assert {
        "OUT",
        "COMPLETE",
        "ERAMP_ACTIVE",
        "ERAMP_STATE",
        "TIME_REMAIN",
        "REF_RAMP_VAL",
        "REF_RAMP_PATH",
    } <= restored.outputs.keys()


def test_ectlsl_terminals_remain_visible_to_online_block_debugger():
    graph = StrategyGraph("ECTLSL-DEBUG")
    block = _selector((1.0, 2.0))
    graph.add_block(block)
    compiled = CompiledStrategy(graph, [block.id], [], [])
    runtime = StrategyRuntime()
    runtime.load(compiled, DataBridge(SharedDataStore()))
    assert runtime.go_online()
    assert runtime.debug_pause()
    # Going online resets runtime state; seed the live terminal values after
    # that boundary just as a wire/input sampling phase would.
    block.inputs["SEL_1"].value = 1.0
    block.inputs["SEL_2"].value = 2.0

    assert runtime.debug_step_block(0.1) == block.id
    status = runtime.get_debug_status()
    assert status["current_block"]["id"] == block.id
    assert block.get_output("SELECTED") == 2
    assert block.get_output("OUT") == 2.0
    # Both value and cascade metadata are normal terminals in the live graph.
    assert block.outputs["BKCAL_SEL2"].status is Quality.GOOD
    assert block.outputs["BKCAL_SEL2_LIM"].data_type is DataType.INT
