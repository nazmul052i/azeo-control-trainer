"""Azeo-compatibility blocks — RET, DTE, PIN, LE.

Four further Azeo function blocks, transcribed from the block
reference in ``doc/AZEO_FUNCTION_BLOCKS.md``:

    RET — Retentive Timer   (accumulated True-time, retained on dropout)
    DTE — Date Time Event   (time-of-day + one-shot / recurring scheduler)
    PIN — Pulse Input       (pulse frequency -> EU rate, modes + alarms)
    LE  — Lab Entry         (operator/lab manual value entry, TRIGGER latched)

Azeo binds PIN to a Multifunction / Discrete Input card pulse channel
and LE to a legacy operator graphics dynamo. This repo has neither, so both blocks
implement the *algorithm* and take their raw data from ordinary block
terminals (see the individual class docstrings).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from ..model.block_base import (
    FunctionBlock, BlockCategory, BlockStatus, DataType,
)
from ..model.block_registry import register_block


# ═══════════════════════════════════════════════════════════════════════
#  RET — Retentive Timer
# ═══════════════════════════════════════════════════════════════════════

@register_block
class RetentiveTimerBlock(FunctionBlock):
    """Retentive Timer (RET) — accumulates True-time across dropouts.

    ``OUT_D`` goes True once ``IN_D`` has been True for a *total* of
    ``TIME_DURATION`` seconds. When ``IN_D`` drops, ``ELAPSED_TIMER``
    stops but **retains** its accumulated value — this is what separates
    RET from the repo's ``TIMER_ON`` / ``TIMER_OFF`` blocks, which zero
    their timer whenever the input drops. Only ``RESET_IN`` clears the
    accumulator.

    Status: ``OUT_D`` is GoodNonCascade in Azeo — the input status does
    *not* propagate — so the block status stays GOOD regardless of the
    driving signal's quality.

    ``TIME_DURATION`` must exceed the parent module's scan rate.
    """
    block_type = "RET"
    category = BlockCategory.LOGIC
    display_name = "Retentive Timer (RET)"
    description = "Accumulates True time, retained on input dropout, cleared by RESET_IN"

    # RET has no modes and no named sets — only the timer preset.
    config_units = {"TIME_DURATION": "s"}

    def __init__(self, instance_name: str = ""):
        self._elapsed = 0.0
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("IN_D", DataType.BOOL, False,
                       "Discrete input value to be timed")
        self.add_input("RESET_IN", DataType.BOOL, False,
                       "Resets ELAPSED_TIMER and OUT_D")
        self.add_output("OUT_D", DataType.BOOL, False,
                        "True once accumulated time reaches TIME_DURATION")
        self.add_output("NOT_OUT_D", DataType.BOOL, True, "Inverse of OUT_D")
        self.add_output("ELAPSED_TIMER", description="Accumulated True time [s]")

    def get_config_schema(self):
        return {
            "TIME_DURATION": (float, 10.0,
                              "Total True time before OUT_D is set [s]; "
                              "must exceed the module scan rate"),
        }

    def reset(self):
        super().reset()
        self._elapsed = 0.0

    def execute(self, dt: float):
        p = self.config.params
        duration = max(0.0, float(p.get("TIME_DURATION", 10.0)))

        if bool(self.get_input("RESET_IN")):
            # Doc: "RESET_IN transitions to True -> ELAPSED_TIMER reset to
            # zero and OUT_D set False"; accumulation is described as
            # happening "with RESET_IN False", so a held reset also pins
            # the accumulator at zero.
            self._elapsed = 0.0
            out = False
        else:
            if bool(self.get_input("IN_D")):
                self._elapsed += dt
            # ELAPSED_TIMER is the accumulated time up to the preset; it
            # stops climbing once the timer has timed out.
            self._elapsed = min(self._elapsed, duration)
            out = self._elapsed >= duration

        self.set_output("ELAPSED_TIMER", self._elapsed)
        self.set_output("OUT_D", out)
        self.set_output("NOT_OUT_D", not out)
        # GoodNonCascade — input status is deliberately not propagated.
        self.status = BlockStatus.GOOD


# ═══════════════════════════════════════════════════════════════════════
#  DTE — Date Time Event
# ═══════════════════════════════════════════════════════════════════════

_PERIOD_RE = re.compile(r"^P(\d{1,5})T(\d{1,2}):(\d{2}):(\d{2})$")

#: Shortest recurrence Azeo accepts for INTERVAL_STR.
_MIN_INTERVAL_S = 5.0


def _parse_period(text: str) -> float:
    """ISO period ``PDDDDDThh:mm:ss`` -> seconds (0.0 if unparsable)."""
    m = _PERIOD_RE.match(str(text).strip())
    if not m:
        return 0.0
    days, hh, mm, ss = (int(g) for g in m.groups())
    return days * 86400.0 + hh * 3600.0 + mm * 60.0 + ss


def _format_period(seconds: float) -> str:
    """Seconds -> ISO period ``PDDDDDThh:mm:ss``."""
    total = int(max(0.0, seconds))
    days, rem = divmod(total, 86400)
    hh, rem = divmod(rem, 3600)
    mm, ss = divmod(rem, 60)
    return f"P{min(days, 99999):05d}T{hh:02d}:{mm:02d}:{ss:02d}"


def _parse_local_time(text: str):
    """ISO local date/time string -> naive ``datetime`` (None if invalid)."""
    s = str(text).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


@register_block
class DateTimeEventBlock(FunctionBlock):
    """Date Time Event (DTE) — time-of-day source and event scheduler.

    Publishes the current local/UTC time as ISO strings and asserts
    ``OUT_D`` when the scheduled local event time ``TE_TIME_STR`` is
    reached. ``INTERVAL_STR`` (ISO period ``PDDDDDThh:mm:ss``) selects a
    one-time event (``P00000T00:00:00``) or a recurrence; Azeo's
    minimum recurrence is 5 s and shorter values are raised to it.

    ``STATE``: 0 = Idle, 1 = Armed, 2 = Terminal.

    On a one-time event ``OUT_D`` latches True (STATE -> Idle) until
    ``RESET``; on a recurring event ``OUT_D`` is True for the scan in
    which the event fires (STATE -> Terminal) and the block re-arms with
    the next target on the following scan — matching the doc's advice to
    pair OUT_D with a timer block when a fixed-duration pulse is wanted.

    Ambiguity (doc "Execution" vs. the parameter table): the execution
    text says RESET leaves STATE = 1 (Armed) while the RESET parameter
    says it "forces the block to the Idle state". Implemented per the
    parameter table — RESET drops to Idle and clears OUT_D — which
    reconciles both, since the following scan re-arms automatically when
    TE_TIME_STR is still in the future.

    The Azeo DST self-adjustment of TE_TIME_STR is not modelled; this
    block works in the host's local time.

    ``_now()`` is a hook returning the current local time; tests
    (and simulated-clock scenarios) may override it.
    """
    block_type = "DTE"
    category = BlockCategory.LOGIC
    display_name = "Date Time Event (DTE)"
    description = "Time-of-day strings + one-time / recurring scheduled discrete event"

    _IDLE, _ARMED, _TERMINAL = 0, 1, 2

    def __init__(self, instance_name: str = ""):
        self._state = self._IDLE
        self._target: datetime | None = None
        self._latched = False
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("ENABLE", DataType.BOOL, True,
                       "0 disables OUT_D, non-zero enables it")
        self.add_input("RESET", DataType.BOOL, False,
                       "True forces the block to the Idle state")
        self.add_output("OUT_D", DataType.BOOL, False,
                        "True when the scheduled event has occurred and ENABLE is True")
        self.add_output("NOT_OUT_D", DataType.BOOL, True, "Inverse of OUT_D")
        self.add_output("STATE", DataType.INT, 0,
                        "0 = Idle, 1 = Armed, 2 = Terminal")
        self.add_output("DIFF_TIME_STR", DataType.STRING, "P00000T00:00:00",
                        "Time remaining before the next event (ISO period)")
        self.add_output("LOCAL_TIME_STR", DataType.STRING, "",
                        "Current local time (ISO)")
        self.add_output("UTC_TIME_STR", DataType.STRING, "",
                        "Current UTC time (ISO)")

    def get_config_schema(self):
        return {
            "TE_TIME_STR":  (str, "",
                             "Scheduled local event time, ISO 'YYYY-MM-DDThh:mm:ss' "
                             "(must be in the future)"),
            "INTERVAL_STR": (str, "P00000T00:00:00",
                             "Recurrence, ISO period 'PDDDDDThh:mm:ss'; "
                             "P00000T00:00:00 = one-time; minimum 5 s"),
        }

    # -- clock hook -------------------------------------------------
    def _now(self) -> datetime:
        """Current local time. Overridable for tests / simulated clocks."""
        return datetime.now()

    def _utc_now(self) -> datetime:
        """Current UTC time, derived from :meth:`_now` so a patched clock
        stays consistent across both strings."""
        return self._now().astimezone(timezone.utc)

    def reset(self):
        super().reset()
        self._state = self._IDLE
        self._target = None
        self._latched = False

    def _interval_s(self) -> float:
        iv = _parse_period(self.config.params.get("INTERVAL_STR",
                                                  "P00000T00:00:00"))
        # Azeo rejects sub-5 s recurrences; clamp rather than fire every scan.
        return max(iv, _MIN_INTERVAL_S) if iv > 0.0 else 0.0

    def _publish(self, now: datetime, out_d: bool, diff_s: float):
        self.set_output("OUT_D", out_d)
        self.set_output("NOT_OUT_D", not out_d)
        self.set_output("STATE", self._state)
        self.set_output("DIFF_TIME_STR", _format_period(diff_s))
        self.set_output("LOCAL_TIME_STR", now.isoformat(timespec="seconds"))
        self.set_output("UTC_TIME_STR",
                        self._utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"))
        # OUT_D / NOT_OUT_D statuses follow the input statuses.
        self.status = BlockStatus.GOOD

    def execute(self, dt: float):
        now = self._now()
        interval = self._interval_s()
        enable = bool(self.get_input("ENABLE"))

        if bool(self.get_input("RESET")):
            self._state = self._IDLE
            self._target = None
            self._latched = False
            self._publish(now, False, 0.0)
            return

        # Establish the target event time: TE_TIME_STR when it is still in
        # the future, otherwise (first execution with INTERVAL nonzero) one
        # interval from now.
        if self._target is None:
            te = _parse_local_time(self.config.params.get("TE_TIME_STR", ""))
            if te is not None and te > now:
                self._target = te
            elif interval > 0.0:
                self._target = now + timedelta(seconds=interval)

        if self._target is None:
            self._state = self._IDLE
            self._publish(now, self._latched and enable, 0.0)
            return

        fired = now >= self._target
        if fired:
            if interval > 0.0:
                # Clock jumps must not iterate once per missed event on the
                # GUI-owned scan thread. Preserve the original phase directly.
                period = timedelta(seconds=interval)
                missed = (now - self._target) // period + 1
                self._target += period * missed
                self._state = self._TERMINAL      # more events scheduled
                self._latched = False
            else:
                self._state = self._IDLE          # no further events
                self._latched = True
            out_d = True
        else:
            if self._state == self._TERMINAL:
                self._state = self._ARMED         # re-armed for the next event
            elif not self._latched:
                self._state = self._ARMED
            out_d = self._latched

        diff = (self._target - now).total_seconds() if self._target else 0.0
        self._publish(now, out_d and enable, diff)


# ═══════════════════════════════════════════════════════════════════════
#  PIN — Pulse Input
# ═══════════════════════════════════════════════════════════════════════

@register_block
class PulseInputBlock(FunctionBlock):
    """Pulse Input (PIN) — pulse frequency converted to an EU rate.

    Azeo binds this block to a Multifunction / Discrete Input card
    Pulse Input channel and reads its ``FREQUENCY`` (counts/second)
    parameter. This repo has no I/O card layer, so the raw signal comes
    from a terminal instead: ``IN`` is either the channel frequency
    (``INPUT_TYPE = FREQUENCY``) or a free-running pulse counter
    (``INPUT_TYPE = COUNT``), from which the block derives frequency
    itself and handles counter rollover at ``COUNT_ROLLOVER``.

    Algorithm (doc)::

        PV        = FREQUENCY [counts/s] x PULSE_VAL [EU/pulse] x TIME_UNITS factor
        OUT       = PV in Auto; operator value (OUT_MAN) in Man
        FIELD_VAL = percent of PV within OUT_SCALE

    ``PV_FTIME`` applies a first-order filter to PV. In Auto a Good PV
    further than -10%..110% outside the OUT_SCALE span makes OUT
    Uncertain; in Man OUT is always Good (limit = Constant).
    """
    block_type = "PIN"
    category = BlockCategory.IO
    display_name = "Pulse Input (PIN)"
    description = "Pulse frequency -> EU rate (PULSE_VAL x TIME_UNITS), modes + alarms"

    _OOS, _MAN, _AUTO = "OOS", "MAN", "AUTO"

    #: TIME_UNITS named set -> seconds-per-time-unit multiplier.
    _TIME_FACTOR = {"SECONDS": 1.0, "MINUTES": 60.0,
                    "HOURS": 3600.0, "DAYS": 86400.0}

    # Doc §Modes: "OOS, Man, Auto". TIME_UNITS is the doc's
    # "seconds / minutes / hours / days" named set; INPUT_TYPE is this
    # repo's stand-in for the card's FREQUENCY channel parameter (the
    # block can also derive frequency from a raw counter).
    config_choices = {
        "MODE": ("AUTO", "MAN", "OOS"),
        "INPUT_TYPE": ("FREQUENCY", "COUNT"),
        "TIME_UNITS": ("SECONDS", "MINUTES", "HOURS", "DAYS"),
    }
    config_units = {
        "OUT_MAN": "EU of OUT_SCALE",
        "PULSE_VAL": "EU/pulse",
        "COUNT_ROLLOVER": "counts",
        "PV_FTIME": "s",
        "OUT_SCALE_LO": "EU per TIME_UNITS",
        "OUT_SCALE_HI": "EU per TIME_UNITS",
        "HI_HI_LIM": "EU of OUT_SCALE",
        "HI_LIM": "EU of OUT_SCALE",
        "LO_LIM": "EU of OUT_SCALE",
        "LO_LO_LIM": "EU of OUT_SCALE",
        "ALARM_HYS": "% of scale",
    }

    def __init__(self, instance_name: str = ""):
        self._pv_filt = 0.0
        self._initialized = False
        self._last_count: float | None = None
        self._hi_hi_act = False
        self._hi_act = False
        self._lo_act = False
        self._lo_lo_act = False
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("IN", description="Pulse channel value: frequency "
                                         "[counts/s] or raw pulse count")
        self.add_input("SIMULATE_IN",
                       description="Simulated frequency/count; overrides IN "
                                   "when simulation is enabled")
        self.add_input("STATUS_IN", DataType.BOOL, True,
                       "Channel status — False = input failure / PV Bad")
        self.add_output("OUT", description="Primary value (EU of OUT_SCALE)")
        self.add_output("PV", description="Computed rate (EU per TIME_UNITS)")
        self.add_output("FREQUENCY", description="Pulse frequency [counts/s]")
        self.add_output("FIELD_VAL", description="Percent of PV within OUT_SCALE")
        self.add_output("MODE", DataType.STRING, "AUTO", "Actual mode")
        self.add_output("STATUS", DataType.BOOL, True, "Signal good")
        self.add_output("SIMULATE_ACT", DataType.BOOL, False, "Simulate active")
        self.add_output("HI_HI_ACT", DataType.BOOL, False, "Hi-Hi alarm active")
        self.add_output("HI_ACT", DataType.BOOL, False, "Hi alarm active")
        self.add_output("LO_ACT", DataType.BOOL, False, "Lo alarm active")
        self.add_output("LO_LO_ACT", DataType.BOOL, False, "Lo-Lo alarm active")

    def get_config_schema(self):
        return {
            "MODE":        (str, "AUTO", "Target mode: OOS / MAN / AUTO"),
            "OUT_MAN":     (float, 0.0, "Operator-set OUT used in Man mode"),
            "INPUT_TYPE":  (str, "FREQUENCY",
                            "IN is FREQUENCY (counts/s) or COUNT (raw pulse counter)"),
            "PULSE_VAL":   (float, 1.0, "Engineering units per pulse"),
            "TIME_UNITS":  (str, "SECONDS",
                            "Rate time base: SECONDS / MINUTES / HOURS / DAYS"),
            "COUNT_ROLLOVER": (float, 65536.0,
                               "Counter modulus for INPUT_TYPE = COUNT rollover handling"),
            "PV_FTIME":    (float, 0.0, "First-order PV filter time constant [s]; 0 = none"),
            "OUT_SCALE_LO": (float, 0.0, "OUT_SCALE EU at 0% (EU per TIME_UNITS)"),
            "OUT_SCALE_HI": (float, 100.0, "OUT_SCALE EU at 100% (EU per TIME_UNITS)"),
            "SIMULATE":    (bool, False, "Enable simulation (SIMULATE_IN replaces IN)"),
            "OPT_UNCERTAIN_IF_MAN": (bool, False,
                                     "STATUS_OPTS: Uncertain if Man mode"),
            "HI_HI_LIM":   (float, float("inf"), "Hi-Hi alarm limit (EU of OUT)"),
            "HI_LIM":      (float, float("inf"), "Hi alarm limit (EU of OUT)"),
            "LO_LIM":      (float, float("-inf"), "Lo alarm limit (EU of OUT)"),
            "LO_LO_LIM":   (float, float("-inf"), "Lo-Lo alarm limit (EU of OUT)"),
            "ALARM_HYS":   (float, 0.0, "Alarm hysteresis (% of scale span)"),
        }

    def reset(self):
        super().reset()
        self._pv_filt = 0.0
        self._initialized = False
        self._last_count = None
        self._hi_hi_act = self._hi_act = self._lo_act = self._lo_lo_act = False

    def _frequency(self, raw: float, dt: float) -> float:
        """Channel frequency in counts/s from the configured input type."""
        p = self.config.params
        if str(p.get("INPUT_TYPE", "FREQUENCY")).upper() != "COUNT":
            return raw
        modulus = float(p.get("COUNT_ROLLOVER", 65536.0))
        last = self._last_count
        self._last_count = raw
        if last is None or dt <= 0.0:
            return 0.0
        delta = raw - last
        if delta < 0.0 and modulus > 0.0:
            delta += modulus          # counter wrapped past its modulus
        return delta / dt

    def execute(self, dt: float):
        p = self.config.params
        mode = str(p.get("MODE", self._AUTO)).upper()
        if mode == "MANUAL":
            mode = self._MAN
        if mode not in (self._OOS, self._MAN, self._AUTO):
            mode = self._AUTO

        if mode == self._OOS:
            # Block not processed: hold OUT, report Out of Service.
            self.set_output("MODE", self._OOS)
            self.set_output("STATUS", False)
            self.status = BlockStatus.OOS
            return

        simulate = bool(p.get("SIMULATE", False))
        raw = self.get_input("SIMULATE_IN") if simulate else self.get_input("IN")
        freq = self._frequency(float(raw), dt)

        factor = self._TIME_FACTOR.get(
            str(p.get("TIME_UNITS", "SECONDS")).upper(), 1.0)
        pv_raw = freq * float(p.get("PULSE_VAL", 1.0)) * factor

        ftime = float(p.get("PV_FTIME", 0.0))
        if not self._initialized:
            self._pv_filt = pv_raw
            self._initialized = True
        elif ftime > 0.0 and dt > 0.0:
            alpha = dt / (ftime + dt)
            self._pv_filt += alpha * (pv_raw - self._pv_filt)
        else:
            self._pv_filt = pv_raw
        pv = self._pv_filt

        lo = float(p.get("OUT_SCALE_LO", 0.0))
        hi = float(p.get("OUT_SCALE_HI", 100.0))
        span = hi - lo
        field_val = 100.0 * (pv - lo) / span if span else 0.0

        good_in = bool(self.get_input("STATUS_IN"))
        if mode == self._MAN:
            out = float(p.get("OUT_MAN", 0.0))
            # Man: OUT limit indicates Constant and status is always Good,
            # unless STATUS_OPTS "Uncertain if Man mode" is selected.
            status = (BlockStatus.UNCERTAIN
                      if p.get("OPT_UNCERTAIN_IF_MAN", False)
                      else BlockStatus.GOOD)
        else:
            out = pv
            if not good_in:
                status = BlockStatus.BAD
            elif field_val < -10.0 or field_val > 110.0:
                status = BlockStatus.UNCERTAIN
            else:
                status = BlockStatus.GOOD

        self.set_output("FREQUENCY", freq)
        self.set_output("PV", pv)
        self.set_output("FIELD_VAL", field_val)
        self.set_output("OUT", out)
        self.set_output("MODE", mode)
        self.set_output("SIMULATE_ACT", simulate)
        self.set_output("STATUS", status is BlockStatus.GOOD)
        self.status = status

        # Alarm detection on OUT; ALARM_HYS is a percent of the scale span.
        hys = abs(span) * max(0.0, min(50.0, float(p.get("ALARM_HYS", 0.0)))) / 100.0
        hi_hi = float(p.get("HI_HI_LIM", float("inf")))
        hi_lim = float(p.get("HI_LIM", float("inf")))
        lo_lim = float(p.get("LO_LIM", float("-inf")))
        lo_lo = float(p.get("LO_LO_LIM", float("-inf")))
        self._hi_hi_act = out >= hi_hi if not self._hi_hi_act else out >= hi_hi - hys
        self._hi_act = out >= hi_lim if not self._hi_act else out >= hi_lim - hys
        self._lo_act = out <= lo_lim if not self._lo_act else out <= lo_lim + hys
        self._lo_lo_act = out <= lo_lo if not self._lo_lo_act else out <= lo_lo + hys
        self.set_output("HI_HI_ACT", self._hi_hi_act)
        self.set_output("HI_ACT", self._hi_act)
        self.set_output("LO_ACT", self._lo_act)
        self.set_output("LO_LO_ACT", self._lo_lo_act)


# ═══════════════════════════════════════════════════════════════════════
#  LE — Lab Entry
# ═══════════════════════════════════════════════════════════════════════

@register_block
class LabEntryBlock(FunctionBlock):
    """Lab Entry (LE) — operator entry of an offline lab analysis result.

    In Azeo an Operate dynamo collects the lab value plus the time the
    grab sample was taken, computes ``DELAY = entry time - sample time``,
    and writes both to the block after range-checking them. There is no
    dynamo here, so the entry arrives on terminals (``SAMPLE`` /
    ``DELAY_IN``, falling back to the ``SAMPLE_VAL`` / ``DELAY_VAL``
    config params when nothing is wired) and the block itself performs
    the acceptance test on a rising edge of ``TRIGGER``:

        OUT_LO_LIM <= value <= OUT_HI_LIM   and
        MIN_DELAY  <= delay <= MAX_DELAY

    A rejected entry leaves OUT/DELAY untouched and pulses ``REJECTED``.
    An accepted entry moves the previous value to ``LAST_VALUE`` and
    flips the OUT status limit from Constant to Not Limited for the next
    **two block executions** (``NEW_SAMPLE`` / ``OUT_LIMIT``) — the
    transition a downstream NN block uses to detect a new analysis.

    Spec note: the doc's TRIGGER row says it "latches the current SAMPLE
    and DELAY values", but the block's value parameter is ``OUT`` (there
    is no SAMPLE parameter in the table). Implemented per the parameter
    table — the latched value *is* OUT — with the entry terminal named
    SAMPLE so both readings line up.

    Modes: ``MAN`` (normal — accepts entries) and ``OOS`` (not
    processed; OUT and DELAY go Bad, block status OOS).
    """
    block_type = "LE"
    category = BlockCategory.APC
    display_name = "Lab Entry (LE)"
    description = "Operator/lab manual value entry with TRIGGER latch and delay limits"

    _OOS, _MAN = "OOS", "MAN"

    #: Azeo caps MAX_DELAY at 24 h.
    _MAX_DELAY_LIMIT = 86400.0

    # Doc §Modes: LE supports **OOS and Man only** — there is no Auto.
    config_choices = {
        "MODE": ("MAN", "OOS"),
    }
    config_units = {
        "SAMPLE_VAL": "EU of OUT_SCALE",
        "DELAY_VAL": "s",
        "OUT_LO_LIM": "EU of OUT_SCALE",
        "OUT_HI_LIM": "EU of OUT_SCALE",
        "MIN_DELAY": "s",
        "MAX_DELAY": "s",
        "OUT_SCALE_LO": "EU of OUT_SCALE",
        "OUT_SCALE_HI": "EU of OUT_SCALE",
    }

    def __init__(self, instance_name: str = ""):
        self._prev_trigger = False
        self._new_scans = 0
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("SAMPLE", description="Lab analysis value to enter "
                                             "(falls back to SAMPLE_VAL config)")
        self.add_input("DELAY_IN", description="Processing delay [s] = entry "
                                               "time - sample time "
                                               "(falls back to DELAY_VAL config)")
        self.add_input("TRIGGER", DataType.BOOL, False,
                       "Rising edge latches SAMPLE/DELAY into OUT/DELAY")
        self.add_output("OUT", description="Lab analysis measurement value")
        self.add_output("DELAY", description="Accepted processing delay [s]")
        self.add_output("LAST_VALUE", description="Previous sample value written to OUT")
        self.add_output("NEW_SAMPLE", DataType.BOOL, False,
                        "True for two executions after an accepted entry")
        self.add_output("OUT_LIMIT", DataType.STRING, "CONSTANT",
                        "OUT status limit: CONSTANT or NOT_LIMITED")
        self.add_output("REJECTED", DataType.BOOL, False,
                        "Last entry was outside the value or delay limits")
        self.add_output("MODE", DataType.STRING, "MAN", "Actual mode")
        self.add_output("STATUS", DataType.BOOL, True, "OUT/DELAY status good")

    def get_config_schema(self):
        return {
            "MODE":        (str, "MAN", "Target mode: MAN / OOS"),
            "SAMPLE_VAL":  (float, 0.0, "Operator-entered lab value (used when "
                                        "SAMPLE is not wired)"),
            "DELAY_VAL":   (float, 0.0, "Operator-entered delay [s] (used when "
                                        "DELAY_IN is not wired)"),
            "OUT_LO_LIM":  (float, 0.0, "Minimum lab value writable to OUT"),
            "OUT_HI_LIM":  (float, 100.0, "Maximum lab value writable to OUT"),
            "MIN_DELAY":   (float, 0.0, "Minimum accepted processing delay [s]"),
            "MAX_DELAY":   (float, 86400.0,
                            "Maximum accepted processing delay [s]; max 86400. "
                            "If MAX_DELAY < MIN_DELAY both revert to defaults"),
            "OUT_SCALE_LO": (float, 0.0, "OUT_SCALE EU at 0%"),
            "OUT_SCALE_HI": (float, 100.0, "OUT_SCALE EU at 100%"),
        }

    def reset(self):
        super().reset()
        self._prev_trigger = False
        self._new_scans = 0

    def _delay_limits(self) -> tuple[float, float]:
        p = self.config.params
        lo = float(p.get("MIN_DELAY", 0.0))
        hi = min(float(p.get("MAX_DELAY", self._MAX_DELAY_LIMIT)),
                 self._MAX_DELAY_LIMIT)
        if hi < lo:
            # Doc: an inverted pair reverts both parameters to defaults.
            lo, hi = 0.0, self._MAX_DELAY_LIMIT
            p["MIN_DELAY"], p["MAX_DELAY"] = lo, hi
        return lo, hi

    def execute(self, dt: float):
        p = self.config.params
        mode = str(p.get("MODE", self._MAN)).upper()
        if mode == "MANUAL":
            mode = self._MAN
        if mode not in (self._OOS, self._MAN):
            mode = self._MAN

        if mode == self._OOS:
            self.set_output("MODE", self._OOS)
            self.set_output("STATUS", False)
            self.set_output("NEW_SAMPLE", False)
            self.set_output("OUT_LIMIT", "CONSTANT")
            self._new_scans = 0
            self.status = BlockStatus.OOS
            return

        trigger = bool(self.get_input("TRIGGER"))
        rising = trigger and not self._prev_trigger
        self._prev_trigger = trigger

        if rising:
            value = (float(self.get_input("SAMPLE"))
                     if self.inputs["SAMPLE"].connected
                     else float(p.get("SAMPLE_VAL", 0.0)))
            delay = (float(self.get_input("DELAY_IN"))
                     if self.inputs["DELAY_IN"].connected
                     else float(p.get("DELAY_VAL", 0.0)))
            lo_lim = float(p.get("OUT_LO_LIM", 0.0))
            hi_lim = float(p.get("OUT_HI_LIM", 100.0))
            d_lo, d_hi = self._delay_limits()
            accept = (lo_lim <= value <= hi_lim) and (d_lo <= delay <= d_hi)
            if accept:
                self.set_output("LAST_VALUE", self.get_output("OUT"))
                self.set_output("OUT", value)
                self.set_output("DELAY", delay)
                self._new_scans = 2      # Not Limited for two executions
            self.set_output("REJECTED", not accept)

        new_sample = self._new_scans > 0
        self.set_output("NEW_SAMPLE", new_sample)
        self.set_output("OUT_LIMIT", "NOT_LIMITED" if new_sample else "CONSTANT")
        if self._new_scans > 0:
            self._new_scans -= 1

        self.set_output("MODE", mode)
        self.set_output("STATUS", True)
        self.status = BlockStatus.GOOD
