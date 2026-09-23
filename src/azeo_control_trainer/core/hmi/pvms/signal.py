"""Signal/math PVM classes — sheet 7.4's remaining single-path set.

The low/high select faceplate names its winner: in a cross-limiting
scheme, *which input is currently selected* is the single most important
fact on the display and the one a P&ID hides. The ratio faceplate puts
actual against target as the two primary values so drift is read by
comparison, not arithmetic. SIF stays unbuilt on purpose — there is no
SIF block yet, and its colour-at-rest departure from §6 is one of the
wireframe's decisions-to-confirm.
"""
from __future__ import annotations

from .base import Bind, PvmClass, register_pvm


@register_pvm
class LowSelectFaceplate(PvmClass):
    block_type = "MIN_SELECT"
    role = "faceplate"
    display_name = "Low Select"
    FACEPLATE_LAYOUT = ("title", "selector", "buttons")

    bindings = (
        Bind("pv.value", "{path}/OUT"),
        Bind("out.value", "{path}/OUT"),
        Bind("selected", "{path}/SELECTED"),
        Bind("in1.value", "{path}/IN1"),
        Bind("in2.value", "{path}/IN2"),
        Bind("in3.value", "{path}/IN3"),
        Bind("in.1", "{path}/IN1"),
        Bind("in.2", "{path}/IN2"),
        Bind("in.3", "{path}/IN3"),
        Bind("out.quality", "{path}/OUT", prop="StatusCode"),
    )


@register_pvm
class HighSelectFaceplate(PvmClass):
    block_type = "MAX_SELECT"
    role = "faceplate"
    display_name = "High Select"
    FACEPLATE_LAYOUT = LowSelectFaceplate.FACEPLATE_LAYOUT

    bindings = LowSelectFaceplate.bindings


@register_pvm
class RatioFaceplate(PvmClass):
    block_type = "RATIO"
    role = "faceplate"
    display_name = "Ratio"
    FACEPLATE_LAYOUT = ("title", "value", "trend", "buttons")

    bindings = (
        Bind("pv.value", "{path}/OUT"),
        Bind("pv.quality", "{path}/OUT", prop="StatusCode"),
        Bind("field.value", "{path}/IN"),
        Bind("wild.value", "{path}/IN"),
        Bind("out.value", "{path}/OUT"),
        Bind("ratio.target", "{path}/CONFIG/RATIO",
             writable=True, persists=True),
        Bind("ratio.bias", "{path}/CONFIG/BIAS",
             writable=True, persists=True),
        Bind("ratio.hi", "{path}/CONFIG/RATIO_HI"),
        Bind("ratio.lo", "{path}/CONFIG/RATIO_LO"),
        # Actual against target — drift reads as a comparison.
        Bind("ratio.actual", expr="out.value / max(wild.value, 0.001)"),
        Bind("out.quality", "{path}/OUT", prop="StatusCode"),
    )


@register_pvm
class TotalizerFaceplate(PvmClass):
    block_type = "TOTALIZER"
    role = "faceplate"
    display_name = "Totalizer"
    FACEPLATE_LAYOUT = ("title", "value", "trend", "buttons")

    bindings = (
        Bind("pv.value", "{path}/OUT"),
        Bind("pv.quality", "{path}/OUT", prop="StatusCode"),
        Bind("field.value", "{path}/RATE"),
        Bind("total", "{path}/OUT"),
        Bind("rate", "{path}/RATE"),
        Bind("enable", "{path}/ENABLE"),
        # Reset from the faceplate is one of the wireframe's
        # decisions-to-confirm; the bind is writable so the role gate
        # decides, not the class.
        Bind("cmd.reset", "{path}/RESET", writable=True),
        Bind("target", "{path}/CONFIG/HI_LIMIT",
             writable=True, persists=True),
        Bind("total.quality", "{path}/OUT", prop="StatusCode"),
    )
