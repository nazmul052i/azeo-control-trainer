"""Explorer-launched Graphics Designer owns an independent, live window session."""
import os
from pathlib import Path
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from azeo_control_trainer.azeo_control_designer.designer_window import StrategyDesignerWindow
from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore, PvmDisplay
from azeo_control_trainer.core.strategy.serialization import strategy_io


def flush(app):
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.fixture
def session(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    from azeo_control_trainer.azeo_graphics_designer import window as graphics_window
    class WorkspaceSettings(QSettings):
        def __init__(self, *_args):
            super().__init__(str(tmp_path / "workspace.ini"), QSettings.IniFormat)
    monkeypatch.setattr(graphics_window, "QSettings", WorkspaceSettings)
    monkeypatch.setattr(strategy_io, "STRATEGY_DIR", tmp_path)
    monkeypatch.setattr(strategy_io, "_SETTINGS_PATH", tmp_path / "settings.json")
    store = DisplayStore(tmp_path / "displays" / "pvm")
    store.save_draft(PvmDisplay(name="Overview"))
    host = StrategyDesignerWindow()
    shell = QWidget()
    host.explorer = shell
    shell.show()
    yield app, host, shell, store
    editor = getattr(host, "_pvm_studio_window", None)
    if editor is not None and shiboken6.isValid(editor):
        editor._confirm_studios_close = lambda _studios: True
        editor.close()
    shell.close()
    if shiboken6.isValid(host):
        host.close()
        host.deleteLater()
    shell.deleteLater()
    flush(app)


def test_editor_is_independent_and_control_close_keeps_session_alive(session):
    app, host, shell, _store = session
    editor = host._pvm_studio()
    flush(app)
    assert editor.parentWidget() is None
    assert editor.windowHandle().transientParent() is None
    host.show()
    host.showMinimized()
    flush(app)
    assert not editor.isMinimized()
    host.close()
    flush(app)
    assert editor.isVisible() and shell.isVisible()
    assert host._pvm_studio_window is editor
    assert editor._timer.isActive() and editor.current()._timer.isActive()
    assert host._tick_timer.isActive()
    assert editor.current()._holds_lock


def test_closed_editor_reopens_with_fresh_timers_and_lock(session):
    app, host, _shell, _store = session
    editor = host._pvm_studio()
    canvas = editor.current()
    editor.close()
    # Reopening before DeferredDelete runs must not resurrect the closing one.
    reopened = host._pvm_studio()
    assert reopened is not editor
    flush(app)
    assert not shiboken6.isValid(editor)
    assert not shiboken6.isValid(canvas)
    assert host._pvm_studio_window is reopened
    assert reopened.isVisible() and reopened._timer.isActive()
    assert reopened.current()._timer.isActive() and reopened.current()._holds_lock


def test_cancelled_close_and_reactivation_preserve_current_document(session):
    app, host, _shell, _store = session
    editor = host._pvm_studio()
    editor._confirm_studios_close = lambda _studios: False
    assert not editor.close()
    editor.showMaximized()
    flush(app)
    assert host._pvm_studio() is editor
    assert editor.isMaximized()
    assert editor._timer.isActive() and editor.current()._holds_lock


def test_destroying_runtime_host_releases_detached_editor(session):
    app, host, _shell, _store = session
    editor = host._pvm_studio()
    host.deleteLater()
    flush(app)
    flush(app)
    assert not shiboken6.isValid(editor)


def test_library_cancel_preserves_the_parent_draft(session):
    from azeo_control_trainer.azeo_graphics_designer.window import HmiStudioWindow
    app, host, _shell, store = session
    editor = host._pvm_studio()
    library = HmiStudioWindow(lambda: {}, store.root.parent / "library")
    editor._library_windows.append(library)
    first, second = editor.current(), library.current()
    first.unsaved = second.unsaved = True
    editor._studio_close_decision = lambda studio: "discard" if studio is first else None
    assert not editor.close()
    assert first.unsaved and second.unsaved
    assert first._timer.isActive() and second._timer.isActive()
    assert host._pvm_studio_window is editor
    editor._studio_close_decision = lambda _studio: "discard"
    assert editor.close()
    flush(app)
    assert not shiboken6.isValid(library)


def test_configuration_cancel_keeps_editor_and_document_live(session, monkeypatch):
    app, host, _shell, _store = session
    editor = host._pvm_studio()
    config = editor.open_pvm_config()
    current = editor.current()
    current.unsaved = True
    config.unsaved = True
    with monkeypatch.context() as patch:
        patch.setattr(config, "close", lambda: False)
        assert not editor.close()
        assert config.isVisible() and current.unsaved and current._timer.isActive()


@pytest.mark.parametrize("choice", [QMessageBox.Save, QMessageBox.Cancel])
def test_control_close_keeps_save_and_cancel_without_stopping_shared_host(session, monkeypatch, choice):
    from azeo_control_trainer.azeo_control_designer import designer_window
    app, host, shell, _store = session
    saved = []
    canvas = SimpleNamespace(dirty=True, file_path="module.json")
    monkeypatch.setattr(designer_window, "_is_headless", lambda: False)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: choice)
    monkeypatch.setattr(host.designer, "has_unsaved_changes", lambda: ["M0"])
    monkeypatch.setattr(host.designer, "get_unsaved_canvases", lambda: [("M0", canvas)])
    monkeypatch.setattr(host.designer, "_save", lambda item: saved.append(item))
    host.show()
    flush(app)
    assert host.close() is (choice == QMessageBox.Save)
    assert saved == ([canvas] if choice == QMessageBox.Save else [])
    assert host.isVisible() is (choice == QMessageBox.Cancel)
    assert shell.isVisible() and host._tick_timer.isActive()
