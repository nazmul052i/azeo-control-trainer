"""Linked Control Module instance behavior and three-way class adoption."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Mapping

from ..composites.library import PublicParameter
from ..deployment_analysis import (
    DeploymentChange,
    DeploymentImpactReport,
    analyze_deployment,
)
from .library import ModuleClassDefinition, ModuleClassLibrary


MODULE_CLASS_KEY = "module_class"
INSTANCE_SCHEMA_VERSION = 1
_MISSING = object()


@dataclass(frozen=True)
class ParameterCandidate:
    name: str
    path: str
    data_type: str
    default: object
    description: str = ""

    def public_parameter(self) -> PublicParameter:
        return PublicParameter(
            self.name, self.path, self.data_type,
            deepcopy(self.default), self.description)


@dataclass
class ModuleClassLink:
    definition_id: str
    definition_name: str
    definition_revision: int
    definition_digest: str
    definition_snapshot: dict
    public_parameter_overrides: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "schema_version": INSTANCE_SCHEMA_VERSION,
            "definition_id": self.definition_id,
            "definition_name": self.definition_name,
            "definition_revision": self.definition_revision,
            "definition_digest": self.definition_digest,
            "definition_snapshot": deepcopy(self.definition_snapshot),
            "public_parameter_overrides": deepcopy(
                self.public_parameter_overrides),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ModuleClassLink":
        if int(value.get("schema_version", 0)) != INSTANCE_SCHEMA_VERSION:
            raise ValueError("unsupported module-class instance schema")
        snapshot = value.get("definition_snapshot")
        definition = ModuleClassDefinition.from_dict(deepcopy(snapshot))
        link = cls(
            definition_id=str(value.get("definition_id", "")),
            definition_name=str(value.get("definition_name", "")),
            definition_revision=int(value.get("definition_revision", 0)),
            definition_digest=str(value.get("definition_digest", "")),
            definition_snapshot=definition.to_dict(),
            public_parameter_overrides=deepcopy(
                value.get("public_parameter_overrides", {})),
        )
        if (link.definition_id != definition.id
                or link.definition_revision != definition.revision
                or link.definition_digest != definition.digest):
            raise ValueError("module-class link does not match its snapshot")
        return link


@dataclass(frozen=True)
class ModuleClassInstanceStatus:
    module: str
    linked: bool
    state: str
    definition_id: str = ""
    definition_name: str = ""
    instance_revision: int = 0
    library_revision: int = 0
    overrides: int = 0
    deviations: tuple[DeploymentChange, ...] = ()


@dataclass
class ModuleClassUpdatePlan:
    module: str
    definition: ModuleClassDefinition
    class_changes: list[DeploymentChange]
    instance_deviations: list[DeploymentChange]
    conflict_paths: list[str]
    adopt_document: dict
    preserve_document: dict | None

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflict_paths)


def _link(document: Mapping[str, Any]) -> ModuleClassLink | None:
    raw = document.get(MODULE_CLASS_KEY)
    if not isinstance(raw, Mapping):
        return None
    return ModuleClassLink.from_dict(raw)


def _without_link(document: Mapping[str, Any]) -> dict:
    result = deepcopy(dict(document))
    result.pop(MODULE_CLASS_KEY, None)
    return result


def _normalize_overrides(
    definition: ModuleClassDefinition,
    overrides: Mapping[str, object], *, allow_unknown: bool,
) -> dict[str, object]:
    from ..blocks.composite_blocks import _coerce_public_value

    declared = {parameter.name.casefold(): parameter
                for parameter in definition.public_parameters}
    result: dict[str, object] = {}
    for raw_name, raw_value in overrides.items():
        name = str(raw_name).strip()
        parameter = declared.get(name.casefold())
        if parameter is None:
            if allow_unknown:
                result[name] = deepcopy(raw_value)
                continue
            raise ValueError(f"unknown public parameter {raw_name!r}")
        result[parameter.name] = _coerce_public_value(
            raw_value, parameter.data_type)
    return result


def _effective_document(
    definition: ModuleClassDefinition,
    module_name: str,
    overrides: Mapping[str, object],
    *, allow_unknown: bool = False,
) -> tuple[dict, dict[str, object]]:
    import azeo_control_trainer.core.strategy.blocks  # noqa: F401

    from ..blocks.composite_blocks import (
        _assign_public_parameter,
        _coerce_public_value,
    )
    from ..engine.compiler import compile_strategy
    from ..serialization.strategy_io import graph_from_document

    normalized = ModuleClassDefinition.from_dict(definition.to_dict())
    values = _normalize_overrides(
        normalized, overrides, allow_unknown=allow_unknown)
    graph, comments = graph_from_document(normalized.graph, strict=True)
    for parameter in normalized.public_parameters:
        raw = values.get(parameter.name, parameter.default)
        _assign_public_parameter(
            graph, parameter.path,
            _coerce_public_value(raw, parameter.data_type))
    graph.name = str(module_name).strip()
    compile_strategy(graph)
    document = graph.to_dict()
    if comments or "comments" in normalized.graph:
        document["comments"] = deepcopy(comments)
    return document, values


def _attach_link(
    document: dict, definition: ModuleClassDefinition,
    overrides: Mapping[str, object],
) -> dict:
    result = _without_link(document)
    result[MODULE_CLASS_KEY] = ModuleClassLink(
        definition_id=definition.id,
        definition_name=definition.name,
        definition_revision=definition.revision,
        definition_digest=definition.digest,
        definition_snapshot=definition.to_dict(),
        public_parameter_overrides=dict(overrides),
    ).to_dict()
    return result


def create_linked_instance(
    definition: ModuleClassDefinition, module_name: str, *,
    overrides: Mapping[str, object] | None = None,
) -> dict:
    """Create a detached, compilable instance linked to one class revision."""
    name = str(module_name).strip()
    if not name:
        raise ValueError("module instance needs a name")
    effective, normalized = _effective_document(
        definition, name, overrides or {})
    return _attach_link(effective, definition, normalized)


def definition_state(
    document: Mapping[str, Any], library: ModuleClassLibrary,
) -> str:
    """Return ``independent``, ``current``, ``stale``, or ``missing``."""
    link = _link(document)
    if link is None:
        return "independent"
    try:
        current = library.get(link.definition_id)
    except (KeyError, OSError, TypeError, ValueError):
        return "missing"
    if (current.revision == link.definition_revision
            and current.digest == link.definition_digest):
        return "current"
    return "stale"


def _baseline(document: Mapping[str, Any]) -> tuple[ModuleClassLink, dict]:
    link = _link(document)
    if link is None:
        raise ValueError("module is not linked to a class")
    definition = ModuleClassDefinition.from_dict(link.definition_snapshot)
    baseline, _normalized = _effective_document(
        definition, str(document.get("name") or "Untitled"),
        link.public_parameter_overrides, allow_unknown=True)
    return link, baseline


def analyze_instance(
    document: Mapping[str, Any], library: ModuleClassLibrary | None = None,
) -> ModuleClassInstanceStatus:
    module = str(document.get("name") or "Untitled")
    link = _link(document)
    if link is None:
        return ModuleClassInstanceStatus(module, False, "independent")
    _stored_link, baseline = _baseline(document)
    report = analyze_deployment(baseline, _without_link(document))
    state = definition_state(document, library) if library is not None \
        else "linked"
    library_revision = 0
    if library is not None and state != "missing":
        try:
            library_revision = library.get(link.definition_id).revision
        except (KeyError, OSError, TypeError, ValueError):
            pass
    return ModuleClassInstanceStatus(
        module=module,
        linked=True,
        state=state,
        definition_id=link.definition_id,
        definition_name=link.definition_name,
        instance_revision=link.definition_revision,
        library_revision=library_revision,
        overrides=len(link.public_parameter_overrides),
        deviations=tuple(report.changes),
    )


def _path_text(path: tuple[str, ...]) -> str:
    return "/".join(path) or "<module>"


def _identified_list(value: Any) -> bool:
    return (isinstance(value, list)
            and all(isinstance(item, Mapping) and item.get("id") is not None
                    for item in value))


def _merge_three_way(
    base: Any, local: Any, remote: Any, path: tuple[str, ...] = (),
) -> tuple[Any, list[str]]:
    """Preserve non-overlapping instance deviations across a class update."""
    def clone(value):
        return _MISSING if value is _MISSING else deepcopy(value)

    if local == base:
        return clone(remote), []
    if remote == base or local == remote:
        return clone(local), []

    if all(isinstance(value, Mapping) for value in (base, local, remote)):
        result: dict = {}
        conflicts: list[str] = []
        keys = list(dict.fromkeys(
            [*remote.keys(), *local.keys(), *base.keys()]))
        for key in keys:
            base_value = base.get(key, _MISSING)
            local_value = local.get(key, _MISSING)
            remote_value = remote.get(key, _MISSING)
            merged, nested = _merge_three_way(
                base_value, local_value, remote_value, (*path, str(key)))
            conflicts.extend(nested)
            if merged is not _MISSING:
                result[key] = merged
        return result, conflicts

    if all(_identified_list(value) for value in (base, local, remote)):
        base_map = {str(item["id"]): item for item in base}
        local_map = {str(item["id"]): item for item in local}
        remote_map = {str(item["id"]): item for item in remote}
        merged, conflicts = _merge_three_way(
            base_map, local_map, remote_map, path)
        if merged is _MISSING:
            return _MISSING, conflicts
        order = list(dict.fromkeys(
            [*(str(item["id"]) for item in remote),
             *(str(item["id"]) for item in local)]))
        return [merged[key] for key in order if key in merged], conflicts

    # Deletion participates in the same equality rules above. If neither side
    # retained the base value, both changed the same leaf and need review.
    return clone(local), [_path_text(path)]


def plan_update(
    document: Mapping[str, Any], definition: ModuleClassDefinition,
) -> ModuleClassUpdatePlan:
    link, baseline = _baseline(document)
    if definition.id != link.definition_id:
        raise ValueError("selected class does not match this module instance")
    module = str(document.get("name") or "Untitled")
    adopt, overrides = _effective_document(
        definition, module, link.public_parameter_overrides,
        allow_unknown=True)
    local = _without_link(document)
    merged, conflicts = _merge_three_way(baseline, local, adopt)
    adopt_linked = _attach_link(adopt, definition, overrides)
    preserve = None if conflicts else _attach_link(
        merged, definition, overrides)
    return ModuleClassUpdatePlan(
        module=module,
        definition=definition,
        class_changes=analyze_deployment(baseline, adopt).changes,
        instance_deviations=analyze_deployment(baseline, local).changes,
        conflict_paths=sorted(set(conflicts)),
        adopt_document=adopt_linked,
        preserve_document=preserve,
    )


def apply_update(
    document: Mapping[str, Any], definition: ModuleClassDefinition, *,
    preserve_deviations: bool = False,
) -> dict:
    plan = plan_update(document, definition)
    if preserve_deviations:
        if plan.preserve_document is None:
            raise ValueError(
                "class update conflicts with instance deviations at: "
                + ", ".join(plan.conflict_paths))
        return deepcopy(plan.preserve_document)
    return deepcopy(plan.adopt_document)


def unlink_instance(document: Mapping[str, Any]) -> dict:
    """Keep effective logic but remove class identity and inheritance."""
    if _link(document) is None:
        return deepcopy(dict(document))
    return _without_link(document)


def _parameter(
    definition: ModuleClassDefinition, name: str,
) -> PublicParameter:
    wanted = str(name).strip().casefold()
    matches = [item for item in definition.public_parameters
               if item.name.casefold() == wanted]
    if len(matches) != 1:
        raise KeyError(f"unknown public parameter {name!r}")
    return matches[0]


def set_public_parameter_override(
    document: Mapping[str, Any], name: str, value: object,
) -> dict:
    """Change one declared instance property without hiding other deviations."""
    import azeo_control_trainer.core.strategy.blocks  # noqa: F401

    from ..blocks.composite_blocks import (
        _assign_public_parameter,
        _coerce_public_value,
    )
    from ..engine.compiler import compile_strategy
    from ..serialization.strategy_io import graph_from_document

    link = _link(document)
    if link is None:
        raise ValueError("module is not linked to a class")
    definition = ModuleClassDefinition.from_dict(link.definition_snapshot)
    parameter = _parameter(definition, name)
    coerced = _coerce_public_value(value, parameter.data_type)
    graph, comments = graph_from_document(
        _without_link(document), strict=True)
    _assign_public_parameter(graph, parameter.path, coerced)
    compile_strategy(graph)
    overrides = dict(link.public_parameter_overrides)
    overrides[parameter.name] = coerced
    updated = graph.to_dict()
    if comments or "comments" in document:
        updated["comments"] = deepcopy(comments)
    return _attach_link(updated, definition, overrides)


def clear_public_parameter_override(
    document: Mapping[str, Any], name: str,
) -> dict:
    link = _link(document)
    if link is None:
        raise ValueError("module is not linked to a class")
    definition = ModuleClassDefinition.from_dict(link.definition_snapshot)
    parameter = _parameter(definition, name)
    result = set_public_parameter_override(
        document, parameter.name, parameter.default)
    refreshed = _link(result)
    assert refreshed is not None
    refreshed.public_parameter_overrides.pop(parameter.name, None)
    result[MODULE_CLASS_KEY] = refreshed.to_dict()
    return result


def public_parameter_value(
    document: Mapping[str, Any], name: str,
) -> object:
    from ..blocks.composite_blocks import _coerce_public_value

    link = _link(document)
    if link is None:
        raise ValueError("module is not linked to a class")
    definition = ModuleClassDefinition.from_dict(link.definition_snapshot)
    parameter = _parameter(definition, name)
    return deepcopy(_coerce_public_value(
        link.public_parameter_overrides.get(
            parameter.name, parameter.default),
        parameter.data_type))


def _type_name(expected: type, value: object) -> str:
    if expected is bool or isinstance(value, bool):
        return "BOOL"
    if expected is int or isinstance(value, int):
        return "INT"
    if expected is float or isinstance(value, float):
        return "FLOAT"
    if expected is str or isinstance(value, str):
        return "STRING"
    return "ANY"


def parameter_candidates(document: Mapping[str, Any]) -> list[ParameterCandidate]:
    """Discover schema-backed properties suitable for class publication."""
    import azeo_control_trainer.core.strategy.blocks  # noqa: F401

    from ..serialization.strategy_io import graph_from_document

    graph, _comments = graph_from_document(
        _without_link(document), strict=True)
    result: list[ParameterCandidate] = []
    for name, spec in graph.module_parameters().items():
        value = spec.get("value")
        kind = str(spec.get("data_type") or _type_name(type(value), value))
        result.append(ParameterCandidate(
            name=str(name),
            path=f"MODULE/PARAMETERS/{name}",
            data_type=kind.upper(),
            default=deepcopy(value),
            description=str(spec.get("description", "")),
        ))
    for block in graph.blocks.values():
        result.append(ParameterCandidate(
            name=f"{block.instance_name}_InstanceName",
            path=f"{block.id}/IDENTITY/INSTANCE_NAME",
            data_type="STRING",
            default=block.instance_name,
            description=(
                "Instance-specific published block identity. Use this when "
                "linked modules would otherwise publish the same control or "
                "I/O block name."
            ),
        ))
        schema = block.get_config_schema()
        for key, spec in schema.items():
            if not isinstance(spec, (tuple, list)) or not spec:
                continue
            expected = spec[0]
            default = spec[1] if len(spec) > 1 else None
            description = str(spec[2]) if len(spec) > 2 else ""
            value = block.config.params.get(key, default)
            result.append(ParameterCandidate(
                name=f"{block.instance_name}_{key}",
                path=f"{block.instance_name}/CONFIG/{key}",
                data_type=_type_name(expected, value),
                default=deepcopy(value),
                description=description,
            ))
    return sorted(result, key=lambda item: item.path.casefold())


def report_for_instance(
    document: Mapping[str, Any],
) -> DeploymentImpactReport:
    """Expose the complete deviation report for non-UI review surfaces."""
    _link_record, baseline = _baseline(document)
    return analyze_deployment(baseline, _without_link(document))
