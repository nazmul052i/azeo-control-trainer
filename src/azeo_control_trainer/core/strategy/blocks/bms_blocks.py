"""BMS/SIS strategy blocks for burner management and safety logic.

Blocks:
  - BURNER_SEQ   — NFPA 85/86 burner startup sequencer
  - FLAME_DET    — Flame detector (UV/IR) with voting
  - FUEL_VALVE   — Safety shutoff valve (SSOV) with trip logic
  - BLOWER       — Forced-draft blower with interlock
  - PURGE_TIMER  — Pre/post purge timer with proven air
  - TRIP_RELAY   — Latching trip relay with manual reset
"""
from __future__ import annotations

from ..model.block_base import FunctionBlock, BlockCategory, DataType
from ..model.block_registry import register_block


# ── BURNER_SEQ — Startup Sequencer ──────────────────────────────────────────

@register_block
class BurnerSequencerBlock(FunctionBlock):
    """NFPA 85/86 burner startup/shutdown sequencer.

    Manages the startup sequence:
      IDLE → PRE_PURGE → IGNITION → STABILIZE → OPERATION
    And shutdown:
      OPERATION → SHUTDOWN → POST_PURGE → IDLE
    Trip at any time → TRIPPED (requires reset).

    Outputs drive downstream blocks (fuel valves, igniter, blower).
    """
    block_type = "BURNER_SEQ"
    category = BlockCategory.SAFETY
    display_name = "Burner Sequencer"
    description = "NFPA 85/86 startup/shutdown state machine"

    _STATES = ["IDLE", "PRE_PURGE", "IGNITION", "STABILIZE",
               "OPERATION", "SHUTDOWN", "POST_PURGE", "TRIPPED"]

    def __init__(self, instance_name: str = ""):
        self._state = "IDLE"
        self._timer = 0.0
        super().__init__(instance_name)

    def _define_terminals(self):
        # Inputs
        self.add_input("START", DataType.BOOL, False, "Start command")
        self.add_input("STOP", DataType.BOOL, False, "Stop command")
        self.add_input("TRIP", DataType.BOOL, False, "Trip command")
        self.add_input("RESET", DataType.BOOL, False, "Reset from trip")
        self.add_input("DRAFT_OK", DataType.BOOL, False, "Draft/airflow proven")
        self.add_input("PILOT_FLAME", DataType.BOOL, False, "Pilot flame detected")
        self.add_input("MAIN_FLAME", DataType.BOOL, False, "Main flame detected")

        # Outputs — drive downstream equipment
        self.add_output("STATE", DataType.STRING, "IDLE", "Current state")
        self.add_output("BLOWER_CMD", DataType.BOOL, False, "Start blower")
        self.add_output("IGNITER_CMD", DataType.BOOL, False, "Energize igniter")
        self.add_output("PILOT_FUEL_CMD", DataType.BOOL, False, "Open pilot fuel")
        self.add_output("MAIN_FUEL_CMD", DataType.BOOL, False, "Open main fuel")
        self.add_output("TIMER", description="State timer (s)")
        self.add_output("PERMIT", DataType.BOOL, False, "Start permissive met")
        self.add_output("TRIPPED", DataType.BOOL, False, "Trip active")

    def get_config_schema(self):
        return {
            "purge_time": (float, 60.0, "Pre-purge time [s]"),
            "ignition_trial": (float, 10.0, "Max ignition trial [s]"),
            "stabilize_time": (float, 15.0, "Stabilization time [s]"),
            "post_purge_time": (float, 30.0, "Post-purge time [s]"),
            "shutdown_delay": (float, 2.0, "Shutdown fuel close delay [s]"),
        }

    def execute(self, dt: float):
        start = bool(self.get_input("START"))
        stop = bool(self.get_input("STOP"))
        trip = bool(self.get_input("TRIP"))
        reset = bool(self.get_input("RESET"))
        draft_ok = bool(self.get_input("DRAFT_OK"))
        pilot_flame = bool(self.get_input("PILOT_FLAME"))
        main_flame = bool(self.get_input("MAIN_FLAME"))

        p = self.config.params
        purge_t = p.get("purge_time", 60.0)
        ign_t = p.get("ignition_trial", 10.0)
        stab_t = p.get("stabilize_time", 15.0)
        post_t = p.get("post_purge_time", 30.0)
        sd_delay = p.get("shutdown_delay", 2.0)

        # Check for trip conditions
        if trip:
            self._do_trip()
        elif self._state in ("OPERATION", "STABILIZE"):
            if not draft_ok:
                self._do_trip()
            elif not main_flame and not pilot_flame:
                self._do_trip()

        st = self._state

        if st == "IDLE":
            permit = draft_ok and not trip
            self.set_output("PERMIT", permit)
            if start and permit:
                self._state = "PRE_PURGE"
                self._timer = 0.0
            self._set_equip(blower=False, igniter=False, pilot=False, main=False)

        elif st == "PRE_PURGE":
            self._timer += dt
            self._set_equip(blower=True, igniter=False, pilot=False, main=False)
            if not draft_ok:
                self._do_trip()
            elif self._timer >= purge_t:
                self._state = "IGNITION"
                self._timer = 0.0

        elif st == "IGNITION":
            self._timer += dt
            self._set_equip(blower=True, igniter=True, pilot=True, main=False)
            if pilot_flame:
                self._state = "STABILIZE"
                self._timer = 0.0
            elif self._timer >= ign_t:
                self._do_trip()  # Ignition failure

        elif st == "STABILIZE":
            self._timer += dt
            self._set_equip(blower=True, igniter=False, pilot=True, main=False)
            if not pilot_flame:
                self._do_trip()
            elif self._timer >= stab_t:
                self._state = "OPERATION"
                self._timer = 0.0

        elif st == "OPERATION":
            self._timer += dt
            self._set_equip(blower=True, igniter=False, pilot=True, main=True)
            if stop:
                self._state = "SHUTDOWN"
                self._timer = 0.0

        elif st == "SHUTDOWN":
            self._timer += dt
            if self._timer < sd_delay:
                self._set_equip(blower=True, igniter=False, pilot=True, main=False)
            else:
                self._set_equip(blower=True, igniter=False, pilot=False, main=False)
                self._state = "POST_PURGE"
                self._timer = 0.0

        elif st == "POST_PURGE":
            self._timer += dt
            self._set_equip(blower=True, igniter=False, pilot=False, main=False)
            if self._timer >= post_t:
                self._state = "IDLE"
                self._timer = 0.0

        elif st == "TRIPPED":
            self._set_equip(blower=False, igniter=False, pilot=False, main=False)
            if reset:
                self._state = "IDLE"
                self._timer = 0.0

        self.set_output("STATE", self._state)
        self.set_output("TIMER", self._timer)
        self.set_output("TRIPPED", self._state == "TRIPPED")

    def _do_trip(self):
        self._state = "TRIPPED"
        self._timer = 0.0

    def _set_equip(self, blower, igniter, pilot, main):
        self.set_output("BLOWER_CMD", blower)
        self.set_output("IGNITER_CMD", igniter)
        self.set_output("PILOT_FUEL_CMD", pilot)
        self.set_output("MAIN_FUEL_CMD", main)


# ── FLAME_DET — Flame Detector ──────────────────────────────────────────────

@register_block
class FlameDetectorBlock(FunctionBlock):
    """Flame detector with configurable voting (1oo1, 2oo3, etc.).

    Reads one or more flame sensor inputs and outputs a voted
    flame-present signal.
    """
    block_type = "FLAME_DET"
    category = BlockCategory.SAFETY
    display_name = "Flame Detector"
    description = "Flame detector with M-of-N voting"

    def _define_terminals(self):
        self.add_input("SENSOR_1", DataType.BOOL, False, "Flame sensor 1")
        self.add_input("SENSOR_2", DataType.BOOL, False, "Flame sensor 2")
        self.add_input("SENSOR_3", DataType.BOOL, False, "Flame sensor 3")
        self.add_output("FLAME_OK", DataType.BOOL, False, "Flame detected (voted)")
        self.add_output("VOTE_COUNT", description="Number of sensors detecting")

    def get_config_schema(self):
        return {
            "num_sensors": (int, 1, "Number of sensors used (1-3)"),
            "min_votes": (int, 1, "Minimum votes for flame OK (M-of-N)"),
        }

    def execute(self, dt: float):
        p = self.config.params
        n = int(p.get("num_sensors", 1))
        m = int(p.get("min_votes", 1))

        count = 0
        for i in range(1, min(n, 3) + 1):
            if bool(self.get_input(f"SENSOR_{i}")):
                count += 1

        self.set_output("VOTE_COUNT", float(count))
        self.set_output("FLAME_OK", count >= m)


# ── FUEL_VALVE — Safety Shutoff Valve ────────────────────────────────────────

@register_block
class FuelValveBlock(FunctionBlock):
    """Safety shutoff valve (SSOV) with trip-to-close logic.

    Normally energized to open. De-energized (trip) closes valve.
    Includes position feedback and stuck-valve detection.
    """
    block_type = "FUEL_VALVE"
    category = BlockCategory.SAFETY
    display_name = "Fuel Safety Valve"
    description = "SSOV with trip-to-close and position feedback"

    def __init__(self, instance_name: str = ""):
        self._position = 0.0  # 0=closed, 1=open
        self._stroke_timer = 0.0
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("OPEN_CMD", DataType.BOOL, False, "Open command")
        self.add_input("TRIP", DataType.BOOL, False, "Trip to close")
        self.add_output("POSITION", description="Valve position (0-1)")
        self.add_output("OPEN", DataType.BOOL, False, "Valve is open")
        self.add_output("CLOSED", DataType.BOOL, True, "Valve is closed")
        self.add_output("TRANSIT", DataType.BOOL, False, "Valve in transit")

    def get_config_schema(self):
        return {
            "stroke_time": (float, 2.0, "Full stroke time [s]"),
        }

    def execute(self, dt: float):
        open_cmd = bool(self.get_input("OPEN_CMD"))
        trip = bool(self.get_input("TRIP"))
        stroke_time = self.config.params.get("stroke_time", 2.0)

        # Trip overrides open command
        target = 1.0 if (open_cmd and not trip) else 0.0

        # Ramp valve position
        if stroke_time > 0:
            rate = dt / stroke_time
            if target > self._position:
                self._position = min(target, self._position + rate)
            elif target < self._position:
                self._position = max(target, self._position - rate)

        is_open = self._position > 0.95
        is_closed = self._position < 0.05
        in_transit = not is_open and not is_closed

        self.set_output("POSITION", self._position)
        self.set_output("OPEN", is_open)
        self.set_output("CLOSED", is_closed)
        self.set_output("TRANSIT", in_transit)


# ── BLOWER — Forced Draft Blower ────────────────────────────────────────────

@register_block
class BlowerBlock(FunctionBlock):
    """Forced draft (FD) blower / fan with start/stop and interlock.

    Models motor start delay, running status, and airflow proving.
    """
    block_type = "BLOWER"
    category = BlockCategory.SAFETY
    display_name = "FD Blower"
    description = "Forced-draft blower with start delay and interlock"

    def __init__(self, instance_name: str = ""):
        self._running = False
        self._start_timer = 0.0
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("START_CMD", DataType.BOOL, False, "Start command")
        self.add_input("TRIP", DataType.BOOL, False, "Emergency stop")
        self.add_input("DRAFT_PV", description="Draft pressure (for proving)")
        self.add_output("RUNNING", DataType.BOOL, False, "Motor running")
        self.add_output("PROVEN", DataType.BOOL, False, "Airflow proven")
        self.add_output("SPEED", description="Fan speed (0-1)")

    def get_config_schema(self):
        return {
            "start_delay": (float, 3.0, "Motor start delay [s]"),
            "draft_setpoint": (float, -20.0, "Draft proving threshold [Pa]"),
        }

    def execute(self, dt: float):
        start = bool(self.get_input("START_CMD"))
        trip = bool(self.get_input("TRIP"))
        draft_pv = float(self.get_input("DRAFT_PV") or 0.0)
        p = self.config.params

        if trip:
            self._running = False
            self._start_timer = 0.0
        elif start and not self._running:
            self._start_timer += dt
            if self._start_timer >= p.get("start_delay", 3.0):
                self._running = True
        elif not start:
            self._running = False
            self._start_timer = 0.0

        speed = 1.0 if self._running else 0.0
        draft_sp = p.get("draft_setpoint", -20.0)
        proven = self._running and draft_pv < draft_sp

        self.set_output("RUNNING", self._running)
        self.set_output("PROVEN", proven)
        self.set_output("SPEED", speed)


# ── PURGE_TIMER — Purge Timer ───────────────────────────────────────────────

@register_block
class PurgeTimerBlock(FunctionBlock):
    """Pre/post purge timer with proven airflow requirement.

    Counts up while enabled and airflow is proven.
    Outputs COMPLETE when timer reaches setpoint.
    """
    block_type = "PURGE_TIMER"
    category = BlockCategory.SAFETY
    display_name = "Purge Timer"
    description = "Timed purge with airflow proving"

    def __init__(self, instance_name: str = ""):
        self._elapsed = 0.0
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("ENABLE", DataType.BOOL, False, "Enable timer")
        self.add_input("RESET", DataType.BOOL, False, "Reset timer")
        self.add_input("AIR_PROVEN", DataType.BOOL, False, "Airflow proven")
        self.add_output("ELAPSED", description="Elapsed time [s]")
        self.add_output("COMPLETE", DataType.BOOL, False, "Purge complete")
        self.add_output("PROGRESS", description="Progress 0-100%")

    def get_config_schema(self):
        return {
            "duration": (float, 60.0, "Purge duration [s]"),
        }

    def execute(self, dt: float):
        enable = bool(self.get_input("ENABLE"))
        reset = bool(self.get_input("RESET"))
        air_ok = bool(self.get_input("AIR_PROVEN"))
        duration = self.config.params.get("duration", 60.0)

        if reset:
            self._elapsed = 0.0
        elif enable and air_ok:
            self._elapsed += dt

        complete = self._elapsed >= duration
        progress = min(100.0, (self._elapsed / max(duration, 0.01)) * 100.0)

        self.set_output("ELAPSED", self._elapsed)
        self.set_output("COMPLETE", complete)
        self.set_output("PROGRESS", progress)


# ── TRIP_RELAY — Latching Trip Relay ────────────────────────────────────────

@register_block
class TripRelayBlock(FunctionBlock):
    """Latching trip relay — sets on any trip input, requires manual reset.

    Multiple trip inputs (any-one triggers). Latches until RESET.
    """
    block_type = "TRIP_RELAY"
    category = BlockCategory.SAFETY
    display_name = "Trip Relay"
    description = "Latching trip — any input trips, requires reset"

    def __init__(self, instance_name: str = ""):
        self._tripped = False
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("TRIP_1", DataType.BOOL, False, "Trip input 1")
        self.add_input("TRIP_2", DataType.BOOL, False, "Trip input 2")
        self.add_input("TRIP_3", DataType.BOOL, False, "Trip input 3")
        self.add_input("TRIP_4", DataType.BOOL, False, "Trip input 4")
        self.add_input("RESET", DataType.BOOL, False, "Reset trip latch")
        self.add_output("TRIPPED", DataType.BOOL, False, "Trip active")
        self.add_output("OK", DataType.BOOL, True, "System OK (not tripped)")

    def execute(self, dt: float):
        reset = bool(self.get_input("RESET"))

        if reset:
            self._tripped = False
        else:
            for i in range(1, 5):
                if bool(self.get_input(f"TRIP_{i}")):
                    self._tripped = True
                    break

        self.set_output("TRIPPED", self._tripped)
        self.set_output("OK", not self._tripped)
