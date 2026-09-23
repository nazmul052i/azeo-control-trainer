"""Reusable equipment packages.

The flowsheet has eight duty-standby pump pairs, sixteen motor operated valves
and eleven shutdown valves. Writing each out by hand is how a tag ends up spelled
differently in one place, or a permissive gets forgotten on the standby. These
classes build the tags, own the devices and do the stepping, so every instance
behaves identically and only the sizing differs.

Each package creates its tags on the owning :class:`ProcessUnit`, so the unit's
``build`` stays readable and the tag names follow one convention:

    MOV   DI  ZSO-<base>, ZSC-<base>, XS-<base>-TRQ, XS-<base>-AVL
          DO  XY-<base>-OPN, XY-<base>-CLS
    Motor DI  XS-<base>-RUN, XS-<base>-FLT, XS-<base>-AVL, [XS-<base>-VFD]
          DO  XY-<base>-STR, XY-<base>-STP
    SDV   DI  ZSO-<base>, ZSC-<base>
          DO  XY-<base>-OPN
"""

from __future__ import annotations

import logging
from typing import List, Optional

from ..core.devices import (CentrifugalPump, ControlValve, Motor,
                            MotorOperatedValve, ValveChar)
from ..core.dynamics import Lag, clamp
from .base import ProcessUnit

log = logging.getLogger(__name__)


def base_of(tag: str) -> str:
    """MOV-1001A becomes MOV1001A, the form used inside compound tag names."""
    return tag.replace("-", "")


class StatefulGroup:
    """Mixin for equipment that is a group of other equipment.

    A pump train owns motor packages which own motors, and a snapshot has to
    reach all the way down. Rather than hand write that chain once per wrapper
    and have the next one forgotten, each group persists any child that knows
    how to persist itself, plus its own plain scalars. Tags are skipped because
    the tag database already saves them.
    """

    # Names that are configuration rather than state. A fail position or a
    # stroke time is an engineering decision made in code; letting a snapshot
    # write it back would silently revert a retune at load.
    CONFIG: frozenset = frozenset()

    def capture_state(self) -> dict:
        out: dict = {}
        for name, obj in vars(self).items():
            if name.startswith("__") or name in self.CONFIG:
                continue
            fn = getattr(obj, "capture_state", None)
            if callable(fn):
                out[name] = fn()
            elif isinstance(obj, (int, float, bool)):
                out[name] = obj
        return out

    def apply_state(self, state: dict) -> None:
        for name, sub in (state or {}).items():
            if name in self.CONFIG:
                continue
            current = getattr(self, name, None)
            fn = getattr(current, "apply_state", None)
            if callable(fn) and isinstance(sub, dict):
                fn(sub)
            elif isinstance(sub, (int, float, bool)) and isinstance(
                    current, (int, float, bool)):
                setattr(self, name, sub)


class MovPackage(StatefulGroup):
    """One control-room operable motor operated valve with its full tag set."""

    simulate_enable: bool = False
    simulate_value: float = 0.0

    def __init__(self, unit: ProcessUnit, tag: str, service: str,
                 travel_time: float = 25.0, position: float = 0.0) -> None:
        self.simulate_enable = False
        self.simulate_value = 0.0
        self.unit = unit
        self.tag = tag
        self.service = service
        b = base_of(tag)
        self.device = MotorOperatedValve(tag, travel_time=travel_time,
                                         position=position)

        self.zso = unit.di(f"ZSO-{b}", f"{tag} open limit switch", "Not open", "Open",
                           value=position >= 99.5)
        self.zsc = unit.di(f"ZSC-{b}", f"{tag} closed limit switch",
                           "Not closed", "Closed", value=position <= 0.5)
        self.trq = unit.di(f"XS-{b}-TRQ", f"{tag} torque or thermal overload trip",
                           "Healthy", "Tripped")
        self.avl = unit.di(f"XS-{b}-AVL", f"{tag} actuator in remote and available",
                           "Local", "Remote", value=True)
        self.cmd_open = unit.do(f"XY-{b}-OPN",
                                f"{tag} open command from control room", "Idle", "Open",
                                value=position >= 99.5)
        self.cmd_close = unit.do(f"XY-{b}-CLS",
                                 f"{tag} close command from control room",
                                 "Idle", "Close")
        self.local = False          # set from the field operations panel


    def step(self, dt: float) -> float:
        self.device.remote = not self.local
        self.device.command(bool(self.cmd_open.effective),
                            bool(self.cmd_close.effective))
        pos = self.device.step(dt)
        if self.simulate_enable:
            # SIMULATE per the DL detail display: the limit switches report
            # the simulated position while the real device keeps moving
            # underneath, exactly the wrong-feedback exercise it exists for.
            self.zso.set(self.simulate_value >= 99.5)
            self.zsc.set(self.simulate_value <= 0.5)
        else:
            self.zso.set(self.device.zso)
            self.zsc.set(self.device.zsc)
        self.trq.set(self.device.torque_tripped)
        self.avl.set(not self.local and not self.device.torque_tripped)
        return pos

    @property
    def fraction(self) -> float:
        return self.device.flow_fraction

    @property
    def is_open(self) -> bool:
        return self.device.zso

    def state(self) -> dict:
        return {"pos": self.device.position, "trip": float(self.device.torque_tripped)}

    def restore(self, s: dict) -> None:
        self.device.position = float(s.get("pos", 0.0))
        self.device.torque_tripped = bool(s.get("trip", 0.0))


class SdvPackage(StatefulGroup):
    """On-off shutdown valve. Fail action decides what it does with no command."""

    CONFIG = frozenset({"stroke", "fail_open"})

    def __init__(self, unit: ProcessUnit, tag: str, service: str,
                 stroke: float = 2.0, fail_open: bool = False,
                 initially_open: bool = True) -> None:
        self.unit = unit
        self.tag = tag
        self.service = service
        b = base_of(tag)
        self.stroke = max(stroke, 0.2)
        self.fail_open = fail_open
        self.position = 100.0 if initially_open else 0.0

        self.zso = unit.di(f"ZSO-{b}", f"{tag} open limit switch", "Not open", "Open",
                           value=initially_open)
        self.zsc = unit.di(f"ZSC-{b}", f"{tag} closed limit switch",
                           "Not closed", "Closed", value=not initially_open)
        self.cmd = unit.do(f"XY-{b}-OPN", f"{tag} open command",
                           "Close", "Open", value=initially_open)


    def step(self, dt: float, trip: bool = False,
             air_failure: bool = False) -> float:
        # A trip or a loss of instrument air de-energises the actuator, and the
        # valve drives to its fail position - open for a depressuring valve,
        # closed for an isolation. Only an energised valve follows its command.
        if trip or air_failure:
            want = self.fail_open
        else:
            want = bool(self.cmd.effective)
        target = 100.0 if want else 0.0
        rate = 100.0 / self.stroke
        if self.position < target:
            self.position = min(target, self.position + rate * dt)
        elif self.position > target:
            self.position = max(target, self.position - rate * dt)
        self.zso.set(self.position >= 99.5)
        self.zsc.set(self.position <= 0.5)
        return self.position

    @property
    def is_open(self) -> bool:
        return self.position >= 99.5

    @property
    def fraction(self) -> float:
        return clamp(self.position / 100.0, 0.0, 1.0)


class MotorPackage(StatefulGroup):
    """A single drive with run feedback, fault, availability and commands."""

    CONFIG = frozenset({"vfd"})

    def __init__(self, unit: ProcessUnit, tag: str, service: str,
                 rated_current: float = 100.0, vfd: bool = False,
                 start_delay: float = 1.0) -> None:
        self.unit = unit
        self.tag = tag
        self.service = service
        b = base_of(tag)
        self.vfd = vfd
        self.device = Motor(tag, rated_current=rated_current, vfd=vfd,
                            start_delay=start_delay)

        self.run = unit.di(f"XS-{b}-RUN", f"{tag} running feedback",
                           "Stopped", "Running")
        self.flt = unit.di(f"XS-{b}-FLT", f"{tag} fault or trip", "Healthy", "Faulted")
        self.avl = unit.di(f"XS-{b}-AVL", f"{tag} available and in remote",
                           "Not avail", "Available", value=True)
        self.vfd_ok = (unit.di(f"XS-{b}-VFD", f"{tag} VFD healthy", "Fault", "Healthy",
                               value=True) if vfd else None)
        self.cmd_start = unit.do(f"XY-{b}-STR", f"{tag} start command", "Idle", "Start")
        self.cmd_stop = unit.do(f"XY-{b}-STP", f"{tag} stop command", "Idle", "Stop")
        self.local = False
        self.simulate_enable = False
        self.simulate_value = 0.0


    def step(self, dt: float, permissive: bool = True,
             speed_ref: float = 100.0, trip: bool = False) -> None:
        if trip:
            self.device.trip()
        self.device.available = not self.local
        self.device.command(bool(self.cmd_start.effective),
                            bool(self.cmd_stop.effective))
        self.device.step(dt, permissive and not trip, speed_ref)
        if self.simulate_enable:
            self.run.set(bool(self.simulate_value))
        else:
            self.run.set(self.device.running)
        self.flt.set(self.device.faulted)
        self.avl.set(not self.local and not self.device.faulted)
        if self.vfd_ok is not None:
            self.vfd_ok.set(self.device.vfd_healthy)

    @property
    def running(self) -> bool:
        return self.device.running

    @property
    def speed(self) -> float:
        return self.device.speed_pct

    @property
    def current(self) -> float:
        return self.device.current


class PumpTrain(StatefulGroup):
    """A duty-standby pump pair, each behind its own suction MOV.

    Start interlocks live in the DCS, not here: the motors obey their commands,
    and the *physics* delivers the consequence. Suction pressure is computed
    from the actual MOV opening, so a pump started against a shut valve
    cavitates whether or not the DCS interlock allowed it, and a pump whose
    vessel has been drawn empty loses prime and develops no head. An ESD trip
    still stops the motor, because a hardwired trip is a real external action
    on the starter rather than a controller in the model.
    """

    def __init__(self, unit: ProcessUnit, tag_a: str, tag_b: str, service: str,
                 mov_a: str, mov_b: str, head_shutoff: float, flow_max: float,
                 flow_min: float, rated_current: float = 100.0,
                 vfd_a: bool = False, travel_time: float = 25.0,
                 duty_running: bool = False) -> None:
        self.service = service
        self.mov_a = MovPackage(unit, mov_a, f"{tag_a} suction isolation",
                                travel_time, 100.0 if duty_running else 0.0)
        self.mov_b = MovPackage(unit, mov_b, f"{tag_b} suction isolation", travel_time)
        self.motor_a = MotorPackage(unit, tag_a, service, rated_current, vfd_a)
        self.motor_b = MotorPackage(unit, tag_b, f"{service} (standby)", rated_current)
        self.pump_a = CentrifugalPump(tag_a, head_shutoff, flow_max, flow_min)
        self.pump_b = CentrifugalPump(tag_b, head_shutoff, flow_max, flow_min)
        self.p_suction_a = self.p_suction_b = 0.0
        self.cav_a = self.cav_b = False
        self._primed = True
        if duty_running:
            self.motor_a.device.running = True
            self.motor_a.device._cmd_start = True
            self.motor_a.cmd_start.value = True
            self.motor_a.device.speed_pct = 100.0
            self.motor_a.device._speed_lag.reset(100.0)

    # ------------------------------------------------------------------ running
    def step(self, dt: float, suction_base: float, flow: float,
             primed: bool = True, speed_ref: float = 100.0,
             vapour_pressure: float = 0.3, friction: float = 0.5,
             design_flow: float = 100.0, trip: bool = False) -> None:
        """Advance both trains. ``suction_base`` is the pressure at the MOV inlet.

        ``primed`` says whether the source vessel still covers the suction
        nozzle. It is not a motor permissive: an unprimed pump keeps running,
        loses prime, develops no head and flags cavitation.
        """
        self.mov_a.step(dt)
        self.mov_b.step(dt)
        self._primed = bool(primed)

        def suction(mov: MovPackage) -> float:
            frac = max(mov.fraction, 1e-3)
            loss = friction * (max(flow, 0.0) / max(design_flow, 1.0)) ** 2 / frac ** 2
            return clamp(suction_base - loss, -0.9, 60.0)

        self.p_suction_a = suction(self.mov_a)
        self.p_suction_b = suction(self.mov_b)

        self.motor_a.step(dt, True, speed_ref, trip)
        self.motor_b.step(dt, True, 100.0, trip)

        self.cav_a = self.pump_a.check_cavitation(self.p_suction_a, vapour_pressure,
                                                  self.motor_a.running)
        self.cav_b = self.pump_b.check_cavitation(self.p_suction_b, vapour_pressure,
                                                  self.motor_b.running)
        if not self._primed:
            if self.motor_a.running:
                self.pump_a.cavitating = self.cav_a = True
            if self.motor_b.running:
                self.pump_b.cavitating = self.cav_b = True

        # Hand each motor its hydraulic load so the ammeter means something.
        # Pumps on a common header share the flow; a cavitating or gas locked
        # pump is moving vapour and runs light, which is the classic symptom.
        share = max(flow, 0.0) / max(self.running_count, 1)
        for pump, motor, cav in ((self.pump_a, self.motor_a, self.cav_a),
                                 (self.pump_b, self.motor_b, self.cav_b)):
            n = max(motor.speed / 100.0, 0.05)
            load = clamp(share / max(pump.flow_max * n, 1.0), 0.05, 1.5)
            if cav or not self._primed:
                load = 0.15
            motor.device.load_frac = load if motor.running else 1.0

    @property
    def running_count(self) -> int:
        return int(self.motor_a.running) + int(self.motor_b.running)

    def discharge_pressure(self, flow: float, density: float = 780.0) -> float:
        """Pressure at the common discharge header, from whichever pump wins."""
        options = []
        for pump, motor, suction, cav in (
                (self.pump_a, self.motor_a, self.p_suction_a, self.cav_a),
                (self.pump_b, self.motor_b, self.p_suction_b, self.cav_b)):
            head = pump.head(flow, motor.speed)
            if not self._primed:
                head = 0.0                # gas locked: no liquid, no head
            elif cav:
                head *= 0.45              # a cavitating pump loses most of its head
            options.append((head, suction))
        head, suction = max(options, key=lambda x: x[0])
        return suction + head * density * 9.81 / 1e5

    @property
    def any_running(self) -> bool:
        return self.motor_a.running or self.motor_b.running

    def state(self) -> dict:
        return {"mov_a": self.mov_a.state(), "mov_b": self.mov_b.state(),
                "motor_a": self.motor_a.device.state(),
                "motor_b": self.motor_b.device.state()}

    def restore(self, s: dict) -> None:
        self.mov_a.restore(s.get("mov_a", {}))
        self.mov_b.restore(s.get("mov_b", {}))
        # Older snapshots carry only run_a/run_b; Motor.restore reconstructs
        # the run command from the run state in that case.
        self.motor_a.device.restore(s.get("motor_a", {"run": s.get("run_a", 0.0)}))
        self.motor_b.device.restore(s.get("motor_b", {"run": s.get("run_b", 0.0)}))


def min_flow_valve(unit: ProcessUnit, tag: str, service: str,
                   cv: float = 60.0) -> ControlValve:
    """Minimum flow recycle valve. Fails open, because that protects the pump."""
    unit.ao(tag, service, value=0.0)
    return ControlValve(tag, cv_rated=cv, char=ValveChar.LINEAR, stroke_time=4,
                        fail_closed=False)
