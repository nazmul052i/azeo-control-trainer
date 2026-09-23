"""Operator gestures select historian tags without editing the process display."""
from __future__ import annotations

import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGraphicsItem

from azeo_control_trainer.azeo_operator_station.console import LiveStation
from azeo_control_trainer.azeo_operator_station.deployment import PvmDeployment
from azeo_control_trainer.core.hmi.pvms.base import Pvm
from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore, PvmDisplay
from azeo_control_trainer.core.hmi.pvms.rendering.items import PvmItem
from azeo_control_trainer.core.strategy.blocks.io_blocks import AIBlock
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph


@pytest.fixture(scope="session")
def app():
    # Icon/font caches outlive a station; their QApplication must too.
    return QApplication.instance() or QApplication([])


@pytest.fixture
def station(app, tmp_path):
    graph = StrategyGraph("UNIT")
    for index in range(3):
        block = AIBlock(f"AI{index}")
        block.outputs["OUT"].value = 20.0 + index
        graph.add_block(block)
    store = DisplayStore(tmp_path / "pvm")
    document = PvmDisplay(name="Unit", width=720, height=360, pvms=[
        Pvm(id=f"AI{index}", pvm_class="", block_type="AI", role="dynamo_compact",
            params={"path": f"UNIT/AI{index}"}, x=50 + 210 * index, y=100,
            w=160, h=54).to_dict() for index in range(3)])
    store.save_draft(document)
    store.publish(document, by="engineer")
    window = LiveStation(PvmDeployment(store), lambda: {"UNIT": graph})
    window._tick.stop()
    window.set_workspace_option("dock_context", True)
    window.resize(1180, 760)
    window.show_display("Unit")
    window.show()
    window.view._timer.stop()
    app.processEvents()
    yield window
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def _items(station):
    return sorted((item for item in station.view.scene().items()
                   if isinstance(item, PvmItem)), key=lambda item: item.pvm.id)


def _click(station, item, modifier=Qt.ControlModifier):
    point = station.view.mapFromScene(item.mapToScene(item.rect().center()))
    QTest.mouseClick(station.view.viewport(), Qt.LeftButton, modifier, point)


def _menu(station, item):
    point = station.view.mapFromScene(item.mapToScene(item.rect().center()))
    event = QContextMenuEvent(QContextMenuEvent.Mouse, point,
                             station.view.viewport().mapToGlobal(point))
    QApplication.sendEvent(station.view.viewport(), event)
    assert station.view._last_chart_context_menu is not None
    return station.view._last_chart_context_menu


def test_ctrl_selection_opens_one_detached_chart_with_both_tags(station):
    one, two, _ = _items(station)
    before = station.view.display.to_dict()
    display_size = station.view.size()
    _click(station, one)
    _click(station, two)
    assert not station.faceplates, "Ctrl-click must not activate a faceplate"
    menu = _menu(station, one)
    action = menu.actions()[0]
    assert action.text() == "Add to Historian (2 tags)"
    action.trigger()
    assert len(station.process_history_views) == 1
    history = station.process_history_views[0]
    assert history.isWindow()
    assert station.workspace.get("trends") is None
    assert history._pens == ["UNIT/AI0/OUT", "UNIT/AI1/OUT"]
    assert station.view.size() == display_size
    assert station.view.display.to_dict() == before
    for item in (one, two):
        assert not item.flags() & (QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable)


def test_right_click_single_tag_opens_historian_directly(station):
    item = _items(station)[0]
    menu = _menu(station, item)
    assert menu.actions()[0].text() == "Add to Historian"
    menu.actions()[0].trigger()
    history = station.process_history_views[0]
    assert history._pens == ["UNIT/AI0/OUT"]
    assert history.isWindow()


def test_ctrl_selection_and_context_menu_reach_the_printed_tag_above_a_pvm(station):
    from PySide6.QtCore import QPointF
    positions = []
    for item in _items(station)[:2]:
        scale = item.content_scale()[1]
        position = station.view.mapFromScene(item.mapToScene(QPointF(
            item.rect().center().x(), item.rect().top() - 7 * scale)))
        positions.append(position)
        QTest.mouseClick(station.view.viewport(), Qt.LeftButton, Qt.ControlModifier, position)
    event = QContextMenuEvent(QContextMenuEvent.Mouse, positions[0],
                             station.view.viewport().mapToGlobal(positions[0]))
    QApplication.sendEvent(station.view.viewport(), event)
    menu = station.view._last_chart_context_menu
    assert menu is not None, "The printed tag above the PVM must be a historian target"
    assert menu.actions()[0].text() == "Add to Historian (2 tags)"
    menu.actions()[0].trigger()
    assert station.process_history_views[0]._pens == ["UNIT/AI0/OUT", "UNIT/AI1/OUT"]


def test_trends_command_uses_same_detached_window_and_releases_on_close(station):
    first = station.open_process_history()
    assert first.isWindow(), "The docking preference must not embed the historian"
    assert station.open_process_history() is first
    assert station.workspace.get("trends") is None
    first.close()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not station.process_history_views
    assert station.open_process_history() is not first


def test_historian_fits_a_small_logical_desktop(station):
    from PySide6.QtCore import QPoint
    history = station.open_process_history()
    history.show()
    history.resize(928, 484)
    QApplication.processEvents()
    assert history.width() <= 928 and history.height() <= 484
    assert history._table.mapTo(history, QPoint(0, history._table.height())).y() <= history.height()
    assert history._chart._plot_widget.height() >= 80


def test_selection_toggles_and_right_click_unselected_tag_starts_fresh(station):
    one, two, three = _items(station)
    _click(station, one)
    _click(station, two)
    _click(station, one)
    assert [point.path for point in station.view.selected_chart_candidates()] == ["UNIT/AI1/OUT"]
    _click(station, one)
    _menu(station, three).actions()[0].trigger()
    assert station.process_history_views[0]._pens == ["UNIT/AI2/OUT"]


def test_escape_and_plain_click_restore_normal_faceplate_interaction(station):
    one, two, _ = _items(station)
    _click(station, one)
    _click(station, two)
    selected_image = station.view.viewport().grab().toImage()
    QTest.keyClick(station.view, Qt.Key_Escape)
    assert not station.view.selected_chart_candidates()
    assert station.view.viewport().grab().toImage() != selected_image
    _click(station, one)
    _click(station, two, Qt.NoModifier)
    assert len(station.faceplates) == 1
    assert not station.view.selected_chart_candidates()


def test_add_reuses_chart_deduplicates_and_preserves_review_interval(station):
    one, two, _ = _items(station)
    _menu(station, one).actions()[0].trigger()
    first = station.process_history_views[0]
    first._chart.review_at(30)
    first._visible[first._pens[0]] = False
    first._chart.set_pen_visible(first._pens[0], False)
    interval = first._chart.displayed_span()
    _click(station, one)
    _click(station, two)
    _menu(station, two).actions()[0].trigger()
    assert station.process_history_views == [first]
    assert first._pens == ["UNIT/AI0/OUT", "UNIT/AI1/OUT"]
    assert first._chart.displayed_span() == pytest.approx(interval)
    assert not first._chart.is_live()
    assert first._chart._pens["UNIT/AI0/OUT"].visible


def test_full_chart_opens_another_window_without_dropping_selected_tags(station):
    first = station.open_process_history()
    paths = [f"EXTRA/{index}" for index in range(10)]
    for path in paths:
        station.historian.add_point(path)
    first.set_pens(paths)
    one, two, _ = _items(station)
    _click(station, one)
    _click(station, two)
    _menu(station, one).actions()[0].trigger()
    assert len(station.process_history_views) == 2
    assert first._pens == paths
    assert station.process_history_views[1]._pens == ["UNIT/AI0/OUT", "UNIT/AI1/OUT"]
    assert all(view.isWindow() for view in station.process_history_views)


def test_hidden_display_clears_transient_tag_selection(station):
    _click(station, _items(station)[0])
    station.view.set_active(False)
    station.view.set_active(True)
    assert not station.view.selected_chart_candidates()


def test_failed_historian_action_does_not_escape_qt(station, monkeypatch, caplog):
    def fail(_bindings):
        raise OSError("History unavailable")
    monkeypatch.setattr(station.view, "_chart_batch_handler", fail)
    _menu(station, _items(station)[0]).actions()[0].trigger()
    assert "Could not open selected historian tags" in caplog.text
    assert not station.process_history_views


def test_failed_pointer_lookup_is_contained_and_clears_the_gesture(station, monkeypatch, caplog):
    from PySide6.QtGui import QMouseEvent
    def fail(*_args, **_kwargs):
        raise RuntimeError("Invalid scene item")
    item = _items(station)[0]
    _click(station, item)
    point = station.view.mapFromScene(item.sceneBoundingRect().center())
    monkeypatch.setattr(station.view, "_runtime_item_at", fail)
    event = QMouseEvent(QEvent.MouseButtonPress, point,
                       station.view.viewport().mapToGlobal(point),
                       Qt.LeftButton, Qt.LeftButton, Qt.ControlModifier)
    # Call the override directly so an unguarded exception fails the check
    # without leaving a pending Python error inside Qt's native dispatch.
    station.view.mousePressEvent(event)
    assert "Operator display interaction failed" in caplog.text
    assert not station.view.selected_chart_candidates()
    assert station.view._pvm_press_item is None


def test_ctrl_drag_does_not_select_or_move_a_tag(station):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QMouseEvent
    item = _items(station)[0]
    origin = item.pos()
    start = station.view.mapFromScene(item.sceneBoundingRect().center())
    end = start + QPoint(30, 0)
    viewport = station.view.viewport()
    QTest.mousePress(viewport, Qt.LeftButton, Qt.ControlModifier, start)
    move = QMouseEvent(QEvent.MouseMove, end, viewport.mapToGlobal(end),
                       Qt.NoButton, Qt.LeftButton, Qt.ControlModifier)
    QApplication.sendEvent(viewport, move)
    QTest.mouseRelease(viewport, Qt.LeftButton, Qt.ControlModifier, end)
    assert item.pos() == origin
    assert not station.view.selected_chart_candidates()
    assert not station.faceplates


def test_ctrl_click_does_not_activate_authored_hotspot(station):
    item = _items(station)[0]
    data = {"id": "command", "kind": "rect", "x": item.pos().x(), "y": item.pos().y(),
            "w": 160, "h": 54, "z": 10,
            "actions": [{"event": "click", "kind": "open_display", "target": "Unit"}]}
    hotspot = station.view.renderer.build_drawing(data)
    station.view.scene().addItem(station.view._read_only(hotspot))
    calls = []
    station.view._action_handler = lambda *args: calls.append(args)
    _click(station, item)
    assert not calls
    assert not station.faceplates
    assert not station.view.selected_chart_candidates()
