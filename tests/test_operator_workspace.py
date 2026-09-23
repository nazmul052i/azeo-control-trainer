"""Operator navigation and background updates must preserve operating context."""
from __future__ import annotations

import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from azeo_control_trainer.azeo_operator_station.console import LiveStation
from azeo_control_trainer.azeo_operator_station.deployment import PvmDeployment
from azeo_control_trainer.azeo_operator_station.dialogs import AlarmFilter, AlarmListDialog
from azeo_control_trainer.core.hmi.binding.alarm_state import RuntimeAlarmRegistry
from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore, PvmDisplay


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def station(app, tmp_path):
    store = DisplayStore(tmp_path / "pvm")
    for name in ("Plant", "Unit"):
        document = PvmDisplay(name=name)
        store.save_draft(document)
        store.publish(document, by="engineer")
    window = LiveStation(PvmDeployment(store), lambda: {})
    window._tick.stop()
    yield window
    window.close()
    window.deleteLater()
    app.processEvents()


def test_station_does_not_invent_source_freshness(station):
    station.sync_status(100)
    assert station.status.status.last_update is None
    assert station.status.status.liveness() == "NO DATA"


def test_return_navigation_reuses_view_and_stops_hidden_polling(station):
    station.show_display("Plant")
    first = station.view
    station.show_display("Unit")
    assert not first._timer.isActive()
    station.show_display("Plant")
    assert station.view is first
    assert first._timer.isActive()


def test_cached_navigation_keeps_the_view_in_its_existing_widget_parent(station):
    station.show_display("Plant")
    first = station.view
    parent = first.parentWidget()
    station.show_display("Unit")
    assert first.parentWidget() is parent
    station.show_display("Plant")
    assert first.parentWidget() is parent


def test_static_display_art_does_not_repaint_unchanged_vectors(app, monkeypatch):
    from azeo_control_trainer.core.hmi.pvms.rendering.items import StaticItem
    from azeo_control_trainer.core.hmi.pvms.rendering.viewer import PvmDisplayView
    calls = []
    original = StaticItem.paint

    def counted(self, *args):
        calls.append(self.data.get("id"))
        return original(self, *args)

    monkeypatch.setattr(StaticItem, "paint", counted)
    view = PvmDisplayView({"display": "Art", "width": 500, "height": 300,
                           "items": [{"id": "rect", "kind": "rect", "x": 20, "y": 20,
                                      "w": 150, "h": 100}]}, lambda: {}, live=False)
    try:
        view.resize(500, 300)
        view.show()
        app.processEvents()
        view.viewport().repaint()
        count = len(calls)
        assert count > 0
        view.viewport().repaint()
        assert len(calls) == count
        item = next(i for i in view.scene().items() if isinstance(i, StaticItem))
        item.data["fill"] = "#004487"
        item.update()
        view.viewport().repaint()
        assert len(calls) > count
    finally:
        view.close()
        view.deleteLater()
        app.processEvents()


def test_return_navigation_does_not_rescan_release_files(station, monkeypatch):
    station.show_display("Plant")
    station.show_display("Unit")
    station.sync_chrome()

    def unexpected_scan():
        pytest.fail("A cached display switch rescanned the release catalog")

    monkeypatch.setattr(station.deployment, "pending", unexpected_scan)
    monkeypatch.setattr(station.deployment, "displays", unexpected_scan)
    assert station.show_display("Plant")
    assert station.nav.can_go["home"]


def test_station_tick_reads_each_release_history_once(station, monkeypatch):
    from collections import Counter

    station.show_display("Plant")
    reads = Counter()
    original = station.deployment.store.history

    def counted(name):
        reads[name] += 1
        return original(name)

    monkeypatch.setattr(station.deployment.store, "history", counted)
    station.tick()
    assert reads == {"Plant": 1, "Unit": 1}
    reads.clear()
    station.tick()
    assert reads == {"Plant": 1, "Unit": 1}, "Each tick must check for external publications"


def test_tick_catalog_reuse_does_not_hold_external_publication_stale(station):
    station.show_display("Plant")
    first = station.view
    station.tick()
    store = station.deployment.store
    document = store.load_draft("Plant")
    document.width = 1234
    store.publish(document, by="engineer")
    other = PvmDisplay(name="Other station")
    store.publish(other, by="engineer", workstations=["OTHER"])
    new = PvmDisplay(name="New unit")
    store.publish(new, by="engineer")
    station.tick()
    assert station._pending_display_count == 1
    assert "New unit" in station._published_displays
    assert "New unit" in station.alarm_rollup._engines
    assert "Other station" not in station._published_displays
    assert station.view is first and station.deployment._held("Plant") == 1
    station.refresh_configuration()
    assert station.view.display.width == 1234


def test_recurring_catalog_reads_run_off_gui_and_do_not_accept_revisions(station, monkeypatch):
    import threading
    from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore

    station.show_display("Plant")
    station.tick()
    view = station.view
    original = DisplayStore.history
    threads = []
    owner = threading.get_ident()

    def observed(store, name):
        threads.append(threading.get_ident())
        assert threading.get_ident() != owner, "recurring release I/O reached the GUI"
        return original(store, name)

    monkeypatch.setattr(DisplayStore, "history", observed)
    getattr(station, "_timer_tick", station.tick)()
    worker = station.deployment._catalog_worker
    worker._future.result(timeout=5)  # test harness waits, the GUI handler must not
    assert threads and owner not in threads
    assert station.view is view and station.deployment._held("Plant") == 1
    station.close()
    assert worker.closed


def test_background_publication_updates_badge_but_operator_refresh_accepts(station):
    station.show_display("Plant")
    first = station.view
    station.tick()
    station._timer_tick()
    worker = station.deployment._catalog_worker
    worker._future.result(timeout=5)
    # A completed earlier result is deliberately left pending during publish.
    store = station.deployment.store
    document = store.load_draft("Plant")
    document.width = 1234
    store.publish(document, by="engineer")
    store.publish(PvmDisplay(name="New unit"), by="engineer")
    store.publish(PvmDisplay(name="Other station"), by="engineer", workstations=["OTHER"])
    station._timer_tick()
    worker._future.result(timeout=5)
    station._timer_tick()
    assert station._pending_display_count == 1
    assert "New unit" in station._published_displays
    assert "New unit" in station.alarm_rollup._engines
    assert "Other station" not in station._published_displays
    assert station.view is first and station.deployment._held("Plant") == 1
    station.refresh_configuration()
    assert station.view.display.width == 1234
    assert station.deployment._held("Plant") == 2
    station._timer_tick()
    assert not station._pending_display_count


def test_failed_tick_releases_catalog_reads_for_the_next_refresh(station, monkeypatch):
    station.show_display("Plant")
    original = station.historian.collect

    def failed():
        raise RuntimeError("Injected historian failure")

    monkeypatch.setattr(station.historian, "collect", failed)
    with pytest.raises(RuntimeError, match="Injected historian"):
        station.tick()
    document = station.deployment.store.load_draft("Plant")
    station.deployment.store.publish(document, by="engineer")
    monkeypatch.setattr(station.historian, "collect", original)
    station.tick()
    assert station._pending_display_count == 1


def test_display_construction_samples_module_inventory_once(app):
    from azeo_control_trainer.core.hmi.pvms.rendering.viewer import PvmDisplayView

    calls = []
    document = PvmDisplay(name="Many values", items=[
        {"kind": "datalink", "id": f"value-{i}", "path": "M/AI/PV",
         "x": 0, "y": i * 20, "w": 100, "h": 20}
        for i in range(30)])
    view = PvmDisplayView(document.to_dict(), lambda: calls.append(1) or {}, live=False)
    try:
        assert view.engine.monitored_count >= 30
        assert len(calls) == 1
    finally:
        view.close()
        view.deleteLater()


def test_alarm_monitor_samples_shared_block_once(station, monkeypatch):
    from azeo_control_trainer.core.strategy.blocks.io_blocks import AIBlock
    from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph

    graph = StrategyGraph("M")
    block = AIBlock("AI")
    graph.add_block(block)
    station.live_source._graphs = lambda: {"M": graph}
    monitor = station.alarm_rollup
    for name in ("Plant", "Unit"):
        monitor._blocks[name] = {"M/AI"}
        for _ in range(12):
            monitor._engines[name].bind("M/AI/OUT")
    block.outputs["HI_ACT"].value = True
    observations = []
    observe = station.alarm_state.observe

    def counted(*args):
        observations.append(args[:2])
        return observe(*args)

    monkeypatch.setattr(station.alarm_state, "observe", counted)
    monitor.poll()
    assert observations == [("M", "AI")]
    assert station.alarm_state.block_summary("M", "AI")["active"]
    block.outputs["HI_ACT"].value = False
    monitor.poll()
    assert not station.alarm_state.block_summary("M", "AI")["active"]


@pytest.mark.parametrize("symbol", ["pump", "vessel", "exchanger"])
def test_symbol_geometry_reads_alpha_in_bulk_without_changing_outline(app, monkeypatch, symbol):
    from PySide6.QtGui import QImage
    from azeo_control_trainer.core.hmi.pvms.symbols import _alpha_geometry

    calls = []
    pixel = QImage.pixel

    def counted(image, x, y):
        calls.append(1)
        return pixel(image, x, y)

    monkeypatch.setattr(QImage, "pixel", counted)
    image, opaque, bounds = _alpha_geometry(symbol)
    assert not calls, "SVG geometry crossed into Qt for every pixel"
    expected = [[((pixel(image, x, y) >> 24) & 255) > 16
                 for x in range(image.width())] for y in range(image.height())]
    assert opaque == expected
    points = [(x, y) for y, row in enumerate(expected) for x, on in enumerate(row) if on]
    assert bounds == (min(x for x, _ in points), min(y for _, y in points),
                      max(x for x, _ in points), max(y for _, y in points))


def test_navigation_holds_revision_until_explicit_refresh(station):
    station.show_display("Plant")
    first = station.view
    store = station.deployment.store
    document = store.load_draft("Plant")
    document.width = 1234
    store.save_draft(document)
    store.publish(document, by="engineer")
    station.show_display("Unit")
    station.show_display("Plant")
    assert station.view is first
    assert station.view.display.width != 1234
    station.refresh_configuration()
    assert station.view is not first
    assert station.view.display.width == 1234


def test_alarm_refresh_does_not_select_a_different_alarm(app):
    registry = RuntimeAlarmRegistry()
    registry.observe("Unit", "AI", [("HI", 11)])
    dialog = AlarmListDialog(registry, AlarmFilter(), lambda row: True)
    dialog.table.selectRow(0)
    registry.observe("Unit", "AI", [])
    registry.acknowledge(("Unit/AI/HI",))
    registry.observe("Other", "AI", [("LO", 11)])
    dialog.refresh()
    assert dialog.selected_record() is None
    dialog.close()


def test_freshness_tracks_scans_slow_modules_and_partial_stalls():
    from types import SimpleNamespace as NS
    from azeo_control_trainer.azeo_operator_station.freshness import ScanFreshness
    def runtime(name, period):
        return NS(is_online=True, scan_count=10, is_debug_paused=False,
                  compiled=NS(graph=NS(name=name, scan_ms=period)))
    fast, slow = runtime("Fast", 500), runtime("Slow", 10000)
    tracker = ScanFreshness()
    assert tracker.sample([fast, slow], 100).last_update is None
    fast.scan_count += 1
    slow.scan_count += 1
    assert tracker.sample([fast, slow], 101).state == "LIVE"
    fast.scan_count += 1
    assert tracker.sample([fast, slow], 104).state == "LIVE"
    slow.scan_count += 1
    state = tracker.sample([fast, slow], 107)
    assert state.state == "STALE" and "Fast" in state.detail
    assert tracker.sample([fast, slow], 108, paused=True).state == "PAUSED"
    assert tracker.sample([], 109).state == "DISCONNECTED"
    assert tracker.sample([fast], 110).last_update is None


def test_zoom_and_pan_survive_navigation_and_resize(station, app):
    station.resize(1050, 760)
    station.show()
    app.processEvents()
    station.view.zoom_by(2)
    transform = station.view.transform()
    station.show_display("Unit")
    station.show_display("Plant")
    station.resize(1000, 740)
    app.processEvents()
    assert station.view.transform() == transform
    station.view.fit_display()
    assert not station.view._manual_view


def test_view_cache_is_bounded_and_eviction_releases_bindings(station):
    for index in range(9):
        document = PvmDisplay(name=f"Unit {index}")
        station.deployment.store.publish(document)
        station.show_display(document.name)
    assert len(station._view_cache) == 6
    assert all(not view._timer.isActive() for view in station._view_cache.values())


def test_alarm_cell_identity_selection_and_scroll_survive_new_priority(app):
    registry = RuntimeAlarmRegistry()
    registry.observe("Unit", "AI", [("HI", 11)])
    dialog = AlarmListDialog(registry, AlarmFilter(), lambda row: True)
    cell = dialog.table.item(0, 0)
    registry.observe("Other", "AI", [("HI_HI", 15)])
    dialog.refresh()
    assert dialog.table.item(0, 0) is cell
    assert dialog.selected_record().key == "Unit/AI/HI"
    dialog.can_operate = lambda: False
    assert dialog.acknowledge_selected() == ()
    assert not dialog.toggle_suppressed()
    dialog.close()


def test_workspace_reuses_tools_and_pauses_hidden_timers(station, app):
    from PySide6.QtCore import QTimer
    station.resize(1180, 800)
    station.show()
    first = QDialog()
    timer = QTimer(first)
    timer.start(150)
    second = QDialog()
    station.workspace.present("first", first, "First")
    app.processEvents()
    assert timer.isActive()
    station.workspace.present("second", second, "Second")
    app.processEvents()
    assert not timer.isActive()
    station.workspace.present("first", first, "First")
    app.processEvents()
    assert timer.isActive()
    station.workspace.hide()
    app.processEvents()
    assert not timer.isActive()
    station.workspace.show()
    app.processEvents()
    assert timer.isActive()


@pytest.mark.parametrize("dock_tools", [True, False])
def test_faceplates_are_detached_and_pins_preserve_independent_windows(station, app, dock_tools):
    from azeo_control_trainer.core.hmi.pvms.base import registry
    station.set_workspace_option("dock_context", dock_tools)
    station.show()
    app.processEvents()
    original_view_size = station.view.size()
    cls = registry.get("PID", "faceplate")
    station.open_faceplate(cls().place("one", path="M/PID1"))
    first = station.faceplates[0][1]
    assert first.isWindow()
    assert first.context_title.pin.isEnabled()
    first.show()
    app.processEvents()
    assert first.width() == first.faceplate_profile.width
    assert first.height() < first.faceplate_profile.height + 40
    first.move(30, 40)
    first_position = first.pos()
    QTest.mouseClick(first.context_title.pin, Qt.LeftButton)
    assert first.pinned
    station.open_faceplate(cls().place("two", path="M/PID2"))
    second = station.faceplates[-1][1]
    assert second.isWindow() and second is not first
    station.open_faceplate(cls().place("three", path="M/PID3"))
    third = station.faceplates[-1][1]
    assert not second.bound
    assert len(station.faceplates) == 2
    assert first.pos() == first_position and first.isVisible()
    third.show()
    QTest.mouseClick(third.context_title.pin, Qt.LeftButton)
    station.open_faceplate(cls().place("four", path="M/PID4"))
    assert len(station.faceplates) == 3
    station.open_faceplate(cls().place("one-again", path="M/PID1"))
    assert len(station.faceplates) == 3
    assert first.pos() == first_position
    assert station.workspace.get("equipment") is None
    assert not station.workspace.isVisible()
    assert station.view.size() == original_view_size
    first.close()
    assert not first.bound
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
    assert len(station.faceplates) == 2
    station.close()
    assert not station.faceplates


def test_layout_retained_view_drives_external_faceplate_requests(station):
    from azeo_control_trainer.core.hmi.pvms.base import registry

    station.show_display("Plant")
    retained = station.view
    station.views["Main"] = retained
    station.view = None
    cls = registry.get("PID", "faceplate")
    station.open_faceplate(cls().place("layout-loop", path="M/PID1"))
    assert station.current_display_view() is retained
    assert station.faceplates and station.faceplates[-1][1].bound

    station.faceplates[-1][1].close()
    station.faceplates.clear()
    station.views.clear()
    station.open_faceplate(cls().place("displayless-loop", path="M/PID2"))
    assert station.current_display_view() is None
    assert station.faceplates[-1][1].engine is station._context_engine
    assert station.faceplates[-1][1].bound


def test_authored_faceplates_are_detached_and_share_pin_replacement(station, app):
    from azeo_control_trainer.core.hmi.pvms.base import registry
    from azeo_control_trainer.core.hmi.pvms.user_library import UserPvmLibrary
    library = UserPvmLibrary(station.deployment.store.root)
    assert library.create_faceplate_blueprint("First")
    assert library.create_faceplate_blueprint("Second")
    first = station.open_user_faceplate("First")
    assert first is not None and first.isWindow()
    first.show()
    app.processEvents()
    assert first.width() == 204
    QTest.mouseClick(first.context_title.pin, Qt.LeftButton)
    assert first.pinned
    second = station.open_user_faceplate("Second")
    assert len(station.user_faceplates) == 2
    assert station.open_user_faceplate("First") is first
    cls = registry.get("PID", "faceplate")
    station.open_faceplate(cls().place("loop", path="M/PID1"))
    assert second._disposed
    assert station.user_faceplates == [first]
    assert len(station.faceplates) == 1
    assert not station.workspace.isVisible()
    station.close()
    assert first._disposed


def test_detached_faceplate_fits_a_small_desktop(station, app, monkeypatch):
    from types import SimpleNamespace
    from PySide6.QtGui import QCursor
    from azeo_control_trainer.core.hmi.pvms.base import registry
    available = QRect(0, 0, 960, 500)
    monkeypatch.setattr(station, "screen", lambda: SimpleNamespace(availableGeometry=lambda: available))
    QCursor.setPos(available.bottomRight())
    cls = registry.get("PID", "faceplate")
    station.open_faceplate(cls().place("loop", path="M/PID1"))
    faceplate = station.faceplates[0][1]
    faceplate.show()
    app.processEvents()
    assert available.contains(faceplate.frameGeometry())
    assert faceplate.faceplate_surface.size().height() == 537
    assert faceplate._surface_scroll.verticalScrollBar().maximum() > 0
    faceplate.toggle_mini_faceplate()
    app.processEvents()
    assert faceplate.height() < 180
    faceplate.toggle_mini_faceplate()
    app.processEvents()
    assert available.contains(faceplate.frameGeometry())
    QTest.mouseClick(faceplate.context_title.pin, Qt.LeftButton)
    station.open_faceplate(cls().place("next-loop", path="M/PID2"))
    second = station.faceplates[-1][1]
    second.show()
    app.processEvents()
    assert not faceplate.frameGeometry().intersects(second.frameGeometry())


def test_repeated_faceplate_close_and_navigation_releases_windows(station, app, monkeypatch):
    import gc
    import weakref
    from collections import Counter
    from azeo_control_trainer.core.hmi.pvms.base import registry
    from azeo_control_trainer.core.presentation import headless
    monkeypatch.setattr(headless, "is_headless", lambda: False)
    references = []
    station.show()
    initial_windows = Counter(type(widget).__name__ for widget in app.topLevelWidgets())
    for index in range(16):
        station.show_display("Plant" if index % 2 else "Unit")
        cls = registry.get("AI" if index % 2 else "PID", "faceplate")
        station.open_faceplate(cls().place(str(index), path=f"M/POINT{index}"))
        faceplate = station.faceplates[-1][1]
        assert faceplate.isWindow() and faceplate.isVisible()
        assert all(widget.font().pointSizeF() > 0 for widget in faceplate.findChildren(QWidget))
        references.append(weakref.ref(faceplate))
        faceplate.close()
        del faceplate
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QTest.qWait(10)
        gc.collect()
    assert not station.faceplates
    assert all(reference() is None for reference in references)
    remaining_windows = Counter(type(widget).__name__ for widget in app.topLevelWidgets())
    assert not (remaining_windows - initial_windows), remaining_windows - initial_windows


def test_operator_faceplate_is_constructed_with_final_window_owner(station, monkeypatch):
    from azeo_control_trainer.core.hmi.pvms.render import PvmFaceplateWidget
    from azeo_control_trainer.core.hmi.pvms.base import registry
    original = PvmFaceplateWidget.__init__
    constructed = []

    def observe(self, *args, **kwargs):
        original(self, *args, **kwargs)
        constructed.append((self.parentWidget(), self.isWindow()))

    monkeypatch.setattr(PvmFaceplateWidget, "__init__", observe)
    station.show_display("Plant")
    station.open_faceplate(registry.get("AI", "faceplate")().place("one", path="M/AI"))
    assert constructed == [(station, True)]


def _title_pointer(title, event_type, global_position, buttons):
    button = Qt.NoButton if event_type == QEvent.MouseMove else Qt.LeftButton
    event = QMouseEvent(event_type, QPointF(title.mapFromGlobal(global_position)),
                        QPointF(global_position), button, buttons, Qt.NoModifier)
    QApplication.sendEvent(title, event)


@pytest.mark.parametrize("pinned", [False, True])
@pytest.mark.parametrize("authored", [False, True])
def test_faceplate_title_drag_tracks_pointer_without_jumps(station, app, pinned, authored):
    from azeo_control_trainer.core.hmi.pvms.base import registry
    station.show()
    if authored:
        from azeo_control_trainer.core.hmi.pvms.user_library import UserPvmLibrary
        UserPvmLibrary(station.deployment.store.root).create_faceplate_blueprint("Drag test")
        faceplate = station.open_user_faceplate("Drag test")
    else:
        cls = registry.get("PID", "faceplate")
        station.open_faceplate(cls().place("loop", path="M/PID1"))
        faceplate = station.faceplates[0][1]
    faceplate.show()
    faceplate.move(140, 80)
    app.processEvents()
    if pinned:
        faceplate.toggle_pin()
    app.processEvents()
    title = faceplate.context_title
    assert faceplate.childAt(title.mapTo(faceplate, title.title.geometry().center())) is title
    assert faceplate.childAt(title.pin.mapTo(faceplate, title.pin.rect().center())) is title.pin
    origin, size, station_origin = faceplate.pos(), faceplate.size(), station.pos()
    initial_title = (title.height(), title.sizeHint().height(), title.pin.height())
    pointer = title.mapToGlobal(title.title.geometry().center())
    _title_pointer(title, QEvent.MouseButtonPress, pointer, Qt.LeftButton)
    for delta in (QPoint(12, 4), QPoint(120, 45), QPoint(35, 20), QPoint(-40, -25)):
        _title_pointer(title, QEvent.MouseMove, pointer + delta, Qt.LeftButton)
        app.processEvents()
        assert faceplate.pos() == origin + delta
    _title_pointer(title, QEvent.MouseButtonRelease, pointer + delta, Qt.NoButton)
    final_position = faceplate.pos()
    _title_pointer(title, QEvent.MouseMove, pointer + QPoint(300, 200), Qt.NoButton)
    assert faceplate.pos() == final_position
    assert faceplate.size() == size and station.pos() == station_origin, (
        size, faceplate.size(), initial_title,
        (title.height(), title.sizeHint().height(), title.pin.height()),
        faceplate.minimumSize(), faceplate.maximumSize())
    assert faceplate.pinned is pinned
    assert title._drag_offset is None


def test_hiding_faceplate_cancels_an_incomplete_title_drag(station, app):
    from azeo_control_trainer.core.hmi.pvms.base import registry
    cls = registry.get("PID", "faceplate")
    station.open_faceplate(cls().place("loop", path="M/PID1"))
    faceplate = station.faceplates[0][1]
    faceplate.show()
    app.processEvents()
    title = faceplate.context_title
    pointer = title.mapToGlobal(title.title.geometry().center())
    _title_pointer(title, QEvent.MouseButtonPress, pointer, Qt.LeftButton)
    faceplate.hide()
    assert title._drag_offset is None


def test_command_bar_native_focus_and_compact_density(station, app):
    station.resize(1180, 760)
    station.show()
    app.processEvents()
    button = station.menu._controls["history"]
    button.setFocus()
    QTest.keyClick(button, Qt.Key_Space)
    app.processEvents()
    assert station.workspace.get("trends") is None
    first = station.process_history_views[-1]
    assert first.isWindow()
    station.open_process_history()
    assert station.process_history_views == [first]
    station.set_workspace_option("comfortable", False)
    assert station.menu.height() == 34


def test_changed_binding_only_repaints_its_own_pvm(app):
    from azeo_control_trainer.core.hmi.pvms.base import Pvm
    from azeo_control_trainer.core.hmi.pvms.rendering.viewer import PvmDisplayView
    from azeo_control_trainer.core.hmi.binding.result import BindingResult
    from azeo_control_trainer.core.strategy.model.terminal import Quality
    from types import SimpleNamespace
    values = {"M/AI1/OUT": 10.0, "M/AI2/OUT": 20.0}
    source = SimpleNamespace(alarm_state=RuntimeAlarmRegistry(),
                             read=lambda path: BindingResult(value=values.get(path), quality=Quality.GOOD))
    document = PvmDisplay(name="Two values", pvms=[Pvm(
        id=name, pvm_class="", block_type="AI", role="dynamo_compact",
        params={"path": f"M/{name}"}, x=x, y=0, w=100, h=50).to_dict()
        for name, x in (("AI1", 0), ("AI2", 120))])
    view = PvmDisplayView(document.to_dict(), lambda: {}, source=source, live=False)
    view.refresh()
    counts = {"AI1": 0, "AI2": 0}
    for item in view._refresh_items:
        def update(*_, name=item.pvm.id):
            counts[name] += 1
        item.update = update
    values["M/AI1/OUT"] = 15
    view.engine.poll()  # A faceplate can poll this shared engine first.
    view.refresh()
    assert counts == {"AI1": 1, "AI2": 0}
    view.close()


def test_bound_refresh_reads_module_inventory_once():
    from azeo_control_trainer.core.hmi.binding import BindingEngine, LiveGraphSource
    calls = []
    def graphs():
        calls.append(True)
        return {}
    engine = BindingEngine(LiveGraphSource(graphs))
    engine.bind("M/AI1/OUT")
    engine.bind("M/AI2/OUT")
    calls.clear()
    engine.poll()
    assert len(calls) == 1


def test_command_feedback_reports_actual_acceptance_and_refusal(station):
    from types import SimpleNamespace
    from azeo_control_trainer.core.hmi.binding import WriteResult
    station.live_source = SimpleNamespace(
        read=lambda path: SimpleNamespace(value=50, units="m3/h"),
        can_write=lambda path: WriteResult(True),
        write=lambda path, value: WriteResult(False, "Output is not in MAN"))
    result = station._write("M/PID/OUT", 60)
    assert not result.success
    assert "Rejected" in station.command_feedback.text()
    assert "Output is not in MAN" in station.command_feedback.text()
    assert "50 → 60 m3/h" in station.command_feedback.text()
    assert not station._feedback_timer.isActive()
    station.live_source.write = lambda path, value: WriteResult(True)
    assert station._write("M/PID/SP", 55).success
    assert "Accepted" in station.command_feedback.text()
    assert station._feedback_timer.isActive()


def test_narrow_detached_trend_keeps_plot_and_value_table_reachable(station, app):
    station.resize(1100, 760)
    station.show()
    trend = station.open_process_history()
    trend.resize(740, 650)
    trend.show()
    app.processEvents()
    assert trend.isWindow()
    assert not trend._chart._legend_scroll.isVisible()
    assert trend._chart._plot_widget.width() < trend.width()
    assert trend._table.mapTo(trend, QPoint(0, trend._table.height())).y() <= trend.height()
    assert trend._table.height() >= 132
    assert trend._table.isColumnHidden(3)
    trend._details.click()
    assert not trend._table.isColumnHidden(3)


def test_popout_keeps_tall_tools_reachable_and_releases_them(station, app):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QScrollArea
    from shiboken6 import isValid

    station.show()
    tool = QDialog()
    tool.setMinimumSize(700, 1600)
    timer = QTimer(tool)
    timer.start(100)
    station.workspace.present("tall", tool, "Tall tool")
    station.workspace.detach_current()
    app.processEvents()
    window = tool.window()
    assert window.height() < window.screen().availableGeometry().height()
    scroll = window.findChild(QScrollArea)
    assert scroll is not None and scroll.verticalScrollBar().maximum() > 0
    window.close()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
    assert not isValid(tool)


def test_workspace_does_not_restart_a_deleted_child_timer(station, app):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QWidget

    station.show()
    tool = QDialog()
    child = QWidget(tool)
    timer = QTimer(child)
    timer.start(100)
    station.workspace.present("first", tool, "First")
    station.workspace.present("second", QDialog(), "Second")
    child.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
    station.workspace.present("first", tool, "First")
    assert station.workspace.tabs.currentWidget().widget() is tool


def test_equipment_click_reaches_faceplate_through_a_decorative_panel(station, app):
    from azeo_control_trainer.core.hmi.pvms.base import Pvm
    from azeo_control_trainer.core.hmi.pvms.rendering.items import PvmItem, StaticItem

    document = PvmDisplay(name="Decorated loop", width=320, height=180, pvms=[Pvm(
        id="loop", pvm_class="", block_type="PID", role="dynamo_inline",
        params={"path": "UNIT/PID1"}, x=50, y=60, w=160, h=54).to_dict()], items=[
            {"id": "card", "kind": "round_rect", "x": 20, "y": 20,
             "w": 260, "h": 120, "fill": "none", "z": 10}])
    station.deployment.store.publish(document)
    station.show_display(document.name)
    station.resize(1100, 760)
    station.show()
    app.processEvents()
    view = station.view
    loop = next(item for item in view.scene().items() if isinstance(item, PvmItem))
    point = view.mapFromScene(loop.mapToScene(loop.rect().center()))
    assert isinstance(view.itemAt(point), StaticItem)
    QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=point)
    app.processEvents()
    assert len(station.faceplates) == 1
    assert station.faceplates[0][1].isWindow()
    assert station.workspace.get("equipment") is None
    assert not station.workspace.isVisible()


@pytest.mark.parametrize("title,revision", [
    ("Overview - L1 Plant", 7), ("U300 - L2 Charge Heating", 4)])
def test_published_plant_equipment_is_clickable(title, revision, app):
    import json
    from azeo_control_trainer.core.hmi.pvms.rendering.viewer import PvmDisplayView
    from azeo_control_trainer.core.hmi.pvms.rendering.items import PvmItem

    root = Path(__file__).resolve().parents[1] / "projects/AzeoPlantVirtualController/displays/pvm"
    document = json.loads((root / title / "revisions" / f"{revision}.json").read_text(encoding="utf-8"))
    activated = []
    view = PvmDisplayView(document, lambda: {}, live=False,
                          pvm_activation_handler=lambda pvm, engine: activated.append(pvm.id))
    view.resize(1480, 780)
    view.show()
    app.processEvents()
    try:
        items = [item for item in view.scene().items() if isinstance(item, PvmItem)]
        assert items
        for item in items:
            point = view.mapFromScene(item.mapToScene(item.rect().center()))
            before = len(activated)
            QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=point)
            assert activated[before:] == [item.pvm.id], item.pvm.id
    finally:
        view.close()


@pytest.mark.parametrize("enabled", [True, False])
def test_authored_hotspot_blocks_equipment_behind_it(station, app, enabled):
    from azeo_control_trainer.core.hmi.pvms.base import Pvm
    from azeo_control_trainer.core.hmi.pvms.rendering.items import PvmItem

    document = PvmDisplay(name="Hotspot", width=320, height=180, pvms=[Pvm(
        id="loop", pvm_class="", block_type="PID", role="dynamo_inline",
        params={"path": "UNIT/PID1"}, x=50, y=60, w=160, h=54).to_dict()], items=[
            {"id": "control", "kind": "round_rect", "x": 20, "y": 20,
             "w": 260, "h": 120, "enabled": enabled, "z": 10,
             "actions": [{"event": "click", "kind": "open_display", "target": "Unit"}]}])
    station.deployment.store.publish(document)
    station.show_display(document.name)
    station.show()
    app.processEvents()
    view = station.view
    loop = next(item for item in view.scene().items() if isinstance(item, PvmItem))
    point = view.mapFromScene(loop.mapToScene(loop.rect().center()))
    QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=point)
    app.processEvents()
    assert not station.faceplates
    assert station.history.current == ("Unit" if enabled else "Hotspot")
