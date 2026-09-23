"""Shared standard-alarm state machine used by ALARM and ALARM_DET.

The Azeo Alarm Detection block and Azeo's older extended ALARM block expose
different parameter and terminal names, but their six standard conditions are
the same algorithm.  Keeping that algorithm here prevents fixes to
conditional delay or hysteresis behavior from silently reaching only one of
the two persisted block contracts.

This module intentionally knows nothing about function blocks, Qt, or the
runtime.  The block adapters resolve scaling, quality, aliases, and their
public names before calling :class:`StandardAlarmEngine`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


STANDARD_ALARMS = ("HI_HI", "HI", "LO", "LO_LO", "DV_HI", "DV_LO")
BASE_ANALOG_ALARMS = frozenset(("HI_HI", "HI", "LO", "LO_LO"))


@dataclass(frozen=True, slots=True)
class AlarmRule:
    """Configuration for one standard alarm condition, in engineering units."""

    limit: float
    high: bool
    hysteresis: float = 0.0
    enabled: bool = True
    delay_on: float = 0.0
    delay_off: float = 0.0
    enable_delay: float = 0.0


@dataclass(slots=True)
class _ConditionalState:
    active: bool = False
    on_elapsed: float = 0.0
    off_elapsed: float = 0.0
    enable_remaining: float = 0.0
    enabled: bool = True
    initialized: bool = False


class StandardAlarmEngine:
    """Evaluate and retain the six Azeo standard alarm conditions.

    ``evaluate`` accepts already-scaled PV/SP values and per-condition rules.
    This keeps each public block free to retain its historical configuration
    names while sharing every state transition.  Conditional delays are
    quantized by ``dt`` exactly as the Azeo help describes: a delay no
    greater than one execution period is satisfied at the next execution.
    """

    def __init__(self) -> None:
        self._base_active = {name: False for name in STANDARD_ALARMS}
        self._conditional = {
            name: _ConditionalState() for name in STANDARD_ALARMS
        }
        self._conditional_mode = False

    @property
    def active(self) -> dict[str, bool]:
        """Return the last published state without exposing mutable internals."""
        if self._conditional_mode:
            return {
                name: self._conditional[name].active
                for name in STANDARD_ALARMS
            }
        return dict(self._base_active)

    def reset(self) -> None:
        """Clear alarms and every conditional timer/enable transition."""
        for name in STANDARD_ALARMS:
            self._base_active[name] = False
            self._conditional[name] = _ConditionalState()
        self._conditional_mode = False

    def evaluate(
        self,
        *,
        pv: float,
        sp: float,
        dt: float,
        rules: Mapping[str, AlarmRule],
        conditional: bool = False,
        master_enabled: bool = True,
    ) -> dict[str, bool]:
        """Return the six active states after one execution period.

        Missing rules are a programming error rather than an implicit alarm
        default.  Both public block adapters build all six, so failing loudly
        here prevents a new alarm output from looking healthy while inert.
        """
        missing = set(STANDARD_ALARMS).difference(rules)
        if missing:
            raise ValueError(
                "Missing standard alarm rules: " + ", ".join(sorted(missing))
            )
        if not master_enabled:
            self.reset()
            return {name: False for name in STANDARD_ALARMS}

        if conditional != self._conditional_mode:
            # Conditional alarming is configured, rather than a process-state
            # latch.  Changing the feature must not revive elapsed timers from
            # an earlier configuration.
            for name in STANDARD_ALARMS:
                self._conditional[name] = _ConditionalState()
            self._conditional_mode = conditional

        deviation = float(pv) - float(sp)
        values = {
            "HI_HI": float(pv),
            "HI": float(pv),
            "LO": float(pv),
            "LO_LO": float(pv),
            "DV_HI": deviation,
            "DV_LO": deviation,
        }
        period = max(0.0, float(dt))
        result: dict[str, bool] = {}

        for name in STANDARD_ALARMS:
            rule = rules[name]
            enabled = bool(rule.enabled)
            if enabled:
                raw = self._base_condition(name, values[name], rule)
            else:
                # Azeo alarm_ENAB=0 immediately forces ACT to zero and
                # suspends processing, including the base hysteresis latch.
                self._base_active[name] = False
                raw = False

            result[name] = (
                self._conditioned(name, raw, period, rule)
                if conditional
                else raw
            )

        return result

    def _base_condition(self, name: str, value: float,
                        rule: AlarmRule) -> bool:
        """Apply trip comparison and clearing-side hysteresis."""
        hysteresis = max(0.0, float(rule.hysteresis))
        limit = float(rule.limit)
        active = self._base_active[name]
        if rule.high:
            active = value >= (limit - hysteresis) if active else value >= limit
        else:
            active = value <= (limit + hysteresis) if active else value <= limit
        self._base_active[name] = active
        return active

    def _conditioned(self, name: str, condition: bool, dt: float,
                     rule: AlarmRule) -> bool:
        state = self._conditional[name]
        enabled = bool(rule.enabled)

        if not state.initialized:
            # Conditional parameters default enabled.  Initial download is
            # therefore not a 0->1 transition and does not invoke ENAB_DELAY.
            state.enabled = enabled
            state.initialized = True
        elif enabled != state.enabled:
            if enabled:
                state.enable_remaining = max(0.0, float(rule.enable_delay))
            else:
                state.enable_remaining = 0.0
            state.enabled = enabled
            state.active = False
            state.on_elapsed = 0.0
            state.off_elapsed = 0.0

        if not enabled:
            state.active = False
            state.on_elapsed = 0.0
            state.off_elapsed = 0.0
            return False

        if state.enable_remaining > 0.0:
            state.enable_remaining = max(0.0, state.enable_remaining - dt)
            state.active = False
            state.on_elapsed = 0.0
            state.off_elapsed = 0.0
            return False

        if condition:
            state.off_elapsed = 0.0
            state.on_elapsed += dt
            if state.on_elapsed >= max(0.0, float(rule.delay_on)):
                state.active = True
        else:
            state.on_elapsed = 0.0
            if state.active:
                state.off_elapsed += dt
                if state.off_elapsed >= max(0.0, float(rule.delay_off)):
                    state.active = False
            else:
                state.off_elapsed = 0.0

        return state.active
