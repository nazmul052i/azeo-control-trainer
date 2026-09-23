"""Common UI building blocks shared across all dashboard tabs.

Re-exports the most frequently used symbols so that downstream code
can do::

    from azeo_control_trainer.core.presentation import Colors, BaseTab, TAG_REGISTRY
"""

from .hmi_theme import Colors, HMIColors, TREND_PALETTE, get_stylesheet, get_plot_colors
from .tab_base import BaseTab
from .user_role import Role, RoleManager, role_manager
from .kpi_card import KpiCard
from .help_content import HELP_HTML, TE_HELP_HTML
from .tag_registry import (
    TagMeta,
    TAG_REGISTRY,
    VALUE_PRESETS_BY_CATEGORY,
    build_tag_registry,
    build_value_presets,
)
from .live_data_provider import LiveDataProvider
from .tag_browser import TagBrowser
from .alarm_sound import AlarmSounder

__all__ = [
    # Theme
    "Colors",
    "HMIColors",
    "TREND_PALETTE",
    "get_stylesheet",
    "get_plot_colors",
    # Base tab
    "BaseTab",
    # User roles
    "Role",
    "RoleManager",
    "role_manager",
    # KPI card
    "KpiCard",
    # Help
    "HELP_HTML",
    "TE_HELP_HTML",
    # Tag registry
    "TagMeta",
    "TAG_REGISTRY",
    "VALUE_PRESETS_BY_CATEGORY",
    "build_tag_registry",
    "build_value_presets",
    # Live data
    "LiveDataProvider",
    # Tag browser
    "TagBrowser",
    # Alarm sound
    "AlarmSounder",
]
