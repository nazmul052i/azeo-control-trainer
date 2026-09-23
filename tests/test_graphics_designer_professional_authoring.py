"""Contracts for professional display-authoring assistance."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QWheelEvent
from PySide6.QtWidgets import QApplication, QGraphicsSceneMouseEvent
from PySide6.QtTest import QTest

import azeo_control_trainer.core.strategy.blocks  # noqa: F401
from azeo_control_trainer.core.hmi.pvms.base import registry as pvm_registry
from azeo_control_trainer.core.hmi.pvms.publishing import (
    DisplayStore, PvmDisplay,
)
from azeo_control_trainer.azeo_graphics_designer.studio.layers import (
    LayersPane, item_layer, layer_title, set_item_layer,
)
from azeo_control_trainer.core.hmi.pvms.rendering.renderer import pvm_from_dict
from azeo_control_trainer.core.hmi.pvms.rendering.items import (
    PipeItem, StaticItem,
)
from azeo_control_trainer.core.hmi.pvms.rendering.pipe_scene import port_anchor
from azeo_control_trainer.azeo_graphics_designer.studio.binding_editor import (
    BindingKind, BindingTarget, ValueType, make_binding_result,
)
from azeo_control_trainer.core.hmi.pvms.properties import descriptor_for
from azeo_control_trainer.azeo_graphics_designer.window import HmiStudioWindow
from azeo_control_trainer.azeo_graphics_designer.window import _PaletteCard
from azeo_control_trainer.azeo_graphics_designer.param_browser import (
    ParameterBrowserDialog,
)
from azeo_control_trainer.azeo_graphics_designer.studio.browser import (
    ControlBrowser,
)
from azeo_control_trainer.core.strategy.model.block_registry import BlockRegistry
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph


def _application():
    return QApplication.instance() or QApplication([])


def _graph():
    block = BlockRegistry().create("PID", "PID1")
    assert block is not None
    block._apply_config()
    graph = StrategyGraph(name="UNIT100")
    graph.add_block(block)
    return graph


def _window(tmp_path):
    _application()
    graph = _graph()
    return HmiStudioWindow(
        lambda: {"UNIT100": graph}, tmp_path, area_name="Plant")


def _flush_layout():
    for _ in range(4):
        QApplication.processEvents()


def _mapped_page_size(studio):
    page = studio.canvas.page_rect()
    top_left = studio.canvas.mapFromScene(page.topLeft())
    bottom_right = studio.canvas.mapFromScene(page.bottomRight())
    return (abs(bottom_right.x() - top_left.x()),
            abs(bottom_right.y() - top_left.y()))


def _tree_item_with_payload(tree, predicate):
    def walk(item):
        payload = item.data(0, Qt.UserRole)
        if payload and predicate(payload):
            return item
        for index in range(item.childCount()):
            match = walk(item.child(index))
            if match is not None:
                return match
        return None

    for index in range(tree.topLevelItemCount()):
        match = walk(tree.topLevelItem(index))
        if match is not None:
            return match
    return None


def test_page_live_fits_real_viewport_and_refits_when_panes_fold(tmp_path):
    DisplayStore(tmp_path).save_draft(PvmDisplay(
        "Overview", width=1600, height=900, background="#E3E6EA"))
    window = _window(tmp_path)
    window.resize(1600, 900)
    window.show()
    _flush_layout()
    studio = window.current()

    page_width, page_height = _mapped_page_size(studio)
    viewport = studio.canvas.viewport().size()
    assert studio.auto_fit_enabled
    assert page_width > viewport.width() * 0.70
    assert page_height > viewport.height() * 0.55
    assert max(page_width / viewport.width(),
               page_height / viewport.height()) > 0.90
    assert studio.canvas_frame.horizontal_ruler.isVisible()
    assert studio.canvas_frame.vertical_ruler.isVisible()
    inspector_width = studio.pane.width()

    window.dispatch("view.rulers.toggle")
    assert not studio.canvas_frame.horizontal_ruler.isVisible()
    window.dispatch("view.rulers.toggle")
    _flush_layout()
    assert studio.canvas_frame.horizontal_ruler.isVisible()

    window.toggle_left_panels()
    _flush_layout()
    expanded_width, _ = _mapped_page_size(studio)
    assert expanded_width > page_width
    assert abs(studio.pane.width() - inspector_width) <= 2

    window.toggle_left_panels()
    _flush_layout()
    assert abs(studio.pane.width() - inspector_width) <= 2

    studio.enter_test()
    _flush_layout()
    assert not studio.canvas_frame.horizontal_ruler.isVisible()
    assert not studio.canvas_frame.vertical_ruler.isVisible()
    studio.exit_test()
    window.close()


def test_left_tools_share_one_compact_resizable_dock(tmp_path):
    DisplayStore(tmp_path).save_draft(PvmDisplay(
        "Overview", width=1600, height=900, background="#E3E6EA"))
    window = _window(tmp_path)
    window.resize(1600, 900)
    window.show_sidebar("split")
    window.show()
    _flush_layout()

    assert window._body_split.count() == 3
    assert window.left_workspace.count() == 2
    assert window.left_workspace.orientation() == Qt.Vertical
    assert window.left_workspace.indexOf(window.explorer_tabs) == 0
    assert window.left_workspace.indexOf(window.palette_box) == 1
    dock_width = window.left_workspace.width()
    assert 230 <= dock_width <= 300
    assert abs(window.explorer_tabs.width() - window.palette_box.width()) <= 2

    window.set_left_panel_visibility(palette=False)
    _flush_layout()
    assert window.explorer_tabs.height() > 600
    assert abs(window.left_workspace.width() - dock_width) <= 2
    window.set_left_panel_visibility(palette=True)
    _flush_layout()
    assert abs(window.left_workspace.width() - dock_width) <= 2
    window.close()


def test_studio_sidebars_use_consistent_compact_chrome(tmp_path):
    """Dock scrollbars and forms must not fall back to raw Qt chrome."""
    DisplayStore(tmp_path).save_draft(PvmDisplay(
        "Overview", width=1600, height=900, background="#E3E6EA"))
    window = _window(tmp_path)
    window.resize(1600, 900)
    window.show()
    _flush_layout()
    studio = window.current()

    assert window.palette_box.objectName() == "palette_scroll"
    assert (window.palette_box.horizontalScrollBarPolicy()
            == Qt.ScrollBarAlwaysOff)
    assert window.left_workspace.objectName() == "engineering_workspace"
    assert studio.pane.objectName() == "graphics_configuration"
    assert 296 <= studio.pane.minimumWidth() <= studio.pane.maximumWidth()
    assert studio.pane.maximumWidth() <= 360

    section_headers = window.palette_box.findChildren(
        type(window._palette_sections[0][0]), "palette_section")
    assert section_headers
    assert all(header.cursor().shape() == Qt.PointingHandCursor
               for header in section_headers)

    qss = window.styleSheet()
    assert "QScrollBar::add-line:vertical" in qss
    assert "height: 0px" in qss
    # Inspector fields now use the same shared control chrome as the other
    # engineering tools; requiring the removed local selector rejects that UI.
    from azeo_control_trainer.core.presentation.authoring_style import AUTHORING_CONTROLS_QSS
    assert AUTHORING_CONTROLS_QSS in qss
    assert "QPushButton#palette_section" in qss
    window.close()


def test_finite_page_chrome_does_not_change_operator_pixels(tmp_path):
    """The polished pasteboard must stop exactly at the document edge."""
    DisplayStore(tmp_path).save_draft(PvmDisplay(
        "Overview", width=1600, height=900, background="#E3E6EA"))
    window = _window(tmp_path)
    studio = window.current()
    studio.grid_visible = False

    def sample(mode):
        studio.mode = mode
        image = QImage(140, 100, QImage.Format_ARGB32)
        image.fill(QColor("#FF00FF"))
        painter = QPainter(image)
        painter.translate(40, 40)
        studio.canvas.drawBackground(
            painter, QRectF(-40.0, -40.0, 140.0, 100.0))
        painter.end()
        return image.pixelColor(10, 10), image.pixelColor(70, 70)

    edit_outside, edit_inside = sample("edit")
    test_outside, test_inside = sample("test")
    operator_background = QColor("#E3E6EA")
    assert edit_outside != operator_background
    assert edit_inside == operator_background
    assert test_outside == operator_background
    assert test_inside == operator_background
    assert studio.canvas.objectName() == "graphics_canvas"
    assert studio.canvas_frame.objectName() == "canvas_frame"
    window.close()


def test_library_explains_assets_and_exposes_the_next_action(tmp_path):
    window = _window(tmp_path)
    library = window.library

    assert "Reusable project assets" in library.purpose.text()
    assert [library.quick_new_pvm.text(),
            library.quick_new_faceplate.text(),
            library.quick_import_svg.text()] == [
                "New PVM", "New Pair", "Import"]
    assert [library.bottom_tabs.tabText(index)
            for index in range(library.bottom_tabs.count())] == [
                "Actions", "Details", "Coverage"]

    class_item = _tree_item_with_payload(
        library.tree,
        lambda payload: payload[0] == "class"
        and payload[1][1] == "dynamo_compact")
    assert class_item is not None
    library._select_tree_item(class_item)
    assert library.item_state.text() == "INSTALLED"
    assert library.item_primary.text() == "Place on Display"
    assert "Control Tag" in library.item_description.text()
    assert library.item_preview.pixmap() is not None
    assert not library.item_preview.pixmap().isNull()

    library.item_secondary.click()
    assert library.bottom_tabs.currentWidget() is library.details_tab
    library.bottom_tabs.setCurrentWidget(library.actions_tab)

    symbol_item = _tree_item_with_payload(
        library.tree, lambda payload: payload[0] == "special_symbol")
    assert symbol_item is not None
    library._select_tree_item(symbol_item)
    assert library.item_primary.text() == "Place on Display"
    assert "Visibility" in library.item_description.text()

    from azeo_control_trainer.core.hmi.pvms.library_catalog import (
        FACEPLATE_CLASS,
    )
    faceplate_folder = _tree_item_with_payload(
        library.tree, lambda payload: payload == (
            "user_class_folder", FACEPLATE_CLASS))
    assert faceplate_folder is not None
    library._select_tree_item(faceplate_folder)
    assert library.item_primary.text() == "New PVM + Faceplate"
    assert "paired" in library.item_description.text().lower()
    window.close()


def test_cad_wheel_zoom_and_ruler_alignment_markers(tmp_path):
    DisplayStore(tmp_path).save_draft(PvmDisplay(
        "Overview", width=1600, height=900, background="#E3E6EA"))
    window = _window(tmp_path)
    window.resize(1200, 800)
    window.show()
    _flush_layout()
    studio = window.current()
    item = studio.add_static("rect", 100, 120, 80, 40)
    item.setSelected(True)
    _flush_layout()

    selected, guides = studio.canvas_frame.horizontal_ruler.marker_values()
    assert len(selected) == 3
    assert not guides
    studio.canvas._smart_guides = [("x", 200.0), ("y", 300.0)]
    _, guides = studio.canvas_frame.horizontal_ruler.marker_values()
    assert guides == [200.0]

    before = studio.canvas.transform().m11()
    studio.canvas.wheelEvent(QWheelEvent(
        QPointF(100, 100), QPointF(100, 100), QPoint(0, 0),
        QPoint(0, 120), Qt.NoButton, Qt.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False))
    assert studio.canvas.transform().m11() > before
    assert not studio.auto_fit_enabled
    manual_scale = studio.canvas.transform().m11()
    studio.canvas.resize(studio.canvas.width() + 100,
                         studio.canvas.height())
    _flush_layout()
    assert abs(studio.canvas.transform().m11() - manual_scale) < 1e-6

    studio.fit_drawing()
    assert studio.auto_fit_enabled
    window.close()


def test_smart_guides_size_and_page_containment(tmp_path):
    window = _window(tmp_path)
    studio = window.current()
    studio.display.width = 320
    studio.display.height = 200
    first = studio.add_static("rect", 10, 20, 40, 30)
    second = studio.add_static("rect", 100, 80, 70, 50)
    second.setSelected(True)

    studio.gesture_checkpoint()
    snapped = studio.canvas.snap_item_position(second, QPointF(45, 80))
    assert snapped.x() == 50  # first element's right edge
    assert ("x", 50) in studio.canvas._smart_guides

    first.setSelected(True)
    assert studio.equalize_selected("both") == 1
    assert second.rect().size() == first.rect().size()
    second.setPos(500, 500)
    assert studio.contain_selected_in_page() >= 1
    safe = studio.canvas.page_rect().adjusted(24, 24, -24, -24)
    assert safe.contains(second.mapRectToScene(second.rect()))
    window.close()


def test_align_uses_visible_process_artwork_and_key_object(tmp_path):
    """Mixed symbols align by their ink and keep connections attached."""
    from azeo_control_trainer.azeo_graphics_designer.studio.selection import (
        visual_scene_rect,
    )

    window = _window(tmp_path)
    studio = window.current()
    pump = studio.add_static(
        "symbol", 80, 90, 120, 90, symbol="pump")
    furnace = studio.add_static(
        "symbol", 330, 210, 180, 210, symbol="furnace")
    pipe = studio.add_pipe(pump, "e", furnace, "w")
    studio.selection.replace((pump, furnace), primary=furnace)

    key_position = QPointF(furnace.pos())
    assert studio.align_selected("vcenter") == 2
    assert furnace.pos() == key_position
    assert abs(visual_scene_rect(pump).center().y()
               - visual_scene_rect(furnace).center().y()) < 0.01

    # The connected line is rerouted in the same atomic operation; Align
    # must never leave the line at the equipment's former location.
    end = pipe._points[-1]
    anchor = furnace.anchor("w")
    assert abs(end.x() - anchor.x()) < 0.01
    assert abs(end.y() - anchor.y()) < 0.01
    window.close()


def test_straighten_connected_run_aligns_pump_valve_heater_endpoints(
        tmp_path):
    """The inline key stays fixed while its neighbour ports become one run."""
    window = _window(tmp_path)
    studio = window.current()
    pump = studio.add_static(
        "symbol", 288, 480, 72, 56, symbol="pump")
    valve_data = studio.place_block(
        "UNIT100/XV1", "DEVCTL", 368, 470,
        role=("dynamo_compact", "hp_b_valve"))
    valve = next(item for item in studio._items()
                 if item.pvm.id == valve_data.id)
    heater = studio.add_static(
        "symbol", 464, 368, 168, 204, symbol="furnace")
    left = studio.add_pipe(pump, "e", valve, "w")
    right = studio.add_pipe(valve, "e", heater, "w")
    pump_before = QPointF(pump.pos())
    valve_before = QPointF(valve.pos())
    heater_before = QPointF(heater.pos())

    # The command resolves the shared valve from either the valve itself or
    # the two pipe runs, matching how engineers commonly select a line pair.
    studio.selection.replace((left, right), primary=right)
    assert studio.connected_run_axis() == "horizontal"
    assert studio.straighten_connected_run() == 2

    endpoint_y = [left._points[0].y(), left._points[-1].y(),
                  right._points[0].y(), right._points[-1].y()]
    assert max(endpoint_y) - min(endpoint_y) < 0.02
    assert all(abs(point.y() - endpoint_y[0]) < 0.02
               for pipe in (left, right) for point in pipe._points)
    assert valve.pos() == valve_before
    assert pump.pos() != pump_before or heater.pos() != heater_before
    assert "route_points" not in left.data
    assert "route_points" not in right.data
    window.close()


def test_selected_pipe_aligns_svg_declared_ports_and_is_undoable(tmp_path):
    """Straight-line alignment uses the symbol library's real nozzles."""
    from azeo_control_trainer.core.hmi.pvms.symbols import connection_ports

    pump_ports = connection_ports("pump")
    valve_ports = connection_ports("control_valve")
    assert pump_ports["e"][1] < 0.3  # centrifugal discharge is high
    assert abs(valve_ports["w"][1] - 74.5 / 108) < 1e-6

    window = _window(tmp_path)
    studio = window.current()
    pump = studio.add_static(
        "symbol", 120, 240, 120, 90, symbol="pump")
    valve = studio.add_static(
        "symbol", 420, 360, 110, 90, symbol="control_valve")
    pipe = studio.add_pipe(pump, "outlet", valve, "inlet")
    valve_before = QPointF(valve.pos())
    pump_id = pump.data["id"]
    valve_id = valve.data["id"]
    studio.selection.replace((pipe,), primary=pipe)

    assert studio.can_align_pipe_endpoints()
    assert studio.align_pipe_endpoints() == 1
    assert pump.pos() == QPointF(120, 240)
    assert valve.pos() != valve_before
    assert abs(pump.anchor("outlet").y()
               - valve.anchor("inlet").y()) < 0.001
    assert max(point.y() for point in pipe._points) \
        - min(point.y() for point in pipe._points) < 0.001

    assert studio.undo()
    restored = {item.data["id"]: item for item in studio._static_items()}
    assert restored[pump_id].pos() == QPointF(120, 240)
    assert restored[valve_id].pos() == valve_before
    window.close()


def test_drawing_shortcut_menu_has_one_compact_command_hierarchy(tmp_path):
    """Context commands appear once, grouped by the engineer's task."""
    window = _window(tmp_path)
    studio = window.current()
    symbol = studio.add_static(
        "symbol", 120, 140, 100, symbol="pump")
    symbol.setSelected(True)
    studio.drawing_context_menu(symbol, QPoint(1, 1))
    menu = studio._context_menu

    def labels(owner, *, recursive=False):
        result = []
        for action in owner.actions():
            if action.isSeparator() or not action.text():
                continue
            result.append(action.text().split("\t", 1)[0])
            if recursive and action.menu() is not None:
                result.extend(labels(action.menu(), recursive=True))
        return result

    top = labels(menu)
    assert top == [
        "Format...", "Style", "Cut", "Copy", "Duplicate", "Arrange",
        "Transform", "Connections", "Reusable Component", "Hide", "Lock",
        "Delete",
    ]
    all_labels = labels(menu, recursive=True)
    for command in ("Line colour…", "Copy style", "Paste style",
                    "Duplicate", "Group", "Lock", "Delete"):
        assert all_labels.count(command) == 1
    assert len(top) <= 12

    pipe_target = studio.add_static(
        "symbol", 380, 140, 100, symbol="control_valve")
    pipe = studio.add_pipe(symbol, "outlet", pipe_target, "inlet")
    studio.canvas.scene().clearSelection()
    pipe.setSelected(True)
    studio.drawing_context_menu(pipe, QPoint(1, 1))
    pipe_top = labels(studio._context_menu)
    assert "Routing" in pipe_top
    assert "Connections" not in pipe_top
    assert pipe_top.count("Routing") == 1
    window.close()


def test_parameter_browser_searches_and_returns_exact_address(tmp_path):
    """Data Link selection is parameter-first; PVM selection remains block-first."""
    _application()
    graph = _graph()

    def provider():
        return {"UNIT100": graph}

    parameters = ParameterBrowserDialog(
        provider, "UNIT100/PID1/PV", selection="parameter")
    assert parameters.selected_path == "UNIT100/PID1/PV"
    assert parameters.selected_type
    assert parameters.ok_button.isEnabled()
    assert parameters.width() >= 620

    parameters.search.setText("pid1 config lo_lim")
    matches = [
        item for item in parameters._items()
        if not item.isHidden()
        and (ControlBrowser.parameter_payload(item) or {}).get("path", "")
        .lower().endswith("/config/lo_lim")]
    assert len(matches) == 1
    parameters.browser.setCurrentItem(matches[0])
    assert parameters.selected_path.lower().endswith("/config/lo_lim")
    assert parameters.selected_kind == "parameter"
    assert parameters.path_label.text() == parameters.selected_path

    # Selecting its parent block cannot accidentally truncate a Data Link.
    block = matches[0].parent().parent()
    parameters.browser.setCurrentItem(block)
    assert not parameters.selected_path
    assert not parameters.ok_button.isEnabled()
    parameters.close()

    objects = ParameterBrowserDialog(
        provider, "UNIT100/PID1", selection="block")
    assert objects.selected_path == "UNIT100/PID1"
    assert objects.selected_type == "PID"
    assert objects.selected_kind == "block"
    assert objects.ok_button.isEnabled()
    objects.close()


def test_align_to_page_supports_independent_centres(tmp_path):
    window = _window(tmp_path)
    studio = window.current()
    studio.display.width = 800
    studio.display.height = 450
    item = studio.add_static("rect", 50, 80, 100, 60)
    item.setSelected(True)
    safe = studio.canvas.page_rect().adjusted(24, 24, -24, -24)

    assert studio.align_selected_to_page("hcenter") == 1
    assert abs(item.mapRectToScene(item.rect()).center().x()
               - safe.center().x()) < 0.01
    assert studio.align_selected_to_page("vcenter") == 1
    assert abs(item.mapRectToScene(item.rect()).center().y()
               - safe.center().y()) < 0.01
    window.close()


def test_align_preserves_group_internal_geometry(tmp_path):
    window = _window(tmp_path)
    studio = window.current()
    first = studio.add_static("rect", 50, 80, 40, 30)
    second = studio.add_static("rect", 110, 110, 40, 30)
    key = studio.add_static("rect", 350, 240, 80, 60)
    studio.selection.replace((first, second), primary=second)
    assert studio.group_selected()
    original_delta = second.pos() - first.pos()

    studio.selection.replace((first, second, key), primary=key)
    assert studio.align_selected("vcenter") == 3
    assert second.pos() - first.pos() == original_delta
    window.close()


def test_ruler_drag_creates_persistent_non_published_snap_guide(tmp_path):
    DisplayStore(tmp_path).save_draft(PvmDisplay(
        "Overview", width=800, height=450, background="#E3E6EA"))
    window = _window(tmp_path)
    window.resize(1200, 800)
    window.show()
    _flush_layout()
    studio = window.current()
    ruler = studio.canvas_frame.horizontal_ruler

    canvas_global = studio.canvas.viewport().mapToGlobal(QPoint(180, 150))
    release_on_ruler = ruler.mapFromGlobal(canvas_global)
    QTest.mousePress(ruler, Qt.LeftButton, Qt.NoModifier,
                     QPoint(90, ruler.height() // 2))
    QTest.mouseRelease(ruler, Qt.LeftButton, Qt.NoModifier,
                       release_on_ruler)
    _flush_layout()

    assert len(studio.authoring_guides) == 1
    axis, coordinate = studio.authoring_guides[0]
    assert axis == "x"
    assert coordinate % 8 == 0  # ordinary ruler drops honor grid snap
    assert not studio.unsaved
    assert "authoring_guides" not in studio.display.to_dict()

    from azeo_control_trainer.azeo_graphics_designer.studio.authoring_state import (
        load_guides,
    )
    assert load_guides(tmp_path, "Overview") == [(axis, coordinate)]
    marker_guides = ruler.marker_values()[1]
    assert coordinate in marker_guides

    item = studio.add_static("rect", coordinate - 39, 80, 40, 30)
    item.setSelected(True)
    studio.gesture_checkpoint()
    snapped = studio.canvas.snap_item_position(item, item.pos())
    assert snapped.x() == coordinate - 40  # right edge lands on the guide
    window.close()

    # Guides are a workstation sidecar: reopen restores them, while the
    # operator-facing draft remains the original document schema.
    reopened = _window(tmp_path)
    assert reopened.current().authoring_guides == [(axis, coordinate)]
    assert reopened.current().clear_authoring_guides() == 1
    assert load_guides(tmp_path, "Overview") == []
    reopened.close()


def test_zoom_to_selection_and_live_geometry_status(tmp_path):
    DisplayStore(tmp_path).save_draft(PvmDisplay(
        "Overview", width=1600, height=900, background="#E3E6EA"))
    window = _window(tmp_path)
    window.resize(1200, 800)
    window.show()
    _flush_layout()
    studio = window.current()
    item = studio.add_static("rect", 100, 200, 80, 40)
    item.setSelected(True)
    _flush_layout()

    readout = studio.geometry_readout()
    assert readout == "SEL  X 100  Y 200  W 80  H 40"
    assert window._segments["geometry"].text() == readout
    window.ribbon_tabs.setCurrentIndex(list(window._RIBBON).index("View"))
    _flush_layout()
    assert window._ribbon_buttons["view.zoom.selection"].text()
    assert any(shortcut.key().toString() == "Shift+F"
               for shortcut in window._shortcuts)

    assert studio.fit_selection()
    assert not studio.auto_fit_enabled
    assert 10 <= studio.zoom_percent <= 800
    item_center = studio.canvas.mapFromScene(
        studio._authored_bounds(item).center())
    viewport_center = studio.canvas.viewport().rect().center()
    assert abs(item_center.x() - viewport_center.x()) <= 2
    assert abs(item_center.y() - viewport_center.y()) <= 2

    studio.canvas.scene().clearSelection()
    studio.update_geometry_readout(QPointF(12.5, -4))
    assert window._segments["geometry"].text() \
        == "CURSOR  X 12.5  Y -4"
    window.close()


def test_unified_binding_apply_is_one_undoable_authoring_action(tmp_path):
    window = _window(tmp_path)
    studio = window.current()
    item = studio.add_static("rect", 80, 90, 120, 60)
    item.data["fill_pct"] = 35
    studio.pane.show_item(item)
    studio.pane.anim_prop.setCurrentText("fill_pct")
    before = len(studio._undo_stack)
    catalog = studio.pane._binding_catalog()
    result = make_binding_result(
        BindingTarget("fill_pct", ValueType.NUMBER, item.data["id"]),
        BindingKind.DIRECT_TAG, source="UNIT100/PID1/PV",
        source_type=ValueType.NUMBER, catalog=catalog)

    assert studio.pane._apply_binding_result(result)
    assert len(studio._undo_stack) == before + 1
    assert descriptor_for(item.data, "fill_pct") == {
        "kind": "animation", "path": "UNIT100/PID1/PV", "type": "value"}
    assert item.data["fill_pct_static"] == 35
    window.close()


def test_failed_class_master_save_keeps_dirty_recovery(tmp_path, monkeypatch):
    window = _window(tmp_path)
    studio = window.open_display("_pvm_EmptyClass")
    studio.unsaved = True
    monkeypatch.setattr(studio, "_capture_pvm_class", lambda: False)

    assert studio.save_draft() is False
    assert studio.unsaved
    assert (studio.store._dir("_pvm_EmptyClass")
            / ".recovery.json").exists()
    window.close()


def test_layers_persist_for_pvms_and_drawing_items(tmp_path):
    window = _window(tmp_path)
    studio = window.current()
    drawing = studio.add_static("symbol", 20, 20, symbol="pump")
    drawing.setSelected(True)
    layers = LayersPane(studio)
    assert layer_title("pvms") == "PVMs"
    assert "PVMs" in [layers.topLevelItem(index).text(0)
                       for index in range(layers.topLevelItemCount())]
    from azeo_control_trainer.core.hmi import compatibility as compat
    assert compat.LEGACY_SCOPE_NAME + "s" not in [
        layers.topLevelItem(index).text(0) for index in range(layers.topLevelItemCount())]
    assert layers.assign_selected("equipment") == 1
    assert item_layer(drawing) == "equipment"

    pvm = studio.place_block(
        "UNIT100/PID1", "PID", 100, 100,
        role=("dynamo_compact", ""))
    placed = next(item for item in studio._items() if item.pvm.id == pvm.id)
    assert not placed.shows_selection_frame()
    assert not placed.shows_operational_alarm_box()
    assert not drawing.shows_selection_frame()
    assert studio.add_static("rect", 240, 20, 40, 30) \
        .shows_selection_frame()
    drawing.setSelected(False)
    placed.setSelected(True)
    assert layers.assign_selected("annotation") == 1
    restored = pvm_from_dict(placed.pvm.to_dict())
    assert restored.layer == "annotation"
    set_item_layer(placed, "PVMs")
    assert item_layer(placed) == "pvms"
    studio.enter_test()
    assert placed.shows_operational_alarm_box()
    studio.exit_test()
    window.close()


def test_test_data_preview_never_changes_live_source(tmp_path):
    window = _window(tmp_path)
    studio = window.current()
    item = studio.add_static("datalink", 20, 20, 100, 28)
    item.data.update({"path": "UNIT100/PID1/OUT",
                      "datalink_type": "numeric"})
    studio.rebind_static(item)
    live = studio.preview_source.source.read("UNIT100/PID1/OUT").value

    studio.enter_test()
    studio.preview_source.set_override(
        "UNIT100/PID1/OUT", value=77.0, quality="GOOD",
        alarm_active=True, alarm_priority=15)
    studio.engine.poll()
    assert item.binding.result.value == 77.0
    assert item.binding.result.alarm_active
    assert studio.preview_source.source.read("UNIT100/PID1/OUT").value == live

    studio.exit_test()
    assert item.binding.result.value == live
    window.close()


def test_recovery_is_newer_than_draft_and_clears_on_save(tmp_path):
    store = DisplayStore(tmp_path)
    draft = PvmDisplay("Overview", description="saved")
    store.save_draft(draft)
    checkpoint = PvmDisplay("Overview", description="unsaved")
    store.save_recovery(checkpoint)
    recovery = store.load_recovery("Overview")
    assert recovery is not None
    assert recovery.description == "unsaved"
    store.clear_recovery("Overview")
    assert store.load_recovery("Overview") is None


def test_magnetic_ports_snap_and_store_exact_routed_endpoints(tmp_path):
    window = _window(tmp_path)
    studio = window.current()
    source = studio.add_static("rect", 80, 100, 100, 70)
    target = studio.add_static("rect", 360, 100, 100, 70)
    source.setSelected(True)

    actual = source.mapFromScene(source.anchor("e"))
    handle = source.connection_handle("e")
    assert handle.x() > actual.x()  # connector clears the resize frame
    # Selection is a transform state, not an implicit connector state.  The
    # outer port becomes hittable only when the engineer arms Connect.
    assert not source.shape().contains(handle)
    studio.connect_armed = True
    assert source.shape().contains(handle)
    studio.connect_armed = False

    studio.start_anchor_drag(source, "e")
    near_target = target.anchor("w") + QPointF(-8, 0)
    studio.drag_anchor_to(near_target)
    assert studio._connect_target == (target, "w")
    assert target._connect_hot_anchor == "w"
    studio.finish_anchor_drag(near_target, exclude=source)

    pipes = [item for item in studio.canvas.scene().items()
             if isinstance(item, PipeItem)]
    assert len(pipes) == 1
    assert pipes[0].data["a_side"] == "e"
    assert pipes[0].data["b_side"] == "w"
    assert pipes[0].data["corner_radius"] == 7.0
    assert studio.interaction_mode == "select"
    window.close()


def test_selected_symbol_handles_resize_width_height_or_both(tmp_path):
    """The real discharge port must not steal a transform handle."""
    window = _window(tmp_path)
    studio = window.current()
    symbol = studio.add_static("symbol", 120, 140, 100, symbol="pump")
    symbol.setSelected(True)

    east = symbol.handle_points()["e"]
    outlet = symbol.mapFromScene(symbol.anchor("outlet"))
    # The SVG's real pump discharge is high on the east side, while the width
    # handle remains at the transform frame's midpoint.  They are separate
    # affordances and either one can be acquired without stealing the other.
    assert symbol.anchor_hit(outlet) in ("e", "outlet")
    assert (outlet - east).manhattanLength() > 12
    assert symbol.anchor_hit(east) is None
    press = QGraphicsSceneMouseEvent(QEvent.GraphicsSceneMousePress)
    press.setPos(east)
    press.setScenePos(symbol.mapToScene(east))
    press.setButton(Qt.LeftButton)
    press.setButtons(Qt.LeftButton)
    symbol.mousePressEvent(press)
    assert symbol._resizing
    assert symbol._resize_handle == "e"
    assert not getattr(symbol, "_anchor_dragging", False)
    symbol._resizing = False
    symbol._resize_handle = None
    studio.cancel_gesture()

    old_w, old_h = symbol.rect().width(), symbol.rect().height()
    symbol._resize_to(
        "e", symbol.mapToScene(QPointF(old_w + 40, old_h / 2)),
        bypass_snap=True)
    assert symbol.rect().width() > old_w
    assert symbol.rect().height() == old_h

    width_after = symbol.rect().width()
    symbol._resize_to(
        "s", symbol.mapToScene(QPointF(width_after / 2, old_h + 35)),
        bypass_snap=True)
    assert symbol.rect().width() == width_after
    assert symbol.rect().height() > old_h

    before_corner = (symbol.rect().width(), symbol.rect().height())
    symbol._resize_to(
        "se", symbol.mapToScene(QPointF(
            before_corner[0] + 30, before_corner[1] + 30)),
        bypass_snap=True)
    assert symbol.rect().width() > before_corner[0]
    assert symbol.rect().height() > before_corner[1]
    window.close()


def test_tiny_valve_first_click_selects_then_handle_resizes(tmp_path):
    """A compact glyph must not be swallowed by its connector hit band."""
    window = _window(tmp_path)
    studio = window.current()
    pvm = studio.place_block(
        "UNIT100/DEV1", "DEVCTL", 180, 120,
        role=("dynamo_compact", "hp_b_valve"))
    assert pvm_registry.get(
        "DEVCTL", "dynamo_compact", "hp_b_valve").RESIZABLE is True
    valve = next(one for one in studio._items() if one.pvm.id == pvm.id)
    studio.canvas.scene().clearSelection()

    # The east outline is intentionally a valid connector source. Before the
    # selection-first rule, this first press started a pipe and the small PVM
    # could never reach its resize handles.
    local = valve.mapFromScene(valve.anchor("e"))
    scene = valve.mapToScene(local)
    assert valve.connection_anchor_at(scene, pixels=12) is not None
    press = QGraphicsSceneMouseEvent(QEvent.GraphicsSceneMousePress)
    press.setPos(local)
    press.setScenePos(scene)
    press.setButton(Qt.LeftButton)
    press.setButtons(Qt.LeftButton)
    valve.mousePressEvent(press)
    assert valve.isSelected()
    assert not getattr(valve, "_anchor_dragging", False)
    assert getattr(valve, "_pending_anchor_drag", None) is not None
    assert getattr(studio, "_connect_pending", None) is None

    release = QGraphicsSceneMouseEvent(QEvent.GraphicsSceneMouseRelease)
    release.setPos(local)
    release.setScenePos(scene)
    release.setButton(Qt.LeftButton)
    valve.mouseReleaseEvent(release)
    assert getattr(valve, "_pending_anchor_drag", None) is None

    # A selected compact valve must remain an ordinary movable object.  Its
    # screen-space connector band used to consume almost every body press.
    body = valve.rect().center()
    body_press = QGraphicsSceneMouseEvent(QEvent.GraphicsSceneMousePress)
    body_press.setPos(body)
    body_press.setScenePos(valve.mapToScene(body))
    body_press.setButton(Qt.LeftButton)
    body_press.setButtons(Qt.LeftButton)
    valve.mousePressEvent(body_press)
    assert not getattr(valve, "_anchor_dragging", False)
    assert getattr(valve, "_pending_anchor_drag", None) is None
    body_release = QGraphicsSceneMouseEvent(QEvent.GraphicsSceneMouseRelease)
    body_release.setPos(body)
    body_release.setScenePos(valve.mapToScene(body))
    body_release.setButton(Qt.LeftButton)
    valve.mouseReleaseEvent(body_release)

    old_width = valve.rect().width()
    east = valve.handle_points()["e"]
    resize_press = QGraphicsSceneMouseEvent(QEvent.GraphicsSceneMousePress)
    resize_press.setPos(east)
    resize_press.setScenePos(valve.mapToScene(east))
    resize_press.setButton(Qt.LeftButton)
    resize_press.setButtons(Qt.LeftButton)
    valve.mousePressEvent(resize_press)
    assert valve._resizing and valve._resize_handle == "e"
    valve._resize_to(
        "e", valve.mapToScene(QPointF(east.x() + 36, east.y())),
        bypass_snap=True)
    assert valve.rect().width() == old_width + 36
    window.close()


def test_connector_starts_and_ends_anywhere_on_symbol_perimeters(tmp_path):
    """Free edge points are normalized, persistent symbol attachments."""
    window = _window(tmp_path)
    studio = window.current()
    source = studio.add_static(
        "symbol", 100, 120, 140, h=110, symbol="pump")
    target = studio.add_static(
        "symbol", 430, 130, 150, h=190, symbol="vessel")
    studio.canvas.scene().clearSelection()

    source_rect = source._content_rect()
    source_local = QPointF(
        source_rect.left() + source_rect.width() * 0.27,
        source_rect.top())
    source_scene = source.mapToScene(source_local)
    press = QGraphicsSceneMouseEvent(QEvent.GraphicsSceneMousePress)
    press.setPos(source_local)
    press.setScenePos(source_scene)
    press.setButton(Qt.LeftButton)
    press.setButtons(Qt.LeftButton)
    source.mousePressEvent(press)

    # An outline press is intentionally ambiguous until movement: click means
    # select, drag means connect. Cross the small screen-space drag threshold.
    move = QGraphicsSceneMouseEvent(QEvent.GraphicsSceneMouseMove)
    move.setPos(source_local + QPointF(12, 12))
    move.setScenePos(source_scene + QPointF(12, 12))
    move.setButtons(Qt.LeftButton)
    source.mouseMoveEvent(move)

    pending_item, source_anchor = studio._connect_pending
    assert pending_item is source
    assert source_anchor.startswith("outline:")
    # SVG alpha geometry projects the pointer to real visible ink. It may be
    # a few raster pixels from the old rectangular content-box probe.
    assert (source.anchor(source_anchor) - source_scene).manhattanLength() < 12

    target_rect = target._content_rect()
    target_local = QPointF(
        target_rect.left(),
        target_rect.top() + target_rect.height() * 0.74)
    target_scene = target.mapToScene(target_local)
    studio.drag_anchor_to(target_scene)
    assert studio._connect_target[0] is target
    target_anchor = studio._connect_target[1]
    assert target_anchor.startswith("outline:")
    studio.finish_anchor_drag(target_scene, exclude=source)
    source._anchor_dragging = False

    pipes = [item for item in studio.canvas.scene().items()
             if isinstance(item, PipeItem)]
    assert len(pipes) == 1
    pipe = pipes[0]
    assert not pipe.data["auto"]
    assert pipe.data["a_side"] == source_anchor
    assert pipe.data["b_side"] == target_anchor
    assert (target.anchor(target_anchor) - target_scene).manhattanLength() < 12

    # Normalized placement follows both movement and a dimension change.
    old_source_point = source.anchor(source_anchor)
    source.setPos(source.pos() + QPointF(35, 20))
    assert source.anchor(source_anchor) == old_source_point + QPointF(35, 20)
    old_target_point = target.anchor(target_anchor)
    old_height = target.rect().height()
    target._resize_to(
        "s", target.mapToScene(QPointF(
            target.rect().center().x(), old_height + 80)),
        bypass_snap=True)
    assert target.anchor(target_anchor).x() == old_target_point.x()
    assert target.anchor(target_anchor).y() > old_target_point.y()
    window.close()


def test_every_palette_symbol_exposes_four_visible_process_ports():
    """Generated catalog metadata may never point into transparent padding."""
    import math

    from azeo_control_trainer.core.hmi.pvms import symbols
    from azeo_control_trainer.core.hmi.pvms.elements import default_symbol_ports

    _application()
    for name in symbols.CATALOG:
        ports = symbols.connection_ports(name)
        outline = symbols.outline_points(name)
        assert set(ports) == {"n", "e", "s", "w"}, name
        for side, point in ports.items():
            distance = min(
                math.hypot(point[0] - sample[0],
                           point[1] - sample[1])
                for sample in outline)
            assert distance <= 0.035, (name, side, point, distance)

        # Exercise the same StaticItem contract used by Studio and Station,
        # not only the XML metadata. Every semantic nozzle must be hittable
        # and must preserve the expected orthogonal departure direction.
        item = StaticItem({
            "kind": "symbol", "symbol": name,
            "w": 160.0, "h": 160.0 * symbols.aspect(name),
            "ports": default_symbol_ports(name),
        }, {})
        for semantic, normal in (("inlet", "w"), ("outlet", "e"),
                                 ("top", "n"), ("bottom", "s")):
            anchor = item.anchor(semantic)
            assert item.connection_anchor_at(anchor, pixels=10) is not None
            assert port_anchor(item, semantic).normal == normal


def test_exchanger_ports_migrate_and_transform_with_the_symbol(tmp_path):
    """Old viewport ports heal; mirror/rotation also changes route normals."""
    from azeo_control_trainer.core.hmi.pvms.elements import default_symbol_ports
    from azeo_control_trainer.core.hmi.pvms.symbols import (
        connection_ports,
        legacy_viewport_ports,
    )

    window = _window(tmp_path)
    studio = window.current()
    exchanger = studio.add_static(
        "symbol", 180, 160, 180, 120, symbol="heater")

    fresh = default_symbol_ports("heater")
    assert all(port.get("source") == "catalog" for port in fresh)
    current = connection_ports("heater")
    old = legacy_viewport_ports("heater")
    assert abs(old["w"][0] - current["w"][0]) > 0.05

    # Simulate a display saved before the geometry-derived-port correction.
    cardinal = {"inlet": "w", "outlet": "e",
                "top": "n", "bottom": "s"}
    exchanger.data["ports"] = [
        {"name": name, "x": old[side][0], "y": old[side][1],
         "normal": side}
        for name, side in cardinal.items()
    ]
    inlet = exchanger.mapFromScene(exchanger.anchor("inlet"))
    assert abs(inlet.x() / exchanger.rect().width()
               - current["w"][0]) < 1e-6

    exchanger.data["mx"] = True
    mirrored = port_anchor(exchanger, "inlet")
    assert mirrored.normal == "e"
    assert mirrored.point.x > exchanger.mapToScene(
        exchanger.rect().center()).x()

    exchanger.setTransformOriginPoint(exchanger.rect().center())
    exchanger.setRotation(90)
    rotated = port_anchor(exchanger, "inlet")
    assert rotated.normal == "s"
    window.close()


def test_named_off_page_streams_establish_direction_and_stay_attached(
        tmp_path):
    """Incoming/outgoing continuations are semantic P&ID objects."""
    from azeo_control_trainer.core.hmi.pvms.elements import validate_data_element

    window = _window(tmp_path)
    studio = window.current()
    incoming = studio.add_stream_connector(
        "incoming", 70, 170, text="FROM FEED HEADER")
    vessel = studio.add_static(
        "symbol", 350, 120, 150, symbol="vessel")
    outgoing = studio.add_stream_connector(
        "outgoing", 680, 170, text="TO COMPRESSION")

    assert incoming.anchor_sides() == ["process"]
    assert outgoing.anchor_sides() == ["process"]
    assert incoming.connection_anchor_at(
        incoming.mapToScene(incoming.rect().center()),
        allow_inside=True) == "process"
    assert not validate_data_element(incoming.data)

    # Drag order cannot accidentally reverse process direction. Incoming is
    # normalized to A/source; outgoing is normalized to B/destination.
    feed = studio.add_pipe(vessel, "inlet", incoming, "process")
    product = studio.add_pipe(outgoing, "process", vessel, "outlet")
    assert feed.data["a"] == incoming.data["id"]
    assert feed.data["b"] == vessel.data["id"]
    assert product.data["a"] == vessel.data["id"]
    assert product.data["b"] == outgoing.data["id"]
    assert feed.data["arrow_end"] == "filled_arrow"
    assert product.data["arrow_end"] == "filled_arrow"
    assert feed._points[0] == incoming.anchor("process")
    assert product._points[-1] == outgoing.anchor("process")

    old_end = QPointF(product._points[-1])
    outgoing.setPos(outgoing.pos() + QPointF(45, 25))
    assert product._points[-1] == outgoing.anchor("process")
    assert product._points[-1] != old_end

    # Rotation changes both the symbol and its route departure normal.
    outgoing.data["rot"] = 90
    outgoing.apply_rotation()
    assert port_anchor(outgoing, "process").normal == "n"
    window.close()


def test_double_click_opens_format_text_for_streams_and_equipment(tmp_path):
    """One direct-edit gesture serves chevrons and process equipment."""
    window = _window(tmp_path)
    window.resize(1400, 850)
    window.show()
    _flush_layout()
    studio = window.current()
    incoming = studio.add_stream_connector(
        "incoming", 80, 80, text="INCOMING STREAM")

    event = QGraphicsSceneMouseEvent(QEvent.GraphicsSceneMouseDoubleClick)
    event.setPos(incoming.rect().center())
    event.setScenePos(incoming.mapToScene(incoming.rect().center()))
    event.setButton(Qt.LeftButton)
    event.setButtons(Qt.LeftButton)
    incoming.mouseDoubleClickEvent(event)
    _flush_layout()

    assert event.isAccepted()
    assert studio.selection.snapshot().primary is incoming
    assert studio.pane._current_static is incoming
    assert studio.pane.text_holder.isVisible()
    assert window.ribbon_tabs.tabText(
        window.ribbon_tabs.currentIndex()) == "Format"
    text_field = studio.pane.item_fields["text"]
    assert text_field.hasFocus()
    assert text_field.selectedText() == "INCOMING STREAM"
    text_field.setText("FROM FEED HEADER")
    text_field.editingFinished.emit()
    assert incoming.data["text"] == "FROM FEED HEADER"

    equipment = studio.add_static(
        "symbol", 350, 100, 150, symbol="vessel")
    equipment_event = QGraphicsSceneMouseEvent(
        QEvent.GraphicsSceneMouseDoubleClick)
    equipment_event.setPos(equipment.rect().center())
    equipment_event.setScenePos(
        equipment.mapToScene(equipment.rect().center()))
    equipment_event.setButton(Qt.LeftButton)
    equipment_event.setButtons(Qt.LeftButton)
    equipment.mouseDoubleClickEvent(equipment_event)
    _flush_layout()

    assert equipment_event.isAccepted()
    assert studio.selection.snapshot().primary is equipment
    assert studio.pane._current_static is equipment
    assert studio.pane.text_holder.isVisible()
    text_field = studio.pane.item_fields["text"]
    text_field.setText("V-101")
    text_field.editingFinished.emit()
    assert equipment.data["text"] == "V-101"
    assert equipment.data.get("text_halign", "center") == "center"
    window.close()


def test_selected_pipe_endpoint_rebinds_to_visible_symbol_outline(tmp_path):
    """Select a pipe, drag an endpoint, and glue it to arbitrary SVG ink."""
    window = _window(tmp_path)
    studio = window.current()
    source = studio.add_static(
        "symbol", 100, 100, 150, symbol="vessel")
    target = studio.add_static(
        "symbol", 470, 310, 100, symbol="pump")
    pipe = studio.add_pipe(source, "s", target, "w")
    pipe.setSelected(True)

    start = QPointF(pipe._points[0])
    press = QGraphicsSceneMouseEvent(QEvent.GraphicsSceneMousePress)
    press.setScenePos(start)
    press.setPos(start)
    press.setButton(Qt.LeftButton)
    press.setButtons(Qt.LeftButton)
    pipe.mousePressEvent(press)
    assert pipe._endpoint_drag == "a"

    # Probe an arbitrary point on the vessel's curved left outline. The
    # resolver returns the exact alpha-edge point used for the final glue.
    probe = source.mapToScene(QPointF(
        source.rect().left(), source.rect().top() + source.rect().height() * .7))
    anchor = source.connection_anchor_at(probe, pixels=24, allow_inside=True)
    assert anchor.startswith("outline:")
    destination = source.anchor(anchor)
    move = QGraphicsSceneMouseEvent(QEvent.GraphicsSceneMouseMove)
    move.setScenePos(destination)
    move.setPos(destination)
    move.setButtons(Qt.LeftButton)
    pipe.mouseMoveEvent(move)
    assert pipe._endpoint_target == (source, anchor)
    assert ("x", destination.x()) in studio.canvas._smart_guides
    assert ("y", destination.y()) in studio.canvas._smart_guides

    release = QGraphicsSceneMouseEvent(QEvent.GraphicsSceneMouseRelease)
    release.setScenePos(destination)
    release.setPos(destination)
    release.setButton(Qt.LeftButton)
    pipe.mouseReleaseEvent(release)
    assert pipe.data["a"] == source.data["id"]
    assert pipe.data["a_side"].startswith("outline:")
    assert pipe._points[0] == source.anchor(pipe.data["a_side"])
    window.close()


def test_manual_pipe_bend_snaps_to_endpoints_and_shows_guides(tmp_path):
    """Dragging a manual bend gets the same alignment help as an object."""
    window = _window(tmp_path)
    studio = window.current()
    source = studio.add_static(
        "symbol", 100, 100, 150, symbol="vessel")
    target = studio.add_static(
        "symbol", 470, 310, 100, symbol="pump")
    pipe = studio.add_pipe(source, "s", target, "w")
    source_anchor = QPointF(pipe._points[0])
    target_anchor = QPointF(pipe._points[-1])
    pipe.data["route_mode"] = "manual"
    pipe.data["route_points"] = [[300.0, 240.0]]
    studio.reroute_pipes()
    pipe.setSelected(True)

    press = QGraphicsSceneMouseEvent(QEvent.GraphicsSceneMousePress)
    press.setScenePos(QPointF(300.0, 240.0))
    press.setPos(QPointF(300.0, 240.0))
    press.setButton(Qt.LeftButton)
    press.setButtons(Qt.LeftButton)
    pipe.mousePressEvent(press)
    assert pipe._waypoint_drag == 0

    # Six screen pixels is the acquisition band. Approach the source X and
    # destination Y without landing exactly on either axis.
    move = QGraphicsSceneMouseEvent(QEvent.GraphicsSceneMouseMove)
    move.setScenePos(QPointF(source_anchor.x() + 3.0,
                            target_anchor.y() - 3.0))
    move.setPos(move.scenePos())
    move.setButtons(Qt.LeftButton)
    pipe.mouseMoveEvent(move)

    assert pipe.data["route_points"][0] == [
        source_anchor.x(), target_anchor.y()]
    assert ("x", source_anchor.x()) in studio.canvas._smart_guides
    assert ("y", target_anchor.y()) in studio.canvas._smart_guides

    release = QGraphicsSceneMouseEvent(QEvent.GraphicsSceneMouseRelease)
    release.setScenePos(move.scenePos())
    release.setPos(move.scenePos())
    release.setButton(Qt.LeftButton)
    pipe.mouseReleaseEvent(release)
    assert studio.canvas._smart_guides == []
    window.close()


def test_equipment_pvm_connects_to_glyph_not_status_band(tmp_path):
    """A compact process PVM's tag/status furniture is not equipment ink."""
    window = _window(tmp_path)
    studio = window.current()
    pvm = studio.place_block(
        "UNIT100/DEV1", "DEVCTL", 180, 120,
        role=("dynamo_compact", "pump"))
    item = next(one for one in studio._items() if one.pvm.id == pvm.id)

    glyph_bottom = item.anchor("s")
    host_bottom = item.mapToScene(QPointF(
        item.rect().center().x(), item.rect().bottom()))
    assert glyph_bottom.y() < host_bottom.y() - 10
    token = item.connection_anchor_at(glyph_bottom, pixels=8)
    assert token is not None
    # A direct perimeter gesture records arbitrary SVG outline geometry.
    if token not in item.anchor_sides():
        assert token.startswith("outline:")
    window.close()


def test_vessel_pvm_ports_land_on_curved_shell_not_offset_readout(tmp_path):
    """The process vessel painter and connector model share one boundary."""
    window = _window(tmp_path)
    studio = window.current()
    pvm = studio.place_block(
        "UNIT100/AI1", "AI", 180, 120,
        role=("dynamo_inline", "vessel"))
    vessel = next(one for one in studio._items() if one.pvm.id == pvm.id)
    vessel.setSelected(True)

    assert vessel._symbol_name() == "vessel"
    body = vessel._symbol_view_rect()
    assert body.bottom() <= vessel.rect().bottom() - 28.0

    # Neither the automatic south port nor the class-authored bottom port may
    # use the readout band or the old 16-pixel helper offset.
    south = vessel.anchor("s")
    bottom = vessel.anchor("bottom")
    handle = vessel.mapToScene(vessel.connection_handle("s"))
    assert handle == south
    assert bottom == south
    assert south.y() < vessel.mapToScene(vessel.rect().bottomLeft()).y() - 20

    # A non-cardinal point on the curved lower cap is also addressable. This
    # is what lets the engineer slide a pipe endpoint along the vessel edge.
    probe = vessel.mapToScene(QPointF(
        body.left() + body.width() * 0.72, body.bottom()))
    token = vessel.connection_anchor_at(probe, pixels=18,
                                        allow_inside=True)
    assert token is not None and token.startswith("outline:")
    assert (vessel.anchor(token) - probe).manhattanLength() < 18
    window.close()


def test_line_tool_glues_any_outline_points_on_unselected_process_pvms(
        tmp_path):
    """The ribbon Line gesture becomes one persistent process connector.

    This is the reported pump-to-valve workflow: neither target is selected,
    and both coordinates are arbitrary silhouette points rather than named
    N/S/E/W ports.
    """
    window = _window(tmp_path)
    studio = window.current()
    pump_pvm = studio.place_block(
        "U200/PUMP1", "DEVCTL", 180, 180,
        role=("dynamo_compact", "pump"))
    valve_pvm = studio.place_block(
        "U200/VALVE1", "DEVCTL", 520, 280,
        role=("dynamo_compact", ""))
    pump = next(item for item in studio._items()
                if item.pvm.id == pump_pvm.id)
    valve = next(item for item in studio._items()
                 if item.pvm.id == valve_pvm.id)
    studio.canvas.scene().clearSelection()

    pump_box = pump._symbol_view_rect()
    pump_probe = pump.mapToScene(QPointF(
        pump_box.left() + pump_box.width() * 0.72, pump_box.top()))
    pump_side = pump.connection_anchor_at(
        pump_probe, pixels=24, allow_inside=True)
    assert pump_side is not None
    pump_point = pump.anchor(pump_side)

    valve_box = valve._symbol_view_rect()
    valve_probe = valve.mapToScene(QPointF(
        valve_box.left(), valve_box.top() + valve_box.height() * 0.72))
    valve_side = valve.connection_anchor_at(
        valve_probe, pixels=24, allow_inside=True)
    assert valve_side is not None
    valve_point = valve.anchor(valve_side)

    studio.arm_line()
    assert studio._line_press(pump_point)
    studio._line_drag(valve_point)
    connector = studio._line_release(valve_point)

    assert isinstance(connector, PipeItem)
    assert connector.data["a"] == pump.pvm.id
    assert connector.data["b"] == valve.pvm.id
    assert connector._points[0] == pump.anchor(connector.data["a_side"])
    assert connector._points[-1] == valve.anchor(connector.data["b_side"])
    assert not any(item.data.get("kind") == "line"
                   for item in studio._static_items())
    assert not any("missing port" in issue for issue in studio.validate())

    old_end = QPointF(connector._points[-1])
    valve.setPos(valve.pos() + QPointF(50, 30))
    assert connector._points[-1] == valve.anchor(connector.data["b_side"])
    assert connector._points[-1] != old_end
    window.close()


def test_loose_process_pipe_endpoint_can_be_finished_on_a_later_drag(tmp_path):
    """A missed first drop stays editable instead of becoming a fake line."""
    window = _window(tmp_path)
    studio = window.current()
    pump_pvm = studio.place_block(
        "U200/PUMP1", "DEVCTL", 150, 150,
        role=("dynamo_compact", "pump"))
    valve_pvm = studio.place_block(
        "U200/VALVE1", "DEVCTL", 520, 260,
        role=("dynamo_compact", ""))
    pump = next(item for item in studio._items()
                if item.pvm.id == pump_pvm.id)
    valve = next(item for item in studio._items()
                 if item.pvm.id == valve_pvm.id)
    start = pump.anchor("e")
    loose = QPointF(360, 210)

    # This contract isolates loose-end persistence from the separate smart-
    # guide contract below; with guides enabled the requested point may
    # intentionally align to nearby process geometry.
    studio.smart_guides_enabled = False
    studio.snap_enabled = False
    studio.arm_line()
    studio._line_press(start)
    studio._line_drag(loose)
    connector = studio._line_release(loose)
    assert isinstance(connector, PipeItem)
    assert connector.data["a"] == pump.pvm.id
    assert connector.data["b"] == ""
    assert connector.data["b_point"] == [loose.x(), loose.y()]
    assert connector._points[-1] == loose
    studio.smart_guides_enabled = True
    studio.snap_enabled = True

    connector.setSelected(True)
    endpoint = QPointF(connector._points[-1])
    press = QGraphicsSceneMouseEvent(QEvent.GraphicsSceneMousePress)
    press.setPos(endpoint)
    press.setScenePos(endpoint)
    press.setButton(Qt.LeftButton)
    press.setButtons(Qt.LeftButton)
    connector.mousePressEvent(press)
    assert connector._endpoint_drag == "b"

    destination = valve.anchor("w")
    move = QGraphicsSceneMouseEvent(QEvent.GraphicsSceneMouseMove)
    move.setPos(destination)
    move.setScenePos(destination)
    move.setButtons(Qt.LeftButton)
    connector.mouseMoveEvent(move)
    assert connector._endpoint_target is not None
    release = QGraphicsSceneMouseEvent(QEvent.GraphicsSceneMouseRelease)
    release.setPos(destination)
    release.setScenePos(destination)
    release.setButton(Qt.LeftButton)
    connector.mouseReleaseEvent(release)

    assert connector.data["b"] == valve.pvm.id
    assert "b_point" not in connector.data
    assert connector._points[-1] == valve.anchor(connector.data["b_side"])
    window.close()


def test_closed_shape_connections_use_visible_outline_not_host_box(tmp_path):
    """A triangle's sloped edge, not its empty rectangular corner, glues."""
    window = _window(tmp_path)
    studio = window.current()
    triangle = studio.add_static("triangle", 120, 100, 160, 120)
    probe = triangle.mapToScene(QPointF(42, 52))
    side = triangle.connection_anchor_at(
        probe, pixels=30, allow_inside=True)
    assert side is not None and side.startswith("outline:")
    point = triangle.mapFromScene(triangle.anchor(side))
    assert point.x() > triangle.rect().left()
    assert point.y() > triangle.rect().top()
    window.close()


def test_specialized_process_pvms_connect_to_equipment_not_furniture(tmp_path):
    """AO/AI process variants share their painter's exact equipment lane."""
    window = _window(tmp_path)
    studio = window.current()
    valve_pvm = studio.place_block(
        "U200/FCV1", "AO", 150, 120,
        role=("dynamo_inline", "valve"))
    reactor_pvm = studio.place_block(
        "U400/REACTOR1", "AI", 480, 120,
        role=("dynamo_inline", "reactor"))
    valve = next(item for item in studio._items()
                 if item.pvm.id == valve_pvm.id)
    reactor = next(item for item in studio._items()
                   if item.pvm.id == reactor_pvm.id)

    assert valve._symbol_name() == "control_valve"
    assert reactor._symbol_name() == "reactor"
    valve_body = valve._symbol_view_rect()
    reactor_body = reactor._symbol_view_rect()
    assert valve_body.bottom() <= valve.rect().bottom() - 40
    assert reactor_body.right() <= reactor.rect().right() - 34

    # The readout/OP bar below the valve and analog bar beside the reactor are
    # not pipe connection surfaces. Their visible equipment outlines are.
    for item, body in ((valve, valve_body), (reactor, reactor_body)):
        probe = item.mapToScene(QPointF(
            body.left(), body.top() + body.height() * 0.63))
        side = item.connection_anchor_at(
            probe, pixels=24, allow_inside=True)
        assert side is not None
        attached = item.mapFromScene(item.anchor(side))
        assert body.adjusted(-1, -1, 1, 1).contains(attached)
    window.close()


def test_cardinal_connector_keeps_the_ports_selected_by_the_engineer(tmp_path):
    """A bottom-port preview must not jump to the PVM side when committed."""
    window = _window(tmp_path)
    studio = window.current()
    tank = studio.add_static(
        "symbol", 100, 100, 160, h=220, symbol="vessel")
    pump = studio.add_static(
        "symbol", 470, 390, 90, h=70, symbol="pump")

    pipe = studio.add_pipe(tank, "s", pump, "w")
    tank_bottom = tank.anchor("s")
    pump_inlet = pump.anchor("w")

    assert pipe.data["auto"] is False
    assert pipe.data["a_side"] == "s"
    assert pipe.data["b_side"] == "w"
    assert pipe._points[0] == tank_bottom
    assert pipe._points[-1] == pump_inlet
    assert pipe._points[1].x() == tank_bottom.x()
    assert pipe._points[1].y() > tank_bottom.y()

    # Drafts created by the former cardinal-auto behavior still contain the
    # selected sides. Rerouting one must repair its appearance rather than
    # continuing to replace the authored bottom port with a facing side.
    pipe.data["auto"] = True
    studio.reroute_pipes()
    assert pipe._points[0] == tank_bottom
    assert pipe._points[1].x() == tank_bottom.x()
    assert pipe._points[1].y() > tank_bottom.y()
    window.close()


def test_component_palette_search_drag_contract_and_float_dock(tmp_path):
    window = _window(tmp_path)
    cards = window.palette_box.findChildren(_PaletteCard)
    assert len(cards) > 100
    draggable = [card for card in cards if card._drag_payload]
    assert draggable
    assert any(card._drag_payload.get("type") == "pvm"
               for card in draggable)
    assert any(card._drag_payload.get("type") == "symbol"
               for card in draggable)
    assert {card._drag_payload.get("direction") for card in draggable
            if card._drag_payload.get("type") == "stream_connector"} \
        == {"incoming", "outgoing"}
    assert all("drag to place" in card.toolTip().lower()
               for card in draggable)

    window.palette_search.setText("centrifugal pump")
    _flush_layout()
    visible = [card for card in cards if not card.isHidden()]
    assert visible
    assert all("centrifugal" in card.search_text
               and "pump" in card.search_text for card in visible)
    assert not window.palette_result_label.isHidden()
    window.palette_search.clear()

    dialog = window.detach_palette()
    _flush_layout()
    assert window.palette_is_floating()
    assert window.left_workspace.indexOf(window.palette_box) == -1
    assert dialog.layout().indexOf(window.palette_box) == 0
    assert window.palette_float_button.text() == "DOCK"
    window.redock_palette()
    _flush_layout()
    assert not window.palette_is_floating()
    assert window.left_workspace.indexOf(window.palette_box) == 1
    assert window.palette_float_button.text() == "FLOAT"

    studio = window.current()
    placed = studio.drop_palette_item(
        {"type": "symbol", "symbol": "pump", "title": "Pump"},
        QPointF(240, 180))
    assert isinstance(placed, StaticItem)
    assert placed.isSelected()
    assert placed.data["ports"]
    assert placed.mapToScene(placed.rect().center()) == QPointF(240, 180)

    continuation = studio.drop_palette_item(
        {"type": "stream_connector", "direction": "incoming",
         "title": "Incoming Stream"},
        QPointF(430, 230))
    assert isinstance(continuation, StaticItem)
    assert continuation.data["stream_direction"] == "incoming"
    assert continuation.anchor_sides() == ["process"]
    assert continuation.mapToScene(continuation.rect().center()) \
        == QPointF(430, 230)
    window.close()
