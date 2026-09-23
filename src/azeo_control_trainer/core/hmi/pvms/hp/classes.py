"""The High Performance PVM classes.

Registered as `hp` variants of the existing types, so the plain
`dynamo_inline` look every shipped display already draws stays exactly
where it was. An engineer opts a PVM into the HP face by picking the
variant; nothing changes underneath a display that did not ask.

**What makes these HP is what they bind.** The combination bar needs
the alarm limits and the working setpoint to say anything the plain bar
does not, and a binding a class does not declare simply does not draw —
so the limit regions and the SP_WRK tick are absent rather than
invented on a class that has no such parameter. That is the same rule
the rest of the PVM layer runs on: a binding that resolves to nothing
is bad quality, never zero.
"""
from __future__ import annotations

from ..base import Bind, PvmClass, register_pvm
from .style_metrics import SIZES

#: The alarm limits the combination bar draws its regions from. These
#: are CONFIG parameters, not terminals — an `AI` block holds limits
#: rather than emitting alarm outputs (the same fact
#: the shared alarm model derives its alarms from), and the
#: path grammar for a configured value is `{path}/CONFIG/<NAME>`. The
#: PID spells them lower case and the AI upper; neither is renameable
#: (hard-won item 4), so the two tuples stay separate rather than
#: relying on a case-tolerant lookup that is not part of the contract.
_AI_LIMITS = (
    Bind("limits.lo", "{path}/CONFIG/LO_LIM"),
    Bind("limits.lo_lo", "{path}/CONFIG/LO_LO_LIM"),
    Bind("limits.hi", "{path}/CONFIG/HI_LIM"),
    Bind("limits.hi_hi", "{path}/CONFIG/HI_HI_LIM"),
)
_PID_LIMITS = (
    Bind("limits.lo", "{path}/CONFIG/lo_lim"),
    Bind("limits.lo_lo", "{path}/CONFIG/lo_lo_lim"),
    Bind("limits.hi", "{path}/CONFIG/hi_lim"),
    Bind("limits.hi_hi", "{path}/CONFIG/hi_hi_lim"),
)

#: Shared state every HP PVM's outer furniture reads: quality feeds the
#: Bad I/O icon, mode feeds the abnormal-mode icon, and simulate feeds the
#: simulation icon. The PID publishes its working setpoint through RCAS_OUT;
#: classes without one simply omit that tick rather than inventing it.
_HP_STATE = (
    Bind("pv.quality", "{path}/PV", prop="StatusCode"),
    Bind("pv.forced", "{path}/PV", prop="Forced"),
    Bind("mode", "{path}/MODE"),
    Bind("simulate", "{path}/SIMULATE_IN"),
    Bind("cond.tracking", "{path}/TRK_IN_D"),
    Bind("cond.bypassed", "{path}/BYPASS"),
)


@register_pvm
class HPCombinationPvm(PvmClass):
    """PID with the High Performance combination bar.

    PV, SP and OUT as fixed-width fields beside a bar that carries the
    setpoint as a perpendicular tick — so a loop on setpoint reads as a
    'T' from across the room and a loop off it reads as a 't'.
    """

    block_type = "PID"
    role = "dynamo_inline"
    variant = "hp"
    display_name = "PID (High Performance)"
    DEFAULT_SIZE = SIZES["HP_MA1_CH_"]

    bindings = (
        Bind("pv.value", "{path}/PV"),
        Bind("sp.value", "{path}/SP"),
        # RCAS_OUT is the strategy block's published working setpoint;
        # the underlying PID core calls the same value SP_WRK.
        Bind("sp_wrk.value", "{path}/RCAS_OUT"),
        Bind("out.value", "{path}/OUT"),
        Bind("pv.units", "{path}/PV", prop="EngineeringUnits"),
        Bind("pv.range", "{path}/PV", prop="EURange"),
    ) + _PID_LIMITS + _HP_STATE


@register_pvm
class HPDeviationPvm(PvmClass):
    """PID with the deviation bar — SP pinned to the centre.

    The same data as `HPCombinationPvm`, asked a different question:
    not "where is PV in range" but "how far is it from target". Worth a
    separate class because a display full of loops that all sit near
    mid-range shows nothing on a combination bar and everything here.
    """

    block_type = "PID"
    role = "dynamo_inline"
    variant = "hp_deviation"
    display_name = "PID Deviation (High Performance)"
    DEFAULT_SIZE = SIZES["HP_MA1_DH_"]

    bindings = HPCombinationPvm.bindings


@register_pvm
class HPIndicatorPvm(PvmClass):
    """AI with the High Performance bar, setpoint indicators hidden.

    "The SP and SP_WRK indicators are hidden when the function block
    definition is the AI block." An indicator has no target, and
    drawing one offers the operator something to chase that no
    controller is pursuing.
    """

    block_type = "AI"
    role = "dynamo_inline"
    variant = "hp"
    display_name = "Indicator (High Performance)"
    DEFAULT_SIZE = SIZES["HP_MA3_CH_"]

    bindings = (
        Bind("pv.value", "{path}/OUT"),
        Bind("pv.units", "{path}/OUT", prop="EngineeringUnits"),
        Bind("pv.range", "{path}/OUT", prop="EURange"),
        Bind("pv.quality", "{path}/OUT", prop="StatusCode"),
        Bind("pv.forced", "{path}/OUT", prop="Forced"),
        Bind("simulate", "{path}/CONFIG/SIMULATE"),
    ) + _AI_LIMITS


@register_pvm
class HPAlarmsPvm(PvmClass):
    """Area-scoped active/suppressed/unacknowledged alarm rollup."""

    block_type = "AREA"
    role = "dynamo_inline"
    variant = "hp_alarms"
    display_name = "Area Alarms"
    DEFAULT_SIZE = SIZES["HP_Alarms"]
    bindings = ()


# Every remaining installed HP class is a typed variant over the same
# tested furniture. The exact class name and measured immutable size
# are retained so an engineer can transfer the catalogue vocabulary.
_FAMILIES = {
    "HP_AT_VLV_": "AT", "HP_B_Valve": "DEVCTL",
    "HP_C_Valve": "DEVCTL", "HP_EDC_PMP_": "EDC",
    "HP_EDC_VLV_": "EDC", "HP_HorizAnalog": "AI",
    "HP_M1_CH_S_": "PID", "HP_MA1_N_": "PID",
    "HP_MA1_VLV_": "PID", "HP_MA2_DH_": "PID",
    "HP_MA2_DV_M_": "PID", "HP_MA2_DV_S_": "PID",
    "HP_MA3_CV_M_": "AI", "HP_MA3_CV_ML_": "AI",
    "HP_MA3_CV_S_": "AI", "HP_MA3_CV_SL_": "AI",
    "HP_MA3_N_": "AI", "HP_MD1_PMP_": "DEVCTL",
    "HP_MD1_VLV_": "DEVCTL", "HP_MSHDI_": "DI",
    "HP_MSHDO_": "DO", "HP_Numeric": "AI",
    "HP_Pump": "DEVCTL", "HP_VertAnalog": "AI",
}

HP_CLASS_KEYS = {
    "HP_Alarms": ("AREA", "dynamo_inline", "hp_alarms"),
    "HP_MA1_CH_": ("PID", "dynamo_inline", "hp"),
    "HP_MA1_DH_": ("PID", "dynamo_inline", "hp_deviation"),
    "HP_MA3_CH_": ("AI", "dynamo_inline", "hp"),
}


def _register_catalogue() -> None:
    from ..base import registry

    for installed_name, block_type in _FAMILIES.items():
        base = registry.get(block_type, "dynamo_compact") \
            or registry.get(block_type, "dynamo_inline")
        if block_type == "PID":
            base = HPCombinationPvm
        elif block_type == "AI":
            base = HPIndicatorPvm
        if base is None:
            continue
        variant = "hp_" + installed_name[3:].strip("_").lower()
        class_name = installed_name.replace("_", "") + "Pvm"
        cls = type(class_name, (base,), {
            "__module__": __name__, "block_type": block_type,
            "role": "dynamo_inline" if block_type in ("AI", "PID")
            else "dynamo_compact",
            "variant": variant, "display_name": installed_name,
            "DEFAULT_SIZE": SIZES[installed_name],
            # The measured catalogue size is the placement default, not an
            # authoring lock. Compact valves in particular must be growable
            # through the same eight-handle transform contract as every PVM.
            "INSTALLED_NAME": installed_name, "RESIZABLE": True,
        })
        globals()[class_name] = register_pvm(cls)
        HP_CLASS_KEYS[installed_name] = (
            cls.block_type, cls.role, cls.variant)


_register_catalogue()
