"""Native operator commands and a contextual workspace beside the process."""
from __future__ import annotations

from functools import lru_cache
from PySide6.QtCore import QEvent, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea, QSizePolicy,
    QTabWidget, QToolButton, QVBoxLayout, QWidget,
)
from shiboken6 import isValid

from .shell.chrome_azeo import MENU_BUTTONS, RIGHT_BUTTONS, draw_operator_icon
from .shell.chrome import OPERATOR_COMFORTABLE_H, OPERATOR_COMPACT_H, stamp
from azeo_control_trainer.core.hmi.theme.palette import RolePalette
from azeo_control_trainer.core.hmi.theme.roles import Role
from azeo_control_trainer.core.hmi.theme.tokens import THEMES
from azeo_control_trainer.core.hmi.theme.widgets import bind_operator_theme


def chrome_palette(theme):
    return RolePalette.for_theme(theme)


@lru_cache(maxsize=128)
def command_icon(key, theme="silver"):
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    try:
        draw_operator_icon(painter, QRect(0, 0, 24, 24), key, RolePalette.for_theme(theme))
    finally:
        painter.end()
    return QIcon(pixmap)


class OperatorCommandBar(QWidget):
    """Labeled native buttons keep keyboard focus and accessible actions real."""

    activated = Signal(str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.server_time = 0.0
        self.bubbles = {"refresh": False, "errors": False}
        self.selected = set()
        self.enabled = {}
        self.buttons = MENU_BUTTONS + (("training", "", "Training workspace"),)
        self.right_buttons = RIGHT_BUTTONS
        self._controls = {}
        self._training_available = False
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 3, 8, 3)
        row.setSpacing(4)
        for key, title, icon in (
                ("search", "Search", "search"), ("alarm_list", "Alarms", "alarm_list"),
                ("history", "Trends", "history"), ("training", "Training", "tools"),
                ("tools", "Tools", "tools"), ("refresh", "Refresh", "refresh")):
            button = QToolButton()
            button.setText(title)
            button.setIcon(command_icon(icon))
            button.setIconSize(QSize(22, 22))
            button.setAccessibleName(title)
            button.setFocusPolicy(Qt.StrongFocus)
            button.setToolTip(next((tip for name, _, tip in self.buttons if name == key), title))
            button.clicked.connect(lambda _=False, name=key: self.activated.emit(name))
            row.addWidget(button)
            self._controls[key] = button
        self.identity = QLabel()
        self.identity.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.identity.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.identity, 1)
        self.more = QToolButton()
        self.more.setText("Station")
        self.more.setIcon(command_icon("utilities"))
        self.more.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.more.setPopupMode(QToolButton.InstantPopup)
        self.more_menu = QMenu(self.more)
        self.more.setMenu(self.more_menu)
        self._actions = {}
        for key, title in (("errors", "Display errors"), ("alarm_filter", "Alarm filter…"),
                           ("tag_settings", "Display tags…"), ("utilities", "Utilities…"),
                           ("mode", "Full screen / window (F11)"), ("logon", "User and write authority…"),
                           ("exit", "Close Operator Live")):
            action = self.more_menu.addAction(command_icon(key), title)
            action.triggered.connect(lambda _=False, name=key: self.activated.emit(name))
            self._actions[key] = action
        row.addWidget(self.more)
        self._hmi_theme_extra = "QToolButton { padding: 3px 7px; }"
        bind_operator_theme(self)
        self.sync()

    def apply_operator_theme(self, theme):
        for key, button in self._controls.items():
            button.setIcon(command_icon("tools" if key == "training" else key, theme))
        self.more.setIcon(command_icon("utilities", theme))
        for key, action in self._actions.items():
            action.setIcon(command_icon(key, theme))

    def drop_buttons(self, keys):
        for key in keys:
            if key in self._controls:
                self._controls[key].hide()
            if key in self._actions:
                self._actions[key].setVisible(False)

    def set_training_available(self, available):
        self._training_available = bool(available)
        self._controls["training"].setVisible(self._training_available)
        if not available:
            self.buttons = MENU_BUTTONS

    def button_rects(self):
        return {key: QRect(button.pos(), button.size()) for key, button in self._controls.items()}

    def sync(self):
        comfortable = self.settings.comfortable
        self.setFixedHeight(OPERATOR_COMFORTABLE_H if comfortable else OPERATOR_COMPACT_H)
        for button in self._controls.values():
            button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon if comfortable and self.width() >= 760
                                      else Qt.ToolButtonIconOnly)
            button.setMinimumHeight(32 if comfortable else 26)
        count = int(self.bubbles.get("refresh", 0))
        self._controls["refresh"].setText(f"Refresh ({count})" if count else "Refresh")
        errors = int(self.bubbles.get("errors", 0))
        self._actions["errors"].setText(f"Display errors ({errors})" if errors else "Display errors")
        self.more.setText(f"Station · {errors} errors" if errors else "Station")
        authority = "Operate" if self.settings.write_authority else "VIEW ONLY"
        text = f"{stamp(self.server_time)}   {self.settings.user} · {authority}"
        self.identity.setText(self.identity.fontMetrics().elidedText(text, Qt.ElideLeft, self.identity.width()))
        self.identity.setToolTip(f"{self.settings.console_id} · {text}")
        self._controls["training"].setVisible(self._training_available)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self.sync()


class FloatingTool(QWidget):
    """Keep fixed-size tools reachable even on a small logical desktop."""

    def __init__(self, tool, title, parent):
        super().__init__(parent, Qt.Window)
        self.tool = tool
        self._closing = False
        self.setWindowTitle(f"{title} — Operator Live")
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(tool)
        root.addWidget(scroll)
        tool.installEventFilter(self)
        available = parent.screen().availableGeometry()
        self.resize(min(max(640, tool.sizeHint().width()), available.width() - 40),
                    min(max(500, tool.sizeHint().height()), available.height() - 80))
        self.move(available.x() + (available.width() - self.width()) // 2,
                  available.y() + (available.height() - self.height()) // 2)
        bind_operator_theme(self)

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self.tool and event.type() == QEvent.Close and not self._closing:
            finish = getattr(watched, "shutdown", None)
            if callable(finish) and finish() is False:
                event.ignore()
                return True
            self._closing = True
            self.close()
        return super().eventFilter(watched, event)

    def closeEvent(self, event):  # noqa: N802
        if not self._closing:
            finish = getattr(self.tool, "shutdown", None)
            if callable(finish) and finish() is False:
                event.ignore()
                return
            self._closing = True
            self.tool.close()
        super().closeEvent(event)


class ContextWorkspace(QWidget):
    """Embed the existing tools without rebuilding their operating models."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 0, 0, 0)
        root.setSpacing(2)
        heading = QHBoxLayout()
        title = self.title = QLabel("Operating workspace")
        heading.addWidget(title, 1)
        popout = QPushButton("Pop out")
        popout.clicked.connect(self.detach_current)
        heading.addWidget(popout)
        hide = QPushButton("Hide")
        hide.clicked.connect(self.hide)
        heading.addWidget(hide)
        root.addLayout(heading)
        self.tabs = QTabWidget()
        self.tabs.setMovable(True)
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._sync_timers)
        root.addWidget(self.tabs, 1)
        self.entries = {}
        self._paused = {}
        self._floating = []
        self.hide()
        bind_operator_theme(self)

    def apply_operator_theme(self, theme):
        self.title.setStyleSheet(
            f"color: {THEMES[theme][Role.HEADING]}; font-weight: 600; padding: 5px;")

    def get(self, key):
        entry = self.entries.get(key)
        return entry[0] if entry else None

    def present(self, key, widget, title):
        if key not in self.entries:
            widget.setAttribute(Qt.WA_DeleteOnClose, False)
            widget.setWindowFlags(Qt.Widget)
            if key in {"trends", "alarms"}:
                widget.setMinimumSize(0, 500)
                widget.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
            scroll = QScrollArea()
            scroll.setFrameShape(QScrollArea.NoFrame)
            scroll.setWidgetResizable(True)
            scroll.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            scroll.setWidget(widget)
            self.entries[key] = (widget, scroll)
            widget.installEventFilter(self)
            self.tabs.addTab(scroll, title)
        widget, page = self.entries[key]
        self.tabs.setCurrentWidget(page)
        self.show()
        widget.show()
        self._sync_timers()
        return widget

    def _sync_timers(self, *_):
        current = self.tabs.currentWidget()
        for widget, page in self.entries.values():
            active = self.isVisible() and page is current
            if active:
                for timer, interval in self._paused.pop(widget, ()):
                    # A hidden equipment panel can lose a replaced faceplate
                    # before the panel is shown again. Qt owns that timer.
                    if isValid(timer):
                        timer.start(interval)
            elif widget not in self._paused:
                timers = [(timer, timer.interval()) for timer in widget.findChildren(QTimer)
                          if timer.isActive() and not timer.isSingleShot()
                          and not timer.property("keepRunningWhenHidden")]
                for timer, _ in timers:
                    timer.stop()
                self._paused[widget] = timers

    def hideEvent(self, event):  # noqa: N802
        self._sync_timers()
        super().hideEvent(event)

    def showEvent(self, event):  # noqa: N802
        self._sync_timers()
        super().showEvent(event)

    def eventFilter(self, watched, event):  # noqa: N802
        if event.type() == QEvent.Close:
            finish = getattr(watched, "shutdown", None)
            if callable(finish) and finish() is False:
                event.ignore()
                return True
            key = next((key for key, entry in self.entries.items() if entry[0] is watched), None)
            if key is not None:
                self._remove(key)
                watched.deleteLater()
        return super().eventFilter(watched, event)

    def _remove(self, key):
        widget, page = self.entries.pop(key)
        widget.removeEventFilter(self)
        self._paused.pop(widget, None)
        self.tabs.removeTab(self.tabs.indexOf(page))
        page.takeWidget()
        page.deleteLater()
        if not self.entries:
            self.hide()
        return widget

    def close_tab(self, index):
        page = self.tabs.widget(index)
        key = next((key for key, entry in self.entries.items() if entry[1] is page), None)
        if key is not None:
            widget = self.entries[key][0]
            if widget.close():
                if key in self.entries:
                    self._remove(key)
                widget.deleteLater()

    def detach_current(self):
        page = self.tabs.currentWidget()
        key = next((key for key, entry in self.entries.items() if entry[1] is page), None)
        if key is None:
            return
        widget = self.entries[key][0]
        title = self.tabs.tabText(self.tabs.indexOf(page))
        timers = self._paused.pop(widget, ())
        self._remove(key)
        window = FloatingTool(widget, title, self.window())
        self._floating.append(window)
        window.destroyed.connect(lambda *_: self._floating.remove(window)
                                 if window in self._floating else None)
        window.show()
        widget.show()
        for timer, interval in timers:
            if isValid(timer):
                timer.start(interval)
        window.raise_()
