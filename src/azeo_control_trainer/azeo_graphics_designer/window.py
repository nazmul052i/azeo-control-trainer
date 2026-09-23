"""Graphics Designer workspace: commands, task browser, canvas and properties.

Home is a compact command bar; the other ribbon pages retain detailed tools.
Components, Displays, Library, Control Data, Objects and Layers share one
full-height sidebar, with an optional split view. All command surfaces use
the existing action dispatcher and the one PvmDisplay authoring model.
"""
from __future__ import annotations

from azeo_control_trainer.core.hmi.compatibility import PVM_SCOPE_PREFIXES

from azeo_control_trainer.core.presentation.menu_style import retain_menu

import html
import json
import logging
from pathlib import Path

from azeo_control_trainer.core.hmi.pvms.faceplate_sections import (
    LIVE_SECTIONS as LIVE_FACEPLATE_SECTIONS,
    SECTIONS as FACEPLATE_SECTIONS,
)
from azeo_control_trainer.core.presentation.authoring_controls import AuthoringComboBox
from PySide6.QtCore import QMimeData, QPoint, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QDrag, QFont, QKeySequence, QShortcut,
)
from PySide6.QtWidgets import (
    QApplication, QFrame, QGridLayout, QHBoxLayout,
    QDialog, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from .library_explorer import LibraryExplorer
from azeo_control_trainer.core.hmi.pvms.publishing import DisplayLocked, DisplayStore, PublishRefused
from azeo_control_trainer.core.hmi.theme.fonts import apply_application_font
from azeo_control_trainer.core.presentation.authoring_style import AUTHORING_CONTROLS_QSS
from azeo_control_trainer.core.presentation.authoring_dialog import apply_authoring_dialog


from azeo_control_trainer.core.hmi.pvms.roles import Tier
from .studio import (
    MODE_EDIT, MODE_TEST, WF, PvmStudio,
)

log = logging.getLogger("azeo.graphics_designer")

ZOOM_STEPS = ("50", "75", "100", "125", "150", "200")

#: Icon-tile width, chosen so two columns fit the palette pane
#: (~210 px less its scrollbar, margins and spacing). Every palette
#: card is capped to it: the grid sizes its columns from the WIDEST
#: card in the whole scroll area, so one long PVM name ("Vessel with
#: Bar + Trend") pushed the second column out of view for every
#: section, equipment included.
ICON_TILE_W = 112
TILE_MAX_W = 132

_CHROME_QSS = f"""
QWidget {{ font-family: "Segoe UI"; font-size: 9.5pt; }}
QToolTip {{ padding: 7px; color: {WF['tx']}; background: #FFFFFF;
             border: 1px solid {WF['bd']}; }}
QWidget#sidebar_shell {{ background: {WF['pane']}; }}
QComboBox#sidebar_navigation {{ margin: 8px; padding: 7px 10px;
    background: {WF['chrome']}; border: 1px solid {WF['bd_lt']};
    border-radius: 5px; color: {WF['tx']}; font-weight: 600; }}
QWidget#pvm_card {{ background: {WF['pane']}; border: 1px solid {WF['bd_lt']};
    border-radius: 6px; }}
QWidget#pvm_card:hover, QWidget#pvm_card:focus {{ background: {WF['hover']};
    border: 1px solid {WF['lapis']}; }}
QLabel#component_name {{ font-size: 9pt; color: {WF['tx']}; font-weight: 600;
    background: transparent; border: none; }}
QLabel#component_kind {{ font-size: 8pt; color: {WF['tx2']};
    background: transparent; border: none; }}
QMainWindow, QWidget#chrome_root {{
    background: {WF['page']}; color: {WF['tx']};
}}
QLabel#tier_chip, QLabel#dirty_chip {{
    min-height: 18px; font-size: 9pt; padding: 3px 9px;
    font-family: "Segoe UI"; letter-spacing: 0px;
    border: 1px solid {WF['bd']}; border-radius: 3px;
    color: {WF['tx2']}; background: {WF['pane']};
}}
QLabel#dirty_chip[dirty="true"] {{
    color: #995307; border-color: #E1A35B; background: #FFF5E5;
}}
QWidget#ribbon_host {{
    background: {WF['navy']}; border-bottom: 1px solid {WF['bd']};
}}
QTabBar#ribbon_tabs {{ background: transparent; }}
QTabBar#ribbon_tabs::tab {{
    min-height: 30px; background: transparent; border: none;
    border-bottom: 2px solid transparent; padding: 2px 13px 1px 13px;
    font-size: 9pt; color: #FFFFFF;
}}
QTabBar#ribbon_tabs::tab:hover {{
    color: {WF['navy']}; background: {WF['hover']};
}}
QTabBar#ribbon_tabs::tab:selected {{
    background: {WF['pane']}; color: {WF['navy']}; font-weight: 650;
    border-bottom: 2px solid {WF['lapis']};
}}
QWidget#ribbon_row {{
    background: {WF['pane']}; border-top: 1px solid {WF['bd_lt']};
}}
QWidget#ribbon_row QToolButton, QWidget#ribbon_row QPushButton {{
    background: transparent; border: 1px solid transparent;
    padding: 2px 6px; font-size: 9pt; color: {WF['tx']};
    border-radius: 3px;
}}
QWidget#ribbon_row QToolButton:hover,
QWidget#ribbon_row QPushButton:hover {{
    border-color: {WF['sel_br']}; background: {WF['hover']};
}}
QWidget#ribbon_row QToolButton:checked,
QWidget#ribbon_row QPushButton:checked {{
    background: {WF['sel']}; border-color: {WF['sel_br']};
}}
QLabel.group_caption {{ color: {WF['tx3']}; font-size: 9pt; }}
QFrame.group_sep {{ color: {WF['bd_lt']}; }}
QTabWidget#explorer_tabs > QTabBar::tab {{
    min-height: 30px; padding: 3px 10px; font-size: 9pt;
    background: {WF['chrome_2']}; border: none;
    border-top: 1px solid {WF['bd']}; color: {WF['tx2']};
}}
QTabWidget#explorer_tabs > QTabBar::tab:hover {{
    background: {WF['hover']}; color: {WF['navy']};
}}
QTabWidget#explorer_tabs > QTabBar::tab:selected {{
    background: {WF['pane']}; color: {WF['navy']}; font-weight: 600;
    border-top: 2px solid {WF['lapis']}; padding-top: 2px;
}}
QTabWidget#explorer_tabs::pane {{
    border: none; background: {WF['pane']};
}}
QTabWidget#explorer_tabs QLineEdit {{
    background: {WF['pane']}; color: {WF['tx']};
    border: 1px solid {WF['bd']}; border-radius: 3px;
    padding: 4px 7px; min-height: 20px;
}}
QTabWidget#explorer_tabs QLineEdit:focus {{
    border-color: {WF['lapis']};
}}
QTreeWidget {{
    background: {WF['pane']}; border: none;
    font-family: "Segoe UI"; font-size: 9pt; color: {WF['tx']};
    outline: none;
}}
QTreeWidget::item {{ min-height: 28px; padding: 2px 5px; }}
QTreeWidget::item:hover {{ background: {WF['hover']}; }}
QTreeWidget::item:selected:active {{
    background: {WF['sel']}; border-left: 2px solid {WF['sel_br']};
}}
QTreeWidget::item:selected {{
    background: {WF['sel']}; color: {WF['tx']};
}}
QHeaderView::section {{
    background: {WF['chrome']}; color: {WF['tx2']};
    border: none; border-bottom: 1px solid {WF['bd']};
    padding: 5px 7px; font-size: 9pt; font-weight: 600;
}}
QLabel.section_head {{
    color: {WF['tx2']}; font-size: 9pt; font-weight: 600;
    padding: 6px 2px 2px 2px; letter-spacing: 0px;
}}
QWidget.pvm_card {{
    background: {WF['pane']}; border: 1px solid {WF['bd_lt']};
    border-radius: 4px;
}}
QWidget#pvm_card:hover {{
    background: {WF['hover']}; border: 1px solid {WF['lapis']};
}}
QLabel.card_title {{ font-family: "Segoe UI"; font-size: 9pt;
                     font-weight: 600; color: {WF['tx']}; }}
QLabel.card_sub {{ font-size: 9pt; color: {WF['tx3']};
                   letter-spacing: 0px; }}
QTabWidget#workspace_tabs > QTabBar::tab {{
    min-width: 115px; min-height: 30px; padding: 3px 13px;
    margin: 3px 1px 0 0; font-size: 9pt; background: {WF['chrome']};
    border: 1px solid {WF['bd']}; border-bottom: none;
    border-top-left-radius: 4px; border-top-right-radius: 4px;
    color: {WF['tx2']};
}}
QTabWidget#workspace_tabs > QTabBar::tab:hover {{
    background: {WF['hover']}; color: {WF['navy']};
}}
QTabWidget#workspace_tabs > QTabBar::tab:selected {{
    min-height: 26px; background: {WF['pane']}; color: {WF['navy']};
    font-weight: 650; border-top: 2px solid {WF['lapis']};
}}
QTabWidget#workspace_tabs::pane {{
    border: none; border-top: 1px solid {WF['bd']};
    background: {WF['chrome_2']};
}}
QStatusBar {{
    background: {WF['chrome']}; color: {WF['tx2']};
    border-top: 1px solid {WF['bd']};
    font-family: "Segoe UI"; font-size: 9pt;
}}
QStatusBar QLabel {{ padding: 2px 10px; font-family: "Segoe UI";
                     font-size: 9pt; color: {WF['tx2']};
                     border-right: 1px solid {WF['bd_lt']}; }}
QStatusBar QLabel#seg_ok {{ color: {WF['ok']}; }}
QStatusBar QLabel#seg_forced[hot="true"] {{ color: {WF['warn']}; }}
QSplitter#studio_body_splitter {{
    background: {WF['chrome_2']};
}}
QSplitter#studio_body_splitter::handle {{
    background: {WF['bd']}; width: 1px;
}}
QSplitter#studio_body_splitter::handle:hover {{
    background: {WF['lapis_lt']};
}}
QDockWidget {{
    font-size: 9pt; color: {WF['tx2']}; background: {WF['pane']};
    border: 1px solid {WF['bd']};
}}
QDockWidget::title {{
    min-height: 30px; padding: 4px 9px; background: {WF['chrome_2']};
    color: {WF['navy']}; border-bottom: 1px solid {WF['bd']};
    font-weight: 600;
}}

/* Sidebars use a buttonless scrollbar. Native step arrows looked like
   stray collapse controls at both panel seams. */
QScrollBar:vertical {{
    background: {WF['chrome']}; width: 9px; margin: 0;
    border: none; border-left: 1px solid {WF['bd_lt']};
}}
QScrollBar::handle:vertical {{
    background: {WF['card_bd']}; min-height: 34px;
    margin: 2px; border-radius: 3px;
}}
QScrollBar::handle:vertical:hover {{ background: {WF['tx3']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px; background: transparent; border: none;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}
QScrollBar:horizontal {{
    background: {WF['chrome']}; height: 9px; margin: 0;
    border: none; border-top: 1px solid {WF['bd_lt']};
}}
QScrollBar::handle:horizontal {{
    background: {WF['card_bd']}; min-width: 34px;
    margin: 2px; border-radius: 3px;
}}
QScrollBar::handle:horizontal:hover {{ background: {WF['tx3']}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px; background: transparent; border: none;
}}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: transparent;
}}
QSplitter#engineering_workspace::handle {{
    background: {WF['chrome_2']}; height: 6px;
    border-top: 1px solid {WF['bd']};
    border-bottom: 1px solid {WF['pane']};
}}
QSplitter#engineering_workspace::handle:hover {{
    background: {WF['sel']}; border-top-color: {WF['lapis']};
}}
QScrollArea#palette_scroll {{
    background: {WF['pane']}; border: none;
}}
QWidget#palette_column {{ background: {WF['pane']}; }}
QWidget#palette_heading {{
    background: {WF['chrome']}; border-bottom: 1px solid {WF['bd']};
}}
QLabel#palette_heading_title {{
    color: {WF['navy']}; font-size: 9pt; font-weight: 700;
    letter-spacing: 0px;
}}
QLabel#palette_heading_hint {{ color: {WF['tx3']}; font-size: 9pt; }}
QLineEdit#palette_search {{
    background: {WF['pane']}; color: {WF['tx']};
    border: 1px solid {WF['bd']}; border-radius: 4px;
    padding: 4px 7px; min-height: 21px;
}}
QLineEdit#palette_search:focus {{ border-color: {WF['lapis']}; }}
QToolButton#palette_float {{
    background: transparent; border: 1px solid transparent;
    border-radius: 3px; color: {WF['lapis']}; padding: 2px 5px;
    font-weight: 700;
}}
QToolButton#palette_float:hover {{
    background: {WF['sel']}; border-color: {WF['lapis_lt']};
}}
QPushButton#palette_section {{
    background: {WF['pane']}; border: none;
    border-bottom: 1px solid {WF['bd_lt']}; color: {WF['tx2']};
    font-size: 9pt; font-weight: 600; text-align: left;
    padding: 6px 8px;
}}
QPushButton#palette_section:hover {{
    background: {WF['chrome']}; color: {WF['navy']};
}}
QPushButton#palette_section:checked {{
    background: {WF['sel']}; color: {WF['navy']};
    border-left: 3px solid {WF['lapis']}; padding-left: 5px;
}}

QWidget#graphics_configuration {{
    background: {WF['pane']}; border-left: 1px solid {WF['bd']};
}}
QLabel#config_pane_title {{
    color: {WF['navy']}; font-size: 9pt; font-weight: 700;
    letter-spacing: 0px; padding: 3px 1px 8px 1px;
    border-bottom: 1px solid {WF['bd']};
}}
QLabel#config_item_title {{
    color: {WF['tx']}; font-size: 9.75pt; font-weight: 600;
    padding: 2px 0 4px 0;
}}
QWidget#graphics_configuration QPushButton {{
    background: {WF['pane']}; color: {WF['tx2']};
    border: 1px solid {WF['bd']}; border-radius: 3px;
    padding: 5px 8px; min-height: 19px;
}}
QWidget#graphics_configuration QPushButton:hover {{
    color: {WF['navy']}; border-color: {WF['lapis']};
    background: {WF['chrome']};
}}
QWidget#graphics_configuration QCheckBox {{
    color: {WF['tx2']}; spacing: 6px; min-height: 20px;
}}
QTabWidget#inspector_tabs > QTabBar::tab {{
    min-width: 0px; min-height: 27px; padding: 4px 12px;
    background: transparent; color: {WF['tx2']}; border: none;
    border-bottom: 2px solid transparent; margin: 0; font-size: 9pt;
}}
QTabWidget#inspector_tabs > QTabBar::tab:selected {{
    color: {WF['lapis']}; border-bottom-color: {WF['lapis']}; font-weight: 600;
}}
QTabWidget#inspector_tabs::pane {{ border: none; }}
QLabel#library_purpose {{
    background: {WF['sel']}; color: {WF['tx2']};
    border: none; border-left: 3px solid {WF['lapis']};
    padding: 6px 8px; font-size: 9pt;
}}
QPushButton#library_quick_action {{
    background: {WF['pane']}; color: {WF['navy']};
    border: 1px solid {WF['bd']}; border-radius: 3px;
    padding: 5px 6px; font-size: 9pt; font-weight: 600;
}}
QPushButton#library_quick_action:hover {{
    background: {WF['chrome']}; border-color: {WF['lapis']};
}}
QLabel#library_preview {{
    background: {WF['chrome']}; color: {WF['tx3']};
    border: 1px solid {WF['bd']}; border-radius: 3px;
    font-size: 9pt;
}}
QPushButton#library_primary_action {{
    background: {WF['lapis']}; color: white;
    border: 1px solid {WF['navy']}; border-radius: 3px;
    padding: 5px 7px; font-weight: 600;
}}
QPushButton#library_primary_action:hover {{
    background: {WF['navy']};
}}
QTabWidget#library_detail_tabs > QTabBar::tab {{
    padding: 5px 10px; border: none; color: {WF['tx2']};
    background: {WF['chrome']};
}}
QTabWidget#library_detail_tabs > QTabBar::tab:selected {{
    color: {WF['navy']}; background: {WF['pane']};
    border-top: 2px solid {WF['lapis']}; font-weight: 600;
}}
QTabWidget#library_detail_tabs::pane {{
    border: none; border-top: 1px solid {WF['bd_lt']};
}}
"""

from PySide6.QtWidgets import QMainWindow  # noqa: E402

#: Ribbon glyph -> vector icon (`ribbon_bar._draw_icon`).
#:
#: **The ribbon carried emoji and the chrome font has none of them**, so
#: much of the toolbar rendered as empty boxes — the same failure the
#: console chrome hit one window over. This repo already ships a vector
#: icon set whose own comment reads "Emoji is not an icon"; the studio
#: was simply not using it. An unmapped glyph falls back to a drawn dot
#: rather than a box, so a button is always a button.

#: Ribbon button width.
#:
#: At 62 px "Publish" rendered as "Publisl" and "Duplicate" as
#: "Duplica" — a label the button cannot hold is one the engineer has
#: to guess at. The fix is not a wider button: captions WRAP at their
#: spaces (see `_ribbon_tab_changed`), so this only has to hold the
#: longest single word.
#:
#: It is not derived by measurement, and deliberately so: headless, Qt
#: resolves this font to DejaVu Sans and `QFontMetrics` reports widths
#: 50 % over what a Windows console shows — the same trap that made
#: the chrome glyph check useless. Verified by screenshot instead.
# The review harness found that 80 px still elided ``Troubleshoot``;
# 88 px preserves the full caption without changing the scroll contract.
RIBBON_BUTTON_W = 88


def _ribbon_caption(label: str) -> str:
    """Balance a ribbon caption over at most two lines.

    Replacing every space with a newline stranded operators such as ``+``
    and clipped three-word commands into the group caption underneath.  A
    ribbon tile has room for two lines, so choose the least-ragged break and
    keep spaces within each line.
    """
    words = label.split()
    if len(words) < 2:
        return label
    choices = []
    for split in range(1, len(words)):
        left = " ".join(words[:split])
        right = " ".join(words[split:])
        choices.append((max(len(left), len(right)),
                        abs(len(left) - len(right)), left, right))
    _width, _rag, left, right = min(choices)
    return f"{left}\n{right}"


RIBBON_ICONS = {
    "\U0001f5b5": "new", "\U0001f4be": "save", "\U0001f150": "download",
    "\u2713": "compile", "\u2714": "compile", "\U0001f5bc": "print",
    "\u2398": "paste", "\u2702": "cut", "\u29c9": "copy",
    "\U0001f58c": "properties", "\u2728": "new",
    "\u25ad": "select_all", "\u25ef": "presets", "\u2571": "connect",
    "\u25e0": "presets", "\u270e": "exec_edit", "\u2605": "presets",
    "\U0001f130": "comment", "\U0001f50d": "search",
    "\u25b2": "select_all", "\u270b": "align",
    "\U0001f477": "checkpoint", "\u21b6": "undo", "\u21b7": "redo",
    "\U0001f5d1": "delete", "\u232b": "delete", "\u229e": "zoom_in",
    "\u229f": "zoom_out", "\u2922": "zoom_fit", "\u25a6": "align",
    "\U0001f56e": "history", "\u2699": "properties",
    "\u25e7": "align", "\U0001f4cb": "paste", "\U0001f517": "connect",
    "\U0001f4c8": "watch", "\U0001f9e9": "templates",
    "\U0001f5a5": "simulator",
    "\U0001f15f": "upload",      # Publish - sending it out
    "\U0001f3a8": "presets",     # Fill / Palette
    "\u25a4": "values",          # Data Link
    "\u25c8": "faceplate",       # PVM
    "\u2317": "connect",         # Connector
    "\u25f7": "watch",           # Trend
    "\u26a0": "diagnostics",     # Unresolved
    "\u2261": "datalog",         # Reg. log
    "\uff0b": "zoom_in",
    "\u2212": "zoom_out",
    "1:1": "auto",
    "\u26f6": "zoom_fit",        # Fit
    "\u25a9": "named_sets",      # Grid
    "\u22b9": "align",           # Snap
    "\U0001f5c2": "open",        # Explorer
    "\u25b6": "simulator",       # Test
    "\u25a2": "select_all",      # Outline
    "\u21e7": "show",            # Bring forward
    "\u21e9": "hide",            # Send backward
    "\u21bb": "redo",            # Rotate
    "\u21e4": "align",
    "?": "diagnostics",           # Context-sensitive help
    "i": "status",                # About / product identity
    "PVM": "templates",           # PVM creation tutorial
    "FP": "faceplate",            # Faceplate creation tutorial
    "CFG": "properties",          # PVM Configuration Designer tutorial
    "PIC": "print",               # Illustrated tutorial
    "F1": "comment",              # Help center
    "KB": "values",               # Keyboard reference
    # Text stand-ins formerly fell through to the generic dot. Keep one
    # vector vocabulary across every tab so colour never highlights an
    # otherwise ambiguous mark.
    "!": "diagnostics", "#": "values", "@": "history",
    "I": "exec_edit", "M": "params", "R": "watch",
    "T": "presets", "TS": "exec_edit", "~": "watch",
    "\u03a3": "datalog", "\u2194": "align", "\u2195": "align",
    "\u2315": "search", "\u2502": "align", "\u25a1": "select_all",
    "\u25a3": "status", "\u25b7": "simulator", "\u25be": "presets",
    "\u25c9": "status", "\u2606": "templates", "\u2611": "compile",
    "\U0001f4c4": "templates",
}


def _ribbon_icon_color(_action_id: str) -> str:
    """Command identity comes from the glyph; every authoring icon is blue."""
    return WF["lapis"]


# Graphics Explorer shares the same vector vocabulary and authoring accent.
GRAPHICS_TREE_ICONS = {
    "project": ("project", WF["lapis"]),
    "displays": ("folder", WF["lapis"]),
    "display_l1": ("display", WF["lapis"]),
    "display_l2": ("display", WF["lapis"]),
    "display_l3": ("display", WF["lapis"]),
    "display_l4": ("display", WF["lapis"]),
    "display_sets": ("named_sets", WF["lapis"]),
    "contextual": ("faceplate", WF["lapis"]),
    "faceplate": ("faceplate", WF["lapis"]),
    "detail": ("properties", WF["lapis"]),
    "layouts": ("templates", WF["lapis"]),
    "screen": ("display", WF["lapis"]),
    "frame": ("select_all", WF["lapis"]),
}


def _graphics_tree_icon(kind: str, level: int = 0):
    """Return a proper vector icon for one Graphics Explorer node."""
    from azeo_control_trainer.core.presentation.studio_icons import draw_icon as _draw_icon

    key = f"display_l{min(4, max(1, level))}" \
        if kind == "display" else kind
    icon_name, colour = GRAPHICS_TREE_ICONS.get(
        key, ("display", WF["lapis"]))
    return _draw_icon(icon_name, 16, colour)


def _card_press(card, title: str, subtitle: str, on_click):
    """Left-click places; right-click offers the same thing by name,
    so a tile that is only a picture can still say what it is."""
    def press(event):
        if event.button() == Qt.RightButton:
            menu = _card_context_menu(card, title, subtitle, on_click)
            retain_menu(card, menu, "_context_menu")
            from azeo_control_trainer.core.presentation.headless import is_headless
            if not is_headless():
                menu.exec_transient(event.globalPosition().toPoint())
        else:
            on_click()
    return press


def _card_context_menu(card, title: str, subtitle: str, on_click):
    """Every palette tile has placement, identity and relevant help."""
    from azeo_control_trainer.core.presentation.menu_style import \
        studio_menu

    menu = studio_menu(f"PALETTE  {title}", subtitle.title())
    place = menu.addAction("Place on display")
    font = place.font()
    font.setBold(True)
    place.setFont(font)
    place.triggered.connect(on_click)
    menu.addSeparator()
    copy_name = menu.addAction("Copy item name")
    copy_name.triggered.connect(
        lambda: QApplication.clipboard().setText(title))
    # A floating palette's native window is its modeless tool dialog. Keep
    # commands addressed to the owning Studio across that reparenting.
    window = getattr(card, "_studio_window", None) or card.window()
    if hasattr(window, "open_help"):
        help_action = menu.addAction("Palette and canvas help")
        help_action.triggered.connect(lambda: window.open_help("workspace"))
        menu.addSeparator()
        hide = menu.addAction("Hide Palette")
        hide.triggered.connect(
            lambda: window.set_left_panel_visibility(palette=False))
    return menu


class _PaletteCard(QWidget):
    """One click-to-place and drag-to-place engineering stencil.

    The old card placed an object on mouse press, before the engineer could
    begin a drag. Separating click from drag at Qt's platform threshold gives
    the palette the same predictable gesture as a professional CAD stencil.
    """

    def __init__(self, title: str, subtitle: str, on_click=None,
                 drag_payload: dict | None = None, parent=None):
        super().__init__(parent)
        self.title = title
        self.subtitle = subtitle
        self.search_text = f"{title} {subtitle}".casefold()
        self._on_click = on_click
        self._drag_payload = dict(drag_payload or {})
        self._press_pos = QPoint()
        self._drag_started = False

    def activate(self):
        window = getattr(self, "_studio_window", None) or self.window()
        try:
            if self._drag_payload and hasattr(window, "_arm_palette_item"):
                return window._arm_palette_item(self._drag_payload)
            if self._on_click is not None:
                return self._on_click()
        except Exception as error:
            log.exception("Could not activate component %s", self.title)
            if hasattr(window, "_show_ui_error"):
                window._show_ui_error(f"Could not place {self.title}: {error}")
        return None

    def keyPressEvent(self, event) -> None:        # noqa: N802
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.activate()
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:       # noqa: N802
        if event.button() == Qt.RightButton:
            if self._on_click is not None:
                menu = _card_context_menu(
                    self, self.title, self.subtitle, self.activate)
                retain_menu(self, menu, "_context_menu")
                from azeo_control_trainer.core.presentation.headless import is_headless
                if not is_headless():
                    menu.exec_transient(event.globalPosition().toPoint())
            event.accept()
            return
        if event.button() == Qt.LeftButton:
            self._press_pos = event.position().toPoint()
            self._drag_started = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:        # noqa: N802
        if not event.buttons() & Qt.LeftButton or not self._drag_payload:
            super().mouseMoveEvent(event)
            return
        distance = (event.position().toPoint() - self._press_pos).manhattanLength()
        if distance < QApplication.startDragDistance():
            return
        self._drag_started = True
        mime = QMimeData()
        from azeo_control_trainer.core.hmi.pvms.rendering.chrome import MIME_PALETTE

        mime.setData(MIME_PALETTE, json.dumps(
            self._drag_payload, separators=(",", ":")).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        preview = self.grab()
        if not preview.isNull():
            drag.setPixmap(preview)
            drag.setHotSpot(self._press_pos)
        drag.exec(Qt.CopyAction)

    def mouseReleaseEvent(self, event) -> None:     # noqa: N802
        if event.button() == Qt.LeftButton:
            if not self._drag_started and self._on_click is not None:
                self.activate()
            event.accept()
            return
        super().mouseReleaseEvent(event)


def _card(title: str, subtitle: str, on_click=None,
          symbol: str | None = None,
          faceplate_icon: str | None = None,
          stream_direction: str | None = None,
          icon_only: bool = False, *, pvm_class=None,
          drag_payload: dict | None = None,
          help_text: str = "", preview_kind: str = "",
          preview_factory=None) -> QWidget:
    """A keyboard-accessible stencil with a real, DPI-aware preview."""
    from .component_icons import (
        ComponentPreview, class_stencil_preview, element_preview, pvm_icon,
        special_preview, symbol_preview,
    )

    card = _PaletteCard(title, subtitle, on_click, drag_payload)
    card.setObjectName("pvm_card")
    card.setAccessibleName(title)
    card.setAccessibleDescription(f"{subtitle}. Click or drag to place on the canvas.")
    card.setFocusPolicy(Qt.StrongFocus)
    card.setCursor(Qt.PointingHandCursor)
    card.setFixedWidth(TILE_MAX_W)
    lay = QVBoxLayout(card)
    lay.setContentsMargins(8, 8, 8, 7)
    lay.setSpacing(4)
    if preview_factory is not None:
        factory = preview_factory
    elif pvm_class is not None:
        def factory():
            return class_stencil_preview(pvm_class, ICON_TILE_W, 64)
    elif symbol is not None:
        def factory():
            return symbol_preview(symbol, ICON_TILE_W, 64)
    elif faceplate_icon is not None:
        def factory():
            return special_preview(faceplate_icon, ICON_TILE_W, 64)
    else:
        kind = stream_direction or preview_kind or title.lower().replace(" ", "_")
        def factory():
            return element_preview(kind, ICON_TILE_W, 52)
    lay.addWidget(ComponentPreview(factory, card))
    name = QLabel(title.replace("_", "_\u200b"))
    name.setObjectName("component_name")
    name.setWordWrap(True)
    name.setAlignment(Qt.AlignCenter)
    name.setFixedHeight(36)
    name.setAttribute(Qt.WA_TransparentForMouseEvents)
    lay.addWidget(name)
    if not icon_only:
        detail_row = QHBoxLayout()
        detail_row.setSpacing(4)
        if pvm_class is not None:
            badge = QLabel(card)
            badge.setPixmap(pvm_icon(pvm_class, 16).pixmap(QSize(16, 16)))
            badge.setAttribute(Qt.WA_TransparentForMouseEvents)
            detail_row.addWidget(badge)
        from .component_icons import FB_ICON_TYPES
        sub = QLabel(subtitle if subtitle in FB_ICON_TYPES else subtitle.title(), card)
        sub.setObjectName("component_kind")
        sub.setAlignment(Qt.AlignCenter)
        sub.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        sub.setWordWrap(True)
        sub.setAttribute(Qt.WA_TransparentForMouseEvents)
        detail_row.addWidget(sub, 1)
        lay.addLayout(detail_row)
    detail = help_text or "Select the placed item to configure its properties."
    card.setToolTip(
        f"<b>{html.escape(title)}</b><br>{html.escape(subtitle.title())}"
        "<br><br>Click, then place on the canvas. Repeat keeps the tool active."
        f"<br>Drag to place directly. Enter also selects this component.<br>{html.escape(detail)}")
    return card


class HmiStudioWindow(QMainWindow):
    closed = Signal()

    def __init__(self, graphs_provider, display_root,
                 tier: Tier = Tier.ASSEMBLER, area_name: str = "",
                 parent=None, *, configuration_root=None,
                 library_name: str = "Project"):
        super().__init__(parent)
        from azeo_control_trainer.core.presentation.app_icon import get_app_icon
        self.setWindowIcon(get_app_icon(application_id="graphics_designer"))
        self._session_closed = False
        # Studio is also opened directly by Control Designer and visual tests,
        # bypassing the main launcher. Keep its Qt-owned labels on the same
        # packaged face as painted display content in those paths too.
        apply_application_font()
        self._graphs = graphs_provider
        self._root = display_root
        self._configuration_root = configuration_root or display_root
        self._library_name = library_name
        self._library_windows = []
        self._loading_tabs = True
        self.tier = tier
        self.area_name = area_name or "Area"
        log.info(
            "Graphics Designer opened: area=%s root=%s tier=%s library=%s",
            self.area_name, Path(display_root).resolve(), tier.value,
            library_name,
        )
        self.setWindowTitle(
            f"Azeo Graphics Designer — {self.area_name} — "
            "Azeo Control Trainer")
        self.resize(1340, 800)
        self.setStyleSheet(_CHROME_QSS + AUTHORING_CONTROLS_QSS)

        central = QWidget()
        central.setObjectName("chrome_root")
        column = QVBoxLayout(central)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        # The project/document identity belongs to the native title bar and
        # the former QAT commands belong to Home. Removing both duplicate
        # strips gives their height back to the engineering canvas.
        column.addWidget(self._build_ribbon())

        # Test-mode navigation bar — the display SET driving what an
        # operator can reach: home, up to the parent, down to children.
        self.nav_bar = QScrollArea()
        self.nav_bar.setWidgetResizable(True)
        self.nav_bar.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.nav_bar.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.nav_bar.setFixedHeight(54)
        nav_content = QWidget()
        self.nav_bar.setWidget(nav_content)
        self.nav_bar.setStyleSheet(
            f"background: {WF['navy']};")
        self._nav_layout = QHBoxLayout(nav_content)
        self._nav_layout.setContentsMargins(10, 3, 10, 3)
        self._nav_layout.setSpacing(6)
        self.nav_bar.hide()
        column.addWidget(self.nav_bar)

        # ---- body: explorer | workspace, under the chrome
        from PySide6.QtWidgets import QSplitter
        body = QSplitter(Qt.Horizontal)
        body.setObjectName("studio_body_splitter")

        # Workspace tabs first — the library's instance counter walks
        # them during its own construction.
        self.tabs = QTabWidget()
        self.tabs.setObjectName("workspace_tabs")
        self.tabs.setTabsClosable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.currentChanged.connect(self._active_tab_changed)
        self.tabs.tabBar().setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabs.tabBar().customContextMenuRequested.connect(
            self._workspace_tab_menu)

        # Commercial authoring tools stack browsers and palettes in one
        # resizable engineering dock. Keeping them as two horizontal
        # columns cost almost 500 px on a 1920 px workstation and left the
        # 16:9 display at 67% even before the inspector was considered.
        # They remain simultaneously available, but now share one width.
        self.left_workspace = QSplitter(Qt.Vertical)
        self.left_workspace.setObjectName("engineering_workspace")
        self.left_workspace.setAccessibleName(
            "Project explorer and component palette")
        self.left_workspace.setHandleWidth(4)

        self.explorer_tabs = QTabWidget()
        self.explorer_tabs.setObjectName("explorer_tabs")
        self.explorer_tabs.setTabPosition(QTabWidget.North)
        self.explorer_tabs.tabBar().hide()
        # The Graphics tree is the dominant daily view. Library and Control
        # Data can scroll their secondary columns when the dock is compact,
        # just as professional engineering property browsers do.
        self.explorer_tabs.setMinimumWidth(292)
        self.explorer_tabs.setMinimumHeight(150)
        self.explorer_tabs.addTab(self._build_graphics_explorer(),
                                  "Graphics Explorer")
        self.library = LibraryExplorer(
            instance_counter=self._count_instances,
            standards_root=self._root,
            configuration_root=self._configuration_root,
            library_opener=self.open_configuration_library)
        self.explorer_tabs.addTab(self.library, "Library Explorer")
        # Azeo keeps configured control objects out of the visual Palette,
        # but still makes them available in the Explorer workspace.  The
        # standalone PvmStudio already had this browser; the hosted product
        # path accidentally omitted it and therefore hid every module/block.
        self.control_data_host = self._build_control_explorer()
        self.explorer_tabs.addTab(self.control_data_host, "Control Data")
        # The Selection pane — `DLCreatingOperatorDisplay.pdf` names it
        # in eight element operations, and it is the only way back to
        # an element that has been hidden or locked: neither can be
        # clicked on the canvas any more.
        self.selection_host = QWidget()
        _sel_lay = QVBoxLayout(self.selection_host)
        _sel_lay.setContentsMargins(6, 6, 6, 4)
        self.selection_pane = None
        self.explorer_tabs.addTab(self.selection_host, "Selection")
        self.layers_host = QWidget()
        _layer_lay = QVBoxLayout(self.layers_host)
        _layer_lay.setContentsMargins(6, 6, 6, 4)
        self.layers_pane = None
        self.explorer_tabs.addTab(self.layers_host, "Layers")
        self.explorer_tabs.currentChanged.connect(self._explorer_tab)
        self.explorer_tabs.tabBar().setContextMenuPolicy(
            Qt.CustomContextMenu)
        self.explorer_tabs.tabBar().customContextMenuRequested.connect(
            self._left_tab_menu)
        self.left_workspace.addWidget(self.explorer_tabs)

        self.palette_box = self._build_palette()
        self.palette_box.setMinimumHeight(120)
        self.left_workspace.addWidget(self.palette_box)
        self.left_workspace.setStretchFactor(0, 3)
        self.left_workspace.setStretchFactor(1, 2)
        self.left_workspace.setSizes([420, 280])
        # A single full-height browser is the default. The existing splitter
        # remains available for engineers who explicitly choose Split view.
        self.sidebar_shell = QWidget()
        self.sidebar_shell.setObjectName("sidebar_shell")
        sidebar_layout = QVBoxLayout(self.sidebar_shell)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)
        self.sidebar_navigation = AuthoringComboBox()
        self.sidebar_navigation.setObjectName("sidebar_navigation")
        self.sidebar_navigation.setAccessibleName("Choose Studio workspace panel")
        from azeo_control_trainer.core.presentation.studio_icons import studio_icon
        for key, title, icon in (
                ("components", "Components", "templates"),
                ("graphics", "Displays", "new"),
                ("library", "Library", "open"),
                ("control", "Control Data", "io_config"),
                ("selection", "Objects", "select_all"),
                ("layers", "Layers", "named_sets"),
                ("split", "Split view", "align")):
            self.sidebar_navigation.addItem(studio_icon(icon), title, key)
        self.sidebar_navigation.currentIndexChanged.connect(
            lambda _index: self.show_sidebar(self.sidebar_navigation.currentData()))
        sidebar_layout.addWidget(self.sidebar_navigation)
        sidebar_layout.addWidget(self.left_workspace, 1)
        body.addWidget(self.sidebar_shell)

        # The collapse strip: one click folds BOTH left panels away
        # so the canvas takes the whole width; one click brings them
        # back at their old sizes. The splitter handles still resize.
        from PySide6.QtWidgets import QToolButton
        self.left_collapse = QToolButton()
        self._collapse_width = 24
        self.left_collapse.setText("\u25c0")
        self.left_collapse.setToolTip(
            "Collapse the explorer and palette (full-width canvas)")
        self.left_collapse.setAccessibleName(
            "Collapse Graphics Explorer and Palette")
        self.left_collapse.setFixedWidth(self._collapse_width)
        # QToolButton's default VERTICAL policy is Fixed, and a
        # horizontal splitter's maximum height is the tightest of its
        # children's — one Fixed child capped the whole body at ~500px
        # and the leftover height spilled into the titlebar/QAT/ribbon
        # rows, tripling them. Expanding is load-bearing.
        from PySide6.QtWidgets import QSizePolicy
        self.left_collapse.setSizePolicy(QSizePolicy.Fixed,
                                         QSizePolicy.Expanding)
        self.left_collapse.setStyleSheet(
            f"QToolButton {{ border: none; border-left: 1px solid "
            f"{WF['bd']}; border-right: 1px solid {WF['bd']};"
            f"background: {WF['chrome_2']}; color: {WF['tx2']};"
            "font-size: 9pt; font-weight: 700; }"
            f"QToolButton:hover {{ background: {WF['hover']};"
            f"color: {WF['navy']}; }}")
        self.left_collapse.clicked.connect(self.toggle_left_panels)
        self.left_collapse.setContextMenuPolicy(Qt.CustomContextMenu)
        self.left_collapse.customContextMenuRequested.connect(
            self._left_panel_menu)
        body.addWidget(self.left_collapse)

        body.addWidget(self.tabs)
        body.setHandleWidth(4)
        body.setSizes([300, self._collapse_width, 1014])
        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 0)
        body.setStretchFactor(2, 1)
        body.setCollapsible(0, False)
        self._body_split = body
        self._left_dock_width = 300
        self._left_vertical_sizes = [420, 280]
        # Compatibility for integration checks and extensions which inspect
        # the commissioned left sizing. Index 0 is the horizontal dock
        # width; index 1 is its lower (Palette) share.
        self._left_sizes = [self._left_dock_width,
                            self._left_vertical_sizes[1]]
        column.addWidget(body, 1)
        self.setCentralWidget(central)
        self._build_problems_dock()
        self._build_test_data_dock()

        # ---- status bar (§8.1 segment order)
        self._segments = {}
        for key in ("endpoint", "subs", "scan", "quality", "forced",
                    "zoom", "geometry"):
            label = QLabel("")
            if key == "quality":
                label.setObjectName("seg_ok")
            if key == "forced":
                label.setObjectName("seg_forced")
            if key == "geometry":
                label.setMinimumWidth(245)
                label.setAccessibleName("Cursor and selection geometry")
            self._segments[key] = label
            self.statusBar().addWidget(label)
        self._rev_segment = QLabel("")
        self.statusBar().addPermanentWidget(self._rev_segment)
        self._recovery_segment = QPushButton("Saved draft")
        self._recovery_segment.setAccessibleName("Recovery status; click to retry")
        self._recovery_segment.clicked.connect(self._retry_recovery)
        self.statusBar().addPermanentWidget(self._recovery_segment)
        self._quality_segment = QPushButton("Checks pending")
        self._quality_segment.setFixedWidth(190)
        self._quality_segment.setAccessibleName("HMI check status; open Problems")
        self._quality_segment.clicked.connect(lambda: (self.problems_dock.show(), self.problems_dock.raise_()))
        self.statusBar().addPermanentWidget(self._quality_segment)
        self.quality_monitor.stateChanged.connect(self._quality_status_changed)
        self._segments["endpoint"].setText("● live graphs · in-process")

        # ---- open stored displays
        store = DisplayStore(display_root)
        names = sorted(p.name for p in store.root.glob("*")
                       if (p / "draft.json").exists()
                       and not p.name.startswith(PvmStudio.PVM_EDIT_PREFIX)) \
            if store.root.exists() else []
        # A project may contain hundreds of displays. They belong in the
        # Explorer, not as hundreds of eagerly-rendered tabs. Open one useful
        # overview and load other documents only when the engineer selects
        # them; this cut the sample project's launch from ~20 s to a normal
        # application response without changing any display document.
        startup_names = names or ["Overview"]
        startup_name = next(
            (name for name in startup_names if name.casefold() == "overview"),
            None)
        startup_name = startup_name or next(
            (name for name in startup_names
             if name.casefold().startswith("overview")), startup_names[0])
        self.open_display(startup_name, edit=False)
        # Graphics Designer is an authoring application. The active document
        # enters EDIT so the canvas never opens silently read-only.
        active = self.current()
        self._loading_tabs = False
        self.quality_monitor.set_studio(active)
        # Opening every project document used to rebuild the complete
        # Graphics Explorer after each tab. A 13-display project therefore
        # did the same tree/library cross-reference work 13 times and made
        # launch appear dead for about 20 seconds. Populate it once after the
        # startup batch; ordinary user opens still refresh immediately.
        self._reload_display_list()
        if active is not None:
            self._request_edit(active)
            self._sync_chrome()
        # The palette is constructed before the first display tab, so its
        # initial My PVMs pass has no current studio and necessarily renders
        # the empty hint.  Refresh after opening the tab or persisted classes
        # stay invisible until the engineer creates/imports another one.
        self.refresh_user_pvms()

        self._install_keys()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(500)
        self._restore_workspace()

    # ------------------------------------------------------------- chrome
    #: The Azeo Operator Station Graphics Designer ribbon anatomy, from
    #: docs/azeo_live_graphics_designer.html (Sept 2019 white paper
    #: figures) — tab -> [(group, [(glyph, label, action_id,
    #: checkable)])]. Ribbon colour/style tools act on drawing items;
    #: selected PVMs expose their governed Fill and Line fields in Properties.
    _RIBBON = {
        "File": [
            ("New", [("🖵", "Display", "display.new", False),
                     ("📄", "From Template",
                      "display.new_from_template", False),
                     ("▦", "L1-L4 Hierarchy",
                      "display.new_hierarchy", False),
                     ("☆", "Save Template",
                      "display.save_template", False)]),
            ("Graphics", [("💾", "Save", "display.save", False),
                          ("🅟", "Publish", "display.publish", False),
                          ("✓", "Verify", "validate.bindings",
                           False),
                          ("▷", "Quick Online", "display.quick_online",
                           False)]),
            ("Transfer", [("🖼", "Export PNG", "file.export_png",
                           False),
                          ("⇧", "Export Config", "file.export_config",
                           False),
                          ("⇩", "Import Config", "file.import_config",
                           False),
                          ("＋", "New Library", "file.new_library",
                           False),
                          ("▣", "Open Library", "file.open_library",
                           False),
                          ("⌕", "Find / Replace", "edit.find_replace",
                           False),
                          ("Σ", "Complexity", "review.complexity",
                           False)]),
        ],
        "Home": [
            ("Display", [("💾", "Save", "display.save", False),
                         ("✎", "Edit", "mode.edit.toggle", True),
                         ("▷", "Quick Online", "display.quick_online", False)]),
            ("History", [("↶", "Undo", "edit.undo", False),
                         ("↷", "Redo", "edit.redo", False)]),
            ("Clipboard", [("⎘", "Paste", "edit.paste", False),
                           ("⧉", "Copy", "edit.copy", False),
                           ("⎘", "Duplicate", "edit.duplicate", False),
                           ("🖌", "Style Brush", "format.copy_style", False)]),
            ("Arrange", [("⇤", "Align", "arrange.align", False),
                         ("↔", "Distribute", "arrange.distribute", False)]),
            ("Deliver", [("✔", "Verify", "validate.bindings", False),
                         ("🅟", "Publish", "display.publish", False)]),
        ],
        "Insert": [
            ("Engineering", [("#", "Worksheet", "engineering.worksheet", False)]),
            ("Assemblies", [("▦", "Assemblies", "engineering.assemblies", False)]),
            ("Data", [("▤", "Data Link", "insert.datalink", False),
                      ("▭", "Display Link", "insert.display_link", False),
                      ("~", "Chart", "insert.chart", False),
                      ("!", "Alarm List", "insert.alarm_list", False),
                      ("#", "Table", "insert.table", False),
                      ("M", "Multi-Point", "insert.multi_point", False),
                      ("R", "Radar Plot", "insert.radar_plot", False),
                      ("T", "Tab", "insert.tab", False),
                      ("@", "Date-Time", "insert.date_time", False)]),
            ("User Entries", [
                ("▣", "Button", "insert.user.button", False),
                ("☑", "Check Box", "insert.user.check_box", False),
                ("▾", "Combo Box", "insert.user.combo_box", False),
                ("◉", "Radio", "insert.user.radio_button", False),
                ("↕", "Slew", "insert.user.slew", False),
                ("↔", "Slider", "insert.user.slider", False),
                ("I", "Text Entry", "insert.user.text_entry", False),
            ]),
            ("Library", [("◈", "PVM", "insert.pvm", False),
                         ("⌗", "Connector", "tool.pipe", False),
                         ("🧩", "PVM Config", "library.pvm_config",
                          False),
                         ("🖼", "Import SVG", "library.import_svg",
                          False)]),
        ],
        "Review": [
            ("Review tools", [("T", "Sequences", "engineering.sequences", False),
                              ("R", "Compare", "engineering.revisions", False)]),
            ("Commissioning", [("✓", "Checklist", "engineering.commissioning", False),
                               ("R", "Release readiness", "engineering.release", False)]),
            ("Validation", [("✔", "Verify", "validate.bindings",
                             False),
                            ("⚠", "Unresolved", "review.unresolved",
                             False)]),
            ("Analysis", [("TS", "Script Assistant", "tools.script_assistant",
                            False),
                          ("🔍", "Where Used", "edit.find", False),
                          ("≡", "Reg. log", "tools.reglog", False)]),
        ],
        "View": [
            ("Configuration", [("T", "Tag Catalog", "engineering.catalog", False)]),
            ("Quick access", [("⌕", "Commands", "tools.command_search", False),
                              ("⚙", "Properties", "tools.property_search", False)]),
            ("Zoom", [("＋", "Zoom In", "view.zoom.in", False),
                      ("−", "Zoom Out", "view.zoom.out", False),
                      ("1:1", "100%", "view.zoom.100", False),
                      ("⛶", "Fit", "view.zoom.fit", False)]),
            ("Focus", [("\u25a3", "Selection",
                        "view.zoom.selection", False)]),
            ("Show", [("▩", "Grid", "view.grid.toggle", True),
                      ("⊹", "Snap", "view.snap.toggle", True),
                      ("│", "Smart Guides", "view.guides.toggle", True),
                      ("⌗", "Rulers", "view.rulers.toggle", True)]),
            ("Panes", [("🗂", "Explorer", "view.pane.explorer", True),
                       ("🎨", "Palette", "view.pane.palette", True),
                       ("⚠", "Problems", "view.pane.problems", True),
                       ("T", "Test Data", "view.pane.test_data", True),
                       ("⛶", "Focus Canvas", "view.focus.toggle", True)]),
            ("Mode", [("▶", "Test", "mode.test", True),
                      ("✎", "Edit", "mode.edit.toggle", True)]),
        ],
        "Format": [
            ("Shape Styles", [("🎨", "Fill", "format.fill", False),
                              ("▢", "Outline", "format.outline",
                               False)]),
            ("Line Crossings", [
                ("╱", "Continuous", "format.crossover.none", False),
                ("╱", "Break", "format.crossover.break", False),
                ("╱", "Jump", "format.crossover.jump", False),
            ]),
            ("Arrange", [("⇧", "Forward", "arrange.front", False),
                         ("⇩", "Backward", "arrange.back", False),
                         ("⧉", "Group", "edit.group", False),
                         ("↻", "Rotate", "format.rotate", False),
                         ("⇤", "Align", "arrange.align", False),
                         ("□", "Same Size", "arrange.size", False),
                         ("▣", "Inside Page", "arrange.inside", False)]),
            ("Publishing", [("👷", "Set WIP", "display.save", False),
                            ("🅟", "Publish", "display.publish",
                             False)]),
        ],
        "Help": [
            ("Learning", [("F1", "Help Center", "help.center", False),
                          ("KB", "Azeo Help", "help.suite", False),
                          ("?", "Context Help", "help.context", False),
                          ("▶", "Guided Tour", "help.tour", False)]),
            ("Tutorials", [
                ("🖵", "Display Creation", "help.display_creation", False),
                ("PVM", "PVM Creation", "help.pvm_creation", False),
                ("FP", "Faceplate Creation",
                 "help.faceplate_creation", False),
                ("CFG", "PVM Config", "help.pvm_configuration", False),
                ("PIC", "Illustrated Guide",
                 "help.illustrated_tutorial", False),
            ]),
            ("Reference", [
                ("▤", "Bindings", "help.bindings", False),
                ("KB", "Shortcuts", "help.shortcuts", False),
            ]),
            ("Support", [
                ("⚠", "Troubleshoot", "help.troubleshooting", False),
                ("i", "About", "help.about", False),
            ]),
        ],
    }

    def _build_ribbon(self) -> QWidget:
        host = QWidget()
        host.setObjectName("ribbon_host")
        lay = QVBoxLayout(host)
        lay.setContentsMargins(7, 2, 7, 0)
        lay.setSpacing(0)
        from PySide6.QtWidgets import QTabBar
        tabs = QTabBar()
        tabs.setObjectName("ribbon_tabs")
        self.ribbon_tabs = tabs
        tabs.setExpanding(False)        # compact, left-aligned (mock)
        tabs.setDrawBase(False)
        # A four-letter tab must never be shortened to "He" while there is
        # unused ribbon width beside it. Qt otherwise permits elision when a
        # restored splitter geometry briefly constrains the layout at startup.
        tabs.setElideMode(Qt.ElideNone)
        tabs.setUsesScrollButtons(False)
        for name in self._RIBBON:
            tabs.addTab(name)
        # QTabBar's minimumSizeHint is only the width of its scroll-button
        # variant (149 px here), so a layout is allowed to crush the final tab
        # even when the row has ample room. Reserve the full unelided hint.
        tabs.setMinimumWidth(tabs.sizeHint().width())
        tabs.currentChanged.connect(self._ribbon_tab_changed)
        tab_row = QHBoxLayout()
        tab_row.setContentsMargins(0, 0, 0, 0)
        tab_row.addWidget(tabs)
        tab_row.addStretch(1)
        # Workflow and save state remain persistent, but they are status
        # indicators rather than a reason for a separate title strip.
        self.tier_chip = QLabel(self.tier.name)
        self.tier_chip.setObjectName("tier_chip")
        self.tier_chip.setToolTip("Graphics Designer workflow tier")
        tab_row.addWidget(self.tier_chip)
        self.dirty_chip = QLabel("● SAVED")
        self.dirty_chip.setObjectName("dirty_chip")
        self.dirty_chip.setToolTip("Active display save state")
        tab_row.addWidget(self.dirty_chip)
        lay.addLayout(tab_row)

        self._ribbon_row = QWidget()
        self._ribbon_row.setObjectName("ribbon_row")
        self._ribbon_row_layout = QHBoxLayout(self._ribbon_row)
        self._ribbon_row_layout.setContentsMargins(7, 4, 7, 2)
        self._ribbon_row_layout.setSpacing(4)
        # A ribbon page can be wider than a laptop's logical desktop at high
        # DPI. Let that row scroll instead of propagating a 2,000+ px minimum
        # width to the native window (QWindowsWindow then rejects maximize
        # geometry with an oversized ``mintrack`` warning).
        self._ribbon_scroll = QScrollArea()
        self._ribbon_scroll.setObjectName("ribbon_scroll")
        self._ribbon_scroll.setFrameShape(QFrame.NoFrame)
        self._ribbon_scroll.setWidgetResizable(True)
        self._ribbon_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._ribbon_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._ribbon_scroll.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._ribbon_scroll.setFixedHeight(94)
        self._ribbon_scroll.setWidget(self._ribbon_row)
        command_bar = QWidget()
        command_bar.setObjectName("ribbon_commands")
        command_bar.setStyleSheet(f"QWidget#ribbon_commands {{ background: {WF['pane']}; }}")
        command_row = QHBoxLayout(command_bar)
        command_row.setContentsMargins(0, 0, 0, 0)
        command_row.setSpacing(8)
        command_row.addWidget(self._ribbon_scroll, 1)
        # Zoom is persistent view state: keep it reachable while the command
        # groups scroll, rather than leaving half a selector beyond the edge.
        self.zoom = AuthoringComboBox()
        self.zoom.addItems(ZOOM_STEPS)
        self.zoom.setCurrentText("100")
        self.zoom.setMinimumContentsLength(0)
        self.zoom.setSizeAdjustPolicy(AuthoringComboBox.SizeAdjustPolicy.AdjustToContents)
        self.zoom.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.zoom.setAccessibleName("Canvas zoom percent")
        self.zoom.currentTextChanged.connect(
            lambda text: self.dispatch("view.zoom.set", text))
        command_row.addWidget(self.zoom, 0, Qt.AlignVCenter)
        lay.addWidget(command_bar)
        self._ribbon_buttons: dict[str, QPushButton] = {}
        # QTabBar emits currentChanged here. Calling the slot again rebuilt
        # Home twice during construction and queued the first full ribbon for
        # deferred deletion before the window had entered the event loop. In
        # a long Qt session that stale native tree surfaced as 0xc0000374 on
        # the next unrelated inspector click.
        tabs.setCurrentIndex(1)         # Home, like the original
        return host

    def _ribbon_tab_changed(self, index: int) -> None:
        layout = self._ribbon_row_layout
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Hide now so a stale ribbon cannot paint, but leave the
                # native parent in place until DeferredDelete is consumed.
                # Detaching also transfers ownership to the short-lived
                # Python wrapper and can double-free the widget when Qt later
                # processes its queued delete.
                widget.hide()
                widget.deleteLater()
        self._ribbon_buttons.clear()
        tab_name = list(self._RIBBON)[index]
        compact = tab_name == "Home"
        self._ribbon_scroll.setFixedHeight(54 if compact else 94)
        for group, buttons in self._RIBBON[tab_name]:
            group_widget = QWidget()
            group_lay = QVBoxLayout(group_widget)
            group_lay.setContentsMargins(0, 0, 0, 0)
            group_lay.setSpacing(1)
            row = QHBoxLayout()
            row.setSpacing(3)
            for glyph, label, action_id, checkable in buttons:
                # **A QToolButton, not a QPushButton.** A ribbon button
                # is an icon OVER a label, and only QToolButton stacks
                # them (`ToolButtonTextUnderIcon`). A QPushButton puts
                # the icon beside the text and then elides it — which
                # is how "Publish" and "Duplicate" shipped as
                # "Publisl" and "Duplica".
                from PySide6.QtWidgets import QToolButton

                from azeo_control_trainer.core.presentation.studio_icons import (
                    draw_icon as _draw_icon,
                )

                button = QToolButton()
                # **Balance the caption over at most two lines.** Office-
                # style ribbons wrap rather than widening every button to
                # fit the longest phrase. A newline per space made three-
                # word commands collide with the group caption below.
                # The tooltip keeps the original label on one line.
                button.setText(_ribbon_caption(label))
                icon_colour = _ribbon_icon_color(action_id)
                button.setIcon(_draw_icon(
                    RIBBON_ICONS.get(glyph, ""), 22, icon_colour))
                button.setIconSize(QSize(22, 22))
                # Exposed for visual-contract checks and stylesheet inspection
                # without sampling antialiased pixels.
                button.setProperty("iconColor", icon_colour)
                button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
                button.setToolTip(
                    "Align selected elements, or straighten a pipe run "
                    "through an inline valve. Ctrl-click to select more; "
                    "the last selected element is the key object."
                    if action_id == "arrange.align" else label)
                button.setCheckable(checkable)
                # Wide enough for the longest caption in `_RIBBON`
                # rather than a round number: a label the button cannot
                # hold is a label the engineer has to guess at.
                button.setFixedSize(RIBBON_BUTTON_W, 64)
                button.setStyleSheet(
                    f"QToolButton {{ background: transparent;"
                    "border: 1px solid transparent;"
                    "border-radius: 3px; font-size: 9pt;"
                    "padding: 3px 2px 2px 2px;"
                    f"color: {WF['tx']}; }}"
                    f"QToolButton:hover {{ border-color: {WF['sel_br']};"
                    f"background: {WF['hover']}; }}"
                    f"QToolButton:checked {{ background: {WF['sel']};"
                    f"border: 1px solid {WF['sel_br']}; }}")
                if compact:
                    button.setText(label)
                    button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
                    button.setFixedSize(button.fontMetrics().horizontalAdvance(label) + 40, 36)
                    if action_id in ("edit.undo", "edit.redo"):
                        button.setToolButtonStyle(Qt.ToolButtonIconOnly)
                        button.setFixedWidth(36)
                from .component_icons import element_icon
                mark = {"format.copy_style": "style_brush", "mode.edit.toggle": "pencil",
                        "validate.bindings": "check_box",
                        "engineering.commissioning": "check_box",
                        "engineering.release": "check_box",
                        "engineering.worksheet": "table",
                        "engineering.sequences": "chart",
                        "engineering.catalog": "table",
                        "engineering.revisions": "group",
                        "tools.property_search": "table",
                        "engineering.assemblies": "group"}.get(action_id)
                if mark:
                    button.setIcon(element_icon(mark))
                if action_id in ("view.snap.toggle", "view.grid.toggle",
                                 "view.guides.toggle",
                                 "view.pane.explorer",
                                 "view.pane.palette", "tool.select"):
                    button.setChecked(True)
                button.clicked.connect(
                    lambda checked=False, a=action_id:
                    self.dispatch(a))
                self._ribbon_buttons[action_id] = button
                row.addWidget(button)
            group_lay.addLayout(row)
            if not compact:
                caption = QLabel(group, group_widget)
                caption.setProperty("class", "group_caption")
                caption.setStyleSheet(f"color: {WF['tx3']};"
                                      "font-size: 9pt; letter-spacing: 0px;")
                caption.setAlignment(Qt.AlignCenter)
                group_lay.addWidget(caption)
            layout.addWidget(group_widget)
            sep = QFrame()
            sep.setFrameShape(QFrame.VLine)
            sep.setStyleSheet(f"color: {WF['bd_lt']};")
            layout.addWidget(sep)
        layout.addStretch(1)

    def _explorer_tab(self, index: int) -> None:
        """Build the Selection pane the first time it is shown.

        Deferred because it reads the ACTIVE display's scene, and at
        construction there may not be one open yet — a pane built early
        would list nothing and never notice.
        """
        name = self.explorer_tabs.tabText(index)
        if name == "Selection":
            self.refresh_selection_pane()
        elif name == "Layers":
            self.refresh_layers_pane()
        if hasattr(self, "sidebar_navigation") and not getattr(self, "_changing_sidebar", False):
            key = ("graphics", "library", "control", "selection", "layers")[index]
            self.show_sidebar(key)

    def show_sidebar(self, key: str) -> None:
        """Show one task at a useful size, or an explicitly requested split."""
        self._changing_sidebar = True
        try:
            self.sidebar_navigation.blockSignals(True)
            self.sidebar_navigation.setCurrentIndex(max(
                0, self.sidebar_navigation.findData(key)))
            self.sidebar_navigation.blockSignals(False)
            if key not in ("components", "split"):
                index = ("graphics", "library", "control", "selection", "layers").index(key)
                self.explorer_tabs.setCurrentIndex(index)
            self.set_left_panel_visibility(
                explorer=key != "components", palette=key in ("components", "split"))
            self.explorer_tabs.tabBar().setVisible(key == "split")
        finally:
            self._changing_sidebar = False

    def activate_explorer(self, index: int) -> None:
        self.show_sidebar(("graphics", "library", "control", "selection", "layers")[index])

    def refresh_selection_pane(self):
        """(Re)build the Selection pane against the active display."""
        studio = self.current()
        if studio is None:
            return None
        from .studio.selection_pane import SelectionPane

        layout = self.selection_host.layout()
        if self.selection_pane is not None:
            layout.removeWidget(self.selection_pane)
            self.selection_pane.hide()
            self.selection_pane.deleteLater()
        self.selection_pane = SelectionPane(studio)
        layout.addWidget(self.selection_pane)
        return self.selection_pane

    def refresh_layers_pane(self):
        """Rebuild the layer hierarchy against the active document."""
        studio = self.current()
        if studio is None:
            return None
        from .studio.layers import LayersPane

        layout = self.layers_host.layout()
        if self.layers_pane is not None:
            layout.removeWidget(self.layers_pane)
            self.layers_pane.hide()
            self.layers_pane.deleteLater()
        self.layers_pane = LayersPane(studio)
        layout.addWidget(self.layers_pane)
        return self.layers_pane

    def _build_graphics_explorer(self) -> QWidget:
        """Azeo's Graphics Explorer, as the white paper draws it:
        the system root over Displays (foldered by hierarchy),
        Display Sets, Contextual Displays and Layouts."""
        from PySide6.QtWidgets import QLineEdit, QTreeWidget

        panel = QWidget()
        panel.setObjectName("side_explorer")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(6, 6, 6, 4)
        lay.setSpacing(4)
        search = QLineEdit()
        search.setPlaceholderText("Search")
        lay.addWidget(search)
        self.graphics_tree = QTreeWidget()
        self.graphics_tree.setHeaderHidden(True)
        self.graphics_tree.setIconSize(QSize(16, 16))
        self.graphics_tree.setStyleSheet("font-size: 9pt;")
        self.graphics_tree.itemDoubleClicked.connect(
            lambda item, _col: self._open_graphics_item(item))
        self.graphics_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.graphics_tree.customContextMenuRequested.connect(
            self._explorer_menu)
        lay.addWidget(self.graphics_tree, 1)

        def filter_tree(text):
            text = text.lower()

            def walk(item):
                visible = bool(item.data(0, Qt.UserRole)) \
                    and text in item.text(0).lower()
                for i in range(item.childCount()):
                    if walk(item.child(i)):
                        visible = True
                item.setHidden(bool(text) and not visible)
                return visible
            for i in range(self.graphics_tree.topLevelItemCount()):
                walk(self.graphics_tree.topLevelItem(i))
        search.textChanged.connect(filter_tree)
        self._reload_display_list()
        return panel

    def _build_control_explorer(self) -> QWidget:
        """Configured modules/FBs/data, as an Explorer rather than Palette.

        A block row is a reusable-object *binding target*, not a drawing
        class. Dragging it selects a compatible PVM; dragging one of its
        terminal/configuration children creates a bound Data Link.
        """
        from PySide6.QtWidgets import QLineEdit
        from .studio import ControlBrowser

        panel = QWidget()
        panel.setObjectName("side_explorer")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 6, 6, 4)
        layout.setSpacing(4)
        search = QLineEdit()
        search.setPlaceholderText("Search modules, blocks, parameters")
        layout.addWidget(search)
        self.control_browser = ControlBrowser(self._graphs)
        layout.addWidget(self.control_browser, 1)

        def filter_tree(text: str) -> None:
            needle = text.strip().lower()

            def walk(item, inherited=False):
                own = inherited or any(
                    needle in item.text(column).lower()
                    for column in range(item.columnCount()))
                child = False
                for index in range(item.childCount()):
                    child |= walk(item.child(index), own)
                shown = not needle or own or child
                item.setHidden(not shown)
                if needle and child:
                    item.setExpanded(True)
                return shown

            for index in range(self.control_browser.topLevelItemCount()):
                walk(self.control_browser.topLevelItem(index))

        search.textChanged.connect(filter_tree)
        self.control_search = search
        return panel

    def _build_problems_dock(self) -> None:
        """Install the modeless validation workspace."""
        from PySide6.QtWidgets import QDockWidget
        from .studio.problems import ProblemsPane

        dock = QDockWidget("Problems", self)
        dock.setObjectName("graphics_designer_problems")
        dock.setAllowedAreas(Qt.BottomDockWidgetArea | Qt.TopDockWidgetArea)
        self.problems_pane = ProblemsPane(dock)
        self.problems_pane.problem_activated.connect(
            self._activate_problem)
        from .studio.quality import QualityMonitor
        from azeo_control_trainer.core.hmi.theme.service import THEME_LABELS
        from PySide6.QtWidgets import QCheckBox
        self.quality_monitor = QualityMonitor(self)
        self.quality_monitor.updated.connect(self._automatic_findings)
        self.quality_monitor.stateChanged.connect(lambda text: dock.setWindowTitle("Problems · " + text))
        self.problems_pane.fix_requested.connect(self._fix_problem)
        body = QWidget(dock)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(4, 2, 4, 2)
        row = QHBoxLayout()
        auto = QCheckBox("Automatic checks")
        auto.setChecked(True)
        auto.toggled.connect(self.quality_monitor.set_enabled)
        row.addWidget(auto)
        row.addWidget(QLabel("Operator context"))
        self.quality_theme = AuthoringComboBox()
        for name, label in THEME_LABELS.items():
            self.quality_theme.addItem(label, name)
        self.quality_viewport = AuthoringComboBox()
        self.quality_viewport.addItem("Design size", None)
        for width, height in ((1366, 768), (1920, 1080), (2560, 1440)):
            self.quality_viewport.addItem(f"{width} × {height}", (width, height))
        self.quality_theme.currentIndexChanged.connect(self._quality_context_changed)
        self.quality_viewport.currentIndexChanged.connect(self._quality_context_changed)
        row.addWidget(self.quality_theme)
        row.addWidget(self.quality_viewport)
        verify = QPushButton("Check now")
        verify.clicked.connect(self._validate)
        row.addWidget(verify)
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(self.problems_pane)
        dock.setWidget(body)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)
        dock.hide()
        dock.visibilityChanged.connect(self._problems_visibility_changed)
        self.problems_dock = dock

    def _build_test_data_dock(self) -> None:
        """Install render-only scenario data beside validation results."""
        from PySide6.QtWidgets import QDockWidget
        from .studio.test_data import TestDataPane

        dock = QDockWidget("Test Data", self)
        dock.setObjectName("graphics_designer_test_data")
        dock.setAllowedAreas(Qt.BottomDockWidgetArea | Qt.TopDockWidgetArea)
        self.test_data_pane = TestDataPane(dock)
        dock.setWidget(self.test_data_pane)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)
        self.tabifyDockWidget(self.problems_dock, dock)
        dock.hide()
        dock.visibilityChanged.connect(self._test_data_visibility_changed)
        self.test_data_dock = dock

    #: What each permanent folder can create, and what to call it.
    #: `None` means the folder holds derived things — contextual
    #: displays come from registered PVM classes, so "New" there would
    #: promise something the folder cannot do.
    FOLDER_NEW = {
        "displays": ("New Display\u2026", "_new_display"),
        "display_sets": ("New Display Set\u2026", "_new_display_set"),
        "layouts": ("New Layout\u2026", "_new_layout"),
        "contextual": (None, None),
    }

    def _explorer_menu(self, pos) -> None:
        """The Graphics Explorer's context menu.

        Two menus, because the tree holds two kinds of thing. On a
        **permanent folder** it offers New, which is where Azeo puts
        it: "created from the ribbon's New command or from their
        permanent folder's context menu in the Explorer view." On a
        **display** it offers the things you do to one.

        It used to bail on anything without a display name — which
        meant every folder and the empty space below the tree had no
        menu, and so there was no way to add a display at all.
        """
        from azeo_control_trainer.core.presentation.menu_style \
            import studio_menu
        from azeo_control_trainer.core.presentation.headless import is_headless

        item = self.graphics_tree.itemAt(pos)
        name = item.data(0, Qt.UserRole) if item else None
        folder = item.data(0, Qt.UserRole + 1) if item else None
        kind = item.data(0, Qt.UserRole + 2) if item else None

        if name and kind in ("layout", "display_set"):
            menu = self._configuration_menu(studio_menu, kind, name)
        elif name and kind == "contextual":
            menu = self._contextual_menu(studio_menu, name)
        elif name and kind in ("layout_child", "display_set_child"):
            menu = self._configuration_child_menu(
                studio_menu, kind, name, item.text(0))
        elif name:
            menu = self._display_menu(studio_menu, name)
        else:
            # Empty space counts as the Displays folder: right-clicking
            # below the tree is how people reach for "new", and an
            # empty area that answers nothing reads as a dead panel.
            menu = self._folder_menu(studio_menu, folder or "displays")

        retain_menu(self, menu, "_explorer_context_menu")
        if not is_headless():
            menu.exec_transient(self.graphics_tree.mapToGlobal(pos))

    def _folder_menu(self, studio_menu, folder: str):
        """New, on a permanent folder."""
        titles = {"displays": "DISPLAYS", "display_sets": "DISPLAY SETS",
                  "layouts": "LAYOUTS",
                  "contextual": "CONTEXTUAL DISPLAYS",
                  "project": self.area_name.upper()}
        menu = studio_menu(titles.get(folder, folder.upper()),
                           "Graphics Explorer folder")
        if folder == "project":
            for label, handler in (
                    ("New Display…", self._new_display),
                    ("New Display Set…", self._new_display_set),
                    ("New Layout…", self._new_layout),
                    ("New L1–L4 Hierarchy…", self.new_hierarchy_sample)):
                menu.addAction(label).triggered.connect(handler)
            menu.addSeparator()
        label, handler = self.FOLDER_NEW.get(folder, (None, None))
        if label and hasattr(self, handler):
            action = menu.addAction(label)
            font = action.font()
            font.setBold(True)
            action.setFont(font)
            action.triggered.connect(getattr(self, handler))
            menu.addSeparator()
        elif folder == "contextual":
            # Say why rather than showing an empty menu: these are
            # derived from registered PVM classes, so there is nothing
            # to create here and a greyed "New" would imply otherwise.
            note = menu.addAction("Derived from registered PVM classes")
            note.setEnabled(False)
            menu.addSeparator()
        paste = menu.addAction("Paste")
        paste.setEnabled(bool(getattr(self, "_explorer_clipboard", None)))
        paste.triggered.connect(self._paste_display)
        menu.addSeparator()
        expand = menu.addAction("Expand all")
        expand.triggered.connect(self.graphics_tree.expandAll)
        collapse = menu.addAction("Collapse all")
        collapse.triggered.connect(self.graphics_tree.collapseAll)
        refresh = menu.addAction("Refresh")
        refresh.triggered.connect(self._reload_display_list)
        menu.addSeparator()
        hide = menu.addAction("Hide Graphics Explorer")
        hide.triggered.connect(
            lambda: self.set_left_panel_visibility(explorer=False))
        if folder == "contextual":
            menu.addSeparator()
            about = menu.addAction("What is a contextual display?")
            about.triggered.connect(self.explain_contextual_displays)
        return menu

    def _contextual_menu(self, studio_menu, name: str):
        """A contextual class needs live tag context, so never fake Open."""
        menu = studio_menu(f"CONTEXTUAL  {name}", "Registered display class")
        note = menu.addAction("Opens from a bound PVM or Station Search")
        note.setEnabled(False)
        menu.addSeparator()
        menu.addAction("Copy Class Name").triggered.connect(
            lambda: QApplication.clipboard().setText(name))
        menu.addAction("About Contextual Displays").triggered.connect(
            self.explain_contextual_displays)
        return menu

    def _configuration_child_menu(self, studio_menu, kind: str,
                                  name: str, label: str):
        """Open the owning layout/set from one of its structural children."""
        document_kind = "layout" if kind == "layout_child" else "display set"
        menu = studio_menu(label, f"{document_kind.title()} member")
        open_action = menu.addAction(f"Open {document_kind.title()}")
        font = open_action.font()
        font.setBold(True)
        open_action.setFont(font)
        open_action.triggered.connect(
            lambda: self.open_layout(name) if kind == "layout_child"
            else self.open_display_set(name))
        menu.addSeparator()
        menu.addAction("Copy Item Text").triggered.connect(
            lambda: QApplication.clipboard().setText(label))
        return menu

    def _configuration_menu(self, studio_menu, kind: str, name: str):
        """Actions for environment documents, never display actions."""
        title = "LAYOUT" if kind == "layout" else "DISPLAY SET"
        menu = studio_menu(f"{title}  {name}", "Graphics Explorer")
        open_action = menu.addAction("Open")
        font = open_action.font()
        font.setBold(True)
        open_action.setFont(font)
        open_action.triggered.connect(
            lambda: self.open_layout(name) if kind == "layout"
            else self.open_display_set(name))
        if kind == "layout":
            menu.addSeparator()
            assign = menu.addAction("Assign to Workstation…")
            assign.triggered.connect(lambda: self._assign_layout(name))
        menu.addSeparator()
        refresh = menu.addAction("Refresh")
        refresh.triggered.connect(self._reload_display_list)
        return menu

    def _display_menu(self, studio_menu, name: str):
        """Open, rename, delete, publish — what you do to a display."""
        menu = studio_menu("DISPLAY  %s" % name, "Graphics Explorer")
        open_action = menu.addAction("Open")
        font = open_action.font()
        font.setBold(True)
        open_action.setFont(font)
        open_action.triggered.connect(lambda: self.open_display(name))
        properties = menu.addAction("Properties…")
        properties.triggered.connect(
            lambda: self._open_display_properties(name))
        as_template = menu.addAction("Save as Template\u2026")
        as_template.triggered.connect(
            lambda: self._save_display_as_template(name))
        menu.addSeparator()
        new = menu.addAction("New Display\u2026")
        new.triggered.connect(self._new_display)
        rename = menu.addAction("Rename\u2026")
        rename.triggered.connect(lambda: self._rename_display(name))
        delete = menu.addAction("Delete\u2026")
        delete.triggered.connect(lambda: self._delete_display(name))
        menu.addSeparator()
        cut = menu.addAction("Cut")
        cut.triggered.connect(lambda: self._clip_display(name, cut=True))
        copy = menu.addAction("Copy")
        copy.triggered.connect(lambda: self._clip_display(name))
        paste = menu.addAction("Paste")
        paste.setEnabled(bool(getattr(self, "_explorer_clipboard", None)))
        paste.triggered.connect(self._paste_display)
        menu.addSeparator()
        verify = menu.addAction("Verify")
        verify.triggered.connect(
            lambda: (self.open_display(name),
                     self.dispatch("validate.bindings")))
        history = menu.addAction("Revision history\u2026")

        def show_history():
            studio = self.open_display(name)
            if studio is not None:
                studio.open_history()
        history.triggered.connect(show_history)
        publish = menu.addAction("Publish\u2026")
        publish.triggered.connect(
            lambda: (self.open_display(name), self._publish()))
        from azeo_control_trainer.core.hmi.pvms.configuration import InstalledItems
        if InstalledItems(self._root).is_installed("display", name):
            reason = "Installed item — copy and rename it before modifying"
            for action in (properties, rename, delete, cut, publish):
                action.setEnabled(False)
                action.setToolTip(reason)
        return menu

    def _open_display_properties(self, name: str):
        """Edit a tree display through its open draft, never a stale copy."""
        studio = self.open_display(name, edit=True)
        return studio.open_display_properties() if studio is not None else None

    # ---------------------------------------------- explorer clipboard
    def _clip_display(self, name: str, cut: bool = False) -> tuple:
        """Cut or copy a display, as Azeo's Explorer offers.

        The manual lists cut/copy/paste/delete on the Explorer's
        context menu. A CUT is not a move until the paste lands —
        holding the name and a flag rather than removing anything means
        an abandoned cut costs nothing.
        """
        self._explorer_clipboard = (name, cut)
        return self._explorer_clipboard

    def _paste_display(self) -> str:
        """Paste the clipboard as a new display. Returns its name.

        A copy lands as `<name> (2)`, counting up — never overwriting,
        because a paste that replaced a display would take its
        revision history with it and nothing can rebuild that.
        """
        clip = getattr(self, "_explorer_clipboard", None)
        if not clip:
            return ""
        name, cut = clip
        store = DisplayStore(self._root)
        source = store.root / name
        if not source.exists():
            return ""
        target, index = store.root / name, 1
        while target.exists():
            index += 1
            target = store.root / ("%s (%d)" % (name, index))
        if cut:
            if not self._move_display(name, target.name):
                return ""
        else:
            from azeo_control_trainer.core.hmi.pvms.publishing import PvmDisplay
            studio = next((one for one in self.studios()
                           if one.display.name == name), None)
            document = (PvmDisplay.from_dict(studio._document()) if studio
                        else store.load_draft(name))
            if document is None:
                return ""
            # A copy is a new draft, not a second writer/recovery session or
            # a published revision of the source display.
            document.name = target.name
            store.create_draft(document)
        self._explorer_clipboard = None
        self._reload_display_list()
        return target.name

    def explain_contextual_displays(self) -> str:
        """What a contextual display IS, in the manual's own words.

        The folder lists faceplates and detail displays and offers no
        New, which invites the question this answers. From
        `azeolive.chm`, "Contextual displays overview".
        """
        text = (
            "A contextual display is a pop-up whose CONTENT VARIES "
            "with the context it is opened in.\n\n"
            "That is what lets one configuration be shared by many "
            "Azeo objects: a single PID faceplate serves every PID "
            "loop, because the module it shows is the context it was "
            "opened with — not something drawn into it.\n\n"
            "Faceplates and detail displays are the common kinds.\n\n"
            "HOW THEY OPEN\n"
            "  \u2022 Clicking a PVM on a display opens the faceplate "
            "for the block that PVM is bound to.\n"
            "  \u2022 The menu bar's Search opens one for a tag.\n"
            "  \u2022 An action on any element can open one, and may "
            "pass extra context that a data link on the contextual "
            "display then shows.\n\n"
            "WHY THIS FOLDER HAS NO 'NEW'\n"
            "  Here they are DERIVED: each is a registered PVM class "
            "with role 'faceplate' or 'detail', so the list is what "
            "this build can open, not a set of files to author. "
            "Adding one means registering a class \u2014 see "
            "docs/FACEPLATE_UI.md.")
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not is_headless():
            QMessageBox.information(self, "Contextual displays", text)
        return text

    def _rename_display(self, name: str) -> bool:
        """Rename a stored display. False when it did not happen.

        Refuses to overwrite: a rename that silently replaced another
        display would destroy a revision history that nothing else can
        reconstruct.
        """
        from azeo_control_trainer.core.presentation.headless import is_headless

        if is_headless():
            return False
        new_name, ok = QInputDialog.getText(
            self, "Rename display", "New name:", text=name)
        new_name = new_name.strip() if ok else ""
        if not new_name or new_name == name:
            return False
        return self._move_display(name, new_name)

    def _move_display(self, name: str, new_name: str) -> bool:
        """Keep the editor, undo history and lock attached to a renamed folder."""
        from azeo_control_trainer.core.hmi.pvms.publishing import DisplayLocked
        from azeo_control_trainer.core.presentation.headless import is_headless
        from .studio.authoring_state import load_guides, save_guides

        studio = next((one for one in self.studios()
                       if one.display.name == name), None)
        store = studio.store if studio else DisplayStore(self._root)
        temporary_lock = not store.owns_lock(name)
        moved = False
        try:
            store.new_display_path(name)
            store.new_display_path(new_name)
            # An open VIEW tab may be stale. Move its identity without saving
            # it; acquiring a lock here must never promote its old canvas.
            if temporary_lock:
                store.acquire_lock(name)
            current_snapshot = studio is None or (
                studio._draft_fingerprint == store.draft_fingerprint(name))
            store.rename_display(name, new_name)
            moved = True
            if studio:
                studio.display.name = new_name
                if current_snapshot:
                    studio._draft_fingerprint = store.draft_fingerprint(new_name)
                for snapshot in (*studio._undo_stack, *studio._redo_stack):
                    snapshot["display"] = new_name
                guides = studio.authoring_guides
                self.tabs.setTabText(self.tabs.indexOf(studio), new_name)
            else:
                guides = load_guides(self._root, name)
            save_guides(self._root, new_name, guides)
            save_guides(self._root, name, [])
        except (DisplayLocked, OSError, ValueError) as error:
            if not is_headless():
                QMessageBox.warning(self, "Rename refused", str(error))
            return False
        finally:
            if temporary_lock:
                store.release_lock(new_name if moved else name)
        self._reload_display_list()
        return True

    def _delete_display(self, name: str) -> bool:
        """Delete a stored display, revisions and all — after asking.

        The confirmation names what goes. "Delete this display?" hides
        that the revision history goes with it, and that is the part
        nobody can get back.
        """
        from azeo_control_trainer.core.presentation.headless import is_headless

        if is_headless():
            return False
        store = DisplayStore(self._root)
        from azeo_control_trainer.core.hmi.pvms.configuration import InstalledItems
        InstalledItems(self._root).assert_editable("display", name)
        folder = store.root / name
        revisions = len(store.history(name))
        answer = QMessageBox.question(
            self, "Delete display",
            "Delete %s and its %d published revision%s?\n\nThe history "
            "cannot be recovered." % (name, revisions,
                                      "" if revisions == 1 else "s"),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return False
        import shutil
        shutil.rmtree(folder, ignore_errors=True)
        self._reload_display_list()
        return True

    def _new_display_set(self) -> None:
        self._new_layout_object("display set")

    def _new_layout(self) -> None:
        self._new_layout_object("layout")

    def _new_layout_object(self, kind: str) -> bool:
        """Create an empty layout or display set.

        Both live in `LayoutStore`; the difference is which list they
        join, so one method serves and there is one place that knows
        how they are written.
        """
        from azeo_control_trainer.core.presentation.headless import is_headless

        if is_headless():
            return False
        name, ok = QInputDialog.getText(
            self, "New %s" % kind, "%s name:" % kind.capitalize())
        name = name.strip() if ok else ""
        if not name:
            return False
        from azeo_control_trainer.core.hmi.pvms.layout import LayoutStore
        store = LayoutStore(self._root)
        created = store.create(name, kind=kind) \
            if hasattr(store, "create") else None
        if created is None:
            QMessageBox.information(
                self, "Name in use",
                "A %s named %s already exists." % (kind, name))
            return False
        self._reload_display_list()
        if kind == "layout":
            self.open_layout(name)
        else:
            self.open_display_set(name)
        return True

    def _open_graphics_item(self, item):
        """Open a display, display set, or layout in the document area."""
        if item is None:
            return None
        name = item.data(0, Qt.UserRole)
        kind = item.data(0, Qt.UserRole + 2) or "display"
        if not name:
            return None
        if kind == "layout":
            return self.open_layout(name)
        if kind == "display_set":
            return self.open_display_set(name)
        return self.open_display(name)

    def _configuration_tab(self, kind: str, name: str):
        for index in range(self.tabs.count()):
            widget = self.tabs.widget(index)
            if getattr(widget, "configuration_kind", "") == kind \
                    and getattr(widget, "configuration_name", "") == name:
                self.tabs.setCurrentIndex(index)
                return widget
        from azeo_control_trainer.core.hmi.pvms.layout import LayoutStore
        from .studio.layout_editors import DisplaySetEditor, LayoutEditor
        store = LayoutStore(self._root)
        if kind == "layout":
            widget = LayoutEditor(store, name)
            widget.assign_requested.connect(self._assign_layout)
        else:
            widget = DisplaySetEditor(
                store, name,
                displays_provider=lambda: tuple(self._display_hierarchy()))
        widget.configuration_kind = kind
        widget.saved.connect(lambda _name: self._reload_display_list())
        self.tabs.addTab(widget, name)
        self.tabs.setCurrentWidget(widget)
        return widget

    def _assign_layout(self, layout_name: str) -> bool:
        """Open Workstation Management on one layout assignment."""
        from azeo_control_trainer.core.presentation.headless import is_headless
        from azeo_control_trainer.core.hmi.pvms.layout import LayoutStore
        from .studio.layout_editors import WorkstationAssignmentDialog

        dialog = WorkstationAssignmentDialog(
            LayoutStore(self._root), layout_name=layout_name, parent=self)
        self._assignment_dialog = dialog
        if is_headless():
            return False
        return bool(dialog.exec())

    def open_layout(self, name: str):
        return self._configuration_tab("layout", name)

    def open_display_set(self, name: str):
        return self._configuration_tab("display_set", name)

    def assign_workstation(self, workstation: str, *, layout: str = "",
                           display_sets=(), active_display_set: str = ""):
        """Workstation Management's assignment operation, without a copy."""
        from azeo_control_trainer.core.hmi.pvms.layout import LayoutStore
        return LayoutStore(self._root).assign(
            workstation, layout=layout, display_sets=display_sets,
            active_display_set=active_display_set)

    def _display_hierarchy(self) -> dict:
        """{name: (level, parent)} for every stored + open display —
        the ISA navigation tree derives from this, not from scripting."""
        out: dict = {}
        store = DisplayStore(self._root)
        if store.root.exists():
            for path in sorted(store.root.glob("*")):
                if (path / "draft.json").exists() \
                        and not path.name.startswith(
                            PvmStudio.PVM_EDIT_PREFIX):
                    doc = store.load_draft(path.name)
                    out[path.name] = (doc.level, doc.parent)
        for studio in self.studios():
            if not studio.edited_user_class_name() and not studio.template_session:
                out[studio.display.name] = (studio.display.level,
                                            studio.display.parent)
        return out

    def _reload_display_list(self) -> None:
        if not hasattr(self, "graphics_tree"):
            return
        from PySide6.QtWidgets import QTreeWidgetItem

        tree = self.graphics_tree
        tree.clear()

        def node(parent, text, name=None, bold=False, folder=None,
                 config=None, icon_kind="", icon_level=0):
            item = QTreeWidgetItem(parent, [text])
            if icon_kind:
                item.setIcon(0, _graphics_tree_icon(icon_kind, icon_level))
            if name:
                item.setData(0, Qt.UserRole, name)
            if folder:
                # The PERMANENT folders carry their kind. Azeo
                # creates configuration "from the ribbon's New command
                # or from their permanent folder's context menu", so
                # the folder has to be identifiable to offer the right
                # New — and without this tag the context menu bailed
                # on every folder, which left no way to add a display
                # at all.
                item.setData(0, Qt.UserRole + 1, folder)
            if config:
                item.setData(0, Qt.UserRole + 2, config)
            if bold:
                font = QFont(tree.font())
                font.setBold(True)
                item.setFont(0, font)
            return item

        root = node(tree, self.area_name, bold=True, folder="project",
                    icon_kind="project")
        displays = node(root, "Displays", folder="displays",
                        icon_kind="displays")
        hierarchy = self._display_hierarchy()
        placed: dict = {}
        # Parents first, children beneath them; the rest by level.
        for name, (level, parent) in sorted(
                hierarchy.items(), key=lambda kv: (kv[1][0], kv[0])):
            host = placed.get(parent, displays)
            placed[name] = node(host, f"{name}  ·  L{level}", name=name,
                                icon_kind="display", icon_level=level)
        # Display Sets and Layouts are real objects on disk now, not
        # captions. A tree that names a thing the engineer cannot open
        # is a tree that lies about what the system contains.
        from azeo_control_trainer.core.hmi.pvms.layout import LayoutStore
        store = LayoutStore(self._root)
        sets = node(root, "Display Sets", folder="display_sets",
                    icon_kind="display_sets")
        for display_set in store.display_sets():
            set_node = node(sets, display_set.name, name=display_set.name,
                            config="display_set", icon_kind="display_sets")
            for member in display_set.nodes():
                node(set_node,
                     f"{'  ' * (member.level - 1)}{member.display}"
                     f"  ·  L{member.level}"
                     + ("  (placeholder)" if member.placeholder else ""),
                     name=display_set.name if member.placeholder
                     else member.display,
                     config="display_set_child" if member.placeholder
                     else None, icon_kind="display", icon_level=member.level)
            for other in display_set.non_hierarchical:
                node(set_node, f"{other}  ·  non-hierarchical", name=other,
                     icon_kind="display", icon_level=2)
            set_node.setExpanded(True)
        contextual = node(root, "Contextual Displays", folder="contextual",
                          icon_kind="contextual")
        from azeo_control_trainer.core.hmi.pvms.base import registry as _registry
        for (bt, role, variant), cls in sorted(
                _registry.all_classes().items()):
            if role in ("faceplate", "detail") and not variant:
                node(contextual, f"{bt} / {role}",
                     name=f"{bt}/{role}", config="contextual",
                     icon_kind=role)
        layouts = node(root, "Layouts", folder="layouts",
                       icon_kind="layouts")
        for one in store.layouts():
            layout_node = node(layouts, one.name, name=one.name,
                               config="layout", icon_kind="layouts")
            for screen in one.screens:
                screen_node = node(
                    layout_node,
                    f"{screen.name}  ·  {screen.width}×{screen.height}",
                    name=one.name, config="layout_child",
                    icon_kind="screen")
                for frame in screen.frames:
                    levels = ", ".join(f"L{n}" for n in frame.levels) \
                        or ("static" if frame.is_static else "other")
                    node(screen_node, f"{frame.name}  ·  {levels}",
                         name=one.name, config="layout_child",
                         icon_kind="frame")
                screen_node.setExpanded(True)
        root.setExpanded(True)
        displays.setExpanded(True)
        for item in placed.values():
            item.setExpanded(True)

    def _build_palette(self) -> QWidget:
        """Searchable stencils with Equipment ready for a new drawing."""
        from .component_icons import PaletteScrollArea
        panel = PaletteScrollArea()
        panel.setObjectName("palette_scroll")
        panel.setWidgetResizable(True)
        panel.setFrameShape(QScrollArea.NoFrame)
        panel.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        panel.setMinimumWidth(300)
        column = QWidget()
        column.setObjectName("palette_column")
        self._palette_sections = []
        lay = QVBoxLayout(column)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        heading = QWidget()
        heading.setObjectName("palette_heading")
        heading_lay = QVBoxLayout(heading)
        heading_lay.setContentsMargins(10, 7, 10, 7)
        heading_lay.setSpacing(5)
        heading_title = QLabel("Add to display")
        heading_title.setObjectName("palette_heading_title")
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.addWidget(heading_title)
        title_row.addStretch(1)
        self.palette_float_button = QToolButton()
        self.palette_float_button.setObjectName("palette_float")
        self.palette_float_button.setText("FLOAT")
        self.palette_float_button.setToolTip(
            "Detach the component palette into a movable tool window")
        self.palette_float_button.setAccessibleName(
            "Float or dock Component Palette")
        self.palette_float_button.clicked.connect(
            self.toggle_palette_floating)
        title_row.addWidget(self.palette_float_button)
        heading_hint = QLabel("Click, then place · Repeat for several")
        heading_hint.setObjectName("palette_heading_hint")
        heading_lay.addLayout(title_row)
        heading_lay.addWidget(heading_hint)
        self.palette_search = QLineEdit()
        self.palette_search.setObjectName("palette_search")
        self.palette_search.setPlaceholderText(
            "Search symbols, PVMs and components…")
        self.palette_search.setClearButtonEnabled(True)
        self.palette_search.setToolTip(
            "Search every process symbol and PVM class in this palette")
        self.palette_search.textChanged.connect(self._filter_palette)
        heading_lay.addWidget(self.palette_search)
        self.palette_result_label = QLabel("")
        self.palette_result_label.setObjectName("palette_heading_hint")
        self.palette_result_label.hide()
        heading_lay.addWidget(self.palette_result_label)
        lay.addWidget(heading)

        def section(title: str, content: QWidget,
                    expanded: bool = False) -> None:
            header = QPushButton(("▾  " if expanded else "▸  ")
                                 + title)
            header.setObjectName("palette_section")
            header.setCheckable(True)
            header.setChecked(expanded)
            header.setCursor(Qt.PointingHandCursor)
            header.toggled.connect(
                lambda on, c=content, h=header, t=title: (
                    c.setVisible(on),
                    h.setText(("▾  " if on else "▸  ") + t)))
            header.setContextMenuPolicy(Qt.CustomContextMenu)
            header.customContextMenuRequested.connect(
                lambda pos, h=header, t=title:
                self._palette_menu(pos, h, t))
            self._palette_sections.append((header, content, title))
            lay.addWidget(header)
            lay.addWidget(content)
            # Parent the stencil before showing it. An expanded, parentless
            # QWidget briefly creates a native top-level window; reparenting
            # its queued show/layout events corrupted the Windows heap when
            # Studio opened in an existing engineering session.
            content.setVisible(expanded)

        def pvm_grid():
            host = QWidget()
            one = QGridLayout(host)
            one.setContentsMargins(6, 4, 6, 4)
            one.setSpacing(4)
            return host, one

        fb_panel, fb_grid = pvm_grid()
        hp_panel, hp_grid = pvm_grid()
        process_panel, process_grid = pvm_grid()
        from azeo_control_trainer.core.hmi.pvms.base import registry as _registry
        from azeo_control_trainer.core.hmi.pvms.symbols import SYMBOL_FOR_VARIANT
        indexes = {"function": 0, "hp": 0, "process": 0}
        for (block_type, role, variant), cls in sorted(
                _registry.all_classes().items()):
            if role not in ("dynamo_compact", "dynamo_inline"):
                continue
            if variant.startswith("hp"):
                family, target_grid = "hp", hp_grid
            elif variant:
                family, target_grid = "process", process_grid
            else:
                family, target_grid = "function", fb_grid
            index = indexes[family]
            title = cls.display_name if variant else block_type
            symbol = SYMBOL_FOR_VARIANT.get(variant) \
                if role == "dynamo_compact" else None
            target_grid.addWidget(
                _card(title, block_type if variant
                      else role.replace("dynamo_", "").upper(),
                      lambda bt=block_type, r=role, v=variant:
                      self._palette_place(bt, r, v),
                      symbol=symbol, pvm_class=cls,
                      drag_payload={
                          "type": "pvm", "block_type": block_type,
                          "role": role, "variant": variant},
                      help_text=(
                          "A live control PVM. Drop it, then bind its Control "
                          "Tag in Graphics Configuration.")),
                index // 2, index % 2)
            indexes[family] += 1

        # Control Data owns the configured block tree; Components carries
        # reusable visuals that can be placed before choosing a control tag.
        stream_panel = QWidget()
        stream_grid = QGridLayout(stream_panel)
        stream_grid.setContentsMargins(6, 4, 6, 4)
        stream_grid.setSpacing(4)
        for index, (direction, title) in enumerate((
                ("incoming", "Incoming Stream"),
                ("outgoing", "Outgoing Stream"))):
            stream_grid.addWidget(
                _card(
                    title, "OFF-PAGE PROCESS CONNECTION",
                    lambda d=direction: self._arm_stream_connector(d),
                    stream_direction=direction, icon_only=True,
                    drag_payload={
                        "type": "stream_connector",
                        "direction": direction,
                        "title": title,
                    },
                    help_text=(
                        "A named process continuation with one semantic "
                        "pipe port. Incoming establishes flow onto the page; "
                        "outgoing establishes flow off the page.")),
                0, index)
        section("Process Streams", stream_panel)
        data_panel = QWidget()
        data_grid = QGridLayout(data_panel)
        data_grid.setContentsMargins(6, 4, 6, 4)
        data_grid.setSpacing(4)
        data_grid.addWidget(
            _card("Data Link", "LIVE VALUE",
                  lambda: self.dispatch("insert.datalink"), preview_kind="datalink"), 0, 0)
        data_grid.addWidget(
            _card("Display Link", "NAVIGATION",
                  lambda: self.dispatch("insert.display_link"), preview_kind="display_link"), 0, 1)
        for index, (title, kind, subtitle) in enumerate((
                ("Chart", "chart", "10 LIVE PENS"),
                ("Alarm List", "alarm_list", "FILTERED ALARMS"),
                ("Table", "table", "LIVE OR STATIC CELLS"),
                ("Multi-Point", "multi_point", "3-12 AXES"),
                ("Radar Plot", "radar_plot", "3-12 AXES"),
                ("Tab", "tab", "1-32 ITEMS"),
                ("Date-Time", "date_time", "LOCAL OR UTC")), start=2):
            data_grid.addWidget(
                _card(title, subtitle, lambda k=kind: self._arm(k), preview_kind=kind),
                index // 2, index % 2)
        section("Data", data_panel)
        icon_panel = QWidget()
        icon_grid = QGridLayout(icon_panel)
        icon_grid.setContentsMargins(6, 4, 6, 4)
        icon_grid.setSpacing(4)
        from azeo_control_trainer.core.hmi.pvms.faceplate_icons import ICON_TITLES
        for index, (name, title) in enumerate(ICON_TITLES.items()):
            icon_grid.addWidget(
                _card(title, "FACEPLATE SPECIAL SYMBOL",
                      lambda n=name: self._arm_special_symbol(n),
                      faceplate_icon=name, icon_only=True,
                      drag_payload={"type": "special_symbol",
                                    "icon": name}),
                index // 2, index % 2)
        user_panel = QWidget()
        user_grid = QGridLayout(user_panel)
        user_grid.setContentsMargins(6, 4, 6, 4)
        user_grid.setSpacing(4)
        from azeo_control_trainer.core.hmi.pvms.elements import USER_ENTRY_TITLES
        for index, (entry_type, title) in enumerate(
                USER_ENTRY_TITLES.items()):
            user_grid.addWidget(
                _card(title, "OPERATOR WRITE",
                      lambda kind=entry_type: self._arm_user_entry(kind),
                      preview_kind=entry_type),
                index // 2, index % 2)
        section("User Entries", user_panel)
        # The manual names these as separate palettes. Keeping all 129
        # classes under Process PVMs made the common FB choices effectively
        # undiscoverable and collapsed three different engineering concepts.
        section("Function Block", fb_panel)
        section("High Performance PVMs", hp_panel)
        section("Process PVMs", process_panel)
        section("Special Symbols", icon_panel)
        self._user_pvm_panel = QWidget()
        self._user_pvm_grid = QGridLayout(self._user_pvm_panel)
        self._user_pvm_grid.setContentsMargins(6, 4, 6, 4)
        self._user_pvm_grid.setSpacing(4)
        self.refresh_user_pvms()
        section("My PVMs", self._user_pvm_panel)

        # Ready-made faceplate parts. The measured blueprint is composed
        # from exactly these, so a part inserted here is the same thing
        # the scaffold would have given, not a lookalike.
        faceplate_panel = QWidget()
        faceplate_grid = QGridLayout(faceplate_panel)
        faceplate_grid.setContentsMargins(6, 4, 6, 4)
        faceplate_grid.setSpacing(4)
        for index, (key, spec) in enumerate(FACEPLATE_SECTIONS.items()):
            card = _card(
                spec.title, "FACEPLATE PART",
                lambda k=key: self._place_faceplate_section(k),
                help_text=spec.description)
            card._studio_window = self
            faceplate_grid.addWidget(card, index // 2, index % 2)
        # Hosted shipped sections. These are not drawings: the real
        # widget renders itself, so the trip inversion and the sequencer
        # row rules stay in one place.
        offset = len(FACEPLATE_SECTIONS)
        for index, (key, spec) in enumerate(LIVE_FACEPLATE_SECTIONS.items()):
            card = _card(
                spec["title"], "LIVE SECTION",
                lambda k=key: self._place_live_section(k),
                help_text=spec["description"])
            card._studio_window = self
            position = offset + index
            faceplate_grid.addWidget(card, position // 2, position % 2)
        section("Faceplate Parts", faceplate_panel)
        section("Equipment", self._build_graphics_tab(), expanded=True)
        equipment_header, equipment_content, _ = self._palette_sections[-1]
        lay.removeWidget(equipment_header)
        lay.removeWidget(equipment_content)
        lay.insertWidget(1, equipment_header)
        lay.insertWidget(2, equipment_content)
        lay.addStretch(1)
        column.setContextMenuPolicy(Qt.CustomContextMenu)
        column.customContextMenuRequested.connect(
            lambda pos: self._palette_menu(pos, column, ""))
        panel.setWidget(column)
        lay.removeWidget(heading)
        panel.set_header(heading)
        for card in column.findChildren(_PaletteCard):
            card._studio_window = self
        return panel

    def _filter_palette(self, query: str) -> None:
        """Filter all palettes as one stencil library, preserving sections."""
        words = [word.casefold() for word in query.split() if word]
        if words and not hasattr(self, "_palette_filter_state"):
            self._palette_filter_state = [
                header.isChecked()
                for header, _content, _title in self._palette_sections]
        shown = 0
        for header, content, title in self._palette_sections:
            cards = content.findChildren(_PaletteCard)
            if words:
                section_match = all(
                    word in title.casefold() for word in words)
                matches = []
                visible_categories = set()
                for card in cards:
                    match = section_match or all(
                        word in card.search_text for word in words)
                    card.setVisible(match)
                    matches.append(match)
                    shown += int(match)
                    if match and card.property("palette_category"):
                        visible_categories.add(
                            str(card.property("palette_category")))
                for label in content.findChildren(QLabel):
                    key = label.property("palette_category_header")
                    if key:
                        label.setVisible(
                            str(key) in visible_categories)
                visible = any(matches)
                header.setVisible(visible)
                content.setVisible(visible)
                # Do not toggle the button here: that would destroy the
                # engineer's pre-search accordion state.
            else:
                header.setVisible(True)
                for card in cards:
                    card.show()
                for label in content.findChildren(QLabel):
                    if label.property("palette_category_header"):
                        label.show()
                state = getattr(self, "_palette_filter_state", None)
                if state is not None:
                    index = self._palette_sections.index(
                        (header, content, title))
                    header.setChecked(state[index])
                content.setVisible(header.isChecked())
        if words:
            self.palette_result_label.setText(
                f"{shown} matching component{'s' if shown != 1 else ''}")
            self.palette_result_label.show()
        else:
            self.palette_result_label.hide()
            if hasattr(self, "_palette_filter_state"):
                del self._palette_filter_state
        QTimer.singleShot(0, self.palette_box.update_header_geometry)

    def palette_is_floating(self) -> bool:
        return getattr(self, "_palette_dialog", None) is not None

    def toggle_palette_floating(self) -> None:
        """Move the one palette between its dock and a modeless tool window."""
        if self.palette_is_floating():
            self.redock_palette()
        else:
            self.detach_palette()

    def detach_palette(self):
        """Float the live Palette widget without cloning its state."""
        if self.palette_is_floating():
            dialog = self._palette_dialog
            dialog.show()
            dialog.raise_()
            return dialog
        self._remember_left_widths()
        self.palette_box.setParent(None)
        dialog = QDialog(self, Qt.Tool)
        dialog.setObjectName("component_palette_window")
        dialog.setWindowTitle("Components — Azeo Graphics Designer")
        apply_authoring_dialog(dialog)
        dialog.setProperty("embeddedAuthoringShell", True)
        dialog.setModal(False)
        dialog.resize(max(270, self._left_dock_width), 720)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.palette_box)
        self._palette_dialog = dialog
        self.palette_float_button.setText("DOCK")
        self.palette_float_button.setToolTip(
            "Return the component palette to the Studio sidebar")
        dialog.finished.connect(
            lambda _result, d=dialog: self._palette_window_closed(d))
        self.palette_box.show()
        dialog.show()
        self.left_workspace.setVisible(not self.explorer_tabs.isHidden())
        self._sync_left_collapse()
        return dialog

    def _palette_window_closed(self, dialog) -> None:
        if getattr(self, "_palette_dialog", None) is dialog:
            self.redock_palette(close_dialog=False)

    def redock_palette(self, *, close_dialog: bool = True) -> None:
        """Return the live palette to its prior place in the left splitter."""
        dialog = getattr(self, "_palette_dialog", None)
        if dialog is None:
            return
        self._palette_dialog = None
        if dialog.layout() is not None:
            dialog.layout().removeWidget(self.palette_box)
        self.palette_box.setParent(None)
        self.left_workspace.insertWidget(1, self.palette_box)
        self.left_workspace.setStretchFactor(0, 3)
        self.left_workspace.setStretchFactor(1, 2)
        self.left_workspace.setSizes(self._left_vertical_sizes)
        self.palette_box.show()
        self.palette_float_button.setText("FLOAT")
        self.palette_float_button.setToolTip(
            "Detach the component palette into a movable tool window")
        if close_dialog:
            dialog.blockSignals(True)
            dialog.close()
            dialog.deleteLater()
        self.left_workspace.show()
        self._sync_left_collapse()

    def set_palette_sections(self, expanded: bool) -> None:
        """Expand/collapse the entire accordion through its real headers."""
        for header, _content, _title in self._palette_sections:
            header.setChecked(bool(expanded))

    def _palette_menu(self, pos, source, title: str = "") -> None:
        """Section and background commands for the complete Palette tree."""
        from azeo_control_trainer.core.presentation.menu_style import \
            studio_menu

        menu = studio_menu(
            f"PALETTE  {title}" if title else "PALETTE",
            "Drawing and reusable graphics")
        header = source if isinstance(source, QPushButton) else None
        if header is not None:
            toggle = menu.addAction(
                "Collapse This Section" if header.isChecked()
                else "Expand This Section")
            font = toggle.font()
            font.setBold(True)
            toggle.setFont(font)
            toggle.triggered.connect(
                lambda: header.setChecked(not header.isChecked()))
            menu.addSeparator()
        menu.addAction("Expand All Sections").triggered.connect(
            lambda: self.set_palette_sections(True))
        menu.addAction("Collapse All Sections").triggered.connect(
            lambda: self.set_palette_sections(False))
        menu.addSeparator()
        float_action = menu.addAction(
            "Dock Palette" if self.palette_is_floating()
            else "Float Palette")
        float_action.triggered.connect(self.toggle_palette_floating)
        menu.addAction("Hide Palette").triggered.connect(
            lambda: self.set_left_panel_visibility(palette=False))
        menu.addAction("Hide Explorer and Palette").triggered.connect(
            lambda: self.set_left_panel_visibility(
                explorer=False, palette=False))
        menu.addSeparator()
        menu.addAction("Palette and Canvas Help").triggered.connect(
            lambda: self.open_help("workspace"))
        retain_menu(self, menu, "_palette_context_menu")
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not is_headless():
            menu.exec_transient(source.mapToGlobal(pos))

    def _build_graphics_tab(self) -> QWidget:
        """The builder's P&ID symbols as placeable drawing items."""
        self._equipment_panel = QWidget()
        self._equipment_grid = QGridLayout(self._equipment_panel)
        self._equipment_grid.setContentsMargins(6, 4, 6, 4)
        self._equipment_grid.setSpacing(4)
        self.refresh_equipment_palette()
        return self._equipment_panel

    def refresh_equipment_palette(self) -> None:
        """Repopulate the Equipment stencil from the symbol catalog.

        An imported SVG joins CATALOG, so this is also what puts a newly
        imported symbol on the palette beside the vendored artwork —
        under its own category rather than in a separate user list.
        """
        from azeo_control_trainer.core.hmi.pvms.symbols import CATALOG

        grid = self._equipment_grid
        while grid.count():
            entry = grid.takeAt(0)
            widget = entry.widget()
            if widget is not None:
                # Rule 50: content stays parented to its laid-out owner
                # until Qt deletes it. Never setVisible/reparent a loose
                # palette widget — that is the heap fault this repo hit.
                widget.hide()
                widget.deleteLater()
        row = 0
        column = 0
        category = None
        # The complete process-symbol library, divided into named stencil
        # groups. Search crosses every group; browsing no longer means
        # scrolling through one anonymous wall of similar silhouettes.
        for name, (_rel, title, caption) in sorted(
                CATALOG.items(), key=lambda kv: (kv[1][2],
                                                 kv[1][1])):
            if caption != category:
                if column:
                    row += 1
                    column = 0
                category = caption
                label = QLabel(caption.upper())
                label.setProperty("palette_category_header",
                                  caption.casefold())
                label.setStyleSheet(
                    f"color: {WF['navy']}; font-size: 9pt; "
                    f"font-weight: 700; border: none; padding: 6px 2px 2px;")
                grid.addWidget(label, row, 0, 1, 2)
                row += 1
            card = _card(
                title, caption,
                lambda n=name: self.dispatch("tool.symbol", n),
                symbol=name, icon_only=True,
                drag_payload={"type": "symbol", "symbol": name,
                              "title": title},
                help_text=(
                    "A scalable process-engineering symbol with named "
                    "connection ports and automatic pipe routing."))
            card.setProperty("palette_category", caption.casefold())
            grid.addWidget(card, row, column)
            column += 1
            if column == 2:
                row += 1
                column = 0
        if column:
            row += 1
            column = 0
        primitive_heading = QLabel("DRAWING TOOLS")
        primitive_heading.setStyleSheet(
            f"color: {WF['navy']}; font-size: 9pt; font-weight: 700; "
            "border: none; padding: 6px 2px 2px;")
        grid.addWidget(primitive_heading, row, 0, 1, 2)
        row += 1
        from azeo_control_trainer.core.hmi.pvms.shapes import SHAPE_KINDS, SHAPE_TITLES
        basics = [("Pipe", "CONNECT", None),
                  ("Line", "SEGMENT", "line"),
                  ("Polyline", "SEGMENTS", "polyline"),
                  ("Freehand", "STROKE", "freehand"),
                  ("Arc", "CURVE", "arc"),
                  ("Rect", "SHAPE", "rect"),
                  ("Rounded Rect", "SHAPE", "round_rect"),
                  ("Square", "SHAPE", "square"),
                  ("Ellipse", "SHAPE", "ellipse"),
                  ("Text", "LABEL", "text")]
        # The whole primitive vocabulary, so the palette is the
        # inventory: a shape the editor can draw is a shape the
        # palette offers.
        basics += [(SHAPE_TITLES.get(k, k.title()), "SHAPE", k)
                   for k in SHAPE_KINDS]
        #: Tools with a gesture of their own, rather than
        #: click-to-place: the line's press sets the start and SHIFT
        #: snaps it to 45°; the pencil draws freehand.
        gesture_tools = {None: "tool.pipe", "line": "tool.line",
                         "freehand": "tool.pencil"}

        def _tool_handler(kind):
            action = gesture_tools.get(kind)
            if action is not None:
                return lambda: self.dispatch(action)
            return lambda: self.dispatch("tool.basic", kind)

        for title, sub, kind in basics:
            handler = _tool_handler(kind)
            grid.addWidget(_card(title, sub, handler, preview_kind=kind or "pipe"),
                           row, column)
            column += 1
            if column == 2:
                row += 1
                column = 0
        grid.setRowStretch(row + 1, 1)
        # Cards created by a later refresh need the same owner the
        # initial palette build hands out, or their drag payload has
        # nowhere to go.
        for card in self._equipment_panel.findChildren(_PaletteCard):
            card._studio_window = self
        search = getattr(self, "palette_search", None)
        if search is not None and search.text().strip():
            self._filter_palette(search.text())

    # ------------------------------------------------------------- help
    def open_suite_help(self):
        from azeo_control_trainer.core.presentation.product_help import open_product_help
        return open_product_help(self)

    def open_help(self, topic_key: str = "getting_started"):
        """Open one modeless Help Center and deep-link to ``topic_key``."""
        from .studio_help import GraphicsDesignerHelpCenter

        dialog = getattr(self, "_help_center", None)
        if dialog is None:
            dialog = GraphicsDesignerHelpCenter(self)
            self._help_center = dialog
        if not dialog.show_topic(topic_key):
            dialog.show_topic("getting_started")
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        return dialog

    def context_help_topic(self, focus=None) -> str:
        """Resolve F1 from the active engineering surface.

        The mapping uses ownership and selected panes, not widget captions;
        translated labels therefore cannot silently break context help.
        ``focus`` is injectable so headless verification need not paint an
        entire second desktop merely to exercise the ownership mapping.
        """
        focus = QApplication.focusWidget() if focus is None else focus

        def contains(widget) -> bool:
            return focus is widget or (
                focus is not None and widget.isAncestorOf(focus))

        if contains(self.library):
            return "engineering_library"
        if contains(self.explorer_tabs):
            return {
                "Graphics Explorer": "graphics_explorer",
                "Library Explorer": "engineering_library",
                "Control Data": "bindings",
                "Selection": "selection_properties",
                "Layers": "selection_properties",
            }.get(self.explorer_tabs.tabText(
                self.explorer_tabs.currentIndex()), "workspace")
        if contains(self.palette_box):
            return "workspace"
        if contains(self.tabs):
            studio = self.current()
            if studio is not None and studio.mode == MODE_TEST:
                return "validation"
            if studio is not None and studio.edited_user_class_name():
                return "class_builder_workflow"
            return "selection_properties"
        return "getting_started"

    def open_guided_tour(self):
        """Start or restart the modeless six-step authoring tour."""
        from .studio_help import GraphicsDesignerTour

        dialog = getattr(self, "_guided_tour", None)
        if dialog is None:
            dialog = GraphicsDesignerTour(self._focus_tour_step, self)
            self._guided_tour = dialog
        dialog.set_step(0)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        return dialog

    def _focus_tour_step(self, topic_key: str) -> None:
        """Reveal the surface described by the current tour step."""
        if topic_key in ("graphics_explorer", "engineering_library",
                         "class_builder_workflow",
                         "bindings", "selection_properties"):
            self.explorer_tabs.show()
            index = {
                "graphics_explorer": 0,
                "engineering_library": 1,
                "class_builder_workflow": 1,
                "bindings": 2,
                "selection_properties": 3,
            }[topic_key]
            self.activate_explorer(index)
        elif topic_key == "workspace":
            self.set_left_panel_visibility(palette=True)
            self.tabs.setFocus()
        self.statusBar().showMessage(
            "Guided Tour — "
            + topic_key.replace("_", " ").title(), 5000)

    def open_about(self):
        """Show product and active-configuration identity."""
        from .studio_help import GraphicsDesignerAboutDialog

        dialog = getattr(self, "_about_dialog", None)
        if dialog is None:
            dialog = GraphicsDesignerAboutDialog(
                self.area_name, self._library_name, Path(self._root), self)
            self._about_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        return dialog

    # -------------------------------------------------- §8.7 dispatch
    def open_configuration_catalog(self):
        from azeo_control_trainer.core.presentation.headless import is_headless
        from azeo_control_trainer.core.presentation.configuration_catalog import (
            ConfigurationCatalogDialog, context_root,
        )
        dialog = getattr(self, "_configuration_catalog", None)
        if dialog is None:
            dialog = ConfigurationCatalogDialog(context_root(self), self)
            self._configuration_catalog = dialog
        dialog.show()
        if not is_headless():
            dialog.raise_()
            dialog.activateWindow()
        return dialog

    def dispatch(self, action_id: str, argument=None) -> None:
        """One action table; ribbon, menus and keys are thin views."""
        studio = self.current()
        table = {
            "engineering.worksheet": lambda: self.open_engineering_tool("worksheet"),
            "engineering.catalog": self.open_configuration_catalog,
            "engineering.sequences": lambda: self.open_engineering_tool("sequences"),
            "engineering.revisions": lambda: self.open_engineering_tool("revisions"),
            "tools.command_search": self.open_command_search,
            "tools.property_search": lambda: studio and studio.pane.open_property_search(),
            "engineering.assemblies": lambda: self.open_engineering_tool("assemblies"),
            "engineering.commissioning": lambda: self.open_engineering_tool("commissioning"),
            "engineering.release": lambda: self.open_engineering_tool("release"),
            "display.save": lambda: studio and studio.save_draft(),
            "display.publish": self._publish,
            "edit.undo": lambda: studio and studio.undo(),
            "edit.redo": lambda: studio and studio.redo(),
            "tool.text": lambda: self._arm(argument or "text"),
            "tool.shape": lambda: self._arm("rect"),
            # Pipe arms connect: click one symbol, then another — the
            # anchor is whichever edge the mouse chose; then it disarms.
            "tool.pipe": lambda: self._run_edit_tool("arm_connect"),
            # The paper's line gesture: press-drag-release, SHIFT
            # snaps to 45° increments.
            "tool.line": lambda: self._run_edit_tool("arm_line"),
            "tool.basic": lambda: self._arm(argument or "rect"),
            "tool.pencil": lambda: self._run_edit_tool("arm_pencil"),
            "tool.eraser": lambda: self._run_edit_tool("arm_eraser"),
            "tool.pan": lambda: studio and studio.set_pan_mode(True),
            "tool.polygon": lambda: self._arm(argument or "polygon"),
            "tool.shape_kind": lambda: self._arm(argument or "star"),
            "edit.cancel": self._cancel_tools,
            "edit.group": lambda: studio and studio.group_selected(),
            "edit.ungroup": lambda: studio
            and studio.ungroup_selected(),
            "arrange.align.left": lambda: studio
            and studio.align_selected("left"),
            "tool.bubble": lambda: self._arm_symbol("valve"),
            "tool.ellipse": lambda: self._arm("ellipse"),
            "tool.arc": lambda: self._arm("arc"),
            "tool.symbol": lambda: self._arm_symbol(argument),
            "file.export_png": self._export_png,
            "file.export_config": self.export_configuration,
            "file.import_config": self.import_configuration,
            "file.new_library": self.create_configuration_library,
            "file.open_library": self.open_configuration_library,
            "view.zoom.in": lambda: self._zoom_step(+1),
            "view.zoom.out": lambda: self._zoom_step(-1),
            "view.zoom.100": lambda: (
                self.zoom.setCurrentText("100"),
                studio and studio.set_zoom(100)),
            "view.zoom.fit": lambda: studio and studio.fit_drawing(),
            "view.zoom.selection": lambda: studio
            and studio.fit_selection(),
            "view.pane.explorer": lambda: self.set_left_panel_visibility(
                explorer=self.explorer_tabs.isHidden()),
            "view.pane.palette": lambda: self.set_left_panel_visibility(
                palette=not self._palette_is_visible()),
            "view.pane.problems": self.toggle_problems,
            "view.pane.test_data": self.toggle_test_data,
            "view.focus.toggle": self.toggle_focus_mode,
            "review.unresolved": self._validate,
            "format.fill": lambda: self._style_selected("fill"),
            "format.outline": lambda: self._style_selected("line"),
            "format.crossover.none": lambda: studio
            and studio.set_selected_crossover(""),
            "format.crossover.break": lambda: studio
            and studio.set_selected_crossover("gap"),
            "format.crossover.jump": lambda: studio
            and studio.set_selected_crossover("jump"),
            "format.rotate": lambda: studio
            and studio.rotate_selected(90.0),
            "format.copy_style": lambda: self._run_edit_tool("arm_style_brush"),
            "tool.select": self._select_tool,
            "arrange.align": self._align_selected,
            # Was `lambda: None` — a ribbon button that did
            # nothing at all.
            "arrange.distribute":
                lambda: (self.current()
                         and self.current().distribute_selected("h")),
            "arrange.size": lambda: studio
            and studio.equalize_selected("both"),
            "arrange.inside": lambda: studio
            and studio.contain_selected_in_page(),
            "view.zoom.cycle": self._zoom_cycle,
            "insert.pvm": lambda: self.show_sidebar("components"),
            "insert.datalink": lambda: self._arm("datalink"),
            "insert.display_link": lambda: self._arm("display_link"),
            "insert.chart": lambda: self._arm("chart"),
            "insert.alarm_list": lambda: self._arm("alarm_list"),
            "insert.table": lambda: self._arm("table"),
            "insert.multi_point": lambda: self._arm("multi_point"),
            "insert.radar_plot": lambda: self._arm("radar_plot"),
            "insert.tab": lambda: self._arm("tab"),
            "insert.date_time": lambda: self._arm("date_time"),
            "insert.user.button": lambda: self._arm_user_entry("button"),
            "insert.user.check_box": lambda: self._arm_user_entry(
                "check_box"),
            "insert.user.combo_box": lambda: self._arm_user_entry(
                "combo_box"),
            "insert.user.radio_button": lambda: self._arm_user_entry(
                "radio_button"),
            "insert.user.slew": lambda: self._arm_user_entry("slew"),
            "insert.user.slider": lambda: self._arm_user_entry("slider"),
            "insert.user.text_entry": lambda: self._arm_user_entry(
                "text_entry"),
            "library.pvm_config": self.open_pvm_config,
            "library.import_svg": self._import_svg,
            "edit.delete": lambda: studio and studio.delete_selected(),
            "edit.duplicate": lambda: studio
            and studio.duplicate_selected(),
            "edit.copy": self._copy_selected,
            "edit.cut": lambda: (self._copy_selected(),
                                 studio and studio.delete_selected()),
            "edit.paste": self._paste_clipboard,
            "edit.find": self._find_pvm,
            "edit.find_replace": self.find_replace_configuration,
            "arrange.front": lambda: self._z_shift(1),
            "arrange.back": lambda: self._z_shift(-1),
            "view.snap.toggle": lambda: studio and setattr(
                studio, "snap_enabled", not studio.snap_enabled),
            "view.guides.toggle": lambda: studio and setattr(
                studio, "smart_guides_enabled",
                not studio.smart_guides_enabled),
            "view.rulers.toggle": lambda: studio and
            studio.set_rulers_visible(not studio.rulers_visible),
            "view.grid.toggle": lambda: studio
            and studio._set_grid(not studio.grid_visible),
            "view.zoom.set": lambda: studio
            and studio.set_zoom(int(argument or 100)),
            "mode.test": self._toggle_test,
            "mode.edit.toggle": self._toggle_edit,
            "mode.exit_test": lambda: studio and studio.exit_test(),
            "validate.bindings": self._validate,
            "format.standard.set": lambda: None,
            "tools.registry": lambda: self.show_sidebar("library"),
            "tools.reglog": self._show_reglog,
            "tools.script_assistant": lambda: studio
            and studio.open_script_assistant(),
            "display.new": self._new_display,
            "display.new_from_template": self._new_display_from_template,
            "display.new_hierarchy": self.new_hierarchy_sample,
            "display.save_template": self.save_current_as_template,
            "display.quick_online": self.open_quick_online,
            "review.complexity": self.show_complexity_report,
            "help.center": self.open_help,
            "help.suite": self.open_suite_help,
            "help.context": lambda: self.open_help(
                self.context_help_topic()),
            "help.tour": self.open_guided_tour,
            "help.pvm_faceplate": lambda: self.open_help("pvm_faceplate"),
            "help.display_creation": lambda: self.open_help(
                "display_creation"),
            "help.pvm_creation": lambda: self.open_help("pvm_creation"),
            "help.faceplate_creation": lambda: self.open_help(
                "faceplate_creation"),
            "help.pvm_configuration": lambda: self.open_help(
                "pvm_configuration"),
            "help.illustrated_tutorial": lambda: self.open_help(
                "illustrated_tutorial"),
            "help.bindings": lambda: self.open_help("bindings"),
            "help.shortcuts": lambda: self.open_help(
                "keyboard_shortcuts"),
            "help.troubleshooting": lambda: self.open_help(
                "troubleshooting"),
            "help.about": self.open_about,
        }
        handler = table.get(action_id)
        if handler is not None:
            handler()
        self._sync_chrome()

    def _install_keys(self) -> None:
        # Keep the Python wrappers alive for exactly as long as the window.
        # Qt parents the C++ objects, but relying on wrapper rediscovery via
        # findChildren() produced a native heap fault after a modeless Help
        # window was opened in the full operator run.
        self._shortcuts: list[QShortcut] = []

        def add(keys, handler) -> QShortcut:
            shortcut = QShortcut(QKeySequence(keys), self, handler)
            self._shortcuts.append(shortcut)
            return shortcut

        for keys, action_id in (("Ctrl+S", "display.save"),
                                ("Ctrl+K", "tools.command_search"),
                                ("Ctrl+Shift+P", "display.publish"),
                                ("F5", "mode.test"),
                                ("F8", "validate.bindings"),
                                ("F11", "view.focus.toggle"),
                                ("Shift+F", "view.zoom.selection"),
                                ("Ctrl+E", "mode.edit.toggle"),
                                ("Ctrl+N", "display.new")):
            add(keys, lambda a=action_id: self.dispatch(a))
        self._help_shortcut_standard_key = QKeySequence.HelpContents
        self._help_shortcut = add(
            self._help_shortcut_standard_key,
            lambda: self.dispatch("help.context"),
        )
        for i in range(5):
            add(f"Ctrl+{i + 1}", lambda index=i:
                self.activate_explorer(index))
        add("Ctrl+Tab", lambda: self.tabs.setCurrentIndex(
            (self.tabs.currentIndex() + 1) % max(1, self.tabs.count())))
        # Single-letter tool keys, the builder's own set. A tool key
        # is the fastest control in a drawing editor and costs
        # nothing: V select, H pan, R rect, E ellipse, L line,
        # C connector, P pencil, A arc, S shape, X eraser, T text.
        for key, action_id in (("V", "tool.select"),
                               ("H", "tool.pan"),
                               ("R", "tool.shape"),
                               ("E", "tool.ellipse"),
                               ("L", "tool.line"),
                               ("C", "tool.pipe"),
                               ("P", "tool.pencil"),
                               ("A", "tool.arc"),
                               ("S", "tool.polygon"),
                               ("X", "tool.eraser"),
                               ("T", "tool.text")):
            add(key, lambda a=action_id: self.dispatch(a))
        add("Escape", self._cancel_tools)
        for keys, dx, dy in (("Left", -1, 0), ("Right", 1, 0),
                             ("Up", 0, -1), ("Down", 0, 1)):
            add(keys, lambda x=dx, y=dy: self._nudge(x, y))
        # Azeo's Shift+Arrow contract resizes symmetrically about the
        # object's centre: Right/Up grow, Left/Down shrink.  Register it at
        # the window because these shortcuts otherwise intercept the canvas.
        for keys, dw, dh in (("Shift+Left", -1, 0),
                             ("Shift+Right", 1, 0),
                             ("Shift+Up", 0, 1),
                             ("Shift+Down", 0, -1)):
            add(keys, lambda w=dw, h=dh: self._resize(w, h))

    def _cancel_tools(self) -> None:
        studio = self.current()
        if studio is not None:
            studio.cancel_gestures()

    def _run_edit_tool(self, method_name: str) -> None:
        """Arm a direct drawing tool only after EDIT is confirmed."""
        studio = self.current()
        if studio is None or not self._request_edit(studio):
            return
        getattr(studio, method_name)()
        self._sync_chrome()

    def _select_tool(self) -> None:
        """Select means authoring selection, never a read-only dead cursor."""
        studio = self.current()
        if studio is None or not self._request_edit(studio):
            return
        studio.select_tool()
        self._sync_chrome()

    def _nudge(self, dx: float, dy: float) -> None:
        studio = self.current()
        if studio is not None and studio.mode == MODE_EDIT:
            studio.nudge_selected(dx, dy)

    def _resize(self, dw: float, dh: float) -> None:
        studio = self.current()
        if studio is not None and studio.mode == MODE_EDIT:
            studio.resize_selected(dw, dh)

    # ------------------------------------------------------------ displays
    def current(self) -> PvmStudio | None:
        widget = self.tabs.currentWidget()
        return widget if isinstance(widget, PvmStudio) else None

    def studios(self) -> list:
        return [self.tabs.widget(i) for i in range(self.tabs.count())
                if isinstance(self.tabs.widget(i), PvmStudio)]

    def _active_tab_changed(self, _index: int) -> None:
        """An activated display is an authoring document by default."""
        if self._loading_tabs:
            return
        studio = self.current()
        if studio is not None and studio.mode not in (MODE_EDIT, MODE_TEST):
            self._request_edit(studio)
        if studio is not None and getattr(self, "_focus_mode", False):
            studio.pane.hide()
        self.test_data_pane.set_studio(studio)
        self.quality_monitor.set_studio(studio)
        self.problems_pane.set_problems(studio.display.name if studio else "", ())
        self._quality_context_changed()
        if studio is not None:
            self._set_geometry_status(studio.geometry_readout())
        if self.explorer_tabs.currentWidget() is self.selection_host:
            self.refresh_selection_pane()
        elif self.explorer_tabs.currentWidget() is self.layers_host:
            self.refresh_layers_pane()
        self._sync_chrome()

    def _set_geometry_status(self, text: str) -> None:
        """Show CAD coordinates only for the active authoring document."""
        source = self.sender()
        if isinstance(source, PvmStudio) and source is not self.current():
            return
        segment = getattr(self, "_segments", {}).get("geometry")
        if segment is not None:
            segment.setText(str(text))

    def _request_edit(self, studio: PvmStudio, *, notify: bool = True) -> bool:
        """Enter EDIT through the one checked single-writer boundary."""
        try:
            studio.enter_edit()
            return True
        except DisplayLocked as error:
            from azeo_control_trainer.core.presentation.headless import is_headless
            if notify and not is_headless():
                QMessageBox.warning(self, "Display locked", str(error))
            return False

    def open_display(self, name: str, *, edit: bool = True) \
            -> PvmStudio | None:
        """Open an engineering display, editable unless explicitly viewed.

        ``edit=False`` is reserved for startup's inactive tabs and TEST-mode
        navigation.  An ordinary Explorer/open action expresses authoring
        intent and therefore acquires the display's writer lock immediately.
        """
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            if isinstance(widget, PvmStudio) \
                    and widget.display.name == name \
                    and not widget.template_session:
                self.tabs.setCurrentIndex(i)
                if edit:
                    self._request_edit(widget)
                self._sync_chrome()
                return widget
        studio = PvmStudio(self._graphs, self._root, display_name=name,
                           tier=self.tier, hosted=True)
        studio.geometryReadoutChanged.connect(self._set_geometry_status)
        studio.uiError.connect(self._show_ui_error)
        studio.recoveryChanged.connect(self._sync_recovery_status)
        # Sequence playback changes mode directly; waiting for the health
        # timer left TEST navigation hidden until the next polling cycle.
        studio.modeChanged.connect(self._sync_chrome)
        studio.display_opener = self.open_display
        self.tabs.addTab(studio, name)
        self.tabs.setCurrentWidget(studio)
        if edit:
            self._request_edit(studio)
        self._set_geometry_status(studio.geometry_readout())
        if not self._loading_tabs:
            self._reload_display_list()
            self._sync_chrome()
        return studio

    def _show_ui_error(self, message: str) -> None:
        """Report a recoverable authoring failure without blocking the user."""
        self.statusBar().showMessage(str(message), 10_000)

    def _sync_recovery_status(self):
        studio = self.current()
        if studio is None or not hasattr(self, "_recovery_segment"):
            return
        self._recovery_segment.setText(studio.recovery_status())
        self._recovery_segment.setToolTip(
            (studio.recovery_error + (f"\nLast recovery: {studio.recovery_saved_at}" if studio.recovery_saved_at else ""))
            if studio.recovery_error else
            "Last successful recovery checkpoint for this display. Click to retry now; Ctrl+S saves the draft.")

    def _retry_recovery(self):
        studio = self.current()
        if studio is not None:
            studio._write_recovery()

    def _open_created_display(self, name: str) -> PvmStudio | None:
        """Open a newly-created draft through the normal authoring path."""
        store = DisplayStore(self._root)
        if store.load_draft(name) is None:
            # A blank Auto canvas has no page edge, so the first thing an
            # engineer can do is accidentally compose outside an unknown
            # operator frame. New UI-created displays use the same 16:9 page
            # as the built-in ISA-101 templates; legacy Auto drafts retain
            # their content-bound semantics.
            from azeo_control_trainer.core.hmi.pvms.publishing import PvmDisplay
            store.save_draft(PvmDisplay(
                name=name, width=1600, height=900))
        return self.open_display(name, edit=True)

    def _new_display(self, *, prefer_template: bool = False):
        """Create a blank or template-based display through one dialog."""
        from azeo_control_trainer.core.presentation.headless import is_headless

        if is_headless():
            return None
        from .new_display import NewDisplayDialog

        dialog = NewDisplayDialog(
            self.library.templates,
            self,
            prefer_template=prefer_template,
            hierarchy=self._display_hierarchy(),
            display_root=self._root,
        )
        self._new_display_dialog = dialog
        if dialog.exec() != QDialog.Accepted:
            return None
        name = dialog.display_name
        if dialog.template_name:
            return self.new_from_template(dialog.template_name, name,
                                          parent=dialog.parent_name)
        return self._create_blank_display(name, level=dialog.display_level,
                                          parent=dialog.parent_name)

    def _new_display_from_template(self):
        """Open the shared creation dialog with templates preselected."""
        return self._new_display(prefer_template=True)

    def _create_blank_display(self, name: str, *, level: int = 1, parent: str = ""):
        """Atomically reserve a valid name before opening a blank draft."""
        from azeo_control_trainer.core.hmi.pvms.publishing import PvmDisplay
        from azeo_control_trainer.core.presentation.headless import is_headless

        try:
            DisplayStore(self._root).create_draft(PvmDisplay(
                name=name.strip(), width=1600, height=900,
                level=max(1, min(4, int(level or 1))), parent=parent.strip(),
            ))
        except (FileExistsError, ValueError) as error:
            if not is_headless():
                QMessageBox.warning(self, "Display not created", str(error))
            return None
        self._reload_display_list()
        studio = self._open_created_display(name.strip())
        self._announce_created(name.strip())
        return studio

    def _announce_created(self, name: str) -> None:
        """Show where a new display landed: the tree node and the draft file."""
        self._reveal_display(name)
        draft = DisplayStore(self._root).load_draft(name)
        if draft is None:
            return
        under = f"under {draft.parent}" if draft.parent else "at the top of Displays"
        self.statusBar().showMessage(
            f"Created {name} (L{draft.level}) {under}: "
            f"{Path(self._root) / name / 'draft.json'}", 15_000)

    def _reveal_display(self, name: str) -> bool:
        """Expand, scroll to and select a display's node in the Graphics Explorer."""
        tree = getattr(self, "graphics_tree", None)
        if tree is None:
            return False

        def find(item):
            for index in range(item.childCount()):
                child = item.child(index)
                if child.data(0, Qt.UserRole) == name and child.data(0, Qt.UserRole + 2) is None:
                    return child
                found = find(child)
                if found is not None:
                    return found
            return None

        node = find(tree.invisibleRootItem())
        if node is None:
            return False
        ancestor = node.parent()
        while ancestor is not None:
            ancestor.setExpanded(True)
            ancestor = ancestor.parent()
        tree.scrollToItem(node)
        tree.setCurrentItem(node)
        return True

    def _save_display_as_template(self, name: str, template_name: str = ""):
        """Any display becomes a template: the open canvas if it is open, else the draft."""
        from azeo_control_trainer.core.presentation.headless import is_headless
        if self.library.templates is None:
            return None
        studio = next((s for s in self.studios()
                       if s.display.name == name and not s.template_session), None)
        if studio is not None:
            document = studio._document()
        else:
            draft = DisplayStore(self._root).load_draft(name)
            if draft is None:
                return None
            document = draft.to_dict()
        if not template_name:
            if is_headless():
                return None
            template_name, ok = QInputDialog.getText(
                self, "Save as template", "Template name:", text=name)
            if not ok or not template_name.strip():
                return None
        template = self.library.new_template(template_name.strip(), "display", document)
        self.statusBar().showMessage(
            f"Template {template_name.strip()} created from {name}: Library › Templates › "
            "Display templates.", 15_000)
        return template

    def edit_template(self, name: str):
        """Open a library template on the ordinary canvas; Save writes the template.

        A built-in is edited in place as a project override; the Library's
        Reset to built-in restores the product document.
        """
        templates = self.library.templates
        if templates is None or name not in templates.entries:
            return None
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            if isinstance(widget, PvmStudio) and widget.template_session == name:
                self.tabs.setCurrentIndex(i)
                self._request_edit(widget)
                self._sync_chrome()
                return widget
        from .template_session import TemplateDocumentStore
        store = TemplateDocumentStore(templates, name, self._root)
        studio = PvmStudio(self._graphs, self._root, display_name=name,
                           tier=self.tier, hosted=True, store=store)
        studio.template_session = name
        studio.geometryReadoutChanged.connect(self._set_geometry_status)
        studio.uiError.connect(self._show_ui_error)
        studio.recoveryChanged.connect(self._sync_recovery_status)
        studio.modeChanged.connect(self._sync_chrome)
        self.tabs.addTab(studio, f"Template: {name}")
        self.tabs.setCurrentWidget(studio)
        self._request_edit(studio)
        self._set_geometry_status(studio.geometry_readout())
        self._sync_chrome()
        self.statusBar().showMessage(
            f"Editing template {name}: Save writes the template, not a display.", 15_000)
        return studio

    def reset_template(self, name: str) -> bool:
        """Discard the project's edit of a built-in template; closes its open session."""
        templates = self.library.templates
        if templates is None:
            return False
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            if isinstance(widget, PvmStudio) and widget.template_session == name:
                widget.unsaved = False
                self.tabs.removeTab(i)
                widget.close()
                widget.deleteLater()
                break
        done = templates.reset(name)
        if done:
            self.library.rebuild()
            self.statusBar().showMessage(f"Template {name} reset to the built-in document.", 10_000)
        return done

    def new_hierarchy_sample(self, prefix: str = ""):
        """Install and open one wired L1-L4 operator-display sample.

        The four individual templates are useful when a hierarchy already
        exists.  This command is the safe first-project route: it creates the
        documents, Display Set and routed Layout together, so navigation is
        not left as an exercise discovered during operator testing.
        """
        from azeo_control_trainer.core.presentation.headless import is_headless
        from azeo_control_trainer.core.hmi.pvms.hierarchy_templates import (
            HierarchySampleExists,
            install_hierarchy_sample,
        )

        if not prefix:
            if is_headless():
                return None
            prefix, ok = QInputDialog.getText(
                self, "New L1-L4 hierarchy",
                "Area, unit or training hierarchy name:",
                text="Training Area",
            )
            prefix = prefix.strip() if ok else ""
        if not prefix:
            return None
        try:
            sample = install_hierarchy_sample(self._root, prefix)
        except (HierarchySampleExists, ValueError) as exc:
            if not is_headless():
                QMessageBox.information(
                    self, "Hierarchy not created", str(exc),
                )
            return None
        self._reload_display_list()
        self._open_created_display(sample.displays[0])
        if not is_headless():
            QMessageBox.information(
                self, "L1-L4 hierarchy created",
                "Created four editable drafts, display set\n"
                f"{sample.display_set}\n\nand routed layout\n"
                f"{sample.layout}.\n\nConfigure the placeholder PVMs and "
                "bindings, Verify, Test, then Publish each accepted "
                "display.",
            )
        return sample

    def save_current_as_template(self, name: str = ""):
        studio = self.current()
        if studio is None or self.library.templates is None:
            return None
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not name:
            if is_headless():
                return None
            name, ok = QInputDialog.getText(
                self, "Save as template", "Template name:")
            if not ok or not name.strip():
                return None
        template = self.library.new_template(
            name.strip(), "display", studio._document())
        return template

    def new_from_template(self, template_name: str = "", name: str = "", *, parent: str = ""):
        store = self.library.templates
        if store is None or not store.names():
            return None
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not template_name:
            if is_headless():
                return None
            template_name, ok = QInputDialog.getItem(
                self, "New from template", "Template:", store.names(),
                editable=False)
            if not ok:
                return None
        if not name:
            if is_headless():
                return None
            name, ok = QInputDialog.getText(
                self, "New from template", "New name:")
            if not ok or not name.strip():
                return None
        template = store.entries.get(template_name)
        document = store.instantiate(template_name, name.strip())
        if template is None or document is None:
            return None
        if parent.strip():
            document["parent"] = parent.strip()
        if template.kind == "layout":
            from azeo_control_trainer.core.hmi.pvms.layout import Layout, LayoutStore
            layouts = LayoutStore(self._root)
            if layouts.layout(name.strip()) is not None:
                if not is_headless():
                    QMessageBox.warning(self, "Name in use",
                                        f"{name.strip()} already exists.")
                return None
            layouts.save_layout(Layout.from_dict(document))
            self._reload_display_list()
            return self.open_layout(name.strip())
        from azeo_control_trainer.core.hmi.pvms.publishing import PvmDisplay
        # Save through the document store, then open through the ordinary
        # tab path so a template never remains linked to its result.
        from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore
        if any(studio.display.name == name.strip() for studio in self.studios()):
            if not is_headless():
                QMessageBox.warning(self, "Name in use",
                                    f"{name.strip()} is already open.")
            return None
        try:
            DisplayStore(self._root).create_draft(PvmDisplay.from_dict(document))
        except (FileExistsError, ValueError) as error:
            if not is_headless():
                QMessageBox.warning(self, "Display not created", str(error))
            return None
        self._reload_display_list()
        studio = self._open_created_display(name.strip())
        self._announce_created(name.strip())
        return studio

    def open_quick_online(self):
        """Add the current draft to the separate live sandbox."""
        studio = self.current()
        if studio is None:
            return None
        if not hasattr(self, "quick_online"):
            from .quick_online import QuickOnlineView
            self.quick_online = QuickOnlineView(
                self._graphs, source=studio.preview_source.source,
                config_root=self._root, parent=self)
        view = self.quick_online.add_display(studio._document())
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not is_headless():
            self.quick_online.show()
            self.quick_online.raise_()
        return view

    def _activate_tab_action(self, index: int, callback):
        """Run a tab-menu command against the tab that was right-clicked."""
        if not 0 <= index < self.tabs.count():
            return None
        self.tabs.setCurrentIndex(index)
        return callback()

    def _workspace_tab_menu(self, pos) -> None:
        """Document lifecycle commands on every workspace tab."""
        from azeo_control_trainer.core.presentation.menu_style import \
            studio_menu

        index = self.tabs.tabBar().tabAt(pos)
        widget = self.tabs.widget(index) if index >= 0 else None
        title = self.tabs.tabText(index).rstrip(" *") if index >= 0 \
            else "Workspace"
        menu = studio_menu(f"DOCUMENT  {title}", "Graphics Designer workspace")
        if index >= 0:
            activate = menu.addAction("Activate")
            font = activate.font()
            font.setBold(True)
            activate.setFont(font)
            activate.triggered.connect(
                lambda: self.tabs.setCurrentIndex(index))
            if isinstance(widget, PvmStudio):
                menu.addSeparator()
                properties = menu.addAction("Display Properties…")
                properties.triggered.connect(
                    lambda: self._activate_tab_action(
                        index, widget.open_display_properties))
                save = menu.addAction("Save\tCtrl+S")
                save.triggered.connect(
                    lambda: self._activate_tab_action(
                        index, widget.save_draft))
                verify = menu.addAction("Verify\tF8")
                verify.triggered.connect(
                    lambda: self._activate_tab_action(index, self._validate))
                publish = menu.addAction("Publish…")
                publish.triggered.connect(
                    lambda: self._activate_tab_action(index, self._publish))
                quick = menu.addAction("Quick Online")
                quick.triggered.connect(
                    lambda: self._activate_tab_action(
                        index, self.open_quick_online))
                from azeo_control_trainer.core.hmi.pvms.configuration import InstalledItems
                if InstalledItems(self._root).is_installed(
                        "display", widget.display.name):
                    reason = (
                        "Installed item — copy and rename it before modifying")
                    for action in (properties, save, publish):
                        action.setEnabled(False)
                        action.setToolTip(reason)
            elif hasattr(widget, "save"):
                menu.addSeparator()
                save = menu.addAction("Save\tCtrl+S")
                save.triggered.connect(
                    lambda: self._activate_tab_action(index, widget.save))
            menu.addSeparator()
            close = menu.addAction("Close")
            close.triggered.connect(lambda: self._close_tab(index))
            close_others = menu.addAction("Close Other Tabs")
            close_others.setEnabled(self.tabs.count() > 1)
            close_others.triggered.connect(
                lambda: self._close_other_tabs(index))
            close_all = menu.addAction("Close All Tabs")
            close_all.triggered.connect(self._close_all_tabs)
        else:
            new_display = menu.addAction("New Display…")
            new_display.triggered.connect(self._new_display)
            hierarchy = menu.addAction("New L1–L4 Hierarchy…")
            hierarchy.triggered.connect(self.new_hierarchy_sample)
            close_all = menu.addAction("Close All Tabs")
            close_all.setEnabled(self.tabs.count() > 0)
            close_all.triggered.connect(self._close_all_tabs)
        retain_menu(self, menu, "_workspace_context_menu")
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not is_headless():
            menu.exec_transient(self.tabs.tabBar().mapToGlobal(pos))

    def _close_other_tabs(self, keep: int) -> bool:
        keep_widget = self.tabs.widget(keep)
        indexes = [index for index in range(self.tabs.count())
                   if self.tabs.widget(index) is not keep_widget]
        studios = [self.tabs.widget(index) for index in indexes
                   if isinstance(self.tabs.widget(index), PvmStudio)]
        if not self._confirm_studios_close(studios):
            return False
        for index in reversed(indexes):
            self._close_tab(index, confirmed=True)
        return True

    def _close_all_tabs(self) -> bool:
        if not self._confirm_studios_close(self.studios()):
            return False
        for index in range(self.tabs.count() - 1, -1, -1):
            self._close_tab(index, confirmed=True)
        return True

    def _studio_close_decision(self, studio: PvmStudio) -> str | None:
        """Collect one close decision without mutating the document."""
        if not studio.unsaved:
            return "clean"
        from azeo_control_trainer.core.presentation.headless import is_headless
        if is_headless():
            # Offscreen checks retain recovery and never enter a modal loop.
            return "retain_recovery"
        answer = QMessageBox.question(
            self, f"Save changes — {studio.display.name}",
            "Save your changes before closing this document?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save)
        if answer == QMessageBox.Cancel:
            return None
        return "save" if answer == QMessageBox.Save else "discard"

    def _apply_close_decisions(self, decisions) -> bool:
        """Apply a fully approved batch; destructive discards happen last."""
        for studio, decision in decisions:
            if decision != "save":
                continue
            try:
                if studio.save_draft():
                    continue
            except (OSError, PermissionError, ValueError) as error:
                from azeo_control_trainer.core.presentation.headless import is_headless
                if not is_headless():
                    QMessageBox.critical(self, "Save failed", str(error))
            return False
        for studio, decision in decisions:
            if decision != "discard":
                continue
            studio.store.clear_recovery(studio.display.name)
            studio.unsaved = False
            studio._sync_status()
        return True

    def _confirm_studios_close(self, studios) -> bool:
        """Two-phase multi-document close: decide all, then apply all."""
        decisions = []
        for studio in studios:
            decision = self._studio_close_decision(studio)
            if decision is None:
                return False
            decisions.append((studio, decision))
        return self._apply_close_decisions(decisions)

    def _confirm_studio_close(self, studio: PvmStudio) -> bool:
        """Compatibility wrapper for one-document close callers."""
        return self._confirm_studios_close((studio,))

    def _close_tab(self, index: int, *, confirmed: bool = False) -> bool:
        widget = self.tabs.widget(index)
        if isinstance(widget, PvmStudio):
            if not confirmed and not self._confirm_studio_close(widget):
                return False
            widget.close()              # releases the lock if held
        self.tabs.removeTab(index)
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        return True

    # ------------------------------------------------------------- actions
    #: Per-kind default sizes — a line is a LINE, not an invisible
    #: 120×60 box with a stroke through it.
    _SHAPE_SIZES = {"line": (140, 12), "polyline": (140, 80),
                    "text": (110, 20), "rect": (120, 80),
                    "square": (80, 80), "polygon": (84, 84),
                    "hexagon": (84, 84), "arc": (90, 60),
                    "chord": (90, 70), "pie": (90, 70),
                    "round_rect": (120, 80), "ellipse": (110, 80),
                    "star": (96, 92), "cloud": (120, 84),
                    "arrow_shape": (120, 70), "chevron": (110, 76),
                    "triangle": (90, 84), "diamond": (90, 90),
                    "pentagon": (88, 86), "octagon": (88, 88),
                    "trapezoid": (110, 76),
                    "parallelogram": (120, 74),
                    "datalink": (128, 28),
                    "display_link": (142, 34),
                    "stream_connector": (150, 30),
                    "user_entry": (150, 34),
                    "chart": (360, 220),
                    "alarm_list": (360, 180),
                    "table": (360, 220),
                    "icon_button": (32, 32),
                    "special_symbol": (32, 32),
                    "multi_point": (360, 220),
                    "radar_plot": (320, 280),
                    "tab": (340, 220),
                    "date_time": (210, 38)}

    def _insert(self, kind: str) -> None:
        studio = self.current()
        if studio is None or studio.mode == MODE_TEST:
            return
        try:
            center = studio.canvas.mapToScene(
                studio.canvas.viewport().rect().center())
            w, h = self._SHAPE_SIZES.get(kind, (120, 60))
            studio.add_static(kind, x=center.x(), y=center.y(),
                              w=w, h=h,
                              text="Text" if kind == "text" else "")
        except DisplayLocked as error:
            QMessageBox.warning(self, "Display locked", str(error))

    def _export_png(self) -> None:
        """File ▸ Export: render the current display to a PNG."""
        studio = self.current()
        if studio is None:
            return
        from PySide6.QtGui import QImage, QPainter as _QP
        rect = studio.canvas.scene().itemsBoundingRect().adjusted(
            -20, -20, 20, 20)
        if rect.isEmpty():
            return
        image = QImage(int(rect.width()), int(rect.height()),
                       QImage.Format_ARGB32)
        image.fill(Qt.white)
        painter = _QP(image)
        studio.canvas.scene().render(painter, target=None,
                                     source=rect)
        painter.end()
        from azeo_control_trainer.core.presentation.headless import is_headless
        if is_headless():
            self.last_export = image
            return
        from PySide6.QtWidgets import QFileDialog
        path, _f = QFileDialog.getSaveFileName(
            self, "Export display", studio.display.name + ".png",
            "PNG image (*.png)")
        if path:
            image.save(path)

    def export_configuration(self, path: str = ""):
        """Export the active graphics library as one portable package."""
        from azeo_control_trainer.core.hmi.pvms.configuration import ConfigurationLibraryStore
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not path:
            if is_headless():
                return None
            from PySide6.QtWidgets import QFileDialog
            path, _filter = QFileDialog.getSaveFileName(
                self, "Export graphics configuration",
                "graphics-configuration.json", "JSON package (*.json)")
        if not path:
            return None
        return ConfigurationLibraryStore(
            self._configuration_root).export_package(
                path, name=self._library_name)

    def create_configuration_library(self, name: str = ""):
        """Create and open an independent library using PvmDisplay files."""
        from azeo_control_trainer.core.hmi.pvms.configuration import ConfigurationLibraryStore
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not name:
            if is_headless():
                return None
            name, ok = QInputDialog.getText(
                self, "New graphics library", "Library name:")
            if not ok:
                return None
        store = ConfigurationLibraryStore(self._configuration_root)
        root = store.create(name)
        if root is None:
            return None
        self.library.rebuild()
        return self.open_configuration_library(name)

    def open_configuration_library(self, name: str = ""):
        """Open a named library in a sibling Studio window."""
        from azeo_control_trainer.core.hmi.pvms.configuration import ConfigurationLibraryStore
        from azeo_control_trainer.core.presentation.headless import is_headless
        store = ConfigurationLibraryStore(self._configuration_root)
        if not name:
            if is_headless():
                return None
            names = list(store.names())
            name, ok = QInputDialog.getItem(
                self, "Open graphics library", "Library:", names,
                max(0, names.index(store.active)), False)
            if not ok:
                return None
        root = store.select(name)
        if name == self._library_name and Path(root) == Path(self._root):
            return self
        window = HmiStudioWindow(
            self._graphs, root, tier=self.tier, area_name=self.area_name,
            configuration_root=self._configuration_root,
            library_name=name)
        window.setWindowTitle(f"Azeo Graphics Designer — {name}")
        self._library_windows.append(window)
        self.library.rebuild()
        if not is_headless():
            window.show()
        return window

    def import_configuration(self, path: str = "", *,
                             library_name: str = ""):
        """Import into a named library, never over existing data."""
        from azeo_control_trainer.core.hmi.pvms.configuration import ConfigurationLibraryStore
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not path:
            if is_headless():
                return ()
            from PySide6.QtWidgets import QFileDialog
            path, _filter = QFileDialog.getOpenFileName(
                self, "Import graphics configuration", "",
                "JSON package (*.json)")
        if not path:
            return ()
        if not library_name and not is_headless():
            suggested = Path(path).stem or "Imported"
            library_name, ok = QInputDialog.getText(
                self, "Import graphics configuration",
                "Destination library:", text=suggested)
            if not ok or not library_name:
                return ()
        written = ConfigurationLibraryStore(
            self._configuration_root).import_package(
                path, library_name=library_name or self._library_name)
        self._reload_display_list()
        self.library.rebuild()
        return written

    def find_replace_configuration(self, find: str = "",
                                   replace: str = "", *, apply=False):
        """Preview or apply a project-wide graphics replacement."""
        from azeo_control_trainer.core.hmi.pvms.configuration import find_replace
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not find:
            if is_headless():
                return ()
            find, ok = QInputDialog.getText(
                self, "Find in graphics configuration", "Find:")
            if not ok or not find:
                return ()
            replace, ok = QInputDialog.getText(
                self, "Replace in graphics configuration", "Replace with:")
            if not ok:
                return ()
            apply = True
        from azeo_control_trainer.core.hmi.pvms.publishing import DisplayLocked
        studios = {studio.display.name: studio for studio in self.studios()
                   if not studio.edited_user_class_name()}
        drafts = {name: studio._document() for name, studio in studios.items()}
        try:
            results = find_replace(
                self._root, find, replace, apply=apply, drafts=drafts,
                owners={name: studio.store for name, studio in studios.items()})
        except DisplayLocked as error:
            if not is_headless():
                QMessageBox.warning(self, "Replacement refused", str(error))
            return ()
        if apply:
            changed = {result.document for result in results}
            for name, studio in studios.items():
                if f"{name}/draft.json" in changed:
                    studio.checkpoint()
                    studio._load_document(drafts[name])
        return results

    def show_complexity_report(self):
        """Return every display's report; show it when interactive."""
        from azeo_control_trainer.core.hmi.pvms.configuration import complexity_report
        from azeo_control_trainer.core.presentation.headless import is_headless
        current = self.current()
        rows = complexity_report(
            self._root, current.renderer if current is not None else None)
        if not is_headless():
            text = "\n".join(
                f"{row['display']}: index {row['index']}  "
                f"({row['handlers']} handlers, {row['tags']} tags, "
                f"{row['parameters']} parameters; reduce "
                f"{row['dominant']})" for row in rows) or "No displays"
            QMessageBox.information(self, "Display complexity", text)
        return rows

    def _zoom_step(self, direction: int) -> None:
        index = self.zoom.currentIndex() + direction
        if 0 <= index < self.zoom.count():
            self.zoom.setCurrentIndex(index)

    def _style_selected(self, key: str) -> None:
        """Format ▸ Shape Styles — drawing items only (I7 for PVMs)."""
        studio = self.current()
        if studio is None:
            return
        targets = [i for i in (studio._static_items()
                               + studio._pipe_items())
                   if i.isSelected()]
        if not targets:
            return
        from azeo_control_trainer.core.presentation.headless import is_headless
        if is_headless():
            colour_name = "#AA0000"
        else:
            from PySide6.QtWidgets import QColorDialog
            colour = QColorDialog.getColor(parent=self)
            if not colour.isValid():
                return
            colour_name = colour.name()
        studio.checkpoint()
        for target in targets:
            target.data[key] = colour_name
            target.update()
        studio.mark_unsaved()

    def _remember_left_widths(self) -> None:
        sizes = self._body_split.sizes()
        if sizes and sizes[0] > 50:     # unrealized/hidden panes report 0
            self._left_dock_width = sizes[0]
        vertical = self.left_workspace.sizes()
        if len(vertical) == 2:
            for index in (0, 1):
                if vertical[index] > 50:
                    self._left_vertical_sizes[index] = vertical[index]
        self._left_sizes = [self._left_dock_width,
                            self._left_vertical_sizes[1]]

    def _sync_left_collapse(self) -> None:
        # ``isVisible`` is false until every ancestor is shown, even when a
        # pane is intentionally enabled. ``isHidden`` records the authored
        # pane state and therefore also works during headless construction.
        folded = self.explorer_tabs.isHidden() \
            and not self._palette_is_visible()
        self._left_folded = folded
        if folded:
            self.left_collapse.setText("\u25b6")
            self.left_collapse.setToolTip(
                "Expand the Graphics Explorer and Palette")
            self.left_collapse.setAccessibleName(
                "Expand Graphics Explorer and Palette")
        else:
            self.left_collapse.setText("\u25c0")
            self.left_collapse.setToolTip(
                "Collapse the Graphics Explorer and Palette "
                "(full-width canvas)")
            self.left_collapse.setAccessibleName(
                "Collapse Graphics Explorer and Palette")

    def _palette_is_visible(self) -> bool:
        if self.palette_is_floating():
            return not self._palette_dialog.isHidden()
        return not self.palette_box.isHidden()

    def set_left_panel_visibility(self, *, explorer=None,
                                  palette=None) -> None:
        """Show/hide either stacked tool pane without stealing canvas width."""
        self._remember_left_widths()
        if explorer is not None:
            self.explorer_tabs.setVisible(bool(explorer))
        if palette is not None:
            if self.palette_is_floating():
                self.palette_box.setVisible(bool(palette))
                self._palette_dialog.setVisible(bool(palette))
            else:
                self.palette_box.setVisible(bool(palette))
        docked_palette_visible = not self.palette_is_floating() \
            and not self.palette_box.isHidden()
        any_visible = not self.explorer_tabs.isHidden() \
            or docked_palette_visible
        self.left_workspace.setVisible(any_visible)
        self.sidebar_shell.setVisible(any_visible)
        if any_visible:
            explorer_height = self._left_vertical_sizes[0] \
                if not self.explorer_tabs.isHidden() else 0
            palette_height = self._left_vertical_sizes[1] \
                if docked_palette_visible else 0
            self.left_workspace.setSizes([explorer_height, palette_height])
        sizes = self._body_split.sizes()
        total = sum(sizes) or self._left_dock_width + 800
        dock_width = self._left_dock_width if any_visible else 0
        rest = max(total - dock_width - self._collapse_width, 200)
        self._body_split.setSizes([
            dock_width, self._collapse_width, rest])
        self._sync_left_collapse()
        if any_visible and not getattr(self, "_changing_sidebar", False):
            if not self.explorer_tabs.isHidden() and docked_palette_visible:
                key = "split"
            elif self.explorer_tabs.isHidden():
                key = "components"
            else:
                key = ("graphics", "library", "control", "selection", "layers")[
                    self.explorer_tabs.currentIndex()]
            self.sidebar_navigation.blockSignals(True)
            self.sidebar_navigation.setCurrentIndex(self.sidebar_navigation.findData(key))
            self.sidebar_navigation.blockSignals(False)
            self.explorer_tabs.tabBar().setVisible(key == "split")
        buttons = getattr(self, "_ribbon_buttons", {})
        if buttons.get("view.pane.explorer") is not None:
            buttons["view.pane.explorer"].setChecked(
                not self.explorer_tabs.isHidden())
        if buttons.get("view.pane.palette") is not None:
            buttons["view.pane.palette"].setChecked(
                self._palette_is_visible())

    def toggle_left_panels(self) -> None:
        """One chevron folds or restores both engineering panes."""
        any_visible = not self.explorer_tabs.isHidden() \
            or self._palette_is_visible()
        if any_visible:
            self._collapsed_panels = (not self.explorer_tabs.isHidden(), self._palette_is_visible())
            self.set_left_panel_visibility(explorer=False, palette=False)
        else:
            explorer, palette = getattr(self, "_collapsed_panels", (False, True))
            self.set_left_panel_visibility(explorer=explorer, palette=palette)

    def reset_left_panel_widths(self) -> None:
        """Return the compact engineering dock to commissioned proportions."""
        self._left_dock_width = 300
        self._left_vertical_sizes = [420, 280]
        self._left_sizes = [self._left_dock_width,
                            self._left_vertical_sizes[1]]
        self.set_left_panel_visibility(explorer=True, palette=True)
        self.left_workspace.setSizes(self._left_vertical_sizes)

    def toggle_problems(self) -> None:
        """Show/hide the validation results without blocking authoring."""
        self.problems_dock.setVisible(self.problems_dock.isHidden())

    def toggle_test_data(self) -> None:
        """Show/hide the non-destructive TEST scenario workspace."""
        self.test_data_pane.set_studio(self.current())
        self.test_data_dock.setVisible(self.test_data_dock.isHidden())

    def _problems_visibility_changed(self, visible: bool) -> None:
        button = getattr(self, "_ribbon_buttons", {}).get(
            "view.pane.problems")
        if button is not None:
            button.setChecked(bool(visible))

    def _test_data_visibility_changed(self, visible: bool) -> None:
        button = getattr(self, "_ribbon_buttons", {}).get(
            "view.pane.test_data")
        if button is not None:
            button.setChecked(bool(visible))

    def toggle_focus_mode(self) -> None:
        """Give the canvas the workstation while retaining pane state."""
        entering = not getattr(self, "_focus_mode", False)
        if entering:
            self._focus_restore = {
                "explorer": not self.explorer_tabs.isHidden(),
                "palette": self._palette_is_visible(),
                "problems": not self.problems_dock.isHidden(),
                "test_data": not self.test_data_dock.isHidden(),
            }
            self.set_left_panel_visibility(explorer=False, palette=False)
            self.problems_dock.hide()
            self.test_data_dock.hide()
            for studio in self.studios():
                studio.pane.hide()
        else:
            state = getattr(self, "_focus_restore", {})
            self.set_left_panel_visibility(
                explorer=state.get("explorer", True),
                palette=state.get("palette", True))
            if state.get("problems", False):
                self.problems_dock.show()
            if state.get("test_data", False):
                self.test_data_dock.show()
            for studio in self.studios():
                studio.pane.show()
        self._focus_mode = entering
        button = getattr(self, "_ribbon_buttons", {}).get(
            "view.focus.toggle")
        if button is not None:
            button.setChecked(entering)

    def _restore_workspace(self) -> None:
        """Restore pane intent, but never a stale maximized-window geometry."""
        from azeo_control_trainer.core.presentation.headless import is_headless

        self._focus_mode = False
        self.show_sidebar("components")
        if is_headless():
            return
        settings = QSettings(QSettings.defaultFormat(), QSettings.UserScope, "Azeo", "GraphicsDesigner")
        dock_width = settings.value("workspace/left_dock_width", None)
        # Migrate the old side-by-side Explorer width when a workstation has
        # not yet saved the compact-dock schema.
        if dock_width is None:
            old_widths = settings.value("workspace/left_widths", [])
            if isinstance(old_widths, (list, tuple)) and old_widths:
                dock_width = old_widths[0]
        try:
            if dock_width is not None:
                self._left_dock_width = max(300, min(480, int(dock_width)))
        except (TypeError, ValueError):
            pass
        vertical = settings.value("workspace/left_dock_split", [])
        try:
            if isinstance(vertical, (list, tuple)) and len(vertical) == 2:
                self._left_vertical_sizes = [
                    max(80, int(value)) for value in vertical]
        except (TypeError, ValueError):
            pass
        self._left_sizes = [self._left_dock_width,
                            self._left_vertical_sizes[1]]
        # Older releases saved two cramped panes as the only layout. Migrate
        # once to Components; subsequent launches restore the chosen task.
        panel = settings.value("workspace/active_panel", "components")
        if self.sidebar_navigation.findData(panel) < 0:
            panel = "components"
        self.show_sidebar(panel)
        if settings.value("workspace/problems_visible", False, type=bool):
            self.problems_dock.show()
        if settings.value("workspace/test_data_visible", False, type=bool):
            self.test_data_dock.show()

    def _save_workspace(self) -> None:
        from azeo_control_trainer.core.presentation.headless import is_headless

        if is_headless():
            return
        self._remember_left_widths()
        settings = QSettings(QSettings.defaultFormat(), QSettings.UserScope, "Azeo", "GraphicsDesigner")
        settings.setValue("workspace/active_panel", self.sidebar_navigation.currentData())
        restore = getattr(self, "_focus_restore", {}) \
            if getattr(self, "_focus_mode", False) else {}
        settings.setValue("workspace/left_dock_width",
                          self._left_dock_width)
        settings.setValue("workspace/left_dock_split",
                          self._left_vertical_sizes)
        settings.setValue("workspace/explorer_visible",
                          restore.get("explorer",
                                      not self.explorer_tabs.isHidden()))
        settings.setValue("workspace/palette_visible",
                          restore.get("palette",
                                      self._palette_is_visible()))
        settings.setValue("workspace/problems_visible",
                          restore.get("problems",
                                      not self.problems_dock.isHidden()))
        settings.setValue("workspace/test_data_visible",
                          restore.get("test_data",
                                      not self.test_data_dock.isHidden()))

    def _left_panel_menu(self, pos) -> None:
        """Right-click menu for the pane chevron itself."""
        from azeo_control_trainer.core.presentation.menu_style import \
            studio_menu

        menu = studio_menu("ENGINEERING PANES", "Canvas workspace")
        explorer = menu.addAction("Show Graphics Explorer")
        explorer.setCheckable(True)
        explorer.setChecked(not self.explorer_tabs.isHidden())
        explorer.toggled.connect(
            lambda on: self.set_left_panel_visibility(explorer=on))
        palette = menu.addAction("Show Palette")
        palette.setCheckable(True)
        palette.setChecked(self._palette_is_visible())
        palette.toggled.connect(
            lambda on: self.set_left_panel_visibility(palette=on))
        menu.addSeparator()
        both_label = "Show Both Panes" if getattr(
            self, "_left_folded", False) \
            else "Hide Both Panes"
        menu.addAction(both_label).triggered.connect(self.toggle_left_panels)
        menu.addAction("Reset Pane Widths").triggered.connect(
            self.reset_left_panel_widths)
        retain_menu(self, menu, "_left_panel_context_menu")
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not is_headless():
            menu.exec_transient(self.left_collapse.mapToGlobal(pos))

    def _left_tab_menu(self, pos) -> None:
        """Pane commands from the Graphics/Library/Selection tab strip."""
        from azeo_control_trainer.core.presentation.menu_style import \
            studio_menu

        index = self.explorer_tabs.tabBar().tabAt(pos)
        if index < 0:
            index = self.explorer_tabs.currentIndex()
        title = self.explorer_tabs.tabText(index)
        menu = studio_menu(title.upper(), "Engineering pane")
        activate = menu.addAction("Activate")
        font = activate.font()
        font.setBold(True)
        activate.setFont(font)
        activate.triggered.connect(
            lambda: self.activate_explorer(index))
        menu.addSeparator()
        menu.addAction("Hide Graphics Explorer").triggered.connect(
            lambda: self.set_left_panel_visibility(explorer=False))
        menu.addAction("Hide Explorer and Palette").triggered.connect(
            lambda: self.set_left_panel_visibility(
                explorer=False, palette=False))
        menu.addAction("Reset Pane Widths").triggered.connect(
            self.reset_left_panel_widths)
        menu.addSeparator()
        topic = {
            "Graphics Explorer": "graphics_explorer",
            "Library Explorer": "engineering_library",
            "Control Data": "bindings",
            "Selection": "selection_properties",
        }.get(title, "workspace")
        menu.addAction("Help for This Pane").triggered.connect(
            lambda: self.open_help(topic))
        retain_menu(self, menu, "_left_tab_context_menu")
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not is_headless():
            menu.exec_transient(self.explorer_tabs.tabBar().mapToGlobal(pos))

    def refresh_user_pvm_instances(self, class_name: str, *,
                                   exclude=None) -> int:
        """Propagate a class-master Save to linked, editable open drafts."""
        refreshed = 0
        for studio in self.studios():
            if studio is exclude or studio.edited_user_class_name():
                continue
            refreshed += studio.refresh_user_pvm_class(class_name)
        return refreshed

    def refresh_user_pvms(self) -> None:
        """Rebuild the My PVMs palette section from the library and
        the imported symbols — called after every Convert / Import."""
        studio = self.current()
        while self._user_pvm_grid.count():
            entry = self._user_pvm_grid.takeAt(0)
            if entry.widget():
                widget = entry.widget()
                widget.hide()
                widget.deleteLater()
        index = 0
        if studio is not None:
            library = studio.user_library()
            # Azeo uses the library folder tree as the palette tree.
            # A site move therefore takes effect here immediately; it
            # is not copied into a second palette configuration.
            for folder in library.folders():
                folder_title = library.folder_title(folder).upper()
                heading = QLabel(folder_title)
                heading.setStyleSheet(
                    f"color: {WF['tx3']}; font-size: 9pt; "
                    f"font-weight: 600; border: none;")
                self._user_pvm_grid.addWidget(
                    heading, (index + 1) // 2, 0, 1, 2)
                index = ((index + 1) // 2 + 1) * 2
                for name in library.names(folder):
                    from .component_icons import authored_preview
                    config = studio._config_for_name(name)
                    card = _card(
                        name, folder_title,
                        lambda n=name: self._place_user_pvm(n),
                        drag_payload={"type": "user_pvm", "name": name},
                        preview_factory=lambda n=name, c=config, lib=library:
                        authored_preview(lib, n, c, ICON_TILE_W, 64),
                        help_text=(
                            "A linked project class. Editing its class master "
                            "updates linked display instances."))
                    card._studio_window = self
                    self._user_pvm_grid.addWidget(
                        card, index // 2, index % 2)
                    index += 1
        from azeo_control_trainer.core.hmi.pvms.symbols import USER_SYMBOLS
        for name in sorted(USER_SYMBOLS):
            card = _card(
                name, "IMPORTED SVG",
                lambda n=name: self.dispatch("tool.symbol", n),
                symbol=name,
                drag_payload={"type": "symbol", "symbol": name,
                              "title": name})
            card._studio_window = self
            self._user_pvm_grid.addWidget(
                card, index // 2, index % 2)
            index += 1
        if index == 0:
            hint = QLabel("Select drawing items and Convert to PVM "
                          "class (right-click), or Insert ▸ Import "
                          "SVG — your classes land here.")
            hint.setWordWrap(True)
            # Word wrap alone does not shrink a label's size hint —
            # it still asks for its one-line width, which is what
            # widened the whole palette and pushed the second column
            # of every section out of view.
            hint.setMaximumWidth(2 * TILE_MAX_W + 4)
            hint.setStyleSheet(f"color: {WF['tx3']};"
                               "font-size: 9pt; border: none;")
            self._user_pvm_grid.addWidget(hint, 0, 0, 1, 2)
        # Palette and Library Explorer are two views of the same persisted
        # class store. Refreshing only one made a newly-created faceplate look
        # as though it was not part of the project.
        if hasattr(self, "library"):
            self.library.rebuild()

    def _place_user_pvm(self, name: str) -> None:
        studio = self.current()
        if studio is None or studio.mode == MODE_TEST:
            return
        try:
            center = studio.canvas.mapToScene(
                studio.canvas.viewport().rect().center())
            modifiers = QApplication.keyboardModifiers()
            alt = bool(modifiers & Qt.AltModifier)
            nested = alt and bool(modifiers & Qt.ShiftModifier)
            studio.place_user_pvm(
                name, center.x(), center.y(),
                link="unlinked" if alt else "linked",
                unlink_nested=nested)
        except DisplayLocked as error:
            QMessageBox.warning(self, "Display locked", str(error))

    def _place_faceplate_section(self, key: str) -> None:
        """Insert a ready-made faceplate part at the viewport centre.

        Building a faceplate from scratch otherwise means redrawing a PV
        bar — a track, a fill and an EU-normalizing animation descriptor
        — from rectangles each time.
        """
        studio = self.current()
        if studio is None or studio.mode == MODE_TEST:
            return
        try:
            center = studio.canvas.mapToScene(
                studio.canvas.viewport().rect().center())
            count, missing = studio.insert_faceplate_section(
                key, center.x(), center.y())
        except DisplayLocked as error:
            QMessageBox.warning(self, "Display locked", str(error))
            return
        if count and missing:
            # Named, not silent: the section is on the canvas but these
            # bindings cannot resolve until the class declares them.
            studio.uiError.emit(
                "Inserted "
                f"{FACEPLATE_SECTIONS[key].title.lower()}. Add "
                + ", ".join(missing)
                + " in PVM Configuration Designer, or its bindings stay "
                  "unresolved.")

    def _place_live_section(self, key: str) -> None:
        """Place a hosted shipped section, asking for its control tag.

        A sequencer body needs one tag and generates its own thirty-three
        paths from it. Leaving the tag blank binds through the class's
        `Pvm.ControlTag`, which is what an authored faceplate wants.
        """
        studio = self.current()
        if studio is None or studio.mode == MODE_TEST:
            return
        from azeo_control_trainer.core.presentation.headless import is_headless
        tag = ""
        if not is_headless():
            from PySide6.QtWidgets import QInputDialog
            tag, chosen = QInputDialog.getText(
                self, LIVE_FACEPLATE_SECTIONS[key]["title"],
                "Control tag (blank binds through Pvm.ControlTag):")
            if not chosen:
                return
        try:
            center = studio.canvas.mapToScene(
                studio.canvas.viewport().rect().center())
            count, keys = studio.insert_live_section(
                key, center.x(), center.y(), control_tag=tag.strip())
        except DisplayLocked as error:
            QMessageBox.warning(self, "Display locked", str(error))
            return
        if count:
            studio.uiError.emit(
                f"Inserted {LIVE_FACEPLATE_SECTIONS[key]['title'].lower()} "
                f"with {len(keys)} binding path(s).")

    def _import_svg(self) -> None:
        """Insert ▸ Import SVG: the files become first-class equipment
        symbols — themed, tintable and ink-anchored like the vendored
        catalog, and placed in the Equipment stencil under the chosen
        category rather than in a separate user list."""
        from azeo_control_trainer.core.presentation.headless import is_headless
        studio = self.current()
        if studio is None or is_headless():
            return
        from PySide6.QtWidgets import (QFileDialog, QInputDialog,
                                       QMessageBox)
        paths, _f = QFileDialog.getOpenFileNames(
            self, "Import SVG symbols", "", "SVG files (*.svg)")
        if not paths:
            return
        from azeo_control_trainer.core.hmi.pvms.symbols import CATALOG
        categories = sorted({str(entry[2]).upper()
                             for entry in CATALOG.values()})
        if "IMPORTED" in categories:
            categories.remove("IMPORTED")
        categories.insert(0, "IMPORTED")
        category, chosen = QInputDialog.getItem(
            self, "Import SVG symbols",
            "Equipment stencil category:", categories, 0, True)
        if not chosen:
            return
        outcomes = []
        for path in paths:
            outcome = studio.import_svg_file(
                path, category=(category or "IMPORTED").strip().upper())
            if outcome.error and "confirm replacement" in outcome.error:
                answer = QMessageBox.question(
                    self, "Replace SVG symbol?",
                    f"{Path(path).name} would replace {outcome.name}.svg.\n\n"
                    "Existing draft placements use that name. Replace its "
                    "artwork?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if answer == QMessageBox.Yes:
                    outcome = studio.import_svg_file(
                        path, category=(category or "IMPORTED").strip().upper(),
                        replace=True)
            outcomes.append(outcome)
        self.refresh_user_pvms()
        self.refresh_equipment_palette()
        if hasattr(self, "library"):
            self.library.rebuild()
        failed = [outcome for outcome in outcomes if not outcome]
        box = QMessageBox(self)
        box.setWindowTitle("Import SVG symbols")
        box.setIcon(QMessageBox.Warning if failed else QMessageBox.Information)
        done = len(outcomes) - len(failed)
        box.setText(f"{done} of {len(outcomes)} file(s) imported.")
        # Never silent: an import restyles the artwork onto the theme
        # roles and may strip content, and the engineer has to be able
        # to see that it happened.
        box.setDetailedText("\n\n".join(
            f"{Path(path).name}\n  {outcome.summary()}"
            for path, outcome in zip(paths, outcomes)))
        box.exec()

    def edit_user_pvm_layout(self, name: str):
        """Open a user PVM class's shapes in the ordinary editor —
        the class_edit pattern: a `_pvm_<Name>` display tab with the
        full palette (symbols, shapes, lines, pipes); Save rebuilds
        the class and every placed instance follows. `Pvm.X` /
        `Standard.Y` shape-binding references are held aside while
        the layout is edited and restored by shape id on capture."""
        from azeo_control_trainer.core.hmi.pvms.user_library import UserPvmLibrary
        library = UserPvmLibrary(self._root)
        entry = library.entries.get(name)
        if entry is None:
            return None
        studio = self.open_display(
            f"{PvmStudio.PVM_EDIT_PREFIX}{name}")
        if studio is None:
            return None
        studio.enter_edit()
        # A class master has an authoritative coordinate system.  Showing its
        # actual entry size prevents the ordinary 1600x900 display frame from
        # making a 132x64 PVM look like a speck and makes intentional internal
        # whitespace survive Save.
        studio.display.width = max(1, int(round(entry.get("w", 1))))
        studio.display.height = max(1, int(round(entry.get("h", 1))))
        studio.display.safe_margin = 0
        studio.display.fit = "fit_to_frame"
        studio.display.description = (
            f"Reusable {entry.get('definition_kind', 'pvm')} class master; "
            "the dotted page is the instance coordinate system.")
        studio.apply_display_frame()
        if not studio._static_items():
            for data in entry.get("items", []):
                clean = dict(data)
                for key in UserPvmLibrary.BINDABLE:
                    value = clean.get(key)
                    if isinstance(value, str) and (
                            value.startswith(PVM_SCOPE_PREFIXES)
                            or value.startswith("Standard.")):
                        clean.pop(key, None)
                studio._restore_item(clean)
        studio.refit_to_viewport()
        studio.pane.show_pvm(None)
        self.raise_()
        self.activateWindow()
        return studio

    def open_pvm_config(self, pvm_class: str = ""):
        """The PVM Configuration Designer — its own window, its own
        ribbon; configurations live beside the displays."""
        existing = getattr(self, "pvm_config_designer", None)
        if existing is not None:
            try:
                if pvm_class:
                    existing._select_class(pvm_class)
                existing.show()
                existing.raise_()
                existing.activateWindow()
                return existing
            except RuntimeError:
                # A Qt-deleted native object leaves a stale Python wrapper.
                # Ordinary Close only hides this modeless tool and must not
                # create another child window on the next ribbon click.
                self.pvm_config_designer = None
        from .configurator.designer import PvmConfigDesigner
        self.pvm_config_designer = PvmConfigDesigner(
            Path(self._root) / "_pvmcfg", pvm_class=pvm_class,
            parent=self)
        self.pvm_config_designer.configuration_saved.connect(
            self._pvm_configuration_saved)
        self.pvm_config_designer.setWindowFlag(Qt.Window, True)
        self.pvm_config_designer.setWindowTitle(
            "PVM Configuration Designer")
        self.pvm_config_designer.show()
        return self.pvm_config_designer

    def _pvm_configuration_saved(self, class_name: str) -> int:
        """Apply a saved class contract to every open authoring display."""
        from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore, displays_using_class
        from azeo_control_trainer.core.hmi.pvms.user_library import UserPvmLibrary

        library = UserPvmLibrary(self._root)
        authored = library.touch_definition(class_name)
        store = DisplayStore(self._root)
        documents = []
        for path in Path(self._root).iterdir():
            if not path.is_dir() or not (path / "draft.json").exists():
                continue
            document = store.load_draft(path.name)
            if document is not None:
                documents.append(document)
        self.last_affected_displays = displays_using_class(
            class_name, documents)

        refreshed = 0
        for studio in self.studios():
            refreshed += studio.refresh_pvm_configuration(class_name)
            if authored:
                refreshed += studio.refresh_user_pvm_class(class_name)
        if self.last_affected_displays:
            self.statusBar().showMessage(
                f"{class_name} saved — affected displays: "
                + ", ".join(self.last_affected_displays), 8000)
        return refreshed

    def new_user_pvm_class(self, name: str = ""):
        """Create a project PVM from the Library Explorer's New action."""
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not name:
            if is_headless():
                return None
            name, ok = QInputDialog.getText(
                self, "New PVM Class", "Class name:", text="UserPVM")
            if not ok:
                return None
        name = name.strip().replace(" ", "")
        if not name:
            return None
        from azeo_control_trainer.core.hmi.pvms.user_library import UserPvmLibrary
        entry = UserPvmLibrary(self._root).create(name)
        if entry is None:
            return None
        self.refresh_user_pvms()
        self.open_pvm_config(name)
        self.edit_user_pvm_layout(name)
        return entry

    def new_faceplate_blueprint(self, name: str = ""):
        """Create the typed faceplate and its compact calling PVM as a pair."""
        designer = self.open_pvm_config()
        entry = designer.new_faceplate_blueprint(name)
        if entry is not None:
            self.refresh_user_pvms()
        return entry

    def place_from_browser(self, path: str, block_type: str) -> None:
        """ctx.block 'Place on display': drop-to-bind at the canvas
        centre, chooser rules unchanged."""
        studio = self.current()
        if studio is None:
            return
        try:
            center = studio.canvas.mapToScene(
                studio.canvas.viewport().rect().center())
            studio.place_block(path, block_type,
                               center.x(), center.y())
        except DisplayLocked as error:
            QMessageBox.warning(self, "Display locked", str(error))

    def place_parameter_link(self, path: str):
        """Place one configured parameter as a bound Data Link."""
        studio = self.current()
        if studio is None or studio.mode == MODE_TEST:
            return None
        try:
            center = studio.canvas.mapToScene(
                studio.canvas.viewport().rect().center())
            item = studio.add_static(
                "datalink", x=center.x(), y=center.y(), w=128, h=28)
            item.data.update({"path": str(path),
                              "datalink_type": "numeric"})
            studio.rebind_static(item)
            studio.pane.show_item(item)
            return item
        except DisplayLocked as error:
            QMessageBox.warning(self, "Display locked", str(error))
            return None

    def _palette_place(self, block_type: str, role: str,
                       variant: str):
        """A palette PVM card — the Azeo Operator Station flow (p.25): the PVM
        lands first, then the Graphics Configuration pane holds its
        Control Tag, typed and verified or browsed on the ellipsis.
        One candidate still binds immediately: zero dialogs when
        there is nothing to choose."""
        studio = self.current()
        if studio is None or studio.mode == MODE_TEST:
            return None
        candidates = [
            f"{module}/{block.instance_name}"
            for module, graph in sorted(self._graphs().items())
            for block in graph.blocks.values()
            if block.block_type == block_type]

        def place(path: str):
            try:
                center = studio.canvas.mapToScene(
                    studio.canvas.viewport().rect().center())
                return studio.place_block(
                    path, block_type, center.x(), center.y(),
                    role=(role, variant))
            except DisplayLocked as error:
                QMessageBox.warning(self, "Display locked",
                                    str(error))
                return None

        if len(candidates) == 1:
            return place(candidates[0])
        pvm = place("")
        item = next((i for i in studio._items()
                     if pvm is not None and i.pvm.id == pvm.id),
                    None)
        if item is not None:
            studio.canvas.scene().clearSelection()
            item.setSelected(True)
            studio.pane.show_pvm(item)
        return pvm

    def _arm_palette_item(self, payload: dict) -> None:
        studio = self.current()
        if studio is None or studio.mode == MODE_TEST or not self._request_edit(studio):
            return
        payload = dict(payload)
        if payload.get("type") == "pvm":
            candidates = [f"{module}/{block.instance_name}"
                          for module, graph in self._graphs().items()
                          for block in graph.blocks.values()
                          if block.block_type == payload.get("block_type")]
            if len(candidates) == 1:
                payload["path"] = candidates[0]
        studio.arm_palette_item(payload)
        from .quick_access import remember_asset
        remember_asset(payload)
        self._sync_chrome()

    def _arm(self, kind: str) -> None:
        """Arm the direct drawing gesture appropriate to this element."""
        studio = self.current()
        if studio is None or studio.mode == MODE_TEST:
            return
        try:
            w, h = self._SHAPE_SIZES.get(kind, (120, 60))
            from azeo_control_trainer.core.hmi.pvms.shapes import SHAPE_KINDS
            if kind == "polyline":
                studio.arm_polyline()
            elif kind in (("rect", "square", "ellipse", "round_rect",
                           "arc", "text") + SHAPE_KINDS):
                studio.arm_shape(kind, w=w, h=h)
            else:
                studio.arm_place(kind, w=w, h=h)
        except DisplayLocked as error:
            QMessageBox.warning(self, "Display locked", str(error))

    def _arm_user_entry(self, entry_type: str) -> None:
        """Arm one of the seven controls from the shared User Entry model."""
        studio = self.current()
        if studio is None or studio.mode == MODE_TEST:
            return
        self._arm("user_entry")
        ghost = getattr(studio, "_place_ghost", None)
        if ghost is None:
            return
        from azeo_control_trainer.core.hmi.pvms.elements import (COMBO_BOX, RADIO_BUTTON,
                               USER_ENTRY_TITLES)
        entry = {"kind": entry_type,
                 "label": USER_ENTRY_TITLES.get(entry_type, "User Entry"),
                 "path": ""}
        if entry_type in (COMBO_BOX, RADIO_BUTTON):
            entry["options"] = [[0, "Off"], [1, "On"]]
        if entry_type in ("slew", "slider"):
            entry.update({"lo": 0.0, "hi": 100.0})
        ghost.data["entry"] = entry
        ghost.update()

    def _arm_faceplate_icon(self, name: str) -> None:
        """Arm a manual-derived vector icon for placement.

        It remains artwork until an Interaction action is assigned.  The
        painter adds its button shell only when it has a real
        hotspot, which keeps an inert icon from claiming to be clickable.
        """
        studio = self.current()
        if studio is None or studio.mode == MODE_TEST:
            return
        self._arm("icon_button")
        ghost = getattr(studio, "_place_ghost", None)
        if ghost is None:
            return
        ghost.data["icon"] = name
        ghost.update()

    def _arm_special_symbol(self, name: str) -> None:
        """Arm one manual-derived faceplate symbol as reusable artwork."""
        studio = self.current()
        if studio is None or studio.mode == MODE_TEST:
            return
        self._arm("special_symbol")
        ghost = getattr(studio, "_place_ghost", None)
        if ghost is None:
            return
        from azeo_control_trainer.core.hmi.pvms.faceplate_icons import SPECIAL_SYMBOL_SIZES
        width, height = SPECIAL_SYMBOL_SIZES.get(name, (32, 32))
        ghost.prepareGeometryChange()
        ghost.setRect(0, 0, width, height)
        ghost.data.update({"w": width, "h": height})
        ghost.data["icon"] = name
        ghost.update()

    def _arm_stream_connector(self, direction: str) -> None:
        """Arm a named off-page continuation with truthful flow semantics."""
        studio = self.current()
        if studio is None or studio.mode == MODE_TEST:
            return
        from azeo_control_trainer.core.hmi.pvms.elements import (
            STREAM_INCOMING, stream_connector_properties,
        )

        label = "INCOMING STREAM" if direction == STREAM_INCOMING \
            else "OUTGOING STREAM"
        try:
            studio.arm_place(
                "stream_connector", w=150, h=30, text=label,
                properties=stream_connector_properties(direction))
        except DisplayLocked as error:
            QMessageBox.warning(self, "Display locked", str(error))

    def _arm_symbol(self, name: str | None) -> None:
        studio = self.current()
        if studio is None or not name or studio.mode == MODE_TEST:
            return
        try:
            studio.arm_place("symbol", symbol=name, w=110)
        except DisplayLocked as error:
            QMessageBox.warning(self, "Display locked", str(error))

    def _copy_selected(self) -> None:
        """PVM clipboard: placements copy as their document form."""
        studio = self.current()
        if studio is None:
            return
        self._pvm_clipboard = studio.copy_selection_payload()

    def _paste_clipboard(self) -> None:
        studio = self.current()
        clipboard = getattr(self, "_pvm_clipboard", None)
        if studio is None or not clipboard:
            return
        try:
            studio.paste_payload(clipboard)
        except DisplayLocked as error:
            QMessageBox.warning(self, "Display locked", str(error))

    def open_command_search(self):
        from .quick_access import CommandSearch, present_search
        self._command_search = present_search(CommandSearch(self))
        return self._command_search

    def open_engineering_tool(self, kind):
        studio = self.current()
        if studio is None:
            return None
        from .engineering_tools import AssemblyDialog, CommissioningDialog
        from .release_workflow import ReleaseDialog
        from .worksheet import WorksheetDialog
        from .sequence_tools import SequenceDialog
        from .revision_review import RevisionReview
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not hasattr(self, "_engineering_dialogs"):
            self._engineering_dialogs = []
        cls = {"assemblies": AssemblyDialog, "commissioning": CommissioningDialog,
               "worksheet": WorksheetDialog, "sequences": SequenceDialog,
               "revisions": RevisionReview, "release": ReleaseDialog}[kind]
        for existing in self._engineering_dialogs:
            if isinstance(existing, cls) and existing.studio is studio:
                if not is_headless():
                    existing.show()
                    existing.raise_()
                    existing.activateWindow()
                return existing
        dialog = cls(studio)
        self._engineering_dialogs.append(dialog)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.destroyed.connect(lambda *_: self._engineering_dialogs.remove(dialog)
                                 if dialog in self._engineering_dialogs else None)
        if not is_headless():
            dialog.show()
        return dialog

    def _find_pvm(self) -> None:
        """Find: select every PVM whose path matches the text."""
        from azeo_control_trainer.core.presentation.headless import is_headless

        studio = self.current()
        if studio is None or is_headless():
            return
        text, ok = QInputDialog.getText(self, "Find", "Tag contains:")
        if not ok or not text.strip():
            return
        needle = text.strip().lower()
        studio.canvas.scene().clearSelection()
        for item in studio._items():
            if needle in str(item.pvm.params).lower():
                item.setSelected(True)

    def _insert_symbol(self, name: str | None) -> None:
        studio = self.current()
        if studio is None or not name or studio.mode == MODE_TEST:
            return
        try:
            center = studio.canvas.mapToScene(
                studio.canvas.viewport().rect().center())
            studio.add_static("symbol", x=center.x(), y=center.y(),
                              w=110, h=90, symbol=name)
        except DisplayLocked as error:
            QMessageBox.warning(self, "Display locked", str(error))

    def _align_selected(self) -> None:
        """Open alignment commands with an explicit key-object contract."""
        studio = self.current()
        if studio is None:
            return
        selected = studio.selection.editable_items()
        selected_pipes = studio.selected_pipe_items()
        from azeo_control_trainer.core.presentation.headless import is_headless
        if is_headless():
            if studio.can_align_pipe_endpoints(selected_pipes):
                studio.align_pipe_endpoints(selected_pipes)
            elif len(selected) >= 2:
                studio.align_selected("left")
            return
        from azeo_control_trainer.core.presentation.menu_style import \
            studio_menu

        menu = studio_menu(
            "ARRANGE",
            (f"{len(selected)} selected · last selected is the key object"
             if selected else "No editable elements selected"))

        def report(label, operation):
            moved = operation()
            if label == "Align Pipe Endpoints":
                message = (f"Aligned {moved} pipe endpoint pairs"
                           if moved else
                           "Select connected pipes with opposing ports")
            elif label == "Straighten Connected Run":
                message = (f"Straightened {moved} connected pipes"
                           if moved else
                           "Select an inline item with pipes on opposite sides")
            else:
                message = (f"{label}: {moved} selected elements aligned"
                           if moved else
                           f"{label}: already aligned or selection is locked")
            self.statusBar().showMessage(message, 4000)

        endpoints = menu.addAction("Align Pipe Endpoints (Straight Line)")
        endpoints.setToolTip(
            "Keep the first endpoint fixed and move connected equipment "
            "across the pipe axis until the authored ports line up")
        endpoints.setEnabled(studio.can_align_pipe_endpoints(selected_pipes))
        endpoints.triggered.connect(
            lambda _checked=False: report(
                "Align Pipe Endpoints", studio.align_pipe_endpoints))
        straighten = menu.addAction("Straighten Connected Run")
        straighten.setToolTip(
            "Select the inline valve or symbol between two pipes")
        straighten.setEnabled(studio.can_straighten_connected_run())
        straighten.triggered.connect(
            lambda _checked=False: report(
                "Straighten Connected Run",
                studio.straighten_connected_run))
        menu.addSeparator()

        for label, edge in (
                ("Align Left", "left"), ("Align Centers", "hcenter"),
                ("Align Right", "right"), ("Align Top", "top"),
                ("Align Middles", "vcenter"), ("Align Bottom", "bottom")):
            action = menu.addAction(label)
            action.setEnabled(len(selected) >= 2)
            action.triggered.connect(
                lambda _checked=False, text=label, value=edge:
                report(text, lambda: studio.align_selected(value)))
        menu.addSeparator()
        for label, axis in (("Distribute Horizontally", "h"),
                            ("Distribute Vertically", "v")):
            action = menu.addAction(label)
            action.setEnabled(len(selected) >= 3)
            action.triggered.connect(
                lambda _checked=False, text=label, value=axis:
                report(text, lambda: studio.distribute_selected(value)))
        page = menu.addMenu("Align to Page")
        page.setEnabled(bool(selected) and not studio.canvas.page_rect().isEmpty())
        for label, anchor in (("Left Safe Edge", "left"),
                              ("Horizontal Centre", "hcenter"),
                              ("Right Safe Edge", "right"),
                              ("Top Safe Edge", "top"),
                              ("Vertical Centre", "vcenter"),
                              ("Bottom Safe Edge", "bottom"),
                              ("Page Centre", "center")):
            page.addAction(label).triggered.connect(
                lambda _checked=False, text=label, value=anchor:
                report(text, lambda:
                       studio.align_selected_to_page(value)))
        self._align_menu = menu
        button = self._ribbon_buttons.get("arrange.align")
        origin = button.mapToGlobal(button.rect().bottomLeft()) \
            if button is not None else self.mapToGlobal(self.rect().center())
        menu.exec_transient(origin)

    def _zoom_cycle(self) -> None:
        index = (self.zoom.currentIndex() + 1) % self.zoom.count()
        self.zoom.setCurrentIndex(index)

    def _z_shift(self, direction: int) -> None:
        studio = self.current()
        if studio is None:
            return
        studio.z_shift(direction)

    def _toggle_edit(self) -> None:
        studio = self.current()
        if studio is None:
            return
        if studio.mode == MODE_EDIT:
            studio.leave_edit()
        else:
            self._request_edit(studio)
        self._sync_chrome()

    def _toggle_test(self) -> None:
        studio = self.current()
        if studio is None:
            return
        if studio.mode == MODE_TEST:
            studio.exit_test()
        else:
            studio.enter_test()
            self.test_data_pane.set_studio(studio)
            if not getattr(self, "_focus_mode", False):
                self.test_data_dock.show()

    def _publish(self) -> None:
        studio = self.current()
        if studio is None:
            return
        if studio.edited_user_class_name():
            return
        if studio.template_session:
            self.statusBar().showMessage(
                f"Templates are not published: make a display from "
                f"{studio.template_session} and publish that.", 10_000)
            return
        try:
            studio.open_publish()
        except PublishRefused as error:
            from azeo_control_trainer.core.presentation.headless import is_headless
            if is_headless():
                raise
            QMessageBox.warning(self, "Publish refused", str(error))

    def _quality_context_changed(self, *_args):
        studio = self.current()
        if studio is not None:
            studio.quality_theme = self.quality_theme.currentData()
            studio.quality_viewport = self.quality_viewport.currentData()
            self.quality_monitor.schedule()

    def _quality_status_changed(self, text):
        self._quality_segment.setText("Checks unavailable" if text.startswith("Checks unavailable:") else text)
        self._quality_segment.setToolTip(text + "\nClick to open Problems.")

    def _automatic_findings(self, studio, findings):
        if studio is self.current():
            self.problems_pane.set_problems(studio.display.name, findings)
            self.last_validation = [finding.message for finding in findings]

    def _fix_problem(self, finding):
        from .studio.quality import apply_visual_fix
        studio = self.current()
        if studio is None:
            return
        try:
            changed = apply_visual_fix(studio, finding)
            self.statusBar().showMessage("Layout corrected · Undo is available" if changed else "This finding is no longer current", 5000)
            self.quality_monitor.schedule()
        except Exception as error:
            log.exception("Problem correction failed")
            self.statusBar().showMessage(str(error), 8000)

    def _validate(self) -> None:
        studio = self.current()
        if studio is None:
            return
        class_name = studio.edited_user_class_name()
        findings = studio.verification_findings()
        problems = [finding.message for finding in findings]
        self.last_validation = problems
        from azeo_control_trainer.core.presentation.headless import is_headless
        self.problems_pane.set_problems(
            class_name or studio.display.name, findings)
        errors = sum(finding.blocks_publish for finding in findings)
        if findings:
            if not is_headless():
                self.problems_dock.show()
            self.statusBar().showMessage(
                f"Verify found {len(findings)} finding(s), "
                f"{errors} error(s) — "
                "double-click a row to locate its object", 8000)
        else:
            self.statusBar().showMessage(
                "Verify complete — no binding or configuration problems",
                5000)

    def _activate_problem(self, problem: str) -> None:
        """Select and reveal the object implicated by a Problems row."""
        studio = self.current()
        if studio is None or not problem:
            return
        from azeo_control_trainer.core.hmi.pvms.rendering.items import item_document_data
        token = self.problems_pane._object_path(problem) or problem
        candidates = studio._items() + studio._static_items() + studio._pipe_items()
        candidates.sort(key=lambda item: str(getattr(getattr(item, "pvm", None), "id", "") or item_document_data(item).get("id", "")) != token)
        match = None
        for item in candidates:
            pvm = getattr(item, "pvm", None)
            data = item_document_data(item)
            if str(getattr(pvm, "id", "") or data.get("id", "")) == token:
                match = item
                break
            haystack = str(pvm.params if pvm is not None else data)
            if token and token in haystack:
                match = item
                break
            if not token and problem in haystack:
                match = item
                break
        if match is None:
            self.statusBar().showMessage(
                "The problem belongs to the display or a missing class; "
                "there is no canvas object to select", 5000)
            return
        studio.selection.replace([match], primary=match)
        studio.canvas.centerOn(match)
        self.refresh_selection_pane()
        self.statusBar().showMessage("Problem object selected", 3500)

    def _show_reglog(self) -> None:
        from azeo_control_trainer.core.hmi.pvms.base import registry

        report = "\n".join(registry.registration_report())
        from azeo_control_trainer.core.presentation.headless import is_headless

        self.last_reglog = report
        if not is_headless():
            QMessageBox.information(self, "Registration log", report)

    def _count_instances(self, block_type: str) -> int:
        return sum(1 for studio in self.studios()
                   for item in studio._items()
                   if item.pvm.block_type == block_type)

    def _rebuild_nav(self) -> None:
        """The runtime navigation strip, from the hierarchy."""
        while self._nav_layout.count():
            item = self._nav_layout.takeAt(0)
            if item.widget() is not None:
                widget = item.widget()
                widget.hide()
                widget.deleteLater()
        studio = self.current()
        if studio is None:
            return
        hierarchy = self._display_hierarchy()
        current = studio.display.name

        def nav_button(text, target, bold=False):
            button = QPushButton(text)
            button.setStyleSheet(
                f"background: {'#FFFFFF' if bold else 'transparent'};"
                f"color: {WF['navy'] if bold else '#FFFFFF'};"
                f"border: 1px solid "
                f"{'#FFFFFF' if not bold else WF['navy']};"
                "padding: 2px 12px; font-size: 9pt;"
                + ("font-weight: 600;" if bold else ""))
            button.clicked.connect(
                lambda checked=False, n=target: self._nav_open(n))
            self._nav_layout.addWidget(button)

        overviews = [n for n, (lv, _p) in sorted(hierarchy.items())
                     if lv == 1]
        for name in overviews:
            nav_button(f"⌂ {name}", name, bold=(name == current))
        parent = hierarchy.get(current, (2, ""))[1]
        if parent:
            nav_button(f"↑ {parent}", parent)
        for name, (lv, par) in sorted(hierarchy.items()):
            if par == current:
                nav_button(name, name, bold=False)
            elif name == current and lv != 1:
                nav_button(name, name, bold=True)
        self._nav_layout.addStretch(1)

    def _nav_open(self, name: str) -> None:
        # TEST navigation must not briefly acquire an engineering lock.
        studio = self.open_display(name, edit=False)
        if studio is not None and studio.mode != MODE_TEST:
            studio.enter_test()
        self._rebuild_nav()

    # ---------------------------------------------------------------- tick
    def _sync_chrome(self) -> None:
        self._sync_recovery_status()
        studio = self.current()
        if studio is None:
            self.setWindowTitle(
                f"Azeo Graphics Designer — {self.area_name} — "
                "Azeo Control Trainer")
            return
        dirty = studio.unsaved
        class_name = studio.edited_user_class_name()
        document_name = class_name or studio.display.name
        class_kind = studio.user_library().entries.get(
            class_name, {}).get("definition_kind", "pvm") \
            if class_name else ""
        document_suffix = (
            f" · {str(class_kind).upper()} CLASS" if class_name else "")
        dirty_mark = " *" if dirty else ""
        self.setWindowTitle(
            f"Azeo Graphics Designer — {self.area_name} — "
            f"{document_name}{document_suffix}{dirty_mark} · "
            f"{studio.mode.upper()} — "
            "Azeo Control Trainer")
        self.dirty_chip.setText("● UNSAVED" if dirty else "● SAVED")
        self.dirty_chip.setProperty("dirty",
                                    "true" if dirty else "false")
        self.dirty_chip.style().unpolish(self.dirty_chip)
        self.dirty_chip.style().polish(self.dirty_chip)
        index = self.tabs.indexOf(studio)
        if index >= 0:
            tab_suffix = (" [Faceplate Class]" if class_kind == "faceplate"
                          else " [PVM Class]") if class_name else ""
            if studio.template_session:
                tab_suffix = " [Template]"
            self.tabs.setTabText(index, document_name + tab_suffix
                                 + (" *" if dirty else ""))
        publish_button = self._ribbon_buttons.get("display.publish")
        if publish_button is not None:
            publish_button.setEnabled(not class_name and not studio.template_session)
            publish_button.setToolTip(
                "Reusable classes are saved to the library. Publish each "
                "affected display after accepting the class change."
                if class_name else "Publish the active display revision.")
        test_button = self._ribbon_buttons.get("mode.test")
        if test_button is not None:
            test_button.setChecked(studio.mode == MODE_TEST)
        edit_button = self._ribbon_buttons.get("mode.edit.toggle")
        if edit_button is not None:
            edit_button.setChecked(studio.mode == MODE_EDIT)
        for action, state in (
                ("view.grid.toggle", studio.grid_visible),
                ("view.snap.toggle", studio.snap_enabled),
                ("view.guides.toggle", studio.smart_guides_enabled),
                ("view.rulers.toggle", studio.rulers_visible)):
            button = self._ribbon_buttons.get(action)
            if button is not None:
                button.setChecked(bool(state))
        studio.canvas_frame.update_tools()
        in_test = studio.mode == MODE_TEST
        if in_test and not self.nav_bar.isVisible():
            self._rebuild_nav()
        self.nav_bar.setVisible(in_test)

    def _tick(self) -> None:
        studio = self.current()
        if studio is None:
            return
        health = studio.health()
        history = studio.store.history(studio.display.name)
        rev = history[-1]["rev"] if history else 0
        self._segments["subs"].setText(f"SUBS {health['subs']}")
        self._segments["scan"].setText("SCAN 250 ms")
        self._segments["quality"].setText(
            f"{health['good']} GOOD / {health['bad']} BAD")
        self._segments["forced"].setText(f"{health['forced']} FORCED")
        self._segments["forced"].setProperty(
            "hot", "true" if health["forced"] else "false")
        self._segments["zoom"].setText(
            f"ZOOM {'FIT ' if studio.auto_fit_enabled else ''}"
            f"{studio.zoom_percent}%")
        self._set_geometry_status(studio.geometry_readout())
        self._rev_segment.setText(f"REV {rev} · DEV")
        self._sync_chrome()

    def _session_windows(self):
        from shiboken6 import isValid
        yield self
        for window in self._library_windows:
            if isValid(window) and not window._session_closed:
                yield from window._session_windows()

    def _confirm_session_close(self, windows):
        # A released top-level editor also releases the windows it retains.
        # Collect all library documents in one batch so a late Cancel cannot
        # discard an earlier document. Configuration Discard only hides its
        # editor; restore that editor if the overall close is cancelled.
        from shiboken6 import isValid
        configurations = []
        for window in windows:
            config = getattr(window, "pvm_config_designer", None)
            if config is not None and isValid(config) and config.isVisible():
                active = config._preview_timer.isActive()
                if not config.close():
                    break
                configurations.append((config, active))
        else:
            if self._confirm_studios_close(
                    [studio for window in windows for studio in window.studios()]):
                return True
        for config, active in configurations:
            config.show()
            if active:
                config._preview_timer.start()
        return False

    def closeEvent(self, event) -> None:            # noqa: N802
        if self._session_closed:
            event.accept()
            return
        log.info("Graphics Designer close requested: area=%s", self.area_name)
        windows = list(self._session_windows())
        if not getattr(self, "_session_close_approved", False) \
                and not self._confirm_session_close(windows):
            log.info("Graphics Designer close cancelled: area=%s", self.area_name)
            event.ignore()
            return
        for window in windows[1:]:
            window._session_close_approved = True
        for window in windows[1:]:
            if not window._session_closed:
                window.close()
                window.deleteLater()
        self._library_windows.clear()
        self.quality_monitor.set_studio(None)
        if hasattr(self, "quick_online"):
            self.quick_online.close()
        self._timer.stop()
        self._save_workspace()
        for studio in self.studios():
            studio.close()
        self._session_closed = True
        self.closed.emit()
        log.info("Graphics Designer session closed: area=%s", self.area_name)
        super().closeEvent(event)
