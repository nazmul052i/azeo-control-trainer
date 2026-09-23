"""Windows dark defaults must not leak into Azeo's silver engineering UI."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6 import QtWidgets
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QLineEdit, QMenu, QRadioButton

from azeo_control_trainer.core.presentation.brand import UI


@pytest.fixture
def dark_desktop():
    app = QApplication.instance() or QApplication([])
    previous_palette = QPalette(app.palette())
    previous_stylesheet = app.styleSheet()
    previous_style = app.style().objectName()
    hints = app.styleHints()
    scheme = hints.colorScheme()
    if hasattr(hints, "setColorScheme"):
        hints.setColorScheme(Qt.ColorScheme.Dark)
    palette = QPalette()
    for role in (QPalette.Window, QPalette.Base, QPalette.Button, QPalette.ToolTipBase):
        palette.setColor(role, QColor("#202020"))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText, QPalette.ToolTipText):
        palette.setColor(role, QColor("#eeeeee"))
    palette.setColor(QPalette.Highlight, QColor("#006060"))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    app.setStyleSheet("")
    app.setPalette(palette)
    yield app
    app.setStyleSheet(previous_stylesheet)
    if previous_style:
        app.setStyle(previous_style)
    if hasattr(hints, "setColorScheme"):
        hints.setColorScheme(scheme)
    app.setPalette(previous_palette)
    app.processEvents()


def _bootstrap(app, monkeypatch, tmp_path, entry):
    # Exercise each real process entry point through its application setup,
    # stopping before project loading or acquiring a working-draft lock.
    class ExistingApplication(QApplication):
        def __new__(cls, *args):
            return app

    monkeypatch.setattr(QtWidgets, "QApplication", ExistingApplication)
    monkeypatch.setattr(sys, "argv", ["azeo", str(tmp_path)])
    from azeo_control_trainer.core.presentation import headless
    monkeypatch.setattr(headless, "is_headless", lambda: True)
    if entry == "suite":
        from azeo_control_trainer import app as module
        from azeo_control_trainer.config import logging_config
        for name in ("setup_logging", "install_qt_message_logging"):
            monkeypatch.setattr(logging_config, name, lambda *args: None)

        def stop_before_project(*args):
            raise ValueError("Appearance checked before loading a project")

        monkeypatch.setattr(module, "_resolve_area", stop_before_project)
        assert module.main() == 2
    else:
        from PySide6.QtCore import QLockFile
        from importlib import import_module
        module = import_module(f"azeo_control_trainer.azeo_explorer.configuration_{entry}")
        monkeypatch.setattr(module, "QApplication", ExistingApplication, raising=False)
        monkeypatch.setattr(QLockFile, "tryLock", lambda *args: False)
        assert module.main() == (1 if entry == "workspace" else 2)
    app.processEvents()


@pytest.mark.parametrize("entry", ("suite", "workspace", "runtime"))
def test_launch_replaces_dark_defaults_before_opening_windows(dark_desktop, monkeypatch, tmp_path, entry):
    app = dark_desktop
    _bootstrap(app, monkeypatch, tmp_path, entry)
    if app.platformName() == "windows" and hasattr(app.styleHints(), "setColorScheme"):
        assert app.styleHints().colorScheme() == Qt.ColorScheme.Light
    # Dialogs and native controls without complete QSS used to inherit dark
    # surfaces, while their neighbouring panels supplied silver backgrounds.
    dialog = QDialog()
    controls = (QLabel("Label", dialog), QLineEdit("Value", dialog),
                QRadioButton("Option", dialog), QMenu("Menu", dialog))
    try:
        for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
            assert dialog.palette().color(group, QPalette.Window) == QColor(UI.page)
        for control in controls:
            control.ensurePolished()
            palette = control.palette()
            assert palette.color(QPalette.Active, QPalette.WindowText) == QColor(UI.text)
            assert palette.color(QPalette.Active, QPalette.Base) == QColor(UI.pane)
            assert palette.color(QPalette.Active, QPalette.Text) == QColor(UI.text)
            assert palette.color(QPalette.Inactive, QPalette.Text) == QColor(UI.text)
    finally:
        dialog.close()
        dialog.deleteLater()
