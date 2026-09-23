"""Dynamic primitives.

Every element here is **unconditionally stable** for any positive time step. That
is a deliberate constraint: an operator training simulator that blows up when the
instructor sets 5x speed is worthless, and explicit integration of a fast lag is
the usual cause.

* :class:`Lag` uses the exact zero-order-hold solution of a first order system,
  so it cannot oscillate or diverge however large ``dt`` becomes.
* :class:`DeadTime` is a ring buffer, which is exact.
* :class:`Integrator` clamps to physical limits on every step, so a runaway
  input cannot drive a level to 1e300.
* :class:`Noise` is band-limited, otherwise the apparent noise amplitude would
  change with the step size and tuning exercises would not transfer.
"""

from __future__ import annotations

import base64
import logging
import math
import random
import struct
from collections import deque
from typing import Deque


log = logging.getLogger(__name__)

def clamp(x: float, lo: float, hi: float) -> float:
    if not math.isfinite(x):
        return lo
    return lo if x < lo else (hi if x > hi else x)


def lerp(a: float, b: float, f: float) -> float:
    return a + (b - a) * clamp(f, 0.0, 1.0)


def safe_sqrt(x: float) -> float:
    """Square root that never raises on a marginally negative argument."""
    return math.sqrt(x) if x > 0.0 else 0.0


def safe_div(num: float, den: float, default: float = 0.0, eps: float = 1e-9) -> float:
    return num / den if abs(den) > eps else default


class Lag:
    """First order lag, exact discrete form ``y += (u - y) * (1 - exp(-dt/tau))``."""

    __slots__ = ("tau", "y")

    def __init__(self, tau: float, y0: float = 0.0) -> None:
        self.tau = max(float(tau), 1e-6)
        self.y = float(y0)

    def step(self, u: float, dt: float) -> float:
        if not math.isfinite(u):
            return self.y
        alpha = 1.0 - math.exp(-max(dt, 0.0) / self.tau)
        self.y += (u - self.y) * alpha
        return self.y

    def reset(self, y0: float) -> None:
        self.y = float(y0)

    def capture_state(self) -> dict:
        return {"y": self.y}

    def apply_state(self, s: dict) -> None:
        self.y = float(s.get("y", self.y))


class LeadLag:
    """Lead-lag element, used for feedforward dynamic compensation."""

    __slots__ = ("lead", "lag", "y", "u_prev")

    def __init__(self, lead: float, lag: float, y0: float = 0.0) -> None:
        self.lead = max(float(lead), 0.0)
        self.lag = max(float(lag), 1e-6)
        self.y = float(y0)
        self.u_prev = float(y0)

    def step(self, u: float, dt: float) -> float:
        alpha = 1.0 - math.exp(-max(dt, 0.0) / self.lag)
        du = (u - self.u_prev) / max(dt, 1e-6)
        self.y += (u + self.lead * du - self.y) * alpha
        self.u_prev = u
        return self.y

    def capture_state(self) -> dict:
        return {"y": self.y, "u": self.u_prev}

    def apply_state(self, s: dict) -> None:
        self.y = float(s.get("y", self.y))
        self.u_prev = float(s.get("u", self.y))


class DeadTime:
    """Pure transport delay implemented as a ring buffer."""

    __slots__ = ("delay", "dt", "buf", "_y0")

    def __init__(self, delay: float, dt: float, y0: float = 0.0) -> None:
        self.delay = max(float(delay), 0.0)
        self.dt = max(float(dt), 1e-6)
        self._y0 = float(y0)
        n = max(1, int(round(self.delay / self.dt)))
        self.buf: Deque[float] = deque([float(y0)] * n, maxlen=n)

    def step(self, u: float) -> float:
        out = self.buf[0]
        self.buf.append(float(u) if math.isfinite(u) else out)
        return out

    def reset(self, y0: float) -> None:
        self.buf = deque([float(y0)] * self.buf.maxlen, maxlen=self.buf.maxlen)

    def capture_state(self) -> dict:
        return {"buf": list(self.buf)}

    def apply_state(self, s: dict) -> None:
        """Refill the delay line, tolerating a buffer sized for a different dt.

        A shorter saved buffer is padded at the old end and a longer one is
        truncated there, so the most recent history is always the part kept.
        """
        buf = [float(v) for v in s.get("buf", ()) if isinstance(v, (int, float))]
        n = self.buf.maxlen
        if not buf:
            return
        if len(buf) < n:
            buf = [buf[0]] * (n - len(buf)) + buf
        self.buf = deque(buf[-n:], maxlen=n)


class RateLimiter:
    """Symmetric or asymmetric rate limit in units per second."""

    __slots__ = ("up", "down", "y")

    def __init__(self, up: float, down: float | None = None, y0: float = 0.0) -> None:
        self.up = abs(float(up))
        self.down = abs(float(down)) if down is not None else self.up
        self.y = float(y0)

    def step(self, u: float, dt: float) -> float:
        if not math.isfinite(u):
            return self.y
        delta = u - self.y
        if delta > 0:
            self.y += min(delta, self.up * dt)
        else:
            self.y += max(delta, -self.down * dt)
        return self.y

    def reset(self, y0: float) -> None:
        self.y = float(y0)

    def capture_state(self) -> dict:
        return {"y": self.y}

    def apply_state(self, s: dict) -> None:
        self.y = float(s.get("y", self.y))


class Integrator:
    """Bounded integrator. Saturation is hard, so states stay physical."""

    __slots__ = ("y", "lo", "hi", "saturated")

    def __init__(self, y0: float = 0.0, lo: float = -1e9, hi: float = 1e9) -> None:
        self.lo, self.hi = float(lo), float(hi)
        self.y = clamp(float(y0), self.lo, self.hi)
        self.saturated = False

    def step(self, rate: float, dt: float) -> float:
        if not math.isfinite(rate):
            return self.y
        raw = self.y + rate * dt
        self.y = clamp(raw, self.lo, self.hi)
        self.saturated = raw != self.y
        return self.y

    def reset(self, y0: float) -> None:
        self.y = clamp(float(y0), self.lo, self.hi)
        self.saturated = False

    def capture_state(self) -> dict:
        return {"y": self.y, "sat": bool(self.saturated)}

    def apply_state(self, s: dict) -> None:
        self.y = clamp(float(s.get("y", self.y)), self.lo, self.hi)
        self.saturated = bool(s.get("sat", False))


class Noise:
    """Band-limited Gaussian noise.

    The output standard deviation equals ``sigma`` regardless of the
    integration step: the raw sample is scaled to compensate the variance the
    lag filter removes, which for a first order lag is a factor of
    ``alpha / (2 - alpha)`` with ``alpha = 1 - exp(-dt/tau)``. Without the
    compensation the apparent noise amplitude would change with the step size
    and tuning exercises would not transfer.
    """

    __slots__ = ("sigma", "lag", "_rng")

    def __init__(self, sigma: float = 0.0, bandwidth: float = 2.0,
                 seed: int | None = None) -> None:
        self.sigma = float(sigma)
        self.lag = Lag(1.0 / max(bandwidth, 1e-3))
        self._rng = random.Random(seed)

    def step(self, dt: float) -> float:
        if self.sigma <= 0.0:
            return 0.0
        alpha = 1.0 - math.exp(-max(dt, 1e-9) / self.lag.tau)
        scale = math.sqrt((2.0 - alpha) / max(alpha, 1e-12))
        raw = self._rng.gauss(0.0, self.sigma) * scale
        return self.lag.step(raw, dt)

    def capture_state(self) -> dict:
        """Filtered output plus the generator position.

        The position is what makes a restored run continue the *same* trend
        rather than a different realisation of the same statistics. Mersenne
        state is 625 words, so it is packed and base64 encoded rather than
        written as an array of integers that would dominate the file.
        """
        state = {"y": self.lag.y}
        try:
            version, words, gauss = self._rng.getstate()
            state["rng"] = base64.b64encode(
                struct.pack(f"<{len(words)}I", *words)).decode("ascii")
            state["rng_v"] = int(version)
            if gauss is not None:
                state["rng_g"] = float(gauss)
        except Exception:                      # never lose a snapshot over noise
            pass
        return state

    def apply_state(self, s: dict) -> None:
        self.lag.y = float(s.get("y", self.lag.y))
        blob = s.get("rng")
        if not blob:
            return
        try:
            raw = base64.b64decode(blob)
            words = struct.unpack(f"<{len(raw) // 4}I", raw)
            self._rng.setstate((int(s.get("rng_v", 3)), tuple(words),
                                s.get("rng_g")))
        except Exception:
            # A snapshot from another Python build can carry an incompatible
            # generator state. The plant is still exact; only the noise
            # realisation restarts, so this is not worth failing the load for.
            log.debug("Could not restore noise generator state", exc_info=True)


_MASK64 = 0xFFFFFFFFFFFFFFFF
_TWO_POW_M53 = 1.0 / 9007199254740992.0


class Xorshift:
    """xorshift64* with a splitmix64 seed: the realism generator.

    The Mersenne Twister behind :class:`Noise` is CPython's, reproduced in
    the native core word for word. The slow components of the instrument
    model (the random walk) use this one instead: a few integer operations
    that any language performs identically, so the sequence is the same on
    both cores by construction rather than by porting a library. Uniforms
    are the top 53 bits scaled by 2^-53, exact in IEEE doubles; the
    Gaussian is the Irwin-Hall sum of twelve of them less six (unit
    variance, no transcendental function, bit-for-bit portable).
    """

    __slots__ = ("state",)

    def __init__(self, seed: int = 0) -> None:
        self.state = self.seed_state(seed)

    @staticmethod
    def seed_state(seed: int) -> int:
        z = (int(seed) + 0x9E3779B97F4A7C15) & _MASK64
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK64
        z ^= z >> 31
        return z or 0x9E3779B97F4A7C15

    def next_u64(self) -> int:
        x = self.state
        x ^= x >> 12
        x ^= (x << 25) & _MASK64
        x ^= x >> 27
        self.state = x
        return (x * 0x2545F4914F6CDD1D) & _MASK64

    def random(self) -> float:
        """Uniform on [0, 1) with 53 random bits."""
        return (self.next_u64() >> 11) * _TWO_POW_M53

    def gauss(self) -> float:
        """Standard normal, Irwin-Hall: exact on any platform."""
        s = 0.0
        for _ in range(12):
            s += self.random()
        return s - 6.0

    def capture_state(self) -> dict:
        return {"s": "%016x" % self.state}

    def apply_state(self, s: dict) -> None:
        raw = s.get("s")
        if raw:
            try:
                self.state = int(str(raw), 16) & _MASK64 or 0x9E3779B97F4A7C15
            except ValueError:
                log.debug("Could not restore the realism generator", exc_info=True)


class RandomWalk:
    """A slow, bounded random walk: the low-frequency half of instrument noise.

    An Ornstein-Uhlenbeck process in its exact discrete form, so it is
    stable for any step and its standard deviation is ``sigma`` whatever
    ``dt`` is: ``y' = a y + sigma sqrt(1 - a^2) N(0, 1)`` with
    ``a = exp(-dt / tau)``. White noise is what a flow transmitter shows on
    a trend; this is the wander a level or an analyser shows over minutes,
    which is what makes a PV filter or a deadband exercise honest.
    """

    __slots__ = ("sigma", "tau", "y", "_rng")

    def __init__(self, sigma: float = 0.0, tau: float = 600.0, seed: int = 0) -> None:
        self.sigma = float(sigma)
        self.tau = max(float(tau), 1e-3)
        self.y = 0.0
        self._rng = Xorshift(seed)

    def step(self, dt: float) -> float:
        if self.sigma <= 0.0:
            return 0.0
        a = math.exp(-max(dt, 0.0) / max(self.tau, 1e-3))
        self.y = self.y * a + self.sigma * math.sqrt(1.0 - a * a) * self._rng.gauss()
        return self.y

    def capture_state(self) -> dict:
        return {"y": self.y, "rng": self._rng.capture_state()}

    def apply_state(self, s: dict) -> None:
        self.y = float(s.get("y", self.y))
        self._rng.apply_state(s.get("rng") or {})


class Debounce:
    """Discrete signal that must hold for ``delay`` seconds before it propagates."""

    __slots__ = ("delay", "state", "_pending", "_timer")

    def __init__(self, delay: float, state: bool = False) -> None:
        self.delay = max(float(delay), 0.0)
        self.state = bool(state)
        self._pending = bool(state)
        self._timer = 0.0

    def step(self, u: bool, dt: float) -> bool:
        u = bool(u)
        if u == self.state:
            self._pending, self._timer = u, 0.0
            return self.state
        if u != self._pending:
            self._pending, self._timer = u, 0.0
        self._timer += dt
        if self._timer >= self.delay:
            self.state = u
            self._timer = 0.0
        return self.state

    def capture_state(self) -> dict:
        return {"state": bool(self.state), "pending": bool(self._pending),
                "timer": self._timer}

    def apply_state(self, s: dict) -> None:
        self.state = bool(s.get("state", self.state))
        self._pending = bool(s.get("pending", self.state))
        self._timer = float(s.get("timer", 0.0))
