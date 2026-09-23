"""What one High Performance PVM's outer furniture is showing.

Everything here is resolution, not drawing: given what the bindings
say, decide which box, which icon and which status icons apply. The
painting lives in `marks.py`, and keeping the two apart is what lets a
test assert the manual's rules without reading pixels.

Three precedence rules from the manual, each of which suppresses
something that is genuinely true — they exist so the PVM says the *most
important* thing rather than everything at once:

1. **An alarm outranks an abnormal condition.** "The status box is
   visible only when there are no active or unacknowledged alarms.
   Otherwise, the alarm box shows." One rectangle, one meaning.
2. **Not Running hides Bad I/O.** "The Bad IO icon is never visible
   when the Not Running icon is visible" — a module that is not
   executing has bad I/O as a consequence, and saying both invites the
   operator to chase the symptom.
3. **Bad I/O and Not Running both hide Simulate.** "The Simulation
   Active icon is never visible if either the Not Running or Bad IO
   icon is visible."

And one for the alarm icon: "The suppressed alarm icon is visible only
when no other active or unacknowledged alarms exist."

The shared runtime alarm registry now drives acknowledgement,
suppression and counts for the banner, every PVM, faceplates and display
rollups. Module-running state comes from the source rather than a fake
block terminal. Device conditions remain class-declared: an unbound
condition stays absent, never a healthy-looking False.
"""
from __future__ import annotations

from dataclasses import dataclass

from ...binding.result import UNRESOLVED, BindingResult
from ...theme.roles import Role
from azeo_control_trainer.core.strategy.model.terminal import Quality

#: Azeo priority bands (CRITICAL 15 / WARNING 11 / ADVISORY 7).
CRITICAL, WARNING, ADVISORY = 15, 11, 7

#: Priority -> the role its box and icon take.
#:
#: **These are OUR priority colours, not Azeo's.** Azeo paints the
#: box red / yellow / magenta / blue; ours are the theme's ALARM_P1..P3
#: and ALARM_SHELVED. The reason is consistency inside one product: the
#: alarm banner, the alarm marks and the faceplates already speak these
#: colours, and a box that called an advisory magenta while the banner
#: beside it called the same alarm yellow would be two colours for one
#: event — worse than either scheme. These have also been through
#: `theme/vision.py`; Azeo's have not, here.
PRIORITY_ROLES = {CRITICAL: Role.ALARM_P1, WARNING: Role.ALARM_P2,
                  ADVISORY: Role.ALARM_P3}

#: The alarm icon's four states, from the manual's table. Each priority
#: crosses with each of these, so the icon carries both what tripped
#: and whether anybody has dealt with it.
ICON_ACTIVE_ACKED = "active_acked"
ICON_ACTIVE_UNACKED = "active_unacked"
ICON_INACTIVE_UNACKED = "inactive_unacked"
ICON_SUPPRESSED = "suppressed"

#: Status icons in the manual's own order. The name is the field on
#: `AlarmBoxState`; the tooltip is what the hover window says.
STATUS_CONDITIONS = (
    ("abnormal_mode", "Mode is not as expected"),
    ("not_running", "Module not running"),
    ("bad_io", "Bad I/O"),
    ("simulated", "Simulation active"),
    ("no_permit", "No permit"),
    ("interlocked", "Interlocked"),
    ("tracking", "Tracking"),
    ("bypassed", "Interlock bypassed"),
)


@dataclass(frozen=True)
class AlarmBoxState:
    """Everything the outer furniture needs, resolved once."""

    #: Highest priority in alarm, 0 when nothing is.
    priority: int = 0
    active: bool = False
    acked: bool = False
    suppressed: bool = False
    #: Total unacknowledged + active + suppressed alarms on the module.
    alarm_count: int = 0
    #: The top alarm's condition name, for the hover window.
    condition: str = ""

    #: Abnormal conditions — the status-icon set.
    bad_io: bool = False
    abnormal_mode: bool = False
    simulated: bool = False
    not_running: bool = False
    no_permit: bool = False
    interlocked: bool = False
    tracking: bool = False
    bypassed: bool = False

    @property
    def has_alarm(self) -> bool:
        """Whether the ALARM box shows.

        Active *or* unacknowledged: the manual keeps the box up until
        somebody has answered, so an alarm that cleared while nobody
        was looking does not erase its own evidence.
        """
        return self.active or (self.priority > 0 and not self.acked)

    @property
    def icon_state(self) -> str | None:
        """Which of the four alarm-icon states applies, or None.

        The suppressed icon is deliberately last: "the suppressed alarm
        icon is visible only when no other active or unacknowledged
        alarms exist", so a suppressed low-priority alarm never masks a
        live critical one.
        """
        if self.has_alarm:
            if self.active:
                return (ICON_ACTIVE_ACKED if self.acked
                        else ICON_ACTIVE_UNACKED)
            return ICON_INACTIVE_UNACKED
        if self.suppressed:
            return ICON_SUPPRESSED
        return None

    def visible_conditions(self) -> tuple[str, ...]:
        """The status icons that actually show, after precedence.

        Rules 2 and 3 above: Not Running hides Bad I/O, and either of
        them hides Simulate. Every other condition is independent.
        """
        shown = []
        for field, _tip in STATUS_CONDITIONS:
            if not getattr(self, field, False):
                continue
            if field == "bad_io" and self.not_running:
                continue
            if field == "simulated" and (self.not_running or self.bad_io):
                continue
            shown.append(field)
        return tuple(shown)

    @property
    def abnormal(self) -> bool:
        """Whether any status-box condition holds."""
        return bool(self.visible_conditions() or self.suppressed)

    @property
    def box_role(self):
        """The rectangle's role, or None when no rectangle shows.

        This IS rule 1: an alarm outranks an abnormal condition, and
        the status box shows only in its absence.
        """
        if self.has_alarm:
            if self.suppressed:
                return Role.ALARM_SHELVED
            return PRIORITY_ROLES.get(self.priority, Role.ALARM_P3)
        if self.abnormal:
            return Role.ALARM_SHELVED if self.suppressed else Role.ACTION
        return None

    @property
    def shows_status_box(self) -> bool:
        return not self.has_alarm and self.abnormal


def resolve_alarm_box(result: BindingResult | None,
                      mode_result: BindingResult | None = None,
                      simulate_result: BindingResult | None = None,
                      not_running: bool = False,
                      conditions: dict | None = None,
                      enabled_conditions=()) -> AlarmBoxState:
    """One PVM's outer state, from what a binding can actually say.

    `conditions` carries the DC/EDC flags a class binds explicitly
    (`no_permit`, `interlocked`, `tracking`, `bypassed`); unbound ones
    stay False rather than being guessed from the analog signal.
    """
    allowed = set(enabled_conditions or dict(STATUS_CONDITIONS))
    extra = {k: bool(v) for k, v in (conditions or {}).items()
             if k in dict(STATUS_CONDITIONS)}
    extra = {key: value for key, value in extra.items() if key in allowed}
    not_running = bool(not_running or getattr(
        result, "module_running", None) is False)
    if result is None or result is UNRESOLVED:
        return AlarmBoxState(bad_io=True, not_running=not_running, **extra)
    mode = mode_result if mode_result not in (None, UNRESOLVED) else result
    # "Actual mode is not equal to the Target mode" — the Normal mode
    # is not modelled here, so only the target comparison is honest.
    abnormal_mode = bool(getattr(mode, "mode_mismatch", False)) \
        if "abnormal_mode" in allowed else False
    simulated = bool("simulated" in allowed and simulate_result is not None
                     and simulate_result is not UNRESOLVED
                     and simulate_result.value)
    priority = int(result.alarm_priority or 0)
    active = bool(result.alarm_active)
    acked = bool(result.alarm_acked)
    suppressed = bool(getattr(result, "alarm_suppressed", False))
    return AlarmBoxState(
        priority=priority,
        active=active,
        acked=acked,
        suppressed=suppressed,
        alarm_count=int(getattr(result, "alarm_count", 0) or bool(
            active or (priority and not acked) or suppressed)),
        condition=str(result.alarm_condition or ""),
        bad_io=result.quality is Quality.BAD and "bad_io" in allowed,
        abnormal_mode=abnormal_mode,
        simulated=simulated,
        not_running=not_running and "not_running" in allowed,
        **extra)
