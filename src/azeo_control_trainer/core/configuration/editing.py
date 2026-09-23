"""Coordinated drafts commit through the same immutable repository writer."""
from __future__ import annotations

from ..hmi.compatibility import (
    DISPLAY_PATH_PREFIXES, is_configuration_document_path, is_pvm_library_path,
)

import json
from functools import wraps
import time
from uuid import uuid4

from .documents import ConfigurationError, Conflict, Missing, prepare_import, safe_path
from .repository import identifier, identity_hash, project_name

LEASE_SECONDS = 90


def checkin_hash(project, session, edits, reason, generation):
    return identity_hash(json.dumps([project, session, edits, reason, generation], sort_keys=True))


def retry_contention(operation):
    @wraps(operation)
    def run(*args, **kwargs):
        from psycopg.errors import DeadlockDetected, LockNotAvailable, SerializationFailure
        for attempt in range(3):
            try:
                return operation(*args, **kwargs)
            except (DeadlockDetected, LockNotAvailable, SerializationFailure):
                # The connection context has rolled back before retrying. Keep
                # the same command identity so a retry cannot duplicate a commit.
                if attempt == 2:
                    raise Conflict("Engineering configuration is busy; retry with the preserved draft") from None
                time.sleep(.05 * (attempt + 1))
    return run


def normalize_edits(edits):
    if not edits or len(edits) > 1000:
        raise ConfigurationError("A change set needs 1–1,000 documents")
    paths, targets, result = set(), set(), []
    for edit in edits:
        path = safe_path(edit["path"])
        target = safe_path(edit.get("new_path") or path)
        if path.casefold() in paths or target.casefold() in targets:
            raise ConfigurationError("A change set contains duplicate source or destination paths")
        if any(p in {"versions", "revisions"} for p in (path + "/" + target).split("/")):
            raise ConfigurationError("Historical files are immutable; edit the working document")
        if any(p.startswith(DISPLAY_PATH_PREFIXES) and p.endswith("/history.json")
               for p in (path, target)):
            raise ConfigurationError("Published display history is immutable; edit its draft")
        revision = edit["expected_revision"]
        if type(revision) is not int or revision < 0:
            raise ConfigurationError("Every edit requires its base revision")
        if edit.get("content") is None and target != path:
            raise ConfigurationError("A rename requires the new document content")
        paths.add(path.casefold())
        targets.add(target.casefold())
        result.append({"path": path, "new_path": target, "expected_revision": revision,
                       "content": edit.get("content")})
    return sorted(result, key=lambda e: e["path"])


class EditingRepository:
    def __init__(self, repository):
        self.repo = repository

    def _project(self, c, token, project, *, edit=False, lock=False):
        project = identifier(project)
        actor = self.repo._actor(c, token)
        self.repo._authorize(c, actor, project, edit=edit)
        row = c.execute("SELECT * FROM projects WHERE id=%s" + (" FOR UPDATE" if lock else ""),
                        (identifier(project),)).fetchone()
        if row is None:
            raise Missing("Project does not exist")
        if edit and row["mode"] != "repository":
            raise Conflict("Create an isolated editing pilot before checking in changes")
        return actor, row

    @staticmethod
    def _retry(c, actor, project, command, digest):
        row = c.execute("SELECT actor,project_id,request_hash,result FROM changes WHERE command_id=%s",
                        (command,)).fetchone()
        if row:
            if (row["actor"] != actor["name"] or str(row["project_id"]) != str(project)
                    or row["request_hash"] != digest):
                raise Conflict("Command identity was already used for a different request")
            return row["result"]
        return None

    def fork(self, token, source, name, command, *, bundle=None, origin=None):
        from psycopg.types.json import Jsonb
        source, command, name = identifier(source), identifier(command), project_name(name)
        # Source bytes are captured before opening the short write transaction.
        bundle = bundle or self.repo.export(token, source)
        prepared = prepare_import(bundle["files"])
        action = "clone_baseline" if origin else "fork_editing"
        request = [source, name, action]
        if origin:
            request.append(origin)
        digest = identity_hash(json.dumps(request, sort_keys=True))
        with self.repo.connection() as c:
            actor, _ = self._project(c, token, source)
            if not actor["administrator"]:
                from .documents import Forbidden
                raise Forbidden("A service administrator must create the editing pilot")
            c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (name.casefold(),))
            previous = c.execute("SELECT * FROM projects WHERE name_key=%s", (name.casefold(),)).fetchone()
            if previous:
                retry = self._retry(c, actor, previous["id"], command, digest)
                if retry:
                    return retry
                raise Conflict("Choose a new name for the isolated editing pilot")
            if c.execute("SELECT id FROM changes WHERE command_id=%s", (command,)).fetchone():
                raise Conflict("Command identity was already used for a different request")
            project = c.execute("""INSERT INTO projects(id,name,name_key,mode,origin)
                VALUES (%s,%s,%s,'repository',%s) RETURNING *""",
                (str(uuid4()), name, name.casefold(), Jsonb({"project": source,
                 "generation": bundle["project"]["generation"], "digest": bundle["digest"], **(origin or {})}))).fetchone()
            return self.repo._commit(c, actor, project, prepared, command, digest,
                                     action=action, reason="Isolated trainee copy" if origin else "Isolated engineering pilot")

    def state(self, token, project):
        with self.repo.connection(snapshot=True) as c:
            actor, p = self._project(c, token, project)
            leases = c.execute("SELECT path,actor,session,expires_at FROM edit_leases "
                               "WHERE project_id=%s AND expires_at>clock_timestamp() ORDER BY path",
                               (project,)).fetchall()
            objects = c.execute("SELECT id,path,kind,name,revision,digest FROM objects "
                                "WHERE project_id=%s AND NOT deleted ORDER BY path", (project,)).fetchall()
            cursor = c.execute("SELECT coalesce(max(id),0) AS n FROM changes WHERE project_id=%s",
                               (project,)).fetchone()["n"]
            return {"project": p, "identity": actor["name"], "leases": leases,
                    "objects": objects, "cursor": cursor}

    @retry_contention
    def lease(self, token, project, session, paths, *, release=False):
        project, session = identifier(project), identifier(session)
        paths = sorted({safe_path(p) for p in paths}, key=str.casefold)
        if len(paths) > 1000 or len({p.casefold() for p in paths}) != len(paths):
            raise ConfigurationError("Invalid edit reservation paths")
        with self.repo.connection() as c:
            actor, _ = self._project(c, token, project, edit=True, lock=True)
            if release:
                c.execute("DELETE FROM edit_leases WHERE project_id=%s AND actor=%s AND session=%s "
                          "AND (cardinality(%s::text[])=0 OR path_key=ANY(%s))",
                          (project, actor["name"], session, paths, [p.casefold() for p in paths]))
            else:
                for path in paths:
                    held = c.execute("SELECT actor,session FROM edit_leases WHERE project_id=%s "
                                     "AND path_key=%s AND expires_at>clock_timestamp()",
                                     (project, path.casefold())).fetchone()
                    if held and (held["actor"] != actor["name"] or str(held["session"]) != session):
                        raise Conflict(f"{path} is being edited by {held['actor']}; your draft is preserved")
                    c.execute("""INSERT INTO edit_leases VALUES (%s,%s,%s,%s,%s,
                        clock_timestamp() + %s * interval '1 second')
                        ON CONFLICT(project_id,path_key) DO UPDATE SET actor=excluded.actor,
                        session=excluded.session,expires_at=excluded.expires_at,path=excluded.path""",
                        (project, path.casefold(), path, actor["name"], session, LEASE_SECONDS))
        return self.state(token, project)

    @staticmethod
    def _candidate(bundle, edits, *, allow_class_snapshots=False):
        objects = {r["path"].casefold(): r for r in bundle["objects"]}
        files = {f["path"]: f for f in bundle["files"]}
        renames = {}
        for edit in edits:
            path, target = edit["path"], edit["new_path"]
            old = objects.get(path.casefold())
            if "_class_revisions" in (path + "/" + target).split("/"):
                if not allow_class_snapshots or old or edit["content"] is None or path != target:
                    raise ConfigurationError("Pinned class files are immutable; use Library Manager to adopt a reviewed version")
            if (old["revision"] if old else 0) != edit["expected_revision"]:
                raise Conflict(f"{path} has a newer revision; compare and resolve your draft")
            if old and old["path"] != path:
                raise Conflict(f"The canonical source path is {old['path']}")
            if target != path:
                if not old or target.casefold() in objects:
                    raise Conflict(f"Rename destination is unavailable: {target}")
                renames[path] = target
            files.pop(path, None)
            if edit["content"] is not None:
                files[target] = {"path": target, "content": edit["content"]}
        from ..hmi.pvms.class_revisions import verify_files
        try:
            verify_files(list(files.values()))
        except ValueError as error:
            raise ConfigurationError(str(error)) from error
        if not allow_class_snapshots:
            from .libraries import class_stamp, decode, inventory
            class_edits = {e["path"] for e in edits if is_configuration_document_path(e["path"])
                           or is_pvm_library_path(e["path"]) or e["path"].endswith("/_standards.json")
                           or e["path"].endswith("/_functions.json")}
            if class_edits:
                state = inventory(bundle)
                classes = {r["id"]: r for r in state["classes"]}
                before_classes, after_classes = decode(bundle), decode({"files": list(files.values())})
                for instance in state["instances"]:
                    cls = classes.get(instance["class_id"], {})
                    root = cls.get("root", "")
                    affected = root and any(p.startswith(root + "/") or p == root.rsplit("/", 1)[0] + "/_standards.json" for p in class_edits)
                    changed = affected and class_stamp(before_classes, root, cls["name"]) != class_stamp(after_classes, root, cls["name"])
                    if changed and instance["status"] == "Unpinned":
                        raise Conflict("Pin existing instances in Library Manager before changing their shared graphics classes: " + instance["name"])
        return prepare_import(list(files.values())), renames

    def preview(self, token, project, edits):
        edits = normalize_edits(edits)
        with self.repo.connection() as c:
            self._project(c, token, project, edit=True)
        bundle = self.repo.export(token, project)
        prepared, renames = self._candidate(bundle, edits)
        summary = prepared.summary()
        summary["warnings"] = [w for w in summary["warnings"] if not w.startswith("Files remain authoritative")]
        summary["warnings"].append("Check-in updates engineering configuration; it does not download or publish it.")
        return {**summary, "generation": bundle["project"]["generation"],
                "paths": [e["path"] for e in edits], "renames": renames,
                "unresolved": [e for e in prepared.catalog["edges"] if e["status"] != "resolved"],
                "issues": prepared.catalog["issues"]}

    def rename_preview(self, token, project, module, new_name):
        from .renaming import rename_module
        with self.repo.connection() as c:
            self._project(c, token, project, edit=True)
        return rename_module(self.repo.export(token, project), module, new_name)

    @retry_contention
    def checkin(self, token, project, session, edits, reason, command, *, expected_generation=None, allow_class_snapshots=False):
        project, session, command = identifier(project), identifier(session), identifier(command)
        edits = normalize_edits(edits)
        reason = reason.strip()
        if not reason or len(reason) > 2000:
            raise ConfigurationError("Describe the engineering change (1–2,000 characters)")
        digest = checkin_hash(project, session, edits, reason, expected_generation)
        for _ in range(4):
            with self.repo.connection() as c:
                actor, _ = self._project(c, token, project, edit=True)
                retry = self._retry(c, actor, project, command, digest)
                if retry:
                    return retry
            bundle = self.repo.export(token, project)
            prepared, renames = self._candidate(bundle, edits, allow_class_snapshots=allow_class_snapshots)
            if (renames or allow_class_snapshots) and expected_generation != bundle["project"]["generation"]:
                raise Conflict("Project changed since impact review; preview its consumers again")
            with self.repo.connection() as c:
                actor, p = self._project(c, token, project, edit=True, lock=True)
                retry = self._retry(c, actor, project, command, digest)
                if retry:
                    return retry
                if p["generation"] != bundle["project"]["generation"]:
                    # Independent module edits can commit without a project-wide
                    # stale-generation conflict. Rebuild against the new snapshot.
                    continue
                for path in {e[k] for e in edits for k in ("path", "new_path")}:
                    lease = c.execute("SELECT actor,session FROM edit_leases WHERE project_id=%s "
                                      "AND path_key=%s AND expires_at>clock_timestamp()",
                                      (project, path.casefold())).fetchone()
                    if not lease or lease["actor"] != actor["name"] or str(lease["session"]) != session:
                        raise Conflict(f"Edit ownership expired or is missing for {path}; reserve again")
                return self.repo._commit(c, actor, p, prepared, command, digest,
                                         action="checkin", reason=reason, renames=renames)
        raise Conflict("Project changed repeatedly during validation; retry with the preserved draft")
