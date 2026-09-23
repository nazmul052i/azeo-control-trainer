# -*- coding: utf-8 -*-
"""The canonical signal catalogue: generated from the build, never edited.

The record schema is the one the azeo_control_trainer's catalogue importer
consumes (``fieldio/opcua_catalog.py``), verified against the catalogue an
earlier export of this simulator left in that repo. The node identifiers
describe THIS server truthfully: the flat ``ns=2;s=<tag>`` id every tag
answers to, plus the hierarchical ``PLANT_SIM.<unit>.<kind>.<tag>`` alias
the address space is organised under.

Validation cross-checks the design workbook so the catalogue can never
drift from either the code (it is generated from the live flowsheet) or
the schedule (every spec point must be present).
"""

from __future__ import annotations

import uuid
from typing import Dict, List, Tuple

from ..core.tags import TagDatabase
from ..core import units as _units
from .ownership import owner_of

_NAMESPACE = uuid.UUID("2f1c9a34-7f10-4bd0-9f37-aeb1d1f2c001")

#: Controller simulation-path parameter per I/O kind. FIELD_VAL_PCT is confirmed
#: for analogue inputs by the design document; the rest follow the DST
#: reference column of the workbook where it names one.
_PARAM = {"AI": "FIELD_VAL_PCT", "AO": "FIELD_VAL_PCT",
          "DI": "FIELD_VAL_D", "DO": "FIELD_VAL_D"}


def build_catalog(db: TagDatabase, unit_names: Dict[str, str],
                  stale_timeout_s: float = 2.0) -> List[dict]:
    records = []
    for t in sorted(db.all(), key=lambda x: (x.unit, x.kind.name, x.name)):
        kind = t.kind.name
        rec = {
            "signal_id": str(uuid.uuid5(_NAMESPACE, t.name)),
            "name": t.name,
            "node_id": f"ns=2;s={t.name}",
            "kind": kind,
            "direction": ("DCS_TO_SIM" if t.kind.dcs_writable
                          else "SIM_TO_DCS"),
            "owner": owner_of(t.kind).value,
            "data_type": "Double" if t.kind.analogue else "Boolean",
            "eu": _units.eu(t) if t.kind.analogue else "",
            "description": t.desc,
            "plant_unit": t.unit,
            "unit_description": unit_names.get(t.unit, ""),
            "stale_timeout_sec": stale_timeout_s,
            "opcua": {
                "node_id": f"ns=2;s={t.name}",
                "browse_path": f"PLANT_SIM/{t.unit}/{kind}/{t.name}",
                "aliases": [f"ns=2;s=PLANT_SIM.{t.unit}.{kind}.{t.name}"],
            },
            "controller_mapping": {
                "dst": t.name,
                "parameter": _PARAM[kind],
                "reference": f"*{t.name}/{_PARAM[kind]}",
                "controller": None,
                "cioc": None,
                "charm": None,
            },
        }
        if t.kind.analogue:
            rec["lo"] = _units.lo(t)
            rec["hi"] = _units.hi(t)
        else:
            rec["state0"] = t.state0
            rec["state1"] = t.state1
        records.append(rec)
    return records


def validate_catalog(records: List[dict],
                     spec_tags: Dict[str, dict] | None = None
                     ) -> Tuple[List[str], List[str]]:
    """Returns (errors, notes). Errors fail the export; notes inform."""
    errors: List[str] = []
    notes: List[str] = []
    names, nodes = set(), set()
    for r in records:
        n = r["name"]
        if n in names:
            errors.append(f"duplicate tag: {n}")
        names.add(n)
        if r["node_id"] in nodes:
            errors.append(f"duplicate node id: {r['node_id']}")
        nodes.add(r["node_id"])
        if r["kind"] in ("AI", "AO"):
            lo, hi = r.get("lo"), r.get("hi")
            if lo is None or hi is None or not (lo < hi):
                errors.append(f"{n}: bad range {lo!r}..{hi!r}")
            if not r["eu"]:
                notes.append(f"{n}: analogue with no engineering unit")
        want = "DCS_TO_SIM" if r["kind"] in ("AO", "DO") else "SIM_TO_DCS"
        if r["direction"] != want:
            errors.append(f"{n}: direction {r['direction']} for {r['kind']}")
        want_owner = "DCS" if r["kind"] in ("AO", "DO") else "SIMULATOR"
        if r["owner"] != want_owner:
            errors.append(f"{n}: owner {r['owner']} for {r['kind']}")
    if spec_tags is not None:
        missing = sorted(set(spec_tags) - names)
        for m in missing:
            errors.append(f"design schedule point missing from build: {m}")
        extras = sorted(names - set(spec_tags))
        if extras:
            notes.append(f"{len(extras)} points beyond the schedule "
                         f"(machine monitoring etc.): "
                         + ", ".join(extras[:10])
                         + ("..." if len(extras) > 10 else ""))
    return errors, notes
