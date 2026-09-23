"""Transport-neutral signal values crossing the virtual I/O boundary."""

from __future__ import annotations

from dataclasses import dataclass

from ..core.tags import Quality, TagKind


@dataclass(frozen=True)
class SignalSample:
    """Immutable copy of one signal at a point in time.

    A provider must never receive the live ``Tag`` object: handing it out
    would let control-side code mutate the plant without ownership and holder
    arbitration.  This copy includes enough engineering metadata for a
    transport or trainer to translate the value without importing model code.
    """

    name: str
    value: float | bool
    quality: Quality
    timestamp: float
    kind: TagKind
    unit: str
    eu: str
    lo: float
    hi: float

    @property
    def good(self) -> bool:
        return self.quality is Quality.GOOD
