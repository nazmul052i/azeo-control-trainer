"""Workspace geometry, library feedback and status follow real editor state."""
from pathlib import Path
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtWidgets import QApplication, QStatusBar

from azeo_control_trainer.azeo_pa_designer import create_window
from azeo_control_trainer.core.presentation.application_style import apply_application_style
from azeo_control_trainer.core.procedures.authoring import ProcedureDraft


@pytest.fixture
def editor(tmp_path):
    app = QApplication.instance() or QApplication([])
    apply_application_style(app)
    window = create_window(tmp_path, graphs_provider=lambda: [])
    window.show()
    app.processEvents()
    app.processEvents()
    yield window
    window._saved = window.snapshot()
    window.close()
    window.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def test_workspace_gets_desktop_space(editor):
    editor.resize(1920, 1000)
    QApplication.processEvents()
    QApplication.processEvents()
    assert editor.canvas.mapTo(editor, QPoint()).y() < 205
    assert editor.canvas.viewport().height() >= 755
    assert editor.canvas.viewport().width() >= 1250
    assert editor.inspector.width() <= 360
    editor.resize(940, 640)
    QApplication.processEvents()
    QApplication.processEvents()
    assert editor.width() <= 940
    assert editor.canvas.viewport().width() >= 320
    assert editor.canvas.viewport().height() >= 395


def test_status_tracks_edits_selection_and_zoom(editor):
    assert isinstance(editor.status, QStatusBar)
    assert editor.status.document.text() == "Not saved"
    assert editor.status.steps.text() == "3 blocks"
    editor.ribbon.buttons["duplicate"].click()
    assert editor.status.document.text() == "Modified"
    assert editor.status.steps.text() == "4 blocks"
    editor.canvas.zoom_reset()
    editor.canvas.zoom_by(1.25)
    assert editor.status.zoom.text() == "125%"
    editor.status.zoom.click()
    assert editor.canvas.transform().m11() == 1.0
    editor.canvas.select_step("complete")
    assert editor.status.selection.text() == "complete"
    editor.canvas.nodes[editor.canvas.key("review")].setSelected(True)
    assert editor.status.selection.text() == "2 selected"
    editor.show_workflow_view(1)
    assert editor.status.selection.text() == editor.draft.data["steps"][editor.steps.currentRow()]["id"]
    editor.save_revision()
    assert editor.status.document.text() == "Saved"
    QApplication.processEvents()
    segments = (editor.status.document, editor.status.steps, editor.status.selection,
                editor.status.zoom, editor.status.fit)
    assert all(a.geometry().right() < b.geometry().left() for a, b in zip(segments, segments[1:]))


def test_empty_filtered_and_unreadable_libraries_explain_their_state(editor, tmp_path):
    assert editor.library_empty.isVisible()
    assert "No saved procedures" in editor.library_empty.text()
    assert not editor.open_button.isEnabled()
    ProcedureDraft.new().save_revision(tmp_path)
    editor.refresh_library()
    assert editor.library_list.count() == 1
    assert not editor.library_empty.isVisible()
    editor.library_list.setCurrentRow(0)
    editor.search.setText("nothing matches")
    assert "No matching procedures" in editor.library_empty.text()
    assert not editor.open_button.isEnabled()
    editor.search.clear()
    assert not editor.library_empty.isVisible()
    broken = editor.library / "broken.yaml"
    broken.write_text("name: [unterminated", encoding="utf-8")
    editor.refresh_library()
    assert "1 unreadable" in editor.library_summary.text()
    assert "broken.yaml" in editor.review.toPlainText()


def test_reopening_refreshes_library_without_changing_the_draft(editor, tmp_path):
    editor.hide()
    ProcedureDraft.new().save_revision(tmp_path)
    before = editor.snapshot()
    editor.show()
    QApplication.processEvents()
    assert editor.library_list.count() == 1
    assert editor.snapshot() == before


def test_plant_library_includes_both_saved_safe_landing_revisions(editor):
    editor.library = Path(__file__).resolve().parents[1] / "projects/AzeoPlantVirtualController/procedures"
    editor.refresh_library()
    rows = [editor.library_list.item(i).text() for i in range(editor.library_list.count())]
    assert any("Safe Landing" in row and "rev-001" in row for row in rows)
    assert any("Safe Landing" in row and "rev-002" in row for row in rows)


def test_ribbon_collapse_action_returns_space_and_restores_commands(editor):
    action = editor.ribbon.collapse_action
    button = editor.ribbon.collapse_button
    assert button.toolButtonStyle() == Qt.ToolButtonIconOnly
    assert button.width() == 34
    assert not action.icon().isNull()
    collapse_icon_key = action.icon().cacheKey()
    height = editor.canvas.viewport().height()
    action.trigger()
    QApplication.processEvents()
    QApplication.processEvents()
    assert editor.canvas.viewport().height() > height + 40
    assert action.text() == "Expand ribbon"
    assert not action.icon().isNull()
    assert action.icon().cacheKey() != collapse_icon_key
    assert button.icon().cacheKey() == action.icon().cacheKey()
    editor.ribbon.select_page(1)
    QApplication.processEvents()
    QApplication.processEvents()
    assert editor.ribbon.stack.isVisible()
    assert not action.isChecked()
    assert action.text() == "Collapse ribbon"
    assert not action.icon().isNull()
