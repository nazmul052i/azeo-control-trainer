"""Engineering-unit sets: a presentation layer, not a model change.

The models, the tag database, the PID blocks, the alarm schedule and the
snapshots stay in SI. This module converts a value at the moment it is
shown or entered: the faceplates, the variables window, the trends, the
alarm summary, the OPC UA value and EU nodes and the exported
catalogue read through :func:`value`, :func:`lo`, :func:`hi`,
:func:`eu` and :func:`fmt`, and hand an operator's or a DCS client's
number back through :func:`internal`. Two unit sets ship:

* ``si`` - what the tags declare (m3/h, barg, degC, t/h, ...); no
  conversion at all, the presented number is the stored one.
* ``refinery`` - bpd, psig, degF, klb/h, MMscfd, inH2O, MMBtu/h, ...

The set is a start-up choice (``run.py --units refinery``) recorded in
the snapshot, so a saved plant reopens in the units it was saved in,
and in the exported catalogue, so a client configured against it reads
the numbers the server publishes. Tag names, node identifiers, ranges
in percent and the model's internal state never change with the set.

Conversions are ``display = internal * factor + offset``; the offset is
only ever temperature. A *difference* of a quantity (a deviation alarm
band, a span) converts by the factor alone: :func:`delta`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Optional

SI = "si"
REFINERY = "refinery"
UNIT_SETS = (SI, REFINERY)

BBL_PER_M3 = 6.28981                 # US barrels per cubic metre
SCF_PER_NM3 = 35.3147                # standard cubic feet per normal cubic metre
LB_PER_KG = 2.20462
PSI_PER_BAR = 14.5038
INH2O_PER_MMH2O = 1.0 / 25.4
INH2O_PER_MBAR = 0.401463
BTU_SCF_PER_MJ_NM3 = 26.8392         # 1 MJ/Nm3 = 26.84 Btu/scf
MMBTU_H_PER_MW = 3.41214
GAL_PER_L = 0.264172
LBFT3_PER_KGM3 = 0.0624280


@dataclass(frozen=True)
class Conversion:
    eu: str            # the presented unit
    factor: float      # display = internal * factor + offset
    offset: float = 0.0
    decimals: Optional[int] = None   # presented decimals; None keeps the tag's own rule

    def to_display(self, v: float) -> float:
        return v * self.factor + self.offset

    def to_internal(self, v: float) -> float:
        return (v - self.offset) / self.factor


# the refinery set, keyed by the SI unit string the tags declare. A unit
# absent here presents unchanged (%, A, mol%, ppm, pH, rpm, ...).
REFINERY_TABLE: Dict[str, Conversion] = {
    "m3/h":    Conversion("bpd", BBL_PER_M3 * 24.0, 0.0, 0),        # 46 m3/h -> 6943 bpd
    "m3":      Conversion("bbl", BBL_PER_M3, 0.0, 1),
    "L/h":     Conversion("gal/h", GAL_PER_L, 0.0, 1),
    "Nm3/h":   Conversion("kscfh", SCF_PER_NM3 / 1000.0, 0.0, 2),     # 2500 Nm3/h -> 88.3 kscfh
    "kNm3/h":  Conversion("MMscfd", SCF_PER_NM3 * 24.0 / 1000.0, 0.0, 2),   # 30 kNm3/h -> 25.4 MMscfd
    "kg/h":    Conversion("lb/h", LB_PER_KG, 0.0, 0),
    "t/h":     Conversion("klb/h", LB_PER_KG, 0.0, 2),
    "kg/kmol": Conversion("lb/lbmol", 1.0, 0.0, None),
    "kg/m3":   Conversion("lb/ft3", LBFT3_PER_KGM3, 0.0, 2),
    "barg":    Conversion("psig", PSI_PER_BAR, 0.0, 1),
    "bara":    Conversion("psia", PSI_PER_BAR, 0.0, 1),
    "bar":     Conversion("psi", PSI_PER_BAR, 0.0, 2),
    "mbar":    Conversion("inH2O", INH2O_PER_MBAR, 0.0, 1),
    "mmH2O":   Conversion("inH2O", INH2O_PER_MMH2O, 0.0, 2),
    "degC":    Conversion("degF", 1.8, 32.0, 1),
    "K":       Conversion("degR", 1.8, 0.0, 1),
    "mm":      Conversion("in", 1.0 / 25.4, 0.0, 2),
    "micron":  Conversion("mil", 1.0 / 25.4, 0.0, 2),
    "MW":      Conversion("MMBtu/h", MMBTU_H_PER_MW, 0.0, 2),
    "kW":      Conversion("MMBtu/h", MMBTU_H_PER_MW / 1000.0, 0.0, 3),
    "MJ/Nm3":  Conversion("Btu/scf", BTU_SCF_PER_MJ_NM3, 0.0, 0),
    "MJ/kg":   Conversion("Btu/lb", 429.923, 0.0, 0),
}

_TABLES: Dict[str, Dict[str, Conversion]] = {SI: {}, REFINERY: REFINERY_TABLE}
_current = SI


def select(name: str) -> str:
    """Choose the unit set for the process. Returns the name selected."""
    global _current
    key = (name or SI).strip().lower()
    if key not in _TABLES:
        raise ValueError(f"unknown unit set {name!r}; choose one of {UNIT_SETS}")
    _current = key
    return _current


def current() -> str:
    return _current


def conversion(si_eu: str, unit_set: Optional[str] = None) -> Optional[Conversion]:
    """The conversion for an SI unit string under the set, None if unchanged."""
    return _TABLES[unit_set or _current].get(si_eu)


# ---------------------------------------------------------------- scalars

def to_display(si_eu: str, v: float) -> float:
    c = conversion(si_eu)
    return c.to_display(v) if c else v


def to_internal(si_eu: str, v: float) -> float:
    c = conversion(si_eu)
    return c.to_internal(v) if c else v


def delta(si_eu: str, dv: float) -> float:
    """A difference of the quantity: the factor without the offset."""
    c = conversion(si_eu)
    return dv * c.factor if c else dv


def delta_internal(si_eu: str, dv: float) -> float:
    c = conversion(si_eu)
    return dv / c.factor if c else dv


def display_eu(si_eu: str) -> str:
    c = conversion(si_eu)
    return c.eu if c else si_eu


def decimals(si_eu: str, lo_si: float, hi_si: float) -> int:
    """Decimals to present: the conversion's own choice, else the tag rule
    (0 above 500, 1 above 20, 2 below) applied to the presented range."""
    c = conversion(si_eu)
    if c and c.decimals is not None:
        return c.decimals
    mag = max(abs(to_display(si_eu, lo_si)), abs(to_display(si_eu, hi_si)))
    return 0 if mag >= 500 else (1 if mag >= 20 else 2)


# ------------------------------------------------------------------- tags

def value(tag) -> float:
    """The tag's value as presented (discretes come back as 0/1 floats)."""
    v = float(tag.value)
    return to_display(tag.eu, v) if tag.kind.analogue else v


def lo(tag) -> float:
    return to_display(tag.eu, float(tag.lo)) if tag.kind.analogue else float(tag.lo)


def hi(tag) -> float:
    return to_display(tag.eu, float(tag.hi)) if tag.kind.analogue else float(tag.hi)


def eu(tag) -> str:
    return display_eu(tag.eu) if tag.kind.analogue else ""


def internal(tag, presented: float) -> float:
    """An operator's or a client's number, back in the tag's own unit."""
    return to_internal(tag.eu, float(presented)) if tag.kind.analogue else float(presented)


def fmt(tag) -> str:
    """The tag's value with its presented unit, the tag's own rule for decimals."""
    if not tag.kind.analogue:
        return tag.state1 if tag.value else tag.state0
    dp = decimals(tag.eu, float(tag.lo), float(tag.hi))
    return f"{value(tag):.{dp}f} {eu(tag)}".strip()


def fmt_value(si_eu: str, v: float, lo_si: float = 0.0, hi_si: float = 100.0) -> str:
    """A number in the tag's SI unit, presented under the set with its unit."""
    dp = decimals(si_eu, lo_si, hi_si)
    return f"{to_display(si_eu, v):.{dp}f} {display_eu(si_eu)}".strip()


def covers(eus: Iterable[str], unit_set: str = REFINERY) -> list[str]:
    """SI unit strings the set leaves unchanged (for a coverage report)."""
    table = _TABLES[unit_set]
    return sorted({e for e in eus if e and e not in table})
