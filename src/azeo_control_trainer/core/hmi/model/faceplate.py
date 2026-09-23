"""What a faceplate needs that a tag snapshot does not carry.

`docs/console/05` asks four faceplates for information no scalar reading contains: an
MPC prediction is a trajectory, an MV's authority is a set of numbers about
moves rather than about the process, an inferential's honesty depends on its
confidence band and its last lab point, and valve health is a history of
travel. None of it belongs in `TagSnapshot`, which is one tag at one instant.

So it is here, as optional records attached to a :class:`FaceplateData`. A
faceplate whose extra data is absent draws the "not available" form rather
than inventing a plausible one — an MPC faceplate showing a confident flat
prediction it does not have would be worse than showing nothing. Recorded as
Q22.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping

from .tags import TagSnapshot


class FaceplateKind(str, Enum):
    """The seven types in `docs/console/05`."""

    ANALOG = "analog"
    DISCRETE = "discrete"
    MPC_CV = "mpc_cv"
    MPC_MV = "mpc_mv"
    INFERENTIAL = "inferential"
    VALVE = "valve"
    INTERLOCK = "interlock"
    MINI = "mini"



#: Azeo's device-state wording. The block publishes an integer; these are
#: the words an operator reads on a Azeo console, and "Confirmed Stopped"
#: says something "STOPPED" does not — that the *feedback* agrees, not merely
#: that nothing is being commanded.
DEVICE_STATES = ("Confirmed Stopped", "Starting", "Confirmed Running",
                 "Stopping", "Fail", "Interlocked", "Locked")


@dataclass(frozen=True)
class DeviceState:
    """What a device block is saying, in the shape a Azeo plate shows it."""

    state: str = ""
    #: What the operator last asked for, against what the device reports.
    target: str = ""
    mode: str = ""
    #: `Clear`, or the fail that is standing.
    fail: str = "Clear"
    #: Seconds into the current transition, and the limit it is racing.
    #: **Both**, because the limit alone cannot say a start is about to fail
    #: and the elapsed alone cannot say whether that matters.
    elapsed: float = 0.0
    limit: float = 0.0
    running: bool = False
    faulted: bool = False
    interlocked: bool = False
    #: `PERMISSIVE_D` on the block: True = clear to start. Kept apart from
    #: `interlocked` because `DEVCTL` keeps them apart — an interlock stops a
    #: running device, a permissive only withholds a start, and telling an
    #: operator the wrong one sends them to the wrong place.
    permissive: bool = True
    #: A transition is being held. The elapsed timer is frozen
    #: while this is true, so the bar must stop too — a bar that
    #: kept filling would say the device was about to fail when
    #: the whole point of the pause is that it is not.
    paused: bool = False

    @property
    def transitioning(self) -> bool:
        return self.state in ("Starting", "Stopping")

    @property
    def start_blocked(self) -> str:
        """Why a start would be refused right now, or an empty string.

        A sentence, not a flag. "Why will it not start" is the question the
        plate exists to answer, and a greyed button that answers it with
        nothing is the most-reported complaint operators make about an HMI.
        """
        if self.interlocked:
            return "Interlocked — the start interlock is not satisfied."
        if not self.permissive:
            return "Permissive not met — the device is not clear to start."
        if self.faulted:
            return "Faulted — RESET before starting."
        return ""

    @property
    def fraction(self) -> float:
        """How far through the transition, 0..1. Zero when nothing is timed."""
        if not self.limit or not self.transitioning:
            return 0.0
        return max(0.0, min(1.0, self.elapsed / self.limit))


@dataclass(frozen=True)
class Condition:
    """One permissive, run condition or trip, as the operator sees it.

    `delay` is carried because an interlock with a delay behaves differently
    from one without — but there is deliberately no *elapsed* field. No block
    publishes how long a condition has been true, so a progress bar toward a
    trip would be a number this code invented. See the open question; the fix
    is a pin, not a renderer.
    """

    label: str
    ok: bool
    bypassed: bool = False
    delay: float = 0.0
    #: True when the condition is the recorded first-out cause.
    first_out: bool = False


@dataclass(frozen=True)
class InterlockState:
    """What an interlock block is saying. Everything here is a published pin."""

    state: str = ""
    tripped: bool = False
    ready: bool = False
    start_permit: bool = False
    run_permit: bool = False
    any_bypassed: bool = False
    #: The condition that tripped first — the one question an operator asks.
    first_out: str = ""
    conditions: tuple = ()

    @property
    def blocking(self) -> tuple:
        """Conditions standing in the way, worst first: the first-out cause,
        then anything not satisfied. A bypassed condition is not blocking —
        it is a different problem and says so on its own row."""
        return tuple(sorted(
            (c for c in self.conditions if not c.ok and not c.bypassed),
            key=lambda c: (not c.first_out, c.label)))


@dataclass(frozen=True)
class Context:
    """What a contextual faceplate is opened *about*.

    Azeo Operator Station passes a tag plus four free-form `Context` strings. `docs/11`
    section 11.5 keeps the contextual idea and refuses the loose strings:
    untyped slots end up carrying whatever the last engineer needed, and the
    next one cannot tell what is safe to change.

    Every field here is named and optional. Adding one is a deliberate edit
    to this class, which is exactly the friction four anonymous slots remove
    and should not.
    """

    tag: str
    #: Process unit the tag belongs to, for the faceplate header.
    unit: str = ""
    #: Alarm/operating area, for the same reason.
    area: str = ""
    #: The control module this point lives in, when it is not the tag itself
    #: -- a cascade's secondary is addressed inside its primary's module.
    module: str = ""
    #: Detail display to open from the faceplate, overriding `TagDef`.
    detail_display: str = ""

@dataclass(frozen=True)
class Prediction:
    """An MPC controlled variable's forecast.

    The operator's question is a shape, not a scalar: *is this going to hit
    the limit, and when.* Past and future are kept apart so the renderer can
    draw the `now` rule between them — a single merged series would let a
    prediction be read as a measurement.
    """

    #: `(server_time, value)` ahead of now, oldest first.
    future: tuple[tuple[float, float], ...] = ()
    lo: float | None = None
    hi: float | None = None
    horizon_s: float = 600.0

    @property
    def violates(self) -> bool:
        return any((self.hi is not None and value > self.hi)
                   or (self.lo is not None and value < self.lo)
                   for _, value in self.future)


@dataclass(frozen=True)
class MvAuthority:
    """Why the MPC has stopped moving this handle.

    Every field is a number an operator can act on. `docs/console/05` insists the
    authority be stated numerically — "At HI limit. 0.00 of ±2.00 %/cycle
    available." — because "constrained" is a word that ends the conversation
    instead of starting it.
    """

    at_limit: str | None = None          # "HI" | "LO" | None
    rate_limit: float = 0.0              # % per cycle, symmetric
    move_this_cycle: float = 0.0
    remaining: float = 0.0
    lp_cost: float | None = None         # read-only from the console

    def sentence(self) -> str:
        if self.at_limit:
            return (f"At {self.at_limit} limit. {self.remaining:.2f} of "
                    f"±{self.rate_limit:.2f} %/cycle available.")
        return (f"Moving. {self.move_this_cycle:+.2f} this cycle, "
                f"±{self.rate_limit:.2f} %/cycle available.")


@dataclass(frozen=True)
class Inference:
    """A soft sensor's own account of how much it should be believed."""

    confidence: float = 0.0              # ± engineering units
    bias: float = 0.0                    # applied lab bias
    last_lab_at: float | None = None
    last_lab_value: float | None = None
    #: Inputs outside the training envelope. The reading is then an
    #: extrapolation, and saying so is the difference between a soft sensor
    #: and a guess with a tag number.
    extrapolating: bool = False

    def decision_rule(self, margin: float | None) -> str:
        """The rule stated on the face, per `docs/console/05`."""
        if margin is None:
            return "Margin to spec unknown — do not push on this reading."
        if margin > self.confidence:
            return (f"Margin to spec {margin:.2f} exceeds ±{self.confidence:.2f} "
                    f"confidence. Reading may justify a push.")
        return (f"Margin to spec {margin:.2f} is inside ±{self.confidence:.2f} "
                f"confidence. Do not push on this reading.")


@dataclass(frozen=True)
class ValveHealth:
    """Final element health: what the valve has actually been doing."""

    travel_total: float = 0.0            # cumulative % of travel
    reversals: int = 0
    deviation: float | None = None       # demanded minus actual
    last_stroke_at: float | None = None
    stiction_suspected: bool = False


@dataclass(frozen=True)
class FaceplateData:
    """One faceplate's whole input. Immutable, like everything a painter sees."""

    snapshot: TagSnapshot
    kind: FaceplateKind = FaceplateKind.ANALOG
    #: Modes offered, in display order. The subset that is *valid* comes from
    #: `TagDef.invalid_modes`, with the reason attached.
    modes: tuple[str, ...] = ("MAN", "AUTO", "CAS")
    commands: tuple[str, ...] = ()
    service: str = ""
    prediction: Prediction | None = None
    authority: MvAuthority | None = None
    inference: Inference | None = None
    health: ValveHealth | None = None
    interlock: InterlockState | None = None
    device: DeviceState | None = None
    #: Most recent accepted write, shown in the footer.
    last_write: object | None = None
    tabs: tuple[str, ...] = field(default_factory=lambda: ("Operate",))
    #: What this faceplate was opened about (section 11.5). Typed, optional,
    #: and never four loose strings.
    context: "Context | None" = None
    #: Live block-pin values, `{PIN: value}`, for the fields
    #: `faceplate_fields.json` declares. Empty is not zero: a pin absent from
    #: this mapping renders a dash, because a faceplate that shows `0.0` for
    #: a pin nobody answered is worse than one that shows nothing.
    pins: Mapping[str, object] = field(default_factory=dict)
    #: The block's configuration — engineering data, not live values. A
    #: valve's fail action and stroke time are configured facts and belong
    #: beside its position for exactly that reason: they say what the thing
    #: will do when nobody is asking.
    config: Mapping[str, object] = field(default_factory=dict)
    #: The block type this faceplate is for, which is what selects the
    #: contract. Carried rather than guessed from `kind`: several block types
    #: share a faceplate kind and they do not publish the same pins.
    block_type: str = ""
    #: Detail display when no context overrides it.
    _detail_default: str = ""

    def field_values(self):
        """The contract's pin fields, resolved against `pins`."""
        from .faceplate_spec import read, spec_for

        spec = spec_for(self.block_type)
        if spec is None:
            return ()
        return read(spec, self.pins)


    @classmethod
    def for_tag(cls, definition, snapshot, *, context: "Context | None" = None,
                **extra) -> "FaceplateData":
        """Build from the tag's own declaration (section 11.5).

        The kind comes from `TagDef.faceplate`. Before this it came from
        whoever assembled the data, which meant a display -- or the code that
        opened the faceplate -- had to know what kind of thing a tag was. It
        is the tag that knows, and it is the tag that changes.
        """
        from .quality import Quality
        from .tags import TagSnapshot

        if snapshot is None:
            # No value yet is *bad* quality, not zero. A faceplate opened
            # before the first scan must not show a number (I6).
            snapshot = TagSnapshot(
                tag=definition.tag, value=None, quality=Quality.BAD,
                timestamp=0.0, definition=definition)
        kind = extra.pop("kind", None) or definition.faceplate
        return cls(snapshot=snapshot, kind=kind, context=context, **extra)

    @property
    def detail_display(self) -> str:
        """Where "Detail" goes. Context wins, then the tag's own default."""
        if self.context is not None and self.context.detail_display:
            return self.context.detail_display
        return self._detail_default

    @property
    def tag(self) -> str:
        return self.snapshot.tag

    @property
    def deviation(self) -> float | None:
        """PV − SP. `None` when either is missing, never zero."""
        snapshot = self.snapshot
        if snapshot.value is None or snapshot.setpoint is None:
            return None
        if not snapshot.quality.shows_value:
            return None
        return snapshot.value - snapshot.setpoint

    def trend_direction(self) -> int:
        """−1, 0 or +1 from the last six samples (`docs/console/05`).

        Six because it is enough to survive one noisy reading and short
        enough to still be about now.
        """
        history = self.snapshot.history[-6:]
        if len(history) < 2:
            return 0
        change = history[-1][1] - history[0][1]
        span = self.snapshot.definition.span or 1.0
        if abs(change) < span * 0.002:
            return 0
        return 1 if change > 0 else -1

    def invalid_reason(self, mode: str) -> str | None:
        return self.snapshot.definition.invalid_modes.get(mode)
