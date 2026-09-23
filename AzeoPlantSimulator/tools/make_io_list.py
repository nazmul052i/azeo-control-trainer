#!/usr/bin/env python3
"""Generate the issued I/O list from the tag database itself.

The build is the authority: this walks the live flowsheet and writes
``docs/IO_List.md`` and ``docs/IO_List.csv``, so the list can never drift
from the code the way a hand-maintained spreadsheet does.

    python tools/make_io_list.py
"""

from __future__ import annotations

import csv
import logging
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

logging.disable(logging.CRITICAL)

from azeoplant.models.flowsheet import Flowsheet, TagDatabase  # noqa: E402


def main() -> int:
    db = TagDatabase()
    fs = Flowsheet(db)
    tags = sorted(db.all(), key=lambda t: (t.unit, t.kind.name, t.name))
    units = {u.code: u.name for u in fs.units}

    csv_path = ROOT / "docs" / "IO_List.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Tag", "Unit", "Type", "Direction", "EU",
                    "LRV / state 0", "URV / state 1", "Description"])
        for t in tags:
            if t.kind.analogue:
                lo, hi, eu = f"{t.lo:g}", f"{t.hi:g}", t.eu
            else:
                lo, hi, eu = t.state0, t.state1, "-"
            direction = ("DCS to SIM" if t.kind.dcs_writable else "SIM to DCS")
            w.writerow([t.name, t.unit, t.kind.name, direction, eu, lo, hi,
                        t.desc])

    by_kind = Counter(t.kind.name for t in tags)
    by_unit = Counter(t.unit for t in tags)
    md = ROOT / "docs" / "IO_List.md"
    with open(md, "w", encoding="utf-8") as f:
        f.write("# AzeoPlant I/O list\n\n")
        f.write("Generated from the tag database by `tools/make_io_list.py`;"
                " regenerate rather than hand-edit.\n\n")
        f.write(f"**{len(tags)} points**: "
                + ", ".join(f"{k} {n}" for k, n in sorted(by_kind.items()))
                + ".\n\n")
        f.write("| Unit | Points | Service |\n|---|---|---|\n")
        for code in sorted(by_unit):
            f.write(f"| {code} | {by_unit[code]} | {units.get(code, '')} |\n")
        for code in sorted(by_unit):
            f.write(f"\n## {code} - {units.get(code, '')}\n\n")
            f.write("| Tag | Type | Dir | EU | Range / states |"
                    " Description |\n|---|---|---|---|---|---|\n")
            for t in tags:
                if t.unit != code:
                    continue
                if t.kind.analogue:
                    rng, eu = f"{t.lo:g} .. {t.hi:g}", t.eu
                else:
                    rng, eu = f"{t.state0} / {t.state1}", "-"
                d = "out" if t.kind.dcs_writable else "in"
                f.write(f"| {t.name} | {t.kind.name} | {d} | {eu} |"
                        f" {rng} | {t.desc} |\n")
    print(f"{len(tags)} points -> {md.name}, {csv_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
