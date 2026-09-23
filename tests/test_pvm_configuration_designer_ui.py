"""Configuration authoring must follow keyboard focus and preserve edits."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QWidget

from azeo_control_trainer.azeo_graphics_designer.configurator.designer import PvmConfigDesigner
from azeo_control_trainer.core.hmi.pvms.configurator.model import VALUE_TYPES, REFERENCE_TYPES, SELECTION_TYPE
from azeo_control_trainer.core.hmi.pvms.configurator.model import PvmConfiguration, PvmProperty, PropertyGroup
from azeo_control_trainer.core.hmi.theme.fonts import apply_application_font, ensure_font_directory


@pytest.fixture
def designer(tmp_path):
    ensure_font_directory()
    app = QApplication.instance() or QApplication([])
    apply_application_font()
    window = PvmConfigDesigner(tmp_path, pvm_class="HP_C_Valve")
    window.show()
    app.processEvents()
    yield window
    window.unsaved = False
    window.close()
    app.processEvents()


def test_arrow_navigation_edits_the_highlighted_property(designer):
    first = designer.tree.topLevelItem(0).child(0).child(0)
    designer.tree.setCurrentItem(first)
    QTest.mouseClick(designer.tree.viewport(), Qt.LeftButton,
                     pos=designer.tree.visualItemRect(first).center())
    original = designer.config.property(designer.selected)
    old_description = original.description
    QTest.keyClick(designer.tree, Qt.Key_Down)
    target = designer.tree.currentItem().data(0, Qt.UserRole)[1]
    assert designer.selected == target
    prop = designer.config.property(target)
    editor = next(w for w in designer.form_scroll.widget().findChildren(QLineEdit)
                  if w.text() == prop.name)
    editor.setText("RenamedByKeyboard")
    editor.editingFinished.emit()
    assert designer.config.property("RenamedByKeyboard") is prop
    assert original.description == old_description
    assert designer.tree.currentItem().data(0, Qt.UserRole) == ("prop", prop.name)


def test_new_group_receives_next_property_and_survives_history(designer):
    designer.add_group()
    group_name = designer.config.groups[-1].name
    assert designer.selected_group == group_name
    assert designer.tree.currentItem().data(0, Qt.UserRole) == ("group", group_name)
    designer.add_property("String")
    name = designer.selected
    assert designer.config.group_of(name).name == group_name
    assert designer.undo()
    assert designer.config.property(name) is None
    assert designer.redo()
    assert designer.config.group_of(name).name == group_name
    path = designer.save()
    assert path
    assert PvmConfiguration.load(path).group_of(name).name == group_name


def test_toolbar_and_property_types_use_real_icons(designer):
    designer._add_property_menu()
    types = set(VALUE_TYPES) | set(REFERENCE_TYPES) | {SELECTION_TYPE}
    actions = [a for a in designer._add_menu.actions() if a.text() in types]
    assert len(actions) == len(types)
    assert all(not a.icon().isNull() for a in actions)
    assert len(designer.command_buttons) == 13
    assert "impact" in designer.command_buttons
    assert all(not b.icon().isNull() for b in designer.command_buttons.values())
    assert all(b.font().pointSizeF() >= 9 for b in designer.command_buttons.values())


def test_preview_resizes_without_forcing_the_window_past_the_desktop(designer):
    designer._select_class("VesselTrendPvm")
    designer.resize(1024, 700)
    QTest.qWait(150)
    first = designer.preview_label.pixmap()
    assert not first.isNull()
    assert designer.width() == 1024
    designer.resize(1366, 900)
    designer.body_splitter.setSizes([270, 560, 500])
    QTest.qWait(150)
    bigger = designer.preview_label.pixmap()
    assert bigger.width() > first.width()
    assert bigger.height() > first.height()
    assert bigger.devicePixelRatioF() == designer.preview_label.devicePixelRatioF()
    assert abs(bigger.deviceIndependentSize().width()
               - designer.preview_label.contentsRect().width()) < 1
    designer.preview_scale.setCurrentText("Actual size")
    assert not designer.preview_label.pixmap().toImage() == bigger.toImage()
    designer.resize(1024, 700)
    QTest.qWait(150)
    assert designer.preview_label.pixmap().height() < bigger.height()
    assert all(w.font().pointSizeF() > 0 for w in designer.findChildren(QWidget))


def test_designer_fits_a_small_logical_desktop_at_high_windows_scaling(designer):
    designer._select_class("VesselTrendPvm")
    designer.resize(900, 480)
    QTest.qWait(150)
    assert designer.height() == 480
    assert designer.width() == 900
    note = next(w for w in designer.findChildren(QLabel)
                if w.text().startswith("Appearance preview"))
    assert designer.preview_label.geometry().bottom() < note.geometry().top()


def test_failed_preview_preserves_editing_and_recovers(designer, monkeypatch):
    import azeo_control_trainer.azeo_graphics_designer.component_icons as icons

    designer.new_composite("RecoveryPvm")
    assert not designer.class_combo.itemIcon(designer.class_combo.currentIndex()).isNull()
    real_preview = icons.authored_preview

    def broken_preview(*args, **kwargs):
        raise ValueError("Broken authored member")

    monkeypatch.setattr(icons, "authored_preview", broken_preview)
    designer.add_property("String")
    assert designer.config.property(designer.selected) is not None
    assert QSignalSpy(designer._preview_timer.timeout).wait(2000)
    assert designer.preview_label.property("previewError") == "Broken authored member"
    assert "Preview unavailable" in designer.preview_label.text()
    monkeypatch.setattr(icons, "authored_preview", real_preview)
    designer._update_preview()
    assert not designer.preview_label.property("previewError")
    assert not designer.preview_label.pixmap().isNull()


def test_configuration_trial_accepts_a_complete_number(designer):
    from azeo_control_trainer.azeo_graphics_designer.configurator.designer import PreviewDialog

    config = PvmConfiguration("NumericTrial", [PropertyGroup("Basic", [
        PvmProperty("Angle", "Number", default="0"),
    ])])
    trial = PreviewDialog(config, designer)
    trial.show()
    trial.activateWindow()
    QApplication.processEvents()
    editor = trial.pickers.findChild(QLineEdit)
    editor.setFocus()
    assert QApplication.focusWidget() is editor
    editor.selectAll()
    QTest.keyClicks(editor, "2")
    assert trial.pickers.findChild(QLineEdit) is editor
    QTest.keyClicks(editor, "50")
    QTest.keyClick(editor, Qt.Key_Tab)
    assert trial.choices["Angle"] == "250"
    trial.close()
