"""Silver Industrial Theme — Brushed-aluminum aesthetic with ISA-101 process colors.

Instantiate SilverTheme() and call .to_qss() for the global stylesheet.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SilverTheme:
    """Silver/aluminum industrial theme constants."""

    # Backgrounds — brushed aluminum progression
    BG_CANVAS: str = "#E4E7EC"
    BG_PANEL: str = "#EBECF1"
    BG_TOOLBAR: str = "#D0D2DB"
    BG_INSET: str = "#DBDBE0"
    BG_INPUT: str = "#F5F6FA"
    BG_WINDOW: str = "#E0E2EB"

    # Metallic accents
    CHROME_LIGHT: str = "#F0F2F6"
    CHROME_MID: str = "#C8CDD8"
    CHROME_DARK: str = "#8A92A8"
    CHROME_BORDER: str = "#9AA5B4"

    # Text
    TEXT_PRIMARY: str = "#1A1C24"
    TEXT_SECONDARY: str = "#4A5068"
    TEXT_DISABLED: str = "#9AA5B4"
    TEXT_ON_ACCENT: str = "#FFFFFF"

    # ISA-101 process colors
    PV_BLUE: str = "#3C6291"
    SP_TAN: str = "#CDC2B6"
    OUT_TEAL: str = "#14696A"

    # State indicators
    STATE_RUNNING: str = "#2D8E3C"
    STATE_STOPPED: str = "#9AA5B4"
    STATE_FAULT: str = "#E8272C"
    STATE_MANUAL: str = "#E8C822"

    # Accent
    ACCENT: str = "#2B5EA7"
    ACCENT_HOVER: str = "#3A72C0"
    ACCENT_PRESSED: str = "#1E4A8A"

    # Alarm hierarchy (ISA-18.2)
    ALARM_CRITICAL: str = "#FF0000"
    ALARM_WARNING: str = "#FFFF00"
    ALARM_ADVISORY: str = "#6F3198"

    def to_qss(self) -> str:
        """Generate the complete Qt stylesheet."""
        return f"""
        /* ══════════════════════════════════════════════════════════
           Process Simulator — Silver Industrial Theme
           ══════════════════════════════════════════════════════════ */

        * {{
            font-family: "Segoe UI", "Tahoma", sans-serif;
            font-size: 9pt;
        }}

        QMainWindow {{
            background-color: {self.BG_WINDOW};
        }}

        QWidget {{
            background-color: {self.BG_PANEL};
            color: {self.TEXT_PRIMARY};
        }}

        /* ── Menu Bar ── */
        QMenuBar {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 {self.CHROME_LIGHT}, stop:1 {self.BG_TOOLBAR});
            color: {self.TEXT_PRIMARY};
            border-bottom: 1px solid {self.CHROME_BORDER};
            padding: 2px 0;
        }}
        QMenuBar::item {{
            padding: 4px 10px;
            background: transparent;
        }}
        QMenuBar::item:selected {{
            background: {self.CHROME_MID};
            border-radius: 3px;
        }}
        QMenu {{
            background: {self.BG_PANEL};
            color: {self.TEXT_PRIMARY};
            border: 1px solid {self.CHROME_BORDER};
        }}
        QMenu::item {{
            padding: 5px 28px 5px 12px;
        }}
        QMenu::item:selected {{
            background: {self.ACCENT};
            color: {self.TEXT_ON_ACCENT};
        }}
        QMenu::separator {{
            height: 1px;
            background: {self.CHROME_MID};
            margin: 4px 8px;
        }}

        /* ── Toolbar ── */
        QToolBar {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 {self.CHROME_LIGHT}, stop:0.5 {self.BG_TOOLBAR},
                stop:1 {self.CHROME_MID});
            border-bottom: 1px solid {self.CHROME_BORDER};
            spacing: 3px;
            padding: 2px;
        }}
        QToolButton {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 {self.CHROME_LIGHT}, stop:1 {self.BG_TOOLBAR});
            border: 1px solid {self.CHROME_BORDER};
            border-radius: 3px;
            padding: 4px 8px;
            color: {self.TEXT_PRIMARY};
        }}
        QToolButton:hover {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #FFFFFF, stop:1 {self.CHROME_LIGHT});
            border-color: {self.ACCENT};
        }}
        QToolButton:pressed {{
            background: {self.BG_INSET};
            border-color: {self.CHROME_DARK};
        }}
        QToolButton:checked {{
            background: {self.BG_INSET};
            border: 1px solid {self.ACCENT};
        }}

        /* ── Push Buttons ── */
        QPushButton {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 {self.CHROME_LIGHT}, stop:1 {self.BG_TOOLBAR});
            border: 1px solid {self.CHROME_BORDER};
            border-radius: 3px;
            padding: 5px 16px;
            color: {self.TEXT_PRIMARY};
            min-height: 20px;
        }}
        QPushButton:hover {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #FFFFFF, stop:1 {self.CHROME_LIGHT});
            border-color: {self.ACCENT};
        }}
        QPushButton:pressed {{
            background: {self.BG_INSET};
        }}
        QPushButton:disabled {{
            color: {self.TEXT_DISABLED};
            background: {self.BG_PANEL};
            border-color: {self.CHROME_MID};
        }}

        /* ── Inputs ── */
        QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
            background: {self.BG_INPUT};
            border: 1px solid {self.CHROME_BORDER};
            border-radius: 2px;
            padding: 3px 6px;
            color: {self.TEXT_PRIMARY};
            selection-background-color: {self.ACCENT};
            selection-color: {self.TEXT_ON_ACCENT};
        }}
        QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
            border-color: {self.ACCENT};
        }}
        /* Hide the old-style native up/down spinner arrows everywhere */
        QSpinBox::up-button, QSpinBox::down-button,
        QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
            width: 0px;
            height: 0px;
            border: none;
            margin: 0px;
            subcontrol-position: right;
        }}
        QSpinBox::up-arrow, QSpinBox::down-arrow,
        QDoubleSpinBox::up-arrow, QDoubleSpinBox::down-arrow {{
            width: 0px;
            height: 0px;
            image: none;
        }}
        QComboBox::drop-down {{
            border-left: 1px solid {self.CHROME_BORDER};
            width: 20px;
        }}
        QComboBox QAbstractItemView {{
            background: {self.BG_INPUT};
            border: 1px solid {self.CHROME_BORDER};
            selection-background-color: {self.ACCENT};
            selection-color: {self.TEXT_ON_ACCENT};
        }}

        /* ── Tab Widget ── */
        QTabWidget::pane {{
            border: 1px solid {self.CHROME_BORDER};
            background: {self.BG_PANEL};
        }}
        QTabBar::tab {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 {self.CHROME_LIGHT}, stop:1 {self.BG_TOOLBAR});
            border: 1px solid {self.CHROME_BORDER};
            border-bottom: none;
            padding: 5px 14px;
            margin-right: 1px;
            border-top-left-radius: 3px;
            border-top-right-radius: 3px;
            color: {self.TEXT_SECONDARY};
        }}
        QTabBar::tab:selected {{
            background: {self.BG_PANEL};
            border-bottom: 1px solid {self.BG_PANEL};
            color: {self.TEXT_PRIMARY};
            font-weight: bold;
        }}
        QTabBar::tab:hover:!selected {{
            background: {self.CHROME_LIGHT};
        }}

        /* ── Splitter ── */
        QSplitter::handle {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 {self.CHROME_MID}, stop:0.5 {self.CHROME_LIGHT},
                stop:1 {self.CHROME_MID});
            width: 4px;
        }}
        QSplitter::handle:horizontal {{
            width: 4px;
        }}
        QSplitter::handle:vertical {{
            height: 4px;
        }}

        /* ── Tree / List Views ── */
        QTreeView, QListView, QTableView, QTreeWidget, QListWidget, QTableWidget {{
            background: {self.BG_INPUT};
            border: 1px solid {self.CHROME_BORDER};
            alternate-background-color: {self.BG_PANEL};
            selection-background-color: {self.ACCENT};
            selection-color: {self.TEXT_ON_ACCENT};
        }}
        QTreeView::item:hover, QListView::item:hover {{
            background: {self.CHROME_LIGHT};
        }}
        QHeaderView::section {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 {self.CHROME_LIGHT}, stop:1 {self.BG_TOOLBAR});
            border: 1px solid {self.CHROME_BORDER};
            padding: 4px;
            color: {self.TEXT_PRIMARY};
        }}

        /* ── Scroll Bars ── */
        QScrollBar:vertical {{
            background: {self.BG_INSET};
            width: 12px;
            border: none;
        }}
        QScrollBar::handle:vertical {{
            background: {self.CHROME_MID};
            border-radius: 4px;
            min-height: 24px;
            margin: 2px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {self.CHROME_DARK};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px;
        }}
        QScrollBar:horizontal {{
            background: {self.BG_INSET};
            height: 12px;
            border: none;
        }}
        QScrollBar::handle:horizontal {{
            background: {self.CHROME_MID};
            border-radius: 4px;
            min-width: 24px;
            margin: 2px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background: {self.CHROME_DARK};
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0px;
        }}

        /* ── Status Bar ── */
        QStatusBar {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 {self.BG_TOOLBAR}, stop:1 {self.CHROME_MID});
            color: {self.TEXT_SECONDARY};
            border-top: 1px solid {self.CHROME_BORDER};
            font-size: 8pt;
        }}

        /* ── Group Box ── */
        QGroupBox {{
            border: 1px solid {self.CHROME_BORDER};
            border-radius: 4px;
            margin-top: 8px;
            padding-top: 12px;
            background: {self.BG_PANEL};
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 4px;
            color: {self.TEXT_SECONDARY};
        }}

        /* ── Tooltips ── */
        QToolTip {{
            background: #E8EBF0;
            color: {self.TEXT_PRIMARY};
            border: 1px solid {self.CHROME_BORDER};
            border-radius: 3px;
            padding: 6px 8px;
            font-size: 8.5pt;
            font-family: 'Segoe UI';
        }}

        /* ── Dialog ── */
        QDialog {{
            background: {self.BG_PANEL};
        }}

        /* ── Progress Bar ── */
        QProgressBar {{
            background: {self.BG_INSET};
            border: 1px solid {self.CHROME_BORDER};
            border-radius: 3px;
            text-align: center;
            color: {self.TEXT_PRIMARY};
        }}
        QProgressBar::chunk {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 {self.ACCENT}, stop:1 {self.ACCENT_HOVER});
            border-radius: 2px;
        }}

        /* ── Check / Radio ── */
        QCheckBox, QRadioButton {{
            spacing: 6px;
            color: {self.TEXT_PRIMARY};
        }}
        QCheckBox::indicator, QRadioButton::indicator {{
            width: 14px;
            height: 14px;
        }}

        /* ── Label ── */
        QLabel {{
            color: {self.TEXT_PRIMARY};
            background: transparent;
        }}

        /* ── Slider ── */
        QSlider::groove:horizontal {{
            background: {self.CHROME_MID};
            height: 4px;
            border-radius: 2px;
        }}
        QSlider::handle:horizontal {{
            background: {self.ACCENT};
            width: 14px;
            margin: -5px 0;
            border-radius: 7px;
        }}
        QSlider::handle:horizontal:hover {{
            background: {self.ACCENT_HOVER};
        }}

        /* ── Text Browser / Editor ── */
        QTextBrowser, QTextEdit, QPlainTextEdit {{
            background: {self.BG_INPUT};
            color: {self.TEXT_PRIMARY};
            border: 1px solid {self.CHROME_BORDER};
        }}
        """
