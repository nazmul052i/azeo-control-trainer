"""Drive native gestures, document persistence and a 500-block frame budget."""
import json
import os
from pathlib import Path
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyleOptionGraphicsItem

from azeo_control_trainer.azeo_pa_designer import create_window
from azeo_control_trainer.azeo_pa_designer._canvas_primitives import (
    PROCEDURE_STEP_CATEGORIES,
    procedure_block_header_color,
)
from azeo_control_trainer.azeo_pa_designer.canvas import BLOCK_MIME, START, END, ProcedureCanvas
from azeo_control_trainer.core.presentation.application_style import apply_application_style
from azeo_control_trainer.core.presentation.function_block_style import (
    FUNCTION_BLOCK_CATEGORY_COLORS,
)
from azeo_control_trainer.core.procedures.authoring import ProcedureDraft
from azeo_control_trainer.core.procedures.library import block_for_type, block_library
from azeo_control_trainer.core.procedures.model import load_definition


@pytest.fixture
def editor(tmp_path):
    app = QApplication.instance() or QApplication([])
    apply_application_style(app)
    window = create_window(tmp_path, graphs_provider=lambda: [])
    window.show()
    app.processEvents()
    window.canvas.centerOn(250, 300)
    yield window
    window._saved = window.snapshot()
    window.close()
    window.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def test_port_drag_changes_real_order_and_survives_save_undo(editor, tmp_path):
    canvas = editor.canvas
    canvas.centerOn(260, 285)
    QApplication.processEvents()
    source = canvas.mapFromScene(canvas.nodes[START].port_scene_position("output"))
    target = canvas.mapFromScene(canvas.nodes[canvas.key("record")].port_scene_position("input"))
    # Direct connection signals exercise the same document command as the native
    # gesture when a short viewport needs scrolling between those two ports.
    if not canvas.viewport().rect().contains(target):
        canvas.zoom_fit()
        source = canvas.mapFromScene(canvas.nodes[START].port_scene_position("output"))
        target = canvas.mapFromScene(canvas.nodes[canvas.key("record")].port_scene_position("input"))
    QTest.mousePress(canvas.viewport(), Qt.LeftButton, Qt.NoModifier, source)
    QTest.mouseMove(canvas.viewport(), target, 10)
    QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, Qt.NoModifier, target)
    assert [step["id"] for step in editor.draft.data["steps"]] == ["record", "review", "complete"]
    assert (START, canvas.key("record")) in canvas.edges
    assert (canvas.key("record"), canvas.key("review")) in canvas.edges
    saved = editor.save_revision()
    assert load_definition(saved, tmp_path / "procedures").procedure.steps[0].id == "record"
    editor.undo()
    assert editor.draft.data["steps"][0]["id"] == "review"
    editor.redo()
    assert editor.draft.data["steps"][0]["id"] == "record"


def test_native_palette_drop_on_wire_inserts_one_undoable_library_block(editor):
    canvas = editor.canvas
    canvas.zoom_fit()
    edge = canvas.edges[(START, canvas.key("review"))]
    point = canvas.mapFromScene(edge.path().pointAtPercent(0.45))
    mime = QMimeData()
    mime.setData(BLOCK_MIME, b"delay")
    enter = QDragEnterEvent(point, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(canvas.viewport(), enter)
    drop = QDropEvent(QPointF(point), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(canvas.viewport(), drop)
    assert drop.isAccepted()
    step = editor.draft.data["steps"][0]
    assert step["library_block_id"] == "azeo.procedure.delay"
    assert any(row["step_id"] == step["id"] for row in editor.draft.data["metadata"]["canvas_layout"])
    editor.undo()
    assert [step["id"] for step in editor.draft.data["steps"]] == ["review", "record", "complete"]
    assert not editor.draft.data["metadata"].get("canvas_layout")


def test_drag_moves_only_local_wires_and_commits_on_release(editor, tmp_path):
    canvas = editor.canvas
    node = canvas.nodes[canvas.key("review")]
    canvas.ensureVisible(node)
    QApplication.processEvents()
    origin = canvas.mapFromScene(node.sceneBoundingRect().center())
    before = node.pos()
    history = len(editor._undo)
    QTest.mousePress(canvas.viewport(), Qt.LeftButton, Qt.NoModifier, origin)
    QTest.mouseMove(canvas.viewport(), origin + QPoint(45, 25), 10)
    assert len(editor._undo) == history
    QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, Qt.NoModifier, origin + QPoint(45, 25))
    assert node.pos() != before
    assert len(editor._undo) == history + 1
    point = QPointF(node.pos())
    saved = editor.save_revision()
    loaded = ProcedureDraft.load(saved, tmp_path / "procedures")
    assert next(row for row in loaded.data["metadata"]["canvas_layout"] if row["step_id"] == "review")["x"] == round(point.x())
    editor.undo()
    assert canvas.nodes[canvas.key("review")].pos() == before


def test_route_geometry_is_saved_and_rename_retains_layout(editor, tmp_path):
    canvas = editor.canvas
    edge = canvas.edges[(canvas.key("review"), canvas.key("record"))]
    edge._route_handle_moved(0, QPointF(550, 330))
    edge.commit_route()
    canvas.nodes[canvas.key("review")].moveBy(24, 12)
    canvas.commit_layout()
    editor.fields["id"].setText("review_start")
    saved = editor.save_revision()
    assert saved
    loaded = ProcedureDraft.load(saved, tmp_path / "procedures")
    assert loaded.data["metadata"]["canvas_routes"][0]["source"] == canvas.key("review_start")
    assert any(row["step_id"] == "review_start" for row in loaded.data["metadata"]["canvas_layout"])
    editor.draft = loaded
    editor.render()
    assert canvas.edges[(canvas.key("review_start"), canvas.key("record"))].route_points[0] == QPointF(550, 330)


def test_selection_and_typing_retain_existing_scene_items(editor):
    canvas = editor.canvas
    nodes = dict(canvas.nodes)
    wires = dict(canvas.edges)
    canvas.nodes[canvas.key("record")].setSelected(True)
    canvas.nodes[canvas.key("review")].setSelected(False)
    QApplication.processEvents()
    assert editor._step_row == 1
    editor.fields["comment_prompt"].setPlainText("Record the observed pressure.")
    assert canvas.nodes == nodes
    assert canvas.edges == wires
    assert "pressure" in canvas.nodes[canvas.key("record")].toolTip()


def test_escape_rolls_back_drag_and_event_failure_is_contained(editor, monkeypatch):
    canvas = editor.canvas
    node = canvas.nodes[canvas.key("review")]
    before = QPointF(node.pos())
    canvas._gesture = {node.node_id: before}
    node.moveBy(60, 24)
    QTest.keyClick(canvas, Qt.Key_Escape)
    assert node.pos() == before
    errors = []
    canvas.ui_error.connect(errors.append)
    original = canvas.update_edges_for_node
    canvas._gesture = {node.node_id: QPointF(node.pos())}
    def broken(_key):
        raise RuntimeError("Injected connection error")
    monkeypatch.setattr(canvas, "update_edges_for_node", broken)
    node.moveBy(12, 0)
    monkeypatch.setattr(canvas, "update_edges_for_node", original)
    QApplication.processEvents()
    assert errors == ["Injected connection error"]
    assert editor.isVisible()
    node.moveBy(12, 0)
    canvas.commit_layout()
    assert editor.draft.data["metadata"]["canvas_layout"]


def test_500_block_drag_paint_and_property_edit_budget(editor, monkeypatch):
    steps = [block_for_type("delay").instantiate(f"delay_{index}") for index in range(500)]
    editor.draft.data["steps"] = steps
    started = time.perf_counter()
    editor.render()
    canvas = editor.canvas
    canvas.zoom_reset()
    canvas.centerOn(canvas.nodes[canvas.key("delay_0")])
    QApplication.processEvents()
    load_ms = (time.perf_counter() - started) * 1000
    assert len(canvas.nodes) == 502
    node = canvas.nodes[canvas.key("delay_0")]
    node.setSelected(True)
    canvas.edge_updates = 0
    # Guard against accidental serialization/validation/checkpoint work at every move.
    original = editor.checkpoint
    monkeypatch.setattr(editor, "checkpoint", lambda: pytest.fail("Document work during pointer motion"))
    batches = []
    for _ in range(3):
        started = time.perf_counter()
        for index in range(40):
            node.setPos(100 + index * 12, 228)
            canvas.viewport().repaint()
        batches.append((time.perf_counter() - started) * 1000 / 40)
    monkeypatch.setattr(editor, "checkpoint", original)
    assert canvas.edge_updates <= 240, canvas.edge_updates
    drag_ms = min(batches)
    assert drag_ms < 16.7, batches
    edits = []
    for index in range(3):
        started = time.perf_counter()
        editor.fields["description"].setPlainText(f"Wait for stabilization {index}.")
        edits.append((time.perf_counter() - started) * 1000)
    assert min(edits) < 100, edits
    menus = []
    for _ in range(3):
        started = time.perf_counter()
        editor.canvas_context_menu(("block", "delay_0"), QPoint(10, 10), node.pos())
        menus.append((time.perf_counter() - started) * 1000)
    assert min(menus) < 100, menus
    report = {"blocks": 500, "initial_load_ms": round(load_ms, 2), "drag_and_paint_ms": round(drag_ms, 2),
              "property_edit_ms": round(min(edits), 2), "context_menu_ms": round(min(menus), 2),
              "adjacent_edge_updates": canvas.edge_updates}
    output = Path(__file__).resolve().parents[1] / "logs/diagnostics/procedure-canvas-performance.json"
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(report)


def test_canvas_uses_the_original_pa_designer_item_components():
    from azeo_control_trainer.azeo_pa_designer._canvas_primitives import FlowNodeGraphicsItem, FlowEdgeGraphicsItem
    from azeo_control_trainer.azeo_pa_designer.canvas import ProcedureNode
    assert issubclass(ProcedureNode, FlowNodeGraphicsItem)
    assert FlowEdgeGraphicsItem.path_between(QPointF(0, 0), QPointF(20, 50)).elementCount() > 3
    assert ProcedureCanvas.key("review") == "step:review"
    assert START != END


def test_procedure_blocks_use_control_designer_category_headers(editor):
    assert set(PROCEDURE_STEP_CATEGORIES) == {
        block.step_type for block in block_library()
    }
    for step_type, category in PROCEDURE_STEP_CATEGORIES.items():
        assert (
            procedure_block_header_color(step_type)
            == FUNCTION_BLOCK_CATEGORY_COLORS[category]
        )

    editor.draft.data["steps"] = [
        block_for_type("check").instantiate("category_check"),
        block_for_type("complete").instantiate("complete"),
    ]
    editor.render()
    node = editor.canvas.nodes[editor.canvas.key("category_check")]
    assert node.data(41) == procedure_block_header_color("check")

    def header_pixel(selected):
        node.setSelected(selected)
        image = QImage(336, 108, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        node.paint(painter, QStyleOptionGraphicsItem())
        painter.end()
        return image.pixelColor(330, 15)

    expected = QColor(
        FUNCTION_BLOCK_CATEGORY_COLORS[PROCEDURE_STEP_CATEGORIES["check"]]
    )
    unselected = header_pixel(False)
    selected = header_pixel(True)
    assert max(
        abs(actual - target)
        for actual, target in zip(unselected.getRgb(), expected.getRgb())
    ) <= 2
    assert selected == unselected


def test_route_handles_materialize_only_for_selected_connection(editor):
    edges = list(editor.canvas.edges.values())
    assert all(not edge._route_handles for edge in edges)
    edges[0].setSelected(True)
    assert len(edges[0]._route_handles) == 1
    assert edges[0]._route_handles[0].isVisible()
    edges[0].setSelected(False)
    assert not edges[0]._route_handles[0].isVisible()
    assert all(not edge._route_handles for edge in edges[1:])


def context_menu(editor, step_id):
    from PySide6.QtGui import QContextMenuEvent
    canvas = editor.canvas
    node = canvas.nodes[canvas.key(step_id)]
    canvas.ensureVisible(node)
    point = canvas.mapFromScene(node.sceneBoundingRect().center())
    event = QContextMenuEvent(QContextMenuEvent.Mouse, point, canvas.viewport().mapToGlobal(point))
    QApplication.sendEvent(canvas.viewport(), event)
    return editor._context_menu


def action_named(menu, label):
    for action in menu.actions():
        if action.text().split("\t")[0] == label:
            return action
        if action.menu():
            found = action_named(action.menu(), label)
            if found:
                return found


def test_right_click_targets_block_and_properties_rename_share_inspector(editor):
    from azeo_control_trainer.core.presentation.menu_style import MENU_QSS
    menu = context_menu(editor, "record")
    assert menu.styleSheet() == MENU_QSS
    assert editor.selected_step_ids() == ["record"]
    assert action_named(menu, "Properties...")
    action_named(menu, "Properties...").trigger()
    assert editor.fields["description"].hasFocus()
    action_named(menu, "Rename...").trigger()
    assert editor.fields["id"].hasFocus()
    assert editor.fields["id"].selectedText() == "record"


def test_context_clipboard_and_keyboard_share_undoable_selection_commands(editor):
    menu = context_menu(editor, "record")
    action_named(menu, "Copy").trigger()
    menu = context_menu(editor, "review")
    assert action_named(menu, "Paste").isEnabled()
    before = len(editor._undo)
    action_named(menu, "Paste").trigger()
    pasted = editor.draft.data["steps"][1]
    assert pasted["type"] == "operator_comment"
    assert pasted["id"] not in {"review", "record", "complete"}
    assert len(editor._undo) == before + 1
    editor.undo()
    assert len(editor.draft.data["steps"]) == 3
    editor.canvas.setFocus()
    QTest.keyClick(editor.canvas, Qt.Key_D, Qt.ControlModifier)
    assert len(editor.draft.data["steps"]) == 4
    QTest.keyClick(editor.canvas, Qt.Key_Delete)
    assert len(editor.draft.data["steps"]) == 3
    editor.undo()
    assert len(editor.draft.data["steps"]) == 4


def test_parameter_groups_filter_and_field_typing_preserve_native_shortcuts(editor):
    editor.add_type.setCurrentIndex(editor.add_type.findData("azeo.procedure.wait_until"))
    editor.add_step()
    assert {"Quick Configuration", "Timing", "Failure Handling", "Documentation"} <= editor.property_groups.keys()
    before = editor.snapshot()
    editor.property_filter.setText("timeout")
    assert not editor.fields["timeout_sec"].isHidden()
    assert editor.fields["description"].isHidden()
    assert editor.snapshot() == before
    editor.property_filter.clear()
    editor.fields["id"].setFocus()
    QTest.keyClick(editor.fields["id"], Qt.Key_A, Qt.ControlModifier)
    QTest.keyClick(editor.fields["id"], Qt.Key_C, Qt.ControlModifier)
    assert QApplication.clipboard().text() == editor.fields["id"].text()
    assert len(editor.draft.data["steps"]) == 4


def test_multi_block_menu_cut_and_paste_preserve_geometry_and_undo(editor):
    canvas = editor.canvas
    edge = canvas.edges[(canvas.key("review"), canvas.key("record"))]
    edge._route_handle_moved(0, QPointF(550, 330))
    edge.commit_route()
    canvas.select_steps(["review", "record"])
    menu = context_menu(editor, "record")
    assert editor.selected_step_ids() == ["review", "record"]
    assert not action_named(menu, "Rename...").isEnabled()
    before = len(editor._undo)
    action_named(menu, "Cut").trigger()
    assert [step["id"] for step in editor.draft.data["steps"]] == ["complete"]
    assert len(editor._undo) == before + 1
    editor.block_command("paste")
    assert [step["id"] for step in editor.draft.data["steps"]] == ["review_1", "record_1", "complete"]
    route = editor.draft.data["metadata"]["canvas_routes"][0]
    assert (route["source"], route["target"]) == (canvas.key("review_1"), canvas.key("record_1"))
    assert route["points"] == [{"x": 586.0, "y": 366.0}]
    editor.undo()
    editor.undo()
    assert [step["id"] for step in editor.draft.data["steps"]] == ["review", "record", "complete"]


def test_wire_and_library_context_actions_are_driven(editor):
    canvas = editor.canvas
    pair = (canvas.key("review"), canvas.key("record"))
    edge = canvas.edges[pair]
    edge._route_handle_moved(0, QPointF(500, 320))
    edge.commit_route()
    editor.canvas_context_menu(("wire", pair), QPoint(10, 10), QPointF(200, 200))
    action_named(editor._context_menu, "Auto Route").trigger()
    assert not canvas.edges[pair].custom_route
    assert not editor.draft.data["metadata"]["canvas_routes"]
    editor.library_tabs.setCurrentIndex(1)
    item = editor.block_tree.topLevelItem(0).child(0)
    point = editor.block_tree.visualItemRect(item).center()
    editor.palette_context_menu(point)
    before = len(editor.draft.data["steps"])
    action_named(editor._context_menu, "Insert into procedure").trigger()
    assert len(editor.draft.data["steps"]) == before + 1
    action_named(editor._context_menu, "Block Help").trigger()
    assert "azeo.procedure.instruction" in editor.help_center.browser.toPlainText()


def test_empty_and_invalid_clipboard_do_not_modify_draft(editor):
    from azeo_control_trainer.azeo_pa_designer.editing import CLIPBOARD_MIME
    QApplication.clipboard().clear()
    editor.update_block_actions()
    assert not editor.block_actions["paste"].isEnabled()
    before = editor.snapshot()
    mime = QMimeData()
    mime.setData(CLIPBOARD_MIME, b'{"version":1,"steps":[{"id":"x","type":"unknown"}]}')
    QApplication.clipboard().setMimeData(mime)
    editor.block_command("paste")
    assert editor.snapshot() == before
    QApplication.clipboard().clear()
