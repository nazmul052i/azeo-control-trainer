"""Native baseline, two-trainee exercise and isolated database restore acceptance.

Creates only new pilot projects, local runtimes and recovery artifacts. The
original virtual-controller project and shipped displays are read-only inputs.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
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
    from PySide6.QtCore import QSettings, qInstallMessageHandler
    from PySide6.QtWidgets import QApplication
    from azeo_control_trainer.core.configuration.client import ConfigurationClient, read_profile
    from azeo_control_trainer.core.configuration.documents import read_project, prepare_import
    from azeo_control_trainer.core.configuration.libraries import decode
    from azeo_control_trainer.core.hmi.pvms.publishing import PvmDisplay
    from azeo_control_trainer.core.hmi.pvms.base import registry
    from azeo_control_trainer.core.hmi.theme.fonts import apply_application_font
    from azeo_control_trainer.core.presentation.configuration_catalog import _shutdown
    from azeo_control_trainer.core.presentation.configuration_training import TrainingManager
    from azeo_control_trainer.azeo_explorer.configuration_runtime import RuntimeWindow
    from azeo_control_trainer.core.strategy.serialization.strategy_io import graph_from_document, write_json_transactional
    from azeo_control_trainer.core.hmi import pvms  # noqa: F401

    out = ROOT / "logs/configuration-training-ui" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    directory = ROOT / "data/configuration/training_acceptance" / out.name
    source = directory / "source"
    source.mkdir(parents=True)
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(directory / "settings"))
    apply_application_font()
    messages = []
    qInstallMessageHandler(lambda kind, context, message: messages.append(message))
    profile = read_profile()
    client = ConfigurationClient(**profile, timeout=600)
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
        wait(future.done, label, timeout=600)
        return future.result()

    def request(path, payload=None):
        return background(lambda: client.request(path, payload), path)

    original = read_project(ROOT / "projects/AzeoPlantVirtualController")
    original_digest = prepare_import(original["files"]).digest
    documents = decode(original)
    vio = next(area["virtual_io"] for area in documents["_project.json"]["areas"] if area.get("virtual_io"))
    module_path = "control/FIC-0101.json"
    for row in original["files"]:
        if row["path"] == module_path or row["path"].startswith(("virtual_io/", "engineering/")):
            target = source / row["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(row["content"]))
    write_json_transactional(source / "_project.json", {"areas": [{"name": "Training", "strategies": [module_path], "virtual_io": vio}]})
    graph = graph_from_document(documents[module_path], strict=True)[0]
    pid = next(b for b in graph.blocks.values() if b.block_type == "PID")
    ai = next(b for b in graph.blocks.values() if b.block_type == "AI")
    loop = f"{graph.name}/{pid.instance_name}"
    input_path = f"{graph.name}/{ai.instance_name}"
    graphic_path = "displays/pvm/Training loop/draft.json"
    item = registry.get("PID", "dynamo_compact")().place("training-loop", path=loop, x=280, y=180)
    display = PvmDisplay(name="Training loop", width=900, height=500, pvms=[item], items=[
        {"id": "title", "kind": "text", "x": 65, "y": 50, "w": 780, "h": 55,
         "text": "FUEL GAS FLOW · TRAINING BASELINE", "font_size": 20, "font_bold": True},
        {"id": "instruction", "kind": "text", "x": 65, "y": 360, "w": 780, "h": 70,
         "text": "Investigate the input quality and control response.\nEach trainee has a separate controller, process and history.", "font_size": 14}])
    write_json_transactional(source / graphic_path, display.to_dict())
    files = read_project(source)["files"]
    name = "Training Recovery " + out.name
    preview = request("/v1/imports/preview", {"name": name + " source", "files": files})
    captured = request("/v1/imports", {"name": name + " source", "files": files,
                       "expected_generation": preview["expected_generation"], "command_id": str(uuid4())})
    teacher = request(f"/v1/projects/{captured['project_id']}/editing/fork", {"name": name, "command_id": str(uuid4())})
    project = next(p for p in request("/v1/projects") if p["id"] == teacher["project_id"])

    def release(project_id):
        prefix = f"/v1/projects/{project_id}"
        reviewed = request(prefix + "/releases/preview", {"paths": [module_path, graphic_path]})
        errors = [f for f in reviewed["manifest"]["findings"] if f["severity"] == "ERROR"]
        assert not errors, errors
        return request(prefix + "/releases", {"preview": reviewed["id"], "reason": "Native training recovery acceptance", "command_id": str(uuid4())})

    def runtime(project_id, label):
        released = release(project_id)
        prefix = f"/v1/projects/{project_id}"
        target = request(prefix + "/runtime-targets", {"name": label + " isolated runtime"})
        folder = directory / label
        write_json_transactional(folder / "target.json", {**target, "url": client.url})
        window = RuntimeWindow(folder)
        windows.append(window)
        window.show()
        request(prefix + "/deployments", {"release": released["id"], "target": target["id"],
                  "components": ["controller", "station"], "command_id": str(uuid4())})
        wait(lambda: bool(window.adapter.loaded) and bool(window.display_store.history("Training loop")), label + " released configuration loaded")
        wait(lambda: window.worker is None, label + " runtime idle")
        window.start_training_process()
        wait(lambda: window.worker is None, label + " process attachment completed")
        assert window.simulation_service is not None, window.status.text()
        window.open_station()
        station = window.station
        station.resize(1200, 760)
        assert station.show_display("Training loop")
        wait(lambda: getattr(station.live_source.read(input_path + "/OUT").quality, "name", "") == "GOOD", label + " actual process input healthy", 30)
        training = station.open_training()
        training.presentation.setCurrentIndex(1)
        training.loop.setCurrentText(loop)
        training.input.setCurrentText(input_path)
        return window, station, training, released

    try:
        teacher_runtime, station, training, teacher_release = runtime(project["id"], "instructor")
        training.capture()
        training.save_exercise()
        manager = TrainingManager(project, profile=profile)
        windows.append(manager)
        manager.show()
        wait(lambda: manager.worker is None and bool(manager.releases), "Native Training Manager loaded")
        review = manager.new_baseline()
        review.name.setText("Normal fuel gas flow and input fault")
        review.reason.setText("Retain exact control, graphics and process starting condition")
        review.attach((asdict(training.definition()), json.loads(Path(training._baseline).read_text(encoding="utf-8"))))
        app.processEvents()
        review.grab().save(str(out / "baseline-review.png"))
        review.create()
        wait(lambda: review.worker is None, "Immutable baseline creation completed")
        assert not review.isVisible(), review.status.text()
        wait(lambda: manager.worker is None and len(manager.model.rows) == 1, "Baseline listed in native UI")
        baseline = manager.model.rows[0]
        retained_baseline = request(f"/v1/projects/{project['id']}/baselines/{baseline['id']}")
        manager.table.selectRow(0)
        clones = []
        for label in ("Trainee A", "Trainee B"):
            clone_name = label + " " + out.name
            manager.trainee_name.setText(clone_name)
            manager.clone()
            wait(lambda: manager.worker is None, label + " clone completed")
            clone = next(p for p in request("/v1/projects") if p["name"] == clone_name)
            clones.append(clone)
        manager.grab().save(str(out / "baselines-and-trainee-copy.png"))
        a, b = [request(f"/v1/projects/{p['id']}/export") for p in clones]
        assert a["files"] == b["files"]
        assert not {o["id"] for o in a["objects"]} & {o["id"] for o in b["objects"]}
        teacher_runtime.close()
        archives, evidence, points = [], [], []
        for label, clone in zip(("trainee-a", "trainee-b"), clones):
            window, station, training, released = runtime(clone["id"], label)
            training.inherited_baseline()
            wait(lambda: training._repository_dialog.worker is None, label + " inherited exercise loaded")
            assert training._configuration.get("baseline_id") == baseline["id"], training._repository_dialog.status.text()
            training.start()
            wait(lambda: len(training.session.archive.read(training.session.identity)["samples"]) >= 2, label + " session recording", 15)
            training.quality.setCurrentText("BAD")
            training.value.setValue(0)
            training.inject()
            wait(lambda: getattr(station.live_source.read(input_path + "/OUT").quality, "name", "") == "BAD", label + " input fault reaches controller", 15)
            wait(lambda: any(s["values"].get(input_path + "/OUT", {}).get("quality") == "BAD"
                             for s in training.session.archive.read(training.session.identity)["samples"]), label + " failed quality recorded", 15)
            training.session.clear_faults()
            wait(lambda: getattr(station.live_source.read(input_path + "/OUT").quality, "name", "") == "GOOD", label + " healthy input restored", 15)
            wait(lambda: getattr(station.live_source.read(loop + "/PV").quality, "name", "") == "GOOD", label + " PID quality recovered", 15)
            before_samples = len(training.session.archive.read(training.session.identity)["samples"])
            wait(lambda: len(training.session.archive.read(training.session.identity)["samples"]) > before_samples,
                 label + " recovered process response recorded", 15)
            training.review.selectRow(1)
            training.evidence.setText("Input BAD quality observed in the controller and trend; normal quality restored after clearing substitution.")
            training.complete()
            training.finish()
            training.status.setText("Session saved with baseline and release evidence")
            app.processEvents()
            training.grab().save(str(out / (label + "-training-report.png")))
            data = training.session.archive.read(training.session.identity)
            context = data["samples"][0]["values"][loop + "/PV"]
            assert context["configuration"]["release_id"] == released["id"], context
            assert context["point_id"] and context["wall_time"] and "sim_time" in context
            assert data["events"][0]["baseline"]["baseline_id"] == baseline["id"]
            points.append(context["point_id"])
            evidence.append({"project": clone["id"], "release": released["id"], "session": training.session.identity,
                             "point_id": context["point_id"], "samples": len(data["samples"]), "events": len(data["events"])})
            training.close()
            station.workspace.hide()
            station.tick()
            app.processEvents()
            station.grab().save(str(out / (label + "-operator.png")))
            archives.extend([(str(window.directory / "history.sqlite3"), "history"),
                             (str(training.session.archive.path), "training"),
                             (str(window.simulation_service.journal.path), "journal")])
            window.close()
        assert points[0] != points[1]
        prefix = f"/v1/projects/{clones[0]['id']}"
        session = str(uuid4())
        obj = next(o for o in a["objects"] if o["path"] == module_path)
        changed = decode(a)[module_path]
        changed["description"] = "Trainee A independent engineering change"
        request(prefix + "/editing/lease", {"session": session, "paths": [module_path]})
        request(prefix + "/editing/checkin", {"session": session, "command_id": str(uuid4()),
            "reason": "Verify independent trainee configuration", "edits": [{"path": module_path,
            "expected_revision": obj["revision"], "content": base64.b64encode(json.dumps(changed).encode()).decode()}]})
        request(prefix + "/editing/lease", {"session": session, "paths": [], "release": True})
        assert request(f"/v1/projects/{clones[1]['id']}/export")["digest"] == b["digest"]
        assert request(f"/v1/projects/{project['id']}/baselines/{baseline['id']}")["document"]["hash"] == retained_baseline["document"]["hash"]
        recovery = manager.recovery()
        wait(lambda: recovery.worker is None and "completed backups" in recovery.status.text(), "Native Recovery Manager loaded")
        recovery.archives = archives
        recovery.archive_label.setText("Two trainee seats · historian, training session and operator journal for each")
        recovery.name.setText("Verified two-trainee checkpoint " + out.name)
        before = len(recovery.backup_model.rows)
        recovery.backup()
        wait(lambda: recovery.worker is None, "Configuration and evidence backup completed", 600)
        assert len(recovery.backup_model.rows) == before + 1, recovery.status.text()
        backup = recovery.backup_model.rows[0]
        assert len(backup["evidence"]) == 6
        recovery.grab().save(str(out / "consistent-backup.png"))
        recovery.rehearse()
        wait(lambda: recovery.worker is None, "Real PostgreSQL and evidence restore completed", 600)
        assert recovery.restore_model.rows and recovery.restore_model.rows[0]["backup"] == backup["id"], recovery.status.text()
        restored = recovery.restore_model.rows[0]
        assert restored["state"] == "verified" and not restored["runtime_started"]
        app.processEvents()
        recovery.grab().save(str(out / "verified-restore.png"))
        portable = background(lambda: client.download_backup(backup["id"], out / "training-recovery.zip"), "Portable backup exported")
        imported = background(lambda: client.upload_evidence(portable, "backup", str(uuid4())), "Portable backup imported and verified")
        assert imported["hash"] == backup["hash"]
        assert prepare_import(read_project(ROOT / "projects/AzeoPlantVirtualController")["files"]).digest == original_digest
        result = {"project": project["id"], "baseline": baseline["id"], "trainees": evidence,
                  "backup": backup["id"], "restore": restored, "original_digest": original_digest, "qt_messages": messages}
        write_json_transactional(out / "receipt.json", result)
        print("PASS native Phase 6 acceptance", out, flush=True)
        print("Qt messages:", messages, flush=True)
    finally:
        for window in reversed(windows):
            window.close()
        app.processEvents()
        _shutdown()
        pool.shutdown(wait=True)
    print("PASS clean native shutdown", flush=True)


def review_station(directory):
    """Reopen a verified trainee seat and capture settled live rendering."""
    from PySide6.QtCore import QSettings, qInstallMessageHandler
    from PySide6.QtWidgets import QApplication
    from azeo_control_trainer.azeo_explorer.configuration_runtime import RuntimeWindow
    from azeo_control_trainer.core.hmi.theme.fonts import apply_application_font
    from azeo_control_trainer.core.presentation.configuration_catalog import _shutdown
    from azeo_control_trainer.core.configuration.client import ConfigurationClient, read_profile
    from azeo_control_trainer.core.strategy.serialization.strategy_io import write_json_transactional
    directory = Path(directory).resolve()
    source_directory = directory
    client = ConfigurationClient(**read_profile(), timeout=150)
    project = json.loads((directory / "target.json").read_text(encoding="utf-8"))["project_id"]
    prefix = f"/v1/projects/{project}"
    bundle = client.request(prefix + "/export")
    preview = client.request(prefix + "/releases/preview", {"paths": [o["path"] for o in bundle["objects"] if o["kind"] in {"module", "display"}]})
    assert not [f for f in preview["manifest"]["findings"] if f["severity"] == "ERROR"]
    released = client.request(prefix + "/releases", {"preview": preview["id"], "reason": "Verify recovered display and active-session shutdown", "command_id": str(uuid4())})
    target = client.request(prefix + "/runtime-targets", {"name": "Recovery review " + uuid4().hex[:8]})
    directory = directory / "reviews" / time.strftime("%Y%m%d-%H%M%S")
    write_json_transactional(directory / "target.json", {**target, "url": client.url})
    client.request(prefix + "/deployments", {"release": released["id"], "target": target["id"],
                    "components": ["controller", "station"], "command_id": str(uuid4())})
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(directory.parent / "settings"))
    apply_application_font()
    messages = []
    qInstallMessageHandler(lambda kind, context, message: messages.append(message))
    runtime = RuntimeWindow(directory)
    runtime.show()
    def wait(predicate):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return
            time.sleep(.01)
        raise AssertionError(runtime.status.text())
    try:
        wait(lambda: runtime.restored and runtime.worker is None and bool(runtime.adapter.loaded)
             and bool(runtime.display_store.history("Training loop")))
        runtime.start_training_process()
        wait(lambda: runtime.worker is None)
        assert runtime.simulation_service is not None, runtime.status.text()
        runtime.open_station()
        station = runtime.station
        training = station.open_training()
        training.presentation.setCurrentIndex(1)
        training.inherited_baseline()
        wait(lambda: training._repository_dialog.worker is None)
        assert training._configuration, training._repository_dialog.status.text()
        training.start()
        loop, input_path = training.session.exercise.loop, training.session.exercise.input_path
        training.session.inject_input(input_path, 0, "BAD")
        wait(lambda: station.live_source.read(loop + "/PV").quality.name == "BAD")
        training.session.clear_faults()
        wait(lambda: station.live_source.read(loop + "/PV").quality.name == "GOOD")
        before = len(training.session.archive.read(training.session.identity)["samples"])
        wait(lambda: len(training.session.archive.read(training.session.identity)["samples"]) > before)
        data = training.session.archive.read(training.session.identity)
        assert data["samples"][-1]["values"][loop + "/PV"]["quality"] == "GOOD"
        training.finish()
        training.close()
        station.workspace.hide()
        before = station.historian.sample_count
        wait(lambda: station.historian.sample_count > before)
        station.view.fit_display()
        app.processEvents()
        out = ROOT / "logs/configuration-training-ui" / source_directory.parent.name
        station.grab().save(str(out / (source_directory.name + "-operator-recovered.png")))
        print("PASS settled Operator Live after fault recovery", loop, flush=True)
        training.session.restart()
        wait(lambda: training.session.elapsed() >= 1)
        duration = training.session.elapsed()
        runtime.close()
        data = training.session.archive.read(training.session.identity)
        assert data["status"] == "finished" and data["duration"] >= duration
        assert data["events"][-1]["sim_time"] >= 1
        print("PASS active exercise saved before process clock detached", flush=True)
        print("Qt messages:", messages, flush=True)
    finally:
        runtime.close()
        app.processEvents()
        _shutdown()
    print("PASS clean review shutdown", flush=True)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--review-station":
        review_station(sys.argv[2])
    else:
        main()
