"""Continuous row qualification and run-local inputs drive the real PA runner."""
import os
from pathlib import Path
import sys
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from azeo_control_trainer.core.pa_designer.connectors.tag_value import TagValue
from azeo_control_trainer.core.procedures.authoring import ProcedureDraft
from azeo_control_trainer.core.procedures.logic import ConditionRow, ContinuousConditions, MemoryValue, row_logic
from azeo_control_trainer.core.procedures.runtime import ProcedureRun
from test_procedure_integration import wait_for


def test_row_timers_reset_independently_and_group_requires_its_own_dwell():
    rows = [ConditionRow(id="C1", expression="level", stable_for_sec=3),
            ConditionRow(id="C2", expression="flow", stable_for_sec=1)]
    tracker = ContinuousConditions(rows, row_logic(rows), 2)
    def observe(t, level=True, flow=True):
        return tracker.observe(t, {"level": level, "flow": flow}.__getitem__)
    assert observe(0)["conditions"][0]["state"] == "Timing"
    assert not observe(2, flow=False)["satisfied"]
    assert observe(3)["conditions"][0]["state"] == "Satisfied"
    assert not observe(4)["complete"]
    assert not observe(5)["complete"]
    assert observe(6)["complete"]
    assert not observe(7, level=None)["satisfied"]
    assert observe(7, level=None)["conditions"][0]["state"] == "Uncertain"
    assert not observe(8)["complete"]
    assert observe(11)["satisfied"]
    assert observe(13)["complete"]


def test_nested_or_requires_known_values_and_restarts_after_false():
    rows = [ConditionRow(id=f"C{i}", expression=f"v{i}") for i in range(1, 4)]
    tracker = ContinuousConditions(rows, row_logic(rows, custom="C1 and (C2 or C3)"), 2)
    assert not tracker.observe(0, {"v1": True, "v2": True, "v3": None}.__getitem__)["satisfied"]
    assert tracker.observe(1, {"v1": True, "v2": False, "v3": True}.__getitem__)["satisfied"]
    assert tracker.observe(3, {"v1": True, "v2": False, "v3": True}.__getitem__)["complete"]
    assert not tracker.observe(4, {"v1": False, "v2": False, "v3": True}.__getitem__)["complete"]
    for invalid in ("C1", "C1 or C4", "not C1", "True", "__import__('os')"):
        with pytest.raises(ValueError):
            row_logic(rows, custom=invalid)


def test_memory_types_limits_and_restricted_calculation_validation():
    memory = MemoryValue(name="limit", value=20, data_type="float", min_value=0, max_value=100)
    assert memory.checked(30) == 30.0
    for invalid in (True, "20", -1, 101, float("nan")):
        with pytest.raises(ValueError):
            memory.checked(invalid)
    draft = ProcedureDraft.new()
    draft.data["variables"] = [memory.model_dump(), dict(name="threshold", value=0)]
    draft.data["steps"] = [dict(id="calc", type="calculate", calculation_rows=[
        dict(variable="threshold", expression="limit * 0.5"),
        dict(variable="limit", expression="threshold + 1")])]
    assert draft.definition()
    draft.data["steps"][0]["calculation_rows"][1]["expression"] = "UNMAPPED.PV + 1"
    with pytest.raises(ValueError, match="Declare and map"):
        draft.definition()
    draft.data["steps"][0]["calculation_rows"][1]["expression"] = "__import__('os')"
    with pytest.raises(ValueError):
        draft.definition()


def test_operator_input_calculations_and_timed_rows_roundtrip_and_run(tmp_path):
    draft = ProcedureDraft.new()
    draft.data["variables"] = [dict(name="limit", value=20, data_type="float", min_value=1, max_value=50),
                               dict(name="threshold", value=0, data_type="float")]
    draft.data["tags"] = [dict(tag="P.LEVEL", data_type="float", access="read")]
    draft.bindings = {"P.LEVEL": "VESSEL/AI/OUT"}
    draft.data["steps"] = [dict(id="input", type="operator_input", variable="limit", input_type="float"),
        dict(id="wait", type="wait_until", calculation_rows=[dict(variable="threshold", expression="limit / 2")],
             condition_rows=[dict(id="C1", expression="P.LEVEL < limit", stable_for_sec=2),
                             dict(id="C2", expression="P.LEVEL < threshold", stable_for_sec=1)],
             condition_match="ALL", stable_for_sec=1, poll_sec=.1, timeout_sec=20)]
    path = draft.save_revision(tmp_path)
    reopened = ProcedureDraft.load(path, tmp_path / "procedures")
    definition = reopened.definition()
    assert definition.procedure.steps[0].max_value == 50
    assert len(definition.procedure.steps[1].condition_rows) == 2
    run = ProcedureRun(tmp_path / "audit.sqlite")
    events = []
    def collect():
        events.extend(run.drain())
    def observe(t):
        run.observe(t, {"P.LEVEL": TagValue("P.LEVEL", 5)})
        wait_for(lambda: any(e["kind"] == "progress" and e["sim_time"] == t for e in events), collect)
    try:
        run.observe(0, {"P.LEVEL": TagValue("P.LEVEL", 5)})
        run.start(definition, actor="Test")
        wait_for(lambda: any(e["kind"] == "prompt" for e in events), collect)
        run.answer(next(e["prompt_id"] for e in events if e["kind"] == "prompt"), 18)
        observe(0)
        observe(2)
        assert run.active
        observe(3)
        wait_for(lambda: not run.active)
        assert run.result.status.value == "COMPLETE"
        assert run.store.verify_audit_chain()[0]
    finally:
        run.close()


def test_false_pulse_between_worker_polls_cannot_earn_hold_time(tmp_path):
    from test_procedure_integration import definition_for
    definition = definition_for([dict(id="wait", type="wait_until", condition="LOOP.PV == 20",
                                     stable_for_sec=5, timeout_sec=30, poll_sec=5)])
    run = ProcedureRun(tmp_path / "audit.sqlite")
    events = []
    def collect():
        events.extend(run.drain())
    def observe(t, value=20):
        run.observe(t, {"LOOP.PV": TagValue("LOOP.PV", value)})
    try:
        observe(0)
        run.start(definition, actor="Test")
        wait_for(lambda: any(e["kind"] == "progress" for e in events), collect)
        observe(2, 0)
        observe(3)
        observe(5)
        wait_for(lambda: any(e["kind"] == "progress" and e["elapsed"] == 5 for e in events), collect)
        assert run.active
        assert [e for e in events if e["kind"] == "progress"][-1]["stable"] == 2
        observe(10)
        wait_for(lambda: not run.active)
        assert run.result.status.value == "COMPLETE"
    finally:
        run.close()


def test_queued_false_after_qualification_prevents_stale_completion(tmp_path):
    from test_procedure_integration import definition_for
    definition = definition_for([dict(id="wait", type="wait_until", condition="LOOP.PV == 20",
                                     stable_for_sec=5, timeout_sec=30, poll_sec=10)])
    run = ProcedureRun(tmp_path / "audit.sqlite")
    events = []
    def collect():
        events.extend(run.drain())
    def observe(t, value=20):
        run.observe(t, {"LOOP.PV": TagValue("LOOP.PV", value)})
    try:
        observe(0)
        run.start(definition, actor="Test")
        wait_for(lambda: any(e["kind"] == "progress" for e in events), collect)
        observe(5)
        observe(6, 0)
        observe(7)
        observe(10)
        wait_for(lambda: any(e["kind"] == "progress" and e["elapsed"] == 10 for e in events), collect)
        assert run.active
        assert [e for e in events if e["kind"] == "progress"][-1]["stable"] == 3
        observe(20)
        wait_for(lambda: not run.active)
        assert run.result.status.value == "COMPLETE"
    finally:
        run.close()


def test_replayed_scan_checks_capture_freshness_without_rewriting_timestamps():
    from azeo_control_trainer.core.procedures.runtime import CapturedTagProvider, ObservationConnector, SimulationControl
    from azeo_control_trainer.core.pa_designer.connectors.advisory_tag_provider import BadTagQualityError
    control = SimulationControl()
    control.observe(0)
    connector = ObservationConnector(control)
    provider = CapturedTagProvider(connector, stale_after_seconds=10)
    captured = datetime.now(timezone.utc) - timedelta(seconds=30)
    sample = TagValue("P.PV", 20, timestamp=captured.isoformat())
    connector.update({"P.PV": sample})
    with connector.frozen({"P.PV": sample}, observed_at=captured):
        assert provider.read_value("P.PV") is sample
    with pytest.raises(BadTagQualityError, match="Stale"):
        provider.read_value("P.PV")
    for invalid in (TagValue("P.PV", 20, timestamp=(captured - timedelta(seconds=11)).isoformat()),
                    TagValue("P.PV", 20, timestamp=(captured + timedelta(seconds=10)).isoformat()),
                    TagValue("P.PV", 20, timestamp=""), TagValue("P.PV", 20, "Bad", captured.isoformat()),
                    TagValue("P.PV", float("nan"), timestamp=captured.isoformat())):
        with connector.frozen({"P.PV": invalid}, observed_at=captured), pytest.raises(BadTagQualityError):
            provider.read_value("P.PV")


def test_continuous_arrivals_do_not_extend_the_captured_wait_frontier(tmp_path, monkeypatch):
    from test_procedure_integration import definition_for
    definition = definition_for([dict(id="wait", type="wait_until", condition="LOOP.PV == 20",
                                     stable_for_sec=5, timeout_sec=1000, poll_sec=1)])
    run = ProcedureRun(tmp_path / "audit.sqlite")
    events = []
    def observe(t, value=20):
        run.observe(t, {"LOOP.PV": TagValue("LOOP.PV", value)})
    try:
        observe(0)
        run.start(definition, actor="Test")
        wait_for(lambda: any(e["kind"] == "progress" for e in events), lambda: events.extend(run.drain()))
        original = run.connector.observations_since
        frontier = 64
        fetches = []
        def continuous_arrivals(sequence):
            nonlocal frontier
            snapshot = original(sequence)
            fetches.append(frontier)
            assert len(fetches) <= 3, "The worker kept extending the captured frontier"
            for stamp in range(frontier + 1, frontier + 65):
                observe(stamp)
            frontier += 64
            return snapshot
        # More than one audit batch, including a late false pulse, must be
        # checked before passing. New arrivals must not move that boundary.
        with run.control.lock:
            monkeypatch.setattr(run.connector, "observations_since", continuous_arrivals)
            for stamp in range(1, 65):
                observe(stamp, 0 if stamp == 63 else 20)
        wait_for(lambda: not run.active)
        assert run.result.status.value == "COMPLETE", run.result
        assert fetches == [64, 128]
        reads = run.store.run_reads(run.run_id)
        assert len(reads) == 129
        assert run.store.verify_audit_chain()[0]
    finally:
        run.close()


def test_audit_coverage_uses_lookups_and_still_detects_unsigned_reads(tmp_path):
    from azeo_control_trainer.core.procedures.audit import ProcedureStore
    store = ProcedureStore(tmp_path / "audit.sqlite")
    store.start_run("run", "test", "Audit coverage", "RUNNING")
    with store.observation_batch():
        for index in range(100):
            store.log_read("run", "P.LEVEL", index)
    with store._connect() as connection:
        statements = []
        connection.set_trace_callback(statements.append)
        assert store._first_uncovered_record(connection, "tag_reads", "TAG_READ") is None
        connection.set_trace_callback(None)
        plan = connection.execute("EXPLAIN QUERY PLAN " + statements[-1]).fetchall()
        assert not any("SCAN audit" in row[-1] for row in plan), plan
        connection.execute("INSERT INTO tag_reads(run_id,tag,value_json,quality,created_at) VALUES ('run','unsigned','1','Good','now')")
    valid, message = store.verify_audit_chain()
    assert not valid and "no audit entry" in message
