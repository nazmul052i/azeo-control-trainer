"""Semantic colour roles — the only vocabulary a painter is allowed.

Invariant I1: no colour literal exists outside ``theme/tokens.py``. A painter
never sees a hex string; it asks a :class:`~azeo_control_trainer.core.hmi.theme.palette.RolePalette`
for a role, and the palette answers from the active theme.

The point is not tidiness. A role is a *meaning*, and meanings are what a style
guide governs: ``ALARM_P1`` is "priority 1 and nothing else", so it cannot be
borrowed for a stop button or a heading, and retheming a plant cannot
accidentally make a priority-1 alarm look like a label.

The members here are exactly the ``roles`` list in ``schema/tokens.json`` and
are checked against it by ``test_generated_tokens_are_current``. Adding a role
means adding it to the schema first.
"""
from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    """A semantic colour slot. ``str`` so it serialises and prints as its name."""

    # ── surfaces ────────────────────────────────────────────────────
    SURFACE_BG = "SURFACE_BG"
    SURFACE_PANEL = "SURFACE_PANEL"
    SURFACE_PANEL_ALT = "SURFACE_PANEL_ALT"
    SURFACE_SUNK = "SURFACE_SUNK"
    SURFACE_FIELD = "SURFACE_FIELD"

    # ── lines and text ──────────────────────────────────────────────
    LINE = "LINE"
    LINE_SOFT = "LINE_SOFT"
    TEXT = "TEXT"
    TEXT_DIM = "TEXT_DIM"
    TEXT_FAINT = "TEXT_FAINT"
    #: A section or plate heading. Not body text: on the Azeo Operator Station plate
    #: the tag reads in a distinct blue so the eye finds *which loop* before
    #: it reads any number on it.
    HEADING = "HEADING"

    # ── process objects ─────────────────────────────────────────────
    #: Every equipment outline and every pipe, regardless of service. There is
    #: deliberately no per-service pipe role: colour is reserved for abnormal.
    EQUIPMENT = "EQUIPMENT"
    EQUIPMENT_FILL = "EQUIPMENT_FILL"
    LIQUID = "LIQUID"

    # ── alarm priorities ────────────────────────────────────────────
    #: Theme-invariant: one meaning, one colour, in every theme. Each is also
    #: paired with a shape (I4) so priority survives greyscale.
    ALARM_P1 = "ALARM_P1"
    ALARM_P2 = "ALARM_P2"
    ALARM_P3 = "ALARM_P3"
    #: Text companions to the three alarm fills. A fill is read against
    #: its mark's outline; text is read against the panel behind it, and
    #: no one hue clears both a light panel and a dark one — `ALARM_P3`
    #: sits 0.9 of luminance from the HP-gray panel, `ALARM_P1` sits 15
    #: from the dark one. **Marks keep the invariant colour; text takes
    #: the companion**, so priority still reads and so does the word.
    ALARM_P1_TEXT = "ALARM_P1_TEXT"
    ALARM_P2_TEXT = "ALARM_P2_TEXT"
    ALARM_P3_TEXT = "ALARM_P3_TEXT"
    ALARM_SHELVED = "ALARM_SHELVED"

    # ── interaction ─────────────────────────────────────────────────
    #: I7: marks a writable affordance and nothing else. Never process state,
    #: never emphasis, never decoration.
    ACTION = "ACTION"
    #: The action colour, one step darker. For a mark that sits *on* an
    #: action-coloured fill and has to stay visible against it.
    ACTION_DEEP = "ACTION_DEEP"
    #: Native operator selection/focus must remain readable on every surface.
    SELECTION = "SELECTION"
    ON_SELECTION = "ON_SELECTION"
    FOCUS = "FOCUS"
    #: The measurement bar, and the demand bar. Deliberately different
    #: materials rather than two tints of one: a PV and an OP are not the
    #: same kind of number, and an operator should not have to read a label
    #: to tell which bar is which.
    BAR_PV = "BAR_PV"
    BAR_OUT = "BAR_OUT"
    #: The shaded "in range" band on an analog indicator.
    NORMAL_BAND = "NORMAL_BAND"


#: Roles a process object may never be painted in. `ACTION` is the operator's
#: signal for "you can act on this"; spending it on a vessel or a pipe destroys
#: the only cue that tells them where the controls are.
FORBIDDEN_ON_PROCESS = frozenset({Role.ACTION})

#: State roles: their meaning does not change with the theme, so neither
#: should their colour. A theme may still deviate, but only by naming the role
#: in `$override` in the schema — `dark` does exactly that for `ALARM_P3`,
#: because a mid yellow goes muddy on a dark ground. Accidental drift is the
#: thing being prevented, not deliberate, reviewed adjustment.
THEME_INVARIANT = frozenset({
    Role.ALARM_P1, Role.ALARM_P2, Role.ALARM_P3, Role.ALARM_SHELVED,
    Role.NORMAL_BAND,
})


#: The text companion for each alarm fill. Painters take this for a run of
#: words and the fill itself only for a mark.
ALARM_TEXT = {
    Role.ALARM_P1: Role.ALARM_P1_TEXT,
    Role.ALARM_P2: Role.ALARM_P2_TEXT,
    Role.ALARM_P3: Role.ALARM_P3_TEXT,
}


def text_role(role: Role) -> Role:
    """The readable companion of `role`, or `role` when it needs none."""
    return ALARM_TEXT.get(role, role)


def role_names() -> list[str]:
    """Role names in declaration order, for comparison with the schema."""
    return [role.value for role in Role]
