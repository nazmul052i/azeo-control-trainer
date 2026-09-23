"""Function-block faceplates — the `FaceplateFB` set, as PVM classes.

**A faceplate is a tuple of section names, not a widget.** Each class
declares `FACEPLATE_LAYOUT` and `render.PvmFaceplateWidget` assembles
exactly those, in the shared order. Adding one is choosing a tuple and
naming bindings — which is what stops the fifteenth faceplate from
being a fifteenth layout.

**Built against the blocks we actually have.** Where a trainer block has
a narrow contract, the faceplate shows that real contract. It never fills
missing rows with healthy-looking defaults.
"""
from __future__ import annotations

from ..base import Bind, PvmClass, register_pvm


@register_pvm
class SEQFaceplate(PvmClass):
    """SEQ_fp — current state over the sequencer's output descriptions.

    The figure lists `DESC_OUTx` coloured by `OUT_Dx`; ours binds the
    descriptions from config and the flags from the outputs, so a row
    reads ON exactly when its output is driven.
    """

    block_type = "SEQ"
    role = "faceplate"
    display_name = "Step Sequencer"
    FACEPLATE_LAYOUT = ("title", "state_list", "buttons")

    bindings = (
        Bind("state.name", "{path}/STATE"),
        Bind("pv.value", "{path}/STATE"),
        Bind("pv.quality", "{path}/STATE", prop="StatusCode"),
        Bind("num_rows", "{path}/CONFIG/NUM_OUTPUTS"),
    ) + tuple(
        b for i in range(1, 17) for b in (
            Bind("row%d" % i, "{path}/CONFIG/DESC_OUT%d" % i),
            Bind("row%d.state" % i, "{path}/OUT_D%d" % i),
        )
    )


@register_pvm
class STDFaceplate(PvmClass):
    """STD_fp — current state over the transitions being evaluated.

    The figure pairs each transition condition (`DESC_INx`, greyed when
    inactive) with the state it would move to. We bind the descriptions,
    input flags, current state and the genuinely addressable MATRIX config;
    the fixed surface derives the current row's destination cells without
    inventing per-transition parameters the block does not publish.
    """

    block_type = "STD"
    role = "faceplate"
    display_name = "State Transition Diagram"
    FACEPLATE_LAYOUT = ("title", "state_list", "buttons")

    bindings = (
        Bind("state.name", "{path}/STATE"),
        Bind("pv.value", "{path}/STATE"),
        Bind("pv.quality", "{path}/STATE", prop="StatusCode"),
        Bind("num_rows", "{path}/CONFIG/NUM_TRANS"),
        Bind("matrix", "{path}/CONFIG/MATRIX"),
    ) + tuple(
        b for i in range(1, 17) for b in (
            Bind("row%d" % i, "{path}/CONFIG/DESC_IN%d" % i),
            Bind("row%d.state" % i, "{path}/IN_D%d" % i),
        )
    )


@register_pvm
class ISELFaceplate(PvmClass):
    """Xmtr_fp — the Input Selector's four inputs and its algorithm."""

    block_type = "ISEL"
    role = "faceplate"
    display_name = "Input Selector"
    FACEPLATE_LAYOUT = ("title", "selector", "buttons")

    bindings = (
        Bind("out.value", "{path}/OUT"),
        Bind("out.quality", "{path}/OUT", prop="StatusCode"),
        Bind("pv.value", "{path}/OUT"),
        Bind("pv.quality", "{path}/OUT", prop="StatusCode"),
        Bind("select_type", "{path}/CONFIG/SELECT_TYPE"),
        # Xmtr_fp's radio group is an operator selection, not a reflection of
        # the algorithm-selected output.  OP_SELECT 0 means use the configured
        # selection type; 1..4 force the matching input.
        Bind("operator_selection", "{path}/CONFIG/OP_SELECT",
             writable=True, persists=True),
        Bind("selected", "{path}/SELECTED"),
    ) + tuple(
        b for i in (1, 2, 3, 4) for b in (
            Bind("in%d.value" % i, "{path}/IN_%d" % i),
            Bind("in%d.disabled" % i, "{path}/DISABLE_%d" % i,
                 writable=True),
        )
    )


@register_pvm
class ATFaceplate(PvmClass):
    """AT_fp for the trainer's analog-tracking block.

    Azeo's AT includes a condition table; this block exposes a tracking
    request and its clamped result instead. Showing those real values is
    preferable to a permanently empty condition table.
    """

    block_type = "AT"
    role = "faceplate"
    display_name = "Analog Tracking"
    FACEPLATE_LAYOUT = ("title", "value", "trend", "buttons")
    bindings = (
        Bind("pv.value", "{path}/TRK_VAL_OUT"),
        Bind("pv.quality", "{path}/TRK_VAL_OUT", prop="StatusCode"),
        Bind("field.value", "{path}/IN"),
        Bind("state.active", "{path}/ACTIVE"),
        Bind("request", "{path}/TRK_REQ_D"),
    )


def _voter_bindings(*, analog: bool) -> tuple:
    input_name = "IN%d" if analog else "IN_D%d"
    bindings = [
        Bind("pv.value", "{path}/TRIP_VOTES"),
        Bind("trip_votes", "{path}/TRIP_VOTES"),
        Bind("votes_required", "{path}/A_NUM_TO_TRIP"),
        Bind("num_inputs", "{path}/CONFIG/NUM_INPUTS"),
        Bind("trip_status", "{path}/TRIP_STATUS"),
        Bind("bypass_permit", "{path}/BYPASS_PERMIT"),
        Bind("bypass_timer", "{path}/BYPASS_TIMER"),
        Bind("startup.active", "{path}/STARTUP"),
        Bind("startup.timer", "{path}/STARTUP_TIMER"),
        Bind("startup.stable", "{path}/STABLE_TIMER"),
        Bind("startup.time_to_stable", "{path}/TIME_TO_STABLE"),
        Bind("alarm.trip", "{path}/ALM_TRIP_ACTIVE"),
        Bind("alarm.bypass", "{path}/ALM_BYPASS_ACTIVE"),
        Bind("alarm.startup", "{path}/ALM_STARTUP_OVERRIDE"),
        Bind("alarm.expiration", "{path}/ALM_EXPIRATION_REMINDER"),
        Bind("alarm.input_bad", "{path}/ALM_INPUT_BAD"),
        Bind("delay_timer", "{path}/DELAY_TIMER"),
        Bind("trip_delay", "{path}/CONFIG/TRIP_DELAY"),
        Bind("normal_delay", "{path}/CONFIG/NORMAL_DELAY"),
        Bind("bypass.allowed_time", "{path}/CONFIG/BYPASS_TIMEOUT"),
        Bind("bypass_timer_h", "{path}/BYPASS_TIMER_H"),
        Bind("bypass.reminder_time", "{path}/CONFIG/REMINDER_TIME"),
        Bind("startup.time", "{path}/CONFIG/STARTUP_TIME"),
        Bind("startup.stable_time", "{path}/CONFIG/STABLE_TIME"),
        Bind("alarm.bypassed_trip",
             "{path}/ALM_BYPASSED_INPUT_TRIPPED"),
    ]
    if analog:
        bindings.extend((
            Bind("detect_type", "{path}/CONFIG/DETECT_TYPE"),
            Bind("trip_limit", "{path}/CONFIG/TRIP_LIM"),
            Bind("pre_trip_limit", "{path}/CONFIG/PRE_TRIP_LIM"),
            Bind("pre_delay_timer", "{path}/PRE_DELAY_TIMER"),
            Bind("trip_hysteresis", "{path}/CONFIG/TRIP_HYS"),
            Bind("deviation.limit", "{path}/CONFIG/DEV_LIM"),
            Bind("deviation.hysteresis", "{path}/CONFIG/DEV_HYS"),
            Bind("pre_status", "{path}/PRE_TRIP_STATUS"),
            Bind("pre_votes", "{path}/PRE_VOTES"),
            Bind("alarm.pre_trip", "{path}/ALM_PRE_TRIP_ACTIVE"),
            Bind("alarm.deviation", "{path}/ALM_DEVIATION"),
            Bind("alarm.bypassed_pre_trip",
                 "{path}/ALM_BYPASSED_INPUT_PRE_TRIPPED"),
        ))
    for index in range(1, 17):
        bindings.extend((
            Bind("in%d.description" % index,
                 "{path}/CONFIG/DESC%d" % index),
            Bind("in%d.value" % index,
                 "{path}/" + input_name % index),
            Bind("in%d.vote" % index,
                 "{path}/TRIP_VOTE_IN%d" % index),
            Bind("in%d.bypassed" % index,
                 "{path}/CONFIG/BYPASS%d" % index),
        ))
        if analog:
            bindings.append(Bind(
                "in%d.pre_vote" % index,
                "{path}/PRE_VOTE_IN%d" % index))
    return tuple(bindings)


@register_pvm
class AVTRFaceplate(PvmClass):
    """Analog voter: Trip, Pre-Trip, Bypass, Startup and Alert tabs."""

    block_type = "AVTR"
    role = "faceplate"
    display_name = "Analog Voter"
    FACEPLATE_LAYOUT = ("title", "voter", "buttons")
    bindings = _voter_bindings(analog=True)


@register_pvm
class DVTRFaceplate(PvmClass):
    """Discrete voter: the shared five-tab voter body without pre-trip."""

    block_type = "DVTR"
    role = "faceplate"
    display_name = "Discrete Voter"
    FACEPLATE_LAYOUT = ("title", "voter", "buttons")
    bindings = _voter_bindings(analog=False)


@register_pvm
class CTLSLFaceplate(PvmClass):
    """ECTLSL_fp over the trainer's three-input Control Selector."""

    block_type = "CTLSL"
    role = "faceplate"
    display_name = "Control Selector"
    FACEPLATE_LAYOUT = ("title", "selector", "buttons")
    bindings = (
        Bind("out.value", "{path}/OUT"),
        Bind("pv.value", "{path}/OUT"),
        Bind("pv.quality", "{path}/OUT", prop="StatusCode"),
        Bind("select_type", "{path}/CONFIG/SEL_TYPE"),
        Bind("selected", "{path}/SELECTED"),
        Bind("mode", "{path}/MODE_ACT"),
    ) + tuple(
        b for index in (1, 2, 3) for b in (
            Bind("in%d.value" % index, "{path}/SEL_%d" % index),
            Bind("in%d.disabled" % index,
                 "{path}/BKCAL_SEL%d_ST" % index),
        )
    )


@register_pvm
class RampSoakFaceplate(PvmClass):
    """ERAMP-style operational view over the trainer's ramp/soak block."""

    block_type = "RAMP_SOAK"
    role = "faceplate"
    display_name = "Ramp / Soak"
    FACEPLATE_LAYOUT = ("title", "value", "trend", "buttons")
    bindings = (
        Bind("pv.value", "{path}/OUT"),
        Bind("pv.quality", "{path}/OUT", prop="StatusCode"),
        Bind("field.value", "{path}/SEGMENT"),
        Bind("state.running", "{path}/RUNNING"),
        Bind("state.complete", "{path}/COMPLETE"),
        Bind("cmd.start", "{path}/START", writable=True),
        Bind("cmd.stop", "{path}/STOP", writable=True),
        Bind("cmd.reset", "{path}/RESET", writable=True),
    )
