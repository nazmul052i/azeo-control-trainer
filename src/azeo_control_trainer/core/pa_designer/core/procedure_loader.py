from __future__ import annotations

from pathlib import Path
import copy

from .procedure_model import Procedure, Step
from .safe_paths import resolve_within
from .validator import validate_procedure_contract
from .yaml_loader import MAX_YAML_BYTES, load_bounded_yaml_file


def _load_yaml(path: Path) -> dict:
    data = load_bounded_yaml_file(path, label="Procedure file", max_bytes=MAX_YAML_BYTES)
    if not isinstance(data, dict):
        raise ValueError(f"Procedure file is not a YAML object: {path}")
    return data


def _expand_subprocedures(data: dict, base_dir: Path, seen: set[Path], library_root: Path) -> dict:
    """Inline subprocedure steps before Pydantic validation.

    A step with type=subprocedure is replaced by the child procedure's steps.
    The replacement step ids are prefixed with either step.prefix or step.id.
    This keeps the Phase 1C engine simple while supporting reusable procedure blocks.
    """
    data = copy.deepcopy(data)
    expanded_steps: list[dict] = []
    for raw_step in data.get("steps", []):
        if raw_step.get("type") != "subprocedure":
            expanded_steps.append(raw_step)
            continue
        rel = raw_step.get("subprocedure_path")
        if not rel:
            raise ValueError(f"subprocedure step {raw_step.get('id')} missing subprocedure_path")
        child_path = resolve_within(library_root, base_dir.relative_to(library_root) / rel, label="subprocedure_path")
        if child_path in seen:
            raise ValueError(f"Recursive subprocedure include detected: {child_path}")
        child_data = _expand_subprocedures(
            _load_yaml(child_path), child_path.parent, seen | {child_path}, library_root
        )
        prefix = raw_step.get("prefix") or raw_step.get("id")
        for child_step in child_data.get("steps", []):
            child_step = copy.deepcopy(child_step)
            child_step["id"] = f"{prefix}.{child_step['id']}"
            if child_step.get("type") == "complete":
                child_step["subprocedure_return"] = True
            for inherited_key in ("section", "unit_procedure", "equipment", "document_link", "video_link"):
                if raw_step.get(inherited_key) and not child_step.get(inherited_key):
                    child_step[inherited_key] = raw_step[inherited_key]
            # Keep child branch targets inside child namespace.
            for key in ("goto_on_pass", "goto_on_fail", "goto_on_timeout"):
                if child_step.get(key):
                    child_step[key] = f"{prefix}.{child_step[key]}"
            expanded_steps.append(child_step)
        # Merge child tags that are not already declared in parent.
        existing = {t.get("tag") for t in data.get("tags", [])}
        for child_tag in child_data.get("tags", []):
            if child_tag.get("tag") not in existing:
                data.setdefault("tags", []).append(copy.deepcopy(child_tag))
                existing.add(child_tag.get("tag"))
    data["steps"] = expanded_steps
    return data


def load_procedure(path: str | Path, *, validate_contract: bool = True, expand_subprocedures: bool = True) -> Procedure:
    path = Path(path)
    data = _load_yaml(path)
    # Linear procedures inline subprocedure steps at load time. Graph-controlled
    # procedures keep subprocedure steps intact; the engine executes them as
    # namespaced nested flows at runtime.
    if expand_subprocedures and not data.get("flow"):
        root = path.resolve().parent
        data = _expand_subprocedures(data, root, {path.resolve()}, root)
    procedure = Procedure.model_validate(data)
    procedure.set_source_path(str(path.resolve()))
    if validate_contract:
        validate_procedure_contract(procedure).raise_for_errors()
    return procedure
