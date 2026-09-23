"""Azeo Advanced Function Blocks — analog / discrete MooN voters.

Layout:
    AVTR — Analog Voter   (M-out-of-N vote on 1..16 analog transmitters)
    DVTR — Discrete Voter (M-out-of-N vote on 1..16 discrete sensors)

Both blocks follow ``doc/AZEO_FUNCTION_BLOCKS.md`` §"Analog Voter
function block (AVTR)" / §"Discrete Voter function block (DVTR)". They
share one voting/bypass/delay core (``_VoterBase``); AVTR adds the limit
comparison with hysteresis, the parallel pre-trip path, and the
deviation check, which DVTR does not have.

Output convention (Azeo): **1 = Normal (On), 0 = Tripped (Off)**.

Repo-specific adaptations (no behavioral simplification intended):

* Azeo option *bitstrings* (BYPASS_OPTS, REPORT_OPTS) are exposed as
  individual ``OPT_*`` boolean config params — this repo's properties
  panel has no bitfield editor. Each description carries the Azeo
  option text. The packed bitstrings are still published on the
  ``AVTR_ALERTS`` / ``DVTR_ALERTS`` integer outputs for alert conditions.
* Azeo signals carry status on the wire; this repo's terminals do not,
  so every input ``INn`` / ``IN_Dn`` has a companion ``INn_BAD`` /
  ``IN_Dn_BAD`` boolean input (True = Bad status). They are hidden by
  default. Uncertain status is treated as Good by Azeo, so it needs no
  terminal.
* ``BYPASSn`` are Azeo *parameters*, not wired inputs (the doc warns
  that wiring them defeats the bypass-timeout auto-clear), so they are
  config params here too and the block clears them on timeout.
* Module-level alarm roll-up (MERROR) does not exist in this repo; the
  roll-up decision is published on the ``MERROR_ROLLUP`` output instead.
* Neither block has a mode parameter in Azeo, so neither exposes MODE.
"""
from __future__ import annotations

from ..model.block_base import (
    FunctionBlock, BlockCategory, BlockStatus, DataType,
)
from ..model.block_registry import register_block


MAX_INPUTS = 16
DEFAULT_INPUTS = 3

# TRIP_STATUS / PRE_TRIP_STATUS named-set members (doc §Parameters).
ST_NORMAL = "NORMAL"
ST_TRIPPED = "TRIPPED"
ST_INHIBITED = "TRIP_INHIBITED"
ST_VOTED_TRIP = "VOTED_TO_TRIP_DELAYED"
ST_VOTED_NORMAL = "VOTED_NORMAL_DELAYED"

# STATUS_OPT named set. Azeo documents STATUS_OPT as an option
# bitstring, but its three members are mutually exclusive selections, so
# it is modelled here as a named set (doc §Status handling).
STATUS_OPTS = ("ALWAYS_USE_VALUE", "WILL_NOT_VOTE_IF_BAD", "VOTE_TO_TRIP_IF_BAD")

# DETECT_TYPE named set (AVTR only).
DETECT_TYPES = ("GREATER_THAN", "LESS_THAN")

# BYPASS_OPTS members — order matches the doc's "complete list"; the
# index is used as the documented bit number in the config descriptions.
BYPASS_OPTS = (
    ("OPT_BYPASS_REDUCES_NUM_TO_TRIP", "A maintenance bypass reduces the number to trip"),
    ("OPT_MULTIPLE_BYPASSES_ALLOWED", "Multiple maintenance bypasses are allowed"),
    ("OPT_BYPASS_TIMEOUT_INDICATION_ONLY", "Maintenance bypass timeout is for indication only"),
    ("OPT_STARTUP_PRESET_WHILE_ACTIVE", "Startup bypass preset is allowed while active"),
    ("OPT_STARTUP_EXPIRES_ON_STABLE", "Startup bypass expires upon stabilization"),
    ("OPT_REMINDER_APPLIES_TO_STARTUP", "Reminder applies to startup bypass"),
    ("OPT_STARTUP_EVENT_BASED", "Startup bypass duration is event based"),
    ("OPT_BYPASS_PERMIT_NOT_REQUIRED", "Bypass permit is not required to bypass"),
    ("OPT_BYPASS_PERMIT_VISIBLE", "Bypass permit control should be visible in operator interface"),
)


class _TripDelay:
    """TRIP_DELAY / NORMAL_DELAY state machine for one discrete output.

    Holds one of the TRIP_STATUS named-set members and returns the
    output value using the Azeo convention (True = Normal).
    """

    def __init__(self):
        self.state = ST_NORMAL
        self.timer = 0.0

    def reset(self):
        self.state = ST_NORMAL
        self.timer = 0.0

    def step(self, condition: bool, inhibited: bool,
             trip_delay: float, normal_delay: float, dt: float) -> bool:
        if inhibited:
            # Startup bypass active, or too few inputs can participate to
            # ever reach A_NUM_TO_TRIP → output forced Normal.
            self.state = ST_INHIBITED
            self.timer = 0.0
            return True
        if self.state == ST_INHIBITED:
            self.state = ST_NORMAL
            self.timer = 0.0

        if self.state in (ST_NORMAL, ST_VOTED_TRIP):      # output currently Normal
            if condition:
                if trip_delay > 0.0:
                    if self.state != ST_VOTED_TRIP:
                        self.state = ST_VOTED_TRIP
                        self.timer = trip_delay
                    self.timer -= dt
                    if self.timer <= 0.0:
                        self.state, self.timer = ST_TRIPPED, 0.0
                else:
                    self.state, self.timer = ST_TRIPPED, 0.0
            else:
                self.state, self.timer = ST_NORMAL, 0.0
        else:                                              # output currently Tripped
            if not condition:
                if normal_delay > 0.0:
                    if self.state != ST_VOTED_NORMAL:
                        self.state = ST_VOTED_NORMAL
                        self.timer = normal_delay
                    self.timer -= dt
                    if self.timer <= 0.0:
                        self.state, self.timer = ST_NORMAL, 0.0
                else:
                    self.state, self.timer = ST_NORMAL, 0.0
            else:
                self.state, self.timer = ST_TRIPPED, 0.0

        return self.state in (ST_NORMAL, ST_VOTED_TRIP, ST_INHIBITED)


class _VoterBase(FunctionBlock):
    """Shared MooN voting core for AVTR and DVTR (not registered).

    Subclasses supply the input naming (``_in_name``) and the per-input
    vote computation (``_compute_votes``); everything else — bypass
    acceptance/permit/timeout/reminder, startup bypass (timed,
    stabilization-expiry and event-based), the trip/normal delay state
    machine, the effective number-to-trip (A_NUM_TO_TRIP) degradation,
    and output status — is common, exactly as the doc states for DVTR
    ("behave exactly as in AVTR").
    """

    category = BlockCategory.SAFETY

    # Neither voter has a mode parameter in Azeo, so neither declares a
    # MODE choice list. STATUS_OPT is the shared Bad-input named set.
    config_choices = {
        "STATUS_OPT": STATUS_OPTS,
    }
    config_units = {
        "TRIP_DELAY": "s",
        "NORMAL_DELAY": "s",
        "BYPASS_TIMEOUT": "s",
        "BYPASS_TIMER": "s",
        "REMINDER_TIME": "s",
        "STARTUP_TIME": "s",
        "STABLE_TIME": "s",
    }

    # Subclass hooks
    _IN_FMT = "IN{n}"
    _VOTE_FMT = "TRIP_VOTE_IN{n}"

    def __init__(self, instance_name: str = ""):
        self._byp = [False] * MAX_INPUTS        # accepted maintenance bypasses
        self._byp_any_prev = False
        self._byp_expired = False               # indication-only timeout latch
        self._startup_prev = False
        self._startup_timer = 0.0
        self._startup_elapsed = 0.0
        self._stable_timer = 0.0
        self._time_to_stable = 0.0
        self._stable_recorded = False
        self._trip = _TripDelay()
        self._trip_nb = _TripDelay()            # OUT_D with no bypasses at all
        super().__init__(instance_name)

    # ─── terminals ─────────────────────────────────────────────────
    def _in_name(self, n: int) -> str:
        return self._IN_FMT.format(n=n)

    def _define_common_terminals(self):
        for n in range(1, MAX_INPUTS + 1):
            t = self.add_input(self._in_name(n), self._IN_TYPE, self._IN_DEFAULT,
                               f"Sensor input {n}")
            t.hidden = n > DEFAULT_INPUTS
            b = self.add_input(f"{self._in_name(n)}_BAD", DataType.BOOL, False,
                               f"Input {n} status is Bad (Uncertain counts as Good)")
            b.hidden = True
        self.add_input("BYPASS_PERMIT", DataType.BOOL, False,
                       "Must be True to accept a BYPASSn unless "
                       "OPT_BYPASS_PERMIT_NOT_REQUIRED")
        self.add_input("STARTUP", DataType.BOOL, False,
                       "Startup bypass trigger (rising edge when timed, "
                       "level when OPT_STARTUP_EVENT_BASED)")

        self.add_output("OUT_D", DataType.BOOL, True,
                        "Trip output — Normal (1) / Tripped (0)")
        self.add_output("OUT_D_NOBYPASS", DataType.BOOL, True,
                        "OUT_D assuming no bypasses and no startup bypass")
        self.add_output("OUT_D_GOOD", DataType.BOOL, True,
                        "OUT_D status is Good")
        self.add_output("TRIP_STATUS", DataType.STRING, ST_NORMAL,
                        "Normal / Tripped / Trip Inhibited / Voted to Trip - "
                        "Delayed / Voted Normal - Delayed")
        self.add_output("TRIP_VOTES", DataType.INT, 0,
                        "Trip votes (bypassed inputs excluded)")
        self.add_output("A_NUM_TO_TRIP", DataType.INT, 0,
                        "Actual votes needed to trip after bypass degradation")
        self.add_output("DELAY_TIMER", DataType.FLOAT, 0.0,
                        "Countdown for TRIP_DELAY / NORMAL_DELAY on OUT_D")
        for n in range(1, MAX_INPUTS + 1):
            t = self.add_output(self._VOTE_FMT.format(n=n), DataType.BOOL, False,
                                f"Input {n} is voting to trip")
            t.hidden = n > DEFAULT_INPUTS

        for name, default, desc, hide in (
            ("BYPASS_TIMER", 0.0, "Maintenance bypass countdown (s)", False),
            ("BYPASS_TIMER_H", 0.0, "Maintenance bypass countdown (h)", True),
            ("STARTUP_TIMER", 0.0, "Startup inhibit countdown (s)", False),
            ("STABLE_TIMER", 0.0, "Consecutive vote-free seconds during startup bypass", True),
            ("TIME_TO_STABLE", 0.0, "Seconds from startup-bypass start until "
                                    "inputs became and stayed stable", True),
        ):
            t = self.add_output(name, DataType.FLOAT, default, desc)
            t.hidden = hide

        self.add_output("MERROR_ROLLUP", DataType.BOOL, False,
                        "Alert should roll up to module level (Input Bad only, "
                        "suppressed by OPT_NO_ALARM_ROLLUP)")

    def _add_alert_outputs(self, names: tuple[tuple[str, str], ...], alerts_term: str):
        """Individual alert booleans + the packed Azeo alert bitstring."""
        for i, (name, desc) in enumerate(names):
            self.add_output(name, DataType.BOOL, False, f"{desc} (bit {i})")
        self.add_output(alerts_term, DataType.INT, 0,
                        "Packed alert bitstring — bit order as listed above")

    # ─── config ────────────────────────────────────────────────────
    def _common_schema(self) -> dict:
        s: dict = {
            "NUM_INPUTS": (int, DEFAULT_INPUTS, "Inputs in service (extensible 1-16)"),
            "NUM_TO_TRIP": (int, 2, "M — configured votes required to trip"),
            "TRIP_DELAY": (float, 0.0,
                           "Consecutive seconds with enough votes before tripping"),
            "NORMAL_DELAY": (float, 0.0,
                             "Consecutive seconds below the required votes before "
                             "returning to Normal"),
            "STATUS_OPT": (str, STATUS_OPTS[0],
                           "Bad-input handling: ALWAYS_USE_VALUE / "
                           "WILL_NOT_VOTE_IF_BAD / VOTE_TO_TRIP_IF_BAD"),
            "BYPASS_TIMEOUT": (float, 0.0,
                               "Max bypass duration before all BYPASSn are cleared; "
                               "0 or indication-only = never auto-clears"),
            "BYPASS_TIMER": (float, 0.0,
                             "Bypass countdown (s) — writeable to extend a bypass"),
            "REMINDER_TIME": (float, 0.0,
                              "Advance-warning window before a bypass timer expires"),
            "STARTUP_TIME": (float, 0.0, "Duration of the timed startup inhibit"),
            "STABLE_TIME": (float, 0.0,
                            "Consecutive vote-free seconds required for "
                            "startup-bypass stabilization expiry"),
        }
        for i, (name, desc) in enumerate(BYPASS_OPTS):
            s[name] = (bool, False, f"BYPASS_OPTS bit {i}: {desc}")
        s["OPT_NO_ALARM_ROLLUP"] = (
            bool, False,
            "REPORT_OPTS bit 0: Alarm conditions do not roll up to module level")
        for n in range(1, MAX_INPUTS + 1):
            s[f"BYPASS{n}"] = (bool, False, f"Exclude input {n} from voting")
        for n in range(1, MAX_INPUTS + 1):
            s[f"DESC{n}"] = (str, "", f"Label for input {n}")
        return s

    def _apply_config(self):
        # Keep the extensible-input pins in step with NUM_INPUTS.
        n_in = self._num_inputs()
        for n in range(1, MAX_INPUTS + 1):
            hide = n > n_in
            self.inputs[self._in_name(n)].hidden = hide
            self.outputs[self._VOTE_FMT.format(n=n)].hidden = hide

    def _num_inputs(self) -> int:
        return max(1, min(MAX_INPUTS, int(self.config.params.get("NUM_INPUTS",
                                                                 DEFAULT_INPUTS))))

    def reset(self):
        super().reset()
        self._byp = [False] * MAX_INPUTS
        self._byp_any_prev = False
        self._byp_expired = False
        self._startup_prev = False
        self._startup_timer = 0.0
        self._startup_elapsed = 0.0
        self._stable_timer = 0.0
        self._time_to_stable = 0.0
        self._stable_recorded = False
        self._trip.reset()
        self._trip_nb.reset()
        self.config.params["BYPASS_TIMER"] = 0.0

    # ─── shared execution helpers ──────────────────────────────────
    def _update_bypasses(self, n_in: int, dt: float) -> None:
        """Accept/reject BYPASSn requests, then run the bypass timeout."""
        p = self.config.params
        permit = (bool(self.get_input("BYPASS_PERMIT"))
                  or bool(p.get("OPT_BYPASS_PERMIT_NOT_REQUIRED", False)))
        multi = bool(p.get("OPT_MULTIPLE_BYPASSES_ALLOWED", False))

        for n in range(1, n_in + 1):
            requested = bool(p.get(f"BYPASS{n}", False))
            idx = n - 1
            if not requested:
                self._byp[idx] = False
                continue
            if self._byp[idx]:
                continue                       # already accepted, keep it
            # A new request needs the permit and, unless multiple bypasses
            # are allowed, no other bypass in effect. Deselecting "multiple
            # allowed" while several are set therefore blocks new bypasses
            # until all of them clear (doc §Execution).
            if permit and (multi or not any(self._byp[:n_in])):
                self._byp[idx] = True
            else:
                p[f"BYPASS{n}"] = False        # rejected — clear the request
        for n in range(n_in, MAX_INPUTS):      # inputs out of service never vote
            self._byp[n] = False

        any_byp = any(self._byp[:n_in])
        timeout = max(0.0, float(p.get("BYPASS_TIMEOUT", 0.0)))
        timer = max(0.0, float(p.get("BYPASS_TIMER", 0.0)))
        if any_byp and not self._byp_any_prev and timeout > 0.0:
            timer = timeout                    # preset on the FIRST bypass only
        if not any_byp:
            timer = 0.0
            self._byp_expired = False
        elif timer > 0.0:
            timer = max(0.0, timer - dt)
            if timer == 0.0:
                if bool(p.get("OPT_BYPASS_TIMEOUT_INDICATION_ONLY", False)):
                    self._byp_expired = True   # bypasses persist, reminder only
                else:
                    for n in range(1, MAX_INPUTS + 1):
                        p[f"BYPASS{n}"] = False
                    self._byp = [False] * MAX_INPUTS
                    any_byp = False
        p["BYPASS_TIMER"] = timer
        self._byp_any_prev = any_byp
        self.set_output("BYPASS_TIMER", timer)
        self.set_output("BYPASS_TIMER_H", timer / 3600.0)

    def _update_startup(self, votes_enough: bool, dt: float) -> bool:
        """Run the startup bypass; returns True while it inhibits tripping."""
        p = self.config.params
        startup_in = bool(self.get_input("STARTUP"))
        rising = startup_in and not self._startup_prev
        self._startup_prev = startup_in
        stable_time = max(0.0, float(p.get("STABLE_TIME", 0.0)))

        if bool(p.get("OPT_STARTUP_EVENT_BASED", False)):
            # Event-based: active exactly while STARTUP is True, and the
            # stabilization timers are not processed (zeroed on True).
            active = startup_in
            self._startup_timer = 0.0
            if startup_in:
                self._stable_timer = 0.0
                self._time_to_stable = 0.0
                self._startup_elapsed = 0.0
                self._stable_recorded = False
        else:
            if rising and (self._startup_timer <= 0.0
                           or bool(p.get("OPT_STARTUP_PRESET_WHILE_ACTIVE", False))):
                self._startup_timer = max(0.0, float(p.get("STARTUP_TIME", 0.0)))
                self._startup_elapsed = 0.0
                self._stable_timer = 0.0
                self._time_to_stable = 0.0
                self._stable_recorded = False
            active = self._startup_timer > 0.0
            if active:
                self._startup_timer = max(0.0, self._startup_timer - dt)
                self._startup_elapsed += dt
                # STABLE_TIMER counts vote-free time, resetting whenever
                # the votes reach the required number.
                self._stable_timer = 0.0 if votes_enough else self._stable_timer + dt
                if not self._stable_recorded and self._stable_timer >= stable_time:
                    # Seconds from startup until the inputs *became* stable.
                    self._time_to_stable = max(
                        0.0, self._startup_elapsed - self._stable_timer)
                    self._stable_recorded = True
                    if bool(p.get("OPT_STARTUP_EXPIRES_ON_STABLE", False)):
                        self._startup_timer = 0.0
                active = self._startup_timer > 0.0

        self.set_output("STARTUP_TIMER", self._startup_timer)
        self.set_output("STABLE_TIMER", self._stable_timer)
        self.set_output("TIME_TO_STABLE", self._time_to_stable)
        return active

    def _effective_num_to_trip(self, n_in: int) -> int:
        p = self.config.params
        m = max(1, int(p.get("NUM_TO_TRIP", 2)))
        n_byp = sum(1 for n in range(n_in) if self._byp[n])
        if n_byp and bool(p.get("OPT_BYPASS_REDUCES_NUM_TO_TRIP", False)):
            # (M-1)oo(N-1) per bypass, never below 1oo(N-1).
            m = max(1, m - n_byp)
        return m

    def _reminder_active(self) -> bool:
        p = self.config.params
        rem = max(0.0, float(p.get("REMINDER_TIME", 0.0)))
        timer = float(p.get("BYPASS_TIMER", 0.0))
        if rem > 0.0 and 0.0 < timer <= rem:
            return True
        if bool(p.get("OPT_REMINDER_APPLIES_TO_STARTUP", False)) and rem > 0.0 \
                and 0.0 < self._startup_timer < rem:
            return True
        # Indication-only timeout: the reminder activates at timeout even
        # with REMINDER_TIME = 0 and stays active until all bypasses go.
        return self._byp_expired

    def _publish_alerts(self, names: tuple[tuple[str, str], ...], alerts_term: str,
                        values: dict[str, bool]) -> None:
        bits = 0
        for i, (name, _desc) in enumerate(names):
            v = bool(values.get(name, False))
            self.set_output(name, v)
            if v:
                bits |= 1 << i
        self.set_output(alerts_term, bits)

    def _output_status_good(self, n_in: int, bad: list[bool], m: int) -> bool:
        """OUT_D status — computed independently of the output value."""
        if all(self._byp[n] for n in range(n_in)):
            return True                        # all inputs bypassed → Good
        good_unbypassed = sum(1 for n in range(n_in)
                              if not self._byp[n] and not bad[n])
        return good_unbypassed >= m


# ═══════════════════════════════════════════════════════════════════════
#  AVTR — Analog Voter
# ═══════════════════════════════════════════════════════════════════════

@register_block
class AnalogVoterBlock(_VoterBase):
    """Analog Voter (AVTR) — M-out-of-N vote on 1..16 analog inputs.

    Each ``INn`` is compared against the common ``TRIP_LIM`` (and, on a
    parallel path, ``PRE_TRIP_LIM``); ``DETECT_TYPE`` selects Greater
    Than or Less Than, and ``TRIP_HYS`` (% of IN_SCALE) is the deadband
    for *clearing* a vote. ``OUT_D`` trips (goes 0) once
    ``TRIP_VOTES >= A_NUM_TO_TRIP`` has persisted for ``TRIP_DELAY``
    seconds; it returns to Normal (1) after ``NORMAL_DELAY`` seconds
    below the required votes. ``PRE_OUT_D`` runs the identical machinery
    off ``PRE_TRIP_LIM``, giving a pre-alarm or a second trip point from
    one block.

    Maintenance bypasses (``BYPASSn`` config params, gated by
    ``BYPASS_PERMIT``) remove an input from voting: an MooN voter
    degrades to Moo(N-1), or to (M-1)oo(N-1) with
    ``OPT_BYPASS_REDUCES_NUM_TO_TRIP``, never below 1oo(N-1).
    ``BYPASS_TIMEOUT`` / ``REMINDER_TIME`` implement the bypass timeout
    and its expiration reminder; ``STARTUP`` implements the timed,
    stabilization-expiry, and event-based startup inhibits.

    The deviation check raises *Deviation Limit Exceeded* when
    (max INn − min INn) exceeds ``DEV_LIM``, with ``DEV_HYS`` (% of
    IN_SCALE) as the reset deadband. ``DEV_LIM = 0`` disables the check.

    Voting configurations: any MooN with 1 ≤ M ≤ N ≤ 16 — 1oo1, 1oo2,
    2oo2, 2oo3, 2oo4, 6oo8 … — is expressed as ``NUM_TO_TRIP`` (M) over
    ``NUM_INPUTS`` (N); the doc's degradation table is reproduced by
    the bypass / Bad-status handling rather than by an enumerated list.

    No mode parameter in Azeo, so no MODE config. Hardware bindings
    (secure writes, SIS logic solver, faceplate bypass buttons) are not
    modelled; ``OPT_BYPASS_PERMIT_VISIBLE`` is carried as configuration
    only.
    """

    block_type = "AVTR"
    display_name = "Analog Voter (AVTR)"
    description = "MooN analog trip voting with bypass, pre-trip and deviation check"

    _IN_FMT = "IN{n}"
    _IN_TYPE = DataType.FLOAT
    _IN_DEFAULT = 0.0

    # DETECT_TYPE is AVTR-only (DVTR's discretes are votes already).
    config_choices = {
        **_VoterBase.config_choices,
        "DETECT_TYPE": DETECT_TYPES,
    }
    config_units = {
        **_VoterBase.config_units,
        "TRIP_LIM": "EU of IN_SCALE",
        "PRE_TRIP_LIM": "EU of IN_SCALE",
        "TRIP_HYS": "% of IN_SCALE",
        "IN_SCALE_LO": "EU of IN_SCALE",
        "IN_SCALE_HI": "EU of IN_SCALE",
        "DEV_LIM": "EU of IN_SCALE",
        "DEV_HYS": "% of IN_SCALE",
    }

    _ALERTS = (
        ("ALM_TRIP_ACTIVE", "Trip Active"),
        ("ALM_PRE_TRIP_ACTIVE", "Pre-Trip Active"),
        ("ALM_BYPASS_ACTIVE", "Bypass Active"),
        ("ALM_STARTUP_OVERRIDE", "Startup Override Active"),
        ("ALM_DEVIATION", "Deviation Limit Exceeded"),
        ("ALM_EXPIRATION_REMINDER", "Expiration Reminder"),
        ("ALM_BYPASSED_INPUT_PRE_TRIPPED", "Bypassed Input Pre-Tripped"),
        ("ALM_BYPASSED_INPUT_TRIPPED", "Bypassed Input Tripped"),
        ("ALM_INPUT_BAD", "Input Bad"),
    )

    def __init__(self, instance_name: str = ""):
        self._vote = [False] * MAX_INPUTS
        self._pre_vote = [False] * MAX_INPUTS
        self._dev_alarm = False
        self._pre_trip = _TripDelay()
        super().__init__(instance_name)

    def _define_terminals(self):
        self._define_common_terminals()
        self.add_output("PRE_OUT_D", DataType.BOOL, True,
                        "Pre-trip output — Normal (1) / Pre-Tripped (0)")
        self.add_output("PRE_TRIP_STATUS", DataType.STRING, ST_NORMAL,
                        "Normal / Pre-Tripped / Pre-Trip Inhibited / Voted to "
                        "Pre-Trip - Delayed / Voted Normal - Delayed")
        self.add_output("PRE_VOTES", DataType.INT, 0,
                        "Pre-trip votes (bypassed inputs excluded)")
        self.add_output("PRE_DELAY_TIMER", DataType.FLOAT, 0.0,
                        "Countdown for TRIP_DELAY / NORMAL_DELAY on PRE_OUT_D")
        self.add_output("DEVIATION", DataType.FLOAT, 0.0,
                        "max INn - min INn across the configured inputs")
        for n in range(1, MAX_INPUTS + 1):
            t = self.add_output(f"PRE_VOTE_IN{n}", DataType.BOOL, False,
                                f"Input {n} is voting to pre-trip")
            t.hidden = True
        self._add_alert_outputs(self._ALERTS, "AVTR_ALERTS")
        # The doc's AVTR parameter table spells this OUT_D_NOBYPBASS — a
        # typo for DVTR's OUT_D_NOBYPASS (doc §AVTR Execution / Parameters).
        # The correct spelling is the real terminal; this hidden mirror
        # keeps any strategy JSON written against the typo working.
        t = self.add_output("OUT_D_NOBYPBASS", DataType.BOOL, True,
                            "Deprecated alias of OUT_D_NOBYPASS (Azeo doc typo)")
        t.hidden = True

    def get_config_schema(self):
        s = {
            "TRIP_LIM": (float, 90.0, "Trip limit compared against each INn"),
            "PRE_TRIP_LIM": (float, 80.0, "Pre-trip limit, common to all inputs"),
            "DETECT_TYPE": (str, DETECT_TYPES[0],
                            "Comparison against the limits: GREATER_THAN / LESS_THAN"),
            "TRIP_HYS": (float, 0.5,
                         "Deadband (0-50 % of IN_SCALE) for clearing a trip / "
                         "pre-trip vote"),
            "IN_SCALE_LO": (float, 0.0, "Common input scale — low"),
            "IN_SCALE_HI": (float, 100.0, "Common input scale — high"),
            "DEV_LIM": (float, 0.0,
                        "Max expected spread between highest and lowest INn "
                        "(0 disables the deviation check)"),
            "DEV_HYS": (float, 0.0,
                        "Deadband (0-50 % of IN_SCALE) for resetting the "
                        "deviation condition"),
        }
        s.update(self._common_schema())
        return s

    def _apply_config(self):
        super()._apply_config()
        p = self.config.params
        # Accept the doc's OUT_D_NOBYPBASS typo as a config alias.
        if "OUT_D_NOBYPBASS" in p and "OUT_D_NOBYPASS" not in p:
            p["OUT_D_NOBYPASS"] = p.pop("OUT_D_NOBYPBASS")
        for n in range(1, MAX_INPUTS + 1):
            self.outputs[f"PRE_VOTE_IN{n}"].hidden = n > self._num_inputs()

    def reset(self):
        super().reset()
        self._vote = [False] * MAX_INPUTS
        self._pre_vote = [False] * MAX_INPUTS
        self._dev_alarm = False
        self._pre_trip.reset()

    # ─── vote detection ────────────────────────────────────────────
    def _limit_vote(self, value: float, prev: bool, limit: float,
                    hys: float, less_than: bool) -> bool:
        """Limit comparison; TRIP_HYS is the deadband for *clearing*."""
        if less_than:
            return value <= limit if not prev else value <= limit + hys
        return value >= limit if not prev else value >= limit - hys

    def execute(self, dt: float):
        p = self.config.params
        n_in = self._num_inputs()
        span = abs(float(p.get("IN_SCALE_HI", 100.0))
                   - float(p.get("IN_SCALE_LO", 0.0)))
        hys = max(0.0, min(50.0, float(p.get("TRIP_HYS", 0.5)))) / 100.0 * span
        less = str(p.get("DETECT_TYPE", DETECT_TYPES[0])).upper() == "LESS_THAN"
        status_opt = str(p.get("STATUS_OPT", STATUS_OPTS[0])).upper()
        trip_lim = float(p.get("TRIP_LIM", 90.0))
        pre_lim = float(p.get("PRE_TRIP_LIM", 80.0))

        vals = [float(self.get_input(self._in_name(n))) for n in range(1, n_in + 1)]
        bad = [bool(self.get_input(f"{self._in_name(n)}_BAD"))
               for n in range(1, n_in + 1)]

        self._update_bypasses(n_in, dt)

        votes = pre_votes = 0
        votes_all = pre_votes_all = 0        # no-bypass shadow
        participating = participating_all = 0
        byp_tripped = byp_pre_tripped = False
        for i in range(n_in):
            v = self._limit_vote(vals[i], self._vote[i], trip_lim, hys, less)
            pv = self._limit_vote(vals[i], self._pre_vote[i], pre_lim, hys, less)
            if bad[i]:
                if status_opt == "VOTE_TO_TRIP_IF_BAD":
                    v = pv = True
                elif status_opt == "WILL_NOT_VOTE_IF_BAD":
                    v = pv = False
            self._vote[i], self._pre_vote[i] = v, pv
            self.set_output(self._VOTE_FMT.format(n=i + 1), v)
            self.set_output(f"PRE_VOTE_IN{i + 1}", pv)

            takes_part = not (bad[i] and status_opt == "WILL_NOT_VOTE_IF_BAD")
            if takes_part:
                participating_all += 1
            if v:
                votes_all += 1
            if pv:
                pre_votes_all += 1
            if self._byp[i]:
                byp_tripped |= v
                byp_pre_tripped |= pv
                continue
            if takes_part:
                participating += 1
            if v:
                votes += 1
            if pv:
                pre_votes += 1

        m = self._effective_num_to_trip(n_in)
        too_few = participating < m or participating == 0
        startup = self._update_startup(votes >= m and not too_few, dt)
        inhibited = startup or too_few

        trip_delay = max(0.0, float(p.get("TRIP_DELAY", 0.0)))
        normal_delay = max(0.0, float(p.get("NORMAL_DELAY", 0.0)))
        out_d = self._trip.step(votes >= m, inhibited, trip_delay, normal_delay, dt)
        pre_out_d = self._pre_trip.step(pre_votes >= m, inhibited,
                                        trip_delay, normal_delay, dt)

        # OUT_D_NOBYPASS — same machinery with no maintenance bypasses and
        # no startup bypass in effect.
        m_nb = max(1, int(p.get("NUM_TO_TRIP", 2)))
        out_nb = self._trip_nb.step(
            votes_all >= m_nb,
            participating_all < m_nb or participating_all == 0,
            trip_delay, normal_delay, dt)

        # Deviation check across every configured input (a bypassed input
        # is still monitored — cf. the Bypassed Input Tripped alert).
        dev = (max(vals) - min(vals)) if len(vals) >= 2 else 0.0
        dev_lim = float(p.get("DEV_LIM", 0.0))
        dev_hys = max(0.0, min(50.0, float(p.get("DEV_HYS", 0.0)))) / 100.0 * span
        if dev_lim > 0.0:
            self._dev_alarm = (dev > dev_lim - dev_hys if self._dev_alarm
                               else dev > dev_lim)
        else:
            self._dev_alarm = False

        self.set_output("OUT_D", out_d)
        self.set_output("PRE_OUT_D", pre_out_d)
        self.set_output("OUT_D_NOBYPASS", out_nb)
        self.set_output("OUT_D_NOBYPBASS", out_nb)
        self.set_output("TRIP_STATUS", self._trip.state)
        self.set_output("PRE_TRIP_STATUS", _pre_state_name(self._pre_trip.state))
        self.set_output("TRIP_VOTES", votes)
        self.set_output("PRE_VOTES", pre_votes)
        self.set_output("A_NUM_TO_TRIP", m)
        self.set_output("DELAY_TIMER", self._trip.timer)
        self.set_output("PRE_DELAY_TIMER", self._pre_trip.timer)
        self.set_output("DEVIATION", dev)

        any_bad = any(bad)
        good = self._output_status_good(n_in, bad, m)
        self.set_output("OUT_D_GOOD", good)
        self.status = BlockStatus.GOOD if good else BlockStatus.BAD
        self.set_output("MERROR_ROLLUP",
                        any_bad and not bool(p.get("OPT_NO_ALARM_ROLLUP", False)))
        self._publish_alerts(self._ALERTS, "AVTR_ALERTS", {
            "ALM_TRIP_ACTIVE": not out_d,
            "ALM_PRE_TRIP_ACTIVE": not pre_out_d,
            "ALM_BYPASS_ACTIVE": any(self._byp[:n_in]),
            "ALM_STARTUP_OVERRIDE": startup,
            "ALM_DEVIATION": self._dev_alarm,
            "ALM_EXPIRATION_REMINDER": self._reminder_active(),
            "ALM_BYPASSED_INPUT_PRE_TRIPPED": byp_pre_tripped,
            "ALM_BYPASSED_INPUT_TRIPPED": byp_tripped,
            "ALM_INPUT_BAD": any_bad,
        })


def _pre_state_name(state: str) -> str:
    """Map the shared TRIP_STATUS members onto PRE_TRIP_STATUS names."""
    return {
        ST_TRIPPED: "PRE_TRIPPED",
        ST_INHIBITED: "PRE_TRIP_INHIBITED",
        ST_VOTED_TRIP: "VOTED_TO_PRE_TRIP_DELAYED",
    }.get(state, state)


# ═══════════════════════════════════════════════════════════════════════
#  DVTR — Discrete Voter
# ═══════════════════════════════════════════════════════════════════════

@register_block
class DiscreteVoterBlock(_VoterBase):
    """Discrete Voter (DVTR) — M-out-of-N vote on 1..16 discrete inputs.

    Identical machinery to AVTR, but the inputs (``IN_Dn``) are
    discretes that *directly represent votes*, so there is no trip
    limit, hysteresis, input scaling, deviation check or pre-trip path.
    ``OUT_D`` trips (goes 0) once ``TRIP_VOTES >= A_NUM_TO_TRIP`` has
    persisted for ``TRIP_DELAY`` seconds and returns to Normal (1) after
    ``NORMAL_DELAY`` seconds below the required votes. Maintenance
    bypass, bypass permit, timeout, reminder and the three startup-bypass
    flavours behave exactly as in AVTR, with alerts raised in
    ``DVTR_ALERTS``.

    Voting configurations: any MooN with 1 ≤ M ≤ N ≤ 16 (1oo1, 1oo2,
    2oo2, 2oo3, 2oo4, 6oo8 …) via ``NUM_TO_TRIP`` (M) over
    ``NUM_INPUTS`` (N).

    Input polarity: the doc states IN_Dn "directly represent votes" and
    that ``TRIP_VOTE_INn`` is True when IN_Dn votes to trip, so
    ``IN_Dn = True`` is a trip vote here. (The doc does not state the
    field-signal polarity explicitly; only the *output* convention
    1 = Normal / 0 = Tripped is given.) Invert upstream for a
    de-energize-to-trip transmitter.

    No mode parameter in Azeo, so no MODE config; SIS hardware
    bindings are not modelled.
    """

    block_type = "DVTR"
    display_name = "Discrete Voter (DVTR)"
    description = "MooN discrete trip voting with bypass and startup inhibit"

    _IN_FMT = "IN_D{n}"
    _IN_TYPE = DataType.BOOL
    _IN_DEFAULT = False
    _VOTE_FMT = "TRIP_VOTE_IN{n}"

    # config_choices (STATUS_OPT) and config_units (the bypass / delay
    # timers) are inherited unchanged from _VoterBase — DVTR has no trip
    # limit, hysteresis, scaling or deviation parameters to annotate.

    _ALERTS = (
        ("ALM_TRIP_ACTIVE", "Trip Active"),
        ("ALM_BYPASS_ACTIVE", "Bypass Active"),
        ("ALM_STARTUP_OVERRIDE", "Startup Override Active"),
        ("ALM_EXPIRATION_REMINDER", "Expiration Reminder"),
        ("ALM_BYPASSED_INPUT_TRIPPED", "Bypassed Input Tripped"),
        ("ALM_INPUT_BAD", "Input Bad"),
    )

    def _define_terminals(self):
        self._define_common_terminals()
        self._add_alert_outputs(self._ALERTS, "DVTR_ALERTS")

    def get_config_schema(self):
        return self._common_schema()

    def execute(self, dt: float):
        p = self.config.params
        n_in = self._num_inputs()
        status_opt = str(p.get("STATUS_OPT", STATUS_OPTS[0])).upper()

        vals = [bool(self.get_input(self._in_name(n))) for n in range(1, n_in + 1)]
        bad = [bool(self.get_input(f"{self._in_name(n)}_BAD"))
               for n in range(1, n_in + 1)]

        self._update_bypasses(n_in, dt)

        votes = votes_all = 0
        participating = participating_all = 0
        byp_tripped = False
        for i in range(n_in):
            v = vals[i]
            if bad[i]:
                if status_opt == "VOTE_TO_TRIP_IF_BAD":
                    v = True
                elif status_opt == "WILL_NOT_VOTE_IF_BAD":
                    v = False
            self.set_output(self._VOTE_FMT.format(n=i + 1), v)

            takes_part = not (bad[i] and status_opt == "WILL_NOT_VOTE_IF_BAD")
            if takes_part:
                participating_all += 1
            if v:
                votes_all += 1
            if self._byp[i]:
                byp_tripped |= v
                continue
            if takes_part:
                participating += 1
            if v:
                votes += 1

        m = self._effective_num_to_trip(n_in)
        too_few = participating < m or participating == 0
        startup = self._update_startup(votes >= m and not too_few, dt)
        inhibited = startup or too_few

        trip_delay = max(0.0, float(p.get("TRIP_DELAY", 0.0)))
        normal_delay = max(0.0, float(p.get("NORMAL_DELAY", 0.0)))
        out_d = self._trip.step(votes >= m, inhibited, trip_delay, normal_delay, dt)

        m_nb = max(1, int(p.get("NUM_TO_TRIP", 2)))
        out_nb = self._trip_nb.step(
            votes_all >= m_nb,
            participating_all < m_nb or participating_all == 0,
            trip_delay, normal_delay, dt)

        self.set_output("OUT_D", out_d)
        self.set_output("OUT_D_NOBYPASS", out_nb)
        self.set_output("TRIP_STATUS", self._trip.state)
        self.set_output("TRIP_VOTES", votes)
        self.set_output("A_NUM_TO_TRIP", m)
        self.set_output("DELAY_TIMER", self._trip.timer)

        any_bad = any(bad)
        good = self._output_status_good(n_in, bad, m)
        self.set_output("OUT_D_GOOD", good)
        self.status = BlockStatus.GOOD if good else BlockStatus.BAD
        self.set_output("MERROR_ROLLUP",
                        any_bad and not bool(p.get("OPT_NO_ALARM_ROLLUP", False)))
        self._publish_alerts(self._ALERTS, "DVTR_ALERTS", {
            "ALM_TRIP_ACTIVE": not out_d,
            "ALM_BYPASS_ACTIVE": any(self._byp[:n_in]),
            "ALM_STARTUP_OVERRIDE": startup,
            "ALM_EXPIRATION_REMINDER": self._reminder_active(),
            "ALM_BYPASSED_INPUT_TRIPPED": byp_tripped,
            "ALM_INPUT_BAD": any_bad,
        })
