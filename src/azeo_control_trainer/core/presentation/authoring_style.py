"""Shared authoring control states, independent of the Windows accent setting."""
from pathlib import Path

from .brand import AUTHORING_BLUE, AUTHORING_HOVER, UI

_ASSETS = Path(__file__).with_name("assets").as_posix()

AUTHORING_FIELDS_QSS = f"""
QLineEdit, QComboBox, QAbstractSpinBox {{ background: {UI.pane}; border: 1px solid {UI.field_border};
    border-radius: 6px; padding: 5px 10px; min-height: 20px; selection-color: white; }}
QLineEdit:hover, QComboBox:hover, QAbstractSpinBox:hover {{ border-color: {UI.field_hover}; }}
QLineEdit:focus, QComboBox:focus, QComboBox:on, QAbstractSpinBox:focus {{ border-color: {UI.blue}; }}
QComboBox {{ padding-right: 34px; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right;
    width: 32px; border: none; border-top-right-radius: 6px; border-bottom-right-radius: 6px;
    background: transparent; }}
QComboBox::drop-down:hover {{ background: {UI.hover}; }}
QComboBox::down-arrow {{ image: url("{_ASSETS}/chevron-down.svg"); width: 16px; height: 16px; }}
QComboBox::down-arrow:on {{ image: url("{_ASSETS}/chevron-up.svg"); }}
QComboBox QAbstractItemView {{ background: {UI.pane}; border: none; padding: 4px;
    selection-background-color: {UI.selection}; selection-color: {UI.blue}; outline: none; }}
QComboBox QAbstractItemView::item {{ min-height: 20px; padding: 4px 10px;
    border-radius: 4px; margin: 1px 0; }}
QComboBox QAbstractItemView::item:hover {{ background: {UI.hover}; }}
QAbstractSpinBox {{ padding-right: 28px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-origin: border;
    subcontrol-position: top right; width: 26px; border: none; border-top-right-radius: 6px;
    background: transparent; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-origin: border;
    subcontrol-position: bottom right; width: 26px; border: none; border-bottom-right-radius: 6px;
    background: transparent; }}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{ background: {UI.hover}; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url("{_ASSETS}/chevron-up.svg"); width: 12px; height: 12px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url("{_ASSETS}/chevron-down.svg"); width: 12px; height: 12px; }}
QAbstractSpinBox QLineEdit, QComboBox QLineEdit {{ border: none; padding: 0; min-height: 0; }}
QPlainTextEdit {{ background: {UI.pane}; color: {UI.text};
    border: 1px solid {UI.field_border}; border-radius: 6px; padding: 5px; }}
QPlainTextEdit:focus {{ border-color: {UI.blue}; }}
"""

AUTHORING_CONTROLS_QSS = f"""
QWidget {{ selection-background-color: {AUTHORING_BLUE};
           selection-color: white; }}
QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{ width: 16px; height: 16px;
    border: 1px solid {UI.disabled}; border-radius: 3px; background: {UI.pane}; }}
QCheckBox::indicator:hover, QCheckBox::indicator:focus {{
    border-color: {AUTHORING_BLUE}; background: {AUTHORING_HOVER}; }}
QCheckBox::indicator:checked, QCheckBox::indicator:indeterminate {{
    border-color: {AUTHORING_BLUE}; background: {AUTHORING_BLUE}; }}
QCheckBox::indicator:checked {{ image: url("{_ASSETS}/check.svg"); }}
QCheckBox::indicator:indeterminate {{ image: url("{_ASSETS}/indeterminate.svg"); }}
QCheckBox::indicator:disabled {{ border-color: {UI.border}; background: {UI.chrome}; }}
QCheckBox::indicator:checked:disabled,
QCheckBox::indicator:indeterminate:disabled {{ background: {UI.disabled}; }}
""" + AUTHORING_FIELDS_QSS

AUTHORING_CHROME_QSS = f"""
QWidget {{ font-family: "Segoe UI"; font-size: 9.5pt; color: {UI.text}; }}
QMainWindow, QDialog {{ background: {UI.page}; }}
QWidget#engineering_pane {{ background: {UI.pane}; }}
QMenuBar {{ background: {UI.blue}; color: {UI.on_blue}; padding: 1px 5px;
    border-bottom: 1px solid {UI.menu_pressed}; font-size: 9pt; }}
QMenuBar::item {{ padding: 6px 11px; background: transparent;
    border-bottom: 2px solid transparent; }}
QMenuBar::item:selected {{ background: {UI.menu_hover};
    color: {UI.on_blue}; border-bottom-color: {UI.selection}; }}
QMenuBar::item:pressed {{ background: {UI.menu_pressed};
    color: {UI.on_blue}; border-bottom-color: {UI.on_blue}; }}
QStatusBar {{ background: {UI.chrome}; color: {UI.text_secondary};
    border-top: 1px solid {UI.border}; font-size: 9pt; }}
QStatusBar::item {{ border: none; }}
QToolBar {{ background: {UI.pane}; border: none;
    border-bottom: 1px solid {UI.border}; padding: 4px 6px; spacing: 2px; }}
QToolButton, QPushButton {{ background: {UI.pane}; color: {UI.text};
    border: 1px solid {UI.border_light}; border-radius: 4px; padding: 5px 8px; }}
QToolButton:hover, QPushButton:hover {{ background: {UI.hover}; border-color: {UI.blue}; }}
QToolButton:checked, QPushButton:checked {{ background: {UI.selection}; border-color: {UI.blue}; }}
QToolButton:focus, QPushButton:focus {{ border-color: {UI.blue}; }}
QToolButton:disabled, QPushButton:disabled {{ color: {UI.disabled}; }}
QLineEdit, QAbstractSpinBox, QComboBox {{ background: {UI.pane}; color: {UI.text};
    border: 1px solid {UI.border}; border-radius: 4px; padding: 4px 7px;
    min-height: 20px; }}
QLineEdit:focus, QAbstractSpinBox:focus, QComboBox:focus {{ border-color: {UI.blue}; }}
QAbstractSpinBox QLineEdit, QComboBox QLineEdit {{
    border: none; padding: 0; min-height: 0; background: transparent; }}
QLineEdit:disabled, QAbstractSpinBox:disabled, QComboBox:disabled {{
    background: {UI.chrome}; color: {UI.disabled}; }}
QComboBox QAbstractItemView {{ background: {UI.pane}; color: {UI.text};
    border: 1px solid {UI.border}; selection-background-color: {UI.selection};
    selection-color: {UI.blue}; outline: none; }}
QTreeWidget, QTreeView, QTableWidget, QListWidget {{ background: {UI.pane};
    alternate-background-color: {UI.chrome}; color: {UI.text};
    border: none; outline: none; }}
QTreeView::item {{ min-height: 24px; padding: 2px 5px; }}
QTreeView::item:hover, QTableView::item:hover, QListView::item:hover {{ background: {UI.hover}; }}
QTreeView::item:selected, QTableView::item:selected, QListView::item:selected {{
    background: {UI.selection}; color: {UI.blue}; }}
QHeaderView::section {{ background: {UI.chrome}; color: {UI.text_secondary};
    border: none; border-bottom: 1px solid {UI.border};
    border-right: 1px solid {UI.border_light}; padding: 5px 8px; font-weight: 600; }}
QTabWidget::pane {{ background: {UI.pane}; border: none; }}
QTabBar::tab {{ background: {UI.chrome}; color: {UI.text_secondary};
    border: none; border-bottom: 2px solid transparent; padding: 5px 10px; font-size: 9pt; }}
QTabBar::tab:hover {{ background: {UI.hover}; color: {UI.blue}; }}
QTabBar::tab:selected {{ background: {UI.pane}; color: {UI.blue};
    border-bottom-color: {UI.blue}; font-weight: 600; }}
QSplitter::handle {{ background: {UI.border_light}; }}
QSplitter::handle:hover {{ background: {UI.blue}; }}
QScrollArea {{ border: none; }}
QToolTip {{ background: {UI.pane}; color: {UI.text}; border: 1px solid {UI.border}; padding: 6px; }}
""" + AUTHORING_CONTROLS_QSS
