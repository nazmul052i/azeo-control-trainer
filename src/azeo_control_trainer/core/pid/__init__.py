"""
PID Widget Package — Reusable PID control UI components.

Drop-in PID faceplate, trend dialog, and ISA PID function block
for PySide6 process control applications.

Usage:
    from azeo_control_trainer.core.pid import PIDBlock, Mode, InlineDynamo, FaceplatePopup
    from azeo_control_trainer.core.pid import ControllerDetailDialog, PidTrendDialog
    from azeo_control_trainer.core.pid import HistorianTrendWidget, TrendPen

Requires: PySide6, pyqtgraph, numpy
"""

# Core PID engine (always available — no Qt dependency)
from .core.pid_block_core import (
    PIDBlock,
    Mode,
    PIDForm,
    Structure,
    LimitStatus,
    SignalStatus,
    ScaleRange,
    BlockError,
    AlarmState,
    validate_pid_config,
)

# UI components are imported lazily so the core can be used without Qt.
# Import them explicitly when needed:
#   from azeo_control_trainer.core.pid.theme.colors import *
#   from azeo_control_trainer.core.pid.theme.stylesheet import MAIN_STYLESHEET
#   from azeo_control_trainer.core.pid.charts import HistorianTrendWidget, TrendPen
#   from azeo_control_trainer.core.pid.widgets import InlineDynamo, FaceplatePopup, ...


def __getattr__(name: str):
    """Lazy import for Qt-dependent components."""
    _THEME_NAMES = {
        "MAIN_STYLESHEET",
        "BG_MAIN", "BG_PANEL", "BG_INSET", "BG_RAISED", "BG_DARK",
        "BG_FACEPLATE", "BG_TREND", "BG_TOOLBAR",
        "TEXT_PRIMARY", "TEXT_SECONDARY", "TEXT_DISABLED", "TEXT_HEADER",
        "TEXT_ON_DARK",
        "TREND_COLORS",
    }
    _CHART_NAMES = {"HistorianTrendWidget", "TrendPen"}
    _WIDGET_NAMES = {
        "CombinationBarGraph", "OutBarGraph", "HorizontalOutBar",
        "DigitalReadout", "ModeIndicator", "ModeLamp",
        "InlineDynamo", "FaceplatePopup",
        "ControllerDetailDialog", "PidTrendDialog", "PID_TREND_MAP",
    }

    if name == "MAIN_STYLESHEET":
        from .theme.stylesheet import MAIN_STYLESHEET
        return MAIN_STYLESHEET
    if name in _THEME_NAMES:
        from .theme import colors
        return getattr(colors, name)
    if name in _CHART_NAMES:
        from .charts import historian_trend
        return getattr(historian_trend, name)
    if name in _WIDGET_NAMES:
        if name == "PID_TREND_MAP":
            from .widgets.trend_dialog import PID_TREND_MAP
            return PID_TREND_MAP
        from .widgets import faceplate_full
        return getattr(faceplate_full, name)

    raise AttributeError(f"module 'azeo_control_trainer.core.pid' has no attribute {name!r}")


__all__ = [
    # Core (always available)
    "PIDBlock", "Mode", "PIDForm", "Structure",
    "LimitStatus", "SignalStatus", "ScaleRange",
    "BlockError", "AlarmState", "validate_pid_config",
    # Theme (lazy)
    "MAIN_STYLESHEET", "TREND_COLORS",
    # Charts (lazy — requires PySide6 + pyqtgraph)
    "HistorianTrendWidget", "TrendPen",
    # Widgets (lazy — requires PySide6)
    "CombinationBarGraph", "OutBarGraph", "HorizontalOutBar",
    "DigitalReadout", "ModeIndicator", "ModeLamp",
    "InlineDynamo", "FaceplatePopup",
    "ControllerDetailDialog", "PidTrendDialog",
    "PID_TREND_MAP",
]
