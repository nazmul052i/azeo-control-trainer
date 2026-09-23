"""Quality/LimitStatus → OPC UA StatusCode, in one place (HMI §8.2).

The model's `Quality` and `LimitStatus` map onto UA's StatusCode exactly:
severity bits for quality, DataValue info bits for limit. The proposal's
implementation note is the reason this is a module and not inline bit
arithmetic: the limit bits are only meaningful when the InfoType bit says
"DataValue", so forgetting `_INFO_TYPE_DATA_VALUE` produces a code every
client silently ignores — a wrong that looks right.

Qt-free and asyncua-free on purpose: the smoke suite exercises all twelve
quality × limit combinations as integers, no server required.
"""
from __future__ import annotations

from azeo_control_trainer.core.strategy.model.terminal import LimitStatus, Quality

# StatusCode severity (top two bits).
_SEVERITY = {
    Quality.GOOD: 0x00000000,
    Quality.UNCERTAIN: 0x40000000,
    Quality.BAD: 0x80000000,
}

# DataValue info bits (bits 8-9: limit) — meaningful only with InfoType
# set to DataValue (bit 10).
_INFO_TYPE_DATA_VALUE = 0x00000400
_LIMIT_BITS = {
    LimitStatus.NOT_LIMITED: 0x0000,
    LimitStatus.LOW_LIMITED: 0x0100,
    LimitStatus.HIGH_LIMITED: 0x0200,
    LimitStatus.CONSTANT: 0x0300,
}


def to_ua_status(quality: Quality,
                 limit: LimitStatus = LimitStatus.NOT_LIMITED) -> int:
    """The numeric StatusCode for a quality/limit pair.

    Limit bits are carried for every quality — a clamped output that goes
    Uncertain still reports where it is clamped.
    """
    code = _SEVERITY.get(quality)
    if code is None:
        # Tolerate a name-alike (an enum from a reloaded module) — and an
        # unknown quality is Bad, never silently Good.
        name = getattr(quality, "name", str(quality)).upper()
        code = {"GOOD": 0x00000000, "UNCERTAIN": 0x40000000}.get(
            name, 0x80000000)
    if limit is not None and limit != LimitStatus.NOT_LIMITED:
        bits = _LIMIT_BITS.get(limit)
        if bits is None:
            name = getattr(limit, "name", str(limit)).upper()
            bits = {"LOW_LIMITED": 0x0100, "HIGH_LIMITED": 0x0200,
                    "CONSTANT": 0x0300}.get(name, 0x0000)
        if bits:
            code |= _INFO_TYPE_DATA_VALUE | bits
    return code


def from_ua_status(code: int) -> tuple[Quality, LimitStatus]:
    """Inverse of `to_ua_status` — the round-trip the tests assert."""
    severity = code & 0xC0000000
    quality = {0x00000000: Quality.GOOD,
               0x40000000: Quality.UNCERTAIN,
               0x80000000: Quality.BAD}.get(severity, Quality.BAD)
    if code & _INFO_TYPE_DATA_VALUE:
        bits = code & 0x0300
        limit = {v: k for k, v in _LIMIT_BITS.items()}[bits]
    else:
        limit = LimitStatus.NOT_LIMITED
    return quality, limit
