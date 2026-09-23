"""Immutable release reviews, durable delivery jobs and target-owned observations.

Engineering requests work; only a separately authenticated runtime reports what
it loaded. Neither a check-in nor a delivery receipt asserts that a loop scans.
"""
from __future__ import annotations

import json
import secrets
from uuid import uuid4

from psycopg.types.json import Jsonb

from .documents import ConfigurationError, Conflict, Forbidden, Missing, prepare_import
from .editing import EditingRepository
from .repository import identifier, identity_hash, project_name
from .release_validation import implementation_digest, validate_bundle
from .release_manifest import deployable, fingerprint, plain


def release_selection(bundle, requested):
    objects = {obj["path"]: obj for obj in bundle["objects"]}
    requested = sorted(set(requested))
    if not requested or any(path not in objects or not deployable(objects[path]) for path in requested):
        raise ConfigurationError("Select checked-in control modules or display drafts")
    prepared = prepare_import(bundle["files"])
    index = {node["path"]: node for node in [*prepared.catalog["nodes"], *prepared.tags]}
    selected = set(requested)
    # Expand control dependencies, including controller-store publications. Display
    # navigation remains an explicitly reviewed external dependency, not a request
    # to download an entire plant just because an Up link reaches its overview.
    while True:
        before = set(selected)
        for edge in prepared.catalog["edges"]:
            owner = index.get(edge["source"], {})
            target = index.get(edge["target"], {})
            source = owner.get("source", "")
            destination = target.get("source", "")
            if source in selected and destination in objects and deployable(objects[destination]) == "controller":
                selected.add(destination)
        if selected == before:
            break
    external = [edge for edge in prepared.catalog["edges"]
                if index.get(edge["source"], {}).get("source") in selected
                and edge["kind"] == "navigation"
                and index.get(edge["target"], {}).get("source") not in selected]
    return sorted(selected), external


class ReleaseRepository:
    def __init__(self, repository, *, validator=validate_bundle):
        self.repo = repository
        self.editing = EditingRepository(repository)
        self.validator = validator

    def preview(self, token, project, paths):
        with self.repo.connection() as c:
            actor, _ = self.editing._project(c, token, project, edit=True)
        bundle = plain(self.repo.export(token, project))
        selected, external = release_selection(bundle, paths)
        findings = [{**finding, "severity": str(finding["severity"]).upper()}
                    for finding in self.validator(bundle, selected)]
        manifest = {"project_id": str(project), "generation": bundle["project"]["generation"],
                    "source_digest": bundle["digest"], "implementation": implementation_digest(),
                    "requested": sorted(set(paths)), "selected": selected,
                    "objects": bundle["objects"], "findings": findings, "external_navigation": external}
        manifest["package_hash"] = fingerprint(manifest)
        preview = str(uuid4())
        with self.repo.connection() as c:
            actor, current = self.editing._project(c, token, project, edit=True)
            if current["digest"] != bundle["digest"]:
                raise Conflict("Configuration changed during verification; review again")
            c.execute("INSERT INTO release_previews(id,project_id,actor,manifest,bundle) VALUES (%s,%s,%s,%s,%s)",
                      (preview, project, actor["name"], Jsonb(manifest), Jsonb(bundle)))
        return {"id": preview, "manifest": manifest}

    def create(self, token, project, preview, reason, command):
        command, preview = identifier(command), identifier(preview)
        reason = str(reason).strip()
        if not reason or len(reason) > 2000:
            raise ConfigurationError("Enter a release reason (up to 2000 characters)")
        request = fingerprint([str(project), preview, reason])
        with self.repo.connection() as c:
            actor, current = self.editing._project(c, token, project, edit=True, lock=True)
            retry = c.execute("SELECT * FROM releases WHERE command_id=%s", (command,)).fetchone()
            if retry:
                if retry["actor"] != actor["name"] or retry["request_hash"] != request:
                    raise Conflict("Release command identity was already used")
                return self._release_row(retry)
            row = c.execute("SELECT * FROM release_previews WHERE id=%s AND project_id=%s AND actor=%s",
                            (preview, project, actor["name"])).fetchone()
            if not row:
                raise Missing("Release review does not exist for this engineer")
            manifest = row["manifest"]
            if any(f["severity"] == "ERROR" for f in manifest["findings"]):
                raise Conflict("Verification errors block release")
            if (manifest["generation"] != current["generation"] or manifest["source_digest"] != current["digest"]
                    or manifest["implementation"] != implementation_digest()):
                raise Conflict("Configuration or installed classes changed; review again")
            number = c.execute("SELECT coalesce(max(number),0)+1 AS n FROM releases WHERE project_id=%s", (project,)).fetchone()["n"]
            result = c.execute("""INSERT INTO releases(id,project_id,number,command_id,actor,request_hash,reason,manifest,bundle)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (str(uuid4()), project, number, command, actor["name"], request, reason,
                 Jsonb(manifest), Jsonb(row["bundle"]))).fetchone()
            return self._release_row(result)

    @staticmethod
    def _release_row(row):
        return plain({k: v for k, v in row.items() if k not in {"bundle", "request_hash"}})

    def bundle(self, token, project, release):
        with self.repo.connection(snapshot=True) as c:
            self.editing._project(c, token, project)
            row = c.execute("SELECT * FROM releases WHERE id=%s AND project_id=%s", (identifier(release), project)).fetchone()
            if not row:
                raise Missing("Release does not exist")
            return {**self._release_row(row), "bundle": row["bundle"]}

    def register_target(self, token, project, name):
        name = project_name(name)
        target, credential = str(uuid4()), secrets.token_urlsafe(48)
        identity = "runtime-" + target
        with self.repo.connection() as c:
            self.editing._project(c, token, project, edit=True, lock=True)
            if c.execute("SELECT id FROM runtime_targets WHERE project_id=%s AND name=%s", (project, name)).fetchone():
                raise Conflict("This runtime name already exists; resume its saved profile")
            c.execute("INSERT INTO identities(name,token_hash) VALUES (%s,%s)", (identity, identity_hash(credential)))
            c.execute("INSERT INTO grants VALUES (%s,%s,'reader')", (project, identity))
            c.execute("INSERT INTO runtime_targets(id,project_id,name,identity) VALUES (%s,%s,%s,%s)",
                      (target, project, name, identity))
        return {"id": target, "project_id": str(project), "name": name, "token": credential}

    def _target(self, c, token, target, *, boot=None, lock=False):
        actor = self.repo._actor(c, token)
        row = c.execute("SELECT *, online AND heartbeat > clock_timestamp()-interval '30 seconds' AS fresh "
                        "FROM runtime_targets WHERE id=%s" + (" FOR UPDATE" if lock else ""),
                        (identifier(target),)).fetchone()
        if not row or row["identity"] != actor["name"]:
            raise Forbidden("Only this runtime target may report or acknowledge its deployment")
        if boot is not None and str(row["boot"]) != identifier(boot):
            raise Conflict("Runtime session was superseded; reconnect before reporting")
        return actor, row

    def hello(self, token, target, boot):
        boot = identifier(boot)
        with self.repo.connection() as c:
            _, row = self._target(c, token, target, lock=True)
            if row["fresh"] and str(row["boot"]) != boot:
                raise Conflict("Another runtime session is still connected")
            changed = str(row["boot"]) != boot
            c.execute("UPDATE runtime_targets SET boot=%s,online=true,heartbeat=clock_timestamp(),report=%s WHERE id=%s",
                      (boot, Jsonb({} if changed else row["report"]), target))
        return {"boot": boot}

    def report(self, token, target, boot, report, *, online=True):
        if len(json.dumps(report)) > 8 * 1024 * 1024 or not isinstance(report.get("objects", []), list):
            raise ConfigurationError("Invalid runtime observation")
        with self.repo.connection() as c:
            self._target(c, token, target, boot=boot, lock=True)
            c.execute("UPDATE runtime_targets SET heartbeat=clock_timestamp(),online=%s,report=%s WHERE id=%s",
                      (online, Jsonb(report), target))
        return {"recorded": True}

    def enqueue(self, token, project, release, target, components, command):
        command, release, target = identifier(command), identifier(release), identifier(target)
        components = sorted(set(components))
        if not components or set(components) - {"controller", "station"}:
            raise ConfigurationError("Choose controller, station, or both")
        request = fingerprint([str(project), release, target, components])
        with self.repo.connection() as c:
            actor, _ = self.editing._project(c, token, project, edit=True, lock=True)
            retry = c.execute("SELECT * FROM deployment_jobs WHERE command_id=%s", (command,)).fetchone()
            if retry:
                if retry["actor"] != actor["name"] or retry["request_hash"] != request:
                    raise Conflict("Deployment command identity was already used")
                return plain(retry)
            package = c.execute("SELECT manifest FROM releases WHERE id=%s AND project_id=%s", (release, project)).fetchone()
            node = c.execute("SELECT id FROM runtime_targets WHERE id=%s AND project_id=%s", (target, project)).fetchone()
            if not package or not node:
                raise Missing("Release and runtime must belong to this project")
            manifest = package["manifest"]
            available = {deployable(obj) for obj in manifest["objects"] if obj["path"] in manifest["selected"]}
            if set(components) - available:
                raise ConfigurationError("Release has no selected objects for one of these components")
            job = c.execute("""INSERT INTO deployment_jobs(id,project_id,release_id,target_id,actor,command_id,request_hash,components)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (str(uuid4()), project, release, target, actor["name"], command, request, Jsonb(components))).fetchone()
            self._event(c, job["id"], actor["name"], "requested", {"release": release, "components": components})
            return plain(job)

    @staticmethod
    def _event(c, job, actor, event, evidence):
        c.execute("INSERT INTO deployment_events(job_id,actor,event,evidence) VALUES (%s,%s,%s,%s)",
                  (job, actor, event, Jsonb(evidence)))

    def pending(self, token, target, boot):
        with self.repo.connection() as c:
            self._target(c, token, target, boot=boot)
            # A failed older command blocks later commands until the engineer
            # retries it, so recovery cannot accidentally roll a target backwards.
            row = c.execute("SELECT * FROM deployment_jobs WHERE target_id=%s AND state NOT IN ('delivered','cancelled') "
                            "ORDER BY created,id LIMIT 1", (target,)).fetchone()
            return [plain(row)] if row and row["state"] != "failed" else []

    def acknowledge(self, token, target, boot, job, state, receipt):
        if state not in {"staged", "delivered", "failed"}:
            raise ConfigurationError("Invalid deployment acknowledgment")
        if len(json.dumps(receipt)) > 1024 * 1024:
            raise ConfigurationError("Deployment receipt is too large")
        with self.repo.connection() as c:
            actor, _ = self._target(c, token, target, boot=boot, lock=True)
            row = c.execute("SELECT * FROM deployment_jobs WHERE id=%s AND target_id=%s FOR UPDATE",
                            (identifier(job), target)).fetchone()
            if not row:
                raise Missing("Deployment job does not belong to this runtime")
            if row["state"] == state and row["receipt"] == receipt:
                return plain(row)
            if row["state"] == "cancelled" and state == "failed" and row["receipt"] == receipt:
                return plain(row)  # A failure receipt lost before the engineer cancelled it.
            if row["state"] in {"delivered", "failed", "cancelled"} or (state == "staged" and row["state"] != "requested"):
                raise Conflict("Deployment job already completed or requires an engineer retry")
            if state == "delivered":
                release = c.execute("SELECT manifest FROM releases WHERE id=%s", (row["release_id"],)).fetchone()["manifest"]
                if receipt.get("package_hash") != release["package_hash"] or sorted(receipt.get("components", [])) != row["components"]:
                    raise Conflict("Acknowledgment does not match the requested package and components")
            result = c.execute("UPDATE deployment_jobs SET state=%s,receipt=%s,modified=clock_timestamp() WHERE id=%s RETURNING *",
                               (state, Jsonb(receipt), job)).fetchone()
            self._event(c, job, actor["name"], state, {"boot": boot, **receipt})
            return plain(result)

    def retry(self, token, project, job):
        with self.repo.connection() as c:
            actor, _ = self.editing._project(c, token, project, edit=True)
            row = c.execute("SELECT * FROM deployment_jobs WHERE id=%s AND project_id=%s FOR UPDATE",
                            (identifier(job), project)).fetchone()
            if not row:
                raise Missing("Deployment job does not exist")
            if row["state"] in {"delivered", "cancelled"}:
                raise Conflict("This deployment was already delivered or cancelled")
            if row["state"] == "failed":
                c.execute("UPDATE deployment_jobs SET state='requested',attempt=attempt+1,modified=clock_timestamp() WHERE id=%s", (job,))
                self._event(c, job, actor["name"], "retry", {"attempt": row["attempt"] + 1})
        return {"retry": str(job)}

    def cancel(self, token, project, job):
        with self.repo.connection() as c:
            actor, _ = self.editing._project(c, token, project, edit=True)
            row = c.execute("SELECT * FROM deployment_jobs WHERE id=%s AND project_id=%s FOR UPDATE",
                            (identifier(job), project)).fetchone()
            if not row:
                raise Missing("Deployment job does not exist")
            if row["state"] == "cancelled":
                return {"cancelled": str(job)}
            if row["state"] != "failed":
                raise Conflict("Only a failed deployment can be cancelled; an active download must finish first")
            c.execute("UPDATE deployment_jobs SET state='cancelled',modified=clock_timestamp() WHERE id=%s", (job,))
            self._event(c, job, actor["name"], "cancelled", {"reason": "Engineer cancelled failed delivery; existing target changes remain reported"})
        return {"cancelled": str(job)}

    def state(self, token, project):
        with self.repo.connection(snapshot=True) as c:
            _, current = self.editing._project(c, token, project)
            releases = c.execute("SELECT id,number,actor,reason,manifest,created FROM releases WHERE project_id=%s ORDER BY number DESC", (project,)).fetchall()
            targets = c.execute("SELECT id,name,boot,heartbeat,report,online AND heartbeat > clock_timestamp()-interval '30 seconds' AS fresh "
                                "FROM runtime_targets WHERE project_id=%s ORDER BY name", (project,)).fetchall()
            jobs = c.execute("SELECT * FROM deployment_jobs WHERE project_id=%s ORDER BY created DESC LIMIT 200", (project,)).fetchall()
            events = c.execute("SELECT e.* FROM deployment_events e JOIN deployment_jobs j ON e.job_id=j.id WHERE j.project_id=%s ORDER BY e.id DESC LIMIT 500", (project,)).fetchall()
            objects = c.execute("SELECT id,path,kind,revision,digest FROM objects WHERE project_id=%s AND NOT deleted ORDER BY path", (project,)).fetchall()
            comparison = []
            for target in targets:
                observations = {str(row.get("object_id")): row for row in target["report"].get("objects", [])}
                for obj in objects:
                    if not deployable(obj):
                        continue
                    observation = observations.get(str(obj["id"]), {}) if target["fresh"] else {}
                    active = observation.get("active", False)
                    status = "Unknown" if not target["fresh"] else "Not loaded"
                    if observation:
                        status = ("Current" if observation.get("digest") == obj["digest"] else "Different") if active else "Available / inactive"
                        if status == "Current" and observation.get("parameters_hash") != observation.get("loaded_parameters"):
                            status = "Online changes"
                    comparison.append({"target": target["name"], "target_id": str(target["id"]), **obj,
                                       "configured": obj["revision"], "running": observation.get("revision") if active else None,
                                       "status": status, "observation": observation})
            return plain({"project": current, "releases": releases, "targets": targets,
                          "jobs": jobs, "events": events, "objects": objects, "comparison": comparison})
