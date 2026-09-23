"""Consistent PostgreSQL and SQLite capture, verified in a new recovery database."""
from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import threading
from uuid import uuid4
import zipfile

from .documents import ConfigurationError, Conflict, Forbidden, Missing
from .release_manifest import fingerprint, plain
from .repository import identifier, project_name, Repository
from ..strategy.serialization.strategy_io import write_json_transactional

MAX_EVIDENCE = 2 * 1024 * 1024 * 1024


def now():
    return datetime.now(timezone.utc).isoformat()


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def sqlite_capture(source, destination):
    """SQLite's online backup API includes committed WAL pages; file copying does not."""
    source, destination = Path(source).resolve(), Path(destination)
    if not source.is_file() or destination.exists():
        raise ConfigurationError("Choose an existing archive and a new snapshot destination")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=10)) as reader:
        with closing(sqlite3.connect(destination)) as writer:
            reader.backup(writer, pages=256, sleep=.05)
            if writer.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ConfigurationError("The SQLite snapshot failed its integrity check")
    return destination


def evidence_inventory(path, kind):
    try:
        return _evidence_inventory(path, kind)
    except (sqlite3.Error, ValueError, TypeError, KeyError) as error:
        raise ConfigurationError("Evidence archive cannot be verified") from error


def _evidence_inventory(path, kind):
    if kind not in {"history", "training", "journal"}:
        raise ConfigurationError("Evidence must be a historian, training-session or operator-journal archive")
    required = {"history": {"history_samples", "history_events", "history_points"},
                "training": {"training_sessions", "training_samples", "training_events"},
                "journal": {"changes", "settings"}}[kind]
    refs, baselines = set(), set()
    def inspect(value):
        if isinstance(value, dict):
            if value.get("release_id") and value.get("project_id"):
                refs.add((identifier(value["project_id"]), identifier(value["release_id"])))
            if value.get("source_release_id") and value.get("source_project_id"):
                refs.add((identifier(value["source_project_id"]), identifier(value["source_release_id"])))
            if value.get("baseline_id"):
                baselines.add(identifier(value["baseline_id"]))
            for item in value.values():
                inspect(item)
        elif isinstance(value, list):
            for item in value:
                inspect(item)
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA trusted_schema=OFF")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not required.issubset(tables) or db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ConfigurationError("Archive schema or integrity does not match its selected evidence type")
        counts = {table: db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] for table in sorted(required)}
        if kind == "history" and "history_sample_context" in tables:
            for row in db.execute("SELECT DISTINCT configuration FROM history_sample_context"):
                inspect(json.loads(row[0]))
        if kind == "history":
            for row in db.execute("SELECT detail FROM history_events"):
                try:
                    detail = json.loads(row[0])
                except (ValueError, TypeError):
                    continue  # Legacy event detail was allowed to be plain text.
                inspect(detail)
        if kind == "training":
            for table, column in (("training_sessions", "metadata"), ("training_samples", "data"), ("training_events", "data")):
                for row in db.execute(f'SELECT "{column}" FROM "{table}"'):
                    inspect(json.loads(row[0]))
    return {"kind": kind, "rows": counts, "releases": [list(row) for row in sorted(refs)],
            "baselines": sorted(baselines), "hash": file_hash(path), "bytes": Path(path).stat().st_size}


def database_inventory(c):
    """Digest canonical rows, including unknown document fields and binary assets."""
    from psycopg import sql
    tables = c.execute("SELECT tablename FROM pg_tables WHERE schemaname='azeo_configuration' ORDER BY tablename").fetchall()
    inventory = {}
    for row in tables:
        name = row["tablename"]
        # Row fingerprints are sorted, independent of physical storage order.
        hashes = []
        for record in c.execute(sql.SQL("SELECT row_to_json(t) AS data FROM azeo_configuration.{} t").format(sql.Identifier(name))):
            hashes.append(fingerprint(record["data"]))
        inventory[name] = {"rows": len(hashes), "hash": fingerprint(sorted(hashes))}
    projects = plain(c.execute("SELECT id,name,generation,digest,mode FROM projects ORDER BY id").fetchall())
    return {"tables": inventory, "projects": projects}


class RecoveryRepository:
    def __init__(self, repository, root, pg_bin):
        self.repo, self.root, self.pg_bin = repository, Path(root).resolve(), Path(pg_bin)
        self.root.mkdir(parents=True, exist_ok=True)
        self._mutex = threading.Lock()

    def authorize(self, token):
        actor = self.repo.authenticate(token)
        if not actor["administrator"]:
            raise Forbidden("Database recovery requires a service administrator")
        return actor

    @contextmanager
    def operation(self):
        if not self._mutex.acquire(blocking=False):
            raise Conflict("A database recovery operation is already running")
        try:
            with (self.root / "operation.lock").open("a+b") as lock:
                lock.seek(0)
                lock.write(b"0")
                lock.flush()
                lock.seek(0)
                if os.name == "nt":
                    import msvcrt
                    try:
                        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    except OSError as error:
                        raise Conflict("Another service is using this recovery directory") from error
                else:
                    import fcntl
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                yield
        finally:
            self._mutex.release()

    def _postgres(self, program, arguments, *, dsn=None):
        from psycopg.conninfo import conninfo_to_dict
        config = conninfo_to_dict(dsn or self.repo._dsn)
        env = os.environ.copy()
        for key in ("host", "port", "user", "password", "dbname", "sslmode", "sslrootcert"):
            variable = "PGDATABASE" if key == "dbname" else "PG" + key.upper()
            if key in config:
                env[variable] = config[key]
            else:
                env.pop(variable, None)
        executable = self.pg_bin / (program + (".exe" if os.name == "nt" else ""))
        if not executable.is_file():
            raise ConfigurationError("Configure the PostgreSQL backup utilities on the service host")
        result = subprocess.run([str(executable), *map(str, arguments)], env=env, capture_output=True,
                                timeout=600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            # Utility stderr can contain connection credentials or captured SQL.
            raise ConfigurationError(program + " failed; the recovery artifact was not accepted")

    def evidence_path(self, token, evidence):
        self.authorize(token)
        directory = self.root / "evidence" / identifier(evidence)
        directory.mkdir(parents=True, exist_ok=True)
        return directory / "archive.sqlite3"

    def accept_evidence(self, token, evidence, kind):
        actor = self.authorize(token)
        path = self.evidence_path(token, evidence)
        if kind == "backup":
            return self.import_backup(token, path)
        document = {"id": evidence, "actor": actor["name"], "captured": now(), **evidence_inventory(path, kind)}
        write_json_transactional(path.parent / "manifest.json", document)
        return document

    def _evidence(self, identity):
        path = self.root / "evidence" / identifier(identity)
        try:
            manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise Missing("Uploaded evidence is incomplete or absent") from error
        if file_hash(path / "archive.sqlite3") != manifest["hash"]:
            raise ConfigurationError("Uploaded evidence has changed")
        return path / "archive.sqlite3", manifest

    def create(self, token, name, evidence, command):
        actor = self.authorize(token)
        command, name = identifier(command), project_name(name)
        selected = [self._evidence(identifier(item)) for item in sorted(set(evidence))]
        request = fingerprint([actor["name"], name, [m for _, m in selected]])
        directory = self.root / "backups" / command
        with self.operation():
            receipt = directory / "manifest.json"
            if receipt.exists():
                previous = json.loads(receipt.read_text(encoding="utf-8"))
                if previous["request_hash"] != request:
                    raise Conflict("This backup command already describes another capture")
                self._verify_files(directory, previous)
                return previous
            directory.mkdir(parents=True, exist_ok=True)
            evidence_rows = []
            for source, info in selected:
                target = directory / (info["id"] + ".sqlite3")
                shutil.copyfile(source, target)
                evidence_rows.append({**info, "file": target.name})
            # Evidence is already frozen. The later SQL snapshot must contain
            # every immutable release/baseline referenced by those observations.
            with self.repo.connection(snapshot=True) as c:
                self.repo._actor(c, token)
                for info in evidence_rows:
                    for project, release in info["releases"]:
                        if not c.execute("SELECT id FROM releases WHERE id=%s AND project_id=%s", (release, project)).fetchone():
                            raise Conflict("Evidence references a release absent from this database: " + release)
                    for baseline in info["baselines"]:
                        if not c.execute("SELECT id FROM training_baselines WHERE id=%s", (baseline,)).fetchone():
                            raise Conflict("Evidence references an absent training baseline: " + baseline)
                snapshot = c.execute("SELECT pg_export_snapshot() AS snapshot").fetchone()["snapshot"]
                database = database_inventory(c)
                self._postgres("pg_dump", ["--format=custom", "--no-owner", "--no-privileges", "--schema=azeo_configuration",
                                           "--snapshot=" + snapshot, "--file=" + str(directory / "configuration.dump")])
            document = {"schema": 1, "id": command, "name": name, "actor": actor["name"], "created": now(),
                        "request_hash": request, "database": database, "evidence": evidence_rows,
                        "database_hash": file_hash(directory / "configuration.dump"),
                        "consistency": "SQLite evidence snapshots precede the PostgreSQL snapshot; referenced immutable releases and baselines were resolved"}
            document["hash"] = fingerprint(document)
            write_json_transactional(receipt, document)
            return document

    @staticmethod
    def _verify_files(directory, manifest):
        if manifest.get("schema") != 1 or manifest.get("hash") != fingerprint({k: v for k, v in manifest.items() if k != "hash"}):
            raise ConfigurationError("Backup manifest integrity check failed")
        if file_hash(directory / "configuration.dump") != manifest["database_hash"]:
            raise ConfigurationError("Database backup checksum failed")
        for row in manifest["evidence"]:
            expected = identifier(row["id"]) + ".sqlite3"
            if row["file"] != expected or file_hash(directory / expected) != row["hash"]:
                raise ConfigurationError("Evidence backup checksum failed")

    def manifest(self, token, backup):
        self.authorize(token)
        directory = self.root / "backups" / identifier(backup)
        try:
            document = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise Missing("Completed backup does not exist") from error
        self._verify_files(directory, document)
        return directory, document

    def export(self, token, backup):
        directory, manifest = self.manifest(token, backup)
        path = directory / "backup.zip"
        with self.operation():
            partial = directory / "export.partial"
            with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for filename in ["manifest.json", "configuration.dump", *(row["file"] for row in manifest["evidence"])]:
                    archive.write(directory / filename, filename)
            partial.replace(path)
        return path

    def import_backup(self, token, source):
        try:
            return self._import_backup(token, source)
        except (zipfile.BadZipFile, KeyError, ValueError, TypeError, OSError) as error:
            raise ConfigurationError("Backup archive is incomplete or invalid") from error

    def _import_backup(self, token, source):
        self.authorize(token)
        from tempfile import TemporaryDirectory
        with self.operation(), zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            if len({entry.filename for entry in entries}) != len(entries) or sum(entry.file_size for entry in entries) > MAX_EVIDENCE:
                raise ConfigurationError("Backup archive is oversized or has duplicate entries")
            if archive.getinfo("manifest.json").file_size > 16 * 1024 * 1024:
                raise ConfigurationError("Backup manifest is too large")
            manifest = json.loads(archive.read("manifest.json"))
            identity = identifier(manifest["id"])
            names = {"manifest.json", "configuration.dump", *(identifier(row["id"]) + ".sqlite3" for row in manifest["evidence"])}
            if names != {entry.filename for entry in entries}:
                raise ConfigurationError("Backup file inventory differs from the manifest")
            with TemporaryDirectory(prefix="import-", dir=self.root) as temporary:
                staging = Path(temporary) / "backup"
                staging.mkdir()
                for name in names:
                    with archive.open(name) as reader, (staging / name).open("xb") as writer:
                        shutil.copyfileobj(reader, writer, 1024 * 1024)
                self._verify_files(staging, manifest)
                destination = self.root / "backups" / identity
                if destination.exists():
                    previous = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
                    self._verify_files(destination, previous)
                    if previous["hash"] != manifest["hash"]:
                        raise Conflict("A different backup already uses this identity")
                    return previous
                destination.parent.mkdir(parents=True, exist_ok=True)
                staging.replace(destination)
            return manifest

    def rehearse(self, token, backup, command):
        actor = self.authorize(token)
        command = identifier(command)
        source, manifest = self.manifest(token, backup)
        directory = self.root / "restores" / command
        with self.operation():
            receipt = directory / "receipt.json"
            if receipt.exists():
                previous = json.loads(receipt.read_text(encoding="utf-8"))
                if previous["backup"] != backup or previous["actor"] != actor["name"]:
                    raise Conflict("This restore command belongs to another rehearsal")
                return previous
            directory.mkdir(parents=True, exist_ok=True)
            from psycopg import connect, sql
            from psycopg.conninfo import make_conninfo
            intent = directory / "intent.json"
            if intent.exists():
                document = json.loads(intent.read_text(encoding="utf-8"))
                if document["backup"] != backup or document["actor"] != actor["name"]:
                    raise Conflict("This pending restore belongs to another rehearsal")
            else:
                document = {"id": command, "backup": backup, "actor": actor["name"], "created": now(),
                            "database": "azeo_rehearsal_" + uuid4().hex, "state": "incomplete", "evidence": []}
                write_json_transactional(intent, document)
            database_name = document["database"]
            import re
            if not re.fullmatch(r"azeo_rehearsal_[a-f0-9]{32}", database_name):
                raise ConfigurationError("Invalid isolated restore destination")
            target_dsn = make_conninfo(self.repo._dsn, dbname=database_name)
            # The name is generated here, never supplied by HTTP or a manifest.
            with connect(self.repo._dsn, autocommit=True) as c:
                if not c.execute("SELECT datname FROM pg_database WHERE datname=%s", (database_name,)).fetchone():
                    c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
            restored = Repository(target_dsn)
            with restored.connection(snapshot=True) as c:
                loaded = c.execute("SELECT to_regclass('azeo_configuration.schema_version') AS table").fetchone()["table"]
            if loaded is None:
                self._postgres("pg_restore", ["--exit-on-error", "--single-transaction", "--no-owner", "--no-privileges",
                                              "--dbname=" + database_name, source / "configuration.dump"], dsn=target_dsn)
            with restored.connection(snapshot=True) as c:
                actual = database_inventory(c)
            if actual != manifest["database"]:
                raise ConfigurationError("Restored configuration, assets or audit evidence differ from the backup")
            for row in manifest["evidence"]:
                target = directory / row["file"]
                shutil.copyfile(source / row["file"], target)
                inventory = evidence_inventory(target, row["kind"])
                if any(inventory[key] != row[key] for key in inventory):
                    raise ConfigurationError("Restored archive evidence differs from the backup")
                document["evidence"].append({"file": row["file"], **inventory})
            document.update(state="verified", completed=now(), database_inventory=actual,
                            backup_hash=manifest["hash"], runtime_started=False)
            write_json_transactional(receipt, document)
            return document

    def state(self, token):
        self.authorize(token)
        def rows(folder, filename):
            result = []
            for path in sorted((self.root / folder).glob("*/" + filename), reverse=True):
                try:
                    result.append(json.loads(path.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    continue
            return result
        return {"backups": rows("backups", "manifest.json"), "restores": rows("restores", "receipt.json"),
                "incomplete": [str(p.parent.name) for p in (self.root / "restores").glob("*/intent.json") if not (p.parent / "receipt.json").exists()]}
