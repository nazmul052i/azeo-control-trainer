"""Hostile authored code and stalled consumers must not stall a controller."""
# ruff: noqa: E402
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from azeo_control_trainer.core.strategy.blocks.action_block import ActionBlock, _safe_eval
from azeo_control_trainer.core.strategy.blocks.expression_block import ExpressionBlock
from azeo_control_trainer.core.strategy.blocks.script_functions import buffer_push
from azeo_control_trainer.core.procedures.runtime import ProcedureRun
from azeo_control_trainer.core.procedures.logic import PreparedEvaluator
from azeo_control_trainer.core.pa_designer.core.expression_engine import ExpressionError


def test_infinite_script_returns_an_error_without_blocking_the_scan():
    source = """
from azeo_control_trainer.core.strategy.blocks.action_block import ActionBlock
block = ActionBlock('BAD')
block.config.params.update(SCRIPT_MODE=2, SCRIPT='while True: pass')
block.execute(.1)
assert block.get_output('ERROR'), block.get_script_error()
assert 'budget' in block.get_script_error().lower()
"""
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    try:
        result = subprocess.run([sys.executable, "-c", source], env=env,
                                capture_output=True, text=True, timeout=5)
    except subprocess.TimeoutExpired:
        pytest.fail("An authored while loop hung the controller")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("expression", ["2 ** 100000", "'x' * 100000", "pow(2, 100000)",
                                       "(lambda: 1)()", "IN1.real", "[x for x in [1, 2]]"])
def test_expression_block_rejects_oversized_values_and_object_access(expression):
    block = ExpressionBlock("CALC")
    block.execute(.1)
    block.config.params["expression"] = expression
    block.execute(.1)
    assert block.get_output("ERROR") is True


def test_action_expression_has_short_circuit_and_allocation_limits():
    assert _safe_eval("False and (1 / 0)", {}) is False
    with pytest.raises(ValueError):
        _safe_eval("[0] * 100000", {})


def test_action_code_cache_does_not_keep_every_edited_revision():
    block = ActionBlock("ACT")
    for index in range(200):
        block.config.params.update(SCRIPT_MODE=2, SCRIPT=f"OUT1 = {index}")
        block.execute(.1)
    assert len(block._code_cache) <= 16


def test_zero_length_buffer_is_not_unbounded():
    with pytest.raises(ValueError):
        buffer_push({}, "history", 1, size=0)


def test_pa_container_limits_include_nested_total_size():
    nested = [0] * 128
    for _ in range(2):
        nested = [nested] * 128
    with pytest.raises(ExpressionError):
        PreparedEvaluator().evaluate("value", {"value": nested})


def test_pa_slow_consumer_cannot_grow_events_forever(tmp_path):
    run = ProcedureRun(tmp_path / "audit.sqlite")
    try:
        for index in range(10000):
            try:
                run.emit("message", message=str(index))
            except RuntimeError:
                break
        assert run.events.qsize() <= 2048
        assert run.control.is_abort_requested
        run.emit("finished", status="FAILED", message="backlog", audit_failed=True)
        events = []
        while run.events.qsize():
            batch = run.drain()
            assert len(batch) <= 256
            events.extend(batch)
        assert events[-1]["kind"] == "finished"
    finally:
        run.close()


def test_pa_rejects_answers_that_are_not_for_an_active_prompt(tmp_path):
    run = ProcedureRun(tmp_path / "audit.sqlite")
    try:
        for index in range(1000):
            assert run.answer(str(index), True) is False
        assert not run._answers
    finally:
        run.close()


def test_new_pa_run_does_not_wait_on_the_previous_runs_pause(tmp_path, monkeypatch):
    from test_procedure_integration import definition_for, wait_for
    from azeo_control_trainer.core.pa_designer.connectors.tag_value import TagValue
    run = ProcedureRun(tmp_path / "audit.sqlite")
    run.observe(0, {"LOOP.PV": TagValue("LOOP.PV", 20)})
    run.control.pause()
    run.control.stop("Previous paused run aborted")
    checkpoint = run.control.checkpoint
    def checked_checkpoint():
        assert not run.control._awaiting_resume_observation, "start would wait on the UI thread"
        return checkpoint()
    monkeypatch.setattr(run.control, "checkpoint", checked_checkpoint)
    try:
        run.start(definition_for([dict(id="check", type="check", condition="LOOP.PV == 20")]), actor="Test")
        wait_for(lambda: not run.active)
        assert run.result.status.value == "COMPLETE"
    finally:
        run.close()


def test_pa_tuning_has_backpressure(tmp_path):
    run = ProcedureRun(tmp_path / "audit.sqlite")
    run.thread = SimpleNamespace(is_alive=lambda: True)
    try:
        with pytest.raises(ValueError, match="pending"):
            for index in range(1000):
                run.request_tuning(dict(parameter="test", value=index))
        assert run.tuning_requests.qsize() <= 64
    finally:
        run.thread = None
        run.close()


@pytest.mark.parametrize("debug", [False, True])
def test_runtime_marks_failed_outputs_bad_and_continues_other_blocks(debug):
    from test_online_fbd_debugger import make_runtime
    from azeo_control_trainer.core.strategy.model.terminal import Quality
    runtime, _, _, (bad, healthy, _), _, _ = make_runtime()
    def fail(_dt):
        raise ValueError("calculation failed")
    bad.execute = fail
    if debug:
        runtime.debug_run_scan(.1)
    else:
        runtime.execute_scan(.1)
    assert bad.outputs["OUT"].status == Quality.BAD
    assert healthy.calls == 1


@pytest.mark.parametrize("dt", [0, -1, float("nan"), float("inf")])
def test_invalid_time_cannot_grow_delay_history(dt):
    from azeo_control_trainer.core.strategy.blocks.filter_blocks import DeadtimeBlock
    block = DeadtimeBlock("DELAY")
    with pytest.raises(ValueError, match="positive"):
        block.execute(dt)
    assert not block._buffer


def test_calendar_timer_skips_missed_periods_in_constant_work(monkeypatch):
    from datetime import datetime, timedelta
    import azeo_control_trainer.core.strategy.blocks.dv_timer_blocks as timers
    block = timers.DateTimeEventBlock("SCHEDULE")
    block.config.params["INTERVAL_STR"] = "P00000T00:00:05"
    block._target = datetime(2000, 1, 1)
    now = datetime(2026, 1, 1)
    block._now = lambda: now
    calls = 0
    def counted_delta(**kwargs):
        nonlocal calls
        calls += 1
        assert calls <= 3, "Timer iterated over historical events"
        return timedelta(**kwargs)
    monkeypatch.setattr(timers, "timedelta", counted_delta)
    block.execute(.1)
    assert block._target == now + timedelta(seconds=5)


def test_act_store_read_errors_are_not_converted_to_plausible_defaults():
    from azeo_control_trainer.core.strategy.engine.runtime_context import RuntimeContext
    def failed(*_args):
        raise RuntimeError("store unavailable")
    context = RuntimeContext(store=SimpleNamespace(get=failed, get_all=failed))
    with pytest.raises(RuntimeError, match="unavailable"):
        context.read("FIELD.PV", default=0)


def test_act_invalid_output_holds_the_previous_value_and_marks_bad():
    from azeo_control_trainer.core.strategy.model.terminal import Quality
    block = ActionBlock("ACT")
    block.config.params.update(SCRIPT_MODE=2, SCRIPT="OUT1 = 42")
    block.execute(.1)
    block.config.params["SCRIPT"] = "OUT1 = 99\nOUT2 = 'not a number'"
    block.execute(.1)
    assert block.get_output("ERROR")
    assert block.get_output("OUT1") == 42
    assert block.outputs["OUT1"].status == Quality.BAD


def test_failed_state_growth_rolls_back_and_valid_loops_still_work():
    block = ActionBlock("ACT")
    block.config.params.update(SCRIPT_MODE=2, SCRIPT="""
state['sum'] = 0
for value in [1, 2, 3]:
    state['sum'] += value
OUT1 = state.get('sum', 0)
""")
    block.execute(.1)
    assert block.get_output("OUT1") == 6
    before = dict(block._user_state)
    block.config.params["SCRIPT"] = "state['cycle'] = state"
    block.execute(.1)
    assert block.get_output("ERROR")
    assert block._user_state == before


def test_expression_state_rolls_back_if_later_arithmetic_fails():
    block = ActionBlock("ACT")
    block.config.params.update(SCRIPT_MODE=1, EXPRESSION="ewma(state, 'pv', IN1, .2) / 0")
    block.execute(.1)
    assert block.get_output("ERROR")
    assert block._user_state == {}


def test_delay_history_fails_instead_of_silently_shortening_delay():
    from azeo_control_trainer.core.strategy.blocks.filter_blocks import DeadtimeBlock
    block = DeadtimeBlock("DELAY")
    block.MAX_HISTORY_SAMPLES = 16
    block.config.params["DELAY"] = 1000
    for _ in range(16):
        block.execute(.1)
    with pytest.raises(ValueError, match="sample budget"):
        block.execute(.1)
    assert len(block._buffer) == len(block._time_buffer) == 16


def test_station_close_does_not_wait_for_slow_worker_io():
    from azeo_control_trainer.azeo_operator_station.procedure_session import ProcedureSession
    timeouts = []
    def close(timeout):
        timeouts.append(timeout)
        return False
    session = SimpleNamespace(closed=False, run=SimpleNamespace(close=close))
    assert ProcedureSession.close(session) is False
    assert timeouts == [.05]
    assert "audit" in session.status


@pytest.mark.parametrize("source", ["import os", "OUT1 = get_tag.__globals__",
                                    "OUT1 = open('test')", "OUT1 = (lambda: 1)()",
                                    "OUT1 = '{0.__class__}'.format(0)",
                                    "def spin():\n    spin()\nspin()"])
def test_authored_code_has_no_python_object_or_process_access(source):
    block = ActionBlock("ACT")
    block.config.params.update(SCRIPT_MODE=2, SCRIPT=source)
    block.execute(.1)
    assert block.get_output("ERROR")


def test_script_tag_writes_keep_the_existing_allowlist():
    from azeo_control_trainer.core.strategy.engine.runtime_context import RuntimeContext
    from azeo_control_trainer.core.datastore.shared_data_store import SharedDataStore
    store = SharedDataStore()
    block = ActionBlock("ACT")
    block.runtime_context = RuntimeContext(store=store, write_allowlist=frozenset({"ALLOWED"}))
    block.config.params.update(SCRIPT_MODE=2, SCRIPT="set_tag('DENIED', 9)")
    block.execute(.1)
    assert block.get_output("ERROR")
    assert store.drain_writes() == []
    block.config.params["SCRIPT"] = "set_tag('ALLOWED', 9)"
    block.execute(.1)
    assert not block.get_output("ERROR")
    assert store.drain_writes() == [("ALLOWED", 9)]


@pytest.mark.parametrize("write", [False, True])
def test_locked_memory_database_does_not_stall_a_scan(tmp_path, write):
    import sqlite3
    import time
    from azeo_control_trainer.core.datastore.memory_tags import MemoryTagStore
    from azeo_control_trainer.core.strategy.blocks.utility_blocks import MemoryFloatBlock
    from azeo_control_trainer.core.strategy.model.terminal import Quality
    store = MemoryTagStore(tmp_path)
    spec = store.create(dict(name="operator_value", data_type="float", value=12.0))
    block = MemoryFloatBlock("MEM")
    block._memory_tags = store
    block.config.params["memory_tag"] = spec.path
    block.inputs["WRITE_EN"].value = write
    block.inputs["IN"].value = 18.0
    lock = sqlite3.connect(store.path)
    try:
        lock.execute("BEGIN EXCLUSIVE")
        times = []
        for _ in range(3):
            start = time.perf_counter()
            block.execute(.1)
            times.append(time.perf_counter() - start)
        assert min(times) < .2, times
        assert block.outputs["OUT"].status == Quality.BAD
        assert "locked" in block._memory_error
    finally:
        lock.rollback()
        lock.close()
    block.execute(.1)
    assert block.outputs["OUT"].status == Quality.GOOD
    assert block.get_output("OUT") == (18.0 if write else 12.0)


def test_looping_sfc_keeps_bounded_recent_history_and_visit_counts():
    from azeo_control_trainer.core.strategy.blocks.sfc_chart_block import (
        Chart, ChartStep, ChartTransition, SfcChartBlock)
    block = SfcChartBlock("LOOP")
    block.set_chart(Chart(steps=[ChartStep("a", "A", initial=True), ChartStep("b", "B")],
                          transitions=[ChartTransition("ab", "AB", condition="True", source="a", target="b"),
                                       ChartTransition("ba", "BA", condition="True", source="b", target="a")]))
    for _ in range(5000):
        block.execute(.1)
    snapshot = block.sequence_snapshot()
    assert len(snapshot["completedPath"]) <= 1024
    assert all(row["visits"] >= 2000 for row in snapshot["steps"])
    assert {row["status"] for row in snapshot["steps"]} == {"ACTIVE", "COMPLETE"}


@pytest.mark.parametrize("kind, parameter", [("MOVING_AVG", "N"), ("STATISTICS", "N"),
                                           ("FIFO", "DEPTH"), ("LIFO", "DEPTH")])
def test_history_configuration_cannot_request_unbounded_memory(kind, parameter):
    from azeo_control_trainer.core.strategy.model.block_registry import registry
    block = registry.create(kind, "BUFFER")
    block.config.params[parameter] = 1000000000
    with pytest.raises(ValueError, match="65536"):
        block.execute(.1)


@pytest.mark.parametrize("parameter, value", [("prediction_horizon", 1000000000),
                                              ("control_horizon", 1000000000),
                                              ("model_horizon", 1000000000),
                                              ("sample_period", 0)])
def test_dmc_rejects_unsafe_dimensions_before_allocating(monkeypatch, parameter, value):
    import azeo_control_trainer.core.strategy.blocks.dmc_blocks as dmc
    block = dmc.DMCControllerBlock("MPC")
    block.config.params[parameter] = value
    def no_allocation(*args):
        pytest.fail("Model allocation began before configuration limits were checked")
    monkeypatch.setattr(dmc, "_build_step_model", no_allocation)
    with pytest.raises(ValueError):
        block._build_model()


def test_dmc_vectorized_free_response_preserves_fir_indexing():
    import numpy as np
    from azeo_control_trainer.core.strategy.blocks.dmc_blocks import DMCControllerBlock, N_CV, N_MV, N_DV
    block = DMCControllerBlock("MPC")
    random = np.random.default_rng(53)
    block._P, block._N_model = 10, 16
    block._S = random.normal(size=(N_CV, 16, N_MV))
    block._Sd = random.normal(size=(N_CV, 16, N_DV))
    block._past_du = random.normal(size=(N_MV, 16))
    block._past_ddv = random.normal(size=(N_DV, 16))
    expected = np.zeros((10, N_CV))
    for k in range(10):
        for cv in range(N_CV):
            expected[k, cv] = sum(block._S[cv, k+t+1, mv] * block._past_du[mv, t]
                                  for t in range(15-k) for mv in range(N_MV))
            expected[k, cv] += sum(block._Sd[cv, k+t+1, dv] * block._past_ddv[dv, t]
                                   for t in range(15-k) for dv in range(N_DV))
    np.testing.assert_allclose(block._compute_free_response(), expected, rtol=1e-12, atol=1e-12)
