"""Dynamo PVM classes for the rest of the block palette.

Control Designer offers 145 function blocks. Before this module five of
them could be placed on a display, so a student could build a module
the palette openly offers — a ratio station, a totalizer, a voter, a
cause-and-effect matrix — and then find nothing to draw with. That is
the trainer's edge showing through, not a lesson.

**Closing the gap does not mean one PVM class per function block.**
Some blocks are intentionally omitted: nobody puts an AND gate or an ABS block on an
operator display, because a display shows the *process*, not the
arithmetic behind it. The target is every block an operator has a
reason to look at, which is what this module supplies.

Two rules held throughout:

- **Every binding names a terminal that actually exists.** A binding
  to an invented parameter renders Bad forever, which looks exactly
  like a dead transmitter — a display that lies about the plant is
  worse than one that is missing.
- **The row keys are the shared vocabulary** (`renderer.ROW_KEYS`):
  bind `pv.value`, `sp.value`, `out.value`, `state.name`, `limits.*`
  and the generic card paints them with no painter needed. A class
  earns a custom painter only where the shape carries meaning a row
  of text cannot.
"""
from __future__ import annotations

from .base import Bind, PvmClass, register_pvm


# ------------------------------------------------------------- discrete I/O
@register_pvm
class DiscreteInputCompact(PvmClass):
    """A discrete input's state, on the graphic.

    `PV_D` rather than `OUT`: OUT is what the block hands downstream
    and can be inverted or forced, while PV_D is what the field
    contact is actually saying. On an operator display the field is
    the interesting one.
    """

    block_type = "DI"
    role = "dynamo_compact"
    display_name = "Discrete Input"

    bindings = (
        Bind("pv.value", "{path}/PV_D"),
        Bind("state.name", "{path}/PV_D"),
        Bind("pv.quality", "{path}/OUT", prop="StatusCode"),
        Bind("pv.forced", "{path}/OUT", prop="Forced"),
    )


@register_pvm
class DiscreteOutputCompact(PvmClass):
    """A discrete output with its readback beside it.

    Commanded and readback together, because a disagreement between
    them is how a stuck valve or a failed starter announces itself —
    the same reason `AO` carries a readback (catalog Part 2).
    """

    block_type = "DO"
    role = "dynamo_compact"
    display_name = "Discrete Output"

    bindings = (
        Bind("out.value", "{path}/OUT_D"),
        Bind("readback.value", "{path}/READBACK_D"),
        Bind("state.name", "{path}/PV_D"),
        Bind("out.quality", "{path}/OUT_D", prop="StatusCode"),
        Bind("out.forced", "{path}/OUT_D", prop="Forced"),
    )


@register_pvm
class EnhancedDeviceCompact(PvmClass):
    """EDC — Azeo's own `HP_EDC_PMP_` / `HP_EDC_VLV_` territory."""

    block_type = "EDC"
    role = "dynamo_compact"
    display_name = "Enhanced Device"

    bindings = (
        Bind("out.value", "{path}/OUT_D"),
        Bind("sp.value", "{path}/SP_D"),
        Bind("pv.value", "{path}/PV_D"),
        Bind("readback.value", "{path}/FV_D"),
        Bind("state.name", "{path}/PV_D"),
        Bind("out.quality", "{path}/OUT_D", prop="StatusCode"),
    )


# ----------------------------------------------------------------- control
@register_pvm
class ManualLoaderCompact(PvmClass):
    """MANLD — the operator's hand on the output, with its own alarms."""

    block_type = "MANLD"
    role = "dynamo_compact"
    display_name = "Manual Loader"

    bindings = (
        Bind("out.value", "{path}/OUT"),
        Bind("sp.value", "{path}/SP_FB"),
        Bind("out.units", "{path}/OUT", prop="EngineeringUnits"),
        Bind("out.range", "{path}/OUT", prop="EURange"),
        Bind("out.quality", "{path}/OUT", prop="StatusCode"),
        Bind("out.forced", "{path}/OUT", prop="Forced"),
    )


@register_pvm
class ManualLoaderBar(ManualLoaderCompact):
    """The same point as a bar, for a display with vertical room."""

    role = "dynamo_inline"
    display_name = "Manual Loader Bar"
    DEFAULT_SIZE = (58.0, 150.0)


@register_pvm
class OnOffCompact(PvmClass):
    """ONOFF — a two-state controller. The float output is bound too
    because a 0/100 demand is what a valve actually receives."""

    block_type = "ONOFF"
    role = "dynamo_compact"
    display_name = "On/Off Controller"

    bindings = (
        Bind("state.name", "{path}/OUT"),
        Bind("out.value", "{path}/OUT_FLOAT"),
        Bind("out.quality", "{path}/OUT", prop="StatusCode"),
    )


@register_pvm
class RampSoakCompact(PvmClass):
    """RAMP_SOAK — Azeo's `RTO`, the ramp-to-output profile.

    Segment and running state alongside the value: a profile's output
    means nothing without knowing which segment produced it and
    whether the profile is still moving.
    """

    block_type = "RAMP_SOAK"
    role = "dynamo_compact"
    display_name = "Ramp / Soak"

    bindings = (
        Bind("out.value", "{path}/OUT"),
        Bind("state.name", "{path}/SEGMENT"),
        Bind("state.fail", "{path}/COMPLETE"),
        Bind("out.units", "{path}/OUT", prop="EngineeringUnits"),
        Bind("out.quality", "{path}/OUT", prop="StatusCode"),
    )


@register_pvm
class GainSchedulerCompact(PvmClass):
    """GAIN_SCHED — which region is in force, and the gains it chose.

    The REGION is the operator-facing fact: a loop behaving oddly at
    one end of its range is a scheduling question before it is a
    tuning one.
    """

    block_type = "GAIN_SCHED"
    role = "dynamo_compact"
    display_name = "Gain Scheduler"

    bindings = (
        Bind("state.name", "{path}/REGION"),
        Bind("pv.value", "{path}/KP"),
        Bind("out.value", "{path}/TI"),
        Bind("pv.quality", "{path}/KP", prop="StatusCode"),
    )


@register_pvm
class ControlSelectorCompact(PvmClass):
    """CTLSL — an override scheme's selected path.

    Which input won is the single most important fact in a
    cross-limiting scheme and the one a P&ID cannot show, so SELECTED
    is bound to the state row rather than buried.
    """

    block_type = "CTLSL"
    role = "dynamo_compact"
    display_name = "Control Selector"

    bindings = (
        Bind("out.value", "{path}/OUT"),
        Bind("state.name", "{path}/SELECTED"),
        Bind("out.quality", "{path}/OUT", prop="StatusCode"),
        Bind("out.limit", "{path}/OUT_LIM"),
    )


@register_pvm
class AutoTunerCompact(PvmClass):
    """AT — is the tuner running, and on what."""

    block_type = "AT"
    role = "dynamo_compact"
    display_name = "Auto Tuner"

    bindings = (
        Bind("state.name", "{path}/ACTIVE"),
        Bind("pv.value", "{path}/TRK_VAL_OUT"),
        Bind("pv.quality", "{path}/TRK_VAL_OUT", prop="StatusCode"),
    )


# ------------------------------------------------------------------ signal
@register_pvm
class RatioCompact(PvmClass):
    """RATIO — actual against target, so drift reads by comparison.

    The faceplate already made this argument (`signal.RatioFaceplate`);
    the dynamo carries the same pair onto the graphic so an operator
    does not have to open a faceplate to see a ratio has wandered.
    """

    block_type = "RATIO"
    role = "dynamo_compact"
    display_name = "Ratio Station"

    bindings = (
        Bind("pv.value", "{path}/IN"),
        Bind("out.value", "{path}/OUT"),
        Bind("sp.value", "{path}/CONFIG/RATIO"),
        Bind("out.quality", "{path}/OUT", prop="StatusCode"),
    )


@register_pvm
class TotalizerCompact(PvmClass):
    """TOTALIZER — the total, and the rate that is filling it."""

    block_type = "TOTALIZER"
    role = "dynamo_compact"
    display_name = "Totalizer"

    bindings = (
        Bind("pv.value", "{path}/OUT"),
        Bind("out.value", "{path}/RATE"),
        Bind("limits.hi", "{path}/CONFIG/HI_LIMIT"),
        Bind("pv.units", "{path}/OUT", prop="EngineeringUnits"),
        Bind("pv.quality", "{path}/OUT", prop="StatusCode"),
    )


@register_pvm
class HighSelectCompact(PvmClass):
    """MAX_SELECT — the winner named, as the faceplate does."""

    block_type = "MAX_SELECT"
    role = "dynamo_compact"
    display_name = "High Select"

    bindings = (
        Bind("out.value", "{path}/OUT"),
        Bind("state.name", "{path}/SELECTED"),
        Bind("out.quality", "{path}/OUT", prop="StatusCode"),
    )


@register_pvm
class LowSelectCompact(HighSelectCompact):
    block_type = "MIN_SELECT"
    display_name = "Low Select"


@register_pvm
class PulseInputCompact(PvmClass):
    """PIN — a pulse input's engineering value and its frequency."""

    block_type = "PIN"
    role = "dynamo_compact"
    display_name = "Pulse Input"

    bindings = (
        Bind("pv.value", "{path}/OUT"),
        Bind("out.value", "{path}/FREQUENCY"),
        Bind("pv.units", "{path}/OUT", prop="EngineeringUnits"),
        Bind("pv.range", "{path}/OUT", prop="EURange"),
        Bind("pv.quality", "{path}/OUT", prop="StatusCode"),
        Bind("pv.forced", "{path}/OUT", prop="Forced"),
    )


@register_pvm
class PulseInputBar(PulseInputCompact):
    """The same point as a bar — a pulse input is a flow more often
    than not, and a flow belongs on a bar."""

    role = "dynamo_inline"
    display_name = "Pulse Input Bar"
    DEFAULT_SIZE = (58.0, 150.0)


@register_pvm
class AlarmBlockCompact(PvmClass):
    """ALARM — the block whose whole job is to say something is wrong.

    `ANY_ALARM` drives the state row so the card goes abnormal on any
    of the eight conditions, and HI/LO ride the limit rows so the card
    says *which*.
    """

    block_type = "ALARM"
    role = "dynamo_compact"
    display_name = "Alarm Block"

    bindings = (
        Bind("state.name", "{path}/ANY_ALARM"),
        Bind("state.fail", "{path}/ANY_ALARM"),
        Bind("limits.hi_hi", "{path}/HI_HI"),
        Bind("limits.hi", "{path}/HI"),
        Bind("limits.lo", "{path}/LO"),
        Bind("limits.lo_lo", "{path}/LO_LO"),
    )


@register_pvm
class AlarmDetectorCompact(PvmClass):
    """ALARM_DET — the detected value with its four limit flags."""

    block_type = "ALARM_DET"
    role = "dynamo_compact"
    display_name = "Alarm Detector"

    bindings = (
        Bind("pv.value", "{path}/PV"),
        Bind("limits.hi_hi", "{path}/HI_HI_ACT"),
        Bind("limits.hi", "{path}/HI_ACT"),
        Bind("limits.lo", "{path}/LO_ACT"),
        Bind("limits.lo_lo", "{path}/LO_LO_ACT"),
        Bind("pv.quality", "{path}/PV", prop="StatusCode"),
    )


# ------------------------------------------------------------------ safety
@register_pvm
class VoterCompact(PvmClass):
    """AVTR — Azeo's `Controller_AVTR_1...3Input_`.

    Votes against the number needed to trip is the whole story of a
    voter: 1-of-3 with one vote in is a healthy system reporting a
    fault, and 2-of-3 with two votes in is a trip. A card that showed
    only the output would hide the difference.
    """

    block_type = "AVTR"
    role = "dynamo_compact"
    display_name = "Analog Voter"

    bindings = (
        Bind("state.name", "{path}/TRIP_STATUS"),
        Bind("state.tripped", "{path}/OUT_D"),
        Bind("pv.value", "{path}/TRIP_VOTES"),
        Bind("sp.value", "{path}/A_NUM_TO_TRIP"),
        Bind("state.fail", "{path}/OUT_D_GOOD"),
    )


@register_pvm
class DiscreteVoterCompact(VoterCompact):
    block_type = "DVTR"
    display_name = "Discrete Voter"


@register_pvm
class CauseEffectCompact(PvmClass):
    """CEM — a cause and effect matrix's first four effects.

    Azeo fail-safe polarity applies (invariant 13): `1` is
    inactive/Normal and `0` is active/Tripped, so a card reading zero
    is a card reporting a trip.
    """

    block_type = "CEM"
    role = "dynamo_compact"
    display_name = "Cause & Effect"

    bindings = (
        Bind("state.tripped", "{path}/OUT1"),
        Bind("pv.value", "{path}/OUT1"),
        Bind("sp.value", "{path}/OUT2"),
        Bind("out.value", "{path}/OUT3"),
        Bind("readback.value", "{path}/OUT4"),
    )


# --------------------------------------------------------------- sequence
@register_pvm
class SequenceCompact(PvmClass):
    """SFC_CHART — Azeo's `Controller_SEQ_`.

    The step NAME and its elapsed time, because "which step, and for
    how long" is the question an operator asks of a running sequence,
    and a chart stuck on step 4 for ten minutes is the fault the
    number alone will not show.
    """

    block_type = "SFC_CHART"
    role = "dynamo_compact"
    display_name = "Sequence"
    DEFAULT_SIZE = (150.0, 62.0)

    bindings = (
        Bind("state.name", "{path}/STEP_NO"),
        Bind("pv.value", "{path}/STEP_TIME"),
        Bind("state.fail", "{path}/FAULT"),
        Bind("state.tripped", "{path}/HELD"),
        Bind("out.value", "{path}/ACTIVE"),
    )


# -------------------------------------------------------------------- APC
@register_pvm
class MpcControllerCompact(PvmClass):
    """DMC_CONTROLLER — Azeo's `MPC_LOOP_1_` territory.

    State and heartbeat together: an MPC that reports READY while its
    heartbeat has stopped is the failure mode worth showing, and
    neither value says it alone.
    """

    block_type = "DMC_CONTROLLER"
    role = "dynamo_compact"
    display_name = "MPC Controller"

    bindings = (
        Bind("state.name", "{path}/STATE_NAME"),
        Bind("pv.value", "{path}/HEARTBEAT"),
        Bind("out.value", "{path}/CYCLE_TIME"),
        Bind("state.fail", "{path}/FAULT"),
        Bind("state.tripped", "{path}/ACTIVE"),
    )


@register_pvm
class InspectCompact(PvmClass):
    """INSPECT — device health rolled up, Azeo's `INSPECT_Block_1_`."""

    block_type = "INSPECT"
    role = "dynamo_compact"
    display_name = "Device Health"

    bindings = (
        Bind("pv.value", "{path}/DEVICES"),
        Bind("sp.value", "{path}/FAILED"),
        Bind("out.value", "{path}/MAINT"),
        Bind("state.fail", "{path}/COMMFAIL"),
        Bind("state.name", "{path}/ADVISORY"),
    )


@register_pvm
class LabEntryCompact(PvmClass):
    """LE — Azeo's Lab Entry PVM.

    The lab value with how stale it is: a grab sample entered four
    hours ago and one entered four minutes ago are different facts,
    and DELAY is what separates them.
    """

    block_type = "LE"
    role = "dynamo_compact"
    display_name = "Lab Entry"

    bindings = (
        Bind("pv.value", "{path}/OUT"),
        Bind("sp.value", "{path}/LAST_VALUE"),
        Bind("out.value", "{path}/DELAY"),
        Bind("state.name", "{path}/MODE"),
        Bind("state.fail", "{path}/REJECTED"),
        Bind("pv.quality", "{path}/OUT", prop="StatusCode"),
    )
