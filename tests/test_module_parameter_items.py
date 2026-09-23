from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Standalone invocation is part of this repository's test contract.
# ruff: noqa: E402
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from azeo_control_trainer.core.datastore.shared_data_store import SharedDataStore
from azeo_control_trainer.core.strategy.blocks.composite_blocks import CompositeBlock
from azeo_control_trainer.core.strategy.engine.bridge import DataBridge
from azeo_control_trainer.core.strategy.engine.compiler import CompileError, compile_strategy
from azeo_control_trainer.core.strategy.engine.runtime import StrategyRuntime
from azeo_control_trainer.core.strategy.model.block_registry import registry
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
from azeo_control_trainer.core.strategy.model.terminal import (
    DataType,
    LimitStatus,
    Quality,
)
from azeo_control_trainer.core.strategy.serialization.strategy_io import load_strategy


KINDS = {
    "INPUT_PARAMETER": ("input", True),
    "OUTPUT_PARAMETER": ("output", False),
    "INTERNAL_READ_PARAMETER": ("internal_read", True),
    "INTERNAL_WRITE_PARAMETER": ("internal_write", False),
}


def _item(block_type: str, name: str, *, data_type="FLOAT", value="0.0"):
    block = registry.create(block_type)
    block.config.params = {
        "parameter_name": name,
        "data_type": data_type,
        "default_value": value,
    }
    block._apply_config()
    return block


def test_four_special_items_have_signal_direction_and_owned_declarations():
    graph = StrategyGraph("PARAM_TEST")

    for block_type, (access, source) in KINDS.items():
        block = _item(block_type, block_type)
        graph.add_block(block)
        assert set(block.outputs) == ({"VALUE"} if source else set())
        assert set(block.inputs) == (set() if source else {"VALUE"})
        spec = graph.module_parameters()[block_type]
        assert spec["access"] == access
        assert spec["item_id"] == block.id
        assert block.counts_as_algorithm is False

    assert not graph.validate()


@pytest.mark.parametrize(
    ("data_type", "configured", "expected"),
    [
        ("FLOAT", "12.5", 12.5),
        ("BOOL", "true", True),
        ("INT", "12", 12),
        ("STRING", "batch-a", "batch-a"),
        ("ENUM", "3", 3),
    ],
)
def test_internal_read_is_the_typed_internal_constant(
        data_type, configured, expected):
    graph = StrategyGraph("CONSTANT_TEST")
    block = _item(
        "INTERNAL_READ_PARAMETER", "FIXED_VALUE",
        data_type=data_type, value=configured,
    )
    graph.add_block(block)
    block._module_graph = graph
    block.execute(0.1)

    terminal = block.outputs["VALUE"]
    assert terminal.data_type is DataType[data_type]
    assert terminal.value == expected
    assert terminal.status is Quality.GOOD


def test_public_input_and_output_execute_through_the_module_runtime():
    graph = StrategyGraph("PUBLIC_IO")
    source = _item("INPUT_PARAMETER", "COMMAND")
    sink = _item("OUTPUT_PARAMETER", "RESULT")
    graph.add_block(source)
    graph.add_block(sink)
    graph.add_wire(source.id, "VALUE", sink.id, "VALUE")

    runtime = StrategyRuntime()
    runtime.load(compile_strategy(graph), DataBridge(SharedDataStore()))
    assert runtime.go_online()
    graph.module_parameters()["COMMAND"]["value"] = 42.5
    graph.module_parameters()["COMMAND"]["status"] = "UNCERTAIN"
    graph.module_parameters()["COMMAND"]["limit"] = "HIGH_LIMITED"

    # Top-level modules intentionally advance one wire boundary per scan.
    runtime.execute_scan(0.1)
    runtime.execute_scan(0.1)

    result = graph.module_parameters()["RESULT"]
    assert result["value"] == 42.5
    assert result["status"] == "UNCERTAIN"
    assert result["limit"] == "HIGH_LIMITED"
    assert source._exec_count == 0
    assert sink._exec_count == 0


def test_unconnected_sink_holds_its_declared_value():
    graph = StrategyGraph("HOLD")
    sink = _item("INTERNAL_WRITE_PARAMETER", "HELD", value="9.5")
    graph.add_block(sink)
    sink._module_graph = graph
    sink.execute(0.1)
    assert graph.module_parameters()["HELD"]["value"] == 9.5


def test_public_parameters_become_composite_pins_with_signal_state():
    inner = StrategyGraph("INNER")
    source = _item("INPUT_PARAMETER", "FEED")
    sink = _item("OUTPUT_PARAMETER", "PRODUCT")
    inner.add_block(source)
    inner.add_block(sink)
    inner.add_wire(source.id, "VALUE", sink.id, "VALUE")

    composite = CompositeBlock("UNIT")
    composite.inner_graph = inner
    composite.compile_inner()
    assert set(composite.inputs) == {"FEED"}
    assert set(composite.outputs) == {"PRODUCT"}

    composite.inputs["FEED"].value = 17.25
    composite.inputs["FEED"].status = Quality.UNCERTAIN
    composite.inputs["FEED"].limit = LimitStatus.LOW_LIMITED
    composite.execute(0.1)

    assert composite.outputs["PRODUCT"].value == 17.25
    assert composite.outputs["PRODUCT"].status is Quality.UNCERTAIN
    assert composite.outputs["PRODUCT"].limit is LimitStatus.LOW_LIMITED


def test_item_rename_and_remove_keep_graph_declaration_owned():
    graph = StrategyGraph("OWNERSHIP")
    block = _item("INTERNAL_READ_PARAMETER", "OLD", value="7")
    graph.add_block(block)

    block.config.params["parameter_name"] = "NEW"
    block._apply_config()
    assert "OLD" not in graph.module_parameters()
    assert graph.module_parameters()["NEW"]["item_id"] == block.id

    graph.remove_block(block.id)
    assert "parameters" not in graph.extra


def test_authoring_copy_gets_a_unique_parameter_record():
    graph = StrategyGraph("COPY")
    original = _item("INTERNAL_READ_PARAMETER", "BIAS", value="2")
    duplicate = _item("INTERNAL_READ_PARAMETER", "BIAS", value="2")
    graph.add_block(original)
    graph.add_block(duplicate)

    assert duplicate.parameter_name == "BIAS_2"
    assert set(graph.module_parameters()) == {"BIAS", "BIAS_2"}
    assert not graph.validate()


def test_loader_does_not_repair_missing_parameter_declaration(tmp_path):
    block = _item("INPUT_PARAMETER", "MISSING")
    data = {
        "name": "BROKEN",
        "blocks": [block.to_dict()],
        "wires": [],
    }
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    restored, _ = load_strategy(path, remember=False)
    assert any("missing module parameter 'MISSING'" in error
               for error in restored.validate())
    with pytest.raises(CompileError):
        compile_strategy(restored)


def test_parameter_item_and_metadata_round_trip_without_type_loss(tmp_path):
    graph = StrategyGraph("ROUNDTRIP")
    block = _item(
        "INTERNAL_READ_PARAMETER", "BATCH_ID",
        data_type="STRING", value="A-104",
    )
    block.config.params.update(
        description="Current batch", units="code",
        write_access="not_writeable",
    )
    graph.add_block(block)
    path = tmp_path / "roundtrip.json"
    path.write_text(json.dumps(graph.to_dict()), encoding="utf-8")

    restored, _ = load_strategy(path, remember=False)
    restored_block = next(iter(restored.blocks.values()))
    spec = restored.module_parameters()["BATCH_ID"]
    assert restored_block.block_type == "INTERNAL_READ_PARAMETER"
    assert restored_block.outputs["VALUE"].data_type is DataType.STRING
    assert spec["value"] == "A-104"
    assert spec["data_type"] == "STRING"
    assert spec["description"] == "Current batch"
    assert spec["units"] == "code"
    assert not restored.validate()


def test_write_permission_is_independent_of_connection_direction():
    graph = StrategyGraph("PERMISSION")
    graph.set_module_parameter(
        "READABLE", 1.0, access="internal_read",
        write_access="writeable",
    )
    graph.set_module_parameter(
        "SINK", 2.0, access="internal_write",
        write_access="not_writeable",
    )
    assert graph.module_parameter_is_writeable(
        graph.module_parameters()["READABLE"])
    assert not graph.module_parameter_is_writeable(
        graph.module_parameters()["SINK"])
