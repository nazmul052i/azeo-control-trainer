"""Visible authoring contracts: readable controls and identifiable components."""
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from azeo_control_trainer.azeo_graphics_designer.window import HmiStudioWindow, _PaletteCard


def test_building_studio_never_shows_a_detached_palette_window(tmp_path):
    app = QApplication.instance() or QApplication([])
    shown = []

    class ShowObserver(QObject):
        def eventFilter(self, watched, event):  # noqa: N802
            if event.type() == QEvent.Show and getattr(watched, "isWindow", lambda: False)():
                shown.append(watched.objectName() or type(watched).__name__)
            return False

    observer = ShowObserver()
    app.installEventFilter(observer)
    result = None
    try:
        result = HmiStudioWindow(lambda: {}, tmp_path)
        app.processEvents()
        assert shown == [], shown
    finally:
        app.removeEventFilter(observer)
        if result is not None:
            result.close()


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    result = HmiStudioWindow(lambda: {}, tmp_path)
    result.resize(1366, 900)
    result.show()
    app.processEvents()
    yield result
    result.close()
    app.processEvents()


def test_every_component_has_a_real_preview_and_keyboard_placement(window):
    cards = window.palette_box.findChildren(_PaletteCard)
    assert len(cards) > 100
    for card in cards:
        preview = card.findChild(QLabel, "component_preview")
        assert preview is not None, card.title
        preview.ensure_loaded()
        assert not preview.pixmap().isNull(), card.title
        assert not preview.property("previewError"), (card.title, preview.property("previewError"))
        assert card.accessibleName(), card.title
        assert card.focusPolicy() == Qt.StrongFocus
    card = next(card for card in cards if card.title == "Button")
    window.palette_search.setText("Button")
    QApplication.processEvents()
    card.setFocus()
    QTest.keyClick(card, Qt.Key_Return)
    assert window.current()._place_ghost is not None
    assert not window.current()._static_items()


def test_side_navigation_gives_each_browser_the_full_height(window):
    assert window.explorer_tabs.isHidden()
    assert not window.palette_box.isHidden()
    window.show_sidebar("library")
    QApplication.processEvents()
    assert window.explorer_tabs.currentWidget() is window.library
    assert not window.explorer_tabs.isHidden()
    assert window.palette_box.isHidden()
    assert window.explorer_tabs.height() > 550
    window.show_sidebar("components")
    QApplication.processEvents()
    assert window.palette_box.height() > 550
    assert window.minimumSizeHint().width() <= 1100


def test_shape_inspector_starts_with_geometry_and_keeps_advanced_actions(window):
    studio = window.current()
    item = studio.add_static("rect", 50, 70, 80, 60)
    item.setSelected(True)
    QApplication.processEvents()
    pane = studio.pane
    assert [pane.item_tabs.tabText(i) for i in range(pane.item_tabs.count())] == [
        "Design", "Data", "Behavior"]
    assert pane.item_tabs.currentIndex() == 0
    assert pane.item_fields["x"].isVisible()
    assert not pane.compound_holder.isVisible()
    pane.item_fields["x"].setText("160")
    pane.item_fields["x"].editingFinished.emit()
    assert item.pos().x() == 160
    studio.undo()
    assert studio._static_items()[0].pos().x() == 50


def test_pvm_inspector_edits_fill_and_line_and_preserves_them_across_theme_changes(window):
    from azeo_control_trainer.core.hmi.pvms.rendering.renderer import pvm_from_dict
    from azeo_control_trainer.core.hmi.theme.roles import Role
    from azeo_control_trainer.core.hmi.theme.tokens import THEMES

    studio = window.current()
    placed = studio.place_block(
        "UNIT/PID1", "PID", 100, 100, role="dynamo_compact")
    item = next(one for one in studio._items() if one.pvm.id == placed.id)
    studio.pane.show_pvm(item)
    fill = studio.pane.rows["fill"]
    line = studio.pane.rows["line"]
    assert fill.placeholderText() == "Theme default"
    assert line.placeholderText() == "Theme default"
    assert studio.pane.rows["layer"].text() == "PVMs"

    fill.setText("#d8e0e7")
    fill.editingFinished.emit()
    line.setText("#425b70")
    line.editingFinished.emit()

    assert item.pvm.fill == "#d8e0e7"
    assert item.pvm.line == "#425b70"
    assert item._palette[Role.SURFACE_PANEL] == "#d8e0e7"
    assert item._palette[Role.EQUIPMENT_FILL] == "#d8e0e7"
    assert item._palette[Role.LINE_SOFT] == "#425b70"
    assert item._palette[Role.EQUIPMENT] == "#425b70"
    restored = pvm_from_dict(item.pvm.to_dict())
    assert restored.fill == "#d8e0e7"
    assert restored.line == "#425b70"

    item.set_palette(THEMES["dark"])
    assert item._palette[Role.SURFACE_PANEL] == "#d8e0e7"
    assert item._palette[Role.LINE] == "#425b70"

    line.clear()
    line.editingFinished.emit()
    assert item.pvm.line == ""
    assert item._palette[Role.LINE] == studio.palette_roles[Role.LINE]


def test_home_commands_are_readable_and_drawing_tools_have_one_home(window):
    home = window._ribbon_buttons
    assert len(home) <= 15
    for key in ("display.save", "edit.undo", "edit.redo", "format.copy_style"):
        assert key in home
        assert home[key].font().pointSizeF() >= 9
    assert "tool.select" not in home
    assert "tool.pan" not in home
    assert not window.current().canvas_frame.tool_buttons["select"].icon().isNull()


def test_display_properties_tabs_switch_to_their_actual_controls(window):
    pane = window.current().pane
    assert [pane.display_tabs.tabText(i) for i in range(pane.display_tabs.count())] == [
        "Basics", "Interaction"]
    assert pane.display_rows["width"].isVisible()
    assert not pane.display_events_json.isVisible()
    pane.display_tabs.setCurrentIndex(1)
    QApplication.processEvents()
    assert pane.display_events_json.isVisible()
    assert not pane.display_rows["width"].isVisible()
    pane.display_tabs.setCurrentIndex(0)
    assert pane.display_rows["width"].isVisible()


def test_fit_keeps_new_content_inside_an_auto_sized_canvas(window):
    studio = window.current()
    assert studio.display.width == studio.display.height == 0
    studio.add_static("rect", 1200, 700, 350, 180)
    studio.add_static("text", 80, 55, 600, 36, text="Feed transfer")
    QApplication.processEvents()
    studio.fit_drawing()
    visible = studio.canvas.mapToScene(studio.canvas.viewport().rect()).boundingRect()
    assert visible.contains(studio.canvas.scene().itemsBoundingRect())


def test_library_classes_all_have_icons(window):
    tree = window.library.tree
    seen = []

    def visit(item):
        payload = item.data(0, Qt.UserRole)
        if payload and payload[0] == "class":
            seen.append(payload[1])
            assert not item.icon(0).isNull(), payload
        for index in range(item.childCount()):
            visit(item.child(index))

    for index in range(tree.topLevelItemCount()):
        visit(tree.topLevelItem(index))
    assert len(seen) > 100
    from PySide6.QtWidgets import QListWidget

    studio = window.current()
    classes = studio.classes_for("PID")
    studio._choose_block_visual("PID", classes, {})
    listing = studio._chooser.findChild(QListWidget)
    assert listing.count() == len(classes)
    assert all(not listing.item(i).icon().isNull() for i in range(listing.count()))


def test_every_installed_pvm_renders_and_has_a_specific_engineering_glyph():
    from azeo_control_trainer.azeo_graphics_designer.component_icons import FB_ICON_TYPES, pvm_preview
    from azeo_control_trainer.core.hmi.pvms.base import registry
    from azeo_control_trainer.core.presentation.function_block_icons import _DRAWERS

    app = QApplication.instance() or QApplication([])
    assert app is not None
    for (block_type, _role, _variant), cls in registry.all_classes().items():
        assert block_type in FB_ICON_TYPES, cls.__name__
        assert FB_ICON_TYPES[block_type] in _DRAWERS, cls.__name__
        preview = pvm_preview(cls, 160, 90)
        assert not preview.isNull(), cls.__name__
        image = preview.toImage()
        assert any(image.pixelColor(x, y).alpha() > 0
                   for y in range(image.height()) for x in range(image.width())), cls.__name__


def test_search_remains_visible_after_scrolling_and_collapse_restores_panel(window):
    window.palette_box.verticalScrollBar().setValue(600)
    QApplication.processEvents()
    assert window.palette_box.header.geometry().contains(
        window.palette_search.mapTo(window.palette_box, window.palette_search.rect().center()))
    window.show_sidebar("library")
    window.toggle_left_panels()
    window.toggle_left_panels()
    assert window.palette_box.isHidden()
    assert not window.explorer_tabs.isHidden()
    assert window.explorer_tabs.currentWidget() is window.library
    window.show_sidebar("components")
    window.activate_explorer(1)
    assert not window.explorer_tabs.isHidden()
    assert window.palette_box.isHidden()


def test_alert_summary_keeps_all_conditions_in_modeless_details(window):
    banner = window.current().alarm_banner
    entries = [("WARN", f"PUMP{i}/RUN", "BAD QUALITY", "10:30") for i in range(4)]
    banner.set_alarms(entries)
    assert "4 PVMs" in banner.summary.text()
    QTest.mouseClick(banner.details_button, Qt.LeftButton)
    assert banner._dialog.isVisible()
    assert not banner._dialog.isModal()
    assert banner._table.rowCount() == 4
    assert banner._table.item(3, 1).text() == "PUMP3/RUN"
    banner.set_alarms([])
    assert banner.isHidden()
    assert banner._table.rowCount() == 0
    banner._dialog.close()


def test_authored_pvm_preview_renders_the_class_and_unresolved_bindings(window):
    studio = window.current()
    library = studio.user_library()
    library.add("Feed indicator", [
        {"id": "frame", "kind": "rect", "x": 0, "y": 0, "w": 120, "h": 70,
         "fill": "#FFFFFF", "line": "#526176"},
        {"id": "value", "kind": "datalink", "x": 10, "y": 15, "w": 90, "h": 25,
         "path": "FEED/AI/PV", "datalink_type": "numeric"},
    ])
    window.refresh_user_pvms()
    card = next(card for card in window.palette_box.findChildren(_PaletteCard)
                if card.title == "Feed indicator")
    preview = card.findChild(QLabel, "component_preview")
    preview.ensure_loaded()
    assert not preview.property("previewError")
    assert not preview.pixmap().isNull()


def test_control_icons_are_distinct_and_remain_sharp_at_high_dpi(monkeypatch):
    from azeo_control_trainer.azeo_graphics_designer import component_icons
    from azeo_control_trainer.core.hmi.pvms.elements import USER_ENTRY_TITLES

    app = QApplication.instance() or QApplication([])
    assert app is not None
    monkeypatch.setattr(component_icons, "_dpr", lambda: 2.0)
    images = []
    for kind in USER_ENTRY_TITLES:
        pixmap = component_icons.element_preview(kind, 80, 60)
        assert pixmap.devicePixelRatioF() == 2.0
        assert pixmap.width() == 160
        pixels = bytes(pixmap.toImage().constBits())
        assert pixels not in images, kind
        images.append(pixels)
