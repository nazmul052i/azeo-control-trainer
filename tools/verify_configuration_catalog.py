"""Native three-application catalog acceptance on a disposable exported project."""
from __future__ import annotations

import json
import faulthandler
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["QT_QPA_PLATFORM"] = "windows" if "--native" in sys.argv else "offscreen"
OUT = ROOT / "logs/configuration-catalog-ui"
OUT.mkdir(parents=True, exist_ok=True)


def main():
    trace = (OUT / "tracebacks.txt").open("w", encoding="utf-8")
    faulthandler.dump_traceback_later(45, repeat=True, file=trace)
    from PySide6.QtCore import QSettings, Qt, qInstallMessageHandler
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QWidget
    from azeo_control_trainer.app import AreaContext
    from azeo_control_trainer.azeo_control_designer.designer_window import StrategyDesignerWindow
    from azeo_control_trainer.azeo_explorer import ExplorerWindow
    from azeo_control_trainer.core.configuration.client import ConfigurationClient, read_profile
    from azeo_control_trainer.core.configuration.documents import export_project, read_project
    from azeo_control_trainer.core.datastore.shared_data_store import SharedDataStore
    from azeo_control_trainer.core.hmi.theme.fonts import apply_application_font
    from azeo_control_trainer.core.presentation import headless
    from azeo_control_trainer.core.presentation.configuration_catalog import _shutdown
    from azeo_control_trainer.core.strategy.engine.pk_controller import PKController
    from azeo_control_trainer.core.strategy.serialization import strategy_io
    from azeo_control_trainer.core.strategy.tagdb import TagDatabase

    app = QApplication([])
    apply_application_font()
    messages = []
    qInstallMessageHandler(lambda kind, context, message: messages.append(str(message)))
    # Only suppress confirmation dialogs on disposable verification windows.
    headless.is_headless = lambda: True
    client = ConfigurationClient(**read_profile())
    print("Reading the captured pilot", flush=True)
    project = next(p for p in client.request("/v1/projects") if p["name"] == "APVC Database Pilot")
    snapshot = client.request(f"/v1/projects/{project['id']}/export")
    source_before = read_project(ROOT / "projects/AzeoPlantVirtualController")
    assert {f["path"]: f["content"] for f in source_before["files"]} == {
        f["path"]: f["content"] for f in snapshot["files"]}
    results = {"digest": snapshot["digest"], "native": "--native" in sys.argv}
    windows = []

    def wait_ready(browser):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            app.processEvents()
            if browser._started and browser._request is None:
                if browser.index is None and browser.projects.findData(str(project["id"])) > 0:
                    index = browser.projects.findData(str(project["id"]))
                    browser.projects.setCurrentIndex(index)
                    browser._project_changed(index)
                    continue
                assert browser.index is not None, browser.status.text()
                return
            time.sleep(.02)
        raise AssertionError("Catalog request timed out")

    def capture(widget, name):
        app.processEvents()
        widget.grab().save(str(OUT / (name + ".png")))

    def check_browser(browser, label):
        wait_ready(browser)
        browser.search.clear()
        QTest.keyClicks(browser.search, "PIC-2001")
        app.processEvents()
        expected = browser.index.entries["PIC-2001/PIC-2001/CONFIG/lo_lim"]
        browser.inspect("PIC-2001")
        refs = browser.reference_model.rows
        assert any(r["kind"] == "controller_assignment" for r in refs)
        assert any(r["kind"] == "field_binding" for r in refs)
        assert any(r["source"].startswith("display:") for r in refs)
        assert any(r["kind"] == "faceplate_class" for r in refs)
        results[label] = {"project": browser.index.project["id"],
                          "generation": browser.index.project["generation"],
                          "metadata": expected, "references": len(refs)}
        capture(browser.window(), label)
        return expected

    with tempfile.TemporaryDirectory(prefix="azeo-catalog-ui-") as temporary:
        root = export_project(snapshot, Path(temporary) / "project")
        print("Exported verification project", flush=True)
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, temporary)
        strategy_io.STRATEGY_DIR = root
        store = SharedDataStore()
        store.tagdb = TagDatabase.from_area(root)
        store.controller = PKController.from_config(None)
        control = StrategyDesignerWindow(store=store, plugin=AreaContext(root))
        print("Control Designer constructed", flush=True)
        windows.append(control)
        control.designer.auto_load_project()
        print("Control modules loaded", flush=True)
        explorer = ExplorerWindow(store=store, area=root, designer=control)
        windows.append(explorer)
        explorer.show()
        try:
            explorer.open_tag_database()
            dialog = control.designer._tagdb_dialog
            dialog.resize(1260, 760)
            dialog.tabs.setCurrentIndex(1)
            first = check_browser(dialog.catalog, "explorer")
            print("Explorer catalog verified", flush=True)
            dialog.close()
            explorer.open_control_designer()
            dialog = control.designer.show_tag_database()
            dialog.resize(1260, 760)
            dialog.tabs.setCurrentIndex(1)
            second = check_browser(dialog.catalog, "control-designer")
            print("Control Designer catalog verified", flush=True)
            dialog.close()
            explorer.open_graphics_designer()
            graphics = control._pvm_studio_window
            windows.append(graphics)
            graphics.ribbon_tabs.setCurrentIndex(list(graphics._RIBBON).index("View"))
            QTest.mouseClick(graphics._ribbon_buttons["engineering.catalog"], Qt.LeftButton)
            catalog = graphics._configuration_catalog
            catalog.resize(1260, 760)
            third = check_browser(catalog.browser, "graphics-designer")
            print("Graphics Designer catalog verified", flush=True)
            assert first == second == third
            browser = catalog.browser
            display = "display:displays/pvm/U200 - L2 Recycle Compression/draft.json"
            browser.inspect(display)
            preview = browser.preview()
            assert preview is not None, browser.status.text()
            capture(preview, "linked-graphic")
            preview.close()
            browser.inspect("PIC-2001/PIC-2001")
            browser.inspect("class:PID/faceplate")
            preview = browser.preview()
            assert preview is not None, browser.status.text()
            capture(preview, "linked-faceplate")
            preview.close()
            samples = []
            for index in range(40):
                started = time.perf_counter()
                browser.search.setText("PIC-2001" if index % 2 else "SIC-2001")
                app.processEvents()
                samples.append((time.perf_counter() - started) * 1000)
            results["search_median_ms"] = round(statistics.median(samples), 2)
            results["search_p95_ms"] = round(sorted(samples)[37], 2)
            assert all(w.font().pointSizeF() > 0 for w in catalog.findChildren(QWidget))
            catalog.close()
            # Reach the existing console after the bridge-publication refactor.
            from azeo_control_trainer.azeo_operator_station.console import LiveStation
            from azeo_control_trainer.azeo_operator_station.deployment import PvmDeployment
            from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore
            station = LiveStation(PvmDeployment(DisplayStore(root / "displays/pvm")), lambda: {},
                                  config_root=root / "displays/pvm", history_path=Path(temporary) / "history.sqlite")
            windows.append(station)
            assert station.show_display("U200 - L2 Recycle Compression")
            station.resize(1400, 850)
            station.show()
            capture(station, "operator-console")
            results["operator_console"] = True
        finally:
            _shutdown()
            for runtime in control.designer.controller_executive().online_runtimes():
                runtime.go_offline()
            for window in reversed(windows):
                window.close()
            app.processEvents()
    assert source_before == read_project(ROOT / "projects/AzeoPlantVirtualController")
    results["qt_messages"] = messages
    results["source_unchanged"] = True
    results["success"] = not messages
    (OUT / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in results.items() if k not in {
        "explorer", "control-designer", "graphics-designer"}}, indent=2))
    assert not messages, messages
    faulthandler.cancel_dump_traceback_later()
    trace.close()


if __name__ == "__main__":
    main()
