"""Immutable exercise baselines over releases and the existing training runner."""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from .documents import ConfigurationError, Conflict, Missing
from .editing import EditingRepository
from .release_manifest import fingerprint, plain
from .releases import ReleaseRepository
from .repository import identifier, project_name


def exercise_document(exercise, snapshot):
    if not exercise and not snapshot:
        return {}
    from ..simulation.workbench import SimulationWorkbenchService, _json_value
    if not isinstance(exercise, dict) or not isinstance(snapshot, dict):
        raise ConfigurationError("An exercise needs its starting snapshot")
    try:
        SimulationWorkbenchService.validate_snapshot_document(snapshot)
    except (ValueError, TypeError, KeyError) as error:
        raise ConfigurationError(str(error)) from error
    if snapshot.get("process") is None:
        raise ConfigurationError("An exercise baseline needs a captured process state")
    if not exercise.get("loop") or not exercise.get("objectives") or not exercise.get("name"):
        raise ConfigurationError("An exercise needs a name, loop and objectives")
    result = {key: deepcopy(exercise.get(key, [] if key in {"objectives", "paths"} else ""))
              for key in ("name", "loop", "input_path", "objectives", "paths")}
    if any(not isinstance(result[key], str) for key in ("name", "loop", "input_path")) or any(
            not isinstance(result[key], list) or any(not isinstance(v, str) for v in result[key]) for key in ("objectives", "paths")):
        raise ConfigurationError("Exercise paths and objectives must be text")
    raw = json.dumps(_json_value(snapshot), ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    if len(raw) > 16 * 1024 * 1024:
        raise ConfigurationError("Starting snapshot exceeds 16 MB")
    return {"exercise": result, "snapshot": base64.b64encode(raw).decode(),
            "snapshot_hash": hashlib.sha256(raw).hexdigest()}


class TrainingRepository:
    def __init__(self, repository):
        self.repo = repository
        self.editing = EditingRepository(repository)
        self.releases = ReleaseRepository(repository)

    def list(self, token, project):
        with self.repo.connection(snapshot=True) as c:
            self.editing._project(c, token, project)
            return plain(c.execute("SELECT id,name,release_id,actor,reason,created,document->>'kind' AS kind "
                                   "FROM training_baselines WHERE project_id=%s ORDER BY created DESC",
                                   (identifier(project),)).fetchall())

    def read(self, token, project, baseline):
        with self.repo.connection(snapshot=True) as c:
            self.editing._project(c, token, project)
            row = c.execute("SELECT * FROM training_baselines WHERE id=%s AND project_id=%s",
                            (identifier(baseline), identifier(project))).fetchone()
            if not row:
                raise Missing("Training baseline does not exist")
            return plain({k: v for k, v in row.items() if k != "request_hash"})

    def create(self, token, project, release, name, reason, command, *, exercise=None, snapshot=None):
        project, release, command = identifier(project), identifier(release), identifier(command)
        name, reason = project_name(name), project_name(reason)
        package = self.releases.bundle(token, project, release)
        objects = package["manifest"]["objects"]
        required = {o["path"] for o in objects if o["kind"] in {"module", "display"}}
        if not required.issubset(package["manifest"]["selected"]):
            raise ConfigurationError("A training baseline requires a release selecting every module and display")
        content = exercise_document(exercise, snapshot)
        if content:
            from ..strategy.serialization.strategy_io import graph_from_document
            files = {f["path"]: f["content"] for f in package["bundle"]["files"]}
            graphs = [graph_from_document(json.loads(base64.b64decode(files[o["path"]])), strict=True)[0]
                      for o in objects if o["kind"] == "module"]
            modules = {g.name for g in graphs}
            if content["exercise"]["loop"].partition("/")[0] not in modules:
                raise ConfigurationError("The exercise loop is absent from the released configuration")
            expected = {g.name: {(str(b.id), str(b.block_type)) for b in g.blocks.values()} for g in graphs}
            actual = {m["name"]: {(str(b.get("id", "")), str(b["type"])) for b in m["blocks"]} for m in snapshot["controller"]}
            if actual != expected:
                raise ConfigurationError("The process starting snapshot belongs to a different controller configuration")
        document = {"schema": 1, "kind": "exercise" if content else "configuration",
                    "source_digest": package["bundle"]["digest"],
                    "package_hash": package["manifest"]["package_hash"],
                    "implementation": package["manifest"]["implementation"], **content}
        document["hash"] = fingerprint(document)
        request = fingerprint([project, release, name, reason, document])
        from psycopg.types.json import Jsonb
        with self.repo.connection() as c:
            actor, _ = self.editing._project(c, token, project, edit=True, lock=True)
            old = c.execute("SELECT * FROM training_baselines WHERE command_id=%s", (command,)).fetchone()
            if old:
                if old["actor"] != actor["name"] or old["request_hash"] != request:
                    raise Conflict("Command identity was already used for another baseline")
                return plain({k: v for k, v in old.items() if k != "request_hash"})
            row = c.execute("""INSERT INTO training_baselines
                (id,project_id,release_id,name,actor,reason,command_id,request_hash,document)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (str(uuid4()), project, release, name, actor["name"], reason, command, request, Jsonb(document))).fetchone()
            return plain({k: v for k, v in row.items() if k != "request_hash"})

    def clone(self, token, project, baseline, name, command):
        row = self.read(token, project, baseline)
        package = self.releases.bundle(token, project, row["release_id"])
        return self.editing.fork(token, project, name, command, bundle=package["bundle"],
                                 origin={"baseline": row["id"], "release": row["release_id"],
                                         "baseline_hash": row["document"]["hash"]})

    def context(self, token, project):
        """Trainees may read their inherited baseline without access to its source project."""
        with self.repo.connection(snapshot=True) as c:
            _, current = self.editing._project(c, token, project)
            origin = current.get("origin") or {}
            if not origin.get("baseline"):
                raise Missing("This project was not cloned from a training baseline")
            row = c.execute("SELECT id,project_id,release_id,name,document FROM training_baselines WHERE id=%s",
                            (identifier(origin["baseline"]),)).fetchone()
            if not row or row["document"]["hash"] != origin.get("baseline_hash"):
                raise Conflict("The inherited baseline cannot be verified")
            manifest = c.execute("SELECT manifest FROM releases WHERE id=%s", (row["release_id"],)).fetchone()["manifest"]
            originals = {o["path"]: o for o in manifest["objects"]}
            copies = c.execute("""SELECT o.id,r.path FROM objects o JOIN revisions r ON r.object_id=o.id AND r.number=1
                WHERE o.project_id=%s ORDER BY r.path""", (project,)).fetchall()
            mapping = [{"source_object": originals[o["path"]]["id"], "trainee_object": o["id"], "path": o["path"],
                        "kind": originals[o["path"]]["kind"], "digest": originals[o["path"]]["digest"]}
                       for o in copies if o["path"] in originals]
            return plain({"baseline": row, "project_id": project, "source_digest": origin["digest"],
                          "matches_baseline": current["digest"] == origin["digest"], "object_mapping": mapping})


def export_exercise(context, directory):
    """Install a checked exercise in the existing local training directory."""
    from ..simulation.training import Exercise
    from ..simulation.workbench import _write_json
    row = context["baseline"]
    document = row["document"]
    if document["kind"] != "exercise":
        raise ConfigurationError("This baseline contains configuration only; no process starting condition was captured")
    if not context["matches_baseline"]:
        raise Conflict("The trainee configuration differs from its starting baseline")
    raw = base64.b64decode(document["snapshot"], validate=True)
    if hashlib.sha256(raw).hexdigest() != document["snapshot_hash"]:
        raise ConfigurationError("The exercise snapshot failed its integrity check")
    root = Path(directory)
    snapshot = root / "baselines" / (row["id"] + ".json")
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_bytes(raw)
    exercise = Exercise(**document["exercise"], snapshot=str(snapshot.resolve()),
                        configuration={"baseline_id": row["id"], "baseline_hash": document["hash"],
                                       "project_id": context["project_id"], "source_project_id": row["project_id"],
                                       "source_release_id": row["release_id"], "source_digest": document["source_digest"],
                                       "snapshot_hash": document["snapshot_hash"],
                                       "exercise_hash": fingerprint(document["exercise"]),
                                       "provider": json.loads(raw).get("provider", {}),
                                       "modules": {o["trainee_object"]: o["digest"] for o in context["object_mapping"] if o["kind"] == "module"}})
    from dataclasses import asdict
    return _write_json(root / "exercises" / (row["id"] + ".json"), asdict(exercise))
