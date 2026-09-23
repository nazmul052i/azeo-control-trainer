"""Tag definitions and the per-scan snapshot the renderer draws from.

Two records with very different lifetimes:

- :class:`TagDef` is engineering data. It changes when somebody re-ranges an
  instrument or the alarm philosophy is revised, which is to say rarely and
  under management of change.
- :class:`TagSnapshot` is one scan's worth of live data. A new set is built
  every scan on a worker thread and handed to the GUI thread whole, so the
  painters never see a half-updated value.

Everything here is frozen. A snapshot that can be edited after delivery is a
snapshot two views can disagree about.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Iterator, Mapping

if TYPE_CHECKING:                       # pragma: no cover
    # Import only for the annotation: `alarms` needs `Priority` and
    # `LimitKind` from here, so a real import would be circular. With
    # `from __future__ import annotations` the annotation stays a string and
    # never triggers one.
    from .alarms import AlarmRecord
    from .faceplate import FaceplateKind

from .errors import DisplayLoadError, UnknownSchemaVersion
from .quality import Quality

#: Version marker for a tag database file. Same refusal-to-guess rule as
#: display files.
TAGS_SCHEMA_ID = "dynalive.tags/1"


class Priority(IntEnum):
    """A required *response*, not a severity adjective (`docs/console/06`).

    Ordered so ``min()`` picks the most urgent, which is what banner and
    summary selection do on every scan.
    """

    #: Immediate action, with a defined consequence if it is not taken.
    P1 = 1
    #: Prompt action.
    P2 = 2
    #: Be aware.
    P3 = 3


class LimitKind(str, Enum):
    """The four limits an analog tag can carry."""

    LL = "LL"
    LO = "LO"
    HI = "HI"
    HH = "HH"

    @property
    def is_low(self) -> bool:
        return self in (LimitKind.LL, LimitKind.LO)


#: Priority a limit takes when the tag does not state one. LL and HH are the
#: limits that have a consequence behind them; LO and HI ask for prompt
#: action. Stated here rather than in the display file, because priority
#: belongs to the alarm philosophy and must be identical on every display the
#: tag appears on.
DEFAULT_PRIORITY: Mapping[LimitKind, Priority] = MappingProxyType({
    LimitKind.LL: Priority.P1,
    LimitKind.HH: Priority.P1,
    LimitKind.LO: Priority.P2,
    LimitKind.HI: Priority.P2,
})


@dataclass(frozen=True)
class AlarmLimit:
    """One configured limit and the priority of exceeding it."""

    kind: LimitKind
    value: float
    #: Optional at construction; resolved from :data:`DEFAULT_PRIORITY` when
    #: omitted, so the attribute is always a real priority by the time
    #: anything reads it.
    priority: Priority | None = None

    def __post_init__(self) -> None:
        if self.priority is None:
            object.__setattr__(self, "priority", DEFAULT_PRIORITY[self.kind])

    def exceeded_by(self, value: float) -> bool:
        return value <= self.value if self.kind.is_low else value >= self.value


@dataclass(frozen=True)
class TagDef:
    """Engineering data for one tag: range, units, precision, limits.

    This is the authority on how a value is *presented*. A painter that
    re-formats a number, invents a range, or decides its own precision is
    showing the operator something the engineering data does not say.
    """

    tag: str
    description: str = ""
    #: Engineering units, as they appear on the P&ID.
    eu: str = ""
    range_lo: float = 0.0
    range_hi: float = 100.0
    #: Displayed decimal places. Authoritative — a transmitter good to +/-2 %
    #: never shows four decimals (`docs/console/02`).
    decimals: int = 1
    #: Operator step for SP/OP entry. ``None`` means 1 % of span.
    step: float | None = None
    limits: tuple[AlarmLimit, ...] = ()
    #: Mode -> reason a faceplate must give when it disables that mode. An
    #: unexplained greyed-out button is why operators phone the engineer.
    invalid_modes: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({}))
    #: **The contextual binding** (`docs/11` section 11.5). The tag names its
    #: own faceplate class and detail display, so re-pointing a tag at a
    #: different faceplate never touches a display file -- the display never
    #: named one. Defaults to the analog faceplate rather than to nothing:
    #: an operator clicking a live value and getting silence is worse than
    #: getting the wrong window, because only one of those is reportable.
    faceplate: "FaceplateKind" = None            # type: ignore[assignment]
    detail_display: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "limits", tuple(self.limits))
        object.__setattr__(self, "invalid_modes",
                           MappingProxyType(dict(self.invalid_modes)))
        if self.faceplate is None:
            from .faceplate import FaceplateKind

            object.__setattr__(self, "faceplate", FaceplateKind.ANALOG)

    # ---------------------------------------------------------------- range
    @property
    def span(self) -> float:
        return self.range_hi - self.range_lo

    def fraction(self, value: float) -> float:
        """Where ``value`` sits in range, 0..1, clamped.

        Clamped because it drives a bar length, and a bar drawn past its own
        box is a rendering bug, not an indication. Whether the value is out
        of range is an alarm question, answered separately.
        """
        if self.span == 0:
            return 0.0
        return max(0.0, min(1.0, (value - self.range_lo) / self.span))

    @property
    def step_size(self) -> float:
        return self.step if self.step is not None else abs(self.span) / 100.0

    # --------------------------------------------------------------- limits
    def limit(self, kind: LimitKind) -> AlarmLimit | None:
        for limit in self.limits:
            if limit.kind is kind:
                return limit
        return None

    @property
    def has_limits(self) -> bool:
        return bool(self.limits)

    def format_value(self, value: float) -> str:
        """The one place a value becomes text. See `docs/console/02`: painters must
        not re-format."""
        return f"{value:.{self.decimals}f}"

    # ------------------------------------------------------------ from file
    @classmethod
    def from_dict(cls, tag: str, data: Mapping[str, Any]) -> "TagDef":
        unknown = set(data) - _TAGDEF_KEYS
        if unknown:
            raise DisplayLoadError(
                f"tag {tag!r}: unknown field(s) {sorted(unknown)}; "
                f"accepted: {sorted(_TAGDEF_KEYS)}")
        span = data.get("range", [0.0, 100.0])
        limits = []
        for kind in LimitKind:
            raw = data.get("limits", {}).get(kind.value)
            if raw is None:
                continue
            if isinstance(raw, Mapping):
                limits.append(AlarmLimit(kind, float(raw["value"]),
                                         Priority(raw["priority"])))
            else:
                limits.append(AlarmLimit(kind, float(raw)))
        return cls(
            tag=tag,
            description=data.get("description", ""),
            eu=data.get("eu", ""),
            range_lo=float(span[0]), range_hi=float(span[1]),
            decimals=int(data.get("decimals", 1)),
            step=data.get("step"),
            limits=tuple(limits),
            invalid_modes=data.get("invalid_modes", {}),
        )

    # -------------------------------------------------------------- to file
    def to_dict(self) -> dict:
        """The inverse of :meth:`from_dict`, and only ever that.

        Emits **only** the keys `from_dict` accepts, so a database this
        writes always loads again — `faceplate` and `detail_display` are
        derived by :meth:`TagDatabase.with_faceplate` rather than stored, and
        writing them would produce a file the loader rejects as unknown
        fields.

        Defaults are omitted rather than spelled out. A generated file that
        repeats every default reads as engineering data somebody chose, and
        the next person maintains values nobody decided.
        """
        data: dict = {}
        if self.description:
            data["description"] = self.description
        if self.eu:
            data["eu"] = self.eu
        data["range"] = [self.range_lo, self.range_hi]
        if self.decimals != 1:
            data["decimals"] = self.decimals
        if self.step is not None:
            data["step"] = self.step
        if self.limits:
            limits: dict = {}
            for limit in self.limits:
                # The priority is written only where it differs from the
                # default for that limit kind: an explicit priority that
                # merely restates the default hides the ones that were a
                # decision.
                if limit.priority == DEFAULT_PRIORITY[limit.kind]:
                    limits[limit.kind.value] = limit.value
                else:
                    limits[limit.kind.value] = {
                        "value": limit.value,
                        "priority": int(limit.priority),
                    }
            data["limits"] = limits
        if self.invalid_modes:
            data["invalid_modes"] = dict(self.invalid_modes)
        return data


_TAGDEF_KEYS = frozenset({
    "description", "eu", "range", "decimals", "step", "limits",
    "invalid_modes",
})


@dataclass(frozen=True, kw_only=True)
class TagSnapshot:
    """One tag, one scan.

    Keyword-only on purpose: ten fields where three of them are floats is a
    place where positional arguments get transposed, and a transposed
    setpoint and output is a plausible-looking faceplate that is wrong.
    """

    tag: str
    #: ``None`` when the source had nothing to give. Distinct from a bad
    #: quality reading of 0.0.
    value: float | None
    quality: Quality
    #: **Server** time, epoch seconds — never the workstation clock.
    #: Operators timestamp log entries from displayed time and those entries
    #: end up in incident timelines; a drifted workstation writes a shift log
    #: that disagrees with the historian.
    timestamp: float
    definition: TagDef
    #: The mode the loop is *in*. Kept apart from ``mode_target`` because a
    #: loop can request CAS and run AUTO, and collapsing the two hides a
    #: broken cascade (`docs/console/05`).
    mode_actual: str | None = None
    #: The mode the loop has been *asked* for.
    mode_target: str | None = None
    setpoint: float | None = None
    #: The setpoint the operator *entered*, where the block distinguishes it
    #: from `setpoint` (the working value the controller is using this
    #: scan). On a loop with SP rate limits the operator typed 80 and the
    #: controller is walking through 62 — a plate that can only show one of
    #: those numbers is hiding whichever one the question was about.
    setpoint_target: float | None = None
    output: float | None = None
    #: The quality, qualified: `Cascade` / `Non-cascade`, and any limit
    #: standing (`High limited`). Azeo prints this beside every readback
    #: because `GOOD` alone cannot say that a cascade is open — and an open
    #: cascade is the most common reason a healthy-looking loop is not
    #: doing what the operator asked.
    substatus: str = ""
    #: The tag's alarm state this scan, or ``None`` if it has never alarmed.
    alarm: "AlarmRecord | None" = None
    #: Recent ``(server_time, value)`` samples, oldest first.
    #:
    #: Not in the dataclass `docs/console/01` lists, and added deliberately: `docs/console/04`
    #: and `docs/console/05` both require a trajectory ("where is it going?" is the
    #: second of the four faceplate questions), and a trend cannot draw one
    #: from a scalar. Recorded as Q19. Empty is a legitimate value — a tag
    #: just subscribed has no history yet, and the trend says so rather than
    #: drawing a flat line at the current value.
    history: tuple[tuple[float, float], ...] = ()

    @property
    def mode_mismatch(self) -> bool:
        """Requested one mode, running another. Worth showing; usually means
        a cascade has dropped."""
        return (self.mode_target is not None
                and self.mode_actual is not None
                and self.mode_target != self.mode_actual)

    @property
    def shows_value(self) -> bool:
        """Invariant I6, applied to this reading."""
        return self.value is not None and self.quality.shows_value

    @property
    def text(self) -> str | None:
        """Formatted value, or ``None`` when there is nothing to show. The
        renderer supplies the dash glyph; the model does not own glyphs."""
        if not self.shows_value:
            return None
        return self.definition.format_value(self.value)


class TagDatabase:
    """Read-only lookup from tag name to :class:`TagDef`.

    Deliberately not a defaultdict and deliberately not forgiving: an unknown
    tag returns nothing, so a display bound to a tag that no longer exists
    draws the documented "NO TAG" state instead of a plausible zero.
    """

    __slots__ = ("_defs",)

    def __init__(self, defs: Mapping[str, TagDef]):
        self._defs = MappingProxyType(dict(defs))

    def __getitem__(self, tag: str) -> TagDef:
        return self._defs[tag]

    def get(self, tag: str) -> TagDef | None:
        return self._defs.get(tag)

    def __contains__(self, tag: object) -> bool:
        return tag in self._defs

    def __iter__(self) -> Iterator[str]:
        return iter(self._defs)

    def __len__(self) -> int:
        return len(self._defs)

    def tags(self) -> tuple[str, ...]:
        return tuple(self._defs)

    # ------------------------------------------------------------ from file
    def with_faceplate(self, tag: str, kind, *,
                       detail_display: str | None = None) -> "TagDatabase":
        """A copy with one tag re-pointed at another faceplate.

        Returns a new database rather than mutating: a tag database is read
        by painters mid-scan, and a definition changing under one is a value
        drawn against the wrong range.
        """
        import dataclasses

        current = self._defs[tag]
        changes = {"faceplate": kind}
        if detail_display is not None:
            changes["detail_display"] = detail_display
        defs = dict(self._defs)
        defs[tag] = dataclasses.replace(current, **changes)
        return TagDatabase(defs)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TagDatabase":
        version = data.get("schema")
        if version != TAGS_SCHEMA_ID:
            raise UnknownSchemaVersion(
                f"tag database schema {version!r} is not understood; "
                f"expected {TAGS_SCHEMA_ID!r}")
        unknown = set(data) - {"schema", "tags"}
        if unknown:
            raise DisplayLoadError(
                f"unknown top-level key(s) in tag database: {sorted(unknown)}")
        return cls({name: TagDef.from_dict(name, spec)
                    for name, spec in data.get("tags", {}).items()})

    @classmethod
    def load(cls, path: str | Path) -> "TagDatabase":
        return cls.from_dict(
            json.loads(Path(path).read_text(encoding="utf-8")))

    # -------------------------------------------------------------- to file
    def to_dict(self) -> dict:
        return {"schema": TAGS_SCHEMA_ID,
                "tags": {tag: self._defs[tag].to_dict()
                         for tag in sorted(self._defs)}}

    def save(self, path: str | Path) -> Path:
        """Write the database beside a display so Studio can find it.

        Studio looks for `tags.json` next to the display it opens. Without
        one every object draws NO TAG — correct behaviour for a tag that does
        not exist, and a baffling thing to meet on a display generated from
        modules that plainly do.
        """
        path = Path(path)
        path.write_bytes(
            (json.dumps(self.to_dict(), indent=2, ensure_ascii=False)
             + "\n").encode("utf-8"))
        return path

    def __repr__(self) -> str:                             # pragma: no cover
        return f"<TagDatabase {len(self._defs)} tags>"
