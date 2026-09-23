"""Equipment Module I/O — save, load, list, validate equipment modules.

Equipment modules are organizational containers (Azeo / Honeywell Experion
style) that group related Control Modules and SFC Modules for a physical
piece of equipment.  They live in ``strategies/<plugin_id>/equipment/``.

Equipment modules are NOT executable — they are metadata describing which
strategies belong together and what equipment-level parameters/alarms apply.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from . import strategy_io

log = logging.getLogger("strategy.equipment_io")

# Equipment module JSON schema version
_SCHEMA_VERSION = 1

# Standard equipment types (UI can also accept free-form text)
EQUIPMENT_TYPES = [
    "REACTOR",
    "COLUMN",
    "VESSEL",
    "HEAT_EXCHANGER",
    "COMPRESSOR",
    "PUMP",
    "VALVE_STATION",
    "FURNACE",
    "BOILER",
    "DRYER",
    "MIXER",
    "SEPARATOR",
    "CUSTOM",
]


def _area_dir() -> Path:
    """The area that is open *now*.

    Read through the module, never a from-imported copy. The launcher rebinds
    ``strategy_io.STRATEGY_DIR`` after import, so a copy taken at import time
    stays pointed at the strategies root. Every path in this file went to the
    wrong place as a result: the symptom is quiet — an empty
    ``src/strategies/equipment`` directory, and equipment modules that cannot
    be found in the area that owns them.
    """
    return Path(strategy_io.STRATEGY_DIR)


def _equipment_dir() -> Path:
    """The equipment module folder of the currently open area."""
    return _area_dir() / "equipment"


def save_equipment_module(data: dict, path: Path | str | None = None) -> Path:
    """Save an equipment module JSON file.

    Args:
        data: Equipment module dict (name, description, control_modules, etc.)
        path: Target file path.  If None, derived from data["name"].

    Returns:
        Path to the saved file.
    """
    eq_dir = _equipment_dir()
    eq_dir.mkdir(parents=True, exist_ok=True)

    if path is None:
        safe_name = data["name"].strip().replace(" ", "_").replace("/", "_")
        path = eq_dir / f"{safe_name}.json"
    else:
        path = Path(path)

    data.setdefault("_schema_version", _SCHEMA_VERSION)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)

    log.info("Saved equipment module: %s", path)
    return path


def load_equipment_module(path: Path | str) -> dict:
    """Load an equipment module from a JSON file."""
    path = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


def list_equipment_modules() -> list[Path]:
    """List all equipment module JSON files for the current plugin."""
    eq_dir = _equipment_dir()
    if not eq_dir.exists():
        return []
    return sorted(eq_dir.glob("*.json"))


def validate_equipment_module(data: dict) -> list[str]:
    """Validate an equipment module dict.

    Returns a list of error/warning strings (empty = valid).
    """
    errors = []

    if not data.get("name", "").strip():
        errors.append("Equipment module name is required")

    if not data.get("equipment_type", "").strip():
        errors.append("Equipment type is required")

    # Check that referenced control modules exist on disk
    for cm in data.get("control_modules", []):
        cm_path = _area_dir() / cm
        if not cm_path.exists():
            errors.append(f"Control module not found: {cm}")

    # Check SFC modules
    for sfc in data.get("sfc_modules", []):
        sfc_path = _area_dir() / sfc
        if not sfc_path.exists():
            errors.append(f"SFC module not found: {sfc}")

    return errors


def resolve_child_paths(em_data: dict) -> dict[str, list[tuple[str, Path | None]]]:
    """Resolve relative CM/SFC references to absolute paths.

    Returns:
        {"control_modules": [(filename, resolved_path_or_None), ...],
         "sfc_modules": [(filename, resolved_path_or_None), ...]}
    """
    result = {"control_modules": [], "sfc_modules": []}

    for cm in em_data.get("control_modules", []):
        cm_path = _area_dir() / cm
        result["control_modules"].append(
            (cm, cm_path if cm_path.exists() else None))

    for sfc in em_data.get("sfc_modules", []):
        sfc_path = _area_dir() / sfc
        result["sfc_modules"].append(
            (sfc, sfc_path if sfc_path.exists() else None))

    return result


def new_equipment_module_template(name: str = "New Equipment",
                                  equipment_type: str = "CUSTOM") -> dict:
    """Return a blank equipment module dict."""
    return {
        "name": name,
        "description": "",
        "equipment_type": equipment_type,
        "tag_prefix": "",
        "parameters": {},
        "control_modules": [],
        "sfc_modules": [],
        "alarms": {},
        "states": ["IDLE", "RUNNING", "FAULTED"],
        "initial_state": "IDLE",
    }
