"""Render and exercise controller tools on an isolated copy of the project."""
import json
import faulthandler
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["QT_QPA_PLATFORM"] = "windows" if "--native" in sys.argv else "offscreen"


def main():
    faulthandler.dump_traceback_later(45, repeat=True)
    from PySide6.QtCore import QSettings, Qt, qInstallMessageHandler
    from PySide6.QtGui import QContextMenuEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QTabWidget, QWidget
    from azeo_control_trainer.app import AreaContext
    from azeo_control_trainer.azeo_control_designer.designer_window import StrategyDesignerWindow
    from azeo_control_trainer.azeo_control_designer.dialogs.controller_properties import ControllerPropertiesDialog
    from azeo_control_trainer.azeo_control_designer.dialogs.controller_status import ControllerStatusDialog
    from azeo_control_trainer.azeo_control_designer.dialogs.controller_simulator import ControllerSimulatorDialog
    from azeo_control_trainer.azeo_control_designer.dialogs.diagnostics_dialog import ControllerDiagnosticsDialog
    from azeo_control_trainer.azeo_explorer.controller_discovery_dialog import ControllerDiscoveryDialog
    from azeo_control_trainer.azeo_explorer.opcua_browser import OpcUaBrowserDialog
    from azeo_control_trainer.azeo_explorer.virtual_io_simulator import VirtualIoSimulatorDialog, _SimulationEditor
    from azeo_control_trainer.azeo_simulation_workbench import window as workbench_ui
    from azeo_control_trainer.connectivity.fieldio.local_virtual_io import LocalVirtualIoDriver
    from azeo_control_trainer.core.datastore.shared_data_store import SharedDataStore
    from azeo_control_trainer.core.hmi.theme.fonts import apply_application_font
    from azeo_control_trainer.core.presentation import headless
    from azeo_control_trainer.core.strategy.engine.pk_controller import PKController
    from azeo_control_trainer.core.strategy.serialization import strategy_io
    from azeo_control_trainer.core.strategy.tagdb import TagDatabase
    import azeo_control_trainer.core.strategy.blocks  # noqa: F401

    output = ROOT / "logs/controller-chrome-ui"
    output.mkdir(exist_ok=True)
    app = QApplication([])
    apply_application_font()
    messages = []
    qInstallMessageHandler(lambda kind, context, message: messages.append(message))
    headless.is_headless = lambda: True
    result = {}
    with tempfile.TemporaryDirectory(prefix="azeo-controller-chrome-") as temporary:
        project = Path(temporary) / "project"
        shutil.copytree(ROOT / "projects/AzeoPlantVirtualController", project,
                        ignore=shutil.ignore_patterns("displays", "*.sqlite*", "*.snapshot.json"))
        document = json.loads((project / "_project.json").read_text(encoding="utf-8"))
        # Six representative modules exercise the tools; the boot smoke owns
        # the full-controller load and scan contract.
        document["areas"][0]["strategies"] = document["areas"][0]["strategies"][:6]
        document["areas"][0]["sfc_modules"] = []
        (project / "_project.json").write_text(json.dumps(document), encoding="utf-8")
        before = (project / "_project.json").read_bytes()
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, temporary)
        strategy_io.STRATEGY_DIR = project
        store = SharedDataStore()
        store.tagdb = TagDatabase.from_area(project)
        store.controller = PKController.from_config(None)
        control = StrategyDesignerWindow(store=store, plugin=AreaContext(project))
        print("Loading isolated controller modules", flush=True)
        control.designer.auto_load_project()
        print("Constructing engineering tools", flush=True)
        config = json.loads(before)["areas"][0]["virtual_io"]
        driver = LocalVirtualIoDriver(store, config, project)
        workbench_ui.data_dir = lambda: Path(temporary) / "data"
        canvas = control.designer._canvas_tabs.widget(0)
        windows = {
            "controller-properties": ControllerPropertiesDialog(store, project / "_project.json"),
            "controller-status": ControllerStatusDialog(control.designer, store),
            "controller-diagnostics": ControllerDiagnosticsDialog(control.designer, store),
            "controller-simulator": ControllerSimulatorDialog(canvas),
            "discovery": ControllerDiscoveryDialog(add_controller=lambda *args: False),
            "opcua-mapping": OpcUaBrowserDialog(store, project / "_project.json"),
            "virtual-io": VirtualIoSimulatorDialog(driver, project),
            "input-editor": _SimulationEditor(dict(store_tag="FIELD.AI-1001.PV", value=45.125,
                                                    range=(0, 100))),
            "simulation-workbench": workbench_ui.SimulationWorkbenchDialog(store, driver, project),
        }
        try:
            for name, window in windows.items():
                if "--workbench-only" in sys.argv and name != "simulation-workbench":
                    continue
                print("Checking " + name, flush=True)
                if name not in {"controller-properties", "input-editor"}:
                    window.resize(1024, 720)
                window.show()
                app.processEvents()
                if name not in {"controller-properties", "input-editor"}:
                    window.resize(1024, 720)
                    app.processEvents()
                window.grab().save(str(output / f"{name}.png"))
                assert window.width() <= 1024, (name, window.width(), window.minimumSizeHint().width())
                assert all(w.font().pointSizeF() > 0 for w in window.findChildren(QWidget)), name
                if hasattr(window, "menus"):
                    for table, _, _ in window.menus._tables.values():
                        if table.rowCount() and table.isVisible():
                            table.setCurrentCell(0, 0)
                    for action in window.menus.bar.actions():
                        if not action.isEnabled():
                            continue
                        QTest.mouseClick(window.menus.bar, Qt.LeftButton,
                                         pos=window.menus.bar.actionGeometry(action).center())
                        app.processEvents()
                        assert action.menu().isVisible(), (name, action.text())
                        action.menu().grab().save(str(output / f"{name}-menu-{action.text().replace('&', '')}.png"))
                        action.menu().hide()
                    for table, commands, title in window.menus._tables.values():
                        if table.rowCount() and table.isVisible():
                            table.setCurrentCell(0, 0)
                            index = table.currentIndex()
                            pos = table.visualRect(index).center()
                            event = QContextMenuEvent(QContextMenuEvent.Mouse, pos,
                                                      table.viewport().mapToGlobal(pos))
                            QApplication.sendEvent(table.viewport(), event)
                            app.processEvents()
                            assert window.menus.context_menu.isVisible()
                            window.menus.context_menu.grab().save(str(output / f"{name}-context.png"))
                            window.menus.context_menu.close()
                for tabs in window.findChildren(QTabWidget):
                    for i in range(tabs.count()):
                        tabs.setCurrentIndex(i)
                        app.processEvents()
                        window.grab().save(str(output / f"{name}-tab-{i}.png"))
                        for field in window.findChildren(QAbstractSpinBox):
                            if field.isVisible():
                                editor = field.lineEdit()
                                assert editor.width() >= editor.fontMetrics().horizontalAdvance(field.text()) + 4, (
                                    name, field.text(), editor.width())
                result[name] = [window.width(), window.height()]
                window.hide()
            assert (project / "_project.json").read_bytes() == before
        finally:
            for window in reversed(list(windows.values())):
                window.close()
            control.close()
            driver.stop()
            app.processEvents()
    result["qt_messages"] = messages
    (output / "report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2), flush=True)
    if "--native" in sys.argv:
        assert not messages, messages
    faulthandler.cancel_dump_traceback_later()


if __name__ == "__main__":
    main()
