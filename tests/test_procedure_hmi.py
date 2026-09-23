"""Authored, published HMI surfaces operate one station-owned advisory session."""
# ruff: noqa: F811
import os
import time
from dataclasses import replace
from pathlib import Path
import json

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QImage, QPainter

from test_procedure_integration import station, wait_for  # noqa: F401
from azeo_control_trainer.azeo_graphics_designer.configurator.designer import PvmConfigDesigner
from azeo_control_trainer.core.hmi.pvms.configurator.model import PvmConfiguration
from azeo_control_trainer.core.hmi.pvms.publishing import PvmDisplay
from azeo_control_trainer.core.hmi.pvms.user_library import UserPvmLibrary
from azeo_control_trainer.core.procedures.hmi import reference, response_value
from azeo_control_trainer.core.hmi.theme.fonts import ensure_font_directory

ensure_font_directory()


def authored(station):
    app, window, runtime, caps, _ = station
    dialog = window.open_procedures()
    dialog.dwell.setValue(.2)
    ref = dialog.save_to_project().relative_to(dialog.library).as_posix()
    root = window.deployment.store.root
    designer = PvmConfigDesigner(root / "_pvmcfg", pvm_class="HP_C_Valve")
    assert designer.new_procedure_blueprint("Startup") is not None
    library = UserPvmLibrary(root)
    assert "Startup_PVM" in library.entries
    config = PvmConfiguration.load(root / "_pvmcfg" / "Startup_PVM.pvmcfg.json")
    assert not config.issues()
    assert config.property("ProcedureRef").ptype == "Procedure Reference"
    items = library.instantiate("Startup_PVM", 40, 40, config=config,
                                choices={"ProcedureRef": ref, "Title": "FLOW STARTUP"})
    document = PvmDisplay(name="Procedure HMI", width=640, height=360, items=items)
    window.deployment.store.save_draft(document)
    from azeo_control_trainer.azeo_graphics_designer.studio.assembler import PvmStudio
    studio = PvmStudio(window.graphs_provider, root, document.name)
    studio.enter_edit()
    findings = studio.verification_findings()
    assert not [f for f in findings if f.severity == "error"], findings
    studio.publish()
    studio.unsaved = False
    studio.close()
    assert window.show_display(document.name)
    dialog.close()
    designer.unsaved = False
    designer.close()
    runtime.scan_count += 1
    session = window.procedure_session()
    session.snapshot(ref)
    session.poll()
    window.view.refresh()
    app.processEvents()
    return window, session, ref


def find(view, key):
    return next(item for item in view.scene().items()
                if isinstance(getattr(item, "data", None), dict)
                and (item.data.get("entry", {}).get("label") == key or
                     item.data.get("rows_path", "").endswith("/" + key)))


def test_published_pvm_faceplate_detail_share_run_and_prompt(station, monkeypatch, tmp_path):
    app, _, runtime, caps, pid = station
    window, session, ref = authored(station)
    caller = find(window.view, "Procedure…")
    assert caller.activate()
    faceplate = window.user_faceplates[-1]
    runtime.scan_count += 1
    session.poll()
    faceplate.refresh()
    assert find(faceplate, "Start").data["enabled"]
    assert find(faceplate, "Start").activate(), window.command_feedback.text()

    def pump():
        runtime.scan_count += 1
        caps["sim_time"] += .1
        session.poll()
        app.processEvents()

    wait_for(lambda: session.prompt is not None, pump)
    faceplate.refresh()
    old_context = session.snapshot(ref)["CONTEXT"]
    assert find(faceplate, "Workflow / observations / events…").activate()
    detail = window.user_faceplates[-1]
    detail.refresh()
    table = find(detail, "STEPS")
    assert len(table.table_rows()) == len(session.definition.procedure.steps)
    assert any(row["state"] == "ACTIVE" for row in table.table_rows())
    assert session.run.active
    # Navigation reaches the installed process faceplate and leaves the worker alive.
    detail.toggle_pin()
    assert find(detail, "Equipment faceplate…").activate()
    assert window.faceplates
    pump()
    detail.refresh()
    from PySide6.QtWidgets import QGraphicsSceneContextMenuEvent
    event = QGraphicsSceneContextMenuEvent(QEvent.GraphicsSceneContextMenu)
    event.setPos(QPointF(40, 28 + 3 * 96 + 30))
    event.setScreenPos(detail.mapToGlobal(detail.mapFromScene(table.mapToScene(event.pos()))))
    table.contextMenuEvent(event)
    assert "Block Help" in [action.text() for action in table._row_menu.actions()]
    assert "Procedure trends…" in [action.text() for action in table._row_menu.actions()]
    table._row_menu.actions()[0].trigger()
    table._row_menu.hide()
    assert window._procedure_help.current_topic_key == "wait_until"
    window._procedure_help.close()
    # Confirm via the normal HMI action dispatcher, replacing only user input.
    from azeo_control_trainer.azeo_operator_station import procedure_actions
    monkeypatch.setattr(procedure_actions, "operator_response", lambda *_: (True, True))
    assert find(detail, "Respond to current prompt…").activate(), window.command_feedback.text()
    with pytest.raises(ValueError, match="changed"):
        session.execute("respond", old_context["token"], True, ref)
    detail.close()
    assert session.run.active
    assert session.timer.isActive()
    wait_for(lambda: session.prompt is not None, pump)
    assert session.prompt["prompt_kind"] == "comment"
    session.execute("respond", session.token(), "Observed through the published HMI.", ref)
    wait_for(lambda: session.last_finished == "COMPLETE", pump)
    assert pid.outputs["SP"].value == 20.0
    assert session.run.store.get_run(session.run.run_id)["status"] == "COMPLETE"
    assert session.snapshot(ref)["RESULT"] == "COMPLETE"


def test_view_only_and_stale_context_cannot_operate(station):
    window, session, ref = authored(station)
    state = session.snapshot(ref)
    window.settings = replace(window.settings, write_authority=False)
    assert session.snapshot(ref)["CAN_START"] is False
    with pytest.raises(ValueError, match="View-only"):
        session.execute("start", state["TOKEN"], ref=ref)
    assert not session.run.active
    assert not window.live_source.can_write(f"@procedure/{ref}/STATE").success
    assert not window.live_source.write(f"@procedure/{ref}/STATE", "Running").success


@pytest.mark.parametrize("bad", ["../outside.yaml", "C:/other.yaml", "/root.yaml", "a/../x.yaml", "a//x.yaml"])
def test_reference_cannot_escape_project_library(bad):
    with pytest.raises(ValueError):
        reference(bad)


def test_unavailable_runtime_cannot_escape_a_graphics_binding():
    from azeo_control_trainer.core.procedures.hmi import ProcedureSource
    from azeo_control_trainer.core.hmi.binding.result import UNRESOLVED
    def unavailable():
        raise ModuleNotFoundError("Procedure runtime dependency unavailable")
    source = ProcedureSource(object(), unavailable)
    assert source.read("@procedure/startup.yaml/STATE") is UNRESOLVED


@pytest.mark.parametrize("dtype,value", [("float", "nan"), ("float", "inf"), ("int", "1.5"), ("bool", "yes")])
def test_response_rejects_invalid_typed_values(dtype, value):
    prompt = {"prompt_kind": "input", "step": {"input_type": dtype}}
    with pytest.raises(ValueError):
        response_value(prompt, value)


@pytest.mark.parametrize("kind", ["confirm", "output", "hmi_window"])
def test_governed_actions_require_explicit_boolean_acceptance(kind):
    prompt = {"prompt_kind": kind, "step": {}}
    assert response_value(prompt, True) is True
    with pytest.raises(ValueError, match="explicit acceptance"):
        response_value(prompt, "true")


def test_dynamic_workflow_paging_is_bounded_and_clickable(station, monkeypatch):
    from collections import Counter
    from azeo_control_trainer.core.hmi.pvms.rendering.items import StaticItem
    paints = Counter()
    original_paint = StaticItem.paint
    def paint(item, *args):
        paints[item.data.get("id", "")] += 1
        return original_paint(item, *args)
    monkeypatch.setattr(StaticItem, "paint", paint)
    app, _, runtime, _, _ = station
    window, session, ref = authored(station)
    definition = session.prepare(ref)
    step = definition.procedure.steps[0]
    definition.procedure.steps = [step.model_copy(update={"id": f"step-{i}"}) for i in range(500)]
    session.poll()
    find(window.view, "Procedure…").activate()
    fp = window.user_faceplates[-1]
    fp.refresh()
    find(fp, "Workflow / observations / events…").activate()
    detail = window.user_faceplates[-1]
    detail.refresh()
    item = find(detail, "STEPS")
    assert len(item.table_rows()) == 500
    position = detail.mapFromScene(item.mapToScene(item.rect().bottomRight() + QPointF(-24, -12)))
    QTest.mouseClick(detail.viewport(), Qt.LeftButton, pos=position)
    assert item._table_offset > 0
    for _ in range(100):
        item.page_table()
    assert item._table_offset < 500
    batches = []
    components = []
    image = QImage(detail.viewport().size(), QImage.Format_ARGB32_Premultiplied)
    painter = QPainter(image)
    for _ in range(3):
        start = time.perf_counter()
        observed = refreshed = painted = 0
        for _ in range(20):
            tick = time.perf_counter()
            session.poll()
            observed += time.perf_counter() - tick
            tick = time.perf_counter()
            detail.refresh()
            refreshed += time.perf_counter() - tick
            tick = time.perf_counter()
            detail.viewport().render(painter, QPointF(0, 0).toPoint())
            painted += time.perf_counter() - tick
        batches.append((time.perf_counter() - start) / 20 * 1000)
        components.append({"observe": observed * 50, "refresh": refreshed * 50, "paint": painted * 50})
    painter.end()
    assert paints[item.data["id"]] < 8, "Unchanged workflow pages must retain their rendered text"
    assert min(batches) < 16.7, (batches, components, paints, item.cacheMode())
    metrics = os.environ.get("AZEO_HMI_CAPTURE_DIR")
    if metrics:
        folder = Path(metrics)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "performance.json").write_text(json.dumps({"500_step_observe_refresh_paint_ms": batches,
            "components_ms": components, "workflow_paints": paints[item.data["id"]]}), encoding="utf-8")
    app.processEvents()


def test_authored_surfaces_follow_live_theme_changes(station):
    app, _, _, _, _ = station
    window, session, ref = authored(station)
    window.set_workspace_option("dock_context", False)
    assert find(window.view, "Procedure…").activate()
    faceplate = window.user_faceplates[-1]
    faceplate.toggle_pin()
    faceplate.refresh()
    assert find(faceplate, "Workflow / observations / events…").activate()
    detail = window.user_faceplates[-1]
    from azeo_control_trainer.core.hmi.theme.tokens import THEMES
    from azeo_control_trainer.core.hmi.theme.roles import Role
    for theme in ("silver", "dark", "hpgray"):
        window.apply_theme(theme)
        session.poll()
        for view in (faceplate, detail):
            view.refresh()
            view.show()
            app.processEvents()
            surface = next(item for item in view.scene().items()
                           if isinstance(getattr(item, "data", None), dict) and item.data.get("fill_role"))
            assert surface.fill_colour().name().lower() == THEMES[theme][Role.SURFACE_PANEL].lower()
            assert view.palette_roles[Role.TEXT] == THEMES[theme][Role.TEXT]
            if os.environ.get("AZEO_HMI_CAPTURE_DIR"):
                folder = Path(os.environ["AZEO_HMI_CAPTURE_DIR"])
                folder.mkdir(parents=True, exist_ok=True)
                assert view.grab().save(str(folder / f"{theme}-{view.class_name}.png"))
    assert session.snapshot(ref)["CAN_START"]


def test_live_workflow_opens_from_faceplate_and_tracks_advanced_run(station):
    from azeo_control_trainer.core.procedures.templates import readiness_workflow
    from azeo_control_trainer.core.hmi.theme.tokens import THEMES
    from azeo_control_trainer.core.hmi.theme.roles import Role
    app, _, runtime, caps, _ = station
    station[1].set_workspace_option("dock_context", False)
    window, session, ref = authored(station)
    assert find(window.view, "Procedure…").activate()
    faceplate = window.user_faceplates[-1]
    faceplate.refresh()
    assert find(faceplate, "Locate step").activate()
    workspace = window.open_procedures()
    assert workspace.tabs.currentWidget() is workspace.workflow
    assert workspace.workflow.definition is session.prepare(ref)
    saved = readiness_workflow().save_revision(window.simulation_service.project_dir)
    advanced_ref = saved.relative_to(workspace.library).as_posix()
    definition = session.prepare(advanced_ref)
    session.select(definition, advanced_ref)
    workspace.show_workflow(advanced_ref)
    assert len(workspace.workflow.nodes) == len(definition.procedure.flow.nodes)
    assert len(workspace.workflow.edges) == len(definition.procedure.flow.edges)
    def pump():
        runtime.scan_count += 1
        caps["sim_time"] += .1
        session.poll()
        workspace.poll(observe=False)
        app.processEvents()
    pump()
    session.start()
    try:
        wait_for(lambda: session.prompt is not None, pump)
        assert workspace.workflow.locate(session.prompt["step"]["id"])
        assert "Response: review" in workspace.workflow.summary.text()
        workspace.resize(1100, 800)
        assert workspace.width() >= 900 and workspace.height() >= 650
        for theme in ("silver", "dark", "hpgray"):
            window.apply_theme(theme)
            workspace.workflow.update_state()
            workspace.workflow.fit()
            app.processEvents()
            assert workspace.workflow.view.backgroundBrush().color().name() == THEMES[theme][Role.SURFACE_BG].lower()
            folder = os.environ.get("AZEO_HMI_CAPTURE_DIR")
            if folder:
                Path(folder).mkdir(parents=True, exist_ok=True)
                assert workspace.grab().save(str(Path(folder) / f"{theme}-live-workflow.png"))
    finally:
        session.run.control.stop("Finished UI verification")
        wait_for(lambda: not session.run.active, pump)
        assert session.run.result.status.value == "ABORTED"
        assert not session.prompt


def test_every_blueprint_body_passes_graphics_designer_verification(station):
    window, _, ref = authored(station)
    from azeo_control_trainer.azeo_graphics_designer.studio.assembler import PvmStudio
    root = window.deployment.store.root
    library = UserPvmLibrary(root)
    for name in ("Startup", "Startup_Detail", "Startup_Conditions", "Startup_Tuning", "Startup_Trends", "Startup_History"):
        config = PvmConfiguration.load(root / "_pvmcfg" / f"{name}.pvmcfg.json")
        items = library.instantiate(name, 0, 0, config=config,
                                    choices={"ProcedureRef": ref, "Title": "Review"})
        doc = PvmDisplay(name="Verify " + name, width=1200, height=800, items=items)
        window.deployment.store.save_draft(doc)
        studio = PvmStudio(window.graphs_provider, root, doc.name)
        try:
            assert not [f for f in studio.verification_findings() if f.severity == "error"]
        finally:
            studio.unsaved = False
            studio.close()


def test_authored_tuning_conditions_trends_and_history_are_driven(station):
    from azeo_control_trainer.core.procedures.logic import MemoryValue
    from azeo_control_trainer.azeo_operator_station.procedure_tuning import ParameterEditor
    app, _, runtime, caps, _ = station
    window, session, ref = authored(station)
    model = session.prepare(ref)
    session.run.memory.create(dict(name="limit", data_type="float", value=10, min_value=1, max_value=20))
    model.procedure.variables.append(MemoryValue(name="limit", data_type="float", value=10,
        min_value=1, max_value=20, tag_path="MEMORY/limit/VALUE", operator_tuning="live", engineering_units="%"))
    session.digests[ref] = model.digest
    session.poll()
    assert find(window.view, "Procedure…").activate()
    fp = window.user_faceplates[-1]
    fp.refresh()
    fp.toggle_pin()
    assert find(fp, "Tuning").activate()
    detail = window.user_faceplates[-1]
    detail.refresh()
    table = find(detail, "PARAMETERS")
    assert table.table_rows()[0]["value"] == 10
    assert table.procedure_row_action("tune", table.table_rows()[0])
    editor = window.operator_dialogs[-1]
    assert isinstance(editor, ParameterEditor)
    editor.value.setText("3.5")
    editor.apply()
    assert session.pending(ref)["memory/limit"]["value"] == 3.5
    assert session.run.memory.read("MEMORY/limit/VALUE") == 10
    editor.value.setText("unfinished numeric entry")
    editor.value.setSelection(0, 10)
    pending, token = session.pending(ref).copy(), session.token()
    for theme in ("dark", "hpgray", "silver"):
        window.choose_theme(theme)
        assert editor.value.text() == "unfinished numeric entry"
        assert editor.value.selectedText() == "unfinished"
        assert session.pending(ref) == pending
        assert session.token() == token
        assert session.run.memory.read("MEMORY/limit/VALUE") == 10
    editor.apply("cancel")
    assert not session.pending(ref)
    editor.value.setText("3.5")
    editor.apply()
    editor.close()
    fp.refresh()
    assert find(fp, "Start").activate(), window.command_feedback.text()
    def pump():
        runtime.scan_count += 1
        caps["sim_time"] += .1
        session.poll()
        app.processEvents()
    wait_for(lambda: session.prompt is not None and session.parameter_values.get("memory/limit") == 3.5, pump)
    assert not session.pending(ref)
    with pytest.raises(ValueError, match="changed"):
        session.tune(ref, ("stale", "", 0), "memory/limit", 4., "live")
    with pytest.raises(ValueError):
        session.tune(ref, session.token(), "memory/limit", 21., "live")
    session.tune(ref, session.token(), "memory/limit", 4., "live")
    wait_for(lambda: session.parameter_values.get("memory/limit") == 4., pump)
    detail.refresh()
    assert find(detail, "Trends").activate()
    trends = window.user_faceplates[-1]
    trends.refresh()
    snapshot = session.trend_snapshot(ref)
    assert "MEMORY/limit/VALUE" in [pen["path"] for pen in snapshot["series"]]
    window.historian.collect(force=True)
    session._trend_cache.clear()
    snapshot = session.trend_snapshot(ref)
    memory = next(pen for pen in snapshot["series"] if pen["path"] == "MEMORY/limit/VALUE")
    assert memory["points"][-1][1] == 4
    assert snapshot["events"]
    assert find(trends, "Process History View…").activate(), window.command_feedback.text()
    assert window.process_history_views
    assert find(trends, "History").activate()
    history = window.user_faceplates[-1]
    history.refresh()
    assert find(history, "HISTORY").table_rows()[0]["run_id"] == session.run.run_id
    assert find(history, "Help").activate()
    assert window._procedure_help.current_topic_key == "operator_hmi"
    window.settings = replace(window.settings, write_authority=False)
    with pytest.raises(ValueError, match="View-only"):
        session.tune(ref, session.token(), "memory/limit", 4., "next_run")
    assert session.snapshot(ref)["CAN_TRENDS"] and session.snapshot(ref)["CAN_HISTORY"]
    assert not session.snapshot(ref)["CAN_TUNE"]
    assert session.run.store.verify_audit_chain()[0]


def test_faceplate_opens_with_its_entire_authored_body_visible(station):
    app, window, *_ = station
    authored(station)
    assert find(window.view, "Procedure…").activate()
    view = window.user_faceplates[-1]
    view.show()
    app.processEvents()
    assert view.viewport().width() >= view.display.width
    assert view.viewport().height() >= view.display.height
    assert view.horizontalScrollBar().maximum() == 0
    assert view.verticalScrollBar().maximum() == 0
