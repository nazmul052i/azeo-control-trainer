"""History interactions must stay responsive and never disguise missing data."""
from __future__ import annotations

import math
import json
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from azeo_control_trainer.core.hmi.history import ContinuousHistorian  # noqa: E402
from azeo_control_trainer.core.hmi.history.view import ProcessHistoryView  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_window_selector_keeps_its_value_readable_and_reports_custom_zoom(app):
    from PySide6.QtWidgets import QStyle, QStyleOptionComboBox
    from azeo_control_trainer.core.pid.charts.historian_trend import HistorianTrendWidget

    chart = HistorianTrendWidget()
    try:
        chart.show()
        app.processEvents()
        combo = chart._window_combo
        option = QStyleOptionComboBox()
        combo.initStyleOption(option)
        text_rect = combo.style().subControlRect(
            QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxEditField, combo)
        assert text_rect.width() >= combo.fontMetrics().horizontalAdvance("10 min")
        original_count = combo.count()
        for minutes in (7.5, 3.25, 0.5):
            chart.set_time_window(minutes)
            assert combo.currentText() == f"{minutes:g} min"
            assert combo.currentData() == minutes
            assert combo.count() == original_count + 1
        combo.setCurrentText("20 min")
        assert chart.time_window_min() == 20
        assert combo.count() == original_count
    finally:
        chart.close()
        chart.deleteLater()
        app.processEvents()


def test_cursor_changes_only_cursor_cells(app, monkeypatch):
    historian = ContinuousHistorian(None)
    historian.add_point("LOOP/PID/PV", module="LOOP").sample(0, 15)
    view = ProcessHistoryView(historian, "LOOP")
    view._timer.stop()
    view._refresh()
    calls = []
    statistics = historian.statistics
    monkeypatch.setattr(historian, "statistics", lambda *args: (calls.append(args), statistics(*args))[1])
    identity = view._table.item(0, 2)
    view._on_cursor_changed(0, {"LOOP/PID/PV": 15})
    assert calls == [], "Moving a cursor must not recompute interval statistics"
    assert view._table.item(0, 2) is identity
    view.close()


def test_latest_bad_sample_is_not_presented_as_current_good_value():
    historian = ContinuousHistorian(None)
    point = historian.add_point("PV")
    point.sample(0, 45, "GOOD")
    point.sample(1, math.nan, "BAD")
    assert math.isnan(historian.statistics("PV")["current"])
    assert historian.statistics("PV")["maximum"] == 45


def test_cursor_outside_recorded_interval_does_not_borrow_a_sample():
    historian = ContinuousHistorian(None)
    historian.add_point("PV").sample(60, 45)
    assert historian.nearest_values(["PV"], 0) == {}
    assert historian.nearest_values(["PV"], 2) == {}


class Source:
    def __init__(self):
        self.values = {"LOOP/PID/PV": 40, "LOOP/PID/SP": 50, "LOOP/PID/OUT": 30}

    def get_all(self):
        return dict(self.values)


def _recorded(tmp_path):
    source = Source()
    historian = ContinuousHistorian(source, period_s=0, archive_path=tmp_path / "history.sqlite")
    for path in source.values:
        historian.add_point(path, unit="%" if path.endswith("/OUT") else "bar", module="LOOP")
    for t in range(12):
        source.values["LOOP/PID/PV"] = 40 + t
        historian.collect(now=t, force=True)
    return source, historian


def test_disk_history_and_groups_survive_restart_with_quality_and_events(tmp_path):
    source, historian = _recorded(tmp_path)
    source.values.pop("LOOP/PID/PV")
    historian.collect(now=12, force=True)
    historian.add_event("operator", "Command accepted", "LOOP/PID/SP", {"requested": 50}, t=5, sim_time=20)
    historian.save_chart_state("group:Flow response", {"pens": list(source.values), "favorite": True})
    historian.close()
    restored = ContinuousHistorian(None, archive_path=tmp_path / "history.sqlite")
    snapshot = restored.query_async(["LOOP/PID/PV"], 0, 1, as_snapshot=True).result(10)
    assert len(snapshot.TAGS["LOOP/PID/PV"].times) == 13
    assert math.isnan(snapshot.statistics("LOOP/PID/PV")["current"])
    assert any(row["action"] == "Command accepted" and row["sim_time"] == 20 for row in snapshot.events)
    assert restored.chart_state("group:Flow response")["favorite"] is True
    restored.close()


def test_clock_reset_creates_a_visible_gap_and_preserves_raw_samples(tmp_path):
    source, historian = _recorded(tmp_path)
    context = {"sim_time": 100, "paused": False}
    historian.clock_context = lambda: dict(context)
    historian.collect(now=20, force=True)
    context.update(sim_time=5, paused=True)
    historian.collect(now=21, force=True)
    values = historian.get_series("LOOP/PID/PV")[1]
    assert math.isnan(values[-2]) and values[-1] == 51
    assert any("reset" in row["action"] for row in historian.events)
    raw = historian.query_async(["LOOP/PID/PV"], 20 / 60, 21 / 60, raw=True).result(10)
    assert len(raw["samples"]["LOOP/PID/PV"]) == 2
    assert raw["samples"]["LOOP/PID/PV"][0][-1] != raw["samples"]["LOOP/PID/PV"][1][-1]
    historian.close()


def test_disk_envelope_preserves_spikes_and_bad_gaps(tmp_path):
    source, historian = _recorded(tmp_path)
    for t in range(12, 201):
        source.values["LOOP/PID/PV"] = 900 if t == 137 else 20
        if t == 73:
            source.values.pop("LOOP/PID/PV")
        historian.collect(now=t, force=True)
    result = historian.archive.query(["LOOP/PID/PV"], 0, 200, max_points=10).result(15)
    rows = result["samples"]["LOOP/PID/PV"]
    assert result["reduced"]
    assert len(rows) < 80
    assert any(row[1] == 900 for row in rows)
    assert any(row[2] == "BAD" for row in rows)
    snapshot = historian.snapshot(result)
    raw = historian.archive.read(["LOOP/PID/PV"], 0, 200, max_points=0)
    good = [r[1] for r in raw["samples"]["LOOP/PID/PV"] if r[2] == "GOOD"]
    assert snapshot.statistics("LOOP/PID/PV", 0, 200 / 60)["average"] == pytest.approx(sum(good) / len(good))
    assert math.isnan(snapshot.statistics("LOOP/PID/PV", 1, 2)["average"])
    historian.close()


def test_range_debounce_rejects_a_ready_result_for_the_previous_interval(app, tmp_path):
    _, historian = _recorded(tmp_path)
    view = ProcessHistoryView(historian, "LOOP")
    view._timer.stop()
    try:
        view._queue_query(0, .1)
        view._query_future.result(10)
        previous = view._source
        view._range_changed(.1, .2)
        assert view._range_timer.isActive()
        view._poll_query()
        assert view._source is previous
        view._load_pending_range()
        view._query_future.result(10)
        view._poll_query()
        assert view._source.TAGS["LOOP/PID/PV"].times[0] >= 6
    finally:
        view.close()
        historian.close()


def test_archive_query_is_nonblocking_and_stale_result_cannot_replace_new_selection(app, tmp_path):
    _, historian = _recorded(tmp_path)
    view = ProcessHistoryView(historian, "LOOP")
    view._timer.stop()
    view._queue_query(0, .1)
    old = view._query_future
    view._queue_query(.1, .2)
    current = view._query_future
    current.result(10)
    view._poll_query()
    assert old is not current
    assert view._source.TAGS["LOOP/PID/PV"].times[0] >= 6
    assert view._chart.visible_time_range() == pytest.approx((.1, .2))
    assert not view._chart.is_live()
    view.close()
    historian.close()


def test_event_navigation_and_ab_cursors_reach_recorded_values(app):
    historian = ContinuousHistorian(None)
    historian.read_only = True
    point = historian.add_point("LOOP/PID/PV", module="LOOP", unit="bar")
    for t in range(121):
        point.sample(t, 10 + t)
    row = historian.add_event("alarm", "active", "LOOP/PID/HI", t=60)
    view = ProcessHistoryView(historian, "LOOP")
    view._timer.stop()
    view._inspect_event(row)
    assert not view._chart.is_live()
    assert view._chart.visible_time_range() == pytest.approx((0, 2))
    view._chart._ab_button.setChecked(True)
    view._chart._a_line.setValue(.5)
    view._chart._b_line.setValue(1)
    assert float(view._table.item(0, 14).text()) == pytest.approx(30)
    assert float(view._table.item(0, 15).text()) == pytest.approx(1)
    view.close()


def test_export_contains_raw_quality_units_events_and_real_timestamps(tmp_path):
    from azeo_control_trainer.core.hmi.history.export import export_history
    source, historian = _recorded(tmp_path)
    source.values.pop("LOOP/PID/PV")
    historian.collect(now=12, force=True)
    historian.add_event("note", "Bookmark", detail="<training observation>", t=5)
    historian.archive.flush()
    paths = list(historian.TAGS)
    target = tmp_path / "review.html"
    export_history(target, paths=paths, metadata={p: historian.point_metadata(historian.TAGS[p]) for p in paths},
                   origin=historian.origin, start=0, end=12, archive=historian.archive)
    text = (tmp_path / "review.samples.csv").read_text(encoding="utf-8-sig")
    assert "recorded_time_utc" in text and "simulation_seconds" in text and "quality,unit" in text
    assert ",BAD,bar" in text
    assert "&lt;training observation&gt;" in target.read_text(encoding="utf-8")
    metadata = json.loads((tmp_path / "review.metadata.json").read_text())
    assert metadata["statistics"]["LOOP/PID/PV"]["good"] == 12
    assert metadata["statistics"]["LOOP/PID/PV"]["count"] == 13
    historian.close()


def test_alignment_measures_only_after_the_selected_step_and_keeps_context():
    from azeo_control_trainer.core.hmi.history.analysis import aligned_response, response_metrics
    rows = [{"time": t, "pv": pv, "sp": sp, "out": 30, "quality": "GOOD"}
            for t, pv, sp in ((0, 40, 40), (1, 40, 50), (2, 52, 50), (3, 50, 50), (8, 50, 50))]
    result = aligned_response(rows, "setpoint")
    assert result["plot"][0]["time"] == -1
    assert result["measurement"][0]["time"] == 0
    metrics = response_metrics(result["measurement"], tolerance=.5)
    assert metrics["overshoot"] == 2
    assert metrics["settling_seconds"] == 2
    with pytest.raises(ValueError, match="No recorded fault"):
        aligned_response(rows, "fault")


def test_comparison_is_driven_by_measured_windows_and_exports(app, tmp_path):
    from azeo_control_trainer.core.hmi.history.workspace_tools import RunComparisonDialog
    historian = ContinuousHistorian(None)
    historian.add_point("LOOP/PID/PV", unit="bar")
    rows = [{"time": t, "pv": value, "sp": 10, "out": 30, "quality": "GOOD"}
            for t, value in ((0, 0), (1, 12), (2, 10), (7, 10))]
    dialog = RunComparisonDialog(historian)
    dialog.set_windows({"baseline": rows, "trial": rows}, "LOOP/PID")
    assert dialog._measured["baseline"]["metrics"]["overshoot"] == 2
    assert len(dialog.chart._pens) == 6
    assert dialog.chart.visible_time_range()[1] >= 7 / 60
    assert dialog.chart._ax_right is dialog.chart._plot.getAxis("right")
    dialog.export(tmp_path / "comparison.html")
    assert (tmp_path / "comparison.html").is_file()
    assert (tmp_path / "comparison.csv").is_file()
    dialog.close()


def test_retention_expires_old_samples_and_remembers_budget(tmp_path):
    source = Source()
    historian = ContinuousHistorian(source, archive_path=tmp_path / "history.sqlite")
    historian.archive.set_retention(7, 128).result(5)
    historian.add_point("LOOP/PID/PV")
    historian.collect(now=-2 * 86400, force=True)
    historian.collect(now=0, force=True)
    historian.archive.set_retention(1, 128).result(5)
    historian.collect(now=1, force=True)
    historian.archive.flush()
    result = historian.archive.read(["LOOP/PID/PV"], -3 * 86400, 2)
    assert len(result["samples"]["LOOP/PID/PV"]) == 2
    historian.close()
    restored = ContinuousHistorian(None, archive_path=tmp_path / "history.sqlite")
    assert restored.archive.retention_days == 1
    assert restored.archive.storage_limit_mb == 128
    restored.close()


def test_expired_history_is_pruned_in_bounded_writer_batches(tmp_path):
    from azeo_control_trainer.core.hmi.history.archive import HistoryArchive

    archive = HistoryArchive(tmp_path / "large-history.sqlite")
    with archive.connect() as db:
        db.executemany("INSERT INTO history_samples VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (("LOOP/PID/PV", float(index), 1.0, "GOOD", 0.0, 0.0, "")
                        for index in range(12000)))
    archive._last_prune = 0
    archive._append({}, [], [])
    with archive.connect() as db:
        assert db.execute("SELECT count(*) FROM history_samples").fetchone()[0] == 7000
    archive.close()


def test_workbench_emits_history_events_without_enabling_command_replay(tmp_path):
    from test_simulation_workbench import _service
    service, ai, _ = _service(tmp_path)
    events = []
    service.history_event_sink = lambda *args: events.append(args)
    assert not service.recording
    service.set_io_value("M100", ai.id, 42, "BAD")
    assert events and events[-1][1] == "M100/AI-101"
    assert service.journal.count() == 0


def test_station_records_alarm_events_in_shared_historian(app, tmp_path):
    from azeo_control_trainer.azeo_operator_station.console import LiveStation
    from azeo_control_trainer.azeo_operator_station.deployment import PvmDeployment
    from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore
    station = LiveStation(PvmDeployment(DisplayStore(tmp_path / "pvm")), lambda: {})
    station._tick.stop()
    station.alarm_state.observe("M", "AI", [("HI", 3, 100)])
    station._history_alarm_events()
    count = len(station.historian.events)
    station._history_alarm_events()
    assert len(station.historian.events) == count
    assert any(row["category"] == "alarm" and row["target"] == "M/AI/HI" for row in station.historian.events)
    station.close()


def test_complete_archive_export_loads_samples_outside_the_visible_window(app, tmp_path):
    from PySide6.QtTest import QTest
    from azeo_control_trainer.core.hmi.history.workspace_tools import ExportHistoryDialog
    _, historian = _recorded(tmp_path)
    historian.read_only = True
    historian.now = lambda: 12
    view = ProcessHistoryView(historian, "LOOP")
    view._timer.stop()
    view._chart.set_review_range(.15, .2)
    dialog = ExportHistoryDialog(view)
    dialog.path.setText(str(tmp_path / "entire.html"))
    dialog.scope.setCurrentIndex(2)
    future, target = dialog.export()
    for _ in range(200):
        QTest.qWait(20)
        view._poll_query()
        if future.done():
            break
    future.result(2)
    metadata = json.loads((tmp_path / "entire.metadata.json").read_text())
    assert metadata["statistics"]["LOOP/PID/PV"]["count"] == 12
    assert "data:image/png;base64," in target.read_text(encoding="utf-8")
    assert view._chart.visible_time_range() == pytest.approx((.15, .2))
    view.close()
    historian.close()


def test_comparison_prefers_the_controller_over_its_input_transmitter(app):
    from azeo_control_trainer.core.hmi.history.workspace_tools import RunComparisonDialog
    historian = ContinuousHistorian(None)
    historian.read_only = True
    for path in ("LOOP/AI/PV", "LOOP/PID/PV", "LOOP/PID/SP", "LOOP/PID/OUT"):
        historian.add_point(path, module="LOOP")
    view = ProcessHistoryView(historian, "LOOP")
    assert view._pens[0] == "LOOP/AI/PV"
    dialog = RunComparisonDialog(historian, view)
    assert dialog.loop.currentText() == "LOOP/PID"
    dialog.close()
    view.close()


def test_loop_default_opens_pv_sp_out_and_retains_other_points_for_selection():
    historian = ContinuousHistorian(None)
    for block, kind, terminals in (("AI", "AI", ("OUT",)), ("PID", "PID", ("PV", "SP", "OUT")),
                                   ("AO", "AO", ("OUT",))):
        for terminal in terminals:
            historian.add_point(f"LOOP/{block}/{terminal}", module="LOOP", block_type=kind)
    assert historian.default_pens_for("LOOP") == ["LOOP/PID/PV", "LOOP/PID/SP", "LOOP/PID/OUT"]
    assert len(historian.TAGS) == 5


def test_response_measurement_rejects_a_selection_across_simulation_reset():
    from azeo_control_trainer.core.hmi.history.analysis import history_response_rows
    historian = ContinuousHistorian(None)
    for terminal in ("PV", "SP", "OUT"):
        point = historian.add_point(f"LOOP/PID/{terminal}")
        point.sample(0, 10, run="before")
        point.sample(2, 12, run="after")
    with pytest.raises(ValueError, match="choose one run"):
        history_response_rows(historian, "LOOP/PID", 0, 3)


def test_archive_failure_cannot_escape_window_close(app, tmp_path, monkeypatch):
    _, historian = _recorded(tmp_path)
    view = ProcessHistoryView(historian, "LOOP")
    def unavailable(*_):
        raise RuntimeError("Disk writer unavailable")
    monkeypatch.setattr(historian.archive, "save_workspace", unavailable)
    view.close()
    assert view._closed and historian.archive.error == "Disk writer unavailable"
    assert historian.chart_state("window:history")
    historian.close()


def test_history_failure_does_not_change_a_successful_operator_write(app, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from azeo_control_trainer.azeo_operator_station.console import LiveStation
    from azeo_control_trainer.azeo_operator_station.deployment import PvmDeployment
    from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore
    from azeo_control_trainer.core.hmi.binding import WriteResult
    station = LiveStation(PvmDeployment(DisplayStore(tmp_path / "pvm")), lambda: {})
    station._tick.stop()
    writes = []
    monkeypatch.setattr(station.live_source, "read", lambda _: SimpleNamespace(value=10, units="bar"))
    monkeypatch.setattr(station.live_source, "can_write", lambda _: WriteResult(True))
    monkeypatch.setattr(station.live_source, "write", lambda *args: (writes.append(args), WriteResult(True))[1])
    def unavailable(*_, **__):
        raise RuntimeError("History event writer unavailable")
    monkeypatch.setattr(station.historian, "add_event", unavailable)
    try:
        assert station._write("LOOP/PID/SP", 12).success
        assert writes == [("LOOP/PID/SP", 12)]
    finally:
        station.close()
