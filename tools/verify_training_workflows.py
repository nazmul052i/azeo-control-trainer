"""Bounded real-controller training checkout, using a disposable project copy.

Run with --native to capture Windows widgets; QT_SCALE_FACTOR can select DPI.
The original project and its displays are never edited by this verification.
"""
from __future__ import annotations

import json
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
native = "--native" in sys.argv
os.environ["QT_QPA_PLATFORM"] = "windows" if native else "offscreen"
out = ROOT / "logs" / "engineering-training-20260907" / (
    ("native-" if native else "offscreen-") + os.environ.get("QT_SCALE_FACTOR", "1"))
out.mkdir(parents=True, exist_ok=True)


def main():
    from PySide6.QtCore import QSettings, QTimer, qInstallMessageHandler
    from PySide6.QtWidgets import QApplication, QWidget
    from PySide6.QtTest import QTest
    from azeo_control_trainer.config import paths
    from azeo_control_trainer.core.presentation import headless
    from azeo_control_trainer.azeo_graphics_designer import HmiStudioWindow
    from azeo_control_trainer.azeo_graphics_designer.engineering_tools import AssemblyDialog, CommissioningDialog
    import azeo_control_trainer.app as application

    report = {"windows": [], "qt_messages": []}
    qInstallMessageHandler(lambda _kind, _context, message: report["qt_messages"].append(message))
    headless.is_headless = lambda: True
    logging.disable(logging.WARNING)
    with tempfile.TemporaryDirectory(prefix="azeo-training-check-") as temporary:
        temp = Path(temporary)
        project = temp / "project"
        shutil.copytree(ROOT / "projects" / "AzeoPlantVirtualController", project,
                        ignore=shutil.ignore_patterns(".lock", ".lock.recover"))
        configuration = json.loads((project / "_project.json").read_text(encoding="utf-8"))

        def relocate(node):
            if isinstance(node, dict):
                if node.get("factory") == "azeoplant.embedding:create_embedded_plant":
                    node["search_paths"] = [str(ROOT / "AzeoPlantSimulator")]
                for value in node.values():
                    relocate(value)
            elif isinstance(node, list):
                for value in node:
                    relocate(value)
        relocate(configuration)
        (project / "_project.json").write_text(json.dumps(configuration), encoding="utf-8")
        paths.data_dir = lambda: temp / "data"
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(temp / "settings"))
        original_exec = QApplication.exec

        def capture(name, window):
            available = window.screen().availableGeometry()
            requested = (min(1180, available.width() - 40), min(840, available.height() - 60))
            window.resize(*requested)
            window.showNormal()
            QTest.qWait(120)
            window.grab().save(str(out / f"{name}.png"))
            fonts = [(type(w).__name__, w.font().pointSizeF()) for w in window.findChildren(QWidget)
                     if w.font().pointSizeF() <= 0]
            report["windows"].append({"name": name, "requested": requested,
                                       "size": [window.width(), window.height()], "invalid_fonts": fonts})
            window.hide()
            assert not fonts, (name, fonts)
            assert window.width() <= available.width() and window.height() <= available.height(), name

        def bounded_exec(_app):
            app = QApplication.instance()
            logging.disable(logging.WARNING)
            qInstallMessageHandler(lambda _kind, _context, message: report["qt_messages"].append(message))
            for widget in app.topLevelWidgets():
                widget.hide()
            started = time.monotonic()
            state = {"phase": 0}

            def advance():
                try:
                    station = next((getattr(w, "_live_station_window", None) for w in app.topLevelWidgets()
                                    if getattr(w, "_live_station_window", None) is not None), None)
                    if station is None:
                        if time.monotonic() - started > 60:
                            names = [(type(w).__name__, w.windowTitle()) for w in app.topLevelWidgets()]
                            raise AssertionError(f"The application did not create its Operator Station: {names}")
                        QTimer.singleShot(250, advance)
                        return
                    session = station.training_session
                    graph_map = station.graphs_provider()
                    if not session or not graph_map or not session.workbench.process_capabilities().get("running"):
                        if time.monotonic() - started > 45:
                            raise AssertionError("Controller/process did not become ready")
                        QTimer.singleShot(250, advance)
                        return
                    if state["phase"] == 0:
                        module = "FIC-0101"
                        graph = graph_map[module]
                        pid = next(b for b in graph.blocks.values() if b.block_type == "PID")
                        ai = next(b for b in graph.blocks.values() if b.block_type == "AI")
                        loop, input_path = f"{module}/{pid.instance_name}", f"{module}/{ai.instance_name}"
                        if station.live_source.read(input_path + "/OUT").quality.name != "GOOD":
                            QTimer.singleShot(250, advance)
                            return
                        training = station.open_training()
                        training.loop.setCurrentText(loop)
                        training.input.setCurrentText(input_path)
                        training.capture()
                        training.save_exercise()
                        training.start()
                        training.quality.setCurrentText("BAD")
                        training.inject()
                        state.update(phase=1, station=station, training=training, loop=loop, input=input_path)
                        QTimer.singleShot(1500, advance)
                        return
                    if state["phase"] == 1:
                        result = station.live_source.read(state["input"] + "/OUT")
                        assert result.quality.name == "BAD", result
                        capture("training-exercise", state["training"])
                        session.clear_faults()
                        state["phase"] = 2
                        QTimer.singleShot(1500, advance)
                        return
                    result = station.live_source.read(state["input"] + "/OUT")
                    assert result.quality.name == "GOOD", result
                    session.tick()
                    diagnosis = station.open_loop_diagnostics(state["loop"])
                    capture("loop-diagnosis", diagnosis)
                    investigation = station.open_alarm_investigation()
                    capture("alarm-investigation", investigation)
                    state["training"].tabs.setCurrentIndex(1)
                    state["training"].show()
                    state["training"].refresh()
                    capture("session-timeline", state["training"])
                    session.finish("Verified live input fault and recovery through the real controller")
                    state["training"].reload_sessions()
                    state["training"].tabs.setCurrentIndex(2)
                    capture("session-report", state["training"])
                    (out / "session.json").write_text(json.dumps(session.archive.read(session.identity), indent=2), encoding="utf-8")
                    capture("operator-live", station)
                    session.workbench.pause()
                    graphics = HmiStudioWindow(lambda: graph_map, project / "displays" / "pvm")
                    assembly = AssemblyDialog(graphics.current())
                    assembly.source.setCurrentIndex(1)
                    ao = next((f"{m}/{b.instance_name}" for m, g in graph_map.items()
                               for b in g.blocks.values() if b.block_type == "AO"))
                    for row in range(assembly.mapping.rowCount()):
                        target = state["loop"] if assembly.mapping.item(row, 0).text() == "LOOP/PID" else ao
                        assembly.mapping.cellWidget(row, 1).setCurrentText(target)
                    assembly.preview()
                    capture("studio-assemblies", assembly)
                    assembly.apply()
                    checklist = CommissioningDialog(graphics.current())
                    checklist.path.setText(state["loop"] + "/PV")
                    checklist.add_cases()
                    checklist.check_all()
                    capture("studio-commissioning", checklist)
                    checklist.preview_case()
                    capture("studio-test-preview", graphics)
                    checklist.restore_preview()
                    checklist.close()
                    assembly.close()
                    graphics.close()
                    report["fault_recovery"] = "GOOD → BAD → GOOD"
                    report["session_samples"] = len(session.archive.read(session.identity)["samples"])
                    assert not report["qt_messages"], report["qt_messages"]
                    station.close()
                    app.quit()
                except Exception:
                    import traceback
                    report["error"] = traceback.format_exc()
                    app.exit(1)
            QTimer.singleShot(300, advance)
            return original_exec()

        QApplication.exec = bounded_exec
        watchdog = threading.Timer(180, lambda: os._exit(124))
        watchdog.daemon = True
        watchdog.start()
        try:
            sys.argv = ["training-workflow-check", str(project), "--station"]
            result = application.main()
        finally:
            watchdog.cancel()
            QApplication.exec = original_exec
            (out / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
        return result


if __name__ == "__main__":
    raise SystemExit(main())
