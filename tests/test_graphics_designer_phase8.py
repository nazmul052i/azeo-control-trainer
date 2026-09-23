"""Commercial selection and bulk-edit contracts."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QGraphicsRectItem, QGraphicsSceneMouseEvent,
)

import azeo_control_trainer.core.strategy.blocks  # noqa: F401
from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore, PvmDisplay
from azeo_control_trainer.core.hmi.pvms.rendering.items import item_document_data
from azeo_control_trainer.azeo_graphics_designer.studio.assembler import PvmStudio
from azeo_control_trainer.azeo_graphics_designer.studio.selection import (
    AggregateKind, visual_scene_rect,
)
from azeo_control_trainer.azeo_graphics_designer.studio.selection_pane import SelectionPane
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


def test_primary_is_stable_through_extend_toggle_and_pane_sync(tmp_path):
    studio = _studio(tmp_path)
    a = studio.add_static("rect", 0, 0)
    b = studio.add_static("rect", 200, 0)
    c = studio.add_static("rect", 400, 0)
    studio.selection.replace([a], primary=a)
    studio.selection.extend([b], primary=b)
    assert studio.selection.snapshot().primary is b
    studio.selection.extend([c])
    assert studio.selection.snapshot().primary is c
    studio.selection.toggle(a)
    assert studio.selection.snapshot().primary is c
    pane = SelectionPane(studio)
    pane.sync_from_canvas()
    assert pane.currentItem().data(2, 0x0100) is c


def test_rotated_alignment_skips_locked_and_is_one_undo(tmp_path):
    studio = _studio(tmp_path)
    primary = studio.add_static("rect", 100, 100, w=100, h=40)
    rotated = studio.add_static("rect", 300, 200, w=80, h=30)
    rotated.data["rot"] = 45
    rotated.apply_rotation()
    locked = studio.add_static("rect", 500, 300)
    locked.data["locked"] = True
    studio.selection.replace([primary, rotated, locked], primary=primary)
    studio._undo_stack.clear()
    locked_before = locked.pos()
    assert studio.align_selection("left", "primary") == 2
    assert abs(visual_scene_rect(rotated).left()
               - visual_scene_rect(primary).left()) < 0.01
    assert locked.pos() == locked_before
    assert len(studio._undo_stack) == 1
    assert studio.align_selection("left", "primary") == 0
    assert len(studio._undo_stack) == 1


def test_mixed_group_and_property_edit_are_atomic(tmp_path):
    studio = _studio(tmp_path)
    studio.place_block("UNIT100/PID1", "PID", 20, 20)
    pvm = studio._items()[0]
    shape = studio.add_static("rect", 200, 20)
    studio.selection.replace([pvm, shape], primary=pvm)
    studio._undo_stack.clear()
    group = studio.group_selected()
    assert group and pvm.pvm.group == group and shape.data["group"] == group
    assert len(studio._undo_stack) == 1
    assert studio.selection_property("visible").kind is AggregateKind.UNIFORM
    changed, skipped = studio.apply_selection_property("opacity", 0.5)
    assert (changed, skipped) == (1, 1)
    assert shape.data["opacity"] == 0.5 and pvm.opacity() == 1.0
    studio.selection.replace([pvm, shape], primary=pvm)
    changed, skipped = studio.apply_selection_property("visible", False)
    assert (changed, skipped) == (2, 0)
    assert not pvm.pvm.visible and not shape.data["visible"]
    assert len(studio._undo_stack) == 3
    studio.selection.replace([pvm, shape], primary=pvm)
    studio.ungroup_selected()
    assert not pvm.pvm.group and "group" not in shape.data


def test_two_pvms_and_their_pipe_form_one_persisted_clickable_group(tmp_path):
    """A connected assembly groups without calling Qt's data() as a dict."""
    studio = _studio(tmp_path)
    studio.place_block("UNIT100/PID1", "PID", 20, 20)
    studio.place_block("UNIT100/PID1", "PID", 280, 20)
    studio.place_block("UNIT100/PID1", "PID", 540, 20)
    first, second, ungrouped = sorted(
        studio._items(), key=lambda item: item.pos().x())
    pipe = studio.add_pipe(first, "e", second, "w")

    studio.selection.replace([first, second, pipe], primary=second)
    group = studio.group_selected()
    assert group
    assert first.pvm.group == second.pvm.group == group
    assert pipe.data["group"] == group
    assert not ungrouped.pvm.group
    assert studio._document()["items"][-1]["group"] == group

    # This exact gesture used to inspect the ungrouped PVM's inherited
    # QGraphicsItem.data method and raise AttributeError from `.get`.
    studio.selection.clear()
    point = first.rect().center()
    press = QGraphicsSceneMouseEvent(QEvent.GraphicsSceneMousePress)
    press.setPos(point)
    press.setScenePos(first.mapToScene(point))
    press.setButton(Qt.LeftButton)
    press.setButtons(Qt.LeftButton)
    first.mousePressEvent(press)
    assert set(studio.selection.snapshot().items) == {first, second, pipe}

    studio.ungroup_selected()
    assert not first.pvm.group and not second.pvm.group
    assert "group" not in pipe.data
    studio.close()


def test_qt_data_method_is_never_treated_as_document_metadata() -> None:
    item = QGraphicsRectItem()
    assert callable(item.data)
    assert item_document_data(item) == {}


def test_canvas_event_failure_is_logged_reported_and_contained(
        tmp_path, monkeypatch, caplog) -> None:
    studio = _studio(tmp_path)
    studio.resize(600, 400)
    studio.show()
    QApplication.processEvents()
    reported = []
    studio.uiError.connect(reported.append)

    def fail(_position):
        raise RuntimeError("gesture failure marker")

    monkeypatch.setattr(studio, "_eraser_at", fail)
    with caplog.at_level("ERROR", logger="azeo.graphics_designer.canvas"):
        QTest.mouseClick(
            studio.canvas.viewport(), Qt.LeftButton, pos=QPoint(20, 20))

    assert any("gesture failure marker" in message for message in reported)
    assert "Canvas event mousePressEvent failed" in caplog.text
    assert not studio._gesture_open
    studio.close()


def test_equal_gap_and_same_size_use_primary(tmp_path):
    studio = _studio(tmp_path)
    a = studio.add_static("rect", 0, 0, w=30, h=20)
    b = studio.add_static("rect", 100, 0, w=60, h=30)
    c = studio.add_static("rect", 300, 0, w=90, h=40)
    studio.selection.replace([a, b, c], primary=b)
    assert studio.distribute_selected("h") == 1
    ordered = sorted((i.sceneBoundingRect().left(), i.sceneBoundingRect().right())
                     for i in (a, b, c))
    gaps = [ordered[i + 1][0] - ordered[i][1] for i in range(2)]
    assert abs(gaps[0] - gaps[1]) < 0.01
    assert studio.equalize_selected("both") == 2
    assert all(i.rect().size() == b.rect().size() for i in (a, b, c))
    assert studio._selection_overlay.isVisible()


def test_page_commands_use_rotated_bounds_skip_locks_and_drop_noops(tmp_path):
    studio = _studio(tmp_path)
    studio.display.width = 800
    studio.display.height = 600
    studio.display.safe_margin = 24
    studio.apply_display_frame()
    plain = studio.add_static("rect", 120, 100, w=80, h=40)
    rotated = studio.add_static("rect", 300, 180, w=100, h=50)
    rotated.data["rot"] = 35
    rotated.apply_rotation()
    locked = studio.add_static("rect", 450, 220, w=70, h=40)
    locked.data["locked"] = True
    locked_before = locked.pos()
    studio.selection.replace([plain, rotated, locked], primary=plain)
    studio._undo_stack.clear()

    assert studio.align_selected_to_page("left") == 2
    assert abs(visual_scene_rect(plain).left() - 24) < 0.01
    assert abs(visual_scene_rect(rotated).left() - 24) < 0.01
    assert locked.pos() == locked_before
    assert len(studio._undo_stack) == 1
    assert studio.align_selected_to_page("left") == 0
    assert len(studio._undo_stack) == 1

    rotated.setPos(-200, -180)
    studio._undo_stack.clear()
    assert studio.contain_selected_in_page() == 1
    assert visual_scene_rect(rotated).left() >= 24 - 0.01
    assert visual_scene_rect(rotated).top() >= 24 - 0.01
    assert locked.pos() == locked_before
    assert len(studio._undo_stack) == 1
    assert studio.contain_selected_in_page() == 0
    assert len(studio._undo_stack) == 1


def test_locked_z_order_and_exact_regroup_are_noops(tmp_path):
    studio = _studio(tmp_path)
    a = studio.add_static("rect", 10, 10)
    b = studio.add_static("rect", 160, 10)
    studio.selection.replace([a, b], primary=a)
    group = studio.group_selected()
    studio._undo_stack.clear()

    assert studio.group_selected() == group
    assert studio._undo_stack == []

    b.data["locked"] = True
    studio.selection.replace([b], primary=b)
    z_before = b.zValue()
    assert studio.z_shift(1) == 0
    assert b.zValue() == z_before
    assert studio._undo_stack == []
