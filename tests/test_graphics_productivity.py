"""Interaction and engineering workflows use the live editor, with one undo model."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtCore import QPointF, QRectF, Qt, QSettings
from PySide6.QtWidgets import QApplication

from azeo_control_trainer.azeo_graphics_designer.window import HmiStudioWindow

APP = QApplication.instance() or QApplication([])


@pytest.fixture
def window(tmp_path):
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path / "settings"))
    window = HmiStudioWindow(lambda: {}, tmp_path)
    yield window
    window.close()
    window.deleteLater()
    APP.processEvents()


def test_snap_holds_same_edge_until_pointer_exits_release_distance(window):
    studio = window.current()
    studio.snap_enabled = False
    studio.smart_guides_enabled = True
    studio.set_zoom(100)
    drag = studio.canvas.pointer_drag
    drag.bounds = QRectF(0, 0, 40, 40)
    drag.targets = ([100, 106], [])
    assert drag._snap_delta(QPointF(59, 0), Qt.NoModifier).x() == 60
    assert drag._snap_delta(QPointF(65, 0), Qt.NoModifier).x() == 60
    assert drag._snap_delta(QPointF(72, 0), Qt.NoModifier).x() != 60
    assert drag._snap_delta(QPointF(59, 0), Qt.AltModifier).x() == 59


def test_background_panel_can_stop_blocking_pipe_routes_and_undo(window):
    from azeo_control_trainer.core.hmi.pvms.rendering.pipe_scene import scene_obstacles
    studio = window.current()
    panel = studio.add_static("round_rect", 100, 100, 800, 500)
    identity = panel.data["id"]
    assert identity in {row.ident for row in scene_obstacles(studio.canvas.scene())}
    studio.selection.replace((panel,), primary=panel)
    studio.pane.show_item(panel)
    studio.pane.item_fields["routing_obstacle"].setChecked(False)
    assert panel.data["routing_obstacle"] is False
    assert identity not in {row.ident for row in scene_obstacles(studio.canvas.scene())}
    studio.undo()
    assert identity in {row.ident for row in scene_obstacles(studio.canvas.scene())}


def test_repeated_snapped_position_does_not_route_again(window, monkeypatch):
    studio = window.current()
    studio.smart_guides_enabled = False
    box = studio.add_static("rect", 100, 100, 80, 60)
    drag = studio.canvas.pointer_drag
    drag.press(box, QPointF(140, 130), Qt.NoModifier)
    calls = []
    original = studio.reroute_pipes
    monkeypatch.setattr(studio, "reroute_pipes", lambda *a, **kw: (calls.append(1), original(*a, **kw)))
    drag.move(QPointF(180, 170), Qt.NoModifier)
    count = len(calls)
    drag.move(QPointF(180, 170), Qt.NoModifier)
    assert len(calls) == count
    drag.release()


def test_overlap_picker_reaches_lower_object_without_reordering(window):
    studio = window.current()
    a = studio.add_static("rect", 100, 100, 90, 70)
    b = studio.add_static("rect", 100, 100, 90, 70)
    studio.selection.replace((b,))
    before = studio._document()
    result = studio.canvas.cycle_overlap(studio.canvas.mapFromScene(QPointF(145, 135)))
    assert result is a
    assert studio._document() == before


def test_worksheet_applies_two_labels_once_and_refuses_stale_preview(window):
    from azeo_control_trainer.azeo_graphics_designer.worksheet import WorksheetDialog
    studio = window.current()
    a = studio.add_static("text", 10, 10, 90, 30)
    b = studio.add_static("text", 150, 10, 90, 30)
    studio.selection.replace((a, b))
    dialog = WorksheetDialog(studio)
    before = studio._document()
    count = len(studio._undo_stack)
    dialog.table.item(0, 3).setText("Feed")
    dialog.table.item(1, 3).setText("Product")
    assert dialog.preview()
    dialog.apply()
    assert len(studio._undo_stack) == count + 1
    assert {item.data["text"] for item in studio._static_items()} == {"Feed", "Product"}
    studio.undo()
    assert studio._document() == before
    dialog.scope.setCurrentIndex(1)
    dialog.table.item(0, 3).setText("Changed")
    assert dialog.preview()
    studio.add_static("rect", 300, 100, 50, 50)
    with pytest.raises(ValueError, match="changed"):
        dialog.apply()
    dialog.close()


def test_worksheet_rejects_missing_display_link_without_mutating(window):
    from azeo_control_trainer.azeo_graphics_designer.worksheet import WorksheetDialog
    studio = window.current()
    item = studio.add_static("display_link", 10, 10, 90, 30)
    item.data["target"] = "Missing display"
    studio.selection.replace((item,))
    before = studio._document()
    dialog = WorksheetDialog(studio)
    assert dialog.preview() is None
    assert not dialog.apply_button.isEnabled()
    assert studio._document() == before
    dialog.close()


def test_command_search_finds_other_ribbon_tabs_and_dispatches(window, monkeypatch):
    dialog = window.open_command_search()
    dialog.search.setText("sequences")
    assert dialog.list.count() == 1
    called = []
    monkeypatch.setattr(window, "dispatch", called.append)
    dialog.activate()
    assert called == ["engineering.sequences"]


def test_property_search_favorites_and_focus(window):
    studio = window.current()
    item = studio.add_static("rect", 10, 10, 90, 30)
    studio.pane.show_item(item)
    dialog = studio.pane.open_property_search()
    assert dialog.list.count() > 5
    dialog.search.setText("fill")
    assert dialog.list.count() > 0
    label = dialog.list.currentItem().text()
    dialog.toggle_favorite()
    assert label in dialog.favorites
    dialog.only_favorites.setChecked(True)
    assert all(dialog.list.item(i).text().startswith("★") for i in range(dialog.list.count()))
    dialog.activate()


def test_sequence_ramps_acknowledges_and_restores_without_writes(window, monkeypatch):
    from azeo_control_trainer.azeo_graphics_designer.sequence_tools import SequenceDialog
    from azeo_control_trainer.core.hmi.binding.result import BindingResult
    studio = window.current()
    source = studio.preview_source.source
    monkeypatch.setattr(source, "read", lambda *_: BindingResult(value=37))
    monkeypatch.setattr(source, "write", lambda *_: pytest.fail("Sequence attempted a process write"))
    studio.preview_source.set_override("KEEP/AI/PV", value=25)
    dialog = SequenceDialog(studio)
    dialog.path.setText("M/AI/PV")
    dialog.add_lifecycle()
    dialog.table.cellWidget(1, 5).setCurrentText("Ramp")
    dialog.step()
    dialog.apply_time(1)
    assert studio.preview_source.read("M/AI/PV").value == 70
    dialog.apply_time(4)
    actual = studio.preview_source.read("M/AI/PV")
    assert actual.alarm_active and actual.alarm_acked
    dialog.apply_time(10)
    from azeo_control_trainer.core.hmi.binding.result import UNRESOLVED
    assert studio.preview_source.read("M/AI/PV") is UNRESOLVED
    dialog.close()
    assert not studio.test_mode
    assert studio.preview_source.overrides == {"KEEP/AI/PV": {"value": 25}}


def test_sequence_validation_persistence_and_undo(window):
    from azeo_control_trainer.azeo_graphics_designer.sequence_tools import SequenceDialog
    from azeo_control_trainer.core.hmi.pvms.publishing import PvmDisplay
    studio = window.current()
    dialog = SequenceDialog(studio)
    dialog.path.setText("M/AI/PV")
    dialog.add_lifecycle()
    dialog.save()
    data = studio._document()
    assert PvmDisplay.from_dict(data).to_dict() == data
    assert len(data["test_sequences"][0]["steps"]) == 7
    studio.undo()
    assert not studio.display.test_sequences
    dialog.table.item(0, 0).setText("nan")
    with pytest.raises(ValueError):
        dialog.play()
    assert not studio.test_mode
    dialog.close()


def test_semantic_revision_review_highlights_nested_bindings_and_removed_items(window):
    from azeo_control_trainer.azeo_graphics_designer.revision_review import semantic_changes, RevisionReview
    changes = semantic_changes({"items": [{"id": "a", "actions": [{"path": "M/AI/PV"}]}, {"id": "b"}]},
                               {"items": [{"id": "a", "actions": [{"path": "M/AI/OUT"}]}]})
    assert any(row[:2] == ("a", "actions[0].path") for row in changes)
    assert any(row[:2] == ("b", "Removed") for row in changes)
    studio = window.current()
    item = studio.add_static("rect", 10, 10, 90, 30)
    dialog = RevisionReview(studio)
    assert len(dialog.views) == 2
    row = next(i for i, value in enumerate(dialog.changes) if value[0] == item.data["id"])
    dialog.table.selectRow(row)
    dialog.locate()
    assert studio.selection.snapshot().primary is item
    assert all(not view.isInteractive() for view in dialog.views)
    dialog.close()
    assert all(view._disposed for view in dialog.views)


def test_configuration_impact_detects_removed_instance_options(window):
    from azeo_control_trainer.azeo_graphics_designer.configurator.impact import choice_issues, affected_instances
    from azeo_control_trainer.core.hmi.pvms.configurator.model import PvmConfiguration, PvmProperty, PropertyGroup, Option
    before = PvmConfiguration("Pump", [PropertyGroup("Basic", [PvmProperty("Orientation", "Selection", default="Left", options=[Option("Left"), Option("Right")])])])
    after = PvmConfiguration("Pump", [PropertyGroup("Basic", [PvmProperty("Orientation", "Selection", default="Left", options=[Option("Left")])])])
    assert "Removed option" in choice_issues(before, after, {"Orientation": "Right"})[0]
    docs = {"Unit": {"items": [{"id": "a", "instance_definition": "Pump", "instance_id": "one", "instance_choices": {"Orientation": "Right"}},
                                 {"id": "b", "instance_definition": "Pump", "instance_id": "one"},
                                 {"id": "c", "instance_definition": "Pump", "instance_id": "two", "instance_link": "unlinked"}]}}
    assert affected_instances("Pump", docs) == [("Unit", "a", {"Orientation": "Right"})]


def test_installed_class_save_and_impact_share_consumer_lookup():
    from azeo_control_trainer.core.hmi.pvms.control import PIDCompact
    from azeo_control_trainer.core.hmi.pvms.publishing import displays_using_class
    from azeo_control_trainer.azeo_graphics_designer.configurator.impact import affected_instances
    document = {"display": "Unit", "pvms": [PIDCompact().place("loop", path="UNIT/PID1").to_dict()]}
    assert displays_using_class("PIDCompact", [document]) == ("Unit",)
    assert affected_instances("PIDCompact", {"Unit": document}) == [("Unit", "loop", {})]


@pytest.mark.parametrize("payload", ["{unfinished", '{"pvms": [null]}'])
def test_class_impact_reports_unreadable_drafts_and_blocks_save(window, payload):
    from PySide6.QtWidgets import QDialogButtonBox
    designer = window.open_pvm_config("PIDCompact")
    path = designer.standards_root / "Unreadable" / "draft.json"
    path.parent.mkdir()
    path.write_text(payload, encoding="utf-8")
    dialog = designer.review_impact(saving=True)
    assert any("Unreadable" in issue for issue in dialog.blockers)
    assert not dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).isEnabled()
    dialog.close()
    designer.close()


def test_new_tools_are_reachable_from_main_window(window):
    for key in ("worksheet", "sequences", "revisions"):
        dialog = window.open_engineering_tool(key)
        assert dialog is window.open_engineering_tool(key)
        dialog.close()
        APP.processEvents()


def test_leaving_paused_sequence_restores_overrides_without_reentering_test(window, monkeypatch):
    from azeo_control_trainer.azeo_graphics_designer.sequence_tools import SequenceDialog
    from azeo_control_trainer.core.hmi.binding.result import BindingResult
    studio = window.current()
    monkeypatch.setattr(studio.preview_source.source, "read", lambda *_: BindingResult(value=37))
    studio.preview_source.set_override("KEEP/AI/PV", value=5)
    studio.set_test_mode(True)
    dialog = SequenceDialog(studio)
    dialog.path.setText("M/AI/PV")
    dialog.add_lifecycle()
    dialog.step()
    assert not dialog.timer.isActive()
    studio.exit_test()
    assert not studio.test_mode
    assert dialog._restore is None
    assert studio.preview_source.overrides == {"KEEP/AI/PV": {"value": 5}}
    dialog.close()


def test_final_pipe_route_uses_obstacles_after_provisional_moves(window, monkeypatch):
    from azeo_control_trainer.core.hmi.pvms.rendering import pipe_scene
    studio = window.current()
    a = studio.add_static("rect", 100, 100, 80, 60)
    b = studio.add_static("rect", 400, 100, 80, 60)
    obstacle = studio.add_static("rect", 250, 50, 70, 200)
    pipe = studio.add_pipe(a, "e", b, "w")
    snapshots = []
    route = pipe_scene.route_pipe
    def track(*args, **kwargs):
        snapshots.append(kwargs["obstacles"])
        return route(*args, **kwargs)
    monkeypatch.setattr(pipe_scene, "route_pipe", track)
    drag = studio.canvas.pointer_drag
    drag.press(a, QPointF(140, 130), Qt.NoModifier)
    drag.move(QPointF(140, 170), Qt.NoModifier)
    assert snapshots[-1] == ()
    drag.release()
    assert any(one.ident == obstacle.data["id"] for one in snapshots[-1])
    assert pipe.data["a_side"] == "e" and pipe.data["b_side"] == "w"


def test_worksheet_validates_pvm_family_and_keeps_controller_configuration(window):
    from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
    from azeo_control_trainer.core.strategy.blocks.pid_block import PIDBlock
    from azeo_control_trainer.core.strategy.blocks.io_blocks import AIBlock
    from azeo_control_trainer.core.hmi.pvms.control import PIDCompact
    from azeo_control_trainer.azeo_graphics_designer.worksheet import WorksheetDialog
    studio = window.current()
    graph = StrategyGraph("UNIT")
    for block in (PIDBlock("PID1"), PIDBlock("PID2"), AIBlock("AI1")):
        graph.add_block(block)
    studio.graphs_provider = lambda: {"UNIT": graph}
    studio.preview_source.source._graphs = studio.graphs_provider
    item = studio._add_item(PIDCompact().place("loop", path="UNIT/PID1", x=50, y=50))
    studio.selection.replace((item,))
    dialog = WorksheetDialog(studio)
    dialog.table.item(0, 2).setText("UNIT/AI1")
    assert dialog.preview() is None
    assert "requires PID" in dialog.table.item(0, 6).text()
    before_config = {block.instance_name: dict(block.config.params) for block in graph.blocks.values()}
    dialog.table.item(0, 2).setText("UNIT/PID2")
    assert dialog.preview() is not None
    dialog.apply()
    assert studio._items()[0].pvm.params["path"] == "UNIT/PID2"
    assert {block.instance_name: dict(block.config.params) for block in graph.blocks.values()} == before_config
    dialog.close()


def test_worksheet_does_not_overwrite_a_sequence_saved_after_its_preview(window):
    from azeo_control_trainer.azeo_graphics_designer.worksheet import WorksheetDialog
    from azeo_control_trainer.azeo_graphics_designer.studio.sequences import alarm_sequence
    studio = window.current()
    item = studio.add_static("text", 10, 10, 90, 30)
    studio.selection.replace((item,))
    dialog = WorksheetDialog(studio)
    dialog.table.item(0, 3).setText("Feed")
    dialog.preview()
    sequence = dict(name="Alarm lifecycle", steps=alarm_sequence("UNIT/PID1/PV"))
    studio.display.test_sequences = [sequence]
    with pytest.raises(ValueError, match="changed"):
        dialog.apply()
    assert studio.display.test_sequences == [sequence]
    dialog.close()


def test_authored_worksheet_preserves_link_pipe_and_one_undo_then_impact_blocks_removed_property(window):
    from azeo_control_trainer.core.hmi.pvms.configurator.model import PvmConfiguration, PvmProperty, PropertyGroup
    from azeo_control_trainer.azeo_graphics_designer.worksheet import WorksheetDialog
    studio = window.current()
    studio.user_library().add("CaptionPvm", [dict(id="caption", kind="text", x=0, y=0, w=100, h=30, text="Pvm.Caption")])
    config = PvmConfiguration("CaptionPvm", [PropertyGroup("Basic", [PvmProperty("Caption", "String", default="Default")])])
    config.save(studio.store.root / "_pvmcfg")
    studio.place_user_pvm("CaptionPvm", 50, 50, choices={"Caption": "Feed"}, group_id="one")
    member = studio._static_items()[0]
    other = studio.add_static("rect", 300, 50, 60, 40)
    pipe = studio.add_pipe(member, "e", other, "w")
    pipe_id = pipe.data["id"]
    studio.selection.replace((member,))
    before = studio._document()
    undo_count = len(studio._undo_stack)
    dialog = WorksheetDialog(studio)
    assert dialog.table.rowCount() == 1
    dialog.table.item(0, 2).setText("Product")
    assert dialog.preview() is not None
    dialog.apply()
    state = studio._user_instance_state("one")
    assert state["choices"]["Caption"] == "Product"
    assert state["link"] == "linked"
    connected = next(one for one in studio._pipe_items() if one.data["id"] == pipe_id)
    assert connected.data["a"] in {one.data["id"] for one in state["members"]}
    assert len(studio._undo_stack) == undo_count + 1
    studio.undo()
    assert studio._document() == before
    dialog.close()
    designer = window.open_pvm_config("CaptionPvm")
    designer.config.groups[0].properties.clear()
    designer._mark_unsaved()
    assert designer.save() is None
    assert designer._impact_dialog.instances
    assert any("Caption" in issue for issue in designer._impact_dialog.blockers)
    assert PvmConfiguration.load(studio.store.root / "_pvmcfg" / "CaptionPvm.pvmcfg.json").property("Caption")
    designer.close()
