#!/usr/bin/env python3
"""Export the canonical OPC UA signal catalogue and validate it.

Writes ``data/opcua_tag_catalog.json`` (the exact schema the
azeo_control_trainer catalogue importer consumes) and
``data/io_validation_report.md``, generated from the live flowsheet and
cross-checked against the design workbook. Regenerate after any tag
change; never hand-edit.

    python tools/export_opcua_catalog.py
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

logging.disable(logging.CRITICAL)

from azeoplant.models.flowsheet import Flowsheet, TagDatabase  # noqa: E402
from azeoplant.io.catalog import build_catalog, validate_catalog  # noqa: E402

WORKBOOK = ROOT / "docs" / "AzeoPlantSimulator_OPCUA_TagList.xlsx"


def load_spec() -> dict | None:
    if not WORKBOOK.exists():
        return None
    try:
        import openpyxl
    except ImportError:
        return None
    wb = openpyxl.load_workbook(WORKBOOK, read_only=True, data_only=True)
    ws = wb["IO_List"]
    rows = list(ws.iter_rows(min_row=3, values_only=True))
    return {str(r[0]).strip(): {"unit": r[1], "type": r[4]}
            for r in rows if r and r[0]}


def main() -> int:
    db = TagDatabase()
    fs = Flowsheet(db)
    units = {u.code: u.name for u in fs.units}
    records = build_catalog(db, units)
    spec = load_spec()
    errors, notes = validate_catalog(records, spec)

    out_dir = ROOT / "data"
    out_dir.mkdir(exist_ok=True)
    payload = {"source": "AzeoPlantSimulator flowsheet",
               "endpoint": "opc.tcp://0.0.0.0:48420/plant_sim/",
               "namespace": 2,
               "unit_set": "si",
               "tags": records}
    (out_dir / "opcua_tag_catalog.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8")

    with open(out_dir / "io_validation_report.md", "w",
              encoding="utf-8") as f:
        f.write("# I/O catalogue validation\n\n")
        f.write(f"{len(records)} signals exported"
                + (f"; checked against {len(spec)} workbook points"
                   if spec else "; workbook not available") + ".\n\n")
        f.write(f"**Errors: {len(errors)}**\n\n")
        for e in errors:
            f.write(f"- {e}\n")
        f.write(f"\n**Notes: {len(notes)}**\n\n")
        for n in notes:
            f.write(f"- {n}\n")

    kinds = {}
    for r in records:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    print(f"{len(records)} signals -> data/opcua_tag_catalog.json  "
          + "  ".join(f"{k} {v}" for k, v in sorted(kinds.items())))
    print(f"errors: {len(errors)}   notes: {len(notes)}"
          f"   report: data/io_validation_report.md")
    for e in errors[:10]:
        print("  ERROR", e)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
