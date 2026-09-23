"""Export PID engineering metadata for the canonical Azeo HMI product.

This is the first convergence pilot, not a second HMI document model.  It
adapts the durable identity available in a Control Designer project into the
strict offline contract already consumed by ``Azeo_HMI_Studio``.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid5

from .control_designer_contract import (
    CAPABILITIES,
    CONSUMER,
    CONTRACT,
    FORMAT,
    PRODUCER,
    PROVIDER,
    QUALITY_CONTRACT,
    SCHEMA_VERSION,
    canonical_json,
    seal_control_designer_bundle,
)

_EXPORT_NAMESPACE = UUID("d7bc9f58-d9e4-56d8-8586-b58e9e8d5d55")


@dataclass(frozen=True, slots=True)
class _PidMember:
    member_id: str
    source_name: str
    display_name: str
    data_type: Literal["boolean", "integer", "number", "string"] = "number"
    source_kind: Literal["terminal", "config", "field"] = "terminal"
    access: Literal["read", "operate", "engineer"] = "read"
    unit_kind: Literal["none", "pv", "out", "seconds", "pv_rate"] = "none"
    range_kind: Literal[
        "none", "pv", "sp", "out", "nonnegative", "fraction", "hysteresis"
    ] = "none"
    confirmation_required: bool = False


_PID_MEMBERS = (
    _PidMember("PV", "PV", "Process Value", unit_kind="pv", range_kind="pv"),
    _PidMember(
        "SP", "SP", "Setpoint", access="operate", unit_kind="pv", range_kind="sp"
    ),
    _PidMember(
        "SP_WRK", "SP_WRK", "Working Setpoint", unit_kind="pv", range_kind="sp"
    ),
    _PidMember(
        "OUT", "OUT", "Output", access="operate", unit_kind="out", range_kind="out"
    ),
    _PidMember(
        "MODE_ACTUAL",
        "MODE.ACTUAL",
        "Actual Mode",
        data_type="string",
    ),
    _PidMember(
        "MODE_TARGET",
        "MODE.TARGET",
        "Target Mode",
        data_type="string",
        access="operate",
    ),
    # Loop_dt limits.  Configured values use the CONFIG leg of the live path;
    # the member IDs retain the Azeo-facing names expected by donor content.
    _PidMember(
        "HI_HI_LIM", "hi_hi_lim", "Hi Hi Limit", source_kind="config",
        access="engineer", unit_kind="pv", range_kind="pv", confirmation_required=True,
    ),
    _PidMember(
        "HI_LIM", "hi_lim", "Hi Limit", source_kind="config",
        access="engineer", unit_kind="pv", range_kind="pv", confirmation_required=True,
    ),
    _PidMember(
        "LO_LIM", "lo_lim", "Lo Limit", source_kind="config",
        access="engineer", unit_kind="pv", range_kind="pv", confirmation_required=True,
    ),
    _PidMember(
        "LO_LO_LIM", "lo_lo_lim", "Lo Lo Limit", source_kind="config",
        access="engineer", unit_kind="pv", range_kind="pv", confirmation_required=True,
    ),
    _PidMember(
        "DV_HI_LIM", "dv_hi_lim", "Deviation Hi Limit", source_kind="config",
        access="engineer", unit_kind="pv", range_kind="pv", confirmation_required=True,
    ),
    _PidMember(
        "DV_LO_LIM", "dv_lo_lim", "Deviation Lo Limit", source_kind="config",
        access="engineer", unit_kind="pv", range_kind="pv", confirmation_required=True,
    ),
    _PidMember(
        "OUT_HI_LIM", "out_hi", "Output Hi Limit", source_kind="config",
        access="engineer", unit_kind="out", range_kind="out", confirmation_required=True,
    ),
    _PidMember(
        "OUT_LO_LIM", "out_lo", "Output Lo Limit", source_kind="config",
        access="engineer", unit_kind="out", range_kind="out", confirmation_required=True,
    ),
    _PidMember(
        "ARW_HI_LIM", "arw_hi", "ARW Hi Limit", source_kind="config",
        access="engineer", unit_kind="out", range_kind="out", confirmation_required=True,
    ),
    _PidMember(
        "ARW_LO_LIM", "arw_lo", "ARW Lo Limit", source_kind="config",
        access="engineer", unit_kind="out", range_kind="out", confirmation_required=True,
    ),
    _PidMember(
        "SP_HI_LIM", "sp_hi", "SP Hi Limit", source_kind="config",
        access="engineer", unit_kind="pv", range_kind="pv", confirmation_required=True,
    ),
    _PidMember(
        "SP_LO_LIM", "sp_lo", "SP Lo Limit", source_kind="config",
        access="engineer", unit_kind="pv", range_kind="pv", confirmation_required=True,
    ),
    _PidMember(
        "ALARM_HYS", "alarm_hys", "Alarm Hysteresis", source_kind="config",
        access="engineer", unit_kind="pv", range_kind="hysteresis",
        confirmation_required=True,
    ),
    # Loop_dt tuning and simulation fields.  Access is explicit; TagDatabase's
    # broad "writable config" flag is not enough to create an HMI command.
    _PidMember(
        "GAIN", "GAIN", "Gain", source_kind="config", access="engineer",
        range_kind="nonnegative", confirmation_required=True,
    ),
    _PidMember(
        "RESET", "RESET", "Reset", source_kind="config", access="engineer",
        unit_kind="seconds", range_kind="nonnegative", confirmation_required=True,
    ),
    _PidMember(
        "RATE", "RATE", "Rate", source_kind="config", access="engineer",
        unit_kind="seconds", range_kind="nonnegative", confirmation_required=True,
    ),
    _PidMember(
        "PV_FTIME", "pv_ftime", "PV Filter TC", source_kind="config",
        access="engineer", unit_kind="seconds", range_kind="nonnegative",
        confirmation_required=True,
    ),
    _PidMember(
        "SP_FTIME", "sp_ftime", "SP Filter TC", source_kind="config",
        access="engineer", unit_kind="seconds", range_kind="nonnegative",
        confirmation_required=True,
    ),
    _PidMember(
        "SP_RATE_DN", "sp_rate_dn", "SP Rate Down", source_kind="config",
        access="engineer", unit_kind="pv_rate", range_kind="nonnegative",
        confirmation_required=True,
    ),
    _PidMember(
        "SP_RATE_UP", "sp_rate_up", "SP Rate Up", source_kind="config",
        access="engineer", unit_kind="pv_rate", range_kind="nonnegative",
        confirmation_required=True,
    ),
    _PidMember(
        "STRUCTURE", "structure", "Structure", data_type="string",
        source_kind="config", access="engineer", confirmation_required=True,
    ),
    _PidMember(
        "BETA", "beta", "Beta", source_kind="config", access="engineer",
        range_kind="fraction", confirmation_required=True,
    ),
    _PidMember(
        "GAMMA", "gamma", "Gamma", source_kind="config", access="engineer",
        range_kind="fraction", confirmation_required=True,
    ),
    _PidMember(
        "BIAS", "bias", "Bias", source_kind="config", access="engineer",
        unit_kind="out", range_kind="out", confirmation_required=True,
    ),
    _PidMember(
        "IDEADBAND", "ideadband", "Integral Deadband", source_kind="config",
        access="engineer", unit_kind="pv", range_kind="nonnegative",
        confirmation_required=True,
    ),
    _PidMember(
        "FF_ENABLE", "ff_enable", "Feedforward Enable", data_type="boolean",
        source_kind="config", access="engineer", confirmation_required=True,
    ),
    _PidMember(
        "FF_GAIN", "ff_gain", "Feedforward Gain", source_kind="config",
        access="engineer", range_kind="nonnegative", confirmation_required=True,
    ),
    _PidMember(
        "ADAPTIVE_MODE", "use_pidplus", "Adaptive Mode", data_type="boolean",
        source_kind="config", access="engineer", confirmation_required=True,
    ),
    _PidMember(
        "SIMULATE", "simulate_enabled", "Simulate", data_type="boolean",
        source_kind="config", access="engineer", confirmation_required=True,
    ),
)


def export_pid_hmi_bundle(
    area_root: str | Path,
    *,
    modules: tuple[str, ...] | list[str] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build a strict metadata-only bundle for configured PID modules.

    ``modules`` filters by configured module name or file stem.  The caller may
    inject ``generated_at`` for reproducible builds; identities and revision
    hashes are deterministic regardless of the wall clock.
    """

    root = Path(area_root).resolve()
    project_path = root / "_project.json"
    project = _read_object(project_path, "project")
    area = _select_area(project, root)
    area_id = _required_text(area.get("area_id"), "area_id")
    project_name = _required_text(area.get("name") or root.name, "area name")
    project_id = _external_id("project", area_id)
    selected = {name.casefold() for name in modules or ()}
    configured_paths = _configured_module_paths(root, area)
    seen_selection: set[str] = set()
    classes: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    class_keys: set[tuple[str, str]] = set()

    for module_path, relative in configured_paths:
        raw = _read_object(module_path, f"module {relative}")
        module_name = _required_text(raw.get("name") or module_path.stem, "module name")
        selection_keys = {module_name.casefold(), module_path.stem.casefold()}
        if selected and selected.isdisjoint(selection_keys):
            continue
        seen_selection.update(selected.intersection(selection_keys))
        raw_blocks = raw.get("blocks", ())
        if not isinstance(raw_blocks, list):
            raise ValueError(f"module {relative} blocks must be an array")
        pid_blocks = [
            block
            for block in raw_blocks
            if isinstance(block, dict) and str(block.get("block_type", "")).upper() == "PID"
        ]
        if not pid_blocks:
            continue
        revision_hash = hashlib.sha256(canonical_json(raw).encode("utf-8")).hexdigest()
        class_id = _external_id("module-class", project_id, relative)
        class_revision_id = f"revision:{revision_hash[:32]}"
        class_key = (class_id, class_revision_id)
        if class_key not in class_keys:
            classes.append(
                {
                    "classId": class_id,
                    "classKind": "module",
                    "classRevisionId": class_revision_id,
                    "classRevisionHash": revision_hash,
                }
            )
            class_keys.add(class_key)
        unit_name = _unit_for_module(area, module_name)
        for block in sorted(pid_blocks, key=lambda item: str(item.get("id", ""))):
            bindings.append(
                _pid_binding(
                    project_id=project_id,
                    project_name=project_name,
                    unit_name=unit_name,
                    module_name=module_name,
                    module_relative=relative,
                    block=block,
                    class_id=class_id,
                    class_revision_id=class_revision_id,
                    class_revision_hash=revision_hash,
                )
            )

    missing = sorted(selected - seen_selection)
    if missing:
        raise ValueError(f"configured modules were not found: {', '.join(missing)}")
    if not bindings:
        scope = ", ".join(modules or ()) or "the configured project"
        raise ValueError(f"no PID blocks found in {scope}")

    timestamp = generated_at or datetime.now(UTC).isoformat().replace("+00:00", "Z")
    payload = {
        "format": FORMAT,
        "schemaVersion": SCHEMA_VERSION,
        "contract": CONTRACT,
        "producer": PRODUCER,
        "consumer": CONSUMER,
        "provider": PROVIDER,
        "liveConnectivity": False,
        "projectId": project_id,
        "projectName": project_name,
        "configurationRevision": _configuration_revision(project, area),
        "generatedAt": timestamp,
        "capabilities": list(CAPABILITIES),
        "classes": sorted(classes, key=lambda item: (item["classId"], item["classRevisionId"])),
        "bindings": sorted(bindings, key=lambda item: item["id"]),
    }
    return seal_control_designer_bundle(payload)


def _pid_binding(
    *,
    project_id: str,
    project_name: str,
    unit_name: str,
    module_name: str,
    module_relative: str,
    block: dict[str, Any],
    class_id: str,
    class_revision_id: str,
    class_revision_hash: str,
) -> dict[str, Any]:
    block_id = _required_text(block.get("id"), f"{module_relative} PID block id")
    block_name = _required_text(
        block.get("instance_name") or block_id,
        f"{module_relative} PID instance name",
    )
    config = block.get("config") or {}
    if not isinstance(config, dict):
        raise ValueError(f"{module_relative}/{block_name} config must be an object")
    binding_id = _external_id("binding-set", project_id, module_relative, block_id)
    instance_id = _external_id("module-instance", project_id, module_relative)
    block_instance_id = _external_id(
        "block-instance", project_id, module_relative, block_id
    )
    parameters: list[dict[str, Any]] = []
    commands: list[dict[str, Any]] = []
    identity_map: dict[str, str] = {
        f"module:{module_relative}": instance_id,
        f"block:{block_id}": block_instance_id,
    }
    for member in _PID_MEMBERS:
        parameter_id = _external_id("parameter", binding_id, member.member_id)
        class_object_id = _external_id("class-member", class_id, block_id, member.member_id)
        source_path = _source_path(module_name, block_name, member)
        permissions = ["view"]
        direction = "read"
        if member.access != "read":
            direction = "read-write"
            permissions.append(member.access)
        minimum, maximum = _member_range(member, config)
        parameter = {
            "id": parameter_id,
            "name": member.display_name,
            "classObjectId": class_object_id,
            "instanceObjectId": block_instance_id,
            "memberId": member.member_id,
            "sourcePath": source_path,
            "targetKey": f"azeo://binding/{project_id}/{parameter_id}",
            "dataType": member.data_type,
            "direction": direction,
            "unit": _member_unit(member, config),
            "range": {"minimum": minimum, "maximum": maximum},
            "statusSourceId": _external_id("status", parameter_id),
            "qualitySourceId": _external_id("quality", parameter_id),
            "qualityContract": QUALITY_CONTRACT,
            "permissions": permissions,
        }
        parameters.append(parameter)
        identity_map[f"member:{block_id}:{member.member_id}"] = parameter_id
        if member.access != "read":
            commands.append(
                {
                    "id": _external_id("command", parameter_id, "set"),
                    "name": (
                        f"Set {member.display_name}"
                        if member.access == "operate"
                        else f"Configure {member.display_name}"
                    ),
                    "parameterBindingId": parameter_id,
                    "permission": member.access,
                    "confirmationRequired": member.confirmation_required,
                    "allowedModes": ["operate", "simulation"],
                }
            )
    identity_map_hash = hashlib.sha256(
        canonical_json(identity_map).encode("utf-8")
    ).hexdigest()
    navigation_prefix = (project_id, module_relative, block_id)
    return {
        "id": binding_id,
        "name": f"{module_name} / {block_name}",
        "scope": "instance",
        "displayHierarchy": [project_name, unit_name, module_name],
        "displayLocation": "L3 Secondary Operation",
        "themeKey": "high-performance-silver",
        "languageKey": "en-US",
        "navigation": {
            "faceplateClassId": "Loop_fp:PID",
            "detailDisplayId": _external_id("display-detail", *navigation_prefix),
            "trendDisplayId": _external_id("display-trend", *navigation_prefix),
            "alarmDisplayId": _external_id("display-alarm", *navigation_prefix),
            "helpTopicKey": "PID:Loop_fp",
            "parentDisplayId": _external_id("display-parent", *navigation_prefix),
            "navigationTarget": f"{module_name}/{block_name}",
        },
        "provenance": {
            "classId": class_id,
            "classKind": "module",
            "classRevisionId": class_revision_id,
            "classRevisionHash": class_revision_hash,
            "instanceId": instance_id,
            "appliedRevisionId": class_revision_id,
            "identityMapHash": identity_map_hash,
            "divergenceStatus": "in-sync",
        },
        "parameterBindings": parameters,
        "commands": commands,
    }


def _source_path(module_name: str, block_name: str, member: _PidMember) -> str:
    if member.source_kind == "config":
        return f"{module_name}/{block_name}/CONFIG/{member.source_name}"
    return f"{module_name}/{block_name}/{member.source_name}"


def _member_unit(member: _PidMember, config: dict[str, Any]) -> str:
    pv_unit = str(config.get("pv_unit") or "")
    out_unit = str(config.get("op_unit") or "%")
    if member.unit_kind == "pv":
        return pv_unit
    if member.unit_kind == "out":
        return out_unit
    if member.unit_kind == "seconds":
        return "s"
    if member.unit_kind == "pv_rate":
        return f"{pv_unit}/s" if pv_unit else "EU/s"
    return ""


def _member_range(
    member: _PidMember, config: dict[str, Any]
) -> tuple[float | None, float | None]:
    pv = _finite_range(config.get("pv_scale_lo", 0.0), config.get("pv_scale_hi", 100.0))
    sp = _finite_range(config.get("sp_lo", pv[0]), config.get("sp_hi", pv[1]))
    out = _finite_range(config.get("out_lo", 0.0), config.get("out_hi", 100.0))
    if member.range_kind == "pv":
        return pv
    if member.range_kind == "sp":
        return sp
    if member.range_kind == "out":
        return out
    if member.range_kind == "nonnegative":
        return 0.0, None
    if member.range_kind == "fraction":
        return 0.0, 1.0
    if member.range_kind == "hysteresis":
        span = pv[1] - pv[0] if pv[0] is not None and pv[1] is not None else None
        return 0.0, span
    return None, None


def _finite_range(low: Any, high: Any) -> tuple[float | None, float | None]:
    minimum = _finite_number(low)
    maximum = _finite_number(high)
    if minimum is None or maximum is None or minimum > maximum:
        return None, None
    return minimum, maximum


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _external_id(kind: str, *parts: str) -> str:
    anchor = "\x1f".join((kind, *(str(part) for part in parts)))
    return f"cs-{kind}:{uuid5(_EXPORT_NAMESPACE, anchor)}"


def _configured_module_paths(
    root: Path, area: dict[str, Any]
) -> list[tuple[Path, str]]:
    configured: list[str] = []
    for key in ("strategies", "sfc_modules"):
        raw = area.get(key)
        if raw is None:
            raw = []
        if not isinstance(raw, list):
            raise ValueError(f"{key} must be an array")
        configured.extend(str(item) for item in raw)
    resolved: list[tuple[Path, str]] = []
    for raw in configured:
        relative = Path(raw.replace("\\", "/"))
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"configured module escapes the project: {raw}") from exc
        if not candidate.is_file():
            raise ValueError(f"configured module does not exist: {raw}")
        resolved.append((candidate, candidate.relative_to(root).as_posix()))
    return sorted(resolved, key=lambda item: item[1].casefold())


def _select_area(project: dict[str, Any], root: Path) -> dict[str, Any]:
    areas = project.get("areas")
    if not isinstance(areas, list) or not areas:
        raise ValueError(f"{root / '_project.json'} has no configured areas")
    if len(areas) == 1 and isinstance(areas[0], dict):
        return areas[0]
    folded = root.name.casefold().replace("_", "")
    matches = [
        area
        for area in areas
        if isinstance(area, dict)
        and str(area.get("area_id") or area.get("name") or "")
        .casefold()
        .replace("_", "")
        == folded
    ]
    if len(matches) != 1:
        raise ValueError("project area is ambiguous for this strategy directory")
    return matches[0]


def _unit_for_module(area: dict[str, Any], module_name: str) -> str:
    for unit in area.get("units") or ():
        if not isinstance(unit, dict):
            continue
        members = {str(item).casefold() for item in unit.get("modules") or ()}
        if module_name.casefold() in members:
            return str(unit.get("name") or "Unassigned Unit")
    return "Unassigned Unit"


def _configuration_revision(project: dict[str, Any], area: dict[str, Any]) -> int:
    raw = area.get("configuration_revision", project.get("configuration_revision", 0))
    if isinstance(raw, bool):
        raise ValueError("configuration_revision must be a non-negative integer")
    try:
        revision = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("configuration_revision must be a non-negative integer") from exc
    if revision < 0:
        raise ValueError("configuration_revision must be a non-negative integer")
    return revision


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read {label}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _required_text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    if len(text) > 256:
        raise ValueError(f"{label} exceeds 256 characters")
    return text
