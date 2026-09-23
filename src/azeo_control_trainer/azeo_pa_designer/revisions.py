"""Semantic procedure revision comparison and impact analysis."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from azeo_control_trainer.core.procedures.authoring import ProcedureDraft


@dataclass(frozen=True)
class RevisionChange:
    area: str
    identity: str
    change: str
    before: str = ""
    after: str = ""
    impact: str = ""


def _index(rows, key):
    return {str(row.get(key, "")): row for row in rows}


def _repr(value):
    text = repr(value)
    return text if len(text) <= 240 else text[:237] + "..."


def compare_documents(before: dict, before_bindings: dict, after: dict, after_bindings: dict) -> list[RevisionChange]:
    changes: list[RevisionChange] = []
    for key in ("procedure_id", "name", "unit", "description"):
        if before.get(key) != after.get(key):
            changes.append(RevisionChange("Procedure", key, "Modified", _repr(before.get(key)), _repr(after.get(key))))
    for key in ("version", "owner", "author", "revision_note", "safety_class"):
        left, right = before.get("metadata", {}).get(key), after.get("metadata", {}).get(key)
        if left != right:
            changes.append(RevisionChange("Metadata", key, "Modified", _repr(left), _repr(right)))
    for key, impact in (
        ("trigger", "Procedure eligibility changed"),
        ("recovery", "Failure recovery policy changed"),
        ("references", "Controlled source material changed"),
        ("connectivity", "Connection/readiness policy changed"),
    ):
        if before.get(key) != after.get(key):
            changes.append(RevisionChange("Procedure", key, "Modified", _repr(before.get(key)), _repr(after.get(key)), impact))
    for area, key, identity in (("Steps", "steps", "id"), ("Tags", "tags", "tag"), ("Memory", "variables", "name")):
        left, right = _index(before.get(key, []), identity), _index(after.get(key, []), identity)
        for name in sorted(left.keys() | right.keys()):
            if name not in left:
                changes.append(RevisionChange(area, name, "Added", "", _repr(right[name]), "New execution input or behavior"))
            elif name not in right:
                changes.append(RevisionChange(area, name, "Removed", _repr(left[name]), "", "Existing callers or expressions may be affected"))
            elif left[name] != right[name]:
                changes.append(RevisionChange(area, name, "Modified", _repr(left[name]), _repr(right[name]), "Execution semantics changed" if area == "Steps" else "Dependent steps should be reviewed"))
    for name in sorted(before_bindings.keys() | after_bindings.keys()):
        left, right = before_bindings.get(name), after_bindings.get(name)
        if left != right:
            change = "Added" if left is None else "Removed" if right is None else "Modified"
            changes.append(RevisionChange("Mappings", name, change, _repr(left), _repr(right), "Live project parameter resolution changed"))
    left_flow, right_flow = before.get("flow"), after.get("flow")
    if left_flow != right_flow:
        changes.append(RevisionChange("Workflow", "advanced flow", "Modified", _repr(left_flow), _repr(right_flow), "Execution paths and reachability must be revalidated"))
    left_annotations = before.get("metadata", {}).get("annotations", [])
    right_annotations = after.get("metadata", {}).get("annotations", [])
    if left_annotations != right_annotations:
        changes.append(RevisionChange("Annotations", "engineering context", "Modified",
                                      _repr(left_annotations), _repr(right_annotations),
                                      "Documentation changed; executable behavior is unaffected"))
    return changes


def compare_revision(path: Path, current_draft: ProcedureDraft) -> list[RevisionChange]:
    baseline = ProcedureDraft.load(Path(path), current_draft.library or Path(path).parents[1])
    return compare_documents(baseline.data, baseline.bindings, current_draft.data, current_draft.bindings)
