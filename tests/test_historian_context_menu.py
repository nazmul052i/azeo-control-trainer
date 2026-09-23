"""Historian gestures must reach product actions, never pyqtgraph's editor."""
import math
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from azeo_control_trainer.core.hmi.history import ContinuousHistorian  # noqa: E402
from azeo_control_trainer.core.hmi.history.view import ProcessHistoryView  # noqa: E402


@pytest.fixture
def view():
    app = QApplication.instance() or QApplication([])
    source = ContinuousHistorian(None)
    source.read_only = True
    for terminal in ("PV", "SP", "OUT"):
        point = source.add_point(f"LOOP/PID/{terminal}", module="LOOP", block_type="PID", unit="bar")
        for t in range(121):
            point.sample(t, t if terminal == "PV" else 50, "BAD" if t == 30 else "GOOD")
    widget = ProcessHistoryView(source, "LOOP")
    widget._timer.stop()
    widget.show()
    app.processEvents()
    yield widget
    widget.close()


def action(menu, key):
    for item in menu.actions():
        if item.objectName() == key:
            return item
        if item.menu():
            found = action(item.menu(), key)
            if found:
                return found
    return None


def test_default_plot_menus_are_disabled_on_both_axes(view):
    chart = view._chart
    assert not chart._plot.vb.menuEnabled()
    assert not chart._view_right.menuEnabled()
    assert chart._plot.getContextMenus(None) is None
    assert chart._plot_widget.scene().contextMenu == []


def test_context_actions_use_the_clicked_time_and_update_host_pens(view):
    chart = view._chart
    chart.build_context_menu(.5).deleteLater()
    menu = chart.build_context_menu(.5)
    action(menu, "cursor_a").trigger()
    action(chart.build_context_menu(1.5), "cursor_b").trigger()
    assert chart._ab_button.isChecked()
    assert chart._a_line.value() == .5
    assert chart._b_line.value() == 1.5
    action(chart.build_context_menu(1), "zoom_ab").trigger()
    assert chart.visible_time_range() == pytest.approx((.5, 1.5))
    action(menu, "pen_hide:LOOP/PID/SP").trigger()
    assert not view._visible["LOOP/PID/SP"]
    assert math.isnan(chart._cursor_snapshot(.5)["LOOP/PID/PV"])


def test_menu_reaches_existing_workspace_services(view, monkeypatch):
    calls = []
    monkeypatch.setattr(view, "_browse_archive", lambda: calls.append("archive"))
    monkeypatch.setattr(view, "_compare_runs", lambda: calls.append("compare"))
    monkeypatch.setattr(view, "_export", lambda **_: calls.append("export"))
    menu = view._chart.build_context_menu(1)
    for key in ("archive", "compare_runs", "export"):
        action(menu, key).trigger()
    assert calls == ["archive", "compare", "export"]


def test_native_context_event_opens_only_the_historian_menu(view):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QContextMenuEvent
    chart = view._chart
    viewport = chart._plot_widget.viewport()
    point = QPoint(viewport.width() // 2, viewport.height() // 2)
    event = QContextMenuEvent(QContextMenuEvent.Mouse, point, viewport.mapToGlobal(point))
    QApplication.sendEvent(viewport, event)
    assert event.isAccepted()
    assert chart._context_menu.isVisible()
    assert chart._context_menu.objectName() == "historian_context_menu"
    assert action(chart._context_menu, "axis_limits")
    assert action(chart._context_menu, "export")
    chart._context_menu.close()


def test_shift_f10_opens_the_same_menu_from_the_focused_plot(view):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    chart = view._chart
    view.activateWindow()
    chart._plot_widget.setFocus()
    QTest.qWait(30)
    QTest.keyClick(chart._plot_widget, Qt.Key_F10, Qt.ShiftModifier)
    assert chart._context_menu is not None and chart._context_menu.isVisible()
    assert action(chart._context_menu, "cursor_a")
    chart._context_menu.close()


def test_right_mouse_gesture_opens_menu_without_changing_view_range(view):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QContextMenuEvent
    from PySide6.QtTest import QTest
    chart = view._chart
    viewport = chart._plot_widget.viewport()
    point = QPoint(viewport.width() // 2, viewport.height() // 2)
    before = chart._plot.vb.viewRange()
    QTest.mouseClick(viewport, Qt.RightButton, Qt.NoModifier, point)
    popup = chart._context_menu
    assert popup is not None and popup.isVisible()
    assert chart._plot.vb.viewRange() == before
    QApplication.sendEvent(viewport, QContextMenuEvent(QContextMenuEvent.Mouse, point, viewport.mapToGlobal(point)))
    assert chart._context_menu is popup
    popup.close()


def test_pen_formatting_and_visibility_survive_saved_group_and_reorder(view):
    chart = view._chart
    path = "LOOP/PID/PV"
    chart.update_pen_style(path, color="#123ABC", line_style="dash", line_width=3.0)
    action(chart.build_context_menu(1), "pen_solo:" + path).trigger()
    view._save_group("Custom loop")
    view._table.selectRow(0)
    view.move_selected_pen(1)
    assert chart._pens[path].color == "#123ABC"
    chart.update_pen_style(path, color="#FFFFFF", line_style="solid", line_width=1.0)
    view._load_group(view._groups.findData("group:Custom loop"))
    assert chart._pens[path].color == "#123ABC"
    assert chart._pens[path].line_style == "dash"
    assert chart._pens[path].line_width == 3.0
    assert not view._visible["LOOP/PID/SP"]
    assert view._table.item(view._pens.index(path), 0).background().color().name().upper() == "#123ABC"


def test_clipboard_preserves_bad_quality_and_does_not_invent_missing_samples(view):
    action(view._chart.build_context_menu(.5), "copy_values").trigger()
    copied = QApplication.clipboard().text()
    assert "Sample minute" in copied and "\t\tbar\tBAD" in copied
    action(view._chart.build_context_menu(20), "copy_values").trigger()
    assert "NO SAMPLE" in QApplication.clipboard().text()


def test_recorded_comparison_menu_has_no_live_or_unconnected_workspace_actions(view):
    from azeo_control_trainer.core.hmi.history.workspace_tools import RunComparisonDialog
    dialog = RunComparisonDialog(view.historian)
    menu = dialog.chart.build_context_menu(1)
    assert action(menu, "live") is None
    assert action(menu, "archive") is None
    assert action(menu, "add_pen") is None
    assert action(menu, "events_visible") is None
    assert action(menu, "export")
    assert action(menu, "export_ab") is None
    dialog.close()


def test_copy_from_a_plain_recorded_chart_does_not_invent_good_quality(view):
    from azeo_control_trainer.core.pid.charts.historian_trend import HistorianTrendWidget, TrendPen
    import numpy as np
    chart = HistorianTrendWidget()
    chart.add_pen(TrendPen("Recorded/PV", "PV", "bar", "#004487"))
    chart.update_data("Recorded/PV", np.array([0., 1.]), np.array([10., 20.]))
    action(chart.build_context_menu(1), "copy_values").trigger()
    assert "NOT RECORDED" in QApplication.clipboard().text()
    assert "\tGOOD" not in QApplication.clipboard().text()
    chart.close()


def test_custom_scale_validation_and_plot_options_are_driven(view):
    from azeo_control_trainer.core.pid.charts.history_menu import AxisLimitsDialog
    chart = view._chart
    dialog = AxisLimitsDialog(chart)
    automatic, bounds = dialog.inputs["Primary"]
    automatic.setChecked(False)
    bounds[0].setValue(80)
    bounds[1].setValue(20)
    dialog.accept()
    assert dialog.result() == 0
    assert "greater" in dialog.error.text()
    bounds[1].setValue(100)
    dialog.accept()
    assert dialog.result() == 1
    action(chart.build_context_menu(1), "configured_scale").trigger()
    assert chart._plot.vb.viewRange()[1] == [0, 100]
    action(chart.build_context_menu(1), "grid").trigger()
    assert not chart._grid_visible
    action(chart.build_context_menu(1), "events_visible").trigger()
    chart.set_events([{"time": 60, "category": "note", "action": "Observe"}])
    assert not chart._event_items[0].isVisible()


def test_axis_limit_action_applies_valid_limits_to_the_plot(view, monkeypatch):
    from azeo_control_trainer.core.pid.charts import history_menu
    monkeypatch.setattr(history_menu, "is_headless", lambda: False)
    def accepted(dialog):
        automatic, bounds = dialog.inputs["Primary"]
        automatic.setChecked(False)
        bounds[0].setValue(10)
        bounds[1].setValue(90)
        dialog.accept()
        return dialog.result()
    monkeypatch.setattr(history_menu.AxisLimitsDialog, "exec", accepted)
    action(view._chart.build_context_menu(1), "axis_limits").trigger()
    assert view._chart._plot.vb.viewRange()[1] == [10, 90]
    assert not view._chart._plot.vb.autoRangeEnabled()[1]


def test_removing_a_pen_during_archive_review_keeps_the_selected_interval(view, monkeypatch):
    chart = view._chart
    chart.set_review_range(10, 12)
    requests = []
    monkeypatch.setattr(view.historian, "archive", object())
    monkeypatch.setattr(view, "_queue_query", lambda *args, **kwargs: requests.append((args, kwargs)))
    # Persistence is covered separately; this fake archive only observes range selection.
    monkeypatch.setattr(view.historian, "save_chart_state", lambda *_: None)
    monkeypatch.setattr(view, "_refresh", lambda: None)
    action(chart.build_context_menu(11), "remove:LOOP/PID/SP").trigger()
    assert requests[-1] == ((10.0, 12.0), {"live": False})
    assert "LOOP/PID/SP" not in view._pens
    monkeypatch.setattr(view.historian, "archive", None)


def test_failed_menu_service_is_reported_without_escaping_qt(view):
    def unavailable(_):
        raise RuntimeError("Archive unavailable")
    view._chart.context_services["archive"] = unavailable
    action(view._chart.build_context_menu(1), "archive").trigger()
    assert view._status.text() == "History action failed: Archive unavailable"


def test_pen_row_menu_targets_clicked_point_and_rejects_removed_target(view):
    table = view._table
    table.selectRow(0)
    target = view._pens[1]
    pos = table.visualItemRect(table.item(1, 2)).center()
    table.customContextMenuRequested.emit(pos)
    menu = view._pen_context_menu
    assert menu.isVisible()
    assert table.currentRow() == 1
    remove = next(a for a in menu.actions() if a.text() == view._remove.text())
    # A refresh/other action may move the selection while the popup is open.
    table.selectRow(0)
    remove.trigger()
    assert target not in view._pens and len(view._pens) == 2
    remove.trigger()
    assert len(view._pens) == 2
    menu.close()


def test_new_gesture_cancels_pending_archive_result_before_mouse_release(view):
    from concurrent.futures import Future
    future = Future()
    view._query_future = future
    serial = view._query_serial
    view._pending_range = (1, 2)
    view._range_timer.start()
    view._chart.navigation_started.emit()
    assert future.cancelled()
    assert view._query_serial == serial + 1
    assert view._pending_range is None and not view._range_timer.isActive()
    view._query_future = None
