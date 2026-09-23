"""Field device models.

These are the pieces that repeat across the plant. Getting them right once is the
whole point of building them separately: the flowsheet has twenty drives and
sixteen motor operated valves, and every one of them should fail in the same
believable ways.

Each device exposes ``step(dt, ...)`` and keeps its own state. None of them touch
the tag database directly; the owning process unit does that, which keeps the
devices testable without a database.
"""

from __future__ import annotations

import enum
import math
import zlib
from dataclasses import dataclass, field

from .dynamics import (DeadTime, Lag, Noise, RandomWalk, RateLimiter, clamp,
                       safe_div, safe_sqrt)


# --------------------------------------------------------------------- valves
class ValveChar(enum.StrEnum):
    LINEAR = "linear"
    EQUAL_PERCENT = "equal_percent"
    QUICK_OPENING = "quick_opening"


def valve_gain(char: ValveChar, x: float, rangeability: float = 50.0) -> float:
    """Installed fraction of rated Cv for a stem position ``x`` in 0-1."""
    x = clamp(x, 0.0, 1.0)
    if char is ValveChar.LINEAR:
        return x
    if char is ValveChar.EQUAL_PERCENT:
        return rangeability ** (x - 1.0) if x > 0.0 else 0.0
    return math.sqrt(x)


@dataclass
class ControlValve:
    """Throttling valve with actuator dynamics and the usual mechanical faults.

    ``stiction`` and ``hysteresis`` are the two faults that most change the
    character of a tuning exercise, so they are first class rather than bolted on.
    """

    tag: str
    cv_rated: float = 100.0
    char: ValveChar = ValveChar.LINEAR
    stroke_time: float = 5.0          # seconds for 0 to 100 percent
    fail_closed: bool = True
    leakage_pct: float = 0.0          # seat leakage as percent of rated Cv
    rangeability: float = 50.0

    # injectable faults
    stiction: float = 0.0             # percent of span the stem sticks by
    hysteresis: float = 0.0           # percent of span of backlash
    stuck: bool = False
    zero_shift: float = 0.0           # percent calibration error

    # The positioner loop (docs/Roadmap_Control_Engineer_Training.md, 7).
    # A positioner is a servo: it drives the stem to the demanded position
    # with its own dynamics, which a tuning exercise sees as a second lag
    # (or a small overshoot) between OUT and the flow. All three are zero
    # by default, which is the ideal positioner the plant was lined up with.
    positioner_time: float = 0.0      # servo time constant 1/wn, seconds; 0 = ideal
    positioner_overshoot: float = 0.0 # percent of a step the stem overshoots by
    positioner_deadband: float = 0.0  # percent of span the positioner ignores

    position: float = 0.0             # actual stem position, percent
    _target: float = 0.0
    _rl: RateLimiter = field(init=False, repr=False)
    _pos_sp: float = 0.0              # the setpoint the positioner is holding
    _servo_x: float = 0.0             # servo output and velocity, percent, percent/s
    _servo_v: float = 0.0

    def __post_init__(self) -> None:
        rate = 100.0 / max(self.stroke_time, 0.05)
        self._rl = RateLimiter(rate, rate, self.position)
        self._pos_sp = self._servo_x = self.position

    # ------------------------------------------------------------------ update
    def step(self, dt: float, command_pct: float, air_failure: bool = False) -> float:
        """Advance the actuator. Returns actual position in percent."""
        if air_failure:
            command_pct = 0.0 if self.fail_closed else 100.0
        cmd = clamp(float(command_pct) + self.zero_shift, 0.0, 100.0)

        if self.stuck:
            return self.position

        # Backlash: the target follows the command only once the command has
        # taken up the deadband, so a direction reversal consumes ``hysteresis``
        # percent of span before the stem moves again.
        if self.hysteresis > 0.0:
            half = self.hysteresis * 0.5
            if cmd > self._target + half:
                self._target = cmd - half
            elif cmd < self._target - half:
                self._target = cmd + half
        else:
            self._target = cmd

        # The positioner: its deadband holds the setpoint until the demand
        # has moved past it, then its servo takes the stem there with the
        # dynamics of a real loop. With the defaults both are pass-through,
        # and ``goal`` is exactly the target it always was.
        goal = self._target
        if self.positioner_deadband > 0.0:
            if abs(goal - self._pos_sp) >= self.positioner_deadband:
                self._pos_sp = goal
            goal = self._pos_sp
        else:
            self._pos_sp = goal
        if self.positioner_time > 0.0:
            goal = self._servo(goal, dt)
        else:
            self._servo_x, self._servo_v = goal, 0.0   # parked: switching on is bumpless

        # Stiction: the stem does not move until the error exceeds the stick band,
        # then it jumps most of the way. This is what makes a loop limit cycle.
        if self.stiction > 0.0:
            err = goal - self.position
            if abs(err) < self.stiction:
                return self.position
            self._rl.y = self.position + math.copysign(self.stiction * 0.8, err)

        self.position = clamp(self._rl.step(goal, dt), 0.0, 100.0)
        return self.position

    def _servo(self, u: float, dt: float) -> float:
        """The positioner's second-order response, exact for any step.

        ``x'' + 2 zeta wn x' + wn^2 x = wn^2 u`` with ``wn = 1 /
        positioner_time`` and the damping ratio taken from the overshoot
        (``os = exp(-pi zeta / sqrt(1 - zeta^2))``); no overshoot means
        critically damped. The deviation from ``u`` is propagated by the
        closed-form solution over the step, so the servo cannot go
        unstable however large ``dt`` is - the same rule as ``Lag``.
        """
        wn = 1.0 / max(self.positioner_time, 1e-3)
        os_ = clamp(self.positioner_overshoot, 0.0, 90.0) / 100.0
        d0 = self._servo_x - u
        v0 = self._servo_v
        dt = max(dt, 0.0)
        if os_ > 0.0:
            ln = math.log(os_)
            zeta = -ln / math.sqrt(math.pi ** 2 + ln * ln)
            a = zeta * wn
            wd = wn * math.sqrt(1.0 - zeta * zeta)
            e = math.exp(-a * dt)
            c = math.cos(wd * dt)
            s = math.sin(wd * dt)
            b = (v0 + a * d0) / wd
            d = e * (d0 * c + b * s)
            v = e * ((-a * d0 + wd * b) * c + (-a * b - wd * d0) * s)
        else:
            e = math.exp(-wn * dt)
            d = e * (d0 + (v0 + wn * d0) * dt)
            v = e * (v0 - wn * (v0 + wn * d0) * dt)
        self._servo_x = u + d
        self._servo_v = v
        return self._servo_x

    # ------------------------------------------------------------- snapshots
    def capture_state(self) -> dict:
        """Stem position, backlash target and the injected mechanical faults.

        ``position`` is where the stem actually is, which lags the command by
        the stroke time. Without it a restored valve springs to its default and
        strokes open again, and every flow on the sheet collapses for a few
        seconds.
        """
        return {"pos": self.position, "tgt": self._target, "rl": self._rl.y,
                "stiction": self.stiction, "hysteresis": self.hysteresis,
                "stuck": bool(self.stuck), "zero": self.zero_shift,
                "psp": self._pos_sp, "sx": self._servo_x, "sv": self._servo_v}

    def apply_state(self, s: dict) -> None:
        self.position = clamp(float(s.get("pos", self.position)), 0.0, 100.0)
        self._target = float(s.get("tgt", self.position))
        self._rl.reset(float(s.get("rl", self.position)))
        self.stiction = float(s.get("stiction", self.stiction))
        self.hysteresis = float(s.get("hysteresis", self.hysteresis))
        self.stuck = bool(s.get("stuck", self.stuck))
        self.zero_shift = float(s.get("zero", self.zero_shift))
        # a snapshot from before the positioner existed parks it at the target
        self._pos_sp = float(s.get("psp", self._target))
        self._servo_x = float(s.get("sx", self._target))
        self._servo_v = float(s.get("sv", 0.0))

    # ------------------------------------------------------------------ output
    def flow(self, dp_bar: float, sg: float = 1.0) -> float:
        """Liquid flow in m3/h from the standard sizing relation."""
        frac = valve_gain(self.char, self.position / 100.0, self.rangeability)
        frac = max(frac, self.leakage_pct / 100.0)
        return 0.865 * self.cv_rated * frac * safe_sqrt(max(dp_bar, 0.0) / max(sg, 0.05))

    # Critical pressure drop ratio. Above it the valve is choked and further
    # reduction in downstream pressure buys no more flow.
    XT = 0.7

    def gas_flow(self, p_up_bara: float, p_dn_bara: float, sg: float = 0.65,
                 t_k: float = 300.0) -> float:
        """Gas flow in Nm3/h from the standard metric sizing relation.

        ``Q = 417 * Cv * P1 * Y * sqrt(x / (Gg * T))`` with pressures in bar
        absolute and temperature in kelvin. ``Y`` is the expansion factor, which
        falls from 1 at zero drop to 2/3 at the choked condition.
        """
        frac = valve_gain(self.char, self.position / 100.0, self.rangeability)
        frac = max(frac, self.leakage_pct / 100.0)
        if frac <= 0.0:
            return 0.0
        p_up = max(p_up_bara, 0.05)
        p_dn = clamp(p_dn_bara, 0.0, p_up)
        x = min((p_up - p_dn) / p_up, self.XT)
        y = 1.0 - x / (3.0 * self.XT)
        return (417.0 * self.cv_rated * frac * p_up * y
                * safe_sqrt(x / max(sg * t_k, 1.0)))

    def clear_faults(self) -> None:
        self.stiction = self.hysteresis = self.zero_shift = 0.0
        self.stuck = False


# ------------------------------------------------------------------------ MOV
class MovState(enum.StrEnum):
    CLOSED = "Closed"
    OPENING = "Opening"
    OPEN = "Open"
    CLOSING = "Closing"
    STOPPED = "Stopped"
    TRIPPED = "Tripped"


@dataclass
class MotorOperatedValve:
    """On-off motor operated valve, operable from the control room.

    Models what actually goes wrong with these: travel that takes real time, a
    torque switch that trips part way, limit switches that fail to make, and an
    actuator that has been left in local.
    """

    tag: str
    travel_time: float = 25.0
    position: float = 0.0             # percent open
    remote: bool = True
    state: MovState = MovState.CLOSED

    # injectable faults
    fail_to_open: bool = False
    torque_trip_at: float | None = None   # percent travel at which it trips
    open_limit_faulty: bool = False
    slow_travel_factor: float = 1.0
    drifts_closed: bool = False

    torque_tripped: bool = False
    _cmd_open: bool = False

    def capture_state(self) -> dict:
        return {"pos": self.position, "cmd": bool(self._cmd_open),
                "tripped": bool(self.torque_tripped), "remote": bool(self.remote)}

    def apply_state(self, s: dict) -> None:
        self.position = float(s.get("pos", self.position))
        self._cmd_open = bool(s.get("cmd", self._cmd_open))
        self.torque_tripped = bool(s.get("tripped", self.torque_tripped))
        self.remote = bool(s.get("remote", self.remote))
    _cmd_close: bool = False

    def command(self, open_cmd: bool, close_cmd: bool) -> None:
        """Latch commands from the DCS. Both true is treated as stop."""
        if open_cmd and close_cmd:
            self._cmd_open = self._cmd_close = False
        else:
            self._cmd_open, self._cmd_close = bool(open_cmd), bool(close_cmd)

    def reset(self) -> None:
        self.torque_tripped = False
        self.state = MovState.STOPPED

    def step(self, dt: float) -> float:
        rate = 100.0 / max(self.travel_time * max(self.slow_travel_factor, 0.05), 0.5)

        if self.torque_tripped:
            self.state = MovState.TRIPPED
        elif not self.remote:
            self.state = MovState.STOPPED
        elif self._cmd_open and not self.fail_to_open:
            if self.position < 100.0:
                self.position = min(100.0, self.position + rate * dt)
                self.state = MovState.OPENING
                if (self.torque_trip_at is not None
                        and self.position >= self.torque_trip_at):
                    self.torque_tripped = True
        elif self._cmd_close:
            if self.position > 0.0:
                self.position = max(0.0, self.position - rate * dt)
                self.state = MovState.CLOSING

        if self.drifts_closed and self.position > 0.0:
            self.position = max(0.0, self.position - rate * 0.15 * dt)

        if not self.torque_tripped:
            if self.position >= 99.5:
                self.state = MovState.OPEN
            elif self.position <= 0.5:
                self.state = MovState.CLOSED
            elif not self._cmd_open and not self._cmd_close:
                self.state = MovState.STOPPED
            elif self.state in (MovState.OPEN, MovState.CLOSED):
                self.state = MovState.STOPPED
        return self.position

    # ---------------------------------------------------------------- feedback
    @property
    def zso(self) -> bool:
        """Open limit switch. A faulty switch never makes, which is the point."""
        return self.position >= 99.5 and not self.open_limit_faulty

    @property
    def zsc(self) -> bool:
        return self.position <= 0.5

    @property
    def flow_fraction(self) -> float:
        """Isolation valves are not throttling devices; treat as quick opening."""
        return math.sqrt(clamp(self.position / 100.0, 0.0, 1.0))

    def clear_faults(self) -> None:
        self.fail_to_open = self.open_limit_faulty = self.drifts_closed = False
        self.torque_trip_at = None
        self.slow_travel_factor = 1.0
        self.torque_tripped = False


# ---------------------------------------------------------------------- motor
@dataclass
class Motor:
    """Contactor or VFD driven motor with run feedback and trip logic."""

    tag: str
    rated_current: float = 100.0
    start_delay: float = 1.0          # contactor to run feedback
    coast_time: float = 3.0
    vfd: bool = False

    running: bool = False
    faulted: bool = False
    available: bool = True
    speed_pct: float = 0.0            # 0-100, always 100 when running DOL
    vfd_healthy: bool = True

    trip_on_overload: bool = False    # injectable
    vfd_comms_fault: bool = False     # injectable

    # Hydraulic load as a fraction of rated, set by whatever the motor drives.
    # It is what makes the ammeter a diagnostic: a gas-locked pump runs light
    # and a pump at runout runs heavy, and an operator who can read that
    # difference finds the fault without walking to the pump.
    load_frac: float = 1.0
    thermal_pct: float = 0.0          # I2t thermal image, trips at 100
    HEAT_TAU = 240.0                  # seconds to trip at ~1.4x current
    COOL_TAU = 900.0                  # stator cooling time constant

    _timer: float = 0.0
    _cmd_start: bool = False
    _speed_lag: Lag = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._speed_lag = Lag(2.5 if self.vfd else 1.0, 0.0)

    def command(self, start: bool, stop: bool) -> None:
        if stop:
            self._cmd_start = False
        elif start:
            self._cmd_start = True

    def trip(self, reason: str = "") -> None:
        self.faulted = True
        self._cmd_start = False

    def reset(self) -> None:
        # Clears the latch only. A persistent overload (the injected fault)
        # re-trips on the next step, so the instructor's malfunction stays in
        # force until the instructor clears it.
        self.faulted = False

    def step(self, dt: float, permissive: bool = True,
             speed_ref_pct: float = 100.0) -> None:
        if self.trip_on_overload:
            self.faulted = True
        self.vfd_healthy = self.vfd and not self.vfd_comms_fault or not self.vfd

        want = self._cmd_start and permissive and not self.faulted and self.available
        if self.vfd and self.vfd_comms_fault:
            want = False

        if want and not self.running:
            self._timer += dt
            if self._timer >= self.start_delay:
                self.running = True
                self._timer = 0.0
        elif not want and self.running:
            self.running = False
            self._timer = 0.0
        elif want:
            self._timer = 0.0

        target = (clamp(speed_ref_pct, 0.0, 100.0) if self.vfd else 100.0) if self.running else 0.0
        self.speed_pct = self._speed_lag.step(target, dt)

        # Thermal overload the way a real relay computes it: the image heats
        # with the square of current above rated and cools slowly. A sustained
        # runout trips the motor in minutes; a start surge does not.
        excess = (self.current / max(self.rated_current, 1.0)) ** 2 - 1.0
        if excess > 0.0 and self.running:
            self.thermal_pct += excess * 100.0 / self.HEAT_TAU * dt
        else:
            self.thermal_pct -= self.thermal_pct * dt / self.COOL_TAU
        self.thermal_pct = clamp(self.thermal_pct, 0.0, 130.0)
        if self.thermal_pct >= 100.0 and self.running:
            self.trip("thermal overload")

    # ------------------------------------------------------------- persistence
    def state(self) -> dict:
        return {"run": float(self.running), "cmd": float(self._cmd_start),
                "spd": self.speed_pct, "load": self.load_frac,
                "thermal": self.thermal_pct}

    # The unit walk looks for capture_state, so expose the pair under the
    # common names as well as the ones the models already call.
    def capture_state(self) -> dict:
        return self.state()

    def apply_state(self, s: dict) -> None:
        self.restore(s)

    def restore(self, s: dict) -> None:
        self.running = bool(s.get("run", 0.0))
        # An older snapshot has no command flag; assume a running motor was
        # commanded to run, otherwise it drops out on the first step.
        self._cmd_start = bool(s.get("cmd", s.get("run", 0.0)))
        self.speed_pct = float(s.get("spd", 100.0 if self.running else 0.0))
        self._speed_lag.reset(self.speed_pct)
        self.load_frac = float(s.get("load", 1.0))
        self.thermal_pct = float(s.get("thermal", 0.0))
        self._timer = 0.0

    @property
    def current(self) -> float:
        """Rough current curve: magnetising plus load, cubic in speed."""
        if self.speed_pct <= 0.5 and not self.running:
            return 0.0
        n = self.speed_pct / 100.0
        run_amps = 0.25 + 0.75 * n ** 3 * clamp(self.load_frac, 0.0, 1.5)
        if self.vfd:
            return self.rated_current * run_amps
        # A contactor start pulls locked rotor current until the machine is
        # nearly at speed. It is the kick every real ammeter shows, and it is
        # why repeated starts trip the thermal image: the relay's starts per
        # hour limit falls straight out of the physics.
        inrush = 6.0 * max(1.0 - n / 0.95, 0.0)
        return self.rated_current * max(run_amps, inrush)

    def clear_faults(self) -> None:
        self.trip_on_overload = self.vfd_comms_fault = False


# ----------------------------------------------------------------------- pump
@dataclass
class CentrifugalPump:
    """Centrifugal pump on affinity laws with a quadratic head curve."""

    tag: str
    head_shutoff: float = 280.0       # metres at rated speed
    flow_max: float = 230.0           # m3/h at zero head, rated speed
    flow_min: float = 25.0            # minimum continuous stable flow
    rated_speed_pct: float = 100.0

    wear_pct: float = 0.0             # injectable head loss
    cavitating: bool = False

    def capture_state(self) -> dict:
        return {"wear": self.wear_pct, "cav": bool(self.cavitating)}

    def apply_state(self, s: dict) -> None:
        self.wear_pct = float(s.get("wear", self.wear_pct))
        self.cavitating = bool(s.get("cav", self.cavitating))

    def head(self, flow_m3h: float, speed_pct: float) -> float:
        """Developed head in metres. Zero when stopped, never negative."""
        n = clamp(speed_pct / max(self.rated_speed_pct, 1.0), 0.0, 1.5)
        if n <= 0.01:
            return 0.0
        h0 = self.head_shutoff * (1.0 - self.wear_pct / 100.0) * n * n
        q_max = self.flow_max * n
        k = safe_div(h0, q_max * q_max, 0.0)
        return max(h0 - k * max(flow_m3h, 0.0) ** 2, 0.0)

    def discharge_pressure(self, suction_barg: float, flow_m3h: float,
                           speed_pct: float, density: float = 780.0) -> float:
        dp_bar = self.head(flow_m3h, speed_pct) * density * 9.81 / 1e5
        return suction_barg + dp_bar

    def check_cavitation(self, suction_barg: float, vapour_pressure_barg: float,
                         running: bool) -> bool:
        margin = suction_barg - vapour_pressure_barg
        self.cavitating = bool(running and margin < 0.3)
        return self.cavitating

    def clear_faults(self) -> None:
        self.wear_pct = 0.0
        self.cavitating = False


# ----------------------------------------------------------- heat exchangers
@dataclass
class HeatExchanger:
    """Surface heat exchanger on a ``UA * dT`` duty, with foulable surface.

    Used for the column reboilers and condensers. The duty returned is what
    the *surface* can pass; the caller takes the minimum of that and whatever
    the process actually demands, so an exchanger sized with margin is normally
    not the limiting element and only bites when it fouls or when its
    temperature driving force collapses. That is how a real exchanger behaves,
    and it is why fouling is worth injecting: nothing changes until the surface
    becomes the constraint, and then everything does.

    ``driver`` scales the active surface, which is where the utility valve
    comes in: a shut cooling water valve wets no tubes and transfers nothing.

    Duty is clamped to be non-negative, so a reversed temperature difference
    stalls the exchanger rather than pumping heat backwards.
    """

    tag: str
    ua_clean: float                   # kW/K, clean surface at full duty
    duty_max: float = 1e9             # kW, mechanical or hydraulic ceiling

    fouling_pct: float = 0.0          # injectable, 0 is clean
    duty: float = 0.0                 # last computed duty, kW
    limited: bool = False             # True when the surface set the duty

    MAX_FOULING = 95.0                # never let the surface vanish entirely

    def capture_state(self) -> dict:
        return {"fouling": self.fouling_pct, "duty": self.duty,
                "limited": bool(self.limited)}

    def apply_state(self, s: dict) -> None:
        self.fouling_pct = float(s.get("fouling", self.fouling_pct))
        self.duty = float(s.get("duty", self.duty))
        self.limited = bool(s.get("limited", self.limited))

    @property
    def ua(self) -> float:
        """Effective UA after fouling, kW/K."""
        return self.ua_clean * (1.0 - clamp(self.fouling_pct, 0.0, self.MAX_FOULING)
                                / 100.0)

    def capacity(self, t_hot: float, t_cold: float, driver: float = 1.0) -> float:
        """Duty the surface can pass for this driving force, kW."""
        if not (math.isfinite(t_hot) and math.isfinite(t_cold)):
            return 0.0
        dt = max(t_hot - t_cold, 0.0)
        self.duty = clamp(self.ua * dt * clamp(driver, 0.0, 1.5), 0.0, self.duty_max)
        return self.duty

    def transfer(self, demand: float, t_hot: float, t_cold: float,
                 driver: float = 1.0) -> float:
        """Actual duty: the lesser of what is demanded and what the surface passes."""
        cap = self.capacity(t_hot, t_cold, driver)
        demand = max(float(demand), 0.0) if math.isfinite(demand) else 0.0
        self.limited = cap < demand
        self.duty = min(demand, cap)
        return self.duty

    def clear_faults(self) -> None:
        self.fouling_pct = 0.0


# ---------------------------------------------------------------- transmitter
class TxFailure(enum.StrEnum):
    NONE = "none"
    FROZEN = "frozen"
    FAIL_LOW = "fail_low"
    FAIL_HIGH = "fail_high"
    OUT_OF_SERVICE = "out_of_service"


@dataclass
class Transmitter:
    """Turns a true process value into a measured value with quality.

    The distinction matters: the P&ID and the trends can show both, which is
    exactly what a drift exercise needs.
    """

    tag: str
    lo: float = 0.0
    hi: float = 100.0
    tau: float = 0.5
    noise_sigma_pct: float = 0.0
    update_period: float = 0.0        # non-zero for discontinuous analysers
    transport: float = 0.0            # sample line dead time, seconds

    drift_pct_per_hour: float = 0.0
    failure: TxFailure = TxFailure.NONE
    extra_lag: float = 0.0

    # The instrument model (docs/Roadmap_Control_Engineer_Training.md, 7):
    # the ordinary imperfections, not the failures. ``noise_sigma_pct`` is
    # the white part; the walk is the slow part with its own time constant;
    # ``damping`` is the transmitter's own output filter, applied to the
    # noisy signal the way a real transmitter's damping is, after the
    # sensor lag ``tau``; ``offset_pct`` is a calibration error. Every
    # default is zero, so a plant that sets none of them measures exactly
    # as it did before these existed - the gate and the golden traces
    # depend on that.
    walk_sigma_pct: float = 0.0       # standard deviation of the slow walk, % span
    walk_tau: float = 600.0           # its time constant, seconds
    damping: float = 0.0              # transmitter damping, seconds; 0 = none
    offset_pct: float = 0.0           # calibration offset, % span

    _lag: Lag = field(init=False, repr=False)
    _pipe: DeadTime | None = field(default=None, init=False, repr=False)
    _noise: Noise = field(init=False, repr=False)
    _walk: RandomWalk = field(init=False, repr=False)
    _damp: Lag = field(init=False, repr=False)
    _drift: float = 0.0
    _held: float = 0.0
    _clock: float = 0.0
    measured: float = 0.0
    quality: int = 0                  # mirrors tags.Quality

    def capture_state(self) -> dict:
        """The measurement chain, including a drift that has been accumulating.

        Drift is the reason this matters beyond cosmetics: it builds over
        hours, so a snapshot that dropped it would quietly heal a fault the
        instructor injected deliberately.
        """
        pipe = self._pipe.capture_state() if self._pipe is not None else None
        return {"pipe": pipe,
                "lag": self._lag.capture_state(),
                "noise": self._noise.capture_state(),
                "walk": self._walk.capture_state(),
                "damp": self._damp.capture_state(),
                "drift": self._drift, "held": self._held, "clock": self._clock,
                "measured": self.measured, "quality": int(self.quality),
                "failure": str(self.failure.value), "extra_lag": self.extra_lag,
                "drift_rate": self.drift_pct_per_hour}

    def apply_state(self, s: dict) -> None:
        pipe = s.get("pipe")
        if pipe and pipe.get("buf"):
            # The pipe is normally built lazily on the first step, so at
            # restore time it may not exist yet. The saved buffer length fixes
            # the step size it was built with, which is all the constructor
            # needs; DeadTime.apply_state then resizes if the engine's step
            # differs. Dropping the buffer instead leaves the analyser holding
            # a sample from the wrong history, which is exactly the sub-scan
            # divergence the round-trip test exists to catch.
            if self._pipe is None and self.transport > 0.0:
                n = max(len(pipe["buf"]), 1)
                self._pipe = DeadTime(self.transport, self.transport / n,
                                      float(pipe["buf"][0]))
            if self._pipe is not None:
                self._pipe.apply_state(pipe)
        self._lag.apply_state(s.get("lag") or {})
        self._noise.apply_state(s.get("noise") or {})
        self._walk.apply_state(s.get("walk") or {})
        self._damp.apply_state(s.get("damp") or {})
        self._drift = float(s.get("drift", self._drift))
        self._held = float(s.get("held", self._held))
        self._clock = float(s.get("clock", self._clock))
        self.measured = float(s.get("measured", self.measured))
        self.quality = int(s.get("quality", self.quality))
        self.failure = TxFailure(s.get("failure", self.failure.value))
        self.extra_lag = float(s.get("extra_lag", self.extra_lag))
        self.drift_pct_per_hour = float(
            s.get("drift_rate", self.drift_pct_per_hour))

    def __post_init__(self) -> None:
        # A discontinuous analyser reports its last sample. Starting the clock
        # at the update period makes it take one on its very first scan;
        # otherwise it reports zero until the first period elapses, which on a
        # five minute analyser means five minutes of a reading that looks like
        # perfect product and is simply the uninitialised hold.
        self._clock = self.update_period
        self._lag = Lag(self.tau, self.lo)
        # crc32 rather than hash(): string hashing is randomised per process,
        # and the noise realisation should be reproducible between sessions.
        self._noise = Noise(0.0, 2.0, seed=zlib.crc32(self.tag.encode()))
        # the walk has its own generator (dynamics.Xorshift), seeded from
        # the same CRC so it is reproducible per tag and per session
        self._walk = RandomWalk(0.0, self.walk_tau, seed=zlib.crc32(self.tag.encode()))
        self._damp = Lag(max(self.damping, 1e-6), self.lo)
        self._held = self.lo
        self.measured = self.lo

    @property
    def span(self) -> float:
        s = self.hi - self.lo
        return s if abs(s) > 1e-12 else 1.0

    def step(self, dt: float, true_value: float) -> float:
        from .tags import OVER_RANGE, Quality  # local: avoids a cycle

        if self.failure is TxFailure.OUT_OF_SERVICE:
            self.quality = int(Quality.BAD)
            return self.measured
        if self.failure is TxFailure.FROZEN:
            self.quality = int(Quality.UNCERTAIN)
            return self.measured
        if self.failure is TxFailure.FAIL_LOW:
            self.measured, self.quality = self.lo, int(Quality.BAD)
            return self.measured
        if self.failure is TxFailure.FAIL_HIGH:
            self.measured, self.quality = self.hi, int(Quality.BAD)
            return self.measured

        self._drift += self.drift_pct_per_hour / 100.0 * self.span * dt / 3600.0
        self._noise.sigma = self.noise_sigma_pct / 100.0 * self.span

        tau = self.tau + self.extra_lag
        if abs(tau - self._lag.tau) > 1e-9:
            self._lag.tau = max(tau, 1e-6)

        # The sample has to travel the line before the analyser sees it.
        # Built lazily because the step size is only known here, and
        # rebuilt when the settings change the dead time under a running
        # analyser (a fresh line, primed with the value now entering it).
        if self.transport > 0.0:
            if self._pipe is None or abs(self._pipe.delay - self.transport) > 1e-9:
                self._pipe = DeadTime(self.transport, dt, float(true_value))
            true_value = self._pipe.step(float(true_value))
        filtered = self._lag.step(float(true_value), dt)
        raw = filtered + self._drift + self._noise.step(dt)

        # the slow part of the noise, the calibration error, then the
        # transmitter's own damping over the lot; each is skipped, not
        # applied with a zero, so the defaults cost nothing and change nothing
        if self.walk_sigma_pct > 0.0:
            self._walk.sigma = self.walk_sigma_pct / 100.0 * self.span
            self._walk.tau = max(self.walk_tau, 1e-3)
            raw += self._walk.step(dt)
        if self.offset_pct != 0.0:
            raw += self.offset_pct / 100.0 * self.span
        if self.damping > 0.0:
            if abs(self.damping - self._damp.tau) > 1e-9:
                self._damp.tau = self.damping
            raw = self._damp.step(raw, dt)
        else:
            self._damp.y = raw          # follows, so switching damping on is bumpless

        if self.update_period > 0.0:
            self._clock += dt
            if self._clock >= self.update_period:
                # Subtract rather than zero: zeroing throws away the remainder,
                # so the sampling instant creeps by a fraction of a scan every
                # cycle and two runs at different step sizes drift apart.
                self._clock -= self.update_period
                self._held = raw
            raw = self._held

        # Zero and span suppression, the way a real instrument is set up: an
        # excursion past a range limit that is within the instrument's own
        # noise is the noise, not an off-scale process. Without this a clean
        # impurity analyser reading its true zero flickers UNCERTAIN forever
        # and quality-sheds whatever cascade trusts it.
        tol = 3.0 * self._noise.sigma
        if self.lo - tol <= raw < self.lo:
            raw = self.lo
        elif self.hi < raw <= self.hi + tol:
            raw = self.hi
        # Saturation, the way a 4-20 mA loop does it. Tag.set uses the same
        # band to decide what counts as a genuine out of range value.
        self.measured = clamp(raw, self.lo - OVER_RANGE * self.span,
                              self.hi + OVER_RANGE * self.span)
        # Off-scale readings are uncertain, not good: this is what a real DCS sees.
        self.quality = int(Quality.UNCERTAIN if not (self.lo <= self.measured <= self.hi)
                           else Quality.GOOD)
        return self.measured

    def clear_faults(self) -> None:
        self.failure = TxFailure.NONE
        self.drift_pct_per_hour = 0.0
        self._drift = 0.0
        self.extra_lag = 0.0
