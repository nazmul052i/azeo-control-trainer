"""Baseline isolation, retained provenance and the existing exercise runner."""
import base64
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from test_configuration_editing import database as database_fixture, files as files_fixture, pilot as pilot_fixture, changed, commit
from azeo_control_trainer.core.configuration.documents import Conflict, Forbidden
from azeo_control_trainer.core.configuration.releases import ReleaseRepository
from azeo_control_trainer.core.configuration.training import TrainingRepository, export_exercise

database, files, pilot = database_fixture, files_fixture, pilot_fixture


def baseline(pilot, *, exercise=None, snapshot=None):
    repo, token, _, _, project = pilot
    releases = ReleaseRepository(repo, validator=lambda bundle, selected: [])
    paths = [o["path"] for o in repo.export(token, project)["objects"] if o["kind"] in {"module", "display"}]
    preview = releases.preview(token, project, paths)
    release = releases.create(token, project, preview["id"], "Baseline release", str(uuid4()))
    training = TrainingRepository(repo)
    result = training.create(token, project, release["id"], "Starting condition", "Instructor baseline", str(uuid4()),
                             exercise=exercise, snapshot=snapshot)
    return training, result


def test_two_trainees_clone_exact_release_after_source_changes_and_have_separate_access(pilot):
    repo, token, editing, _, project = pilot
    training, retained = baseline(pilot)
    original = repo.export(token, project)
    commit(editing, token, project, changed(repo, token, project))
    command = str(uuid4())
    first = training.clone(token, project, retained["id"], "Trainee A", command)
    assert training.clone(token, project, retained["id"], "Trainee A", command) == first
    second = training.clone(token, project, retained["id"], "Trainee B", str(uuid4()))
    a, b = (repo.export(token, result["project_id"]) for result in (first, second))
    assert a["files"] == b["files"] == original["files"]
    assert not {o["id"] for o in a["objects"]} & {o["id"] for o in b["objects"]}
    before_b = b["digest"]
    commit(editing, token, first["project_id"], changed(repo, token, first["project_id"]))
    assert repo.export(token, second["project_id"])["digest"] == before_b
    assert not training.context(token, first["project_id"])["matches_baseline"]
    identity = repo.provision_identity("trainee-a")
    repo.grant("trainee-a", first["project_id"], "engineer")
    assert training.context(identity, first["project_id"])["baseline"]["id"] == retained["id"]
    with pytest.raises(Forbidden):
        repo.export(identity, second["project_id"])
    with pytest.raises(Forbidden):
        training.clone(identity, project, retained["id"], "Escalation", str(uuid4()))


def test_baseline_is_immutable_and_creation_replay_is_checked(pilot):
    repo, token, _, _, project = pilot
    training, row = baseline(pilot)
    repeated = training.create(token, project, row["release_id"], row["name"], row["reason"], row["command_id"])
    assert repeated == row
    with pytest.raises(Conflict):
        training.create(token, project, row["release_id"], "Changed", row["reason"], row["command_id"])
    import psycopg
    with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
        with repo.connection() as c:
            c.execute("UPDATE training_baselines SET name='changed' WHERE id=%s", (row["id"],))


def test_exported_exercise_retains_snapshot_and_remaps_module_identities(pilot, tmp_path):
    from test_simulation_workbench import _service
    from azeo_control_trainer.core.simulation.training import TrainingSession, Exercise
    from test_engineering_training import Source
    workbench, _, _ = _service(tmp_path)
    session = TrainingSession(workbench, Source(), Source().alarm_state, tmp_path / "training")
    snapshot = json.loads(session.capture_baseline().read_text())
    from azeo_control_trainer.core.strategy.serialization.strategy_io import graph_from_document
    bundle = pilot[0].export(pilot[1], pilot[4])
    graphs = [graph_from_document(json.loads(base64.b64decode(f["content"])), strict=True)[0]
              for f in bundle["files"] if f["path"].startswith("control/")]
    snapshot["controller"] = [{"name": graph.name, "blocks": [{"id": b.id, "type": b.block_type} for b in graph.blocks.values()]} for graph in graphs]
    exercise = {"name": "Recorded loop", "loop": "LOOP/PID1", "objectives": ["Explain the response"]}
    training, row = baseline(pilot, exercise=exercise, snapshot=snapshot)
    clone = training.clone(pilot[1], pilot[4], row["id"], "Exercise trainee", str(uuid4()))
    context = training.context(pilot[1], clone["project_id"])
    path = export_exercise(context, tmp_path / "installed")
    installed = Exercise(**json.loads(path.read_text()))
    assert hashlib.sha256(Path(installed.snapshot).read_bytes()).hexdigest() == installed.configuration["snapshot_hash"]
    assert installed.configuration["modules"]
    assert base64.b64decode(row["document"]["snapshot"]) == Path(installed.snapshot).read_bytes()


def test_training_preflight_rejects_wrong_project_before_process_restore_and_records_point_context(tmp_path):
    from test_simulation_workbench import _service
    from test_engineering_training import Source
    from azeo_control_trainer.core.simulation.training import TrainingSession, Exercise
    workbench, ai, _ = _service(tmp_path)
    ai.outputs["FIRST_OUT"] = ai.outputs["OUT"]
    source = Source()
    source.values["M100/AI-101/OUT"] = 12.0
    source.values["M100/AI-101/FIRST_OUT"] = 1
    session = TrainingSession(workbench, source, source.alarm_state, tmp_path / "training")
    snapshot = session.capture_baseline()
    graph = workbench._runtimes()[0].compiled.graph
    graph._configuration_identity = {"project_id": str(uuid4()), "object_id": str(uuid4()), "object_digest": "checked",
                                     "revision": 1, "release_id": str(uuid4()), "package_hash": "package"}
    context = graph._configuration_identity
    for runtime in workbench._runtimes()[1:]:
        runtime.compiled.graph._configuration_identity = {**context, "object_id": str(uuid4())}
    exercise = Exercise(loop="M100/PID", snapshot=str(snapshot), paths=["M100/AI-101/OUT"], configuration={
        "baseline_id": str(uuid4()), "project_id": str(uuid4()), "snapshot_hash": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        "modules": {r.compiled.graph._configuration_identity["object_id"]: "checked" for r in workbench._runtimes()}})
    from azeo_control_trainer.core.configuration.release_manifest import fingerprint
    exercise.configuration["exercise_hash"] = fingerprint({key: getattr(exercise, key) for key in ("name", "loop", "input_path", "objectives", "paths")})
    with pytest.raises(ValueError, match="trainee baseline"):
        session.start(exercise)
    exercise.configuration["project_id"] = context["project_id"]
    session.start(exercise)
    session.finish("Evidence captured")
    session.restart()
    session.finish("Repeated immutable baseline")
    data = session.archive.read(session.identity)
    assert data["configuration"][graph.name]["release_id"] == context["release_id"]
    assert data["events"][0]["session_id"] == session.identity
    assert data["events"][0]["baseline"]["baseline_id"] == exercise.configuration["baseline_id"]
    point = data["samples"][0]["values"]["M100/AI-101/OUT"]
    assert point["point_id"] and point["configuration"]["release_id"] == context["release_id"]
    assert "M100/AI-101/FIRST_OUT" in data["samples"][0]["values"]
    assert exercise.paths == ["M100/AI-101/OUT"]


def test_rejected_review_can_be_corrected_while_unconfirmed_commands_remain_retryable(tmp_path, monkeypatch):
    from azeo_control_trainer.core.presentation import configuration_releases as ui
    from azeo_control_trainer.core.configuration.client import ServiceUnavailable
    from azeo_control_trainer.core.configuration.documents import ConfigurationError
    monkeypatch.setattr(ui, "_pending_root", lambda: tmp_path)
    failure = [ConfigurationError("invalid review")]
    def request(*_args, **_kwargs):
        raise failure[0]
    monkeypatch.setattr(ui.ConfigurationClient, "request", request)
    profile = {"url": "http://127.0.0.1:8766", "token": "test"}
    payload = {"command_id": str(uuid4())}
    with pytest.raises(ConfigurationError):
        ui.send_command(profile, "project", "/v1/projects/project/baselines", payload)
    assert not list(tmp_path.glob("*.json"))
    failure[0] = ServiceUnavailable("reply lost")
    with pytest.raises(ServiceUnavailable):
        ui.send_command(profile, "project", "/v1/projects/project/baselines", payload)
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_isolated_training_boot_does_not_read_an_excluded_mutable_process_snapshot(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from azeo_control_trainer.azeo_explorer.configuration_training_runtime import attach_training_process
    from azeo_control_trainer.connectivity.fieldio import local_virtual_io
    configured = {"areas": [{"virtual_io": {"type": "local_virtual_io", "snapshot": "virtual_io/excluded.snapshot.json", "provider": {
        "factory": "azeoplant.embedding:create_embedded_plant", "search_paths": ["untrusted/path"],
        "options": {"snapshot": "${PROJECT_DIR}/virtual_io/excluded.snapshot.json"}}}}]}
    (tmp_path / "_project.json").write_text(json.dumps(configured))
    class Driver:
        def __init__(self, store, config, root):
            self.config = config
            self.running = False
        def start(self):
            assert self.config["provider"]["options"]["snapshot"] is None
            assert self.config["snapshot"] is None
            assert self.config["provider"]["search_paths"] != ["untrusted/path"]
            self.running = True
            return True
    monkeypatch.setattr(local_virtual_io, "LocalVirtualIoDriver", Driver)
    store = SimpleNamespace()
    driver = attach_training_process(store, tmp_path)
    assert store.field_io_driver is driver and driver.running
    assert driver._configuration_provider_identity["implementation_hash"]


def test_runtime_closes_station_evidence_before_detaching_its_process_clock():
    from types import SimpleNamespace
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import QApplication, QDialog
    from azeo_control_trainer.azeo_explorer.configuration_runtime import RuntimeWindow
    from azeo_control_trainer.core.presentation.configuration_catalog import _workers
    app = QApplication.instance() or QApplication([])
    window = RuntimeWindow.__new__(RuntimeWindow)
    QDialog.__init__(window)
    clock, evidence = [12.0], []
    window.closing, window.worker = False, None
    window.timer = SimpleNamespace(stop=lambda: None)
    window.executive = SimpleNamespace(stop=lambda: None)
    window.training_driver = SimpleNamespace(stop=lambda: clock.__setitem__(0, 0.0))
    window.station = SimpleNamespace(close=lambda: evidence.append(clock[0]))
    window.adapter = SimpleNamespace(observation=lambda **_: {})
    window.client = SimpleNamespace(request=lambda *_: None)
    window.prefix, window.boot, window.inspectors = "/test", "test", []
    before = set(_workers)
    window.closeEvent(QCloseEvent())
    for worker in set(_workers) - before:
        worker.wait()
    app.processEvents()
    assert evidence == [12.0]
    assert clock[0] == 0.0
