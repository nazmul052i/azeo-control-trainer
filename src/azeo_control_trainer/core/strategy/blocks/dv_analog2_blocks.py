"""Azeo analog calculation / selection blocks — ARITH, CTLSL, ISEL, SGSL.

Four Azeo-compatible function blocks transcribed from the block
reference (``doc/AZEO_FUNCTION_BLOCKS.md``):

    ARITH — Arithmetic (nine compensation algorithms + range extension)
    CTLSL — Control Selector (override control, 3 back-calculation paths)
    ISEL  — Input Selector (4 inputs, 6 selection algorithms, MIN_GOOD)
    SGSL  — Signal Selector (max / min / avg of up to 16 inputs)

These are *new* block types. The pre-existing lightweight
``MIN_SELECT`` / ``MAX_SELECT`` / ``MID_SELECT`` / ``AVG_SELECT`` blocks
are untouched.

Signal status
-------------
Azeo carries a value **and** a status on every wire. This repo's
wires carry a bare value, so every input that Azeo status-checks has a
companion integer terminal ``<IN>_ST`` and every status-bearing output a
companion ``<OUT>_ST``. Codes (module constants below)::

    0 GOOD          1 UNCERTAIN      2 BAD
    3 GOOD:IR       — Good:Cascade, Initialization Request
                      (downstream block not in Cas — drives CTLSL to IMan)
    4 GOOD:NS       — Good:Cascade, Not Selected  (CTLSL losing input)
    5 GOOD:NI       — Good:Cascade, Not Invited   (CTLSL in Man)

Limit status travels on ``<name>_LIM`` integer terminals::

    0 NOT LIMITED   1 LOW LIMITED    2 HIGH LIMITED   3 CONSTANT

The status/limit pins are hidden by default so the blocks stay compact
on the canvas; set the ``SHOW_STATUS_PINS`` config parameter to expose
them (visibility is recomputed in ``_apply_config`` so it survives a
JSON round-trip).

Not modelled: fieldbus/HART device binding, BLOCK_ERR bit records,
ALERT_KEY / ST_REV / STRATEGY housekeeping parameters, and Azeo's
custom-alarm subsystem — none of that infrastructure exists here. The
block *algorithms*, modes, and status/limit propagation are complete.
"""
from __future__ import annotations

import math

from ..model.block_base import (
    FunctionBlock, BlockCategory, BlockStatus, DataType,
)
from ..model.block_registry import register_block


# ═══════════════════════════════════════════════════════════════════════
#  Shared status helpers
# ═══════════════════════════════════════════════════════════════════════

ST_GOOD = 0
ST_UNCERTAIN = 1
ST_BAD = 2
ST_INIT_REQ = 3       # Good: Cascade, Initialization Request
ST_NOT_SELECTED = 4   # Good: Cascade, Not Selected
ST_NOT_INVITED = 5    # Good: Cascade, Not Invited

LIM_NONE = 0
LIM_LOW = 1
LIM_HIGH = 2
LIM_CONST = 3

_BIG = 3.40282e38     # Azeo's float extreme, used for unlimited defaults


def _quality(code) -> int:
    """Reduce a status code to plain quality: GOOD / UNCERTAIN / BAD."""
    c = int(code)
    if c == ST_BAD:
        return ST_BAD
    if c == ST_UNCERTAIN:
        return ST_UNCERTAIN
    return ST_GOOD


def _worst(codes) -> int:
    """Worst quality among status codes (BAD > UNCERTAIN > GOOD)."""
    worst = ST_GOOD
    for c in codes:
        q = _quality(c)
        if q > worst:
            worst = q
    return worst


def _usable(code, use_uncertain: bool, use_bad: bool = False) -> bool:
    """A value is usable when Good, or when its INPUT_OPTS option is set."""
    q = _quality(code)
    if q == ST_GOOD:
        return True
    if q == ST_UNCERTAIN:
        return use_uncertain
    return use_bad


def _block_status(code) -> BlockStatus:
    q = _quality(code)
    if q == ST_BAD:
        return BlockStatus.BAD
    if q == ST_UNCERTAIN:
        return BlockStatus.UNCERTAIN
    return BlockStatus.GOOD


def _clamp(v: float, lo: float, hi: float) -> tuple[float, int]:
    """Clamp and report the limit status that resulted."""
    if v > hi:
        return hi, LIM_HIGH
    if v < lo:
        return lo, LIM_LOW
    return v, LIM_NONE


def _signed_sqrt(x: float) -> float:
    """Square root of a negative value = -sqrt(|x|) (no imaginary roots)."""
    return math.copysign(math.sqrt(abs(x)), x)


class _StatusPinMixin:
    """Show/hide the companion status pins from ``SHOW_STATUS_PINS``."""

    def _status_pin_names(self) -> tuple[list[str], list[str]]:
        """Return (input names, output names) of the optional pins."""
        return [], []

    def _hide_status_pins(self):
        ins, outs = self._status_pin_names()
        for n in ins:
            if n in self.inputs:
                self.inputs[n].hidden = True
        for n in outs:
            if n in self.outputs:
                self.outputs[n].hidden = True

    def _apply_status_pin_visibility(self):
        show = bool(self.config.params.get("SHOW_STATUS_PINS", False))
        ins, outs = self._status_pin_names()
        for name in ins:
            t = self.inputs.get(name)
            # Never hide a wired terminal — the wire would lose its pin.
            if t is not None and not (t.connected and not show):
                t.hidden = not show
        for name in outs:
            t = self.outputs.get(name)
            if t is not None and not (t.connected and not show):
                t.hidden = not show

    def _apply_config(self):
        self._apply_status_pin_visibility()


# ═══════════════════════════════════════════════════════════════════════
#  ARITH — Arithmetic
# ═══════════════════════════════════════════════════════════════════════

@register_block
class ArithmeticBlock(_StatusPinMixin, FunctionBlock):
    """Arithmetic (ARITH) — range extension + nine compensation functions.

    Pipeline (doc §"Arithmetic function block")::

        PV  = range extension of IN with IN_LO
        t(k) = GAIN_IN_k * (BIAS_IN_k + IN_k)      k = 1..3
        func = one of nine ARITH_TYPE algorithms of PV and t(1..3)
        PRE_OUT = clamp(GAIN*func + BIAS, OUT_LO_LIM, OUT_HI_LIM)
        OUT = PRE_OUT (Auto) / OUT_MAN (Man) / held (OOS)

    Range extension blends the primary input with its low-range
    companion: ``PV = G*IN + (1-G)*IN_LO`` where G ramps 0 → 1 across
    RANGE_LO..RANGE_HI. When one of the two is unusable, G is forced
    (1 for IN alone above RANGE_LO, 0 for IN_LO alone below RANGE_HI)
    and PV keeps Good status while that condition holds.

    ``ARITH_TYPE`` named set (nine values)::

        FLOW_COMP_LINEAR              func = PV * f,  f = t1/t2
        FLOW_COMP_SQUARE_ROOT         func = PV * f,  f = sqrt(t1/(t2*t3))
        FLOW_COMP_APPROXIMATE         func = PV * f,  f = sqrt(t1*t2*t3^2)
        BTU_FLOW                      func = PV * f,  f = t1 - t2
        TRADITIONAL_MULTIPLY_DIVIDE   func = f * PV,  f = t1/t2 + t3
        AVERAGE                       func = (PV + Σ used t) / (1 + n used)
        SUMMER                        func = PV + Σ used t
        FOURTH_ORDER_POLY             func = PV + t1^2 + t2^3 + t3^4
        SIMPLE_HTG_COMP_LEVEL         func = (PV - t1) / (PV - t2)

    ``f`` is clamped to COMP_LO_LIM..COMP_HI_LIM for the first five
    types. On divide-by-zero the result is forced to COMP_HI_LIM for a
    non-negative numerator and COMP_LO_LIM for a negative one (the doc
    states this rule for the square-root, multiply/divide and HTG types;
    it is applied to the linear type too — the manual gives no other
    reading for a zero divisor).

    An unusable compensation input holds the last value computed from a
    usable one. INPUT_OPTS (`IN Use Uncertain`, `IN_LO Use Uncertain`,
    `IN_x Use Uncertain`, `IN_x Use Bad`) decide usability and are
    exposed as the ``OPT_*`` booleans; Azeo only allows them to be
    changed online in OOS, which is not enforced here.

    AVERAGE and SUMMER use only *configured* compensation inputs: a
    wired IN_k. If none of IN_1..IN_3 is wired (block driven directly,
    e.g. from a test or an external write) all three are considered
    configured.
    """
    block_type = "ARITH"
    category = BlockCategory.MATH
    display_name = "Arithmetic (ARITH)"
    description = "Nine flow/energy compensation algorithms with range extension"

    _TYPES = (
        "FLOW_COMP_LINEAR", "FLOW_COMP_SQUARE_ROOT", "FLOW_COMP_APPROXIMATE",
        "BTU_FLOW", "TRADITIONAL_MULTIPLY_DIVIDE", "AVERAGE", "SUMMER",
        "FOURTH_ORDER_POLY", "SIMPLE_HTG_COMP_LEVEL",
    )

    # Named sets (doc §"ARITH_TYPE — all nine algorithm choices"; modes
    # per doc §Modes: "Supports OOS, Man, Auto").
    config_choices = {
        "MODE": ("AUTO", "MAN", "OOS"),
        "ARITH_TYPE": _TYPES,
    }
    # Units per the doc's parameter table (GAIN/BIAS are dimensionless).
    config_units = {
        "RANGE_LO": "EU of PV_SCALE",
        "RANGE_HI": "EU of PV_SCALE",
        "COMP_HI_LIM": "EU of PV_SCALE",
        "COMP_LO_LIM": "EU of PV_SCALE",
        "OUT_HI_LIM": "EU of OUT_SCALE",
        "OUT_LO_LIM": "EU of OUT_SCALE",
        "OUT_MAN": "EU of OUT_SCALE",
    }

    # Compensation inputs each algorithm consumes (AVERAGE/SUMMER: configured)
    _USED = {
        "FLOW_COMP_LINEAR": (1, 2),
        "FLOW_COMP_SQUARE_ROOT": (1, 2, 3),
        "FLOW_COMP_APPROXIMATE": (1, 2, 3),
        "BTU_FLOW": (1, 2),
        "TRADITIONAL_MULTIPLY_DIVIDE": (1, 2, 3),
        "FOURTH_ORDER_POLY": (1, 2, 3),
        "SIMPLE_HTG_COMP_LEVEL": (1, 2),
    }

    def __init__(self, instance_name: str = ""):
        self._t_hold = [0.0, 0.0, 0.0]      # last usable t(k)
        self._t_valid = [False, False, False]
        self._t_usable = [True, True, True]  # usable this scan (AVERAGE skips)
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("IN", description="Primary analog input")
        self.add_input("IN_LO", description="Low-range companion of IN")
        self.add_input("IN_1", description="Compensation input 1")
        self.add_input("IN_2", description="Compensation input 2")
        self.add_input("IN_3", description="Compensation input 3")
        for n in ("IN_ST", "IN_LO_ST", "IN_1_ST", "IN_2_ST", "IN_3_ST"):
            self.add_input(n, DataType.INT, ST_GOOD,
                           f"Status of {n[:-3]} (0=Good 1=Uncert 2=Bad)")
        self.add_output("OUT", description="Block output (EU of OUT_SCALE)")
        self.add_output("PRE_OUT", description="GAIN*func + BIAS after limiting")
        self.add_output("PV", description="Range-extended process variable")
        self.add_output("OUT_ST", DataType.INT, ST_GOOD, "Status of OUT")
        self.add_output("MODE_ACT", DataType.STRING, "AUTO", "Actual mode")
        self._hide_status_pins()

    def _status_pin_names(self):
        return (["IN_ST", "IN_LO_ST", "IN_1_ST", "IN_2_ST", "IN_3_ST"],
                ["OUT_ST"])

    def get_config_schema(self):
        return {
            "MODE":        (str, "AUTO", "Target mode: AUTO / MAN / OOS"),
            "ARITH_TYPE":  (str, "FLOW_COMP_LINEAR",
                            "FLOW_COMP_LINEAR / FLOW_COMP_SQUARE_ROOT / "
                            "FLOW_COMP_APPROXIMATE / BTU_FLOW / "
                            "TRADITIONAL_MULTIPLY_DIVIDE / AVERAGE / SUMMER / "
                            "FOURTH_ORDER_POLY / SIMPLE_HTG_COMP_LEVEL"),
            "RANGE_LO":    (float, 0.0, "IN low range limit (G=0 at or below)"),
            "RANGE_HI":    (float, 0.0,
                            "IN high range limit (G=1 above); range extension "
                            "is inactive while RANGE_HI <= RANGE_LO"),
            "GAIN":        (float, 1.0, "Gain applied to func"),
            "BIAS":        (float, 0.0, "Bias added to GAIN*func"),
            "GAIN_IN_1":   (float, 1.0, "Gain for IN_1"),
            "GAIN_IN_2":   (float, 1.0, "Gain for IN_2"),
            "GAIN_IN_3":   (float, 1.0, "Gain for IN_3"),
            "BIAS_IN_1":   (float, 0.0, "Bias for IN_1 (absolute conversion)"),
            "BIAS_IN_2":   (float, 0.0, "Bias for IN_2 (absolute conversion)"),
            "BIAS_IN_3":   (float, 0.0, "Bias for IN_3 (absolute conversion)"),
            "COMP_HI_LIM": (float, _BIG, "High limit of the compensation factor"),
            "COMP_LO_LIM": (float, -_BIG, "Low limit of the compensation factor"),
            "OUT_HI_LIM":  (float, _BIG, "Maximum allowed output"),
            "OUT_LO_LIM":  (float, -_BIG, "Minimum allowed output"),
            "OUT_MAN":     (float, 0.0, "Operator output value used in Man mode"),
            "OPT_IN_USE_UNCERTAIN":    (bool, False,
                                        "INPUT_OPTS: IN Use Uncertain"),
            "OPT_IN_LO_USE_UNCERTAIN": (bool, False,
                                        "INPUT_OPTS: IN_LO Use Uncertain"),
            "OPT_IN_X_USE_UNCERTAIN":  (bool, False,
                                        "INPUT_OPTS: IN_x Use Uncertain (IN_1..IN_3)"),
            "OPT_IN_X_USE_BAD":        (bool, False,
                                        "INPUT_OPTS: IN_x Use Bad (IN_1..IN_3)"),
            "SHOW_STATUS_PINS": (bool, False, "Expose the _ST status pins"),
        }

    def reset(self):
        super().reset()
        self._t_hold = [0.0, 0.0, 0.0]
        self._t_valid = [False, False, False]
        self._t_usable = [True, True, True]

    # ── range extension ───────────────────────────────────────────────
    def _range_extend(self, p) -> tuple[float, int]:
        in_v = float(self.get_input("IN"))
        lo_v = float(self.get_input("IN_LO"))
        in_st = int(self.get_input("IN_ST"))
        lo_st = int(self.get_input("IN_LO_ST"))
        in_ok = _usable(in_st, bool(p.get("OPT_IN_USE_UNCERTAIN", False)))
        lo_ok = _usable(lo_st, bool(p.get("OPT_IN_LO_USE_UNCERTAIN", False)))
        r_lo = float(p.get("RANGE_LO", 0.0))
        r_hi = float(p.get("RANGE_HI", 0.0))

        # Range extension unconfigured (RANGE_HI <= RANGE_LO): PV = IN.
        if r_hi <= r_lo:
            if in_ok or not lo_ok:
                return in_v, in_st
            return lo_v, lo_st

        if in_ok and lo_ok:
            if in_v <= r_lo:
                g = 0.0
            elif in_v > r_hi:
                g = 1.0
            else:
                g = (in_v - r_lo) / (r_hi - r_lo)
            pv = g * in_v + (1.0 - g) * lo_v
            return pv, (in_st if g >= 0.5 else lo_st)
        if in_ok and in_v > r_lo:
            return in_v, ST_GOOD          # G forced to 1, PV stays Good
        if lo_ok and lo_v < r_hi:
            return lo_v, ST_GOOD          # G forced to 0, PV stays Good
        # Neither usable in its forced region — last PV, Bad status
        return float(self.get_output("PV")), ST_BAD

    # ── compensation terms ────────────────────────────────────────────
    def _terms(self, p) -> list[float]:
        use_unc = bool(p.get("OPT_IN_X_USE_UNCERTAIN", False))
        use_bad = bool(p.get("OPT_IN_X_USE_BAD", False))
        out: list[float] = []
        for k in (1, 2, 3):
            raw = float(self.get_input(f"IN_{k}"))
            st = int(self.get_input(f"IN_{k}_ST"))
            val = float(p.get(f"GAIN_IN_{k}", 1.0)) * (
                float(p.get(f"BIAS_IN_{k}", 0.0)) + raw)
            ok = _usable(st, use_unc, use_bad)
            self._t_usable[k - 1] = ok
            if ok:
                self._t_hold[k - 1] = val
                self._t_valid[k - 1] = True
                out.append(val)
            elif self._t_valid[k - 1]:
                out.append(self._t_hold[k - 1])   # hold last usable value
            else:
                out.append(val)                   # never had a usable value
        return out

    def _configured(self) -> list[int]:
        """Compensation inputs that are wired (all three if none is)."""
        wired = [k for k in (1, 2, 3) if self.inputs[f"IN_{k}"].connected]
        return wired if wired else [1, 2, 3]

    def _div(self, num: float, den: float, hi: float, lo: float) -> float:
        """Divide with the Azeo zero-divisor rule."""
        if den == 0.0:
            return hi if num >= 0.0 else lo
        return num / den

    # ── nine algorithms ───────────────────────────────────────────────
    def _func(self, atype: str, pv: float, t: list[float], p) -> float:
        hi = float(p.get("COMP_HI_LIM", _BIG))
        lo = float(p.get("COMP_LO_LIM", -_BIG))
        if lo > hi:
            lo, hi = hi, lo
        t1, t2, t3 = t

        if atype == "FLOW_COMP_LINEAR":
            f = self._div(t1, t2, hi, lo)
            return pv * _clamp(f, lo, hi)[0]
        if atype == "FLOW_COMP_SQUARE_ROOT":
            den = t2 * t3
            f = hi if t1 >= 0.0 else lo
            if den != 0.0:
                f = _signed_sqrt(t1 / den)
            return pv * _clamp(f, lo, hi)[0]
        if atype == "FLOW_COMP_APPROXIMATE":
            f = _signed_sqrt(t1 * t2 * t3 * t3)
            return pv * _clamp(f, lo, hi)[0]
        if atype == "BTU_FLOW":
            return pv * _clamp(t1 - t2, lo, hi)[0]
        if atype == "TRADITIONAL_MULTIPLY_DIVIDE":
            f = self._div(t1, t2, hi, lo)
            if t2 != 0.0:
                f += t3
            return _clamp(f, lo, hi)[0] * pv
        if atype == "AVERAGE":
            # Unusable compensation inputs drop out of both sum and count;
            # PV is always included.
            total, n = pv, 1
            for k in self._configured():
                if not self._t_usable[k - 1]:
                    continue
                total += t[k - 1]
                n += 1
            return total / n
        if atype == "SUMMER":
            total = pv
            for k in self._configured():
                total += t[k - 1]
            return total
        if atype == "FOURTH_ORDER_POLY":
            return pv + t1 ** 2 + t2 ** 3 + t3 ** 4
        if atype == "SIMPLE_HTG_COMP_LEVEL":
            num = pv - t1
            den = pv - t2
            if den == 0.0:
                return hi if num >= 0.0 else lo
            return num / den
        # Unknown selection — pass the PV through unchanged
        return pv

    def execute(self, dt: float):
        p = self.config.params
        mode = str(p.get("MODE", "AUTO")).upper()

        if mode == "OOS":
            self.status = BlockStatus.OOS
            self.set_output("OUT_ST", ST_BAD)
            self.set_output("MODE_ACT", "OOS")
            return                                # OUT / PV / PRE_OUT hold

        atype = str(p.get("ARITH_TYPE", "FLOW_COMP_LINEAR")).upper()
        pv, pv_st = self._range_extend(p)
        t = self._terms(p)
        func = self._func(atype, pv, t, p)

        pre_out = float(p.get("GAIN", 1.0)) * func + float(p.get("BIAS", 0.0))
        out_hi = float(p.get("OUT_HI_LIM", _BIG))
        out_lo = float(p.get("OUT_LO_LIM", -_BIG))
        if out_lo > out_hi:
            out_lo, out_hi = out_hi, out_lo
        pre_out, _lim = _clamp(pre_out, out_lo, out_hi)

        # PRE_OUT status = worst of PV and the inputs the algorithm uses
        used = self._USED.get(atype) or self._configured()
        pre_st = _worst([pv_st] + [int(self.get_input(f"IN_{k}_ST")) for k in used])

        self.set_output("PV", pv)
        self.set_output("PRE_OUT", pre_out)

        if mode == "MAN":
            out, _ = _clamp(float(p.get("OUT_MAN", 0.0)), out_lo, out_hi)
            self.set_output("OUT", out)
            self.set_output("OUT_ST", ST_GOOD)
            self.set_output("MODE_ACT", "MAN")
            self.status = BlockStatus.GOOD
            return

        self.set_output("OUT", pre_out)
        self.set_output("OUT_ST", pre_st)
        self.set_output("MODE_ACT", "AUTO")
        self.status = _block_status(pre_st)


# ═══════════════════════════════════════════════════════════════════════
#  CTLSL — Control Selector
# ═══════════════════════════════════════════════════════════════════════

@register_block
class ControlSelectorBlock(_StatusPinMixin, FunctionBlock):
    """Control Selector (CTLSL) — override control with 3 BKCAL paths.

    Passes the High, Low, or Middle of up to three controller outputs
    (SEL_1..SEL_3) to OUT, and hands each upstream controller its own
    back-calculation value so the *losing* controllers track the winner
    and never wind up — this is what makes an override scheme bumpless.

    Per the reference:

    * **Auto** — OUT = high / low / middle of the connected SEL inputs
      per ``SEL_TYPE``; the selected input's BKCAL_SEL takes OUT's value
      and status, the non-selected ones take the same value with
      substatus *Not Selected* and a forced limit status that blocks
      further integration in the losing direction:
      ``SEL_TYPE=LOW`` → High Limited, ``SEL_TYPE=HIGH`` → Low Limited,
      ``SEL_TYPE=MIDDLE`` → Low Limited for the lowest input and High
      Limited for the highest.
    * If any connected SEL input goes Bad in Auto, the actual mode sheds
      to Man (OUT holds, and ``OUT_MAN`` snaps to it for a bumpless
      operator hand-off); Auto resumes when the input returns Good.
    * **Man** — OUT is the operator value; all BKCAL_SEL = OUT with
      substatus *Not Invited*.
    * **IMan** — entered when ``BKCAL_IN_ST`` reports Initialization
      Request (code 3 = the downstream block is not in Cas). OUT =
      BKCAL_IN and all BKCAL_SEL = BKCAL_IN value/status.
    * **OOS** — outputs hold with Bad: Out of Service status.
    * When OUT is not limited but BKCAL_IN reports a limit, the
      BKCAL_IN limit status *and value* are copied to the selected
      BKCAL_SEL (anti-windup from the downstream block).

    OUT_HI_LIM / OUT_LO_LIM are additionally restricted at runtime to
    OUT_SCALE ±10 % of span, as Azeo does.

    Which SEL inputs participate is decided by wiring (``connected``);
    if none is wired all three participate, so the block still runs
    standalone. STATUS_OPTS are the ``OPT_*`` booleans; ``IFS if Bad
    IN`` has no fail-safe transport here, so it is realised as a Bad
    OUT status alongside the Man shed. Per-SEL *input* limit status is
    not carried (Azeo copies the selected input's limit status to
    OUT); OUT's limit status comes from OUT_HI_LIM/OUT_LO_LIM and
    BKCAL_IN.
    """
    block_type = "CTLSL"
    category = BlockCategory.CONTROL
    display_name = "Control Selector (CTLSL)"
    description = "High/Low/Middle override selector with 3 back-calculation outputs"

    _SEL_TYPES = ("HIGH", "LOW", "MIDDLE")

    # Doc §Modes: "OOS, IMan, Man, Auto" — IMan is an *actual* mode the
    # block enters on an Initialization Request, never a target mode, so
    # only the three selectable targets are offered.
    config_choices = {
        "MODE": ("AUTO", "MAN", "OOS"),
        "SEL_TYPE": _SEL_TYPES,
    }
    config_units = {
        "OUT_SCALE_HI": "EU of OUT_SCALE",
        "OUT_SCALE_LO": "EU of OUT_SCALE",
        "OUT_HI_LIM": "EU of OUT_SCALE",
        "OUT_LO_LIM": "EU of OUT_SCALE",
        "OUT_MAN": "EU of OUT_SCALE",
    }

    def __init__(self, instance_name: str = ""):
        self._prev_actual = "AUTO"
        super().__init__(instance_name)

    def _define_terminals(self):
        for k in (1, 2, 3):
            self.add_input(f"SEL_{k}", description=f"Selector input {k}")
        self.add_input("BKCAL_IN", is_bkcal=True,
                       description="BKCAL_OUT of the downstream block")
        for k in (1, 2, 3):
            self.add_input(f"SEL_{k}_ST", DataType.INT, ST_GOOD,
                           f"Status of SEL_{k} (0=Good 1=Uncert 2=Bad)")
        self.add_input("BKCAL_IN_ST", DataType.INT, ST_GOOD,
                       "Status of BKCAL_IN (3 = Init Request -> IMan)")
        self.add_input("BKCAL_IN_LIM", DataType.INT, LIM_NONE,
                       "Limit status of BKCAL_IN (0=None 1=Low 2=High)")

        self.add_output("OUT", description="Selected control signal")
        for k in (1, 2, 3):
            self.add_output(f"BKCAL_SEL{k}", is_bkcal=True,
                            description=f"Back-calculation to the block on SEL_{k}")
        self.add_output("SELECTED", DataType.INT, 0,
                        "Selector input driving OUT (0 = none)")
        self.add_output("OUT_ST", DataType.INT, ST_GOOD, "Status of OUT")
        self.add_output("OUT_LIM", DataType.INT, LIM_NONE, "Limit status of OUT")
        for k in (1, 2, 3):
            self.add_output(f"BKCAL_SEL{k}_ST", DataType.INT, ST_GOOD,
                            f"Status of BKCAL_SEL{k} (4 = Not Selected)")
            self.add_output(f"BKCAL_SEL{k}_LIM", DataType.INT, LIM_NONE,
                            f"Limit status of BKCAL_SEL{k}")
        self.add_output("MODE_ACT", DataType.STRING, "AUTO", "Actual mode")
        self._hide_status_pins()

    def _status_pin_names(self):
        ins = [f"SEL_{k}_ST" for k in (1, 2, 3)] + ["BKCAL_IN_ST", "BKCAL_IN_LIM"]
        outs = ["OUT_ST", "OUT_LIM"]
        for k in (1, 2, 3):
            outs += [f"BKCAL_SEL{k}_ST", f"BKCAL_SEL{k}_LIM"]
        return ins, outs

    def get_config_schema(self):
        return {
            "MODE":         (str, "AUTO", "Target mode: AUTO / MAN / OOS"),
            "SEL_TYPE":     (str, "LOW", "Selector type: HIGH / LOW / MIDDLE"),
            "OUT_SCALE_HI": (float, 100.0, "OUT_SCALE EU100"),
            "OUT_SCALE_LO": (float, 0.0, "OUT_SCALE EU0"),
            "OUT_HI_LIM":   (float, 100.0,
                             "Maximum output (clipped to EU100 + 10% of span)"),
            "OUT_LO_LIM":   (float, 0.0,
                             "Minimum output (clipped to EU0 - 10% of span)"),
            "OUT_MAN":      (float, 0.0, "Operator output value used in Man mode"),
            "OPT_USE_UNCERTAIN_AS_GOOD": (bool, False,
                                          "STATUS_OPTS: Use Uncertain as Good"),
            "OPT_IFS_IF_BAD_IN": (bool, False,
                                  "STATUS_OPTS: IFS if Bad IN (Bad OUT status)"),
            "SHOW_STATUS_PINS": (bool, False, "Expose the status / limit pins"),
        }

    def reset(self):
        super().reset()
        self._prev_actual = "AUTO"

    def _limits(self, p) -> tuple[float, float]:
        """OUT limits restricted to OUT_SCALE +/- 10 % of span."""
        s_hi = float(p.get("OUT_SCALE_HI", 100.0))
        s_lo = float(p.get("OUT_SCALE_LO", 0.0))
        span = s_hi - s_lo
        hi = min(float(p.get("OUT_HI_LIM", s_hi)), s_hi + 0.1 * span)
        lo = max(float(p.get("OUT_LO_LIM", s_lo)), s_lo - 0.1 * span)
        if lo > hi:
            lo, hi = hi, lo
        return lo, hi

    def _participating(self) -> list[int]:
        wired = [k for k in (1, 2, 3) if self.inputs[f"SEL_{k}"].connected]
        return wired if wired else [1, 2, 3]

    def _emit(self, out, out_st, out_lim, selected, bk_vals, bk_sts, bk_lims,
              actual):
        self.set_output("OUT", out)
        self.set_output("OUT_ST", out_st)
        self.set_output("OUT_LIM", out_lim)
        self.set_output("SELECTED", selected)
        for k in (1, 2, 3):
            self.set_output(f"BKCAL_SEL{k}", bk_vals[k - 1])
            self.set_output(f"BKCAL_SEL{k}_ST", bk_sts[k - 1])
            self.set_output(f"BKCAL_SEL{k}_LIM", bk_lims[k - 1])
        self.set_output("MODE_ACT", actual)
        self._prev_actual = actual

    def execute(self, dt: float):
        p = self.config.params
        mode = str(p.get("MODE", "AUTO")).upper()
        lo_lim, hi_lim = self._limits(p)
        out_prev = float(self.get_output("OUT"))

        # ── OOS: everything Bad: Out of Service, values hold ──────────
        if mode == "OOS":
            self.status = BlockStatus.OOS
            self._emit(out_prev, ST_BAD, LIM_NONE, 0,
                       [self.get_output(f"BKCAL_SEL{k}") for k in (1, 2, 3)],
                       [ST_BAD] * 3, [LIM_NONE] * 3, "OOS")
            return

        bk_val = float(self.get_input("BKCAL_IN"))
        bk_st = int(self.get_input("BKCAL_IN_ST"))
        bk_lim = int(self.get_input("BKCAL_IN_LIM"))

        # ── IMan: downstream block not in Cas (Initialization Request) ─
        if bk_st == ST_INIT_REQ:
            out, _ = _clamp(bk_val, lo_lim, hi_lim)
            # Good: Cascade, Initiate Acknowledge in response to the IR
            self.status = BlockStatus.GOOD
            self._emit(out, ST_GOOD, bk_lim, 0,
                       [bk_val] * 3, [bk_st] * 3, [bk_lim] * 3, "IMAN")
            p["OUT_MAN"] = out
            return

        part = self._participating()
        use_unc = bool(p.get("OPT_USE_UNCERTAIN_AS_GOOD", False))
        sts = {k: int(self.get_input(f"SEL_{k}_ST")) for k in part}
        bad = [k for k in part if _quality(sts[k]) == ST_BAD]
        usable = [k for k in part if _quality(sts[k]) != ST_BAD]

        shed = bool(bad) or not usable
        if mode == "MAN" or shed:
            # Entering Man (operator or shed) snaps OUT_MAN to the last
            # output so the hand-off is bumpless.
            if self._prev_actual != "MAN":
                p["OUT_MAN"] = out_prev
            out, lim = _clamp(float(p.get("OUT_MAN", out_prev)), lo_lim, hi_lim)
            out_st = ST_GOOD
            if shed and bool(p.get("OPT_IFS_IF_BAD_IN", False)):
                out_st = ST_BAD                      # IFS if Bad IN
            self.status = BlockStatus.BAD if shed else BlockStatus.GOOD
            # Good: Cascade, Not Invited on every back-calculation path
            self._emit(out, out_st, lim, 0, [out] * 3,
                       [ST_NOT_INVITED] * 3, [lim] * 3, "MAN")
            return

        # ── Auto: high / low / middle of the usable SEL inputs ────────
        sel_type = str(p.get("SEL_TYPE", "LOW")).upper()
        vals = {k: float(self.get_input(f"SEL_{k}")) for k in usable}
        order = sorted(usable, key=lambda k: vals[k])
        if sel_type == "HIGH":
            sel = order[-1]
        elif sel_type == "MIDDLE":
            # Middle of an even count has no true median — take the
            # lower of the two central values (deterministic choice).
            sel = order[(len(order) - 1) // 2]
        else:
            sel = order[0]

        out, lim = _clamp(vals[sel], lo_lim, hi_lim)
        out_st = ST_GOOD if (use_unc and _quality(sts[sel]) == ST_UNCERTAIN) \
            else sts[sel]

        bk_vals = [out] * 3
        bk_sts = [ST_NOT_SELECTED] * 3
        bk_lims = [LIM_NONE] * 3

        # Selected path: OUT's value/status, or BKCAL_IN's limit + value
        # when OUT itself is not limited but the downstream block is.
        if lim == LIM_NONE and bk_lim != LIM_NONE:
            bk_vals[sel - 1] = bk_val
            bk_lims[sel - 1] = bk_lim
        else:
            bk_lims[sel - 1] = lim
        bk_sts[sel - 1] = out_st

        # Non-selected paths: forced limit status stops the losing
        # controllers integrating further in the losing direction.
        for k in (1, 2, 3):
            if k == sel:
                continue
            bk_vals[k - 1] = bk_vals[sel - 1]
            if sel_type == "LOW":
                bk_lims[k - 1] = LIM_HIGH
            elif sel_type == "HIGH":
                bk_lims[k - 1] = LIM_LOW
            else:   # MIDDLE — lowest input low-limited, highest high-limited
                if k in vals and k == order[0]:
                    bk_lims[k - 1] = LIM_LOW
                elif k in vals and k == order[-1]:
                    bk_lims[k - 1] = LIM_HIGH
                else:
                    bk_lims[k - 1] = LIM_NONE

        self.status = _block_status(out_st)
        self._emit(out, out_st, lim, sel, bk_vals, bk_sts, bk_lims, "AUTO")


# ═══════════════════════════════════════════════════════════════════════
#  ISEL — Input Selector
# ═══════════════════════════════════════════════════════════════════════

@register_block
class InputSelectorBlock(_StatusPinMixin, FunctionBlock):
    """Input Selector (ISEL) — 4 inputs, operator or algorithm selection.

    ``OP_SELECT`` 1..4 direct-selects IN_n (even a disabled one — OUT
    then takes that input's status); ``OP_SELECT = 0`` defers to
    ``SELECT_TYPE``:

    ===============  =======================================================
    FIRST_GOOD       first usable input counting up from IN_1
    MIN / MAX        lowest / highest usable input
    MID              middle usable input; an even count averages the two
                     central values (substatus NonSpecific, limit
                     NotLimited) and reports the usable count in SELECTED
    AVG              average of the usable inputs (SELECTED = count)
    HOT_BACKUP       holds the previous selection while it stays usable,
                     otherwise advances upward to the next usable input
    ===============  =======================================================

    ``DISABLE_1..4`` remove an input from the algorithms. Bad inputs are
    excluded as well; when fewer than ``MIN_GOOD`` inputs are usable,
    OUT and SELECTED go Bad. STATUS_OPTS `Uncertain if Man mode` and
    `Use Uncertain as Good` are the ``OPT_*`` booleans.

    Which inputs exist is decided by wiring (``connected``); if none is
    wired all four participate so the block runs standalone. Modes:
    OOS (outputs hold, status OOS), Man (OUT = OUT_MAN), Auto.
    """
    block_type = "ISEL"
    category = BlockCategory.SIGNAL
    display_name = "Input Selector (ISEL)"
    description = "4-input first-good/min/max/mid/avg/hot-backup selector"

    _SELECT_TYPES = ("FIRST_GOOD", "MIN", "MAX", "MID", "AVG", "HOT_BACKUP")

    # Doc §Modes: "OOS, Man, Auto"; SELECT_TYPE named set per
    # §"SELECT_TYPE algorithms" (First Good / Minimum / Maximum / Middle /
    # Average / Hot Backup).
    config_choices = {
        "MODE": ("AUTO", "MAN", "OOS"),
        "SELECT_TYPE": _SELECT_TYPES,
    }
    config_units = {
        "OUT_MAN": "EU of OUT_SCALE",
    }

    def __init__(self, instance_name: str = ""):
        self._hot_sel = 0
        super().__init__(instance_name)

    def _define_terminals(self):
        for k in (1, 2, 3, 4):
            self.add_input(f"IN_{k}", description=f"Analog input {k}")
        for k in (1, 2, 3, 4):
            self.add_input(f"DISABLE_{k}", DataType.BOOL, False,
                           f"True removes IN_{k} from the SELECT_TYPE algorithms")
        for k in (1, 2, 3, 4):
            self.add_input(f"IN_{k}_ST", DataType.INT, ST_GOOD,
                           f"Status of IN_{k} (0=Good 1=Uncert 2=Bad)")
        self.add_output("OUT", description="Selected / computed output")
        self.add_output("SELECTED", DataType.INT, 0,
                        "Selected input number, or usable count for AVG / MID-even")
        self.add_output("OUT_ST", DataType.INT, ST_GOOD, "Status of OUT")
        self.add_output("MODE_ACT", DataType.STRING, "AUTO", "Actual mode")
        self._hide_status_pins()

    def _status_pin_names(self):
        return ([f"IN_{k}_ST" for k in (1, 2, 3, 4)], ["OUT_ST"])

    def get_config_schema(self):
        return {
            "MODE":        (str, "AUTO", "Target mode: AUTO / MAN / OOS"),
            "SELECT_TYPE": (str, "FIRST_GOOD",
                            "FIRST_GOOD / MIN / MAX / MID / AVG / HOT_BACKUP"),
            "OP_SELECT":   (int, 0, "0 = use SELECT_TYPE, 1-4 = direct select IN_n"),
            "MIN_GOOD":    (int, 0, "Minimum usable inputs for a not-Bad result"),
            "OUT_MAN":     (float, 0.0, "Operator output value used in Man mode"),
            "OPT_UNCERTAIN_IF_MAN":     (bool, False,
                                         "STATUS_OPTS: Uncertain if Man mode"),
            "OPT_USE_UNCERTAIN_AS_GOOD": (bool, False,
                                          "STATUS_OPTS: Use Uncertain as Good"),
            "SHOW_STATUS_PINS": (bool, False, "Expose the _ST status pins"),
        }

    def reset(self):
        super().reset()
        self._hot_sel = 0

    def _present(self) -> list[int]:
        wired = [k for k in (1, 2, 3, 4) if self.inputs[f"IN_{k}"].connected]
        return wired if wired else [1, 2, 3, 4]

    def execute(self, dt: float):
        p = self.config.params
        mode = str(p.get("MODE", "AUTO")).upper()

        if mode == "OOS":
            self.status = BlockStatus.OOS
            self.set_output("OUT_ST", ST_BAD)
            self.set_output("MODE_ACT", "OOS")
            return                                    # OUT / SELECTED hold

        if mode == "MAN":
            self.set_output("OUT", float(p.get("OUT_MAN", 0.0)))
            st = ST_UNCERTAIN if bool(p.get("OPT_UNCERTAIN_IF_MAN", False)) \
                else ST_GOOD
            self.set_output("OUT_ST", st)
            self.set_output("SELECTED", 0)
            self.set_output("MODE_ACT", "MAN")
            self.status = _block_status(st)
            return

        present = self._present()
        sts = {k: int(self.get_input(f"IN_{k}_ST")) for k in present}
        vals = {k: float(self.get_input(f"IN_{k}")) for k in present}
        use_unc = bool(p.get("OPT_USE_UNCERTAIN_AS_GOOD", False))

        # Direct operator select bypasses DISABLE_n and the algorithms.
        op_sel = int(p.get("OP_SELECT", 0))
        if op_sel in present:
            # n_usable=None -> MIN_GOOD does not apply to a direct select
            self._finish(vals[op_sel], op_sel, sts[op_sel], p, None)
            return

        usable = [k for k in present
                  if not bool(self.get_input(f"DISABLE_{k}"))
                  and _quality(sts[k]) != ST_BAD]
        n_use = len(usable)
        if not usable:
            # Nothing to select — hold the output with Bad status.
            self._finish(float(self.get_output("OUT")), 0, ST_BAD, p, 0)
            return

        stype = str(p.get("SELECT_TYPE", "FIRST_GOOD")).upper()
        order = sorted(usable, key=lambda k: vals[k])

        if stype == "MAX":
            sel = order[-1]
            out, sel_no, st = vals[sel], sel, sts[sel]
        elif stype == "MIN":
            sel = order[0]
            out, sel_no, st = vals[sel], sel, sts[sel]
        elif stype == "MID":
            if n_use % 2:
                sel = order[n_use // 2]
                out, sel_no, st = vals[sel], sel, sts[sel]
            else:
                a, b = order[n_use // 2 - 1], order[n_use // 2]
                out = 0.5 * (vals[a] + vals[b])
                sel_no = n_use                 # no single input -> usable count
                st = _worst([sts[a], sts[b]])  # NonSpecific substatus
        elif stype == "AVG":
            out = sum(vals[k] for k in usable) / n_use
            sel_no = n_use
            st = _worst([sts[k] for k in usable])
        elif stype == "HOT_BACKUP":
            sel = self._hot_sel if self._hot_sel in usable else 0
            if not sel:
                # Advance upward from the previous selection, wrapping.
                start = self._hot_sel
                ring = [k for k in present if k > start] + \
                       [k for k in present if k <= start]
                sel = next((k for k in ring if k in usable), usable[0])
            self._hot_sel = sel
            out, sel_no, st = vals[sel], sel, sts[sel]
        else:   # FIRST_GOOD (also the fallback for an unknown selection)
            sel = min(usable)
            out, sel_no, st = vals[sel], sel, sts[sel]

        if use_unc and _quality(st) == ST_UNCERTAIN:
            st = ST_GOOD
        self._finish(out, sel_no, st, p, n_use)

    def _finish(self, out, selected, st, p, n_usable: int | None):
        # Below MIN_GOOD usable inputs both OUT and SELECTED go Bad.
        if n_usable is not None and n_usable < int(p.get("MIN_GOOD", 0)):
            st = ST_BAD
        self.set_output("OUT", out)
        self.set_output("SELECTED", int(selected))
        self.set_output("OUT_ST", st)
        self.set_output("MODE_ACT", "AUTO")
        self.status = _block_status(st)


# ═══════════════════════════════════════════════════════════════════════
#  SGSL — Signal Selector
# ═══════════════════════════════════════════════════════════════════════

@register_block
class SignalSelectorBlock(_StatusPinMixin, FunctionBlock):
    """Signal Selector (SGSL) — max / min / avg of up to 16 inputs.

    A lightweight selector with **no modes and no alarm detection** —
    that is what separates it from ISEL and CTLSL. ``SEL_TYPE`` picks
    MAX, MIN, or AVG of IN1..IN16.

    IN1..IN16 are Azeo *extensible* parameters: only two connectors
    exist by default. ``N_INPUTS`` (2..16) plays that role here — the
    surplus input pins are hidden until the count is raised.

    Status: OUT status is the worst status among the inputs. Bad inputs
    are excluded from the computation; if *all* inputs are Bad the block
    executes using the Bad inputs anyway.
    """
    block_type = "SGSL"
    category = BlockCategory.SIGNAL
    display_name = "Signal Selector (SGSL)"
    description = "max / min / avg of up to 16 inputs with status propagation"

    _N_MAX = 16
    _SEL_TYPES = ("MAX", "MIN", "AVG")

    # SGSL has no modes (doc §"No modes and no alarm detection"), so no
    # MODE choice list — only the SEL_TYPE named set.
    config_choices = {
        "SEL_TYPE": _SEL_TYPES,
    }

    def _define_terminals(self):
        for k in range(1, self._N_MAX + 1):
            t = self.add_input(f"IN{k}", description=f"Analog input {k}")
            t.hidden = k > 2                # extensible: 2 connectors by default
        for k in range(1, self._N_MAX + 1):
            self.add_input(f"IN{k}_ST", DataType.INT, ST_GOOD,
                           f"Status of IN{k} (0=Good 1=Uncert 2=Bad)")
        self.add_output("OUT", description="Selected / computed value")
        self.add_output("OUT_ST", DataType.INT, ST_GOOD,
                        "Status of OUT (worst input status)")
        self._hide_status_pins()

    def _status_pin_names(self):
        return ([f"IN{k}_ST" for k in range(1, self._N_MAX + 1)], ["OUT_ST"])

    def get_config_schema(self):
        return {
            "SEL_TYPE": (str, "MAX", "Selection method: MAX / MIN / AVG"),
            "N_INPUTS": (int, 2, "Number of inputs in use (2-16)"),
            "SHOW_STATUS_PINS": (bool, False, "Expose the _ST status pins"),
        }

    def _n_inputs(self) -> int:
        return max(1, min(self._N_MAX, int(self.config.params.get("N_INPUTS", 2))))

    def _apply_config(self):
        n = self._n_inputs()
        show = bool(self.config.params.get("SHOW_STATUS_PINS", False))
        for k in range(1, self._N_MAX + 1):
            t = self.inputs[f"IN{k}"]
            if not (t.connected and k > n):
                t.hidden = k > n
            st = self.inputs[f"IN{k}_ST"]
            visible = show and k <= n
            if not (st.connected and not visible):
                st.hidden = not visible
        out_st = self.outputs["OUT_ST"]
        if not (out_st.connected and not show):
            out_st.hidden = not show

    def execute(self, dt: float):
        p = self.config.params
        n = self._n_inputs()
        stype = str(p.get("SEL_TYPE", "MAX")).upper()

        vals = [float(self.get_input(f"IN{k}")) for k in range(1, n + 1)]
        sts = [int(self.get_input(f"IN{k}_ST")) for k in range(1, n + 1)]

        good = [v for v, s in zip(vals, sts) if _quality(s) != ST_BAD]
        # All inputs Bad -> the block still executes, using the Bad values.
        used = good if good else vals

        if stype == "MIN":
            out = min(used)
        elif stype == "AVG":
            out = sum(used) / len(used)
        else:
            out = max(used)

        out_st = _worst(sts)
        self.set_output("OUT", out)
        self.set_output("OUT_ST", out_st)
        self.status = _block_status(out_st)
