"""Regression coverage for the ten Control Designer productivity workflows."""
from __future__ import annotations

import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import azeo_control_trainer.core.strategy.blocks  # noqa: E402,F401
from azeo_control_trainer.azeo_control_designer.canvas.strategy_scene import (  # noqa: E402
    StrategyScene,
)
from azeo_control_trainer.azeo_control_designer.dialogs.command_palette import (  # noqa: E402
    fuzzy_score,
)
from azeo_control_trainer.azeo_control_designer.dialogs.composite_ports import (  # noqa: E402
    CompositePortsDialog,
    exposable_selected_terminals,
)
from azeo_control_trainer.azeo_control_designer.dialogs.connection_target import (  # noqa: E402
    reconnect_targets,
)
from azeo_control_trainer.azeo_control_designer.dialogs.control_loop_wizard import (  # noqa: E402
    LoopWizardSettings,
    generate_control_loop,
)
from azeo_control_trainer.azeo_control_designer.dialogs.quick_insert import (  # noqa: E402
    compatible_insert_candidates,
    compatible_wire_insert_candidates,
)
from azeo_control_trainer.azeo_control_designer.panels.problems_panel import (  # noqa: E402
    apply_quick_fix,
    quick_fixes_for,
)
from azeo_control_trainer.azeo_control_designer.panels.properties_panel import (  # noqa: E402
    PropertiesPanel,
)
from azeo_control_trainer.azeo_control_designer.widgets.diagram_navigator import (  # noqa: E402
    DiagramNavigatorDialog,
)
from azeo_control_trainer.azeo_control_designer.canvas.strategy_view import (  # noqa: E402
    StrategyView,
)
from azeo_control_trainer.core.strategy.blocks.composite_blocks import (  # noqa: E402
    CompositeBlock,
)
from azeo_control_trainer.core.strategy.blocks.pid_block import PIDBlock  # noqa: E402
from azeo_control_trainer.core.strategy.blocks.signal_blocks import BiasBlock  # noqa: E402
from azeo_control_trainer.core.strategy.engine.validator import (  # noqa: E402
    validate_strategy,
)
from azeo_control_trainer.core.strategy.engine.compiler import compile_strategy  # noqa: E402


def _app():
    return QApplication.instance() or QApplication([])


def _bias_scene(count: int = 2):
    _app()
    scene = StrategyScene()
    blocks = []
    for index in range(count):
        block = BiasBlock(f"BIAS_{index + 1}")
        blocks.append(block)
        scene.add_block(block, QPointF(index * 220, 100))
    scene.undo_stack.clear()
    return scene, blocks


def test_smart_wiring_highlights_snaps_and_connects():
    scene, (source, destination) = _bias_scene()
    source_pin = scene.get_block_item(source.id).get_terminal_item("out", "OUT")
    destination_pin = scene.get_block_item(destination.id).get_terminal_item("in", "IN")

    assert scene.start_wiring(source_pin)
    assert destination_pin._is_highlighted
    assert destination_pin._highlight_compatible
    assert "Type: FLOAT" in destination_pin.toolTip()
    scene.update_wiring(destination_pin.get_scene_center() + QPointF(8, 5))
    assert scene.wiring_snap_target is destination_pin
    scene.finish_wiring(None)

    assert len(scene.graph.wires) == 1
    assert not destination_pin._is_highlighted


def test_quick_insert_filters_by_type_and_is_one_undo_operation():
    scene, (source, _destination) = _bias_scene()
    source_pin = scene.get_block_item(source.id).get_terminal_item("out", "OUT")
    candidates = compatible_insert_candidates(source_pin.terminal)
    candidate = next(item for item in candidates if item.block_type == "BIAS")

    inserted = scene.quick_insert_from_terminal(source_pin, candidate, QPointF(250, 250))

    assert inserted is not None
    assert len(scene.graph.blocks) == 3
    assert len(scene.graph.wires) == 1
    scene.undo_stack.undo()
    assert len(scene.graph.blocks) == 2
    assert not scene.graph.wires


def test_live_problem_quick_fix_is_undoable():
    _app()
    scene = StrategyScene()
    pid = PIDBlock("PID_BAD_LIMITS")
    pid.config.params.update(out_lo=50.0, out_hi=10.0)
    scene.add_block(pid, QPointF())
    scene.undo_stack.clear()
    finding = next(item for item in validate_strategy(scene.graph)
                   if "invalid output limits" in item.message)
    fix = next(item for item in quick_fixes_for(scene.graph, finding)
               if item.key == "normalize_pid_limits")

    assert apply_quick_fix(scene, finding, fix)
    assert pid.config.params["out_lo"] < pid.config.params["out_hi"]
    scene.undo_stack.undo()
    assert pid.config.params["out_lo"] == 50.0
    assert pid.config.params["out_hi"] == 10.0


def test_command_palette_fuzzy_matching_is_predictable():
    assert fuzzy_score("gclw", "guided control loop wizard") is not None
    assert fuzzy_score("pid", "insert pid controller") is not None
    assert fuzzy_score("xyz", "fit diagram") is None


def test_multi_block_property_edit_and_undo():
    _app()
    scene = StrategyScene()
    first = PIDBlock("PID_1")
    second = PIDBlock("PID_2")
    scene.add_block(first, QPointF())
    scene.add_block(second, QPointF(250, 0))
    scene.undo_stack.clear()
    panel = PropertiesPanel()
    panel._scene = scene
    panel.set_blocks([first, second])

    panel._apply_multi_value("out_hi", float, "75")

    assert first.config.params["out_hi"] == 75.0
    assert second.config.params["out_hi"] == 75.0
    scene.undo_stack.undo()
    assert first.config.params.get("out_hi") != 75.0
    assert second.config.params.get("out_hi") != 75.0


def test_composite_port_editor_exposes_selected_pin():
    _app()
    parent_scene = StrategyScene()
    composite = CompositeBlock("PACKAGE")
    parent_scene.add_block(composite, QPointF())
    inner_scene = StrategyScene()
    inner_scene.load_graph(composite.inner_graph)
    block = BiasBlock("CALC")
    inner_scene.add_block(block, QPointF())
    inner_scene.get_block_item(block.id).setSelected(True)
    available = exposable_selected_terminals(inner_scene)
    output_index = next(index for index, item in enumerate(available)
                        if item.direction == "output")
    dialog = CompositePortsDialog(
        composite, inner_scene, parent_scene)
    dialog.exposable.setCurrentIndex(output_index)

    dialog._expose()
    composite._rebuild_terminals()

    assert composite.outputs
    assert any(block.block_type == "OUTPORT"
               for block in inner_scene.graph.blocks.values())
    assert len(inner_scene.graph.wires) == 1


def test_wire_refactoring_insert_reconnect_and_remove_heal():
    scene, (source, destination) = _bias_scene()
    wire_item = scene.add_wire(source.id, "OUT", destination.id, "IN")
    scene.undo_stack.clear()
    candidates = compatible_wire_insert_candidates(scene.graph, wire_item.wire)
    candidate = next(item for item in candidates if item.block_type == "BIAS")

    inserted = scene.insert_block_into_wire(
        wire_item.wire.id, candidate, QPointF(200, 240))
    assert inserted is not None
    assert len(scene.graph.wires) == 2
    assert scene.remove_block_and_heal(inserted.block.id)
    assert len(scene.graph.wires) == 1

    third = BiasBlock("NEW_SOURCE")
    scene.add_block(third, QPointF(-220, 200))
    wire = next(iter(scene.graph.wires.values()))
    target = next(item for item in reconnect_targets(scene.graph, wire, "source")
                  if item.block_id == third.id)
    assert scene.reconnect_wire(wire.id, "source", target)
    rewired = next(iter(scene.graph.wires.values()))
    assert rewired.src_block_id == third.id


def test_guided_loop_wizard_generates_complete_pattern_as_one_undo():
    _app()
    scene = StrategyScene()
    settings = LoopWizardSettings(
        "override", "PIC_100", "PT_100", "FT_100", "FV_100")

    added = generate_control_loop(scene, settings, QPointF(-800, -300))

    assert len(added) == 7
    assert {block.block_type for block in scene.graph.blocks.values()} >= {
        "AI", "PID", "MIN_SELECT", "SCALER", "AO",
    }
    assert len(scene.graph.wires) == 9
    scene.undo_stack.undo()
    assert not scene.graph.blocks
    assert not scene.graph.wires


def test_every_guided_loop_pattern_compiles():
    for pattern in ("single", "cascade", "override"):
        _app()
        scene = StrategyScene()
        settings = LoopWizardSettings(
            pattern, f"LOOP_{pattern}", "PV_1", "PV_2", "MV_1")
        assert generate_control_loop(scene, settings, QPointF(-800, -300))
        compiled = compile_strategy(scene.graph)
        assert len(compiled.exec_order) == len(scene.graph.blocks)


def test_diagram_navigator_bookmark_and_block_navigation():
    scene, blocks = _bias_scene(3)
    view = StrategyView(scene)
    navigator = DiagramNavigatorDialog(scene, view)
    navigator.add_current_bookmark("Feed section")
    assert navigator.bookmarks.count() == 1
    navigator.step_block(1)
    assert any(scene.get_block_item(block.id).isSelected() for block in blocks)
