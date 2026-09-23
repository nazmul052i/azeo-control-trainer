"""Process PVMs — the PlantPAx display set, as registered classes.

The reference graphic's equipment vocabulary, each one a variant so the
plain card and symbol looks stay available:

- **ValveOpPvm** — the control-valve silhouette with the OP bar under
  it. A valve's one honest number is how open the controller has driven
  it; the bar answers at a glance what the symbol alone cannot.
- **VesselTrendPvm** — a vessel with the level as a vertical bar AND
  the recent history INSIDE the shell. The bar carries the limits, the
  trend carries the direction — together they answer "where is it and
  where is it going" without a faceplate.
- **Equipment status PVMs** — pump, compressor, fan, blower, turbine,
  motor: the P&ID silhouette with the running state as fill density
  plus a text label (never green vs red), the alarm badge riding on
  fault. DEVICE_KIND only distinguishes MOTOR from VALVE, so the
  specific machine is the variant's business, chosen at placement.

All of them stay one-parameter PVMs (§10.2): the path is the only thing
a placement carries; symbol, bar geometry and state vocabulary are the
class's.
"""
from __future__ import annotations

from .base import Bind, PvmClass, register_pvm
from .control import PIDInline
from .device import DeviceSymbol
from .dynamos import _ANALOG_BINDS


PROCESS_CONNECTION_POINTS = (
    {"name": "inlet", "x": 0.0, "y": 0.5},
    {"name": "outlet", "x": 1.0, "y": 0.5},
    {"name": "top", "x": 0.5, "y": 0.0},
    {"name": "bottom", "x": 0.5, "y": 1.0},
)


@register_pvm
class ValveOpPvm(PvmClass):
    block_type = "AO"
    role = "dynamo_inline"
    variant = "valve"
    display_name = "Valve with OP Bar"
    DEFAULT_SIZE = (96.0, 112.0)

    bindings = (
        Bind("out.value", "{path}/OUT"),
        Bind("out.units", "{path}/OUT", prop="EngineeringUnits"),
        Bind("out.range", "{path}/OUT", prop="EURange"),
        Bind("out.limit", "{path}/OUT", prop="Limit"),
        Bind("out.quality", "{path}/OUT", prop="StatusCode"),
        Bind("out.forced", "{path}/OUT", prop="Forced"),
        Bind("readback.value", "{path}/READBACK"),
        Bind("mode", "{path}/MODE"),
    )


@register_pvm
class VesselTrendPvm(PvmClass):
    block_type = "AI"
    role = "dynamo_inline"
    variant = "vessel"
    display_name = "Vessel with Bar + Trend"
    DEFAULT_SIZE = (160.0, 190.0)
    CONNECTION_POINTS = PROCESS_CONNECTION_POINTS

    bindings = _ANALOG_BINDS


@register_pvm
class PIDVesselTrendPvm(PvmClass):
    """A process vessel driven by a PID loop, opening the PID faceplate."""

    block_type = "PID"
    role = "dynamo_inline"
    variant = "vessel"
    display_name = "PID Vessel with Bar + Trend"
    DEFAULT_SIZE = (160.0, 190.0)
    CONNECTION_POINTS = PROCESS_CONNECTION_POINTS

    bindings = PIDInline.bindings + (
        Bind("limits.hi_hi", "{path}/CONFIG/hi_hi_lim"),
        Bind("limits.hi", "{path}/CONFIG/hi_lim"),
        Bind("limits.lo", "{path}/CONFIG/lo_lim"),
        Bind("limits.lo_lo", "{path}/CONFIG/lo_lo_lim"),
    )


@register_pvm
class HorizontalBarPvm(PvmClass):
    """The horizontal analog indicator — the same bar vocabulary as
    AnalogBarPvm turned on its side, for the reading that sits beside
    a pipe run rather than a vessel."""

    block_type = "AI"
    role = "dynamo_inline"
    variant = "hbar"
    display_name = "Horizontal Bar"
    DEFAULT_SIZE = (170.0, 52.0)

    bindings = _ANALOG_BINDS


@register_pvm
class ReactorBarPvm(PvmClass):
    """A reactor silhouette with its measurement as a vertical bar
    beside the shell — limits ticked on the bar, value beneath, the
    equipment and its number read together."""

    block_type = "AI"
    role = "dynamo_inline"
    variant = "reactor"
    display_name = "Reactor with Bar"
    DEFAULT_SIZE = (130.0, 160.0)

    bindings = _ANALOG_BINDS


def _status_pvm(machine: str, title: str) -> type:
    """One DEVCTL status class per machine — same bindings as
    DeviceSymbol; the variant pins the silhouette."""

    @register_pvm
    class _StatusPvm(PvmClass):
        block_type = "DEVCTL"
        role = "dynamo_compact"
        variant = machine
        display_name = title
        DEFAULT_SIZE = (84.0, 88.0)

        bindings = DeviceSymbol.bindings

    _StatusPvm.__name__ = f"{title.replace(' ', '')}StatusPvm"
    _StatusPvm.__qualname__ = _StatusPvm.__name__
    return _StatusPvm


PumpStatusPvm = _status_pvm("pump", "Pump Status")
CompressorStatusPvm = _status_pvm("compressor", "Compressor Status")
FanStatusPvm = _status_pvm("fan", "Fan Status")
BlowerStatusPvm = _status_pvm("blower", "Blower Status")
TurbineStatusPvm = _status_pvm("turbine", "Turbine Status")
MotorStatusPvm = _status_pvm("motor", "Motor Status")
TaperedTurbineStatusPvm = _status_pvm("turbine_tapered", "Tapered Turbine")
TaperedCompressorStatusPvm = _status_pvm("compressor_tapered", "Tapered Compressor")
