"""Station-local Qt styling; shared engineering widgets keep their own style."""
from __future__ import annotations

from functools import lru_cache
import logging
import weakref

from PySide6.QtCore import QObject, Slot
from PySide6.QtGui import QColor, QPalette
from shiboken6 import isValid

from .roles import Role
from .tokens import THEMES

log = logging.getLogger(__name__)

# Retain the engineering tool's established control sizes when that tool is
# hosted by the station. Measured equipment faceplates do not use these rules.
TOOL_METRICS = """
QWidget { font-family: 'Segoe UI'; font-size: 9.5pt; }
QPushButton, QToolButton { padding: 5px 10px; }
QLineEdit, QComboBox, QAbstractSpinBox { padding: 5px 10px; min-height: 20px; }
QComboBox { padding-right: 28px; }
QAbstractSpinBox QLineEdit, QComboBox QLineEdit { padding: 0; min-height: 0; border: none; }
QHeaderView::section { padding: 5px 8px; }
QTabBar::tab { padding: 5px 10px; }
QTreeView::item { min-height: 24px; padding: 2px 5px; }
"""


def operator_service(widget):
    """Find the owning console, without choosing another console's defaults."""
    current = widget
    while current is not None:
        service = getattr(current, "themes", None)
        if service is not None and hasattr(service, "current"):
            return service
        current = current.parent()
    return None


@lru_cache(maxsize=8)
def widget_stylesheet(theme: str) -> str:
    p = THEMES[theme]
    return f"""
        QWidget {{ background-color: {p[Role.SURFACE_PANEL]}; color: {p[Role.TEXT]}; }}
        QLabel {{ background-color: transparent; }}
        QLineEdit, QAbstractSpinBox, QTextEdit, QPlainTextEdit, QComboBox,
        QAbstractItemView {{ background-color: {p[Role.SURFACE_FIELD]}; color: {p[Role.TEXT]};
            border: 1px solid {p[Role.LINE]};
            selection-background-color: {p[Role.SELECTION]}; selection-color: {p[Role.ON_SELECTION]}; }}
        QAbstractItemView {{ alternate-background-color: {p[Role.SURFACE_PANEL_ALT]}; }}
        QLineEdit:read-only {{ color: {p[Role.TEXT_DIM]}; }}
        QPushButton, QToolButton {{ background-color: {p[Role.SURFACE_PANEL_ALT]};
            color: {p[Role.ACTION]}; border: 1px solid {p[Role.LINE]}; }}
        QPushButton:hover, QToolButton:hover, QComboBox:hover {{ background-color: {p[Role.SURFACE_SUNK]}; }}
        QPushButton:checked, QToolButton:checked, QPushButton:pressed, QToolButton:pressed {{
            background-color: {p[Role.SELECTION]}; color: {p[Role.ON_SELECTION]}; }}
        QPushButton:focus, QToolButton:focus, QLineEdit:focus, QComboBox:focus,
        QAbstractSpinBox:focus, QAbstractItemView:focus {{ border: 1px solid {p[Role.FOCUS]}; }}
        QWidget:disabled {{ color: {p[Role.TEXT_DIM]}; }}
        QHeaderView::section {{ background-color: {p[Role.SURFACE_PANEL_ALT]};
            color: {p[Role.TEXT]}; border: 1px solid {p[Role.LINE_SOFT]}; }}
        QTabWidget::pane {{ border: 1px solid {p[Role.LINE]}; }}
        QTabBar::tab {{ background-color: {p[Role.SURFACE_PANEL_ALT]}; color: {p[Role.TEXT_DIM]}; }}
        QTabBar::tab:selected {{ background-color: {p[Role.SELECTION]}; color: {p[Role.ON_SELECTION]}; }}
        QMenu, QToolTip {{ background-color: {p[Role.SURFACE_PANEL_ALT]}; color: {p[Role.TEXT]};
            border: 1px solid {p[Role.LINE]}; }}
        QMenu::item:selected {{ background-color: {p[Role.SELECTION]}; color: {p[Role.ON_SELECTION]}; }}
        QMenu::item {{ padding: 7px 24px 7px 12px; }}
        QMenu::item:disabled {{ color: {p[Role.TEXT_DIM]}; }}
        QMenu::separator {{ height: 1px; background-color: {p[Role.LINE]}; }}
        QScrollBar {{ background-color: {p[Role.SURFACE_SUNK]}; }}
        QScrollBar::handle {{ background-color: {p[Role.LINE]}; }}
        QSplitter::handle {{ background-color: {p[Role.LINE_SOFT]}; }}
        QProgressBar {{ background-color: {p[Role.SURFACE_SUNK]}; color: {p[Role.TEXT]};
            border: 1px solid {p[Role.LINE]}; }}
        QProgressBar::chunk {{ background-color: {p[Role.BAR_PV]}; }}
    """


@lru_cache(maxsize=16)
def _qt_palette(values, surface):
    p = dict(zip(Role, values))
    palette = QPalette()
    roles = {
        QPalette.Window: surface, QPalette.WindowText: Role.TEXT,
        QPalette.Base: Role.SURFACE_FIELD, QPalette.AlternateBase: Role.SURFACE_PANEL_ALT,
        QPalette.Text: Role.TEXT, QPalette.Button: Role.SURFACE_PANEL_ALT,
        QPalette.ButtonText: Role.ACTION, QPalette.Highlight: Role.SELECTION,
        QPalette.HighlightedText: Role.ON_SELECTION, QPalette.ToolTipBase: Role.SURFACE_PANEL_ALT,
        QPalette.ToolTipText: Role.TEXT, QPalette.Link: Role.FOCUS,
        QPalette.LinkVisited: Role.ACTION_DEEP, QPalette.PlaceholderText: Role.TEXT_DIM,
        QPalette.Light: Role.LINE, QPalette.Midlight: Role.TEXT_DIM,
        QPalette.Mid: Role.LINE_SOFT, QPalette.Dark: Role.SURFACE_SUNK,
        QPalette.Shadow: Role.TEXT_FAINT, QPalette.BrightText: Role.HEADING,
    }
    for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
        for native, role in roles.items():
            if group == QPalette.Disabled and native in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
                role = Role.TEXT_DIM
            palette.setColor(group, native, QColor(p[role]))
    return palette


def apply_role_palette(widget, palette, *, surface=Role.SURFACE_PANEL):
    values = tuple(palette.get(role, THEMES["silver"][role]) for role in Role)
    widget.setPalette(_qt_palette(values, surface))


def set_stylesheet(widget, stylesheet):
    """Avoid native repolish when a semantic or geometric rule did not change."""
    if widget.styleSheet() != stylesheet:
        widget.setStyleSheet(stylesheet)


def set_surface_theme(widget, palette):
    """Flat containers use a native palette, avoiding descendant CSS rebuilds."""
    apply_role_palette(widget, palette)
    widget.setAutoFillBackground(True)


def apply_widget_theme(widget, theme: str, *, surface=Role.SURFACE_PANEL, extra="", basic=False):
    """Supply every native palette role locally, including disabled controls."""
    p = THEMES[theme]
    apply_role_palette(widget, p, surface=surface)
    if basic:
        # An inherited engineering stylesheet outranks a widget's Qt
        # palette. Keep this local rule even on painted canvases, or native
        # editors and alarm tables inherit engineering colors in Dark mode.
        set_stylesheet(widget, f"background: {p[surface]}; color: {p[Role.TEXT]};")
        widget.setAutoFillBackground(True)
    else:
        stylesheet = widget_stylesheet(theme) + extra
        if widget.styleSheet() != stylesheet:
            widget.setStyleSheet(stylesheet)
    widget._hmi_theme_name = theme
    from PySide6.QtWidgets import QAbstractButton
    from azeo_control_trainer.core.presentation.studio_icons import studio_icon
    for button in widget.findChildren(QAbstractButton):
        icon = button.property("operator_icon")
        if icon:
            button.setIcon(studio_icon(icon, 17, p[Role.ACTION]))


class WidgetThemeBinding(QObject):
    """Qt owns the subscription; weak references cannot retain a closed tool."""

    def __init__(self, widget, service):
        super().__init__(widget)
        self._widget = weakref.ref(widget)
        self._service = weakref.ref(service)
        self._current = None
        service.changed.connect(self._changed)

    def apply_theme(self, name, *, force=False):
        widget = self._widget()
        if widget is None or not isValid(widget) or (name == self._current and not force):
            return
        apply_widget_theme(widget, name, extra=getattr(widget, "_hmi_theme_extra", ""))
        custom = getattr(widget, "apply_operator_theme", None)
        if custom is not None:
            custom(name)
        self._current = name

    @Slot(str)
    def _changed(self, name):
        # The station applies owned bindings before committing its selection.
        # A later signal is normally a no-op; standalone reparented tools still
        # have a protected Qt callback rather than leaking an exception into Qt.
        try:
            service = self._service()
            if service is None or service.current != name:
                return
            self.apply_theme(name)
        except Exception:
            log.exception("Could not apply operator theme %s", name)


def bind_operator_theme(widget, *, tool_controls=False):
    """Opt a shared operating tool into its owner's theme after building it."""
    existing = getattr(widget, "_hmi_theme_binding", None)
    if existing is not None:
        return existing
    service = operator_service(widget)
    if service is None:
        return None
    if tool_controls:
        widget._hmi_theme_extra = TOOL_METRICS
    binding = WidgetThemeBinding(widget, service)
    widget._hmi_theme_binding = binding
    binding.apply_theme(service.current)
    return binding
