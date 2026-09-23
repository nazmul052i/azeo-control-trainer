"""Signal processing blocks — LeadLag, Ratio, SignalCharacterizer, Ramp, Scaler.

Signal quality: terminals carry a ``Quality`` and a ``LimitStatus`` alongside
their value (``model/terminal.py``), and the runtime propagates both along
forward and BKCAL wires. The blocks here follow the manual's rule for a
computation block — the result is only as trustworthy as its least trustworthy
input — and a block holding its output at a limit says so on ``OUT`` (and back
up its BKCAL terminal) so an upstream controller can stop winding reset into it.
Defaults are Good / Not limited, so nothing changes for a strategy that ignores
status.
"""
from __future__ import annotations
import math
from ..model.block_base import FunctionBlock, BlockCategory, DataType
from ..model.block_registry import register_block
from ..model.terminal import Quality, LimitStatus
from .alarm_engine import (
    AlarmRule,
    BASE_ANALOG_ALARMS,
    StandardAlarmEngine,
)
from .filter_blocks import DISCRETIZATION_CHOICES, first_order_alpha


_REMOTE_MISSING = object()


def _worst(*qualities: Quality) -> Quality:
    """Worst of a set of qualities (Quality is ordered worst-last)."""
    return max(qualities, key=lambda q: q.value)


def _flip_limit(limit: LimitStatus) -> LimitStatus:
    """Swap high/low limiting — used when a block inverts its signal."""
    if limit is LimitStatus.HIGH_LIMITED:
        return LimitStatus.LOW_LIMITED
    if limit is LimitStatus.LOW_LIMITED:
        return LimitStatus.HIGH_LIMITED
    return limit


def scaler_forward(val: float, in_lo: float, in_hi: float,
                   out_lo: float, out_hi: float, invert: bool = False) -> float:
    """Forward scaling: input range → output range.

    Shared by ScalerBlock.execute() and bridge.py forward cascade init.
    """
    in_span = in_hi - in_lo
    out_span = out_hi - out_lo
    if abs(in_span) < 1e-12:
        return out_lo
    norm = (val - in_lo) / in_span
    if invert:
        norm = 1.0 - norm
    return out_lo + norm * out_span


def scaler_inverse(val: float, in_lo: float, in_hi: float,
                   out_lo: float, out_hi: float, invert: bool = False) -> float:
    """Inverse scaling: output range → input range (BKCAL direction).

    Shared by ScalerBlock.execute() and bridge.py BKCAL initialization.
    """
    out_span = out_hi - out_lo
    in_span = in_hi - in_lo
    if abs(out_span) < 1e-12:
        return in_lo
    norm = (val - out_lo) / out_span
    if invert:
        norm = 1.0 - norm
    if abs(in_span) < 1e-12:
        return in_lo
    return in_lo + norm * in_span


@register_block
class RemoteAnalogBlock(FunctionBlock):
    """Read a typed analog value published by another control module.

    Cross-module cascade and override links cannot use an ACT ``get_tag``
    call: that returns only a float and silently discards the source quality
    and limit state.  ``REMOTE_ANALOG`` reads the value plus the companion
    ``.quality``/``.limit`` fields published by :class:`DataBridge`, then
    presents one ordinary terminal so selectors and BKCAL chains retain the
    same value/status contract as an in-module wire.

    The path is configuration, not program logic.  This keeps the primitive
    reusable for any controller project and leaves all plant-specific names
    in generated engineering documents.
    """

    block_type = "REMOTE_ANALOG"
    category = BlockCategory.SIGNAL
    display_name = "Remote Analog"
    description = "Typed cross-module analog reference (value, quality, limit)"

    def _define_terminals(self):
        self.add_output("OUT", description="Remote analog value")

    def get_config_schema(self):
        return {
            "path": (str, "", "Published value path"),
            "default": (float, 0.0, "Value held until the path is available"),
            "quality_path": (
                str, "", "Quality companion (empty = <path>.quality)"),
            "limit_path": (
                str, "", "Limit companion (empty = <path>.limit)"),
            "missing_is_bad": (
                bool, True, "Publish Bad quality while the value is absent"),
        }

    @staticmethod
    def _quality(value) -> Quality:
        if isinstance(value, Quality):
            return value
        name = str(getattr(value, "name", value) or "").strip().upper()
        if name in Quality.__members__:
            return Quality[name]
        try:
            level = int(value)
        except (TypeError, ValueError):
            return Quality.BAD
        return (Quality.GOOD if level <= 0 else
                Quality.UNCERTAIN if level == 1 else Quality.BAD)

    @staticmethod
    def _limit(value) -> LimitStatus:
        if isinstance(value, LimitStatus):
            return value
        name = str(getattr(value, "name", value) or "").strip().upper()
        return LimitStatus.__members__.get(name, LimitStatus.NOT_LIMITED)

    def execute(self, dt: float):
        del dt
        p = self.config.params
        path = str(p.get("path") or "").strip()
        default = float(p.get("default", 0.0))
        context = getattr(self, "runtime_context", None)
        store = getattr(context, "store", None) if context is not None else None
        value = _REMOTE_MISSING
        if store is not None and callable(getattr(store, "get", None)):
            try:
                # O(1) reads matter here: a plant can have dozens of remote
                # links and copying the complete tag map for every block on
                # every scan makes cross-module wiring quadratic in practice.
                value = store.get(path, _REMOTE_MISSING)
            except Exception:  # provider loss is represented as Bad quality
                value = _REMOTE_MISSING

        if not path or value is _REMOTE_MISSING:
            # Holding the last received value is safer than substituting zero;
            # before the first sample, use the explicit configured default.
            if not getattr(self, "_received", False):
                self.set_output("OUT", default)
            quality = (Quality.BAD if p.get("missing_is_bad", True)
                       else Quality.UNCERTAIN)
            self.set_output_status("OUT", quality, LimitStatus.NOT_LIMITED)
            return

        try:
            value = float(value)
        except (TypeError, ValueError):
            self.set_output_status("OUT", Quality.BAD, LimitStatus.NOT_LIMITED)
            return
        quality_path = str(p.get("quality_path") or f"{path}.quality")
        limit_path = str(p.get("limit_path") or f"{path}.limit")
        quality = self._quality(store.get(quality_path, "GOOD"))
        limit = self._limit(store.get(limit_path, "NOT_LIMITED"))
        self.set_output("OUT", value)
        self.set_output_status("OUT", quality, limit)
        self._received = True

    def reset(self):
        super().reset()
        self._received = False


@register_block
class LeadLagBlock(FunctionBlock):
    """Lead/lag dynamic compensator (Azeo LL).

    Realizes ``GAIN * (1 + T_lead*s) / (1 + T_lag*s)`` — a step ΔIN kicks
    OUT by ``(T_lead/T_lag) * GAIN * ΔIN`` and settles to ``GAIN * ΔIN``.
    ``T_lead = 0`` gives a pure first-order lag (the spec's "first-order
    filter" configuration).

    ``PV`` is the unlimited compensated value; ``OUT`` is PV clamped to
    ``OUT_LO_LIM``..``OUT_HI_LIM`` (defaults are non-limiting). ``FOLLOW``
    bypasses the compensation so OUT = IN × GAIN. ``MAN`` holds OUT at
    ``OUT_MAN``; on Man→Auto a bias makes OUT continuous and ramps out over
    ``BAL_TIME``.

    ``DISCRETIZATION`` defaults to ``EULER`` — the lag's pre-audit
    ``alpha = dt/(T_lag+dt)`` form, which 75 shipped LEAD_LAG instances were
    tuned against. ``EXACT`` switches to the spec's ``1-e^(-dt/T_lag)``.
    """
    block_type = "LEAD_LAG"
    category = BlockCategory.SIGNAL
    display_name = "Lead/Lag"
    description = "Lead-lag compensator: T_lead*s+1 / T_lag*s+1"

    def __init__(self, instance_name=""):
        self._prev_in = 0.0
        self._prev_out = 0.0
        self._bias = 0.0
        self._bal_left = 0.0
        self._prev_man = False
        self._last_out = 0.0
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("IN", description="Input signal")
        self.add_input("FOLLOW", DataType.BOOL, False,
                       "True = bypass compensation, OUT = IN × GAIN")
        self.add_input("MAN", DataType.BOOL, False,
                       "True = Man mode, OUT held at OUT_MAN")
        self.add_input("OUT_MAN", description="Operator-entered OUT while MAN")
        self.add_output("OUT", description="Filtered output")
        self.add_output("PV", description="Compensated value before output limiting")
        self.add_output("LIMITED", DataType.BOOL, False, "OUT is at a limit")

    config_aliases = {
        "lead_time": "T_lead", "LEAD_TIME": "T_lead",
        "lag_time": "T_lag", "LAG_TIME": "T_lag",
    }
    config_choices = {"DISCRETIZATION": DISCRETIZATION_CHOICES}
    config_units = {"T_lead": "s", "T_lag": "s", "BAL_TIME": "s"}

    def get_config_schema(self):
        return {
            "T_lead": (float, 1.0, "Lead time constant [s] (Azeo LEAD_TIME; 0 = pure lag)"),
            "T_lag": (float, 10.0, "Lag time constant [s] (Azeo LAG_TIME)"),
            "GAIN": (float, 1.0, "Static gain"),
            "OUT_LO_LIM": (float, -1e9, "Minimum allowed output"),
            "OUT_HI_LIM": (float, 1e9, "Maximum allowed output"),
            "BAL_TIME": (float, 0.0, "Man→Auto bias ramp-out time [s]"),
            "DISCRETIZATION": (str, "EULER",
                               "Lag form: EULER = dt/(T_lag+dt); EXACT = 1-e^(-dt/T_lag)"),
        }

    def execute(self, dt: float):
        p = self.config.params
        t_lead = max(p.get("T_lead", 1.0), 0.0)
        t_lag = max(p.get("T_lag", 10.0), 0.001)
        gain = p.get("GAIN", 1.0)
        lo = p.get("OUT_LO_LIM", -1e9)
        hi = p.get("OUT_HI_LIM", 1e9)

        # Terminals are read directly: this block executes every scan on 75
        # shipped instances, so the extra get_input/set_output calls matter.
        ins = self.inputs
        inp = ins["IN"].value

        if ins["FOLLOW"].value:
            # Spec: no dynamic compensation, OUT reflects IN × GAIN.
            pv = gain * inp
            self._prev_out = pv
            self._bias = 0.0
            self._bal_left = 0.0
            self._prev_man = False
        else:
            mode = p.get("DISCRETIZATION", "EULER")
            if mode == "EULER":
                alpha = dt / (t_lag + dt)
            else:
                alpha = first_order_alpha(dt, t_lag, mode)
            lead_term = t_lead * (inp - self._prev_in) / dt if dt > 1e-9 else 0.0
            # GAIN multiplies the whole compensator, GAIN*(1+LEAD*s)/(1+LAG*s) —
            # applying it to the input alone understates the initial lead kick.
            pv = alpha * gain * (inp + lead_term) + (1 - alpha) * self._prev_out
            self._prev_out = pv

        self._prev_in = inp

        limit = LimitStatus.NOT_LIMITED
        if ins["MAN"].value:
            out = ins["OUT_MAN"].value
            self._prev_man = True
            self._bias = 0.0
            self._bal_left = 0.0
            limited = False
            # Man: OUT is operator-entered and cannot move on its own.
            limit = LimitStatus.CONSTANT
        else:
            if self._prev_man:
                # Bumpless Man→Auto: bias PV onto the held OUT, ramp it out.
                self._bias = self._last_out - pv
                self._bal_left = max(p.get("BAL_TIME", 0.0), 0.0)
                if self._bal_left <= 0.0:
                    self._bias = 0.0
                self._prev_man = False
            # OUT matches the held value on the transition scan; the bias
            # then ramps to zero over the remaining BAL_TIME.
            biased = pv + self._bias if self._bias else pv
            if biased > hi:
                out, limited = hi, True
                limit = LimitStatus.HIGH_LIMITED
            elif biased < lo:
                out, limited = lo, True
                limit = LimitStatus.LOW_LIMITED
            else:
                out, limited = biased, False
            if self._bal_left > 0.0:
                self._bias *= max(0.0, self._bal_left - dt) / self._bal_left
                self._bal_left = max(0.0, self._bal_left - dt)
            else:
                self._bias = 0.0

        self._last_out = out
        outs = self.outputs
        outs["PV"].value = pv
        outs["OUT"].value = out
        outs["LIMITED"].value = limited

        # A dynamic compensator passes its input's quality through, and says
        # so when OUT is pinned at a limit (or held by Man).
        quality = ins["OUT_MAN"].status if ins["MAN"].value else ins["IN"].status
        outs["OUT"].status = quality
        outs["OUT"].limit = limit
        outs["PV"].status = ins["IN"].status
        outs["PV"].limit = LimitStatus.NOT_LIMITED

    def reset(self):
        super().reset()
        self._prev_in = 0.0
        self._prev_out = 0.0
        self._bias = 0.0
        self._bal_left = 0.0
        self._prev_man = False
        self._last_out = 0.0


@register_block
class RatioBlock(FunctionBlock):
    block_type = "RATIO"
    category = BlockCategory.CONTROL
    display_name = "Ratio"
    description = "OUT = IN * RATIO + BIAS"

    config_aliases = {"ratio": "RATIO", "bias": "BIAS", "SP": "RATIO"}

    def _define_terminals(self):
        self.add_input("IN", description="Wild flow (measured)")
        self.add_input("RATIO_IN", description="External ratio adjustment")
        self.add_output("OUT", description="Controlled flow setpoint")

    def get_config_schema(self):
        return {
            "RATIO": (float, 1.0, "Ratio setpoint"),
            "RATIO_LO": (float, 0.5, "Ratio low limit"),
            "RATIO_HI": (float, 2.0, "Ratio high limit"),
            "BIAS": (float, 0.0, "Output bias"),
        }

    def execute(self, dt: float):
        p = self.config.params
        ratio = p.get("RATIO", 1.0)
        if self.inputs["RATIO_IN"].connected:
            ratio = self.get_input("RATIO_IN")
        ratio_lo = p.get("RATIO_LO", 0.5)
        ratio_hi = p.get("RATIO_HI", 2.0)
        ratio = max(ratio_lo, min(ratio_hi, ratio))
        # Prevent zero ratio (would produce zero output regardless of input)
        if abs(ratio) < 1e-6:
            ratio = 1e-6

        out = self.get_input("IN") * ratio + p.get("BIAS", 0.0)
        self.set_output("OUT", out)
        # Only as trustworthy as the wild flow and the external ratio.
        self.set_output_status(
            "OUT", _worst(self.input_status("IN"), self.input_status("RATIO_IN")))


@register_block
class SignalCharBlock(FunctionBlock):
    """Piecewise-linear signal characterizer (Azeo SGCR).

    Two independent inputs (``IN``/``IN_1`` and ``IN_2``) are characterized
    through the same curve to ``OUT``/``OUT_1`` and ``OUT_2``. The curve is
    ``x_points``/``y_points`` (aliases ``CURVE_X``/``CURVE_Y``, accepted as
    a CSV string or a list), capped at the spec's 21 points; inputs beyond
    the curve clamp to the corresponding endpoint.

    Curve validation follows the spec instead of raising: X must be strictly
    ascending, the pairs are truncated at the first violation (or at the
    shorter of the two lists), and ``CFG_ERR`` is set — previously a
    mismatched-length curve raised ``IndexError`` every scan and froze OUT.

    ``SWAP_2`` swaps the axes for channel 2 (IN_2 reads CURVE_Y, OUT_2
    yields CURVE_X) for inverse characterization / split range.
    ``BYPASS`` passes both channels straight through.
    """
    block_type = "SIGNAL_CHAR"
    category = BlockCategory.SIGNAL
    display_name = "Signal Characterizer"
    description = "Piecewise linear characterizer (up to 21 points)"

    #: Azeo CURVE_X / CURVE_Y array size.
    MAX_POINTS = 21

    config_aliases = {"CURVE_X": "x_points", "CURVE_Y": "y_points"}
    terminal_aliases = {"IN_1": "IN", "OUT_1": "OUT"}

    def _define_terminals(self):
        self.add_input("IN", description="Input signal (Azeo IN_1)")
        self.add_input("IN_2", description="Second input, same curve")
        self.add_input("BYPASS", DataType.BOOL, False,
                       "True = OUT = IN, OUT_2 = IN_2 (no characterization)")
        self.add_output("OUT", description="Characterized output (Azeo OUT_1)")
        self.add_output("OUT_2", description="Characterized second output")
        self.add_output("CFG_ERR", DataType.BOOL, False,
                        "Curve configuration error (non-ascending X / short curve)")

    def get_config_schema(self):
        return {
            "x_points": (str, "0,25,50,75,100", "X breakpoints (Azeo CURVE_X)"),
            "y_points": (str, "0,25,50,75,100", "Y breakpoints (Azeo CURVE_Y)"),
            "SWAP_2": (bool, False, "Channel 2 swaps the X and Y axes"),
        }

    @staticmethod
    def _parse(value, fallback):
        """Accept a CSV string or a list/tuple of numbers."""
        if isinstance(value, (list, tuple)):
            seq = value
        else:
            seq = str(value).split(",")
        try:
            return [float(v) for v in seq]
        except (TypeError, ValueError):
            return list(fallback)

    def _curve(self):
        """Return ``(xs, ys, cfg_err)`` — validated, truncated curve."""
        p = self.config.params
        xs = self._parse(p.get("x_points", "0,100"), (0.0, 100.0))
        ys = self._parse(p.get("y_points", "0,100"), (0.0, 100.0))

        err = False
        # Spec: arrays hold up to 21 points; extra points are not part of
        # the curve definition.
        if len(xs) > self.MAX_POINTS or len(ys) > self.MAX_POINTS:
            err = True
            xs = xs[:self.MAX_POINTS]
            ys = ys[:self.MAX_POINTS]
        # Pairs only — a curve is X,Y coordinate pairs.
        n = min(len(xs), len(ys))
        if len(xs) != len(ys):
            err = True
        xs, ys = xs[:n], ys[:n]
        # Spec: X must be strictly ascending; truncate at the first violation.
        for i in range(1, len(xs)):
            if xs[i] < xs[i - 1]:
                err = True
                xs, ys = xs[:i], ys[:i]
                break
        return xs, ys, err

    @staticmethod
    def _interp(val, xs, ys):
        """Point-slope interpolation with endpoint clamping."""
        if val <= xs[0]:
            return ys[0]
        if val >= xs[-1]:
            return ys[-1]
        for i in range(len(xs) - 1):
            if xs[i] <= val <= xs[i + 1]:
                span = xs[i + 1] - xs[i]
                m = (ys[i + 1] - ys[i]) / span if abs(span) > 1e-12 else 0.0
                return ys[i] + m * (val - xs[i])
        return val

    @staticmethod
    def _clamp_limit(val, xs) -> LimitStatus:
        """An input past either end of the curve pins OUT at that endpoint."""
        if val <= xs[0]:
            return LimitStatus.LOW_LIMITED
        if val >= xs[-1]:
            return LimitStatus.HIGH_LIMITED
        return LimitStatus.NOT_LIMITED

    def _publish_status(self, err: bool, lim1: LimitStatus, lim2: LimitStatus):
        """A characteriser passes each channel's input quality through; a
        curve configuration error downgrades both to Uncertain."""
        q1 = self.input_status("IN")
        q2 = self.input_status("IN_2")
        if err:
            q1 = _worst(q1, Quality.UNCERTAIN)
            q2 = _worst(q2, Quality.UNCERTAIN)
        self.set_output_status("OUT", q1, lim1)
        self.set_output_status("OUT_2", q2, lim2)

    def execute(self, dt: float):
        inp = self.get_input("IN")
        in2 = self.get_input("IN_2")

        if self.get_input("BYPASS"):
            self.set_output("OUT", inp)
            self.set_output("OUT_2", in2)
            self.set_output("CFG_ERR", False)
            self._publish_status(False, LimitStatus.NOT_LIMITED,
                                 LimitStatus.NOT_LIMITED)
            return

        xs, ys, err = self._curve()
        if len(xs) < 2:
            self.set_output("OUT", inp)
            self.set_output("OUT_2", in2)
            self.set_output("CFG_ERR", True)
            self._publish_status(True, LimitStatus.NOT_LIMITED,
                                 LimitStatus.NOT_LIMITED)
            return

        self.set_output("OUT", self._interp(inp, xs, ys))
        lim1 = self._clamp_limit(inp, xs)
        lim2 = self._clamp_limit(in2, xs)

        if self.config.params.get("SWAP_2", False):
            # Channel 2 reads CURVE_Y and yields CURVE_X; Y must then also
            # be ascending or the swapped curve is truncated.
            sxs, sys_ = ys, xs
            for i in range(1, len(sxs)):
                if sxs[i] < sxs[i - 1]:
                    err = True
                    sxs, sys_ = sxs[:i], sys_[:i]
                    break
            out2 = self._interp(in2, sxs, sys_) if len(sxs) >= 2 else in2
            lim2 = (self._clamp_limit(in2, sxs) if len(sxs) >= 2
                    else LimitStatus.NOT_LIMITED)
        else:
            out2 = self._interp(in2, xs, ys)
        self.set_output("OUT_2", out2)
        self.set_output("CFG_ERR", err)
        self._publish_status(err, lim1, lim2)


@register_block
class RampBlock(FunctionBlock):
    """Ramp an output toward a target (Azeo RAMP).

    ``TARGET`` (alias ``END_VALUE``) is the endpoint and ``RATE`` (alias
    ``RAMP_RATE``) the EU/s rate; wire ``COMPLETE`` for ``DONE``.

    ``DISABLE_ACTION`` governs ``ENABLE`` = False and defaults to the
    spec's ``HOLD`` — OUT holds its last value (or tracks ``IN`` when
    ``TRK_IN_D`` is True), with ``COMPLETE`` forced False and
    ``TIME_REMAIN`` = 0. The pre-audit behaviour, which stepped OUT
    straight to the target — the very bump the block exists to prevent —
    is still available as ``JUMP``.

    ``START_MODE`` defaults to ``CONTINUOUS``: the block chases whatever
    TARGET currently is, every scan (a rate limiter on TARGET). ``EDGE``
    is the Azeo semantic — the ramp starts on a False→True ``ENABLE``
    from ``IN`` (if TRK_IN_D) or the previous OUT, and re-initializes when
    TARGET, the selected rate, or RAMP_TYPE changes.

    ``RAMP_TYPE`` False takes the rate from ``RAMP_TIME`` (seconds from the
    start value to TARGET) instead of ``RATE``. ``PAUSE`` freezes OUT and
    ``TIME_REMAIN``.
    """
    block_type = "RAMP"
    category = BlockCategory.SIGNAL
    display_name = "Ramp"
    description = "Rate-limited ramp to target"

    terminal_aliases = {
        "END_VALUE": "TARGET", "RAMP_RATE": "RATE", "COMPLETE": "DONE",
    }
    config_choices = {
        "DISABLE_ACTION": ("HOLD", "JUMP", "TRACK_IN"),
        "START_MODE": ("CONTINUOUS", "EDGE"),
    }
    config_units = {"RAMP_TIME": "s"}

    def __init__(self, instance_name=""):
        self._current = 0.0
        self._start = 0.0
        self._time_remain = 0.0
        self._initialized = False
        self._prev_enable = False
        self._prev_key = None
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("TARGET", description="Target value (Azeo END_VALUE)")
        self.add_input("RATE", default=1.0, description="Ramp rate per second")
        self.add_input("ENABLE", DataType.BOOL, True, "Enable ramping")
        self.add_input("IN", description="Ramp start / tracking value")
        self.add_input("PAUSE", DataType.BOOL, False, "Hold OUT and TIME_REMAIN")
        self.add_input("TRK_IN_D", DataType.BOOL, False,
                       "True = OUT tracks IN, and IN is the ramp start")
        self.add_output("OUT", description="Ramped output")
        self.add_output("DONE", DataType.BOOL, False, "Ramp complete (Azeo COMPLETE)")
        self.add_output("TIME_REMAIN", description="Seconds until DONE")

    def get_config_schema(self):
        return {
            "DISABLE_ACTION": (str, "HOLD",
                               "ENABLE=False: HOLD last OUT (Azeo) | JUMP to "
                               "target (legacy) | TRACK_IN"),
            "START_MODE": (str, "CONTINUOUS",
                           "CONTINUOUS = chase TARGET every scan | EDGE = start "
                           "on ENABLE rising edge (Azeo)"),
            "RAMP_TYPE": (bool, True, "True = rate from RATE; False = from RAMP_TIME"),
            "RAMP_TIME": (float, 60.0, "Seconds from the ramp start to TARGET"),
        }

    def _out_quality(self, tracking: bool) -> Quality:
        """OUT is only as good as the values that produced it."""
        if tracking:
            return _worst(self.input_status("IN"), self.input_status("TARGET"))
        return _worst(self.input_status("TARGET"), self.input_status("RATE"))

    def _rate(self, target: float) -> float:
        p = self.config.params
        if p.get("RAMP_TYPE", True):
            return abs(self.get_input("RATE"))
        span = abs(target - self._start)
        ramp_time = max(p.get("RAMP_TIME", 60.0), 1e-9)
        return span / ramp_time

    def execute(self, dt: float):
        p = self.config.params
        target = self.get_input("TARGET")
        enable = bool(self.get_input("ENABLE"))
        trk = bool(self.get_input("TRK_IN_D"))
        in_val = self.get_input("IN")

        if not self._initialized:
            if trk:
                self._current = in_val
            self._start = self._current
            self._initialized = True

        if not enable:
            action = str(p.get("DISABLE_ACTION", "HOLD")).upper()
            if trk or action == "TRACK_IN":
                self._current = in_val
            elif action == "JUMP":
                self._current = target
            # HOLD: OUT keeps its last value.
            self._start = self._current
            self._time_remain = 0.0
            self._prev_enable = False
            self._prev_key = None
            self.set_output("OUT", self._current)
            self.set_output("DONE", False)   # spec: COMPLETE forced False
            self.set_output("TIME_REMAIN", 0.0)
            # Disabled: OUT is held (or tracking) — it cannot move on its own.
            self.set_output_status(
                "OUT", self._out_quality(trk),
                LimitStatus.NOT_LIMITED if (trk or action == "TRACK_IN")
                else LimitStatus.CONSTANT)
            return

        key = (target, p.get("RAMP_TYPE", True),
               self.get_input("RATE"), p.get("RAMP_TIME", 60.0))
        edge_mode = str(p.get("START_MODE", "CONTINUOUS")).upper() == "EDGE"
        if edge_mode and (not self._prev_enable or
                          (self._prev_key is not None and key != self._prev_key)):
            # Ramp (re-)starts: from IN when tracking, else the previous OUT.
            if not self._prev_enable and trk:
                self._current = in_val
            self._start = self._current
        elif not edge_mode:
            self._start = self._current if self._prev_key != key else self._start
        self._prev_enable = True
        self._prev_key = key

        if not self.get_input("PAUSE"):
            rate = self._rate(target)
            delta = target - self._current
            max_change = rate * dt
            if abs(delta) <= max_change:
                self._current = target
            else:
                self._current += max_change if delta > 0 else -max_change
            remain = abs(target - self._current)
            self._time_remain = remain / rate if rate > 1e-12 else 0.0

        done = abs(self._current - target) < 1e-6
        self.set_output("OUT", self._current)
        self.set_output("DONE", done)
        self.set_output("TIME_REMAIN", 0.0 if done else self._time_remain)
        self.set_output_status(
            "OUT", self._out_quality(trk),
            LimitStatus.CONSTANT if self.get_input("PAUSE")
            else LimitStatus.NOT_LIMITED)

    def reset(self):
        super().reset()
        self._current = 0.0
        self._start = 0.0
        self._time_remain = 0.0
        self._initialized = False
        self._prev_enable = False
        self._prev_key = None


@register_block
class ScalerBlock(FunctionBlock):
    """Linear signal scaler with optional square root, clamping, and invert.

    Converts input from one engineering range to another:
        scaled = (IN - in_lo) / (in_hi - in_lo)    [0..1 normalized]
        if sqrt_enable: scaled = sqrt(scaled)       [flow compensation]
        OUT = out_lo + scaled * (out_hi - out_lo)   [re-ranged]
        if clamp:  OUT clamped to [out_lo, out_hi]
        if invert: OUT = out_hi - (OUT - out_lo)
    """

    block_type = "SCALER"
    category = BlockCategory.SIGNAL
    display_name = "Scaler"
    description = "Linear scaling with sqrt extraction, clamp, and invert"

    def _define_terminals(self):
        self.add_input("IN", description="Input signal")
        self.add_input("BKCAL_IN", description="Back-calculation from downstream",
                       is_bkcal=True)
        self.add_output("OUT", description="Scaled output")
        self.add_output("BKCAL_OUT", description="Inverse-scaled back-calculation",
                        is_bkcal=True)
        self.add_output("PCT", description="Normalized 0-100%")
        self.add_output("HI_LIM", data_type=DataType.BOOL, default=False,
                        description="Output at high limit")
        self.add_output("LO_LIM", data_type=DataType.BOOL, default=False,
                        description="Output at low limit")

    def get_config_schema(self):
        return {
            "in_lo": (float, 0.0, "Input range low"),
            "in_hi": (float, 100.0, "Input range high"),
            "out_lo": (float, 0.0, "Output range low"),
            "out_hi": (float, 100.0, "Output range high"),
            "clamp": (bool, True, "Clamp output to [out_lo, out_hi]"),
            "invert": (bool, False, "Invert output direction"),
            "sqrt_enable": (bool, False, "Square root extraction (flow)"),
            "gain": (float, 1.0, "Output gain multiplier"),
            "bias": (float, 0.0, "Output bias (added after scaling)"),
        }

    def execute(self, dt: float):
        p = self.config.params
        in_lo = p.get("in_lo", 0.0)
        in_hi = p.get("in_hi", 100.0)
        out_lo = p.get("out_lo", 0.0)
        out_hi = p.get("out_hi", 100.0)
        gain = p.get("gain", 1.0)
        bias = p.get("bias", 0.0)

        inp = self.get_input("IN")

        # Normalize to 0..1
        span = in_hi - in_lo
        if abs(span) < 1e-12:
            normalized = 0.0
        else:
            normalized = (inp - in_lo) / span

        # Square root extraction (common for DP flow transmitters)
        if p.get("sqrt_enable", False):
            normalized = math.sqrt(max(0.0, normalized))

        # Percentage output
        self.set_output("PCT", normalized * 100.0)

        # Scale to output range
        out = out_lo + normalized * (out_hi - out_lo)

        # Invert
        if p.get("invert", False):
            out = out_hi - (out - out_lo)

        # Apply gain and bias
        out = out * gain + bias

        # Clamp (against gain/bias-adjusted limits)
        hi_lim = False
        lo_lim = False
        if p.get("clamp", True):
            mn, mx = min(out_lo, out_hi), max(out_lo, out_hi)
            # Adjust clamp limits for gain and bias
            adj_mn = mn * gain + bias
            adj_mx = mx * gain + bias
            clamp_lo, clamp_hi = min(adj_mn, adj_mx), max(adj_mn, adj_mx)
            if out >= clamp_hi:
                out = clamp_hi
                hi_lim = True
            elif out <= clamp_lo:
                out = clamp_lo
                lo_lim = True

        self.set_output("OUT", out)
        self.set_output("HI_LIM", hi_lim)
        self.set_output("LO_LIM", lo_lim)

        # Status: a scaler passes its input's quality through, and reports the
        # clamp as a limit so the chain either side of it knows OUT cannot move
        # further that way.
        invert = p.get("invert", False)
        in_q = self.input_status("IN")
        # A range conversion does not erase a downstream constraint.  Keep
        # the incoming limit unless this scaler itself clamps first; external
        # reset chains rely on that status to stop a remote cascade master
        # winding into a slave that can no longer move.
        own_limit = (LimitStatus.HIGH_LIMITED if hi_lim else
                     LimitStatus.LOW_LIMITED if lo_lim else
                     self.input_limit("IN"))
        self.set_output_status("OUT", in_q, own_limit)
        self.set_output_status("PCT", in_q, own_limit)

        # Inverse scaling for BKCAL chain (output range → input range)
        # Must reverse gain/bias before inverse scaling
        if self.inputs["BKCAL_IN"].connected:
            bk_in = self.get_input("BKCAL_IN")
            # Reverse gain and bias first
            if abs(gain) > 1e-12:
                bk_in = (bk_in - bias) / gain
            bk_out = scaler_inverse(
                bk_in, in_lo, in_hi, out_lo, out_hi, p.get("invert", False))
            self.set_output("BKCAL_OUT", bk_out)
            # Relay the downstream block's limit (AO at its travel stop) plus
            # our own clamp back up the cascade. Both are expressed in the
            # *input* direction, so an inverting scaler swaps them.
            down = self.input_limit("BKCAL_IN")
            limit = down if down is not LimitStatus.NOT_LIMITED else own_limit
            if invert:
                limit = _flip_limit(limit)
            self.set_output_status("BKCAL_OUT",
                                   self.input_status("BKCAL_IN"), limit)


# ──────────────────────────────────────────────────────────────────
# Tier 1 — BIAS, TRANSFER, ALARM
# ──────────────────────────────────────────────────────────────────

@register_block
class BiasBlock(FunctionBlock):
    """Bias/Gain (BG) — Azeo-compatible adjustable bias + gain station.

    OUT = GAIN * IN + BIAS, with optional external bias, output limits,
    and external tracking (bumpless transfer). When ``TRK_IN_D`` is True,
    OUT is driven to ``TRK_VAL`` directly, bypassing the bias/gain math
    — useful when overriding control transfers control back.
    """
    block_type = "BIAS"
    category = BlockCategory.SIGNAL
    display_name = "Bias / Gain (BG)"
    description = "OUT = GAIN × IN + BIAS (operator-adjustable, with tracking)"

    def _define_terminals(self):
        self.add_input("IN", description="Input signal")
        self.add_input("BIAS_IN", description="External bias input (overrides config)")
        self.add_input("TRK_VAL", description="Tracking value (used when TRK_IN_D=True)")
        self.add_input("TRK_IN_D", DataType.BOOL, False,
                       "Tracking enable: force OUT=TRK_VAL when True")
        self.add_output("OUT", description="Biased output")
        self.add_output("BKCAL_OUT", description="Back-calc output (IN = (OUT - BIAS) / GAIN)",
                        is_bkcal=True)
        self.add_output("TRACKING", DataType.BOOL, False,
                        "True while tracking external signal")

    def get_config_schema(self):
        return {
            "GAIN": (float, 1.0, "Gain multiplier"),
            "BIAS": (float, 0.0, "Bias offset"),
            "OUT_LO": (float, -1e6, "Output low limit"),
            "OUT_HI": (float, 1e6, "Output high limit"),
        }

    def execute(self, dt: float):
        p = self.config.params
        gain = p.get("GAIN", 1.0)
        bias = p.get("BIAS", 0.0)
        lo = p.get("OUT_LO", -1e6)
        hi = p.get("OUT_HI", 1e6)

        if self.inputs["BIAS_IN"].connected:
            bias = self.get_input("BIAS_IN")

        tracking = bool(self.get_input("TRK_IN_D"))
        if tracking:
            out_raw = self.get_input("TRK_VAL")
        else:
            out_raw = gain * self.get_input("IN") + bias

        out = max(lo, min(hi, out_raw))
        self.set_output("OUT", out)
        self.set_output("TRACKING", tracking)

        # Status: worst of the values that made OUT, plus the limit state —
        # clamped at OUT_HI/OUT_LO, or Constant while tracking (OUT is being
        # driven from outside and the bias/gain path cannot move it).
        if tracking:
            quality = self.input_status("TRK_VAL")
            limit = LimitStatus.CONSTANT
        else:
            quality = _worst(self.input_status("IN"),
                             self.input_status("BIAS_IN"))
            limit = (LimitStatus.HIGH_LIMITED if out_raw > hi else
                     LimitStatus.LOW_LIMITED if out_raw < lo else
                     LimitStatus.NOT_LIMITED)
        self.set_output_status("OUT", quality, limit)

        # Inverse for BKCAL — use unclamped value so the BKCAL chain
        # is not distorted when the output hits a limit.
        if abs(gain) > 1e-12:
            self.set_output("BKCAL_OUT", (out_raw - bias) / gain)
        else:
            self.set_output("BKCAL_OUT", 0.0)
        # Report the limit upstream in the input's direction (a negative gain
        # reverses which way "high" is).
        self.set_output_status("BKCAL_OUT", quality,
                               _flip_limit(limit) if gain < 0 else limit)


@register_block
class TransferBlock(FunctionBlock):
    """Transfer switch (Azeo XFR).

    ``SELECTOR`` False places ``IN_1`` on ``OUT``, True places ``IN_2``;
    after a selection change OUT ramps **linearly to the newly selected
    input over ``BAL_TIME`` seconds**, so the switchover takes the same time
    whatever its size — the spec's behaviour, and the reason ``BAL_TIME``
    takes precedence over ``RAMP_RATE`` whenever it is greater than zero.
    ``BAL_TIME = 0`` (the default) keeps the legacy fixed-rate ``RAMP_RATE``
    ramp, where a 100-unit switchover takes ten times as long as a 10-unit one.

    ``IN_1`` / ``IN_2`` / ``SELECTOR`` are accepted as terminal aliases of
    ``IN1`` / ``IN2`` / ``SELECT``, so a strategy authored against the manual's
    names wires up instead of silently losing the connection.

    Status follows the spec: OUT carries the worse of the selected input's
    status and the SELECTOR's — which is what makes the block's other stated
    use, falling back to a predetermined value when a field signal goes Bad,
    visible downstream.
    """
    block_type = "TRANSFER"
    category = BlockCategory.SIGNAL
    display_name = "Transfer (XFR)"
    description = "Transfer between two signal sources over BAL_TIME"

    terminal_aliases = {"IN_1": "IN1", "IN_2": "IN2", "SELECTOR": "SELECT"}
    config_units = {"BAL_TIME": "s"}

    def __init__(self, instance_name=""):
        self._current = 0.0
        self._initialized = False
        self._prev_select = False
        self._bal_left = 0.0
        self._bal_from = 0.0
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("IN1", description="Source 1, Azeo IN_1 (when SELECT=False)")
        self.add_input("IN2", description="Source 2, Azeo IN_2 (when SELECT=True)")
        self.add_input("SELECT", DataType.BOOL, False,
                       "Azeo SELECTOR: False=IN_1, True=IN_2")
        self.add_output("OUT", description="Output (tracks selected source)")
        self.add_output("TRACKING", DataType.BOOL, False, "Transfer in progress")

    def get_config_schema(self):
        return {
            "BAL_TIME": (float, 0.0,
                         "Seconds for OUT to ramp to the newly selected input "
                         "(Azeo; 0 = use RAMP_RATE)"),
            "RAMP_RATE": (float, 10.0, "Transfer ramp rate [units/s] (0=instant)"),
        }

    def execute(self, dt: float):
        select = bool(self.get_input("SELECT"))
        target = self.get_input("IN2") if select else self.get_input("IN1")

        if not self._initialized:
            self._current = target
            self._prev_select = select
            self._initialized = True

        bal_time = max(self.config.params.get("BAL_TIME", 0.0) or 0.0, 0.0)
        if select != self._prev_select:
            self._prev_select = select
            # A fresh switchover restarts the balance ramp from wherever OUT
            # happens to be.
            self._bal_left = bal_time
            self._bal_from = self._current

        if bal_time > 0.0:
            # Azeo: fixed transfer *time*. The per-scan step comes from the
            # span at the moment of the switch, so the ramp completes in
            # BAL_TIME regardless of how big the step is.
            if self._bal_left > 0.0:
                rate = abs(target - self._bal_from) / bal_time
                delta = target - self._current
                max_change = rate * dt
                if abs(delta) <= max_change or self._bal_left <= dt:
                    self._current = target
                else:
                    self._current += max_change if delta > 0 else -max_change
                self._bal_left = max(0.0, self._bal_left - dt)
            else:
                # Not transferring: OUT simply follows the selected input.
                self._current = target
        else:
            rate = self.config.params.get("RAMP_RATE", 10.0)
            if rate <= 0:
                self._current = target
            else:
                delta = target - self._current
                max_change = rate * dt
                if abs(delta) <= max_change:
                    self._current = target
                else:
                    self._current += max_change if delta > 0 else -max_change

        self.set_output("OUT", self._current)
        self.set_output("TRACKING", abs(self._current - target) > 1e-6)

        # Spec: output status = worse of the selected input and SELECTOR.
        self.set_output_status(
            "OUT",
            _worst(self.input_status("IN2" if select else "IN1"),
                   self.input_status("SELECT")),
            self.input_limit("IN2" if select else "IN1"),
        )

    def reset(self):
        super().reset()
        self._current = 0.0
        self._initialized = False
        self._prev_select = False
        self._bal_left = 0.0
        self._bal_from = 0.0


@register_block
class AlarmBlock(FunctionBlock):
    """Alarm Detection block (Azeo ALM) — HI/HI_HI/LO/LO_LO + deviation.

    Applies limit and deviation alarm detection to a value from I/O or from
    another block's calculation. The block has no modes.

    ``ENABLE`` (True when unwired) is the spec's master switch: False disables
    alarming and clears **every** ``_ACT`` output. ``SHELVE`` suppresses
    annunciation instead; ``SHELVE_ACTION`` decides whether the latched states
    are cleared while shelved (``CLEAR``, the long-standing behaviour) or held
    frozen so an unshelved alarm re-appears exactly as it was (``HOLD``).

    ``SCALE_ENABLE`` accepts ``IN`` in percent and applies it to
    ``IN_SCALE_LO``..``IN_SCALE_HI`` to produce ``PV`` in EU before the limits
    are compared (the limits are always EU); with it on, ``DEADBAND``
    (Azeo ``ALARM_HYS``) is read as % of IN_SCALE and capped at 50 % of span.
    ``DEFAULT`` supplies PV whenever ``IN`` is not connected.

    Deviation alarming compares ``PV − SP``. Azeo writes ``DV_LO_LIM`` as a
    **negative** number; this block has always taken ``DEV_LO`` as a positive
    magnitude, so ``DV_LO_SIGN`` defaults to ``AUTO`` — a negative limit is read
    as a signed threshold, while a positive one keeps the magnitude
    convention. ``DV_HYS``
    adds the deadband the deviation alarms never had (0 = the historical
    chatter-on-the-limit behaviour); ``ROC_HYS`` does the same for the
    platform-specific rate-of-change alarm.

    ``CONDALM_ENABLED`` turns on the manual's conditional alarming, which
    *adds* five parameters per alarm — ``_ENAB`` (the existing ``HI_EN`` /
    ``HH_EN`` / ``LO_EN`` / ``LL_EN`` / ``DEV_EN`` keys, aliased as
    ``HI_ENAB`` …), ``_DELAY_ON``, ``_DELAY_OFF``, ``_ENAB_DELAY`` and
    ``_HYS``. All default to a no-op, and with conditional alarming on the
    per-alarm ``_HYS`` replaces ``ALARM_HYS`` for the four analog alarms while
    ``ALARM_HYS`` stays the deviation deadband, exactly as the manual says.

    Note: ``DEADBAND`` defaults to 1.0 here, where Azeo's ``ALARM_HYS``
    defaults to 0. An alarm whose limits are closer together than the deadband
    should set it explicitly.
    """
    block_type = "ALARM"
    category = BlockCategory.SIGNAL
    display_name = "Alarm Detection"
    description = "Process alarm: HI/LO/HH/LL/DEV/ROC with deadband & shelving"

    #: The manual's six alarm conditions, in _ACT parameter order.
    _ALARMS = ("HI", "HI_HI", "LO", "LO_LO", "DV_HI", "DV_LO")
    #: alarm → the config key enabling it (Azeo ``alarm_ENAB``).
    _ALARM_ENAB = {"HI": "HI_EN", "HI_HI": "HH_EN", "LO": "LO_EN",
                   "LO_LO": "LL_EN", "DV_HI": "DEV_EN", "DV_LO": "DEV_EN"}
    _ENAB_DEFAULT = {"HI": True, "HI_HI": True, "LO": True, "LO_LO": True,
                     "DV_HI": False, "DV_LO": False}
    #: alarm → this block's output terminal (Azeo ``alarm_ACT``).
    _ALARM_OUT = {"HI": "HI", "HI_HI": "HI_HI", "LO": "LO", "LO_LO": "LO_LO",
                  "DV_HI": "DEV_HI", "DV_LO": "DEV_LO"}

    # The manual's names for this block's value input and its _ACT outputs.
    terminal_aliases = {
        "IN": "PV",
        "HI_ACT": "HI", "HI_HI_ACT": "HI_HI", "LO_ACT": "LO",
        "LO_LO_ACT": "LO_LO", "DV_HI_ACT": "DEV_HI", "DV_LO_ACT": "DEV_LO",
    }
    config_aliases = {
        "ALARM_HYS": "DEADBAND",
        "DV_HI_LIM": "DEV_HI", "DV_LO_LIM": "DEV_LO",
        "HI_ENAB": "HI_EN", "HI_HI_ENAB": "HH_EN",
        "LO_ENAB": "LO_EN", "LO_LO_ENAB": "LL_EN",
    }
    config_choices = {
        "DV_LO_SIGN": ("AUTO", "POSITIVE", "AZEO"),
        "SHELVE_ACTION": ("CLEAR", "HOLD"),
    }
    config_units = {"ROC_LIM": "EU/s"}

    def __init__(self, instance_name=""):
        self._alarm_engine = StandardAlarmEngine()
        self._roc_active = False
        self._prev_pv = 0.0
        self._initialized = False
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("PV", description="Process variable (Azeo IN)")
        self.add_input("SP", description="Setpoint (for deviation alarm)")
        self.add_input("SHELVE", DataType.BOOL, False, "Shelve (suppress) all alarms")
        self.add_input("ENABLE", DataType.BOOL, True,
                       "Azeo ENABLE: False disables alarming and clears every _ACT")
        self.add_output("HI", DataType.BOOL, False, "High alarm active (HI_ACT)")
        self.add_output("HI_HI", DataType.BOOL, False, "High-high alarm active (HI_HI_ACT)")
        self.add_output("LO", DataType.BOOL, False, "Low alarm active (LO_ACT)")
        self.add_output("LO_LO", DataType.BOOL, False, "Low-low alarm active (LO_LO_ACT)")
        self.add_output("DEV_HI", DataType.BOOL, False, "Deviation high alarm (DV_HI_ACT)")
        self.add_output("DEV_LO", DataType.BOOL, False, "Deviation low alarm (DV_LO_ACT)")
        self.add_output("ROC", DataType.BOOL, False, "Rate-of-change alarm")
        self.add_output("ANY_ALARM", DataType.BOOL, False, "Any alarm active")
        self.add_output("PV", description="PV used for limit detection [EU]")

    def get_config_schema(self):
        schema = {
            "HI_LIM": (float, 90.0, "High alarm setpoint [EU]"),
            "HI_HI_LIM": (float, 95.0, "High-high alarm setpoint [EU]"),
            "LO_LIM": (float, 10.0, "Low alarm setpoint [EU]"),
            "LO_LO_LIM": (float, 5.0, "Low-low alarm setpoint [EU]"),
            "DEV_HI": (float, 5.0, "Deviation high limit, PV - SP (Azeo DV_HI_LIM)"),
            "DEV_LO": (float, 5.0, "Deviation low limit (Azeo DV_LO_LIM, negative)"),
            "DV_LO_SIGN": (str, "AUTO",
                           "DEV_LO convention: AUTO (negative = Azeo) | "
                           "POSITIVE magnitude | AZEO negative"),
            "DV_HYS": (float, 0.0, "Deviation alarm deadband (0 = none)"),
            "ROC_LIM": (float, 10.0, "Rate-of-change limit [units/s]"),
            "ROC_HYS": (float, 0.0, "Rate-of-change deadband (0 = none)"),
            "DEADBAND": (float, 1.0, "Alarm deadband (Azeo ALARM_HYS)"),
            "HI_EN": (bool, True, "Enable HI alarm (Azeo HI_ENAB)"),
            "HH_EN": (bool, True, "Enable HI_HI alarm (Azeo HI_HI_ENAB)"),
            "LO_EN": (bool, True, "Enable LO alarm (Azeo LO_ENAB)"),
            "LL_EN": (bool, True, "Enable LO_LO alarm (Azeo LO_LO_ENAB)"),
            "DEV_EN": (bool, False, "Enable deviation alarms"),
            "ROC_EN": (bool, False, "Enable rate-of-change alarm"),
            "SCALE_ENABLE": (bool, False, "IN is percent of IN_SCALE, not EU"),
            "IN_SCALE_LO": (float, 0.0, "IN_SCALE low [EU] (SCALE_ENABLE only)"),
            "IN_SCALE_HI": (float, 100.0, "IN_SCALE high [EU] (SCALE_ENABLE only)"),
            "DEFAULT": (float, 0.0, "PV used while IN is not connected"),
            "SHELVE_ACTION": (str, "CLEAR",
                              "SHELVE: CLEAR the latched states | HOLD them frozen"),
            "CONDALM_ENABLED": (bool, False,
                                "Conditional alarming: adds per-alarm delays "
                                "and deadbands"),
        }
        if self.config.params.get("CONDALM_ENABLED", False):
            # The manual adds these only when conditional alarming is enabled.
            for a in self._ALARMS:
                schema[f"{a}_DELAY_ON"] = (
                    float, 0.0, f"{a}: seconds the condition must persist [s]")
                schema[f"{a}_DELAY_OFF"] = (
                    float, 0.0, f"{a}: seconds _ACT stays true after it clears [s]")
                schema[f"{a}_ENAB_DELAY"] = (
                    float, 0.0, f"{a}: seconds _ACT is forced 0 after enabling [s]")
                schema[f"{a}_HYS"] = (
                    float, 0.0, f"{a}: deadband replacing ALARM_HYS (0 = ALARM_HYS)")
        return schema

    # ── helpers ──────────────────────────────────────────────────────
    def _pv(self, p) -> float:
        """PV in EU: IN (or DEFAULT when unconnected), scaled if enabled."""
        terminal = self.inputs["PV"]
        raw = (self.get_input("PV") if terminal.connected or terminal.forced
               else p.get("DEFAULT", 0.0))
        if p.get("SCALE_ENABLE", False):
            lo = p.get("IN_SCALE_LO", 0.0)
            hi = p.get("IN_SCALE_HI", 100.0)
            return lo + (raw / 100.0) * (hi - lo)
        return raw

    def _deadband(self, p) -> float:
        """ALARM_HYS in EU — % of IN_SCALE, capped at 50 % of span, when
        SCALE_ENABLE is on; taken as EU otherwise."""
        db = max(float(p.get("DEADBAND", 1.0) or 0.0), 0.0)
        if p.get("SCALE_ENABLE", False):
            span = abs(p.get("IN_SCALE_HI", 100.0) - p.get("IN_SCALE_LO", 0.0))
            return min(db, 50.0) / 100.0 * span
        return db

    def _hysteresis_eu(self, value: float, p) -> float:
        """Convert a conditional alarm's configured hysteresis to EU."""
        hys = max(float(value or 0.0), 0.0)
        if p.get("SCALE_ENABLE", False):
            span = abs(float(p.get("IN_SCALE_HI", 100.0))
                       - float(p.get("IN_SCALE_LO", 0.0)))
            return min(hys, 50.0) / 100.0 * span
        return hys

    def _clear_state(self):
        self._alarm_engine.reset()
        self._roc_active = False

    def _publish(self, act: dict, roc: bool):
        for a in self._ALARMS:
            self.set_output(self._ALARM_OUT[a], act[a])
        self.set_output("ROC", roc)
        self.set_output("ANY_ALARM", any(act.values()) or roc)

    # ── execute ──────────────────────────────────────────────────────
    def execute(self, dt: float):
        p = self.config.params
        pv = float(self._pv(p))
        sp = float(self.get_input("SP"))

        # Spec: PV status is the input's — Bad when IN is in use and Bad.
        self.set_output("PV", pv)
        self.set_output_status("PV", self.input_status("PV")
                               if self.inputs["PV"].connected else Quality.GOOD)

        if not self._initialized:
            self._prev_pv = pv
            self._initialized = True

        if not self.get_input("ENABLE"):
            # ENABLE False(0): alarming disabled, all _ACT fields cleared.
            self._clear_state()
            self._prev_pv = pv
            self._publish({a: False for a in self._ALARMS}, False)
            return

        condalm = bool(p.get("CONDALM_ENABLED", False))
        db = self._deadband(p)

        if self.get_input("SHELVE"):
            # HOLD freezes the standard-alarm and ROC latches while hidden;
            # CLEAR removes them.  Updating the derivative baseline in either
            # case prevents an artificial ROC alarm when shelving ends.
            if str(p.get("SHELVE_ACTION", "CLEAR")).upper() != "HOLD":
                self._clear_state()
            self._prev_pv = pv
            self._publish({a: False for a in self._ALARMS}, False)
            return

        def hys_for(name: str) -> float:
            if condalm and name in BASE_ANALOG_ALARMS:
                # Zero is a valid explicit conditional deadband; it does not
                # fall back to ALARM_HYS once conditional alarming is enabled.
                return self._hysteresis_eu(p.get(f"{name}_HYS", 0.0), p)
            if name in BASE_ANALOG_ALARMS:
                return db
            dv_hys = max(float(p.get("DV_HYS", 0.0) or 0.0), 0.0)
            return db if condalm else dv_hys

        def alarm_rule(name: str, limit: float, high: bool) -> AlarmRule:
            return AlarmRule(
                limit=float(limit),
                high=high,
                hysteresis=hys_for(name),
                enabled=bool(p.get(
                    self._ALARM_ENAB[name], self._ENAB_DEFAULT[name])),
                delay_on=float(p.get(f"{name}_DELAY_ON", 0.0) or 0.0),
                delay_off=float(p.get(f"{name}_DELAY_OFF", 0.0) or 0.0),
                enable_delay=float(
                    p.get(f"{name}_ENAB_DELAY", 0.0) or 0.0),
            )

        dev_lo = float(p.get("DEV_LO", 5.0))
        sign = str(p.get("DV_LO_SIGN", "AUTO")).upper()
        azeo_lo = sign == "AZEO" or (sign == "AUTO" and dev_lo < 0.0)
        lo_threshold = dev_lo if azeo_lo else -dev_lo
        rules = {
            "HI_HI": alarm_rule(
                "HI_HI", p.get("HI_HI_LIM", 95.0), True),
            "HI": alarm_rule("HI", p.get("HI_LIM", 90.0), True),
            "LO": alarm_rule("LO", p.get("LO_LIM", 10.0), False),
            "LO_LO": alarm_rule(
                "LO_LO", p.get("LO_LO_LIM", 5.0), False),
            "DV_HI": alarm_rule(
                "DV_HI", p.get("DEV_HI", 5.0), True),
            "DV_LO": alarm_rule("DV_LO", lo_threshold, False),
        }
        act = self._alarm_engine.evaluate(
            pv=pv,
            sp=sp,
            dt=dt,
            rules=rules,
            conditional=condalm,
        )

        # ── rate of change (platform extension, no Azeo equivalent) ─
        if p.get("ROC_EN", False):
            roc = abs(pv - self._prev_pv) / max(dt, 1e-6)
            roc_lim = p.get("ROC_LIM", 10.0)
            roc_hys = max(float(p.get("ROC_HYS", 0.0) or 0.0), 0.0)
            if roc > roc_lim:
                self._roc_active = True
            elif roc <= roc_lim - roc_hys:
                self._roc_active = False
        else:
            self._roc_active = False
        roc_active = self._roc_active
        self._prev_pv = pv

        self._publish(act, roc_active)

    def reset(self):
        super().reset()
        self._clear_state()
        self._prev_pv = 0.0
        self._initialized = False
