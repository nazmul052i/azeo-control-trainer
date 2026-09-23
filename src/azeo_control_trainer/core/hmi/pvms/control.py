"""Control PVM classes — the PID family and the one documented exception.

One `PIDFaceplate` drives every loop; cascade masters and slaves share
it, because the mode set comes from the type, not a flag. The cascade
*pair* view is the catalog's Part 3 exception: it genuinely takes two
paths, so it declares `EXCEPTION` and is logged at registration — the
single-parameter rule stays enforceable while staying honest.
"""
from __future__ import annotations

from .base import Bind, PvmClass, register_pvm

_PID_STATE = (
    Bind("pv.quality", "{path}/PV", prop="StatusCode"),
    Bind("pv.forced", "{path}/PV", prop="Forced"),
    Bind("out.limit", "{path}/OUT", prop="Limit"),
    Bind("mode", "{path}/MODE"),
)


@register_pvm
class PIDFaceplate(PvmClass):
    block_type = "PID"
    role = "faceplate"
    display_name = "PID Controller"
    FACEPLATE_LAYOUT = ("title", "value", "mode", "trend", "alarms",
                        "unit", "buttons")

    bindings = (
        Bind("pv.value", "{path}/PV"),
        Bind("sp.value", "{path}/SP", writable=True),
        Bind("sp.working", "{path}/SP_WRK"),
        Bind("out.value", "{path}/OUT", writable=True),
        Bind("pv.units", "{path}/PV", prop="EngineeringUnits"),
        Bind("pv.range", "{path}/PV", prop="EURange"),
        Bind("out.units", "{path}/OUT", prop="EngineeringUnits"),
        Bind("out.range", "{path}/OUT", prop="EURange"),
        Bind("deviation", expr="pv.value - sp.value"),
        Bind("limits.lo", "{path}/CONFIG/lo_lim",
             writable=True, persists=True),
        Bind("limits.lo_lo", "{path}/CONFIG/lo_lo_lim",
             writable=True, persists=True),
        Bind("limits.hi", "{path}/CONFIG/hi_lim",
             writable=True, persists=True),
        Bind("limits.hi_hi", "{path}/CONFIG/hi_hi_lim",
             writable=True, persists=True),
        Bind("limits.out_hi", "{path}/CONFIG/out_hi",
             writable=True, persists=True),
        Bind("limits.out_lo", "{path}/CONFIG/out_lo",
             writable=True, persists=True),
        Bind("limits.sp_hi", "{path}/CONFIG/sp_hi",
             writable=True, persists=True),
        Bind("limits.sp_lo", "{path}/CONFIG/sp_lo",
             writable=True, persists=True),
        Bind("simulate", "{path}/CONFIG/simulate_enabled",
             writable=True, persists=True),
        Bind("adaptive.enabled", "{path}/CONFIG/use_pidplus"),
        Bind("bypass.enabled", "{path}/CONFIG/bypass_enable"),
        Bind("bypass.value", "{path}/CONFIG/bypass",
             writable=True, persists=True),
        Bind("tuning.gain", "{path}/CONFIG/gain",
             writable=True, persists=True),
        Bind("tuning.reset", "{path}/CONFIG/reset",
             writable=True, persists=True),
        Bind("tuning.rate", "{path}/CONFIG/rate",
             writable=True, persists=True),
        # MODE itself is feedback; .TARGET is the controller's checked
        # operator handle used by Loop_fp's actual-mode dropdown.
        Bind("mode.command", "{path}/MODE.TARGET", writable=True),
    ) + _PID_STATE


@register_pvm
class PIDInline(PvmClass):
    block_type = "PID"
    role = "dynamo_inline"
    display_name = "PID Controller"

    bindings = (
        Bind("pv.value", "{path}/PV"),
        Bind("sp.value", "{path}/SP"),
        Bind("out.value", "{path}/OUT"),
        Bind("pv.units", "{path}/PV", prop="EngineeringUnits"),
        Bind("pv.range", "{path}/PV", prop="EURange"),
    ) + _PID_STATE


@register_pvm
class PIDCompact(PvmClass):
    block_type = "PID"
    role = "dynamo_compact"
    display_name = "PID Controller"

    bindings = (
        Bind("pv.value", "{path}/PV"),
        Bind("pv.units", "{path}/PV", prop="EngineeringUnits"),
    ) + _PID_STATE


@register_pvm
class PIDDetail(PvmClass):
    block_type = "PID"
    role = "detail"
    display_name = "PID Detail"

    bindings = PIDFaceplate.bindings + (
        Bind("module.description", "{path}/CONFIG/description"),
        Bind("limits.dev_hi", "{path}/CONFIG/dv_hi_lim",
             writable=True, persists=True),
        Bind("limits.dev_lo", "{path}/CONFIG/dv_lo_lim",
             writable=True, persists=True),
        Bind("limits.arw_hi", "{path}/CONFIG/arw_hi",
             writable=True, persists=True),
        Bind("limits.arw_lo", "{path}/CONFIG/arw_lo",
             writable=True, persists=True),
        Bind("limits.hys", "{path}/CONFIG/alarm_hys",
             writable=True, persists=True),
        Bind("simulate.enabled", "{path}/CONFIG/simulate_enabled",
             writable=True, persists=True),
        Bind("simulate.value", "{path}/SIMULATE_IN", writable=True),
        Bind("field.value", "{path}/IN"),
        Bind("tuning.pv_filter", "{path}/CONFIG/pv_ftime",
             writable=True, persists=True),
        Bind("tuning.sp_filter", "{path}/CONFIG/sp_ftime",
             writable=True, persists=True),
        Bind("tuning.sp_rate_dn", "{path}/CONFIG/sp_rate_dn",
             writable=True, persists=True),
        Bind("tuning.sp_rate_up", "{path}/CONFIG/sp_rate_up",
             writable=True, persists=True),
        Bind("tuning.structure", "{path}/CONFIG/structure",
             writable=True, persists=True),
        Bind("tuning.beta", "{path}/CONFIG/beta",
             writable=True, persists=True),
        Bind("tuning.gamma", "{path}/CONFIG/gamma",
             writable=True, persists=True),
        Bind("tuning.bias", "{path}/CONFIG/bias",
             writable=True, persists=True),
        Bind("tuning.ideadband", "{path}/CONFIG/ideadband",
             writable=True, persists=True),
        Bind("tuning.ff_enabled", "{path}/CONFIG/ff_enable"),
        Bind("tuning.ff_gain", "{path}/CONFIG/ff_gain",
             writable=True, persists=True),
        Bind("tuning.adaptive", "{path}/CONFIG/use_pidplus",
             writable=True, persists=True),
        Bind("terms.p", "{path}/P_TERM"),
        Bind("terms.i", "{path}/I_TERM"),
        Bind("terms.d", "{path}/D_TERM"),
        Bind("bkcal.value", "{path}/BKCAL_OUT"),
    )


@register_pvm
class FLCFaceplate(PvmClass):
    """FLC_fp reuses Loop_fp as specified by the Azeo help."""

    block_type = "FLC"
    role = "faceplate"
    display_name = "Fuzzy Logic Controller"
    FACEPLATE_LAYOUT = PIDFaceplate.FACEPLATE_LAYOUT

    bindings = (
        Bind("pv.value", "{path}/PV"),
        Bind("sp.value", "{path}/SP", writable=True),
        Bind("sp.working", "{path}/SP"),
        Bind("out.value", "{path}/OUT", writable=True),
        Bind("pv.units", "{path}/PV", prop="EngineeringUnits"),
        Bind("pv.range", "{path}/PV", prop="EURange"),
        Bind("out.units", "{path}/OUT", prop="EngineeringUnits"),
        Bind("out.range", "{path}/OUT", prop="EURange"),
        Bind("limits.out_hi", "{path}/CONFIG/OUT_HI_LIM"),
        Bind("limits.out_lo", "{path}/CONFIG/OUT_LO_LIM"),
        Bind("limits.sp_hi", "{path}/CONFIG/SP_HI_LIM"),
        Bind("limits.sp_lo", "{path}/CONFIG/SP_LO_LIM"),
        Bind("mode.command", "{path}/MODE.TARGET", writable=True),
        Bind("mode", "{path}/MODE"),
        Bind("pv.quality", "{path}/PV", prop="StatusCode"),
        Bind("pv.forced", "{path}/PV", prop="Forced"),
        Bind("out.limit", "{path}/OUT", prop="Limit"),
    )


@register_pvm
class FLCInline(PvmClass):
    block_type = "FLC"
    role = "dynamo_inline"
    display_name = "Fuzzy Logic Controller"

    bindings = (
        Bind("pv.value", "{path}/PV"),
        Bind("sp.value", "{path}/SP"),
        Bind("out.value", "{path}/OUT"),
        Bind("pv.units", "{path}/PV", prop="EngineeringUnits"),
        Bind("pv.range", "{path}/PV", prop="EURange"),
    ) + _PID_STATE


@register_pvm
class FLCCompact(PvmClass):
    block_type = "FLC"
    role = "dynamo_compact"
    display_name = "Fuzzy Logic Controller"

    bindings = (
        Bind("pv.value", "{path}/PV"),
        Bind("pv.units", "{path}/PV", prop="EngineeringUnits"),
    ) + _PID_STATE


@register_pvm
class CascadePairFaceplate(PvmClass):
    """Composite view of two related loops — master over slave, stacked.

    The state that matters most: the slave not in CAS means the cascade
    is broken, however healthy each loop looks alone.
    """

    block_type = "PID"
    role = "faceplate"
    variant = "cascade_pair"
    display_name = "Cascade Pair"
    PARAMS = ("master", "slave")
    EXCEPTION = ("Composite view of two related loops; see "
                 "PVM_CLASS_CATALOG.md Part 3.")
    FACEPLATE_LAYOUT = ("title", "buttons")

    bindings = (
        Bind("master.pv", "{master}/PV"),
        Bind("master.sp", "{master}/SP", writable=True),
        Bind("master.out", "{master}/OUT"),
        Bind("master.mode", "{master}/MODE"),
        Bind("slave.pv", "{slave}/PV"),
        Bind("slave.sp", "{slave}/SP"),
        Bind("slave.out", "{slave}/OUT", writable=True),
        Bind("slave.mode", "{slave}/MODE"),
    )
