"""Regression coverage for operator historian collection and chart authoring."""
from __future__ import annotations

import math
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from azeo_control_trainer.core.hmi.history import (  # noqa: E402
    ContinuousHistorian, MAX_CHART_PENS,
)
from azeo_control_trainer.core.hmi.history.view import (  # noqa: E402
    ProcessHistoryView,
)
from azeo_control_trainer.core.pid.charts.historian_trend import (  # noqa: E402
    HistorianTrendWidget, TrendPen,
)


class Store:
    def __init__(self):
        self.values = {}

    def get_all(self):
        return dict(self.values)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_historian_keeps_bad_samples_as_gaps_and_reports_statistics() -> None:
    store = Store()
    historian = ContinuousHistorian(store, period_s=0.0)
    historian.add_point("U100/PID/PV", lo=0.0, hi=200.0)

    store.values["U100/PID/PV"] = 10.0
    historian.collect(now=0.0, force=True)
    store.values.clear()
    historian.collect(now=1.0, force=True)
    store.values["U100/PID/PV"] = 30.0
    historian.collect(now=2.0, force=True)

    times, values = historian.get_series("U100/PID/PV")
    assert np.allclose(times, [0.0, 1.0 / 60.0, 2.0 / 60.0])
    assert values[0] == 10.0 and math.isnan(values[1]) and values[2] == 30.0
    assert historian.get_quality_series("U100/PID/PV").tolist() == [
        "GOOD", "BAD", "GOOD"]
    stats = historian.statistics("U100/PID/PV")
    assert stats == {
        "current": 30.0, "minimum": 10.0, "maximum": 30.0,
        "average": 20.0, "delta": 20.0, "count": 2,
    }


def test_historian_csv_aligns_actual_timestamps_and_chart_state_is_isolated() -> None:
    historian = ContinuousHistorian(None)
    one = historian.add_point("A")
    two = historian.add_point("B")
    one.sample(0.0, 1.0)
    one.sample(2.0, 3.0)
    two.sample(1.0, 2.0)
    csv_text = historian.to_csv(["A", "B"])
    assert csv_text.splitlines() == [
        "time_min,A,B", "0.0000,1,", "0.0167,,2", "0.0333,3,",
    ]

    historian.save_chart_state("module:A", {"pens": ["A"], "visible": {"A": True}})
    state = historian.chart_state("module:A")
    state["pens"].append("B")
    assert historian.chart_state("module:A")["pens"] == ["A"]


def test_chart_enforces_ten_pens_normalizes_and_cleans_legend_rows() -> None:
    _app()
    chart = HistorianTrendWidget()
    for index in range(MAX_CHART_PENS):
        assert chart.add_pen(TrendPen(
            f"P{index}", f"Pen {index}", "bar", "#4FC3F7", 0.0, 200.0))
    assert not chart.add_pen(TrendPen(
        "P10", "Pen 10", "bar", "#FFD54F", 0.0, 100.0))
    chart.update_data("P0", np.array([0.0, 1.0]), np.array([0.0, 100.0]))
    chart.set_pen_visible("P0", False)
    chart.set_normalized(True)
    assert chart.is_normalized()
    assert not chart._pens["P0"].visible
    plotted = chart._pens["P1"]._curve
    chart.update_data("P1", np.array([0.0, 1.0]), np.array([0.0, 100.0]))
    assert np.allclose(plotted.getData()[1], [0.0, 50.0])
    chart.remove_all_pens()
    assert chart._pens == {}
    assert chart._legend_rows == {}
    chart.close()


def test_process_history_pen_management_and_module_session_restore() -> None:
    _app()
    historian = ContinuousHistorian(None)
    for index in range(12):
        historian.add_point(
            f"U100/PID/P{index}", label=f"P{index}", module="U100")
    view = ProcessHistoryView(historian, "U100")
    view._timer.stop()
    first = view._pens[0]
    view._visible[first] = False
    view._chart.set_pen_visible(first, False)
    view._chart.set_time_window(5.0)
    view._chart.set_normalized(True)
    view.close()

    restored = ProcessHistoryView(historian)
    restored._timer.stop()
    assert restored._pens == historian.default_pens_for("U100")
    assert not restored._visible[first]
    assert restored._chart.time_window_min() == 5.0
    assert restored._chart.is_normalized()

    restored.set_pens([f"U100/PID/P{index}" for index in range(12)])
    assert len(restored._pens) == MAX_CHART_PENS
    assert not restored.add_point("U100/PID/P10")
    restored._table.selectRow(1)
    assert restored.move_selected_pen(-1)
    assert restored._pens[0] == "U100/PID/P1"
    restored.close()
