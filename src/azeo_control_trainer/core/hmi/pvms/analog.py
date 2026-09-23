"""Analog PVM classes — AI, PIN (the shared-shell case), AO.

`AO` is the catalog's "significant gap": it is not an AI with the arrow
reversed. The operator needs OUT, the field readback and the
back-calculation value together, because divergence between them is how
a stuck valve announces itself.
"""
from __future__ import annotations

from .base import Bind, PvmClass, register_pvm


@register_pvm
class AnalogFaceplate(PvmClass):
    """AI_fp — the Analog Monitoring module faceplate.

    The layout follows the installed analog-faceplate contract. It is a
    tuple of section names, not a widget: see
    `faceplate_ui` for the sections and `docs/FACEPLATE_UI.md` for how
    to give another block type the same treatment.

    AI carries every section, which is why it was the one to build
    first — a faceplate that omits sections cannot show that the
    omitted ones work.
    """

    block_type = "AI"
    role = "faceplate"
    display_name = "Analog Input"

    #: The installed AI faceplate stack, top to bottom.
    FACEPLATE_LAYOUT = ("title", "value", "pv_bar", "mode", "trend",
                        "alarms", "unit", "buttons")

    bindings = (
        Bind("pv.value", "{path}/OUT"),
        Bind("pv.units", "{path}/OUT", prop="EngineeringUnits"),
        Bind("pv.range", "{path}/OUT", prop="EURange"),
        Bind("pv.quality", "{path}/OUT", prop="StatusCode"),
        Bind("pv.forced", "{path}/OUT", prop="Forced"),
        Bind("field.value", "{path}/FIELD_VAL"),
        # The mode row and the Ⓢ badge. AI has no setpoint, so the bar
        # simply draws no working-SP mark. An input-only block has no
        # setpoint to represent, so inventing one would mislead operators.
        Bind("mode", "{path}/MODE"),
        # AI_fp's circular S reports simulation, not Initiate Fault State.
        # The latter happened to be the only existing boolean output and made
        # a healthy simulated input look inactive (or a fault look simulated).
        Bind("simulate", "{path}/CONFIG/simulate_enabled"),
        Bind("limits.hi_hi", "{path}/CONFIG/HI_HI_LIM",
             writable=True, persists=True),
        Bind("limits.hi", "{path}/CONFIG/HI_LIM",
             writable=True, persists=True),
        Bind("limits.lo", "{path}/CONFIG/LO_LIM",
             writable=True, persists=True),
        Bind("limits.lo_lo", "{path}/CONFIG/LO_LO_LIM",
             writable=True, persists=True),
        Bind("tuning.filter", "{path}/CONFIG/PV_FTIME",
             writable=True, persists=True),
    )


@register_pvm
class AnalogDetail(PvmClass):
    """AI_dt — limits, simulation, tuning and diagnostics."""

    block_type = "AI"
    role = "detail"
    display_name = "Analog Input Detail"

    bindings = AnalogFaceplate.bindings + (
        Bind("module.description", "{path}/CONFIG/label"),
        Bind("limits.hys", "{path}/CONFIG/ALARM_HYS",
             writable=True, persists=True),
        Bind("limits.low_cut", "{path}/CONFIG/LOW_CUT",
             writable=True, persists=True),
        Bind("simulate.enabled", "{path}/CONFIG/simulate_enabled",
             writable=True, persists=True),
        Bind("simulate.value", "{path}/CONFIG/SIMULATE",
             writable=True, persists=True),
        Bind("tuning.pv_filter", "{path}/CONFIG/PV_FTIME",
             writable=True, persists=True),
        # AI_dt documents the linearization type as an indication. Its value
        # is selected in block configuration, so it must not look like one of
        # the hash-mark online entry fields.
        Bind("linearization.type", "{path}/CONFIG/L_TYPE"),
    )


@register_pvm
class PulseInputFaceplate(AnalogFaceplate):
    """AI and PIN share a shell, differing only in the tuning section —
    a small subclass, not a copy (catalog Part 2)."""

    block_type = "PIN"
    role = "faceplate"
    display_name = "Pulse Input"

    #: AI's stack, unchanged — the two blocks differ in tuning, not in
    #: what an operator looks at.
    FACEPLATE_LAYOUT = AnalogFaceplate.FACEPLATE_LAYOUT

    bindings = tuple(
        # PIN names its simulation flag SIMULATE_ACT where AI names it
        # IFS_ACT. Inheriting AI's bind wholesale pointed at a terminal
        # PIN does not have, which renders Bad for ever — the binding
        # integrity check caught it, which is what that check is for.
        b for b in AnalogFaceplate.bindings[:-1] if b.key != "simulate"
    ) + (
        Bind("simulate", "{path}/SIMULATE_ACT"),
        # PULSE_VAL, not PULSE_VALUE. The config lookup is
        # case-tolerant but not synonym-tolerant, so this resolved to
        # nothing and the pulse row rendered Bad for ever — which on a
        # faceplate is indistinguishable from a dead transmitter.
        Bind("tuning.pulse", "{path}/CONFIG/PULSE_VAL",
             writable=True, persists=True),
        Bind("tuning.time_units", "{path}/CONFIG/TIME_UNITS",
             writable=True, persists=True),
    )


@register_pvm
class PulseInputDetail(PvmClass):
    """PI_dt — the pulse monitor variant of the analog detail shell."""

    block_type = "PIN"
    role = "detail"
    display_name = "Pulse Input Detail"

    bindings = PulseInputFaceplate.bindings + (
        Bind("limits.hys", "{path}/CONFIG/ALARM_HYS",
             writable=True, persists=True),
        Bind("simulate.enabled", "{path}/CONFIG/SIMULATE",
             writable=True, persists=True),
        Bind("simulate.value", "{path}/SIMULATE_IN", writable=True),
        Bind("tuning.pv_filter", "{path}/CONFIG/PV_FTIME",
             writable=True, persists=True),
        Bind("linearization.type", "{path}/CONFIG/INPUT_TYPE",
             writable=True, persists=True),
    )


@register_pvm
class AnalogCompact(PvmClass):
    block_type = "AI"
    role = "dynamo_compact"
    display_name = "Analog Indicator"

    bindings = (
        Bind("pv.value", "{path}/OUT"),
        Bind("pv.units", "{path}/OUT", prop="EngineeringUnits"),
        Bind("pv.quality", "{path}/OUT", prop="StatusCode"),
        Bind("pv.forced", "{path}/OUT", prop="Forced"),
    )


@register_pvm
class AnalogOutputFaceplate(PvmClass):
    block_type = "AO"
    role = "faceplate"
    display_name = "Analog Output"
    FACEPLATE_LAYOUT = ("title", "value", "mode", "trend", "alarms",
                        "unit", "buttons")

    bindings = (
        Bind("pv.value", "{path}/OUT"),
        Bind("pv.units", "{path}/OUT", prop="EngineeringUnits"),
        Bind("pv.range", "{path}/OUT", prop="EURange"),
        Bind("pv.quality", "{path}/OUT", prop="StatusCode"),
        Bind("field.value", "{path}/READBACK"),
        Bind("out.value", "{path}/OUT"),
        Bind("out.units", "{path}/OUT", prop="EngineeringUnits"),
        Bind("out.range", "{path}/OUT", prop="EURange"),
        Bind("out.limit", "{path}/OUT", prop="Limit"),
        Bind("out.quality", "{path}/OUT", prop="StatusCode"),
        Bind("sp.value", "{path}/CAS_IN", writable=True),
        Bind("bkcal.value", "{path}/BKCAL_OUT"),
        Bind("readback.value", "{path}/READBACK"),
        # The state that matters most: readback diverging from OUT.
        Bind("divergence", expr="out.value - readback.value"),
        Bind("mode", "{path}/MODE"),
    )


@register_pvm
class AnalogOutputDetail(PvmClass):
    """TAGAO-style output limits, simulation and diagnostics."""

    block_type = "AO"
    role = "detail"
    display_name = "Analog Output Detail"

    bindings = AnalogOutputFaceplate.bindings + (
        Bind("limits.out_hi", "{path}/CONFIG/out_hi",
             writable=True, persists=True),
        Bind("limits.out_lo", "{path}/CONFIG/out_lo",
             writable=True, persists=True),
        Bind("limits.sp_hi", "{path}/CONFIG/sp_hi_lim",
             writable=True, persists=True),
        Bind("limits.sp_lo", "{path}/CONFIG/sp_lo_lim",
             writable=True, persists=True),
        Bind("simulate.enabled", "{path}/CONFIG/simulate_enabled",
             writable=True, persists=True),
        Bind("simulate.value", "{path}/CONFIG/SIMULATE",
             writable=True, persists=True),
        Bind("tuning.sp_rate_up", "{path}/CONFIG/sp_rate_up",
             writable=True, persists=True),
        Bind("tuning.sp_rate_dn", "{path}/CONFIG/sp_rate_dn",
             writable=True, persists=True),
        Bind("tuning.fstate_value", "{path}/CONFIG/FSTATE_VAL",
             writable=True, persists=True),
        Bind("tuning.fstate_time", "{path}/CONFIG/FSTATE_TIME",
             writable=True, persists=True),
    )
