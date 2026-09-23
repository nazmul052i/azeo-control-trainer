"""Extract a linear selection into a pinned reusable procedure revision."""
from __future__ import annotations

import ast
from copy import deepcopy
import re

from azeo_control_trainer.core.procedures.authoring import ProcedureDraft
from azeo_control_trainer.core.procedures.library import block_for_type
from azeo_control_trainer.core.procedures.model import AdvisoryStep
from azeo_control_trainer.core.pa_designer.core.validator import referenced_tags_in_step


def _names(expression: str) -> set[str]:
    try:
        return {node.id for node in ast.walk(ast.parse(expression, mode="eval")) if isinstance(node, ast.Name)}
    except SyntaxError:
        return set()


def extract_selection(draft: ProcedureDraft, selected_ids: set[str], project, *, procedure_id: str, name: str,
                      version="1.0.0", author="", revision_note=""):
    if draft.data.get("flow"):
        raise ValueError("Extract to reusable procedure currently requires a linear workflow")
    if not selected_ids:
        raise ValueError("Select at least one procedure block")
    steps = draft.data.get("steps", [])
    indexes = [index for index, step in enumerate(steps) if step.get("id") in selected_ids]
    if len(indexes) != len(selected_ids) or indexes != list(range(min(indexes), max(indexes) + 1)):
        raise ValueError("Select one contiguous sequence of blocks")
    chosen = deepcopy(steps[indexes[0]:indexes[-1] + 1])
    if any(step.get("type") == "complete" for step in chosen):
        raise ValueError("Keep the parent Complete block outside the extracted selection")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{1,79}", procedure_id.strip()):
        raise ValueError("Reusable procedure ID must start with a letter and use letters, numbers, underscore or hyphen")
    declared_tags = {row["tag"]: row for row in draft.data.get("tags", [])}
    used_tags = set()
    declared_vars = {row["name"]: row for row in draft.data.get("variables", [])}
    used_vars = set()
    for raw in chosen:
        step = AdvisoryStep.model_validate(raw)
        used_tags.update(referenced_tags_in_step(step))
        if raw.get("feedback_tag"):
            used_tags.add(raw["feedback_tag"])
        for field in ("condition", "expression", "skip_if"):
            if raw.get(field):
                used_vars.update(_names(raw[field]))
        if raw.get("variable"):
            used_vars.add(raw["variable"])
        used_vars.update(row.get("variable", "") for row in raw.get("calculation_rows", []))
        used_vars.update(raw.get("result_variables", {}).values())
        used_vars.update(raw.get("adapter_results", {}).values())
        for value in raw.get("adapter_inputs", {}).values():
            if isinstance(value, str) and value.startswith("="):
                used_vars.update(_names(value[1:]))
        for value in raw.get("parameters", {}).values():
            if isinstance(value, dict) and isinstance(value.get("expression"), str):
                used_vars.update(_names(value["expression"]))
    used_vars &= declared_vars.keys()
    missing = used_tags - declared_tags.keys()
    if missing:
        raise ValueError("Extracted blocks use undeclared tags: " + ", ".join(sorted(missing)))
    complete_id, suffix = "complete", 2
    chosen_ids = {row["id"] for row in chosen}
    while complete_id in chosen_ids:
        complete_id, suffix = f"complete_{suffix}", suffix + 1
    child_steps = chosen + [dict(block_for_type("complete").instantiate(complete_id), description="Reusable procedure complete.")]
    child = ProcedureDraft({
        "procedure_id": procedure_id.strip(), "name": name.strip() or procedure_id.strip(), "mode": "advisory",
        "metadata": {"version": version.strip() or "1.0.0", "author": author.strip(),
                     "revision_note": revision_note.strip(), "safety_class": "advisory"},
        "tags": [deepcopy(declared_tags[tag]) for tag in sorted(used_tags)],
        "variables": [deepcopy(declared_vars[var]) for var in sorted(used_vars)],
        "steps": child_steps,
    }, {tag: draft.bindings[tag] for tag in used_tags if tag in draft.bindings})
    path = child.save_revision(project)
    library = child.library
    draft.library = library
    local_vars = sorted(var for var in used_vars if not declared_vars[var].get("tag_path"))
    call_id = procedure_id.strip()
    suffix = 2
    existing = {step["id"] for step in steps}
    while call_id in existing - selected_ids:
        call_id, suffix = f"{procedure_id}_{suffix}", suffix + 1
    call = block_for_type("subprocedure").instantiate(call_id)
    call.update(description=f"Run reusable procedure: {child.data['name']}",
                subprocedure_path=path.relative_to(library).as_posix(),
                tag_aliases={tag: tag for tag in sorted(used_tags)},
                parameters={var: {"expression": var} for var in local_vars},
                result_variables={var: var for var in local_vars})
    updated = deepcopy(steps)
    updated[indexes[0]:indexes[-1] + 1] = [call]
    return child, path, updated, call_id
