"""The Assembler's own palette, sizes and mode vocabulary.

Colour, layout and navigation follow the wireframe HTML rather than
the DynaLive theme, so this is the one place the two are allowed to
disagree — every other module in the package reads its colours from
`WF` and never writes a literal.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import (
    AUTHORING_BLUE, AUTHORING_HOVER, AUTHORING_SELECTION, UI,
)

MIME_BLOCK = "application/x-azeo-block"
#: A concrete terminal/configuration parameter dragged from Control Data.
#: It is intentionally distinct from ``MIME_BLOCK``: dropping a block asks
#: for a compatible PVM, while dropping one parameter creates a Data Link.
MIME_PARAMETER = "application/x-azeo-parameter"
#: A reusable visual dragged from the component palette.  This is separate
#: from ``MIME_BLOCK`` because palette content is a visual class or drawing
#: stencil, not a configured control object.  Keeping that distinction in
#: the MIME contract prevents a dropped vessel from pretending to be a tag.
MIME_PALETTE = "application/x-azeo-graphics-palette"
#: Roles an Assembler places from the browser, in preference order.
DYNAMO_ROLES = ("dynamo_compact", "dynamo_inline")
PVM_W, PVM_H = 150.0, 54.0
#: Per-role card sizes, from the wireframe's own Placement pane.
ROLE_SIZES = {"dynamo_inline": (176.0, 94.0),
              "dynamo_compact": (96.0, 42.0)}

#: The wireframe's palette (hmi_designer_and_faceplates (3).html CSS
#: variables) — colour, layout and navigation follow the HTML, not the
#: DynaLive theme.
WF = {
    # Application chrome.  These roles intentionally stop at the edge of
    # the authored page: process colour continues to come from the selected
    # operator theme, so polishing the Studio cannot alter a published HMI.
    "page": UI.page, "pane": UI.pane, "chrome": UI.chrome,
    "chrome_2": UI.chrome_alt, "work": "#EEF1F5",
    "bd": UI.border, "bd_lt": UI.border_light,
    "tx": UI.text, "tx2": UI.text_secondary, "tx3": UI.text_muted,
    "navy": AUTHORING_BLUE, "lapis": AUTHORING_BLUE, "lapis_lt": AUTHORING_BLUE,
    "sel": AUTHORING_SELECTION, "sel_br": AUTHORING_BLUE, "hover": AUTHORING_HOVER,
    "canvas": "#D6DCE1", "grid": "#CDD3D8",
    "isa_elem": "#7A7E84", "isa_line": "#70747A",
    "crit": "#C0392B", "warn": "#C06A1E", "ok": "#3E7D4A",
    # Older authoring role names remain aliases of the single product accent.
    "cmd_teal": AUTHORING_BLUE, "cmd_violet": AUTHORING_BLUE,
    "mode_cas": "#2A7F87", "mode_man": "#A8811B", "mode_oos": "#7A7A7A",
    "fld_ro": "#E0E0E0", "track": "#D6D6D6", "bar": "#7E8286",
    "card_bd": "#B4B8BB",
}

#: §8.6 — the three modes. Test is a sandbox: bindings live, writes
#: blocked; the lock is acquired on ENTERING edit, not at open.
MODE_VIEW, MODE_EDIT, MODE_TEST = "view", "edit", "test"


def _mode_chip_colour(mode: str) -> str:
    mode = (mode or "").upper()
    if mode in ("CAS", "RCAS"):
        return WF["mode_cas"]
    if mode == "MAN":
        return WF["mode_man"]
    if mode == "OOS":
        return WF["mode_oos"]
    return WF["lapis"]


#: The theme the Azeo Operator Station half wears — studio canvas AND station.
#:
#: **One theme, because WYSIWYG is a comparison.** `hpgray` is the
#: console theme: a mid-grey ground chosen so colour on it means
#: abnormal. Authoring against it while the station runs something else
#: would mean the engineer never sees what the operator will, and
#: `tests/_smoke_operator.py` compares the two renders pixel for pixel
#: — it caught exactly that when only the studio was changed.
#:
#: `azeo_live` already existed and is the light ground Azeo Operator Station
#: actually uses. Nothing here hardcodes a colour, so retheming still
#: works and a display that sets its own `background` still wins.
STUDIO_THEME = "azeo_live"
