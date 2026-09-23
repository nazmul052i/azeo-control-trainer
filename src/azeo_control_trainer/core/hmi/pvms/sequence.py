"""Sequence PVM classes — the SFC chart faceplate.

The catalog said "build the faceplate when the blocks land"; they have
(`SFC_CHART`, with `sequence_snapshot()` as the running-chart contract).
The faceplate answers the operator's actual questions: which step is
active, how long, what transition is next and why it hasn't fired, and
hold / restart.
"""
from __future__ import annotations

from .base import Bind, PvmClass, register_pvm


@register_pvm
class SfcChartFaceplate(PvmClass):
    block_type = "SFC_CHART"
    role = "faceplate"
    display_name = "Sequence"
    FACEPLATE_LAYOUT = ("title", "buttons")

    bindings = (
        Bind("step.active", "{path}/ACTIVE"),
        Bind("step.number", "{path}/STEP_NO"),
        Bind("step.time", "{path}/STEP_TIME"),
        Bind("state.done", "{path}/DONE"),
        Bind("state.held", "{path}/HELD"),
        Bind("state.fault", "{path}/FAULT"),
        Bind("cmd.enable", "{path}/EN_D", writable=True),
        Bind("cmd.reset", "{path}/RESET_D", writable=True),
        Bind("state.quality", "{path}/ACTIVE", prop="StatusCode"),
        # The chart structure itself — steps and transitions — for the
        # running-chart panel. Config read: static, Good quality.
        Bind("chart", "{path}/CONFIG/CHART"),
    )


@register_pvm
class SfcChartDetail(PvmClass):
    """The running chart itself — steps coloured by ACTIVE/COMPLETE/idle,
    fed by `sequence_snapshot()` on the live block."""

    block_type = "SFC_CHART"
    role = "detail"
    display_name = "Sequence Detail"

    bindings = SfcChartFaceplate.bindings
