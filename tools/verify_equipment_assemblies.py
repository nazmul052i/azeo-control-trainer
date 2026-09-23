"""Native equipment authoring and published-station review in a disposable project."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["QT_QPA_PLATFORM"] = "windows" if "--native" in sys.argv else "offscreen"
OUT = ROOT / "logs" / "equipment-assemblies"
OUT.mkdir(parents=True, exist_ok=True)


def main():
    from PySide6.QtCore import QSettings, Qt, QTimer, qInstallMessageHandler
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    import azeo_control_trainer.core.strategy.blocks  # noqa: F401
    from azeo_control_trainer.core.strategy.model.block_registry import BlockRegistry
    from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
    from azeo_control_trainer.core.strategy.model.terminal import Quality
    from azeo_control_trainer.azeo_graphics_designer.window import HmiStudioWindow
    from azeo_control_trainer.azeo_operator_station.console import LiveStation
    from azeo_control_trainer.azeo_operator_station.deployment import PvmDeployment
    from azeo_control_trainer.core.presentation import headless

    app = QApplication([])
    headless.is_headless = lambda: True
    messages, results = [], {}
    qInstallMessageHandler(lambda kind, context, message: messages.append(message))
    with tempfile.TemporaryDirectory(prefix="azeo-equipment-") as temporary:
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, temporary)
        graphs = {}
        for module in ("PUMP_A", "PUMP_B", "VESSEL", "COMPRESSOR", "TURBINE"):
            graph = StrategyGraph(module)
            graph.description = "Cooling water equipment" if module.startswith("PUMP") else "Utility equipment"
            for kind, name in (("PID", "SPEED"), ("DEVCTL", "RUN"), ("AI", "LEVEL"), ("AO", "VALVE")):
                block = BlockRegistry().create(kind, name)
                graph.add_block(block)
                for terminal in (*block.inputs.values(), *block.outputs.values()):
                    terminal.status = Quality.GOOD
                for term in ("PV", "SP", "OUT"):
                    terminal = block.outputs.get(term) or block.inputs.get(term)
                    if terminal is not None:
                        terminal.value = 48.0
                        terminal.units = "%"
                        terminal.eu_range = (0, 100)
            graphs[module] = graph
        window = HmiStudioWindow(lambda: graphs, Path(temporary) / "displays")
        studio = window.current()
        studio.display.level = 2
        studio.display.width, studio.display.height = 1400, 800
        available = window.screen().availableGeometry()
        window.resize(min(1600, available.width() - 80), min(950, available.height() - 80))
        errors = []
        studio.uiError.connect(errors.append)
        window.show()
        station = None

        def capture(widget, name):
            widget.show()
            widget.raise_()
            app.processEvents()
            assert widget.grab().save(str(OUT / f"{name}.png"))

        def check():
            nonlocal station
            try:
                window.ribbon_tabs.setCurrentIndex(list(window._RIBBON).index("Insert"))
                QTest.mouseClick(window._ribbon_buttons["engineering.assemblies"], Qt.LeftButton)
                dialog = window.open_engineering_tool("assemblies")
                results["placements"] = []
                for name, module, x, y in (
                    ("Pump and VFD", "PUMP_A", 40, 70),
                    ("Pump and VFD", "PUMP_B", 490, 70),
                    ("Vessel and outlet", "VESSEL", 40, 390),
                    ("Compressor and speed control", "COMPRESSOR", 500, 410),
                    ("Turbine and speed control", "TURBINE", 950, 70),
                ):
                    dialog.source.setCurrentIndex(dialog.source.findText(name))
                    dialog.position_x.setValue(x)
                    dialog.position_y.setValue(y)
                    targets = {"RUN/DEV": f"{module}/RUN", "SPEED/PID": f"{module}/SPEED",
                               "LEVEL/AI": f"{module}/LEVEL", "VALVE/AO": f"{module}/VALVE"}
                    for row in range(dialog.mapping.rowCount()):
                        dialog.mapping.cellWidget(row, 1).setCurrentText(targets[dialog.mapping.item(row, 0).text()])
                    dialog.preview()
                    if module == "PUMP_A":
                        for theme in ("silver", "dark", "hpgray"):
                            dialog.theme.setCurrentIndex(dialog.theme.findData(theme))
                            capture(dialog, f"assembly-{theme}")
                        from azeo_control_trainer.azeo_graphics_designer.param_browser import ParameterBrowserDialog
                        picker = ParameterBrowserDialog(lambda: graphs, "PUMP_A/RUN", dialog, block_types={"DEVCTL"})
                        picker.search.setText("cooling")
                        capture(picker, "compatible-tag-search")
                        assert picker.result_count.text() == "2 matching objects"
                        picker.close()
                        picker.deleteLater()
                    before = len(studio._undo_stack)
                    started = time.perf_counter()
                    dialog.apply()
                    assert len(studio._undo_stack) == before + 1
                    results["placements"].append({"equipment": module, "apply_ms": round((time.perf_counter() - started) * 1000, 3)})
                dialog.close()
                studio.fit_drawing()
                capture(window, "equipment-studio")
                assert studio.save_draft()
                studio.publish("TEST")
                station = LiveStation(PvmDeployment(studio.store), lambda: graphs,
                                      config_root=studio.store.root, history_path=Path(temporary) / "history.sqlite")
                station.resize(min(1600, available.width() - 80), min(950, available.height() - 80))
                assert station.show_display(studio.display.name)
                capture(station, "equipment-operator")
                window.open_quick_online()
                preview = window.quick_online
                preview.choose_theme("dark")
                machine = next(item.pvm for item in studio._items()
                               if item.pvm.variant == "vfd" and item.pvm.params["device"] == "PUMP_A/RUN")
                face = preview.open_faceplate(machine)
                assert face is not None and face.params["device"] == "PUMP_A/RUN"
                capture(face, "vfd-faceplate")
                preview.close()
                assert not errors, errors
                results["success"] = True
            except Exception:
                import traceback
                results["error"] = traceback.format_exc()
            finally:
                if station is not None:
                    station.close()
                    station.deleteLater()
                window.close()
                results["qt_messages"] = messages
                results["ui_errors"] = errors
                (OUT / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
                app.quit()

        QTimer.singleShot(800, check)
        app.exec()
        window.deleteLater()
        app.processEvents()
    print(json.dumps(results, indent=2))
    return 0 if results.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
