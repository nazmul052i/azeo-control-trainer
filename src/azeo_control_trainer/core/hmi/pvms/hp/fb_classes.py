"""Function-block PVM classes — the `AdvancedFunctionBlock` set.

These classes bind to a single *function block* rather than to a module.
That keeps every datalink on a faceplate or detail display rooted at one
well-defined block path.

Our binding grammar is already block-granular (`MODULE/BLOCK/PARAM`),
so nothing new was needed to address them — only classes that name the
right terminals.

**Both of these are deliberately tiny.** `Controller_STD_` and
`Controller_SEQ_` each expose the function block's STATE value. A PVM
that shows one number is still worth
having when the alternative is an engineer hand-drawing a text box and
binding it themselves on every display.
"""
from __future__ import annotations

from ..base import Bind, PvmClass, register_pvm


@register_pvm
class ControllerSEQPvm(PvmClass):
    """Step Sequencer state — Azeo's `Controller_SEQ_`.

    The faceplate this opens defaults to `SEQ_fp` in Azeo. We have
    the block and now the PVM; the faceplate is recorded as a gap
    rather than stubbed, because a faceplate that opens empty is worse
    than a PVM that says plainly it has none.
    """

    block_type = "SEQ"
    role = "dynamo_compact"
    display_name = "Step Sequencer"
    DEFAULT_SIZE = (110.0, 44.0)

    bindings = (
        Bind("pv.value", "{path}/STATE"),
        Bind("pv.quality", "{path}/STATE", prop="StatusCode"),
        Bind("state.name", "{path}/STATE"),
    )


@register_pvm
class ControllerSTDPvm(PvmClass):
    """State Transition Diagram state — Azeo's `Controller_STD_`."""

    block_type = "STD"
    role = "dynamo_compact"
    display_name = "State Transition Diagram"
    DEFAULT_SIZE = (110.0, 44.0)

    bindings = (
        Bind("pv.value", "{path}/STATE"),
        Bind("pv.quality", "{path}/STATE", prop="StatusCode"),
        Bind("state.name", "{path}/STATE"),
    )


@register_pvm
class ISELDynamo(PvmClass):
    """The Input Selector on a display — its OUT and which input won.

    Placed alongside `ISELFaceplate`: a block an engineer can open but
    cannot draw is a dead end, and `tests/_smoke_operator.py` asserts
    the pairing. `SELECTED` is the interesting half — three
    transmitters agreeing is unremarkable, and *which* one the
    algorithm is using is what an operator wants when they disagree.
    """

    block_type = "ISEL"
    role = "dynamo_compact"
    display_name = "Input Selector"
    DEFAULT_SIZE = (110.0, 44.0)

    bindings = (
        Bind("pv.value", "{path}/OUT"),
        Bind("pv.quality", "{path}/OUT", prop="StatusCode"),
        Bind("pv.units", "{path}/OUT", prop="EngineeringUnits"),
        Bind("pv.range", "{path}/OUT", prop="EURange"),
        Bind("state.name", "{path}/SELECTED"),
    )


#: The DC/EDC abnormal-status conditions, bound off `DEVCTL`'s own
#: terminals. `PVMS+FB.pdf` p14 gives the conditions exactly; ours map
#: like this, and the polarity is the trap:
#:
#: - **Interlocked** — "the DC block's DC_STATE parameter is
#:   Shutdown/Interlock". `DEVCTL.STATE` is an INT and 5 is that state
#:   (`DeviceControlBlock._INTERLOCKED`), so the PVM compares against
#:   it rather than reading the INTERLOCK input, which is a permit and
#:   not a state.
#: - **No permit** — `PERMISSIVE_D`, and note **True means permit
#:   granted**. The icon shows on the FALSE case; reading it the
#:   obvious way round lights "no permit" on every healthy device.
#: - **Tracking** — the PID's `TRK_IN_D`.
#: - **Bypassed** — block-level bypass, which is not a terminal here.
DEVICE_CONDITION_BINDS = (
    Bind("cond.state", "{path}/STATE"),
    Bind("cond.permissive", "{path}/PERMISSIVE_D"),
    Bind("cond.interlock", "{path}/INTERLOCK"),
)


VOTER_PVM_KEYS: dict[str, tuple[str, str, str]] = {}
CEM_EFFECT_KEYS: dict[str, tuple[str, str, str]] = {}
ADVANCED_CONTROL_KEYS: dict[str, tuple[str, str, str]] = {
    "INSPECT": ("INSPECT", "dynamo_compact", ""),
    "LAB": ("LE", "dynamo_compact", ""),
}


def _register_voter_inputs() -> None:
    """Register the AVTR/DVTR per-input PVM families from PVMS+FB.

    The first three keep the manual's `1Input`..`3Input` vocabulary;
    inputs 4..16 are concrete forms of its `InputX` class. A concrete
    variant keeps the normal one-path PVM invariant and still lets an
    engineer place every extensible input without an inert index field.
    """
    for block_type, analog in (("AVTR", True), ("DVTR", False)):
        for index in range(1, 17):
            variant = "%dinput" % index if index <= 3 else "input%d" % index
            input_name = "IN%d" % index if analog else "IN_D%d" % index
            bindings = [
                Bind("pv.value", "{path}/%s" % input_name),
                Bind("state.name", "{path}/CONFIG/DESC%d" % index),
                Bind("state.tripped", "{path}/TRIP_VOTE_IN%d" % index),
                Bind("cond.bypassed", "{path}/CONFIG/BYPASS%d" % index),
                Bind("votes.required", "{path}/A_NUM_TO_TRIP"),
            ]
            if analog:
                bindings.extend((
                    Bind("sp.value", "{path}/CONFIG/TRIP_LIM"),
                    Bind("out.value", "{path}/CONFIG/PRE_TRIP_LIM"),
                    Bind("pre_trip.vote", "{path}/PRE_VOTE_IN%d" % index),
                ))
            class_name = "%sInput%dPvm" % (block_type, index)
            cls = type(class_name, (PvmClass,), {
                "__module__": __name__, "block_type": block_type,
                "role": "dynamo_compact", "variant": variant,
                "display_name": "%s Input %d" % (block_type, index),
                "bindings": tuple(bindings),
            })
            globals()[class_name] = register_pvm(cls)
            VOTER_PVM_KEYS["%s_%s" % (block_type, variant)] = (
                block_type, cls.role, variant)

        # The catalogue's InputX tile is the aggregate route into any
        # concrete row above, and remains useful as a block summary.
        variant = "inputx"
        class_name = "%sInputXPvm" % block_type
        cls = type(class_name, (PvmClass,), {
            "__module__": __name__, "block_type": block_type,
            "role": "dynamo_compact", "variant": variant,
            "display_name": "%s Input X" % block_type,
            "bindings": (
                Bind("pv.value", "{path}/TRIP_VOTES"),
                Bind("sp.value", "{path}/A_NUM_TO_TRIP"),
                Bind("state.name", "{path}/TRIP_STATUS"),
                Bind("state.fail", "{path}/OUT_D_GOOD"),
            ),
        })
        globals()[class_name] = register_pvm(cls)
        VOTER_PVM_KEYS["%s_inputx" % block_type] = (
            block_type, cls.role, variant)


def _register_cem_effects() -> None:
    """One CEM PVM per effect, including first-out and override state."""
    for index in range(1, 17):
        variant = "effect%d" % index
        class_name = "CEMEffect%dPvm" % index
        cls = type(class_name, (PvmClass,), {
            "__module__": __name__, "block_type": "CEM",
            "role": "dynamo_compact", "variant": variant,
            "display_name": "CEM Effect %d" % index,
            "bindings": (
                Bind("effect.normal", "{path}/OUT%d" % index),
                Bind("pv.value", "{path}/OUT%d" % index),
                Bind("state.name", "{path}/STATE%d" % index),
                Bind("first_out.value", "{path}/FIRST_OUT_%d" % index),
                Bind("override.value",
                     "{path}/CONFIG/FORCE_EFFECT_%d" % index),
                Bind("state.tripped", expr="1 - effect.normal"),
            ),
        })
        globals()[class_name] = register_pvm(cls)
        CEM_EFFECT_KEYS[variant] = ("CEM", cls.role, variant)


def _register_advanced_control() -> None:
    """MPC family names over the trainer's running DMC controller.

    This is not a painted placeholder: each variant binds the actual
    controller state, heartbeat, cycle time and fault flag.  The three
    product names share one runtime algorithm here, which is a narrower
    trainer implementation than Azeo's licensed product families.
    """
    for name in ("MPC", "MPCPlus", "MPCPro"):
        variant = name.lower()
        class_name = name + "ControllerPvm"
        cls = type(class_name, (PvmClass,), {
            "__module__": __name__, "block_type": "DMC_CONTROLLER",
            "role": "dynamo_compact", "variant": variant,
            "display_name": name,
            "bindings": (
                Bind("state.name", "{path}/STATE_NAME"),
                Bind("pv.value", "{path}/HEARTBEAT"),
                Bind("out.value", "{path}/CYCLE_TIME"),
                Bind("state.fail", "{path}/FAULT"),
                Bind("state.tripped", "{path}/ACTIVE"),
            ),
        })
        globals()[class_name] = register_pvm(cls)
        ADVANCED_CONTROL_KEYS[name] = (
            "DMC_CONTROLLER", cls.role, variant)


_register_voter_inputs()
_register_cem_effects()
_register_advanced_control()
