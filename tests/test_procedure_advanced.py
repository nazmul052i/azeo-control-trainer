"""Advanced PA paths remain bounded, observable and operator guided."""
import time
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from azeo_control_trainer.core.procedures.model import AdvisoryProcedure, ProcedureDefinition
from azeo_control_trainer.core.procedures.runtime import ProcedureRun


def definition(steps, nodes, edges, variables=()):
    return ProcedureDefinition(AdvisoryProcedure.model_validate({
        "procedure_id": "advanced", "name": "Advanced guidance", "mode": "advisory",
        "steps": steps, "variables": list(variables),
        "flow": {"nodes": [{"id": "start", "kind": "start"}, *nodes,
                           {"id": "end", "kind": "end"}], "edges": edges},
    }), {}).validate()


def execute(tmp_path, definition, answer=lambda event: True):
    run = ProcedureRun(tmp_path / "history.sqlite")
    run.observe(0, {})
    run.start(definition, actor="test operator")
    events, tick = [], 0
    try:
        deadline = time.monotonic() + 12
        while run.active and time.monotonic() < deadline:
            tick += .1
            run.observe(tick, {})
            batch = run.drain()
            events.extend(batch)
            for event in batch:
                if event["kind"] == "prompt":
                    assert run.answer(event["prompt_id"], answer(event))
            time.sleep(.005)
        events.extend(run.drain())
        assert not run.active, "PA worker did not finish"
        return run.result, events
    finally:
        assert run.close(timeout=5)


def test_decision_uses_operator_input_and_records_selected_path(tmp_path):
    d = definition([
        {"id": "decision", "type": "operator_input", "variable": "retry", "input_type": "bool"},
        {"id": "retry_action", "type": "instruction", "description": "Inspect equipment", "require_confirmation": False},
    ], [{"id": "ask", "kind": "action", "step_id": "decision"},
        {"id": "choose", "kind": "choice"},
        {"id": "retry", "kind": "action", "step_id": "retry_action"}], [
        {"source": "start", "target": "ask"}, {"source": "ask", "target": "choose"},
        {"source": "choose", "target": "retry", "condition": "retry == True"},
        {"source": "choose", "target": "end", "is_default": True},
        {"source": "retry", "target": "end"}], [{"name": "retry", "value": False, "data_type": "bool"}])
    result, events = execute(tmp_path, d, lambda _: False)
    assert result.status.value == "COMPLETE"
    assert any(e["kind"] == "step" and e["step"] == "retry_action" and e["status"] == "SKIPPED" for e in events)
    assert any(e["kind"] == "flow" and e["event"] == "FLOW_EDGE" and e["payload"]["target"] == "end" for e in events)


def test_retry_loop_exits_at_authored_limit(tmp_path):
    d = definition([{"id": "attempt", "type": "user_event", "event_name": "attempt"}],
        [{"id": "retry", "kind": "loop", "max_iterations": 2},
         {"id": "attempt", "kind": "action", "step_id": "attempt"}], [
        {"source": "start", "target": "retry"},
        {"source": "retry", "target": "attempt", "condition": "True"},
        {"source": "retry", "target": "end", "is_default": True},
        {"source": "attempt", "target": "retry"}])
    result, events = execute(tmp_path, d)
    assert result.status.value == "COMPLETE"
    assert sum(e["kind"] == "step" and e["status"] == "PASSED" for e in events) == 2


def test_parallel_prompts_are_serialized_and_all_completed(tmp_path):
    d = definition([{"id": key, "type": "operator_confirm", "description": key} for key in ("a", "b")],
        [{"id": "fork", "kind": "parallel_fork"},
         *[{"id": key, "kind": "action", "step_id": key} for key in ("a", "b")],
         {"id": "join", "kind": "parallel_join"}], [
        {"source": "start", "target": "fork"},
        *[{"source": "fork", "target": key} for key in ("a", "b")],
        *[{"source": key, "target": "join"} for key in ("a", "b")],
        {"source": "join", "target": "end"}])
    result, events = execute(tmp_path, d)
    assert result.status.value == "COMPLETE"
    assert {e["step"]["id"] for e in events if e["kind"] == "prompt"} == {"a", "b"}


def test_unbounded_cycle_is_rejected():
    with pytest.raises(ValueError, match="[Bb]ounded|[Ll]oop"):
        definition([{"id": "a", "type": "delay", "delay_sec": 1}],
            [{"id": "a", "kind": "action", "step_id": "a"}, {"id": "choice", "kind": "choice"}],
            [{"source": "start", "target": "a"}, {"source": "a", "target": "choice"},
             {"source": "choice", "target": "a", "condition": "True"},
             {"source": "choice", "target": "end", "is_default": True}])


def test_subprocedure_keeps_typed_results_and_revision_snapshot(tmp_path):
    from azeo_control_trainer.core.procedures.authoring import ProcedureDraft
    from azeo_control_trainer.core.procedures.model import load_definition
    child = ProcedureDraft({"procedure_id": "child", "name": "Reusable calculation", "mode": "advisory",
        "variables": [{"name": "input_value", "value": 0, "data_type": "int"},
                      {"name": "result", "value": 0, "data_type": "int"}],
        "steps": [{"id": "calculate", "type": "calculate", "variable": "result", "expression": "input_value + 2"}]}, {})
    child_path = child.save_revision(tmp_path)
    parent = ProcedureDraft({"procedure_id": "parent", "name": "Reusable guidance", "mode": "advisory",
        "variables": [{"name": "answer", "value": 0, "data_type": "int"}],
        "steps": [{"id": "call", "type": "subprocedure", "subprocedure_path": child_path.relative_to(tmp_path / "procedures").as_posix(),
                   "parameters": {"input_value": 5}, "result_variables": {"result": "answer"}},
                  {"id": "verify", "type": "check", "condition": "answer == 7"}]}, {})
    saved = parent.save_revision(tmp_path)
    d = load_definition(saved, tmp_path / "procedures")
    assert d.authored_document["dependencies"]
    assert any(s.id == "call/calculate" for s in d.procedure.steps)
    result, events = execute(tmp_path, d)
    assert result.status.value == "COMPLETE", result.message
    assert any(e["kind"] == "memory" and e["values"].get("answer") == 7 for e in events)
    # A parent revision is self-contained and cannot drift with its source child.
    child_path.write_text("changed after preparation", encoding="utf-8")
    assert load_definition(saved, tmp_path / "procedures").digest == d.digest


@pytest.mark.parametrize("ready,expected", [(True, "COMPLETE"), (False, "HELD")])
def test_workflow_example_success_and_escalation(tmp_path, ready, expected):
    from azeo_control_trainer.core.procedures.templates import readiness_workflow
    d = readiness_workflow().definition()
    def answer(event):
        kind = event["prompt_kind"]
        return ("Reviewed unit observations" if kind == "comment" else True if kind == "confirm" else
                "Hold for review" if event["step"]["id"] == "escalate" else ready)
    result, _ = execute(tmp_path, d, answer)
    assert result.status.value == expected, result.message


def test_editor_connects_real_workflow_and_retains_it_on_undo(tmp_path):
    from PySide6.QtWidgets import QApplication
    from azeo_control_trainer.azeo_pa_designer.window import PADesignerWindow
    from azeo_control_trainer.core.procedures.templates import readiness_workflow
    app = QApplication.instance() or QApplication([])
    editor = PADesignerWindow(tmp_path)
    try:
        editor.draft = readiness_workflow()
        editor.render()
        editor.show()
        editor.resize(1000, 700)
        app.processEvents()
        assert editor.width() <= 1000
        assert len(editor.canvas.edges) == len(editor.draft.data["flow"]["edges"])
        folder = os.environ.get("AZEO_HMI_CAPTURE_DIR")
        if folder:
            from pathlib import Path
            Path(folder).mkdir(parents=True, exist_ok=True)
            assert editor.grab().save(str(Path(folder) / "advanced-authoring.png"))
        before = editor.snapshot()
        editor.flow_editor.add_node("merge")
        assert len(editor.canvas.nodes) == len(editor.draft.data["flow"]["nodes"])
        editor.undo()
        assert editor.snapshot() == before
        editor.open_help_topic("advanced_workflow")
        assert "bounded" in editor.help_center.browser.toPlainText().lower()
    finally:
        editor._saved = editor.snapshot()
        editor.close()
        editor.deleteLater()
        app.processEvents()


def test_runtime_fault_does_not_follow_process_failure_edge(tmp_path):
    d = definition([{"id": "bad", "type": "calculate", "variable": "value", "expression": "1 / 0"},
                    {"id": "recovery", "type": "user_event", "event_name": "must_not_run"}],
        [{"id": "bad", "kind": "action", "step_id": "bad"},
         {"id": "recovery", "kind": "action", "step_id": "recovery"}],
        [{"source": "start", "target": "bad"}, {"source": "bad", "target": "end", "outcome": "passed"},
         {"source": "bad", "target": "recovery", "outcome": "failed"}, {"source": "recovery", "target": "end"}],
        [{"name": "value", "value": 0, "data_type": "float"}])
    result, events = execute(tmp_path, d)
    assert result.status.value == "FAILED"
    assert not any(e["kind"] == "step" and e["step"] == "recovery" for e in events)


@pytest.mark.parametrize("join_mode", ["and", "or"])
def test_parallel_cancellation_releases_pending_prompt(tmp_path, join_mode):
    d = definition([{"id": "slow", "type": "operator_confirm", "description": "Pending response"},
                    {"id": "fast", "type": "delay", "delay_sec": 1}],
        [{"id": "fork", "kind": "parallel_fork"},
         {"id": "slow", "kind": "action", "step_id": "slow"}, {"id": "fast", "kind": "action", "step_id": "fast"},
         {"id": "join", "kind": "parallel_join", "join_mode": join_mode}],
        [{"source": "start", "target": "fork"}, {"source": "fork", "target": "slow"}, {"source": "fork", "target": "fast"},
         {"source": "slow", "target": "join"}, {"source": "fast", "target": "join"}, {"source": "join", "target": "end"}])
    run = ProcedureRun(tmp_path / "cancel.sqlite")
    run.observe(0, {})
    run.start(d, actor="operator")
    events, tick, stopped = [], 0, False
    try:
        deadline = time.monotonic() + 8
        while run.active and time.monotonic() < deadline:
            tick += .1
            run.observe(tick, {})
            batch = run.drain()
            events.extend(batch)
            if join_mode == "and" and any(e["kind"] == "prompt" for e in batch):
                run.control.pause()
                run.control.stop("Operator aborted a paused group")
                stopped = True
            time.sleep(.01)
        events.extend(run.drain())
        assert not run.active
        assert run.result.status.value == ("ABORTED" if join_mode == "and" else "COMPLETE")
        assert stopped or any(e["kind"] == "step" and e["status"] == "CANCELLED" for e in events)
        assert run._pending_prompt is None and not run._prompt_queue
    finally:
        assert run.close(timeout=5)


def test_proposal_cannot_complete_via_failed_verification():
    proc = AdvisoryProcedure.model_validate({"procedure_id": "proposal", "name": "Proposal paths", "mode": "advisory",
        "tags": [{"tag": "UNIT.SP", "access": "read_write", "data_type": "float"}],
        "steps": [{"id": "propose", "type": "write_tag", "tag": "UNIT.SP", "value": 5},
                  {"id": "verify", "type": "wait_until", "condition": "UNIT.SP == 5"},
                  {"id": "bypass", "type": "user_event", "event_name": "invalid"}],
        "flow": {"nodes": [{"id": "start", "kind": "start"},
            *[{"id": key, "kind": "action", "step_id": key} for key in ("propose", "verify", "bypass")],
            {"id": "end", "kind": "end"}], "edges": [
            {"source": "start", "target": "propose"}, {"source": "propose", "target": "verify"},
            {"source": "verify", "target": "end", "outcome": "passed"},
            {"source": "verify", "target": "bypass", "outcome": "timeout"}, {"source": "bypass", "target": "end"}]}})
    with pytest.raises(ValueError, match="verification"):
        ProcedureDefinition(proc, {"UNIT.SP": "UNIT/PID/SP"}).validate()


def test_repeated_subprocedure_gets_a_fresh_retry_budget(tmp_path):
    from azeo_control_trainer.core.procedures.authoring import ProcedureDraft
    child = definition([{"id": "attempt", "type": "user_event", "event_name": "attempt"}],
        [{"id": "retry", "kind": "loop", "max_iterations": 2},
         {"id": "attempt", "kind": "action", "step_id": "attempt"}], [
        {"source": "start", "target": "retry"},
        {"source": "retry", "target": "attempt", "condition": "True"},
        {"source": "retry", "target": "end", "is_default": True},
        {"source": "attempt", "target": "retry"}])
    path = ProcedureDraft(child.procedure.model_dump(mode="json"), {}).save_revision(tmp_path)
    parent = child.procedure.model_dump(mode="json")
    parent["procedure_id"] = "parent"
    parent["steps"] = [{"id": "attempt", "type": "subprocedure",
                        "subprocedure_path": path.relative_to(tmp_path / "procedures").as_posix()}]
    draft = ProcedureDraft(parent, {}, library=tmp_path / "procedures")
    result, events = execute(tmp_path, draft.definition())
    assert result.status.value == "COMPLETE", result.message
    assert sum(e["kind"] == "step" and e["step"] == "attempt/attempt" and e["status"] == "PASSED" for e in events) == 4


@pytest.mark.parametrize("policy,expected", [("hold", "HELD"), ("abort", "ABORTED")])
def test_reusable_failure_obeys_caller_policy(tmp_path, policy, expected):
    from azeo_control_trainer.core.procedures.authoring import ProcedureDraft
    child = ProcedureDraft({"procedure_id": "child", "name": "Unready equipment", "mode": "advisory",
                            "steps": [{"id": "check", "type": "check", "condition": "False"}]}, {})
    path = child.save_revision(tmp_path)
    parent = ProcedureDraft({"procedure_id": "parent", "name": "Recovery policy", "mode": "advisory",
        "steps": [{"id": "call", "type": "subprocedure", "on_failure": policy,
                   "subprocedure_path": path.relative_to(tmp_path / "procedures").as_posix()}]}, {},
        library=tmp_path / "procedures")
    result, _ = execute(tmp_path, parent.definition())
    assert result.status.value == expected, result.message


def test_transition_rechecks_process_after_operator_confirmation(tmp_path):
    from azeo_control_trainer.core.pa_designer.connectors.tag_value import TagValue
    from test_procedure_integration import wait_for
    proc = AdvisoryProcedure.model_validate({"procedure_id": "transition", "name": "Observed transition", "mode": "advisory",
        "tags": [{"tag": "UNIT.READY", "access": "read", "data_type": "bool"}],
        "steps": [{"id": "done", "type": "complete"}],
        "flow": {"nodes": [{"id": "start", "kind": "start"},
            {"id": "ready", "kind": "transition", "condition": "UNIT.READY", "completion_mode": "process_and_operator",
             "stable_for_sec": 1, "poll_sec": .1, "timeout_sec": 20},
            {"id": "done", "kind": "action", "step_id": "done"}, {"id": "end", "kind": "end"}],
            "edges": [{"source": "start", "target": "ready"}, {"source": "ready", "target": "done"},
                      {"source": "done", "target": "end"}]}})
    run = ProcedureRun(tmp_path / "transition.sqlite")
    events = []
    def collect():
        events.extend(run.drain())
    def observe(t, ready):
        run.observe(t, {"UNIT.READY": TagValue("UNIT.READY", ready)})
    try:
        observe(0, True)
        run.start(ProcedureDefinition(proc, {"UNIT.READY": "UNIT/DI/OUT"}).validate(), actor="operator")
        wait_for(lambda: any(e["kind"] == "progress" for e in events), collect)
        observe(2, True)
        wait_for(lambda: any(e["kind"] == "prompt" for e in events), collect)
        observe(3, False)
        run.answer(next(e["prompt_id"] for e in events if e["kind"] == "prompt"), True)
        wait_for(lambda: any(e["kind"] == "progress" and e["sim_time"] == 3 for e in events), collect)
        assert run.active
        observe(4, True)
        wait_for(lambda: any(e["kind"] == "progress" and e["sim_time"] == 4 for e in events), collect)
        assert run.active
        observe(5.1, True)
        wait_for(lambda: not run.active)
        assert run.result.status.value == "COMPLETE", run.result.message
    finally:
        run.close()


def test_workflow_clipboard_keeps_control_positions_and_undo(tmp_path):
    from PySide6.QtWidgets import QApplication
    from azeo_control_trainer.azeo_pa_designer.window import PADesignerWindow
    from azeo_control_trainer.core.procedures.templates import readiness_workflow
    app = QApplication.instance() or QApplication([])
    editor = PADesignerWindow(tmp_path)
    try:
        editor.draft = readiness_workflow()
        editor.render()
        steps = editor.draft.data["steps"][:2]
        payload = {"version": 1, "steps": steps,
            "layout": [{"step_id": "flow:branch", "x": 800, "y": 300}],
            "routes": [{"source": "step:" + steps[0]["id"], "target": "step:" + steps[1]["id"],
                        "points": [{"x": 20, "y": 20}, {"x": 100, "y": 20}]}],
            "flow_fragment": {"nodes": [{"id": "branch", "kind": "choice"},
                *[{"id": s["id"], "kind": "action", "step_id": s["id"]} for s in steps]],
                "edges": [{"source": "branch", "target": steps[0]["id"], "condition": "True"}]}}
        before = editor.snapshot()
        editor.paste_blocks(payload)
        position = next(row for row in editor.draft.data["metadata"]["canvas_layout"] if row["step_id"] == "flow:branch_1")
        assert (position["x"], position["y"]) == (836, 336)
        editor.undo()
        assert editor.snapshot() == before
        payload["flow_fragment"]["edges"][0]["target"] = "missing"
        with pytest.raises(ValueError, match="missing"):
            editor.paste_blocks(payload)
        assert editor.snapshot() == before
    finally:
        editor._saved = editor.snapshot()
        editor.close()
        editor.deleteLater()
        app.processEvents()


def test_parallel_checks_every_calculation_result_before_start():
    with pytest.raises(ValueError, match="distinct result variables"):
        definition([{"id": key, "type": "calculate", "variable": key, "expression": "1",
                     "calculation_rows": [{"variable": key, "expression": "1"},
                                          {"variable": "shared_result", "expression": "2"}]} for key in ("a", "b")],
            [{"id": "fork", "kind": "parallel_fork"},
             *[{"id": key, "kind": "action", "step_id": key} for key in ("a", "b")],
             {"id": "join", "kind": "parallel_join"}], [
            {"source": "start", "target": "fork"},
            *[{"source": "fork", "target": key} for key in ("a", "b")],
            *[{"source": key, "target": "join"} for key in ("a", "b")],
            {"source": "join", "target": "end"}],
            [{"name": key, "data_type": "int", "value": 0} for key in ("a", "b", "shared_result")])
