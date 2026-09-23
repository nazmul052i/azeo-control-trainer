"""Advanced scripting functions for ACT block expressions/scripts.

Provides process control, signal processing, logic, alarm, and utility
functions available in ACT block expressions and scripts.

Stateful functions use a ``state`` dict and ``key`` string for persistence
between scan cycles.  Pure functions have no state dependency.

Usage in ACT scripts::

    # Stateful — pass state dict + unique key
    filtered = ewma(state, 'pv_filt', IN1, 0.2)
    tripped  = rising_edge(state, 'start_btn', IN1 > 50)

    # Pure — no state needed
    limited  = clamp(OUT1, 0.0, 100.0)
    psi      = press_convert(3.5, 'bar', 'psi')
"""

from __future__ import annotations

import logging
import math
from typing import Any, Callable

log = logging.getLogger("strategy.script_functions")


# ═══════════════════════════════════════════════════════════════════════
#  Process Control
# ═══════════════════════════════════════════════════════════════════════

def clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* between *lo* and *hi* limits."""
    return max(lo, min(hi, float(value)))


def scale(value: float, in_lo: float, in_hi: float,
          out_lo: float, out_hi: float) -> float:
    """Linear scaling from input range [in_lo, in_hi] to [out_lo, out_hi]."""
    if in_hi == in_lo:
        return float(out_lo)
    ratio = (value - in_lo) / (in_hi - in_lo)
    return out_lo + ratio * (out_hi - out_lo)


def deadband(value: float, target: float, band: float) -> float:
    """Return *target* when *value* is within *band*, otherwise *value*."""
    if abs(value - target) <= band:
        return float(target)
    return float(value)


def hysteresis(state: dict, key: str, value: float,
               rising: float, falling: float) -> bool:
    """On/off output with separate rising and falling thresholds.

    Returns ``True`` when *value* >= *rising*, ``False`` when *value* <= *falling*.
    Between thresholds the previous state is held.
    """
    prev = state.get(key, False)
    if prev:
        result = value > falling
    else:
        result = value >= rising
    state[key] = result
    return result


def lerp(a: float, b: float, t: float) -> float:
    """Linear interpolation: ``a + t*(b - a)``  (t in 0-1)."""
    return a + t * (b - a)


def normalize(value: float, lo: float, hi: float) -> float:
    """Normalize *value* from [lo, hi] to [0, 1]."""
    if hi == lo:
        return 0.0
    return (value - lo) / (hi - lo)


def denormalize(value: float, lo: float, hi: float) -> float:
    """Map a 0-1 *value* back to [lo, hi] engineering units."""
    return lo + value * (hi - lo)


def remap(value: float, breakpoints: list, outputs: list) -> float:
    """Piecewise-linear characterisation curve.

    *breakpoints* and *outputs* must be lists of the same length, sorted
    in ascending order of breakpoints.
    """
    if not breakpoints or not outputs or len(breakpoints) != len(outputs):
        return float(value)
    if value <= breakpoints[0]:
        return float(outputs[0])
    if value >= breakpoints[-1]:
        return float(outputs[-1])
    for i in range(len(breakpoints) - 1):
        if breakpoints[i] <= value <= breakpoints[i + 1]:
            span = breakpoints[i + 1] - breakpoints[i]
            if span == 0:
                return float(outputs[i])
            t = (value - breakpoints[i]) / span
            return outputs[i] + t * (outputs[i + 1] - outputs[i])
    return float(outputs[-1])


# ═══════════════════════════════════════════════════════════════════════
#  Signal Processing  (stateful)
# ═══════════════════════════════════════════════════════════════════════

def ewma(state: dict, key: str, value: float, alpha: float = 0.1) -> float:
    """Exponential weighted moving average (first-order filter).

    *alpha*: smoothing factor 0-1 -- higher means less filtering.
    """
    prev = state.get(key, value)
    result = alpha * value + (1.0 - alpha) * prev
    state[key] = result
    return result


def rate_of_change(state: dict, key: str, value: float,
                   dt: float) -> float:
    """Rate of change (derivative).  Returns dvalue / dt."""
    prev = state.get(key, value)
    state[key] = value
    if dt <= 0:
        return 0.0
    return (value - prev) / dt


def integrate(state: dict, key: str, value: float, dt: float,
              lo: float | None = None, hi: float | None = None) -> float:
    """Accumulator / integrator with optional high/low limits."""
    accum = state.get(key, 0.0) + value * dt
    if hi is not None:
        accum = min(accum, hi)
    if lo is not None:
        accum = max(accum, lo)
    state[key] = accum
    return accum


def rate_limit(state: dict, key: str, value: float, dt: float,
               rising_rate: float, falling_rate: float | None = None) -> float:
    """Slew-rate limiter.  Limits how fast value can change per second.

    *rising_rate*: max increase per second.
    *falling_rate*: max decrease per second (defaults to rising_rate).
    """
    if falling_rate is None:
        falling_rate = rising_rate
    prev = state.get(key, value)
    if dt <= 0:
        state[key] = value
        return float(value)
    diff = value - prev
    max_rise = rising_rate * dt
    max_fall = falling_rate * dt
    if diff > max_rise:
        result = prev + max_rise
    elif diff < -max_fall:
        result = prev - max_fall
    else:
        result = value
    state[key] = result
    return result


# ═══════════════════════════════════════════════════════════════════════
#  PLC-style Logic  (stateful)
# ═══════════════════════════════════════════════════════════════════════

def rising_edge(state: dict, key: str, value: Any) -> bool:
    """One-shot on False -> True transition."""
    prev = state.get(key, False)
    state[key] = bool(value)
    return bool(value) and not prev


def falling_edge(state: dict, key: str, value: Any) -> bool:
    """One-shot on True -> False transition."""
    prev = state.get(key, True)
    state[key] = bool(value)
    return not bool(value) and prev


def sr_latch(state: dict, key: str, set_val: Any,
             reset_val: Any) -> bool:
    """Set/Reset flip-flop (reset has priority)."""
    if reset_val:
        state[key] = False
    elif set_val:
        state[key] = True
    return state.get(key, False)


def timer_on(state: dict, key: str, condition: Any,
             dt: float, delay: float) -> bool:
    """On-delay timer (TON).

    Output goes ``True`` after *condition* has been ``True`` for *delay* seconds.
    Resets immediately when *condition* becomes ``False``.
    """
    timer_key = f"{key}_t"
    if condition:
        elapsed = state.get(timer_key, 0.0) + dt
        state[timer_key] = elapsed
        result = elapsed >= delay
    else:
        state[timer_key] = 0.0
        result = False
    state[key] = result
    return result


def timer_off(state: dict, key: str, condition: Any,
              dt: float, delay: float) -> bool:
    """Off-delay timer (TOF).

    Output stays ``True`` for *delay* seconds after *condition* goes ``False``.
    """
    timer_key = f"{key}_t"
    if condition:
        state[timer_key] = 0.0
        result = True
    else:
        elapsed = state.get(timer_key, 0.0) + dt
        state[timer_key] = elapsed
        result = elapsed < delay
    state[key] = result
    return result


def counter(state: dict, key: str, trigger: Any,
            reset: bool = False, preset: int = 0) -> int:
    """Up-counter with preset.

    Increments on each rising edge of *trigger*.  Resets to 0 when *reset*
    is ``True``.  Returns the current count.
    """
    count_key = f"{key}_c"
    prev_key = f"{key}_p"
    if reset:
        state[count_key] = 0
        state[prev_key] = False
        state[key] = False
        return 0
    prev_trigger = state.get(prev_key, False)
    count = state.get(count_key, 0)
    if trigger and not prev_trigger:
        count += 1
    state[count_key] = count
    state[prev_key] = bool(trigger)
    done = count >= preset if preset > 0 else False
    state[key] = done
    return count


# ═══════════════════════════════════════════════════════════════════════
#  Alarm Functions  (stateful)
# ═══════════════════════════════════════════════════════════════════════

def alarm_hi_lo(state: dict, key: str, value: float,
                hi: float | None = None, lo: float | None = None,
                db: float = 0.0) -> tuple[bool, bool]:
    """High / Low alarm with deadband.

    Returns ``(hi_alarm, lo_alarm)``.  *db* is the alarm deadband --
    the alarm clears when the value moves back inside by *db*.
    """
    hi_key = f"{key}_hi"
    lo_key = f"{key}_lo"
    hi_alarm = state.get(hi_key, False)
    lo_alarm = state.get(lo_key, False)

    if hi is not None:
        hi_alarm = value > (hi - db) if hi_alarm else value >= hi
    else:
        hi_alarm = False

    if lo is not None:
        lo_alarm = value < (lo + db) if lo_alarm else value <= lo
    else:
        lo_alarm = False

    state[hi_key] = hi_alarm
    state[lo_key] = lo_alarm
    return (hi_alarm, lo_alarm)


def alarm_rate(state: dict, key: str, value: float,
               dt: float, limit: float) -> bool:
    """Rate-of-change alarm.  Returns ``True`` when |dv/dt| > *limit*."""
    roc = rate_of_change(state, f"{key}_roc", value, dt)
    return abs(roc) > limit


def alarm_dev(value: float, setpoint: float,
              dev_hi: float, dev_lo: float | None = None) -> tuple[bool, bool]:
    """Deviation alarm.  Returns ``(hi_dev, lo_dev)``.

    *dev_lo* defaults to *dev_hi* if not supplied (symmetric band).
    """
    if dev_lo is None:
        dev_lo = dev_hi
    deviation = value - setpoint
    return (deviation > dev_hi, deviation < -dev_lo)


# ═══════════════════════════════════════════════════════════════════════
#  Signal Selection
# ═══════════════════════════════════════════════════════════════════════

def select_hi(*values: float) -> float | None:
    """High select -- returns the highest non-None value."""
    valid = [v for v in values if v is not None]
    return max(valid) if valid else None


def select_lo(*values: float) -> float | None:
    """Low select -- returns the lowest non-None value."""
    valid = [v for v in values if v is not None]
    return min(valid) if valid else None


def select_mid(a: float, b: float, c: float) -> float:
    """Median select -- returns the middle of three values (sensor voting)."""
    return sorted([a, b, c])[1]


def first_good(*values: Any) -> Any:
    """Return the first non-None, non-NaN value."""
    for v in values:
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            return v
    return None


# ═══════════════════════════════════════════════════════════════════════
#  Valve Characterisation
# ═══════════════════════════════════════════════════════════════════════

def valve_eq_pct(position: float, rangeability: float = 50.0) -> float:
    """Equal-percentage valve characteristic.

    *position*: 0-1 (fraction open).
    *rangeability*: typical 50:1.
    """
    position = max(0.0, min(1.0, float(position)))
    if rangeability <= 1.0:
        return position
    return rangeability ** (position - 1.0)


def valve_quick_open(position: float) -> float:
    """Quick-opening valve characteristic.  *position*: 0-1."""
    position = max(0.0, min(1.0, float(position)))
    return math.sqrt(position)


def valve_installed(position: float, curve: str = "linear",
                    dp_ratio: float = 1.0,
                    rangeability: float = 50.0) -> float:
    """Installed valve characteristic accounting for pressure-drop ratio.

    *curve*: ``"linear"``, ``"equal_pct"``, or ``"quick_open"``.
    *dp_ratio*: valve dP / total system dP at full-open (0-1).
    """
    position = max(0.0, min(1.0, float(position)))
    if curve == "equal_pct":
        cv = valve_eq_pct(position, rangeability)
    elif curve == "quick_open":
        cv = valve_quick_open(position)
    else:
        cv = position  # linear

    if dp_ratio >= 1.0:
        return cv
    if cv <= 0:
        return 0.0
    return cv / math.sqrt(1.0 + (1.0 / dp_ratio - 1.0) * cv * cv)


# ═══════════════════════════════════════════════════════════════════════
#  Bit Manipulation
# ═══════════════════════════════════════════════════════════════════════

def get_bit(word: int, bit: int) -> bool:
    """Read single bit from integer word."""
    return bool(int(word) & _bit_mask(bit))


def set_bit(word: int, bit: int) -> int:
    """Set *bit* to 1, return new word."""
    return int(word) | _bit_mask(bit)


def clear_bit(word: int, bit: int) -> int:
    """Clear *bit* to 0, return new word."""
    return int(word) & ~_bit_mask(bit)


def toggle_bit(word: int, bit: int) -> int:
    """Flip *bit*, return new word."""
    return int(word) ^ _bit_mask(bit)


def _bit_mask(bit):
    bit = int(bit)
    if not 0 <= bit < 256:
        raise ValueError("Bit index must be between 0 and 255")
    return 1 << bit


def test_bits(word: int, mask: int) -> bool:
    """Test whether all bits in *mask* are set in *word*."""
    return (int(word) & int(mask)) == int(mask)


# ═══════════════════════════════════════════════════════════════════════
#  Unit Conversion
# ═══════════════════════════════════════════════════════════════════════

def temp_convert(value: float, from_unit: str, to_unit: str) -> float:
    """Temperature conversion.  Units: ``'C'``, ``'F'``, ``'K'``, ``'R'``."""
    fu = from_unit.upper()
    tu = to_unit.upper()
    # -> Celsius
    if fu == "C":
        c = value
    elif fu == "F":
        c = (value - 32.0) * 5.0 / 9.0
    elif fu == "K":
        c = value - 273.15
    elif fu == "R":
        c = (value - 491.67) * 5.0 / 9.0
    else:
        return float(value)
    # Celsius ->
    if tu == "C":
        return c
    if tu == "F":
        return c * 9.0 / 5.0 + 32.0
    if tu == "K":
        return c + 273.15
    if tu == "R":
        return (c + 273.15) * 9.0 / 5.0
    return float(value)


# Conversion factors -> kPa
_PRESS_TO_KPA: dict[str, float] = {
    "psi": 6.89476, "bar": 100.0, "kpa": 1.0,
    "atm": 101.325, "mmhg": 0.133322,
}


def press_convert(value: float, from_unit: str, to_unit: str) -> float:
    """Pressure conversion.  Units: ``'psi'``, ``'bar'``, ``'kpa'``, ``'atm'``, ``'mmhg'``."""
    fu = from_unit.lower()
    tu = to_unit.lower()
    if fu not in _PRESS_TO_KPA or tu not in _PRESS_TO_KPA:
        return float(value)
    kpa = value * _PRESS_TO_KPA[fu]
    return kpa / _PRESS_TO_KPA[tu]


def flow_sq_root(dp: float, k: float = 1.0,
                 low_cutoff: float = 0.1) -> float:
    """Compensated flow from differential pressure.

    ``flow = k * sqrt(dp)`` with linear interpolation below *low_cutoff*
    to avoid noise amplification near zero.
    """
    if dp < 0:
        return 0.0
    if dp < low_cutoff:
        return k * math.sqrt(low_cutoff) * (dp / low_cutoff)
    return k * math.sqrt(dp)


# ═══════════════════════════════════════════════════════════════════════
#  Timing / Generators  (stateful)
# ═══════════════════════════════════════════════════════════════════════

def pulse(state: dict, key: str, trigger: Any,
          dt: float, duration: float) -> bool:
    """One-shot pulse of fixed *duration* on rising edge of *trigger*."""
    timer_key = f"{key}_t"
    active_key = f"{key}_a"
    prev_key = f"{key}_p"
    prev = state.get(prev_key, False)
    active = state.get(active_key, False)
    if trigger and not prev:
        active = True
        state[timer_key] = 0.0
    state[prev_key] = bool(trigger)
    if active:
        elapsed = state.get(timer_key, 0.0) + dt
        state[timer_key] = elapsed
        if elapsed >= duration:
            active = False
    state[active_key] = active
    state[key] = active
    return active


def blink(state: dict, key: str, dt: float,
          on_time: float, off_time: float | None = None) -> bool:
    """Oscillator / square-wave generator.

    *off_time* defaults to *on_time* (50% duty cycle).
    """
    if off_time is None:
        off_time = on_time
    period = on_time + off_time
    if period <= 0:
        return False
    elapsed = state.get(f"{key}_t", 0.0) + dt
    elapsed = elapsed % period
    state[f"{key}_t"] = elapsed
    result = elapsed < on_time
    state[key] = result
    return result


def ramp(state: dict, key: str, target: float,
         dt: float, rate: float) -> float:
    """Linear ramp toward *target* at *rate* units per second."""
    current = state.get(key, target)
    if dt <= 0 or rate <= 0:
        state[key] = target
        return float(target)
    diff = target - current
    max_step = rate * dt
    if abs(diff) <= max_step:
        result = target
    elif diff > 0:
        result = current + max_step
    else:
        result = current - max_step
    state[key] = result
    return result


# ═══════════════════════════════════════════════════════════════════════
#  Data / Trending  (stateful)
# ═══════════════════════════════════════════════════════════════════════

def buffer_push(state: dict, key: str, value: Any,
                size: int = 100) -> list:
    """Push *value* onto a circular buffer of *size*.  Returns the buffer."""
    if type(size) is not int or not 1 <= size <= 512:
        raise ValueError("Buffer size must be an integer between 1 and 512")
    buf = state.get(key, [])
    buf.append(value)
    if len(buf) > size:
        buf = buf[-size:]
    state[key] = buf
    return buf


def peak_detect(state: dict, key: str, value: float,
                reset: bool = False) -> tuple[float, float]:
    """Track min/max since last *reset*.  Returns ``(min_val, max_val)``."""
    min_key = f"{key}_min"
    max_key = f"{key}_max"
    if reset or min_key not in state:
        state[min_key] = value
        state[max_key] = value
    else:
        if value < state[min_key]:
            state[min_key] = value
        if value > state[max_key]:
            state[max_key] = value
    return (state[min_key], state[max_key])


def time_avg(state: dict, key: str, value: float, dt: float) -> float:
    """Time-weighted running average."""
    sum_key = f"{key}_s"
    time_key = f"{key}_t"
    total = state.get(sum_key, 0.0) + value * dt
    total_time = state.get(time_key, 0.0) + dt
    state[sum_key] = total
    state[time_key] = total_time
    return total / total_time if total_time > 0 else float(value)


# ═══════════════════════════════════════════════════════════════════════
#  Statistical  (pure -- operate on lists)
# ═══════════════════════════════════════════════════════════════════════

def avg(values: list) -> float:
    """Arithmetic mean of *values*."""
    if not values:
        return 0.0
    return sum(values) / len(values)


def std_dev(values: list) -> float:
    """Sample standard deviation of *values*."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
    return math.sqrt(variance)


def median(values: list) -> float:
    """Median of *values*."""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    if n % 2 == 0:
        return (s[n // 2 - 1] + s[n // 2]) / 2.0
    return float(s[n // 2])


# ═══════════════════════════════════════════════════════════════════════
#  Safety / Voting
# ═══════════════════════════════════════════════════════════════════════

def vote_2oo3(a: Any, b: Any, c: Any) -> bool:
    """2-out-of-3 voting logic."""
    return (a and b) or (a and c) or (b and c)


def vote_1oo2(a: Any, b: Any) -> bool:
    """1-out-of-2 voting logic (either triggers)."""
    return bool(a) or bool(b)


def watchdog(state: dict, key: str, trigger: Any,
             dt: float, timeout: float) -> bool:
    """Watchdog timer.  Returns ``True`` (alarm) if *trigger* is not
    received within *timeout* seconds."""
    timer_key = f"{key}_t"
    if trigger:
        state[timer_key] = 0.0
        state[key] = False
        return False
    elapsed = state.get(timer_key, 0.0) + dt
    state[timer_key] = elapsed
    alarm = elapsed >= timeout
    state[key] = alarm
    return alarm


# ═══════════════════════════════════════════════════════════════════════
#  Math / Process
# ═══════════════════════════════════════════════════════════════════════

def poly(x: float, *coefficients: float) -> float:
    """Polynomial evaluation: ``a0 + a1*x + a2*x^2 + ...``"""
    result = 0.0
    for i, c in enumerate(coefficients):
        result += c * (x ** i)
    return result


def heat_duty(flow: float, cp: float, dt_temp: float) -> float:
    """Heat duty: ``Q = flow * Cp * dT``."""
    return flow * cp * dt_temp


# ═══════════════════════════════════════════════════════════════════════
#  Registry -- single dict of all functions for namespace injection
# ═══════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════
#  Plugin-extensible registry — register_script_function / decorator
# ═══════════════════════════════════════════════════════════════════════
#
# Plugins (or any module loaded at startup) can add domain-specific
# helpers that become callable from every ACT script — without editing
# this file. Names cannot collide with built-ins unless ``override=True``
# is passed explicitly; this keeps ACT scripts portable.
#
# Example::
#
#     from azeo_control_trainer.core.strategy.blocks.script_functions import (
#         register_script_function, script_function,
#     )
#
#     # Direct registration
#     register_script_function("te_economics",
#                              lambda steam, power: 0.4 * steam + 0.1 * power)
#
#     # Decorator form
#     @script_function("heater_combustion_eff")
#     def heater_combustion_eff(fuel_kgs, air_kgs):
#         ...

_USER_FUNCTIONS: dict[str, Callable[..., Any]] = {}


def register_script_function(name: str, fn: Callable[..., Any],
                              *, override: bool = False) -> None:
    """Add a function to the ACT script namespace.

    Args:
        name: Identifier visible inside scripts (e.g. ``"te_economics"``).
        fn:   The callable to expose. Should be pure-ish or take an explicit
              ``state`` dict if it needs persistence between scans (mirrors
              the built-in stateful convention).
        override: If True, allows replacing an already-registered name —
              including built-ins. Defaults False; collisions raise
              ``ValueError`` so accidental shadowing is caught early.

    Raises:
        ValueError: name collides with a built-in or another registered
            function and ``override`` is False.
        TypeError:  ``fn`` is not callable.
    """
    if not callable(fn):
        raise TypeError(f"register_script_function: fn must be callable, "
                         f"got {type(fn).__name__}")
    if not name or not isinstance(name, str):
        raise ValueError("register_script_function: name must be a non-empty string")
    builtins = _builtin_functions()
    if not override:
        if name in builtins:
            raise ValueError(
                f"register_script_function: '{name}' shadows a built-in. "
                f"Pass override=True to replace it.")
        if name in _USER_FUNCTIONS:
            raise ValueError(
                f"register_script_function: '{name}' is already registered. "
                f"Pass override=True to replace it.")
    _USER_FUNCTIONS[name] = fn
    log.info("Script function registered: %s", name)


def unregister_script_function(name: str) -> bool:
    """Remove a previously-registered function. Returns True if removed."""
    if name in _USER_FUNCTIONS:
        del _USER_FUNCTIONS[name]
        log.info("Script function unregistered: %s", name)
        return True
    return False


def script_function(name: str | None = None, *, override: bool = False):
    """Decorator form of :func:`register_script_function`.

    Usage::

        @script_function()                    # uses fn.__name__
        def my_helper(x): ...

        @script_function("alias_name")        # custom name
        def my_helper(x): ...
    """
    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        reg_name = name or fn.__name__
        register_script_function(reg_name, fn, override=override)
        return fn
    return deco


def list_registered() -> dict[str, Callable[..., Any]]:
    """Return a shallow copy of the user-registered function dict."""
    return dict(_USER_FUNCTIONS)


def clear_registered() -> None:
    """Drop all user-registered functions. Built-ins stay. (Mostly for tests.)"""
    _USER_FUNCTIONS.clear()


def _builtin_functions() -> dict[str, Any]:
    """Return the hardcoded built-in function dict (no user extensions)."""
    return {
        # Process Control
        "clamp": clamp,
        "scale": scale,
        "deadband": deadband,
        "hysteresis": hysteresis,
        "lerp": lerp,
        "normalize": normalize,
        "denormalize": denormalize,
        "remap": remap,
        # Signal Processing
        "ewma": ewma,
        "rate_of_change": rate_of_change,
        "integrate": integrate,
        "rate_limit": rate_limit,
        # PLC-style Logic
        "rising_edge": rising_edge,
        "falling_edge": falling_edge,
        "sr_latch": sr_latch,
        "timer_on": timer_on,
        "timer_off": timer_off,
        "counter": counter,
        # Alarm Functions
        "alarm_hi_lo": alarm_hi_lo,
        "alarm_rate": alarm_rate,
        "alarm_dev": alarm_dev,
        # Signal Selection
        "select_hi": select_hi,
        "select_lo": select_lo,
        "select_mid": select_mid,
        "first_good": first_good,
        # Valve Characterisation
        "valve_eq_pct": valve_eq_pct,
        "valve_quick_open": valve_quick_open,
        "valve_installed": valve_installed,
        # Bit Manipulation
        "get_bit": get_bit,
        "set_bit": set_bit,
        "clear_bit": clear_bit,
        "toggle_bit": toggle_bit,
        "test_bits": test_bits,
        # Unit Conversion
        "temp_convert": temp_convert,
        "press_convert": press_convert,
        "flow_sq_root": flow_sq_root,
        # Timing / Generators
        "pulse": pulse,
        "blink": blink,
        "ramp": ramp,
        # Data / Trending
        "buffer_push": buffer_push,
        "peak_detect": peak_detect,
        "time_avg": time_avg,
        # Statistical
        "avg": avg,
        "std_dev": std_dev,
        "median": median,
        # Safety / Voting
        "vote_2oo3": vote_2oo3,
        "vote_1oo2": vote_1oo2,
        "watchdog": watchdog,
        # Math / Process
        "poly": poly,
        "heat_duty": heat_duty,
    }


def get_script_functions() -> dict[str, Any]:
    """Return built-ins merged with plugin-registered functions.

    Plugin extensions registered via :func:`register_script_function`
    appear in the returned dict alongside built-ins. When a name is
    registered with ``override=True``, the user version wins.
    """
    out = _builtin_functions()
    out.update(_USER_FUNCTIONS)
    return out
