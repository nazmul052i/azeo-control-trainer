"""Immutable revision review evidence stored beside, never inside, the payload."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from azeo_control_trainer.core.pa_designer.core.yaml_loader import load_bounded_yaml_file


GOVERNANCE_FILE = "governance.json"
_NEXT = {"draft": "review", "review": "approved", "approved": "released"}


def _revision_dir(path: Path) -> Path:
    value = Path(path).resolve()
    directory = value.parent if value.is_file() else value
    if not (directory / "procedure.yaml").is_file():
        raise ValueError("Select a saved procedure revision")
    return directory


def revision_digest(path: Path) -> str:
    directory = _revision_dir(path)
    digest = hashlib.sha256()
    for file in sorted(row for row in directory.rglob("*") if row.is_file()
                       and row.name != GOVERNANCE_FILE and not row.name.startswith(".governance.")):
        if file.is_symlink() or not file.resolve().is_relative_to(directory):
            raise ValueError("Procedure revision cannot contain external or symbolic file references")
        relative = file.relative_to(directory).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        size = file.stat().st_size
        digest.update(size.to_bytes(8, "big"))
        consumed = 0
        with file.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                consumed += len(chunk)
                digest.update(chunk)
        if consumed != size:
            raise ValueError("Procedure revision changed while its digest was calculated")
    return digest.hexdigest()


def _event_hash(event: dict) -> str:
    material = {key: value for key, value in event.items() if key != "event_hash"}
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def read_governance(path: Path) -> dict:
    directory = _revision_dir(path)
    content_digest = revision_digest(directory)
    record_path = directory / GOVERNANCE_FILE
    if not record_path.exists():
        return {"schema_version": 1, "content_digest": content_digest, "status": "draft", "events": []}
    if record_path.is_symlink():
        raise ValueError("Procedure governance evidence cannot be a symbolic link")
    if record_path.stat().st_size > 2_000_000:
        raise ValueError("Procedure governance evidence exceeds the supported size")
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Procedure governance evidence is unreadable") from error
    if not isinstance(record, dict) or record.get("schema_version") != 1 or not isinstance(record.get("events"), list):
        raise ValueError("Procedure governance evidence has an unsupported format")
    if record.get("content_digest") != content_digest:
        raise ValueError("Procedure revision content changed after governance began")
    previous = ""
    status = "draft"
    for event in record["events"]:
        required = {"from_status", "to_status", "actor", "role", "reason", "occurred_at",
                    "content_digest", "previous_event_hash", "event_hash"}
        if not isinstance(event, dict) or not required <= event.keys():
            raise ValueError("Procedure governance event is incomplete")
        if event["content_digest"] != content_digest:
            raise ValueError("Procedure governance event is bound to different revision content")
        if event.get("previous_event_hash", "") != previous or event.get("event_hash") != _event_hash(event):
            raise ValueError("Procedure governance evidence chain is invalid")
        if event.get("from_status") != status or _NEXT.get(status) != event.get("to_status"):
            raise ValueError("Procedure governance transition history is invalid")
        previous = event["event_hash"]
        status = event["to_status"]
    if record.get("status") != status:
        raise ValueError("Procedure governance status does not match its evidence")
    return record


def transition_revision(path: Path, to_status: str, *, actor: str, role: str, reason: str = "") -> dict:
    directory = _revision_dir(path)
    lock = directory / ".governance.lock"
    try:
        with lock.open("x", encoding="utf-8") as stream:
            stream.write(f"Review transition started {datetime.now(timezone.utc).isoformat()}")
    except FileExistsError as error:
        raise ValueError("Another governance transition is active; close it or remove a confirmed stale .governance.lock") from error
    try:
        return _transition_locked(directory, to_status, actor=actor, role=role, reason=reason)
    finally:
        if lock.exists():
            lock.unlink()


def _transition_locked(directory: Path, to_status: str, *, actor: str, role: str, reason: str) -> dict:
    actor, role, reason = actor.strip(), role.strip(), reason.strip()
    if not actor or not role:
        raise ValueError("Actor and role are required for controlled review")
    record = read_governance(directory)
    from .authoring import ProcedureDraft
    ProcedureDraft.load(directory / "procedure.yaml", directory.parent).definition()
    current = record["status"]
    if _NEXT.get(current) != to_status:
        raise ValueError(f"The next controlled state after {current} is {_NEXT.get(current, 'none')}")
    procedure = load_bounded_yaml_file(directory / "procedure.yaml", label="Procedure")
    author = str(procedure.get("metadata", {}).get("author", "")).strip().casefold()
    previous_actors = {event["to_status"]: event["actor"].strip().casefold() for event in record["events"]}
    identity = actor.casefold()
    if to_status in {"approved", "released"} and author and identity == author:
        raise ValueError("The procedure author cannot approve or release their own revision")
    if to_status == "approved" and identity == previous_actors.get("review"):
        raise ValueError("Reviewer and approver must be different people")
    if to_status == "released" and identity == previous_actors.get("approved"):
        raise ValueError("Approver and releaser must be different people")
    if to_status in {"approved", "released"} and not reason:
        raise ValueError("Approval and release require a reason")
    event = {
        "from_status": current,
        "to_status": to_status,
        "actor": actor,
        "role": role,
        "reason": reason,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "content_digest": record["content_digest"],
        "previous_event_hash": record["events"][-1]["event_hash"] if record["events"] else "",
    }
    event["event_hash"] = _event_hash(event)
    record["events"].append(event)
    record["status"] = to_status
    temporary = directory / f".governance.{uuid4().hex}.pending"
    try:
        temporary.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
        if revision_digest(directory) != record["content_digest"]:
            raise ValueError("Procedure revision content changed during governance transition")
        temporary.replace(directory / GOVERNANCE_FILE)
    finally:
        if temporary.exists():
            temporary.unlink()
    return record
