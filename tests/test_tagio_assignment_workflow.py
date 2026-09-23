"""TAGIO assignment is one lossless engineering operation, not delete/add."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QComboBox

from azeo_control_trainer.core.strategy import blocks as _blocks  # noqa: F401
from azeo_control_trainer.core.strategy.blocks.dv_tag_io_blocks import (
    TagAnalogInputBlock,
    TagAnalogOutputBlock,
    TagDiscreteInputBlock,
    TagDiscreteOutputBlock,
    TagIoBlock,
)
from azeo_control_trainer.core.strategy.engine.compiler import compile_strategy
from azeo_control_trainer.core.strategy.model.block_registry import registry
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
from azeo_control_trainer.core.strategy.serialization.strategy_io import (
    load_strategy,
    save_strategy,
)
from azeo_control_trainer.azeo_control_designer.canvas.strategy_scene import (
    StrategyScene,
)
from azeo_control_trainer.azeo_control_designer.panels.properties_panel import (
    PropertiesPanel,
)


def _configured_placeholder() -> TagIoBlock:
    block = TagIoBlock("PLC_MON")
    block.id = "tagio-fixed-id"
    block.x = 17.0
    block.y = 29.0
    block.scan_rate = 4
    block.bypassed = True
    block._ui_width = 211.0
    block._ui_height = 133.0
    block.config.params = {
        "tag": "PLC.DO",
        "TAG_SEPARATOR": ".",
        "SOURCE_VAL_FDBK": "PLC.DO.ActualFeedback",
        "TAG_DEFINITION": "UNASSIGNED",
    }
    return block


def _summer(name: str = "DEST"):
    block = registry.create("SUMMER", name)
    assert block is not None
    return block


def test_graph_replacement_preserves_identity_wires_and_round_trip(tmp_path):
    graph = StrategyGraph("TAGIO-ASSIGNMENT")
    placeholder = _configured_placeholder()
    destination = _summer()
    graph.add_block(placeholder)
    graph.add_block(destination)
    wire_id = graph.add_wire(placeholder.id, "OUT", destination.id, "IN1")
    assert wire_id is not None
    graph.wires[wire_id].route_points = [[20.0, 30.0], [80.0, 30.0]]

    converted = placeholder.convert_assignment(
        "TagDO_MONITOR",
        config={
            **placeholder.config.params,
            "TAG_DEFINITION": "TagDO_MONITOR",
        },
    )
    previous = graph.replace_block(
        placeholder.id,
        converted,
        output_terminal_map={"OUT": "OUT_D"},
    )

    assert previous is placeholder
    assert isinstance(graph.blocks[placeholder.id], TagDiscreteOutputBlock)
    assert converted.id == "tagio-fixed-id"
    assert converted.instance_name == "PLC_MON"
    assert (converted.x, converted.y) == (17.0, 29.0)
    assert converted.scan_rate == 4
    assert converted.bypassed is True
    assert (converted._ui_width, converted._ui_height) == (211.0, 133.0)
    assert converted.config.params["tag"] == "PLC.DO"
    assert converted.config.params["TAG_SEPARATOR"] == "."
    assert converted.config.params["SOURCE_VAL_FDBK"] == (
        "PLC.DO.ActualFeedback"
    )
    assert "TAG_DEFINITION" not in converted.config.params
    assert graph.wires[wire_id].src_terminal == "OUT_D"
    assert graph.wires[wire_id].route_points == [
        [20.0, 30.0],
        [80.0, 30.0],
    ]
    assert converted.outputs["OUT_D"].connected is True
    assert compile_strategy(graph).exec_order == [converted.id, destination.id]

    path = tmp_path / "tagio_assigned.json"
    save_strategy(graph, path=path)
    restored, comments = load_strategy(path, remember=False)
    assert comments == []
    restored_monitor = restored.blocks[converted.id]
    restored_wire = restored.wires[wire_id]
    assert isinstance(restored_monitor, TagDiscreteOutputBlock)
    assert restored_monitor.instance_name == "PLC_MON"
    assert restored_wire.src_terminal == "OUT_D"
    assert restored_wire.route_points == [[20.0, 30.0], [80.0, 30.0]]
    compile_strategy(restored)


def test_graph_replacement_refusal_is_atomic():
    graph = StrategyGraph("TAGIO-ATOMIC")
    placeholder = _configured_placeholder()
    destination = _summer()
    graph.add_block(placeholder)
    graph.add_block(destination)
    wire_id = graph.add_wire(placeholder.id, "OUT", destination.id, "IN1")
    assert wire_id is not None

    converted = placeholder.convert_assignment("TagDO_MONITOR")
    try:
        graph.replace_block(
            placeholder.id,
            converted,
            output_terminal_map={"OUT": "DOES_NOT_EXIST"},
        )
    except ValueError as exc:
        assert "does not exist" in str(exc)
    else:  # pragma: no cover - the assertion above is the contract
        raise AssertionError("invalid replacement unexpectedly succeeded")

    assert graph.blocks[placeholder.id] is placeholder
    assert graph.wires[wire_id].src_terminal == "OUT"
    assert placeholder.outputs["OUT"].connected is True


@pytest.mark.parametrize(
    ("definition", "expected_type", "expected_terminal"),
    (
        ("TagAI_MONITOR", TagAnalogInputBlock, "OUT"),
        ("TagAO_MONITOR", TagAnalogOutputBlock, "OUT"),
        ("TagDI_MONITOR", TagDiscreteInputBlock, "OUT_D"),
        ("TagDO_MONITOR", TagDiscreteOutputBlock, "OUT_D"),
    ),
)
def test_scene_assigns_every_tagio_definition_and_remaps_out(
    definition,
    expected_type,
    expected_terminal,
):
    app = QApplication.instance() or QApplication([])
    scene = StrategyScene()
    placeholder = _configured_placeholder()
    destination = _summer()
    scene.add_block(placeholder)
    scene.add_block(destination)
    wire_item = scene.add_wire(
        placeholder.id,
        "OUT",
        destination.id,
        "IN1",
    )
    assert wire_item is not None

    assigned = scene.assign_tag_io(
        placeholder.id,
        definition,
        config={**placeholder.config.params, "TAG_DEFINITION": definition},
    )
    assert isinstance(assigned, expected_type)
    assert scene.graph.wires[wire_item.wire.id].src_terminal == expected_terminal
    compile_strategy(scene.graph)

    scene.deleteLater()
    app.processEvents()


def test_properties_assignment_is_one_undoable_scene_conversion():
    app = QApplication.instance() or QApplication([])
    scene = StrategyScene()
    placeholder = _configured_placeholder()
    destination = _summer()
    placeholder_item = scene.add_block(placeholder)
    scene.add_block(destination)
    wire_item = scene.add_wire(
        placeholder.id,
        "OUT",
        destination.id,
        "IN1",
    )
    assert wire_item is not None
    wire_id = wire_item.wire.id
    placeholder_item.setSelected(True)

    panel = PropertiesPanel()
    panel._scene = scene
    panel.set_block(placeholder)
    selector = panel._widgets["TAG_DEFINITION"]
    assert isinstance(selector, QComboBox)
    target_index = selector.findData("TagDO_MONITOR")
    assert target_index >= 0
    selector.setCurrentIndex(target_index)
    panel.flush_pending()

    assigned = scene.graph.blocks[placeholder.id]
    assert isinstance(assigned, TagDiscreteOutputBlock)
    assert panel._block is assigned
    assert scene.get_block_item(placeholder.id).block is assigned
    assert scene.graph.wires[wire_id].src_terminal == "OUT_D"
    assert wire_id in scene._wire_items

    scene.undo_stack.undo()
    assert scene.graph.blocks[placeholder.id] is placeholder
    assert scene.graph.wires[wire_id].src_terminal == "OUT"
    assert wire_id in scene._wire_items

    scene.undo_stack.redo()
    assert scene.graph.blocks[placeholder.id] is assigned
    assert scene.graph.wires[wire_id].src_terminal == "OUT_D"
    assert wire_id in scene._wire_items

    panel.deleteLater()
    scene.deleteLater()
    app.processEvents()


def test_properties_assignment_is_refused_while_on_scan():
    app = QApplication.instance() or QApplication([])
    scene = StrategyScene()
    placeholder = _configured_placeholder()
    scene.add_block(placeholder)
    scene.set_structure_locked(True)
    refused: list[str] = []
    scene.structureEditBlocked.connect(refused.append)

    panel = PropertiesPanel()
    panel._scene = scene
    panel.set_block(placeholder)
    selector = panel._widgets["TAG_DEFINITION"]
    selector.setCurrentIndex(selector.findData("TagAI_MONITOR"))
    panel.flush_pending()

    assert scene.graph.blocks[placeholder.id] is placeholder
    assert placeholder.config.params["TAG_DEFINITION"] == "UNASSIGNED"
    assert refused == ["assign a TAGIO control tag"]
    assert panel._block is placeholder

    panel.deleteLater()
    scene.deleteLater()
    app.processEvents()
