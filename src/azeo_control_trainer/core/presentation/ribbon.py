"""Shared Control Designer ribbon tiles, groups, pages and silver styling."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QToolButton, QVBoxLayout, QWidget
from .brand import UI
from .menu_style import MENU_QSS
from .studio_icons import studio_icon

_C = {
    # Ribbon chrome
    "ribbon_bg":        UI.pane,
    "ribbon_bg_top":    UI.chrome,
    "ribbon_bg_bot":    UI.chrome,
    "ribbon_border":    UI.border,
    "tab_bg":           "transparent",
    "tab_bg_hover":     UI.hover,
    "tab_bg_active":    UI.pane,
    "tab_border_active":UI.blue,
    "tab_text":         UI.text_secondary,
    "tab_text_active":  UI.blue,
    # QAT
    "qat_bg":           UI.chrome,
    "qat_border":       UI.border_light,
    # Groups
    "group_sep":        UI.border_light,
    "group_label":      UI.text_secondary,
    # Buttons
    "btn_bg":           "transparent",
    "btn_bg_hover":     UI.hover,
    "btn_bg_pressed":   UI.selection,
    "btn_bg_checked":   UI.selection,
    "btn_border":       "transparent",
    "btn_border_hover": UI.blue,
    "btn_text":         UI.text,
    "btn_text_secondary":UI.text_secondary,
    "btn_text_disabled":UI.disabled,
    # Accent
    "accent":           UI.blue,
    "accent_hover":     UI.blue,
    "accent_pressed":   UI.blue,
    "accent_text":      "#FFFFFF",
    # Status
    "online_bg":        "#2D8E3C",
    "online_border":    "#1B6E2C",
    "offline_bg":       UI.chrome_alt,
    "offline_border":   UI.border,
    "offline_text":     UI.text_secondary,
}


# ══════════════════════════════════════════════════════════════════════
# Icon Factory — shared QPainter vector icons (crisp at all DPI / sizes)
# ══════════════════════════════════════════════════════════════════════

#: Shape identifies each command; all engineering icons share the product blue.
def _icon(name: str, size: int = 20) -> QIcon:
    return studio_icon(name, size)


# ══════════════════════════════════════════════════════════════════════
# Stylesheet fragments
# ══════════════════════════════════════════════════════════════════════

_QSS_QAT = f"""
    QWidget#QATBar {{
        background: {_C['qat_bg']};
        border-bottom: 1px solid {_C['qat_border']};
        min-height: 28px; max-height: 28px;
    }}
    QWidget#QATBar QToolButton {{
        background: transparent;
        border: 1px solid transparent;
        border-radius: 2px;
        padding: 2px 4px;
        margin: 0px 1px;
        color: {_C['btn_text']};
        font-size: 9pt;
    }}
    QWidget#QATBar QToolButton:hover {{
        background: {_C['btn_bg_hover']};
        border-color: {_C['btn_border_hover']};
    }}
    QWidget#QATBar QToolButton:pressed {{
        background: {_C['btn_bg_pressed']};
    }}
"""

_QSS_TAB_BAR = f"""
    QTabBar#RibbonTabBar {{
        background: transparent;
        border: none;
        qproperty-drawBase: 0;
    }}
    QTabBar#RibbonTabBar::tab {{
        background: {_C['tab_bg']};
        color: {_C['tab_text']};
        border: none;
        border-bottom: 2px solid transparent;
        padding: 5px 16px 4px 16px;
        font-size: 9pt;
        font-weight: 500;
        min-width: 56px;
    }}
    QTabBar#RibbonTabBar::tab:hover {{
        background: {_C['tab_bg_hover']};
        color: {_C['tab_text_active']};
    }}
    QTabBar#RibbonTabBar::tab:selected {{
        background: {_C['tab_bg_active']};
        color: {_C['tab_text_active']};
        border-bottom: 2px solid {_C['tab_border_active']};
        font-weight: bold;
    }}
"""

_QSS_RIBBON_PAGE = f"""
    QWidget#RibbonPage {{
        background: {_C['ribbon_bg']};
        border-top: none;
        border-bottom: 1px solid {_C['ribbon_border']};
    }}
"""

_QSS_LARGE_BTN = f"""
    QToolButton {{
        background: {_C['btn_bg']};
        border: 1px solid {_C['btn_border']};
        border-radius: 3px;
        padding: 2px 6px;
        color: {_C['btn_text']};
        font-size: 9pt;
        font-family: 'Segoe UI';
    }}
    QToolButton:hover {{
        background: {_C['btn_bg_hover']};
        border-color: {_C['btn_border_hover']};
    }}
    QToolButton:pressed {{
        background: {_C['btn_bg_pressed']};
        border-color: {_C['accent']};
    }}
    QToolButton:checked {{
        background: {_C['btn_bg_checked']};
        border-color: {_C['accent']};
    }}
    QToolButton:disabled {{
        color: {_C['btn_text_disabled']};
    }}
"""

_QSS_SMALL_BTN = f"""
    QToolButton {{
        background: {_C['btn_bg']};
        border: 1px solid {_C['btn_border']};
        border-radius: 3px;
        padding: 2px 8px;
        color: {_C['btn_text']};
        font-size: 9pt;
        font-family: 'Segoe UI';
    }}
    QToolButton:hover {{
        background: {_C['btn_bg_hover']};
        border-color: {_C['btn_border_hover']};
    }}
    QToolButton:pressed {{
        background: {_C['btn_bg_pressed']};
        border-color: {_C['accent']};
    }}
    QToolButton:checked {{
        background: {_C['btn_bg_checked']};
        border-color: {_C['accent']};
    }}
    QToolButton:disabled {{
        color: {_C['btn_text_disabled']};
    }}
"""

_QSS_ACCENT_BTN = f"""
QToolButton {{ background: {UI.selection}; color: {UI.blue};
    border: 1px solid {UI.blue}; border-radius: 4px; padding: 2px 6px;
    font-size: 9pt; font-weight: 600; }}
QToolButton:hover {{ background: {UI.hover}; }}
QToolButton:pressed {{ background: {UI.selection}; }}
QToolButton:disabled {{ background: {UI.chrome}; color: {UI.disabled}; border-color: {UI.border}; }}
"""

_QSS_DEACTIVATE_BTN = _QSS_LARGE_BTN

# Shown on every affordance that would need a physical controller. Those
# buttons are kept (so the pages match Azeo's layout) but disabled, rather
# than left as silent no-ops.
_NO_CONTROLLER = ("Not available — the simulator runs modules in-process; "
                  "there is no physical controller to connect to.")

_QSS_MENU = (
    MENU_QSS
)


# ══════════════════════════════════════════════════════════════════════
# Ribbon Components
# ══════════════════════════════════════════════════════════════════════

class RibbonButton(QToolButton):
    """Large ribbon button — icon above, text below."""

    def __init__(self, text: str, icon_name: str = "",
                 tip: str = "", parent=None):
        super().__init__(parent)
        self.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.setText(text)
        self.setToolTip(tip or text)
        if icon_name:
            self.setIcon(_icon(icon_name, 22))
        self.setIconSize(QSize(22, 22))
        self.setMinimumWidth(72)
        self.setFixedHeight(58)
        self.setStyleSheet(_QSS_LARGE_BTN)
        self.setAutoRaise(True)


class SmallRibbonButton(QToolButton):
    """Small ribbon button — icon left, text right (single row)."""

    def __init__(self, text: str, icon_name: str = "",
                 tip: str = "", parent=None):
        super().__init__(parent)
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setText(text)
        self.setToolTip(tip or text)
        if icon_name:
            self.setIcon(_icon(icon_name, 16))
        self.setIconSize(QSize(16, 16))
        self.setFixedHeight(24)
        self.setStyleSheet(_QSS_SMALL_BTN)
        self.setAutoRaise(True)


class RibbonSeparator(QFrame):
    """Vertical separator line between ribbon groups."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(1)
        self.setFrameShape(QFrame.VLine)
        self.setStyleSheet(f"color: {_C['group_sep']};")
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)


class RibbonGroup(QFrame):
    """A labeled group of buttons within a ribbon page."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self._title = title
        self._buttons: list[QWidget] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 2, 4, 0)
        root.setSpacing(1)

        self._content = QWidget()
        self._content_layout = QHBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(2)
        root.addWidget(self._content, 1)

        lbl = QLabel(title)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(
            f"color: {_C['group_label']}; font-size: 9pt; border: none; "
            "background: transparent; padding: 0; margin: 0;")
        lbl.setFixedHeight(18)
        root.addWidget(lbl)

    @property
    def title(self) -> str:
        """Group caption as painted under the buttons."""
        return self._title

    def buttons(self) -> list[QToolButton]:
        """Every ribbon button in this group, in insertion order."""
        return [w for w in self._buttons if isinstance(w, QToolButton)]

    def addWidget(self, w: QWidget):
        self._buttons.append(w)
        self._content_layout.addWidget(w)

    def addLayout(self, layout):
        self._content_layout.addLayout(layout)

    def addSmallButtonColumn(self, *buttons):
        """Stack 2-3 small buttons vertically in a column."""
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(1)
        for btn in buttons:
            self._buttons.append(btn)
            col.addWidget(btn)
        if len(buttons) < 3:
            col.addStretch(1)
        self._content_layout.addLayout(col)


class RibbonPage(QWidget):
    """One tab page of the ribbon — contains multiple RibbonGroups."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("RibbonPage")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(_QSS_RIBBON_PAGE)
        self._groups: list[RibbonGroup] = []

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(4, 2, 4, 0)
        self._layout.setSpacing(2)

    def addGroup(self, group: RibbonGroup):
        if self._layout.count() > 0:
            self._layout.addWidget(RibbonSeparator())
        self._layout.addWidget(group)
        self._groups.append(group)

    def groups(self) -> list[RibbonGroup]:
        """Groups on this page, left to right."""
        return list(self._groups)

    def addStretch(self):
        self._layout.addStretch(1)

    def addWidget(self, w: QWidget):
        self._layout.addWidget(w)


# ══════════════════════════════════════════════════════════════════════
# Main Ribbon Bar
# ══════════════════════════════════════════════════════════════════════


RIBBON_TAB_STYLE = _QSS_TAB_BAR
QUICK_ACCESS_STYLE = _QSS_QAT
