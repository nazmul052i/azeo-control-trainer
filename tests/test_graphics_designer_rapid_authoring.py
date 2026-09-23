"""Canvas workflows should keep an engineer drawing, with reversible gestures."""
from __future__ import annotations

import os
import sys
import json
from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QSettings, QMimeData
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from azeo_control_trainer.azeo_graphics_designer.window import HmiStudioWindow, _PaletteCard
from azeo_control_trainer.core.hmi.pvms.rendering.items import item_group_id
from azeo_control_trainer.core.hmi.pvms.rendering.chrome import MIME_PALETTE


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope,
                      str(tmp_path / "settings"))
    window = HmiStudioWindow(lambda: {}, tmp_path)
    window.resize(1400, 900)
    window.show()
    studio = window.current()
    errors = []
    studio.uiError.connect(errors.append)
    studio.display.width, studio.display.height = 1600, 1200
    studio.apply_display_frame()
    app.processEvents()
    studio.set_zoom(100)
    studio.canvas.centerOn(400, 300)
    app.processEvents()
    yield window
    window.close()
    window.deleteLater()
    app.processEvents()
    assert errors == []


def mouse(studio, kind, point, modifiers=Qt.NoModifier, button=Qt.LeftButton):
    viewport = studio.canvas.viewport()
    position = studio.canvas.mapFromScene(QPointF(*point))
    move = kind == QEvent.MouseMove
    release = kind == QEvent.MouseButtonRelease
    event = QMouseEvent(kind, QPointF(position),
                        QPointF(viewport.mapToGlobal(position)),
                        Qt.NoButton if move else button,
                        Qt.NoButton if release else button, modifiers)
    QApplication.sendEvent(viewport, event)


def drag(studio, start, end, modifiers=Qt.NoModifier):
    mouse(studio, QEvent.MouseButtonPress, start, modifiers)
    mouse(studio, QEvent.MouseMove, end, modifiers)
    mouse(studio, QEvent.MouseButtonRelease, end, modifiers)


def test_drag_preserves_off_grid_selection_spacing(window):
    studio = window.current()
    first = studio.add_static("rect", 101, 137, 80, 50)
    second = studio.add_static("rect", 238, 179, 80, 50)
    studio.selection.replace((first, second), primary=first)
    studio.smart_guides_enabled = False
    before = second.pos() - first.pos()
    drag(studio, (141, 162), (207, 207))
    assert first.pos() != QPointF(101, 137)
    assert second.pos() - first.pos() == before


def test_shift_drag_constrains_the_whole_selection(window):
    studio = window.current()
    studio.snap_enabled = False
    studio.smart_guides_enabled = False
    first = studio.add_static("rect", 100, 100, 80, 60)
    second = studio.add_static("rect", 230, 140, 80, 60)
    studio.selection.replace((first, second), primary=first)
    drag(studio, (140, 130), (230, 165), Qt.ShiftModifier)
    assert first.pos() == QPointF(190, 100)
    assert second.pos() == QPointF(320, 140)


def test_ctrl_drag_copies_an_assembly_and_its_manual_pipe_once(window):
    studio = window.current()
    studio.snap_enabled = studio.smart_guides_enabled = False
    first = studio.add_static("rect", 100, 100, 80, 60)
    second = studio.add_static("rect", 300, 100, 80, 60)
    pipe = studio.add_pipe(first, "e", second, "w")
    pipe.data.update(route_mode="manual", route_points=[[220, 130]])
    studio.selection.replace((first, second, pipe))
    studio.group_selected()
    original_group = item_group_id(first)
    before_undo = len(studio._undo_stack)
    drag(studio, (140, 130), (230, 220), Qt.ControlModifier)
    assert first.pos() == QPointF(100, 100)
    assert second.pos() == QPointF(300, 100)
    assert len(studio._static_items()) == 4
    assert len(studio._pipe_items()) == 2
    copies = [item for item in studio._groupable_items()
              if item_group_id(item) != original_group]
    assert len(copies) == 3
    assert len({item_group_id(item) for item in copies}) == 1
    copied_pipe = next(item for item in studio._pipe_items() if item is not pipe)
    assert copied_pipe.data["route_points"] == [[310, 220]]
    assert copied_pipe.data["a"] != pipe.data["a"]
    assert len(studio._undo_stack) == before_undo + 1
    studio.undo()
    assert len(studio._static_items()) == 2
    assert len(studio._pipe_items()) == 1


def test_repeat_placement_does_not_save_the_cursor_ghost(window):
    studio = window.current()
    studio.repeat_placement = True
    studio.arm_place("rect", w=80, h=50)
    assert studio._document().get("items", []) == []
    for point in ((140, 130), (340, 130)):
        mouse(studio, QEvent.MouseMove, point, button=Qt.NoButton)
        mouse(studio, QEvent.MouseButtonPress, point)
        mouse(studio, QEvent.MouseButtonRelease, point)
    assert len(studio._document()["items"]) == 2
    assert len({item.data["id"] for item in studio._static_items()}) == 2
    assert studio._place_ghost is not None
    studio.undo()
    assert len(studio._document()["items"]) == 1
    assert studio.interaction_mode == "draw"


def test_repeat_shapes_and_right_click_cancel(window):
    studio = window.current()
    studio.repeat_placement = True
    studio.arm_shape("rect")
    drag(studio, (100, 100), (180, 150))
    drag(studio, (300, 100), (380, 150))
    assert len(studio._static_items()) == 2
    mouse(studio, QEvent.MouseButtonPress, (500, 100), button=Qt.RightButton)
    mouse(studio, QEvent.MouseButtonRelease, (500, 100), button=Qt.RightButton)
    assert len(studio._static_items()) == 2
    assert studio.interaction_mode == "select"


def test_space_pan_preserves_the_armed_drawing_tool(window):
    studio = window.current()
    studio.arm_shape("ellipse")
    studio.canvas.setFocus()
    horizontal = studio.canvas.horizontalScrollBar()
    before = horizontal.value()
    QTest.keyPress(studio.canvas, Qt.Key_Space)
    drag(studio, (350, 250), (440, 290))
    QTest.keyRelease(studio.canvas, Qt.Key_Space)
    assert horizontal.value() != before
    assert studio._shape_armed
    assert studio._static_items() == []


def test_palette_click_waits_for_a_canvas_position(window):
    studio = window.current()
    card = next(card for card in window.palette_box.findChildren(_PaletteCard)
                if card._drag_payload.get("type") == "pvm")
    card.mouseReleaseEvent(QMouseEvent(
        QEvent.MouseButtonRelease, QPointF(5, 5), QPointF(5, 5),
        Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
    assert studio._items() == []
    mouse(studio, QEvent.MouseButtonPress, (300, 200))
    mouse(studio, QEvent.MouseButtonRelease, (300, 200))
    assert len(studio._items()) == 1
    item = studio._items()[0]
    assert (item.mapToScene(item.rect().center()) - QPointF(300, 200)).manhattanLength() < 2


def test_ribbon_style_brush_copies_style_without_binding_or_geometry(window):
    studio = window.current()
    source = studio.add_static("rect", 100, 100, 80, 60,
                               properties={"fill": "#123456", "line": "#abcdef", "width": 3})
    target = studio.add_static("rect", 300, 100, 80, 60,
                               properties={"fill": "#ffffff", "path": "KEEP/PV"})
    studio.selection.replace((source,))
    window.dispatch("format.copy_style")
    mouse(studio, QEvent.MouseButtonPress, (340, 130))
    mouse(studio, QEvent.MouseButtonRelease, (340, 130))
    assert target.data["fill"] == "#123456"
    assert target.data["path"] == "KEEP/PV"
    assert target.pos() == QPointF(300, 100)
    studio.undo()
    restored = next(item for item in studio._static_items() if item.data["id"] == target.data["id"])
    assert restored.data["fill"] == "#ffffff"


def test_escape_rolls_back_copy_drag_and_its_undo_entry(window):
    studio = window.current()
    item = studio.add_static("rect", 100, 100, 80, 60)
    studio.selection.replace((item,))
    before = studio._document()
    undo_count = len(studio._undo_stack)
    mouse(studio, QEvent.MouseButtonPress, (140, 130), Qt.ControlModifier)
    mouse(studio, QEvent.MouseMove, (240, 230), Qt.ControlModifier)
    assert len(studio._static_items()) == 2
    QTest.keyClick(studio.canvas, Qt.Key_Escape)
    mouse(studio, QEvent.MouseButtonRelease, (240, 230))
    assert studio._document() == before
    assert len(studio._undo_stack) == undo_count


def test_ctrl_click_toggles_selection_without_copying(window):
    studio = window.current()
    item = studio.add_static("rect", 100, 100, 80, 60)
    studio.selection.replace((item,))
    undo_count = len(studio._undo_stack)
    mouse(studio, QEvent.MouseButtonPress, (140, 130), Qt.ControlModifier)
    mouse(studio, QEvent.MouseButtonRelease, (140, 130), Qt.ControlModifier)
    assert not item.isSelected()
    assert len(studio._static_items()) == 1
    assert len(studio._undo_stack) == undo_count


def test_alt_drag_bypasses_grid_and_alignment(window):
    studio = window.current()
    item = studio.add_static("rect", 101, 103, 80, 60)
    studio.selection.replace((item,))
    drag(studio, (141, 133), (198, 169), Qt.AltModifier)
    assert item.pos() == QPointF(158, 139)


def test_resize_handle_keeps_its_existing_gesture(window):
    studio = window.current()
    studio.snap_enabled = studio.smart_guides_enabled = False
    item = studio.add_static("rect", 100, 100, 80, 60)
    studio.selection.replace((item,))
    drag(studio, (180, 130), (220, 130))
    assert item.pos() == QPointF(100, 100)
    assert item.rect().width() == 120
    assert item.rect().height() == 60


def test_repeat_toggle_drives_connection_tool_and_preserves_ports(window):
    studio = window.current()
    QTest.mouseClick(studio.canvas_frame.tool_buttons["repeat"], Qt.LeftButton)
    assert studio.repeat_placement
    for y in (100, 280):
        studio.add_static("rect", 100, y, 80, 60)
        studio.add_static("rect", 400, y, 80, 60)
    studio.arm_connect()
    drag(studio, (180, 130), (400, 130))
    assert studio.connect_armed
    drag(studio, (180, 310), (400, 310))
    assert len(studio._pipe_items()) == 2
    for pipe in studio._pipe_items():
        assert pipe.data["a_side"] == "e"
        assert pipe.data["b_side"] == "w"


def test_test_mode_removes_unplaced_preview_and_toolbar(window):
    studio = window.current()
    studio.arm_place("rect")
    mouse(studio, QEvent.MouseMove, (300, 200), button=Qt.NoButton)
    studio.enter_test()
    assert studio._place_ghost is None
    assert studio._document().get("items", []) == []
    assert studio.canvas_frame.tools.isHidden()


def test_space_release_before_mouse_restores_drawing_cursor(window):
    studio = window.current()
    studio.arm_shape("ellipse")
    studio.canvas.setFocus()
    QTest.keyPress(studio.canvas, Qt.Key_Space)
    mouse(studio, QEvent.MouseButtonPress, (350, 250))
    mouse(studio, QEvent.MouseMove, (440, 290))
    QTest.keyRelease(studio.canvas, Qt.Key_Space)
    mouse(studio, QEvent.MouseButtonRelease, (440, 290))
    assert studio.canvas.cursor().shape() == Qt.CrossCursor
    assert studio._shape_armed


def test_double_click_still_opens_drawing_format(window, monkeypatch):
    studio = window.current()
    item = studio.add_static("text", 100, 100, 160, 60, text="Feed pump")
    edited = []
    monkeypatch.setattr(studio, "edit_drawing_format", edited.append)
    mouse(studio, QEvent.MouseButtonPress, (180, 130))
    mouse(studio, QEvent.MouseButtonRelease, (180, 130))
    mouse(studio, QEvent.MouseButtonDblClick, (180, 130))
    mouse(studio, QEvent.MouseButtonRelease, (180, 130))
    assert edited == [item]


def test_dragging_another_stencil_replaces_the_click_placement_tool(window):
    studio = window.current()
    studio.arm_place("ellipse")
    mime = QMimeData()
    mime.setData(MIME_PALETTE, json.dumps({"type": "symbol", "symbol": "pump"}).encode())
    event = QDragEnterEvent(QPoint(200, 200), Qt.CopyAction, mime,
                           Qt.LeftButton, Qt.NoModifier)
    studio.canvas.dragEnterEvent(event)
    assert event.isAccepted()
    assert studio._place_ghost is None
    assert studio._document().get("items", []) == []
    studio.canvas.dropEvent(QDropEvent(QPointF(200, 200), Qt.CopyAction, mime,
                                       Qt.LeftButton, Qt.NoModifier))
    assert studio.canvas._ghost is None
    assert len(studio._static_items()) == 1
    assert studio._static_items()[0].data["symbol"] == "pump"
    assert studio.interaction_mode == "select"


def test_drag_routes_only_connections_touched_by_selection(window, monkeypatch):
    from azeo_control_trainer.core.hmi.pvms.rendering import pipe_scene

    studio = window.current()
    boxes = [studio.add_static("rect", x, y, 80, 60)
             for y in (100, 400) for x in (100, 300)]
    first = studio.add_pipe(boxes[0], "e", boxes[1], "w")
    untouched = studio.add_pipe(boxes[2], "e", boxes[3], "w")
    original = [QPointF(point) for point in untouched._points]
    studio.selection.replace(boxes[:2])
    routed = []
    route = pipe_scene.route_pipe

    def track(scene, pipe, *args, **kwargs):
        routed.append(pipe)
        return route(scene, pipe, *args, **kwargs)

    monkeypatch.setattr(pipe_scene, "route_pipe", track)
    drag(studio, (140, 130), (210, 220))
    assert routed and set(routed) == {first}
    assert untouched._points == original


def test_hovering_a_placement_preview_does_not_dirty_or_obstruct_the_display(window):
    from azeo_control_trainer.core.hmi.pvms.rendering.pipe_scene import scene_obstacles

    studio = window.current()
    assert studio.save_draft()
    studio.arm_place("symbol", symbol="pump")
    mouse(studio, QEvent.MouseMove, (300, 200), button=Qt.NoButton)
    assert not studio.unsaved
    assert scene_obstacles(studio.canvas.scene()) == ()
