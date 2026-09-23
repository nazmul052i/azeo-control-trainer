"""Azeo PID function block.

Implemented to ``docs/FB_FP/PID_FB_ref.pdf``. The parts of the block a
regulatory layer actually lives on are all here and behave as the reference
describes them:

* the eight mode enumeration, with the actual mode shedding to Man on a bad
  PV and the operator's target mode preserved,
* the standard form equation with GAIN normalised to percent of scale,
  RESET and RATE in seconds,
* the reset implemented as a positive feedback network, which is what gives
  external reset (dynamic reset limiting) its anti-windup behaviour for free:
  feed the network a slave's BKCAL_OUT and a limited slave stops the master's
  integral exactly as the reference promises,
* STRUCTURE selection via the BETA and GAMMA two-degrees-of-freedom weights,
  to the table on page 10 of the reference,
* SP and OUT limits, SP rate limiting, direct or reverse action, SP-PV
  tracking in Man, feedforward with FF_GAIN, PV filtering,
* BKCAL_OUT publishing the working setpoint (or PV, per control option) so a
  master initialises bumplessly when its slave leaves Cas,
* HI_HI/HI/LO/LO_LO and deviation alarms with hysteresis, deviation alarms
  suppressed after a setpoint change as the reference requires.

Deliberately not carried over: PIDPlus (wireless exception reporting),
fieldbus shadow blocks, and adaptive tuning. None of them participate in
closed loop behaviour at a fixed scan.

The arithmetic is bounded at every step - the block obeys the same
unconditional stability rule as the process models it commands.
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass, field
from typing import Optional

from ..core.dynamics import Lag, clamp


class Mode(enum.Enum):
    """The controller mode set. Order reflects the usual shed hierarchy."""
    OOS = "OOS"
    IMAN = "IMan"
    LO = "LO"
    MAN = "Man"
    AUTO = "Auto"
    CAS = "Cas"
    RCAS = "RCas"
    ROUT = "ROut"


class Structure(enum.Enum):
    """STRUCTURE selections, as (beta, gamma, integral) per the reference."""
    PID_ON_ERROR = "PID action on error"
    PI_ERROR_D_PV = "PI action on error, D action on PV"
    I_ERROR_PD_PV = "I action on error, PD action on PV"
    PD_ON_ERROR = "PD action on error"
    P_ERROR_D_PV = "P action on error, D action on PV"
    ID_ON_ERROR = "ID action on error"
    I_ERROR_D_PV = "I action on error, D action on PV"
    TWO_DEGREES = "Two degrees of freedom"


# beta (P on SP weight), gamma (D on SP weight), has_P, has_I, has_D
_STRUCTURE_TABLE = {
    Structure.PID_ON_ERROR:  (1.0, 1.0, True, True, True),
    Structure.PI_ERROR_D_PV: (1.0, 0.0, True, True, True),
    Structure.I_ERROR_PD_PV: (0.0, 0.0, True, True, True),
    Structure.PD_ON_ERROR:   (1.0, 1.0, True, False, True),
    Structure.P_ERROR_D_PV:  (1.0, 0.0, True, False, True),
    Structure.ID_ON_ERROR:   (None, 1.0, False, True, True),
    Structure.I_ERROR_D_PV:  (None, 0.0, False, True, True),
    Structure.TWO_DEGREES:   (None, None, True, True, True),
}


@dataclass
class Alarms:
    hi_hi: bool = False
    hi: bool = False
    lo: bool = False
    lo_lo: bool = False
    dv_hi: bool = False
    dv_lo: bool = False

    def any_active(self) -> bool:
        return any((self.hi_hi, self.hi, self.lo, self.lo_lo,
                    self.dv_hi, self.dv_lo))


@dataclass
class PID:
    """One PID function block. Scaling, tuning and options mirror the reference."""

    name: str = "PID"
    description: str = ""
    # ------------------------------------------------------------- scaling
    pv_eu0: float = 0.0
    pv_eu100: float = 100.0
    out_eu0: float = 0.0
    out_eu100: float = 100.0
    # -------------------------------------------------------------- tuning
    gain: float = 1.0                 # normalised, percent of span per percent
    reset: float = 100.0              # seconds; 0 or inf disables integral
    rate: float = 0.0                 # seconds
    structure: Structure = Structure.PI_ERROR_D_PV
    beta: float = 1.0                 # two degrees of freedom weights
    gamma: float = 0.0
    pv_ftime: float = 0.0             # PV filter, seconds
    sp_ftime: float = 0.0             # SP filter, seconds
    # -------------------------------------------------------------- limits
    sp_hi_lim: float = math.inf
    sp_lo_lim: float = -math.inf
    sp_rate_up: float = 0.0           # EU/s, 0 disables
    sp_rate_dn: float = 0.0
    out_hi_lim: float = 100.0         # percent of OUT scale
    out_lo_lim: float = 0.0
    # ------------------------------------------------------------- options
    direct_acting: bool = False       # CONTROL_OPTS Direct Acting
    sp_pv_track_in_man: bool = True   # CONTROL_OPTS SP-PV Track in Man
    use_pv_for_bkcal: bool = False    # CONTROL_OPTS Use PV for BKCAL_OUT
    ff_enable: bool = False
    ff_gain: float = 0.0
    # -------------------------------------------------------------- alarms
    hi_hi_lim: float = math.inf
    hi_lim: float = math.inf
    lo_lim: float = -math.inf
    lo_lo_lim: float = -math.inf
    dv_hi_lim: float = math.inf
    dv_lo_lim: float = -math.inf
    alarm_hys: float = 0.5            # percent of PV span
    # Anti-reset-windup limits: the reset network is held inside these rather
    # than the OUT limits, which is what the ARW rows on the detail display
    # adjust. They default to the OUT limits, to keep manual and automatic output bounds consistent.
    arw_hi_lim: float = 100.0
    arw_lo_lim: float = 0.0
    # SIMULATE: the block runs on this value instead of the wired PV. Field
    # value and status stay visible on the display, per the reference.
    simulate_enable: bool = False
    simulate_value: float = 0.0

    # --------------------------------------------------------------- state
    target_mode: Mode = Mode.MAN
    sp: float = 0.0                   # entered setpoint, engineering units
    sp_wrk: float = 0.0               # working setpoint after limits and rate
    out: float = 0.0                  # percent of OUT scale
    pv: float = 0.0
    pv_good: bool = True
    field_value: float = 0.0          # the wired PV before SIMULATE
    field_good: bool = True
    alarms: Alarms = field(default_factory=Alarms)
    # per-alarm enable and shelve, driven from the detail display
    alarm_enab: dict = field(default_factory=lambda: {
        n: True for n in ("hi_hi", "hi", "lo", "lo_lo", "dv_hi", "dv_lo")})
    alarm_shelved: dict = field(default_factory=dict)
    # per-alarm on-delay in seconds, from the alarm table: the condition
    # must stand this long before the block's own indication shows, so a
    # faceplate never annunciates ahead of the alarm system. Assigned as
    # a whole dict: the native block hands out a copy of its map.
    alarm_delay: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._pv_filter: Optional[Lag] = (
            Lag(self.pv_ftime, self.pv) if self.pv_ftime > 0.0 else None)
        # The filter state is garbage until the first real measurement
        # arrives: built at construction it holds zero, and a whole plant
        # of loops reading PV = 0 for the first second of a closure slams
        # every cascade at once. Primed on the first step, and re-primed
        # after a state restore.
        self._pv_filter_primed = False
        # The reset positive-feedback network: OUT = P + lag(feedback), with
        # the lag time equal to RESET. Feeding the network the block's own
        # output gives classic integral action; feeding it a downstream
        # BKCAL gives external reset. Page 11 of the reference, literally.
        self._reset_fb = Lag(max(self.reset, 1e-3), self.out)
        self._d_prev: Optional[float] = None
        self._sp_suppress_dev = 0.0
        self._alarm_timer: dict = {}
        self._out_limited = ""        # '', 'high', 'low' for BKCAL status
        self._actual = Mode.MAN
        self.sp_wrk = self.sp
        self._was_closed = False      # for initialization on mode entry

    # ------------------------------------------------------------ scaling
    @property
    def pv_span(self) -> float:
        return max(self.pv_eu100 - self.pv_eu0, 1e-9)

    def _pct(self, eu: float) -> float:
        return (eu - self.pv_eu0) / self.pv_span * 100.0

    @property
    def out_eu(self) -> float:
        return self.out_eu0 + self.out / 100.0 * (self.out_eu100 - self.out_eu0)

    # ------------------------------------------------------------- modes
    @property
    def actual_mode(self) -> Mode:
        return self._actual

    def set_mode(self, mode: Mode) -> None:
        self.target_mode = mode

    @property
    def bkcal_out(self) -> float:
        """Value handed to an upstream master, in this block's PV units."""
        return self.pv if self.use_pv_for_bkcal else self.sp_wrk

    @property
    def bkcal_limit(self) -> str:
        """'high'/'low' when this block cannot move further that way."""
        if self._actual not in (Mode.AUTO, Mode.CAS, Mode.RCAS):
            return "constant"
        return self._out_limited

    # --------------------------------------------------------------- scan
    def step(self, dt: float, pv: float, pv_good: bool = True,
             cas_in: Optional[float] = None,
             bkcal_in: Optional[float] = None,
             bkcal_in_limit: str = "",
             ff_val: float = 0.0) -> float:
        """One block execution. Returns OUT in percent of OUT scale."""
        self.field_value, self.field_good = pv, pv_good
        if self.simulate_enable:
            pv, pv_good = self.simulate_value, True
        if self._pv_filter is not None:
            if not self._pv_filter_primed:
                self._pv_filter.reset(pv)
                self._pv_filter_primed = True
            pv = self._pv_filter.step(pv, dt)
        self.pv, self.pv_good = pv, pv_good

        # ------------------------------------------------- mode resolution
        # A bad PV sheds the ACTUAL mode to Man; the operator's target is
        # untouched, so control resumes when the measurement returns.
        if self.target_mode is Mode.OOS:
            self._actual = Mode.OOS
            return self.out
        want = self.target_mode
        if not pv_good and want in (Mode.AUTO, Mode.CAS, Mode.RCAS):
            self._actual = Mode.MAN
        else:
            self._actual = want
        mode = self._actual

        # ------------------------------------------------ setpoint selection
        # The operator (or master) owns SP; the block owns SP_WRK, which is
        # what the equation, the alarms and BKCAL_OUT all use. Limits and the
        # SP rate ride on the working value, so a stepped entry walks.
        if mode in (Mode.CAS, Mode.RCAS) and cas_in is not None:
            self.sp = cas_in
        elif mode is Mode.MAN and self.sp_pv_track_in_man:
            self.sp = pv
        sp_target = clamp(self.sp, self.sp_lo_lim, self.sp_hi_lim)
        # The rate limit shapes changes made while the loop is closed. On
        # entry it must not apply, or the working setpoint would spend minutes
        # walking up from wherever the block was built.
        if self._was_closed and mode in (Mode.AUTO, Mode.CAS, Mode.RCAS) and (
                self.sp_rate_up > 0.0 or self.sp_rate_dn > 0.0):
            step_up = (self.sp_rate_up or math.inf) * dt
            step_dn = (self.sp_rate_dn or math.inf) * dt
            sp_target = clamp(sp_target, self.sp_wrk - step_dn,
                              self.sp_wrk + step_up)
        if self.sp_ftime > 0.0 and self._was_closed:
            a = 1.0 - math.exp(-dt / max(self.sp_ftime, 1e-6))
            sp_target = self.sp_wrk + (sp_target - self.sp_wrk) * a
        self.sp_wrk = sp_target

        self._alarm_scan(dt)

        if mode in (Mode.MAN, Mode.ROUT, Mode.LO, Mode.IMAN):
            # Operator (or tracking) owns OUT. Keep the reset network
            # initialised to the live output so the return to Auto is
            # bumpless: on the first closed scan P is added to a feedback
            # term that already equals what the plant is receiving.
            self.out = clamp(self.out, self.out_lo_lim, self.out_hi_lim)
            self._d_prev = None
            self._out_limited = ""
            self._was_closed = False
            return self.out

        # --------------------------------------------------- PID equation
        b, g, has_p, has_i, has_d = _STRUCTURE_TABLE[self.structure]
        beta = self.beta if b is None else b
        gamma = self.gamma if g is None else g

        sp_pct, pv_pct = self._pct(self.sp_wrk), self._pct(pv)
        # Reverse acting (the default) drives OUT up as PV falls below SP;
        # direct acting is the mirror. One sign carries it through P and D so
        # the two-degrees-of-freedom weights stay honest for both actions.
        sign = 1.0 if self.direct_acting else -1.0
        p_term = self.gain * sign * (pv_pct - beta * sp_pct)

        d_term = 0.0
        if has_d and self.rate > 0.0:
            d_input = sign * (pv_pct - gamma * sp_pct)
            if self._d_prev is None:
                self._d_prev = d_input
            # Derivative with the standard 1/8 filter so a noisy PV cannot
            # slam the output on a single scan.
            alpha = dt / max(self.rate / 8.0, dt)
            d_raw = (d_input - self._d_prev) / max(dt, 1e-9)
            d_term = self.gain * self.rate * d_raw * min(alpha, 1.0)
            self._d_prev = d_input

        if not has_p:
            p_term = 0.0

        # Initialization on entering closed loop: the reset network is set so
        # the first closed scan reproduces the output the plant was already
        # receiving, whatever the structure's P term happens to be. This is
        # the bumpless transfer the reference promises for every structure,
        # not only the ones whose P term is zero at zero error.
        ff = (self.ff_gain * ff_val) if self.ff_enable else 0.0

        # ------------------------------------------- reset (integral) term
        if has_i and self.reset > 0.0 and math.isfinite(self.reset):
            self._reset_fb.tau = max(self.reset, 1e-3)
            # External reset: what feeds back is the downstream block's
            # achieved value when one is wired, else this block's own OUT.
            # The feedforward is taken back OUT of the feedback before the
            # lag: the achieved value already carries the FF's effect, and
            # integrating it a second time ramps the output by the whole FF
            # contribution once per reset time. This is what put the boiler
            # drum's standing level offset there before it was fixed.
            feedback = self.out if bkcal_in is None else bkcal_in
            i_term = self._reset_fb.step(feedback - ff, dt)
        else:
            i_term = self._reset_fb.y   # frozen: P/PD structures hold bias

        # Initialization on entering closed loop: the reset network is set so
        # the first closed scan reproduces the output the plant was already
        # receiving, whatever the structure's P term or the feedforward
        # happen to contribute. Bumpless for every structure, as promised.
        if not self._was_closed:
            self._reset_fb.reset(self.out - p_term - ff)
            i_term = self._reset_fb.y
            self._was_closed = True

        unlimited = p_term + d_term + i_term + ff
        self.out = clamp(unlimited, self.out_lo_lim, self.out_hi_lim)
        self._out_limited = ("high" if unlimited > self.out_hi_lim - 1e-9 else
                             "low" if unlimited < self.out_lo_lim + 1e-9 else "")
        # Clamped integral action: when we hit our own limit (and no external
        # reset is wired) the feedback network must not integrate past the
        # anti-reset-windup band.
        if bkcal_in is None and self._out_limited:
            self._reset_fb.reset(clamp(self._reset_fb.y,
                                       self.arw_lo_lim - abs(p_term) - 1.0,
                                       self.arw_hi_lim + abs(p_term) + 1.0))
        # A limited downstream block stops the reset the same way: the lag
        # simply converges on the slave's achieved value and goes no further.
        return self.out

    # -------------------------------------------------------------- alarms
    def _alarm_scan(self, dt: float) -> None:
        hys = self.alarm_hys / 100.0 * self.pv_span
        a, pv = self.alarms, self.pv

        def latch(active: bool, level: float, above: bool) -> bool:
            if above:
                return pv >= level if not active else pv > level - hys
            return pv <= level if not active else pv < level + hys

        def timed(key: str, cond: bool) -> bool:
            # the on-delay: the condition must stand for its delay before
            # the indication shows, and the timer clears the moment it drops
            delay = float(self.alarm_delay.get(key, 0.0) or 0.0)
            if not cond:
                self._alarm_timer[key] = 0.0
                return False
            if delay <= 0.0:
                return True
            held = min(self._alarm_timer.get(key, 0.0) + dt, delay)
            self._alarm_timer[key] = held
            return held >= delay

        en = self.alarm_enab
        a.hi_hi = timed("hi_hi", latch(a.hi_hi, self.hi_hi_lim, True)) and en.get("hi_hi", True)
        a.hi = timed("hi", latch(a.hi, self.hi_lim, True)) and en.get("hi", True)
        a.lo = timed("lo", latch(a.lo, self.lo_lim, False)) and en.get("lo", True)
        a.lo_lo = timed("lo_lo", latch(a.lo_lo, self.lo_lo_lim, False)) and en.get("lo_lo", True)

        # Deviation alarms hold off after a setpoint change until the PV has
        # come back inside the band once, per the reference.
        self._sp_suppress_dev = max(0.0, self._sp_suppress_dev - dt)
        dev = pv - self.sp_wrk
        if self._sp_suppress_dev <= 0.0:
            a.dv_hi = timed("dv_hi", latch(a.dv_hi, self.sp_wrk + self.dv_hi_lim, True)
                            if math.isfinite(self.dv_hi_lim) else False)
            a.dv_lo = timed("dv_lo", latch(a.dv_lo, self.sp_wrk + self.dv_lo_lim, False)
                            if math.isfinite(self.dv_lo_lim) else False)
        elif abs(dev) < min(abs(self.dv_hi_lim), abs(self.dv_lo_lim)):
            self._sp_suppress_dev = 0.0

    def note_sp_change(self, seconds: float = 60.0) -> None:
        """Call when the SP is stepped, to suppress deviation alarms."""
        self._sp_suppress_dev = seconds

    # --------------------------------------------------------- persistence
    def capture_state(self) -> dict:
        return {"sp": self.sp, "spw": self.sp_wrk, "out": self.out,
                "mode": self.target_mode.value,
                "pv": self.pv, "pvg": self.pv_good,
                "fb": self._reset_fb.y, "closed": self._was_closed,
                "dprev": self._d_prev, "sup": self._sp_suppress_dev,
                "gain": self.gain, "reset": self.reset, "rate": self.rate,
                "sim": self.simulate_enable, "simv": self.simulate_value,
                "arwh": self.arw_hi_lim, "arwl": self.arw_lo_lim,
                # The setpoint walk limits are commissioned state, not
                # configuration: the column masters are clamped to a band
                # around the adopted operating point at seed time, and a
                # restore that drops them hands the cascade the raw span.
                "splo": self.sp_lo_lim, "sphi": self.sp_hi_lim,
                "spft": self.sp_ftime, "enab": dict(self.alarm_enab),
                "shlv": dict(self.alarm_shelved),
                "adly": dict(self.alarm_delay), "atmr": dict(self._alarm_timer)}

    def apply_state(self, s: dict) -> None:
        self.sp = float(s.get("sp", self.sp))
        self.sp_wrk = float(s.get("spw", self.sp))
        self.out = float(s.get("out", self.out))
        # the restored plant's first scan re-primes the PV filter
        self._pv_filter_primed = False
        try:
            self.target_mode = Mode(s.get("mode", self.target_mode.value))
        except ValueError:
            pass
        self.pv = float(s.get("pv", self.pv))
        self.pv_good = bool(s.get("pvg", True))
        self._reset_fb.reset(float(s.get("fb", self.out)))
        self._was_closed = bool(s.get("closed", False))
        d = s.get("dprev")
        self._d_prev = float(d) if d is not None else None
        self._sp_suppress_dev = float(s.get("sup", 0.0))
        self.gain = float(s.get("gain", self.gain))
        self.reset = float(s.get("reset", self.reset))
        self.rate = float(s.get("rate", self.rate))
        self.simulate_enable = bool(s.get("sim", False))
        self.simulate_value = float(s.get("simv", self.simulate_value))
        self.arw_hi_lim = float(s.get("arwh", self.arw_hi_lim))
        self.arw_lo_lim = float(s.get("arwl", self.arw_lo_lim))
        # absent in snapshots made before the limits were carried: keep
        # whatever the strategy built (the 16.5 t/h steam cap included)
        self.sp_lo_lim = float(s.get("splo", self.sp_lo_lim))
        self.sp_hi_lim = float(s.get("sphi", self.sp_hi_lim))
        self.sp_ftime = float(s.get("spft", self.sp_ftime))
        if isinstance(s.get("enab"), dict):
            self.alarm_enab.update({k: bool(v) for k, v in s["enab"].items()})
        if isinstance(s.get("shlv"), dict):
            self.alarm_shelved = {k: bool(v) for k, v in s["shlv"].items()}
        if isinstance(s.get("adly"), dict):
            self.alarm_delay = {k: float(v) for k, v in s["adly"].items()}
        self._alarm_timer = ({k: float(v) for k, v in s["atmr"].items()}
                             if isinstance(s.get("atmr"), dict) else {})
