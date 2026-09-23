"""The instrument model's parameters and the realism levels that set them.

Every transmitter (``core.devices.Transmitter``) carries a measurement
chain - sensor lag, white noise, a slow random walk, calibration offset,
drift, transmitter damping, and for an analyser a sample line dead time
and a cycle time - and every control valve carries a positioner loop
beside its stiction, hysteresis and stroke time. The parameters live on
the devices, on both cores; this module is the one place that knows
which they are, what a *level* of realism sets them to, and how to walk
the plant to apply or report them.

A level never touches what the models were built with: the white noise
bands, the sensor lags and the analyser cycles stay as the unit set
them, because those are the instruments' data sheets. A level adds the
imperfections around them. ``off`` adds nothing, and is the plant every
test lines up against; ``typical`` is a well-maintained plant; ``poor``
is one that is due a turnaround. Per-instrument overrides land on top of
the level, so an exercise can give one transmitter a walk or one valve
a slow positioner without changing the rest.

    from azeoplant.core import instruments
    instruments.apply(flowsheet, "typical")
    instruments.apply(flowsheet, "typical", {"FT-1001": {"damping": 2.0}})
    rows = instruments.describe(flowsheet)      # what tools/audit_models.py prints
"""

from __future__ import annotations

import zlib
from dataclasses import asdict, dataclass, fields
from typing import Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class RealismLevel:
    """What one level sets on every transmitter and every control valve."""

    name: str
    label: str
    note: str
    # transmitters
    walk_sigma_pct: float        # slow random walk, % span (standard deviation)
    walk_tau: float              # its time constant, s
    damping: float               # transmitter damping, s
    offset_pct: float            # calibration offset magnitude, % span; sign and size per tag
    drift_pct_per_hour: float    # drift rate applied to every transmitter
    # control valves
    positioner_time: float       # servo time constant, s
    positioner_overshoot: float  # % of a step
    positioner_deadband: float   # % of span


LEVELS: Dict[str, RealismLevel] = {
    "off": RealismLevel(
        "off", "Off", "The plant as commissioned: white noise bands only, ideal positioners.",
        walk_sigma_pct=0.0, walk_tau=600.0, damping=0.0, offset_pct=0.0, drift_pct_per_hour=0.0,
        positioner_time=0.0, positioner_overshoot=0.0, positioner_deadband=0.0),
    # The positioner numbers were chosen against the plant, not from a data
    # sheet: the combustion cross-limits (FIC-3002/3003, FIC-7002/7003) run
    # unfiltered because their speed is their protection, and with the
    # commissioned tuning they lose their margin at half a second of
    # positioner lag - the charge is then cut back by the firing limit and
    # the columns go off spec. A quarter second holds the 10,000 bpd basis
    # for an hour (docs/Validation_Basis.md); the slower positioners are
    # what the "poor" level and the per-instrument table are for.
    "typical": RealismLevel(
        "typical", "Typical", "A well-maintained plant: slow wander, half a second of damping, "
        "quarter-percent calibration, a quick positioner with a little overshoot.",
        walk_sigma_pct=0.15, walk_tau=900.0, damping=0.5, offset_pct=0.25, drift_pct_per_hour=0.0,
        positioner_time=0.25, positioner_overshoot=5.0, positioner_deadband=0.25),
    "poor": RealismLevel(
        "poor", "Poor", "Due a turnaround: half a percent of wander, a second of damping, "
        "one percent calibration and drift, a positioner that overshoots and has a deadband. "
        "The plant stays up; the flow and combustion loops visibly struggle.",
        walk_sigma_pct=0.5, walk_tau=300.0, damping=1.0, offset_pct=1.0, drift_pct_per_hour=0.02,
        positioner_time=0.35, positioner_overshoot=10.0, positioner_deadband=0.5),
}

#: the transmitter parameters the settings and the audit see, in the order shown
TX_PARAMS: Tuple[str, ...] = ("tau", "noise_sigma_pct", "walk_sigma_pct", "walk_tau", "damping",
                              "offset_pct", "drift_pct_per_hour", "update_period", "transport")
#: the control valve parameters likewise
VALVE_PARAMS: Tuple[str, ...] = ("stroke_time", "positioner_time", "positioner_overshoot",
                                 "positioner_deadband", "stiction", "hysteresis")

#: the parameters a level sets, as (level field, device attribute)
_TX_LEVEL = ("walk_sigma_pct", "walk_tau", "damping", "drift_pct_per_hour")
_VALVE_LEVEL = ("positioner_time", "positioner_overshoot", "positioner_deadband")

PARAM_HELP: Dict[str, Tuple[str, str]] = {
    "tau": ("s", "sensor response, first order"),
    "noise_sigma_pct": ("% span", "white noise, standard deviation"),
    "walk_sigma_pct": ("% span", "slow random walk, standard deviation"),
    "walk_tau": ("s", "random walk time constant"),
    "damping": ("s", "transmitter damping, first order, after the noise"),
    "offset_pct": ("% span", "calibration offset"),
    "drift_pct_per_hour": ("%/h", "drift rate"),
    "update_period": ("s", "analyser cycle time; 0 = continuous"),
    "transport": ("s", "sample line dead time"),
    "stroke_time": ("s", "full stroke time"),
    "positioner_time": ("s", "positioner servo time constant; 0 = ideal"),
    "positioner_overshoot": ("%", "positioner overshoot on a step"),
    "positioner_deadband": ("% span", "positioner deadband"),
    "stiction": ("% span", "stick band"),
    "hysteresis": ("% span", "backlash"),
}


def level(name: str) -> RealismLevel:
    key = str(name or "off").strip().lower()
    if key not in LEVELS:
        raise ValueError(f"unknown realism level {name!r}; one of {', '.join(LEVELS)}")
    return LEVELS[key]


def calibration_offset(tag: str, magnitude_pct: float) -> float:
    """A calibration error for this tag: deterministic, spread over
    ``[-magnitude, +magnitude]`` so a plant does not read uniformly high."""
    if magnitude_pct == 0.0:
        return 0.0
    frac = (zlib.crc32(str(tag).encode()) % 2001) / 1000.0 - 1.0
    return magnitude_pct * frac


# ----------------------------------------------------------------- the plant
def transmitters(flowsheet) -> List[Tuple[str, str, object]]:
    """``(unit code, attribute, transmitter)`` for every transmitter in the
    plant, on either core, in unit order."""
    out = []
    for unit in flowsheet.units:
        fn = getattr(unit, "transmitters", None)
        if callable(fn):
            for attr, tx in fn().items():
                out.append((unit.code, attr, tx))
    return out


def valves(flowsheet) -> List[Tuple[str, str, object]]:
    """``(unit code, attribute, valve)`` for every control valve likewise."""
    out = []
    for unit in flowsheet.units:
        fn = getattr(unit, "valves", None)
        if callable(fn):
            for attr, v in fn().items():
                out.append((unit.code, attr, v))
    return out


def apply(flowsheet, level_name: str, overrides: Optional[Dict[str, Dict[str, float]]] = None) -> dict:
    """Set the level on every instrument, then the overrides on top.

    ``overrides`` maps a tag (``FT-1001``, ``FCV-1001``) to the parameters
    to set for it, by the names in ``TX_PARAMS`` / ``VALVE_PARAMS``. An
    unknown tag or parameter is reported, not raised: a settings file
    written against another build must not stop the plant starting.
    Returns ``{"level", "transmitters", "valves", "overridden", "unknown"}``.
    """
    lv = level(level_name)
    ov = {str(k): dict(v) for k, v in (overrides or {}).items() if isinstance(v, dict)}
    seen: set = set()
    unknown: List[str] = []
    n_tx = n_v = n_ov = 0
    for _unit, _attr, tx in transmitters(flowsheet):
        for name in _TX_LEVEL:
            setattr(tx, name, float(getattr(lv, name)))
        tx.offset_pct = calibration_offset(tx.tag, lv.offset_pct)
        n_tx += 1
        if tx.tag in ov:
            seen.add(tx.tag)
            n_ov += _set(tx, ov[tx.tag], TX_PARAMS, unknown)
    for _unit, _attr, v in valves(flowsheet):
        for name in _VALVE_LEVEL:
            setattr(v, name, float(getattr(lv, name)))
        n_v += 1
        if v.tag in ov:
            seen.add(v.tag)
            n_ov += _set(v, ov[v.tag], VALVE_PARAMS, unknown)
    unknown.extend(sorted(set(ov) - seen))
    return {"level": lv.name, "transmitters": n_tx, "valves": n_v,
            "overridden": n_ov, "unknown": unknown}


def _set(dev, params: Dict[str, float], allowed: Iterable[str], unknown: List[str]) -> int:
    n = 0
    for name, value in params.items():
        if name not in allowed:
            unknown.append(f"{dev.tag}.{name}")
            continue
        try:
            setattr(dev, name, float(value))
            n += 1
        except (TypeError, ValueError):
            unknown.append(f"{dev.tag}.{name}={value!r}")
    return n


def describe(flowsheet) -> List[dict]:
    """One row per instrument: ``unit, tag, kind, attr`` and every parameter.

    This is what ``tools/audit_models.py`` prints and what the settings
    page's per-instrument table shows, so the two cannot disagree about
    what a parameter is called.
    """
    rows = []
    for unit, attr, tx in transmitters(flowsheet):
        row = {"unit": unit, "tag": tx.tag, "kind": "transmitter", "attr": attr,
               "lo": float(tx.lo), "hi": float(tx.hi)}
        for p in TX_PARAMS:
            row[p] = float(getattr(tx, p))
        rows.append(row)
    for unit, attr, v in valves(flowsheet):
        row = {"unit": unit, "tag": v.tag, "kind": "valve", "attr": attr}
        for p in VALVE_PARAMS:
            row[p] = float(getattr(v, p))
        rows.append(row)
    return rows


def level_table() -> List[dict]:
    """The levels as rows, for the help page and the audit."""
    return [asdict(lv) for lv in LEVELS.values()]


def level_fields() -> List[str]:
    return [f.name for f in fields(RealismLevel) if f.name not in ("name", "label", "note")]
