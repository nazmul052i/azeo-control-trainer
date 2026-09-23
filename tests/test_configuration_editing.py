"""Real concurrent check-in, lease recovery, rename and runtime isolation contracts."""
from concurrent.futures import ThreadPoolExecutor
import base64
import json
from uuid import uuid4

import pytest

from test_configuration_database import database as database_fixture, files as files_fixture, encoded, capture
from azeo_control_trainer.core.configuration.documents import ConfigurationError, Conflict, Forbidden
from azeo_control_trainer.core.configuration.editing import EditingRepository

database = database_fixture
files = files_fixture


@pytest.fixture
def pilot(database, files):
    repo, token = database
    second = json.loads(base64.b64decode(files[1]["content"]))
    second["name"] = "OTHER"
    files.append(encoded("control/OTHER.json", second))
    files[0] = encoded("_project.json", {"areas": [{"strategies": ["control/LOOP.json", "control/OTHER.json"]}]})
    source = capture(repo, token, files)["project_id"]
    editing = EditingRepository(repo)
    project = editing.fork(token, source, "Editing pilot", str(uuid4()))["project_id"]
    return repo, token, editing, source, project


def changed(repo, token, project, path="control/LOOP.json", value="Changed"):
    bundle = repo.export(token, project)
    item = next(f for f in bundle["files"] if f["path"] == path)
    raw = json.loads(base64.b64decode(item["content"]))
    raw["description"] = value
    revision = next(o["revision"] for o in bundle["objects"] if o["path"] == path)
    return {**encoded(path, raw), "expected_revision": revision}


def commit(editing, token, project, edit, session=None, command=None):
    session = session or str(uuid4())
    editing.lease(token, project, session, [edit["path"]])
    result = editing.checkin(token, project, session, [edit], "Verified change", command or str(uuid4()))
    editing.lease(token, project, session, [], release=True)
    return result


def test_fork_is_isolated_idempotent_and_refuses_legacy_import(pilot, files):
    repo, token, editing, source, project = pilot
    before = repo.export(token, source)
    command = str(uuid4())
    first = editing.fork(token, source, "Another pilot", command)
    assert editing.fork(token, source, "Another pilot", command) == first
    commit(editing, token, project, changed(repo, token, project))
    assert repo.export(token, source) == before
    with pytest.raises(Conflict, match="check-in"):
        capture(repo, token, files, name="Editing pilot", expected=2)


def test_separate_module_checkins_do_not_lose_each_other(pilot):
    repo, token, editing, _, project = pilot
    a = changed(repo, token, project, value="Engineer A")
    b = changed(repo, token, project, "control/OTHER.json", "Engineer B")
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda e: commit(editing, token, project, e), [a, b]))
    assert sorted(r["generation"] for r in results) == [2, 3]
    bundle = repo.export(token, project)
    content = {f["path"]: json.loads(base64.b64decode(f["content"])) for f in bundle["files"] if f["path"].startswith("control/")}
    assert content[a["path"]]["description"] == "Engineer A"
    assert content[b["path"]]["description"] == "Engineer B"
    assert content[a["path"]]["unmodeled_contract"]["original_id"] == "keep-this"


def test_brief_project_lock_contention_retries_without_duplicate_commit(pilot, monkeypatch):
    from contextlib import contextmanager
    from threading import Event
    from psycopg.errors import LockNotAvailable
    repo, token, editing, _, project = pilot
    edit = changed(repo, token, project)
    session, command = str(uuid4()), str(uuid4())
    editing.lease(token, project, session, [edit["path"]])
    connection = repo.connection
    blocked = Event()
    @contextmanager
    def quick_timeout(**kwargs):
        try:
            with connection(**kwargs) as c:
                c.execute("SET LOCAL lock_timeout='25ms'")
                yield c
        except LockNotAvailable:
            blocked.set()
            raise
    monkeypatch.setattr(repo, "connection", quick_timeout)
    with ThreadPoolExecutor(1) as pool:
        with connection() as c:
            c.execute("SELECT id FROM projects WHERE id=%s FOR UPDATE", (project,))
            future = pool.submit(editing.checkin, token, project, session, [edit], "Contended change", command)
            assert blocked.wait(10), "The concurrent command must reach the held project lock"
        receipt = future.result(timeout=10)
    assert receipt["generation"] == 2
    assert len([r for r in repo.audit(token, project) if str(r["command_id"]) == command]) == 1


def test_checkin_keeps_unchanged_tag_rows_and_updates_changed_projection(pilot):
    repo, token, editing, _, project = pilot
    def tags():
        with repo.connection() as c:
            return {r["path"]: r for r in c.execute(
                "SELECT path,xmin::text AS transaction,data FROM tags WHERE project_id=%s", (project,)).fetchall()}
    before = tags()
    commit(editing, token, project, changed(repo, token, project, value="Updated module description"))
    after = tags()
    unchanged = [p for p in before if before[p]["data"] == after[p]["data"]]
    assert unchanged
    assert all(before[p]["transaction"] == after[p]["transaction"] for p in unchanged)
    assert any(before[p]["data"] != after[p]["data"] for p in before)


def test_expired_owner_cannot_overwrite_new_revision(pilot):
    repo, token, editing, _, project = pilot
    a, b = str(uuid4()), str(uuid4())
    stale = changed(repo, token, project, value="Old draft")
    editing.lease(token, project, a, [stale["path"]])
    with pytest.raises(Conflict, match="being edited"):
        editing.lease(token, project, b, [stale["path"]])
    with repo.connection() as c:
        c.execute("UPDATE edit_leases SET expires_at=clock_timestamp()-interval '1 second'")
    with pytest.raises(Conflict, match="expired"):
        editing.checkin(token, project, a, [stale], "Old work", str(uuid4()))
    commit(editing, token, project, changed(repo, token, project, value="New revision"), session=b)
    editing.lease(token, project, a, [stale["path"]])
    with pytest.raises(Conflict, match="newer revision"):
        editing.checkin(token, project, a, [stale], "Old work", str(uuid4()))


def test_checkin_retry_after_release_returns_original_receipt(pilot):
    repo, token, editing, _, project = pilot
    session, command = str(uuid4()), str(uuid4())
    edit = changed(repo, token, project)
    result = commit(editing, token, project, edit, session, command)
    assert editing.checkin(token, project, session, [edit], "Verified change", command) == result
    assert repo.projects(token)[1]["generation"] in {1, 2}
    with pytest.raises(Conflict, match="different request"):
        editing.checkin(token, project, session, [edit], "Another reason", command)


def test_reader_and_importer_cannot_reserve_or_checkin(pilot):
    repo, token, editing, _, project = pilot
    edit = changed(repo, token, project)
    for role in ("reader", "importer"):
        credential = repo.provision_identity(role)
        repo.grant(role, project, role)
        assert editing.state(credential, project)["objects"]
        with pytest.raises(Forbidden):
            editing.lease(credential, project, str(uuid4()), [edit["path"]])
        with pytest.raises(Forbidden):
            editing.checkin(credential, project, str(uuid4()), [edit], "Invalid permission", str(uuid4()))
    credential = repo.provision_identity("Writer")
    repo.grant("Writer", project, "engineer")
    commit(editing, credential, project, edit)
    assert repo.audit(token, project)[-1]["actor"] == "Writer"


def test_failed_checkin_has_no_partial_files_tags_or_audit(pilot, monkeypatch):
    repo, token, editing, _, project = pilot
    before = repo.export(token, project)
    audit = repo.audit(token, project)
    original = repo._commit
    def failed(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("Injected interruption after revision writes")
    monkeypatch.setattr(repo, "_commit", failed)
    with pytest.raises(RuntimeError):
        commit(editing, token, project, changed(repo, token, project))
    assert repo.export(token, project) == before
    assert repo.audit(token, project) == audit


def test_multi_path_lease_acquisition_is_all_or_nothing(pilot):
    repo, token, editing, _, project = pilot
    a, b = str(uuid4()), str(uuid4())
    editing.lease(token, project, a, ["control/OTHER.json"])
    with pytest.raises(Conflict):
        editing.lease(token, project, b, ["control/LOOP.json", "control/OTHER.json"])
    assert len(editing.state(token, project)["leases"]) == 1


def test_rename_preserves_identity_and_updates_known_consumers(pilot):
    repo, token, editing, _, project = pilot
    doc = {"display": "Test", "pvms": [{"id": "loop", "class": "PID/dynamo_inline", "params": {"path": "LOOP/PID1"}}],
           "scripts": {"click": "read('LOOP/PID1/PV')"}}
    commit(editing, token, project, {**encoded("displays/pvm/Test/draft.json", doc), "expected_revision": 0})
    before = repo.export(token, project)
    plan = editing.rename_preview(token, project, "LOOP", "RENAMED")
    assert any("scripts" in u["location"] for u in plan["unknown"])
    paths = {e[k] for e in plan["edits"] for k in ("path", "new_path")}
    session = str(uuid4())
    editing.lease(token, project, session, list(paths))
    editing.checkin(token, project, session, plan["edits"], "Rename reviewed", str(uuid4()), expected_generation=plan["generation"])
    after = repo.export(token, project)
    old = next(o for o in before["objects"] if o["path"] == "control/LOOP.json")
    new = next(o for o in after["objects"] if o["path"] == "control/RENAMED.json")
    assert old["id"] == new["id"] and new["revision"] == old["revision"] + 1
    content = next(f for f in after["files"] if f["path"] == "displays/pvm/Test/draft.json")
    raw = json.loads(base64.b64decode(content["content"]))
    assert raw["pvms"][0]["params"]["path"] == "RENAMED/PID1"
    assert raw["scripts"] == doc["scripts"]
    catalog = repo.catalog(token, project)
    assert any(t["path"] == "RENAMED/PID1/CONFIG/lo_lim" for t in catalog["tags"])
    assert not any(t["path"].startswith("LOOP/") for t in catalog["tags"])


def test_rename_refuses_consumers_added_after_impact_review(pilot):
    repo, token, editing, _, project = pilot
    plan = editing.rename_preview(token, project, "LOOP", "RENAMED")
    commit(editing, token, project, changed(repo, token, project, "control/OTHER.json"))
    with pytest.raises(Conflict, match="impact review"):
        editing.checkin(token, project, str(uuid4()), plan["edits"], "Stale rename", str(uuid4()),
                         expected_generation=plan["generation"])


def test_invalid_bulk_member_rolls_back_every_change(pilot):
    repo, token, editing, _, project = pilot
    edits = [changed(repo, token, project), changed(repo, token, project, "control/OTHER.json")]
    edits[1]["content"] = base64.b64encode(b"invalid json").decode()
    before = repo.export(token, project)
    with pytest.raises(ConfigurationError, match="Invalid JSON"):
        editing.checkin(token, project, str(uuid4()), edits, "Invalid bulk", str(uuid4()))
    assert repo.export(token, project) == before


def test_api_exposes_authenticated_editing_workflow(pilot):
    from fastapi.testclient import TestClient
    from azeo_control_trainer.services.configuration.api import create_app
    repo, token, _, _, project = pilot
    with TestClient(create_app(repo)) as api:
        base = f"/v1/projects/{project}/editing"
        assert api.get(base + "/state").status_code == 403
        api.headers["Authorization"] = "Bearer " + token
        session = str(uuid4())
        edit = changed(repo, token, project)
        assert api.post(base + "/lease", json={"session": session, "paths": [edit["path"]]}).status_code == 200
        assert api.post(base + "/preview", json={"edits": [edit]}).status_code == 200
        response = api.post(base + "/checkin", json={"session": session, "edits": [edit],
                            "reason": "API workflow", "command_id": str(uuid4())})
        assert response.status_code == 200, response.text
        assert response.json()["generation"] == 2
