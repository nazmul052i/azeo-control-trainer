"""Save and load strategy graphs to/from JSON files."""
from __future__ import annotations
import json
import logging
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

from azeo_control_trainer.config.paths import strategies_dir, data_dir as _data_dir_fn
from ..model.strategy_graph import StrategyGraph
from ..model.wire import Wire
from ..model.block_registry import registry

log = logging.getLogger("strategy.io")

# Base strategy directory (per-simulation subdirs under this)
_BASE_STRATEGY_DIR = strategies_dir()

# Default save directory — set at startup by app._launch_with_plugin()
STRATEGY_DIR = _BASE_STRATEGY_DIR


def get_strategy_dir(sim_id: str) -> Path:
    """Return the strategy directory for a given simulation plugin id."""
    return _BASE_STRATEGY_DIR / sim_id

# Settings file for remembering last strategy, etc.
_SETTINGS_PATH = _data_dir_fn() / "settings.json"

# Template storage directory
TEMPLATE_DIR = _data_dir_fn() / "templates"

# Maximum number of auto-versions to keep per strategy
MAX_VERSIONS = 20


def write_json_transactional(path: Path | str, data: dict) -> Path:
    """Atomically replace *path* with deterministic JSON in the same folder.

    The temporary file is created beside the target so ``os.replace`` cannot
    cross volumes.  A serialization or disk-write failure therefore leaves
    the last valid strategy/checkpoint intact instead of a truncated file.
    """
    path = Path(path)
    from azeo_control_trainer.core.configuration.package_paths import assert_mutable
    assert_mutable(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, indent=2, default=str, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return path


def _load_settings() -> dict:
    """Load app settings from data/settings.json."""
    if _SETTINGS_PATH.exists():
        try:
            with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_settings(settings: dict) -> None:
    """Save app settings to data/settings.json."""
    _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)


def set_last_strategy(path: Path | str | None) -> None:
    """Remember the last loaded/saved strategy path (per-simulation).

    Uses the current STRATEGY_DIR to derive the simulation id so that
    each simulation remembers its own last strategy independently.
    """
    settings = _load_settings()
    sim_key = _current_sim_key()
    if sim_key:
        per_sim = settings.setdefault("last_strategy_per_sim", {})
        per_sim[sim_key] = str(path) if path else None
    else:
        settings["last_strategy"] = str(path) if path else None
    _save_settings(settings)


def get_last_strategy() -> Path | None:
    """Return the path to the last used strategy for the active simulation.

    Only returns a path that exists AND is within the current STRATEGY_DIR
    to prevent cross-plugin leakage (e.g. loading a heater strategy when
    the distillation plugin is active).
    """
    settings = _load_settings()
    sim_key = _current_sim_key()
    raw = None
    if sim_key:
        raw = settings.get("last_strategy_per_sim", {}).get(sim_key)
    if not raw:
        # Fall back to legacy global key
        raw = settings.get("last_strategy")
    if raw:
        p = Path(raw)
        if p.exists():
            # Validate the path is within the current plugin's STRATEGY_DIR
            try:
                p.resolve().relative_to(STRATEGY_DIR.resolve())
                return p
            except ValueError:
                # Path is outside current STRATEGY_DIR (wrong plugin)
                log.debug("Ignoring last strategy %s — outside %s",
                          p, STRATEGY_DIR)
                return None
    return None


def _current_sim_key() -> str | None:
    """Derive the current simulation id from STRATEGY_DIR."""
    # STRATEGY_DIR is e.g. .../strategies/heater — take the last component
    if STRATEGY_DIR and STRATEGY_DIR != _BASE_STRATEGY_DIR:
        return STRATEGY_DIR.name
    return None


def save_strategy(graph: StrategyGraph, path: Path | str | None = None,
                  comments: list[dict] | None = None,
                  save_as: bool = False) -> Path:
    """Save a strategy graph to JSON.

    Args:
        graph: The strategy graph to save
        path: Target file. **Always pass this for an existing module.**
        comments: Optional list of comment annotation dicts (canvas-only data)
        save_as: Explicitly allow inventing ``STRATEGY_DIR/<name>.json`` when
            ``path`` is None. Required, because silently inventing a path is
            how editing ``control/FIC-101.json`` used to write a *different*
            file at the area root that ``_project.json`` never loads — the
            edit appeared to save and never reached the runtime. 44 such
            orphans were committed before this was caught.

    Returns:
        Path to the saved file
    """
    if path is None and not save_as:
        raise ValueError(
            "save_strategy() needs an explicit path: pass path=<file> to write "
            "the module back where it came from, or save_as=True to "
            "deliberately create STRATEGY_DIR/<graph name>.json."
        )
    if path is None:
        STRATEGY_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = graph.name.replace(" ", "_").replace("/", "_")
        path = STRATEGY_DIR / f"{safe_name}.json"
    else:
        path = Path(path)
    from azeo_control_trainer.core.configuration.package_paths import release_root
    if release_root(path):
        raise ValueError("Released configuration is immutable. Use Shared editing or reviewed online tuning upload.")
    path.parent.mkdir(parents=True, exist_ok=True)

    data = graph.to_dict()
    if comments:
        data["comments"] = comments
    elif path.exists():
        # Preserve an existing empty "comments": [] rather than dropping the
        # key, so re-saving an unchanged module is byte-identical in both
        # directions (load_strategy can't tell "absent" from "empty").
        try:
            if "comments" in json.loads(path.read_text(encoding="utf-8")):
                data["comments"] = []
        except Exception:
            pass
    # ensure_ascii=False keeps the file byte-identical when nothing changed —
    # the default escapes every non-ASCII character (a comment's "→" becomes
    # "→"), so merely opening and saving a module produced a whole-file
    # diff and churned the tracked strategy sources.
    #
    # newline="\n" and the trailing newline make the writer DETERMINISTIC
    # (I1): platform text mode wrote CRLF on Windows while git checkouts
    # were LF, and json.dump ends without a final newline while editors
    # add one — either way "byte-identical" depended on which tool last
    # touched the file. The Phase 0 idempotence sweep found the corpus in
    # three different states; canonical is LF with a trailing newline.
    write_json_transactional(path, data)

    log.info("Saved strategy '%s' to %s", graph.name, path)
    set_last_strategy(path)

    # Auto-version: save a timestamped copy
    _save_version(path, data)

    return path


def load_strategy(path: Path | str, *, remember: bool = True
                  ) -> tuple[StrategyGraph, list[dict]]:
    """Load a strategy graph from JSON.

    Args:
        path: Path to the JSON file

    Returns:
        Tuple of (StrategyGraph, list of comment dicts)
    """
    path = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    graph, comments = graph_from_document(data)
    log.info("Loaded strategy '%s' from %s: %d blocks, %d wires, %d comments",
             graph.name, path, len(graph.blocks), len(graph.wires), len(comments))
    if remember:
        set_last_strategy(path)
    from azeo_control_trainer.core.configuration.workspace import draft_root
    graph._configuration_draft = draft_root(path) is not None
    return graph, comments


def graph_from_document(data: dict, *, strict: bool = False
                        ) -> tuple[StrategyGraph, list[dict]]:
    """Use the same loader for files and repository documents, without seat settings.

    Repository imports cannot silently drop an unknown block from their tag index.
    Legacy file callers retain their existing tolerant loading behavior.
    """
    if strict and (not isinstance(data, dict) or not isinstance(data.get("name"), str)):
        raise ValueError("A module needs a name and a JSON object")
    if strict and data.get("module_class") is not None:
        from ..module_classes.instances import ModuleClassLink
        ModuleClassLink.from_dict(data["module_class"])

    graph = StrategyGraph(name=data.get("name", "Untitled"))
    graph.description = data.get("description", "") or ""
    # Carry through anything we don't model so re-saving is lossless.
    _MODELLED = {"name", "description", "blocks", "wires", "comments"}
    graph.extra = {k: v for k, v in data.items() if k not in _MODELLED}

    # Load blocks
    for bdata in data.get("blocks", []):
        block_type = bdata["block_type"]
        block_cls = registry.get(block_type)
        if block_cls is None:
            if strict:
                raise ValueError(f"Unknown block type {block_type!r}")
            log.warning("Unknown block type '%s' — skipping", block_type)
            continue
        block = block_cls.from_dict(bdata)
        if strict and block.id in graph.blocks:
            raise ValueError(f"Duplicate block identity {block.id!r}")
        # Attach graph-aware Special Items without repairing a malformed file.
        # Validation must report a missing parameter declaration; loading must
        # never make an invalid strategy look valid by inventing one.
        graph.add_block(block, declare_parameters=False)

    # Load wires
    for wdata in data.get("wires", []):
        wire = Wire.from_dict(wdata)
        if strict and wire.id in graph.wires:
            raise ValueError(f"Duplicate wire identity {wire.id!r}")
        graph.wires[wire.id] = wire
        # Restore terminal connected flags
        src = graph.blocks.get(wire.src_block_id)
        dst = graph.blocks.get(wire.dst_block_id)
        if src and wire.src_terminal in src.outputs:
            src.outputs[wire.src_terminal].connected = True
        if dst and wire.dst_terminal in dst.inputs:
            dst.inputs[wire.dst_terminal].connected = True

    # Load comments (canvas-only annotations)
    comments = data.get("comments", [])

    return graph, comments


def list_project_modules(sim_id: str) -> list[Path]:
    """Every module listed in ``strategies/<sim_id>/_project.json``.

    Control modules follow the Azeo convention of one control scheme per
    module, so a plugin's control strategy is the whole set, not a single
    file. Returns [] when the plugin has no project file. Paths are returned
    in project order (control modules, then SFC, then equipment) and missing
    files are skipped with a warning.
    """
    import json as _json

    base = get_strategy_dir(sim_id)
    proj = base / "_project.json"
    if not proj.exists():
        return []
    try:
        cfg = _json.loads(proj.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("Unreadable _project.json for '%s': %s", sim_id, exc)
        return []

    out: list[Path] = []
    for area in cfg.get("areas", []):
        for key in ("strategies", "sfc_modules", "equipment_modules"):
            for rel in area.get(key, []):
                path = base / Path(str(rel).replace("\\", "/"))
                if path.exists():
                    out.append(path)
                else:
                    log.warning("Project module missing: %s", path)
    return out


def load_project_strategies(sim_id: str):
    """Load every project module. Returns [(path, graph, comments), ...]."""
    loaded = []
    for path in list_project_modules(sim_id):
        try:
            graph, comments = load_strategy(path)
        except Exception as exc:
            log.warning("Failed to load project module %s: %s", path, exc)
            continue
        loaded.append((path, graph, comments))
    return loaded


def list_strategies() -> list[Path]:
    """List all saved strategy files (top-level only)."""
    if not STRATEGY_DIR.exists():
        return []
    return sorted(STRATEGY_DIR.glob("*.json"))


def list_strategy_folders() -> dict[str, list[Path]]:
    """List strategies organized by subfolder.

    Returns a dict mapping folder name → list of strategy paths.
    Top-level strategies are under the key "" (empty string).
    The "versions" subfolder is excluded.
    """
    if not STRATEGY_DIR.exists():
        return {}
    result: dict[str, list[Path]] = {}
    # Top-level strategies (skip internal _project.json)
    top = sorted(p for p in STRATEGY_DIR.glob("*.json")
                 if not p.name.startswith("_"))
    if top:
        result[""] = top
    # Subdirectories (skip runtime/library roots and equipment documents).
    # Definition libraries embed graph-shaped JSON but are not executable
    # module instances; listing them here made template/bulk pickers offer a
    # class definition as though it were a downloadable Control Module.
    for subdir in sorted(STRATEGY_DIR.iterdir()):
        if (subdir.is_dir()
                and not subdir.name.startswith("_")
                and subdir.name.lower() not in ("versions", "equipment")):
            files = sorted(subdir.glob("*.json"))
            if files:
                result[subdir.name] = files
    return result


# ---------------------------------------------------------------- Versioning

def _save_version(strategy_path: Path, data: dict) -> None:
    """Save a timestamped version copy next to the strategy file.

    Keeps at most MAX_VERSIONS copies, deleting the oldest when exceeded.
    """
    versions_dir = strategy_path.parent / "versions"
    versions_dir.mkdir(parents=True, exist_ok=True)

    stem = strategy_path.stem
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
    version_path = versions_dir / f"{stem}_{timestamp}.json"

    write_json_transactional(version_path, data)

    log.info("Saved version: %s", version_path)

    # Prune old versions
    existing = sorted(
        versions_dir.glob(f"{stem}_*.json"),
        key=lambda p: p.stat().st_mtime,
    )
    while len(existing) > MAX_VERSIONS:
        oldest = existing.pop(0)
        try:
            oldest.unlink()
            log.info("Pruned old version: %s", oldest)
        except OSError:
            pass


def list_versions(strategy_path: Path | str) -> list[Path]:
    """List all saved versions for a strategy file, newest first."""
    strategy_path = Path(strategy_path)
    versions_dir = strategy_path.parent / "versions"
    if not versions_dir.exists():
        return []
    stem = strategy_path.stem
    return sorted(
        versions_dir.glob(f"{stem}_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


# ---------------------------------------------------------------- Templates

def save_template(name: str, blocks: list[dict], wires: list[dict]) -> Path:
    """Save a group of blocks and their wires as a reusable template.

    Args:
        name: Template name
        blocks: List of block dicts (from block.to_dict())
        wires: List of wire dicts (from wire.to_dict()) — only internal wires

    Returns:
        Path to the saved template file
    """
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = name.replace(" ", "_").replace("/", "_")
    path = TEMPLATE_DIR / f"{safe_name}.json"

    data = {
        "name": name,
        "blocks": blocks,
        "wires": wires,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)

    log.info("Saved template '%s' with %d blocks, %d wires to %s",
             name, len(blocks), len(wires), path)
    return path


def load_template(path: Path | str) -> dict:
    """Load a template from JSON.

    Returns:
        Dict with 'name', 'blocks', 'wires' keys
    """
    path = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


def list_templates() -> list[Path]:
    """List all saved template files."""
    if not TEMPLATE_DIR.exists():
        return []
    return sorted(TEMPLATE_DIR.glob("*.json"))


def instantiate_template(data: dict, offset_x: float = 0.0,
                         offset_y: float = 0.0) -> tuple[list, list]:
    """Create new block and wire instances from a template.

    Generates new unique IDs for all blocks and remaps wires accordingly.
    Appends a numeric suffix to instance names to avoid collisions.

    Args:
        data: Template dict with 'blocks' and 'wires'
        offset_x: X offset to apply to block positions
        offset_y: Y offset to apply to block positions

    Returns:
        Tuple of (list of FunctionBlock instances, list of Wire instances)
    """
    from ..model.block_registry import registry as block_registry

    # Map old block IDs to new IDs
    id_map: dict[str, str] = {}
    blocks = []
    suffix = uuid.uuid4().hex[:4]

    for bdata in data.get("blocks", []):
        block_type = bdata["block_type"]
        block_cls = block_registry.get(block_type)
        if block_cls is None:
            log.warning("Template: unknown block type '%s' — skipping", block_type)
            continue

        # Create from serialized data but with fresh ID
        block = block_cls.from_dict(bdata)
        old_id = block.id
        new_id = uuid.uuid4().hex[:12]
        block.id = new_id
        id_map[old_id] = new_id

        # Rename to avoid collision
        block.instance_name = f"{block.instance_name}_{suffix}"

        # Apply position offset
        block.x += offset_x
        block.y += offset_y

        blocks.append(block)

    # Remap wires
    wires = []
    for wdata in data.get("wires", []):
        old_src = wdata["src_block_id"]
        old_dst = wdata["dst_block_id"]
        new_src = id_map.get(old_src)
        new_dst = id_map.get(old_dst)
        if not new_src or not new_dst:
            continue
        wire = Wire(
            src_block_id=new_src,
            src_terminal=wdata["src_terminal"],
            dst_block_id=new_dst,
            dst_terminal=wdata["dst_terminal"],
            is_bkcal=wdata.get("is_bkcal", False),
        )
        wires.append(wire)

    return blocks, wires
