"""Tag database.

Single source of truth for every process signal. The simulation engine, the OPC UA
server and the user interface all read and write through this module.

Threading contract
------------------
``TagDatabase.lock`` is a re-entrant lock guarding every mutation. It is held for
coarse operations only:

* the engine holds it for one complete integration step;
* the OPC UA writer holds it while taking a snapshot of outputs;
* the OPC UA subscription handler holds it while applying one client write;
* the UI holds it while taking a display snapshot.

Model code holds direct references to :class:`Tag` objects for speed and mutates
them inside the engine step, which is already inside the lock.
"""

from __future__ import annotations

import enum
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple


class Quality(enum.IntEnum):
    """Signal quality, mapped to OPC UA status codes by :mod:`azeoplant.opc.server`."""

    GOOD = 0
    UNCERTAIN = 1
    BAD = 2

    @property
    def label(self) -> str:
        return {0: "Good", 1: "Uncertain", 2: "Bad"}[int(self)]


class TagKind(enum.StrEnum):
    AI = "AI"   # analogue input  - simulator writes, DCS reads
    AO = "AO"   # analogue output - DCS writes, simulator reads
    DI = "DI"   # discrete input  - simulator writes, DCS reads
    DO = "DO"   # discrete output - DCS writes, simulator reads

    @property
    def dcs_writable(self) -> bool:
        return self in (TagKind.AO, TagKind.DO)

    @property
    def analogue(self) -> bool:
        return self in (TagKind.AI, TagKind.AO)


# How far past its span a real transmitter can still indicate before it
# saturates. ``devices.Transmitter`` saturates its output at exactly this, so a
# reading a little under zero is normal instrument behaviour, not a defect. Only
# a value beyond it means a model has produced something no instrument could
# ever show.
OVER_RANGE = 0.02


@dataclass
class Tag:
    """One signal crossing the simulator / DCS boundary.

    Attributes
    ----------
    value:
        Engineering-unit value for analogue tags, ``bool`` for discrete tags.
    quality:
        Simulated instrument quality. Published to OPC UA as a status code and
        rendered distinctively on the P&ID.
    override / override_value:
        Local override used when no DCS is connected. ``effective`` returns the
        override for ``AO``/``DO`` tags so the simulator can be exercised
        standalone. Overrides never apply to ``AI``/``DI``: those are model
        outputs and forcing them would hide the physics.
    """

    name: str
    kind: TagKind
    unit: str
    desc: str
    eu: str = ""
    lo: float = 0.0
    hi: float = 100.0
    value: float | bool = 0.0
    quality: Quality = Quality.GOOD
    ts: float = field(default_factory=time.time)
    state0: str = "Off"
    state1: str = "On"
    override: bool = False
    override_value: float | bool = 0.0
    excursions: int = 0        # times the model drove this outside its span
    worst: float = 0.0         # furthest it got, in engineering units

    # ------------------------------------------------------------------ helpers
    @property
    def span(self) -> float:
        s = self.hi - self.lo
        return s if abs(s) > 1e-12 else 1.0

    @property
    def pct(self) -> float:
        """Value as 0-100 percent of span, clamped. Maps to ``FIELD_VAL_PCT``."""
        if not self.kind.analogue:
            return 100.0 if self.value else 0.0
        return _clamp((float(self.value) - self.lo) / self.span * 100.0, -10.0, 110.0)

    @property
    def effective(self) -> float | bool:
        """Value the process model should act on."""
        if self.override and self.kind.dcs_writable:
            return self.override_value
        return self.value

    def set(self, value: float | bool, quality: Quality = Quality.GOOD) -> None:
        """Write a model output. Non-finite values are rejected, not propagated."""
        if self.kind.analogue:
            v = float(value)
            if not math.isfinite(v):
                # A NaN reaching the DCS is worse than a frozen value: freeze and
                # flag bad so the operator sees an instrument fault, not garbage.
                self.quality = Quality.BAD
                self.ts = time.time()
                return
            # A value outside the declared span is a model defect, not an
            # instrument fault: no transmitter reads a negative absolute flow.
            # It is counted rather than clamped, because clamping would hide
            # the defect and the whole point of this simulator is that the
            # consequence is allowed to happen and be seen.
            margin = OVER_RANGE * self.span
            if v < self.lo - margin or v > self.hi + margin:
                self.excursions += 1
                over = v - self.hi if v > self.hi else self.lo - v
                self.worst = max(self.worst, over)
            self.value = v
        else:
            self.value = bool(value)
        self.quality = quality
        self.ts = time.time()

    # ----------------------------------------------------------- OPC UA
    @property
    def node_id(self) -> str:
        """Canonical OPC UA identifier, as defined by the tag workbook.

        ``PLANT_SIM`` is the address space root agreed with the DCS. It is not
        product branding and must not follow a rename, or every reference in an
        existing controller database breaks.
        """
        return f"ns=2;s=PLANT_SIM.{self.unit}.{self.kind.value}.{self.name}"

    def set_from_dcs(self, value: float | bool) -> tuple[float | bool, bool]:
        """Apply a write that arrived from a client.

        Returns the value actually applied and whether it had to be adjusted.
        An analogue write outside the tag range is clamped rather than
        rejected: a DCS that momentarily commands 101 percent should drive the
        valve fully open, not have its write disappear. A value that is not a
        number at all, or a write to a tag the DCS does not own, is refused.
        """
        if not self.kind.dcs_writable:
            raise PermissionError(
                f"{self.name} is {self.kind.value}; only AO and DO accept DCS writes")
        if self.kind.analogue:
            v = float(value)
            if not math.isfinite(v):
                raise ValueError(f"{self.name}: non-finite write rejected")
            applied: float | bool = _clamp(v, self.lo, self.hi)
            adjusted = applied != v
        else:
            applied, adjusted = bool(value), False
        self.value = applied
        self.quality = Quality.GOOD
        self.ts = time.time()
        return applied, adjusted

    def format(self) -> str:
        if not self.kind.analogue:
            return self.state1 if self.value else self.state0
        mag = max(abs(self.lo), abs(self.hi))
        dp = 0 if mag >= 500 else (1 if mag >= 20 else 2)
        return f"{float(self.value):.{dp}f} {self.eu}".strip()


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else (hi if x > hi else x)


class DuplicateTagError(KeyError):
    """Raised when two models claim the same tag name."""


class TagDatabase:
    """Ordered, name-indexed collection of :class:`Tag`."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self._tags: Dict[str, Tag] = {}

    # ------------------------------------------------------------------ building
    def add(self, tag: Tag) -> Tag:
        if tag.name in self._tags:
            raise DuplicateTagError(
                f"tag {tag.name!r} already defined by unit "
                f"{self._tags[tag.name].unit}; duplicate from {tag.unit}"
            )
        self._tags[tag.name] = tag
        return tag

    def analog(self, name, kind, unit, desc, eu, lo, hi, value=None) -> Tag:
        return self.add(Tag(name, TagKind(kind), unit, desc, eu=eu, lo=lo, hi=hi,
                            value=lo if value is None else value))

    def discrete(self, name, kind, unit, desc, state0="Off", state1="On",
                 value=False) -> Tag:
        return self.add(Tag(name, TagKind(kind), unit, desc, lo=0, hi=1,
                            value=value, state0=state0, state1=state1))

    # ------------------------------------------------------------------- access
    def __contains__(self, name: str) -> bool:
        return name in self._tags

    def __getitem__(self, name: str) -> Tag:
        return self._tags[name]

    def get(self, name: str) -> Optional[Tag]:
        return self._tags.get(name)

    def all(self) -> List[Tag]:
        return list(self._tags.values())

    def by_kind(self, *kinds: TagKind) -> List[Tag]:
        return [t for t in self._tags.values() if t.kind in kinds]

    def by_unit(self, unit: str) -> List[Tag]:
        return [t for t in self._tags.values() if t.unit == unit]

    def units(self) -> List[str]:
        seen: List[str] = []
        for t in self._tags.values():
            if t.unit not in seen:
                seen.append(t.unit)
        return seen

    def counts(self) -> Dict[str, int]:
        out = {k.value: 0 for k in TagKind}
        for t in self._tags.values():
            out[t.kind.value] += 1
        return out

    # ---------------------------------------------------------------- snapshots
    def excursions(self) -> List[Tuple[str, int, float]]:
        """Analogues the models have driven outside their declared span.

        Sorted worst first. An empty list is the healthy answer: a level that
        reads minus one percent or a flow that reads negative is not physics,
        it is a model that needs looking at.
        """
        out = [(t.name, t.excursions, t.worst) for t in self.all()
               if t.kind.analogue and t.excursions]
        out.sort(key=lambda r: -r[2])
        return out

    def snapshot(self, names: Optional[Iterable[str]] = None) -> Dict[str, tuple]:
        """Thread-safe copy of ``(value, quality, ts)`` for UI and OPC UA."""
        with self.lock:
            src = (self._tags[n] for n in names) if names else self._tags.values()
            return {t.name: (t.value, int(t.quality), t.ts) for t in src}

    def save_state(self) -> Dict[str, object]:
        with self.lock:
            return {t.name: t.value for t in self._tags.values()}

    def load_state(self, state: Dict[str, object]) -> int:
        applied = 0
        with self.lock:
            for name, value in state.items():
                tag = self._tags.get(name)
                if tag is None:
                    continue
                tag.value = float(value) if tag.kind.analogue else bool(value)
                tag.quality = Quality.GOOD
                tag.ts = time.time()
                applied += 1
        return applied
