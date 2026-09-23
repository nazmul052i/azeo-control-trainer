"""PlantPAx-style dynamo PVMs — the tank with a trend, the analog bar.

The reference display's two workhorses:

- **AnalogBarPvm** — the vertical bar indicator: grey caps, light
  track, blue normal band between the L and H limits, pointer triangle
  at the value. The bar goes red/amber at the breached end during an
  alarm — colour only where something is abnormal.
- **TankTrendPvm** — a vessel silhouette with the recent history drawn
  INSIDE it and the level as a fill. A tank that shows where it has
  been answers the question a number cannot: is it filling or falling?

Both bind one path and read everything else from the type; history for
the sparkline is display-side state kept by the canvas item (the model
has no business buffering pixels).
"""
from __future__ import annotations

from .base import Bind, PvmClass, register_pvm

_ANALOG_BINDS = (
    Bind("pv.value", "{path}/OUT"),
    Bind("pv.units", "{path}/OUT", prop="EngineeringUnits"),
    Bind("pv.range", "{path}/OUT", prop="EURange"),
    Bind("pv.quality", "{path}/OUT", prop="StatusCode"),
    Bind("pv.forced", "{path}/OUT", prop="Forced"),
    Bind("limits.hi_hi", "{path}/CONFIG/HI_HI_LIM"),
    Bind("limits.hi", "{path}/CONFIG/HI_LIM"),
    Bind("limits.lo", "{path}/CONFIG/LO_LIM"),
    Bind("limits.lo_lo", "{path}/CONFIG/LO_LO_LIM"),
)


@register_pvm
class AnalogBarPvm(PvmClass):
    block_type = "AI"
    role = "dynamo_inline"
    display_name = "Analog Bar"
    DEFAULT_SIZE = (58.0, 150.0)

    bindings = _ANALOG_BINDS


@register_pvm
class AnalogFanPvm(PvmClass):
    """A read-only scale indicator paired with the standard AI faceplate."""

    block_type = "AI"
    role = "dynamo_inline"
    variant = "fan_indicator"
    display_name = "Fan Indicator"
    DEFAULT_SIZE = (184.0, 150.0)
    bindings = _ANALOG_BINDS


@register_pvm
class TankTrendPvm(PvmClass):
    block_type = "AI"
    role = "dynamo_inline"
    variant = "tank"
    display_name = "Tank with Trend"
    DEFAULT_SIZE = (150.0, 170.0)

    bindings = _ANALOG_BINDS
