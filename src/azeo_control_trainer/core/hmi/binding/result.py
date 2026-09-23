"""The binding result — everything a PVM needs, in one structure (§9.2).

Rev A collapsed a binding to `(value, quality, limit, forced)`; the
faceplates need more, and the proposal names the failure mode exactly:
**collapsing this to a float at any layer is what makes graphics lie.**
A PVM receives this whole record and renders the display-state contract
from it; nothing downstream re-derives quality from a number.
"""
from __future__ import annotations

from dataclasses import dataclass

from azeo_control_trainer.core.strategy.model.terminal import LimitStatus, Quality


@dataclass(frozen=True)
class BindingResult:
    """One resolved read. Frozen — a result is a snapshot, not a channel."""

    value: object = None
    quality: Quality = Quality.BAD
    limit: LimitStatus = LimitStatus.NOT_LIMITED
    forced: bool = False

    units: str = ""
    eu_range: tuple | None = None

    alarm_active: bool = False
    alarm_acked: bool = False
    alarm_priority: int = 0
    alarm_condition: str = ""
    breached_limit: float | None = None
    #: Module-wide operator state, supplied by the shared alarm registry.
    alarm_count: int = 0
    alarm_suppressed: bool = False
    #: Runtime execution state. None means the source cannot answer;
    #: False is the MSTATUS-equivalent "module not running" condition.
    module_running: bool | None = None

    #: Display state 1 needs these after quality goes Bad: the last good
    #: value and when it was good — shown explicitly labelled as history,
    #: never as a stale number pretending to be live.
    last_good_value: object = None
    last_good_at: float | None = None

    #: Mode triple where the bound block has one (display state 4).
    #: NORMAL is what the block should ordinarily be in; TARGET is what
    #: has been asked for; ACTUAL is where it is. Azeo needs all
    #: three — a loop sitting obediently in the mode an operator
    #: selected is still abnormal if that is not its normal mode, and
    #: only `mode_normal` can say so.
    mode_target: str = ""
    mode_actual: str = ""
    mode_normal: str = ""

    @property
    def mode_mismatch(self) -> bool:
        """Actual != NORMAL **or** actual != target.

        Both halves, per the Azeo manual (stated three times in
        `PVMs/PVMS+FB.pdf`): "the Actual mode is not equal to the
        Normal mode" OR "the Actual mode is not equal to the Target
        mode". We carried only the target half, which stayed quiet for
        the case that matters most — a loop parked in MAN that nobody
        is going to put back.
        """
        if not self.mode_actual:
            return False
        if self.mode_target and self.mode_target != self.mode_actual:
            return True
        return bool(self.mode_normal
                    and self.mode_normal != self.mode_actual)


#: The inert result an unresolved path yields: Bad, valueless. A binding
#: that resolves to nothing is bad quality, not zero.
UNRESOLVED = BindingResult()
