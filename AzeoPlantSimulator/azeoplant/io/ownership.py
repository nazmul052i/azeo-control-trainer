# -*- coding: utf-8 -*-
"""Who may write which signal.

The rule is the physical one: the simulator owns what the field produces
(AI and DI - a controller cannot will a pressure into being), and the DCS
owns what the field consumes (AO and DO - the plant does not move its own
valves). The rule derives from :class:`~azeoplant.core.tags.TagKind`, and
the bus enforces it at every doorway a foreign write can enter.
"""

from __future__ import annotations

import enum

from ..core.tags import TagKind


class SignalOwner(enum.StrEnum):
    SIMULATOR = "SIMULATOR"
    DCS = "DCS"


def owner_of(kind: TagKind) -> SignalOwner:
    return SignalOwner.DCS if kind.dcs_writable else SignalOwner.SIMULATOR


class OwnershipViolation(Exception):
    """A write from the wrong side of the boundary."""

    def __init__(self, tag: str, attempted_by: str) -> None:
        super().__init__(
            f"{attempted_by} may not write {tag}: wrong side of the "
            f"virtual I/O boundary")
        self.tag = tag
        self.attempted_by = attempted_by
