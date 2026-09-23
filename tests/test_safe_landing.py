"""Plant shutdown guidance keeps process authority with the operator."""
# ruff: noqa: E402
import os
from pathlib import Path
import sys
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]

from azeo_control_trainer.core.hmi.binding.source import LiveGraphSource
from azeo_control_trainer.core.strategy.blocks.io_blocks import DOBlock
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph


def test_discrete_manual_output_uses_operator_handle_and_preserves_mode_authority():
    graph = StrategyGraph("DEVICE")
    block = DOBlock("COMMAND")
    graph.add_block(block)
    source = LiveGraphSource(lambda: {"DEVICE": graph})
    block.set_mode("MAN")
    block.execute(.1)
    assert source.write("DEVICE/COMMAND/OUT_D", True).success
    block.execute(.1)
    assert block.outputs["OUT_D"].value is True
    assert source.write("DEVICE/COMMAND/OUT_D", "false").success
    block.execute(.1)
    assert block.outputs["OUT_D"].value is False
    assert not source.write("DEVICE/COMMAND/OUT_D", "not-a-boolean").success
    assert not source.write("DEVICE/COMMAND/OUT_D.TARGET", True).success
    assert not source.write("DEVICE/COMMAND/OUT_D.ST", True).success
    block.set_mode("CAS")
    block.execute(.1)
    assert not source.write("DEVICE/COMMAND/OUT_D", True).success


def test_condition_groups_keep_nested_logic_and_show_unknown():
    from azeo_control_trainer.core.procedures.conditions import evaluate_conditions
    results = {"P.A": True, "P.B or P.C": False}
    passed, match, rows = evaluate_conditions("P.A and (P.B or P.C)", results.__getitem__)
    assert not passed and match == "ALL"
    assert [row["state"] for row in rows] == ["Satisfied", "Waiting"]
    passed, match, rows = evaluate_conditions("P.A or P.B", {"P.A": True, "P.B": None}.__getitem__)
    assert not passed and match == "ANY"
    assert rows[1]["state"] == "Uncertain"


@pytest.mark.parametrize("interruption", ["pause", "uncertain", "false"])
def test_multiple_conditions_require_fresh_continuous_dwell(tmp_path, interruption):
    from test_procedure_integration import definition_for, wait_for
    from azeo_control_trainer.core.procedures.runtime import ProcedureRun
    from azeo_control_trainer.core.pa_designer.connectors.tag_value import TagValue
    recipe = definition_for([dict(id="prove", type="wait_until",
        condition="LOOP.PV >= 15 and (LOOP.PV == 20 or LOOP.PV == 21)",
        stable_for_sec=5, timeout_sec=40, poll_sec=.1, on_timeout="hold")])
    run = ProcedureRun(tmp_path / "history.sqlite")
    events = []

    def observe(stamp, value=20, quality="Good"):
        run.observe(stamp, {"LOOP.PV": TagValue("LOOP.PV", value, quality)})
        wait_for(lambda: any(e["kind"] == "progress" and e["sim_time"] == stamp for e in events),
                 lambda: events.extend(run.drain()))

    try:
        run.observe(0, {"LOOP.PV": TagValue("LOOP.PV", 20)})
        run.start(recipe, actor="Test")
        observe(0)
        observe(4)
        if interruption == "pause":
            run.control.pause()
            run.observe(5, {"LOOP.PV": TagValue("LOOP.PV", 20)})
            run.control.resume()
        elif interruption == "uncertain":
            observe(5, quality="Uncertain")
            assert events[-1]["conditions"][0]["state"] == "Uncertain"
        else:
            observe(5, value=16)
        observe(6)
        assert run.active
        observe(10)
        assert run.active
        observe(11)
        wait_for(lambda: not run.active)
        assert run.result.status.value == "COMPLETE"
        assert len(next(e for e in events if e["kind"] == "progress")["conditions"]) == 2
    finally:
        run.close()


def test_uncertain_condition_times_out_to_held_without_using_its_value(tmp_path):
    from test_procedure_integration import definition_for, wait_for
    from azeo_control_trainer.core.procedures.runtime import ProcedureRun
    from azeo_control_trainer.core.pa_designer.connectors.tag_value import TagValue
    recipe = definition_for([dict(id="prove", type="wait_until", condition="LOOP.PV == 20",
                                  stable_for_sec=5, timeout_sec=6, poll_sec=.1, on_timeout="hold")])
    run = ProcedureRun(tmp_path / "history.sqlite")
    events = []
    try:
        run.observe(0, {"LOOP.PV": TagValue("LOOP.PV", 20)})
        run.start(recipe, actor="Test")
        wait_for(lambda: any(e["kind"] == "progress" for e in events), lambda: events.extend(run.drain()))
        run.observe(7, {"LOOP.PV": TagValue("LOOP.PV", 20, "Uncertain")})
        wait_for(lambda: not run.active)
        assert run.result.status.value == "HELD"
    finally:
        run.close()


def test_frozen_condition_reads_one_scan_without_blocking_new_observations():
    from azeo_control_trainer.core.procedures.runtime import ObservationConnector, SimulationControl
    from azeo_control_trainer.core.pa_designer.connectors.tag_value import TagValue
    connector = ObservationConnector(SimulationControl())
    connector.update({"A": TagValue("A", 1), "B": TagValue("B", 2)})
    with connector.frozen(connector.snapshot_values()):
        connector.update({"A": TagValue("A", 3), "B": TagValue("B", 4)})
        assert connector.read_value("A").value == 1
        assert connector.read_value("B").value == 2
    assert connector.read_value("B").value == 4


def test_windows_audit_anchor_retries_transient_lock_and_fails_persistent_lock(tmp_path, monkeypatch):
    from azeo_control_trainer.core.procedures.audit import ProcedureStore
    import azeo_control_trainer.core.pa_designer.storage.sqlite_store as storage
    store = ProcedureStore(tmp_path / "history.sqlite")
    original = storage.os.replace
    attempts = []

    def transient(source, target):
        attempts.append(target)
        if len(attempts) <= 2:
            raise PermissionError("Windows sharing violation")
        return original(source, target)

    monkeypatch.setattr(storage.os, "replace", transient)
    monkeypatch.setattr(storage.time, "sleep", lambda _: None)
    store._write_audit_anchor(0, "")
    assert len(attempts) == 3
    monkeypatch.setattr(storage.os, "replace", lambda *_: (_ for _ in ()).throw(PermissionError("locked")))
    with pytest.raises(PermissionError):
        store._write_audit_anchor(1, "not published")


def test_multi_condition_audit_commits_once_and_preserves_every_signed_read(tmp_path, monkeypatch):
    from azeo_control_trainer.core.procedures.audit import ProcedureStore
    store = ProcedureStore(tmp_path / "history.sqlite")
    store.start_run("batch", "test", "Batch", "RUNNING")
    anchors = []
    original = store._write_audit_anchor
    def capture(*args):
        anchors.append(args)
        return original(*args)
    monkeypatch.setattr(store, "_write_audit_anchor", capture)
    with store.observation_batch():
        for index in range(40):
            store.log_read("batch", f"TAG{index}", index)
    assert len(anchors) == 1
    assert len(store.run_reads("batch")) == 40
    assert store.verify_audit_chain()[0]
    with pytest.raises(ValueError):
        with store.observation_batch():
            store.log_read("batch", "rolled_back", 999)
            raise ValueError("evaluation failed")
    assert len(store.run_reads("batch")) == 40
    assert store.verify_audit_chain()[0]


def test_native_plant_safe_landing_end_to_end_with_manual_takeover(tmp_path):
    from plant_shutdown_harness import plant_controller, run_guidance, PROJECT
    from plant_safe_landing import definition, stages, route_index
    from azeo_control_trainer.core.procedures.runtime import ProcedureRun
    recipe = definition(PROJECT)
    assert all(step.type != "write_tag" for step in recipe.procedure.steps)
    assert all(tag.access == "read" for tag in recipe.procedure.tags)
    events = []
    with plant_controller() as (product, graphs, runtimes, source, advance):
        assert product._core == "cpp"
        assert len(runtimes) == 160
        run = ProcedureRun(tmp_path / "plant.sqlite")
        routes = route_index(PROJECT)
        def prompt(event, advance_observing):
            key = event["step"]["id"].removesuffix("_operate")
            if key == "upstream":
                product.set_simulation_disturbance("MF-031", True, 0)
            if key == "rotating":
                run.control.pause()
            for stage in stages():
                if stage.key == key:
                    for tag, value in stage.actions:
                        path, kind = routes[tag]
                        assert source.write(path + "/MODE.TARGET", "MAN").success
                        result = source.write(path + ("/OUT" if kind == "AO" else "/OUT_D"), value)
                        assert result.success, (tag, result)
            if key == "rotating":
                advance_observing(30)
                path, _ = routes["XS-P501A-RUN"]
                assert source.read(path + "/PV_D").value is False
                assert run.active and run.control.is_paused
                run.control.resume()
        try:
            seen = run_guidance(recipe, run, source, product, advance, on_prompt=prompt, on_event=events.append)
            assert run.result.status.value == "COMPLETE", run.result
            assert "handover" in seen
            progress = [event for event in events if event["kind"] == "progress" and event["step"] == "hot_hold_prove"]
            assert progress[-1]["stable"] >= 30
            assert all(row["state"] == "Satisfied" for row in progress[-1]["conditions"])
            assert run.store.verify_audit_chain()[0]
            assert not run.store.run_writes(run.run_id)
        finally:
            run.close()


def test_published_plant_screens_are_reachable_and_keep_manual_commands_independent():
    import json
    from plant_safe_landing import MONITOR, WORKFLOW, REFERENCE, definition, route_index, stages
    from plant_shutdown_graphics import control_name
    from azeo_control_trainer.core.procedures.model import load_definition
    from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore
    project = ROOT / "projects/AzeoPlantVirtualController"
    root = project / "displays/pvm"
    store = DisplayStore(root)
    recipe = load_definition(project / "procedures" / REFERENCE, project / "procedures")
    assert recipe.bindings == definition(project).bindings
    assert recipe.procedure.steps == definition(project).procedure.steps
    hierarchy = json.loads((root / "_display_sets.json").read_text(encoding="utf-8"))
    def names(rows):
        return [name for row in rows for name in (row["display"], *names(row.get("children", [])))]
    reachable = set(names(hierarchy[0]["hierarchy"]))
    assert {MONITOR, WORKFLOW} <= reachable
    document = store.revision_document(MONITOR, store.history(MONITOR)[-1]["rev"])
    order = document["stacking_order"]
    # Opaque cards previously covered both vessels and AI readouts at runtime.
    for item in document["items"]:
        if item["id"].endswith("_panel"):
            assert all(order.index(item["id"]) < order.index(pvm["id"]) for pvm in document["pvms"])
    routes = route_index(project)
    for stage in stages():
        if not stage.actions:
            continue
        name = control_name(stage)
        assert name in reachable
        document = store.revision_document(name, store.history(name)[-1]["rev"])
        entries = [item for item in document["items"] if item["kind"] == "user_entry"]
        for tag, value in stage.actions:
            path, kind = routes[tag]
            apply = next(item for item in entries if item["entry"].get("path") == path + ("/OUT" if kind == "AO" else "/OUT_D"))
            assert apply["entry"]["value"] == value
            assert "command_context" not in apply and "props" not in apply
            assert any(item["entry"].get("path") == path + "/MODE.TARGET" and item["entry"]["value"] == "MAN" for item in entries)
