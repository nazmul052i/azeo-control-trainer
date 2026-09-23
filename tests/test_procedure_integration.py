"""Procedures observe real Trainer bindings without acquiring control authority."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtWidgets import QApplication

from azeo_control_trainer.azeo_operator_station.console import LiveStation
from azeo_control_trainer.azeo_operator_station.deployment import PvmDeployment
from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore, PvmDisplay
from azeo_control_trainer.core.strategy.blocks.pid_block import PIDBlock
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
from azeo_control_trainer.core.strategy.model.terminal import Quality
from azeo_control_trainer.core.procedures.model import (
    AdvisoryProcedure, ProcedureDefinition, load_definition, loop_verification,
)
from azeo_control_trainer.core.procedures.runtime import ProcedureRun, SimulationControl
from azeo_control_trainer.core.pa_designer.connectors.tag_value import TagValue
from azeo_control_trainer.core.pa_designer.reports import build_run_report, verify_exported_report


@pytest.fixture
def station(tmp_path):
    app = QApplication.instance() or QApplication([])
    graph = StrategyGraph("PILOT")
    pid = PIDBlock("PID1")
    graph.add_block(pid)
    pid.set_mode("AUTO")
    pid.set_sp(20)
    for terminal in (*pid.inputs.values(), *pid.outputs.values()):
        terminal.status = Quality.GOOD
    pid.outputs["PV"].value = 20.0
    pid.outputs["SP"].value = 20.0
    runtime = SimpleNamespace(is_online=True, is_debug_paused=False,
                              scan_count=1, compiled=SimpleNamespace(graph=graph))
    caps = {"available": True, "paused": False, "running": True, "sim_time": 0.0}
    service = SimpleNamespace(project_dir=tmp_path, _runtimes=lambda: [runtime],
                              process_capabilities=lambda: dict(caps))
    store = DisplayStore(tmp_path / "pvm")
    document = PvmDisplay(name="Pilot")
    store.save_draft(document)
    store.publish(document, by="engineer")
    window = LiveStation(PvmDeployment(store), lambda: {graph.name: graph})
    window._tick.stop()
    # The fixture supplies real bindings and a controlled clock, without
    # constructing a simulator or changing a shipped course project.
    window.simulation_service = service
    window.show_display("Pilot")
    window.show()
    yield app, window, runtime, caps, pid
    window.close()
    wait_for(lambda: bool(getattr(window, "_closing", False)), app.processEvents)
    window.deleteLater()
    app.processEvents()


def wait_for(predicate, pump=lambda: None, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pump()
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), "Timed out waiting for procedure state"


@pytest.mark.parametrize("ending, expected", [("complete", "COMPLETE"), ("hold", "HELD"), ("abort", "ABORTED")])
def test_library_blocks_execute_the_advertised_advisory_behavior(tmp_path, ending, expected):
    from azeo_control_trainer.core.procedures.library import block_for_type, core_block_library

    steps = [block.instantiate(block.step_type) for block in core_block_library()
             if block.step_type not in {"hold", "abort", "complete", "subprocedure"}]
    proposal = next(index for index, step in enumerate(steps) if step["type"] == "write_tag")
    steps.insert(proposal + 1, dict(block_for_type("wait_until").instantiate("readback"), condition="TAG.SP == 0"))
    ramp = next(index for index, step in enumerate(steps) if step["type"] == "ramp_tag")
    steps[ramp].update(start=0, end=0, rate_per_sec=1, poll_sec=1)
    steps.insert(ramp + 1, dict(block_for_type("wait_until").instantiate("ramp_readback"), condition="TAG.SP == 0"))
    steps.append(block_for_type(ending).instantiate("end"))
    definition = ProcedureDefinition(AdvisoryProcedure.model_validate({
        "procedure_id": "catalog", "name": "Block library behavior", "mode": "advisory",
        "tags": [{"tag": "TAG.PV", "data_type": "float", "access": "read"},
                 {"tag": "TAG.SP", "data_type": "float", "access": "read_write"}],
        "variables": [{"name": name, "value": 0} for name in
                      ("operator_value", "pcs_value", "source_value", "calculated_value")],
        "steps": steps,
    }), {"TAG.PV": "LOOP/PID/PV", "TAG.SP": "LOOP/PID/SP"}).validate()
    run = ProcedureRun(tmp_path / "catalog.sqlite")
    clock = 0.0
    observed_prompts = set()

    def pump():
        nonlocal clock
        clock += 0.5
        run.observe(clock, {"TAG.PV": TagValue("TAG.PV", 1.0), "TAG.SP": TagValue("TAG.SP", 0.0)})
        for event in run.drain():
            if event["kind"] == "prompt":
                observed_prompts.add(event["step"]["type"])
                assert event["step"]["library_block_id"].startswith("azeo.procedure.")
                answer = {"confirm": True, "input": 5.0, "comment": "Observed."}[event["prompt_kind"]]
                run.answer(event["prompt_id"], answer)

    try:
        pump()
        run.start(definition, actor="test")
        wait_for(lambda: not run.active, pump, timeout=20)
        assert run.result.status.value == expected
        assert observed_prompts == {"instruction", "operator_confirm", "operator_input", "operator_comment"}
        events = {event["event_type"] for event in run.store.run_events(run.run_id)}
        assert {"PERMISSIVE_OK", "WATCHDOG_OK", "ADVISORY_WRITE_PROPOSAL", "CALCULATION",
                "USER_EVENT", "WARNING", "ALARM"} <= events
        assert run.connector.read_value("TAG.SP").value == 0.0
    finally:
        assert run.close()


def test_station_exposes_procedures_only_with_a_simulation_host(station):
    _app, window, *_ = station
    assert "procedures" in {row[0] for row in window.tools_menu()}
    window.simulation_service = None
    assert "procedures" not in {row[0] for row in window.tools_menu()}
    assert window.open_procedures() is None


def test_pending_audit_keeps_station_open_but_views_may_close(station, monkeypatch):
    _app, window, *_ = station
    dialog = window.open_procedures()
    monkeypatch.setattr(dialog.run, "close", lambda **_kwargs: False)
    assert window.close() is False
    assert not getattr(window, "_closing", False)
    assert not dialog._closed
    index = window.workspace.tabs.indexOf(window.workspace.entries["procedures"][1])
    window.workspace.close_tab(index)
    assert window.workspace.get("procedures") is None
    assert window.procedure_session().timer.isActive()
    assert window._procedure_close_retry.isActive()
    monkeypatch.setattr(dialog.run, "close", lambda **_kwargs: True)
    wait_for(lambda: bool(getattr(window, "_closing", False)), _app.processEvents)


def test_authored_revision_is_discovered_and_completed_by_station(station, tmp_path):
    from azeo_control_trainer.azeo_pa_designer import create_window

    app, window, runtime, caps, _pid = station
    editor = create_window(tmp_path, graphs_provider=lambda: [runtime.compiled.graph])
    try:
        editor.properties["name"].setText("Authored commissioning check")
        editor.add_tag()
        editor.tags.item(0, 0).setText("FLOW.PV")
        editor.tags.item(0, 3).setText("PILOT/PID1/PV")
        editor.add_type.setCurrentIndex(editor.add_type.findData("azeo.procedure.wait_until"))
        editor.add_step()
        editor.fields["condition"].setText("FLOW.PV >= 19 and FLOW.PV <= 21")
        editor.fields["stable_for_sec"].setText("0.2")
        path = editor.save_revision()
        assert path, editor.status.text()
        dialog = window.open_procedures()
        index = dialog.procedure.findData(str(path))
        assert index >= 0
        dialog.procedure.setCurrentIndex(index)
        runtime.scan_count += 1
        dialog.poll()
        assert dialog.start_button.isEnabled(), dialog.clock.text()
        dialog.start_button.click()

        def pump():
            runtime.scan_count += 1
            caps["sim_time"] += 0.1
            dialog.poll()
            app.processEvents()

        wait_for(lambda: dialog._prompt is not None, pump)
        dialog.submit.click()
        wait_for(lambda: dialog._prompt is not None and dialog._prompt["prompt_kind"] == "comment", pump)
        dialog.answer.setText("Authored flow verification passed.")
        dialog.submit.click()
        wait_for(lambda: dialog._last_finished == "COMPLETE", pump)
        assert dialog.run.result.status.value == "COMPLETE"
    finally:
        editor._saved = editor.snapshot()
        editor.close()


def test_missing_optional_install_dependency_is_reported_inside_station(station, monkeypatch):
    import builtins
    _app, window, *_ = station
    original = builtins.__import__
    def importing(name, *args, **kwargs):
        if name == "procedures":
            raise ModuleNotFoundError("Install the project dependencies: pydantic is unavailable")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", importing)
    dialog = window.open_procedures()
    assert "pydantic" in dialog.errors[0][2]


def test_complete_workflow_reads_controller_and_records_operator_evidence(station, tmp_path):
    app, window, runtime, caps, pid = station
    dialog = window.open_procedures()
    assert window.open_procedures() is dialog
    assert not dialog.start_button.isEnabled()  # Opening a window proves no completed scan.
    runtime.scan_count += 1
    dialog.poll()
    assert dialog.start_button.isEnabled(), dialog.clock.text()
    dialog.dwell.setValue(0.5)
    dialog.start_run()

    def pump():
        runtime.scan_count += 1
        caps["sim_time"] += 0.1
        dialog.poll()
        app.processEvents()

    wait_for(lambda: dialog._prompt is not None, pump)
    assert dialog._prompt["prompt_kind"] == "confirm"
    dialog.submit.click()
    wait_for(lambda: dialog._prompt is not None and dialog._prompt["prompt_kind"] == "comment", pump)
    dialog.answer.setText("Mode, setpoint and observed settling verified.")
    dialog.submit.click()
    wait_for(lambda: dialog._last_finished == "COMPLETE", pump)
    assert pid.inputs["SP"].value == 20.0
    report = build_run_report(dialog.run.store, dialog.run.run_id)
    assert report["audit_integrity"]["valid"]
    assert any(row["event_type"] == "TRAINER_STEP" for row in dialog.run.store.run_events(dialog.run.run_id))
    assert dialog.history.rowCount() == 1
    dialog.history.selectRow(0)
    dialog.review()
    assert "Mode, setpoint and observed settling verified." in dialog.report.toPlainText()
    exported = dialog.export(tmp_path / "run.json")
    assert verify_exported_report(exported, dialog.run.store)[0]
    saved = dialog.save_to_project()
    definition = load_definition(saved, dialog.library)
    assert definition.bindings == dialog.definition.bindings
    assert definition.procedure.steps[3].stable_for_sec == 0.5


def test_live_output_is_authorized_and_applied_by_station_checked_write(station):
    from azeo_control_trainer.core.procedures.library import block_for_source, block_for_type

    app, window, runtime, caps, pid = station
    output = block_for_source("pcs.set_data").instantiate("set_sp")
    output.update(tag="LOOP.SP", feedback_tag="LOOP.SP", value=25.0)
    steps = [
        output,
        dict(
            block_for_type("wait_until").instantiate("actual_sp"),
            condition="LOOP.SP == 25",
            timeout_sec=5,
            poll_sec=0.1,
        ),
        block_for_type("complete").instantiate("done"),
    ]
    definition = ProcedureDefinition(
        AdvisoryProcedure.model_validate({
            "procedure_id": "checked_output",
            "name": "Checked output",
            "mode": "advisory",
            "tags": [{"tag": "LOOP.SP", "access": "read_write", "data_type": "float"}],
            "steps": steps,
        }),
        {"LOOP.SP": "PILOT/PID1/SP"},
    ).validate()
    session = window.procedure_session()
    session.select(definition)
    runtime.scan_count += 1
    session.poll()
    runtime.scan_count += 1
    session.poll()
    session.start()

    def pump():
        # The fixture advances scan identity without running the controller
        # executive; mirror the PID's accepted local target as scan readback.
        pid.outputs["SP"].value = pid.inputs["SP"].value
        runtime.scan_count += 1
        caps["sim_time"] += 0.1
        session.poll()
        app.processEvents()

    wait_for(lambda: session.prompt is not None, pump)
    assert session.prompt["prompt_kind"] == "output"
    assert session.prompt["path"] == "PILOT/PID1/SP"
    session.execute("respond", session.token(), True)
    wait_for(lambda: bool(session.last_finished), pump)
    assert session.last_finished == "COMPLETE", [
        (row["event_type"], row["message"], row["data_json"])
        for row in session.run.store.run_events(session.run.run_id)
    ]
    assert pid.inputs["SP"].value == 25.0
    events = {row["event_type"] for row in session.run.store.run_events(session.run.run_id)}
    assert {"PCS_OUTPUT_CONFIRMATION", "PCS_OUTPUT_APPLIED"} <= events
    assert "ADVISORY_WRITE_PROPOSAL" not in events


def test_hmi_blocks_open_published_target_and_raise_shared_procedure_alarm(station):
    from azeo_control_trainer.core.procedures.library import block_for_source, block_for_type

    app, window, runtime, caps, _pid = station
    definition = ProcedureDefinition(
        AdvisoryProcedure.model_validate({
            "procedure_id": "hmi_blocks",
            "name": "HMI blocks",
            "mode": "advisory",
            "steps": [
                block_for_source("message.hmi_window").instantiate("open_pilot")
                | {"hmi_target": "Pilot"},
                block_for_source("message.hmi_alarm").instantiate("notify_operator")
                | {"description": "Procedure condition requires attention"},
                block_for_type("complete").instantiate("done"),
            ],
        }),
        {},
    ).validate()
    session = window.procedure_session()
    session.select(definition)
    runtime.scan_count += 1
    session.poll()
    runtime.scan_count += 1
    session.poll()
    session.start()

    def pump():
        runtime.scan_count += 1
        caps["sim_time"] += 0.1
        session.poll()
        app.processEvents()

    wait_for(lambda: session.prompt is not None, pump)
    assert session.prompt["prompt_kind"] == "hmi_window"
    assert session.open_hmi_target(session.prompt["target"])
    session.execute("respond", session.token(), True)
    wait_for(lambda: bool(session.last_finished), pump)
    assert session.last_finished == "COMPLETE"
    records = window.alarm_state.records(modules=("PROCEDURE",))
    assert len(records) == 1
    assert "Procedure condition requires attention" in records[0].condition
    assert not records[0].active and not records[0].acknowledged


def test_hidden_or_closed_procedure_view_does_not_abort_prompt(station):
    from PySide6.QtWidgets import QDialog
    app, window, runtime, caps, _pid = station
    window.set_workspace_option("dock_context", True)
    dialog = window.open_procedures()
    runtime.scan_count += 1
    dialog.poll()
    dialog.start_run()
    wait_for(lambda: dialog._prompt is not None, lambda: (dialog.poll(), app.processEvents()))
    window._open_workspace_tool("another", "Another tool", lambda: QDialog(window))
    app.processEvents()
    assert dialog.timer.isActive()
    dialog.close()
    assert dialog.run.active
    reopened = window.open_procedures()
    assert reopened is not dialog
    assert reopened.run is dialog.run
    assert reopened._prompt == window.procedure_session().prompt
    window.close()
    wait_for(lambda: bool(getattr(window, "_closing", False)), app.processEvents)
    assert not dialog.run.active
    assert dialog.run.store.get_run(dialog.run.run_id)["status"] == "ABORTED"


@pytest.mark.parametrize("change", ["quality", "rewind", "offline", "configuration"])
def test_lost_evidence_aborts_even_while_waiting_for_operator(station, change):
    app, window, runtime, caps, pid = station
    caps["sim_time"] = 10
    dialog = window.open_procedures()
    runtime.scan_count += 1
    dialog.poll()
    dialog.start_run()
    wait_for(lambda: dialog._prompt is not None, lambda: (dialog.poll(), app.processEvents()))
    if change == "quality":
        pid.outputs["PV"].status = Quality.BAD
    elif change == "rewind":
        caps["sim_time"] = 0
    elif change == "offline":
        runtime.is_online = False
    else:
        runtime.compiled = SimpleNamespace(graph=runtime.compiled.graph)
    wait_for(lambda: dialog._last_finished == "ABORTED", lambda: (dialog.poll(), app.processEvents()))


def test_simulation_clock_freezes_during_host_and_operator_pause():
    clock = SimulationControl()
    clock.observe(10)
    clock.observe(12)
    assert clock.adjusted_monotonic() == 2
    clock.pause()
    clock.observe(40)
    assert clock.adjusted_monotonic() == 2
    clock.resume()
    clock.observe(41, paused=True)
    clock.observe(60, paused=True)
    assert clock.adjusted_monotonic() == 3
    clock.observe(60, paused=False)
    clock.observe(63)
    assert clock.adjusted_monotonic() == 6


def test_faceplate_construction_gap_is_excluded_from_observed_dwell():
    control = SimulationControl()
    control.observe(100)
    control.observe(102)
    epoch = control.observation_epoch
    control.suspend_for_presentation()
    assert control.host_paused
    assert control.observation_epoch > epoch
    control.observe(115, paused=True)
    control.observe(116, paused=False)
    assert control.adjusted_monotonic() == 2
    control.observe(117)
    assert control.adjusted_monotonic() == 3


def test_long_simulator_pause_resumes_only_after_new_scan(station, monkeypatch):
    import azeo_control_trainer.azeo_operator_station.procedure_observations as bridge
    _app, window, runtime, caps, _pid = station
    now = [100.0]
    monkeypatch.setattr(bridge.time, "time", lambda: now[0])
    observer = bridge.ProcedureObservations(window)
    bindings = {"PV": "PILOT/PID1/PV"}
    assert not observer.capture(bindings).ready
    runtime.scan_count += 1
    assert observer.capture(bindings).ready
    caps["paused"] = True
    observer.capture(bindings)
    now[0] += 120
    paused = observer.capture(bindings)
    assert paused.paused and not paused.fault
    caps["paused"] = False
    waiting = observer.capture(bindings)
    assert waiting.paused and not waiting.ready and not waiting.fault
    runtime.scan_count += 1
    assert observer.capture(bindings).ready


def test_source_freshness_does_not_refresh_when_only_ui_updates(station, monkeypatch):
    import azeo_control_trainer.azeo_operator_station.procedure_observations as bridge
    _app, window, runtime, _caps, _pid = station
    now = [100.0]
    monkeypatch.setattr(bridge.time, "time", lambda: now[0])
    observer = bridge.ProcedureObservations(window)
    observer.capture({"PV": "PILOT/PID1/PV"})
    runtime.scan_count += 1
    first = observer.capture({"PV": "PILOT/PID1/PV"})
    now[0] += 60
    later = observer.capture({"PV": "PILOT/PID1/PV"})
    assert later.samples["PV"].timestamp == first.samples["PV"].timestamp
    assert later.fault.startswith("No new scan")


def test_controller_debug_pause_freezes_procedure_even_if_plant_keeps_running(station):
    from azeo_control_trainer.azeo_operator_station.procedure_observations import ProcedureObservations
    _app, window, runtime, _caps, _pid = station
    observer = ProcedureObservations(window)
    bindings = {"PV": "PILOT/PID1/PV"}
    observer.capture(bindings)
    runtime.scan_count += 1
    assert observer.capture(bindings).ready
    runtime.is_debug_paused = True
    observation = observer.capture(bindings)
    assert observation.paused and not observation.ready and not observation.fault
    runtime.is_debug_paused = False
    assert observer.capture(bindings).paused
    runtime.scan_count += 1
    assert observer.capture(bindings).ready


def test_counter_reset_removes_evidence_until_another_scan(station):
    from azeo_control_trainer.azeo_operator_station.procedure_observations import ProcedureObservations
    _app, window, runtime, _caps, _pid = station
    observer = ProcedureObservations(window)
    bindings = {"PV": "PILOT/PID1/PV"}
    observer.capture(bindings)
    runtime.scan_count += 1
    assert observer.capture(bindings).ready
    runtime.scan_count = 0
    reset = observer.capture(bindings)
    assert reset.fault and not reset.samples["PV"].timestamp


def test_training_session_receives_run_identity(station):
    app, window, runtime, _caps, _pid = station
    events = []
    session = SimpleNamespace(active=True, identity="exercise-123",
                              event=lambda *args: events.append(args), finish=lambda **kwargs: None)
    window.training_session = session
    dialog = window.open_procedures()
    runtime.scan_count += 1
    dialog.poll()
    dialog.start_run()
    wait_for(lambda: dialog._prompt is not None, lambda: (dialog.poll(), app.processEvents()))
    assert any(row[0] == "procedure_started" and row[1] == dialog.run.run_id for row in events)
    session.identity = "restarted-exercise"
    wait_for(lambda: dialog._last_finished == "ABORTED", lambda: (dialog.poll(), app.processEvents()))
    window.training_session = None


def test_procedure_engine_is_first_party_code():
    """The engine lives inside the package; no top-level copy shadows it."""
    import importlib.util
    from azeo_control_trainer.core import pa_designer as engine
    root = Path(__file__).resolve().parents[1] / "src/azeo_control_trainer/core/pa_designer"
    assert Path(engine.__file__).resolve().parent == root
    assert not (root / "IMPORT_MANIFEST.json").exists()
    assert importlib.util.find_spec("pa_designer") is None


def definition_for(steps, *, writable=False):
    procedure = AdvisoryProcedure.model_validate({
        "procedure_id": "test", "name": "Test", "mode": "advisory",
        "tags": [{"tag": "LOOP.PV", "access": "read_write" if writable else "read", "data_type": "float"}],
        "steps": steps,
    })
    return ProcedureDefinition(procedure, {"LOOP.PV": "PILOT/PID1/PV"}).validate()


def test_write_proposal_cannot_complete_without_actual_readback(tmp_path):
    proposal = {"id": "proposal", "type": "write_tag", "tag": "LOOP.PV", "value": 25.0}
    with pytest.raises(ValueError, match="subsequent actual-value wait"):
        definition_for([proposal], writable=True)
    definition = definition_for([proposal, {"id": "actual", "type": "wait_until",
        "condition": "LOOP.PV == 25", "poll_sec": 0.1, "timeout_sec": 2}], writable=True)
    run = ProcedureRun(tmp_path / "history.sqlite")
    try:
        run.observe(0, {"LOOP.PV": TagValue("LOOP.PV", 20.0)})
        run.start(definition, actor="Test")
        events = []
        wait_for(lambda: any(event["kind"] == "progress" for event in events),
                 lambda: events.extend(run.drain()))
        assert run.active
        assert run.connector.read_value("LOOP.PV").value == 20.0
        run.observe(3, {"LOOP.PV": TagValue("LOOP.PV", 20.0)})
        wait_for(lambda: not run.active)
        assert run.result.status.value == "FAILED"
        assert any(row["event_type"] == "ADVISORY_WRITE_PROPOSAL" for row in run.store.run_events(run.run_id))
    finally:
        run.close()


def test_close_waits_for_a_slow_final_audit_commit(tmp_path, monkeypatch):
    definition = definition_for([{"id": "confirm", "type": "operator_confirm",
                                  "description": "Confirm the condition."}])
    run = ProcedureRun(tmp_path / "history.sqlite")
    finish = run.store.finish_run
    def slow_finish(*args, **kwargs):
        time.sleep(2.1)
        return finish(*args, **kwargs)
    monkeypatch.setattr(run.store, "finish_run", slow_finish)
    run.observe(0, {"LOOP.PV": TagValue("LOOP.PV", 20.0)})
    events = []
    try:
        run.start(definition, actor="Test")
        wait_for(lambda: any(event["kind"] == "prompt" for event in events),
                 lambda: events.extend(run.drain()))
        assert run.close()
        assert not run.active
        assert run.store.get_run(run.run_id)["status"] == "ABORTED"
    finally:
        run.close()


def test_dwell_resets_after_out_of_band_value_and_uses_accelerated_time(tmp_path):
    definition = definition_for([{"id": "stable", "type": "wait_until", "condition": "LOOP.PV == 20",
                                  "stable_for_sec": 5, "timeout_sec": 30, "poll_sec": 0.1}])
    run = ProcedureRun(tmp_path / "history.sqlite")
    events = []
    def observe(stamp, value):
        run.observe(stamp, {"LOOP.PV": TagValue("LOOP.PV", value)})
        wait_for(lambda: any(event["kind"] == "progress" and event["sim_time"] == stamp for event in events),
                 lambda: events.extend(run.drain()))
    try:
        run.observe(0, {"LOOP.PV": TagValue("LOOP.PV", 20.0)})
        run.start(definition, actor="Test")
        observe(0, 20)
        observe(4, 20)
        assert run.active
        observe(5, 21)
        observe(6, 20)
        observe(10, 20)
        assert run.active
        observe(11, 20)
        wait_for(lambda: not run.active)
        assert run.result.status.value == "COMPLETE"
    finally:
        run.close()


def test_template_is_valid_without_sibling_dcs_or_desktop():
    definition = loop_verification("PILOT/PID1", 20, 1, 10)
    assert len(definition.procedure.steps) == 6
    assert not any(name == "azeo" or name.startswith("azeo.io") for name in sys.modules)
