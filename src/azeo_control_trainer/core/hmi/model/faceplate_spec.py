"""The faceplate field contract, as objects.

`faceplate_fields.json` says which block pin each faceplate field reads. It
was written and verified against live block instances — every `param` in it
exists on the block it names — and then read by nothing but its own test, so
faceplates still showed a fixed PV/SP/OUT view while the contract described a
much richer one.

This loads it. Two things it deliberately does not do:

- **It does not invent a value for a pin that is absent.** `FieldValue`
  carries `present`, and a field whose pin is missing renders the dash glyph.
  The reference implementation this contract came from defaults a missing pin
  to `0` and draws it as a number, which is worse than a blank: an operator
  reads a fabricated `0.0` on a level as a real empty vessel. This is I6 —
  bad quality never renders a stale number — applied one level down, to a
  pin rather than a tag.
- **It does not decide layout.** The vertical order of a faceplate is fixed
  by `docs/console/05` for every type, and a contract that could reorder it would
  undo the reason the order is fixed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

CONTRACT_PATH = Path(__file__).with_name("faceplate_fields.json")

#: What a field is. `bar` and `mode` are drawn by the plate's own body; the
#: rest are rows the pin band renders.
VALUE = "value"
STATE = "state"
ALARM = "alarm"
COMMAND = "command"
MODE = "mode"
BAR = "bar"

#: What the plates already draw from `TagSnapshot` themselves. Repeating them
#: in the pin band would put PV on a faceplate twice, under two labels, which
#: reads as two different numbers that happen to agree.
CORE_PARAMS = frozenset({"PV", "SP", "OUT", "MODE", "ACTUAL_MODE",
                         # The discrete plate's large word is this pin.
                         # Repeating it as a row put RUNNING on the
                         # faceplate twice, under two labels.
                         "RUNNING"})


@dataclass(frozen=True)
class FieldSpec:
    """One row of a faceplate, as the contract declares it."""

    label: str
    type: str
    param: str = ""
    command: str = ""
    derive: str = ""
    units: str = ""
    decimals: int | None = None
    #: Names for an integer state. DEVCTL's `STATE` is an enumeration,
    #: and `2.0` on an operator's screen is a number with no meaning.
    choices: tuple[str, ...] = ()

    @property
    def is_core(self) -> bool:
        return self.param in CORE_PARAMS or self.type in (BAR, MODE)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FieldSpec":
        return cls(label=data.get("label", ""), type=data.get("type", VALUE),
                   param=data.get("param", ""),
                   command=data.get("command", ""),
                   derive=data.get("derive", ""),
                   units=data.get("units", ""),
                   decimals=data.get("decimals"),
                   choices=tuple(data.get("choices", ())))


@dataclass(frozen=True)
class BlockSpec:
    """Every field one block type publishes to its faceplate."""

    name: str
    title: str
    block: str
    faceplate: str
    modes: tuple[str, ...]
    fields: tuple[FieldSpec, ...]

    def pin_fields(self) -> tuple[FieldSpec, ...]:
        """The fields the plate does not already draw — the ones that make a
        device faceplate say more than "running"."""
        return tuple(f for f in self.fields
                     if not f.is_core and f.type != COMMAND)

    def commands(self) -> tuple[str, ...]:
        return tuple(f.command for f in self.fields
                     if f.type == COMMAND and f.command)


@dataclass(frozen=True)
class FieldValue:
    """A field's value, and whether there was one.

    `present` is the whole point. Without it a caller cannot tell a pin
    reading zero from a pin that is not there, and the two must never draw
    the same.
    """

    spec: FieldSpec
    value: Any = None
    present: bool = False

    def text(self, dash: str = "--") -> str:
        if not self.present:
            return dash
        numeric = (isinstance(self.value, (int, float))
                   and not isinstance(self.value, bool))
        if self.spec.choices and numeric:
            index = int(self.value)
            if 0 <= index < len(self.spec.choices):
                return self.spec.choices[index]
            # Out of range is a real answer, not a blank: a device reporting
            # a state nobody declared is worth seeing rather than hiding.
            return f"STATE {index}"
        if self.spec.type == STATE or isinstance(self.value, bool):
            return "ON" if _truthy(self.value) else "OFF"
        if isinstance(self.value, (int, float)):
            decimals = 1 if self.spec.decimals is None else self.spec.decimals
            return f"{float(self.value):.{decimals}f}"
        return str(self.value)

    @property
    def active(self) -> bool:
        """For an alarm or state field: is it asserted? Absent is not
        active — an alarm lamp that lights because nothing answered is a
        lamp nobody will believe the next time."""
        return self.present and _truthy(self.value)


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().upper() not in ("", "0", "OFF", "FALSE", "NO")
    return bool(value)


@lru_cache(maxsize=1)
def _contract() -> dict:
    return json.loads(CONTRACT_PATH.read_bytes().decode("utf-8"))


@lru_cache(maxsize=1)
def definitions() -> dict[str, BlockSpec]:
    """Every definition, keyed by its own name (`motor`, `device`, …)."""
    out: dict[str, BlockSpec] = {}
    for name, data in _contract()["definitions"].items():
        out[name] = BlockSpec(
            name=name, title=data.get("title", name),
            block=data["block"], faceplate=data["faceplate"],
            modes=tuple(data.get("modes", ())),
            fields=tuple(FieldSpec.from_dict(f)
                         for f in data.get("fields", ())))
    return out


@lru_cache(maxsize=1)
def specs() -> dict[str, BlockSpec]:
    """Every definition, keyed by the **block type** it describes.

    More than one definition may name the same block — `motor` and `device`
    are both `DEVCTL`, one flavoured for a motor and one generic. Keying
    straight off the block type let whichever sorted last win and silently
    dropped the other's fields, so a motor faceplate lost its `RUNNING` state
    to a generic device definition purely because of file order.

    They are merged instead, because both describe pins the same block really
    publishes and the union is the truth about it. The merge is by `param`
    (or by label for the paramless), in definition-name order, first
    occurrence winning — deterministic, so the faceplate does not change
    shape when somebody reorders the file.
    """
    grouped: dict[str, list[BlockSpec]] = {}
    for name in sorted(definitions()):
        spec = definitions()[name]
        grouped.setdefault(spec.block, []).append(spec)

    out: dict[str, BlockSpec] = {}
    for block, group in grouped.items():
        if len(group) == 1:
            out[block] = group[0]
            continue
        seen: set[str] = set()
        fields: list[FieldSpec] = []
        modes: list[str] = []
        for spec in group:
            for field_spec in spec.fields:
                key = field_spec.param or f"~{field_spec.label}"
                if key in seen:
                    continue
                seen.add(key)
                fields.append(field_spec)
            for mode in spec.modes:
                if mode not in modes:
                    modes.append(mode)
        first = group[0]
        out[block] = BlockSpec(
            name="+".join(spec.name for spec in group),
            title=first.title, block=block, faceplate=first.faceplate,
            modes=tuple(modes), fields=tuple(fields))
    return out


def spec_for(block_type: str | None) -> BlockSpec | None:
    """The contract for a block type, or None when it has no faceplate.

    None rather than an empty spec: "this block type has no declared
    faceplate" and "this faceplate declares no fields" are different, and a
    caller that cannot tell them apart draws an empty band for both.
    """
    if not block_type:
        return None
    return specs().get(str(block_type).upper())


def read(spec: BlockSpec, pins: Mapping[str, Any]) -> tuple[FieldValue, ...]:
    """Resolve every pin field against what the block actually published."""
    out = []
    for field in spec.pin_fields():
        if field.derive:
            value, present = _derive(field.derive, pins)
        elif field.param in pins and pins[field.param] is not None:
            value, present = pins[field.param], True
        else:
            value, present = None, False
        out.append(FieldValue(spec=field, value=value, present=present))
    return tuple(out)


def _derive(expression: str, pins: Mapping[str, Any]) -> tuple[Any, bool]:
    """The four derived fields the contract declares.

    A tiny table rather than `eval`: the expressions are data from a file,
    and a faceplate is not a place to find out that a file could run code.
    An expression nobody implemented resolves to *absent*, which draws a
    dash — the honest answer for "declared but not computed".
    """
    text = expression.strip()
    if text == "SP - PV":
        left, right = pins.get("SP"), pins.get("PV")
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            return float(left) - float(right), True
    return None, False


__all__ = ["ALARM", "BAR", "COMMAND", "CORE_PARAMS", "MODE", "STATE", "VALUE",
           "BlockSpec", "FieldSpec", "FieldValue", "read", "spec_for", "specs"]
