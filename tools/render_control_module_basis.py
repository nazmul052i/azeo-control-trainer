"""Render the public APVC control-module schedule from its checked-in JSON."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASIS = ROOT / "projects/AzeoPlantVirtualController/engineering/control_modules.json"
SCHEDULE = BASIS.with_name("Control_Modules.md")


def render(document: dict) -> str:
    rows = [
        "# APVC control modules",
        "",
        "This schedule is generated from `control_modules.json`. Edit and review the",
        "machine-readable basis, then run `tools/render_control_module_basis.py`.",
        "Gains are percent output per percent PV span; reset values are seconds.",
        "",
        f"**{len(document['modules'])} regulatory modules.**",
        "",
        "| Module | PV | Output | Acting | Mode | Master | Gain | Reset | Role |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, module in sorted(document["modules"].items()):
        cells = (
            name,
            module["pv"],
            module["output"],
            module["action"],
            module["normal_mode"],
            module["master"] or "-",
            f"{module['gain']:.2f}",
            f"{module['reset']:g}",
            module["role"],
        )
        rows.append("| " + " | ".join(str(cell) for cell in cells) + " |")
    rows.append("")
    return "\n".join(rows)


def main() -> int:
    document = json.loads(BASIS.read_text(encoding="utf-8"))
    schedule = render(document)
    SCHEDULE.write_text(schedule, encoding="utf-8", newline="\n")
    document.pop("source", None)
    document.pop("source_sha256", None)
    document["companion"] = "engineering/Control_Modules.md"
    document["companion_sha256"] = hashlib.sha256(
        schedule.encode("utf-8")
    ).hexdigest()
    BASIS.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Rendered {len(document['modules'])} modules to {SCHEDULE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
