"""Safety function blocks — FOL Logic, SIS Voter, Interlock."""
from __future__ import annotations
from ..model.block_base import FunctionBlock, BlockCategory, DataType
from ..model.block_registry import register_block


@register_block
class FOLLogicBlock(FunctionBlock):
    """Fuel Override Logic — safety override that reduces fuel on abnormal conditions.

    Monitors multiple boolean trip conditions and applies graduated cutback.
    """
    block_type = "FOL_LOGIC"
    category = BlockCategory.SAFETY
    display_name = "FOL Logic"
    description = "Fuel Override Logic — graduated cutback on trip conditions"

    def __init__(self, instance_name=""):
        self._cutback_pct = 0.0
        self._active = False
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("TRIP1", DataType.BOOL, False, "Trip condition 1 (e.g. high CO)")
        self.add_input("TRIP2", DataType.BOOL, False, "Trip condition 2 (e.g. low O2)")
        self.add_input("TRIP3", DataType.BOOL, False, "Trip condition 3 (e.g. high draft)")
        self.add_input("TRIP4", DataType.BOOL, False, "Trip condition 4")
        self.add_input("FUEL_IN", description="Fuel demand from control")
        self.add_input("RESET", DataType.BOOL, False, "Manual reset")
        self.add_output("FUEL_OUT", description="Override fuel output")
        self.add_output("ACTIVE", DataType.BOOL, False, "FOL active")
        self.add_output("CUTBACK_PCT", description="Cutback percentage")

    def get_config_schema(self):
        return {
            "cutback_rate": (float, 10.0, "Cutback rate [%/s]"),
            "min_fuel": (float, 0.1, "Minimum fuel output"),
            "auto_reset": (bool, False, "Auto-reset when conditions clear"),
        }

    def execute(self, dt: float):
        trips = [self.get_input(f"TRIP{i}") for i in range(1, 5)]
        any_trip = any(trips)
        p = self.config.params
        rate = p.get("cutback_rate", 10.0)
        min_fuel = p.get("min_fuel", 0.1)

        if self.get_input("RESET") and not any_trip:
            self._active = False
            self._cutback_pct = 0.0
        elif any_trip:
            self._active = True
            n_trips = sum(1 for t in trips if t)
            target = min(100.0, n_trips * 25.0)
            if self._cutback_pct < target:
                self._cutback_pct = min(target, self._cutback_pct + rate * dt)
        elif self._active and p.get("auto_reset", False):
            self._cutback_pct = max(0.0, self._cutback_pct - rate * dt)
            if self._cutback_pct <= 0:
                self._active = False

        fuel_in = self.get_input("FUEL_IN")
        fuel_out = fuel_in * (1.0 - self._cutback_pct / 100.0)
        fuel_out = max(min_fuel, fuel_out)

        self.set_output("FUEL_OUT", fuel_out)
        self.set_output("ACTIVE", self._active)
        self.set_output("CUTBACK_PCT", self._cutback_pct)

    def reset(self):
        super().reset()
        self._cutback_pct = 0.0
        self._active = False


@register_block
class SISVoterBlock(FunctionBlock):
    """SIS Voting Logic — 1oo2, 2oo3, 1oo3 voting for safety instrumented systems."""
    block_type = "SIS_VOTER"
    category = BlockCategory.SAFETY
    display_name = "SIS Voter"
    description = "Safety voting (1oo2, 2oo3, 1oo3)"

    def _define_terminals(self):
        self.add_input("IN1", DataType.BOOL, False, "Input 1")
        self.add_input("IN2", DataType.BOOL, False, "Input 2")
        self.add_input("IN3", DataType.BOOL, False, "Input 3")
        self.add_input("BYPASS", DataType.BOOL, False, "Bypass voting")
        self.add_output("TRIP", DataType.BOOL, False, "Trip output")
        self.add_output("VOTE_COUNT", DataType.INT, 0, "Number of inputs voted")

    def get_config_schema(self):
        return {
            "voting": (str, "2oo3", "Voting pattern: 1oo2, 2oo3, 1oo3, 1oo1"),
        }

    def execute(self, dt: float):
        if self.get_input("BYPASS"):
            self.set_output("TRIP", False)
            return

        votes = sum(1 for i in range(1, 4) if self.get_input(f"IN{i}"))
        self.set_output("VOTE_COUNT", votes)

        pattern = self.config.params.get("voting", "2oo3")
        if pattern == "1oo1":
            trip = votes >= 1
        elif pattern == "1oo2":
            trip = votes >= 1
        elif pattern == "2oo3":
            trip = votes >= 2
        elif pattern == "1oo3":
            trip = votes >= 1
        else:
            trip = votes >= 2

        self.set_output("TRIP", trip)


@register_block
class InterlockBlock(FunctionBlock):
    """Interlock — latched shutdown with permissive reset."""
    block_type = "INTERLOCK"
    category = BlockCategory.SAFETY
    display_name = "Interlock"
    description = "Latched shutdown requiring all permissives for reset"

    def __init__(self, instance_name=""):
        self._tripped = False
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("TRIP", DataType.BOOL, False, "Trip condition")
        self.add_input("PERM1", DataType.BOOL, True, "Reset permissive 1")
        self.add_input("PERM2", DataType.BOOL, True, "Reset permissive 2")
        self.add_input("RESET", DataType.BOOL, False, "Reset command")
        self.add_output("TRIPPED", DataType.BOOL, False, "Interlock tripped")
        self.add_output("READY", DataType.BOOL, True, "Ready to reset")

    def execute(self, dt: float):
        if self.get_input("TRIP"):
            self._tripped = True

        permissives_ok = self.get_input("PERM1") and self.get_input("PERM2") and not self.get_input("TRIP")
        self.set_output("READY", permissives_ok and self._tripped)

        if self.get_input("RESET") and permissives_ok:
            self._tripped = False

        self.set_output("TRIPPED", self._tripped)

    def reset(self):
        super().reset()
        self._tripped = False


# ════════════════════════════════════════════════════════════════════
#  MOTOR_INTERLOCK — DCC-style motor / pump equipment interlock
# ════════════════════════════════════════════════════════════════════

# Canonical state codes (mirrored on the STATE output). Kept as int so
# the strategy and downstream UIs can compare cheaply; descriptive
# strings are exposed via STATE_NAME for the faceplate.
_MI_STATE_STOPPED          = 0
_MI_STATE_STARTING         = 1
_MI_STATE_RUNNING          = 2
_MI_STATE_STOPPING         = 3
_MI_STATE_TRIPPED          = 4
_MI_STATE_FAILED_TO_START  = 5
_MI_STATE_MAINT            = 6
_MI_STATE_LOCAL            = 7
_MI_STATE_LOCKOUT          = 8

_MI_STATE_NAMES = {
    _MI_STATE_STOPPED:         "STOPPED",
    _MI_STATE_STARTING:        "STARTING",
    _MI_STATE_RUNNING:         "RUNNING",
    _MI_STATE_STOPPING:        "STOPPING",
    _MI_STATE_TRIPPED:         "TRIPPED",
    _MI_STATE_FAILED_TO_START: "FAILED_TO_START",
    _MI_STATE_MAINT:           "MAINTENANCE",
    _MI_STATE_LOCAL:           "LOCAL",
    _MI_STATE_LOCKOUT:         "LOCKOUT",
}


@register_block
class MotorInterlockBlock(FunctionBlock):
    """Motor / pump interlock with categorised conditions, per-channel
    delays, bypass, and first-out tracking — Azeo DCC + EDC style.

    Distinguishes the three real-world condition classes:

        * **PERM_1..8** — *start* permissives. Must ALL be True (after
          their per-channel ``PERM_<N>_DELAY``) before ``START_PERMIT``
          goes True. Bypassable individually via ``BYPASS_PERM_<N>``.
          Failures do **not** by themselves trip a running pump.

        * **RUN_1..8** — *running* interlocks. Must remain True while
          running. A condition that stays False for longer than
          ``RUN_<N>_DELAY_OFF`` trips the pump. Bypassable.

        * **TRIP_1..4** — protective trips. Immediate, never bypassed,
          and not delayed unless explicitly configured. Examples: ESD,
          E-stop, motor overload.

    Operator inputs (``START_REQ``, ``STOP_REQ``, ``RESET``, ``ACK``)
    plus plant feedback (``RUN_FB``, ``LOCAL``, ``MAINT``) drive a
    9-state machine. The block exposes:

        START_PERMIT   - gate this into DEVCTL.START
        RUN_PERMIT     - True only while RUNNING + RUN interlocks OK
        TRIPPED        - latched trip flag
        READY_TO_START - perms OK AND not tripped AND in REMOTE
        FIRST_OUT      - 1..20 = which input tripped first (see _CHANNELS)
        FIRST_OUT_DESC - human-readable description of that channel
        STATE          - integer state code (see _MI_STATE_*)
        STATE_NAME     - matching string name
        ANY_BYPASSED   - True if any bypass is active

    Pair with :class:`DevctlBlock` — wire ``START_PERMIT`` → DEVCTL's
    start-gate and ``TRIPPED`` (inverted) → DEVCTL's run-gate.
    """

    block_type = "MOTOR_INTERLOCK"
    category = BlockCategory.SAFETY
    display_name = "Motor Interlock (DCC)"
    description = ("Equipment interlock with categorised permissives, "
                    "run-interlocks, trips, delays + first-out (Azeo DCC)")

    _NUM_PERMS = 8
    _NUM_RUNS = 8
    _NUM_TRIPS = 4

    def __init__(self, instance_name: str = ""):
        # Per-channel "True for" timers and "False for" timers, plus
        # state-machine bookkeeping. Built before _define_terminals so
        # the latter is free of bookkeeping branches.
        self._perm_true_time: list[float] = [0.0] * self._NUM_PERMS
        self._run_false_time: list[float] = [0.0] * self._NUM_RUNS
        self._state: int = _MI_STATE_STOPPED
        self._start_timer: float = 0.0
        self._tripped: bool = False
        self._first_out_idx: int = 0   # 1..N (channel number); 0 = none
        self._first_out_desc: str = ""
        super().__init__(instance_name)

    # ----- Terminals -----
    def _define_terminals(self):
        # Permissives (start)
        for i in range(1, self._NUM_PERMS + 1):
            self.add_input(f"PERM_{i}", DataType.BOOL, True,
                            f"Start permissive #{i} (must be True to allow start)")
            self.add_input(f"BYPASS_PERM_{i}", DataType.BOOL, False,
                            f"Maintenance bypass for PERM_{i}")
        # Run interlocks
        for i in range(1, self._NUM_RUNS + 1):
            self.add_input(f"RUN_{i}", DataType.BOOL, True,
                            f"Running interlock #{i} (must stay True while running)")
            self.add_input(f"BYPASS_RUN_{i}", DataType.BOOL, False,
                            f"Maintenance bypass for RUN_{i}")
        # Trips
        for i in range(1, self._NUM_TRIPS + 1):
            self.add_input(f"TRIP_{i}", DataType.BOOL, False,
                            f"Protective trip #{i} (immediate, never bypassed)")

        # Operator + plant feedback
        self.add_input("START_REQ", DataType.BOOL, False, "Operator START request")
        self.add_input("STOP_REQ",  DataType.BOOL, False, "Operator STOP request")
        self.add_input("RESET",     DataType.BOOL, False, "Operator RESET (with ACK) to clear trip")
        self.add_input("ACK",       DataType.BOOL, False, "Operator ACK of trip cause")
        self.add_input("RUN_FB",    DataType.BOOL, False, "Plant feedback — motor actually running")
        self.add_input("LOCAL",     DataType.BOOL, False, "Field LOCAL/REMOTE switch — when True, DCS cannot start")
        self.add_input("MAINT",     DataType.BOOL, False, "Maintenance mode active — equipment OOS")
        self.add_input("LOCKOUT",   DataType.BOOL, False, "Lockout / tagout active")

        # Outputs
        self.add_output("START_PERMIT",  DataType.BOOL, False, "True when safe to issue START to device")
        self.add_output("RUN_PERMIT",    DataType.BOOL, False, "True while RUNNING and run-interlocks OK")
        self.add_output("TRIPPED",       DataType.BOOL, False, "Latched trip — clears only via RESET+ACK with cause clean")
        self.add_output("READY_TO_START", DataType.BOOL, True, "Perms OK, not tripped, REMOTE, not in maintenance")
        self.add_output("FIRST_OUT",     DataType.INT,   0,    "Channel number 1..20 of the first cause; 0 = none")
        self.add_output("FIRST_OUT_DESC", DataType.STRING, "", "Description of first-out channel")
        self.add_output("STATE",         DataType.INT,   _MI_STATE_STOPPED, "Numeric state code")
        self.add_output("STATE_NAME",    DataType.STRING, "STOPPED", "State name (STOPPED, STARTING, ...)")
        self.add_output("ANY_BYPASSED",  DataType.BOOL, False, "True if any condition is bypassed")
        self.add_output("PERMS_OK",      DataType.BOOL, True,  "All start permissives healthy (incl. delay)")
        self.add_output("TRIP_ACTIVE",   DataType.BOOL, False, "True while any trip / failed run-interlock is active (instantaneous)")

    # ----- Config -----
    def get_config_schema(self) -> dict:
        schema: dict = {
            "STARTUP_TIMEOUT": (float, 5.0,
                "Seconds from START_REQ to RUN_FB before declaring FAILED_TO_START"),
            "STOP_TIMEOUT":    (float, 5.0,
                "Seconds from STOP_REQ to !RUN_FB before forcing STOPPED"),
            "AUTO_RESET":      (bool, False,
                "If True, trips clear automatically once all causes clean"),
            "LATCH_TRIPS":     (bool, True,
                "If True (default), trips require explicit RESET+ACK"),
        }
        # Per-channel delay + description config
        for i in range(1, self._NUM_PERMS + 1):
            schema[f"PERM_{i}_DELAY"] = (float, 0.0,
                f"Seconds PERM_{i} must be True before counted")
            schema[f"PERM_{i}_DESC"]  = (str, "",
                f"Operator-facing description of PERM_{i}")
        for i in range(1, self._NUM_RUNS + 1):
            schema[f"RUN_{i}_DELAY_OFF"] = (float, 0.0,
                f"Seconds RUN_{i} must be False before tripping the pump")
            schema[f"RUN_{i}_DESC"]      = (str, "",
                f"Operator-facing description of RUN_{i}")
        for i in range(1, self._NUM_TRIPS + 1):
            schema[f"TRIP_{i}_DESC"] = (str, "",
                f"Operator-facing description of TRIP_{i}")
        return schema

    def reset(self):
        super().reset()
        self._perm_true_time = [0.0] * self._NUM_PERMS
        self._run_false_time = [0.0] * self._NUM_RUNS
        self._state = _MI_STATE_STOPPED
        self._start_timer = 0.0
        self._tripped = False
        self._first_out_idx = 0
        self._first_out_desc = ""

    # ----- Channel-number conventions (for FIRST_OUT) -----
    # 1..8   -> PERM_1..PERM_8
    # 9..16  -> RUN_1..RUN_8
    # 17..20 -> TRIP_1..TRIP_4
    def _channel_desc(self, channel: int) -> str:
        p = self.config.params
        if 1 <= channel <= self._NUM_PERMS:
            d = p.get(f"PERM_{channel}_DESC", "")
            return d or f"PERM_{channel}"
        if self._NUM_PERMS < channel <= self._NUM_PERMS + self._NUM_RUNS:
            n = channel - self._NUM_PERMS
            d = p.get(f"RUN_{n}_DESC", "")
            return d or f"RUN_{n}"
        n = channel - self._NUM_PERMS - self._NUM_RUNS
        d = p.get(f"TRIP_{n}_DESC", "")
        return d or f"TRIP_{n}"

    def _latch_first_out(self, channel: int):
        if self._first_out_idx == 0:
            self._first_out_idx = channel
            self._first_out_desc = self._channel_desc(channel)

    # ----- The queryable condition table (HMI §8.6) -----
    def condition_table(self, include_unused: bool = False) -> list[dict]:
        """Every interlock condition as a row — the faceplate's table.

        Per condition: channel number, kind, name, operator description,
        current state, on/off delay, elapsed timer toward that delay,
        bypassed flag, and whether it is the latched first-out. By default
        only conditions that are wired or described are returned — a
        20-row table of unused channels is noise the operator scrolls
        past to find the one that matters.
        """
        p = self.config.params
        rows: list[dict] = []

        def used(name: str, desc: str) -> bool:
            terminal = self.inputs.get(name)
            return bool(desc) or bool(terminal is not None
                                      and terminal.connected)

        for i in range(1, self._NUM_PERMS + 1):
            desc = str(p.get(f"PERM_{i}_DESC", "") or "")
            if not include_unused and not used(f"PERM_{i}", desc):
                continue
            rows.append({
                "channel": i, "kind": "permissive", "name": f"PERM_{i}",
                "description": desc or f"PERM_{i}",
                "state": bool(self.get_input(f"PERM_{i}")),
                "delay": float(p.get(f"PERM_{i}_DELAY", 0.0) or 0.0),
                "timer": round(self._perm_true_time[i - 1], 2),
                "bypassed": bool(self.get_input(f"BYPASS_PERM_{i}")),
                "first_out": self._first_out_idx == i,
            })
        for i in range(1, self._NUM_RUNS + 1):
            desc = str(p.get(f"RUN_{i}_DESC", "") or "")
            if not include_unused and not used(f"RUN_{i}", desc):
                continue
            channel = self._NUM_PERMS + i
            rows.append({
                "channel": channel, "kind": "interlock",
                "name": f"RUN_{i}", "description": desc or f"RUN_{i}",
                "state": bool(self.get_input(f"RUN_{i}")),
                "delay": float(p.get(f"RUN_{i}_DELAY_OFF", 0.0) or 0.0),
                "timer": round(self._run_false_time[i - 1], 2),
                "bypassed": bool(self.get_input(f"BYPASS_RUN_{i}")),
                "first_out": self._first_out_idx == channel,
            })
        for i in range(1, self._NUM_TRIPS + 1):
            desc = str(p.get(f"TRIP_{i}_DESC", "") or "")
            if not include_unused and not used(f"TRIP_{i}", desc):
                continue
            channel = self._NUM_PERMS + self._NUM_RUNS + i
            rows.append({
                "channel": channel, "kind": "trip", "name": f"TRIP_{i}",
                "description": desc or f"TRIP_{i}",
                "state": bool(self.get_input(f"TRIP_{i}")),
                "delay": 0.0, "timer": 0.0,
                "bypassed": False,      # trips are never bypassed
                "first_out": self._first_out_idx == channel,
            })
        return rows

    # ----- Execute -----
    def execute(self, dt: float):
        p = self.config.params

        # ── Read inputs ─────────────────────────────────────────────
        bypassed_any = False
        perms_effective: list[bool] = []   # after bypass + delay
        for i in range(1, self._NUM_PERMS + 1):
            raw = bool(self.get_input(f"PERM_{i}"))
            byp = bool(self.get_input(f"BYPASS_PERM_{i}"))
            bypassed_any = bypassed_any or byp
            delay = max(0.0, float(p.get(f"PERM_{i}_DELAY", 0.0)))
            if raw or byp:
                self._perm_true_time[i - 1] += dt
            else:
                self._perm_true_time[i - 1] = 0.0
            perms_effective.append(byp or (raw and self._perm_true_time[i - 1] >= delay))

        # Run interlocks — track "False for" duration so we trip on
        # sustained loss, not transients
        run_failed: list[bool] = []
        for i in range(1, self._NUM_RUNS + 1):
            raw = bool(self.get_input(f"RUN_{i}"))
            byp = bool(self.get_input(f"BYPASS_RUN_{i}"))
            bypassed_any = bypassed_any or byp
            delay_off = max(0.0, float(p.get(f"RUN_{i}_DELAY_OFF", 0.0)))
            if byp or raw:
                self._run_false_time[i - 1] = 0.0
                run_failed.append(False)
            else:
                self._run_false_time[i - 1] += dt
                run_failed.append(self._run_false_time[i - 1] >= delay_off)

        trip_active = [bool(self.get_input(f"TRIP_{i}"))
                       for i in range(1, self._NUM_TRIPS + 1)]

        start_req = bool(self.get_input("START_REQ"))
        stop_req  = bool(self.get_input("STOP_REQ"))
        reset     = bool(self.get_input("RESET"))
        ack       = bool(self.get_input("ACK"))
        run_fb    = bool(self.get_input("RUN_FB"))
        local     = bool(self.get_input("LOCAL"))
        maint     = bool(self.get_input("MAINT"))
        lockout   = bool(self.get_input("LOCKOUT"))

        # ── Latch first-out as soon as ANY trip/run-fail fires ─────
        for i, t in enumerate(trip_active, start=self._NUM_PERMS + self._NUM_RUNS + 1):
            if t:
                self._latch_first_out(i)
        for i, f in enumerate(run_failed, start=self._NUM_PERMS + 1):
            # Only latch run-interlock failures while in a running state —
            # a low-suction-pressure permissive at idle isn't a trip cause.
            if f and self._state == _MI_STATE_RUNNING:
                self._latch_first_out(i)

        # Latching state. LATCH_TRIPS controls whether trips need
        # explicit reset; AUTO_RESET allows auto-clearance once causes
        # are clean.
        any_trip_now = any(trip_active) or any(
            run_failed[i - 1] for i in range(1, self._NUM_RUNS + 1)
            if self._state == _MI_STATE_RUNNING
        )

        latch_trips = bool(p.get("LATCH_TRIPS", True))
        auto_reset  = bool(p.get("AUTO_RESET", False))

        if any_trip_now and self._state in (
                _MI_STATE_STARTING, _MI_STATE_RUNNING, _MI_STATE_STOPPING):
            # Trip while operating
            self._tripped = True
            self._state = _MI_STATE_TRIPPED

        # ── State machine ──────────────────────────────────────────
        # Helper: are *all* perms (after delay) healthy?
        perms_ok = all(perms_effective)
        # Reset eligibility (§19): trip latch clear if cause-clean +
        # operator RESET+ACK (or auto-reset)
        causes_clean = (not any(trip_active)) and \
                        (not any(run_failed))
        if self._tripped:
            if (reset and ack and causes_clean) or (auto_reset and causes_clean
                                                     and not latch_trips):
                self._tripped = False
                self._first_out_idx = 0
                self._first_out_desc = ""
                self._state = _MI_STATE_STOPPED

        # Maintenance + lockout dominate everything
        if lockout:
            self._state = _MI_STATE_LOCKOUT
        elif maint:
            self._state = _MI_STATE_MAINT
        elif self._tripped:
            self._state = _MI_STATE_TRIPPED
        elif local and self._state not in (_MI_STATE_RUNNING, _MI_STATE_STOPPING):
            self._state = _MI_STATE_LOCAL
        else:
            # Normal transitions
            if self._state == _MI_STATE_STOPPED:
                if start_req and perms_ok and not local and not maint and not lockout:
                    self._state = _MI_STATE_STARTING
                    self._start_timer = 0.0
            elif self._state == _MI_STATE_LOCAL and not local:
                self._state = _MI_STATE_STOPPED
            elif self._state == _MI_STATE_STARTING:
                self._start_timer += dt
                if run_fb:
                    self._state = _MI_STATE_RUNNING
                elif self._start_timer >= float(p.get("STARTUP_TIMEOUT", 5.0)):
                    self._state = _MI_STATE_FAILED_TO_START
                    # FIRST_OUT records the timeout class — channel 0 stays;
                    # operators see STATE_NAME instead.
            elif self._state == _MI_STATE_RUNNING:
                if stop_req:
                    self._state = _MI_STATE_STOPPING
                    self._start_timer = 0.0
                elif not run_fb:
                    # Lost run feedback without a stop request — treat as trip
                    self._tripped = True
                    self._latch_first_out(self._NUM_PERMS + self._NUM_RUNS +
                                           self._NUM_TRIPS + 1)
                    self._state = _MI_STATE_TRIPPED
            elif self._state == _MI_STATE_STOPPING:
                self._start_timer += dt
                if not run_fb:
                    self._state = _MI_STATE_STOPPED
                elif self._start_timer >= float(p.get("STOP_TIMEOUT", 5.0)):
                    self._state = _MI_STATE_STOPPED
            elif self._state == _MI_STATE_FAILED_TO_START:
                # Treat the same as a trip for reset purposes; ack+reset clears.
                if reset and ack:
                    self._state = _MI_STATE_STOPPED
                    self._first_out_idx = 0
                    self._first_out_desc = ""

        # ── Outputs ────────────────────────────────────────────────
        start_permit = (perms_ok and not self._tripped and
                         not local and not maint and not lockout and
                         self._state in (_MI_STATE_STOPPED, _MI_STATE_STARTING))
        run_permit = (self._state == _MI_STATE_RUNNING and
                       not any(run_failed) and not any(trip_active))
        ready_to_start = (perms_ok and not self._tripped and
                           not local and not maint and not lockout and
                           self._state == _MI_STATE_STOPPED)

        self.set_output("START_PERMIT", start_permit)
        self.set_output("RUN_PERMIT", run_permit)
        self.set_output("TRIPPED", self._tripped)
        self.set_output("READY_TO_START", ready_to_start)
        self.set_output("FIRST_OUT", self._first_out_idx)
        self.set_output("FIRST_OUT_DESC", self._first_out_desc)
        self.set_output("STATE", self._state)
        self.set_output("STATE_NAME", _MI_STATE_NAMES.get(self._state, "UNKNOWN"))
        self.set_output("ANY_BYPASSED", bypassed_any)
        self.set_output("PERMS_OK", perms_ok)
        self.set_output("TRIP_ACTIVE", any_trip_now)
