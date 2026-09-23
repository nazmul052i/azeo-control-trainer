"""Capture real Workbench and training tools from a disposable APVC project.

The published image is a widget capture, not fabricated application artwork.
The original project, user settings and operator session are not modified.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_SCALE_FACTOR"] = "1"
os.environ["AZEO_LOG_DIR"] = str(ROOT / "logs" / "manual-capture")


def main():
    import tempfile

    from PySide6.QtCore import QSettings, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from azeo_control_trainer.config import paths
    from azeo_control_trainer.core.presentation import headless
    from azeo_control_trainer.core.strategy.serialization import strategy_io
    import azeo_control_trainer.app as application

    output = ROOT / "docs" / "images" / "user_manual"
    output.mkdir(parents=True, exist_ok=True)
    headless.is_headless = lambda: True
    original_exec = QApplication.exec
    with tempfile.TemporaryDirectory(prefix="azeo-manual-") as temporary:
        scratch = Path(temporary)
        project = scratch / "ManualTraining"
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
            app.setQuitOnLastWindowClosed(False)
            started = time.monotonic()

            def capture():
                try:
                    owner = next((w for w in app.topLevelWidgets()
                                  if getattr(w, "_live_station_window", None)), None)
                    if owner is None or not owner._live_station_window.graphs_provider():
                        if time.monotonic() - started > 80:
                            raise RuntimeError("The documentation controller did not start")
                        QTimer.singleShot(500, capture)
                        return
                    from azeo_control_trainer.azeo_simulation_workbench.window import SimulationWorkbenchDialog
                    dialog = SimulationWorkbenchDialog(
                        owner._store, owner._store.field_io_driver, project,
                        executive=owner.designer.controller_executive())
                    dialog.resize(1440, 820)
                    dialog.show()
                    dialog.tabs.setCurrentIndex(1)
                    QTest.qWait(250)
                    target = output / "simulation-workbench.png"
                    if not dialog.grab().save(str(target)):
                        raise RuntimeError(f"Could not save {target}")
                    print(f"Captured {target}", flush=True)
                    dialog.close()

                    from azeo_control_trainer.azeo_operator_station.training import (
                        AlarmInvestigationDialog, LoopDiagnosticsDialog, TrainingDialog,
                    )
                    station = owner._live_station_window
                    training = TrainingDialog(station)
                    training.presentation.setCurrentIndex(1)
                    training.loop.setCurrentText("FIC-0101/FIC-0101")
                    training.input.setCurrentText("FIC-0101/FT-0102")
                    training.capture()
                    training.save_exercise()
                    training.start()
                    training.resize(1440, 820)
                    training.show()
                    training.refresh()
                    QTest.qWait(400)
                    training.grab().save(str(output / "training-exercise.png"))

                    training.value.setValue(15.0)
                    training.quality.setCurrentText("GOOD")
                    training.inject()
                    QTest.qWait(2500)
                    station.tick()
                    station.training_session.clear_faults()
                    QTest.qWait(1800)
                    station.tick()
                    training.tabs.setCurrentIndex(1)
                    training.refresh()
                    if training.events.rowCount():
                        training.events.selectRow(training.events.rowCount() - 1)
                        training.inspect_event()
                    QTest.qWait(200)
                    training.grab().save(str(output / "session-timeline.png"))
                    training.hide()

                    diagnosis = LoopDiagnosticsDialog(station, "FIC-0101/FIC-0101")
                    diagnosis.resize(1440, 840)
                    diagnosis.show()
                    QTest.qWait(400)
                    diagnosis.refresh()
                    diagnosis.grab().save(str(output / "loop-diagnosis.png"))
                    diagnosis.close()
                    station.training_session.finish()
                    training.close()

                    # A labeled documentation fixture illustrates the real
                    # alarm UI without falsely claiming a process upset.
                    station.simulation_service.pause()
                    station._tick.stop()
                    for view in set(station.views.values()) | {station.view}:
                        if view is not None:
                            view._timer.stop()
                    station.alarm_state.observe("FIC-0101", "FT-0102", [("HI", 3, 1200.0)])
                    investigation = AlarmInvestigationDialog(station)
                    investigation.resize(1440, 820)
                    investigation.show()
                    investigation.summary.refresh()
                    from PySide6.QtCore import Qt
                    for index in range(investigation.summary.table.rowCount()):
                        if investigation.summary.table.item(index, 0).data(Qt.UserRole) == "FIC-0101/FT-0102/HI":
                            investigation.summary.table.selectRow(index)
                            break
                    investigation.refresh()
                    investigation.guidance.setPlainText(
                        "DOCUMENTATION EXAMPLE - verify the measurement quality and compare related "
                        "process values. Open the loop faceplate and trend, follow the prepared "
                        "exercise response, and record recovery. This text is a demonstration, "
                        "not a plant operating instruction.")
                    QTest.qWait(150)
                    investigation.grab().save(str(output / "alarm-investigation.png"))
                    investigation.close()
                    print("Captured current training, diagnosis and demonstration alarm tools", flush=True)
                    owner._live_station_window.close()
                    app.quit()
                except Exception:
                    import traceback
                    traceback.print_exc()
                    app.exit(1)

            QTimer.singleShot(1500, capture)
            return original_exec()

        QApplication.exec = bounded_exec
        try:
            sys.argv = ["manual-workbench", str(project), "--station"]
            return application.main()
        finally:
            QApplication.exec = original_exec


if __name__ == "__main__":
    raise SystemExit(main())
