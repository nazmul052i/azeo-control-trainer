"""Filter & delay blocks — FILTER, DEADTIME, MOVING_AVG."""
from __future__ import annotations
import collections
import math
from ..model.block_base import FunctionBlock, BlockCategory, DataType
from ..model.block_registry import register_block


# Discretization of a first-order lag, shared by FILTER / LEAD_LAG /
# RATE_LIMITER's ROC indication.
#
#   "EXACT" — Azeo FLTR (spec §Filter): OUT = x + (IN-x)*(1 - e^(-dt/tau)),
#             so OUT reaches 63.2 % of a step after exactly tau seconds at
#             any scan rate.
#   "EULER" — backward-Euler approximation alpha = dt/(tau+dt). Agrees with
#             EXACT only for dt << tau; at dt = tau/2.5 it is ~15 % slow.
#
# Kept selectable because 75 shipped LEAD_LAG instances were tuned against
# the Euler form (see DISCRETIZATION on each block for the per-block default).
DISCRETIZATION_CHOICES = ("EXACT", "EULER")

#: Azeo TIME_UNITS / TIME_UNITn named set → seconds per unit. Shared by
#: RATE_LIMITER (rate conversion + ROC) and INTEGRATOR (rate time base).
TIME_UNIT_SECONDS = {
    "SECONDS": 1.0,
    "MINUTES": 60.0,
    "HOURS": 3600.0,
    "DAYS": 86400.0,
}


def first_order_alpha(dt: float, tau: float, mode: str = "EXACT") -> float:
    """Per-scan blend factor of a first-order lag with time constant ``tau``.

    Returns 1.0 (no filtering) for a non-positive ``tau``, matching the
    spec's "TIMECONST = 0 ⇒ OUT = IN".
    """
    if tau <= 0.0 or dt <= 0.0:
        return 1.0
    # Ordered so the two canonical spellings cost one string compare each
    # — this runs every scan on 75 shipped LEAD_LAG instances.
    if mode == "EULER":
        return dt / (tau + dt)
    if mode != "EXACT" and str(mode).upper() == "EULER":
        return dt / (tau + dt)
    return 1.0 - math.exp(-dt / tau)


@register_block
class FilterBlock(FunctionBlock):
    """First-order lag / low-pass filter (Azeo FLTR).

    ``OUT = x + (IN - x) * (1 - e^(-dt/TIMECONST))`` — TAU (alias
    ``TIMECONST``) is the time for OUT to reach 63 % of a step in IN.
    ``FOLLOW`` (or the legacy ``BYPASS``) disables the filter so OUT
    tracks IN, and ``TAU = 0`` means no filtering at all.

    ``DISCRETIZATION`` defaults to ``EXACT`` (the spec's exponential);
    ``EULER`` restores the pre-audit ``alpha = dt/(TAU+dt)`` approximation.
    """
    block_type = "FILTER"
    category = BlockCategory.SIGNAL
    display_name = "Filter"
    description = "First-order exponential filter (noise reduction)"

    config_aliases = {"TIMECONST": "TAU"}
    config_choices = {"DISCRETIZATION": DISCRETIZATION_CHOICES}
    config_units = {"TAU": "s"}

    def __init__(self, instance_name=""):
        self._prev_out = 0.0
        self._initialized = False
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("IN", description="Input signal")
        self.add_input("BYPASS", DataType.BOOL, False, "Bypass filter (= FOLLOW)")
        self.add_input("FOLLOW", DataType.BOOL, False,
                       "Azeo FOLLOW: True = OUT tracks IN (filter disabled)")
        self.add_output("OUT", description="Filtered output")

    def get_config_schema(self):
        return {
            "TAU": (float, 5.0, "Filter time constant [s] (Azeo TIMECONST; 0 = no filtering)"),
            "DISCRETIZATION": (str, "EXACT",
                               "EXACT = 1-e^(-dt/TAU) (Azeo); EULER = dt/(TAU+dt)"),
        }

    def execute(self, dt: float):
        inp = self.get_input("IN")
        tau = self.config.params.get("TAU", 5.0)
        # FOLLOW and the legacy BYPASS are OR-ed; TIMECONST = 0 is "no filtering".
        if (self.get_input("BYPASS") or self.get_input("FOLLOW")
                or not self._initialized or tau <= 0.0):
            self._prev_out = inp
            self._initialized = True
        else:
            alpha = first_order_alpha(
                dt, tau, self.config.params.get("DISCRETIZATION", "EXACT"))
            self._prev_out = alpha * inp + (1.0 - alpha) * self._prev_out
        self.set_output("OUT", self._prev_out)

    def reset(self):
        super().reset()
        self._prev_out = 0.0
        self._initialized = False


@register_block
class DeadtimeBlock(FunctionBlock):
    """Transport delay / dead time block (Azeo DEADTIME).

    OUT is IN delayed by ``DELAY`` (alias ``DEAD_TIME``) seconds, clamped
    to the spec's 0–32400 s range. ``FOLLOW`` re-initializes the stored
    history to the current IN and passes it straight through.

    ``INTERP`` (default True) interpolates between the two buffered samples
    straddling the requested delay, so the delivered delay is DEAD_TIME
    rather than ``floor(DEAD_TIME/dt)*dt``; set it False for the pre-audit
    whole-scan behaviour.

    ``MAN`` holds OUT at ``OUT_MAN``. On the Man→Auto transition a bias is
    added to the delayed signal so OUT is continuous, then ramped out over
    ``BAL_TIME`` seconds (0 = step straight to the delayed value).
    """
    block_type = "DEADTIME"
    category = BlockCategory.SIGNAL
    display_name = "Dead Time"
    description = "Transport delay — delays signal by configurable time"

    config_aliases = {"DEAD_TIME": "DELAY"}
    config_units = {"DELAY": "s", "BAL_TIME": "s"}

    #: Azeo DEAD_TIME range (spec §Deadtime parameters).
    MAX_DEAD_TIME = 32400.0
    MAX_HISTORY_SAMPLES = 324002  # nine hours at the supported 100 ms scan rate

    def __init__(self, instance_name=""):
        self._buffer: collections.deque = collections.deque()
        self._time_buffer: collections.deque = collections.deque()
        self._bias = 0.0
        self._bal_left = 0.0
        self._prev_man = False
        self._last_out = 0.0
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("IN", description="Input signal")
        self.add_input("FOLLOW", DataType.BOOL, False,
                       "True = OUT tracks IN (history re-initialized, no delay)")
        self.add_input("MAN", DataType.BOOL, False,
                       "True = Man mode, OUT held at OUT_MAN")
        self.add_input("OUT_MAN", description="Operator-entered OUT while MAN")
        self.add_output("OUT", description="Delayed output")

    def get_config_schema(self):
        return {
            "DELAY": (float, 10.0, "Delay time [s] (Azeo DEAD_TIME, 0-32400)"),
            "INTERP": (bool, True,
                       "Interpolate between buffered samples (exact DEAD_TIME)"),
            "BAL_TIME": (float, 0.0, "Man→Auto bias ramp-out time [s]"),
        }

    def execute(self, dt: float):
        if not math.isfinite(dt) or dt <= 0:
            raise ValueError("Delay scan interval must be finite and positive")
        p = self.config.params
        delay = min(max(p.get("DELAY", 10.0), 0.0), self.MAX_DEAD_TIME)
        inp = self.get_input("IN")

        if self.get_input("FOLLOW"):
            # Spec: history is initialized to the current IN, OUT tracks IN.
            self._buffer.clear()
            self._time_buffer.clear()
            self._buffer.append(inp)
            self._time_buffer.append(0.0)
            self._bias = 0.0
            self._bal_left = 0.0
            self._prev_man = False
            self._last_out = inp
            self.set_output("OUT", inp)
            return

        # Push current value
        self._buffer.append(inp)
        if len(self._time_buffer) > 0:
            self._time_buffer.append(self._time_buffer[-1] + dt)
        else:
            self._time_buffer.append(0.0)

        now = self._time_buffer[-1]
        target = now - delay

        if p.get("INTERP", True):
            # Keep the newest sample at or before ``target`` as buffer[0] so
            # the pair (buffer[0], buffer[1]) straddles it.
            while len(self._time_buffer) > 1 and self._time_buffer[1] <= target:
                self._buffer.popleft()
                self._time_buffer.popleft()
            delayed = self._buffer[0]
            if len(self._buffer) > 1 and self._time_buffer[0] < target:
                t0, t1 = self._time_buffer[0], self._time_buffer[1]
                span = t1 - t0
                if span > 1e-12:
                    frac = (target - t0) / span
                    delayed += frac * (self._buffer[1] - self._buffer[0])
        else:
            # Legacy: keep samples with age <= delay (delay biased short).
            while len(self._time_buffer) > 1 and (now - self._time_buffer[0]) > delay:
                self._buffer.popleft()
                self._time_buffer.popleft()
            delayed = self._buffer[0]

        if len(self._buffer) > self.MAX_HISTORY_SAMPLES:
            self._buffer.pop()
            self._time_buffer.pop()
            raise ValueError("Delay history exceeds the sample budget; increase scan interval or reduce delay")

        if self.get_input("MAN"):
            out = self.get_input("OUT_MAN")
            self._prev_man = True
            self._bias = 0.0
            self._bal_left = 0.0
        else:
            if self._prev_man:
                # Bumpless Man→Auto: bias the delayed signal onto the held OUT.
                self._bias = self._last_out - delayed
                self._bal_left = max(p.get("BAL_TIME", 0.0), 0.0)
                if self._bal_left <= 0.0:
                    self._bias = 0.0
                self._prev_man = False
            # OUT matches the held value on the transition scan; the bias
            # then ramps to zero over the remaining BAL_TIME.
            out = delayed + self._bias
            if self._bal_left > 0.0:
                self._bias *= max(0.0, self._bal_left - dt) / self._bal_left
                self._bal_left = max(0.0, self._bal_left - dt)
            else:
                self._bias = 0.0

        self._last_out = out
        self.set_output("OUT", out)

    def reset(self):
        super().reset()
        self._buffer.clear()
        self._time_buffer.clear()
        self._bias = 0.0
        self._bal_left = 0.0
        self._prev_man = False
        self._last_out = 0.0


@register_block
class MovingAvgBlock(FunctionBlock):
    """Moving average filter (Honeywell MOVAVG).

    Computes average of last N samples.
    """
    block_type = "MOVING_AVG"
    category = BlockCategory.SIGNAL
    display_name = "Moving Average"
    description = "N-sample moving average filter"

    def __init__(self, instance_name=""):
        self._samples: collections.deque = collections.deque(maxlen=100)
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("IN", description="Input signal")
        self.add_input("RESET", DataType.BOOL, False, "Reset buffer")
        self.add_output("OUT", description="Averaged output")
        self.add_output("COUNT", DataType.INT, 0, "Number of samples in buffer")

    def get_config_schema(self):
        return {
            "N": (int, 10, "Window size (number of samples)"),
        }

    def execute(self, dt: float):
        n = max(int(self.config.params.get("N", 10)), 1)
        if n > 65536:
            raise ValueError("Moving average window exceeds 65536 samples")

        if self.get_input("RESET"):
            self._samples.clear()

        # Resize window if N changed
        if self._samples.maxlen != n:
            old = list(self._samples)
            self._samples = collections.deque(old[-n:], maxlen=n)

        self._samples.append(self.get_input("IN"))
        avg = sum(self._samples) / len(self._samples)
        self.set_output("OUT", avg)
        self.set_output("COUNT", len(self._samples))

    def reset(self):
        super().reset()
        self._samples.clear()
