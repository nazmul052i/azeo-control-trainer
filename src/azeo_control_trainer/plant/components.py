"""Process components a training plant is assembled from.

Deliberately first-order. A trainer's job is to make a control scheme behave
the way a plant would — a level that integrates, a valve that takes time to
stroke, a motor whose feedback arrives after its contactor closes — not to be
a rigorous process model. Every component here is a handful of state variables
and one ``step``.

Nothing in this module imports Qt or the tag store. Components know about
physics; wiring them to tags is :mod:`azeo_control_trainer.plant.process`.
"""
from __future__ import annotations

from dataclasses import dataclass, field


def _clamp(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else (hi if value > hi else value)


@dataclass
class Tank:
    """A vessel whose level integrates net flow.

    Level is carried in gallons and flows in gallons per minute, because that
    is what the shipped modules scale their transmitters in.
    """

    name: str
    capacity_gal: float = 1000.0
    level_gal: float = 500.0
    #: Volume below which the outlet loses suction. A pump taking suction from
    #: an empty tank must stop delivering, or the level integrates negative and
    #: the whole loop becomes nonsense.
    suction_loss_gal: float = 5.0

    overflowed: bool = False
    ran_dry: bool = False

    def step(self, dt: float, inflow_gpm: float, outflow_gpm: float) -> float:
        """Advance one step. Returns the outflow actually achieved.

        The return value matters: a tank that cannot supply the demanded
        outflow limits it, and the caller has to use that reduced number for
        whatever is downstream, or volume appears from nowhere.
        """
        available = max(0.0, self.level_gal - self.suction_loss_gal)
        deliverable = min(outflow_gpm, available / max(dt, 1e-6) * 60.0)
        deliverable = max(0.0, deliverable)

        self.level_gal += (inflow_gpm - deliverable) * dt / 60.0

        self.ran_dry = self.level_gal <= self.suction_loss_gal
        self.overflowed = self.level_gal >= self.capacity_gal
        self.level_gal = _clamp(self.level_gal, 0.0, self.capacity_gal)
        return deliverable

    @property
    def percent(self) -> float:
        return 100.0 * self.level_gal / self.capacity_gal if self.capacity_gal else 0.0

    @property
    def has_suction(self) -> bool:
        return self.level_gal > self.suction_loss_gal


@dataclass
class Motor:
    """A motor with contactor-to-feedback delay, as a starter really behaves.

    ``run_feedback`` is what the DCS sees on its ``DI``. It deliberately lags
    ``energised``: a device block's fail-to-start timer is only meaningful if
    the feedback can actually be late, and the pump workshop turns on the
    student seeing STARTING before RUNNING.
    """

    name: str
    start_delay_s: float = 1.5
    stop_delay_s: float = 1.0
    #: Set True to simulate a motor that will not start — the fail-to-start
    #: exercise. The contactor closes and no feedback ever comes back.
    fail_to_start: bool = False

    energised: bool = False
    running: bool = False
    _timer: float = 0.0

    def step(self, dt: float, energise: bool) -> bool:
        """Advance one step. Returns ``run_feedback``."""
        if energise != self.energised:
            self.energised = energise
            self._timer = 0.0

        self._timer += dt
        if self.energised:
            if self.fail_to_start:
                self.running = False
            elif self._timer >= self.start_delay_s:
                self.running = True
        elif self._timer >= self.stop_delay_s:
            self.running = False
        return self.running


@dataclass
class OnOffValve:
    """A solenoid-actuated block valve that takes real time to stroke.

    Position runs 0.0 (shut) to 1.0 (open). ``fail_closed`` decides where it
    goes when the solenoid is de-energised, which is the whole point of the
    'NC, on-off valve with solenoid actuator' on the training P&ID.
    """

    name: str
    stroke_time_s: float = 3.0
    fail_closed: bool = True
    position: float = 0.0
    #: Limit switches make at the very ends of travel, not at the midpoint.
    switch_margin: float = 0.02

    def step(self, dt: float, energised: bool) -> float:
        target = 1.0 if energised else (0.0 if self.fail_closed else 1.0)
        rate = dt / max(self.stroke_time_s, 1e-6)
        if self.position < target:
            self.position = min(target, self.position + rate)
        elif self.position > target:
            self.position = max(target, self.position - rate)
        return self.position

    @property
    def open_limit(self) -> bool:
        """ZSO — the open limit switch."""
        return self.position >= 1.0 - self.switch_margin

    @property
    def closed_limit(self) -> bool:
        """ZSC — the closed limit switch."""
        return self.position <= self.switch_margin

    @property
    def in_transit(self) -> bool:
        return not (self.open_limit or self.closed_limit)


@dataclass
class ControlValve:
    """A modulating valve that slews toward its demand.

    Demand arrives as 0–100 %, matching what an ``AO`` block writes. The valve
    fails closed on loss of signal, like the FC diaphragm valves on the P&ID.
    """

    name: str
    stroke_time_s: float = 4.0
    position: float = 0.0            # percent
    #: Below this the valve is effectively shut — real trim does not pass
    #: meaningful flow in the last fraction of a percent.
    seat_pct: float = 0.5

    def step(self, dt: float, demand_pct: float) -> float:
        demand = _clamp(float(demand_pct), 0.0, 100.0)
        # Full travel takes stroke_time_s, so the rate is in percent/second.
        step = 100.0 * dt / max(self.stroke_time_s, 1e-6)
        delta = demand - self.position
        if abs(delta) <= step:
            self.position = demand
        else:
            self.position += step if delta > 0 else -step
        return self.position

    @property
    def fraction(self) -> float:
        """Flow-passing fraction, 0.0–1.0."""
        return 0.0 if self.position < self.seat_pct else self.position / 100.0


@dataclass
class FlowMeter:
    """A first-order measurement with optional noise.

    Real transmitters lag and dither. Without a little of both, a trend line
    is a staircase and a student never learns to read one.
    """

    name: str
    tau_s: float = 1.0
    noise: float = 0.0
    value: float = 0.0
    _rng: object = field(default=None, repr=False)

    def step(self, dt: float, actual: float) -> float:
        alpha = dt / max(self.tau_s + dt, 1e-6)
        self.value += alpha * (actual - self.value)
        if self.noise and self._rng is not None:
            self.value += self._rng.uniform(-self.noise, self.noise)
        return max(0.0, self.value)
