"""Visual theme.

The P&ID drawings follow classic drafting style: a light grey sheet, equipment
drawn as heavy white outlines, process pipes in saturated blue with filled flow
arrows, instrument signals as thin dashed black lines, and ISA-5.1 symbols in
black on white. Saturated warning colour is still reserved for abnormal
conditions - a trip ring, a lost flame, an off-limit value - so live status
reads at a glance against the otherwise monochrome-plus-blue sheet.

Magenta marks bad quality. It is unmistakable against the grey without
competing with the alarm colours.
"""

from __future__ import annotations

from pathlib import Path
from PySide6.QtGui import QColor, QFont

# --------------------------------------------------------------------- surfaces
BACKGROUND = QColor("#E8E9E8")
PANEL = QColor("#D6D6D6")
CANVAS_GRID = QColor("#C6C6C6")

# -------------------------------------------------------------------- equipment
OUTLINE = QColor("#1E1E1E")
EQUIP_WHITE = QColor("#FFFFFF")
EQUIP_FILL = QColor("#FAFAFA")
EQUIP_FILL_DARK = QColor("#C8C8C8")
# Vessel bodies sit just off the sheet colour and are separated from it by a
# heavy white stroke, which is what gives the drawings their depth.
EQUIP_BODY = QColor("#D9DCDD")
EQUIP_EDGE = QColor("#777D81")
LIQUID = QColor("#8FA8BF")
LIQUID_DARK = QColor("#6E8AA5")
FLAME = QColor("#E8960F")
INSULATION = QColor("#CFCFCF")

# ------------------------------------------------------------------------ lines
PROCESS_LINE = QColor("#2033CC")
UTILITY_LINE = QColor("#5A5A5A")
SIGNAL_LINE = QColor("#3A3A3A")

# ------------------------------------------------------------------ status hues
NORMAL_TEXT = QColor("#1A1A1A")
MUTED_TEXT = QColor("#5A5A5A")
RUNNING = QColor("#2F6B3A")
STOPPED = QColor("#8A8A8A")
ALARM_CRITICAL = QColor("#C1272D")
ALARM_HIGH = QColor("#E8A317")
ALARM_ADVISORY = QColor("#3B6FA8")
BAD_QUALITY = QColor("#B0179B")
UNCERTAIN = QColor("#8A6D1F")
OVERRIDE = QColor("#7A3FA8")

VALUE_BG = QColor("#FFFFFF")
VALUE_BORDER = QColor("#4A4A4A")


def font(size: int = 9, bold: bool = False, mono: bool = False) -> QFont:
    f = QFont()
    f.setFamilies(["Consolas", "DejaVu Sans Mono"] if mono
                  else ["Segoe UI", "Arial", "DejaVu Sans"])
    f.setPointSize(size)
    f.setBold(bold)
    return f


def quality_colour(quality: int) -> QColor:
    return {0: NORMAL_TEXT, 1: UNCERTAIN, 2: BAD_QUALITY}.get(int(quality), NORMAL_TEXT)


# The application chrome is modern-flat: light neutral surfaces, one blue
# accent, rounded controls, slim scrollbars. Only the chrome - the process
# displays keep their drafting-sheet look, painted from the constants above.
# No font-family rules here: a stylesheet font would override the mono fonts
# the tables set programmatically.
STYLESHEET = """
QMainWindow, QDialog { background: #D9DBDE; }
QWidget { background: #D9DBDE; color: #1A1C1E; }

QMenuBar { background: #F4F5F7; border-bottom: 1px solid #C9CCD1;
    padding: 2px 6px; }
QMenuBar::item { padding: 4px 12px; margin: 1px 2px;
    background: transparent; border-radius: 5px; }
QMenuBar::item:selected { background: #DCE6F2; }
QMenuBar::item:pressed { background: #C9DAEE; }
QMenu { background: #FDFDFE; border: 1px solid #B9BDC4; padding: 5px; }
QMenu::item { padding: 5px 32px 5px 12px; border-radius: 4px;
    margin: 1px 3px; background: transparent; }
QMenu::item:selected { background: #DCE6F2; }
QMenu::item:disabled { color: #9AA0A8; }
QMenu::separator { height: 1px; background: #E2E4E8; margin: 5px 10px; }
QMenu::indicator { width: 14px; height: 14px; margin-left: 6px; }

QToolBar { background: #F4F5F7; border-bottom: 1px solid #C9CCD1;
    padding: 3px 6px; spacing: 3px; }
QToolBar::separator { background: #D5D8DC; width: 1px; margin: 4px 4px; }
QToolButton { padding: 4px 10px; border-radius: 5px;
    border: 1px solid transparent; background: transparent; }
QToolButton:hover { background: #E2E8F0; border-color: #C6D2E0; }
QToolButton:pressed { background: #CFDCEA; }

QStatusBar { background: #F4F5F7; border-top: 1px solid #C9CCD1; }
QStatusBar::item { border: none; }
QStatusBar QLabel { background: transparent; padding: 0 6px; }

QTabWidget::pane { border: none; border-top: 1px solid #C9CCD1; }
QTabBar { background: transparent; }
QTabBar::tab { background: transparent; padding: 6px 16px; border: none;
    border-bottom: 2px solid transparent; color: #4A4E54; margin: 0 1px; }
QTabBar::tab:selected { color: #17385C; border-bottom: 2px solid #3D6FA5; }
QTabBar::tab:hover:!selected { background: #E4E8ED; color: #1A1C1E; }

QDockWidget { titlebar-close-icon: none; }
QDockWidget::title { background: #E9EBEE; padding: 5px 8px;
    border-bottom: 1px solid #C9CCD1; }

QTreeWidget, QTableWidget, QPlainTextEdit, QTextBrowser, QListWidget {
    background: #FCFCFD; border: 1px solid #C5C9CE;
    selection-background-color: #CFE0F1; selection-color: #1A1C1E; }
QHeaderView::section { background: #EFF1F4; border: none;
    border-right: 1px solid #E2E4E8; border-bottom: 1px solid #C9CCD1;
    padding: 4px 6px; }

QGroupBox { border: 1px solid #C5C9CE; border-radius: 5px;
    margin-top: 9px; padding-top: 6px; background: transparent; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }

QPushButton { background: #FFFFFF; border: 1px solid #B9BDC4;
    border-radius: 5px; padding: 4px 12px; min-height: 18px; }
QPushButton:hover { background: #F0F4F9; border-color: #8FA6C0; }
QPushButton:pressed { background: #DCE6F2; }
QPushButton:checked { background: #DCE6F2; border-color: #3D6FA5; }
QPushButton:focus { border-color: #3D6FA5; }
QPushButton:disabled { color: #9AA0A8; background: #ECEDEF;
    border-color: #D5D8DC; }
QToolButton:checked { background: #DCE6F2; border-color: #3D6FA5; }

QDoubleSpinBox, QSpinBox, QLineEdit, QComboBox {
    background: #FFFFFF; border: 1px solid #B9BDC4; border-radius: 4px;
    padding: 3px 6px; min-height: 18px;
    selection-background-color: #CFE0F1; selection-color: #1A1C1E; }
QLineEdit[placeholderText] { color: #1A1C1E; }
QDoubleSpinBox:focus, QSpinBox:focus, QLineEdit:focus, QComboBox:focus {
    border-color: #3D6FA5; }
QComboBox::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView { background: #FDFDFE;
    border: 1px solid #B9BDC4; selection-background-color: #DCE6F2;
    selection-color: #1A1C1E; }
QCheckBox { spacing: 6px; }
QCheckBox::indicator, QGroupBox::indicator {
    width: 14px; height: 14px; border: 1px solid #B9BDC4;
    border-radius: 3px; background: #FFFFFF; }
QCheckBox::indicator:hover { border-color: #8FA6C0; }
QCheckBox::indicator:checked, QGroupBox::indicator:checked {
    background: #3D6FA5; border-color: #2F5A88; }
QCheckBox::indicator:disabled { background: #ECEDEF;
    border-color: #D5D8DC; }
QRadioButton::indicator { width: 13px; height: 13px;
    border: 1px solid #B9BDC4; border-radius: 7px; background: #FFFFFF; }
QRadioButton::indicator:checked { background: #3D6FA5;
    border: 3px solid #FFFFFF; outline: 1px solid #3D6FA5; }

QStatusBar QLabel { padding: 0 8px;
    border-left: 1px solid #DFE2E6; }
QStatusBar QLabel:first-child { border-left: none; }

#navBand { background: #F4F5F7; border-bottom: 1px solid #C9CCD1; }
#navBand QLabel { background: transparent; }
#navBand QToolButton { border: 1px solid transparent; border-radius: 5px;
    padding: 3px 8px; background: transparent; }
#navBand QToolButton:hover { background: #E2E8F0; border-color: #C6D2E0; }
#navBand QToolButton:pressed { background: #CFDCEA; }
#navBand QToolButton::menu-indicator { image: none; }
QLabel#navCrumb { color: #17385C; font-weight: 600; padding: 0 10px; }
QLabel#modeChip { border-radius: 9px; padding: 2px 12px;
    font-weight: 700; }
#alarmBanner { background: #EFF1F4;
    border-bottom: 1px solid #C9CCD1; }
#alarmBanner QLabel { background: transparent; }

QScrollBar:vertical { background: transparent; width: 11px; margin: 0; }
QScrollBar:horizontal { background: transparent; height: 11px; margin: 0; }
QScrollBar::handle { background: #B4B9C0; border-radius: 4px; margin: 2px; }
QScrollBar::handle:vertical { min-height: 30px; }
QScrollBar::handle:horizontal { min-width: 30px; }
QScrollBar::handle:hover { background: #97A0AA; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QSplitter::handle { background: #C9CCD1; }
QToolTip { background: #FFFFF2; color: #1A1C1E;
    border: 1px solid #B9BDC4; padding: 3px 6px; }
"""

# Generated UI assets keep the standalone distribution independent of the
# trainer package. Process colors and painter fonts above remain local roles.
_CHROME = Path(__file__).with_name("chrome")
STYLESHEET += (_CHROME / "authoring.qss").read_text(encoding="utf-8").replace(
    "@ASSETS@", _CHROME.as_posix())
