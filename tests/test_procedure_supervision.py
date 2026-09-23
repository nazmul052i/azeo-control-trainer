"""Concurrent station runs and governed operator decisions use real workers."""
# ruff: noqa: F811
from __future__ import annotations

from test_procedure_integration import station, wait_for  # noqa: F401

import pytest

from azeo_control_trainer.core.procedures.model import AdvisoryProcedure, ProcedureDefinition


def _definition(name, *, target="", skippable=False):
    tags = ([{"tag": "LOOP.SP", "access": "read_write", "data_type": "float"}]
            if target else [{"tag": "LOOP.PV", "access": "read", "data_type": "float"}])
    step = {"id": "review", "type": "instruction", "description": "Review the loop",
            "require_confirmation": True}
    if skippable:
        step["operator_skip_policy"] = "reason"
    return ProcedureDefinition(AdvisoryProcedure.model_validate({
        "procedure_id": name, "name": name, "mode": "advisory", "tags": tags,
        "steps": [step, {"id": "done", "type": "complete", "description": "Complete"}],
    }), {tags[0]["tag"]: target or "PILOT/PID1/PV"}).validate()


def _pump(session, runtime, caps, app):
    runtime.scan_count += 1
    caps["sim_time"] += 0.1
    session.poll()
    app.processEvents()


def test_two_main_runs_keep_prompts_tokens_and_audit_separate(station):
    app, window, runtime, caps, _pid = station
    supervisor = window.procedure_session()
    first = _definition("first")
    second = _definition("second")
    supervisor.select(first, "first/procedure.yaml")
    _pump(supervisor, runtime, caps, app)
    supervisor.start()
    first_context = supervisor.context_for("first/procedure.yaml")
    wait_for(lambda: first_context.prompt is not None,
             lambda: _pump(supervisor, runtime, caps, app))
    first_token = first_context.token()

    supervisor.select(second, "second/procedure.yaml")
    _pump(supervisor, runtime, caps, app)
    supervisor.start()
    second_context = supervisor.context_for("second/procedure.yaml")
    wait_for(lambda: second_context.prompt is not None,
             lambda: _pump(supervisor, runtime, caps, app))
    assert len(supervisor.active_runs) == 2
    assert first_context.run.run_id != second_context.run.run_id
    assert first_context.run.memory is second_context.run.memory
    dialog = window.open_procedures()
    dialog.sync_active_runs()
    assert dialog.active_picker.count() == 3
    dialog.active_picker.setCurrentIndex(dialog.active_picker.findData("first/procedure.yaml"))
    assert supervisor.ref == "first/procedure.yaml"
    dialog.active_picker.setCurrentIndex(dialog.active_picker.findData("second/procedure.yaml"))
    assert supervisor.ref == "second/procedure.yaml"
    with pytest.raises(ValueError, match="changed"):
        supervisor.execute("respond", first_token, True, "second/procedure.yaml")
    supervisor.execute("respond", second_context.token(), True, "second/procedure.yaml")
    wait_for(lambda: second_context.last_finished == "COMPLETE",
             lambda: _pump(supervisor, runtime, caps, app))
    assert first_context.run.active
    supervisor.execute("respond", first_token, True, "first/procedure.yaml")
    wait_for(lambda: first_context.last_finished == "COMPLETE",
             lambda: _pump(supervisor, runtime, caps, app))
    assert first_context.run.store is second_context.run.store
    assert supervisor.store.verify_audit_chain()[0]


def test_second_run_cannot_reserve_an_active_output(station):
    app, window, runtime, caps, _pid = station
    supervisor = window.procedure_session()
    supervisor.select(_definition("first_output", target="PILOT/PID1/SP"),
                      "first/procedure.yaml")
    _pump(supervisor, runtime, caps, app)
    supervisor.start()
    supervisor.select(_definition("second_output", target="PILOT/PID1/SP"),
                      "second/procedure.yaml")
    _pump(supervisor, runtime, caps, app)
    assert not supervisor.snapshot("second/procedure.yaml")["CAN_START"]
    with pytest.raises(ValueError, match="reserved"):
        supervisor.start()


def test_skippable_guidance_requires_reason_and_records_step_evidence(station):
    app, window, runtime, caps, _pid = station
    supervisor = window.procedure_session()
    supervisor.select(_definition("skippable", skippable=True))
    _pump(supervisor, runtime, caps, app)
    supervisor.start()
    wait_for(lambda: supervisor.prompt is not None,
             lambda: _pump(supervisor, runtime, caps, app))
    assert supervisor.prompt["prompt_kind"] == "skip_gate"
    with pytest.raises(ValueError, match="reason"):
        supervisor.execute("respond", supervisor.token(), {"decision": "skip", "reason": ""})
    supervisor.execute("respond", supervisor.token(),
                       {"decision": "skip", "reason": "Already reviewed with the field operator"})
    wait_for(lambda: supervisor.last_finished == "COMPLETE",
             lambda: _pump(supervisor, runtime, caps, app))
    assert supervisor.step_states["review"] == "SKIPPED"
    events = supervisor.run.store.run_events(supervisor.run.run_id)
    skipped = [row for row in events if row["event_type"] == "OPERATOR_SKIP"]
    assert len(skipped) == 1
    assert "field operator" in skipped[0]["message"]


def test_break_requires_reason_and_pauses_only_selected_run(station):
    app, window, runtime, caps, _pid = station
    supervisor = window.procedure_session()
    supervisor.select(_definition("break_run"))
    _pump(supervisor, runtime, caps, app)
    supervisor.start()
    wait_for(lambda: supervisor.prompt is not None,
             lambda: _pump(supervisor, runtime, caps, app))
    with pytest.raises(ValueError, match="reason"):
        supervisor.execute("break", supervisor.token(), "")
    supervisor.execute("break", supervisor.token(), "Field review")
    assert supervisor.run.control.is_paused
    events = supervisor.run.store.run_events(supervisor.run.run_id)
    assert any(row["event_type"] == "OPERATOR_BREAK" and row["message"] == "Field review"
               for row in events)


def test_engineering_rejects_skip_on_output_or_required_verification():
    for step_type in ("write_tag", "wait_until"):
        step = {"id": "unsafe", "type": step_type, "operator_skip_policy": "reason"}
        if step_type == "write_tag":
            step.update(tag="LOOP.SP", value=10)
        else:
            step.update(condition="LOOP.SP == 10")
        definition = ProcedureDefinition(AdvisoryProcedure.model_validate({
            "procedure_id": "unsafe", "name": "Unsafe skip", "mode": "advisory",
            "tags": [{"tag": "LOOP.SP", "access": "read_write", "data_type": "float"}],
            "steps": [step, {"id": "done", "type": "complete"}],
        }), {"LOOP.SP": "PILOT/PID1/SP"})
        with pytest.raises(ValueError, match="Operator skip"):
            definition.validate()
