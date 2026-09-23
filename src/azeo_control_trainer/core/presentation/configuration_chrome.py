"""Configuration workspaces use the same controls and vector marks as Control Designer."""
from functools import lru_cache
from html import escape
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QIcon, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QTableView, QToolButton, QVBoxLayout, QWidget,
)

from .authoring_style import AUTHORING_CHROME_QSS
from .brand import UI
from .authoring_controls import AuthoringComboBox
from .studio_icons import studio_icon
from .menu_style import studio_menu

_ASSETS = Path(__file__).with_name("assets").as_posix()

CONFIGURATION_QSS = AUTHORING_CHROME_QSS + f"""
QWidget#configurationWorkspace {{ background: {UI.chrome}; }}
QFrame#configurationHeader {{ background: transparent; border: none; }}
QFrame#configurationAppHeader {{ background: {UI.pane}; border-bottom: 1px solid {UI.border_light}; }}
QFrame#configurationContext {{ background: {UI.pane}; border-bottom: 1px solid {UI.border_light}; }}
QLabel#configurationTitle {{ font-size: 14pt; font-weight: 600; color: {UI.text}; }}
QLabel#configurationSubtitle {{ color: {UI.text_secondary}; }}
QLabel#configurationFieldLabel {{ font-size: 9pt; color: {UI.text_secondary}; font-weight: 600; }}
QLabel#configurationBadge {{ background: {UI.selection}; color: {UI.blue};
    border-radius: 5px; padding: 4px 8px; font-size: 9pt; }}
QLabel#configurationRevision {{ background: {UI.chrome}; color: {UI.text_secondary};
    border: 1px solid {UI.border_light}; border-radius: 5px; padding: 4px 8px; font-size: 9pt; }}
QLabel[configurationState="error"] {{ color: {UI.error}; }}
QLabel[configurationState="working"] {{ color: {UI.blue}; }}
QPushButton, QToolButton {{ border-radius: 6px; min-height: 20px; padding: 5px 10px; }}
QPushButton:focus, QToolButton:focus {{ border-color: {UI.blue}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {UI.selection}; }}
QPushButton[configurationQuiet="true"], QToolButton[configurationQuiet="true"] {{
    background: transparent; border-color: transparent; }}
QPushButton[configurationQuiet="true"]:hover, QToolButton[configurationQuiet="true"]:hover {{
    background: {UI.hover}; border-color: {UI.hover}; }}
QPushButton[configurationQuiet="true"]:focus, QToolButton[configurationQuiet="true"]:focus {{
    border-color: {UI.blue}; }}
QPushButton:disabled, QToolButton:disabled {{ background: {UI.chrome}; border-color: {UI.border_light};
    color: {UI.text_muted}; }}
QPushButton[configurationQuiet="true"]:disabled, QToolButton[configurationQuiet="true"]:disabled {{
    background: transparent; border-color: transparent; }}
QPushButton[configurationPrimary="true"] {{ background: {UI.blue}; color: white;
    border: 1px solid {UI.blue}; font-weight: 600; }}
QPushButton[configurationPrimary="true"]:hover {{ background: {UI.menu_hover}; }}
QPushButton[configurationPrimary="true"]:pressed {{ background: {UI.menu_pressed}; }}
QPushButton[configurationPrimary="true"]:disabled {{ background: {UI.chrome};
    color: {UI.disabled}; border-color: {UI.border}; }}
QToolButton::menu-indicator {{ image: url("{_ASSETS}/chevron-down.svg"); width: 12px; height: 12px;
    subcontrol-position: right center; subcontrol-origin: padding; right: 2px; }}
QToolButton[configurationMore="true"] {{ padding-right: 20px; }}
QMenu {{ background: {UI.pane}; color: {UI.text}; border: 1px solid {UI.field_border};
    border-radius: 6px; padding: 5px; }}
QMenu::item {{ padding: 7px 26px 7px 12px; margin: 1px; border-radius: 4px; }}
QMenu::item:selected {{ background: {UI.selection}; color: {UI.blue}; }}
QMenu::item:disabled {{ color: {UI.disabled}; }}
QMenu::separator {{ height: 1px; background: {UI.border_light}; margin: 4px 8px; }}
QTableView {{ background: {UI.pane}; alternate-background-color: {UI.table_alternate};
    color: {UI.text}; border: 1px solid {UI.border_light}; border-radius: 5px; outline: none; }}
QHeaderView::section {{ background: {UI.table_alternate}; color: {UI.text_secondary}; border: none;
    border-bottom: 1px solid {UI.border}; padding: 7px 10px; font-size: 9pt; font-weight: 600; }}
QTableView::indicator {{ width: 16px; height: 16px; background: {UI.pane};
    border: 1px solid #A9B6C5; border-radius: 3px; }}
QTableView::indicator:checked {{ background: {UI.blue}; border-color: {UI.blue}; image: url("{_ASSETS}/check.svg"); }}
QPlainTextEdit, QTextBrowser {{ background: {UI.pane}; color: {UI.text};
    border: 1px solid {UI.border_light}; border-radius: 5px; padding: 10px; }}
QTextBrowser#configurationProperties {{ border: none; padding: 12px; }}
QFrame#configurationInspector {{ background: {UI.pane}; border: 1px solid {UI.border_light}; border-radius: 6px; }}
QLabel#configurationObjectTitle {{ font-size: 12pt; font-weight: 600; color: {UI.text}; }}
QTabWidget::pane {{ border: none; background: {UI.pane}; }}
QTabBar::tab {{ background: transparent; padding: 8px 11px; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ background: transparent; border-bottom-color: {UI.blue}; color: {UI.blue}; }}
QTabBar::tab:hover {{ background: {UI.hover}; }}
QTabBar QToolButton {{ background: {UI.pane}; border: none; border-radius: 0;
    padding: 0; width: 24px; min-height: 28px; }}
QTabBar QToolButton:hover {{ background: {UI.hover}; }}
QTabBar QToolButton::left-arrow {{ image: url("{_ASSETS}/chevron-left.svg"); width: 16px; height: 16px; }}
QTabBar QToolButton::right-arrow {{ image: url("{_ASSETS}/chevron-right.svg"); width: 16px; height: 16px; }}
QListWidget#configurationNavigation {{ background: {UI.chrome}; border: none;
    border-right: 1px solid {UI.border_light}; padding: 10px 6px; }}
QListWidget#configurationNavigation::item {{ padding: 7px 9px; margin: 1px 2px;
    border-radius: 6px; color: {UI.text_secondary}; }}
QListWidget#configurationNavigation::item:selected {{ background: {UI.selection};
    color: {UI.blue}; font-weight: 600; }}
QFrame#configurationCommands {{ background: transparent; border: none; }}
QSplitter::handle {{ background: {UI.chrome}; }}
QSplitter::handle:hover {{ background: {UI.selection}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ background: {UI.scroll_handle}; border-radius: 3px; min-height: 28px; min-width: 28px; }}
QScrollBar::handle:hover {{ background: {UI.scroll_hover}; }}
QScrollBar::handle:pressed {{ background: {UI.blue}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ border: none; width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QAbstractScrollArea::corner {{ background: transparent; }}
"""


class _RequestAction:
    """Keep selection-driven enablement while a background command is pending."""
    def setEnabled(self, enabled):  # noqa: N802
        self._enabled_after_request = bool(enabled)
        super().setEnabled(enabled and not getattr(self, "_request_busy", False))

    def set_request_busy(self, busy):
        if busy and not getattr(self, "_request_busy", False):
            self._enabled_after_request = self.isEnabled()
        self._request_busy = busy
        super().setEnabled(not busy and self._enabled_after_request)


class ConfigurationButton(_RequestAction, QPushButton):
    pass


class ConfigurationToolButton(_RequestAction, QToolButton):
    pass


class ConfigurationComboBox(AuthoringComboBox):
    """Compatibility name for the shared engineering selector."""


@lru_cache(maxsize=48)
def icon(name):
    return studio_icon(name, 18)


def mark_for(text):
    text = text.lower()
    if text in {"back", "previous"}:
        return "undo"
    for words, mark in (
        (("module", "control/"), "module_props"),
        (("terminal",), "values"), (("parameter",), "params"),
        (("field", "i/o", "io:"), "io_config"),
        (("display", "graphic"), "faceplate"),
        (("next",), "redo"),
        (("recover", "restore", "retry"), "restore"),
        (("backup", "baseline", "pin"), "checkpoint"),
        (("compare", "review", "impact", "adopt"), "compare"),
        (("release", "deploy", "download", "publish"), "download"),
        (("training", "trainee", "exercise"), "simulator"),
        (("library", "class", "pvm", "faceplate"), "templates"),
        (("save", "check in"), "save"), (("refresh",), "restore"),
        (("copy",), "copy"), (("rename", "edit", "changes"), "exec_edit"),
        (("search", "find", "catalog", "tag"), "search"),
        (("import", "upload"), "upload"), (("export",), "download"),
        (("new", "create", "add"), "new"), (("open", "resume", "reserve"), "open"),
        (("connect",), "connect"), (("history", "audit"), "history"),
        (("clear", "remove", "cancel"), "delete"),
    ):
        if any(word in text for word in words):
            return mark
    return "properties"


def style_button(button, mark=None, *, primary=False, quiet=False, command_id=""):
    button.setAutoDefault(False)
    button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    button.setIconSize(QSize(18, 18))
    mark = mark or button.property("configurationIconName") or mark_for(button.text())
    button.setProperty("configurationIconName", mark)
    if command_id:
        button.setProperty("commandId", command_id)
    button.setIcon(studio_icon(mark, 18, UI.on_blue) if primary else icon(mark))
    button.setProperty("configurationPrimary", primary)
    button.setProperty("configurationQuiet", quiet or bool(button.property("configurationQuiet")))
    if not button.accessibleName() or button.accessibleName() == button.property("configurationAutoName"):
        name = button.text().replace("…", "").replace("&", "")
        button.setAccessibleName(name)
        button.setProperty("configurationAutoName", name)
    button.style().unpolish(button)
    button.style().polish(button)
    return button


def command_bar(parent, commands, *, more=()):
    """Keep common commands compact; recovery commands remain in a labeled menu."""
    bar = QFrame(parent, objectName="configurationCommands")
    layout = QHBoxLayout(bar)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    bar.buttons = {}
    for text, callback, mark, primary in commands:
        button = ConfigurationButton(text, bar)
        button.clicked.connect(callback)
        style_button(button, mark, primary=primary, quiet=not primary)
        layout.addWidget(button)
        bar.buttons[text] = button
    layout.addStretch()
    if more:
        button = ConfigurationToolButton(bar)
        button.setText("More")
        button.setIcon(QIcon(f"{_ASSETS}/more.svg"))
        button.setProperty("configurationQuiet", True)
        button.setProperty("configurationMore", True)
        button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        button.setPopupMode(QToolButton.InstantPopup)
        menu = studio_menu(parent=button)
        for text, callback, mark in more:
            action = menu.addAction(icon(mark), text)
            action.triggered.connect(callback)
        button.setMenu(menu)
        layout.addWidget(button)
        bar.more = button
    return bar


def heading(parent, title, subtitle="", mark="properties", *, status=None):
    frame = QFrame(parent, objectName="configurationHeader")
    frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
    layout = QHBoxLayout(frame)
    layout.setContentsMargins(0, 0, 0, 0)
    if mark:
        image = QLabel()
        image.setPixmap(studio_icon(mark, 24).pixmap(24, 24))
        layout.addWidget(image)
    text = QVBoxLayout()
    text.setSpacing(3)
    label = QLabel(title, objectName="configurationTitle")
    label.setTextFormat(Qt.PlainText)
    text.addWidget(label)
    if status is not None:
        status.setObjectName("configurationSubtitle")
        status.setWordWrap(True)
        text.addWidget(status)
    if subtitle:
        note = QLabel(subtitle, objectName="configurationSubtitle")
        note.setWordWrap(True)
        note.setTextFormat(Qt.PlainText)
        text.addWidget(note)
    layout.addLayout(text, 1)
    return frame


def detail_document(title, summary, body, metadata=""):
    """Give an inspected record a readable hierarchy without treating it as HTML."""
    return (
        f'<p style="font-size:12pt; font-weight:600; margin-top:0">{escape(title)}</p>'
        f'<p style="color:{UI.text_secondary}">{escape(summary)}</p>'
        f'<p>{escape(body).replace(chr(10), "<br>")}</p>'
        f'<p style="font-size:8.5pt; color:{UI.text_secondary}">{escape(metadata).replace(chr(10), "<br>")}</p>'
    )


def polish_page(page):
    page.setStyleSheet(CONFIGURATION_QSS)
    page.setWindowIcon(icon(mark_for(page.windowTitle())))
    if page.layout():
        page.layout().setContentsMargins(18, 14, 18, 14)
        page.layout().setSpacing(12)
    for table in page.findChildren(QTableView):
        table.setShowGrid(False)
        table.setAlternatingRowColors(True)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(30)
        table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    for button in page.findChildren(QPushButton):
        # A manager can own a separate review dialog; its primary action is local.
        owner = button.parentWidget()
        while owner is not None and owner is not page and not isinstance(owner, QDialog):
            owner = owner.parentWidget()
        if owner is page:
            primary = bool(button.property("configurationPrimary")) or button is getattr(page, "commit_button", None)
            style_button(button, primary=primary)
    from .configuration_commands import install_table_menus
    install_table_menus(page)


class LoadingLine(QWidget):
    """A quiet blue activity line; native busy controls vary with the Windows theme."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("configurationProgress")
        self.setAccessibleName("Configuration request in progress")
        self.setFixedHeight(3)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._phase = 0.0
        self._animation = QVariantAnimation(self)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setDuration(1400)
        self._animation.setLoopCount(-1)
        self._animation.setEasingCurve(QEasingCurve.InOutSine)
        self._animation.valueChanged.connect(self._advance)
        self.hide()

    def _advance(self, value):
        self._phase = value
        self.update()

    def showEvent(self, event):  # noqa: N802
        self._animation.start()
        super().showEvent(event)

    def hideEvent(self, event):  # noqa: N802
        self._animation.stop()
        super().hideEvent(event)

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(UI.selection))
        length = max(24.0, self.width() * .28)
        x = (self.width() + length) * self._phase - length
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(UI.blue))
        painter.drawRoundedRect(QRectF(x, 0, length, self.height()), 1.5, 1.5)


def progress(parent):
    return LoadingLine(parent)


def state_color(value):
    value = str(value).casefold()
    if any(word in value for word in ("failed", "conflict", "error", "denied")):
        return QColor(UI.error)
    if any(word in value for word in ("unknown", "offline", "different", "update available")):
        return QColor(UI.warning)
    if value in {"current", "verified", "in sync", "delivered", "ready"}:
        return QColor(UI.success)
    return QColor(UI.text)
