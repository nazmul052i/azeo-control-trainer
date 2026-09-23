"""APC engagement blocks — industrial APC-to-DCS communication.

Implements the standard APC engagement pattern used in modern DCS/APC
systems (Honeywell RMPCT, AspenTech DMC3, Yokogawa APC):

  APC_CONTROL  — supervisory optimizer stub (heartbeat, MV targets)
  WATCHDOG     — communication health monitor (timeout detection)
  SHED_LOGIC   — engagement coordinator (graceful shed levels)
  SP_HANDOFF   — bumpless SP transition (ramp engage/disengage)
  MV_CLAMP     — MV limit enforcement with status feedback

SP_HANDOFF and MV_CLAMP use a ``controller_tag`` config parameter to
identify which PID loop they manage.  The bridge reads the PID's current
SP into DCS_SP and writes TARGET_SP back, so users just configure the
tag name (e.g. "TIC109") in the properties panel.
"""
from __future__ import annotations

from ..model.block_base import FunctionBlock, BlockCategory, DataType
from ..model.block_registry import register_block
from ..model.terminal import LimitStatus


# ═══════════════════════════════════════════════════════════════════════════
# APC_CONTROL — supervisory controller interface
# ═══════════════════════════════════════════════════════════════════════════

@register_block
class APCControlBlock(FunctionBlock):
    """APC supervisory controller interface block.

    Serves as the DCS-side stub for an APC optimizer.  Generates a
    heartbeat for the WATCHDOG, passes through MV setpoint targets,
    and tracks initialization / active / off status.

    In a real system the optimizer computes optimal MV targets.  Here
    the MV_SP outputs simply follow the corresponding CV inputs (or
    a configurable bias/gain) so the strategy can be tested end-to-end
    without an external optimizer.
    """
    block_type = "APC_CONTROL"
    category = BlockCategory.APC
    display_name = "APC Control"
    description = "APC supervisory controller — heartbeat, MV targets, status"

    # Status codes
    OFF, INITIALIZING, ACTIVE, SHEDDING = 0, 1, 2, 3

    def _define_terminals(self):
        self.add_input("ENABLE", DataType.BOOL, False, "Operator enable")
        for i in range(1, 7):
            self.add_input(f"CV_{i}", DataType.FLOAT, 0.0, f"Controlled variable {i}")
            self.add_input(f"MV_FB_{i}", DataType.FLOAT, 0.0, f"MV feedback {i}")

        self.add_output("ACTIVE", DataType.BOOL, False, "APC is active")
        self.add_output("HEARTBEAT", DataType.BOOL, False, "Heartbeat toggle")
        self.add_output("STATUS", DataType.INT, 0, "0=OFF 1=INIT 2=ACTIVE 3=SHED")
        self.add_output("CYCLE_TIME", DataType.FLOAT, 0.0, "Last cycle time (s)")
        for i in range(1, 7):
            self.add_output(f"MV_SP_{i}", DataType.FLOAT, 0.0, f"MV setpoint target {i}")

        # Internal state
        self._hb_state = False
        self._init_timer = 0.0
        self._cycle_timer = 0.0
        self._state = self.OFF
        self._was_enabled = False

    def get_config_schema(self):
        return {
            "init_time": (float, 10.0, "Initialization time before going ACTIVE (s)"),
            "cycle_period": (float, 60.0, "Optimizer cycle period (s)"),
        }

    def execute(self, dt: float):
        enable = bool(self.get_input("ENABLE"))
        init_time = float(self.config.params.get("init_time", 10.0))
        cycle_period = float(self.config.params.get("cycle_period", 60.0))

        if not enable:
            # OFF — track MV feedbacks for bumpless engage
            self._state = self.OFF
            self._init_timer = 0.0
            self._cycle_timer = 0.0
            for i in range(1, 7):
                self.set_output(f"MV_SP_{i}", self.get_input(f"MV_FB_{i}"))
            self.set_output("ACTIVE", False)
            self.set_output("HEARTBEAT", False)
            self.set_output("STATUS", self.OFF)
            self._was_enabled = False
            return

        # Rising edge — start initialization
        if not self._was_enabled:
            self._state = self.INITIALIZING
            self._init_timer = 0.0
            self._cycle_timer = 0.0
            # Capture current MV positions for bumpless start
            for i in range(1, 7):
                self.set_output(f"MV_SP_{i}", self.get_input(f"MV_FB_{i}"))
        self._was_enabled = True

        if self._state == self.INITIALIZING:
            self._init_timer += dt
            # Track MV feedbacks during init
            for i in range(1, 7):
                self.set_output(f"MV_SP_{i}", self.get_input(f"MV_FB_{i}"))
            if self._init_timer >= init_time:
                self._state = self.ACTIVE

        if self._state == self.ACTIVE:
            self._cycle_timer += dt
            if self._cycle_timer >= cycle_period:
                self._cycle_timer = 0.0
                # Toggle heartbeat each cycle
                self._hb_state = not self._hb_state

        self.set_output("ACTIVE", self._state == self.ACTIVE)
        self.set_output("HEARTBEAT", self._hb_state)
        self.set_output("STATUS", self._state)
        self.set_output("CYCLE_TIME", self._cycle_timer)


# ═══════════════════════════════════════════════════════════════════════════
# WATCHDOG — communication health monitor
# ═══════════════════════════════════════════════════════════════════════════

@register_block
class WatchdogBlock(FunctionBlock):
    """APC watchdog — monitors heartbeat and detects communication failure.

    On each rising edge of HEARTBEAT, the internal timer resets.
    If the timer exceeds TIMEOUT seconds, COMM_FAIL goes True and
    HEALTHY goes False, triggering a shed in downstream SHED_LOGIC.
    """
    block_type = "WATCHDOG"
    category = BlockCategory.APC
    display_name = "Watchdog"
    description = "APC communication health monitor"

    def _define_terminals(self):
        self.add_input("HEARTBEAT", DataType.BOOL, False, "Heartbeat from APC")
        self.add_input("TIMEOUT", DataType.FLOAT, 30.0, "Timeout (s)")
        self.add_input("ENABLE", DataType.BOOL, True, "Master enable")

        self.add_output("HEALTHY", DataType.BOOL, False, "Communication OK")
        self.add_output("COMM_FAIL", DataType.BOOL, True, "Communication failure")
        self.add_output("TIME_SINCE_HB", DataType.FLOAT, 0.0, "Seconds since last HB")

        # Internal state
        self._prev_hb = False
        self._timer = 0.0

    def get_config_schema(self):
        return {
            "timeout": (float, 30.0, "Heartbeat timeout before COMM_FAIL (s)"),
        }

    def execute(self, dt: float):
        enable = bool(self.get_input("ENABLE"))
        hb = bool(self.get_input("HEARTBEAT"))
        timeout = float(self.get_input("TIMEOUT") or
                        self.config.params.get("timeout", 30.0))

        if not enable:
            self.set_output("HEALTHY", False)
            self.set_output("COMM_FAIL", True)
            self.set_output("TIME_SINCE_HB", self._timer)
            return

        # Detect rising edge of heartbeat
        if hb and not self._prev_hb:
            self._timer = 0.0
        self._prev_hb = hb

        self._timer += dt
        healthy = self._timer < timeout

        self.set_output("HEALTHY", healthy)
        self.set_output("COMM_FAIL", not healthy)
        self.set_output("TIME_SINCE_HB", self._timer)


# ═══════════════════════════════════════════════════════════════════════════
# SHED_LOGIC — engagement / shedding coordinator
# ═══════════════════════════════════════════════════════════════════════════

@register_block
class ShedLogicBlock(FunctionBlock):
    """APC shed logic — coordinates engagement and graceful degradation.

    Shed levels:
      0 = NORMAL  — all MVs active, APC fully engaged
      1 = PARTIAL — some MVs shed, remaining MVs still under APC
      2 = FULL    — all MVs shed, APC disengaged

    When WATCHDOG_OK is False or ENABLE is False, immediately goes to
    FULL shed.  Otherwise, counts how many MV_STATUS inputs are False;
    if over the partial_threshold config, goes to FULL.
    """
    block_type = "SHED_LOGIC"
    category = BlockCategory.APC
    display_name = "Shed Logic"
    description = "APC engagement coordinator — NORMAL/PARTIAL/FULL shed"

    NORMAL, PARTIAL, FULL = 0, 1, 2

    def _define_terminals(self):
        self.add_input("ENABLE", DataType.BOOL, True, "Master enable")
        self.add_input("WATCHDOG_OK", DataType.BOOL, False, "Watchdog healthy")
        for i in range(1, 7):
            self.add_input(f"MV_STATUS_{i}", DataType.BOOL, True, f"MV {i} OK")

        self.add_output("ACTIVE", DataType.BOOL, False, "APC fully active")
        self.add_output("SHED_LEVEL", DataType.INT, 2, "0=NORMAL 1=PARTIAL 2=FULL")
        for i in range(1, 7):
            self.add_output(f"SHED_CMD_{i}", DataType.BOOL, True, f"Shed MV {i}")
        self.add_output("MV_COUNT_OK", DataType.INT, 0, "Healthy MV count")

    def get_config_schema(self):
        return {
            "partial_threshold": (int, 2, "Failed MV count to go from PARTIAL to FULL"),
        }

    def execute(self, dt: float):
        enable = bool(self.get_input("ENABLE"))
        wd_ok = bool(self.get_input("WATCHDOG_OK"))
        partial_threshold = int(self.config.params.get("partial_threshold", 2))

        if not enable or not wd_ok:
            # Full shed
            self.set_output("ACTIVE", False)
            self.set_output("SHED_LEVEL", self.FULL)
            for i in range(1, 7):
                self.set_output(f"SHED_CMD_{i}", True)
            self.set_output("MV_COUNT_OK", 0)
            return

        # Count healthy MVs
        failed = []
        ok_count = 0
        for i in range(1, 7):
            if bool(self.get_input(f"MV_STATUS_{i}")):
                ok_count += 1
            else:
                failed.append(i)

        fail_count = len(failed)
        self.set_output("MV_COUNT_OK", ok_count)

        if fail_count == 0:
            level = self.NORMAL
        elif fail_count < partial_threshold:
            level = self.PARTIAL
        else:
            level = self.FULL

        self.set_output("SHED_LEVEL", level)
        self.set_output("ACTIVE", level != self.FULL)

        # Per-MV shed commands
        for i in range(1, 7):
            if level == self.FULL:
                self.set_output(f"SHED_CMD_{i}", True)
            elif i in failed:
                self.set_output(f"SHED_CMD_{i}", True)
            else:
                self.set_output(f"SHED_CMD_{i}", False)


# ═══════════════════════════════════════════════════════════════════════════
# SP_HANDOFF — bumpless setpoint transition
# ═══════════════════════════════════════════════════════════════════════════

@register_block
class SPHandoffBlock(FunctionBlock):
    """APC SP handoff — bumpless engage/disengage of APC setpoints.

    Configure ``controller_tag`` (e.g. "TIC109") to bind this block to a
    PID loop.  The bridge will automatically:
      - Read ctrl.<tag>.SP_WRK into the DCS_SP input each scan
      - Write TARGET_SP to the configured SP/CAS_IN/RCAS_IN writeback

    On engage (ACTIVE rising edge): captures DCS_SP, then ramps
    TARGET_SP toward APC_SP at the configured ramp rate.

    On disengage (ACTIVE falling edge): holds TARGET_SP at last value
    (if hold_on_shed=True) or snaps back to DCS_SP.

    RAMP_RATE of 0 means instant transition.
    """
    block_type = "SP_HANDOFF"
    category = BlockCategory.APC
    display_name = "SP Handoff"
    description = "Bumpless APC SP engage/disengage with ramp"

    def _define_terminals(self):
        self.add_input("APC_SP", DataType.FLOAT, 0.0, "APC-computed setpoint")
        self.add_input("DCS_SP", DataType.FLOAT, 0.0, "Current PID setpoint (auto from controller_tag)")
        self.add_input("ACTIVE", DataType.BOOL, False, "APC active for this MV")
        self.add_input("RAMP_RATE", DataType.FLOAT, 0.0, "Ramp rate (EU/s), 0=use config")

        self.add_output("TARGET_SP", DataType.FLOAT, 0.0, "SP to send to PID")
        self.add_output("TRACKING", DataType.BOOL, False, "Ramping in progress")
        self.add_output("AT_TARGET", DataType.BOOL, True, "At APC target")

        # Internal state
        self._current_sp = 0.0
        self._was_active = False
        self._initialized = False
        self._controller_mode = ""
        self._effective_active = False
        self._ownership_requested = False
        self._invalidate_target = False

    def get_config_schema(self):
        return {
            "controller_tag": (str, "", "PID controller tag (e.g. TIC109) — auto reads SP, writes TARGET_SP"),
            "ramp_rate": (float, 1.0, "SP ramp rate (EU/s), 0=instant"),
            "hold_on_shed": (bool, True, "Hold last SP on disengage (vs snap to DCS_SP)"),
            "target_parameter": (
                str,
                "SP",
                "PID write target: SP (operator/APC), CAS_IN, or RCAS_IN",
            ),
            "required_modes": (
                str,
                "",
                "Comma-separated downstream PID modes that permit ownership",
            ),
        }

    def set_controller_mode(self, mode: str) -> None:
        """Supply the downstream PID's published actual mode.

        An empty ``required_modes`` setting accepts ACTIVE exactly as before.
        A configured gate prevents the target from ramping invisibly while
        the downstream loop is in a mode that cannot accept the transfer.
        """
        self._controller_mode = str(mode or "").strip().upper()

    @property
    def effective_active(self) -> bool:
        """Whether this handoff currently owns the downstream setpoint."""
        return self._effective_active

    @property
    def ownership_requested(self) -> bool:
        """Whether the operator request and downstream mode permit ownership.

        This remains true when a typed source becomes Bad so the bridge can
        invalidate an already-published remote demand.  Treating that case as
        simply inactive left the previous Good CAS_IN resident in the shared
        store and the slave continued controlling a stale target.
        """
        return self._ownership_requested

    @property
    def invalidate_target(self) -> bool:
        """Whether the bridge must revoke a resident remote demand."""
        return self._invalidate_target

    def reset(self) -> None:
        """Return the ownership state to a safe pre-scan condition."""
        super().reset()
        self._current_sp = 0.0
        self._was_active = False
        self._initialized = False
        self._controller_mode = ""
        self._effective_active = False
        self._ownership_requested = False
        self._invalidate_target = False

    def execute(self, dt: float):
        requested_active = bool(self.get_input("ACTIVE"))
        required_modes = {
            token.strip().upper()
            for token in str(
                self.config.params.get("required_modes", "")
            ).split(",")
            if token.strip()
        }
        mode_permitted = (
            not required_modes or self._controller_mode in required_modes
        )
        self._ownership_requested = requested_active and mode_permitted
        # A typed supervisory link is useful only if ownership follows its
        # status.  Previously REMOTE_ANALOG could correctly publish Bad while
        # this block continued ramping and queueing the last numeric value.
        # Shed the handoff on either an unusable target or an unavailable DCS
        # tracking value; the last TARGET_SP is retained but never written.
        apc_usable = self.input_status("APC_SP").is_usable
        dcs_usable = self.input_status("DCS_SP").is_usable
        was_active = self._was_active
        active = (
            self._ownership_requested and apc_usable and dcs_usable
        )
        self._invalidate_target = (
            (self._ownership_requested and not active)
            or (was_active and not active)
        )
        self._effective_active = active
        apc_sp = float(self.get_input("APC_SP"))
        dcs_sp = float(self.get_input("DCS_SP"))
        ramp_rate = float(self.get_input("RAMP_RATE") or
                          self.config.params.get("ramp_rate", 1.0))
        hold_on_shed = bool(self.config.params.get("hold_on_shed", True))

        if not self._initialized:
            self._current_sp = dcs_sp
            self._initialized = True

        # Rising edge — capture DCS SP for bumpless start
        if active and not self._was_active:
            self._current_sp = dcs_sp

        # Falling edge — disengage
        if not active and was_active:
            if not hold_on_shed:
                self._current_sp = dcs_sp

        self._was_active = active

        if active:
            # Ramp toward APC SP
            target = apc_sp
            if ramp_rate > 0 and dt > 0:
                diff = target - self._current_sp
                max_step = ramp_rate * dt
                if abs(diff) > max_step:
                    self._current_sp += max_step if diff > 0 else -max_step
                else:
                    self._current_sp = target
            else:
                self._current_sp = target

            at_target = abs(self._current_sp - apc_sp) < 1e-6
            self.set_output("TRACKING", not at_target)
            self.set_output("AT_TARGET", at_target)
        else:
            # Not active — track DCS SP (so next engage is bumpless)
            if not hold_on_shed:
                self._current_sp = dcs_sp
            self.set_output("TRACKING", False)
            self.set_output("AT_TARGET", True)

        self.set_output("TARGET_SP", self._current_sp)
        target_quality = (
            max(
                self.input_status("APC_SP"),
                self.input_status("DCS_SP"),
                key=lambda quality: quality.value,
            )
            if requested_active else self.input_status("DCS_SP")
        )
        self.set_output_status(
            "TARGET_SP", target_quality, LimitStatus.NOT_LIMITED
        )


# ═══════════════════════════════════════════════════════════════════════════
# MV_CLAMP — manipulated variable limit enforcement
# ═══════════════════════════════════════════════════════════════════════════

@register_block
class MVClampBlock(FunctionBlock):
    """APC MV clamp — enforces dynamic MV limits with status feedback.

    Configure ``controller_tag`` (e.g. "TIC109") to bind this block to a
    PID loop.  The bridge will automatically:
      - Read ctrl.<tag>.OUT into the MV_READBACK input each scan

    Clamps MV_IN to [LO_LIM, HI_LIM] when ACTIVE.  Reports AT_HI,
    AT_LO, CLAMPED status.  MV_OK output indicates whether the actual
    MV_READBACK is within limits (feeds back to SHED_LOGIC.MV_STATUS).
    """
    block_type = "MV_CLAMP"
    category = BlockCategory.APC
    display_name = "MV Clamp"
    description = "MV limit enforcement with status feedback"

    def _define_terminals(self):
        self.add_input("MV_IN", DataType.FLOAT, 0.0, "MV value from APC/DCS")
        self.add_input("HI_LIM", DataType.FLOAT, 100.0, "High limit")
        self.add_input("LO_LIM", DataType.FLOAT, 0.0, "Low limit")
        self.add_input("MV_READBACK", DataType.FLOAT, 0.0, "Actual MV position (auto from controller_tag)")
        self.add_input("ACTIVE", DataType.BOOL, True, "Clamp enforcement active")

        self.add_output("MV_OUT", DataType.FLOAT, 0.0, "Clamped MV value")
        self.add_output("AT_HI", DataType.BOOL, False, "At high limit")
        self.add_output("AT_LO", DataType.BOOL, False, "At low limit")
        self.add_output("CLAMPED", DataType.BOOL, False, "MV is being clamped")
        self.add_output("MV_OK", DataType.BOOL, True, "MV within limits")

    def get_config_schema(self):
        return {
            "controller_tag": (str, "", "PID controller tag (e.g. TIC109) — auto reads OUT for readback"),
            "hi_default": (float, 100.0, "Default high limit when HI_LIM not wired"),
            "lo_default": (float, 0.0, "Default low limit when LO_LIM not wired"),
            "deadband": (float, 0.5, "Limit deadband for MV_OK status"),
        }

    def execute(self, dt: float):
        mv_in = float(self.get_input("MV_IN"))
        hi = float(self.get_input("HI_LIM") or
                   self.config.params.get("hi_default", 100.0))
        lo = float(self.get_input("LO_LIM") or
                   self.config.params.get("lo_default", 0.0))
        readback = float(self.get_input("MV_READBACK"))
        active = bool(self.get_input("ACTIVE"))
        deadband = float(self.config.params.get("deadband", 0.5))

        if active:
            clamped = max(lo, min(hi, mv_in))
            at_hi = mv_in >= hi
            at_lo = mv_in <= lo
        else:
            clamped = mv_in
            at_hi = False
            at_lo = False

        mv_ok = (lo - deadband) <= readback <= (hi + deadband)

        self.set_output("MV_OUT", clamped)
        self.set_output("AT_HI", at_hi)
        self.set_output("AT_LO", at_lo)
        self.set_output("CLAMPED", at_hi or at_lo)
        self.set_output("MV_OK", mv_ok)
