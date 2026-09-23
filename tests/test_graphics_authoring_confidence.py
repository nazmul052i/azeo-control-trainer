"""Authoring feedback and previews must describe the document actually in use."""
import copy
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from azeo_control_trainer.azeo_graphics_designer.quick_online import QuickOnlineView
from azeo_control_trainer.azeo_graphics_designer.studio.assembler import PvmStudio
from azeo_control_trainer.core.hmi.binding import LiveGraphSource
from azeo_control_trainer.core.hmi.pvms.publishing import PvmDisplay


def application():
    from azeo_control_trainer.core.hmi.theme.fonts import ensure_font_directory
    ensure_font_directory()
    return QApplication.instance() or QApplication([])


@pytest.fixture
def studio(tmp_path):
    app = application()
    widget = PvmStudio(lambda: {}, tmp_path)
    widget.enter_edit()
    yield widget
    widget.close()
    widget.deleteLater()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.fixture
def preview(tmp_path):
    app = application()
    widget = QuickOnlineView(lambda: {}, source=LiveGraphSource(lambda: {}), config_root=tmp_path)
    yield widget
    widget.close()
    widget.deleteLater()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def disk_failure(*_args):
    raise OSError("Disk is full")


def test_recovery_failure_is_visible_and_retry_preserves_draft(studio, monkeypatch):
    studio.add_static("rect", 50, 50, 80, 60)
    saved = studio.store.draft_fingerprint(studio.display.name)
    with monkeypatch.context() as patch:
        patch.setattr(studio.store, "save_recovery", disk_failure)
        assert studio._write_recovery() is False
        assert "Disk is full" in studio.recovery_error
        assert "unavailable" in studio.recovery_status().lower()
        assert studio.unsaved
    assert studio._write_recovery()
    assert not studio.recovery_error and studio.recovery_saved_at
    assert studio.store.load_recovery(studio.display.name) is not None
    assert studio.store.draft_fingerprint(studio.display.name) == saved


def test_continuous_edits_do_not_restart_recovery_deadline(studio):
    studio.mark_unsaved()
    deadline = studio._recovery_deadline_timer
    first = deadline.remainingTime()
    assert 0 < first <= 30_000
    for _ in range(15):
        studio.mark_unsaved()
    assert deadline.remainingTime() <= first
    assert studio._write_recovery()
    assert not deadline.isActive()


def test_failed_save_keeps_unsaved_document_and_reports_error(studio, monkeypatch):
    studio.add_static("rect", 50, 50, 80, 60)
    before = copy.deepcopy(studio._document())
    errors = []
    studio.uiError.connect(errors.append)
    with monkeypatch.context() as patch:
        patch.setattr(studio.store, "save_draft", disk_failure)
        assert studio.save_draft() is False
    assert studio.unsaved and studio._document() == before
    assert any("Disk is full" in message for message in errors)
    assert studio.save_draft()


def test_quick_online_refreshes_the_draft_instead_of_reusing_old_content(preview):
    document = PvmDisplay(name="Unit", description="first").to_dict()
    original = preview.add_display(document)
    document["description"] = "latest unsaved edit"
    current = preview.add_display(document)
    assert current.display.description == "latest unsaved edit"
    assert preview.documents() == ("Unit",)
    assert original._disposed and not original._timer.isActive()
    assert document["description"] == "latest unsaved edit"


def test_preview_close_releases_timers_and_reopen_builds_a_live_view(preview):
    document = PvmDisplay(name="Unit").to_dict()
    old = preview.add_display(document)
    preview.close()
    assert old._disposed and not old._timer.isActive()
    assert preview.documents() == ()
    assert preview.add_display(document) is not old


def test_theme_changes_keep_view_and_block_process_writes(preview):
    view = preview.add_display(PvmDisplay(name="Unit").to_dict())
    engine = view.engine
    scene = view.scene()
    assert preview.choose_theme("dark")
    from azeo_control_trainer.core.hmi.theme.roles import Role
    from azeo_control_trainer.core.hmi.theme.tokens import THEMES
    assert view.palette_roles[Role.SURFACE_BG] == THEMES["dark"][Role.SURFACE_BG]
    assert view.engine is engine and view.scene() is scene
    assert not preview.source.write("M/PID/SP", 42).success


def test_blocked_pipe_can_select_its_obstruction_without_editing(studio):
    item = studio.add_static("rect", 200, 100, 80, 60)
    from azeo_control_trainer.core.hmi.pvms.rendering.items import PipeItem
    from azeo_control_trainer.core.hmi.theme.tokens import THEMES, DEFAULT_THEME
    pipe = PipeItem({"id": "blocked", "kind": "pipe"}, THEMES[DEFAULT_THEME])
    studio.canvas.scene().addItem(pipe)
    pipe.route_status = "blocked"
    pipe.route_collisions = (item.data["id"],)
    before = copy.deepcopy(studio._document())
    assert studio.show_route_obstructions(pipe) == 1
    assert item.isSelected()
    assert studio._document() == before


def test_faceplate_and_detail_follow_theme_without_rebinding(preview):
    from azeo_control_trainer.core.hmi.pvms.base import Pvm
    from azeo_control_trainer.core.hmi.theme.tokens import THEMES
    pvm = Pvm("pid", "", "PID", "display", {"path": "M/PID"})
    face = preview.open_faceplate(pvm)
    assert face is not None
    assert preview.open_faceplate(pvm) is face
    original = dict(face.bound)
    face.action_requested.emit("detail")
    assert len(preview.faces) == 2
    for theme in ("dark", "hpgray", "silver"):
        assert preview.choose_theme(theme)
        assert all(popup.palette_roles is THEMES[theme] for popup in preview.faces)
        assert face.bound == original
    assert not face.write_handler("M/PID/SP", 99).success
    preview.close()
    assert not face.bound and not face._timer.isActive()


def test_user_faceplate_preview_carries_context_and_has_no_write_authority(preview):
    from types import SimpleNamespace
    from azeo_control_trainer.core.hmi.pvms.user_library import UserPvmLibrary
    from azeo_control_trainer.core.hmi.theme.tokens import THEMES
    library = UserPvmLibrary(preview.config_root)
    library.add("Procedure face", [{"kind": "rect", "id": "panel", "x": 0, "y": 0,
                                  "w": 240, "h": 320, "fill_role": "SURFACE_PANEL"}],
                definition_kind="faceplate")
    source_item = SimpleNamespace(data={"pvm_choices": {"ProcedureRef": "safe_landing"}})
    action = {"kind": "open_user_faceplate", "target": "Procedure face"}
    assert preview.display_action(action, item=source_item)
    face = preview.faces[0]
    assert face.instance_choices == source_item.data["pvm_choices"]
    assert preview.display_action(action, item=source_item)
    assert len(preview.faces) == 1
    preview.choose_theme("dark")
    assert face.palette_roles is THEMES["dark"]
    assert not face.engine._source.write("M/PID/SP", 13).success
    assert not preview.display_action({"kind": "procedure_command", "target": "start"}, face)
    assert not preview.display_action({"kind": "script", "source": "anything"}, face)
    preview.close()
    assert face._disposed and not face._timer.isActive()


def test_preview_resolution_measures_display_viewport_and_restores_fit(preview):
    view = preview.add_display(PvmDisplay(name="Unit", width=1600, height=900).to_dict())
    preview.show()
    preview.resolution_selector.setCurrentIndex(2)
    QApplication.processEvents()
    assert (view.viewport().width(), view.viewport().height()) == (1920, 1080)
    preview.resolution_selector.setCurrentIndex(0)
    QApplication.processEvents()
    assert view.maximumWidth() > 1920
    assert view.width() <= preview.width()


def test_only_active_preview_samples_and_unknown_theme_is_refused(preview):
    first = preview.add_display(PvmDisplay(name="One").to_dict())
    second = preview.add_display(PvmDisplay(name="Two").to_dict())
    assert not first._timer.isActive() and second._timer.isActive()
    assert preview.open_display("One")
    assert first._timer.isActive() and not second._timer.isActive()
    theme = preview.theme
    assert not preview.choose_theme("not-a-theme")
    assert preview.theme == theme


def test_new_display_uses_operator_theme_and_recovery_status_is_persistent(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from azeo_control_trainer.azeo_graphics_designer import window as window_module
    class WorkspaceSettings(QSettings):
        def __init__(self, *_args):
            super().__init__(str(tmp_path / "workspace.ini"), QSettings.IniFormat)
    monkeypatch.setattr(window_module, "QSettings", WorkspaceSettings)
    app = application()
    window = window_module.HmiStudioWindow(lambda: {}, tmp_path / "displays")
    try:
        studio = window._open_created_display("Theme ready")
        assert not studio.display.background
        studio.add_static("rect", 20, 20, 60, 60)
        with monkeypatch.context() as patch:
            patch.setattr(studio.store, "save_recovery", disk_failure)
            studio._write_recovery()
        window.statusBar().clearMessage()
        assert "unavailable" in window._recovery_segment.text().lower()
        assert "Disk is full" in window._recovery_segment.toolTip()
        window._recovery_segment.click()
        assert studio.recovery_saved_at in window._recovery_segment.text()
        view = window.open_quick_online()
        window.close()
        assert view._disposed and not view._timer.isActive()
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_recovery_failure_during_close_does_not_schedule_another_retry(studio, monkeypatch):
    studio.mark_unsaved()
    with monkeypatch.context() as patch:
        patch.setattr(studio.store, "save_recovery", disk_failure)
        studio.close()
    assert not studio._recovery_timer.isActive()
    assert not studio._recovery_deadline_timer.isActive()


def test_preview_click_opens_real_faceplate_and_export_button_writes_image(preview, monkeypatch, tmp_path):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QImage
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QFileDialog, QPushButton
    from azeo_control_trainer.core.hmi.pvms.base import Pvm
    from azeo_control_trainer.core.presentation import headless
    document = PvmDisplay(name="Unit", width=900, height=600,
                          pvms=[Pvm("pid", "", "PID", "display", {"path": "M/PID"},
                                    x=100, y=100, w=210, h=140)]).to_dict()
    view = preview.add_display(document)
    preview.show()
    QApplication.processEvents()
    QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=view.mapFromScene(QPointF(200, 170)))
    assert len(preview.faces) == 1
    output = tmp_path / "preview.png"
    monkeypatch.setattr(headless, "is_headless", lambda: False)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args: (str(output), "PNG"))
    next(button for button in preview.findChildren(QPushButton)
         if button.text() == "Export image…").click()
    captured = QImage(str(output))
    assert not captured.isNull()
    assert captured.width() == view.viewport().width() * view.devicePixelRatioF()
