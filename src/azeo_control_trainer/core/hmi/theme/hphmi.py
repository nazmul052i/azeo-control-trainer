"""Machine-readable High-Performance HMI standards shared by the product.

`docs/hphmi_wireframe.html` is the project's style guide: ISA-101.01-2015 and
IEC 63303:2024 aligned, with ISA-18.2 for the alarm side. ISA-101 does not hand
you hex values — it requires that a project *have* a philosophy and a style
guide, and that displays be testable against it. This module is that style
guide in machine-readable form.

It emits a portable ``hmiStandards`` block, so a display references a
**named standard** and never a literal colour. That is the mechanism Azeo
uses too (a value-to-colour pair names a theme colour), and it
is what makes retheming a plant not touch a single display.

The four rules that decide almost every visual question here:

1. **Colour is reserved for abnormal.** Equipment outlines, equipment fill and
   *every* pipe are grey regardless of service. A screen where nothing is
   wrong is a grey screen.
2. **Running versus stopped is fill density and a text label, never green
   versus red.** Green for "running" spends the operator's colour vocabulary
   on the normal case and leaves nothing for the abnormal one.
3. **Every alarm priority carries a shape as well as a colour** (▲ P1, ◆ P2,
   ■ P3) so it survives deuteranopia and greyscale printing.
4. **Bad quality replaces the number with dashes.** Never show a stale value;
   simulated and out-of-service must look distinct from bad.
"""
from __future__ import annotations

# ── alarm and interaction tokens ─────────────────────────────────────
# Theme-invariant on purpose: one meaning, one colour, everywhere.
A1 = "#C4262E"      # Priority 1 — critical
A2 = "#E08A1E"      # Priority 2 — high
A3 = "#E3D02A"      # Priority 3 — low
A4 = "#9AA0A6"      # journal / suppressed
ACT = "#1F6FB2"     # operator-actionable affordance, selection, edit field
OK = "#5C7A5C"      # desaturated "normal band" shading on analog indicators
BAD = "#7A2E2E"

#: Priority glyphs. The shape is not decoration — it is the redundant channel
#: that carries priority when colour cannot.
PRIORITY_SHAPE = {
    "CRITICAL": "▲",   # ▲
    "WARNING": "◆",    # ◆
    "ADVISORY": "■",   # ■
    "LOG": "▬",        # ▬
}

#: Every alarm mark is drawn fill-plus-outline. The outline is not decoration:
#: it is what keeps a light priority separable from a light ground when colour
#: carries no information - a priority-3 yellow on HP-gray fails the
#: monochrome test on fill alone. See `hmi/vision.py`.
ALARM_OUTLINE = "#2A2A2A"

#: Data-quality tags. BAD blanks the numeric; SIM and OOS stay readable but
#: must look unmistakably different from a live measurement.
QUALITY_TAGS = ("GOOD", "UNCERT", "BAD", "SIM", "OOS")

#: What a bad measurement shows instead of a number.
BAD_QUALITY_TEXT = "----"

# ── the three style guides, mapped onto the four theme slots ──
# Core HMI ships exactly four variants. HP-HMI neutral gray is the default
# because it is what the style guide leads with; the dark console becomes the
# low-light variant, which is what it is for.
THEMES = ("normal", "lowLight", "highContrast", "colorAccessible")

_GROUND = {
    "normal":          {"bg": "#D2D2D2", "eq": "#8C8C8C", "eqfill": "#C0C0C0",
                        "pipe": "#8A8A8A", "tx": "#1C1C1C", "field": "#E4E4E4"},
    "lowLight":        {"bg": "#2C3134", "eq": "#7E888D", "eqfill": "#3A4145",
                        "pipe": "#7E888D", "tx": "#E4E8EA", "field": "#22272A"},
    "highContrast":    {"bg": "#FFFFFF", "eq": "#000000", "eqfill": "#FFFFFF",
                        "pipe": "#000000", "tx": "#000000", "field": "#FFFFFF"},
    "colorAccessible": {"bg": "#D4D4D0", "eq": "#7E7E78", "eqfill": "#C2C2BC",
                        "pipe": "#7E7E78", "tx": "#1A1A18", "field": "#E6E6E2"},
}

#: Alarm colours per variant. Only the accessible variant shifts them, and it
#: shifts red/green rather than dulling the whole set.
_ALARM = {
    "normal":          {"a1": A1, "a2": A2, "a3": A3, "act": ACT, "sup": A4},
    "lowLight":        {"a1": "#D9484F", "a2": "#E89A3C", "a3": "#D8C425",
                        "act": "#5AA0DA", "sup": "#8B9296"},
    "highContrast":    {"a1": "#B00000", "a2": "#A85800", "a3": "#7A6B00",
                        "act": "#00429B", "sup": "#4A4A4A"},
    "colorAccessible": {"a1": "#8E44AD", "a2": "#E08A1E", "a3": "#E3D02A",
                        "act": "#0072B2", "sup": "#9AA0A6"},
}


def _colour(std_id: str, name: str, role: str, pick, required: bool = True) -> dict:
    """One colour standard, valued for every theme variant."""
    return {
        "id": std_id,
        "name": name,
        "type": "color",
        "role": role,
        "required": required,
        "values": {theme: pick(theme) for theme in THEMES},
    }


def colour_standards() -> list[dict]:
    """The colour half of the style guide, in the portable HMI schema.

    Note what is *not* here: there is no per-service pipe colour, no "running"
    colour and no "stopped" colour. Those are the rules above, expressed by
    leaving the vocabulary out — a display author cannot reach for a colour
    the standards do not define.
    """
    return [
        _colour("color-background", "Display background", "background",
                lambda t: _GROUND[t]["bg"]),
        _colour("color-equipment-outline", "Equipment outline",
                "equipment-outline", lambda t: _GROUND[t]["eq"]),
        _colour("color-equipment-fill", "Equipment fill", "equipment-fill",
                lambda t: _GROUND[t]["eqfill"]),
        # One pipe colour for every service. The style guide keeps --pipe as
        # its own token, a shade off --eq, but the rule that matters is that
        # there is exactly *one* of it: no steam-is-red, no cooling-is-blue.
        _colour("color-process-line", "Process line (all services)",
                "process-line", lambda t: _GROUND[t]["pipe"]),
        _colour("color-text", "Operator text", "text",
                lambda t: _GROUND[t]["tx"]),
        _colour("color-alarm", "Priority 1 alarm", "alarm",
                lambda t: _ALARM[t]["a1"]),
        _colour("color-warning", "Priority 2 alarm", "warning",
                lambda t: _ALARM[t]["a2"]),
        _colour("color-bad-quality", "Bad quality", "bad-quality",
                lambda t: _ALARM[t]["sup"]),
        # The interchange schema calls this role "manual"; its meaning is
        # wider — anything the operator can act on, including an edit field.
        _colour("color-manual", "Operator-actionable", "manual",
                lambda t: _ALARM[t]["act"]),
        # Roleless standards: the interchange format maps nine roles to CSS
        # variables, but a display can still reference these by id.
        _colour("color-alarm-3", "Priority 3 alarm", "",
                lambda t: _ALARM[t]["a3"], required=False),
        _colour("color-normal-band", "Normal band shading", "",
                lambda t: OK, required=False),
        _colour("color-data-field", "Data field background", "",
                lambda t: _GROUND[t]["field"], required=False),
    ]


def line_standards() -> list[dict]:
    """Line widths. Equipment and process lines are the same weight — a
    heavier pipe reads as more important, and no pipe is more important."""
    return [
        {"id": "line-width-equipment", "name": "Equipment line",
         "type": "line-width", "role": "", "required": True,
         "values": {t: 1.5 for t in THEMES}},
        {"id": "line-width-process", "name": "Process line",
         "type": "line-width", "role": "", "required": True,
         "values": {t: 1.5 for t in THEMES}},
    ]


def hmi_standards(active: str = "normal") -> dict:
    """The complete ``hmiStandards`` block for a display document."""
    if active not in THEMES:
        active = "normal"
    return {
        "activeTheme": active,
        "themes": [
            {"id": "normal", "name": "HP-HMI neutral gray"},
            {"id": "lowLight", "name": "Dark console (night shift)"},
            {"id": "highContrast", "name": "High contrast"},
            {"id": "colorAccessible", "name": "Red/green-shifted accessibility"},
        ],
        "definitions": colour_standards() + line_standards(),
    }


#: What a symbol references instead of carrying literal colours.
EQUIPMENT_REFS = {
    "stroke": "color-equipment-outline",
    "fill": "color-equipment-fill",
    "strokeWidth": "line-width-equipment",
}

#: Literal fallbacks, written alongside the reference so a document opened
#: without its standards still renders as the style guide intends.
EQUIPMENT_LITERALS = {
    "stroke": _GROUND["normal"]["eq"],
    "fill": _GROUND["normal"]["eqfill"],
    "strokeWidth": 1.5,
}

PROCESS_LINE = {
    "colorStandard": "color-process-line",
    "widthStandard": "line-width-process",
    "color": _GROUND["normal"]["pipe"],
    "width": 1.5,
}


#: Colours that may never appear on a process object. `--act` marks something
#: the operator can act on; spending it on a pipe or a vessel destroys the one
#: signal that tells them where the controls are.
PROCESS_FORBIDDEN = (ACT,)


def shape_for(severity: str) -> str:
    """The glyph that carries this priority when colour cannot."""
    return PRIORITY_SHAPE.get(str(severity).upper(), PRIORITY_SHAPE["ADVISORY"])


def alarm_colour(severity: str, theme: str = "normal") -> str:
    """Alarm colour for a named priority, in a theme variant."""
    table = _ALARM.get(theme, _ALARM["normal"])
    return {
        "CRITICAL": table["a1"],
        "WARNING": table["a2"],
        "ADVISORY": table["a3"],
        "LOG": table["sup"],
    }.get(str(severity).upper(), table["a3"])
