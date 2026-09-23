"""Exercise quality, PA assemblies and release UI in a disposable native project."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["QT_QPA_PLATFORM"] = "windows" if "--native" in sys.argv else "offscreen"
OUT = ROOT / "logs" / "graphics-quality"
OUT.mkdir(parents=True, exist_ok=True)


def main():
    from PySide6.QtCore import QSettings, Qt, qInstallMessageHandler
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from azeo_control_trainer.azeo_graphics_designer.window import HmiStudioWindow
    from azeo_control_trainer.azeo_operator_station.console import LiveStation
    from azeo_control_trainer.azeo_operator_station.deployment import PvmDeployment
    from azeo_control_trainer.core.hmi.pvms.procedure_blueprint import create_blueprint, configuration
    from azeo_control_trainer.core.hmi.pvms.procedure_assemblies import procedure_references
    from azeo_control_trainer.core.hmi.pvms.publishing import PvmDisplay
    from azeo_control_trainer.core.procedures.authoring import ProcedureDraft
    from azeo_control_trainer.core.presentation import headless
    from azeo_control_trainer.core.strategy.blocks.pid_block import PIDBlock
    from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
    from azeo_control_trainer.core.strategy.model.terminal import Quality

    app = QApplication([])
    headless.is_headless = lambda: True
    messages, result = [], {}
    handler = qInstallMessageHandler(lambda kind, context, message: messages.append(message))
    with tempfile.TemporaryDirectory(prefix="azeo-quality-") as temporary:
        project = Path(temporary)
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, temporary)
        graph = StrategyGraph("UNIT")
        block = PIDBlock("PID1")
        graph.add_block(block)
        for term in (*block.inputs.values(), *block.outputs.values()):
            term.status = Quality.GOOD
        block.outputs["PV"].value = 48.0
        graphs = {"UNIT": graph}
        window = HmiStudioWindow(lambda: graphs, project / "displays" / "pvm")
        studio = window.current()
        studio.display.level, studio.display.width, studio.display.height = 2, 1366, 768
        window.resize(1550, 930)
        window.show()
        errors = []
        studio.uiError.connect(errors.append)
        station = None

        def capture(widget, name):
            widget.show()
            widget.raise_()
            app.processEvents()
            assert widget.grab().save(str(OUT / (name + ".png")))

        try:
            ref = ProcedureDraft.new().save_revision(project).relative_to(project / "procedures").as_posix()
            for name in create_blueprint(studio.user_library(), "SafeLanding"):
                configuration(name).save(studio.store.root / "_pvmcfg")
            dialog = window.open_engineering_tool("assemblies")
            dialog.source.setCurrentIndex(dialog.source.findText("PA · SafeLanding_PVM"))
            dialog.mapping.cellWidget(0, 1).setCurrentText(ref)
            dialog.position_x.setValue(120)
            dialog.position_y.setValue(170)
            dialog.preview()
            capture(dialog, "pa-assembly")
            dialog.apply()
            dialog.close()
            assert procedure_references(studio._document()) == [ref]
            studio.add_static("text", 120, 65, 80, 16, text="Shutdown procedure · operator guidance")
            label = next(i for i in studio._static_items() if not i.data.get("user_pvm"))
            label.data["font_size"] = 14
            studio.mark_unsaved()
            received = []
            window.quality_monitor.updated.connect(lambda owner, rows: received.append(rows))
            QTest.mouseClick(window._quality_segment, Qt.LeftButton)
            assert window.problems_dock.isVisible()
            deadline = time.monotonic() + 10
            while not received and time.monotonic() < deadline:
                QTest.qWait(20)
            assert received
            finding = next(f for f in received[-1] if getattr(f, "code", "") == "clipped_text" and f.item == label.data["id"])
            window._activate_problem(finding.item)
            assert studio.selection.snapshot().primary is label
            capture(window, "automatic-problems")
            window._fix_problem(finding)
            studio.fit_drawing()
            window.open_quick_online()
            quick = window.quick_online
            quick.resolution_selector.setCurrentIndex(1)
            for theme in ("silver", "dark", "hpgray"):
                quick.choose_theme(theme)
                quick.check_readability()
                capture(quick, "preview-" + theme)
            quick.close()
            window.ribbon_tabs.setCurrentIndex(list(window._RIBBON).index("Review"))
            QTest.mouseClick(window._ribbon_buttons["engineering.release"], Qt.LeftButton)
            release = window.open_engineering_tool("release")
            capture(release, "release-readiness")
            assert release.report["commissioning"]["total"] == 0
            assert studio.save_draft()
            studio.publish("TEST")
            release.close()
            runtime = SimpleNamespace(is_online=True, is_debug_paused=False, scan_count=1,
                                      compiled=SimpleNamespace(graph=graph))
            station = LiveStation(PvmDeployment(studio.store), lambda: graphs,
                                  config_root=studio.store.root, history_path=project / "history.sqlite")
            station.simulation_service = SimpleNamespace(project_dir=project, _runtimes=lambda: [runtime],
                process_capabilities=lambda: {"available": True, "paused": False, "running": True, "sim_time": 0.0})
            station.resize(1550, 930)
            assert station.show_display(studio.display.name)
            station.procedure_session().snapshot(ref)
            station.procedure_session().poll()
            station.view.refresh()
            capture(station, "published-operator")
            button = next(i for i in station.view.scene().items()
                          if isinstance(getattr(i, "data", None), dict) and i.data.get("entry", {}).get("label") == "Procedure…")
            assert button.activate()
            face = station.user_faceplates[-1]
            capture(face, "pa-faceplate")
            face.close()
            station.close()
            # Best of three is recorded after UI checks, not alongside other
            # workloads. This measures cooperative batch work, not frame rate.
            document = PvmDisplay(name=studio.display.name, width=4000, height=3000,
                items=[{"id": f"label-{i}", "kind": "text", "x": (i % 25) * 150, "y": (i // 25) * 60,
                        "w": 130, "h": 25, "text": f"Pressure {i}", "font_size": 9} for i in range(1000)]).to_dict()
            studio._load_document(document)
            batches = []
            for _ in range(3):
                received.clear()
                window.quality_monitor.schedule()
                start, longest, slices = time.perf_counter(), 0.0, 0
                while not received and time.perf_counter() - start < 30:
                    window.quality_monitor.advance()
                    longest = max(longest, window.quality_monitor.last_slice_ms)
                    slices += 1
                assert received
                batches.append({"elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
                                "max_slice_ms": round(longest, 2), "slices": slices})
            result["quality_1000_items"] = batches
            result["success"] = not errors and not messages
        except Exception:
            import traceback
            result["error"] = traceback.format_exc()
        finally:
            if station is not None:
                station.close()
                station.deleteLater()
            window.close()
            window.deleteLater()
            app.processEvents()
            result.update(qt_messages=messages, ui_errors=errors)
            (OUT / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    qInstallMessageHandler(handler)
    print(json.dumps(result, indent=2))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
