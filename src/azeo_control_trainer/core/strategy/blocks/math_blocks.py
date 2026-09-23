"""Math function blocks — Summer, Multiplier, Divider, Sqrt, Integrator, etc."""
from __future__ import annotations
import collections
import math
from ..model.block_base import FunctionBlock, BlockCategory, DataType
from ..model.block_registry import register_block
from .filter_blocks import TIME_UNIT_SECONDS


#: Azeo extensible-parameter cap for ADD / MLTY (2..16 inputs).
MAX_EXT_INPUTS = 16


def _extend_inputs(block, first: int, last: int, default: float) -> None:
    """Add ``IN{first}``..``IN{last}`` connectors, dropping unwired extras.

    Mirrors Azeo's extensible-parameter behaviour: raising the input
    count adds connectors, lowering it removes the ones nothing is wired to
    (a wired terminal is always kept so the wire cannot lose its pin).
    """
    for i in range(first, last + 1):
        if f"IN{i}" not in block.inputs:
            block.add_input(f"IN{i}", default=default, description=f"Input {i}")
    for i in range(last + 1, MAX_EXT_INPUTS + 1):
        term = block.inputs.get(f"IN{i}")
        if term is not None and not term.connected:
            del block.inputs[f"IN{i}"]


@register_block
class SummerBlock(FunctionBlock):
    """Weighted sum (Azeo ADD, extended with gains, bias and limiting).

    ``OUT = G1*IN1 + … + Gn*INn + BIAS``, optionally clamped to
    ``OUT_LO``..``OUT_HI`` when ``CLAMP`` is set (``LIMITED`` reports it).

    ``N_INPUTS`` mirrors Azeo's extensible input count: raise it above 4
    and IN5…IN16 appear, each summed with unit weight by default. The
    original four keep their historical gain defaults (G1 = G2 = 1,
    G3 = G4 = 0), so an unset G3/G4 drops a wired IN3/IN4 — set
    ``AUTO_GAIN`` to treat an unset gain on a *connected* input as 1.0
    (Azeo ADD semantics).
    """
    block_type = "SUMMER"
    category = BlockCategory.MATH
    display_name = "Summer"
    description = "OUT = G1*IN1 + G2*IN2 + G3*IN3 + G4*IN4 + BIAS"

    #: Historical gain defaults for the four base inputs.
    _BASE_GAINS = {1: 1.0, 2: 1.0, 3: 0.0, 4: 0.0}

    def _define_terminals(self):
        for i in range(1, 5):
            self.add_input(f"IN{i}", description=f"Input {i}")
        self.add_output("OUT", description="Sum output")
        self.add_output("LIMITED", DataType.BOOL, False, "OUT is at a limit")

    config_aliases = {
        "gain1": "G1", "gain2": "G2", "gain3": "G3", "gain4": "G4",
        "gain_1": "G1", "gain_2": "G2", "gain_3": "G3", "gain_4": "G4",
        "bias": "BIAS",
        "out_lo": "OUT_LO", "out_hi": "OUT_HI", "clamp": "CLAMP",
    }

    def _n_inputs(self) -> int:
        try:
            n = int(self.config.params.get("N_INPUTS", 4))
        except (TypeError, ValueError):
            n = 4
        return min(max(n, 2), MAX_EXT_INPUTS)

    def _apply_config(self):
        # Azeo "Extensible Parameters": adding inputs adds connectors.
        _extend_inputs(self, 5, self._n_inputs(), 0.0)

    def _gain_default(self, i: int) -> float:
        return self._BASE_GAINS.get(i, 1.0)

    def get_config_schema(self):
        schema = {
            "N_INPUTS": (int, 4, f"Number of inputs (2-{MAX_EXT_INPUTS}, Azeo extensible)"),
        }
        for i in range(1, max(self._n_inputs(), 4) + 1):
            schema[f"G{i}"] = (float, self._gain_default(i), f"Gain on IN{i}")
        schema.update({
            "BIAS": (float, 0.0, "Output bias"),
            "AUTO_GAIN": (bool, False, "Treat an unset gain on a connected input as 1.0"),
            "CLAMP": (bool, False, "Limit OUT to OUT_LO..OUT_HI"),
            "OUT_LO": (float, 0.0, "Output low limit (Azeo OUT_LO_LIM)"),
            "OUT_HI": (float, 100.0, "Output high limit (Azeo OUT_HI_LIM)"),
        })
        return schema

    def _sum_extended(self, p, ins) -> float:
        """General weighted sum — extended inputs and/or AUTO_GAIN."""
        auto = p.get("AUTO_GAIN", False)
        out = p.get("BIAS", 0.0)
        for i in range(1, len(ins) + 1):
            term = ins.get(f"IN{i}")
            if term is None:
                continue
            key = f"G{i}"
            if key in p:
                gain = p[key]
            elif auto and term.connected:
                gain = 1.0
            else:
                gain = self._gain_default(i)
            out += gain * term.value
        return out

    def execute(self, dt: float):
        p = self.config.params
        ins = self.inputs
        # Hot path: the stock four inputs with explicit gains (89 shipped
        # instances). The general form only runs for extended/AUTO_GAIN blocks.
        if len(ins) > 4 or p.get("AUTO_GAIN", False):
            out = self._sum_extended(p, ins)
        else:
            out = (p.get("G1", 1.0) * ins["IN1"].value +
                   p.get("G2", 1.0) * ins["IN2"].value +
                   p.get("G3", 0.0) * ins["IN3"].value +
                   p.get("G4", 0.0) * ins["IN4"].value +
                   p.get("BIAS", 0.0))

        limited = False
        if p.get("CLAMP", False):
            lo = p.get("OUT_LO", 0.0)
            hi = p.get("OUT_HI", 100.0)
            clamped = max(lo, min(hi, out))
            limited = clamped != out
            out = clamped
        self.set_output("OUT", out)
        self.set_output("LIMITED", limited)


@register_block
class MultiplierBlock(FunctionBlock):
    """Product of 2–16 inputs (Azeo MLTY) with an extra output ``GAIN``.

    ``N_INPUTS`` mirrors Azeo's extensible input count; IN3…IN16 appear
    when it is raised above 2 and each defaults to 1.0, the multiplicative
    identity, so an unwired extra input never zeroes the product.
    """
    block_type = "MULTIPLIER"
    category = BlockCategory.MATH
    display_name = "Multiplier"
    description = "OUT = IN1 * IN2 * GAIN"

    def _define_terminals(self):
        self.add_input("IN1", description="Input 1")
        self.add_input("IN2", default=1.0, description="Input 2")
        self.add_output("OUT", description="Product output")

    def _n_inputs(self) -> int:
        try:
            n = int(self.config.params.get("N_INPUTS", 2))
        except (TypeError, ValueError):
            n = 2
        return min(max(n, 2), MAX_EXT_INPUTS)

    def _apply_config(self):
        # Extra inputs default to 1.0 — the multiplicative identity.
        _extend_inputs(self, 3, self._n_inputs(), 1.0)

    def get_config_schema(self):
        return {
            "GAIN": (float, 1.0, "Output gain"),
            "N_INPUTS": (int, 2, f"Number of inputs (2-{MAX_EXT_INPUTS}, Azeo extensible)"),
        }

    def execute(self, dt: float):
        ins = self.inputs
        out = ins["IN1"].value * ins["IN2"].value * self.config.params.get("GAIN", 1.0)
        if len(ins) > 2:                       # extended inputs only
            for i in range(3, len(ins) + 1):
                term = ins.get(f"IN{i}")
                if term is not None:
                    out *= term.value
        self.set_output("OUT", out)


@register_block
class DividerBlock(FunctionBlock):
    """Quotient of two inputs (Azeo DIV).

    Divide-by-zero returns ±3.40282e38 with the sign of the numerator.
    ``ZERO_TOL`` is the magnitude below which the divisor counts as zero;
    set it to 0.0 for strict spec parity (only an exact 0 is special-cased).
    """
    block_type = "DIVIDER"
    category = BlockCategory.MATH
    display_name = "Divider"
    description = "OUT = IN_1 / IN_2 (Azeo-style)"

    # Azeo returns max float on divide-by-zero
    _MAX_FLOAT = 3.40282e+038

    def _define_terminals(self):
        self.add_input("IN_1", description="Numerator")
        self.add_input("IN_2", default=1.0, description="Denominator")
        self.add_output("OUT", description="Quotient")

    def get_config_schema(self):
        return {
            "ZERO_TOL": (float, 1e-12,
                         "Divisor magnitude treated as zero (0.0 = exact-zero only)"),
        }

    def execute(self, dt: float):
        num = self.get_input("IN_1")
        den = self.get_input("IN_2")
        tol = self.config.params.get("ZERO_TOL", 1e-12)
        is_zero = (abs(den) < tol) if tol > 0.0 else (den == 0.0)
        if is_zero:
            # Azeo convention: return ±max float on divide-by-zero
            sign = 1.0 if num >= 0.0 else -1.0
            self.set_output("OUT", sign * self._MAX_FLOAT)
        else:
            self.set_output("OUT", num / den)


@register_block
class AbsBlock(FunctionBlock):
    """Absolute Value — OUT = |IN|."""
    block_type = "ABS"
    category = BlockCategory.MATH
    display_name = "Absolute Value"
    description = "OUT = |IN|"

    def _define_terminals(self):
        self.add_input("IN", description="Input value")
        self.add_output("OUT", description="Absolute value")

    def execute(self, dt: float):
        self.set_output("OUT", abs(self.get_input("IN")))


@register_block
class SubtractBlock(FunctionBlock):
    """Subtract — OUT = IN1 - IN2 (Azeo SUB: IN_1 - IN_2).

    The Azeo spellings ``IN_1``/``IN_2`` are accepted as wiring aliases
    for ``IN1``/``IN2`` (DIVIDER in this same file uses the underscored
    names, so both conventions resolve here).
    """
    block_type = "SUB"
    category = BlockCategory.MATH
    display_name = "Subtract"
    description = "OUT = IN1 - IN2"

    terminal_aliases = {"IN_1": "IN1", "IN_2": "IN2"}

    def _define_terminals(self):
        self.add_input("IN1", description="Minuend")
        self.add_input("IN2", description="Subtrahend")
        self.add_output("OUT", description="Difference")

    def execute(self, dt: float):
        self.set_output("OUT", self.get_input("IN1") - self.get_input("IN2"))


# ──────────────────────────────────────────────────────────────────
# Tier 1 — Critical math blocks
# ──────────────────────────────────────────────────────────────────

@register_block
class SqrtBlock(FunctionBlock):
    """Square root with low-cutoff (Honeywell SQRT).

    Used for DP flow measurement: flow ∝ √(ΔP).
    Below LOW_CUTOFF the output is zero to avoid noise at low flows.
    """
    block_type = "SQRT"
    category = BlockCategory.MATH
    display_name = "Square Root"
    description = "OUT = √IN with low-cutoff for flow measurement"

    def _define_terminals(self):
        self.add_input("IN", description="Input (e.g. differential pressure)")
        self.add_output("OUT", description="Square root output")
        self.add_output("LO_CUT", DataType.BOOL, False, "Below low-cutoff")

    def get_config_schema(self):
        return {
            "LOW_CUTOFF": (float, 1.0, "Low cutoff % (below this, OUT=0)"),
            "IN_LO": (float, 0.0, "Input range low"),
            "IN_HI": (float, 100.0, "Input range high"),
        }

    def execute(self, dt: float):
        p = self.config.params
        inp = self.get_input("IN")
        in_lo = p.get("IN_LO", 0.0)
        in_hi = p.get("IN_HI", 100.0)
        span = in_hi - in_lo
        cutoff = p.get("LOW_CUTOFF", 1.0)

        if abs(span) < 1e-12:
            self.set_output("OUT", 0.0)
            self.set_output("LO_CUT", True)
            return

        pct = (inp - in_lo) / span * 100.0
        if pct < cutoff:
            self.set_output("OUT", 0.0)
            self.set_output("LO_CUT", True)
        else:
            normalized = max(0.0, pct / 100.0)
            out_pct = math.sqrt(normalized) * 100.0
            self.set_output("OUT", in_lo + out_pct / 100.0 * span)
            self.set_output("LO_CUT", False)


#: Azeo INTEG_TYPE named set (spec §Integrator).
INTEG_TYPES = (
    "0_TO_SP_AUTO",              # 0 → SP, auto reset at SP
    "0_TO_SP_DEMAND",            # 0 → SP, demand reset
    "SP_TO_0_AUTO",              # SP → 0, auto reset  (OUT = SP − TOTAL)
    "SP_TO_0_DEMAND",            # SP → 0, demand reset
    "0_TO_INF_PERIODIC",         # 0 → ∞, periodic reset (CLOCK_PER)
    "0_TO_INF_DEMAND",           # 0 → ∞, demand reset  ← default = legacy
    "0_TO_INF_PERIODIC_DEMAND",  # 0 → ∞, periodic and demand reset
)


@register_block
class IntegratorBlock(FunctionBlock):
    """Integrator / totalizer (Azeo INT).

    ``TOTAL[t] = TOTAL[t-1] + (x + y)·dt`` where ``x = IN·GAIN/TIME_UNIT1``
    and ``y = IN_2·UNIT_CONV/TIME_UNIT2`` (0 unless ``ENABLE_IN_2``).
    ``REV_FLOW1``/``REV_FLOW2`` force an input negative; the ``FLOW_FORWARD``
    / ``FLOW_REVERSE`` options select which increments are integrated.

    ``INTEG_TYPE`` picks one of the spec's seven count/reset behaviours; the
    default ``0_TO_INF_DEMAND`` is a free-running total with a level-sensitive
    ``RESET`` — the block's historical behaviour. The two ``SP_TO_0`` types
    output ``SP − TOTAL``; the two ``_AUTO`` types reset themselves at SP
    (carrying the excess forward when ``CARRY`` is set).

    ``PRE_TRIP`` (0 disables) sets ``OUT_PTRIP`` when the value comes within
    PRE_TRIP of the trip target; ``OUT_TRIP`` latches at the target for 5 s
    or one scan, whichever is longer. ``N_RESET`` counts resets.

    ``METHOD`` stays trapezoidal by default; ``RECT`` is the Azeo-parity
    setting (the spec's increment uses the current sample only) — on a 0→90
    ramp over 10 s the two differ by 10 % of the accumulated total.
    """
    block_type = "INTEGRATOR"
    category = BlockCategory.MATH
    display_name = "Integrator"
    description = "Time integration: OUT += IN × dt"

    terminal_aliases = {"IN_1": "IN", "RESET_IN": "RESET"}
    config_choices = {
        "METHOD": ("TRAP", "RECT"),
        "INTEG_TYPE": INTEG_TYPES,
        "TIME_UNIT1": tuple(TIME_UNIT_SECONDS),
        "TIME_UNIT2": tuple(TIME_UNIT_SECONDS),
    }
    config_units = {"CLOCK_PER": "s"}

    #: Spec: OUT_TRIP latches for 5 seconds or one scan, whichever is greater.
    TRIP_LATCH_S = 5.0

    def __init__(self, instance_name=""):
        self._accumulator = 0.0
        self._prev_in = 0.0
        self._prev_in2 = 0.0
        self._n_reset = 0
        self._since_reset = 0.0
        self._ptrip = False
        self._trip = False
        self._trip_left = 0.0
        self._was_tripped = False
        self._prev_cmd = False
        self._prev_reset = False
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("IN", description="Input to integrate (Azeo IN_1)")
        self.add_input("RESET", DataType.BOOL, False, "Reset accumulator to INIT")
        self.add_input("HOLD", DataType.BOOL, False, "Hold (freeze output)")
        self.add_input("IN_2", description="Second input (net / reverse flow)")
        self.add_input("ENABLE_IN_2", DataType.BOOL, False, "Include IN_2 in the total")
        self.add_input("REV_FLOW1", DataType.BOOL, False, "Treat IN as negative")
        self.add_input("REV_FLOW2", DataType.BOOL, False, "Treat IN_2 as negative")
        self.add_input("OP_CMD_INT", DataType.BOOL, False, "Operator reset command")
        self.add_output("OUT", description="Integrated output")
        self.add_output("TOTAL", description="Raw accumulator (before SP−TOTAL)")
        self.add_output("HI_LIM", DataType.BOOL, False, "At high limit")
        self.add_output("LO_LIM", DataType.BOOL, False, "At low limit")
        self.add_output("OUT_PTRIP", DataType.BOOL, False, "Within PRE_TRIP of the target")
        self.add_output("OUT_TRIP", DataType.BOOL, False, "Trip target reached")
        self.add_output("N_RESET", DataType.INT, 0, "Number of resets")

    def get_config_schema(self):
        return {
            "INIT": (float, 0.0, "Initial/reset value"),
            "HI_LIMIT": (float, 1e6, "High output limit"),
            "LO_LIMIT": (float, -1e6, "Low output limit"),
            "METHOD": (str, "TRAP", "Integration method: RECT (Azeo capability) or TRAP"),
            "GAIN": (float, 1.0, "Integration gain"),
            "INTEG_TYPE": (str, "0_TO_INF_DEMAND", "Count/reset behaviour (7 Azeo types)"),
            "SP": (float, 0.0, "Trip target / count-down start"),
            "PRE_TRIP": (float, 0.0, "Distance before the trip target (0 = disabled)"),
            "CLOCK_PER": (float, 3600.0, "Periodic reset period [s]"),
            "UNIT_CONV": (float, 1.0, "Scales IN_2 units to IN units (no time factor)"),
            "TIME_UNIT1": (str, "SECONDS", "IN rate time base"),
            "TIME_UNIT2": (str, "SECONDS", "IN_2 rate time base"),
            "FLOW_FORWARD": (bool, True, "Integrate positive increments"),
            "FLOW_REVERSE": (bool, True, "Integrate negative increments"),
            "CARRY": (bool, False, "Auto-reset types carry the excess past SP forward"),
        }

    @staticmethod
    def _unit(name) -> float:
        return TIME_UNIT_SECONDS.get(str(name).upper(), 1.0)

    def _increment(self, val, prev, dt, method, fwd, rev) -> float:
        inc = (val + prev) / 2.0 * dt if method == "TRAP" else val * dt
        if inc > 0.0 and not fwd:
            return 0.0
        if inc < 0.0 and not rev:
            return 0.0
        return inc

    def execute(self, dt: float):
        p = self.config.params
        itype = str(p.get("INTEG_TYPE", "0_TO_INF_DEMAND")).upper()
        method = str(p.get("METHOD", "TRAP")).upper()
        sp = p.get("SP", 0.0)
        pre_trip = p.get("PRE_TRIP", 0.0)

        x = self.get_input("IN") * p.get("GAIN", 1.0) / self._unit(p.get("TIME_UNIT1", "SECONDS"))
        if self.get_input("REV_FLOW1"):
            x = -abs(x)
        if self.get_input("ENABLE_IN_2"):
            y = (self.get_input("IN_2") * p.get("UNIT_CONV", 1.0)
                 / self._unit(p.get("TIME_UNIT2", "SECONDS")))
            if self.get_input("REV_FLOW2"):
                y = -abs(y)
        else:
            y = 0.0

        # ── reset sources ────────────────────────────────────────────
        cmd = bool(self.get_input("OP_CMD_INT"))
        cmd_edge = cmd and not self._prev_cmd
        self._prev_cmd = cmd
        periodic = itype in ("0_TO_INF_PERIODIC", "0_TO_INF_PERIODIC_DEMAND")
        demand_ok = itype != "0_TO_INF_PERIODIC"
        self._since_reset += dt

        do_reset = cmd_edge or (demand_ok and bool(self.get_input("RESET")))
        if periodic and self._since_reset > max(p.get("CLOCK_PER", 3600.0), 1e-9):
            do_reset = True

        if do_reset:
            # N_RESET counts initializations, not scans — a level-held
            # RESET is one reset that persists until released.
            self._do_reset(p.get("INIT", 0.0), x, y, count=not self._prev_reset)
            self._prev_reset = True
        else:
            self._prev_reset = False
        if not do_reset and not self.get_input("HOLD"):
            fwd = p.get("FLOW_FORWARD", True)
            rev = p.get("FLOW_REVERSE", True)
            self._accumulator += self._increment(x, self._prev_in, dt, method, fwd, rev)
            if self.get_input("ENABLE_IN_2"):
                self._accumulator += self._increment(
                    y, self._prev_in2, dt, method, fwd, rev)
            self._prev_in = x
            self._prev_in2 = y

        hi = p.get("HI_LIMIT", 1e6)
        lo = p.get("LO_LIMIT", -1e6)
        hi_lim = self._accumulator >= hi
        lo_lim = self._accumulator <= lo
        self._accumulator = max(lo, min(hi, self._accumulator))

        # ── trip / pre-trip (SP-based types only) ────────────────────
        countdown = itype in ("SP_TO_0_AUTO", "SP_TO_0_DEMAND")
        sp_based = itype.startswith("0_TO_SP") or countdown
        if sp_based and not do_reset:
            # Both directions reduce to the same comparison: for count-down
            # OUT = SP − TOTAL, so "OUT reaches 0" is "TOTAL reaches SP".
            remaining = sp - self._accumulator
            tripped_now = sp >= 0.0 and self._accumulator >= sp
            # OUT_PTRIP arms on approach and unlatches when OUT_TRIP latches;
            # it stays clear past the target until the integrator is reset.
            if (pre_trip > 0.0 and sp >= 0.0 and pre_trip >= remaining
                    and not self._trip and not self._was_tripped
                    and not tripped_now):
                self._ptrip = True
            if tripped_now and not self._was_tripped:
                self._trip = True
                self._trip_left = max(self.TRIP_LATCH_S, dt)
                self._ptrip = False
                if itype.endswith("_AUTO"):
                    carry = (self._accumulator - sp) if p.get("CARRY", False) else 0.0
                    self._do_reset(p.get("INIT", 0.0) + carry, x, y)
                    tripped_now = False
            self._was_tripped = tripped_now
        if self._trip:
            self._trip_left -= dt
            if self._trip_left <= 0.0:
                self._trip = False

        out = (sp - self._accumulator) if countdown else self._accumulator

        self.set_output("OUT", out)
        self.set_output("TOTAL", self._accumulator)
        self.set_output("HI_LIM", hi_lim)
        self.set_output("LO_LIM", lo_lim)
        self.set_output("OUT_PTRIP", self._ptrip)
        self.set_output("OUT_TRIP", self._trip)
        self.set_output("N_RESET", self._n_reset)

    def _do_reset(self, value: float, x: float, y: float, count: bool = True):
        self._accumulator = value
        self._prev_in = x
        self._prev_in2 = y
        self._since_reset = 0.0
        self._ptrip = False
        self._was_tripped = False
        if count:
            self._n_reset += 1

    def reset(self):
        super().reset()
        self._accumulator = 0.0
        self._prev_in = 0.0
        self._prev_in2 = 0.0
        self._n_reset = 0
        self._since_reset = 0.0
        self._ptrip = False
        self._trip = False
        self._trip_left = 0.0
        self._was_tripped = False
        self._prev_cmd = False
        self._prev_reset = False


@register_block
class DerivativeBlock(FunctionBlock):
    """Rate of change / derivative block (Honeywell RATE).

    OUT = d(IN)/dt with optional filtering to reduce noise.
    """
    block_type = "DERIVATIVE"
    category = BlockCategory.MATH
    display_name = "Derivative"
    description = "Rate of change: OUT = d(IN)/dt"

    def __init__(self, instance_name=""):
        self._prev_in = 0.0
        self._prev_out = 0.0
        self._initialized = False
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("IN", description="Input signal")
        self.add_output("OUT", description="Rate of change [units/s]")

    def get_config_schema(self):
        return {
            "TAU": (float, 1.0, "Filter time constant [s] (0=unfiltered)"),
            "GAIN": (float, 1.0, "Output gain"),
        }

    def execute(self, dt: float):
        p = self.config.params
        inp = self.get_input("IN")

        if not self._initialized:
            self._prev_in = inp
            self._prev_out = 0.0
            self._initialized = True
            self.set_output("OUT", 0.0)
            return

        raw = (inp - self._prev_in) / max(dt, 1e-6) * p.get("GAIN", 1.0)
        self._prev_in = inp

        tau = max(p.get("TAU", 1.0), 0.0)
        if tau > 0.001:
            alpha = dt / (tau + dt)
            self._prev_out = alpha * raw + (1.0 - alpha) * self._prev_out
        else:
            self._prev_out = raw

        self.set_output("OUT", self._prev_out)

    def reset(self):
        super().reset()
        self._prev_in = 0.0
        self._prev_out = 0.0
        self._initialized = False


@register_block
class TotalizerBlock(FunctionBlock):
    """Flow totalizer (Honeywell TOTLZR).

    Accumulates flow rate over time. OUT = Σ(FLOW × dt).
    Supports resettable and non-resettable totals.
    """
    block_type = "TOTALIZER"
    category = BlockCategory.MATH
    display_name = "Totalizer"
    description = "Flow totalization: OUT = Σ(IN × dt)"

    def __init__(self, instance_name=""):
        self._total = 0.0
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("IN", description="Flow rate input")
        self.add_input("RESET", DataType.BOOL, False, "Reset total")
        self.add_input("ENABLE", DataType.BOOL, True, "Enable totalization")
        self.add_output("OUT", description="Accumulated total")
        self.add_output("RATE", description="Current rate (pass-through)")

    def get_config_schema(self):
        return {
            "FACTOR": (float, 1.0, "Conversion factor (e.g. units/hr to units/s)"),
            "HI_LIMIT": (float, 1e9, "Total high limit (rollover)"),
        }

    def execute(self, dt: float):
        if self.get_input("RESET"):
            self._total = 0.0

        if self.get_input("ENABLE"):
            rate = self.get_input("IN") * self.config.params.get("FACTOR", 1.0)
            self._total += rate * dt

        hi = self.config.params.get("HI_LIMIT", 1e9)
        if self._total > hi:
            self._total -= hi  # rollover

        self.set_output("OUT", self._total)
        self.set_output("RATE", self.get_input("IN"))

    def reset(self):
        super().reset()
        self._total = 0.0


# ──────────────────────────────────────────────────────────────────
# Tier 2 — Advanced math blocks
# ──────────────────────────────────────────────────────────────────

@register_block
class PolyBlock(FunctionBlock):
    """Polynomial calculation (Honeywell POLYC).

    OUT = a0 + a1*x + a2*x² + a3*x³ + a4*x⁴ + a5*x⁵
    """
    block_type = "POLY"
    category = BlockCategory.MATH
    display_name = "Polynomial"
    description = "OUT = a0 + a1·x + a2·x² + ... + a5·x⁵"

    def _define_terminals(self):
        self.add_input("IN", description="Input variable (x)")
        self.add_output("OUT", description="Polynomial result")

    def get_config_schema(self):
        return {
            "A0": (float, 0.0, "Constant term"),
            "A1": (float, 1.0, "Linear coefficient"),
            "A2": (float, 0.0, "Quadratic coefficient"),
            "A3": (float, 0.0, "Cubic coefficient"),
            "A4": (float, 0.0, "4th order coefficient"),
            "A5": (float, 0.0, "5th order coefficient"),
        }

    def execute(self, dt: float):
        p = self.config.params
        x = self.get_input("IN")
        out = (p.get("A0", 0.0) +
               p.get("A1", 1.0) * x +
               p.get("A2", 0.0) * x**2 +
               p.get("A3", 0.0) * x**3 +
               p.get("A4", 0.0) * x**4 +
               p.get("A5", 0.0) * x**5)
        self.set_output("OUT", out)


@register_block
class LogExpBlock(FunctionBlock):
    """Logarithm / Exponential (Honeywell LOG/EXPN).

    MODE=LN: OUT = ln(IN), MODE=LOG10: OUT = log10(IN),
    MODE=EXP: OUT = e^IN, MODE=POW10: OUT = 10^IN
    """
    block_type = "LOG_EXP"
    category = BlockCategory.MATH
    display_name = "Log / Exp"
    description = "Logarithm (ln, log10) and exponential (e^x, 10^x)"

    def _define_terminals(self):
        self.add_input("IN", description="Input value")
        self.add_output("OUT", description="Result")
        self.add_output("ERROR", DataType.BOOL, False, "Math error (e.g. log of negative)")

    def get_config_schema(self):
        return {
            "MODE": (str, "LN", "Function: LN, LOG10, EXP, POW10"),
        }

    def execute(self, dt: float):
        mode = self.config.params.get("MODE", "LN").upper()
        inp = self.get_input("IN")
        err = False
        try:
            if mode == "LN":
                out = math.log(max(inp, 1e-30))
                err = inp <= 0
            elif mode == "LOG10":
                out = math.log10(max(inp, 1e-30))
                err = inp <= 0
            elif mode == "EXP":
                out = math.exp(min(inp, 700))
            elif mode == "POW10":
                out = 10.0 ** min(inp, 300)
            else:
                out = inp
        except (ValueError, OverflowError):
            out = 0.0
            err = True
        self.set_output("OUT", out)
        self.set_output("ERROR", err)


@register_block
class PowerBlock(FunctionBlock):
    """Power function (Honeywell POWER). OUT = BASE ^ EXPONENT."""
    block_type = "POWER"
    category = BlockCategory.MATH
    display_name = "Power"
    description = "OUT = BASE ^ EXPONENT"

    def _define_terminals(self):
        self.add_input("BASE", description="Base value")
        self.add_input("EXPONENT", default=2.0, description="Exponent")
        self.add_output("OUT", description="Result")
        self.add_output("ERROR", DataType.BOOL, False, "Math error")

    def execute(self, dt: float):
        base = self.get_input("BASE")
        exp = self.get_input("EXPONENT")
        try:
            out = base ** exp
            if math.isnan(out) or math.isinf(out):
                raise ValueError
            self.set_output("OUT", out)
            self.set_output("ERROR", False)
        except (ValueError, OverflowError, ZeroDivisionError):
            self.set_output("OUT", 0.0)
            self.set_output("ERROR", True)


@register_block
class TrigBlock(FunctionBlock):
    """Trigonometric functions (Honeywell TRIG).

    MODE: SIN, COS, TAN, ASIN, ACOS, ATAN, ATAN2
    Input in degrees or radians based on UNIT config.
    """
    block_type = "TRIG"
    category = BlockCategory.MATH
    display_name = "Trigonometry"
    description = "Trig functions: sin, cos, tan, asin, acos, atan, atan2"

    def _define_terminals(self):
        self.add_input("IN", description="Input angle / value")
        self.add_input("IN2", description="Second input (for ATAN2)")
        self.add_output("OUT", description="Result")

    def get_config_schema(self):
        return {
            "MODE": (str, "SIN", "Function: SIN, COS, TAN, ASIN, ACOS, ATAN, ATAN2"),
            "UNIT": (str, "DEG", "Angle unit: DEG or RAD"),
        }

    def execute(self, dt: float):
        p = self.config.params
        mode = p.get("MODE", "SIN").upper()
        deg = p.get("UNIT", "DEG").upper() == "DEG"
        inp = self.get_input("IN")

        if mode in ("SIN", "COS", "TAN"):
            angle = math.radians(inp) if deg else inp
            if mode == "SIN":
                out = math.sin(angle)
            elif mode == "COS":
                out = math.cos(angle)
            else:
                out = math.tan(angle)
        elif mode == "ASIN":
            out = math.asin(max(-1, min(1, inp)))
            if deg:
                out = math.degrees(out)
        elif mode == "ACOS":
            out = math.acos(max(-1, min(1, inp)))
            if deg:
                out = math.degrees(out)
        elif mode == "ATAN":
            out = math.atan(inp)
            if deg:
                out = math.degrees(out)
        elif mode == "ATAN2":
            out = math.atan2(inp, self.get_input("IN2"))
            if deg:
                out = math.degrees(out)
        else:
            out = inp

        self.set_output("OUT", out)


@register_block
class FlowCompBlock(FunctionBlock):
    """Compensated flow calculation (Honeywell COMP/MASSFL).

    Corrects volumetric flow for pressure and temperature:
    FLOW_COMP = FLOW_RAW × √((P_actual / P_design) × (T_design / T_actual))
    """
    block_type = "FLOW_COMP"
    category = BlockCategory.MATH
    display_name = "Flow Compensator"
    description = "Pressure/temperature compensated flow calculation"

    def _define_terminals(self):
        self.add_input("FLOW", description="Raw (uncompensated) flow")
        self.add_input("PRESSURE", description="Actual pressure")
        self.add_input("TEMPERATURE", description="Actual temperature")
        self.add_output("OUT", description="Compensated flow")

    def get_config_schema(self):
        return {
            "P_DESIGN": (float, 101.325, "Design pressure [kPa]"),
            "T_DESIGN": (float, 293.15, "Design temperature [K]"),
            "COMP_TYPE": (str, "PT", "Compensation: P, T, or PT"),
        }

    def execute(self, dt: float):
        p = self.config.params
        flow = self.get_input("FLOW")
        comp = p.get("COMP_TYPE", "PT").upper()
        factor = 1.0

        if "P" in comp:
            p_des = max(p.get("P_DESIGN", 101.325), 1e-6)
            p_act = max(self.get_input("PRESSURE"), 1e-6)
            factor *= p_act / p_des

        if "T" in comp:
            t_des = max(p.get("T_DESIGN", 293.15), 1.0)
            t_act = max(self.get_input("TEMPERATURE"), 1.0)
            factor *= t_des / t_act

        self.set_output("OUT", flow * math.sqrt(max(factor, 0.0)))


@register_block
class StatisticsBlock(FunctionBlock):
    """Statistical calculation block (Honeywell STATS).

    Computes mean, standard deviation, min, max over a rolling window.
    """
    block_type = "STATISTICS"
    category = BlockCategory.MATH
    display_name = "Statistics"
    description = "Mean, std dev, min, max over N-sample window"

    def __init__(self, instance_name=""):
        self._samples: collections.deque = collections.deque(maxlen=100)
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("IN", description="Input signal")
        self.add_input("RESET", DataType.BOOL, False, "Reset statistics")
        self.add_output("MEAN", description="Average")
        self.add_output("STD_DEV", description="Standard deviation")
        self.add_output("MIN", description="Minimum value")
        self.add_output("MAX", description="Maximum value")
        self.add_output("COUNT", DataType.INT, 0, "Sample count")

    def get_config_schema(self):
        return {
            "N": (int, 100, "Window size"),
        }

    def execute(self, dt: float):
        n = max(int(self.config.params.get("N", 100)), 1)
        if n > 65536:
            raise ValueError("Statistics window exceeds 65536 samples")

        if self.get_input("RESET"):
            self._samples.clear()

        if self._samples.maxlen != n:
            old = list(self._samples)
            self._samples = collections.deque(old[-n:], maxlen=n)

        self._samples.append(self.get_input("IN"))
        count = len(self._samples)

        if count == 0:
            self.set_output("MEAN", 0.0)
            self.set_output("STD_DEV", 0.0)
            self.set_output("MIN", 0.0)
            self.set_output("MAX", 0.0)
        else:
            s = list(self._samples)
            mean = sum(s) / count
            if count > 1:
                variance = sum((x - mean) ** 2 for x in s) / (count - 1)
                std_dev = math.sqrt(variance)
            else:
                std_dev = 0.0
            self.set_output("MEAN", mean)
            self.set_output("STD_DEV", std_dev)
            self.set_output("MIN", min(s))
            self.set_output("MAX", max(s))

        self.set_output("COUNT", count)

    def reset(self):
        super().reset()
        self._samples.clear()


@register_block
class LookupBlock(FunctionBlock):
    """Table lookup with linear interpolation (Honeywell LOOKUP/INTERP).

    Up to 10 (X, Y) points. Linearly interpolates between breakpoints.
    """
    block_type = "LOOKUP"
    category = BlockCategory.MATH
    display_name = "Lookup Table"
    description = "Table lookup with linear interpolation (up to 10 points)"

    def _define_terminals(self):
        self.add_input("IN", description="Lookup input (X)")
        self.add_output("OUT", description="Interpolated output (Y)")

    def get_config_schema(self):
        return {
            "N_POINTS": (int, 5, "Number of points (2-10)"),
            "X_1": (float, 0.0, "X1"), "Y_1": (float, 0.0, "Y1"),
            "X_2": (float, 25.0, "X2"), "Y_2": (float, 10.0, "Y2"),
            "X_3": (float, 50.0, "X3"), "Y_3": (float, 50.0, "Y3"),
            "X_4": (float, 75.0, "X4"), "Y_4": (float, 90.0, "Y4"),
            "X_5": (float, 100.0, "X5"), "Y_5": (float, 100.0, "Y5"),
            "X_6": (float, 0.0, "X6"), "Y_6": (float, 0.0, "Y6"),
            "X_7": (float, 0.0, "X7"), "Y_7": (float, 0.0, "Y7"),
            "X_8": (float, 0.0, "X8"), "Y_8": (float, 0.0, "Y8"),
            "X_9": (float, 0.0, "X9"), "Y_9": (float, 0.0, "Y9"),
            "X_10": (float, 0.0, "X10"), "Y_10": (float, 0.0, "Y10"),
        }

    def execute(self, dt: float):
        p = self.config.params
        n = min(max(int(p.get("N_POINTS", 5)), 2), 10)
        xs = [p.get(f"X_{i+1}", 0.0) for i in range(n)]
        ys = [p.get(f"Y_{i+1}", 0.0) for i in range(n)]
        inp = self.get_input("IN")

        if inp <= xs[0]:
            self.set_output("OUT", ys[0])
        elif inp >= xs[-1]:
            self.set_output("OUT", ys[-1])
        else:
            for i in range(n - 1):
                if xs[i] <= inp <= xs[i + 1]:
                    span = xs[i + 1] - xs[i]
                    frac = (inp - xs[i]) / max(span, 1e-12)
                    self.set_output("OUT", ys[i] + frac * (ys[i + 1] - ys[i]))
                    break
            else:
                self.set_output("OUT", ys[-1])


@register_block
class BtuCalcBlock(FunctionBlock):
    """BTU/energy calculation (Honeywell BTUCLC).

    Q = FLOW × Cp × (T_OUT - T_IN) × FACTOR
    For heat exchanger / fired heater duty calculation.
    """
    block_type = "BTU_CALC"
    category = BlockCategory.MATH
    display_name = "BTU Calculator"
    description = "Heat duty: Q = Flow × Cp × ΔT"

    def _define_terminals(self):
        self.add_input("FLOW", description="Flow rate")
        self.add_input("T_IN", description="Inlet temperature")
        self.add_input("T_OUT", description="Outlet temperature")
        self.add_output("OUT", description="Heat duty (Q)")
        self.add_output("DELTA_T", description="Temperature difference")

    def get_config_schema(self):
        return {
            "CP": (float, 4.186, "Specific heat capacity [kJ/(kg·°C)]"),
            "FACTOR": (float, 1.0, "Unit conversion factor"),
        }

    def execute(self, dt: float):
        p = self.config.params
        flow = self.get_input("FLOW")
        dt_temp = self.get_input("T_OUT") - self.get_input("T_IN")
        q = flow * p.get("CP", 4.186) * dt_temp * p.get("FACTOR", 1.0)
        self.set_output("OUT", q)
        self.set_output("DELTA_T", dt_temp)
