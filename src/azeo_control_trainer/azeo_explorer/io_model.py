"""The I/O card/channel model — the DST layer, derived.

Azeo shows a controller's I/O as cards holding channels, each channel
carrying a Device Tag, a channel type and an enable. Ours derives from
what is actually attached: the Modbus map's points grouped into cards
by channel type in the course's own order (C01 analog in, C02 analog
out, C03 discrete in, C04 discrete out — p. 91), eight channels to a
card. Nothing here is hand-maintained; a card exists because points of
its type exist.

`referenced` is the honest extra Azeo cannot show this directly: does
any control module actually read or write this DST? A channel whose
device tag no configuration references feeds a point nobody consumes —
the mismatch a re-tagging mistake creates, marked instead of hidden.
"""
from __future__ import annotations

from dataclasses import dataclass

from azeo_control_trainer.connectivity.fieldio.modbus_map import Area

#: Course card order, p. 91: C01 AI, C02 AO, C03 DI, C04 DO.
_CARD_ORDER = (
    (Area.INPUT, "Analog Input Channel"),
    (Area.HOLDING, "Analog Output Channel"),
    (Area.DISCRETE_INPUT, "Discrete Input Channel"),
    (Area.COIL, "Discrete Output Channel"),
)
_CHANNELS_PER_CARD = 8


@dataclass
class IoChannel:
    card: str                   # "C01"
    label: str                  # "CH01"
    tag: str                    # the map's own tag (the channel's key)
    device_tag: str             # what it currently publishes as
    channel_type: str
    address: int                # the drawing address (30001-style)
    description: str
    enabled: bool
    referenced: bool            # does any module touch the device tag?


@dataclass
class IoCard:
    label: str                  # "C01"
    channel_type: str
    channels: list


def _master_of(driver):
    """The ModbusFieldDriver under whatever wrapper we were handed."""
    if driver is None:
        return None
    inner = getattr(driver, "driver", driver)
    return inner if hasattr(inner, "points") \
        and hasattr(inner, "device_tag") else None


def build_io_cards(store, driver) -> list:
    """Cards and channels from the attached field transport; empty
    when nothing channel-shaped is attached (an OPC UA link or a
    simulated plant has signals, not channels)."""
    master = _master_of(driver)
    if master is None:
        return []
    tagdb = getattr(store, "tagdb", None)
    try:
        field_tags = set(tagdb.field_tags()) if tagdb else set()
    except Exception:                               # noqa: BLE001
        field_tags = set()

    cards: list = []
    card_index = 0
    for area, channel_type in _CARD_ORDER:
        points = [p for p in master.points() if p.area is area]
        for start in range(0, len(points), _CHANNELS_PER_CARD):
            card_index += 1
            card = IoCard(f"C{card_index:02d}", channel_type, [])
            for i, point in enumerate(
                    points[start:start + _CHANNELS_PER_CARD]):
                device_tag = master.device_tag(point)
                card.channels.append(IoChannel(
                    card=card.label,
                    label=f"CH{i + 1:02d}",
                    tag=point.tag,
                    device_tag=device_tag,
                    channel_type=channel_type,
                    address=point.address,
                    description=point.description,
                    enabled=point.tag
                    not in master.disabled_channels,
                    referenced=device_tag in field_tags))
            cards.append(card)
    return cards
