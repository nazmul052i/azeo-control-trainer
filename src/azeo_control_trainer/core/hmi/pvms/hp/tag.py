"""The display tag, the EU descriptor and the data fields.

Small pieces, but the data field is the one with a rule worth keeping:
"Shows up to six right-justified digits, including decimal point and
minus sign, if applicable." Six *characters*, right-justified, so a
column of PVMs lines its decimal points up and the eye can compare
magnitudes down the column without reading each number. A field that
grows with its value breaks that column, which is the entire reason
the manual specifies a width at all.
"""
from __future__ import annotations

#: What the display tag shows — Azeo's `ShowTag` layout variable.
TAG_MODULE, TAG_DESCRIPTION = "module", "description"
TAG_FRIENDLY, TAG_NONE = "friendly", "none"
SHOW_TAG_MODES = (TAG_MODULE, TAG_DESCRIPTION, TAG_FRIENDLY, TAG_NONE)

#: The manual's field width, in characters.
FIELD_WIDTH = 6
#: What a data field shows when its value is not a number. A blank
#: number is the repo's existing answer for bad quality (never a stale
#: value dressed as live), and the dash says the field exists.
NO_VALUE = "---"


def module_of(pvm) -> str:
    """The module name a PVM is bound to, from its parameters."""
    params = getattr(pvm, "params", {}) or {}
    if "path" in params:
        return str(params["path"] or "").split("/")[0]
    for value in params.values():
        text = str(value or "")
        if text:
            return text.split("/")[0] if "/" in text else text
    return ""


def display_tag(pvm, mode: str = TAG_MODULE,
                description: str = "") -> str:
    """What the display tag shows, per Azeo's `ShowTag`.

    "When the display tag is configured to show the friendly name or
    description, then PVMs that do not have these options configured
    show the module name instead" — so friendly and description both
    fall back rather than rendering a PVM with no identity on it.
    """
    module = module_of(pvm)
    if mode == TAG_NONE:
        return ""
    if mode == TAG_FRIENDLY:
        return getattr(pvm, "label", "") or module
    if mode == TAG_DESCRIPTION:
        return description or module
    return module


def format_data_field(value, decimals: int | None = None,
                      width: int = FIELD_WIDTH) -> str:
    """A PV/SP/OUT number, right-justified in `width` characters.

    `decimals` is the block's F_DECPT. With none given the places are
    chosen so the number fits the width — which keeps 1234.5 and 0.25
    both readable in the same column instead of truncating one.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return NO_VALUE.rjust(width)
    if decimals is not None:
        text = f"{number:.{int(decimals)}f}"
    else:
        for places in range(3, -1, -1):
            text = f"{number:.{places}f}"
            if len(text) <= width:
                break
    if len(text) > width:
        # Too big to show honestly at this width. A truncated number is
        # a WRONG number, so say the field overflowed instead.
        return ("*" * width)[:width]
    return text.rjust(width)


def eu_descriptor(result) -> str:
    """The A_UNITS of the applicable scaling parameter."""
    return str(getattr(result, "units", "") or "")
