"""Measure and inspect Operator Live on a disposable copy of the full plant."""
from __future__ import annotations

import json
import faulthandler
import logging
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
NATIVE = "--native" in sys.argv
NAVIGATION_PROFILE = "--navigation-profile" in sys.argv
NAVIGATION_BENCHMARK = "--navigation-benchmark" in sys.argv
TASK_BENCHMARK = "--task-benchmark" in sys.argv
LIFECYCLE_ONLY = "--lifecycle-only" in sys.argv
RESPONSIVENESS = "--responsiveness" in sys.argv
MONITOR_CHECK = "--monitor-check" in sys.argv
FACEPLATE_FEEDBACK = "--faceplate-feedback" in sys.argv
THEME_CHECK = "--themes" in sys.argv
THEME_PROFILE = "--themes-profile" in sys.argv
THEME_TIMING = "--themes-timing-only" in sys.argv
THEME_VISUAL = "--themes-visual-only" in sys.argv
THEME_EXAMPLES = "--themes-examples-only" in sys.argv
DISTILLATION_CHECK = "--distillation" in sys.argv
os.environ["QT_QPA_PLATFORM"] = "windows" if NATIVE else "offscreen"
if "--scale2" in sys.argv:
    os.environ["QT_SCALE_FACTOR"] = "2"
OUT = ROOT / "logs" / "operator-workspace" / os.environ.get("QT_SCALE_FACTOR", "1")
if "--output-dir" in sys.argv:
    OUT = Path(sys.argv[sys.argv.index("--output-dir") + 1]).resolve()
OUT.mkdir(parents=True, exist_ok=True)


def verification_exit_code(code, report):
    """Preserve failed task gates even when the application discards exec's code."""
    if report.get("python_errors") or report.get("error"):
        return 1
    if "responsiveness" in report:
        data = report["responsiveness"]
        navigation = data.get("navigation", {})
        if not navigation or not all(row.get("all_batches_passed", False) for row in navigation.values()):
            return 1
        if data.get("history_workload") and not data.get("pause_resume", {}).get("budget_passed", False):
            return 1
        if "soak" in data and not data["soak"].get("duration_and_lifecycle_passed", False):
            return 1
    return code


def main():
    faulthandler.enable()
    from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QSettings, QTimer, Qt, qInstallMessageHandler
    from PySide6.QtTest import QTest
    from PySide6.QtGui import QCursor, QMouseEvent
    from PySide6.QtWidgets import QApplication, QWidget
    from azeo_control_trainer.config import paths
    from azeo_control_trainer.core.presentation import headless
    from azeo_control_trainer.core.hmi.pvms.rendering.items import PvmItem
    import azeo_control_trainer.app as application

    report = {"screens": [], "qt_messages": []}
    if RESPONSIVENESS:
        from operator_responsiveness import ResponsivenessRun
        responsiveness = ResponsivenessRun(report, OUT)
        responsiveness.install_probes()
    handler_times = {}
    measuring = False
    if NAVIGATION_BENCHMARK:
        from functools import wraps
        from azeo_control_trainer.azeo_control_designer.executive import ControllerExecutive
        from azeo_control_trainer.azeo_operator_station.console import LiveStation
        from azeo_control_trainer.core.hmi.pvms.rendering.viewer import PvmDisplayView

        def measured(method):
            @wraps(method)
            def call(*args, **kwargs):
                before = time.perf_counter()
                try:
                    return method(*args, **kwargs)
                finally:
                    if measuring:
                        handler_times.setdefault(method.__qualname__, []).append(
                            (time.perf_counter() - before) * 1000)
            return call

        for kind, method in ((ControllerExecutive, "_advance_scan"),
                             (LiveStation, "tick"), (PvmDisplayView, "refresh")):
            setattr(kind, method, measured(getattr(kind, method)))
    headless.is_headless = lambda: True
    original_exec = QApplication.exec
    with tempfile.TemporaryDirectory(prefix="azeo-operator-ui-") as temporary:
        temp = Path(temporary)
        project = temp / "project"
        shutil.copytree(ROOT / "projects" / "AzeoPlantVirtualController", project,
                        ignore=shutil.ignore_patterns(".lock", ".lock.recover"))
        if DISTILLATION_CHECK:
            from create_distillation_display import plant_document
            from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore
            store = DisplayStore(project / "displays" / "pvm")
            document = plant_document()
            store.save_draft(document)
            store.publish(document, env="PROD", workstations=["CON-01"], by="Isolated visual check")
            from azeo_control_trainer.core.hmi.pvms.layout import LayoutStore
            layouts = LayoutStore(project / "displays" / "pvm")
            for display_set in layouts.display_sets():
                display_set.add_non_hierarchical(document.name)
                layouts.save_display_set(display_set)
        if THEME_EXAMPLES:
            from generate_azeo_plant_displays import DISPLAY_NAMES, install, publish_operator
            display_root = project / "displays" / "pvm"
            install(display_root, names=DISPLAY_NAMES)
            publish_operator(display_root, names=DISPLAY_NAMES)
        config = json.loads((project / "_project.json").read_text(encoding="utf-8"))

        def relocate(value):
            if isinstance(value, dict):
                if value.get("factory") == "azeoplant.embedding:create_embedded_plant":
                    value["search_paths"] = [str(ROOT / "AzeoPlantSimulator")]
                for child in value.values():
                    relocate(child)
            elif isinstance(value, list):
                for child in value:
                    relocate(child)
        relocate(config)
        (project / "_project.json").write_text(json.dumps(config), encoding="utf-8")
        paths.data_dir = lambda: temp / "data"
        from azeo_control_trainer.core.strategy.serialization import strategy_io
        strategy_io._SETTINGS_PATH = temp / "data" / "settings.json"
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(temp / "settings"))

        def bounded_exec(_app):
            app = QApplication.instance()
            app.setQuitOnLastWindowClosed(False)
            logging.disable(logging.WARNING)
            def qt_message(_kind, _context, message):
                report["qt_messages"].append(message)
                print("QT: " + message, flush=True)
            qInstallMessageHandler(qt_message)
            previous_hook = sys.excepthook

            def record_exception(kind, value, trace):
                import traceback
                report.setdefault("python_errors", []).append("".join(traceback.format_exception(kind, value, trace)))
                previous_hook(kind, value, trace)

            sys.excepthook = record_exception
            for widget in app.topLevelWidgets():
                widget.hide()
            started = time.monotonic()

            def capture(station, name):
                station.showNormal()
                QTest.qWait(160)
                image = OUT / f"{name}.png"
                station.grab().save(str(image))
                if "faceplate" in name or name == "equipment-comparison":
                    for index, (_key, faceplate) in enumerate(station.faceplates):
                        assert faceplate.isWindow() and faceplate.isVisible()
                        assert faceplate.screen().availableGeometry().contains(faceplate.frameGeometry())
                        faceplate.grab().save(str(OUT / f"{name}-{index}.png"))
                    if NATIVE:
                        geometry = station.frameGeometry()
                        station.screen().grabWindow(0, geometry.x(), geometry.y(),
                                                    geometry.width(), geometry.height()).save(str(image))
                invalid = [(type(w).__name__, w.font().pointSizeF())
                           for w in station.findChildren(QWidget) if w.font().pointSizeF() <= 0]
                available = station.screen().availableGeometry()
                assert station.width() <= available.width() and station.height() <= available.height()
                assert not invalid, invalid
                report["screens"].append({"name": name, "width": station.width(), "height": station.height()})
                print("Captured " + name, flush=True)

            def drag_faceplate(station, faceplate):
                title = faceplate.context_title
                station_position = station.pos()
                start = faceplate.pos()
                size, pinned = faceplate.size(), faceplate.pinned
                pointer = title.mapToGlobal(title.title.geometry().center())

                def send(kind, point, buttons):
                    button = Qt.NoButton if kind == QEvent.MouseMove else Qt.LeftButton
                    QApplication.sendEvent(title, QMouseEvent(
                        kind, QPointF(title.mapFromGlobal(point)), QPointF(point),
                        button, buttons, Qt.NoModifier))

                send(QEvent.MouseButtonPress, pointer, Qt.LeftButton)
                batches = []
                for _batch in range(3):
                    elapsed = 0.0
                    for step in range(24):
                        delta = QPoint((12 - abs(12 - step)) * 3, (step % 7) * 2)
                        before = time.perf_counter()
                        send(QEvent.MouseMove, pointer + delta, Qt.LeftButton)
                        elapsed += time.perf_counter() - before
                        assert faceplate.pos() == start + delta
                        QTest.qWait(16)
                    batches.append(elapsed / 24 * 1000)
                send(QEvent.MouseButtonRelease, pointer + delta, Qt.NoButton)
                assert faceplate.size() == size and faceplate.pinned is pinned
                assert station.pos() == station_position and title._drag_offset is None
                report["faceplate_drag_ms"] = min(batches)
                assert min(batches) < 16.7, batches
                faceplate.move(start)

            def verify():
                nonlocal measuring
                try:
                    station = next((getattr(w, "_live_station_window", None) for w in app.topLevelWidgets()
                                    if getattr(w, "_live_station_window", None) is not None), None)
                    if station is None or not station.graphs_provider() or station.status.status.liveness() == "NO DATA":
                        assert time.monotonic() - started < 65, "Controller/station did not become ready"
                        QTimer.singleShot(500, verify)
                        return
                    service = station.simulation_service
                    owner = next(w for w in app.topLevelWidgets()
                                 if getattr(w, "_live_station_window", None) is station)
                    assert len(service._runtimes()) == 160
                    available = station.screen().availableGeometry()
                    station.resize(min(1480, available.width() - 30), min(900, available.height() - 50))
                    station.showNormal()
                    station.workspace.hide()
                    if DISTILLATION_CHECK:
                        from distillation_check import check_distillation
                        check_distillation(station, OUT, report)
                        assert not report["qt_messages"], report["qt_messages"]
                        station.close()
                        app.quit()
                        return
                    if THEME_CHECK:
                        from operator_theme_check import check_operator_themes
                        check_operator_themes(station, OUT, report, profile_only=THEME_PROFILE,
                                              timing_only=THEME_TIMING, visual_only=THEME_VISUAL,
                                              examples_only=THEME_EXAMPLES)
                        assert not report["qt_messages"], report["qt_messages"]
                        station.close()
                        app.quit()
                        return
                    if FACEPLATE_FEEDBACK:
                        from faceplate_feedback_check import check_faceplate_feedback
                        report["faceplate_feedback"] = check_faceplate_feedback(station, OUT)
                        assert not report["qt_messages"], report["qt_messages"]
                        station.close()
                        app.quit()
                        return
                    if RESPONSIVENESS:
                        responsiveness.start(station)
                        return
                    capture(station, "overview")
                    report["liveness"] = station.status.status.liveness()
                    if NAVIGATION_PROFILE or NAVIGATION_BENCHMARK:
                        import cProfile
                        import pstats
                        profile = cProfile.Profile()
                        if NAVIGATION_PROFILE:
                            profile.enable()
                        measuring = True
                        report["navigation_samples"] = []
                        for target in ("Overview - L1 Plant", "U100 - L2 Feed Preparation",
                                       "U300 - L2 Charge Heating") * 3:
                            before = time.perf_counter()
                            assert station.show_display(target)
                            opened = time.perf_counter()
                            station.view.refresh()
                            station.view.viewport().repaint()
                            painted = time.perf_counter()
                            app.processEvents()
                            report["navigation_samples"].append({
                                "display": target, "open_ms": (opened-before)*1000,
                                "paint_ms": (painted-opened)*1000,
                                "events_ms": (time.perf_counter()-painted)*1000})
                            print(f"NAV {target}: open={(opened-before)*1000:.1f} "
                                  f"paint={(painted-opened)*1000:.1f} "
                                  f"events={(time.perf_counter()-painted)*1000:.1f} ms", flush=True)
                        for _ in range(3):
                            before = time.perf_counter()
                            station.tick()
                            print(f"TICK {(time.perf_counter()-before)*1000:.1f} ms", flush=True)
                        if NAVIGATION_PROFILE:
                            profile.disable()
                            pstats.Stats(profile).strip_dirs().sort_stats("cumulative").print_stats(45)
                        measuring = False
                        report["handlers_ms"] = {name: {"calls": len(values),
                            "mean": sum(values) / len(values), "max": max(values)}
                            for name, values in handler_times.items()}
                        station.close()
                        app.quit()
                        return
                    choices = station.choose_display()[:2]

                    def timings(cold):
                        batches = []
                        for _ in range(3):
                            elapsed = []
                            for target in choices * (1 if cold else 3):
                                if cold:
                                    station._clear_view_cache()
                                before = time.perf_counter()
                                station.show_display(target)
                                elapsed.append((time.perf_counter() - before) * 1000)
                            batches.append(sum(elapsed) / len(elapsed))
                        return min(batches)
                    report["cold_navigation_ms"] = timings(True)
                    report["warm_navigation_ms"] = timings(False)
                    if TASK_BENCHMARK and not LIFECYCLE_ONLY:
                        import math
                        import platform
                        from PySide6.QtCore import qVersion

                        def distribution(samples):
                            ordered = sorted(samples)
                            return {"samples_ms": samples, "count": len(samples),
                                    "p50_ms": ordered[math.ceil(len(ordered) * .50) - 1],
                                    "p95_ms": ordered[math.ceil(len(ordered) * .95) - 1],
                                    "max_ms": ordered[-1]}

                        workload = {"python": platform.python_version(), "qt": qVersion(),
                                    "python_thread_switch_ms": sys.getswitchinterval() * 1000,
                                    "scale": os.environ.get("QT_SCALE_FACTOR", "1"),
                                    "method": "display request through synchronous viewport repaint; best p95 of three 30-sample batches (AGENTS.md); all batches retained",
                                    "module_files": len(list((project / "control").glob("*.json")))}
                        targets = ["Overview - L1 Plant", "U100 - L2 Feed Preparation",
                                   "U300 - L2 Charge Heating"]
                        for cold in (True, False):
                            batches = []
                            for batch in range(3):
                                samples, delivery = [], []
                                if not cold:
                                    for target in targets:
                                        assert station.show_display(target)
                                        station.view.viewport().repaint()
                                        app.processEvents()
                                        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                                for target in targets * 10:
                                    if cold:
                                        station._clear_view_cache()
                                    before = time.perf_counter()
                                    QTimer.singleShot(0, lambda start=before: delivery.append(
                                        (time.perf_counter() - start) * 1000))
                                    assert station.show_display(target)
                                    station.view.viewport().repaint()
                                    samples.append((time.perf_counter() - before) * 1000)
                                    app.processEvents()
                                    # The benchmark runs inside one timer callback.
                                    # processEvents alone does not service deferred
                                    # deletion as a return to Qt's event loop does.
                                    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                                batches.append({"navigation": distribution(samples),
                                                "event_delivery": distribution(delivery)})
                                print(f"{'Cold' if cold else 'Warm'} navigation batch {batch + 1}: "
                                      f"p95 {batches[-1]['navigation']['p95_ms']:.1f} ms", flush=True)
                            key = "cold" if cold else "warm"
                            best = min(batches, key=lambda row: row["navigation"]["p95_ms"])
                            workload[key] = best["navigation"]
                            workload[key + "_event_delivery"] = best["event_delivery"]
                            workload[key + "_batches"] = batches
                            workload[key + "_all_samples"] = distribution([
                                value for row in batches for value in row["navigation"]["samples_ms"]])
                        report["task_benchmark"] = workload
                        workload["budgets_passed"] = (workload["warm"]["p95_ms"] <= 150
                                                      and workload["cold"]["p95_ms"] <= 500)
                        (OUT / "task-benchmark.json").write_text(json.dumps(workload, indent=2), encoding="utf-8")
                    elif LIFECYCLE_ONLY:
                        report["task_benchmark"] = {"budgets_passed": None, "method": "lifecycle only"}
                    station.navigate("home")
                    overview_item = next(item for item in station.view.scene().items()
                                         if isinstance(item, PvmItem) and item.pvm.block_type == "AI")
                    point = station.view.mapFromScene(overview_item.mapToScene(overview_item.rect().center()))
                    headless.is_headless = lambda: False
                    process_size = station.view.size()
                    QCursor.setPos(station.view.viewport().mapToGlobal(point))
                    QTest.mouseClick(station.view.viewport(), Qt.LeftButton, pos=point)
                    assert len(station.faceplates) == 1 and not station.workspace.isVisible()
                    assert station.view.size() == process_size
                    capture(station, "overview-faceplate")
                    station.faceplates[0][1].close()
                    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                    if TASK_BENCHMARK:
                        import gc
                        from shiboken6 import isValid
                        counts = []
                        for cycle in range(50):
                            print(f"Faceplate cycle {cycle + 1}", flush=True)
                            QTest.mouseClick(station.view.viewport(), Qt.LeftButton, pos=point)
                            assert len(station.faceplates) == 1
                            station.faceplates[0][1].close()
                            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                            app.processEvents()
                            gc.collect()
                            assert not station.faceplates
                            counts.append(sum(isValid(w) for w in app.topLevelWidgets()))
                        report["task_benchmark"]["faceplate_cycles"] = {
                            "cycles": 50, "remaining_faceplates": len(station.faceplates),
                            "top_level_widget_counts": counts}
                        assert max(counts[-10:]) <= max(counts[:10]), counts
                    assert station.show_display("U300 - L2 Charge Heating")
                    QTest.qWait(100)
                    loops = [item for item in station.view.scene().items()
                             if isinstance(item, PvmItem) and item.pvm.block_type == "PID"][:2]
                    assert len(loops) == 2
                    for index, item in enumerate(loops):
                        point = station.view.mapFromScene(item.mapToScene(item.rect().center()))
                        QCursor.setPos(station.view.viewport().mapToGlobal(point))
                        QTest.mouseClick(station.view.viewport(), Qt.LeftButton, pos=point)
                        assert len(station.faceplates) == index + 1
                        assert station.context_path == item.pvm.params["path"]
                        QTest.mouseClick(station.faceplates[-1][1].context_title.pin, Qt.LeftButton)
                        assert station.faceplates[-1][1].pinned
                        QTest.qWait(100)
                    assert station.faceplates[0][1].pos() != station.faceplates[1][1].pos()
                    if MONITOR_CHECK:
                        from physical_monitor_check import check_monitor_moves
                        report["physical_monitors"] = check_monitor_moves(
                            [("station", station), ("faceplate", station.faceplates[0][1])], OUT)
                    drag_faceplate(station, station.faceplates[0][1])
                    report["equipment_clicks"] = "Overview AI and two unit PID PVMs opened separate windows; both PID windows pinned"
                    capture(station, "equipment-comparison")
                    headless.is_headless = lambda: True
                    station.show_alarm_list()
                    capture(station, "alarms")
                    loop_path = loops[0].pvm.params["path"]
                    station.open_process_history(loop_path.partition("/")[0])
                    capture(station, "trend")
                    training = station.open_training()
                    capture(station, "training-trainee")
                    training.presentation.setCurrentIndex(1)
                    capture(station, "training-instructor")
                    station.open_loop_diagnostics(loop_path)
                    capture(station, "diagnosis")
                    station.workspace.hide()
                    station.set_workspace_option("comfortable", False)
                    capture(station, "compact")
                    service.pause()
                    station.tick()
                    assert station.status.status.liveness() == "PAUSED", station.status.status
                    capture(station, "paused")
                    service.resume()
                    # Resume starts asynchronous, budgeted module scans. Check
                    # their completion, not an arbitrary 1.2-second sleep after
                    # a stress run with many queued Qt events.
                    resumed_at = time.perf_counter()
                    while time.perf_counter() - resumed_at < 5:
                        QTest.qWait(100)
                        station.tick()
                        if station.status.status.source_state == "LIVE":
                            break
                    report["resume_to_fresh_ms"] = (time.perf_counter() - resumed_at) * 1000
                    assert station.status.status.source_state == "LIVE", (
                        station.status.status, station.status.toolTip())
                    report["scan_states"] = "LIVE -> PAUSED -> LIVE"
                    station.close()
                    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                    app.processEvents()
                    assert owner._live_station_window is None
                    reopened = owner._live_station()
                    reopened.resize(min(1480, available.width() - 30), min(900, available.height() - 50))
                    QTest.qWait(1500)
                    reopened.tick()
                    assert reopened._tick.isActive() and reopened.view._timer.isActive()
                    assert len(reopened.graphs_provider()) == 160
                    capture(reopened, "reopened")
                    reopened.close()
                    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                    app.processEvents()
                    report["close_reopen"] = "Passed"
                    assert not report["qt_messages"], report["qt_messages"]
                    if TASK_BENCHMARK and not LIFECYCLE_ONLY:
                        assert report["task_benchmark"]["budgets_passed"], "Navigation p95 exceeds release budget"
                    app.quit()
                except Exception:
                    import traceback
                    report["error"] = traceback.format_exc()
                    app.exit(1)
            QTimer.singleShot(1000, verify)
            return original_exec()

        QApplication.exec = bounded_exec
        def timed_out():
            faulthandler.dump_traceback()
            os._exit(124)

        extra = float(sys.argv[sys.argv.index("--soak-seconds") + 1]) if "--soak-seconds" in sys.argv else 0
        watchdog = threading.Timer(extra + (900 if RESPONSIVENESS or THEME_EXAMPLES else 360 if TASK_BENCHMARK else 240), timed_out)
        watchdog.daemon = True
        watchdog.start()
        try:
            sys.argv = ["operator-workspace-check", str(project), "--station"]
            code = application.main()
            code = verification_exit_code(code, report)
        finally:
            QApplication.exec = original_exec
            watchdog.cancel()
            (OUT / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
        return code


if __name__ == "__main__":
    raise SystemExit(main())
