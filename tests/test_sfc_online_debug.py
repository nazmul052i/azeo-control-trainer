"""Runtime contracts for Azeo-style SFC online diagnostics."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from azeo_control_trainer.core.strategy.blocks.sfc_chart_block import (
    Chart,
    ChartAction,
    ChartStep,
    ChartTransition,
    SfcChartBlock,
)
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph


def _block(*, condition: str = "False",
           actions: list[ChartAction] | None = None) -> tuple[
               SfcChartBlock, StrategyGraph]:
    block = SfcChartBlock("SEQ")
    block.set_chart(Chart(
        steps=[
            ChartStep("s1", "Start", initial=True, actions=actions or []),
            ChartStep("s2", "Finish"),
        ],
        transitions=[ChartTransition(
            "t1", "Advance", condition=condition, source="s1", target="s2")],
    ))
    graph = StrategyGraph("SFC_TEST")
    graph.add_block(block)
    graph.set_module_parameter(
        "COUNT", 0, access="internal_write", description="test counter")
    block._module_graph = graph
    return block, graph


def test_transition_disable_still_reports_hypothetical_result():
    block, _ = _block(condition="True")
    assert block.debug_disable_transition("t1")

    block.execute(0.1)

    assert block.get_output("ACTIVE") == "Start"
    assert block.transition_evaluation_snapshot() == [{
        "id": "t1",
        "name": "Advance",
        "condition": "True",
        "value": True,
        "fired": False,
        "forced": False,
        "disabled": True,
        "sourceStepDisabled": False,
        "error": "",
    }]


def test_transition_force_is_active_only_and_momentary():
    block, _ = _block(condition="False")
    assert not block.debug_force_transition("t1")  # no active level yet
    block.execute(0.1)
    assert block.get_output("ACTIVE") == "Start"
    assert block.debug_force_transition("t1")

    block.execute(0.1)

    assert block.get_output("ACTIVE") == "Finish"
    row = block.transition_evaluation_snapshot()[0]
    assert row["forced"] is True
    assert row["fired"] is True
    assert not block.debug_force_transition("t1")  # no longer leaves active step


def test_level_stop_pauses_elapsed_time_and_reset_keeps_debug_disables():
    block, _ = _block()
    block.execute(0.25)
    assert block.get_output("STEP_TIME") == 0.25
    block.debug_disable_transition("t1")
    block.debug_stop()

    block.execute(4.0)

    assert block.get_output("STEP_TIME") == 0.25
    assert block.sequence_snapshot()["mode"] == "STOPPED"
    block.debug_reset()
    assert "t1" in block.sequence_snapshot()["disabledTransitions"]
    block.debug_start()
    block.execute(0.5)
    assert block.get_output("STEP_TIME") == 0.5


def test_stored_action_runs_until_matching_reset_action():
    stored = ChartAction(
        "a_store", name="PumpDemand", qualifier="S",
        expression="write_param('COUNT', COUNT + 1)")
    reset = ChartAction(
        "a_reset", name="ResetPump", qualifier="R", target="PumpDemand")
    block = SfcChartBlock("SEQ")
    block.set_chart(Chart(
        steps=[
            ChartStep("s1", "Start", initial=True, actions=[stored]),
            ChartStep("s2", "Finish", actions=[reset]),
        ],
        transitions=[ChartTransition(
            "t1", condition="True", source="s1", target="s2")],
    ))
    graph = StrategyGraph("SFC_TEST")
    graph.add_block(block)
    graph.set_module_parameter("COUNT", 0, access="internal_write")
    block._module_graph = graph

    block.execute(0.1)  # store and run; transition to s2
    assert graph.module_parameters()["COUNT"]["value"] == 1
    block.execute(0.1)  # stored action runs, then R removes it
    assert graph.module_parameters()["COUNT"]["value"] == 2
    block.execute(0.1)
    assert graph.module_parameters()["COUNT"]["value"] == 2
    assert block.sequence_snapshot()["storedActions"] == []


def test_limited_and_delayed_action_qualifiers_use_step_time():
    limited = ChartAction(
        "a_l", qualifier="L", time_s=0.25,
        expression="write_param('COUNT', COUNT + 1)")
    delayed = ChartAction(
        "a_d", qualifier="D", time_s=0.2,
        expression="write_param('COUNT', COUNT + 10)")
    block, graph = _block(actions=[limited, delayed])

    block.execute(0.1)   # L only
    block.execute(0.1)   # L + D
    block.execute(0.1)   # D only

    assert graph.module_parameters()["COUNT"]["value"] == 22
