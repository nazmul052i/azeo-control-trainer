"""Shared fields preserve native value semantics and fit compact desktops."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QAccessible
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QDialog, QDoubleSpinBox, QFormLayout,
                              QLabel, QStyle, QStyleOptionComboBox)

from azeo_control_trainer.core.presentation.authoring_controls import AuthoringComboBox, name_form_fields
from azeo_control_trainer.core.presentation.authoring_style import AUTHORING_CHROME_QSS
from azeo_control_trainer.core.presentation.dialog_layout import bounded_window_rect, fit_dialog_to_screen


@pytest.fixture
def form():
    app = QApplication.instance() or QApplication([])
    root = QDialog()
    root.setStyleSheet(AUTHORING_CHROME_QSS)
    layout = QFormLayout(root)
    yield app, root, layout
    root.close()
    root.deleteLater()
    app.processEvents()


def test_selector_preserves_identity_keyboard_and_accessible_label(form):
    app, root, layout = form
    selector = AuthoringComboBox()
    selector.addItem("A very long project name " * 25, "first-id")
    selector.addItem("Second project", "second-id")
    label = QLabel("&Project")
    layout.addRow(label, selector)
    name_form_fields(root)
    root.show()
    app.processEvents()
    assert root.width() < 700
    assert label.buddy() is selector
    interface = QAccessible.queryAccessibleInterface(selector)
    assert interface.text(QAccessible.Name) == "Project"
    assert interface.role() == QAccessible.ComboBox
    selector.setFocus()
    QTest.keyClick(selector, Qt.Key_Down)
    assert selector.currentData() == "second-id"
    option = QStyleOptionComboBox()
    selector.initStyleOption(option)
    arrow = selector.style().subControlRect(QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxArrow, selector)
    editor = selector.style().subControlRect(QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxEditField, selector)
    assert arrow.width() >= 28 and not arrow.intersects(editor)
    selector.showPopup()
    app.processEvents()
    assert selector.screen().availableGeometry().contains(selector.view().window().frameGeometry())
    selector.hidePopup()


def test_numeric_editor_keeps_precision_and_commits_complete_input(form):
    app, root, layout = form
    number = QDoubleSpinBox()
    number.setRange(-1e12, 1e12)
    number.setDecimals(6)
    number.setKeyboardTracking(False)
    number.setSpecialValueText("Auto")
    layout.addRow("Setpoint", number)
    name_form_fields(root)
    changes = []
    number.valueChanged.connect(changes.append)
    root.show()
    app.processEvents()
    number.setFocus()
    number.selectAll()
    QTest.keyClicks(number, "-12345.678901")
    assert changes == []
    QTest.keyClick(number, Qt.Key_Return)
    assert changes == [-12345.678901]
    QTest.keyClick(number, Qt.Key_Up)
    assert number.value() == pytest.approx(-12344.678901)
    assert number.accessibleName() == "Setpoint"


@pytest.mark.parametrize("available", [QRect(0, 0, 1024, 720), QRect(-1280, 0, 1280, 800),
                                       QRect(1920, -900, 1440, 900)])
def test_restored_frame_stays_inside_current_monitor(available):
    assert available.contains(bounded_window_rect(QRect(4000, 2000, 2000, 1400), available))
    already = QRect(available.x() + 20, available.y() + 20, 400, 300)
    assert bounded_window_rect(already, available) == already


def test_actual_restored_dialog_caption_is_reachable(form):
    app, root, _ = form
    root.move(8000, 8000)
    root.resize(600, 450)
    root.show()
    app.processEvents()
    fit_dialog_to_screen(root)
    assert root.screen().availableGeometry().contains(root.frameGeometry())
