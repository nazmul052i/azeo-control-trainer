"""Performance fixes retain document, point and configuration identity."""
import math
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtCore import QPointF, QSettings, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from azeo_control_trainer.core.hmi.history import ContinuousHistorian


@pytest.fixture(scope="module")
def app():
    from azeo_control_trainer.core.hmi.theme.fonts import ensure_font_directory
    ensure_font_directory()
    return QApplication.instance() or QApplication([])


def test_drag_only_synchronizes_document_at_transaction_boundaries(app, tmp_path, monkeypatch):
    from azeo_control_trainer.azeo_graphics_designer.window import HmiStudioWindow

    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    window = HmiStudioWindow(lambda: {}, tmp_path / "displays")
    studio = window.current()
    studio.enter_edit()
    studio._load_document(dict(display=studio.display.name, width=1600, height=900,
                               items=[dict(id="box", kind="rect", x=100, y=100, w=60, h=40)]))
    studio.snap_enabled = studio.smart_guides_enabled = False
    item = studio._static_items()[0]
    studio.selection.replace((item,))
    calls = []
    sync = studio._sync_document
    monkeypatch.setattr(studio, "_sync_document", lambda: (calls.append(1), sync())[1])
    drag = studio.canvas.pointer_drag
    start = item.mapToScene(item.rect().center())
    try:
        assert drag.press(item, start, Qt.NoModifier)
        drag.move(start + QPointF(50, 50), Qt.NoModifier)
        initial = len(calls)
        for step in range(1, 21):
            drag.move(start + QPointF(50 + step, 50 + step), Qt.NoModifier)
        assert len(calls) == initial, "Every move walked the entire document"
        assert studio.unsaved
        drag.release()
        document = studio._document()
        assert document["items"][0]["x"] == 170
        studio.undo()
        assert studio._document()["items"][0]["x"] == 100
        studio.redo()
        assert studio._document()["items"][0]["x"] == 170
    finally:
        studio.unsaved = False
        window.close()
        window.deleteLater()
        app.processEvents()


def test_designer_coalesces_preview_edits_and_uses_current_class(app, tmp_path, monkeypatch):
    from azeo_control_trainer.azeo_graphics_designer.configurator.designer import PvmConfigDesigner

    window = PvmConfigDesigner(tmp_path, pvm_class="HP_C_Valve")
    window.show()
    app.processEvents()
    window._preview_timer.stop()
    rendered = []
    monkeypatch.setattr(window, "_render_preview", lambda: rendered.append(window.current_class))
    try:
        prop = window.config.groups[0].properties[0]
        for step in range(20):
            prop.description = str(step)
            window._mark_unsaved()
        assert rendered == [], "Typing rendered every intermediate configuration"
        assert window.unsaved
        assert QSignalSpy(window._preview_timer.timeout).wait(2000)
        assert rendered == ["HP_C_Valve"]
        prop.description = "pending"
        window._mark_unsaved()
        window._select_class("VesselTrendPvm")
        rendered.clear()
        QTest.qWait(150)
        assert not rendered, "A preview from the previous selection remained queued"
    finally:
        window.unsaved = False
        window.close()
        window.deleteLater()
        app.processEvents()


def test_history_range_and_cursor_do_not_materialize_unrelated_samples(monkeypatch):
    historian = ContinuousHistorian(None, capacity=10000)
    point = historian.add_point("PV", unit="bar")
    for second in range(10000):
        point.sample(second, second)
    monkeypatch.setattr(point, "arrays", lambda: pytest.fail("Converted the entire retained history"))
    assert historian.statistics("PV", 9990 / 60, 9999 / 60)["count"] == 10
    assert historian.nearest_values(["PV"], 9995 / 60)["PV"] == (9995, "GOOD", 9995 / 60)


def test_history_range_retains_quality_units_and_boundaries():
    historian = ContinuousHistorian(None)
    point = historian.add_point("PV", unit="bar")
    point.sample(60, 10)
    point.sample(61, 20, "BAD")
    point.sample(62, 30, "UNCERTAIN")
    point.sample(63, 40)
    stats = historian.statistics("PV", 1, 63 / 60)
    assert stats["count"] == 2 and stats["average"] == 25
    assert math.isnan(historian.nearest_values(["PV"], 61 / 60)["PV"][0])
    assert historian.nearest_values(["PV"], 62 / 60)["PV"][1] == "UNCERTAIN"
    point.unit = "psi"
    assert historian.statistics("PV", 1, 63 / 60)["count"] == 0
    assert math.isnan(historian.nearest_values(["PV"], 1)["PV"][0])
    point.sample(64, 100)
    assert historian.statistics("PV", 1, 64 / 60)["count"] == 1


def test_history_empty_statistics_are_cached_without_hiding_new_sample(monkeypatch):
    historian = ContinuousHistorian(None)
    point = historian.add_point("PV")
    point.sample(60, math.nan)
    historian.statistics("PV", 0, 2)
    assert "PV" in historian._statistics_cache
    point.sample(61, 42)
    assert historian.statistics("PV", 0, 2)["current"] == 42


def test_selection_refreshes_inspector_once_with_final_target(app, tmp_path, monkeypatch):
    from azeo_control_trainer.azeo_graphics_designer.window import HmiStudioWindow

    window = HmiStudioWindow(lambda: {}, tmp_path / "selection")
    studio = window.current()
    studio.enter_edit()
    studio._load_document(dict(display=studio.display.name, items=[
        dict(id=name, kind="rect", x=x, y=100, w=60, h=40)
        for name, x in (("a", 100), ("b", 300))]))
    first, second = studio._static_items()
    studio.selection.replace((first,))
    refreshed = []
    monkeypatch.setattr(studio.pane, "show_item", lambda item: refreshed.append(item))
    monkeypatch.setattr(studio.pane, "show_pvm", lambda item: refreshed.append(item))
    try:
        studio.selection.replace((second,))
        assert refreshed == [second]
        refreshed.clear()
        studio.selection.replace((second,))
        assert refreshed == [], "Selecting the same object rebuilt the active editor"
    finally:
        studio.unsaved = False
        window.close()
        window.deleteLater()
        app.processEvents()


def test_plot_envelope_keeps_extrema_gaps_and_raw_measurements():
    import numpy as np

    historian = ContinuousHistorian(None, capacity=10000)
    point = historian.add_point("PV")
    for second in range(10000):
        point.sample(second, 500 if second == 4500 else -100 if second == 4550 else 20,
                     "BAD" if 6000 <= second <= 6100 else "GOOD")
    times, values, _ = historian.get_plot_series("PV", 0, 200, budget=400)
    assert len(times) < 420
    assert np.nanmax(values) == 500 and np.nanmin(values) == -100
    assert np.isnan(values[(times >= 100) & (times <= 6100 / 60)]).any()
    assert historian.statistics("PV")["count"] == 9899
    assert len(historian.get_series("PV")[0]) == 10000


def test_plot_window_changes_immediately_and_fit_uses_full_recording(app):
    from azeo_control_trainer.core.hmi.history.view import ProcessHistoryView

    historian = ContinuousHistorian(None, capacity=10000)
    historian.read_only = True
    point = historian.add_point("LOOP/PV", module="LOOP")
    for second in range(10000):
        point.sample(second, second)
    window = ProcessHistoryView(historian, "LOOP")
    window._timer.stop()
    chart = window._chart
    try:
        chart.set_time_window(1)
        assert chart._data_cache["LOOP/PV"][0][0] > 160
        chart.set_review_range(10, 11)
        assert chart._data_cache["LOOP/PV"][0][0] < 10
        chart.fit_data()
        left, right = chart.visible_time_range()
        assert left <= 0 and right >= 9999 / 60
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_graphics_tick_only_polls_scene_and_watch_bindings(app, tmp_path):
    from test_faceplate_poll_scope import Source
    from azeo_control_trainer.azeo_graphics_designer.window import HmiStudioWindow

    window = HmiStudioWindow(lambda: {}, tmp_path / "poll")
    studio = window.current()
    studio._load_document(dict(display=studio.display.name, items=[dict(
        id="link", kind="datalink", x=0, y=0, w=100, h=30,
        path="DISPLAY/AI/OUT", data_type="numeric")]))
    source = Source()
    studio.engine._source = source
    studio.engine.bind("OTHER/FP/PV")
    source.reads.clear()
    try:
        studio._tick()
        assert source.reads == ["DISPLAY/AI/OUT"]
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
