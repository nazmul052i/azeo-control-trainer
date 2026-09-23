"""Field tag ↔ Modbus register, written down where a student can read it.

This file **is** the lesson. In a real DCS somebody sits down with a vendor's
register list and a tag list and writes exactly this table, and every mistake
in it shows up as a point that reads plausible nonsense rather than as an
error. So it is data, not code: an address, a scale, a unit, a direction.

Three things it has to get right, and each is a real-world defect:

- **Direction.** A `read` point is a measurement the field publishes and the
  DCS may only read; a `write` point is a command the DCS owns. Modbus
  enforces this in the protocol — measurements live in *input* registers and
  *discrete inputs*, which have no write function code at all. Get the
  direction wrong and you have a controller writing to its own measurement.
- **Scale.** Modbus carries integers. `LT101_LEVEL_GAL` is gallons × 10, so
  register 8000 means 800.0 gal. Halve the scale by mistake and the level
  reads half; nothing errors.
- **Range.** These are **unsigned 16-bit**: 0..65535. At × 100 that caps a
  flow at 655.35 gpm, and a *negative* value wraps to about 65535 rather
  than going below zero. `signed` marks the points where that matters.

Addresses are given twice: the protocol offset the wire uses (zero-based)
and the conventional display address an engineer reads on a drawing
(1/10001/30001/40001). They are the same register.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Area(str, Enum):
    """The four Modbus spaces, and what each is *for*.

    The split is the safety property: a measurement cannot be written
    because its space has no write function code.
    """

    COIL = "coil"                       # DCS writes  — discrete command
    DISCRETE_INPUT = "discrete_input"   # DCS reads   — discrete feedback
    HOLDING = "holding"                 # DCS writes  — analogue command
    INPUT = "input"                     # DCS reads   — analogue measurement

    @property
    def writable(self) -> bool:
        return self in (Area.COIL, Area.HOLDING)

    @property
    def display_base(self) -> int:
        return {Area.COIL: 1, Area.DISCRETE_INPUT: 10_001,
                Area.INPUT: 30_001, Area.HOLDING: 40_001}[self]


@dataclass(frozen=True)
class Point:
    """One field point: a store tag on one side, a register on the other."""

    tag: str
    area: Area
    offset: int
    scale: float = 1.0
    unit: str = ""
    signed: bool = False
    description: str = ""

    @property
    def address(self) -> int:
        """The address an engineer reads on a drawing."""
        return self.area.display_base + self.offset

    @property
    def writable(self) -> bool:
        return self.area.writable

    def to_engineering(self, raw: int) -> float:
        """Register → engineering units."""
        if self.signed and raw >= 0x8000:
            raw -= 0x10000
        return raw / self.scale

    def to_raw(self, value: float) -> int:
        """Engineering units → register, clamped to what 16 bits can hold.

        Clamped rather than wrapped. A value that will not fit is a
        configuration error, and 70000 gpm arriving as 4464 is the kind of
        wrong that looks right.
        """
        raw = int(round(float(value) * self.scale))
        if self.signed:
            raw = max(-32768, min(32767, raw))
            return raw & 0xFFFF
        return max(0, min(65535, raw))


#: The tag names on the left are **ours**; the registers on the
#: right are the simulator's published contract. Renaming a tag here is how
#: an engineer re-points a module at a different field device.
POINTS: tuple[Point, ...] = (
    # ---- commands the controller owns (DCS writes) ---------------------
    Point("MTR-101.do_start", Area.COIL, 0,
          description="Motor start command"),
    Point("XV-101.solenoid", Area.COIL, 1,
          description="Block valve open command"),
    Point("MTR-101.reset", Area.COIL, 2,
          description="Trip reset pulse"),
    Point("FV-101.OUT", Area.HOLDING, 0, 100.0, "%",
          description="Control-valve demand"),
    Point("VFD-101.speed_ref", Area.HOLDING, 1, 100.0, "%",
          description="VFD speed reference"),
    # **The feed.** Holding 2 is the device's tank inlet, and it was simply
    # missing from this table — so the trainer could drain TANK-101 and
    # never fill it, and `LIC-101` was a level controller with no inflow to
    # balance against. A level loop whose level can only fall cannot hold a
    # setpoint, and the exercise was one-shot: run the sequence once, empty
    # the tank, and there is nothing left to control.
    Point("FEED-101.OUT", Area.HOLDING, 2, 100.0, "gpm",
          description="Tank feed / makeup flow into TANK-101"),

    # ---- measurements the field publishes (DCS reads) -------------------
    Point("XV-101.ZSO", Area.DISCRETE_INPUT, 0,
          description="Block valve open limit switch"),
    Point("XV-101.ZSC", Area.DISCRETE_INPUT, 1,
          description="Block valve closed limit switch"),
    Point("MTR-101.run_fb", Area.DISCRETE_INPUT, 2,
          description="Motor running feedback"),
    Point("MTR-101.ready", Area.DISCRETE_INPUT, 3,
          description="Motor ready to start"),
    Point("MTR-101.trip", Area.DISCRETE_INPUT, 4,
          description="Motor tripped"),
    Point("VFD-101.ready", Area.DISCRETE_INPUT, 5),
    Point("VFD-101.fault", Area.DISCRETE_INPUT, 6),
    Point("SYS.comms_ok", Area.DISCRETE_INPUT, 7,
          description="Field says the link is healthy"),
    Point("SYS.power_healthy", Area.DISCRETE_INPUT, 8),

    Point("LT-101.PV", Area.INPUT, 0, 10.0, "gal",
          description="Tank level"),
    Point("LT-101.PCT", Area.INPUT, 1, 100.0, "%"),
    Point("FT-101.PV", Area.INPUT, 2, 100.0, "gpm",
          description="Discharge flow"),
    Point("PT-101.PV", Area.INPUT, 3, 100.0, "psi",
          description="Pump discharge pressure"),
    Point("FV-101.ZT", Area.INPUT, 4, 100.0, "%",
          description="Control-valve position feedback"),
    Point("XV-101.ZT", Area.INPUT, 5, 100.0, "%"),
    Point("VFD-101.speed_pv", Area.INPUT, 6, 100.0, "%"),
    Point("MTR-101.current", Area.INPUT, 7, 100.0, "A"),
    Point("MTR-101.power", Area.INPUT, 8, 100.0, "kW"),
    Point("P-101.head", Area.INPUT, 9, 100.0, "ft"),
    Point("SYS.comms_age_ms", Area.INPUT, 11, 1.0, "ms",
          description="How stale the field thinks our commands are"),
    Point("VFD-101.frequency", Area.INPUT, 12, 100.0, "Hz"),
    Point("MTR-101.speed", Area.INPUT, 13, 1.0, "rpm"),
)

#: The register the DCS must keep changing or the field goes to safe state.
#: Not in `POINTS` because no control module owns it — the *link* owns it,
#: and a module that had to remember to write a heartbeat would be a module
#: that fails safe only when its engineer remembered.
HEARTBEAT = Point("SYS.heartbeat", Area.HOLDING, 3,
                  description="Command heartbeat — the field's watchdog")

BY_TAG = {point.tag: point for point in POINTS}


def reads() -> tuple[Point, ...]:
    """Points the field publishes to us."""
    return tuple(p for p in POINTS if not p.writable)


def writes() -> tuple[Point, ...]:
    """Points we publish to the field."""
    return tuple(p for p in POINTS if p.writable)


def table() -> str:
    """The map as an engineer would read it. Printed by `--modbus-map`."""
    lines = [f"{'TAG':22} {'ADDR':>6}  {'DIR':5} {'SCALE':>7}  UNIT  DESCRIPTION",
             "-" * 92]
    for point in POINTS:
        lines.append(
            f"{point.tag:22} {point.address:>6}  "
            f"{'write' if point.writable else 'read ':5} "
            f"{point.scale:>7g}  {point.unit or '-':5} {point.description}")
    lines.append(
        f"{HEARTBEAT.tag:22} {HEARTBEAT.address:>6}  write "
        f"{HEARTBEAT.scale:>7g}  {'-':5} {HEARTBEAT.description}")
    return "\n".join(lines)
