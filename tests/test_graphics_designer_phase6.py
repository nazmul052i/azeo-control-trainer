"""Focused contracts for Phase 6 canvas infrastructure."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QGraphicsView

import azeo_control_trainer.core.strategy.blocks  # noqa: F401
from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore, PvmDisplay
from azeo_control_trainer.azeo_graphics_designer.studio.assembler import PvmStudio
from azeo_control_trainer.azeo_graphics_designer.studio.layers import LayersPane
from azeo_control_trainer.core.strategy.model.block_registry import BlockRegistry
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph


def _studio(tmp_path):
    QApplication.instance() or QApplication([])
    block = BlockRegistry().create("PID", "PID1")
    block._apply_config()
    graph = StrategyGraph(name="UNIT100")
    graph.add_block(block)
    DisplayStore(tmp_path).save_draft(PvmDisplay("Overview"))
    studio = PvmStudio(lambda: {"UNIT100": graph}, tmp_path)
    studio.enter_edit()
    return studio


def test_interaction_modes_have_one_authoritative_drag_mode(tmp_path):
    studio = _studio(tmp_path)
    studio.select_tool()
    assert studio.canvas.dragMode() == QGraphicsView.RubberBandDrag
    studio.set_pan(True)
    assert studio.canvas.dragMode() == QGraphicsView.ScrollHandDrag
    studio.arm_line()
    assert studio.canvas.dragMode() == QGraphicsView.NoDrag
    studio.cancel_gestures()
    assert studio.canvas.dragMode() == QGraphicsView.RubberBandDrag
    studio.enter_test()
    assert studio.canvas.dragMode() == QGraphicsView.NoDrag


def test_gesture_transactions_are_one_per_drag_and_drop_click_noops(tmp_path):
    studio = _studio(tmp_path)
    item = studio.add_static("rect", 10, 10)
    studio._undo_stack.clear()
    studio.begin_gesture()
    item.setPos(20, 20)
    studio.end_gesture()
    assert len(studio._undo_stack) == 1
    studio.begin_gesture()
    item.setPos(30, 30)
    studio.end_gesture()
    assert len(studio._undo_stack) == 2
    studio.begin_gesture()
    studio.end_gesture()
    assert len(studio._undo_stack) == 2


def test_action_only_button_is_enabled_and_passes_canvas_validation(tmp_path):
    studio = _studio(tmp_path)
    button = studio.add_static("user_entry", 10, 10)
    button.data["actions"] = [
        {"event": "click", "kind": "open_display", "target": "Overview"}
    ]
    opened = []
    button.interaction_handler = lambda action, _item: opened.append(action) or True

    assert button.refresh_write_permission()
    assert button.activate()
    assert opened[0]["target"] == "Overview"
    assert not any(problem.startswith("User Entry:")
                   for problem in studio.validate())


def test_typed_clipboard_deep_copies_groups_and_internal_pipes(tmp_path):
    studio = _studio(tmp_path)
    left = studio.add_static("rect", 10, 10)
    right = studio.add_static("rect", 200, 10)
    outside = studio.add_static("rect", 400, 10)
    left.data.update(group="grp_source", points=[[0, 0], [1, 1]])
    right.data["group"] = "grp_source"
    studio.add_pipe(left, "e", right, "w")
    studio.add_pipe(right, "e", outside, "w")
    studio.canvas.scene().clearSelection()
    left.setSelected(True)
    right.setSelected(True)
    payload = studio.copy_selection_payload()
    assert [r["type"] for r in payload["records"]].count("pipe") == 1
    left.data["points"][0][0] = 99
    copied_left = next(r["data"] for r in payload["records"]
                       if r["type"] == "item" and r["data"]["id"] == left.data["id"])
    assert copied_left["points"][0][0] == 0
    assert studio.paste_payload(payload) == 3
    selected = [i for i in studio._static_items() if i.isSelected()]
    assert len(selected) == 2
    assert len({i.data["group"] for i in selected}) == 1
    assert selected[0].data["group"] != "grp_source"
    selected_ids = {i.data["id"] for i in selected}
    assert any({p.data["a"], p.data["b"]} == selected_ids
               for p in studio._pipe_items())


def test_clipboard_uses_logical_selection_for_hidden_items(tmp_path):
    studio = _studio(tmp_path)
    visible = studio.add_static("rect", 10, 10)
    hidden = studio.add_static("rect", 200, 10)
    studio.add_pipe(visible, "e", hidden, "w")
    hidden.data["visible"] = False
    studio._apply_mode()
    studio.selection.replace([visible, hidden], primary=hidden)

    payload = studio.copy_selection_payload()
    assert [row["type"] for row in payload["records"]].count("item") == 2
    assert [row["type"] for row in payload["records"]].count("pipe") == 1


def test_cancel_gesture_restores_document_undo_redo_and_dirty_state(tmp_path):
    studio = _studio(tmp_path)
    item = studio.add_static("rect", 10, 10)
    item_id = item.data["id"]
    studio._undo_stack.clear()
    studio._redo_stack[:] = [{"sentinel": True}]
    studio.unsaved = False
    studio._recovery_timer.stop()

    studio.begin_gesture()
    item.setPos(90, 70)
    studio.mark_unsaved()
    studio._sync_document()
    studio.store.save_recovery(studio.display)
    studio.cancel_gesture()

    restored = next(row for row in studio._static_items()
                    if row.data["id"] == item_id)
    assert restored.pos().x() == 10 and restored.pos().y() == 10
    assert studio._undo_stack == []
    assert studio._redo_stack == [{"sentinel": True}]
    assert not studio.unsaved
    assert studio.store.load_recovery(studio.display.name) is None


def test_z_order_persists_and_layer_isolation_is_viewport_only(tmp_path):
    studio = _studio(tmp_path)
    studio.place_block("UNIT100/PID1", "PID", 10, 10)
    pvm = studio._items()[0]
    shape = studio.add_static("rect", 200, 10)
    pvm.setSelected(True)
    shape.setSelected(True)
    studio.z_shift(3)
    document = studio._document()
    assert document["pvms"][0]["z"] == 3
    assert next(i for i in document["items"] if i["kind"] == "rect")["z"] == 3
    before = studio._document()
    undo_count = len(studio._undo_stack)
    unsaved = studio.unsaved
    pane = LayersPane(studio)
    pane.isolate("pvms")
    assert pvm.isVisible() and not shape.isVisible()
    assert studio._document() == before
    assert len(studio._undo_stack) == undo_count
    assert studio.unsaved == unsaved
    pane.isolate("pvms")
    assert pvm.isVisible() and shape.isVisible()
