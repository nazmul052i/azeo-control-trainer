"""Training workflows must be repeatable, driven, and usable from the UI."""
from __future__ import annotations

import copy
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from azeo_control_trainer.core.hmi.binding.alarm_state import RuntimeAlarmRegistry
from azeo_control_trainer.core.hmi.binding.result import BindingResult, UNRESOLVED
from azeo_control_trainer.core.hmi.history.analysis import response_metrics
from azeo_control_trainer.core.hmi.pvms.engineering import (
    check_case, control_roots, document_digest, remap_controls, starter_assemblies,
)
from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore, PvmDisplay
from azeo_control_trainer.core.hmi.pvms.instances import TemplateStore
from azeo_control_trainer.core.simulation.training import Exercise, SessionArchive, TrainingSession
from azeo_control_trainer.core.strategy.model.terminal import Quality
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
from azeo_control_trainer.core.strategy.blocks.pid_block import PIDBlock
from azeo_control_trainer.core.strategy.blocks.io_blocks import AIBlock, AOBlock
from azeo_control_trainer.azeo_graphics_designer.window import HmiStudioWindow
from azeo_control_trainer.azeo_graphics_designer.engineering_tools import AssemblyDialog, CommissioningDialog
from azeo_control_trainer.azeo_graphics_designer.studio.preview import PreviewSource
from azeo_control_trainer.azeo_operator_station.console import LiveStation
from azeo_control_trainer.azeo_operator_station.deployment import PvmDeployment
from test_simulation_workbench import _service


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class Source:
    def __init__(self):
        self.alarm_state = RuntimeAlarmRegistry()
        self.values = {"M100/PID/PV": 48, "M100/PID/SP": 50, "M100/PID/OUT": 30,
                       "M100/PID/INTERLOCK": True}

    def read(self, path):
        if path not in self.values:
            return UNRESOLVED
        return BindingResult(value=self.values[path], quality=Quality.GOOD,
                             mode_actual="AUTO", mode_target="AUTO")


def test_response_metrics_measure_known_response_and_reject_gaps():
    rows = [{"time": t, "pv": pv, "sp": 10, "quality": "GOOD"}
            for t, pv in ((0, 0), (1, 8), (2, 12), (3, 10), (8, 10))]
    result = response_metrics(rows, tolerance=0.5)
    assert result["overshoot"] == 2
    assert result["settling_seconds"] == 3
    assert result["iae"] == 9
    rows[2]["quality"] = "BAD"
    result = response_metrics(rows, tolerance=0.5)
    assert result["settling_seconds"] is None
    assert result["iae"] == 6  # no integration through the outage
    rows[2]["quality"] = "GOOD"
    rows[3]["sp"] = 11
    assert response_metrics(rows)["settling_seconds"] is None


def test_timed_shelving_expires_and_reannunciates_without_losing_onset():
    clock = [100.0]
    registry = RuntimeAlarmRegistry(clock=lambda: clock[0])
    registry.observe("M", "AI", [("HI", 11, 80)])
    key = "M/AI/HI"
    registry.shelve(key, 60, "Instrument check")
    assert registry.records()[0].suppressed
    assert registry.block_summary("M", "AI")["active"] is False
    clock[0] = 161
    row = registry.records()[0]
    assert not row.suppressed and not row.acknowledged
    assert row.raised_at == 100
    assert [r["action"] for r in registry.events] == ["raised", "shelved", "shelf_expired"]
    registry.observe("M", "AI", [])
    returned = row.changed_at
    clock[0] = 170
    registry.observe("M", "AI", [])
    assert row.changed_at == returned


@pytest.mark.parametrize("seconds,reason", [(float("nan"), "check"), (0, "check"), (60, "")])
def test_invalid_shelf_does_not_mutate_alarm(seconds, reason):
    registry = RuntimeAlarmRegistry()
    registry.observe("M", "AI", [("HI", 11)])
    with pytest.raises(ValueError):
        registry.shelve("M/AI/HI", seconds, reason)
    assert not registry.records()[0].suppressed


def test_session_restores_faults_and_persists_samples_events_and_review(tmp_path):
    workbench, ai, _ = _service(tmp_path)
    source = Source()
    session = TrainingSession(workbench, source, source.alarm_state, tmp_path / "training")
    baseline = session.capture_baseline()
    exercise = Exercise(loop="M100/PID", input_path="M100/AI-101", snapshot=str(baseline))
    session.save_exercise(exercise)
    session.start(session.exercises()[0])
    original = copy.deepcopy(ai.config.params)
    session.inject_input("M100/AI-101", 99, "BAD")
    assert ai.config.params["simulate_enabled"] is True
    source.alarm_state.observe("M100", "AI-101", [("HI", 11)])
    workbench.driver.sim_time += 3
    workbench.record_tag_write("M100/PID/SP", 51)
    session.tick()
    session.objective(0, True, "PV and SP checked on the trend")
    identity = session.finish("Recovered the loop")
    assert ai.config.params == original
    data = SessionArchive(session.archive.path).read(identity)
    assert len(data["samples"]) == 2
    assert any(row["action"] == "alarm_raised" for row in data["events"])
    assert any(row["action"] == "tag_write" for row in data["events"])
    assert data["objectives"]["0"]["complete"]
    assert "Recovered the loop" in session.report(identity)
    session.restart()
    assert session.identity != identity and session.active
    session.finish()


def test_fault_preflight_rejects_nonfinite_without_mutation(tmp_path):
    workbench, ai, _ = _service(tmp_path)
    source = Source()
    session = TrainingSession(workbench, source, source.alarm_state, tmp_path / "training")
    snapshot = session.capture_baseline()
    session.start(Exercise(loop="M100/PID", snapshot=str(snapshot)))
    before = copy.deepcopy(ai.config.params)
    with pytest.raises(ValueError):
        session.inject_input("M100/AI-101", float("inf"))
    assert ai.config.params == before
    session.finish()


def graphs():
    graph = StrategyGraph("UNIT")
    for block in (PIDBlock("PID1"), AIBlock("AI1"), AOBlock("AO1")):
        graph.add_block(block)
        for terminal in (*block.inputs.values(), *block.outputs.values()):
            terminal.status = Quality.GOOD
    return {graph.name: graph}


def test_mapping_validates_family_before_touching_document():
    document = starter_assemblies()["Control loop"]
    before = copy.deepcopy(document)
    mapping = {"LOOP/PID": "UNIT/PID1", "VALVE/AO": "UNIT/AO1"}
    result = remap_controls(document, mapping, graphs())
    assert result["pvms"][0]["params"]["path"] == "UNIT/PID1"
    assert document == before
    with pytest.raises(ValueError, match="requires PID"):
        remap_controls(document, {**mapping, "LOOP/PID": "UNIT/AI1"}, graphs())
    with pytest.raises(ValueError, match="existing control block"):
        remap_controls(document, {**mapping, "LOOP/PID": "MISSING/PID1"}, graphs())


def test_bulk_mapping_resolves_expression_and_config_paths_without_rewriting_labels():
    document = {"items": [{"id": "OLD/PID", "text": "OLD/PID/PV",
                           "props": {"fill": {"kind": "expression",
                            "expr": 'DLSYS["OLD/PID/PV"] > "OLD/PID/CONFIG/GAIN"'}}}]}
    assert control_roots(document) == ["OLD/PID"]
    result = remap_controls(document, {"OLD/PID": "UNIT/PID1"}, graphs())
    assert result["items"][0]["id"] == "OLD/PID"
    assert result["items"][0]["text"] == "OLD/PID/PV"
    assert result["items"][0]["props"]["fill"]["expr"] == 'DLSYS["UNIT/PID1/PV"] > "UNIT/PID1/CONFIG/GAIN"'
    document["items"][0]["path"] = "OLD/PID/CONFIG/nonexistent"
    with pytest.raises(ValueError, match="target parameter"):
        remap_controls(document, {"OLD/PID": "UNIT/PID1"}, graphs())


def test_mapping_preserves_units_and_updates_linked_class_choices():
    document = {"pvms": [{"params": {"path": "OLD/PID", "unit": "kg/h"}}],
                "items": [{"path": "OLD/PID/PV", "pvm_choices": {"ControlTag": "OLD/PID", "Unit": "kg/h"},
                           "instance_choices": {"ControlTag": "OLD/PID"}}]}
    assert control_roots(document) == ["OLD/PID"]
    result = remap_controls(document, {"OLD/PID": "UNIT/PID1"}, graphs())
    assert result["items"][0]["pvm_choices"] == {"ControlTag": "UNIT/PID1", "Unit": "kg/h"}
    assert result["items"][0]["instance_choices"]["ControlTag"] == "UNIT/PID1"
    graph_map = graphs()
    graph_map["UNIT"].set_module_parameter("LIMIT", 50)
    result = remap_controls({"items": [{"path": "OLD/PARAMETERS/LIMIT"}]},
                            {"OLD/PARAMETERS": "UNIT/PARAMETERS"}, graph_map)
    assert result["items"][0]["path"] == "UNIT/PARAMETERS/LIMIT"


def test_archives_close_database_handles_after_each_transaction(tmp_path):
    archive = SessionArchive(tmp_path / "training.sqlite")
    with archive.connect() as connection:
        connection.execute("SELECT 1")
    import sqlite3
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
    workbench, _, _ = _service(tmp_path)
    with workbench.journal._connect() as journal:
        journal.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        journal.execute("SELECT 1")


@pytest.fixture
def studio_window(tmp_path, app):
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path / "settings"))
    graph_map = graphs()
    window = HmiStudioWindow(lambda: graph_map, tmp_path / "displays")
    yield window
    window.close()
    window.deleteLater()
    app.processEvents()


def test_assembly_dialog_previews_inserts_and_undo_restores(studio_window):
    studio = studio_window.current()
    dialog = AssemblyDialog(studio)
    dialog.source.setCurrentIndex(1)
    mapping = {"LOOP/PID": "UNIT/PID1", "VALVE/AO": "UNIT/AO1"}
    for row in range(dialog.mapping.rowCount()):
        dialog.mapping.cellWidget(row, 1).setCurrentText(mapping[dialog.mapping.item(row, 0).text()])
    before = studio._document()
    dialog.preview()
    dialog.apply()
    assert len(studio._items()) == 2
    assert len(studio._undo_stack) == 1
    studio.undo()
    assert studio._document() == before
    dialog.close()


def test_selection_assembly_retains_internal_pipe_and_reloads(studio_window):
    studio = studio_window.current()
    a = studio.add_static("rect", 10, 10, 80, 60)
    b = studio.add_static("rect", 200, 10, 80, 60)
    pipe = studio.add_pipe(a, "e", b, "w")
    studio.selection.replace((a, b, pipe))
    dialog = AssemblyDialog(studio)
    dialog.name.setText("Transfer train")
    dialog.save_selection()
    record = TemplateStore(studio.store.root).entries["Assembly · Transfer train"].document
    assert len(record["items"]) == 3
    assert record["items"][-1]["kind"] == "pipe"
    assert len({item["group"] for item in record["items"]}) == 1
    dialog.close()


def test_commissioning_detects_wrong_expectation_and_restores_preview(studio_window):
    studio = studio_window.current()
    source = Source()
    preview = PreviewSource(source)
    case = {"path": "M100/PID/PV", "expected": {"quality": "BAD"}}
    preview.set_override(case["path"], quality="GOOD")
    preview.enabled = True
    assert not check_case(preview, case)[0]
    dialog = CommissioningDialog(studio)
    dialog.path.setText("UNIT/PID1/PV")
    dialog.add_cases()
    previous = copy.deepcopy(studio.preview_source.overrides)
    original_mode = studio.mode
    dialog.table.selectRow(0)
    dialog.preview_case()
    assert studio.test_mode and studio.writes_blocked
    dialog.restore_preview()
    assert studio.mode == original_mode
    assert studio.preview_source.overrides == previous
    dialog.save()
    document = studio._document()
    cases = PvmDisplay.from_dict(document).commissioning
    # A PID has no INTERLOCK terminal. Offering that case invented a broken
    # reference which could never be commissioned successfully.
    assert {case["name"] for case in cases} == {
        "Normal", "Alarm", "Bad quality", "Manual mode", "Communication loss"}
    dialog.close()


def test_commissioning_keeps_failed_checks_and_invalidates_after_canvas_edit(studio_window):
    from azeo_control_trainer.azeo_graphics_designer.studio.verification import findings_for_studio
    studio = studio_window.current()
    dialog = CommissioningDialog(studio)
    dialog.path.setText("UNIT/PID1/PV")
    dialog.add_cases()
    dialog.cases = dialog.cases[:1]
    dialog.cases[0]["expected"]["value"] = 99
    dialog.check_all()
    dialog.save()
    assert any(f.severity == "error" and "expected 99" in f.message for f in findings_for_studio(studio))
    old_digest = document_digest(studio._document())
    studio.add_static("rect", 10, 10, 50, 50)
    dialog.save()
    assert studio.display.commissioning[0]["check_digest"] == old_digest
    assert any("need to be rerun" in f.message for f in findings_for_studio(studio))
    dialog.close()


def test_test_navigation_does_not_expand_the_desktop(studio_window, app):
    import time
    from PySide6.QtTest import QTest
    studio_window._display_hierarchy = lambda: {"A long display name " + str(i): (1, "") for i in range(30)}
    studio_window._timer.stop()
    studio_window.current().enter_test()
    assert not studio_window.nav_bar.isHidden()
    studio_window._rebuild_nav()
    studio_window.resize(1000, 700)
    studio_window.show()
    app.processEvents()
    assert studio_window.width() == 1000
    # Showing the scroll area posts its child's layout request. One event
    # pass can finish before that request establishes the overflow range.
    deadline = time.monotonic() + 1.0
    while studio_window.nav_bar.horizontalScrollBar().maximum() == 0 \
            and time.monotonic() < deadline:
        QTest.qWait(10)
    assert studio_window.nav_bar.horizontalScrollBar().maximum() > 0


def test_live_tools_reach_real_dialogs_and_preserve_shared_services(tmp_path, app):
    workbench, _, _ = _service(tmp_path)
    graph_map = graphs()
    deployment = PvmDeployment(DisplayStore(tmp_path / "displays"))
    station = LiveStation(deployment, lambda: graph_map, simulation_service=workbench,
                          training_root=tmp_path / "training", config_root=tmp_path / "displays")
    training = station.open_training()
    assert training.session.workbench is workbench
    assert training.loop.count() == 1
    diagnosis = station.open_loop_diagnostics("UNIT/PID1")
    diagnosis.begin("baseline")
    diagnosis.refresh()
    workbench.driver.sim_time += 10
    diagnosis.end()
    assert diagnosis.metrics.rowCount() == 4
    alarms = station.open_alarm_investigation()
    assert alarms.summary.registry is station.alarm_state
    station.alarm_state.observe("UNIT", "PID1", [("HI", 11, 80)])
    alarms.summary.refresh()
    alarms.summary.table.selectRow(0)
    alarms.refresh()
    alarms.reason.setText("Training instrument check")
    assert alarms.shelve()
    assert station.alarm_state.records()[0].shelved_until > 0
    alarms.unshelve()
    assert not station.alarm_state.records()[0].suppressed
    station.set_local_user("observer", False)
    with pytest.raises(ValueError, match="view-only"):
        training.inject()
    assert not alarms.shelve()
    station.close()
    station.deleteLater()
    app.processEvents()


def test_loop_history_keeps_output_scale_separate_from_pv():
    from azeo_control_trainer.core.hmi.history.historian import ContinuousHistorian
    graph_map = graphs()
    pid = next(b for b in graph_map["UNIT"].blocks.values() if b.block_type == "PID")
    pid.config.params.update(pv_unit="Nm3/h", pv_scale_hi=10000)
    historian = ContinuousHistorian(None)
    historian.configure_from(None, list(graph_map.values()))
    assert historian.TAGS["UNIT/PID1/OUT"].unit == "%"
    assert historian.TAGS["UNIT/PID1/OUT"].hi == 100


def test_recording_retries_uncommitted_events_after_archive_failure(tmp_path, monkeypatch):
    workbench, _, _ = _service(tmp_path)
    source = Source()
    session = TrainingSession(workbench, source, source.alarm_state, tmp_path / "training")
    baseline = session.capture_baseline()
    session.start(Exercise(loop="M100/PID", snapshot=str(baseline)))
    source.alarm_state.observe("M100", "PID", [("HI", 11)])
    workbench.driver.sim_time += 3
    append = session.archive.append
    def fail(*args):
        raise OSError("Disk unavailable")
    monkeypatch.setattr(session.archive, "append", fail)
    with pytest.raises(OSError):
        session.tick()
    assert session._alarm_cursor == 0
    monkeypatch.setattr(session.archive, "append", append)
    session.tick()
    data = session.archive.read(session.identity)
    assert len(data["samples"]) == 2
    assert sum(row["action"] == "alarm_raised" for row in data["events"]) == 1
    session.finish()
