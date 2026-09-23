# -*- coding: utf-8 -*-
"""Loop shaping around the PID block - the block itself stays untouched.

The Whitehouse-parity control techniques, implemented the way a DCS
engineer would bolt them onto a standard PID rather than by rewriting
it:

* **Gain schedulers** - a callable per loop that returns the working
  gain each scan. Error-squared and gap control are exactly the
  classic nonlinear-gain modifiers, applied by scheduling the block's
  GAIN parameter; banded scheduling and programmed adaptive schedules
  come from the same hook. The block's equation never changes.
* **Deadtime compensation** - a Smith predictor as a PV corrector in
  front of the block: pv* = pv + model(u) - model_delayed(u). Dahlin
  and IMC arrive as tuning syntheses over the same FOPDT model (their
  closed forms for a first-order-plus-deadtime process reduce to a
  PI with model-derived constants, plus the predictor for Dahlin).

Everything here is scan-driven by the strategy and persists what it
must through capture/apply on the strategy side.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..core.dynamics import DeadTime, Lag, clamp


# Named process-slope sources for the ``slope`` algorithm kind, so a
# selection can be persisted by name. Models or strategy layers register
# callables pv -> d(PV)/d(OUT) here (the pH titration curve being the
# canonical entry).
SLOPE_SOURCES: dict = {}


def titration_slope(ph: float, curve_gain: float = 3.2) -> float:
    """Relative slope of the U800 titration curve at a given pH.

    The model's curve is pH = 7 - 3.2*sign(x)*log10(1+|x|) in buffered
    excess-reagent units, so the local gain relative to neutrality is
    exactly 10^(-|pH-7|/3.2): steepest at the equivalence point,
    orders of magnitude flatter out on the ends - which is why a fixed
    gain that is stable at pH 7 is uselessly slow at pH 4.
    """
    return 10.0 ** (-abs(ph - 7.0) / curve_gain)


SLOPE_SOURCES["u800_titration"] = titration_slope


# ------------------------------------------------------- gain schedulers
def error_squared(base_gain: float, mix_c: float = 0.9):
    """Azeo nonlinear gain: small near SP, full far away.

    gain(e) = base * ((1 - C) + C * |e|% / 100). C = 1 is fully
    squared; C = 0 is the linear block again.
    """
    def fn(pid) -> float:
        span = max(pid.pv_span, 1e-9)
        e_pct = abs(pid.pv - pid.sp_wrk) / span * 100.0
        return base_gain * ((1.0 - mix_c) + mix_c * min(e_pct, 100.0) / 100.0)
    return fn


def gap(base_gain: float, gap_lo: float = 10.0, gap_hi: float = 10.0,
        k_gap: float = 0.1):
    """Gap control: k_gap x gain inside the band around SP, full outside.

    Gaps are in percent of PV span (lo below SP, hi above).
    """
    def fn(pid) -> float:
        span = max(pid.pv_span, 1e-9)
        e_pct = (pid.pv - pid.sp_wrk) / span * 100.0
        inside = -abs(gap_lo) <= e_pct <= abs(gap_hi)
        return base_gain * (k_gap if inside else 1.0)
    return fn


def banded(bands):
    """Three-region (or n-region) gain scheduling on |error|.

    ``bands`` is a list of (break_pct, gain) sorted ascending by break;
    the last entry's gain applies beyond the last break. Example:
    [(1.5, 0.00033), (3.0, 0.002), (inf, 0.05)] - inner/middle/outer.
    """
    def fn(pid) -> float:
        span = max(pid.pv_span, 1e-9)
        e_pct = abs(pid.pv - pid.sp_wrk) / span * 100.0
        for brk, g in bands:
            if e_pct <= brk:
                return g
        return bands[-1][1]
    return fn


def slope_scheduled(slope_fn, kp_target: float,
                    g_min: float = 1e-4, g_max: float = 1e3):
    """Linearised-PV control expressed as gain scheduling.

    ``slope_fn(pv)`` returns the local process slope d(PV)/d(OUT) in
    EU per percent (for pH: the titration curve slope at the current
    pH). The working gain is chosen so gain x slope is constant -
    which is exactly what feeding the block a linearised PV achieves,
    without touching the block or the faceplate's engineering units.
    ``kp_target`` is the desired loop-gain product (percent out per
    percent span).
    """
    def fn(pid) -> float:
        span = max(pid.pv_span, 1e-9)
        slope = abs(slope_fn(pid.pv)) / span * 100.0    # %span per %out
        return clamp(kp_target / max(slope, 1e-9), g_min, g_max)
    return fn


def programmed(base_gain: float, k1: float, k2: float,
               g_min: float = 1e-4, g_max: float = 1e3):
    """Programmed adaptive gain: gain(PV) = base * 10^(k1 + k2 * PV)."""
    def fn(pid) -> float:
        return clamp(base_gain * 10.0 ** (k1 + k2 * pid.pv), g_min, g_max)
    return fn


# --------------------------------------------------- deadtime compensation
@dataclass
class FOPDT:
    """First-order-plus-deadtime process model, in the loop's own units:
    gain K in (percent of PV span) per (percent of OUT), theta and tau
    in seconds."""
    k: float
    theta: float
    tau: float


class SmithPredictor:
    """The classic predictor as a PV corrector.

    pv* = pv + K*lag(u) - K*lag(delay(u)): the block controls the
    undelayed model while the model mismatch (real PV minus delayed
    model) still feeds through, so load upsets are not invisible.
    Built lazily on the first correct() call because the scan period
    is only known then.
    """

    def __init__(self, model: FOPDT) -> None:
        self.model = model
        self._fast: Lag | None = None
        self._dead: DeadTime | None = None
        self._slow: Lag | None = None

    def correct(self, pv_pct: float, out_pct: float, dt: float) -> float:
        m = self.model
        if self._fast is None:
            self._fast = Lag(m.tau, out_pct)
            self._slow = Lag(m.tau, out_pct)
            self._dead = DeadTime(m.theta, dt, out_pct)
        undelayed = m.k * self._fast.step(out_pct, dt)
        delayed = m.k * self._slow.step(self._dead.step(out_pct), dt)
        return pv_pct + (undelayed - delayed)

    def set_theta(self, theta: float) -> None:
        """Adaptive deadtime: transport delay scales inversely with
        throughput, so the model's theta must follow the feed rate (the
        reference's 'adaptive' checkbox: theta 65 min at one feed, 131
        at half). Small drifts just update the model; a shift past 15 %
        rebuilds the delay line, which re-primes on the next scan - a
        brief, bounded transient that beats controlling on a model
        twice as wrong."""
        theta = max(theta, 0.0)
        if (self._dead is not None
                and abs(theta - self.model.theta)
                > 0.15 * max(self.model.theta, 1e-6)):
            self._fast = self._dead = self._slow = None
        self.model.theta = theta

    def capture_state(self) -> dict:
        if self._fast is None:
            return {}
        return {"f": self._fast.y, "s": self._slow.y,
                "d": self._dead.capture_state()
                if hasattr(self._dead, "capture_state") else None}

    def apply_state(self, s: dict) -> None:
        # a restored predictor simply re-primes on the next scan
        self._fast = self._dead = self._slow = None


class Inferential:
    """Build-your-own soft sensor, the reference's QC2 screen.

    ``terms`` is a list of (expr, coeff): ``"const"`` for the
    intercept, a tag name, or ``"TAG-A/TAG-B"`` for a ratio.
    ``transform`` names what the linear model FITS: ``"sqrt"`` means
    the combo predicts sqrt(Y), so the published value is its square.

    The published value is fast (tags plus algebra); the ANALYSER it
    stands in for is slow and discontinuous. ``theta``/``lag1``/
    ``lag2`` align a delayed copy of the inferential with the analyser
    clock, and each analyser UPDATE nudges a filtered bias
    (``bias_filter``, their P = 0.98) by the aligned mismatch - the
    lab keeps the soft sensor honest without ever slowing it down.
    """

    def __init__(self, terms, transform: str = "none",
                 bias_tag: str = "", bias_filter: float = 0.98,
                 theta: float = 0.0, lag1: float = 0.0,
                 lag2: float = 0.0) -> None:
        self.terms = [(str(e), float(c)) for e, c in terms]
        self.transform = transform
        self.bias_tag = bias_tag
        self.p = clamp(bias_filter, 0.0, 0.9999)
        self.theta, self.t1, self.t2 = theta, lag1, lag2
        self.bias = 0.0
        self.last = 0.0
        self._dead = None
        self._l1 = None
        self._l2 = None
        self._last_an = None

    def _linear(self, db) -> float:
        y = 0.0
        for expr, c in self.terms:
            if expr == "const":
                v = 1.0
            elif "/" in expr:
                a, b = expr.split("/", 1)
                v = float(db[a].value) / max(abs(float(db[b].value)), 1e-6)
            else:
                v = float(db[expr].value)
            y += c * v
        return y

    def value(self, db, dt: float) -> float:
        y = self._linear(db)
        if self.transform == "sqrt":
            raw = max(y, 0.0) ** 2
        elif self.transform == "log":
            raw = 10.0 ** clamp(y, -10.0, 10.0)
        else:
            raw = y
        if self._l1 is None:
            self._l1 = Lag(max(self.t1, 1e-6), raw)
            self._l2 = Lag(max(self.t2, 1e-6), raw)
            self._dead = DeadTime(self.theta, max(dt, 1e-3), raw)
        aligned = self._l2.step(self._l1.step(self._dead.step(raw), dt),
                                dt)
        if self.bias_tag:
            an = float(db[self.bias_tag].value)
            if (self._last_an is not None
                    and abs(an - self._last_an) > 1e-9):
                # the analyser updated: nudge the filtered bias by the
                # time-aligned mismatch
                self.bias = (self.p * self.bias
                             + (1.0 - self.p) * (an - aligned))
            self._last_an = an
        self.last = raw + self.bias
        return self.last


def dahlin_tuning(model: FOPDT, lam: float) -> tuple:
    """Dahlin synthesis for FOPDT reduces to PI constants plus the
    predictor: gain = tau / (K (lambda + theta)), reset = tau.
    Returns (gain, reset_seconds)."""
    g = model.tau / max(model.k * (lam + model.theta), 1e-9)
    return g, model.tau


def imc_tuning(model: FOPDT, lam: float) -> tuple:
    """IMC (lambda) tuning for FOPDT without a predictor:
    gain = tau / (K (lambda + theta)), reset = tau. The difference
    from Dahlin here is deployment: IMC runs on the raw PV with a
    conservative lambda; Dahlin pairs the same synthesis with the
    Smith predictor and can afford a small lambda."""
    return dahlin_tuning(model, lam)


def make_compensator(kind: str, model: FOPDT, lam: float):
    """Returns (predictor_or_None, gain, reset) for a loop adopting
    Smith, Dahlin or IMC on the given model. ``smith`` keeps the
    loop's existing tuning (predictor only) - pass gain/reset through
    by using the returned None values."""
    if kind == "smith":
        return SmithPredictor(model), None, None
    if kind == "dahlin":
        g, r = dahlin_tuning(model, lam)
        return SmithPredictor(model), g, r
    if kind == "imc":
        g, r = imc_tuning(model, lam)
        return None, g, r
    raise ValueError(f"unknown compensator {kind!r}")
