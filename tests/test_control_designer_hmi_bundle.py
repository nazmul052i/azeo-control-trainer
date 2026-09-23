"""Contract tests for the canonical-HMI convergence pilot."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from azeo_control_trainer.core.hmi.integration.control_designer_contract import (
    canonical_json,
    seal_control_designer_bundle,
    validate_control_designer_bundle,
    write_control_designer_bundle,
)
from azeo_control_trainer.core.hmi.integration.control_designer_exporter import (
    export_pid_hmi_bundle,
)

ROOT = Path(__file__).resolve().parents[1]
AREA = ROOT / "src" / "strategies" / "azeo_modbus"
GENERATED_AT = "2026-08-28T22:00:00Z"


def _pilot() -> dict:
    return export_pid_hmi_bundle(
        AREA,
        modules=["FIC-101"],
        generated_at=GENERATED_AT,
    )


def _members(bundle: dict) -> dict[str, dict]:
    binding = bundle["bindings"][0]
    return {item["memberId"]: item for item in binding["parameterBindings"]}


def test_pid_pilot_matches_strict_offline_contract() -> None:
    bundle = _pilot()
    validate_control_designer_bundle(bundle)
    unsigned = copy.deepcopy(bundle)
    digest = unsigned.pop("bundleHash")
    assert digest == hashlib.sha256(canonical_json(unsigned).encode()).hexdigest()
    assert bundle["format"] == "azeo-live-hmi-binding-bundle"
    assert bundle["contract"] == "azeo-live-hmi-binding-v1"
    assert bundle["liveConnectivity"] is False
    assert bundle["capabilities"] == ["offline-import", "metadata-only"]
    assert len(bundle["classes"]) == len(bundle["bindings"]) == 1


def test_pid_pilot_exports_real_paths_and_explicit_commands() -> None:
    bundle = _pilot()
    binding = bundle["bindings"][0]
    members = _members(bundle)
    assert members["PV"]["sourcePath"] == "FIC-101/PID1/PV"
    assert members["SP_WRK"]["sourcePath"] == "FIC-101/PID1/SP_WRK"
    assert members["MODE_ACTUAL"]["sourcePath"] == "FIC-101/PID1/MODE.ACTUAL"
    assert members["MODE_TARGET"]["sourcePath"] == "FIC-101/PID1/MODE.TARGET"
    assert members["HI_HI_LIM"]["sourcePath"] == (
        "FIC-101/PID1/CONFIG/hi_hi_lim"
    )
    assert members["GAIN"]["sourcePath"] == "FIC-101/PID1/CONFIG/GAIN"
    assert members["PV"]["range"] == {"minimum": 0.0, "maximum": 200.0}
    assert members["OUT"]["range"] == {"minimum": 0.0, "maximum": 100.0}
    assert members["PV"]["direction"] == "read"
    assert members["SP"]["permissions"] == ["view", "operate"]
    assert members["GAIN"]["permissions"] == ["view", "engineer"]

    command_members = {
        command["parameterBindingId"]: command for command in binding["commands"]
    }
    assert members["PV"]["id"] not in command_members
    assert command_members[members["SP"]["id"]]["permission"] == "operate"
    assert command_members[members["OUT"]["id"]]["confirmationRequired"] is False
    assert command_members[members["GAIN"]["id"]]["permission"] == "engineer"
    assert command_members[members["GAIN"]["id"]]["confirmationRequired"] is True


def test_repeated_export_is_byte_deterministic_with_fixed_timestamp(tmp_path: Path) -> None:
    first = _pilot()
    second = _pilot()
    assert first == second
    first_path = write_control_designer_bundle(tmp_path / "first.json", first)
    second_path = write_control_designer_bundle(tmp_path / "second.json", second)
    assert first_path.read_bytes() == second_path.read_bytes()
    assert first_path.read_bytes().endswith(b"\n")


def test_label_rename_preserves_external_identity(tmp_path: Path) -> None:
    baseline = _pilot()
    renamed_area = tmp_path / "renamed_area"
    (renamed_area / "control").mkdir(parents=True)
    project = json.loads((AREA / "_project.json").read_text(encoding="utf-8"))
    area = project["areas"][0]
    area["name"] = "RENAMED PROCESS AREA"
    area["strategies"] = ["control/FIC-101.json"]
    area["sfc_modules"] = []
    (renamed_area / "_project.json").write_text(
        json.dumps(project), encoding="utf-8"
    )
    shutil.copy2(AREA / "control" / "FIC-101.json", renamed_area / "control")
    module_path = renamed_area / "control" / "FIC-101.json"
    module = json.loads(module_path.read_text(encoding="utf-8"))
    module["name"] = "RENAMED-FLOW-LOOP"
    pid = next(block for block in module["blocks"] if block["block_type"] == "PID")
    pid["instance_name"] = "RENAMED_PID"
    module_path.write_text(json.dumps(module), encoding="utf-8")

    renamed = export_pid_hmi_bundle(
        renamed_area,
        modules=["RENAMED-FLOW-LOOP"],
        generated_at=GENERATED_AT,
    )
    assert renamed["projectId"] == baseline["projectId"]
    assert renamed["bindings"][0]["id"] == baseline["bindings"][0]["id"]
    assert renamed["bindings"][0]["provenance"]["instanceId"] == (
        baseline["bindings"][0]["provenance"]["instanceId"]
    )
    baseline_ids = {key: item["id"] for key, item in _members(baseline).items()}
    renamed_ids = {key: item["id"] for key, item in _members(renamed).items()}
    assert renamed_ids == baseline_ids
    assert _members(renamed)["PV"]["sourcePath"] == (
        "RENAMED-FLOW-LOOP/RENAMED_PID/PV"
    )
    assert renamed["bundleHash"] != baseline["bundleHash"]


def test_strict_producer_rejects_connection_metadata_and_inert_commands() -> None:
    bundle = _pilot()
    unsigned = copy.deepcopy(bundle)
    unsigned.pop("bundleHash")
    unsigned["serverEndpoint"] = "opc.tcp://example.invalid:4840"
    with pytest.raises(ValueError, match="fields differ|connection metadata"):
        seal_control_designer_bundle(unsigned)

    unsigned = copy.deepcopy(bundle)
    unsigned.pop("bundleHash")
    parameter = unsigned["bindings"][0]["parameterBindings"][0]
    parameter["direction"] = "read"
    parameter["permissions"] = ["view"]
    unsigned["bindings"][0]["commands"].append(
        {
            "id": "invalid-command",
            "name": "Inert command",
            "parameterBindingId": parameter["id"],
            "permission": "operate",
            "confirmationRequired": False,
            "allowedModes": ["operate"],
        }
    )
    with pytest.raises(ValueError, match="read-only parameter"):
        seal_control_designer_bundle(unsigned)
