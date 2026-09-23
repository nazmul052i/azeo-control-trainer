"""Authoring tiers — sheet 04. Structural, not advisory.

The Assembler pane does not grey out a colour picker; it has none.
Anything reachable eventually gets reached for, so capability lives in
what a tier's surface *contains*, and this module only names the tiers
and answers the gate questions the surfaces ask.

Mapping onto the trainer: there is no login; the tier is a launch/menu
choice the way the keylock is. OPERATOR sees runtime only (SP/MODE/OUT
writes per the §8.3 routing); ASSEMBLER may edit displays — bind by
path, pick class and standard, arrange; AUTHOR edits the system —
classes, standards, the theme.
"""
from __future__ import annotations

from enum import IntEnum


class Tier(IntEnum):
    OPERATOR = 0
    ASSEMBLER = 1
    AUTHOR = 2


def may_edit_displays(tier: Tier) -> bool:
    return tier >= Tier.ASSEMBLER


def may_edit_classes(tier: Tier) -> bool:
    return tier >= Tier.AUTHOR


def may_place_composites(tier: Tier) -> bool:
    """Composites bind several paths at once — Author-only until the
    wireframe's open decision says otherwise (sheet 7.6)."""
    return tier >= Tier.AUTHOR


def may_reach_simulate(tier: Tier) -> bool:
    """Sim Enable is a live process risk on an operator screen —
    Author tier only by default (proposal §11.3)."""
    return tier >= Tier.AUTHOR
