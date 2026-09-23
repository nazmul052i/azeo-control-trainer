"""Operator tuning changes the worker, restarts evidence and leaves signed history."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from azeo_control_trainer.core.pa_designer.connectors.tag_value import TagValue
from azeo_control_trainer.core.procedures.authoring import ProcedureDraft
from azeo_control_trainer.core.procedures.runtime import ProcedureRun
from azeo_control_trainer.core.procedures.parameters import pending_parameters
from test_procedure_integration import wait_for


def definition():
    draft = ProcedureDraft.new()
    draft.data["variables"] = [dict(name="limit", value=10, data_type="float", min_value=1,
                                    max_value=20, operator_tuning="live")]
    draft.data["tags"] = [dict(tag="P.LEVEL", data_type="float", access="read")]
    draft.bindings = {"P.LEVEL": "VESSEL/AI/OUT"}
    draft.data["steps"] = [dict(id="wait", type="wait_until", condition="P.LEVEL < limit",
                               stable_for_sec=5, timeout_sec=100, poll_sec=.1, timer_tuning="live")]
    return draft.definition()


def test_live_parameter_resets_qualification_and_records_values(tmp_path):
    run = ProcedureRun(tmp_path / "history.sqlite")
    model = definition()
    events = []
    def collect():
        events.extend(run.drain())
    def observe(t):
        run.observe(t, {"P.LEVEL": TagValue("P.LEVEL", 5)})
        wait_for(lambda: any(e["kind"] == "progress" and e["elapsed"] == t for e in events), collect)
    try:
        run.observe(0, {"P.LEVEL": TagValue("P.LEVEL", 5)})
        run.start(model, actor="Test")
        observe(0)
        observe(4)
        run.request_tuning(dict(ref="test.yaml", definition=model.digest, parameter="step/wait/hold",
                                value=3., actor="Test"))
        wait_for(lambda: any(e["kind"] == "parameter_applied" for e in events), collect)
        observe(5)
        assert run.active
        progress = [e for e in events if e["kind"] == "progress"][-1]
        assert progress["stable"] == 0 and progress["dwell"] == 3
        assert progress["conditions"][0]["quality"] == "Good"
        assert "P.LEVEL = 5" in progress["conditions"][0]["value"]
        observe(8)
        wait_for(lambda: not run.active)
        assert run.result.status.value == "COMPLETE"
        assert model.procedure.steps[0].stable_for_sec == 5
        assert run.store.verify_audit_chain()[0]
    finally:
        run.close()


def test_next_run_queue_is_signed_retained_and_consumed_once(tmp_path):
    run = ProcedureRun(tmp_path / "history.sqlite")
    model = definition()
    request = dict(ref="test.yaml", definition=model.digest, parameter="memory/limit", value=3., actor="Test", effective="next_run")
    run.store.log_event("tuning", "PA_TUNING_QUEUED", "test.yaml", "Next run", request)
    pending = pending_parameters(run.store, "test.yaml", model.digest)
    assert pending["memory/limit"]["value"] == 3
    events = []
    def collect():
        events.extend(run.drain())
        run.observe(1, {"P.LEVEL": TagValue("P.LEVEL", 5)})
    try:
        run.observe(0, {"P.LEVEL": TagValue("P.LEVEL", 5)})
        run.start(model, actor="Test", tuning=[dict(request, queued_event=pending["memory/limit"]["event_id"])])
        wait_for(lambda: any(e["kind"] == "parameter_applied" for e in events), collect)
        wait_for(lambda: any(e["kind"] == "progress" for e in events), collect)
        assert [e for e in events if e["kind"] == "progress"][-1]["conditions"][0]["state"] == "Waiting"
        assert not pending_parameters(run.store, "test.yaml", model.digest)
        assert run.store.verify_audit_chain()[0]
    finally:
        run.close()


def test_tuning_rejects_unexposed_types_and_limits():
    from azeo_control_trainer.core.procedures.parameters import parameters
    spec = parameters(definition().procedure)["memory/limit"]
    for value in (True, "3", -1, 21, float("nan")):
        with pytest.raises(ValueError):
            spec.checked(value)


def test_applied_audit_failure_ends_guidance_instead_of_reusing_old_evidence(tmp_path, monkeypatch):
    run = ProcedureRun(tmp_path / "history.sqlite")
    original = run.store.log_event
    def write(run_id, kind, *args):
        if kind == "PA_TUNING_APPLIED":
            raise OSError("Audit disk unavailable")
        return original(run_id, kind, *args)
    monkeypatch.setattr(run.store, "log_event", write)
    events = []
    def collect():
        events.extend(run.drain())
    try:
        run.observe(0, {"P.LEVEL": TagValue("P.LEVEL", 5)})
        run.start(definition(), actor="Test")
        wait_for(lambda: any(e["kind"] == "progress" for e in events), collect)
        run.request_tuning(dict(ref="test.yaml", parameter="step/wait/hold", value=0., actor="Test"))
        wait_for(lambda: not run.active)
        assert run.result.status.value == "FAILED"
        assert "Audit disk unavailable" in run.result.message
        assert not any(e["kind"] == "parameter_applied" for e in run.drain())
    finally:
        run.close()


def test_numeric_current_criteria_use_the_active_memory_value():
    from types import SimpleNamespace
    from azeo_control_trainer.azeo_operator_station.procedure_monitor import current_criteria
    model = definition()
    session = SimpleNamespace(prepare=lambda _: model, ref="test.yaml", step_states={"wait": "ACTIVE"},
                              memory_values={"limit": 9.}, run=SimpleNamespace(memory=SimpleNamespace(read_many=lambda: {})))
    guides = current_criteria(session, "test.yaml")
    assert guides["VESSEL/AI/OUT"][0]["value"] == 9.
    session.memory_values.clear()
    assert not current_criteria(session, "test.yaml")
