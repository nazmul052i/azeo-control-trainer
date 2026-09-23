"""Integration contract for Control Designer's module engineering workspace."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import azeo_control_trainer.core.strategy.blocks  # noqa: E402,F401
from azeo_control_trainer.core.strategy.model.block_registry import (  # noqa: E402
    registry,
)
from azeo_control_trainer.core.strategy.model.strategy_graph import (  # noqa: E402
    StrategyGraph,
)
from azeo_control_trainer.azeo_control_designer.designer_tab import (  # noqa: E402
    StrategyDesignerTab,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _tab_with_graph(graph: StrategyGraph) -> tuple[StrategyDesignerTab, object]:
    _app()
    tab = StrategyDesignerTab()
    canvas = tab._create_canvas(graph.name)
    canvas.scene.load_graph(graph)
    tab._connect_all_block_signals(canvas.scene)
    return tab, canvas


def _alarm_graph(name: str = "UNIT_100"):
    graph = StrategyGraph(name)
    ai = registry.create("AI", "FT_101")
    assert ai is not None
    ai.config.params.update({
        "HI_HI_LIM": 90.0,
        "HI_LIM": 80.0,
        "LO_LIM": 20.0,
        "LO_LO_LIM": 10.0,
        "HI_HI_ENAB": True,
        "HI_ENAB": True,
        "LO_ENAB": True,
        "LO_LO_ENAB": True,
    })
    graph.add_block(ai)
    return graph, ai


def test_workspace_replaces_no_models_and_tracks_active_module_and_selection():
    graph, ai = _alarm_graph()
    tab, first = _tab_with_graph(graph)
    workspace = tab._engineering_workspace

    assert tab._left_splitter.widget(0) is tab._project_tree
    assert tab._left_splitter.widget(1) is workspace
    assert tab._palette is workspace.palette
    assert workspace.hierarchy._graph is graph
    assert workspace.alarms._graph is graph

    workspace.hierarchy.blockActivated.emit(ai.id)
    item = first.scene.get_block_item(ai.id)
    assert item.isSelected()
    assert tab._props._block is ai
    assert workspace.hierarchy.tree.currentItem().data(
        0, workspace.hierarchy.ROLE_BLOCK_ID) == ai.id

    second_graph = StrategyGraph("UNIT_200")
    block = registry.create("ABS", "CALC_201")
    assert block is not None
    second_graph.add_block(block)
    second = tab._create_canvas(second_graph.name)
    second.scene.load_graph(second_graph)
    tab._connect_all_block_signals(second.scene)
    assert workspace.hierarchy._graph is second_graph
    assert workspace.hierarchy.tree.topLevelItem(0).text(0) == "UNIT_200"

    tab._canvas_tabs.setCurrentWidget(first)
    assert workspace.hierarchy._graph is graph
    assert workspace.hierarchy.tree.topLevelItem(0).text(0) == "UNIT_100"
    tab.cleanup()
    tab.deleteLater()


def test_alarm_edit_is_normalized_undoable_dirty_and_live_refreshed():
    graph = StrategyGraph("LOOP_100")
    pid = registry.create("PID", "FIC_101")
    assert pid is not None
    pid.config.params["hi_lim"] = 75.0
    graph.add_block(pid)
    tab, canvas = _tab_with_graph(graph)
    canvas.scene.undo_stack.clear()
    canvas.dirty = False
    canvas.downloaded_at = 1.0
    canvas.modified_since_download = False

    # The public Azeo spelling normalizes to this PID's canonical lower-case
    # key before ChangeConfigCommand applies it.
    assert tab._edit_alarm_configuration(pid.id, "HI_LIM", 88.5)
    assert pid.config.params["hi_lim"] == 88.5
    assert "HI_LIM" not in pid.config.params
    assert canvas.scene.undo_stack.count() == 1
    assert canvas.dirty and canvas.modified_since_download

    canvas.scene.undo_stack.undo()
    assert pid.config.params["hi_lim"] == 75.0
    canvas.scene.undo_stack.redo()
    assert pid.config.params["hi_lim"] == 88.5

    ai_graph, ai = _alarm_graph("LIVE_ALARMS")
    canvas.scene.load_graph(ai_graph)
    tab._connect_all_block_signals(canvas.scene)
    ai.outputs["HI_ACT"].value = True
    tab._engineering_live_timer.timeout.emit()
    assert tab._engineering_workspace.alarms.summary.text().startswith("1 active")
    assert tab._engineering_live_timer.interval() == 1000
    assert tab._engineering_live_timer.isActive()
    tab.cleanup()
    assert not tab._engineering_live_timer.isActive()
    tab.deleteLater()


def test_palette_and_canvas_template_routes_preserve_the_drop_coordinate(
    tmp_path,
):
    graph = StrategyGraph("TEMPLATE_TARGET")
    tab, canvas = _tab_with_graph(graph)
    source = registry.create("ABS", "SOURCE")
    sink = registry.create("ABS", "SINK")
    assert source is not None and sink is not None
    source.x, source.y = 0.0, 0.0
    sink.x, sink.y = 60.0, 40.0
    template = {
        "name": "Calculation Pair",
        "blocks": [source.to_dict(), sink.to_dict()],
        "wires": [{
            "id": "template-wire",
            "src_block_id": source.id,
            "src_terminal": "OUT",
            "dst_block_id": sink.id,
            "dst_terminal": "IN",
            "is_bkcal": False,
        }],
    }
    path = tmp_path / "calculation_pair.json"
    path.write_text(json.dumps(template), encoding="utf-8")

    canvas.scene.undo_stack.clear()
    # Block positions use the canvas grid; this exact drop must be retained
    # rather than silently replaced by the viewport centre.
    drop = QPointF(140.0, -60.0)
    canvas.view.templateDropped.emit(str(path), drop)
    positions = sorted((block.x, block.y)
                       for block in canvas.scene.graph.blocks.values())
    assert positions == [(140.0, -60.0), (200.0, -20.0)]
    assert len(canvas.scene.graph.wires) == 1
    assert canvas.scene.undo_stack.count() == 1

    canvas.scene.undo_stack.undo()
    assert canvas.scene.graph.blocks == {}
    assert canvas.scene.graph.wires == {}

    # The custom-palette double-click route reaches the same placement method;
    # without a pointer coordinate it deliberately uses the viewport centre.
    tab._palette.templatePlacementRequested.emit(str(path))
    assert len(canvas.scene.graph.blocks) == 2
    assert len(canvas.scene.graph.wires) == 1
    tab.cleanup()
    tab.deleteLater()
