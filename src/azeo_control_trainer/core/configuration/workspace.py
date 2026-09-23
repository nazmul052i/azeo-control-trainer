"""Recoverable, isolated working drafts; only check-in changes shared configuration."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4

from .catalog_client import _write
from .client import ConfigurationClient, read_profile
from .documents import ConfigurationError, Conflict, Forbidden, export_project, read_project, safe_path

MARKER = ".repository-draft.json"


def draft_root(path):
    path = Path(path).resolve()
    for candidate in (path, *path.parents):
        if (candidate / MARKER).is_file():
            return candidate
    return None


def reject_draft_deployment(path):
    if draft_root(path):
        raise ConfigurationError("This is an engineering draft workspace. Check-in does not deploy it; "
                                 "use Release Manager to validate and deploy a checked-in revision.")


def _scope(profile):
    return hashlib.sha256((profile["url"] + "\0" + profile["token"]).encode()).hexdigest()


def _digest(content):
    return hashlib.sha256(base64.b64decode(content)).hexdigest()


def _replace(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        temporary = Path(f.name)
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _historical(path):
    return any(part in {"versions", "revisions"} for part in path.split("/"))


class DraftWorkspace:
    """All methods performing service or filesystem work run on the UI's worker."""
    def __init__(self, directory, *, profile=None, client=None):
        self.directory = Path(directory).resolve()
        self.root = self.directory / "draft"
        self.profile = profile or read_profile()
        self.client = client or ConfigurationClient(**self.profile)
        self.meta = json.loads((self.directory / "workspace.json").read_text(encoding="utf-8"))
        if self.meta["scope"] != _scope(self.profile):
            raise ConfigurationError("This draft belongs to a different configuration-service identity")
        self.project = self.meta["project"]
        self.session = self.meta["session"]
        self.base = json.loads((self.directory / "base.json").read_text(encoding="utf-8"))
        self.remote = None

    @classmethod
    def create(cls, directory, project, *, profile=None, client=None):
        profile = profile or read_profile()
        client = client or ConfigurationClient(**profile)
        directory = Path(directory).resolve()
        if directory.exists():
            raise ConfigurationError("A new draft workspace needs a new directory")
        bundle = client.request(f"/v1/projects/{project}/export")
        if bundle["project"]["mode"] != "repository":
            raise ConfigurationError("Create an isolated editing pilot first")
        directory.mkdir(parents=True)
        export_project(bundle, directory / "draft")
        session = str(uuid4())
        _write(directory / "base.json", bundle)
        _write(directory / "workspace.json", {"project": project, "session": session,
                                              "scope": _scope(profile), "name": bundle["project"]["name"]})
        _write(directory / "draft" / MARKER, {"project": project, "session": session, "protocol": 1})
        return cls(directory, profile=profile, client=client)

    def request(self, action, payload=None):
        return self.client.request(f"/v1/projects/{self.project}/editing/{action}", payload)

    def _file(self, relative):
        path = self.root / safe_path(relative)
        if not path.resolve().is_relative_to(self.root):
            raise ConfigurationError("A working draft path points outside this workspace")
        return path

    def refresh(self):
        # Every reconnect reads the complete revision/lease inventory. Audit cursor
        # gaps cannot leave a missed rename or deletion hidden behind a stale badge.
        self.remote = self.request("state")
        return self.remote

    def reserve(self, paths):
        self.remote = self.request("lease", {"session": self.session, "paths": list(paths)})
        return self.remote

    def release(self, paths=()):
        return self.request("lease", {"session": self.session, "paths": list(paths), "release": True})

    def release_on_close(self):
        if isinstance(self.client, ConfigurationClient):
            client = ConfigurationClient(**self.profile, timeout=2)
            return client.request(f"/v1/projects/{self.project}/editing/lease",
                                  {"session": self.session, "paths": [], "release": True})
        return self.release()

    def heartbeat(self):
        state = self.refresh()
        paths = [r["path"] for r in state["leases"] if str(r["session"]) == self.session]
        return self.reserve(paths) if paths else state

    def changes(self):
        current = {f["path"]: f for f in read_project(self.root)["files"] if not _historical(f["path"])}
        previous = {f["path"]: f for f in self.base["files"] if not _historical(f["path"])}
        objects = {o["path"]: o for o in self.base["objects"]}
        edits = []
        for path in sorted(current.keys() | previous.keys()):
            if current.get(path) == previous.get(path):
                continue
            edits.append({"path": path, "new_path": path,
                          "expected_revision": objects.get(path, {}).get("revision", 0),
                          "content": current.get(path, {}).get("content")})
        return edits

    def preview(self):
        pending = self.pending()
        edits = pending["edits"] if pending else self.changes()
        if not edits:
            raise ConfigurationError("Save your Studio edits to the working draft before reviewing changes")
        return {"edits": edits, "review": self.request("preview", {"edits": edits})}

    def pending(self):
        path = self.directory / "pending.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def checkin(self, reviewed, reason):
        pending = self.pending()
        if pending is not None:
            return self.retry()
        if pending is None:
            if reviewed["edits"] != self.changes():
                raise Conflict("Draft files changed after review; review the new changes before checking in")
            pending = {"session": self.session, "edits": reviewed["edits"], "reason": reason,
                       "command_id": str(uuid4()), "expected_generation": reviewed["review"]["generation"]}
            # Persist before the request: a lost response can be retried after a crash
            # with exactly the same command, payload and revision expectations.
        paths = {e[k] for e in pending["edits"] for k in ("path", "new_path")}
        self.reserve(paths)
        _write(self.directory / "pending.json", pending)
        try:
            receipt = self.request("checkin", pending)
        except (Conflict, Forbidden):
            self.cancel_refused()
            raise
        if any(e["path"] != e["new_path"] for e in pending["edits"]):
            self._apply_renamed_files(pending["edits"])
        self._accept(receipt, pending["edits"])
        (self.directory / "pending.json").unlink(missing_ok=True)
        return receipt

    def retry(self):
        pending = self.pending()
        if not pending:
            raise ConfigurationError("There is no interrupted check-in to retry")
        # The server recognizes a committed command even if its old lease expired.
        try:
            receipt = self.request("checkin", pending)
        except (Conflict, Forbidden):
            self.cancel_refused()
            raise
        if any(e["path"] != e["new_path"] for e in pending["edits"]):
            self._apply_renamed_files(pending["edits"])
        self._accept(receipt, pending["edits"])
        (self.directory / "pending.json").unlink(missing_ok=True)
        return receipt

    def cancel_refused(self):
        """Call only after a definite rejection, never after a transport timeout."""
        (self.directory / "pending.json").unlink(missing_ok=True)

    def _accept(self, receipt, edits):
        objects = {o["path"]: o for o in self.base["objects"]}
        files = {f["path"]: f for f in self.base["files"]}
        for edit in edits:
            path, target = edit["path"], edit["new_path"]
            old = objects.pop(path, None)
            files.pop(path, None)
            if edit["content"] is not None:
                files[target] = {"path": target, "content": edit["content"]}
                if old:
                    objects[target] = {**old, "path": target}
        for obj in receipt["objects"]:
            objects[obj["path"]] = obj
        self.base["files"], self.base["objects"] = list(files.values()), list(objects.values())
        self.base["project"]["generation"] = receipt["generation"]
        _write(self.directory / "base.json", self.base)

    def comparison(self, path):
        path = safe_path(path)
        bundle = self.client.request(f"/v1/projects/{self.project}/export")
        base = next((f["content"] for f in self.base["files"] if f["path"] == path), None)
        remote = next((f["content"] for f in bundle["files"] if f["path"] == path), None)
        local = self._file(path)
        draft = base64.b64encode(local.read_bytes()).decode() if local.exists() else None
        return {"path": path, "base": base, "draft": draft, "current": remote,
                "object": next((o for o in bundle["objects"] if o["path"] == path), None)}

    def resolve(self, comparison, *, keep_draft=False):
        path = safe_path(comparison["path"])
        local = self._file(path)
        # Preserve a dated recovery copy before any explicit reload/rebase.
        recovery = self.directory / "recovery" / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f") / path
        draft = base64.b64encode(local.read_bytes()).decode() if local.exists() else None
        if draft != comparison["draft"]:
            raise Conflict("Local draft changed while the comparison was open")
        if local.exists():
            recovery.parent.mkdir(parents=True, exist_ok=True)
            recovery.write_bytes(local.read_bytes())
        if not keep_draft:
            if comparison["current"] is None:
                local.unlink(missing_ok=True)
            else:
                local.parent.mkdir(parents=True, exist_ok=True)
                _replace(local, base64.b64decode(comparison["current"]))
        self.base["files"] = [f for f in self.base["files"] if f["path"] != path]
        self.base["objects"] = [o for o in self.base["objects"] if o["path"] != path]
        if comparison["object"]:
            self.base["objects"].append(comparison["object"])
            self.base["files"].append({"path": path, "content": comparison["current"]})
        _write(self.directory / "base.json", self.base)

    def rename_preview(self, module, new_name):
        if self.changes() or self.pending():
            raise Conflict("Check in or resolve saved draft changes before reviewing a module rename")
        return self.request("rename", {"module": module, "new_name": new_name})

    def rename_checkin(self, plan, reason):
        if self.changes() or self.pending():
            raise Conflict("Draft changed during rename review; preserve it and review again")
        paths = {e[k] for e in plan["edits"] for k in ("path", "new_path")}
        self.reserve(paths)
        pending = {"session": self.session, "edits": plan["edits"], "reason": reason,
                   "command_id": str(uuid4()), "expected_generation": plan["generation"]}
        _write(self.directory / "pending.json", pending)
        try:
            receipt = self.request("checkin", pending)
        except (Conflict, Forbidden):
            self.cancel_refused()
            raise
        self._apply_renamed_files(pending["edits"])
        self._accept(receipt, pending["edits"])
        (self.directory / "pending.json").unlink(missing_ok=True)
        return receipt

    def _apply_renamed_files(self, edits):
        bases = {f["path"]: f["content"] for f in self.base["files"]}
        for edit in edits:
            current = self._file(edit["path"])
            target = self._file(edit["new_path"])
            draft = base64.b64encode(current.read_bytes()).decode() if current.exists() else None
            destination = base64.b64encode(target.read_bytes()).decode() if target.exists() else None
            changed = (draft not in {bases.get(edit["path"]), edit["content"]}
                       and not (draft is None and destination == edit["content"]))
            if current != target and destination not in {None, edit["content"]}:
                changed = True
            if changed:
                raise ConfigurationError("The shared rename committed, but newer local edits were preserved. "
                                         "Compare and recover those drafts before retrying its local update.")
        for edit in edits:
            old, target = self._file(edit["path"]), self._file(edit["new_path"])
            _replace(target, base64.b64decode(edit["content"]))
            if old != target:
                old.unlink(missing_ok=True)
