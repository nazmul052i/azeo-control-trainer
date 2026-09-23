"""Verify historian workflows through a disposable, running APVC station.

Use --native for a desktop check or --review to leave it open after capture. All controller,
training, settings and historian changes are confined to the temporary project.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
REVIEW = "--review" in sys.argv
NATIVE = REVIEW or "--native" in sys.argv
if not NATIVE:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_SCALE_FACTOR"] = "1"
os.environ["AZEO_LOG_DIR"] = str(ROOT / "logs" / "history-live")


def main():
    from PySide6.QtCore import QSettings, QTimer, Qt
    from PySide6.QtGui import QContextMenuEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from azeo_control_trainer.config import paths
    from azeo_control_trainer.core.presentation import headless
    from azeo_control_trainer.core.strategy.serialization import strategy_io
    import azeo_control_trainer.app as application

    output = ROOT / "logs" / "history-live"
    output.mkdir(parents=True, exist_ok=True)
    headless.is_headless = lambda: not NATIVE
    original_exec = QApplication.exec
    with tempfile.TemporaryDirectory(prefix="azeo-history-review-") as temporary:
        scratch = Path(temporary)
        project = scratch / "HistoryReview"
        shutil.copytree(ROOT / "projects" / "AzeoPlantVirtualController", project,
                        ignore=shutil.ignore_patterns(".lock", ".lock.recover"))
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
        paths.data_dir = lambda: scratch / "data"
        strategy_io._SETTINGS_PATH = scratch / "settings.json"
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(scratch / "settings"))

        def bounded_exec(_app):
            app = QApplication.instance()
            app.setQuitOnLastWindowClosed(REVIEW)
            started = time.monotonic()

            def capture():
                try:
                    owner = next((w for w in app.topLevelWidgets() if getattr(w, "_live_station_window", None)), None)
                    if owner is None or not owner._live_station_window.graphs_provider():
                        if time.monotonic() - started > 90:
                            raise RuntimeError("The APVC controller did not start")
                        QTimer.singleShot(500, capture)
                        return
                    station = owner._live_station_window
                    station.resize(1500, 900)
                    station.show()
                    QTest.qWait(200)
                    from azeo_control_trainer.core.hmi.pvms.rendering.viewer import chart_candidates
                    source_view = station.view
                    targets = []
                    for item in source_view.scene().items():
                        points = chart_candidates(item)
                        if not points or points[0].path in [path for path, _ in targets]:
                            continue
                        point = source_view.mapFromScene(item.sceneBoundingRect().center())
                        if source_view._runtime_item_at(point, chart=True) is item:
                            targets.append((points[0].path, point))
                        if len(targets) == 2:
                            break
                    assert len(targets) == 2, "The running display must expose two real tags"
                    geometry = source_view.size()
                    for _, point in targets:
                        QTest.mouseClick(source_view.viewport(), Qt.LeftButton, Qt.ControlModifier, point)
                    assert not station.faceplates
                    source_view.viewport().grab().save(str(output / "selected-tags.png"))
                    point = targets[0][1]
                    event = QContextMenuEvent(QContextMenuEvent.Mouse, point,
                                             source_view.viewport().mapToGlobal(point))
                    QApplication.sendEvent(source_view.viewport(), event)
                    menu = source_view._last_chart_context_menu
                    assert menu.actions()[0].text() == "Add to Historian (2 tags)"
                    if NATIVE:
                        QTest.mouseClick(menu, Qt.LeftButton, pos=menu.actionGeometry(menu.actions()[0]).center())
                    else:
                        menu.actions()[0].trigger()
                        menu.close()
                    history = station.process_history_views[-1]
                    assert history._pens == [path for path, _ in targets]
                    assert history.isWindow() and station.workspace.get("trends") is None
                    assert source_view.size() == geometry
                    QTest.qWait(300)
                    history.grab().save(str(output / "selected-tags-historian.png"))
                    if NATIVE:
                        history.screen().grabWindow(0).save(str(output / "detached-historian-desktop.png"))
                    view = station.open_process_history("FIC-0101")
                    assert view is history
                    assert view._timer.isActive() and view._query_timer.isActive()
                    from azeo_control_trainer.core.simulation.training import Exercise
                    session = station.training_session
                    exercise = Exercise(name="Historian verification", loop="FIC-0101/FIC-0101",
                                        input_path="FIC-0101/FT-0102", snapshot=str(session.capture_baseline()))
                    for attempt in range(2):
                        session.start(exercise)
                        QTest.qWait(1200)
                        session.inject_input(exercise.input_path, 15 + attempt * 5, "GOOD")
                        QTest.qWait(1800)
                        station.tick()
                        session.clear_faults()
                        QTest.qWait(1500)
                        station.tick()
                        session.event("instructor_note", exercise.loop, "Disposable UI verification attempt")
                        session.finish("Historian verification complete")
                    view._chart.set_time_window(1)
                    view._save_group("Flow response review")
                    view._details.setChecked(False)
                    view._adapt_width()
                    QTest.qWait(300)
                    assert view._source.TAGS[exercise.loop + "/PV"].times[-1] >= station.historian.now() - 3
                    station.grab().save(str(output / "operator-history.png"))
                    view.grab().save(str(output / "history.png"))
                    view._tabs.setCurrentIndex(1)
                    view._update_events(force=True)
                    view.grab().save(str(output / "events.png"))
                    view._compare_runs()
                    comparison = view._comparison_dialogs[-1]
                    comparison.sessions.setCurrentIndex(1)
                    comparison.load("baseline")
                    comparison.sessions.setCurrentIndex(2)
                    comparison.load("trial")
                    for _ in range(40):
                        QTest.qWait(50)
                        if len(comparison._runs) == 2:
                            break
                    if len(comparison._runs) != 2:
                        raise RuntimeError(comparison.status.text())
                    QTest.qWait(200)
                    assert all(row.isVisible() for row in comparison.chart._legend_rows.values())
                    comparison.grab().save(str(output / "recorded-comparison.png"))
                    comparison.export(output / "recorded-comparison.html")
                    comparison.close()
                    from azeo_control_trainer.azeo_operator_station.training import LoopDiagnosticsDialog
                    diagnosis = LoopDiagnosticsDialog(station, exercise.loop)
                    diagnosis.resize(1366, 850)
                    diagnosis.show()
                    QTest.qWait(300)
                    diagnosis.grab().save(str(output / "loop-diagnosis.png"))
                    diagnosis.close()
                    station.historian.archive.flush()
                    from azeo_control_trainer.core.hmi.history.archive import HistoryArchive
                    reopened = HistoryArchive(station.historian.archive.path)
                    records = reopened.read([exercise.loop + "/PV"], 0, station.historian.now())
                    persisted = "group:Flow response review" in reopened.workspaces
                    reopened.close()
                    assert records["samples"][exercise.loop + "/PV"] and persisted
                    report = {"project": str(project), "archive_reopened": True, "group_persisted": persisted,
                              "selected_tags": [path for path, _ in targets], "detached_historian": True,
                              "process_geometry_preserved": source_view.size() == geometry,
                              "samples": len(records["samples"][exercise.loop + "/PV"]),
                              "events": len(records["events"]), "recorded_attempts": len(session.archive.sessions())}
                    (output / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
                    print(json.dumps(report), flush=True)
                    view._tabs.setCurrentIndex(0)
                    if REVIEW:
                        station.setWindowTitle("Azeo Operator Live — Historian review (disposable training project)")
                        station.showMaximized()
                        station.raise_()
                    else:
                        station.close()
                        app.quit()
                except Exception:
                    import traceback
                    traceback.print_exc()
                    app.exit(1)

            QTimer.singleShot(1600, capture)
            return original_exec()

        QApplication.exec = bounded_exec
        try:
            sys.argv = ["history-review", str(project), "--station"]
            return application.main()
        finally:
            QApplication.exec = original_exec


if __name__ == "__main__":
    raise SystemExit(main())
