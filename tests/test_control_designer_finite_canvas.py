"""Finite-sheet and first-frame regressions for Control Designer."""
from __future__ import annotations

import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import azeo_control_trainer.core.strategy.blocks  # noqa: E402,F401
from azeo_control_trainer.core.strategy.model.block_registry import registry  # noqa: E402
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph  # noqa: E402
from azeo_control_trainer.azeo_control_designer.canvas.strategy_scene import (  # noqa: E402
    SHEET_RECT,
    StrategyScene,
)
from azeo_control_trainer.azeo_control_designer.canvas.strategy_view import (  # noqa: E402
    StrategyView,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _assert_item_inside_sheet(scene: StrategyScene, item) -> None:
    assert scene.sheet_rect.contains(item.mapRectToScene(item.boundingRect()))


def test_module_sheet_is_finite_and_confines_new_and_loaded_objects() -> None:
    _app()
    scene = StrategyScene()

    assert scene.sheet_rect == SHEET_RECT
    assert scene.sceneRect().width() < 4000
    assert scene.sceneRect().height() < 3000

    block = registry.create("DI", "DI_NEW")
    assert block is not None
    item = scene.add_block(block, QPointF(100_000, -100_000))
    _assert_item_inside_sheet(scene, item)

    item.setPos(QPointF(-100_000, 100_000))
    _assert_item_inside_sheet(scene, item)

    comment = scene.add_comment("bounded note", QPointF(100_000, 100_000))
    _assert_item_inside_sheet(scene, comment)

    loaded_graph = StrategyGraph("External coordinates")
    loaded = registry.create("DI", "DI_LOADED")
    assert loaded is not None
    loaded.x, loaded.y = 90_000, -90_000
    loaded_graph.add_block(loaded)
    scene.load_graph(
        loaded_graph,
        comments=[{"text": "loaded note", "x": -80_000, "y": 80_000}],
    )
    _assert_item_inside_sheet(scene, scene._block_items[loaded.id])
    _assert_item_inside_sheet(scene, scene._comment_items[0])
    scene.deleteLater()


def test_manual_wire_routes_cannot_leave_the_module_sheet() -> None:
    _app()
    scene = StrategyScene()
    source = registry.create("DI", "DI_1")
    destination = registry.create("DO", "DO_1")
    assert source is not None and destination is not None
    scene.add_block(source, QPointF(0, 0))
    scene.add_block(destination, QPointF(500, 300))

    wire = scene.add_wire(source.id, "OUT", destination.id, "IN")
    assert wire is not None
    start = wire.src_terminal.get_scene_center()
    end = wire.dst_terminal.get_scene_center()
    assert wire.set_manual_route([
        QPointF(100_000, start.y()),
        QPointF(100_000, end.y()),
    ])

    for x, y in wire.manual_route_data() or []:
        assert scene.sheet_rect.contains(QPointF(x, y))
    handle = wire.route_handles()[0]
    assert wire.move_handle(handle, QPointF(-100_000, 100_000))
    for x, y in wire.manual_route_data() or []:
        assert scene.sheet_rect.contains(QPointF(x, y))
    scene.deleteLater()


def test_first_visible_frame_centres_logic_and_runs_only_once() -> None:
    app = _app()
    scene = StrategyScene()
    left = registry.create("DI", "DI_LEFT")
    right = registry.create("DO", "DO_RIGHT")
    assert left is not None and right is not None
    scene.add_block(left, QPointF(-500, -100))
    scene.add_block(right, QPointF(700, 300))

    view = StrategyView(scene)
    view.resize(900, 600)
    view.request_initial_frame()
    view.show()
    app.processEvents()
    app.processEvents()

    visible_center = view.mapToScene(view.viewport().rect().center())
    logic_center = scene.itemsBoundingRect().center()
    assert abs(visible_center.x() - logic_center.x()) < 3
    assert abs(visible_center.y() - logic_center.y()) < 3
    assert 0.1 <= view.transform().m11() <= 1.4

    # Once the engineer changes the view, a later resize must not silently
    # re-run the automatic fit and discard that intentional zoom.
    view.set_zoom(0.75)
    view.resize(1000, 650)
    app.processEvents()
    assert abs(view.transform().m11() - 0.75) < 0.001
    view.close()
    scene.deleteLater()
