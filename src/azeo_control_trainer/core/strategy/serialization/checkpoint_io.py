"""Diagnostic snapshots with an explicit, narrow restore contract.

The strategy, readbacks, outputs, and scan statistics are captured for audit
and troubleshooting. They are not a restorable process image. Only paths
listed in ``restore_writes`` are replayed: currently PID setpoint, mode,
manual output, tuning and configured limits, plus block-provided extension
hooks. Output terminals and field measurements are deliberately read-only.

Checkpoints are saved to <STRATEGY_DIR>/checkpoints/ as JSON files.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("strategy.checkpoint")

# Maximum automatic checkpoints to keep (named checkpoints are never pruned)
MAX_AUTO_CHECKPOINTS = 50
CHECKPOINT_SCHEMA_VERSION = 2


def _pid_restore_path(instance_name: str, parameter: str) -> str | None:
    if parameter in ("SP", "ManOut"):
        return f"ctrl.{instance_name}.wb.{parameter}"
    if parameter == "MODE":
        return f"ctrl.{instance_name}.wb.Mode"
    if parameter in (
        "GAIN", "RESET", "RATE", "alpha", "beta", "gamma", "bias",
        "SP_Min", "SP_Max", "OP_Min", "OP_Max",
    ):
        return f"ctrl.{instance_name}.wb.{parameter}"
    return None


def _checkpoint_dir() -> Path:
    """Return the checkpoint directory for the current simulation."""
    from .strategy_io import STRATEGY_DIR
    d = STRATEGY_DIR / "checkpoints"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_checkpoint(
    name: str,
    canvases: list,
    store,
    auto: bool = False,
) -> Path:
    """Save a diagnostic snapshot and its explicitly restorable writes.

    Args:
        name: Human-readable checkpoint name
        canvases: List of (tab_name, StrategyCanvas) tuples
        store: SharedDataStore for reading runtime values
        auto: If True, this is an automatic checkpoint (subject to pruning)

    Returns:
        Path to the saved checkpoint file
    """
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
    safe_name = name.replace(" ", "_").replace("/", "_")

    data = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "restore_contract": (
            "Only restore_writes are replayed; strategy, outputs and "
            "readbacks are diagnostic-only snapshots."),
        "name": name,
        "timestamp": timestamp,
        "created_at": time.time(),
        "auto": auto,
        "modules": [],
    }

    for tab_name, canvas in canvases:
        if not canvas.runtime or not canvas.runtime.compiled:
            continue

        graph = canvas.runtime.compiled.graph
        module_data = {
            "tab_name": tab_name,
            "file_path": canvas.file_path,
            "online": canvas.runtime.is_online,
            "strategy": graph.to_dict(),
            "runtime_status": canvas.runtime.get_status(),
            "parameters": {},
            "restore_writes": [],
        }

        # Capture all live block parameters from store
        if store:
            for block_id, block in graph.blocks.items():
                iname = block.instance_name
                bt = block.block_type
                params = {}

                if bt == "PID":
                    for key in ("PV", "SP", "OUT", "MODE", "GAIN", "RESET",
                                "RATE", "ManOut", "ARWStatus",
                                "SP_Min", "SP_Max", "OP_Min", "OP_Max",
                                "alpha", "beta", "gamma", "bias",
                                "_integral", "P_term", "I_term", "D_term",
                                "BKCAL_OUT", "AWS"):
                        val = store.get(f"ctrl.{iname}.{key}")
                        if val is not None:
                            params[key] = _serialize_val(val)

                elif bt in ("AI", "AO"):
                    tag = block.config.params.get("tag", "")
                    if tag:
                        val = store.get(tag)
                        if val is not None:
                            params["value"] = _serialize_val(val)
                    for key in ("SP", "mode", "status"):
                        val = store.get(f"io.{iname}.{key}")
                        if val is not None:
                            params[key] = _serialize_val(val)

                # Block output values
                for tname, term in block.outputs.items():
                    params[f"OUT.{tname}"] = _serialize_val(term.value)

                module_data["parameters"][iname] = params

                hook = getattr(block, "checkpoint_restore_writes", None)
                if callable(hook):
                    writes = hook(store) or []
                    for write in writes:
                        if (isinstance(write, dict) and write.get("path")
                                and "value" in write):
                            module_data["restore_writes"].append({
                                "path": str(write["path"]),
                                "value": _serialize_val(write["value"]),
                                "block": iname,
                                "parameter": str(
                                    write.get("parameter", "extension")),
                            })
                elif bt == "PID":
                    for key, value in params.items():
                        path = _pid_restore_path(iname, key)
                        if path is not None:
                            module_data["restore_writes"].append({
                                "path": path,
                                "value": value,
                                "block": iname,
                                "parameter": key,
                            })

        data["modules"].append(module_data)

    # Save to disk
    filename = f"{'auto_' if auto else ''}{safe_name}_{timestamp}.json"
    path = _checkpoint_dir() / filename
    from .strategy_io import write_json_transactional
    write_json_transactional(path, data)

    log.info("Checkpoint saved: %s (%d modules)", name, len(data["modules"]))

    # Prune old auto-checkpoints
    if auto:
        _prune_auto_checkpoints()

    return path


def load_checkpoint(path: Path | str) -> dict:
    """Load a checkpoint from disk.

    Returns:
        Checkpoint data dict with 'name', 'timestamp', 'modules', etc.
    """
    path = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("modules"), list):
        raise ValueError(f"Invalid checkpoint document: {path}")
    version = int(data.get("schema_version", 1))
    if version < 1 or version > CHECKPOINT_SCHEMA_VERSION:
        raise ValueError(f"Unsupported checkpoint schema version: {version}")
    return data


def list_checkpoints() -> list[dict]:
    """List all saved checkpoints, newest first.

    Returns:
        List of dicts with 'path', 'name', 'timestamp', 'auto', 'module_count'
    """
    cdir = _checkpoint_dir()
    results = []
    for p in sorted(cdir.glob("*.json"), key=lambda x: x.stat().st_mtime,
                    reverse=True):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            results.append({
                "path": p,
                "name": data.get("name", p.stem),
                "timestamp": data.get("timestamp", ""),
                "auto": data.get("auto", False),
                "module_count": len(data.get("modules", [])),
                "created_at": data.get("created_at", 0),
            })
        except (json.JSONDecodeError, OSError):
            continue
    return results


def delete_checkpoint(path: Path | str) -> bool:
    """Delete a checkpoint file."""
    path = Path(path)
    try:
        path.unlink()
        log.info("Checkpoint deleted: %s", path)
        return True
    except OSError as e:
        log.error("Failed to delete checkpoint: %s", e)
        return False


def restore_checkpoint(checkpoint_data: dict, store) -> dict:
    """Replay only the checkpoint's declared controller write paths.

    Version-1 files are supported through the former PID-only mapping. New
    files never infer a writable path from captured readbacks, which prevents
    a future diagnostic field from accidentally becoming a control write.

    Args:
        checkpoint_data: Loaded checkpoint dict
        store: SharedDataStore to write into

    Returns:
        Dict mapping module name -> count of restored parameters
    """
    results = {}
    schema_version = int(checkpoint_data.get("schema_version", 1))
    for module in checkpoint_data.get("modules", []):
        tab_name = module.get("tab_name", "unknown")
        count = 0
        if schema_version >= 2:
            for write in module.get("restore_writes", []):
                if not isinstance(write, dict):
                    continue
                path = write.get("path")
                if not isinstance(path, str) or not path or "value" not in write:
                    continue
                store.queue_write(path, write["value"])
                count += 1
            results[tab_name] = count
            continue
        for iname, params in module.get("parameters", {}).items():
            for key, val in params.items():
                if key.startswith("OUT."):
                    continue  # Don't restore output terminals directly
                # Write tuning and SP parameters back via store write-back
                if key in ("SP", "ManOut"):
                    store.queue_write(f"ctrl.{iname}.wb.{key}", val)
                    count += 1
                elif key in ("GAIN", "RESET", "RATE", "alpha", "beta",
                             "gamma", "bias", "SP_Min", "SP_Max",
                             "OP_Min", "OP_Max"):
                    store.queue_write(f"ctrl.{iname}.wb.{key}", val)
                    count += 1
                elif key == "MODE":
                    store.queue_write(f"ctrl.{iname}.wb.Mode", val)
                    count += 1
        results[tab_name] = count
    return results


def _prune_auto_checkpoints():
    """Remove old auto-checkpoints beyond MAX_AUTO_CHECKPOINTS."""
    cdir = _checkpoint_dir()
    auto_files = sorted(
        [p for p in cdir.glob("auto_*.json")],
        key=lambda x: x.stat().st_mtime,
    )
    while len(auto_files) > MAX_AUTO_CHECKPOINTS:
        oldest = auto_files.pop(0)
        try:
            oldest.unlink()
            log.info("Pruned auto-checkpoint: %s", oldest)
        except OSError:
            pass


def _serialize_val(val):
    """Serialize a value for JSON storage."""
    if isinstance(val, (int, float, str, bool, type(None))):
        return val
    if isinstance(val, dict):
        return {k: _serialize_val(v) for k, v in val.items()}
    if isinstance(val, (list, tuple)):
        return [_serialize_val(v) for v in val]
    # Enum or other
    return str(val)
