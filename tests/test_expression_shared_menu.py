"""Expression menus keep their editor owner and insert the chosen function."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from azeo_control_trainer.azeo_control_designer.widgets.expression_editor_ribbon import RibbonExpressionEditor
from azeo_control_trainer.core.presentation.menu_style import MENU_QSS


def test_function_and_recent_menu_share_chrome_and_editor_target():
    app = QApplication.instance() or QApplication([])
    editor = RibbonExpressionEditor()
    try:
        menu = editor._fn_btn.menu()
        assert menu.parent() is editor
        assert menu.styleSheet() == MENU_QSS
        menu.aboutToShow.emit()
        category = next(a.menu() for a in menu.actions() if a.text() == "Process Control")
        assert category.styleSheet() == MENU_QSS
        next(a for a in category.actions() if a.text() == "clamp").trigger()
        assert editor.editor.toPlainText() == "clamp()"
        recent = editor._recent_btn.menu()
        assert recent.parent() is editor
        recent.aboutToShow.emit()
        assert any(a.text() == "clamp" for a in recent.actions())
    finally:
        editor.close()
        editor.deleteLater()
        app.processEvents()
