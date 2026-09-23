"""Strict producer-side rules for the offline Control Designer HMI bundle.

The canonical HMI owns the Pydantic contract.  Control Designer deliberately
does not duplicate that persistent domain model: this module validates the
external DTO with the standard library, then serializes it exactly as the
canonical importer checks it.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

FORMAT = "azeo-live-hmi-binding-bundle"
SCHEMA_VERSION = 1
CONTRACT = "azeo-live-hmi-binding-v1"
PRODUCER = "Azeo Control Designer"
CONSUMER = "Azeo Live Graphics"
PROVIDER = "Offline Metadata Bundle"
CAPABILITIES = ("offline-import", "metadata-only")
QUALITY_CONTRACT = "good-uncertain-bad"
MAX_BUNDLE_BYTES = 32 * 1024 * 1024

_HEX = frozenset("0123456789abcdef")
_FORBIDDEN_KEY_PARTS = (
    "endpoint",
    "url",
    "host",
    "credential",
    "password",
    "secret",
    "token",
    "apikey",
)
_ROOT_KEYS = {
    "format",
    "schemaVersion",
    "contract",
    "producer",
    "consumer",
    "provider",
    "liveConnectivity",
    "projectId",
    "projectName",
    "configurationRevision",
    "generatedAt",
    "capabilities",
    "classes",
    "bindings",
    "bundleHash",
}
_CLASS_KEYS = {
    "classId",
    "classKind",
    "classRevisionId",
    "classRevisionHash",
}
_BINDING_KEYS = {
    "id",
    "name",
    "scope",
    "displayHierarchy",
    "displayLocation",
    "themeKey",
    "languageKey",
    "navigation",
    "provenance",
    "parameterBindings",
    "commands",
}
_NAVIGATION_KEYS = {
    "faceplateClassId",
    "detailDisplayId",
    "trendDisplayId",
    "alarmDisplayId",
    "helpTopicKey",
    "parentDisplayId",
    "navigationTarget",
}
_PROVENANCE_KEYS = {
    "classId",
    "classKind",
    "classRevisionId",
    "classRevisionHash",
    "instanceId",
    "appliedRevisionId",
    "identityMapHash",
    "divergenceStatus",
}
_PARAMETER_KEYS = {
    "id",
    "name",
    "classObjectId",
    "instanceObjectId",
    "memberId",
    "sourcePath",
    "targetKey",
    "dataType",
    "direction",
    "unit",
    "range",
    "statusSourceId",
    "qualitySourceId",
    "qualityContract",
    "permissions",
}
_COMMAND_KEYS = {
    "id",
    "name",
    "parameterBindingId",
    "permission",
    "confirmationRequired",
    "allowedModes",
}


def canonical_json(value: Mapping[str, Any]) -> str:
    """Return the exact compact JSON representation used for SHA-256."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def bundle_hash(value: Mapping[str, Any]) -> str:
    """Hash a bundle without trusting or including its ``bundleHash`` field."""

    unsigned = copy.deepcopy(dict(value))
    unsigned.pop("bundleHash", None)
    return hashlib.sha256(canonical_json(unsigned).encode("utf-8")).hexdigest()


def seal_control_designer_bundle(value: Mapping[str, Any]) -> dict[str, Any]:
    """Copy, checksum, and strictly validate an unsigned bundle."""

    payload = copy.deepcopy(dict(value))
    payload.pop("bundleHash", None)
    payload["bundleHash"] = bundle_hash(payload)
    validate_control_designer_bundle(payload)
    return payload


def validate_control_designer_bundle(value: Mapping[str, Any]) -> None:
    """Reject malformed or connectivity-bearing producer DTOs.

    This intentionally mirrors the canonical HMI's strict Pydantic boundary.
    Keeping a producer check catches integration defects before a file leaves
    Control Designer while the canonical importer remains the final authority.
    """

    root = _mapping(value, "bundle")
    _exact_keys(root, _ROOT_KEYS, "bundle")
    _literal(root["format"], FORMAT, "format")
    _literal(root["schemaVersion"], SCHEMA_VERSION, "schemaVersion")
    _literal(root["contract"], CONTRACT, "contract")
    _literal(root["producer"], PRODUCER, "producer")
    _literal(root["consumer"], CONSUMER, "consumer")
    _literal(root["provider"], PROVIDER, "provider")
    _literal(root["liveConnectivity"], False, "liveConnectivity")
    _external_id(root["projectId"], "projectId")
    _external_id(root["projectName"], "projectName")
    revision = root["configurationRevision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("configurationRevision must be a non-negative integer")
    _bounded_text(root["generatedAt"], "generatedAt", 64)
    _sequence(root["capabilities"], "capabilities", exact=CAPABILITIES)
    _sha256(root["bundleHash"], "bundleHash")
    if root["bundleHash"] != bundle_hash(root):
        raise ValueError("bundleHash does not match the canonical bundle content")

    classes = _sequence(root["classes"], "classes", maximum=10_000)
    class_keys: set[tuple[str, str]] = set()
    declared: set[tuple[str, str, str, str]] = set()
    for index, raw in enumerate(classes):
        path = f"classes[{index}]"
        item = _mapping(raw, path)
        _exact_keys(item, _CLASS_KEYS, path)
        class_id = _external_id(item["classId"], f"{path}.classId")
        class_kind = _one_of(item["classKind"], {"module", "composite"}, path)
        revision_id = _external_id(
            item["classRevisionId"], f"{path}.classRevisionId"
        )
        revision_hash = _sha256(
            item["classRevisionHash"], f"{path}.classRevisionHash"
        )
        key = (class_id, revision_id)
        if key in class_keys:
            raise ValueError(f"duplicate class revision: {key}")
        class_keys.add(key)
        declared.add((class_id, class_kind, revision_id, revision_hash))

    bindings = _sequence(root["bindings"], "bindings", maximum=20_000)
    binding_ids: set[str] = set()
    for index, raw in enumerate(bindings):
        path = f"bindings[{index}]"
        binding = _mapping(raw, path)
        _exact_keys(binding, _BINDING_KEYS, path)
        binding_id = _external_id(binding["id"], f"{path}.id")
        if binding_id in binding_ids:
            raise ValueError(f"duplicate binding set ID: {binding_id}")
        binding_ids.add(binding_id)
        _external_id(binding["name"], f"{path}.name")
        _one_of(binding["scope"], {"class", "instance"}, f"{path}.scope")
        hierarchy = _sequence(
            binding["displayHierarchy"],
            f"{path}.displayHierarchy",
            minimum=1,
            maximum=32,
        )
        for level, name in enumerate(hierarchy):
            _external_id(name, f"{path}.displayHierarchy[{level}]")
        for name in ("displayLocation", "themeKey", "languageKey"):
            _external_id(binding[name], f"{path}.{name}")
        _validate_navigation(binding["navigation"], f"{path}.navigation")
        provenance_key = _validate_provenance(
            binding["provenance"], f"{path}.provenance"
        )
        if provenance_key not in declared:
            raise ValueError(f"{path} references an undeclared class revision")
        parameter_ids, parameters = _validate_parameters(
            binding["parameterBindings"], f"{path}.parameterBindings"
        )
        _validate_commands(
            binding["commands"], f"{path}.commands", parameter_ids, parameters
        )

    _reject_connection_metadata(root)
    encoded = canonical_json(root).encode("utf-8")
    if len(encoded) > MAX_BUNDLE_BYTES:
        raise ValueError("Control Designer HMI bundle exceeds 32 MiB")


def write_control_designer_bundle(path: str | Path, value: Mapping[str, Any]) -> Path:
    """Atomically write deterministic, validated UTF-8 JSON.

    The temporary file is flushed and fsynced before ``os.replace``.  A crash
    therefore leaves either the previous complete bundle or the new complete
    bundle, never a partially-written integration artifact.
    """

    validate_control_designer_bundle(value)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    ) + "\n"
    if len(body.encode("utf-8")) > MAX_BUNDLE_BYTES:
        raise ValueError("Control Designer HMI bundle exceeds 32 MiB")
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as stream:
            temporary_name = stream.name
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, destination)
    finally:
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass
    return destination


def _validate_navigation(value: Any, path: str) -> None:
    item = _mapping(value, path)
    _exact_keys(item, _NAVIGATION_KEYS, path)
    for name in _NAVIGATION_KEYS - {"navigationTarget"}:
        _external_id(item[name], f"{path}.{name}")
    _bounded_text(item["navigationTarget"], f"{path}.navigationTarget", 2048)


def _validate_provenance(value: Any, path: str) -> tuple[str, str, str, str]:
    item = _mapping(value, path)
    _exact_keys(item, _PROVENANCE_KEYS, path)
    class_id = _external_id(item["classId"], f"{path}.classId")
    class_kind = _one_of(
        item["classKind"], {"module", "composite"}, f"{path}.classKind"
    )
    revision_id = _external_id(item["classRevisionId"], f"{path}.classRevisionId")
    revision_hash = _sha256(
        item["classRevisionHash"], f"{path}.classRevisionHash"
    )
    _bounded_text(item["instanceId"], f"{path}.instanceId", 256, allow_empty=True)
    _external_id(item["appliedRevisionId"], f"{path}.appliedRevisionId")
    identity_hash = item["identityMapHash"]
    if identity_hash != "":
        _sha256(identity_hash, f"{path}.identityMapHash")
    _bounded_text(item["divergenceStatus"], f"{path}.divergenceStatus", 64)
    return class_id, class_kind, revision_id, revision_hash


def _validate_parameters(
    value: Any, path: str
) -> tuple[set[str], dict[str, Mapping[str, Any]]]:
    rows = _sequence(value, path, maximum=10_000)
    ids: set[str] = set()
    targets: set[str] = set()
    parameters: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(rows):
        item_path = f"{path}[{index}]"
        item = _mapping(raw, item_path)
        _exact_keys(item, _PARAMETER_KEYS, item_path)
        parameter_id = _external_id(item["id"], f"{item_path}.id")
        if parameter_id in ids:
            raise ValueError(f"duplicate parameter binding ID: {parameter_id}")
        ids.add(parameter_id)
        parameters[parameter_id] = item
        for name in (
            "name",
            "classObjectId",
            "memberId",
            "statusSourceId",
            "qualitySourceId",
        ):
            _external_id(item[name], f"{item_path}.{name}")
        _bounded_text(
            item["instanceObjectId"],
            f"{item_path}.instanceObjectId",
            256,
            allow_empty=True,
        )
        _bounded_text(item["sourcePath"], f"{item_path}.sourcePath", 2048)
        target = _bounded_text(item["targetKey"], f"{item_path}.targetKey", 2048)
        if len(target) < 16 or not target.startswith("azeo://binding/"):
            raise ValueError(f"{item_path}.targetKey is not an Azeo binding key")
        folded = target.casefold()
        if folded in targets:
            raise ValueError(f"duplicate parameter target key: {target}")
        targets.add(folded)
        _one_of(
            item["dataType"],
            {"boolean", "integer", "number", "string"},
            f"{item_path}.dataType",
        )
        direction = _one_of(
            item["direction"], {"read", "read-write"}, f"{item_path}.direction"
        )
        _bounded_text(item["unit"], f"{item_path}.unit", 64, allow_empty=True)
        value_range = _mapping(item["range"], f"{item_path}.range")
        _exact_keys(value_range, {"minimum", "maximum"}, f"{item_path}.range")
        minimum = _finite_or_none(value_range["minimum"], f"{item_path}.range.minimum")
        maximum = _finite_or_none(value_range["maximum"], f"{item_path}.range.maximum")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError(f"{item_path}.range minimum exceeds maximum")
        _literal(
            item["qualityContract"], QUALITY_CONTRACT, f"{item_path}.qualityContract"
        )
        permissions = _sequence(
            item["permissions"], f"{item_path}.permissions", minimum=1, maximum=8
        )
        if len(permissions) != len(set(permissions)):
            raise ValueError(f"{item_path}.permissions contains duplicates")
        if any(permission not in {"view", "operate", "engineer"} for permission in permissions):
            raise ValueError(f"{item_path}.permissions contains an unknown permission")
        if "view" not in permissions:
            raise ValueError(f"{item_path}.permissions must include view")
        if direction == "read-write" and not {"operate", "engineer"}.intersection(
            permissions
        ):
            raise ValueError(f"{item_path} is writable without a write permission")
    return ids, parameters


def _validate_commands(
    value: Any,
    path: str,
    parameter_ids: set[str],
    parameters: Mapping[str, Mapping[str, Any]],
) -> None:
    rows = _sequence(value, path, maximum=5_000)
    ids: set[str] = set()
    for index, raw in enumerate(rows):
        item_path = f"{path}[{index}]"
        item = _mapping(raw, item_path)
        _exact_keys(item, _COMMAND_KEYS, item_path)
        command_id = _external_id(item["id"], f"{item_path}.id")
        if command_id in ids:
            raise ValueError(f"duplicate command binding ID: {command_id}")
        ids.add(command_id)
        _external_id(item["name"], f"{item_path}.name")
        parameter_id = _external_id(
            item["parameterBindingId"], f"{item_path}.parameterBindingId"
        )
        if parameter_id not in parameter_ids:
            raise ValueError(f"{item_path} references a missing parameter")
        parameter = parameters[parameter_id]
        if parameter["direction"] != "read-write":
            raise ValueError(f"{item_path} references a read-only parameter")
        permission = _one_of(
            item["permission"], {"operate", "engineer"}, f"{item_path}.permission"
        )
        if permission not in parameter["permissions"]:
            raise ValueError(f"{item_path} permission is absent from the parameter")
        if not isinstance(item["confirmationRequired"], bool):
            raise ValueError(f"{item_path}.confirmationRequired must be boolean")
        modes = _sequence(
            item["allowedModes"], f"{item_path}.allowedModes", minimum=1, maximum=8
        )
        if len(modes) != len(set(modes)):
            raise ValueError(f"{item_path}.allowedModes contains duplicates")
        if any(mode not in {"operate", "simulation"} for mode in modes):
            raise ValueError(f"{item_path}.allowedModes contains an unknown mode")


def _reject_connection_metadata(value: Any, path: str = "bundle") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            folded = str(key).casefold().replace("_", "").replace("-", "")
            if any(part in folded for part in _FORBIDDEN_KEY_PARTS):
                raise ValueError(f"connection metadata is forbidden: {path}.{key}")
            _reject_connection_metadata(child, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _reject_connection_metadata(child, f"{path}[{index}]")


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must be an object")
    return value


def _sequence(
    value: Any,
    path: str,
    *,
    minimum: int = 0,
    maximum: int | None = None,
    exact: Sequence[Any] | None = None,
) -> Sequence[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError(f"{path} must be an array")
    if len(value) < minimum or (maximum is not None and len(value) > maximum):
        raise ValueError(f"{path} has an invalid item count")
    if exact is not None and tuple(value) != tuple(exact):
        raise ValueError(f"{path} must equal {tuple(exact)!r}")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ValueError(f"{path} fields differ; missing={missing}, unknown={unknown}")


def _literal(value: Any, expected: Any, path: str) -> Any:
    if type(value) is not type(expected) or value != expected:
        raise ValueError(f"{path} must be {expected!r}")
    return value


def _bounded_text(
    value: Any,
    path: str,
    maximum: int,
    *,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str) or (not allow_empty and not value) or len(value) > maximum:
        raise ValueError(f"{path} must be a bounded string")
    return value


def _external_id(value: Any, path: str) -> str:
    return _bounded_text(value, path, 256)


def _sha256(value: Any, path: str) -> str:
    text = _bounded_text(value, path, 64)
    if len(text) != 64 or any(character not in _HEX for character in text):
        raise ValueError(f"{path} must be a lower-case SHA-256 digest")
    return text


def _one_of(value: Any, choices: set[str], path: str) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"{path} must be one of {sorted(choices)!r}")
    return value


def _finite_or_none(value: Any, path: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{path} must be a number or null")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{path} must be finite")
    return number
