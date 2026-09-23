"""ALARM and ALARM_DET share one standard-alarm runtime contract."""
from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from azeo_control_trainer.core.datastore.shared_data_store import (  # noqa: E402
    SharedDataStore,
)
from azeo_control_trainer.core.strategy.blocks.alarm_engine import (  # noqa: E402
    StandardAlarmEngine,
)
from azeo_control_trainer.core.strategy.blocks.dv_advanced_blocks import (  # noqa: E402
    AlarmDetectionBlock,
)
from azeo_control_trainer.core.strategy.blocks.signal_blocks import (  # noqa: E402
    AlarmBlock,
)
from azeo_control_trainer.core.strategy.engine.bridge import DataBridge  # noqa: E402
from azeo_control_trainer.core.strategy.engine.compiler import (  # noqa: E402
    compile_strategy,
)
from azeo_control_trainer.core.strategy.engine.runtime import (  # noqa: E402
    StrategyRuntime,
)
from azeo_control_trainer.core.strategy.model.strategy_graph import (  # noqa: E402
    StrategyGraph,
)
from azeo_control_trainer.core.strategy.model.terminal import Quality  # noqa: E402


_OUTPUTS = {
    "HI_HI": ("HI_HI", "HI_HI_ACT"),
    "HI": ("HI", "HI_ACT"),
    "LO": ("LO", "LO_ACT"),
    "LO_LO": ("LO_LO", "LO_LO_ACT"),
    "DV_HI": ("DEV_HI", "DV_HI_ACT"),
    "DV_LO": ("DEV_LO", "DV_LO_ACT"),
}


def _configure_equivalent_blocks() -> tuple[AlarmBlock, AlarmDetectionBlock]:
    alarm = AlarmBlock("ALM_101")
    alarm.config.params.update({
        "HI_HI_LIM": 90.0,
        "HI_LIM": 80.0,
        "LO_LIM": 20.0,
        "LO_LO_LIM": 10.0,
        "DEV_HI": 5.0,
        "DEV_LO": -5.0,
        "DV_LO_SIGN": "AZEO",
        "DEADBAND": 2.0,
        "DEV_EN": True,
    })
    alarm.inputs["PV"].connected = True
    alarm.inputs["SP"].connected = True

    alarm_det = AlarmDetectionBlock("ALMDET_101")
    alarm_det.config.params.update({
        "HI_HI_LIM": 90.0,
        "HI_LIM": 80.0,
        "LO_LIM": 20.0,
        "LO_LO_LIM": 10.0,
        "DV_HI_LIM": 5.0,
        "DV_LO_LIM": -5.0,
        "ALARM_HYS": 2.0,
    })
    alarm_det.inputs["IN"].connected = True
    alarm_det.inputs["SP"].connected = True
    return alarm, alarm_det


def _scan_pair(alarm: AlarmBlock, alarm_det: AlarmDetectionBlock,
               pv: float, sp: float = 50.0, dt: float = 0.5) -> None:
    alarm.inputs["PV"].value = pv
    alarm.inputs["SP"].value = sp
    alarm_det.inputs["IN"].value = pv
    alarm_det.inputs["SP"].value = sp
    alarm.execute(dt)
    alarm_det.execute(dt)


def _states(block: AlarmBlock | AlarmDetectionBlock,
            column: int) -> dict[str, bool]:
    return {
        name: bool(block.get_output(outputs[column]))
        for name, outputs in _OUTPUTS.items()
    }


def test_both_block_contracts_run_the_same_standard_alarm_engine() -> None:
    alarm, alarm_det = _configure_equivalent_blocks()
    assert isinstance(alarm._alarm_engine, StandardAlarmEngine)
    assert isinstance(alarm_det._alarm_engine, StandardAlarmEngine)

    for pv in (95.0, 89.0, 87.0, 50.0, 5.0, 11.0, 13.0):
        _scan_pair(alarm, alarm_det, pv)
        assert _states(alarm, 0) == _states(alarm_det, 1)

    alarm.inputs["PV"].status = Quality.BAD
    alarm_det.inputs["IN"].status = Quality.BAD
    _scan_pair(alarm, alarm_det, 50.0)
    assert alarm.outputs["PV"].status is Quality.BAD
    assert alarm_det.outputs["PV"].status is Quality.BAD

    alarm.config.params.update({
        "CONDALM_ENABLED": True,
        "HI_DELAY_ON": 0.5,
        "HI_DELAY_OFF": 0.5,
        "HI_HYS": 2.0,
    })
    alarm_det.config.params.update({
        "CONDALM_ENABLED": True,
        "HI_DELAY_ON": 0.5,
        "HI_DELAY_OFF": 0.5,
        "HI_HYS": 2.0,
    })
    alarm.reset()
    alarm_det.reset()
    for pv in (85.0, 85.0, 79.0, 77.0, 77.0):
        _scan_pair(alarm, alarm_det, pv, dt=0.25)
        assert _states(alarm, 0) == _states(alarm_det, 1)


def test_alarm_det_conditional_enable_delays_hysteresis_and_reset() -> None:
    block = AlarmDetectionBlock("ALMDET_COND")
    block.inputs["IN"].connected = True
    block.config.params.update({
        "CONDALM_ENABLED": True,
        "HI_HI_LIM": 1e9,
        "HI_LIM": 10.0,
        "LO_LIM": -1e9,
        "LO_LO_LIM": -1e9,
        "DV_HI_LIM": 1e9,
        "DV_LO_LIM": -1e9,
        "IN_LO": 0.0,
        "IN_HI": 200.0,
        "HI_ENAB": False,
        "HI_ENAB_DELAY": 0.5,
        "HI_DELAY_ON": 1.0,
        "HI_DELAY_OFF": 1.0,
        "HI_HYS": 1.0,  # 1% of the 200 EU IN range = 2 EU
    })
    block.inputs["IN"].value = 12.0

    block.execute(0.5)
    assert block.get_output("HI_ACT") is False
    block.config.params["HI_ENAB"] = True
    block.execute(0.5)  # ENAB_DELAY is consumed; processing remains suppressed.
    assert block.get_output("HI_ACT") is False
    block.execute(0.5)  # First half of DELAY_ON.
    assert block.get_output("HI_ACT") is False
    block.execute(0.5)
    assert block.get_output("HI_ACT") is True

    block.inputs["IN"].value = 9.0  # Still inside the 2 EU HI deadband.
    block.execute(0.5)
    assert block.get_output("HI_ACT") is True
    block.inputs["IN"].value = 7.0
    block.execute(0.5)
    assert block.get_output("HI_ACT") is True
    block.execute(0.5)
    assert block.get_output("HI_ACT") is False

    # Master disable and reset both remove latent timers and active states.
    block.inputs["IN"].value = 12.0
    block.execute(0.5)
    block.inputs["ENABLE"].value = False
    block.execute(0.5)
    assert not any(block.get_output(name) for name in (
        "HI_HI_ACT", "HI_ACT", "LO_ACT", "LO_LO_ACT",
        "DV_HI_ACT", "DV_LO_ACT",
    ))
    block.inputs["ENABLE"].value = True
    block.reset()
    assert block.get_output("HI_ACT") is False


def test_alarm_extensions_keep_roc_and_shelving_semantics() -> None:
    block = AlarmBlock("ALM_EXT")
    block.inputs["PV"].connected = True
    block.config.params.update({
        "HI_LIM": 10.0,
        "HI_HI_LIM": 100.0,
        "LO_LIM": -100.0,
        "LO_LO_LIM": -200.0,
        "DEADBAND": 1.0,
        "ROC_EN": True,
        "ROC_LIM": 5.0,
        "ROC_HYS": 1.0,
        "SHELVE_ACTION": "HOLD",
    })
    block.inputs["PV"].value = 12.0
    block.execute(1.0)
    assert block.get_output("HI") is True
    assert block.get_output("ROC") is False

    block.inputs["SHELVE"].value = True
    block.execute(1.0)
    assert block.get_output("HI") is False
    assert block.get_output("ANY_ALARM") is False
    block.inputs["SHELVE"].value = False
    block.execute(1.0)
    assert block.get_output("HI") is True

    block.reset()
    block.inputs["PV"].value = 0.0
    block.execute(1.0)
    block.inputs["PV"].value = 10.0
    block.execute(1.0)
    assert block.get_output("ROC") is True
    block.inputs["SHELVE"].value = True
    block.inputs["PV"].value = 20.0
    block.execute(1.0)
    assert block.get_output("ROC") is False
    block.inputs["SHELVE"].value = False
    block.execute(1.0)
    assert block.get_output("ROC") is False  # no post-shelve derivative spike

    block.config.params["SHELVE_ACTION"] = "CLEAR"
    block.config.params["ROC_EN"] = False
    block.inputs["SHELVE"].value = True
    block.execute(1.0)
    block.inputs["SHELVE"].value = False
    block.inputs["PV"].value = 0.0
    block.execute(1.0)
    assert block.get_output("HI") is False
    assert block.get_output("ANY_ALARM") is False


def test_alias_serialization_preserves_each_block_type_and_terminal_surface() -> None:
    alarm = AlarmBlock.from_dict({
        "id": "alarm-legacy",
        "block_type": "ALARM",
        "instance_name": "ALM_OLD",
        "config": {
            "ALARM_HYS": 3.0,
            "HI_HI_ENAB": False,
            "CONDALM_ENABLED": True,
            "HI_HI_DELAY_ON": 2.0,
        },
    })
    assert alarm.config.params["DEADBAND"] == 3.0
    assert alarm.config.params["HH_EN"] is False
    assert "ALARM_HYS" not in alarm.config.params
    assert json.loads(json.dumps(alarm.to_dict()))["block_type"] == "ALARM"
    assert alarm.resolve_terminal("IN", output=False) == "PV"
    assert alarm.resolve_terminal("HI_HI_ACT", output=True) == "HI_HI"

    alarm_det = AlarmDetectionBlock.from_dict({
        "id": "alarm-det-legacy",
        "block_type": "ALARM_DET",
        "instance_name": "ALMDET_OLD",
        "config": {
            "DEADBAND": 4.0,
            "IN_SCALE_LO": -20.0,
            "IN_SCALE_HI": 80.0,
            "HH_EN": False,
            "CONDALM_ENABLED": True,
            "HI_HI_DELAY_ON": 1.5,
        },
    })
    assert alarm_det.config.params["ALARM_HYS"] == 4.0
    assert alarm_det.config.params["IN_LO"] == -20.0
    assert alarm_det.config.params["IN_HI"] == 80.0
    assert alarm_det.config.params["HI_HI_ENAB"] is False
    assert json.loads(json.dumps(alarm_det.to_dict()))["block_type"] == "ALARM_DET"
    assert alarm_det.resolve_terminal("PV", output=False) == "IN"
    assert alarm_det.resolve_terminal("HI_HI", output=True) == "HI_HI_ACT"
    assert tuple(alarm_det.inputs) == ("IN", "SP", "ENABLE")
    assert tuple(alarm_det.outputs) == (
        "PV", "HI_HI_ACT", "HI_ACT", "LO_ACT", "LO_LO_ACT",
        "DV_HI_ACT", "DV_LO_ACT",
    )


def test_alarm_det_runs_under_compiler_runtime_and_block_debugger() -> None:
    graph = StrategyGraph("ALARM_RUNTIME")
    block = AlarmDetectionBlock("ALMDET_201")
    block.config.params.update({"DEFAULT": 85.0, "HI_LIM": 80.0})
    graph.add_block(block)
    runtime = StrategyRuntime()
    runtime.load(compile_strategy(graph), DataBridge(SharedDataStore()))
    assert runtime.go_online()
    assert runtime.debug_pause()
    assert runtime.debug_step_block(0.25) == block.id
    assert block.get_output("HI_ACT") is True
    status = runtime.get_debug_status()
    assert status["current_block"] == {
        "id": block.id,
        "name": "ALMDET_201",
        "type": "ALARM_DET",
        "order": 1,
    }
    assert block.outputs["PV"].value == 85.0
    assert block.force_terminal("IN", 70.0)
    assert runtime.debug_run_scan(0.25) == 1
    assert block.outputs["PV"].value == 70.0
    assert block.get_output("HI_ACT") is False
