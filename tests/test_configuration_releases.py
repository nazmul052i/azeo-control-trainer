"""Release immutability, independent runtime identity, recovery and real editor validation."""
import json
from uuid import uuid4

import pytest

from test_configuration_editing import database as database_fixture, files as files_fixture, pilot as pilot_fixture, changed, commit
from azeo_control_trainer.core.configuration.documents import Conflict, Forbidden
from azeo_control_trainer.core.configuration.releases import ReleaseRepository

database, files, pilot = database_fixture, files_fixture, pilot_fixture


def package(pilot, validator=lambda bundle, paths: []):
    repo, token, editing, source, project = pilot
    releases = ReleaseRepository(repo, validator=validator)
    preview = releases.preview(token, project, ["control/LOOP.json"])
    release = releases.create(token, project, preview["id"], "Reviewed training loop", str(uuid4()))
    return releases, token, project, release


def target_job(pilot):
    releases, token, project, release = package(pilot)
    target = releases.register_target(token, project, "Pilot runtime")
    boot = str(uuid4())
    releases.hello(target["token"], target["id"], boot)
    job = releases.enqueue(token, project, release["id"], target["id"], ["controller"], str(uuid4()))
    return releases, token, project, release, target, boot, job


def test_release_pins_every_file_and_keeps_exact_content_after_edit(pilot):
    releases, token, project, release = package(pilot)
    before = releases.bundle(token, project, release["id"])
    commit(pilot[2], token, project, changed(pilot[0], token, project))
    assert releases.bundle(token, project, release["id"]) == before
    assert len(release["manifest"]["objects"]) == len(before["bundle"]["files"])
    assert any(o["path"] == "assets/custom.bin" for o in release["manifest"]["objects"])
    import psycopg
    with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
        with pilot[0].connection() as c:
            c.execute("UPDATE releases SET reason='changed' WHERE id=%s", (release["id"],))


def test_error_gate_stale_review_and_command_replay(pilot):
    repo, token, editing, _, project = pilot
    releases = ReleaseRepository(repo, validator=lambda bundle, paths: [{"severity": "error", "message": "Broken binding"}])
    bad = releases.preview(token, project, ["control/LOOP.json"])
    with pytest.raises(Conflict, match="Verification errors"):
        releases.create(token, project, bad["id"], "Cannot bypass", str(uuid4()))
    releases.validator = lambda bundle, paths: []
    preview = releases.preview(token, project, ["control/LOOP.json"])
    commit(editing, token, project, changed(repo, token, project))
    with pytest.raises(Conflict, match="changed"):
        releases.create(token, project, preview["id"], "Stale", str(uuid4()))
    preview = releases.preview(token, project, ["control/LOOP.json"])
    command = str(uuid4())
    first = releases.create(token, project, preview["id"], "Confirmed", command)
    assert releases.create(token, project, preview["id"], "Confirmed", command) == first
    with pytest.raises(Conflict, match="identity"):
        releases.create(token, project, preview["id"], "Different request", command)


def test_target_only_acknowledgments_and_boot_fencing(pilot):
    releases, token, project, release, target, boot, job = target_job(pilot)
    with pytest.raises(Forbidden):
        releases.report(token, target["id"], boot, {"objects": []})
    with pytest.raises(Forbidden):
        releases.create(target["token"], project, str(uuid4()), "Runtime cannot release", str(uuid4()))
    with pytest.raises(Conflict, match="still connected"):
        releases.hello(target["token"], target["id"], str(uuid4()))
    releases.report(target["token"], target["id"], boot, {"objects": []}, online=False)
    replacement = str(uuid4())
    releases.hello(target["token"], target["id"], replacement)
    with pytest.raises(Conflict, match="superseded"):
        releases.acknowledge(target["token"], target["id"], boot, job["id"], "staged", {})


def test_delivery_is_not_running_and_expired_target_is_unknown(pilot):
    releases, token, project, release, target, boot, job = target_job(pilot)
    receipt = {"components": ["controller"], "package_hash": release["manifest"]["package_hash"]}
    first = releases.acknowledge(target["token"], target["id"], boot, job["id"], "delivered", receipt)
    assert releases.acknowledge(target["token"], target["id"], boot, job["id"], "delivered", receipt) == first
    assert not releases.pending(target["token"], target["id"], boot)
    state = releases.state(token, project)
    assert all(row["running"] is None for row in state["comparison"])
    obj = next(o for o in release["manifest"]["objects"] if o["path"] == "control/LOOP.json")
    report = {"objects": [{"object_id": obj["id"], "revision": obj["revision"], "digest": obj["digest"], "active": True, "scan_count": 7}]}
    releases.report(target["token"], target["id"], boot, report)
    assert next(r for r in releases.state(token, project)["comparison"] if r["path"] == obj["path"])["status"] == "Current"
    with pilot[0].connection() as c:
        c.execute("UPDATE runtime_targets SET heartbeat=clock_timestamp()-interval '31 seconds' WHERE id=%s", (target["id"],))
    assert all(r["status"] == "Unknown" and r["running"] is None for r in releases.state(token, project)["comparison"])


def test_durable_retry_preserves_order_and_partial_evidence(pilot):
    releases, token, project, release, target, boot, job = target_job(pilot)
    second = releases.enqueue(token, project, release["id"], target["id"], ["controller"], str(uuid4()))
    failure = {"message": "Station disconnected", "controller": "loaded"}
    releases.acknowledge(target["token"], target["id"], boot, job["id"], "failed", failure)
    restarted_service = ReleaseRepository(pilot[0])
    assert not restarted_service.pending(target["token"], target["id"], boot)
    releases.retry(token, project, job["id"])
    pending = restarted_service.pending(target["token"], target["id"], boot)
    assert pending[0]["id"] == job["id"] and pending[0]["receipt"] == failure
    assert second["id"] != pending[0]["id"]
    assert [row["event"] for row in releases.state(token, project)["events"]][:2] == ["retry", "failed"]


def test_cancelling_failed_job_unblocks_corrected_release_and_keeps_failure_evidence(pilot):
    releases, token, project, release, target, boot, job = target_job(pilot)
    with pytest.raises(Conflict, match="Only a failed"):
        releases.cancel(token, project, job["id"])
    receipt = {"message": "Package verification failed"}
    releases.acknowledge(target["token"], target["id"], boot, job["id"], "failed", receipt)
    second = releases.enqueue(token, project, release["id"], target["id"], ["controller"], str(uuid4()))
    releases.cancel(token, project, job["id"])
    retry = releases.acknowledge(target["token"], target["id"], boot, job["id"], "failed", receipt)
    assert retry["state"] == "cancelled" and retry["receipt"] == receipt
    assert releases.pending(target["token"], target["id"], boot)[0]["id"] == second["id"]


def test_real_release_validator_compiles_and_detects_invalid_graph(tmp_path, files):
    import base64
    from azeo_control_trainer.core.configuration.release_validation import validate_bundle
    from azeo_control_trainer.core.configuration.documents import prepare_import
    bundle = {"files": files, "digest": prepare_import(files).digest}
    findings = validate_bundle(bundle, ["control/LOOP.json"])
    assert any(f["severity"] == "ERROR" and "process variable input" in f["message"] for f in findings)
    from azeo_control_trainer.core.strategy.serialization.strategy_io import graph_from_document
    from azeo_control_trainer.core.strategy.blocks.io_blocks import AIBlock
    from test_configuration_database import encoded
    graph, _ = graph_from_document(json.loads(base64.b64decode(files[1]["content"])), strict=True)
    pid = next(iter(graph.blocks.values()))
    ai = AIBlock("AI1")
    ai.config.params["tag"] = "test.PV"
    graph.add_block(ai)
    graph.add_wire(ai.id, "OUT", pid.id, "IN")
    files[1] = encoded("control/LOOP.json", graph.to_dict())
    bundle = {"files": files, "digest": prepare_import(files).digest}
    findings = validate_bundle(bundle, ["control/LOOP.json"])
    assert not [f for f in findings if f["severity"] == "ERROR"]
    assert all({"path", "message", "severity"} <= set(row) for row in findings)


def test_selective_online_upload_is_a_new_revision_and_retry_is_idempotent(pilot):
    import base64
    from azeo_control_trainer.core.configuration.uploads import UploadRepository
    releases, token, project, release, target, boot, job = target_job(pilot)
    obj = next(o for o in release["manifest"]["objects"] if o["path"] == "control/LOOP.json")
    file = next(f for f in releases.bundle(token, project, release["id"])["bundle"]["files"] if f["path"] == obj["path"])
    raw = json.loads(base64.b64decode(file["content"]))
    block = raw["blocks"][0]
    report = {"objects": [{"object_id": obj["id"], "active": True, "scan_count": 5,
                           "parameters": [{"block_id": block["id"], "block_type": "PID", "values": {"GAIN": 4.5, "RESET": 37.0}}]}]}
    releases.report(target["token"], target["id"], boot, report)
    uploads = UploadRepository(pilot[0])
    preview = uploads.preview(token, project, target["id"])
    gain = next(row for row in preview["rows"] if row["parameter"] == "GAIN")
    command = str(uuid4())
    receipt = uploads.commit(token, project, preview["id"], [gain["id"]], "Training tuning", command)
    releases.report(target["token"], target["id"], boot, report, online=False)
    assert uploads.commit(token, project, preview["id"], [gain["id"]], "Training tuning", command) == receipt
    current = pilot[0].export(token, project)
    updated = json.loads(base64.b64decode(next(f for f in current["files"] if f["path"] == obj["path"])["content"]))
    assert updated["blocks"][0]["config"]["GAIN"] == 4.5
    assert updated["blocks"][0]["config"].get("RESET") == block["config"].get("RESET")
    assert updated["comments"] == raw["comments"] and updated["unmodeled_contract"] == raw["unmodeled_contract"]
    assert release["manifest"]["source_digest"] != current["digest"]


def test_upload_ignores_implicit_defaults_and_equivalent_pid_enum_labels(pilot):
    import base64
    from azeo_control_trainer.core.configuration.uploads import UploadRepository
    releases, token, project, release, target, boot, job = target_job(pilot)
    obj = next(o for o in release["manifest"]["objects"] if o["path"] == "control/LOOP.json")
    file = next(f for f in releases.bundle(token, project, release["id"])["bundle"]["files"] if f["path"] == obj["path"])
    block = json.loads(base64.b64decode(file["content"]))["blocks"][0]
    report = {"objects": [{"object_id": obj["id"], "active": True,
                           "parameters": [{"block_id": block["id"], "block_type": "PID", "values": {
                               "GAIN": 4.5, "RESET": 60.0, "RATE": 0.0, "ff_enable": False,
                               "form": "Standard", "structure": "Two Degrees of Freedom"}}]}]}
    releases.report(target["token"], target["id"], boot, report)
    preview = UploadRepository(pilot[0]).preview(token, project, target["id"])
    assert [row["parameter"] for row in preview["rows"]] == ["GAIN"]


def test_upload_refuses_changed_observation_or_newer_engineering_revision(pilot):
    import base64
    from azeo_control_trainer.core.configuration.uploads import UploadRepository
    releases, token, project, release, target, boot, job = target_job(pilot)
    obj = next(o for o in release["manifest"]["objects"] if o["path"] == "control/LOOP.json")
    file = next(f for f in releases.bundle(token, project, release["id"])["bundle"]["files"] if f["path"] == obj["path"])
    block = json.loads(base64.b64decode(file["content"]))["blocks"][0]
    report = {"objects": [{"object_id": obj["id"], "active": True,
                           "parameters": [{"block_id": block["id"], "block_type": "PID", "values": {"GAIN": 4.5}}]}]}
    releases.report(target["token"], target["id"], boot, report)
    uploads = UploadRepository(pilot[0])
    preview = uploads.preview(token, project, target["id"])
    selected = [preview["rows"][0]["id"]]
    report["objects"][0]["parameters"][0]["values"]["GAIN"] = 9.0
    releases.report(target["token"], target["id"], boot, report)
    with pytest.raises(Conflict, match="changed after review"):
        uploads.commit(token, project, preview["id"], selected, "Stale", str(uuid4()))
    preview = uploads.preview(token, project, target["id"])
    commit(pilot[2], token, project, changed(pilot[0], token, project))
    with pytest.raises(Conflict):
        uploads.commit(token, project, preview["id"], [preview["rows"][0]["id"]], "Stale engineering", str(uuid4()))


def test_display_verifier_comes_from_the_product_catalog(monkeypatch, tmp_path):
    from azeo_control_trainer.core.configuration import release_validation
    from azeo_control_trainer.azeo_graphics_designer import release_check
    assert release_validation.display_verifier() is release_check.verify_displays
    monkeypatch.setattr(release_validation, "RELEASE_DISPLAY_VERIFIER",
                        "azeo_control_trainer.no_such_component")
    assert release_validation.display_verifier() is None
    project = tmp_path / "project"
    (project / "displays" / "pvm" / "Unit").mkdir(parents=True)
    (project / "displays/pvm/Unit/draft.json").write_text(
        json.dumps({"display": "Unit", "pvms": [], "items": []}), encoding="utf-8")
    findings = release_validation.verify_project(project, ["displays/pvm/Unit/draft.json"])
    assert [row["severity"] for row in findings] == ["ERROR"]
    assert "Graphics Designer" in findings[0]["message"]


def test_display_verification_runs_the_studio_verifier_in_process(tmp_path):
    from azeo_control_trainer.core.configuration.release_validation import verify_project
    from azeo_control_trainer.core.hmi.pvms.publishing import PvmDisplay
    project = tmp_path / "project"
    (project / "displays" / "pvm" / "Unit").mkdir(parents=True)
    document = PvmDisplay(name="Unit", work_in_progress=True, wip_reason="draft").to_dict()
    (project / "displays/pvm/Unit/draft.json").write_text(json.dumps(document), encoding="utf-8")
    findings = verify_project(project, ["displays/pvm/Unit/draft.json"])
    assert any(row["severity"] == "ERROR" and "Work In Progress" in row["message"] for row in findings)
    assert all({"path", "message", "severity"} <= set(row) for row in findings)
