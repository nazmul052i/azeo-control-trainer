"""Real pg_dump/pg_restore and committed SQLite evidence, never active-file copying."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest

from test_configuration_editing import database as database_fixture, files as files_fixture, pilot as pilot_fixture, changed, commit
from test_configuration_training import baseline
from azeo_control_trainer.core.configuration.documents import ConfigurationError, Conflict, Forbidden
from azeo_control_trainer.core.configuration.recovery import RecoveryRepository, sqlite_capture
from azeo_control_trainer.core.hmi.history.archive import HistoryArchive
from azeo_control_trainer.core.simulation.training import SessionArchive

database, files, pilot = database_fixture, files_fixture, pilot_fixture


def recovery(pilot, tmp_path):
    config = json.loads(Path(os.environ["AZEO_CONFIGURATION_TEST_DSN_FILE"]).read_text())
    return RecoveryRepository(pilot[0], tmp_path / "recovery", config.get("pg_bin", "C:/Program Files/PostgreSQL/16/bin"))


def cleanup(pilot, database):
    from psycopg import connect, sql
    assert database.startswith("azeo_rehearsal_") and len(database) == len("azeo_rehearsal_") + 32
    with connect(pilot[0]._dsn, autocommit=True) as c:
        c.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))


def test_database_and_wal_evidence_restore_exactly_after_source_changes(pilot, tmp_path):
    repo, token, editing, _, project = pilot
    _, retained = baseline(pilot)
    manager = recovery(pilot, tmp_path)
    archive = HistoryArchive(tmp_path / "history.sqlite3")
    training = SessionArchive(tmp_path / "sessions.sqlite")
    session = str(uuid4())
    context = {"project_id": project, "release_id": retained["release_id"]}
    training.save(session, {"started": archive.origin, "exercise": {"name": "Original session"}, "configuration": {"LOOP": context}})
    training.append(session, 1, {"LOOP/PID1/PV": {"value": 12, "configuration": context}}, [])
    restored_name = None
    try:
        archive.append({"OLD/PV": {"label": "Legacy address"}}, [("OLD/PV", 1, 12, "GOOD", archive.origin + 1, 1, session)], []).result()
        evidence = []
        for source, kind in ((archive.path, "history"), (training.path, "training")):
            identity = str(uuid4())
            sqlite_capture(source, manager.evidence_path(token, identity))
            evidence.append(manager.accept_evidence(token, identity, kind)["id"])
        archive.append({}, [("OLD/PV", 2, 99, "GOOD", archive.origin + 2, 2, session)], []).result()
        command = str(uuid4())
        captured = manager.create(token, "Recovery test", evidence, command)
        assert manager.create(token, "Recovery test", evidence, command) == captured
        commit(editing, token, project, changed(repo, token, project))
        restore_command = str(uuid4())
        restored = manager.rehearse(token, captured["id"], restore_command)
        restored_name = restored["database"]
        assert restored["state"] == "verified" and not restored["runtime_started"]
        assert manager.rehearse(token, captured["id"], restore_command) == restored
        (manager.root / "restores" / restore_command / "receipt.json").unlink()
        resumed_restore = manager.rehearse(token, captured["id"], restore_command)
        assert resumed_restore["database"] == restored_name
        assert restored["database_inventory"] == captured["database"]
        copy = manager.root / "restores" / restored["id"]
        history_file = next(e["file"] for e in restored["evidence"] if e["kind"] == "history")
        resumed = HistoryArchive(copy / history_file)
        try:
            assert [row[1] for row in resumed.read(["OLD/PV"], 0, 3)["samples"]["OLD/PV"]] == [12]
        finally:
            resumed.close()
        training_file = next(e["file"] for e in restored["evidence"] if e["kind"] == "training")
        assert SessionArchive(copy / training_file).read(session)["samples"][0]["values"]["LOOP/PID1/PV"]["value"] == 12
        captured_project = next(p for p in captured["database"]["projects"] if p["id"] == project)
        assert repo.export(token, project)["digest"] != captured_project["digest"]
        exported = manager.export(token, captured["id"])
        imported = RecoveryRepository(repo, tmp_path / "separate-recovery", manager.pg_bin)
        assert imported.import_backup(token, exported)["hash"] == captured["hash"]
    finally:
        archive.close()
        if restored_name:
            cleanup(pilot, restored_name)


def test_missing_release_and_tampered_backup_are_refused_before_restore(pilot, tmp_path):
    manager = recovery(pilot, tmp_path)
    token = pilot[1]
    archive = SessionArchive(tmp_path / "foreign.sqlite")
    archive.save("session", {"started": 1, "exercise": {"name": "Foreign"},
                             "configuration": {"project_id": str(uuid4()), "release_id": str(uuid4())}})
    identity = str(uuid4())
    sqlite_capture(archive.path, manager.evidence_path(token, identity))
    manager.accept_evidence(token, identity, "training")
    with pytest.raises(Conflict, match="absent"):
        manager.create(token, "Foreign evidence", [identity], str(uuid4()))
    captured = manager.create(token, "Configuration only", [], str(uuid4()))
    dump = manager.root / "backups" / captured["id"] / "configuration.dump"
    with dump.open("ab") as output:
        output.write(b"damaged")
    with pytest.raises(ConfigurationError, match="checksum"):
        manager.rehearse(token, captured["id"], str(uuid4()))
    assert manager.state(token)["restores"] == []
    denied = pilot[0].provision_identity("reader")
    with pytest.raises(Forbidden):
        manager.state(denied)


def test_sqlite_backup_captures_wal_without_copying_uncommitted_rows(tmp_path):
    source, target = tmp_path / "wal.sqlite3", tmp_path / "snapshot.sqlite3"
    with closing(sqlite3.connect(source)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE evidence(value)")
        db.execute("INSERT INTO evidence VALUES (1)")
        db.commit()
        db.execute("INSERT INTO evidence VALUES (2)")
        sqlite_capture(source, target)
        with closing(sqlite3.connect(target)) as snapshot:
            assert snapshot.execute("SELECT value FROM evidence").fetchall() == [(1,)]
        db.rollback()


def test_streaming_evidence_endpoint_checks_authentication_and_archive_type(pilot, tmp_path):
    from fastapi.testclient import TestClient
    from azeo_control_trainer.services.configuration.api import create_app
    manager = recovery(pilot, tmp_path)
    archive = SessionArchive(tmp_path / "sessions.sqlite")
    payload = archive.path.read_bytes()
    with TestClient(create_app(pilot[0], recovery=manager)) as client:
        path = f"/v1/recovery/evidence/{uuid4()}?kind=training"
        assert client.put(path, content=payload).status_code == 403
        headers = {"Authorization": "Bearer " + pilot[1]}
        result = client.put(path, content=payload, headers=headers)
        assert result.status_code == 200, result.json()
        assert result.json()["kind"] == "training"
        assert client.put(path, content=payload, headers=headers).status_code == 409
        invalid = client.put(f"/v1/recovery/evidence/{uuid4()}?kind=history", content=b"not a database", headers=headers)
        assert invalid.status_code == 422


def test_interrupted_backup_download_does_not_publish_partial_or_replace_existing_backup(tmp_path):
    from io import BytesIO
    from types import SimpleNamespace
    from azeo_control_trainer.core.configuration.client import ConfigurationClient
    client = ConfigurationClient("http://127.0.0.1:8766", "test")
    target = tmp_path / "backup.zip"
    class Interrupted(BytesIO):
        def read(self, size=-1):
            if self.tell():
                raise OSError("connection interrupted")
            return super().read(size)
    client._opener = SimpleNamespace(open=lambda *_args, **_kw: Interrupted(b"partial"))
    with pytest.raises(OSError, match="interrupted"):
        client.download_backup(str(uuid4()), target)
    assert not target.exists() and not list(tmp_path.glob("*.partial"))
    client._opener = SimpleNamespace(open=lambda *_args, **_kw: BytesIO(b"complete"))
    assert client.download_backup(str(uuid4()), target).read_bytes() == b"complete"
    client._opener = SimpleNamespace(open=lambda *_args, **_kw: BytesIO(b"replacement"))
    with pytest.raises(FileExistsError):
        client.download_backup(str(uuid4()), target)
    assert target.read_bytes() == b"complete" and not list(tmp_path.glob("*.partial"))
