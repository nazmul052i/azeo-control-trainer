from __future__ import annotations

import pytest
from PySide6.QtWidgets import QWidget

from azeo_control_trainer.azeo_operator_station.console import LiveStation
from azeo_control_trainer.core.hmi.history import ContinuousHistorian
from azeo_control_trainer.core.strategy.blocks.composite_blocks import (
    CompositeBlock,
    InportBlock,
    OutportBlock,
)
from azeo_control_trainer.core.strategy.blocks.io_blocks import AIBlock, AOBlock
from azeo_control_trainer.core.strategy.blocks.pid_block import PIDBlock
from azeo_control_trainer.core.strategy.blocks.signal_blocks import BiasBlock
from azeo_control_trainer.core.strategy.composites import CompositeLibrary
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
from azeo_control_trainer.azeo_control_designer.canvas.strategy_scene import (
    StrategyScene,
)
from azeo_control_trainer.core.presentation.menu_style import studio_menu


def _inner_graph(*, outputs=("OUT",)) -> StrategyGraph:
    graph = StrategyGraph("Reusable")
    inport = InportBlock("IN")
    inport.config.params["port_name"] = "IN"
    inport._apply_config()
    graph.add_block(inport)
    for name in outputs:
        outport = OutportBlock(name)
        outport.config.params["port_name"] = name
        outport._apply_config()
        graph.add_block(outport)
    return graph


def _connected_scene(definition):
    outer = StrategyGraph("UNIT-100")
    source = BiasBlock("SOURCE")
    composite = CompositeBlock.from_definition(definition, "SKID")
    sink = BiasBlock("SINK")
    for block in (source, composite, sink):
        outer.add_block(block)
    incoming = outer.add_wire(source.id, "OUT", composite.id, "IN")
    outgoing = outer.add_wire(composite.id, "OUT", sink.id, "IN")
    assert incoming and outgoing
    scene = StrategyScene()
    scene.load_graph(outer)
    return scene, composite, incoming, outgoing


def _marquee_grouping_scene():
    graph = StrategyGraph("GROUPING")
    source = BiasBlock("SOURCE")
    first = BiasBlock("FIRST")
    second = BiasBlock("SECOND")
    sink = BiasBlock("SINK")
    for index, block in enumerate((source, first, second, sink)):
        block.x = index * 180.0
        block.y = 100.0
        graph.add_block(block)
    incoming = graph.add_wire(source.id, "OUT", first.id, "IN")
    internal = graph.add_wire(first.id, "OUT", second.id, "IN")
    outgoing = graph.add_wire(second.id, "OUT", sink.id, "IN")
    assert incoming and internal and outgoing

    scene = StrategyScene()
    scene.load_graph(graph)
    scene.undo_stack.clear()
    scene.get_block_item(first.id).setSelected(True)
    scene.get_block_item(second.id).setSelected(True)
    return scene, (source, first, second, sink), {
        incoming, internal, outgoing,
    }


def test_marquee_selection_creates_selected_undoable_composite(qapp):
    scene, blocks, original_wire_ids = _marquee_grouping_scene()
    source, first, second, sink = blocks

    composite_id = scene.create_composite_from_selection("CALCULATION")

    assert composite_id is not None
    composite = scene.graph.blocks[composite_id]
    assert isinstance(composite, CompositeBlock)
    assert set(composite.inner_graph.blocks) >= {first.id, second.id}
    assert set(scene.graph.blocks) == {source.id, composite_id, sink.id}
    assert {wire.dst_block_id for wire in scene.graph.wires.values()} >= {
        composite_id, sink.id,
    }
    assert {wire.src_block_id for wire in scene.graph.wires.values()} >= {
        source.id, composite_id,
    }
    assert [item.block.id for item in scene.selectedItems()
            if hasattr(item, "block")] == [composite_id]
    assert scene.undo_stack.undoText().startswith("Group 2 Blocks")

    scene.undo_stack.undo()
    assert set(scene.graph.blocks) == {block.id for block in blocks}
    assert set(scene.graph.wires) == original_wire_ids

    scene.undo_stack.redo()
    assert set(scene.graph.blocks) == {source.id, composite_id, sink.id}
    assert isinstance(scene.graph.blocks[composite_id], CompositeBlock)
    scene.deleteLater()


def test_composite_selection_action_is_shared_and_disabled_online(
    qapp, monkeypatch,
):
    scene, _blocks, _wire_ids = _marquee_grouping_scene()
    invoked = []
    monkeypatch.setattr(
        scene,
        "create_composite_from_selection",
        lambda name=None: invoked.append(name),
    )
    menu = studio_menu()
    action = scene._add_create_composite_action(menu)

    assert action is not None
    assert action.text() == "Create Composite from Selection...  (2 blocks)"
    assert action.isEnabled()
    action.trigger()
    assert invoked == [None]

    scene.set_structure_locked(True)
    locked_menu = studio_menu()
    locked_action = scene._add_create_composite_action(locked_menu)
    assert locked_action is not None
    assert not locked_action.isEnabled()
    scene.deleteLater()


def test_context_dialog_is_modeless_retained_and_reports_link_state(
        qapp, tmp_path):
    library = CompositeLibrary(tmp_path / "composites")
    definition = library.create("Skid", _inner_graph().to_dict())
    scene, composite, _incoming, _outgoing = _connected_scene(definition)
    item = scene.get_block_item(composite.id)

    assert item._composite_definition_action_text(library).endswith("[CURRENT]")
    dialog = item._open_composite_definition_dialog(library)
    assert dialog is item._composite_definition_dialog
    assert dialog.isVisible()
    assert item._open_composite_definition_dialog(library) is dialog

    library.update(definition.id, expected_revision=1, description="new")
    assert item._composite_definition_action_text(library).endswith("[STALE]")
    library.delete(definition.id)
    assert item._composite_definition_action_text(library).endswith("[MISSING]")
    dialog.close()
    scene.deleteLater()


def test_compatible_refresh_rebuilds_ports_reroutes_and_marks_dirty(
        qapp, tmp_path):
    library = CompositeLibrary(tmp_path / "composites")
    original = library.create("Skid", _inner_graph().to_dict())
    expanded = library.create(
        "Skid Plus Diagnostics", _inner_graph(outputs=("OUT", "DIAG")).to_dict())
    scene, composite, _incoming, outgoing = _connected_scene(original)
    item = scene.get_block_item(composite.id)
    changed = []
    scene.strategyModified.connect(lambda: changed.append(True))
    dialog = item._open_composite_definition_dialog(library)
    dialog.definition_combo.setCurrentIndex(
        dialog.definition_combo.findData(expanded.id))

    assert dialog.link_selected()

    assert composite.definition_id == expanded.id
    assert item.get_terminal_item("out", "DIAG") is not None
    assert composite.outputs["OUT"].connected
    assert scene._wire_items[outgoing].src_terminal is \
        item.get_terminal_item("out", "OUT")
    assert len(changed) == 1
    dialog.close()
    scene.deleteLater()


def test_breaking_refresh_rolls_back_without_orphaning_outer_wires(
        qapp, tmp_path):
    library = CompositeLibrary(tmp_path / "composites")
    original = library.create("Skid", _inner_graph().to_dict())
    broken = library.create("No Output", _inner_graph(outputs=()).to_dict())
    scene, composite, incoming, outgoing = _connected_scene(original)
    item = scene.get_block_item(composite.id)
    changed = []
    scene.strategyModified.connect(lambda: changed.append(True))
    dialog = item._open_composite_definition_dialog(library)
    dialog.definition_combo.setCurrentIndex(
        dialog.definition_combo.findData(broken.id))

    with pytest.raises(ValueError, match="connected output .* would be removed"):
        dialog.link_selected()

    assert composite.definition_id == original.id
    assert "OUT" in composite.outputs
    assert set(scene.graph.wires) == {incoming, outgoing}
    assert set(scene._wire_items) == {incoming, outgoing}
    assert scene._wire_items[outgoing].src_terminal is \
        item.get_terminal_item("out", "OUT")
    assert changed == []
    dialog.close()
    scene.deleteLater()


def test_trend_command_uses_registered_paths_and_keeps_every_pen(qapp):
    graph = StrategyGraph("UNIT-200")
    pid = PIDBlock("PIC-201")
    graph.add_block(pid)
    scene = StrategyScene()
    scene.load_graph(graph)
    item = scene.get_block_item(pid.id)
    requested = item._collect_trend_tags()
    assert requested == [
        "UNIT-200/PIC-201/PV",
        "UNIT-200/PIC-201/SP",
        "UNIT-200/PIC-201/OUT",
    ]

    class HistoryHost(QWidget):
        _configure_historian = LiveStation._configure_historian
        _current_history_view = LiveStation._current_history_view

        def __init__(self):
            super().__init__()
            from azeo_control_trainer.core.hmi.binding.source import LiveGraphSource
            self.historian = ContinuousHistorian(None)
            self.graphs_provider = lambda: {graph.name: graph}
            self.live_source = LiveGraphSource(self.graphs_provider)
            self._historian_signature = None
            self.process_history_views = []

        @staticmethod
        def _present(view, retain=False):
            return view

        @staticmethod
        def _retain_window(collection, window):
            collection.append(window)

    host = HistoryHost()
    view = LiveStation.open_process_history(
        host, module=graph.name, paths=requested)
    view._timer.stop()

    assert set(requested) <= host.historian.TAGS.keys()
    assert view._pens == requested
    assert view._table.rowCount() == len(requested)
    view.close()
    host.close()
    scene.deleteLater()


@pytest.mark.parametrize(
    ("block_class", "name"),
    ((AIBlock, "AI1"), (AOBlock, "AO1")),
)
def test_io_trend_command_uses_canonical_out_path(qapp, block_class, name):
    graph = StrategyGraph("UNIT-300")
    block = block_class(name)
    graph.add_block(block)
    scene = StrategyScene()
    scene.load_graph(graph)

    assert scene.get_block_item(block.id)._collect_trend_tags() == [
        f"UNIT-300/{name}/OUT"]
    scene.deleteLater()
