"""Native isolated APVC release/download/Refresh/upload/recovery acceptance.

--prepare creates a fresh repository fork and private runtime profile. The
normal mode drives the actual Qt release controls against that private target.
No source project files, external transports or original project modes change.
"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OUT = ROOT / "logs/configuration-release-ui"
OUT.mkdir(exist_ok=True)
RECEIPT = ROOT / "data/configuration/release_acceptance.json"
PARENT = "3ce164c4-daf5-4f78-863e-b6b2e8f696aa"
MODULE = "control/PIC-2001.json"
GRAPHIC = "displays/pvm/U200 - L2 Recycle Compression/draft.json"


def prepare():
    from azeo_control_trainer.core.configuration.client import ConfigurationClient, read_profile
    from azeo_control_trainer.core.configuration.documents import read_project, prepare_import
    from azeo_control_trainer.core.configuration.workspace import DraftWorkspace
    from azeo_control_trainer.core.strategy.serialization.strategy_io import write_json_transactional
    client = ConfigurationClient(**read_profile(), timeout=150)
    source_digest = prepare_import(read_project(ROOT / "projects/AzeoPlantVirtualController")["files"]).digest
    name = "APVC Release Pilot " + time.strftime("%Y%m%d-%H%M%S")
    fork = client.request(f"/v1/projects/{PARENT}/editing/fork", {"name": name, "command_id": str(uuid4())})
    project = next(p for p in client.request("/v1/projects") if p["id"] == fork["project_id"])
    workspace = DraftWorkspace.create(ROOT / "data/configuration/workspaces" / str(uuid4()), project["id"])
    module_path, graphic_path = workspace.root / MODULE, workspace.root / GRAPHIC
    module = json.loads(module_path.read_text(encoding="utf-8"))
    module["description"] = str(module.get("description", "")) + " — isolated release training pilot"
    write_json_transactional(module_path, module)
    graphic = json.loads(graphic_path.read_text(encoding="utf-8"))
    next(item for item in graphic["items"] if item["id"] == "u200_process_panel")["routing_obstacle"] = False
    for item in graphic["items"]:
        if item["id"] in {"u200_in_label", "u200_out_label"}:
            item["y"] += 50
    write_json_transactional(graphic_path, graphic)
    checkin = workspace.checkin(workspace.preview(), "Prepare isolated PIC-2001/U200 release exercise; clear background and label pipe obstructions")
    workspace.release()
    target = client.request(f"/v1/projects/{project['id']}/runtime-targets", {"name": "APVC isolated runtime"})
    directory = ROOT / "data/configuration/runtime_nodes" / target["id"]
    write_json_transactional(directory / "target.json", {**target, "url": client.url})
    write_json_transactional(RECEIPT, {"project": project, "directory": str(directory), "workspace": str(workspace.directory),
                                      "source_digest": source_digest, "checkin": checkin})
    print("Prepared", project["name"], project["id"], "generation", checkin["generation"], flush=True)


def verify():
    os.environ["QT_QPA_PLATFORM"] = "windows"
    from PySide6.QtCore import QSettings, Qt, qInstallMessageHandler
    from PySide6.QtWidgets import QApplication
    from azeo_control_trainer.core.configuration.client import ConfigurationClient, ServiceUnavailable, read_profile
    from azeo_control_trainer.core.configuration.documents import read_project, prepare_import
    from azeo_control_trainer.core.hmi.theme.fonts import apply_application_font
    from azeo_control_trainer.core.presentation.configuration_catalog import _shutdown
    from azeo_control_trainer.core.presentation.configuration_releases import ReleaseManager, UploadDialog
    from azeo_control_trainer.azeo_explorer.configuration_runtime import RuntimeWindow

    setup = json.loads(RECEIPT.read_text(encoding="utf-8"))
    project, directory = setup["project"], Path(setup["directory"])
    client = ConfigurationClient(**read_profile(), timeout=150)
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    apply_application_font()
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(directory / "verification-settings"))
    qt_messages = []
    qInstallMessageHandler(lambda kind, context, message: qt_messages.append(message))
    pool = ThreadPoolExecutor(2)
    manager = ReleaseManager(project)
    runtime = RuntimeWindow(directory)
    manager.show()
    runtime.show()

    def wait(predicate, label, timeout=150):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                print("PASS", label, flush=True)
                return
            time.sleep(0.01)
        raise AssertionError(label + " timed out: " + manager.status.text() + " / " + runtime.status.text())

    def background(operation, label):
        future = pool.submit(operation)
        wait(future.done, label)
        return future.result()

    def refresh():
        wait(lambda: manager.worker is None, "release manager idle")
        manager.refresh()
        wait(lambda: manager.worker is None, "fresh target evidence")

    def make_release(reason):
        wait(lambda: manager.worker is None, "release selection available")
        before = len(manager._dialogs)
        for row in range(manager.objects.rowCount()):
            path = manager.objects.item(row, 1).text()
            manager.objects.item(row, 0).setCheckState(Qt.Checked if path in {MODULE, GRAPHIC} else Qt.Unchecked)
        manager.preview()
        wait(lambda: manager.worker is None and len(manager._dialogs) > before, "real release verification completed")
        review = manager._dialogs[-1]
        assert not [f for f in review.preview["manifest"]["findings"] if f["severity"] == "ERROR"], review.preview
        assert len(review.preview["manifest"]["selected"]) == 8
        review.reason.setText(reason)
        review.reviewed.setChecked(True)
        assert review.commit_button.isEnabled()
        review.grab().save(str(OUT / "release-review.png"))
        review.commit()
        wait(lambda: review.worker is None and not review.isVisible(), "reviewed immutable release created")
        refresh()
        manager.releases.selectRow(0)
        return manager.release_model.rows[0]

    try:
        wait(lambda: manager.state is not None and manager.worker is None and runtime.restored, "native Release Manager and runtime ready")
        first_release = make_release("PIC-2001 and U200 — initial release integration exercise")
        lost = {"raised": False}
        original_request = runtime.client.request
        def lose_ack(path, payload=None):
            result = original_request(path, payload)
            if path.startswith(runtime.prefix + "/jobs/") and payload.get("state") == "delivered" and not lost["raised"]:
                lost["raised"] = True
                raise ServiceUnavailable("Injected lost delivery acknowledgment")
            return result
        runtime.client.request = lose_ack
        manager.deploy()
        wait(lambda: len(runtime.adapter.loaded) == 7 and bool(runtime.deployment.displays()), "real controller download and station publication")
        active = runtime.adapter.graphs()["PIC-2001"]
        loop = next(entry["runtime"] for entry in runtime.adapter.loaded.values() if entry["runtime"].compiled.graph.name == "PIC-2001")
        wait(lambda: lost["raised"] and runtime.completion is None and runtime.worker is None, "lost acknowledgment recovered")
        assert runtime.adapter.graphs()["PIC-2001"] is active
        assert len(runtime.display_store.history("U200 - L2 Recycle Compression")) == 1
        runtime.client.request = original_request
        runtime.open_station()
        station = runtime.station
        station.show_display("U200 - L2 Recycle Compression")
        wait(lambda: loop.scan_count > 3, "existing controller executive is scanning")
        runtime.open_engineering("PIC-2001")
        wait(lambda: runtime.worker is None, "runtime observation worker idle")
        runtime.poll()
        wait(lambda: runtime.worker is None, "station acceptance reported")
        refresh()
        matches = [row for row in manager.state["comparison"] if row["path"] in {MODULE, GRAPHIC}]
        assert all(row["running"] is not None for row in matches), [(r["path"], r["status"]) for r in matches]
        assert all(row["status"] == "Current" for row in matches), [(r["path"], r["status"]) for r in matches]
        assert manager.comparison_model.rowCount() == 8
        manager.tabs.setCurrentIndex(2)
        manager.grab().save(str(OUT / "configured-running.png"))
        station.grab().save(str(OUT / "operator-u200.png"))
        before = loop.scan_count
        runtime.client.request = lambda *args, **kwargs: (_ for _ in ()).throw(ServiceUnavailable("Injected engineering API outage"))
        wait(lambda: loop.scan_count >= before + 6 and runtime.worker is None, "controller continues through engineering outage", timeout=30)
        assert station.show_display("U200 - L2 Recycle Compression")
        runtime.client.request = original_request
        before_view = station.view
        tuning_path = "PIC-2001/PIC-2001/CONFIG/GAIN"
        old_gain = station.live_source.read(tuning_path).value
        gain = float(old_gain) + 0.125
        scan_before_write = loop.scan_count
        result = station._write(tuning_path, gain)
        assert result.success, result
        wait(lambda: loop.scan_count > scan_before_write and runtime.store.get("ctrl.PIC-2001.GAIN") == gain,
             "controller scan publishes accepted tuning")
        wait(lambda: runtime.worker is None and runtime.completion is None, "runtime ready after tuning")
        runtime.poll()
        wait(lambda: runtime.worker is None, "online tuning observation reported")
        wait(lambda: manager.worker is None, "upload review available")
        manager.upload()
        wait(lambda: manager.worker is None and isinstance(manager._dialogs[-1], UploadDialog), "selective upload comparison opened")
        upload = manager._dialogs[-1]
        assert not [row for row in upload.preview["rows"] if row["category"] not in {"sp", "mode"}
                    and (row["module"], row["parameter"]) != ("PIC-2001", "GAIN")], upload.preview["rows"]
        candidates = [(i, row) for i, row in enumerate(upload.preview["rows"]) if row["module"] == "PIC-2001" and row["parameter"] == "GAIN"]
        assert len(candidates) == 1 and candidates[0][1]["runtime_value"] == gain, candidates
        upload.table.item(candidates[0][0], 0).setCheckState(Qt.Checked)
        upload.reason.setText("Retain the reviewed PIC-2001 training gain")
        upload.grab().save(str(OUT / "selective-upload.png"))
        upload.commit()
        wait(lambda: upload.worker is None and not upload.isVisible(), "selected tuning checked in as a new engineering revision")
        assert runtime.adapter.graphs()["PIC-2001"] is active and station.view is before_view
        # A second graphics revision proves that publishing does not replace the
        # operator's accepted display or its class/asset directory.
        def change_graphic():
            bundle = client.request(f"/v1/projects/{project['id']}/export")
            file = next(f for f in bundle["files"] if f["path"] == GRAPHIC)
            obj = next(o for o in bundle["objects"] if o["path"] == GRAPHIC)
            graphic = json.loads(base64.b64decode(file["content"]))
            graphic["description"] = str(graphic.get("description", "")) + " — reviewed operator refresh exercise"
            session = str(uuid4())
            prefix = f"/v1/projects/{project['id']}/editing"
            client.request(prefix + "/lease", {"session": session, "paths": [GRAPHIC]})
            result = client.request(prefix + "/checkin", {"session": session, "command_id": str(uuid4()),
                "reason": "Prepare operator Refresh exercise", "edits": [{"path": GRAPHIC, "expected_revision": obj["revision"],
                "content": base64.b64encode(json.dumps(graphic).encode()).decode()}]})
            client.request(prefix + "/lease", {"session": session, "paths": [], "release": True})
            return result
        background(change_graphic, "new checked-in graphic revision")
        refresh()
        second = make_release("PIC-2001 tuning and U200 — explicit operator Refresh exercise")
        manager.deploy()
        wait(lambda: len(runtime.display_store.history("U200 - L2 Recycle Compression")) == 2, "second release published")
        assert station.view is before_view
        assert station.view.config_root.name == "pvm"
        assert station.view.config_root.parent.parent.name == first_release["manifest"]["package_hash"]
        station.refresh_configuration()
        assert station.view.config_root.parent.parent.name == second["manifest"]["package_hash"]
        wait(lambda: runtime.completion is None and runtime.worker is None, "second target receipt durable")
        runtime.close()
        background(lambda: _shutdown(), "graceful runtime shutdown report")
        app.processEvents()
        restored = RuntimeWindow(directory)
        restored.show()
        runtime = restored
        wait(lambda: runtime.restored and len(runtime.adapter.loaded) == 7, "controller restored from verified local packages")
        runtime.open_station()
        assert runtime.station.deployment._held("U200 - L2 Recycle Compression") == 2
        wait(lambda: runtime.worker is None and "Connected" in runtime.status.text(), "new runtime session reconnects")
        assert runtime.adapter.graphs()["PIC-2001"].blocks[next(b.id for b in runtime.adapter.graphs()["PIC-2001"].blocks.values() if b.block_type == "PID")].config.params["GAIN"] == gain
        after = background(lambda: prepare_import(read_project(ROOT / "projects/AzeoPlantVirtualController")["files"]).digest,
                           "original project source digest verified")
        assert after == setup["source_digest"]
        assert not qt_messages, qt_messages
        final = {"project": project["id"], "target": runtime.profile["id"], "releases": [first_release["id"], second["id"]],
                 "gain_before": old_gain, "gain_uploaded": gain, "source_digest": after, "qt_messages": qt_messages}
        (OUT / "acceptance.json").write_text(json.dumps(final, indent=2), encoding="utf-8")
        print("NATIVE RELEASE ACCEPTANCE PASSED", flush=True)
    finally:
        runtime.close()
        manager.close()
        _shutdown()
        pool.shutdown(wait=True)
        app.processEvents()


if __name__ == "__main__":
    prepare() if "--prepare" in sys.argv else verify()
