"""Ribbon commands and contextual help stay connected to the real editor."""
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtCore import QEvent, Qt, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFormLayout, QWidget

from azeo_control_trainer.azeo_pa_designer import create_window
from azeo_control_trainer.azeo_pa_designer.help_content import help_topics, PARAMETER_HELP
from azeo_control_trainer.core.presentation.application_style import apply_application_style
from azeo_control_trainer.core.presentation.ribbon import RibbonButton, RibbonPage
from azeo_control_trainer.core.procedures.library import block_library, parameter_specs


@pytest.fixture
def editor(tmp_path):
    app = QApplication.instance() or QApplication([])
    apply_application_style(app)
    window = create_window(tmp_path, graphs_provider=lambda: [])
    window.show()
    app.processEvents()
    yield window
    window._saved = window.snapshot()
    window.close()
    window.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def test_ribbon_reuses_studio_components_and_drives_edit_save_view(editor):
    from azeo_control_trainer.azeo_control_designer.panels.ribbon_bar import RibbonButton as ControlButton
    assert ControlButton is RibbonButton
    assert list(editor.ribbon.pages) == ["Home", "Procedure", "View", "Help"]
    assert all(isinstance(page, RibbonPage) for page in editor.ribbon.pages.values())
    editor.ribbon.buttons["duplicate"].click()
    assert len(editor.draft.data["steps"]) == 4
    assert editor.ribbon.buttons["Undo"].isEnabled()
    editor.ribbon.buttons["Undo"].click()
    assert len(editor.draft.data["steps"]) == 3
    editor.ribbon.buttons["Save revision"].click()
    assert editor.draft.source.is_file()
    assert not editor.dirty
    editor.ribbon.tabs.setCurrentIndex(2)
    editor.ribbon.buttons["list"].click()
    assert editor.workflow_views.currentIndex() == 1
    editor.ribbon.buttons["workflow"].click()
    assert editor.workflow_views.currentIndex() == 0
    editor.ribbon.toggle_collapsed(2)
    assert not editor.ribbon.stack.isVisible()
    editor.ribbon.select_page(0)
    assert editor.ribbon.stack.isVisible()
    editor.resize(940, 640)
    QApplication.processEvents()
    assert editor.width() <= 940
    assert all(widget.font().pointSizeF() > 0 for widget in editor.findChildren(QWidget))


def test_contextual_f1_and_block_help_are_modeless_and_preserve_draft(editor):
    editor.add_type.setCurrentIndex(editor.add_type.findData("azeo.procedure.wait_until"))
    editor.add_step()
    before = editor.snapshot()
    editor.activateWindow()
    editor.canvas.setFocus()
    QTest.keyClick(editor.canvas, Qt.Key_F1)
    help_center = editor.help_center
    assert help_center.current_topic_key == "wait_until"
    assert help_center.isVisible() and not help_center.isModal()
    text = help_center.browser.toPlainText()
    assert "Continuous dwell" in text and "stable_for_sec" in text
    assert "Default:" in text and "Common mistakes" in text
    help_center.close()
    editor.show_block_reference("write_tag")
    assert editor.help_center is help_center
    assert help_center.current_topic_key == "write_tag"
    assert "readback" in help_center.browser.toPlainText()
    assert editor.snapshot() == before
    editor._saved = editor.snapshot()
    editor.close()
    assert not help_center.isVisible()


def test_search_links_history_and_no_results_recover_without_losing_topic(editor):
    editor.open_help_topic("instruction")
    help_center = editor.help_center
    help_center.open_link(QUrl("help:timing"))
    assert help_center.current_topic_key == "timing"
    help_center.back.click()
    assert help_center.current_topic_key == "instruction"
    help_center.forward.click()
    assert help_center.current_topic_key == "timing"
    help_center.search.setText("zz-no-such-topic-zz")
    assert "No matching topic" in help_center.browser.toPlainText()
    help_center.search.clear()
    assert "Timing and conditions" in help_center.browser.toPlainText()
    help_center.search.setText("resume-from-held-step")
    assert not help_center._items["hold"].isHidden()
    editor.show_block_reference("delay")
    assert help_center.current_topic_key == "delay"
    assert help_center.search.text() == ""


def test_every_supported_block_has_detailed_help_for_its_exposed_properties():
    topics = help_topics()
    retired_name = "exa" + "pilot"
    for block in block_library():
        topic = topics[block.help_key]
        assert retired_name not in " ".join(
            (block.block_id, block.label, block.category, block.description, topic.search_text)
        ).casefold()
        assert block.block_id in topic.html
        assert all(f"<h3>{section}</h3>" in topic.html for section in
                   ("Configure", "When reached", "Example", "Properties", "Common mistakes"))
        for key, _label, _kind, _default in parameter_specs(block.step_type, block):
            assert len(PARAMETER_HELP[key]) > 30
            assert f"<code>{key}</code>" in topic.html


def test_compact_inspector_keeps_labels_and_fields_separate(editor):
    for width, height in ((940, 640), (1240, 800)):
        editor.resize(width, height)
        for block in block_library():
            editor.add_type.setCurrentIndex(editor.add_type.findData(block.block_id))
            editor.add_step()
            editor.property_filter.setText("id")
            QApplication.processEvents()
            editor.property_filter.clear()
            QApplication.processEvents()
            QApplication.processEvents()
            for group in editor.property_groups.values():
                form = group.form()
                previous_bottom = -1
                for row in range(form.rowCount()):
                    label_item = form.itemAt(row, QFormLayout.LabelRole)
                    field_item = form.itemAt(row, QFormLayout.FieldRole)
                    if not label_item or not field_item:
                        continue
                    label, field = label_item.widget(), field_item.widget()
                    if not label.isVisible() or not field.isVisible():
                        continue
                    context = (width, block.step_type, label.text())
                    assert label.geometry().top() > previous_bottom, context
                    assert field.geometry().top() > label.geometry().bottom(), context
                    assert field.height() >= min(field.maximumHeight(), field.minimumSizeHint().height()), context
                    previous_bottom = field.geometry().bottom()
