"""Composite PVM classes — sheet 7.5, the multi-path exceptions.

Balance is a relationship: four separate faceplates cannot show a
spread from the mean. `PassBalanceFaceplate` is the second — and, with
the cascade pair, the LAST — declarative exception to the
single-parameter rule. Sheet 7.6: two exceptions is a design; a growing
list means the rule is wrong and should be changed deliberately.

The heater Combustion composite stays unbuilt: it is fired-heater
content (cross-limit naming, O₂/CO) and this repo's areas are the
discharge train. It belongs with a heater area, not ahead of one.
"""
from __future__ import annotations

from .base import Bind, PvmClass, register_pvm


@register_pvm
class PassBalanceFaceplate(PvmClass):
    """Four parallel paths compared against their mean."""

    block_type = "AI"
    role = "faceplate"
    variant = "pass_balance"
    display_name = "Pass Balance"
    PARAMS = ("a", "b", "c", "d")
    EXCEPTION = ("Balance is a relationship across four parallel paths; "
                 "see PVM_CLASS_CATALOG.md and wireframe sheet 7.5.")
    FACEPLATE_LAYOUT = ("title", "buttons")

    bindings = (
        Bind("a.value", "{a}/OUT"),
        Bind("b.value", "{b}/OUT"),
        Bind("c.value", "{c}/OUT"),
        Bind("d.value", "{d}/OUT"),
        Bind("mean", expr="(a.value + b.value + c.value + d.value) / 4"),
        Bind("spread", expr="max(a.value, b.value, c.value, d.value) "
                            "- min(a.value, b.value, c.value, d.value)"),
    )
