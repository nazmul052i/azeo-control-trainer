"""Native selective adoption, detached faceplates and historian rename acceptance.

Each run creates a new isolated project and private runtime. Existing project
files, archives and shipped course displays are not modified.
"""
from concurrent.futures import ThreadPoolExecutor
import base64
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["QT_QPA_PLATFORM"] = "windows"


def main():
    from PySide6.QtCore import QSettings, Qt, qInstallMessageHandler
    from PySide6.QtWidgets import QApplication
    from azeo_control_trainer.core.configuration.client import ConfigurationClient, read_profile
    from azeo_control_trainer.core.configuration.documents import read_project, prepare_import
    from azeo_control_trainer.core.configuration.libraries import decode
    from azeo_control_trainer.core.configuration.catalog_client import CatalogSession
    from azeo_control_trainer.core.hmi.pvms.publishing import PvmDisplay
    from azeo_control_trainer.core.hmi.pvms.user_library import UserPvmLibrary
    from azeo_control_trainer.core.hmi.pvms.configurator.model import PvmConfiguration, PropertyGroup, PvmProperty
    from azeo_control_trainer.core.hmi.theme.fonts import apply_application_font
    from azeo_control_trainer.core.hmi.history.view import ProcessHistoryView
    from azeo_control_trainer.core.presentation.configuration_catalog import ConfigurationCatalogDialog, _shutdown
    from azeo_control_trainer.core.presentation.configuration_libraries import LibraryManager
    from azeo_control_trainer.azeo_explorer.configuration_runtime import RuntimeWindow
    from azeo_control_trainer.core.strategy.blocks.io_blocks import AIBlock
    from azeo_control_trainer.core.strategy.blocks.pid_block import PIDBlock
    from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
    from azeo_control_trainer.core.strategy.serialization.strategy_io import write_json_transactional

    out = ROOT / "logs/configuration-library-ui" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    directory = ROOT / "data/configuration/library_acceptance" / out.name
    source = directory / "source"
    graphics = source / "displays/pvm"
    graphics.mkdir(parents=True)
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(directory / "settings"))
    apply_application_font()
    qt_messages = []
    qInstallMessageHandler(lambda kind, context, message: qt_messages.append(message))
    profile = read_profile()
    client = ConfigurationClient(**profile, timeout=150)
    pool = ThreadPoolExecutor(2)
    windows = []

    def wait(predicate, label, timeout=150):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                print("PASS", label, flush=True)
                return
            time.sleep(.01)
        raise AssertionError(label + " timed out")

    def background(operation, label):
        future = pool.submit(operation)
        wait(future.done, label)
        return future.result()

    def request(path, payload=None):
        return background(lambda: client.request(path, payload), path)

    original_digest = prepare_import(read_project(ROOT / "projects/AzeoPlantVirtualController")["files"]).digest
    graph = StrategyGraph("TRAINING_LOOP")
    ai, pid = AIBlock("AI1"), PIDBlock("PID1")
    ai.config.params["tag"] = "TRAINING.PV"
    pid.config.params["pv_unit"] = "bar"
    graph.add_block(ai)
    graph.add_block(pid)
    graph.add_wire(ai.id, "OUT", pid.id, "IN")
    write_json_transactional(source / "_project.json", {"areas": [{"name": "Training", "strategies": ["control/TRAINING_LOOP.json"]}]})
    write_json_transactional(source / "control/TRAINING_LOOP.json", graph.to_dict())
    library = UserPvmLibrary(graphics)
    for name, kind, title in [("TrainingFaceplate", "faceplate", "Faceplate version 1"), ("TrainingPVM", "pvm", "PVM version 1")]:
        library.add(name, [
            {"id": "surface", "kind": "rect", "x": 0, "y": 0, "w": 280, "h": 180, "fill": "#E2E6EF"},
            {"id": "title", "kind": "text", "x": 20, "y": 25, "w": 240, "h": 32, "font_size": 14, "text": title},
            {"id": "value", "kind": "datalink", "x": 35, "y": 95, "w": 200, "h": 50, "path": "Pvm.PVPath",
             "datalink_type": "numeric", "decimals": 2}], definition_kind=kind,
                    paired_faceplate="TrainingFaceplate" if kind == "pvm" else "")
    choices = {"PVPath": "TRAINING_LOOP/PID1/PV"}
    configs = {}
    for name in library.entries:
        cfg = PvmConfiguration(name, [PropertyGroup("Data", [PvmProperty("PVPath", "String", default="")])])
        cfg.save(graphics / "_pvmcfg")
        configs[name] = cfg
    items = library.instantiate("TrainingPVM", 80, 160, config=configs["TrainingPVM"], choices=choices)
    items += library.instantiate("TrainingPVM", 480, 160, config=configs["TrainingPVM"], choices=choices)
    items += [{"id": "heading", "kind": "text", "x": 65, "y": 50, "w": 740, "h": 45,
               "text": "CONTROL ENGINEERING · CLASS ADOPTION", "font_size": 20, "font_bold": True},
              {"id": "left-label", "kind": "text", "x": 80, "y": 125, "w": 280, "h": 25, "text": "Selected instance"},
              {"id": "right-label", "kind": "text", "x": 480, "y": 125, "w": 280, "h": 25, "text": "Retained instance"}]
    display = PvmDisplay(name="Library training", width=900, height=480, items=items)
    graphic_path = "displays/pvm/Library training/draft.json"
    write_json_transactional(source / graphic_path, display.to_dict())
    source_files = read_project(source)["files"]
    name = "Class and History Training " + out.name
    preview = request("/v1/imports/preview", {"name": name + " source", "files": source_files})
    captured = request("/v1/imports", {"name": name + " source", "files": source_files,
                                      "expected_generation": preview["expected_generation"], "command_id": str(uuid4())})
    fork = request(f"/v1/projects/{captured['project_id']}/editing/fork", {"name": name, "command_id": str(uuid4())})
    project = next(p for p in request("/v1/projects") if p["id"] == fork["project_id"])
    prefix = f"/v1/projects/{project['id']}"
    manager = LibraryManager(project, profile=profile)
    windows.append(manager)
    manager.show()

    def refresh():
        wait(lambda: manager.worker is None, "Library Manager idle")
        manager.refresh()
        wait(lambda: manager.worker is None, "Library Manager refreshed")

    def adopt(selected, pin=False):
        for i, row in enumerate(manager.rows):
            manager.instances.item(i, 0).setCheckState(Qt.Checked if row["id"] in selected else Qt.Unchecked)
        before = len(manager._dialogs)
        manager.preview(pin=pin)
        wait(lambda: manager.worker is None, "Class preview completed")
        assert len(manager._dialogs) > before, manager.status.text()
        review = manager._dialogs[-1]
        review.reason.setText("Retain baseline contracts" if pin else "Adopt version 2 for the selected training PVM")
        review.reviewed.setChecked(True)
        review.grab().save(str(out / ("initial-pins.png" if pin else "adoption-review.png")))
        review.commit()
        wait(lambda: review.worker is None, "Adoption check-in completed")
        assert not review.isVisible(), review.status.text()
        refresh()

    def checkin(edits, reason, generation=None):
        session = str(uuid4())
        paths = list({e[key] for e in edits for key in ("path", "new_path") if e.get(key)})
        request(prefix + "/editing/lease", {"session": session, "paths": paths})
        try:
            return request(prefix + "/editing/checkin", {"session": session, "command_id": str(uuid4()),
                           "edits": edits, "reason": reason, "expected_generation": generation})
        finally:
            request(prefix + "/editing/lease", {"session": session, "paths": [], "release": True})

    def release(module):
        reviewed = request(prefix + "/releases/preview", {"paths": ["control/" + module + ".json", graphic_path]})
        assert not [f for f in reviewed["manifest"]["findings"] if f["severity"] == "ERROR"], reviewed["manifest"]["findings"]
        return request(prefix + "/releases", {"preview": reviewed["id"], "reason": "Native class/history acceptance", "command_id": str(uuid4())})

    try:
        wait(lambda: manager.state is not None and manager.worker is None, "Native Library Manager ready")
        instance_ids = [row["id"] for row in manager.rows]
        assert len(instance_ids) == 2
        adopt(instance_ids, pin=True)
        baseline = request(prefix + "/export")
        baseline_items = decode(baseline)[graphic_path]["items"]
        library_path = "displays/pvm/_library/user_pvms.json"
        changed = decode(baseline)[library_path]
        for class_name, label in [("TrainingPVM", "PVM version 2"), ("TrainingFaceplate", "Faceplate version 2")]:
            changed[class_name]["items"][1]["text"] = label
            changed[class_name]["definition_revision"] += 1
        obj = next(o for o in baseline["objects"] if o["path"] == library_path)
        checkin([{"path": library_path, "expected_revision": obj["revision"],
                  "content": base64.b64encode(json.dumps(changed).encode()).decode()}], "Update shared PVM and paired faceplate")
        refresh()
        assert all(r["status"] == "Update available" for r in manager.rows)
        adopt(instance_ids[:1])
        manager.instances.selectRow(0)
        manager.grab().save(str(out / "mixed-class-versions.png"))
        mixed = decode(request(prefix + "/export"))[graphic_path]["items"]
        untouched_id = baseline_items[3]["instance_id"]
        assert [i for i in mixed if i.get("instance_id") == untouched_id] == [i for i in baseline_items if i.get("instance_id") == untouched_id]
        first = release("TRAINING_LOOP")
        target = request(prefix + "/runtime-targets", {"name": "Class/history isolated runtime"})
        runtime_dir = directory / "runtime"
        write_json_transactional(runtime_dir / "target.json", {**target, "url": client.url})
        runtime = RuntimeWindow(runtime_dir)
        windows.append(runtime)
        runtime.show()
        request(prefix + "/deployments", {"release": first["id"], "target": target["id"],
                                          "components": ["controller", "station"], "command_id": str(uuid4())})
        wait(lambda: bool(runtime.adapter.loaded) and bool(runtime.display_store.history("Library training")), "Class versions downloaded and published")
        runtime.store.set("TRAINING.PV", 32.0)
        runtime.open_station()
        station = runtime.station
        assert station.show_display("Library training")
        station.resize(1200, 760)
        wait(lambda: station.historian.sample_count >= 3, "Actual controller recorded by Operator Live", timeout=30)
        from azeo_control_trainer.core.hmi.pvms.rendering.items import item_document_data
        source_items = [item for item in station.view.scene().items() if item_document_data(item).get("source_element_id")
                        and item_document_data(item).get("kind") == "datalink"]
        popups = []
        for item in source_items:
            popup = station.open_user_faceplate("TrainingFaceplate", item, station.view)
            assert popup
            popup.pinned = True
            popups.append(popup)
        assert len(popups) == 2 and popups[0].config_root != popups[1].config_root
        texts = [str(popup.display.items) for popup in popups]
        assert any("Faceplate version 1" in text for text in texts) and any("Faceplate version 2" in text for text in texts)
        for index, popup in enumerate(popups):
            popup.move(220 + index * 370, 460)
            popup.grab().save(str(out / f"faceplate-{index}.png"))
        station.grab().save(str(out / "operator-mixed-versions.png"))
        path = "TRAINING_LOOP/PID1/PV"
        identity = station.historian.TAGS[path].point_id
        assert identity
        trend = ProcessHistoryView(station.historian)
        windows.append(trend)
        trend.set_pens([path])
        trend.show()
        rename = request(prefix + "/editing/rename", {"module": "TRAINING_LOOP", "new_name": "RENAMED_LOOP"})
        checkin(rename["edits"], "Rename recorded training loop", rename["generation"])
        second = release("RENAMED_LOOP")
        request(prefix + "/deployments", {"release": second["id"], "target": target["id"],
                                          "components": ["controller", "station"], "command_id": str(uuid4())})
        wait(lambda: "RENAMED_LOOP" in runtime.adapter.graphs() and len(runtime.display_store.history("Library training")) == 2,
             "Renamed module downloaded and graphics published")
        station.refresh_configuration()
        station._configure_historian()
        new = "RENAMED_LOOP/PID1/PV"
        assert station.historian.TAGS[new].point_id == identity
        runtime.store.set("TRAINING.PV", 44.0)
        before_count = station.historian.sample_count
        wait(lambda: station.historian.sample_count > before_count + 2, "Recording continues after rename", timeout=30)
        trend._refresh()
        assert trend._pens == [new]
        background(station.historian.archive.flush, "Recorded samples durable")
        result = background(lambda: station.historian.query_async([new], 0, station.historian.now() / 60, raw=True).result(timeout=60), "Identity query across rename")
        assert {row["configuration"]["release_id"] for row in result["provenance"][new]} == {first["id"], second["id"]}
        trend._chart.set_review_range(0, station.historian.now() / 60)
        trend._queue_query(0, station.historian.now() / 60)
        wait(lambda: trend._query_future is None, "Historical trace loaded")
        trend.grab().save(str(out / "historian-renamed-loop.png"))
        details = trend.show_point_details()
        wait(lambda: details.worker is None, "Recorded release provenance visible")
        details.grab().save(str(out / "historian-recorded-releases.png"))
        session = CatalogSession(profile=profile, cache_dir=directory / "catalog")
        catalog = ConfigurationCatalogDialog(session=session)
        windows.append(catalog)
        catalog.browser._selected = project["id"]
        catalog.show()
        wait(lambda: catalog.browser.index is not None and catalog.browser._request is None, "Shared engineering inspector ready")
        catalog.browser.inspect(new)
        wait(lambda: catalog.browser._inspection_worker is None and catalog.browser.revision_model.rowCount() >= 2, "Object history spans rename")
        assert catalog.browser.index.entries[new]["point_id"] == identity
        catalog.browser.tabs.setCurrentWidget(catalog.browser.revisions)
        catalog.grab().save(str(out / "shared-revision-inspector.png"))
        catalog.browser.inspect("display:" + graphic_path)
        captured_preview = catalog.browser.preview()
        wait(lambda: captured_preview.worker is None, "Retained classes loaded in engineering preview")
        assert hasattr(captured_preview, "viewer"), captured_preview.status.text()
        wait(lambda: captured_preview.viewer.isVisible() and captured_preview.viewer.viewport().height() > 200,
             "Captured display laid out for first paint")
        assert captured_preview.viewer.scene().items()
        captured_preview.grab().save(str(out / "captured-display-preview.png"))
        assert prepare_import(read_project(ROOT / "projects/AzeoPlantVirtualController")["files"]).digest == original_digest
        write_json_transactional(out / "receipt.json", {"project": project, "runtime_directory": str(runtime_dir),
                                                        "point_id": identity, "releases": [first["id"], second["id"]],
                                                        "original_source_digest": original_digest, "qt_messages": qt_messages})
        print("PASS native class/history acceptance:", out, flush=True)
    finally:
        for window in reversed(windows):
            window.close()
        background(_shutdown, "Qt workers stopped")
        app.processEvents()
        pool.shutdown()
        (out / "qt-messages.json").write_text(json.dumps(qt_messages, indent=2))


if __name__ == "__main__":
    main()
