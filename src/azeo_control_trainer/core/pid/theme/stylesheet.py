"""ISA-101 Silver Theme — Qt Stylesheet (QSS)."""

from .colors import (
    BG_MAIN, BG_PANEL, BG_INSET, BG_RAISED, BG_TREND, BG_TOOLBAR,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_DISABLED, TEXT_HEADER, TEXT_ON_DARK,
    DV_PV_BG, DV_SP_WORK, DV_PV_FG, DV_PV_LEVEL_BG,
    DV_ALARM_BAR1, DV_ALARM_BAR2, DV_STATUS_BORDER,
    DV_EQUIP_DARK3,
)

MAIN_STYLESHEET = f"""
/* === Global === */
QWidget {{
    background-color: {BG_MAIN};
    color: {TEXT_PRIMARY};
    font-family: "Segoe UI", "Arial", sans-serif;
    font-size: 10pt;
}}

/* === Main Window === */
QMainWindow {{
    background-color: {BG_MAIN};
}}

/* === Menu Bar === */
QMenuBar {{
    background-color: {DV_ALARM_BAR2};
    color: {TEXT_ON_DARK};
    font-size: 9pt;
    padding: 2px;
    border-bottom: 1px solid {DV_EQUIP_DARK3};
}}
QMenuBar::item {{
    padding: 4px 10px;
    background: transparent;
}}
QMenuBar::item:selected {{
    background-color: {DV_EQUIP_DARK3};
}}
QMenu {{
    background-color: {BG_RAISED};
    color: {TEXT_PRIMARY};
    border: 1px solid {DV_ALARM_BAR2};
}}
QMenu::item {{
    padding: 5px 22px 5px 22px;
}}
QMenu::item:selected {{
    background-color: {DV_PV_FG};
    color: white;
}}
QMenu::separator {{
    height: 1px;
    background: {DV_ALARM_BAR1};
    margin: 2px 8px;
}}

/* === Toolbar === */
QToolBar {{
    background-color: {BG_TOOLBAR};
    border: none;
    border-bottom: 1px solid {DV_ALARM_BAR1};
    spacing: 3px;
    padding: 2px 4px;
}}
QToolButton {{
    background-color: {BG_RAISED};
    border: 1px solid {DV_ALARM_BAR1};
    border-radius: 2px;
    padding: 3px 6px;
    font-size: 9pt;
    color: {TEXT_SECONDARY};
}}
QToolButton:hover {{
    background-color: {DV_PV_LEVEL_BG};
    border-color: {DV_PV_FG};
}}
QToolButton:pressed {{
    background-color: {DV_ALARM_BAR1};
    border-color: {DV_PV_FG};
}}
QToolButton:checked {{
    background-color: {DV_PV_LEVEL_BG};
    border-color: {DV_PV_FG};
}}

/* === Status Bar === */
QStatusBar {{
    background-color: {DV_ALARM_BAR2};
    color: {TEXT_ON_DARK};
    border-top: 1px solid {DV_EQUIP_DARK3};
    font-size: 8pt;
}}
QStatusBar::item {{
    border: none;
}}
QLabel#statusLabel {{
    color: {TEXT_ON_DARK};
    padding: 0 6px;
}}

/* === Tab Widget === */
QTabWidget::pane {{
    border: 1px solid {DV_ALARM_BAR1};
    background-color: {BG_PANEL};
}}
QTabBar::tab {{
    background-color: {BG_TOOLBAR};
    border: 1px solid {DV_ALARM_BAR1};
    border-bottom: none;
    padding: 5px 14px;
    font-size: 9pt;
    min-width: 80px;
    color: {TEXT_SECONDARY};
}}
QTabBar::tab:selected {{
    background-color: {BG_PANEL};
    font-weight: bold;
    border-top: 2px solid {DV_STATUS_BORDER};
    color: {TEXT_PRIMARY};
}}
QTabBar::tab:hover:!selected {{
    background-color: {DV_PV_LEVEL_BG};
}}

/* === Group Box === */
QGroupBox {{
    border: 1px solid {DV_ALARM_BAR1};
    border-radius: 3px;
    margin-top: 14px;
    padding: 6px 4px 4px 4px;
    font-size: 9pt;
    font-weight: bold;
    background-color: {BG_PANEL};
    color: {TEXT_SECONDARY};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 8px;
    padding: 0 4px;
    color: {TEXT_HEADER};
    background-color: {BG_PANEL};
}}

/* === Push Button === */
QPushButton {{
    background-color: {BG_RAISED};
    border: 1px solid {DV_ALARM_BAR1};
    border-radius: 3px;
    padding: 4px 12px;
    font-size: 9pt;
    min-height: 22px;
    color: {TEXT_SECONDARY};
}}
QPushButton:hover {{
    background-color: {DV_PV_LEVEL_BG};
    border-color: {DV_PV_FG};
}}
QPushButton:pressed {{
    background-color: {DV_ALARM_BAR1};
    border-color: {DV_PV_FG};
    padding-top: 5px;
}}
QPushButton:disabled {{
    background-color: {BG_INSET};
    color: {TEXT_DISABLED};
}}

/* === Spin Box / Double Spin Box === */
QDoubleSpinBox, QSpinBox {{
    background-color: {BG_INSET};
    border: 1px solid {DV_ALARM_BAR1};
    border-radius: 2px;
    padding: 2px 4px;
    font-family: "Consolas", "Courier New", monospace;
    font-size: 9pt;
    selection-background-color: {DV_PV_FG};
    color: {TEXT_PRIMARY};
}}
QDoubleSpinBox:focus, QSpinBox:focus {{
    border-color: {DV_STATUS_BORDER};
    background-color: #F0F2F8;
}}

/* === Combo Box === */
QComboBox {{
    background-color: {BG_RAISED};
    border: 1px solid {DV_ALARM_BAR1};
    border-radius: 2px;
    padding: 2px 6px;
    font-size: 9pt;
    min-height: 22px;
    color: {TEXT_PRIMARY};
}}
QComboBox::drop-down {{
    border: none;
    width: 18px;
}}
QComboBox QAbstractItemView {{
    background-color: {BG_RAISED};
    selection-background-color: {DV_PV_FG};
    color: {TEXT_PRIMARY};
}}

/* === CheckBox === */
QCheckBox {{
    spacing: 5px;
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {DV_ALARM_BAR1};
    background-color: {BG_INSET};
    border-radius: 2px;
}}
QCheckBox::indicator:checked {{
    background-color: {DV_PV_FG};
    border-color: {DV_EQUIP_DARK3};
}}

/* === Table Widget === */
QTableWidget {{
    background-color: {BG_INSET};
    alternate-background-color: {BG_PANEL};
    gridline-color: {DV_ALARM_BAR1};
    border: 1px solid {DV_ALARM_BAR1};
    font-size: 9pt;
    color: {TEXT_PRIMARY};
}}
QTableWidget QHeaderView::section {{
    background-color: {DV_ALARM_BAR2};
    color: {TEXT_ON_DARK};
    padding: 3px 6px;
    border: none;
    border-right: 1px solid {DV_EQUIP_DARK3};
    font-size: 9pt;
}}

/* === Scroll Bar === */
QScrollBar:vertical {{
    width: 12px;
    background: {BG_INSET};
}}
QScrollBar::handle:vertical {{
    background: {DV_ALARM_BAR1};
    min-height: 20px;
    border-radius: 3px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

/* === Splitter === */
QSplitter::handle {{
    background-color: {DV_ALARM_BAR1};
}}
QSplitter::handle:horizontal {{
    width: 4px;
}}
QSplitter::handle:vertical {{
    height: 4px;
}}
"""
