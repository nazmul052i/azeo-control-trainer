"""
ISA-101 Silver Theme -- HMI Color Palette & Stylesheet

Based on the ISA-101 Themes white paper (August 2016).
Replaces the previous ISA-101 theme with ISA-101 Silver Theme colors
following the information priority hierarchy:
  1. Alarms (bright saturated -- Red, Yellow, Purple)
  2. Abnormal status (saturated accent -- Pumpkin, Cyan, Blue)
  3. Process values / information (readable, mid-contrast)
  4. Display information (subtle, cool, non-distracting)
"""

from __future__ import annotations
from dataclasses import dataclass

from azeo_control_trainer.core.pid.theme.colors import (
    # Base colors
    BG_MAIN, BG_PANEL, BG_INSET, BG_RAISED, BG_DARK, BG_FACEPLATE,
    BG_TREND, BG_TOOLBAR,
    # Text
    TEXT_PRIMARY as DV_TEXT_PRIMARY, TEXT_SECONDARY as DV_TEXT_SECONDARY,
    TEXT_DISABLED as DV_TEXT_DISABLED, TEXT_HEADER, TEXT_ON_DARK,
    # Dynamo
    DV_PV_BG, DV_SP_WORK, DV_PV_FG, DV_PV_LEVEL_BG,
    DV_OUT_FG, DV_OUT_BG, DV_ALARM_BAR1, DV_ALARM_BAR2,
    DV_STATUS_BORDER, DV_MODULE_SELECT,
    # Equipment
    DV_PIPE, DV_EQUIP, DV_EQUIP_OUTLINE, DV_EQUIP_DARK1,
    DV_EQUIP_DARK2, DV_EQUIP_DARK3, DV_TEXT_ON_TANK,
    DV_EQUIP_OUTLINE_DYN, DV_EQUIP_ON, DV_EQUIP_OFF,
    # Process variable colors
    COLOR_PV, COLOR_SP, COLOR_OP, COLOR_LEVEL, COLOR_STEAM,
    COLOR_GAS, COLOR_AIR, COLOR_O2, COLOR_PRESS, COLOR_FWT, COLOR_FW,
    # Alarms
    ALARM_CRITICAL, ALARM_HIGH, ALARM_WARN, ALARM_ADVISORY,
    ALARM_INFO, ALARM_OK,
    # Control status
    STATUS_AUTO, STATUS_MAN, STATUS_CAS, STATUS_OOS, STATUS_INIT,
    # Trend
    TREND_COLORS,
    # Navigation
    DV_ARROW1, DV_ARROW2, DV_TITLE_BG, DV_TITLE_OUTLINE, DV_CTRL_LINE,
)


@dataclass(frozen=True)
class HMIColors:
    """ISA-101 Silver Theme Color Palette.

    All field names from the previous ISA-101 theme are preserved for
    backward compatibility but now map to ISA-101 Silver Theme values.
    """

    # =========================================================================
    # BACKGROUNDS  (ISA-101 Silver Theme)
    # =========================================================================
    BG_PRIMARY: str = BG_MAIN               # #E0E2EB -- Picture background
    BG_SECONDARY: str = BG_PANEL            # #EBECF1 -- Dynamo / panel bg
    BG_PLOT: str = BG_TREND                 # #192F3D -- Dark navy trend bg
    BG_DIALOG: str = BG_PANEL               # #EBECF1
    BG_INPUT: str = "#FFFFFF"               # Editable text fields
    BG_INPUT_DISABLED: str = BG_INSET       # #DBDBE0
    BG_METER: str = DV_PV_BG               # #DFD7CC -- Light tan
    BG_STATUS_BAR: str = DV_ALARM_BAR2      # #7B92AD -- Dark grey-blue
    BG_TOOLBAR: str = BG_TOOLBAR             # #D0D2DB -- Toolbar bg
    BG_INSET: str = BG_INSET                 # #DBDBE0 -- Inset/recessed bg

    # =========================================================================
    # TEXT COLORS  (ISA)
    # =========================================================================
    TEXT_PRIMARY: str = DV_TEXT_PRIMARY      # #000000 -- Black
    TEXT_SECONDARY: str = DV_TEXT_SECONDARY  # #0E3260 -- Dark blue
    TEXT_DISABLED: str = DV_TEXT_DISABLED    # #9BADC6 -- Grey-blue
    TEXT_LIVE: str = "#000000"              # Real-time values
    TEXT_STATIC: str = DV_TEXT_SECONDARY    # #0E3260
    TEXT_LINK: str = DV_STATUS_BORDER       # #0000C6 -- Blue

    # =========================================================================
    # EQUIPMENT STATES  (kept for existing tabs)
    # =========================================================================
    STATE_RUNNING: str = "#2E8B2E"          # Running/Open -- Green
    STATE_RUNNING_BORDER: str = "#1E5B1E"
    STATE_STOPPED: str = DV_EQUIP_OFF       # #B4B9C8 -- Silver-grey
    STATE_STOPPED_BORDER: str = "#707074"
    STATE_TRANSITION: str = "#DAA520"       # Transition -- Gold
    STATE_TRANSITION_BORDER: str = "#AA7510"
    STATE_MANUAL: str = STATUS_MAN          # #9D4F00 -- Pumpkin (abnormal)
    STATE_MANUAL_BORDER: str = "#7D3F00"
    STATE_FAULT: str = ALARM_CRITICAL       # #FF0000 -- Red
    STATE_FAULT_BORDER: str = "#CC0000"
    STATE_SCHEDULED: str = "#606060"
    STATE_SCHEDULED_BORDER: str = "#404040"
    STATE_MAINTENANCE: str = ALARM_ADVISORY  # #6F3198 -- Purple
    STATE_MAINTENANCE_BORDER: str = "#4F1178"

    # =========================================================================
    # STATIC EQUIPMENT  (ISA-101 theme)
    # =========================================================================
    EQUIP_OUTLINE: str = DV_EQUIP_OUTLINE   # #192F3D -- Dark navy
    EQUIP_FILL: str = DV_EQUIP              # #B2C7D5 -- Light blue-gray
    PIPE_OUTLINE: str = DV_PIPE             # #6B8DAF -- Muted blue

    # =========================================================================
    # ALARM COLORS  (ISA -- sacred, not reused for decoration)
    # =========================================================================
    ALARM_CRITICAL: str = ALARM_CRITICAL     # #FF0000 -- Red
    ALARM_CRITICAL_BORDER: str = "#CC0000"
    ALARM_HIGH: str = ALARM_CRITICAL         # #FF0000 -- Red (ISA HI=critical)
    ALARM_HIGH_BORDER: str = "#CC0000"
    ALARM_MEDIUM: str = ALARM_WARN           # #FFFF00 -- Yellow (warning)
    ALARM_MEDIUM_BORDER: str = "#CCA300"
    ALARM_LOW: str = ALARM_ADVISORY          # #6F3198 -- Purple (advisory)
    ALARM_LOW_BORDER: str = "#4F1178"
    ALARM_DIAGNOSTIC: str = ALARM_ADVISORY   # #6F3198 -- Purple
    ALARM_DIAGNOSTIC_BORDER: str = "#4F1178"

    # =========================================================================
    # CONTROL MODE INDICATORS  (ISA-101 philosophy)
    # =========================================================================
    MODE_AUTO: str = STATUS_AUTO             # #9BADC6 -- Grey-blue (normal, muted)
    MODE_MANUAL: str = STATUS_MAN            # #9D4F00 -- Pumpkin (abnormal)
    MODE_CASCADE: str = STATUS_CAS           # #0000C6 -- Blue
    MODE_LOCAL: str = "#DAA520"              # Gold (local override)

    # =========================================================================
    # PROCESS VARIABLE INDICATORS  (ISA)
    # =========================================================================
    PV_NORMAL: str = DV_PV_FG               # #3C6291 -- Dark blue
    PV_HIGH_WARN: str = ALARM_WARN           # #FFFF00 -- Yellow
    PV_HIGH_ALARM: str = ALARM_CRITICAL      # #FF0000 -- Red
    PV_LOW_WARN: str = ALARM_WARN            # #FFFF00 -- Yellow
    PV_LOW_ALARM: str = ALARM_CRITICAL       # #FF0000 -- Red
    PV_BAR_BG: str = DV_PV_BG               # #DFD7CC -- Light tan
    SP_INDICATOR: str = DV_SP_WORK           # #CDC2B6 -- Dark tan

    # =========================================================================
    # PLOT/TREND COLORS  (ISA palette)
    # =========================================================================
    PLOT_PV: str = COLOR_PV                  # #3C6291 -- Dark blue
    PLOT_SP: str = COLOR_SP                  # #CDC2B6 -- Tan
    PLOT_OUT: str = COLOR_OP                 # #14696A -- Blue-green
    PLOT_ERROR: str = ALARM_ADVISORY         # #6F3198 -- Purple
    PLOT_P_TERM: str = "#CD853F"             # Peru
    PLOT_I_TERM: str = "#4682B4"             # Steel Blue
    PLOT_D_TERM: str = "#9ACD32"             # Yellow Green
    PLOT_GRID_MAJOR: str = DV_CTRL_LINE      # #C6C6C6
    PLOT_GRID_MINOR: str = BG_INSET          # #DBDBE0
    PLOT_AXIS: str = DV_EQUIP_OUTLINE        # #192F3D
    PLOT_AXIS_LABEL: str = DV_TEXT_SECONDARY  # #0E3260

    # =========================================================================
    # BUTTON COLORS -- Standard  (ISA-101 theme)
    # =========================================================================
    BTN_NORMAL: str = BG_RAISED              # #EBECF1
    BTN_NORMAL_TEXT: str = DV_TEXT_SECONDARY  # #0E3260
    BTN_NORMAL_BORDER: str = DV_ALARM_BAR1   # #9BADC6
    BTN_HOVER: str = DV_PV_LEVEL_BG          # #CBD9E2
    BTN_HOVER_BORDER: str = DV_PV_FG         # #3C6291
    BTN_PRESSED: str = DV_ALARM_BAR1         # #9BADC6
    BTN_PRESSED_BORDER: str = DV_PV_FG       # #3C6291
    BTN_DISABLED: str = BG_INSET             # #DBDBE0
    BTN_DISABLED_TEXT: str = DV_TEXT_DISABLED  # #9BADC6
    BTN_DISABLED_BORDER: str = DV_ALARM_BAR1  # #9BADC6
    BTN_FOCUS_BORDER: str = DV_STATUS_BORDER  # #0000C6

    # =========================================================================
    # BUTTON COLORS -- Action
    # =========================================================================
    BTN_START: str = "#2E8B2E"
    BTN_START_BORDER: str = "#1E5B1E"
    BTN_STOP: str = "#B22222"
    BTN_STOP_BORDER: str = "#8B0000"
    BTN_RESET: str = "#DAA520"
    BTN_RESET_BORDER: str = "#AA7510"
    BTN_APPLY: str = DV_STATUS_BORDER        # #0000C6 -- Blue
    BTN_APPLY_BORDER: str = "#0000A6"
    BTN_ACTION_TEXT: str = "#FFFFFF"

    # =========================================================================
    # TOGGLE BUTTON COLORS
    # =========================================================================
    TOGGLE_OFF: str = DV_ALARM_BAR1          # #9BADC6
    TOGGLE_OFF_TEXT: str = DV_TEXT_SECONDARY  # #0E3260
    TOGGLE_OFF_BORDER: str = DV_ALARM_BAR2   # #7B92AD
    TOGGLE_ON: str = "#2E8B2E"
    TOGGLE_ON_TEXT: str = "#FFFFFF"
    TOGGLE_ON_BORDER: str = "#1E5B1E"

    # =========================================================================
    # SLIDER COLORS
    # =========================================================================
    SLIDER_TRACK: str = DV_ALARM_BAR1        # #9BADC6
    SLIDER_FILL: str = DV_PV_FG             # #3C6291
    SLIDER_HANDLE: str = BG_RAISED           # #EBECF1
    SLIDER_HANDLE_BORDER: str = DV_ALARM_BAR2  # #7B92AD
    SLIDER_HANDLE_HOVER: str = DV_PV_LEVEL_BG  # #CBD9E2

    # =========================================================================
    # SPINBOX COLORS
    # =========================================================================
    SPINBOX_BG: str = "#FFFFFF"
    SPINBOX_BORDER: str = DV_ALARM_BAR1      # #9BADC6
    SPINBOX_ARROW: str = BG_RAISED           # #EBECF1
    SPINBOX_ARROW_HOVER: str = DV_PV_LEVEL_BG  # #CBD9E2

    # =========================================================================
    # STATUS INDICATORS
    # =========================================================================
    STATUS_RUNNING: str = "#2E8B2E"
    STATUS_PAUSED: str = "#DAA520"
    STATUS_STOPPED: str = DV_EQUIP_OFF       # #B4B9C8
    STATUS_ERROR: str = ALARM_CRITICAL       # #FF0000
    STATUS_TEXT: str = "#FFFFFF"

    # =========================================================================
    # COMMUNICATION STATUS
    # =========================================================================
    COMM_OK: str = "#2E8B2E"
    COMM_DISCONNECTED: str = ALARM_CRITICAL   # #FF0000
    COMM_TIMEOUT: str = "#DAA520"


# Singleton instance
Colors = HMIColors()


@dataclass(frozen=True)
class DarkColors:
    """ISA-101 Dark / Night theme colors."""

    BG_PRIMARY: str = "#1E1E1E"
    BG_SECONDARY: str = "#252526"
    BG_PLOT: str = "#0D1117"
    BG_DIALOG: str = "#252526"
    BG_INPUT: str = "#2D2D30"
    BG_INPUT_DISABLED: str = "#3E3E42"
    BG_METER: str = "#2D2D30"
    BG_STATUS_BAR: str = "#1E1E1E"
    BG_TOOLBAR: str = "#2D2D30"
    BG_INSET: str = "#2D2D30"

    TEXT_PRIMARY: str = "#D4D4D4"
    TEXT_SECONDARY: str = "#9BADC6"
    TEXT_DISABLED: str = "#6B7B8D"
    TEXT_LIVE: str = "#D4D4D4"
    TEXT_STATIC: str = "#9BADC6"
    TEXT_LINK: str = "#0078D4"

    STATE_RUNNING: str = "#2E8B2E"
    STATE_RUNNING_BORDER: str = "#1E5B1E"
    STATE_STOPPED: str = "#6B7B8D"
    STATE_STOPPED_BORDER: str = "#4A5568"
    STATE_TRANSITION: str = "#DAA520"
    STATE_TRANSITION_BORDER: str = "#AA7510"
    STATE_MANUAL: str = "#9D4F00"
    STATE_MANUAL_BORDER: str = "#7D3F00"
    STATE_FAULT: str = "#FF4444"
    STATE_FAULT_BORDER: str = "#CC0000"
    STATE_SCHEDULED: str = "#606060"
    STATE_SCHEDULED_BORDER: str = "#404040"
    STATE_MAINTENANCE: str = "#8B5CF6"
    STATE_MAINTENANCE_BORDER: str = "#6D28D9"

    EQUIP_OUTLINE: str = "#4A5568"
    EQUIP_FILL: str = "#3E3E42"
    PIPE_OUTLINE: str = "#6B7B8D"

    ALARM_CRITICAL: str = "#FF4444"
    ALARM_CRITICAL_BORDER: str = "#CC0000"
    ALARM_HIGH: str = "#FF4444"
    ALARM_HIGH_BORDER: str = "#CC0000"
    ALARM_MEDIUM: str = "#FFB800"
    ALARM_MEDIUM_BORDER: str = "#CC9300"
    ALARM_LOW: str = "#8B5CF6"
    ALARM_LOW_BORDER: str = "#6D28D9"
    ALARM_DIAGNOSTIC: str = "#8B5CF6"
    ALARM_DIAGNOSTIC_BORDER: str = "#6D28D9"

    MODE_AUTO: str = "#9BADC6"
    MODE_MANUAL: str = "#9D4F00"
    MODE_CASCADE: str = "#0078D4"
    MODE_LOCAL: str = "#DAA520"

    PV_NORMAL: str = "#4A90D9"
    PV_HIGH_WARN: str = "#FFB800"
    PV_HIGH_ALARM: str = "#FF4444"
    PV_LOW_WARN: str = "#FFB800"
    PV_LOW_ALARM: str = "#FF4444"
    PV_BAR_BG: str = "#2D2D30"
    SP_INDICATOR: str = "#6B7B8D"

    PLOT_PV: str = "#4A90D9"
    PLOT_SP: str = "#CDC2B6"
    PLOT_OUT: str = "#14696A"
    PLOT_ERROR: str = "#8B5CF6"
    PLOT_P_TERM: str = "#CD853F"
    PLOT_I_TERM: str = "#4682B4"
    PLOT_D_TERM: str = "#9ACD32"
    PLOT_GRID_MAJOR: str = "#3E3E42"
    PLOT_GRID_MINOR: str = "#2D2D30"
    PLOT_AXIS: str = "#9BADC6"
    PLOT_AXIS_LABEL: str = "#9BADC6"

    BTN_NORMAL: str = "#3E3E42"
    BTN_NORMAL_TEXT: str = "#D4D4D4"
    BTN_NORMAL_BORDER: str = "#555555"
    BTN_HOVER: str = "#4E4E52"
    BTN_HOVER_BORDER: str = "#6B7B8D"
    BTN_PRESSED: str = "#094771"
    BTN_PRESSED_BORDER: str = "#0078D4"
    BTN_DISABLED: str = "#2D2D30"
    BTN_DISABLED_TEXT: str = "#6B7B8D"
    BTN_DISABLED_BORDER: str = "#3E3E42"
    BTN_FOCUS_BORDER: str = "#0078D4"

    BTN_START: str = "#2E8B2E"
    BTN_START_BORDER: str = "#1E5B1E"
    BTN_STOP: str = "#B22222"
    BTN_STOP_BORDER: str = "#8B0000"
    BTN_RESET: str = "#DAA520"
    BTN_RESET_BORDER: str = "#AA7510"
    BTN_APPLY: str = "#0078D4"
    BTN_APPLY_BORDER: str = "#005A9E"
    BTN_ACTION_TEXT: str = "#FFFFFF"

    TOGGLE_OFF: str = "#3E3E42"
    TOGGLE_OFF_TEXT: str = "#9BADC6"
    TOGGLE_OFF_BORDER: str = "#555555"
    TOGGLE_ON: str = "#2E8B2E"
    TOGGLE_ON_TEXT: str = "#FFFFFF"
    TOGGLE_ON_BORDER: str = "#1E5B1E"

    SLIDER_TRACK: str = "#3E3E42"
    SLIDER_FILL: str = "#0078D4"
    SLIDER_HANDLE: str = "#D4D4D4"
    SLIDER_HANDLE_BORDER: str = "#6B7B8D"
    SLIDER_HANDLE_HOVER: str = "#FFFFFF"

    SPINBOX_BG: str = "#2D2D30"
    SPINBOX_BORDER: str = "#3E3E42"
    SPINBOX_ARROW: str = "#3E3E42"
    SPINBOX_ARROW_HOVER: str = "#4E4E52"

    STATUS_RUNNING: str = "#2E8B2E"
    STATUS_PAUSED: str = "#DAA520"
    STATUS_STOPPED: str = "#6B7B8D"
    STATUS_ERROR: str = "#FF4444"
    STATUS_TEXT: str = "#FFFFFF"

    COMM_OK: str = "#2E8B2E"
    COMM_DISCONNECTED: str = "#FF4444"
    COMM_TIMEOUT: str = "#DAA520"


# ---------------------------------------------------------------------------
# Dark Theme Global Stylesheet
# ---------------------------------------------------------------------------
DARK_STYLESHEET = """
QMainWindow, QWidget { background-color: #1E1E1E; color: #D4D4D4; }
QTabWidget::pane { background: #252526; border: 1px solid #3E3E42; }
QTabBar::tab { background: #2D2D30; color: #D4D4D4; padding: 6px 16px; border: 1px solid #3E3E42; }
QTabBar::tab:selected { background: #1E1E1E; border-bottom: 2px solid #0078D4; }
QMenuBar { background: #2D2D30; color: #D4D4D4; }
QMenuBar::item:selected { background: #3E3E42; }
QMenu { background: #252526; color: #D4D4D4; border: 1px solid #3E3E42; }
QMenu::item:selected { background: #094771; }
QStatusBar { background: #1E1E1E; color: #D4D4D4; border-top: 1px solid #3E3E42; }
QPushButton { background: #3E3E42; color: #D4D4D4; border: 1px solid #555; padding: 4px 12px; border-radius: 3px; }
QPushButton:hover { background: #4E4E52; }
QPushButton:pressed { background: #094771; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: #2D2D30; color: #D4D4D4; border: 1px solid #3E3E42; padding: 3px; }
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { width: 0px; height: 0px; border: none; }
QSpinBox::up-arrow, QSpinBox::down-arrow, QDoubleSpinBox::up-arrow, QDoubleSpinBox::down-arrow { width: 0px; height: 0px; image: none; }
QTableWidget { background: #1E1E1E; color: #D4D4D4; gridline-color: #3E3E42; }
QHeaderView::section { background: #2D2D30; color: #D4D4D4; border: 1px solid #3E3E42; padding: 4px; }
QSplitter::handle { background: #3E3E42; }
QScrollBar { background: #1E1E1E; }
QScrollBar::handle { background: #3E3E42; border-radius: 4px; }
QToolBar { background: #2D2D30; border: none; }
QGroupBox { border: 1px solid #3E3E42; color: #D4D4D4; margin-top: 8px; }
QGroupBox::title { color: #9BADC6; }
QSlider::groove:horizontal { background: #3E3E42; height: 4px; }
QSlider::handle:horizontal { background: #0078D4; width: 12px; margin: -4px 0; border-radius: 6px; }
QLabel { color: #D4D4D4; }
QTreeWidget { background: #1E1E1E; color: #D4D4D4; }
QTreeWidget::item:selected { background: #094771; }
QListWidget { background: #1E1E1E; color: #D4D4D4; }
QListWidget::item:selected { background: #094771; }
QCheckBox { color: #D4D4D4; }
QRadioButton { color: #D4D4D4; }
QTextBrowser { background: #1E1E1E; color: #D4D4D4; }
QDialog { background: #1E1E1E; color: #D4D4D4; }
QToolTip { background: #252526; color: #D4D4D4; border: 1px solid #3E3E42; }
"""


# ---------------------------------------------------------------------------
# Theme Manager
# ---------------------------------------------------------------------------
class ThemeManager:
    """Manages switching between Silver (light) and Dark (night) themes."""

    import threading as _threading

    _dark_mode: bool = False
    _listeners: list = []
    _lock = _threading.Lock()

    @classmethod
    def is_dark(cls) -> bool:
        return cls._dark_mode

    @classmethod
    def toggle(cls):
        with cls._lock:
            cls._dark_mode = not cls._dark_mode
            listeners = list(cls._listeners)
        for cb in listeners:
            try:
                cb(cls._dark_mode)
            except Exception as e:
                import logging
                logging.getLogger("ui.theme").warning("Theme listener error: %s", e)

    @classmethod
    def set_dark(cls, dark: bool):
        with cls._lock:
            if cls._dark_mode == dark:
                return
            cls._dark_mode = dark
            listeners = list(cls._listeners)
        for cb in listeners:
            try:
                cb(cls._dark_mode)
            except Exception as e:
                import logging
                logging.getLogger("ui.theme").warning("Theme listener error: %s", e)

    @classmethod
    def add_listener(cls, callback):
        with cls._lock:
            cls._listeners.append(callback)

    @classmethod
    def remove_listener(cls, callback):
        with cls._lock:
            try:
                cls._listeners.remove(callback)
            except ValueError:
                pass

    @classmethod
    def current_colors(cls):
        """Return the active color palette instance."""
        return DarkColors() if cls._dark_mode else Colors


# ISA-101 8-color trend palette + extended ISA colors
TREND_PALETTE = list(TREND_COLORS) + [
    "#DC143C",  # Crimson
    "#20B2AA",  # Light Sea Green
    "#DAA520",  # Goldenrod
    "#6A5ACD",  # Slate Blue
    "#FF69B4",  # Hot Pink
    "#00CED1",  # Dark Turquoise
    "#B22222",  # Firebrick
    "#3CB371",  # Medium Sea Green
]


def get_stylesheet() -> str:
    """ISA-101 Silver Theme Qt stylesheet — brushed-aluminum industrial look."""
    if ThemeManager.is_dark():
        return DARK_STYLESHEET
    from azeo_control_trainer.core.presentation.silver_theme import SilverTheme
    return SilverTheme().to_qss()


def get_plot_colors() -> dict:
    """Return plot-specific colors for pyqtgraph configuration."""
    c = ThemeManager.current_colors()
    return {
        'pv': c.PLOT_PV,
        'sp': c.PLOT_SP,
        'out': c.PLOT_OUT,
        'error': c.PLOT_ERROR,
        'p_term': c.PLOT_P_TERM,
        'i_term': c.PLOT_I_TERM,
        'd_term': c.PLOT_D_TERM,
        'background': c.BG_PLOT,
        'grid_major': c.PLOT_GRID_MAJOR,
        'grid_minor': c.PLOT_GRID_MINOR,
        'axis': c.PLOT_AXIS,
        'axis_label': c.PLOT_AXIS_LABEL,
    }


# Neutral badge background (stopped / off states)
BADGE_GRAY = "#808080"


def badge_style(bg: str) -> str:
    """Stylesheet for the small status-bar badges used across the shell.

    Single source of truth — do not inline badge stylesheets in windows.
    """
    return (
        "QLabel { background: %s; color: white; padding: 1px 6px; "
        "border-radius: 3px; font-weight: bold; font-size: 8pt; }" % bg
    )
