"""Create a review draft mapped to the APVC column; leave published revisions intact."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from azeo_control_trainer.core.hmi.pvms.distillation_template import distillation_document  # noqa: E402
from azeo_control_trainer.core.hmi.pvms.publishing import CURRENT_DISPLAY_SCHEMA_VERSION, DisplayStore  # noqa: E402

DISPLAY_NAME = "U500 - L2 Distillation Column"
PLANT_BINDINGS = {
    "pressure_ai": "PIC-5001/PT-5001",
    "quality_ai": "AIC-5001/AT-5001",
    "temperature_ai": "TIC-5002/TT-5006",
    "pressure": "PIC-5001/PIC-5001",
    "temperature": "TIC-5002/TIC-5002",
    "reflux_flow": "FIC-5001/FIC-5001",
    "feed_transfer": "LIC-4002/LIC-4002",
    "steam_flow": "FIC-5002/FIC-5002",
    "drum_level": "LIC-5001/LIC-5001",
    "bottom_level": "LIC-5002/LIC-5002",
    "boot_level": "LIC-5003/LIC-5003",
    "reflux_pump": "MC-P501A/MC-P501A",
    "bottom_pump": "MC-P502A/MC-P502A",
}


def plant_document():
    return distillation_document(DISPLAY_NAME, bindings=PLANT_BINDINGS,
                                 parent="Overview - L1 Plant")


def install(root):
    store, document = DisplayStore(root), plant_document()
    document.schema_version = CURRENT_DISPLAY_SCHEMA_VERSION
    old = store.load_draft(document.name)
    if old is not None and old.to_dict() != document.to_dict():
        raise FileExistsError(f"Review draft already exists: {document.name}; keep the engineer's edits")
    return store.save_draft(document)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT / "projects/AzeoPlantVirtualController/displays/pvm")
    args = parser.parse_args()
    print(install(args.root))
