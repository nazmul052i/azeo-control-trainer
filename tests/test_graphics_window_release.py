"""A closed Graphics Designer window is released: nothing in its inspector outlives the session.

One parentless inspector holder was enough to leak a whole window per open/close
cycle: a Python-owned widget stays alive while its wrapper is referenced, its
editors' signal lambdas are held from C++, and those lambdas reference the pane,
which references the window, which references the holder. The collector cannot
see the C++ edge, so the cycle never breaks. Every inspector widget must have a
Qt parent, and a closed window must be gone after deferred deletes run.
"""
import gc
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402
import shiboken6  # noqa: E402
from PySide6.QtCore import QCoreApplication, QEvent, QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from azeo_control_trainer.azeo_control_designer.designer_window import StrategyDesignerWindow  # noqa: E402
from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore, PvmDisplay  # noqa: E402
from azeo_control_trainer.core.strategy.serialization import strategy_io  # noqa: E402


def flush(app):
    # Outside app.exec() nothing delivers DeferredDelete; a session's event
    # loop does, so the test asks for it the way the loop would.
    for _ in range(3):
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        gc.collect()


def live_editors():
    return [o for o in gc.get_objects() if type(o).__name__ == "HmiStudioWindow"]


@pytest.fixture
def host(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    from azeo_control_trainer.azeo_graphics_designer import window as graphics_window

    class WorkspaceSettings(QSettings):
        def __init__(self, *_args):
            super().__init__(str(tmp_path / "workspace.ini"), QSettings.IniFormat)

    monkeypatch.setattr(graphics_window, "QSettings", WorkspaceSettings)
    monkeypatch.setattr(strategy_io, "STRATEGY_DIR", tmp_path)
    monkeypatch.setattr(strategy_io, "_SETTINGS_PATH", tmp_path / "settings.json")
    DisplayStore(tmp_path / "displays" / "pvm").save_draft(PvmDisplay(name="Overview"))
    designer = StrategyDesignerWindow()
    shell = QWidget()
    designer.explorer = shell
    shell.show()
    yield app, designer
    editor = getattr(designer, "_pvm_studio_window", None)
    if editor is not None and shiboken6.isValid(editor):
        editor._confirm_studios_close = lambda _studios: True
        editor.close()
    shell.close()
    if shiboken6.isValid(designer):
        designer.close()
        designer.deleteLater()
    shell.deleteLater()
    flush(app)


def test_every_inspector_widget_has_a_qt_parent(host):
    app, designer = host
    editor = designer._pvm_studio()
    flush(app)
    studios = editor.studios()
    assert studios, "the Overview draft should open a studio"
    orphans = [f"{type(studio.pane).__name__}.{name}"
               for studio in studios
               for name, value in vars(studio.pane).items()
               if isinstance(value, QWidget) and shiboken6.isValid(value) and value.parent() is None]
    assert not orphans, orphans


def test_closing_the_editor_releases_every_window_object(host):
    app, designer = host
    flush(app)
    baseline = len(live_editors())
    editor = designer._pvm_studio()
    flush(app)
    assert editor.studios()
    editor._confirm_studios_close = lambda _studios: True
    assert editor.close()
    del editor
    flush(app)
    assert len(live_editors()) <= baseline, "a closed Graphics Designer window is still referenced"
    assert getattr(designer, "_pvm_studio_window", None) is None
