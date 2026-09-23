"""High Performance PVM anatomy — the parts every HP PVM shares.

All High Performance PVM classes carry the same outer furniture, so it lives
here once rather than in each painter:

    display tag  ·  alarm box / status box  ·  alarm icon
    status icons  ·  alarm count  ·  bar graphs  ·  hover window

Split four ways because the pieces answer different questions and are
tested differently:

- `state`  — what is showing, and the precedence rules that decide it
- `marks`  — the box, the icons, the count
- `bars`   — combination, deviation and OUT bar graphs
- `tag`    — display tag, EU descriptor, data-field formatting
- `hover`  — the pop-up window's content

`PvmItem.paint` calls into this after the class's own painter has run,
so the box surrounds the finished PVM rather than being painted over by
it. Nothing here knows which PVM class it is decorating — that is what
makes it shared furniture rather than 28 near-copies.
"""
from importlib import import_module

# Keep package import Qt-free. `pvms.__init__` imports declaration
# submodules through this package, and eagerly importing the painters
# here pulled PySide into tools that only inspect the registry (I2).
# Public names stay unchanged and load their owning module on first use.
_EXPORTS = {
    "OUT_TICKS": "bars", "BarData": "bars", "BarScale": "bars",
    "deviation_fraction": "bars", "draw_combination_bar": "bars",
    "draw_deviation_bar": "bars", "draw_out_bar": "bars",
    "resolve_scale": "bars",
    "hover_lines": "hover", "hover_text": "hover",
    "BOX_WIDTH": "marks", "CONDITION_GLYPHS": "marks",
    "CONDITION_TIPS": "marks", "ICON_GAP": "marks",
    "ICON_SIZE": "marks", "UNACKED_EXTRA": "marks",
    "box_stroke": "marks", "draw_alarm_box": "marks",
    "draw_alarm_count": "marks", "draw_alarm_icon": "marks",
    "draw_status_icons": "marks", "draw_status_indicator": "marks",
    "ADVISORY": "state", "CRITICAL": "state",
    "ICON_ACTIVE_ACKED": "state", "ICON_ACTIVE_UNACKED": "state",
    "ICON_INACTIVE_UNACKED": "state", "ICON_SUPPRESSED": "state",
    "PRIORITY_ROLES": "state", "STATUS_CONDITIONS": "state",
    "WARNING": "state", "AlarmBoxState": "state",
    "resolve_alarm_box": "state",
    "FIELD_WIDTH": "tag", "NO_VALUE": "tag",
    "SHOW_TAG_MODES": "tag", "TAG_DESCRIPTION": "tag",
    "TAG_FRIENDLY": "tag", "TAG_MODULE": "tag", "TAG_NONE": "tag",
    "display_tag": "tag", "eu_descriptor": "tag",
    "format_data_field": "tag", "module_of": "tag",
}

__all__ = tuple(_EXPORTS)


def __getattr__(name: str):
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{module_name}"), name)
    globals()[name] = value
    return value
