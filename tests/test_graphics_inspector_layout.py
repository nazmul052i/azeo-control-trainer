"""Inspector geometry is checked inside the real, constrained Studio splitter."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QComboBox, QLineEdit,
                              QScrollArea, QStyle, QStyleOptionComboBox,
                              QStyleOptionSpinBox, QWidget)

from azeo_control_trainer.azeo_graphics_designer.window import HmiStudioWindow


@pytest.fixture
def window(tmp_path, monkeypatch):
    from azeo_control_trainer.core.presentation import headless
    monkeypatch.setattr(headless, "is_headless", lambda: True)
    app = QApplication.instance() or QApplication([])
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path / "settings"))
    result = HmiStudioWindow(lambda: {}, tmp_path / "displays")
    screen = result.screen().availableGeometry()
    if app.platformName() == "offscreen":
        result.resize(1440, 900)
    else:
        result.resize(min(1440, screen.width() - 20), min(900, screen.height() - 40))
    result.show()
    app.processEvents()
    yield result
    result.close()
    result.deleteLater()
    app.processEvents()


def settle():
    for _ in range(4):
        QApplication.processEvents()


def set_inspector_width(studio, width):
    split = studio.workspace_splitter
    split.setSizes([split.width() - split.handleWidth() - width, width])
    settle()


def assert_page_fits(pane):
    for scroll in pane.findChildren(QScrollArea, "property_scroll"):
        if not scroll.isVisible():
            continue
        assert scroll.widget().width() <= scroll.viewport().width(), (
            scroll.widget().width(), scroll.viewport().width())
        assert scroll.horizontalScrollBar().maximum() == 0
        for field in scroll.widget().findChildren(QWidget):
            if not isinstance(field, (QComboBox, QLineEdit, QAbstractSpinBox)) or not field.isVisible():
                continue
            left = field.mapTo(scroll.viewport(), QPoint()).x()
            assert left >= 0 and left + field.width() <= scroll.viewport().width()
            if isinstance(field, QComboBox):
                option = QStyleOptionComboBox()
                field.initStyleOption(option)
                arrow = field.style().subControlRect(
                    QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxArrow, field)
                assert field.rect().contains(arrow)
            elif isinstance(field, QAbstractSpinBox):
                option = QStyleOptionSpinBox()
                field.initStyleOption(option)
                for part in (QStyle.SC_SpinBoxUp, QStyle.SC_SpinBoxDown):
                    arrow = field.style().subControlRect(QStyle.CC_SpinBox, option, part, field)
                    assert field.rect().contains(arrow)
                assert field.lineEdit().width() >= field.fontMetrics().horizontalAdvance(field.text())


@pytest.mark.parametrize("width", [320, 340, 360])
def test_display_properties_fit_narrow_splitter(window, width):
    studio = window.current()
    set_inspector_width(studio, width)
    assert studio.pane.width() == width
    assert_page_fits(studio.pane)
    assert studio.pane.display_title.width() > 100
    assert studio.pane.display_kind_chip.height() < 50
    studio.display.name = "Overview - L1 Plant with a long engineering title " * 4
    studio.display.parent = "Long parent display " * 10
    studio.pane._refresh_display_page()
    settle()
    assert_page_fits(studio.pane)
    studio.pane.display_tabs.setCurrentIndex(1)
    settle()
    assert_page_fits(studio.pane)


@pytest.mark.parametrize("kind", ["rect", "text", "symbol", "datalink"])
def test_item_properties_fit_each_tab(window, kind):
    studio = window.current()
    set_inspector_width(studio, 320)
    item = studio.add_static(kind, 20, 20, 100, 100)
    studio.pane.show_item(item)
    for index in range(studio.pane.item_tabs.count()):
        studio.pane.item_tabs.setCurrentIndex(index)
        settle()
        assert_page_fits(studio.pane)


def test_zoom_stays_visible_when_insert_ribbon_overflows(window):
    window.ribbon_tabs.setCurrentIndex(list(window._RIBBON).index("Insert"))
    settle()
    zoom = window.zoom
    visible = zoom.visibleRegion().boundingRect()
    assert visible == zoom.rect(), (visible, zoom.rect())
    window._ribbon_scroll.horizontalScrollBar().setValue(
        window._ribbon_scroll.horizontalScrollBar().maximum())
    settle()
    assert zoom.visibleRegion().boundingRect() == zoom.rect()
    assert window.minimumSizeHint().width() <= 1100
    zoom.setCurrentText("150")
    assert window.current().zoom_percent == 150
    window.ribbon_tabs.setCurrentIndex(list(window._RIBBON).index("Home"))
    assert window.zoom.currentText() == "150"


def test_display_width_commits_and_undoes_from_narrow_inspector(window):
    studio = window.current()
    previous = studio.display.width
    number = studio.pane.display_rows["width"]
    studio.pane._stack.currentWidget().ensureWidgetVisible(number)
    number.setFocus()
    number.selectAll()
    QTest.keyClicks(number, "1920")
    QTest.keyClick(number, Qt.Key_Return)
    assert studio.display.width == 1920
    studio.undo()
    assert studio.display.width == previous


def test_function_block_pvm_properties_fit_narrow_inspector(window):
    studio = window.current()
    set_inspector_width(studio, 320)
    pvm = studio.place_block("UNIT/PID1", "PID", 100, 100, role="dynamo_compact")
    item = next(item for item in studio._items() if item.pvm.id == pvm.id)
    studio.pane.show_pvm(item)
    settle()
    assert_page_fits(studio.pane)


def test_description_wraps_and_commits_on_leaving_the_field(window):
    studio = window.current()
    field = studio.pane.display_rows["description"]
    scroll = studio.pane._stack.currentWidget()
    scroll.ensureWidgetVisible(field)
    field.setFocus()
    previous = studio.display.description
    QTest.keyClicks(field, "Process overview")
    QTest.keyClick(field, Qt.Key_Return)
    QTest.keyClicks(field, "Equipment and operating limits")
    assert studio.display.description == previous
    QTest.keyClick(field, Qt.Key_Tab)
    assert studio.display.description == "Process overview\nEquipment and operating limits"
    studio.undo()
    assert studio.display.description == previous
