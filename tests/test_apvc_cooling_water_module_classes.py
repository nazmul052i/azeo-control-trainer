"""Qualification of the class-based APVC cooling-water modules."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sys


REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from azeo_control_trainer.core.strategy import blocks as _blocks  # noqa: E402,F401
from azeo_control_trainer.core.strategy.engine.compiler import (  # noqa: E402
    compile_strategy,
)
from azeo_control_trainer.core.strategy.module_classes import (  # noqa: E402
    ModuleClassLibrary,
    analyze_instance,
)
from azeo_control_trainer.core.strategy.serialization.strategy_io import (  # noqa: E402
    graph_from_document,
)


PROJECT = REPO / "projects" / "AzeoPlantVirtualController"
CLASS_MODULES = {
    "4d9ec064-6608-5bf4-911e-976f74a582d5": {
        "AIC-0103", "LIC-0101", "PIC-0105", "TIC-0102",
    },
    "63c63eb2-9b7f-5bca-966d-02d7097a0ea6": {
        "MC-CT011A", "MC-CT011B",
    },
    "e21053f6-7d77-50c0-8666-07f9538630d2": {
        "MC-P011A", "MC-P011B",
    },
    "9fc5a2df-cea9-57c8-ac57-29a3111cb371": {"CT1-PERF"},
}
OUTPUT_OWNERS = {
    "SC-0102": "TIC-0102",
    "SC-0101": "PIC-0105",
    "LCV-0101": "LIC-0101",
    "FCV-0102": "AIC-0103",
    "XY-CT011A-STR": "MC-CT011A",
    "XY-CT011A-STP": "MC-CT011A",
    "XY-CT011B-STR": "MC-CT011B",
    "XY-CT011B-STP": "MC-CT011B",
    "XY-MOV0111A-OPN": "MC-P011A",
    "XY-MOV0111A-CLS": "MC-P011A",
    "XY-P011A-STR": "MC-P011A",
    "XY-P011A-STP": "MC-P011A",
    "XY-MOV0111B-OPN": "MC-P011B",
    "XY-MOV0111B-CLS": "MC-P011B",
    "XY-P011B-STR": "MC-P011B",
    "XY-P011B-STP": "MC-P011B",
}
PUBLISHED_BLOCK_TYPES = {"AI", "AO", "DI", "DO", "PID", "DEVCTL", "VLVCTL"}


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _module(name: str) -> dict:
    return _read(PROJECT / "control" / f"{name}.json")


def _wires(document: dict) -> set[tuple[str, str, str, str]]:
    return {
        (
            str(wire["src_block_id"]), str(wire["src_terminal"]),
            str(wire["dst_block_id"]), str(wire["dst_terminal"]),
        )
        for wire in document["wires"]
    }


def test_ct1_instances_are_current_compilable_class_members() -> None:
    library = ModuleClassLibrary(PROJECT / "_module_classes")

    assert {definition.id for definition in library.list()} == set(CLASS_MODULES)
    for definition_id, names in CLASS_MODULES.items():
        definition = library.get(definition_id)
        assert definition.revision == 1
        for name in names:
            document = _module(name)
            status = analyze_instance(document, library)
            graph, _comments = graph_from_document(document, strict=True)

            assert status.linked and status.state == "current"
            assert status.definition_id == definition_id
            assert not status.deviations
            assert graph.name == name
            assert len(compile_strategy(graph).exec_order) == len(graph.blocks)


def test_ct1_public_block_identities_do_not_collide() -> None:
    project = _read(PROJECT / "_project.json")
    area = project["areas"][0]
    all_documents = [
        _read(PROJECT / relative)
        for relative in [*area["strategies"], *area.get("sfc_modules", [])]
    ]
    counts = Counter(
        str(block.get("instance_name") or "").casefold()
        for document in all_documents
        for block in document.get("blocks", [])
        if block.get("block_type") in PUBLISHED_BLOCK_TYPES
    )
    class_names = {
        str(block["instance_name"]).casefold()
        for names in CLASS_MODULES.values()
        for name in names
        for block in _module(name)["blocks"]
        if block.get("block_type") in PUBLISHED_BLOCK_TYPES
    }

    assert class_names
    assert {name: counts[name] for name in class_names} == {
        name: 1 for name in class_names
    }


def test_ct1_commands_have_one_strategy_owner_and_safe_download_modes() -> None:
    project = _read(PROJECT / "_project.json")
    routes = project["areas"][0]["virtual_io"]["output_ownership"]["routes"]

    actual_outputs: dict[str, tuple[str, dict]] = {}
    for names in CLASS_MODULES.values():
        for name in names:
            for block in _module(name)["blocks"]:
                if block.get("block_type") not in {"AO", "DO"}:
                    continue
                tag = str(block["config"]["tag"])
                actual_outputs[tag] = (name, block["config"])

    assert set(actual_outputs) == set(OUTPUT_OWNERS)
    for tag, expected_module in OUTPUT_OWNERS.items():
        module, config = actual_outputs[tag]
        assert module == expected_module
        assert config["mode"] == "MAN"
        assert config["normal_mode"] == "CAS"
        assert routes[tag] == {
            "owner": "strategy",
            "reference": f"control/{expected_module}.json",
        }


def test_fan_cells_stage_with_drive_and_basin_protection() -> None:
    for name in ("MC-CT011A", "MC-CT011B"):
        document = _module(name)
        blocks = {block["id"]: block for block in document["blocks"]}
        wires = _wires(document)

        assert blocks["b011"]["block_type"] == "DEVCTL"
        assert ("b003", "OUT", "b011", "CAS_IN_D") in wires
        assert ("b008", "OUT_D", "b011", "PERMISSIVE_D") in wires
        assert ("b008", "OUT_D", "b011", "INTERLOCK") in wires
        assert ("b009", "OUT", "b011", "SHUTDOWN_D") in wires
        assert ("b010", "OUT", "b011", "RUN_FB") in wires


def test_pump_modules_prove_the_discharge_valve_before_start() -> None:
    for name in ("MC-P011A", "MC-P011B"):
        document = _module(name)
        blocks = {block["id"]: block for block in document["blocks"]}
        wires = _wires(document)

        assert blocks["b016"]["block_type"] == "VLVCTL"
        assert blocks["b027"]["block_type"] == "DEVCTL"
        assert ("b006", "OUT_D", "b016", "OPEN_CMD") in wires
        assert ("b007", "OUT", "b024", "IN_D4") in wires
        assert ("b006", "OUT_D", "b027", "CAS_IN_D") in wires
        assert ("b024", "OUT_D", "b027", "PERMISSIVE_D") in wires
        assert ("b025", "OUT", "b027", "SHUTDOWN_D") in wires
        assert ("b026", "OUT", "b027", "RUN_FB") in wires


def test_tower_performance_module_calculates_range_approach_and_duty() -> None:
    document = _module("CT1-PERF")
    blocks = {block["id"]: block for block in document["blocks"]}
    wires = _wires(document)

    assert blocks["b008"]["block_type"] == "SUB"
    assert blocks["b009"]["block_type"] == "SUB"
    assert blocks["b010"]["block_type"] == "MULTIPLIER"
    assert blocks["b010"]["config"]["GAIN"] == 0.001158
    assert ("b002", "OUT", "b008", "IN1") in wires
    assert ("b001", "OUT", "b008", "IN2") in wires
    assert ("b001", "OUT", "b009", "IN1") in wires
    assert ("b003", "OUT", "b009", "IN2") in wires
    assert ("b004", "OUT", "b010", "IN1") in wires
    assert ("b008", "OUT", "b010", "IN2") in wires
