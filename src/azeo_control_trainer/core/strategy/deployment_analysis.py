"""Controller deployment diff and dependency-impact analysis.

The designer needs to answer two separate questions before a download:

* what changed since the last successful download; and
* what else can observe that change.

This module is deliberately Qt-free.  It compares serialized module documents,
which makes the result deterministic and lets the same analysis serve a dialog,
an automated review, or a future release gate.  Canvas-only geometry is reported
but never exaggerated into a controller restart.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


STRUCTURE = "structure"
PARAMETER = "parameter"
RUNTIME = "runtime"
LAYOUT = "layout"
DOCUMENTATION = "documentation"

_CONTROLLER_SCOPES = frozenset({STRUCTURE, PARAMETER, RUNTIME})
_LAYOUT_KEYS = frozenset({"x", "y", "ui_width", "ui_height", "hidden_terminals"})
_RUNTIME_KEYS = frozenset({"bypassed", "scan_rate", "forced_terminals"})
_COMPOSITE_STRUCTURE_KEYS = frozenset({"inner_graph"})
_COMPOSITE_PARAMETER_KEYS = frozenset({
    "definition_id", "definition_revision", "definition_digest",
    "definition_snapshot", "public_parameter_overrides",
})
_OUTPUT_BLOCKS = frozenset({"AO", "DO"})


@dataclass(frozen=True)
class DeploymentChange:
    """One human-readable difference between baseline and candidate."""

    scope: str
    action: str
    object_id: str
    label: str
    detail: str
    before: Any = None
    after: Any = None

    @property
    def controller_effect(self) -> bool:
        return self.scope in _CONTROLLER_SCOPES


@dataclass(frozen=True)
class ImpactedObject:
    """A block or module that can observe a changed value."""

    module: str
    object_id: str
    label: str
    distance: int
    reason: str
    external: bool = False


@dataclass
class DeploymentImpactReport:
    module: str
    changes: list[DeploymentChange] = field(default_factory=list)
    impacts: list[ImpactedObject] = field(default_factory=list)
    baseline_available: bool = True

    @property
    def controller_changes(self) -> list[DeploymentChange]:
        return [change for change in self.changes if change.controller_effect]

    @property
    def layout_changes(self) -> list[DeploymentChange]:
        return [change for change in self.changes if change.scope == LAYOUT]

    @property
    def restart_required(self) -> bool:
        return any(change.scope == STRUCTURE for change in self.changes)

    @property
    def risk(self) -> str:
        if self.restart_required:
            return "HIGH"
        if self.controller_changes:
            return "MEDIUM"
        if self.changes:
            return "LOW"
        return "NONE"

    def summary(self) -> str:
        if not self.changes:
            return "No differences from the downloaded baseline"
        controlled = len(self.controller_changes)
        layout = len(self.layout_changes)
        affected = len(self.impacts)
        suffix = "; restart required" if self.restart_required else ""
        return (
            f"{controlled} controller change(s), {layout} layout change(s), "
            f"{affected} affected object(s){suffix}"
        )


def snapshot_document(graph_or_document: Any) -> dict:
    """Return a detached serialized deployment baseline."""
    import copy

    if isinstance(graph_or_document, Mapping):
        document = dict(graph_or_document)
    else:
        document = graph_or_document.to_dict()
    return copy.deepcopy(document)


def analyze_deployment(
    baseline: Mapping[str, Any] | None,
    candidate: Mapping[str, Any] | Any,
    *,
    project_documents: Iterable[Mapping[str, Any] | Any] = (),
) -> DeploymentImpactReport:
    """Compare one candidate module with its last downloaded baseline.

    ``project_documents`` supplies other modules for field-tag impact analysis.
    The changed module may be present in the iterable; it is ignored by name.
    """
    current = snapshot_document(candidate)
    has_baseline = baseline is not None
    old = snapshot_document(baseline) if baseline is not None else {
        "name": current.get("name", "Untitled"), "blocks": [], "wires": []
    }
    module = str(current.get("name") or old.get("name") or "Untitled")
    report = DeploymentImpactReport(
        module=module, baseline_available=has_baseline)

    _compare_module_fields(old, current, report.changes)
    changed_blocks = _compare_blocks(old, current, report.changes)
    wire_blocks = _compare_wires(old, current, report.changes)
    changed_blocks.update(wire_blocks)

    controller_block_changes = {
        change.object_id for change in report.changes
        if change.controller_effect and change.object_id not in {"<module>", "<wire>"}
    }
    controller_block_changes.update(changed_blocks)
    report.impacts.extend(_downstream_impacts(
        module, old, current, controller_block_changes))
    report.impacts.extend(_cross_module_impacts(
        module, old, current, controller_block_changes, project_documents))
    report.impacts = _deduplicate_impacts(report.impacts)
    return report


def _compare_module_fields(old: dict, current: dict,
                           changes: list[DeploymentChange]) -> None:
    old_scan = old.get("scan_ms", 500)
    new_scan = current.get("scan_ms", 500)
    if old_scan != new_scan:
        changes.append(DeploymentChange(
            STRUCTURE, "changed", "<module>", "Module scan rate",
            "Scan period changed", old_scan, new_scan))

    for key, scope, label in (
        ("parameters", PARAMETER, "Module parameters"),
        ("named_sets", PARAMETER, "Named sets"),
        ("description", DOCUMENTATION, "Module description"),
        ("comments", DOCUMENTATION, "Module comments"),
    ):
        default = "" if key == "description" else [] if key == "comments" else {}
        before = old.get(key, default)
        after = current.get(key, default)
        if before != after:
            changes.append(DeploymentChange(
                scope, "changed", "<module>", label,
                f"{label} changed", before, after))


def _blocks(document: Mapping[str, Any]) -> dict[str, dict]:
    return {
        str(block.get("id")): dict(block)
        for block in document.get("blocks", [])
        if isinstance(block, Mapping) and block.get("id") is not None
    }


def _block_label(block: Mapping[str, Any] | None, fallback: str) -> str:
    if not block:
        return fallback
    name = str(block.get("instance_name") or fallback)
    btype = str(block.get("block_type") or "?")
    return f"{name} ({btype})"


def _compare_blocks(old: dict, current: dict,
                    changes: list[DeploymentChange]) -> set[str]:
    before = _blocks(old)
    after = _blocks(current)
    changed: set[str] = set()

    for block_id in sorted(after.keys() - before.keys()):
        block = after[block_id]
        changed.add(block_id)
        changes.append(DeploymentChange(
            STRUCTURE, "added", block_id, _block_label(block, block_id),
            "Block added", None, block))
    for block_id in sorted(before.keys() - after.keys()):
        block = before[block_id]
        changed.add(block_id)
        changes.append(DeploymentChange(
            STRUCTURE, "removed", block_id, _block_label(block, block_id),
            "Block removed", block, None))

    for block_id in sorted(before.keys() & after.keys()):
        old_block, new_block = before[block_id], after[block_id]
        label = _block_label(new_block, block_id)
        for key in ("block_type", "instance_name"):
            if old_block.get(key) != new_block.get(key):
                changed.add(block_id)
                changes.append(DeploymentChange(
                    STRUCTURE, "changed", block_id, label,
                    f"{key.replace('_', ' ').title()} changed",
                    old_block.get(key), new_block.get(key)))

        old_config = old_block.get("config") or {}
        new_config = new_block.get("config") or {}
        for key in sorted(set(old_config) | set(new_config)):
            if old_config.get(key) != new_config.get(key):
                changed.add(block_id)
                changes.append(DeploymentChange(
                    PARAMETER, "changed", block_id, label,
                    f"Configuration {key}", old_config.get(key),
                    new_config.get(key)))

        for key in sorted(_RUNTIME_KEYS):
            if old_block.get(key) != new_block.get(key):
                changed.add(block_id)
                changes.append(DeploymentChange(
                    RUNTIME, "changed", block_id, label,
                    f"Runtime setting {key.replace('_', ' ')}",
                    old_block.get(key), new_block.get(key)))

        for key in sorted(_LAYOUT_KEYS):
            if old_block.get(key) != new_block.get(key):
                changes.append(DeploymentChange(
                    LAYOUT, "changed", block_id, label,
                    f"Drawing {key.replace('_', ' ')}",
                    old_block.get(key), new_block.get(key)))

        # Composite behavior is serialized beside ``config``. Ignoring these
        # fields makes an edited subsystem appear deployment-identical even
        # though the controller will execute a different internal graph.
        for key in sorted(_COMPOSITE_STRUCTURE_KEYS):
            if old_block.get(key) != new_block.get(key):
                changed.add(block_id)
                changes.append(DeploymentChange(
                    STRUCTURE, "changed", block_id, label,
                    f"Composite {key.replace('_', ' ')} changed",
                    old_block.get(key), new_block.get(key)))
        for key in sorted(_COMPOSITE_PARAMETER_KEYS):
            if old_block.get(key) != new_block.get(key):
                changed.add(block_id)
                changes.append(DeploymentChange(
                    PARAMETER, "changed", block_id, label,
                    f"Composite {key.replace('_', ' ')} changed",
                    old_block.get(key), new_block.get(key)))
    return changed


def _wire_signature(wire: Mapping[str, Any]) -> tuple:
    return (
        wire.get("src_block_id"), wire.get("src_terminal"),
        wire.get("dst_block_id"), wire.get("dst_terminal"),
        bool(wire.get("is_bkcal", False)),
    )


def _compare_wires(old: dict, current: dict,
                   changes: list[DeploymentChange]) -> set[str]:
    old_wires = {_wire_signature(wire) for wire in old.get("wires", [])}
    new_wires = {_wire_signature(wire) for wire in current.get("wires", [])}
    touched: set[str] = set()
    for action, wires in (("added", new_wires - old_wires),
                          ("removed", old_wires - new_wires)):
        for wire in sorted(wires, key=lambda value: tuple(map(str, value))):
            src, src_term, dst, dst_term, bkcal = wire
            touched.update(str(block_id) for block_id in (src, dst) if block_id)
            kind = "BKCAL wire" if bkcal else "Wire"
            changes.append(DeploymentChange(
                STRUCTURE, action, "<wire>",
                f"{src}.{src_term} -> {dst}.{dst_term}",
                f"{kind} {action}", wire if action == "removed" else None,
                wire if action == "added" else None))
    return touched


def _adjacency(document: Mapping[str, Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for wire in document.get("wires", []):
        src, dst, *_ = _wire_signature(wire)
        if src and dst:
            result.setdefault(str(src), set()).add(str(dst))
    return result


def _downstream_impacts(module: str, old: dict, current: dict,
                        roots: set[str]) -> list[ImpactedObject]:
    all_blocks = {**_blocks(old), **_blocks(current)}
    graph = _adjacency(old)
    for source, targets in _adjacency(current).items():
        graph.setdefault(source, set()).update(targets)
    queue = deque((root, 0, "changed block") for root in sorted(roots))
    seen: dict[str, int] = {}
    impacts: list[ImpactedObject] = []
    while queue:
        block_id, distance, reason = queue.popleft()
        if block_id in seen and seen[block_id] <= distance:
            continue
        seen[block_id] = distance
        block = all_blocks.get(block_id)
        if block is not None:
            impacts.append(ImpactedObject(
                module, block_id, _block_label(block, block_id), distance,
                reason if distance == 0 else "downstream signal path"))
        for target in sorted(graph.get(block_id, ())):
            queue.append((target, distance + 1, "downstream signal path"))
    return impacts


def _field_endpoints(document: Mapping[str, Any]) -> dict[str, list[tuple[str, str, str]]]:
    endpoints: dict[str, list[tuple[str, str, str]]] = {}
    for block in document.get("blocks", []):
        if not isinstance(block, Mapping):
            continue
        config = block.get("config") or {}
        tag = str(config.get("tag") or "").strip()
        if not tag:
            continue
        block_id = str(block.get("id") or "")
        direction = "write" if str(block.get("block_type")) in _OUTPUT_BLOCKS else "read"
        endpoints.setdefault(tag, []).append((
            block_id, _block_label(block, block_id), direction))
    return endpoints


def _cross_module_impacts(module: str, old: dict, current: dict,
                          roots: set[str], project_documents) -> list[ImpactedObject]:
    changed_tags: set[str] = set()
    for document in (old, current):
        for tag, endpoints in _field_endpoints(document).items():
            if any(block_id in roots for block_id, _label, _direction in endpoints):
                changed_tags.add(tag)
    if not changed_tags:
        return []

    impacts: list[ImpactedObject] = []
    for supplied in project_documents:
        document = snapshot_document(supplied)
        other_module = str(document.get("name") or "Untitled")
        if other_module == module:
            continue
        for tag, endpoints in _field_endpoints(document).items():
            if tag not in changed_tags:
                continue
            for block_id, label, direction in endpoints:
                impacts.append(ImpactedObject(
                    other_module, block_id, label, 1,
                    f"shared field tag {tag} ({direction})", external=True))
    return impacts


def _deduplicate_impacts(impacts: list[ImpactedObject]) -> list[ImpactedObject]:
    by_key: dict[tuple[str, str, str], ImpactedObject] = {}
    for impact in impacts:
        key = (impact.module, impact.object_id, impact.reason)
        existing = by_key.get(key)
        if existing is None or impact.distance < existing.distance:
            by_key[key] = impact
    return sorted(by_key.values(), key=lambda item: (
        item.external, item.module.casefold(), item.distance, item.label.casefold()))
