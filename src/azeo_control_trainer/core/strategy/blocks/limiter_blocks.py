"""Limiter/constraining blocks — Limiter, Deadband, Rate Limiter."""
from __future__ import annotations
from ..model.block_base import FunctionBlock, BlockCategory, DataType
from ..model.block_registry import register_block
from .filter_blocks import TIME_UNIT_SECONDS, first_order_alpha


@register_block
class LimiterBlock(FunctionBlock):
    """Clamp an input between two references (Azeo LIM).

    ``HI``/``LO`` (aliases ``OUT_HI_LIM``/``OUT_LO_LIM``) bound OUT;
    ``HI_LIM``/``LO_LIM`` (mirrored as the Azeo-named ``OUT_HI_ACT`` /
    ``OUT_LO_ACT``) flag instantaneous limiting.

    ``LIM_INDICATOR`` is the spec's latched indicator: set True when IN
    reaches HI and held until IN falls to LO — the deadband start/stop
    signal for e.g. a level-driven drain pump.
    """
    block_type = "LIMITER"
    category = BlockCategory.SIGNAL
    display_name = "Limiter"
    description = "Clamps output between LO and HI"

    config_aliases = {"OUT_LO_LIM": "LO", "OUT_HI_LIM": "HI"}

    def __init__(self, instance_name=""):
        self._latched = False
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("IN", description="Input signal")
        self.add_output("OUT", description="Clamped output")
        self.add_output("HI_LIM", DataType.BOOL, False, "High limited")
        self.add_output("LO_LIM", DataType.BOOL, False, "Low limited")
        self.add_output("OUT_HI_ACT", DataType.BOOL, False,
                        "High limited (Azeo name for HI_LIM)")
        self.add_output("OUT_LO_ACT", DataType.BOOL, False,
                        "Low limited (Azeo name for LO_LIM)")
        self.add_output("LIM_INDICATOR", DataType.BOOL, False,
                        "Latched: set at HI, cleared at LO")
        self.add_output("CFG_ERR", DataType.BOOL, False,
                        "Configuration error (LO > HI)")

    def get_config_schema(self):
        return {
            "LO": (float, 0.0, "Low limit (Azeo OUT_LO_LIM)"),
            "HI": (float, 100.0, "High limit (Azeo OUT_HI_LIM)"),
        }

    def execute(self, dt: float):
        val = self.get_input("IN")
        lo = self.config.params.get("LO", 0.0)
        hi = self.config.params.get("HI", 100.0)
        clamped = max(lo, min(hi, val))
        hi_act = val >= hi
        lo_act = val <= lo

        # Latched indicator: set at the high limit, cleared at the low limit,
        # holds its previous value in between (spec §Limit).
        if hi_act:
            self._latched = True
        elif lo_act:
            self._latched = False

        self.set_output("OUT", clamped)
        self.set_output("HI_LIM", hi_act)
        self.set_output("LO_LIM", lo_act)
        self.set_output("OUT_HI_ACT", hi_act)
        self.set_output("OUT_LO_ACT", lo_act)
        self.set_output("LIM_INDICATOR", self._latched)
        self.set_output("CFG_ERR", lo > hi)

    def reset(self):
        super().reset()
        self._latched = False


@register_block
class DeadbandBlock(FunctionBlock):
    block_type = "DEADBAND"
    category = BlockCategory.SIGNAL
    display_name = "Deadband"
    description = "Ignores input changes within deadband"

    def __init__(self, instance_name=""):
        self._last_out = None
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("IN", description="Input signal")
        self.add_output("OUT", description="Deadbanded output")

    def get_config_schema(self):
        return {"DB": (float, 1.0, "Deadband width")}

    def execute(self, dt: float):
        val = self.get_input("IN")
        db = self.config.params.get("DB", 1.0)
        if self._last_out is None or abs(val - self._last_out) > db:
            self._last_out = val
        self.set_output("OUT", self._last_out)

    def reset(self):
        super().reset()
        self._last_out = None


@register_block
class RateLimiterBlock(FunctionBlock):
    """Limit the rate of change of a signal (Azeo RTLM).

    ``roc = (IN - OUT[t-1]) / dt`` is compared against ``RATE_UP`` /
    ``RATE_DN`` (aliases ``INCREASE_MAX`` / ``DECREASE_MAX``; Azeo's
    DECREASE_MAX is negative, RATE_DN is the positive magnitude — the alias
    negates on load). ``TIME_UNITS`` converts the configured rates from
    EU per unit to EU/s.

    ``ENABLE`` False makes OUT track IN. ``INCR_LIMITED`` / ``DECR_LIMITED``
    / ``LIMITED`` report limiting, and ``ROC`` is a separately filtered
    (``TIMECONST``) rate-of-change indication of IN in EU per ``TIME_UNITS``.
    """
    block_type = "RATE_LIMITER"
    category = BlockCategory.SIGNAL
    display_name = "Rate Limiter"
    description = "Limits rate of change of signal"

    config_aliases = {"INCREASE_MAX": "RATE_UP"}
    config_choices = {"TIME_UNITS": tuple(TIME_UNIT_SECONDS)}
    config_units = {"TIMECONST": "s"}

    def __init__(self, instance_name=""):
        self._prev = 0.0
        self._prev_in = 0.0
        self._roc = 0.0
        self._initialized = False
        super().__init__(instance_name)

    def normalize_config(self) -> list[str]:
        # Azeo DECREASE_MAX is a negative number; RATE_DN is the positive
        # magnitude. A plain alias would keep the sign, so convert here.
        params = self.config.params
        if "DECREASE_MAX" in params:
            val = params.pop("DECREASE_MAX")
            if "RATE_DN" not in params:
                try:
                    params["RATE_DN"] = abs(float(val))
                except (TypeError, ValueError):
                    pass
        return super().normalize_config()

    def _define_terminals(self):
        self.add_input("IN", description="Input signal")
        self.add_input("ENABLE", DataType.BOOL, True,
                       "True = rate limiting active; False = OUT tracks IN")
        self.add_output("OUT", description="Rate-limited output")
        self.add_output("INCR_LIMITED", DataType.BOOL, False,
                        "Increasing rate was limited")
        self.add_output("DECR_LIMITED", DataType.BOOL, False,
                        "Decreasing rate was limited")
        self.add_output("LIMITED", DataType.BOOL, False,
                        "INCR_LIMITED OR DECR_LIMITED")
        self.add_output("ROC", description="Filtered rate of change of IN "
                                           "[EU per TIME_UNITS]")

    def get_config_schema(self):
        return {
            "RATE_UP": (float, 10.0, "Max increase rate (Azeo INCREASE_MAX) [EU/TIME_UNITS]"),
            "RATE_DN": (float, 10.0, "Max decrease rate magnitude (Azeo |DECREASE_MAX|)"),
            "TIME_UNITS": (str, "SECONDS", "Rate time base: SECONDS|MINUTES|HOURS|DAYS"),
            "TIMECONST": (float, 0.0, "Filter time constant on the ROC indication [s]"),
        }

    def execute(self, dt: float):
        val = self.get_input("IN")
        raw_in = val
        if not self._initialized:
            self._prev = val
            self._prev_in = val
            self._initialized = True

        p = self.config.params
        unit_s = TIME_UNIT_SECONDS.get(
            str(p.get("TIME_UNITS", "SECONDS")).upper(), 1.0)
        max_up = p.get("RATE_UP", 10.0) / unit_s * dt
        max_dn = p.get("RATE_DN", 10.0) / unit_s * dt

        incr = decr = False
        if self.get_input("ENABLE"):
            delta = val - self._prev
            if delta > max_up:
                val = self._prev + max_up
                incr = True
            elif delta < -max_dn:
                val = self._prev - max_dn
                decr = True

        self._prev = val

        # ROC — an indication of IN's own rate, filtered independently of
        # the scan-to-scan rate the limiting algorithm uses.
        raw_roc = ((raw_in - self._prev_in) / dt * unit_s) if dt > 1e-12 else 0.0
        self._prev_in = raw_in
        alpha = first_order_alpha(dt, p.get("TIMECONST", 0.0))
        self._roc = alpha * raw_roc + (1.0 - alpha) * self._roc

        self.set_output("OUT", val)
        self.set_output("INCR_LIMITED", incr)
        self.set_output("DECR_LIMITED", decr)
        self.set_output("LIMITED", incr or decr)
        self.set_output("ROC", self._roc)

    def reset(self):
        super().reset()
        self._prev = 0.0
        self._prev_in = 0.0
        self._roc = 0.0
        self._initialized = False
