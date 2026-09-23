"""Signal quality — the part of a tag value that says whether to believe it.

A value without its quality is a number an operator will act on. Every path
that carries a value carries this beside it, and invariant I6 hangs off one
predicate here: :attr:`Quality.shows_value`. There is no code path in the
renderer that draws a stale number as if it were current, because the renderer
asks this enum first.

Values match the field-device vocabulary the trainer's `Terminal.status`
already uses, so a bridge between the two is a lookup rather than a mapping
with judgement in it.
"""
from __future__ import annotations

from enum import Enum


class Quality(str, Enum):
    """How much of the value can be trusted.

    ``str`` so it serialises and prints as its name.
    """

    #: Live and within the instrument's stated performance.
    GOOD = "GOOD"
    #: Live, but the source has flagged it doubtful — a value still worth
    #: drawing, with the doubt shown rather than hidden.
    UNCERTAIN = "UNCERTAIN"
    #: No usable value. The last good number must never be redrawn as current.
    BAD = "BAD"
    #: Coming from a simulation, not the field. Unmistakable during
    #: commissioning is the whole requirement: a simulated value that looks
    #: live is how a loop gets tuned against a model and then surprises
    #: everyone on startup.
    SIM = "SIM"
    #: Out of service by maintenance. Drawn, chipped, and not alarmed.
    OOS = "OOS"

    @property
    def shows_value(self) -> bool:
        """Whether a number may be drawn at all (invariant I6).

        Bad quality renders the dash glyph instead. Note that ``SIM`` and
        ``OOS`` *do* show a number — their chip is what carries the caveat,
        because an operator commissioning a loop needs to read the value.
        """
        return self is not Quality.BAD

    @property
    def draws_bar(self) -> bool:
        """Whether the range bar is drawn.

        Bad quality drops the bar rather than drawing it empty: an empty bar
        reads as "at the bottom of range", which is a number, not an absence.
        """
        return self is not Quality.BAD

    @property
    def chip(self) -> str | None:
        """Short chip text for the faceplate footer, or ``None``.

        Only the qualities that are invisible in the number itself get one.
        Bad quality is already unmistakable from the dashes; good quality
        needs no announcement, and a "GOOD" chip on every faceplate would
        train operators to stop reading chips.
        """
        return _CHIPS.get(self)

    @property
    def alarmable(self) -> bool:
        """Whether limit checking should run against this value.

        Out of service and bad both stop alarms — an instrument in the shop
        must not flood the console from its own absence.
        """
        return self in (Quality.GOOD, Quality.UNCERTAIN, Quality.SIM)


_CHIPS = {
    Quality.UNCERTAIN: "UNCERT",
    Quality.SIM: "SIM",
    Quality.OOS: "OOS",
}
