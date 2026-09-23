"""Transactional PostgreSQL snapshot repository, with project-scoped authorization.

Each command owns one short database transaction. No caller keeps a transaction
open while an engineer previews an import or operates a controller.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
from contextlib import contextmanager
from uuid import UUID, uuid4

from .documents import ConfigurationError, Conflict, Forbidden, Missing, PreparedImport
from . import schema


def identity_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def identifier(value: str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as error:
        raise ConfigurationError("Invalid repository identity") from error


def project_name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > 160 or any(ord(c) < 32 for c in name):
        raise ConfigurationError("Project name must be 1–160 printable characters")
    return name


class Repository:
    def __init__(self, dsn: str):
        # Do not log DSNs: deployment credentials belong to the service process.
        self._dsn = dsn
        self._pool = None

    def open_pool(self):
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
        self._pool = ConnectionPool(self._dsn, min_size=2, max_size=8, open=False,
                                    timeout=5, max_waiting=16,
                                    kwargs={"autocommit": True, "row_factory": dict_row,
                                            "connect_timeout": 5},
                                    check=ConnectionPool.check_connection)
        self._pool.open(wait=True, timeout=10)

    def close_pool(self):
        if self._pool is not None:
            self._pool.close()
            self._pool = None

    @contextmanager
    def connection(self, *, snapshot: bool = False):
        import psycopg
        from psycopg.rows import dict_row

        context = (self._pool.connection() if self._pool is not None else
                   psycopg.connect(self._dsn, connect_timeout=5, autocommit=True, row_factory=dict_row))
        with context as connection:
            with connection.transaction():
                if snapshot:
                    connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                connection.execute("SET LOCAL search_path TO azeo_configuration, public")
                connection.execute("SET LOCAL TIME ZONE 'UTC'")
                connection.execute("SET LOCAL statement_timeout = '30s'")
                connection.execute("SET LOCAL lock_timeout = '5s'")
                yield connection

    def migrate(self):
        known = {version: hashlib.sha256(sql.encode()).hexdigest()
                 for version, sql in schema.MIGRATIONS}
        with self.connection() as c:
            c.execute("SELECT pg_advisory_xact_lock(718930501)")
            exists = c.execute("SELECT to_regclass('azeo_configuration.schema_version') AS t"
                               ).fetchone()["t"]
            if exists:
                rows = c.execute("SELECT * FROM schema_version ORDER BY version").fetchall()
                if (not rows or [r["version"] for r in rows] != list(range(1, len(rows) + 1))
                        or any(known.get(r["version"]) != r["checksum"] for r in rows)):
                    raise ConfigurationError("Unsupported or modified configuration schema")
            else:
                rows = []
            applied = {r["version"] for r in rows}
            for version, sql in schema.MIGRATIONS:
                if version not in applied:
                    c.execute(sql)
                    c.execute("INSERT INTO schema_version VALUES (%s,%s)", (version, known[version]))

    def provision_identity(self, name: str, *, administrator: bool = False) -> str:
        """Local service-administrator operation, deliberately absent from HTTP."""
        name = project_name(name)
        token = secrets.token_urlsafe(48)
        with self.connection() as c:
            c.execute("""INSERT INTO identities(name,token_hash,administrator) VALUES (%s,%s,%s)
                ON CONFLICT(name) DO UPDATE SET token_hash=excluded.token_hash,
                administrator=excluded.administrator, enabled=true""",
                      (name, identity_hash(token), administrator))
        return token

    def grant(self, identity: str, project: str, role: str):
        if role not in {"reader", "importer", "engineer"}:
            raise ConfigurationError("Role must be reader, importer or engineer")
        with self.connection() as c:
            c.execute("""INSERT INTO grants VALUES (%s,%s,%s)
                ON CONFLICT(project_id,identity) DO UPDATE SET role=excluded.role""",
                      (identifier(project), identity, role))

    def revoke(self, identity: str):
        with self.connection() as c:
            c.execute("UPDATE identities SET enabled=false WHERE name=%s", (identity,))

    @staticmethod
    def _actor(c, token: str) -> dict:
        row = c.execute("SELECT name,administrator FROM identities "
                        "WHERE token_hash=%s AND enabled", (identity_hash(token),)).fetchone()
        if row is None:
            raise Forbidden("Sign in with a valid configuration-service access token")
        return row

    @staticmethod
    def _authorize(c, actor, project: str, *, write: bool = False, edit: bool = False):
        if actor["administrator"]:
            return
        row = c.execute("SELECT role FROM grants WHERE project_id=%s AND identity=%s",
                        (project, actor["name"])).fetchone()
        if row is None or (write and row["role"] != "importer") or (edit and row["role"] != "engineer"):
            raise Forbidden("This identity does not have the required project access")

    def authenticate(self, token: str) -> dict:
        with self.connection() as c:
            return self._actor(c, token)

    def projects(self, token: str) -> list[dict]:
        with self.connection() as c:
            actor = self._actor(c, token)
            return c.execute("""SELECT p.*, c.document->>'source_project_id' AS source_project_id,
                c.document->>'source_name' AS source_name
                FROM projects p LEFT JOIN catalogs c ON c.project_id=p.id WHERE %s OR EXISTS
                (SELECT 1 FROM grants g WHERE g.project_id=p.id AND g.identity=%s)
                ORDER BY p.name_key""", (actor["administrator"], actor["name"])).fetchall()

    def preview(self, token: str, name: str, prepared: PreparedImport) -> dict:
        name = project_name(name)
        with self.connection(snapshot=True) as c:
            actor = self._actor(c, token)
            project = c.execute("SELECT * FROM projects WHERE name_key=%s",
                                (name.casefold(),)).fetchone()
            if project:
                self._authorize(c, actor, project["id"], write=True)
                if project["mode"] != "file_shadow":
                    raise Conflict("This project uses repository check-in; snapshot imports are disabled")
                old = c.execute("SELECT path,digest FROM objects WHERE project_id=%s "
                                "AND NOT deleted", (project["id"],)).fetchall()
            else:
                if not actor["administrator"]:
                    raise Forbidden("Only a service administrator can import a new project")
                old = []
            previous = {row["path"]: row["digest"] for row in old}
            current = {d.path: d.digest for d in prepared.documents}
            return {**prepared.summary(), "project_id": project["id"] if project else None,
                    "expected_generation": project["generation"] if project else 0,
                    "added": sorted(current.keys() - previous.keys()),
                    "removed": sorted(previous.keys() - current.keys()),
                    "updated": sorted(p for p in current.keys() & previous.keys()
                                      if current[p] != previous[p])}

    def import_snapshot(self, token: str, name: str, prepared: PreparedImport, *,
                        expected_generation: int, command_id: str) -> dict:
        name, command_id = project_name(name), identifier(command_id)
        if expected_generation < 0:
            raise ConfigurationError("An expected generation is required")
        request_hash = identity_hash(json.dumps([name, prepared.digest, expected_generation]))
        # Index upgrades are service implementation details, not a new client command.
        projection_hash = identity_hash(json.dumps([name, prepared.digest, expected_generation,
                                                    prepared.catalog["version"]]))
        with self.connection() as c:
            actor = self._actor(c, token)
            # Serializes creation as well as updates to a project. A retry does not
            # race a second request into either a duplicate project or partial tags.
            c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (name.casefold(),))
            project = c.execute("SELECT * FROM projects WHERE name_key=%s FOR UPDATE",
                                (name.casefold(),)).fetchone()
            if project:
                self._authorize(c, actor, project["id"], write=True)
                if project["mode"] != "file_shadow":
                    raise Conflict("This project uses repository check-in; snapshot imports are disabled")
            elif not actor["administrator"]:
                raise Forbidden("Only a service administrator can import a new project")
            retry = c.execute("SELECT actor,request_hash,result FROM changes WHERE command_id=%s",
                              (command_id,)).fetchone()
            if retry:
                if retry["actor"] != actor["name"] or retry["request_hash"] not in {request_hash, projection_hash}:
                    raise Conflict("Command identity was already used for a different request")
                return retry["result"]
            generation = project["generation"] if project else 0
            if generation != expected_generation:
                raise Conflict(f"Project is now at snapshot {generation}; preview again before importing")
            if project is None:
                project = c.execute("""INSERT INTO projects(id,name,name_key) VALUES (%s,%s,%s)
                    RETURNING *""", (str(uuid4()), name, name.casefold())).fetchone()
            return self._commit(c, actor, project, prepared, command_id, request_hash)

    def _commit(self, c, actor, project, prepared, command_id, request_hash, *,
                action="import_snapshot", reason="", renames=None):
        """One revision/catalog writer for snapshot capture and engineering check-in."""
        from psycopg.types.json import Jsonb

        project_id = project["id"]
        generation = project["generation"]
        old = {row["path_key"]: row for row in c.execute(
            "SELECT * FROM objects WHERE project_id=%s", (project_id,)).fetchall()}
        for before, after in (renames or {}).items():
            row = old.pop(before.casefold())
            if after.casefold() in old:
                raise Conflict(f"Rename target already has a repository identity: {after}")
            old[after.casefold()] = row
        changed = [d for d in prepared.documents if d.path.casefold() not in old
                   or old[d.path.casefold()]["digest"] != d.digest
                   or old[d.path.casefold()]["path"] != d.path
                   or old[d.path.casefold()]["deleted"]]
        current_paths = {d.path.casefold() for d in prepared.documents}
        removed = [r for p, r in old.items() if p not in current_paths and not r["deleted"]]
        generation += bool(changed or removed)
        previous_catalog = c.execute("SELECT document FROM catalogs WHERE project_id=%s",
                                     (project_id,)).fetchone()
        reindex = previous_catalog is None or previous_catalog["document"] != prepared.catalog
        committed = []
        for d in changed:
            prior = old.get(d.path.casefold())
            committed.append({"id": str(prior["id"]) if prior else str(uuid4()), "path": d.path,
                              "kind": d.kind, "name": d.name, "digest": d.digest,
                              "revision": prior["revision"] + 1 if prior else 1})
        identities = {r["path"]: r for r in committed}
        result = {"project_id": str(project_id), "generation": generation,
                  "digest": prepared.digest, "changed": len(changed), "removed": len(removed),
                  "files": len(prepared.documents), "tags": len(prepared.tags),
                  "mode": project["mode"], "reason": reason, "reindexed": bool(reindex),
                  "catalog_version": prepared.catalog["version"], "objects": committed,
                  "deleted_paths": [r["path"] for r in removed]}
        change_id = c.execute("""INSERT INTO changes(command_id,project_id,generation,actor,
            action,request_hash,result) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                              (command_id, project_id, generation, actor["name"],
                               action, request_hash, Jsonb(result))).fetchone()["id"]
        for document in changed:
            row = identities[document.path]
            object_id, revision = row["id"], row["revision"]
            c.execute("""INSERT INTO objects(id,project_id,path,path_key,kind,name,revision,digest)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET
                path=excluded.path, path_key=excluded.path_key, kind=excluded.kind, name=excluded.name,
                revision=excluded.revision,digest=excluded.digest,deleted=false""",
                      (object_id, project_id, document.path, document.path.casefold(),
                       document.kind, document.name, revision, document.digest))
            c.execute("""INSERT INTO revisions(object_id,number,change_id,path,digest,document,
                content) VALUES (%s,%s,%s,%s,%s,%s,%s)""", (object_id, revision, change_id,
                      document.path, document.digest, Jsonb(document.payload), document.content))
        for row in removed:
            c.execute("UPDATE objects SET deleted=true,revision=revision+1 WHERE id=%s",
                      (row["id"],))
            c.execute("""INSERT INTO revisions(object_id,number,change_id,path,digest,content,
                deleted) VALUES (%s,%s,%s,%s,'',%s,true)""",
                      (row["id"], row["revision"] + 1, change_id, row["path"], b""))
        if changed or removed or reindex:
            ids = {r["path"]: r["id"] for r in c.execute(
                "SELECT id,path FROM objects WHERE project_id=%s AND NOT deleted",
                (project_id,)).fetchall()}
            # Replacing all 34,000 rows made independent check-ins exceed the
            # project lock timeout. Stage once, then change only differing rows;
            # unchanged tags avoid index churn and repeated foreign-key checks.
            c.execute("""CREATE TEMP TABLE incoming_configuration_tags(
                path text PRIMARY KEY, object_id uuid NOT NULL, data jsonb NOT NULL
                ) ON COMMIT DROP""")
            with c.cursor().copy("COPY incoming_configuration_tags(path,object_id,data) FROM STDIN") as copy:
                for tag in prepared.tags:
                    copy.write_row((tag["path"], ids[tag["source"]],
                                    json.dumps(tag, allow_nan=False)))
            c.execute("""DELETE FROM tags WHERE project_id=%s AND NOT EXISTS(
                SELECT 1 FROM incoming_configuration_tags incoming WHERE incoming.path=tags.path)""", (project_id,))
            c.execute("""INSERT INTO tags(project_id,path,object_id,data)
                SELECT %s,path,object_id,data FROM incoming_configuration_tags
                ON CONFLICT(project_id,path) DO UPDATE SET object_id=excluded.object_id,data=excluded.data
                WHERE tags.object_id IS DISTINCT FROM excluded.object_id OR tags.data IS DISTINCT FROM excluded.data""",
                      (project_id,))
            c.execute("""INSERT INTO catalogs(project_id,version,document) VALUES (%s,%s,%s)
                ON CONFLICT(project_id) DO UPDATE SET version=excluded.version,
                document=excluded.document,built_at=clock_timestamp()""",
                      (project_id, prepared.catalog["version"], Jsonb(prepared.catalog)))
        if changed or removed:
            c.execute("UPDATE projects SET generation=%s,digest=%s,modified=clock_timestamp() "
                      "WHERE id=%s", (generation, prepared.digest, project_id))
        return result

    def catalog(self, token, project, *, known=""):
        project = identifier(project)
        with self.connection(snapshot=True) as c:
            self._authorize(c, self._actor(c, token), project)
            p = c.execute("SELECT * FROM projects WHERE id=%s", (project,)).fetchone()
            if p is None:
                raise Missing("Project does not exist")
            catalog = c.execute("SELECT version,built_at FROM catalogs WHERE project_id=%s",
                                (project,)).fetchone()
            if catalog is None:
                raise Conflict("Capture this project again to build its engineering catalog")
            stamp = f"{p['generation']}:{p['digest']}:{catalog['version']}:{catalog['built_at'].isoformat()}"
            if known == stamp:
                return {"project": p, "catalog_stamp": stamp, "not_modified": True}
            document = c.execute("SELECT document FROM catalogs WHERE project_id=%s",
                                 (project,)).fetchone()["document"]
            tags = c.execute("SELECT data FROM tags WHERE project_id=%s ORDER BY path",
                             (project,)).fetchall()
            objects = c.execute("SELECT id,path,kind,revision,digest FROM objects "
                                "WHERE project_id=%s AND NOT deleted ORDER BY path", (project,)).fetchall()
            return {"project": p, "catalog": document, "indexed_at": catalog["built_at"],
                    "catalog_stamp": stamp,
                    "tags": [r["data"] for r in tags], "objects": objects}

    def _read(self, token, project, query, args=(), *, snapshot=False):
        project = identifier(project)
        with self.connection(snapshot=snapshot) as c:
            self._authorize(c, self._actor(c, token), project)
            if not c.execute("SELECT id FROM projects WHERE id=%s", (project,)).fetchone():
                raise Missing("Project no longer exists")
            return c.execute(query, (project, *args)).fetchall()

    @staticmethod
    def _page(limit, offset):
        if not 1 <= limit <= 500 or not 0 <= offset <= 1000000:
            raise ConfigurationError("Page size must be 1–500; offset must be 0–1,000,000")

    def objects(self, token, project, *, query="", limit=200, offset=0):
        self._page(limit, offset)
        return self._read(token, project, """SELECT id,path,kind,name,revision,digest FROM objects
            WHERE project_id=%s AND NOT deleted AND strpos(lower(path || ' ' || name),lower(%s))>0
            ORDER BY path LIMIT %s OFFSET %s""", (query[:200], limit, offset))

    def tags(self, token, project, *, query="", limit=200, offset=0):
        self._page(limit, offset)
        return self._read(token, project, """SELECT path,object_id,data FROM tags WHERE project_id=%s
            AND strpos(search_text,lower(%s))>0
            ORDER BY path LIMIT %s OFFSET %s""", (query[:200], limit, offset))

    def revisions(self, token, project, object_id, *, limit=200, offset=0):
        self._page(limit, offset)
        return self._read(token, project, """SELECT r.number,r.path,r.digest,r.deleted,c.actor,
            c.occurred,c.generation,c.action,c.result->>'reason' AS reason FROM objects o JOIN revisions r ON o.id=r.object_id
            JOIN changes c ON r.change_id=c.id WHERE o.project_id=%s AND o.id=%s
            ORDER BY r.number DESC LIMIT %s OFFSET %s""", (identifier(object_id), limit, offset))

    def document(self, token, project, object_id, revision: int | None = None):
        rows = self._read(token, project, """SELECT r.* FROM objects o JOIN revisions r
            ON r.object_id=o.id WHERE o.project_id=%s AND o.id=%s
            AND r.number=coalesce(%s,o.revision)""", (identifier(object_id), revision))
        if not rows:
            raise Missing("Object revision does not exist in this project")
        row = rows[0]
        row["content"] = base64.b64encode(row["content"]).decode("ascii")
        return row

    def audit(self, token, project, *, after=0, limit=200):
        self._page(limit, 0)
        return self._read(token, project, """SELECT id,command_id,generation,actor,action,result,
            occurred FROM changes WHERE project_id=%s AND id>%s ORDER BY id LIMIT %s""",
                          (after, limit))

    def export(self, token, project, *, generation=None):
        project = identifier(project)
        with self.connection(snapshot=True) as c:
            self._authorize(c, self._actor(c, token), project)
            p = c.execute("SELECT * FROM projects WHERE id=%s", (project,)).fetchone()
            if p is None:
                raise Missing("Project does not exist")
            if generation is None:
                rows = c.execute("""SELECT o.id,o.path,o.kind,o.revision,o.digest,r.content FROM objects o JOIN revisions r
                    ON o.id=r.object_id AND o.revision=r.number WHERE o.project_id=%s AND NOT o.deleted
                    ORDER BY o.path""", (project,)).fetchall()
            else:
                if type(generation) is not int or not 1 <= generation <= p["generation"]:
                    raise ConfigurationError("Requested project generation is unavailable")
                rows = c.execute("""SELECT id,path,kind,revision,digest,content FROM (
                    SELECT DISTINCT ON(o.id) o.id,r.path,o.kind,r.number AS revision,r.digest,r.content,r.deleted
                    FROM objects o JOIN revisions r ON o.id=r.object_id JOIN changes ch ON ch.id=r.change_id
                    WHERE o.project_id=%s AND ch.generation<=%s ORDER BY o.id,r.number DESC
                    ) snapshot WHERE NOT deleted ORDER BY path""", (project, generation)).fetchall()
                change = c.execute("SELECT result FROM changes WHERE project_id=%s AND generation=%s ORDER BY id DESC LIMIT 1",
                                   (project, generation)).fetchone()
                p = {**p, "generation": generation, "digest": change["result"]["digest"]}
            return {"project": p, "digest": p["digest"],
                    "objects": [{k: v for k, v in r.items() if k != "content"} for r in rows], "files": [
                {"path": r["path"], "content": base64.b64encode(r["content"]).decode("ascii")}
                for r in rows]}
