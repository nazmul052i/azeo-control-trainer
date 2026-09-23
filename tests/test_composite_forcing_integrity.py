"""Integrity contracts for online forcing and composite execution."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from azeo_control_trainer.core.strategy.blocks.composite_blocks import (
    CompositeBlock,
    InportBlock,
    OutportBlock,
    collapse_to_composite,
)
from azeo_control_trainer.core.strategy.engine.compiler import (
    MAX_COMPOSITE_DEPTH,
    CompileError,
    compile_strategy,
)
from azeo_control_trainer.core.strategy.engine.runtime import StrategyRuntime
from azeo_control_trainer.core.strategy.model.block_base import (
    BlockCategory,
    FunctionBlock,
)
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
from azeo_control_trainer.core.strategy.model.terminal import (
    DataType,
    LimitStatus,
    Quality,
)
from azeo_control_trainer.azeo_control_designer.dialogs.force_value_dialog import (
    ForceValueDialog,
)


class _BoolPass(FunctionBlock):
    block_type = "TEST_BOOL_PASS"
    category = BlockCategory.LOGIC

    def _define_terminals(self):
        self.add_input("IN", DataType.BOOL, False, "Boolean input")
        self.add_output("OUT", DataType.BOOL, False, "Boolean output")

    def execute(self, dt: float):
        source = self.inputs["IN"]
        destination = self.outputs["OUT"]
        destination.value = source.value
        destination.status = source.status
        destination.limit = source.limit


class _CountingPass(FunctionBlock):
    block_type = "TEST_COUNTING_PASS"
    category = BlockCategory.SIGNAL

    def __init__(self, instance_name: str = ""):
        self.executions = 0
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("IN", DataType.FLOAT, 0.0)
        self.add_output("OUT", DataType.FLOAT, 0.0)

    def execute(self, dt: float):
        self.executions += 1
        source = self.inputs["IN"]
        destination = self.outputs["OUT"]
        destination.value = source.value
        destination.status = source.status
        destination.limit = source.limit


def _configured_inport(name: str, data_type: DataType) -> InportBlock:
    block = InportBlock(name)
    block.config.params.update({
        "port_name": name,
        "data_type": data_type.value,
    })
    block._apply_config()
    return block


def _configured_outport(name: str, data_type: DataType) -> OutportBlock:
    block = OutportBlock(name)
    block.config.params.update({
        "port_name": name,
        "data_type": data_type.value,
    })
    block._apply_config()
    return block


def test_forcing_is_input_only_in_model_and_dialog():
    block = _BoolPass("PASS")

    assert block.force_terminal("OUT", True) is False
    assert block.force_terminal("IN", True) is True
    assert block.forced_terminals() == ["IN"]
    assert block.is_forced("OUT") is False
    assert block.to_dict()["forced_terminals"] == {"IN": True}

    app = QApplication.instance() or QApplication([])
    dialog = ForceValueDialog(block)
    assert dialog._combo.count() == 1
    assert dialog._combo.itemData(0) == ("in", "IN")

    # A legacy/directly-mutated output force is never advertised and Release
    # All scrubs it instead of leaving a hidden override behind.
    block.outputs["OUT"].forced = True
    dialog._refresh_active()
    assert dialog._table.rowCount() == 1
    assert "OUT" not in block.to_dict()["forced_terminals"]
    assert block.release_all_forces() == 2
    assert block.outputs["OUT"].forced is False
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


def test_collapse_uses_unique_typed_boundary_ports():
    graph = StrategyGraph("collapse")
    sources = [_BoolPass(f"SRC{i}") for i in range(2)]
    selected = [_BoolPass(f"INNER{i}") for i in range(2)]
    sinks = [_BoolPass(f"SINK{i}") for i in range(2)]
    for block in (*sources, *selected, *sinks):
        graph.add_block(block)

    for source, inner, sink in zip(sources, selected, sinks):
        assert graph.add_wire(source.id, "OUT", inner.id, "IN")
        assert graph.add_wire(inner.id, "OUT", sink.id, "IN")

    composite = collapse_to_composite(
        graph, [block.id for block in selected], "PAIR"
    )
    assert composite is not None
    assert set(composite.inputs) == {"IN", "IN_1"}
    assert set(composite.outputs) == {"OUT", "OUT_1"}
    assert all(t.data_type is DataType.BOOL for t in composite.inputs.values())
    assert all(t.data_type is DataType.BOOL for t in composite.outputs.values())

    incoming = [
        wire for wire in graph.wires.values()
        if wire.dst_block_id == composite.id
    ]
    outgoing = [
        wire for wire in graph.wires.values()
        if wire.src_block_id == composite.id
    ]
    assert {wire.dst_terminal for wire in incoming} == {"IN", "IN_1"}
    assert {wire.src_terminal for wire in outgoing} == {"OUT", "OUT_1"}
    assert all(terminal.connected for terminal in composite.inputs.values())
    assert all(terminal.connected for terminal in composite.outputs.values())

    inports = [
        block for block in composite.inner_graph.blocks.values()
        if isinstance(block, InportBlock)
    ]
    outports = [
        block for block in composite.inner_graph.blocks.values()
        if isinstance(block, OutportBlock)
    ]
    assert all(block.outputs["OUT"].data_type is DataType.BOOL for block in inports)
    assert all(block.inputs["IN"].data_type is DataType.BOOL for block in outports)


@pytest.mark.parametrize(
    ("data_type", "default"),
    [
        (DataType.FLOAT, 0.0),
        (DataType.BOOL, False),
        (DataType.INT, 0),
        (DataType.STRING, ""),
        (DataType.ENUM, 0),
    ],
)
def test_boundary_types_survive_composite_serialization(data_type, default):
    inner = StrategyGraph("typed")
    inner.add_block(_configured_inport("IN", data_type))
    inner.add_block(_configured_outport("OUT", data_type))
    composite = CompositeBlock("TYPED")
    composite.inner_graph = inner

    restored = CompositeBlock.from_dict(composite.to_dict())
    assert restored.inputs["IN"].data_type is data_type
    assert restored.outputs["OUT"].data_type is data_type
    assert restored.inputs["IN"].default_value == default
    assert restored.outputs["OUT"].default_value == default
    restored_inport = next(
        block for block in restored.inner_graph.blocks.values()
        if isinstance(block, InportBlock)
    )
    restored_outport = next(
        block for block in restored.inner_graph.blocks.values()
        if isinstance(block, OutportBlock)
    )
    assert restored_inport.outputs["OUT"].data_type is data_type
    assert restored_outport.inputs["IN"].data_type is data_type


def test_composite_carries_state_and_honors_inner_scan_rate_and_context():
    inner = StrategyGraph("inner")
    inport = _configured_inport("PV", DataType.FLOAT)
    probe = _CountingPass("PROBE")
    probe.scan_rate = 2
    outport = _configured_outport("OUT", DataType.FLOAT)
    for block in (inport, probe, outport):
        inner.add_block(block)
    assert inner.add_wire(inport.id, "OUT", probe.id, "IN")
    assert inner.add_wire(probe.id, "OUT", outport.id, "IN")

    composite = CompositeBlock("C1")
    composite.inner_graph = inner
    outer = StrategyGraph("outer")
    outer.add_block(composite)
    compile_strategy(outer)

    context = object()
    composite.runtime_context = context
    composite.inputs["PV"].value = 12.5
    composite.inputs["PV"].status = Quality.BAD
    composite.inputs["PV"].limit = LimitStatus.HIGH_LIMITED
    composite.execute(0.1)

    assert probe.executions == 1
    assert probe._module_graph is inner
    assert probe.runtime_context is context
    assert composite.outputs["OUT"].value == 12.5
    assert composite.outputs["OUT"].status is Quality.BAD
    assert composite.outputs["OUT"].limit is LimitStatus.HIGH_LIMITED

    composite.inputs["PV"].value = 20.0
    composite.inputs["PV"].status = Quality.GOOD
    composite.inputs["PV"].limit = LimitStatus.LOW_LIMITED
    composite.execute(0.1)
    assert probe.executions == 1
    assert composite.outputs["OUT"].value == 12.5
    assert composite.outputs["OUT"].status is Quality.BAD

    composite.execute(0.1)
    assert probe.executions == 2
    assert composite.outputs["OUT"].value == 20.0
    assert composite.outputs["OUT"].status is Quality.GOOD
    assert composite.outputs["OUT"].limit is LimitStatus.LOW_LIMITED


def test_inner_compile_error_aborts_outer_compile_and_online_fallback():
    inner = StrategyGraph("invalid inner")
    first = _BoolPass("FIRST")
    second = _BoolPass("SECOND")
    inner.add_block(first)
    inner.add_block(second)
    assert inner.add_wire(first.id, "OUT", second.id, "IN")
    assert inner.add_wire(second.id, "OUT", first.id, "IN")

    composite = CompositeBlock("BROKEN")
    composite.inner_graph = inner
    outer = StrategyGraph("outer")
    outer.add_block(composite)

    with pytest.raises(CompileError, match="BROKEN.*interior failed"):
        compile_strategy(outer)
    with pytest.raises(CompileError, match="Cycle detected"):
        StrategyRuntime._ensure_composites_compiled(outer)


def _nested_composites(levels: int) -> StrategyGraph:
    root = StrategyGraph("root")
    graph = root
    for level in range(1, levels + 1):
        composite = CompositeBlock(f"C{level}")
        graph.add_block(composite)
        child = StrategyGraph(f"level {level}")
        composite.inner_graph = child
        graph = child
    return root


def test_composite_nesting_limit_is_six():
    assert MAX_COMPOSITE_DEPTH == 6
    compile_strategy(_nested_composites(6))
    with pytest.raises(CompileError, match="nested 7 levels deep; maximum is 6"):
        compile_strategy(_nested_composites(7))
