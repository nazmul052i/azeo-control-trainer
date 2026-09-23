"""EXT_DMC_BRIDGE — bridge between an external DMC system and PID controllers.

Provides bumpless, validated handover from operator setpoints to external DMC
setpoints, with watchdog protection that falls back to internal control if
the external DMC stops communicating.

Inputs:
  ENABLE          - bool: operator enable for external DMC takeover
  EXT_SP          - float: setpoint from external system (read from store)
  CURRENT_SP      - float: current PID setpoint (for bumpless handover)
  HEARTBEAT_IN    - bool: heartbeat from external DMC
  MV_LIMIT_LO     - float: lower limit for SP
  MV_LIMIT_HI     - float: upper limit for SP
  CV_PV           - float: current process variable (for safety check)

Outputs:
  SP_OUT          - float: setpoint to write to PID RCAS_IN
  ENGAGED         - bool: True when external DMC is currently controlling
  WATCHDOG_OK     - bool: True when external heartbeat is fresh
  STATE           - str: state name for HMI display
  REJECT_COUNT    - int: how many SPs were rejected (out of limits)
  TIME_SINCE_HB   - float: seconds since last heartbeat

Configuration:
  ramp_rate       - max SP change per second on engage/disengage (EU/s)
  watchdog_timeout - seconds without heartbeat before fall back (default 10)
  validate_pv     - if True, reject SPs more than N % from PV (safety)
  pv_dev_max_pct  - max allowed |SP-PV| as percent of MV span
"""
from __future__ import annotations

import logging
import time

from ..model.block_base import FunctionBlock, BlockCategory, DataType
from ..model.block_registry import register_block

log = logging.getLogger("strategy.ext_dmc")


@register_block
class EXTDMCBridgeBlock(FunctionBlock):
    """Bridge external DMC setpoints to a PID's RCAS_IN with watchdog + bumpless ramp."""

    block_type = "EXT_DMC_BRIDGE"
    category = BlockCategory.APC
    display_name = "External DMC Bridge"
    description = (
        "Routes setpoints from external DMC (via OPC UA) into PID RCAS_IN. "
        "Implements bumpless handover, MV limit validation, "
        "watchdog timeout fallback, and PV deviation safety check."
    )

    # State machine
    OFF              = 0   # disabled, SP_OUT tracks CURRENT_SP
    ENGAGING         = 1   # ramping from CURRENT_SP toward EXT_SP
    ENGAGED          = 2   # external DMC is in control
    WATCHDOG_FAIL    = 3   # external DMC heartbeat lost, holding SP
    DISENGAGING      = 4   # ramping back to CURRENT_SP

    _STATE_NAMES = {
        0: "OFF", 1: "ENGAGING", 2: "ENGAGED",
        3: "WATCHDOG_FAIL", 4: "DISENGAGING",
    }

    def _define_terminals(self):
        # Inputs
        self.add_input("ENABLE", DataType.BOOL, False,
                       "Operator enable for external DMC takeover")
        self.add_input("EXT_SP", DataType.FLOAT, 0.0,
                       "Setpoint from external DMC system")
        self.add_input("CURRENT_SP", DataType.FLOAT, 0.0,
                       "Current PID SP (used for bumpless handover)")
        self.add_input("HEARTBEAT_IN", DataType.BOOL, False,
                       "Heartbeat from external system")
        self.add_input("MV_LIMIT_LO", DataType.FLOAT, 0.0, "Lower SP limit")
        self.add_input("MV_LIMIT_HI", DataType.FLOAT, 100.0, "Upper SP limit")
        self.add_input("CV_PV", DataType.FLOAT, 0.0,
                       "Process variable (safety: reject SPs far from PV)")

        # Outputs
        self.add_output("SP_OUT", DataType.FLOAT, 0.0,
                        "Setpoint to PID RCAS_IN")
        self.add_output("ENGAGED", DataType.BOOL, False,
                        "True when external DMC is currently controlling")
        self.add_output("WATCHDOG_OK", DataType.BOOL, False,
                        "True when external heartbeat is fresh")
        self.add_output("STATE", DataType.STRING, "OFF",
                        "Current state name")
        self.add_output("REJECT_COUNT", DataType.INT, 0,
                        "Number of SPs rejected (out of limits)")
        self.add_output("TIME_SINCE_HB", DataType.FLOAT, 0.0,
                        "Seconds since last heartbeat")

        # Internal state
        self._state = self.OFF
        self._sp_current = 0.0       # current SP being output
        self._sp_target = 0.0        # target SP we're ramping toward
        self._reject_count = 0
        self._last_heartbeat_state = False
        self._last_heartbeat_time = 0.0
        self._was_enabled = False
        self._wall_time = 0.0        # accumulated time

    def get_config_schema(self) -> dict:
        return {
            "ramp_rate": (float, 1.0,
                          "Max SP change per second on engage/disengage (EU/s)"),
            "watchdog_timeout": (float, 10.0,
                                 "Seconds without heartbeat before fallback"),
            "validate_pv": (bool, True,
                            "Reject SPs too far from PV"),
            "pv_dev_max_pct": (float, 30.0,
                               "Max allowed |SP-PV| as % of MV span"),
            "auto_recover": (bool, True,
                             "Auto re-engage when heartbeat returns"),
        }

    def _transition(self, new_state: int):
        if new_state != self._state:
            log.info("EXT_DMC_BRIDGE [%s]: %s -> %s",
                     self.instance_name,
                     self._STATE_NAMES.get(self._state, "?"),
                     self._STATE_NAMES.get(new_state, "?"))
            self._state = new_state

    def _validate_sp(self, sp: float, pv: float, lo: float, hi: float) -> tuple[float, bool]:
        """Validate and clamp the external setpoint.

        Returns (clamped_sp, accepted).  If validate_pv is on and SP is too
        far from PV, returns the safe-clamped SP and accepted=False.
        """
        p = self.config.params
        # Clamp to MV limits
        clamped = max(lo, min(hi, sp))
        accepted = (abs(clamped - sp) < 1e-6)

        if p.get("validate_pv", True):
            mv_span = max(hi - lo, 1e-6)
            max_dev = mv_span * float(p.get("pv_dev_max_pct", 30.0)) / 100.0
            if abs(clamped - pv) > max_dev:
                # Reject — pull SP toward PV by max allowed amount
                if clamped > pv:
                    clamped = pv + max_dev
                else:
                    clamped = pv - max_dev
                clamped = max(lo, min(hi, clamped))
                accepted = False

        return clamped, accepted

    def execute(self, dt: float):
        p = self.config.params
        ramp_rate = float(p.get("ramp_rate", 1.0))
        watchdog_timeout = float(p.get("watchdog_timeout", 10.0))
        auto_recover = bool(p.get("auto_recover", True))

        self._wall_time += dt
        enable = bool(self.get_input("ENABLE"))
        ext_sp = float(self.get_input("EXT_SP"))
        current_sp = float(self.get_input("CURRENT_SP"))
        hb = bool(self.get_input("HEARTBEAT_IN"))
        mv_lo = float(self.get_input("MV_LIMIT_LO"))
        mv_hi = float(self.get_input("MV_LIMIT_HI"))
        cv_pv = float(self.get_input("CV_PV"))

        # Heartbeat detection (any change = alive)
        if hb != self._last_heartbeat_state:
            self._last_heartbeat_time = self._wall_time
            self._last_heartbeat_state = hb

        time_since_hb = self._wall_time - self._last_heartbeat_time
        watchdog_ok = time_since_hb < watchdog_timeout

        # ── State machine ──

        if self._state == self.OFF:
            # SP_OUT tracks current operator SP for bumpless future engage
            self._sp_current = current_sp
            if enable and not self._was_enabled:
                self._sp_target = current_sp  # start at current
                self._transition(self.ENGAGING)

        elif self._state == self.ENGAGING:
            if not enable:
                self._transition(self.DISENGAGING)
            elif not watchdog_ok:
                self._transition(self.WATCHDOG_FAIL)
            else:
                # Validate external SP
                sp, accepted = self._validate_sp(ext_sp, cv_pv, mv_lo, mv_hi)
                if not accepted:
                    self._reject_count += 1
                self._sp_target = sp
                # Ramp current toward target
                diff = self._sp_target - self._sp_current
                max_step = ramp_rate * dt
                if abs(diff) > max_step:
                    self._sp_current += max_step if diff > 0 else -max_step
                else:
                    self._sp_current = self._sp_target
                    self._transition(self.ENGAGED)

        elif self._state == self.ENGAGED:
            if not enable:
                self._transition(self.DISENGAGING)
            elif not watchdog_ok:
                log.warning("EXT_DMC_BRIDGE [%s]: watchdog timeout (%.1fs)",
                            self.instance_name, time_since_hb)
                self._transition(self.WATCHDOG_FAIL)
            else:
                # Track external SP with rate limit + validation
                sp, accepted = self._validate_sp(ext_sp, cv_pv, mv_lo, mv_hi)
                if not accepted:
                    self._reject_count += 1
                self._sp_target = sp
                diff = self._sp_target - self._sp_current
                max_step = ramp_rate * dt
                if abs(diff) > max_step:
                    self._sp_current += max_step if diff > 0 else -max_step
                else:
                    self._sp_current = self._sp_target

        elif self._state == self.WATCHDOG_FAIL:
            # Hold SP at last known value; auto-recover on heartbeat return
            if not enable:
                self._transition(self.DISENGAGING)
            elif watchdog_ok and auto_recover:
                log.info("EXT_DMC_BRIDGE [%s]: heartbeat recovered, re-engaging",
                         self.instance_name)
                self._transition(self.ENGAGING)

        elif self._state == self.DISENGAGING:
            # Ramp from current SP back to operator SP
            self._sp_target = current_sp
            diff = self._sp_target - self._sp_current
            max_step = ramp_rate * dt
            if abs(diff) > max_step:
                self._sp_current += max_step if diff > 0 else -max_step
            else:
                self._sp_current = self._sp_target
                self._transition(self.OFF)

        # ── Outputs ──
        self.set_output("SP_OUT", float(self._sp_current))
        self.set_output("ENGAGED", self._state == self.ENGAGED)
        self.set_output("WATCHDOG_OK", watchdog_ok)
        self.set_output("STATE", self._STATE_NAMES.get(self._state, "?"))
        self.set_output("REJECT_COUNT", self._reject_count)
        self.set_output("TIME_SINCE_HB", float(time_since_hb))

        self._was_enabled = enable
