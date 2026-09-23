"""Exercise the new Studio workflows on a disposable project and capture native UI."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["QT_QPA_PLATFORM"] = "windows" if "--native" in sys.argv else "offscreen"
OUT = ROOT / "logs" / "graphics-productivity-ui"
if "--output-dir" in sys.argv:
    OUT = Path(sys.argv[sys.argv.index("--output-dir") + 1]).resolve()
OUT.mkdir(parents=True, exist_ok=True)


def main():
    from PySide6.QtCore import QSettings, QTimer, Qt, qInstallMessageHandler
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from azeo_control_trainer.azeo_graphics_designer.window import HmiStudioWindow
    from azeo_control_trainer.core.hmi.pvms.engineering import starter_assemblies, remap_controls
    from azeo_control_trainer.core.hmi.pvms.base import registry
    from azeo_control_trainer.core.hmi.pvms.configurator.model import PropertyGroup, PvmProperty
    from azeo_control_trainer.azeo_operator_station.console import LiveStation
    from azeo_control_trainer.azeo_operator_station.deployment import PvmDeployment
    from azeo_control_trainer.core.strategy.blocks.pid_block import PIDBlock
    from azeo_control_trainer.core.strategy.blocks.io_blocks import AIBlock, AOBlock
    from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
    from azeo_control_trainer.core.strategy.model.terminal import Quality
    from azeo_control_trainer.core.presentation import headless
    from azeo_control_trainer.azeo_graphics_designer.configurator import designer as designer_module

    app = QApplication([])
    messages = []
    qInstallMessageHandler(lambda kind, context, message: messages.append(str(message)))
    # Automatic verification must not wait for a native unsaved-close prompt.
    headless.is_headless = lambda: True
    designer_module.is_headless = lambda: True
    with tempfile.TemporaryDirectory(prefix="azeo-graphics-ui-") as temporary:
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, temporary)
        graph = StrategyGraph("UNIT")
        for block in (PIDBlock("PID1"), AIBlock("AI1"), AOBlock("AO1")):
            graph.add_block(block)
            for terminal in (*block.inputs.values(), *block.outputs.values()):
                terminal.status = Quality.GOOD
        graphs = {"UNIT": graph}
        window = HmiStudioWindow(lambda: graphs, Path(temporary) / "displays")
        # Keep the verifier's requested client area inside the native desktop;
        # oversized fixtures otherwise produce Windows geometry warnings before
        # the monitor-move check can inspect the actual product layout.
        available = window.screen().availableGeometry()
        window.resize(min(1600, available.width() - 80), min(950, available.height() - 80))
        studio = window.current()
        studio.enter_edit()
        errors = []
        studio.uiError.connect(errors.append)
        document = remap_controls(starter_assemblies()["Control loop"], {"LOOP/PID": "UNIT/PID1", "VALVE/AO": "UNIT/AO1"}, graphs)
        document.update(display=studio.display.name, width=1200, height=700)
        studio._load_document(document)
        studio.save_draft()
        studio.publish("TEST")
        studio.add_static("text", 420, 90, 300, 38).data["text"] = "Feed flow control · training"
        studio.add_static("symbol", 520, 220, 110, 180, symbol="bubble_column")
        studio.mark_unsaved()
        window.show()
        results = {}

        def capture(widget, name):
            widget.show()
            widget.raise_()
            app.processEvents()
            assert widget.grab().save(str(OUT / f"{name}.png"))

        def check():
            try:
                studio.fit_drawing()
                capture(window, "studio")
                if "--monitor-check" in sys.argv:
                    from physical_monitor_check import check_monitor_moves
                    results["physical_monitors"] = check_monitor_moves([("graphics", window)], OUT)
                window.ribbon_tabs.setCurrentIndex(list(window._RIBBON).index("Insert"))
                QTest.mouseClick(window._ribbon_buttons["engineering.worksheet"], Qt.LeftButton)
                worksheet = window.open_engineering_tool("worksheet")
                worksheet.scope.setCurrentIndex(1)
                worksheet.table.item(0, 3).setText("Feed controller")
                assert worksheet.preview() is not None, worksheet.status.text()
                capture(worksheet, "worksheet")
                worksheet.apply()
                worksheet.close()
                command = window.open_command_search()
                command.search.setText("sequences")
                capture(command, "command-search")
                command.activate()
                sequence = window.open_engineering_tool("sequences")
                sequence.path.setText("UNIT/PID1/PV")
                sequence.add_lifecycle()
                sequence.step()
                sequence.apply_time(4)
                capture(sequence, "sequences")
                results["acknowledged_preview"] = studio.preview_source.read("UNIT/PID1/PV").alarm_acked
                sequence.close()
                revision = window.open_engineering_tool("revisions")
                capture(revision, "revision-review")
                results["revision_changes"] = len(revision.changes)
                revision.close()
                cls = registry.get("PID", "dynamo_compact")
                designer = window.open_pvm_config(cls.__name__)
                designer.config.groups.append(PropertyGroup("Review", [PvmProperty("ReviewNote", "String", default="Feed control")]))
                designer._mark_unsaved()
                impact = designer.review_impact()
                capture(impact, "pvm-impact")
                results["affected_instances"] = len(impact.instances)
                impact.close()
                designer.close()
                # Reach the published revision through the real console and
                # deployment path, not just a standalone rendering widget.
                station = LiveStation(PvmDeployment(studio.store), lambda: graphs,
                                      config_root=studio.store.root,
                                      history_path=Path(temporary) / "history.sqlite")
                available = station.screen().availableGeometry()
                station.resize(min(1600, available.width() - 80), min(950, available.height() - 80))
                assert station.show_display(studio.display.name)
                capture(station, "operator-published")
                results["operator_revision"] = station.deployment._held(studio.display.name)
                assert results["operator_revision"] == 1
                station.close()
                station.deleteLater()
                view = window.open_quick_online()
                preview = window.quick_online
                preview.show()
                pid = next(item.pvm for item in studio._items() if item.pvm.block_type == "PID")
                face = preview.open_faceplate(pid)
                detail = preview.open_faceplate(pid, "detail")
                assert face is not None and detail is not None
                results["preview_themes"] = []
                for theme in ("silver", "dark", "hpgray", "azeo_live"):
                    assert preview.choose_theme(theme)
                    capture(preview, f"preview-{theme}")
                    capture(face, f"preview-{theme}-faceplate")
                    capture(detail, f"preview-{theme}-detail")
                    results["preview_themes"].append(theme)
                preview.resolution_selector.setCurrentIndex(2)
                app.processEvents()
                results["preview_viewport"] = [view.viewport().width(), view.viewport().height()]
                assert results["preview_viewport"] == [1920, 1080]
                assert view.viewport().grab().save(str(OUT / "preview-1920x1080.png"))
                preview.close()
                assert view._disposed and not face.bound and not detail.bound
                studio._write_recovery()
                assert studio.recovery_saved_at and not studio.recovery_error
                capture(window, "studio-recovery-status")
                results["errors"] = errors
                assert not errors, errors
                assert results["acknowledged_preview"] and results["affected_instances"]
                results["success"] = True
            except Exception:
                import traceback
                results["error"] = traceback.format_exc()
            finally:
                results["qt_messages"] = list(messages)
                (OUT / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
                (OUT / "qt.log").write_text("\n".join(messages), encoding="utf-8")
                window.close()
                app.quit()

        QTimer.singleShot(1200, check)
        app.exec()
        print(json.dumps(results, indent=2))
        window.deleteLater()
        app.processEvents()
        return 0 if results.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
