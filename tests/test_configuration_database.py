"""Real PostgreSQL transactions, lossless import and authenticated snapshot workflows.

Set AZEO_CONFIGURATION_TEST_DSN_FILE to the isolated pilot's server.json. Tests
create and drop only uniquely named test databases on that explicitly selected server.
"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from azeo_control_trainer.core.configuration.documents import (
    ConfigurationError, Conflict, Forbidden, export_project, prepare_import, read_project, safe_path,
)
from azeo_control_trainer.core.configuration.repository import Repository


def encoded(path, document):
    content = document if isinstance(document, bytes) else json.dumps(document).encode()
    return {"path": path, "content": base64.b64encode(content).decode("ascii")}


@pytest.fixture
def files():
    from azeo_control_trainer.core.strategy.blocks.pid_block import PIDBlock
    from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
    graph = StrategyGraph("LOOP")
    graph.add_block(PIDBlock("PID1"))
    document = graph.to_dict()
    document["unmodeled_contract"] = {"original_id": "keep-this", "revision": 17}
    document["comments"] = [{"text": "Keep this annotation →", "x": 1}]
    return [encoded("_project.json", {"areas": []}), encoded("control/LOOP.json", document),
            encoded("assets/custom.bin", bytes(range(256)))]


@pytest.fixture
def database(request):
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    selected = os.environ.get("AZEO_CONFIGURATION_TEST_DSN_FILE")
    if not selected:
        pytest.skip("PostgreSQL integration requires an explicit isolated test DSN file")
    dsn = json.loads(Path(selected).read_text(encoding="utf-8"))["dsn"]
    name = "azeo_configuration_test_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    repository = Repository(make_conninfo(dsn, dbname=name))
    if getattr(request, "param", None) == 1:
        import hashlib
        from azeo_control_trainer.core.configuration import schema
        with repository.connection() as c:
            c.execute(schema.SQL)
            c.execute("INSERT INTO schema_version VALUES (1,%s)",
                      (hashlib.sha256(schema.SQL.encode()).hexdigest(),))
    else:
        repository.migrate()
    token = repository.provision_identity("engineer", administrator=True)
    try:
        yield repository, token
    finally:
        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def capture(repository, token, files, *, name="Pilot", expected=0, command=None):
    return repository.import_snapshot(token, name, prepare_import(files),
                                      expected_generation=expected, command_id=command or str(uuid4()))


def test_source_roundtrip_keeps_unknown_json_fields_and_binary_bytes(tmp_path, files):
    root = tmp_path / "source"
    root.mkdir()
    for item in files:
        path = root / item["path"]
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(base64.b64decode(item["content"]))
    (root / ".lock").write_text("runtime", encoding="utf-8")
    bundle = read_project(root)
    assert len(bundle["files"]) == 3
    assert bundle["excluded"] == [{"path": ".lock", "reason": "runtime state"}]
    prepared = prepare_import(bundle["files"])
    exported = export_project({**bundle, "digest": prepared.digest}, tmp_path / "export")
    for item in files:
        assert (exported / item["path"]).read_bytes() == (root / item["path"]).read_bytes()
    assert any(t["path"] == "LOOP/PID1/CONFIG/lo_lim" for t in prepared.tags)
    # PID schema defaults include unbounded limits; SQL JSONB must retain their
    # meaning without raising midway through an otherwise valid import.
    assert '"$number": "Infinity"' in json.dumps(prepared.tags, allow_nan=False)
    with pytest.raises(ConfigurationError, match="new destination"):
        export_project({**bundle, "digest": prepared.digest}, exported)


@pytest.mark.parametrize("path", ["../escape", "/absolute", "C:/escape", "a\\b", "a//b",
                                  "a/./b", "AUX.json", "a/CON.txt", "module. /a"])
def test_snapshot_rejects_paths_that_escape_or_alias_on_windows(path):
    with pytest.raises(ConfigurationError):
        safe_path(path)


def test_unknown_block_is_preserved_by_legacy_loader_but_refused_by_indexer(files):
    raw = json.loads(base64.b64decode(files[1]["content"]))
    raw["blocks"][0]["block_type"] = "UNINSTALLED_BLOCK"
    files[1] = encoded("control/LOOP.json", raw)
    with pytest.raises(ConfigurationError, match="Unknown block type"):
        prepare_import(files)


def test_bad_manifest_never_creates_an_export_directory(tmp_path, files):
    target = tmp_path / "export"
    with pytest.raises(ConfigurationError, match="manifest"):
        export_project({"files": files, "digest": "bad"}, target)
    assert not target.exists()


@pytest.mark.parametrize("document", [b'{"a":1,"a":2}', b'{"a":1e999}', b'{"a":"\\u0000"}'])
def test_ambiguous_or_nonfinite_json_is_refused_during_preview(files, document):
    with pytest.raises(ConfigurationError, match="Invalid JSON"):
        prepare_import(files + [encoded("metadata.json", document)])


def test_literal_unicode_escape_text_is_not_confused_with_a_nul(files):
    document = {"text": r"A literal \u0000 in a tutorial"}
    prepared = prepare_import(files + [encoded("metadata.json", document)])
    assert prepared.documents[-1].payload == document


def test_reimport_is_idempotent_and_revisions_survive_reconnect(database, files):
    repository, token = database
    command = str(uuid4())
    first = capture(repository, token, files, command=command)
    assert capture(repository, token, files, command=command) == first
    second = capture(repository, token, files, expected=1)
    assert second["generation"] == 1 and second["changed"] == 0
    fresh = Repository(repository._dsn)
    assert fresh.projects(token)[0]["generation"] == 1
    obj = next(r for r in fresh.objects(token, first["project_id"]) if r["kind"] == "module")
    assert len(fresh.revisions(token, first["project_id"], str(obj["id"]))) == 1
    assert len(fresh.audit(token, first["project_id"])) == 2
    bundle = fresh.export(token, first["project_id"])
    assert sorted(bundle["files"], key=lambda f: f["path"]) == sorted(files, key=lambda f: f["path"])


def test_stale_import_and_reused_command_cannot_overwrite(database, files):
    repository, token = database
    command = str(uuid4())
    first = capture(repository, token, files, command=command)
    files[-1] = encoded("assets/custom.bin", b"changed")
    with pytest.raises(Conflict, match="snapshot 1"):
        capture(repository, token, files)
    with pytest.raises(Conflict, match="different request"):
        capture(repository, token, files, expected=1, command=command)
    assert repository.projects(token)[0]["digest"] == first["digest"]
    assert len(repository.audit(token, first["project_id"])) == 1


def test_two_engineers_cannot_both_commit_the_same_base(database, files):
    repository, token = database
    first = capture(repository, token, files)
    editor = repository.provision_identity("second")
    repository.grant("second", first["project_id"], "importer")

    def edit(credential, value):
        changed = files[:2] + [encoded("assets/custom.bin", value)]
        try:
            return capture(repository, credential, changed, expected=1)
        except Conflict:
            return "conflict"

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(edit, token, b"a"), pool.submit(edit, editor, b"b")]
        results = [future.result() for future in futures]
    assert results.count("conflict") == 1
    assert repository.projects(token)[0]["generation"] == 2


def test_database_failure_rolls_back_objects_indexes_and_audit(database, files):
    import psycopg
    repository, token = database
    first = capture(repository, token, files)
    prepared = prepare_import(files[:2] + [encoded("assets/custom.bin", b"changed")])
    # Inject an impossible projection after normal validation, so failure occurs
    # inside COPY after revisions and audit have already been inserted.
    prepared.tags.append(dict(prepared.tags[0]))
    with pytest.raises(psycopg.errors.UniqueViolation):
        repository.import_snapshot(token, "Pilot", prepared, expected_generation=1,
                                   command_id=str(uuid4()))
    assert repository.projects(token)[0]["digest"] == first["digest"]
    assert len(repository.audit(token, first["project_id"])) == 1
    assert repository.export(token, first["project_id"])["digest"] == first["digest"]


def test_deleted_object_retains_history_and_identity_when_restored(database, files):
    repository, token = database
    first = capture(repository, token, files)
    obj = next(r for r in repository.objects(token, first["project_id"]) if r["kind"] == "asset")
    capture(repository, token, files[:2], expected=1)
    assert len(repository.objects(token, first["project_id"])) == 2
    assert repository.document(token, first["project_id"], str(obj["id"]))["deleted"]
    capture(repository, token, files, expected=2)
    restored = repository.document(token, first["project_id"], str(obj["id"]))
    assert restored["number"] == 3 and not restored["deleted"]
    assert repository.document(token, first["project_id"], str(obj["id"]), 1)["content"] == files[-1]["content"]


def test_api_authentication_project_scope_and_revocation(database, files):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from azeo_control_trainer.services.configuration.api import create_app
    repository, token = database
    first = capture(repository, token, files)
    other = capture(repository, token, files, name="Other")
    reader = repository.provision_identity("observer")
    repository.grant("observer", first["project_id"], "reader")
    client = TestClient(create_app(repository))
    headers = {"Authorization": f"Bearer {reader}"}
    assert client.get("/v1/projects").status_code == 403
    assert len(client.get("/v1/projects", headers=headers).json()) == 1
    assert client.get(f"/v1/projects/{other['project_id']}/export", headers=headers).status_code == 403
    payload = {"name": "Pilot", "files": files, "expected_generation": 1, "command_id": str(uuid4())}
    assert client.post("/v1/imports", json=payload, headers=headers).status_code == 403
    assert client.get(f"/v1/projects/{first['project_id']}/tags?limit=501", headers=headers).status_code == 422
    repository.revoke("observer")
    assert client.get("/v1/projects", headers=headers).status_code == 403
    with pytest.raises(Forbidden):
        repository.export(reader, first["project_id"])


def test_migration_detects_drift_and_database_enforces_immutable_evidence(database, files):
    import psycopg
    repository, token = database
    capture(repository, token, files)
    repository.migrate()
    with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
        with repository.connection() as c:
            c.execute("DELETE FROM revisions")
    with repository.connection() as c:
        c.execute("UPDATE schema_version SET checksum='tampered'")
    with pytest.raises(ConfigurationError, match="modified"):
        repository.migrate()


def test_ui_entry_point_and_background_request_lifetime(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from azeo_control_trainer.azeo_explorer.project_admin import ProjectAdministrator
    from azeo_control_trainer.azeo_explorer.project_administrator import ProjectAdministratorDialog
    from azeo_control_trainer.azeo_explorer.configuration_database import ConfigurationClient
    import threading
    app = QApplication.instance() or QApplication([])
    observed = []
    main_thread = threading.get_ident()

    def delayed(_self, _path, _payload=None):
        import time
        observed.append(threading.get_ident())
        time.sleep(0.1)
        if _path == "/v1/status":
            return {"identity": {"name": "Test engineer", "administrator": True}}
        return []

    monkeypatch.setattr(ConfigurationClient, "request", delayed)
    administrator = ProjectAdministratorDialog(ProjectAdministrator(tmp_path), tmp_path)
    window = administrator.open_configuration_database()
    assert window.isVisible()
    browser = window.workspace.open_page("capture")
    assert not browser.isWindow()
    timer_ticks = []
    timer = QTimer(browser)
    timer.timeout.connect(lambda: timer_ticks.append(True))
    timer.start(10)
    browser.connect_service()
    window.close()
    assert window.isVisible(), "Closing cannot destroy a running Qt worker"
    for _ in range(200):
        app.processEvents()
        if browser._request is None:
            break
        QTest.qWait(10)
    assert browser._request is None and timer_ticks
    assert observed and all(t != main_thread for t in observed)
    window.close()
    administrator.close()
    from azeo_control_trainer.core.presentation.configuration_catalog import _shutdown
    _shutdown()
    app.processEvents()


def test_native_table_population_batches_geometry_work():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import time
    from PySide6.QtWidgets import QApplication, QTableWidget
    from azeo_control_trainer.azeo_explorer.configuration_database import ConfigurationDatabaseDialog
    app = QApplication.instance() or QApplication([])
    table = QTableWidget()
    table.resize(1100, 500)
    table.show()
    rows = [([f"LOOP/PID1/CONFIG/PARAMETER_{n}", "parameter", "FLOAT", "%", "Configured value"],
             {"path": str(n)}) for n in range(200)]
    samples = []
    for _ in range(3):
        started = time.perf_counter()
        ConfigurationDatabaseDialog._fill(table, ["Tag", "Kind", "Type", "Unit", "Description"], rows)
        app.processEvents()
        samples.append(time.perf_counter() - started)
    assert min(samples) < 0.75, samples
    assert table.rowCount() == 200
    table.close()


def test_pooled_connections_rollback_before_returning_to_another_request(database, files):
    pytest.importorskip("psycopg_pool")
    repository, token = database
    repository.open_pool()
    try:
        first = capture(repository, token, files)
        with pytest.raises(RuntimeError):
            with repository.connection() as connection:
                connection.execute("UPDATE projects SET generation=99 WHERE id=%s",
                                   (first["project_id"],))
                raise RuntimeError("Interrupted command")
        assert repository.projects(token)[0]["generation"] == 1
    finally:
        repository.close_pool()


@pytest.mark.parametrize("database", [1], indirect=True)
def test_schema_upgrade_preserves_committed_bytes_and_rebuilds_search_projection(database, files):
    repository, token = database
    repository.migrate()
    captured = capture(repository, token, files)
    before = repository.export(token, captured["project_id"])
    # Reconstruct the original schema around real committed evidence. Current
    # application writes intentionally require the current schema version.
    with repository.connection() as c:
        c.execute("DROP TABLE training_baselines, library_previews, deployment_events, deployment_jobs, upload_previews, runtime_targets, releases, release_previews")
        c.execute("DROP TABLE edit_leases")
        c.execute("ALTER TABLE projects DROP COLUMN origin")
        c.execute("ALTER TABLE projects DROP CONSTRAINT projects_mode_check")
        c.execute("ALTER TABLE projects ADD CONSTRAINT projects_mode_check CHECK(mode='file_shadow')")
        c.execute("ALTER TABLE grants DROP CONSTRAINT grants_role_check")
        c.execute("ALTER TABLE grants ADD CONSTRAINT grants_role_check CHECK(role IN ('reader','importer'))")
        c.execute("DROP TABLE catalogs")
        c.execute("ALTER TABLE tags DROP COLUMN search_text")
        c.execute("DELETE FROM schema_version WHERE version > 1")
    repository.migrate()
    assert repository.export(token, captured["project_id"]) == before
    assert repository.tags(token, captured["project_id"], query="LOOP/PID1/CONFIG/lo_lim")
    rebuilt = capture(repository, token, files, expected=1)
    assert rebuilt["generation"] == 1 and rebuilt["reindexed"]
    assert repository.catalog(token, captured["project_id"])["catalog"]["version"] == 1
    assert repository.export(token, captured["project_id"]) == before
