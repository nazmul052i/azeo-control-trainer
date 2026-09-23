"""Exercise historian navigation through actual viewport mouse events."""
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from azeo_control_trainer.core.pid.charts.historian_trend import HistorianTrendWidget, TrendPen
from test_historian_context_menu import action


@pytest.fixture
def chart():
    app = QApplication.instance() or QApplication([])
    widget = HistorianTrendWidget()
    widget.resize(1100, 650)
    widget.add_pen(TrendPen("PV", "PV", "bar", "#004487"))
    widget.add_pen(TrendPen("OUT", "OUT", "%", "#008080"), use_right_axis=True)
    for tag in ("PV", "OUT"):
        widget.update_data(tag, np.linspace(0, 10, 101), np.linspace(0, 100, 101))
    widget.show()
    app.processEvents()
    widget.set_review_range(0, 10)
    widget._plot.setYRange(0, 100, padding=0)
    widget._view_right.setYRange(200, 400, padding=0)
    app.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    app.processEvents()


def plot_point(chart, x, y):
    bounds = chart._plot.vb.sceneBoundingRect()
    return chart._plot_widget.mapFromScene(QPointF(
        bounds.left() + bounds.width() * x, bounds.top() + bounds.height() * y))


def drag(chart, start, end, button=Qt.LeftButton):
    viewport = chart._plot_widget.viewport()
    QTest.mouseMove(viewport, start)
    QTest.mousePress(viewport, button, pos=start)
    for fraction in (.2, .4, .6, .8, 1):
        point = start + (end - start) * fraction
        QApplication.sendEvent(viewport, QMouseEvent(
            QEvent.MouseMove, QPointF(point), QPointF(viewport.mapToGlobal(point)),
            Qt.NoButton, button, Qt.NoModifier))
        QApplication.processEvents()
    QTest.mouseRelease(viewport, button, pos=end)
    QApplication.processEvents()


def test_left_drag_zooms_both_axes_and_zoom_back_restores_them(chart):
    original = chart._plot.vb.viewRange()
    original_right = chart._view_right.viewRange()[1]
    changes = []
    chart.review_range_changed.connect(lambda *args: changes.append(args))
    drag(chart, plot_point(chart, .2, .2), plot_point(chart, .8, .8))
    assert chart.visible_time_range() == pytest.approx((2, 8), abs=.04)
    assert chart._plot.vb.viewRange()[1] == pytest.approx((20, 80), abs=.4)
    assert chart._view_right.viewRange()[1] == pytest.approx((240, 360), abs=.8)
    assert len(changes) == 1, "Rectangle review must query on release, not on every move"
    back = action(chart.build_context_menu(5), "zoom_back")
    assert back and back.isEnabled()
    back.trigger()
    assert np.allclose(chart._plot.vb.viewRange(), original)
    assert chart._view_right.viewRange()[1] == pytest.approx(original_right)


def test_middle_drag_pans_both_axes_without_scaling(chart):
    drag(chart, plot_point(chart, .5, .5), plot_point(chart, .6, .6), Qt.MiddleButton)
    assert chart.visible_time_range() == pytest.approx((-1, 9), abs=.04)
    assert chart._plot.vb.viewRange()[1] == pytest.approx((10, 110), abs=.4)
    assert chart._view_right.viewRange()[1] == pytest.approx((220, 420), abs=.8)


def test_wheel_keeps_pointer_anchor_and_freezes_live_follow(chart):
    chart.set_live_mode(True)
    QApplication.processEvents()
    point = plot_point(chart, .3, .4)
    scene = chart._plot_widget.mapToScene(point)
    before = [v.mapSceneToView(scene) for v in (chart._plot.vb, chart._view_right)]
    span = chart.displayed_span()
    viewport = chart._plot_widget.viewport()
    QApplication.sendEvent(viewport, QWheelEvent(QPointF(point),
        QPointF(viewport.mapToGlobal(point)), QPoint(), QPoint(0, 120), Qt.NoButton,
        Qt.NoModifier, Qt.NoScrollPhase, False))
    QApplication.processEvents()
    assert not chart.is_live()
    assert chart.displayed_span() < span
    for view, expected in zip((chart._plot.vb, chart._view_right), before):
        actual = view.mapSceneToView(scene)
        assert actual.x() == pytest.approx(expected.x(), abs=.02)
        assert actual.y() == pytest.approx(expected.y(), abs=.1)


def test_double_click_fits_recorded_data(chart):
    chart.set_review_range(3, 5)
    QTest.mouseDClick(chart._plot_widget.viewport(), Qt.LeftButton, pos=plot_point(chart, .5, .5))
    QApplication.processEvents()
    start, end = chart.visible_time_range()
    assert start <= 0 and end >= 10
    assert not chart.is_live()


def test_ab_cursor_drag_does_not_zoom_the_plot(chart):
    chart._ab_button.setChecked(True)
    chart._a_line.setValue(2)
    original = chart._plot.vb.viewRange()
    drag(chart, plot_point(chart, .2, .5), plot_point(chart, .4, .5))
    assert chart._a_line.value() == pytest.approx(4, abs=.05)
    assert np.allclose(chart._plot.vb.viewRange(), original)
