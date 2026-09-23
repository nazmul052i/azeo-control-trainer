"""The ANSI/ISA-18.2 alarm state machine, banner selection and metrics.

Qt-free and fully unit-testable, which is the point: an alarm state machine
that can only be exercised through a running console is one nobody exercises.

Three ideas carry the module.

**Acknowledgement and recovery are different facts.** The state is a product
of two booleans — is the condition true, and is there an unacknowledged
event — and the seven ISA states are the readable names for those
combinations plus the three that take a point out of service. Conflating them
is why so many consoles lose the overnight transient that nobody saw.

**No state is a trap.** `docs/console/06` spells out the return path for shelving
("timer expiry or unshelve -> re-evaluate"); suppression and out-of-service
get the same treatment, because a point that can be suppressed and never
un-suppressed is a silently disabled alarm.

**Re-evaluation is not restoration.** Coming back from a shelf asks the
condition what is true *now*. Restoring the state the alarm had when it was
shelved would resurrect an excursion that has since cleared.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Mapping

from .quality import Quality
from .tags import LimitKind, Priority, TagDatabase, TagDef


class AlarmState(str, Enum):
    """The seven states of the ISA-18.2 model (`docs/console/06`)."""

    #: Not in alarm, acknowledged. The only state that is not on the summary.
    NORM = "NORM"
    #: In alarm, unacknowledged. Blinks.
    UNACK_ALM = "UNACK_ALM"
    #: In alarm, acknowledged. Still active, no longer demanding attention.
    ACK_ALM = "ACK_ALM"
    #: Returned to normal, still unacknowledged. Blinks — the plant recovered
    #: but nobody has said they saw it.
    RTN_UNACK = "RTN_UNACK"
    #: Shelved by an operator, time-limited.
    SHLVD = "SHLVD"
    #: Designed suppression, by logic — a unit that is down, say.
    DSUPR = "DSUPR"
    #: Out of service by maintenance.
    OOSRV = "OOSRV"


class AlarmEvent(str, Enum):
    """What can happen to an alarm."""

    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    RETURN_TO_NORMAL = "RETURN_TO_NORMAL"
    ACKNOWLEDGE = "ACKNOWLEDGE"
    SHELVE = "SHELVE"
    SHELVE_EXPIRED = "SHELVE_EXPIRED"
    UNSHELVE = "UNSHELVE"
    SUPPRESS = "SUPPRESS"
    UNSUPPRESS = "UNSUPPRESS"
    OUT_OF_SERVICE = "OUT_OF_SERVICE"
    IN_SERVICE = "IN_SERVICE"


class _ReEvaluate:
    """Sentinel: the next state depends on the condition, not on history.

    `docs/console/06` writes this as "re-evaluate" in the transition table. Keeping it
    as a distinct value rather than picking a state here is what stops the
    table from claiming to know something it cannot.
    """

    __slots__ = ()

    def __repr__(self) -> str:                             # pragma: no cover
        return "RE_EVALUATE"


RE_EVALUATE = _ReEvaluate()

#: States that blink. Exactly the unacknowledged ones and nothing else — an
#: extra blinking state is an extra thing competing with the one that matters.
BLINK_STATES = frozenset({AlarmState.UNACK_ALM, AlarmState.RTN_UNACK})

#: States in which the process condition is true.
ACTIVE_STATES = frozenset({AlarmState.UNACK_ALM, AlarmState.ACK_ALM})

#: What "any active" in the doc's table covers. A returned-but-unacknowledged
#: alarm is included: it is still on the console asking to be dealt with,
#: which is precisely what an operator shelves.
SHELVABLE = (AlarmState.UNACK_ALM, AlarmState.ACK_ALM, AlarmState.RTN_UNACK)


def _table() -> dict[tuple[AlarmState, AlarmEvent], object]:
    rows: dict[tuple[AlarmState, AlarmEvent], object] = {
        (AlarmState.NORM, AlarmEvent.LIMIT_EXCEEDED): AlarmState.UNACK_ALM,
        (AlarmState.UNACK_ALM, AlarmEvent.ACKNOWLEDGE): AlarmState.ACK_ALM,
        (AlarmState.UNACK_ALM, AlarmEvent.RETURN_TO_NORMAL): AlarmState.RTN_UNACK,
        (AlarmState.ACK_ALM, AlarmEvent.RETURN_TO_NORMAL): AlarmState.NORM,
        (AlarmState.RTN_UNACK, AlarmEvent.ACKNOWLEDGE): AlarmState.NORM,
        (AlarmState.RTN_UNACK, AlarmEvent.LIMIT_EXCEEDED): AlarmState.UNACK_ALM,
        (AlarmState.SHLVD, AlarmEvent.SHELVE_EXPIRED): RE_EVALUATE,
        (AlarmState.SHLVD, AlarmEvent.UNSHELVE): RE_EVALUATE,
        (AlarmState.DSUPR, AlarmEvent.UNSUPPRESS): RE_EVALUATE,
        (AlarmState.OOSRV, AlarmEvent.IN_SERVICE): RE_EVALUATE,
    }
    for state in SHELVABLE:
        rows[(state, AlarmEvent.SHELVE)] = AlarmState.SHLVD
    for state in AlarmState:                        # the doc's "any" rows
        rows[(state, AlarmEvent.SUPPRESS)] = AlarmState.DSUPR
        rows[(state, AlarmEvent.OUT_OF_SERVICE)] = AlarmState.OOSRV
    return rows


#: The transition table, exactly as `docs/06-alarm-model.md` states it.
#: `tests/test_alarms.py` parses that table and checks this against it.
TRANSITIONS: Mapping[tuple[AlarmState, AlarmEvent], object] = _table()


def next_state(state: AlarmState, event: AlarmEvent) -> object:
    """The state after an event, :data:`RE_EVALUATE`, or the state unchanged.

    An undocumented pair is a no-op rather than an error. Acknowledging an
    out-of-service point is a real thing an operator can do with a button that
    exists; it simply must not clear the OOS state.
    """
    return TRANSITIONS.get((state, event), state)


def blink_on(server_time: float) -> bool:
    """Blink phase, one second on, one second off, from **server** time.

    Derived rather than timed locally so every object on every console blinks
    together. A wall of independently-phased blinks is visual noise; a wall
    blinking in unison is a single signal.
    """
    return int(server_time) % 2 == 0


# ----------------------------------------------------------------- the record
@dataclass(frozen=True)
class AlarmRecord:
    """One tag's alarm state, as handed to a painter.

    Immutable: it travels in a :class:`~azeo_control_trainer.core.hmi.model.tags.TagSnapshot` and
    two views must not be able to disagree about it.
    """

    tag: str
    state: AlarmState
    priority: Priority
    #: Which limit was exceeded. ``None`` once the condition has cleared and
    #: the record is only waiting for an acknowledgement.
    kind: LimitKind | None
    #: The value that tripped it, kept so the banner can say what happened
    #: rather than only that something did.
    value: float | None
    description: str = ""
    eu: str = ""
    #: Carried from the `TagDef` so the banner formats at instrument
    #: resolution. `docs/console/02`: painters must not re-format.
    decimals: int = 1
    raised_at: float | None = None
    changed_at: float = 0.0
    acked_at: float | None = None
    acked_by: str | None = None
    shelved_until: float | None = None
    shelved_by: str | None = None
    #: Whether the process condition is true right now. Distinct from the
    #: state, which also carries whether anyone has acknowledged it.
    condition_active: bool = False

    @property
    def blink(self) -> bool:
        return self.state in BLINK_STATES

    @property
    def active(self) -> bool:
        return self.condition_active

    @property
    def acknowledged(self) -> bool:
        return self.state not in BLINK_STATES

    @property
    def out_of_scan(self) -> bool:
        """Shelved, suppressed or out of service — quiet, but never hidden."""
        return self.state in (AlarmState.SHLVD, AlarmState.DSUPR,
                              AlarmState.OOSRV)

    def remaining(self, now: float) -> float | None:
        """Seconds left on the shelf, or ``None`` if not shelved.

        Always available, because `docs/console/06` requires the remaining time to be
        visible: a shelf whose expiry an operator cannot see is a shelf they
        will forget.
        """
        if self.shelved_until is None:
            return None
        return max(0.0, self.shelved_until - now)

    @property
    def text(self) -> str:
        """One line naming the tag, the limit, the value and the service."""
        parts = [self.tag]
        if self.kind is not None:
            parts.append(self.kind.value)
        if self.value is not None:
            shown = f"{self.value:.{self.decimals}f}"
            parts.append(f"{shown}{(' ' + self.eu) if self.eu else ''}")
        if self.description:
            parts.append(self.description)
        return "  ".join(parts)


# ------------------------------------------------------------ banner and perf
@dataclass(frozen=True)
class BannerLine:
    text: str
    record: AlarmRecord | None = None
    acknowledged: bool = False


@dataclass(frozen=True)
class Banner:
    """What the chrome's banner shows this scan."""

    lines: tuple[BannerLine, ...]
    counts: Mapping[Priority, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


#: ISA-18.2 performance targets (`docs/console/06`).
RATE_TARGET_PER_HOUR = 6.0
PEAK_10MIN_TARGET = 10
PEAK_WINDOW_S = 600.0
#: Active longer than this and it is standing: an alarm that is always on is
#: not an alarm, it is a lamp.
STANDING_S = 3600.0
STALE_S = 24 * 3600.0


@dataclass(frozen=True)
class AlarmPerformance:
    """Rolling-window metrics for the alarm summary.

    Named lists rather than bare counts for standing and stale: "3 standing
    alarms" is a number nobody acts on, and the tags are what a shift
    engineer needs in order to go and fix them.
    """

    window_s: float
    count: int
    rate_per_hour: float
    peak_10min: int
    standing: tuple[str, ...] = ()
    stale: tuple[str, ...] = ()
    shelved: tuple[tuple[str, float], ...] = ()

    @property
    def within_targets(self) -> bool:
        return (self.rate_per_hour <= RATE_TARGET_PER_HOUR
                and self.peak_10min <= PEAK_10MIN_TARGET
                and not self.standing and not self.stale)


# ---------------------------------------------------------------- the monitor
@dataclass
class _Point:
    """Mutable per-tag bookkeeping. Never leaves the monitor."""

    tag: str
    state: AlarmState = AlarmState.NORM
    condition: bool = False
    kind: LimitKind | None = None
    priority: Priority = Priority.P3
    value: float | None = None
    raised_at: float | None = None
    changed_at: float = 0.0
    acked_at: float | None = None
    acked_by: str | None = None
    shelved_until: float | None = None
    shelved_by: str | None = None
    oos_by: str | None = None


class AlarmMonitor:
    """Runs the state machine for a set of tags.

    Deliberately not a singleton and deliberately not global: a test builds
    one, and so does a console. Time arrives as an argument on every call —
    there is no clock in here, which is what makes a 25-hour stale-alarm test
    run in a millisecond.
    """

    #: How long raise events are kept for the metrics window. Two days covers
    #: the 24 h stale test with room to spare.
    RETENTION_S = 48 * 3600.0

    def __init__(self, tags: TagDatabase, *, now: float = 0.0):
        self._tags = tags
        self._points: dict[str, _Point] = {}
        self._raises: list[tuple[float, str, Priority]] = []
        self._now = now

    # ------------------------------------------------------------ evaluation
    def evaluate(self, tag: str, value: float | None, quality: Quality,
                 *, now: float) -> AlarmRecord | None:
        """Check one reading against its limits and advance the machine."""
        self._gate(now)
        definition = self._tags.get(tag)
        if definition is None:
            return None

        exceeded = self._worst_exceeded(definition, value, quality)
        point = self._points.get(tag)
        if exceeded is None and point is None:
            return None                     # never alarmed, nothing to track
        if point is None:
            point = self._points[tag] = _Point(tag=tag, changed_at=now)

        point.value = value
        if exceeded is not None:
            escalated = (point.condition
                         and exceeded.priority < point.priority)
            point.kind, point.priority = exceeded.kind, exceeded.priority
            if not point.condition:
                point.condition = True
                self._apply(point, AlarmEvent.LIMIT_EXCEEDED, now)
            elif escalated:
                self._reraise(point, now)
        elif point.condition:
            point.condition = False
            point.kind = None
            self._apply(point, AlarmEvent.RETURN_TO_NORMAL, now)

        return self.record(tag)

    def _worst_exceeded(self, definition: TagDef, value: float | None,
                        quality: Quality):
        """The most severe limit this value breaks, or ``None``.

        Most severe, not first found: above HH the HI limit is also exceeded,
        and reporting HI would put a P1 excursion on the console as a P2.
        """
        if value is None or not quality.alarmable or not definition.has_limits:
            return None
        breached = [limit for limit in definition.limits
                    if limit.exceeded_by(value)]
        if not breached:
            return None
        return min(breached, key=lambda limit: (limit.priority,
                                                -abs(limit.value)))

    # -------------------------------------------------------------- commands
    def acknowledge(self, tag: str, *, by: str, now: float) -> None:
        point = self._points.get(tag)
        if point is None:
            return
        self._gate(now)
        before = point.state
        self._apply(point, AlarmEvent.ACKNOWLEDGE, now)
        if point.state is not before:
            point.acked_at, point.acked_by = now, by

    def acknowledge_all(self, *, by: str, now: float) -> tuple[str, ...]:
        acked = tuple(tag for tag, point in self._points.items()
                      if point.state in BLINK_STATES)
        for tag in acked:
            self.acknowledge(tag, by=by, now=now)
        return acked

    def shelve(self, tag: str, *, seconds: float, by: str, now: float) -> None:
        point = self._points.get(tag)
        if point is None:
            return
        self._gate(now)
        before = point.state
        self._apply(point, AlarmEvent.SHELVE, now)
        if point.state is AlarmState.SHLVD and before is not AlarmState.SHLVD:
            point.shelved_until, point.shelved_by = now + seconds, by

    def unshelve(self, tag: str, *, now: float) -> None:
        point = self._points.get(tag)
        if point is None:
            return
        self._gate(now)
        self._apply(point, AlarmEvent.UNSHELVE, now)
        point.shelved_until = point.shelved_by = None

    def suppress(self, tag: str, on: bool, *, now: float) -> None:
        point = self._points.get(tag)
        if point is None:
            return
        self._gate(now)
        self._apply(point, AlarmEvent.SUPPRESS if on else AlarmEvent.UNSUPPRESS,
                    now)

    def out_of_service(self, tag: str, on: bool, *, by: str,
                       now: float) -> None:
        point = self._points.get(tag)
        if point is None:
            return
        self._gate(now)
        self._apply(point,
                    AlarmEvent.OUT_OF_SERVICE if on else AlarmEvent.IN_SERVICE,
                    now)
        point.oos_by = by if on else None

    def tick(self, now: float) -> None:
        """Advance time: expire shelves whose timer has run out."""
        self._gate(now)

    # ------------------------------------------------------------ transitions
    def _apply(self, point: _Point, event: AlarmEvent, now: float) -> None:
        target = next_state(point.state, event)
        if target is RE_EVALUATE:
            target = self._resolve(point)
        if target is point.state:
            return
        if target is AlarmState.UNACK_ALM and point.state is not \
                AlarmState.UNACK_ALM:
            point.raised_at = now
            self._raises.append((now, point.tag, point.priority))
            self._prune(now)
        if target is AlarmState.NORM:
            point.raised_at = None
        point.state = target                                # type: ignore[assignment]
        point.changed_at = now

    def _reraise(self, point: _Point, now: float) -> None:
        """An already-active alarm crossing a worse limit.

        `docs/console/06` has no row for escalation, because its table is written in
        terms of "limit exceeded" against a single condition. An acknowledged
        HI that becomes an HH is a P1 excursion nobody has seen: relabelling
        the existing record would leave a priority-1 alarm sitting there
        un-blinking and already acknowledged. So it is raised again.

        A point that is shelved, suppressed or out of service stays quiet —
        being taken out of scan outranks an escalation, and the escalation is
        still there to be found when it comes back.
        """
        if point.state in (AlarmState.SHLVD, AlarmState.DSUPR,
                           AlarmState.OOSRV):
            return
        point.raised_at = now
        point.state = AlarmState.UNACK_ALM
        point.changed_at = now
        self._raises.append((now, point.tag, point.priority))
        self._prune(now)

    def _resolve(self, point: _Point) -> AlarmState:
        """What a point returns to when a shelf, suppression or OOS lifts.

        The condition decides, not the state it had when it was taken out of
        scan — that excursion may have cleared an hour ago.
        """
        if point.condition:
            return AlarmState.UNACK_ALM
        return (AlarmState.NORM if point.acked_at is not None
                and (point.raised_at is None
                     or point.acked_at >= point.raised_at)
                else AlarmState.RTN_UNACK)

    def _gate(self, now: float) -> None:
        """Expire any shelf that has run out, then remember the time."""
        self._now = max(self._now, now)
        for point in self._points.values():
            if (point.state is AlarmState.SHLVD
                    and point.shelved_until is not None
                    and now >= point.shelved_until):
                self._apply(point, AlarmEvent.SHELVE_EXPIRED, now)
                point.shelved_until = point.shelved_by = None

    def _prune(self, now: float) -> None:
        cutoff = now - self.RETENTION_S
        if self._raises and self._raises[0][0] < cutoff:
            self._raises = [row for row in self._raises if row[0] >= cutoff]

    # ----------------------------------------------------------------- views
    def record(self, tag: str) -> AlarmRecord | None:
        point = self._points.get(tag)
        if point is None:
            return None
        definition = self._tags.get(tag)
        return AlarmRecord(
            tag=tag, state=point.state, priority=point.priority,
            kind=point.kind, value=point.value,
            description=definition.description if definition else "",
            eu=definition.eu if definition else "",
            decimals=definition.decimals if definition else 1,
            raised_at=point.raised_at, changed_at=point.changed_at,
            acked_at=point.acked_at, acked_by=point.acked_by,
            shelved_until=point.shelved_until, shelved_by=point.shelved_by,
            condition_active=point.condition)

    def records(self) -> tuple[AlarmRecord, ...]:
        return tuple(r for r in (self.record(t) for t in self._points) if r)

    def summary(self) -> tuple[AlarmRecord, ...]:
        """Everything not in NORM, shelved and suppressed points included.

        `docs/console/06`: shelved alarms are "always visible in the summary with a
        greyed shape. Never silently hidden."
        """
        return tuple(r for r in self.records() if r.state is not AlarmState.NORM)

    def unacknowledged(self) -> tuple[AlarmRecord, ...]:
        return tuple(r for r in self.records() if r.blink)

    def active(self) -> tuple[AlarmRecord, ...]:
        return tuple(r for r in self.records() if r.condition_active)

    # ---------------------------------------------------------------- banner
    def banner(self, *, lines: int = 2, now: float) -> Banner:
        """The banner model: counts, then the lines themselves.

        Never returns zero lines. Blank is ambiguous with a banner that has
        failed, and an operator cannot tell "quiet" from "broken" by looking
        at nothing.
        """
        self._gate(now)
        counts = {priority: 0 for priority in Priority}
        for record in self.records():
            if record.blink or record.condition_active:
                counts[record.priority] += 1

        unacked = sorted(self.unacknowledged(),
                         key=lambda r: (r.priority, -(r.changed_at)))
        if unacked:
            chosen = tuple(BannerLine(text=r.text, record=r,
                                      acknowledged=False)
                           for r in unacked[:max(1, lines)])
            return Banner(lines=chosen, counts=counts)

        active = sorted(self.active(), key=lambda r: (r.priority,
                                                      -(r.changed_at)))
        if active:
            record = active[0]
            return Banner(
                lines=(BannerLine(text=f"{record.text}  (acknowledged)",
                                  record=record, acknowledged=True),),
                counts=counts)

        return Banner(lines=(BannerLine(text="No active alarms"),),
                      counts=counts)

    # ----------------------------------------------------------- performance
    def performance(self, *, now: float,
                    window_s: float = 3600.0) -> AlarmPerformance:
        self._gate(now)
        events = [row for row in self._raises if row[0] >= now - window_s]
        hours = window_s / 3600.0
        standing, stale = [], []
        for record in self.records():
            if not record.condition_active or record.raised_at is None:
                continue
            age = now - record.raised_at
            if age > STANDING_S:
                standing.append(record.tag)
            if age > STALE_S:
                stale.append(record.tag)
        shelved = tuple(
            (r.tag, r.remaining(now)) for r in sorted(
                self.records(), key=lambda r: r.tag)
            if r.state is AlarmState.SHLVD)
        return AlarmPerformance(
            window_s=window_s, count=len(events),
            rate_per_hour=len(events) / hours if hours else 0.0,
            peak_10min=_peak(row[0] for row in events),
            standing=tuple(sorted(standing)), stale=tuple(sorted(stale)),
            shelved=shelved)


def _peak(timestamps: Iterable[float]) -> int:
    """Most raises in any ten-minute window. Sliding, not bucketed — a flood
    that straddles two fixed buckets is still a flood."""
    times = sorted(timestamps)
    best = 0
    for index, start in enumerate(times):
        count = 0
        for other in times[index:]:
            if other - start > PEAK_WINDOW_S:
                break
            count += 1
        best = max(best, count)
    return best
