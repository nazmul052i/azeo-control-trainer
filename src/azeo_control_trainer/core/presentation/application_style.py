"""Application defaults beneath each product's own widget and display styles."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from .brand import UI


def apply_application_style(app: QApplication) -> None:
    """Install the silver UI baseline before creating any product windows.

    Partial stylesheets leave native dialogs, containers and control states
    using the system palette. On a dark Windows desktop that mixed dark
    surfaces and white text into the silver engineering UI. Set both the
    native appearance hint and every palette role; Fusion also honors these
    colors on supported older Qt versions without the appearance setter.

    This supplies defaults, not a global stylesheet: the Operator Station's
    runtime themes still own their display, chrome and faceplate colors.
    """
    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):
        hints.setColorScheme(Qt.ColorScheme.Light)
    # Replacing a live Fusion style repolishes every retained widget and can
    # hang inside Qt after several engineering windows have been opened.
    if app.style().objectName().lower() != "fusion":
        app.setStyle("Fusion")

    palette = QPalette()
    colors = {
        QPalette.Window: UI.page,
        QPalette.WindowText: UI.text,
        QPalette.Base: UI.pane,
        QPalette.AlternateBase: UI.table_alternate,
        QPalette.ToolTipBase: UI.pane,
        QPalette.ToolTipText: UI.text,
        QPalette.Text: UI.text,
        QPalette.Button: UI.chrome,
        QPalette.ButtonText: UI.text,
        QPalette.BrightText: UI.on_blue,
        QPalette.Light: UI.pane,
        QPalette.Midlight: UI.chrome_alt,
        QPalette.Mid: UI.border,
        QPalette.Dark: UI.disabled,
        QPalette.Shadow: UI.text_secondary,
        QPalette.Highlight: UI.selection,
        QPalette.HighlightedText: UI.blue,
        QPalette.Link: UI.blue,
        QPalette.LinkVisited: UI.menu_hover,
        QPalette.PlaceholderText: UI.text_muted,
        QPalette.Accent: UI.blue,
    }
    for role, color in colors.items():
        # Set Active, Inactive and Disabled explicitly so later OS palette
        # updates cannot reintroduce a dark role when a window loses focus.
        palette.setColor(QPalette.All, role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor(UI.text_muted))
    palette.setColor(QPalette.Disabled, QPalette.Base, QColor(UI.chrome))
    palette.setColor(QPalette.Disabled, QPalette.Highlight, QColor(UI.border_light))
    palette.setColor(QPalette.Disabled, QPalette.HighlightedText, QColor(UI.text_muted))
    if app.palette() != palette:
        app.setPalette(palette)
