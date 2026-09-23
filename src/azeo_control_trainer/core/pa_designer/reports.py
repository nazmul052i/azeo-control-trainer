from __future__ import annotations

import json
import io
import os
import secrets
from hashlib import sha256
from pathlib import Path
from typing import Any
from datetime import datetime

from .core.procedure_model import Procedure, Step
from .core.validator import validate_procedure_contract, referenced_tags
from .storage.sqlite_store import SQLiteStore


def build_run_report(store: SQLiteStore, run_id: str) -> dict[str, Any]:
    report = store.run_report_snapshot(run_id)
    if report is None:
        raise ValueError(f"Run ID not found: {run_id}")
    if not report["audit_integrity"]["valid"]:
        raise RuntimeError(
            f"Run report blocked because audit integrity failed: "
            f"{report['audit_integrity']['message']}"
        )
    return report


def build_run_integrity_manifest(
    report: dict[str, Any], store: SQLiteStore | None = None
) -> dict[str, str]:
    canonical = json.dumps(
        report, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        default=str, allow_nan=False,
    )
    digest = sha256(canonical.encode("utf-8")).hexdigest()
    manifest = {"algorithm": "SHA-256", "digest": digest}
    if store is not None:
        manifest["authentication"] = "HMAC-SHA-256"
        manifest["signature"] = store.sign_report_digest(digest)
    return manifest


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _write_integrity_sidecar(path: Path, text: str, store: SQLiteStore) -> Path:
    digest = sha256(text.encode("utf-8")).hexdigest()
    manifest = {
        "algorithm": "SHA-256",
        "digest": digest,
        "authentication": "HMAC-SHA-256",
        "signature": store.sign_report_digest(digest),
    }
    sidecar = path.with_suffix(path.suffix + ".integrity.json")
    _atomic_write_text(sidecar, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return sidecar


def verify_exported_report(path: str | Path, store: SQLiteStore) -> tuple[bool, str]:
    """Verify a JSON report manifest or a Markdown/CSV integrity sidecar."""

    report_path = Path(path)
    if report_path.suffix.casefold() == ".json" and not report_path.name.endswith(".integrity.json"):
        try:
            document = json.loads(report_path.read_text(encoding="utf-8"))
            manifest = document.pop("integrity")
            digest = build_run_integrity_manifest(document)["digest"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            return False, "JSON report or integrity manifest is invalid"
    else:
        sidecar = report_path.with_suffix(report_path.suffix + ".integrity.json")
        try:
            manifest = json.loads(sidecar.read_text(encoding="utf-8"))
            digest = sha256(report_path.read_bytes()).hexdigest()
        except (OSError, json.JSONDecodeError, TypeError):
            return False, "Report integrity sidecar is missing or invalid"
    if not isinstance(manifest, dict):
        return False, "Report integrity manifest is not an object"
    if manifest.get("algorithm") != "SHA-256" or manifest.get("digest") != digest:
        return False, "Report digest does not match"
    if manifest.get("authentication") != "HMAC-SHA-256" or not store.verify_report_digest(
        digest, str(manifest.get("signature") or "")
    ):
        return False, "Report signature does not match"
    return True, "Report integrity verified"


def build_sop_markdown(procedure: Procedure) -> str:
    """Generate a controlled SOP-style document from an executable procedure."""

    report = validate_procedure_contract(procedure)
    lines: list[str] = []
    lines.append(f"# {procedure.name}")
    lines.append("")
    lines.append("## Document Control")
    lines.append("")
    lines.append(f"- Procedure ID: `{procedure.procedure_id}`")
    lines.append(f"- Unit: {procedure.unit or 'Unspecified'}")
    lines.append(f"- Mode: {procedure.mode.value}")
    lines.append(f"- Version: {procedure.metadata.version}")
    lines.append(f"- Approval: {procedure.metadata.approval_status}")
    lines.append(f"- Safety class: {procedure.metadata.safety_class}")
    lines.append(f"- Owner: {procedure.metadata.owner or 'Unspecified'}")
    lines.append(f"- Approved by: {procedure.metadata.approved_by or 'Unapproved'}")
    lines.append(f"- Approved at: {procedure.metadata.approved_at or 'Unspecified'}")
    lines.append(f"- Released by: {procedure.metadata.released_by or 'Unreleased'}")
    lines.append(f"- Released at: {procedure.metadata.released_at or 'Unspecified'}")
    if procedure.description:
        lines.extend(["", "## Purpose", "", procedure.description])

    lines.extend(["", "## Safety Boundary", ""])
    lines.append("- Live DCS/PLC writes are not enabled by this release.")
    lines.append("- Simulator write execution requires approval status, safety class, role, confirmation, and tag-limit checks.")
    lines.append("- Advisory or trial execution records write proposals without forwarding them to a live endpoint.")

    lines.extend(["", "## Execution and Recovery Policy", ""])
    lines.append(f"- Trigger mode: {procedure.trigger.mode}")
    lines.append(f"- Trigger condition: `{procedure.trigger.condition or 'manual'}`")
    lines.append(f"- Maximum cycles: {procedure.trigger.max_cycles}")
    lines.append(f"- Recovery mode: {procedure.recovery.mode}")
    lines.append(f"- Readiness recheck required: {'yes' if procedure.recovery.require_readiness_recheck else 'no'}")
    lines.append(f"- Recovery instructions: {procedure.recovery.instructions}")
    lines.append(f"- Live connectivity: {'read-only' if procedure.connectivity.read_only else 'write capable'}")
    lines.append(f"- Connectivity profile: {procedure.connectivity.profile or 'Unbound'}")

    lines.extend(["", "## Controlled Source References", ""])
    if procedure.references:
        lines.append("| ID | Type | Title | Revision | Required | Source Hash |")
        lines.append("|---|---|---|---|---|---|")
        for reference in procedure.references:
            lines.append(
                f"| `{reference.reference_id}` | {reference.kind} | {_escape_md(reference.title)} | "
                f"{reference.revision or '-'} | {'yes' if reference.required else 'no'} | "
                f"`{reference.source_hash[:16] or '-'}` |"
            )
    else:
        lines.append("No procedure-level controlled source references are declared.")

    lines.extend(["", "## Validation Summary", ""])
    lines.append(f"- Status: {'VALID' if report.ok else 'INVALID'}")
    lines.append(f"- Referenced tags: {len(referenced_tags(procedure))}")
    lines.append(f"- Declared tags: {len(procedure.tags)}")
    if report.errors:
        lines.append("- Errors:")
        lines.extend(f"  - {item}" for item in report.errors)
    if report.warnings:
        lines.append("- Warnings:")
        lines.extend(f"  - {item}" for item in report.warnings)

    lines.extend(["", "## Tag Contract", ""])
    if procedure.tags:
        lines.append("| Tag | Access | Type | Initial | Limits | Description |")
        lines.append("|---|---|---|---|---|---|")
        for tag in procedure.tags:
            limits = ""
            if tag.min_value is not None or tag.max_value is not None:
                limits = f"{tag.min_value if tag.min_value is not None else ''} .. {tag.max_value if tag.max_value is not None else ''}"
            lines.append(f"| {tag.tag} | {tag.access} | {tag.data_type} | `{tag.initial_value!r}` | {limits} | {_escape_md(tag.description)} |")
    else:
        lines.append("No explicit tag contract is declared.")

    lines.extend(["", "## Operator Pre-run Checklist", ""])
    checklist = [
        "Correct procedure revision selected",
        "Unit and equipment lineup confirmed",
        "Readiness dashboard shows acceptable tag quality and freshness",
        "Role authorization confirmed for selected mode",
        "Abort path and manual takeover plan understood",
        "Communication with field operator established",
    ]
    lines.extend(f"- [ ] {item}" for item in checklist)

    plan = build_run_plan(procedure)
    lines.extend(["", "## Modular Procedure Structure", ""])
    for section in plan:
        lines.append(f"- {section['name']}: {section['step_count']} step(s)")
        for unit in section["unit_procedures"]:
            lines.append(f"  - {unit['name']}: {unit['step_count']} step(s)")

    if procedure.flow:
        lines.extend(["", "## Execution Topology", ""])
        lines.append("| Node | Kind | Action / Condition | Completion |")
        lines.append("|---|---|---|---|")
        for node in procedure.flow.nodes:
            action = node.step_id or node.condition or node.label
            lines.append(f"| `{node.id}` | {node.kind} | {_escape_md(action or '')} | {node.completion_mode} |")
        lines.extend(["", "### Flow Edges", ""])
        lines.append("| Source | Target | Guard / Outcome | Priority |")
        lines.append("|---|---|---|---:|")
        for edge in procedure.flow.edges:
            route = edge.condition or ("default" if edge.is_default else edge.outcome)
            lines.append(f"| `{edge.source}` | `{edge.target}` | {_escape_md(route)} | {edge.priority} |")

    lines.extend(["", "## Procedure Flow", ""])
    lines.append("| # | Section | Step | Type | Equipment | Action / Check | Branching |")
    lines.append("|---:|---|---|---|---|---|---|")
    for index, step in enumerate(procedure.steps, start=1):
        lines.append(
            f"| {index} | {_escape_md(_step_section(step))} | `{step.id}` | {step.type} | {_escape_md(step.equipment)} | {_escape_md(_step_action_text(step))} | {_escape_md(_step_branch_text(step))} |"
        )

    tracked_steps = [step for step in procedure.steps if step.library_block_id]
    if tracked_steps:
        lines.extend(["", "## Block Library Traceability", ""])
        lines.append("| Step | Library block | Version |")
        lines.append("|---|---|---|")
        for step in tracked_steps:
            lines.append(f"| `{step.id}` | `{step.library_block_id}` | {step.library_block_version or '-'} |")

    if procedure.governance_history:
        lines.extend(["", "## Governance Record", ""])
        lines.append("| Time | Transition | Actor | Role | Reason | Content Hash |")
        lines.append("|---|---|---|---|---|---|")
        for record in procedure.governance_history:
            lines.append(
                f"| {record.occurred_at} | {record.from_status} -> {record.to_status} | "
                f"{_escape_md(record.actor)} | {_escape_md(record.role)} | {_escape_md(record.reason)} | "
                f"`{record.content_hash[:16]}` |"
            )

    lines.extend(["", "## Revision Note", "", procedure.metadata.revision_note or "No revision note recorded."])
    return "\n".join(lines) + "\n"


def export_sop_markdown(procedure: Procedure, path: str | Path) -> Path:
    path = Path(path)
    _atomic_write_text(path, build_sop_markdown(procedure))
    return path


def build_run_plan(procedure: Procedure) -> list[dict[str, Any]]:
    """Return a section/unit-procedure summary for UI and SOP views."""

    sections: list[dict[str, Any]] = []
    section_lookup: dict[str, dict[str, Any]] = {}
    for index, step in enumerate(procedure.steps, start=1):
        section_name = _step_section(step)
        section = section_lookup.get(section_name)
        if section is None:
            section = {"name": section_name, "step_count": 0, "steps": [], "unit_procedures": []}
            section_lookup[section_name] = section
            sections.append(section)
        unit_name = step.unit_procedure or section_name
        unit = next((item for item in section["unit_procedures"] if item["name"] == unit_name), None)
        if unit is None:
            unit = {"name": unit_name, "step_count": 0, "steps": []}
            section["unit_procedures"].append(unit)
        row = {
            "index": index,
            "id": step.id,
            "type": step.type,
            "description": step.description,
            "equipment": step.equipment,
            "display_text": step.display_text,
            "operator_guidance": step.operator_guidance,
            "document_link": step.document_link,
            "video_link": step.video_link,
            "library_block_id": step.library_block_id,
            "library_block_version": step.library_block_version,
        }
        section["steps"].append(row)
        section["step_count"] += 1
        unit["steps"].append(row)
        unit["step_count"] += 1
    return sections


def _escape_md(value: str) -> str:
    return value.replace("|", "\\|")


def _step_section(step: Step) -> str:
    return step.section or step.unit_procedure or "Main Procedure"


def _step_action_text(step: Step) -> str:
    parts: list[str] = []
    if step.description:
        parts.append(step.description)
    if step.display_text:
        parts.append(f"display: {step.display_text}")
    if step.operator_guidance:
        parts.append(f"guidance: {step.operator_guidance}")
    if step.condition:
        parts.append(f"condition: {step.condition}")
    if step.expression:
        parts.append(f"{step.variable} = {step.expression}")
    if step.event_name:
        parts.append(f"event: {step.event_name}")
    if step.skip_if:
        parts.append(f"skip if: {step.skip_if}")
    if step.tag:
        if step.type == "ramp_tag":
            parts.append(f"{step.tag}: {step.start} -> {step.end} at {step.rate_per_sec}/s")
        elif step.type == "read_tag":
            parts.append(f"{step.tag} -> {step.variable}")
        else:
            parts.append(f"{step.tag} := {step.value!r}")
    if step.delay_sec is not None:
        parts.append(f"delay {step.delay_sec}s")
    if step.comment_prompt:
        parts.append(f"comment prompt: {step.comment_prompt}")
    if step.document_link:
        parts.append(f"document: {step.document_link}")
    if step.video_link:
        parts.append(f"video: {step.video_link}")
    return "; ".join(parts) or step.type


def _step_branch_text(step: Step) -> str:
    branches = []
    if step.goto_on_pass:
        branches.append(f"pass -> {step.goto_on_pass}")
    if step.goto_on_fail:
        branches.append(f"fail -> {step.goto_on_fail}")
    if step.goto_on_timeout:
        branches.append(f"timeout -> {step.goto_on_timeout}")
    if step.on_timeout != "fail":
        branches.append(f"on timeout: {step.on_timeout}")
    if step.on_failure != "fail":
        branches.append(f"on failure: {step.on_failure}")
    return "; ".join(branches) or "next step"


def export_json_report(store: SQLiteStore, run_id: str, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    report = build_run_report(store, run_id)
    report["integrity"] = build_run_integrity_manifest(report, store)
    _atomic_write_text(path, json.dumps(report, indent=2, allow_nan=False) + "\n")
    return path


def export_markdown_report(store: SQLiteStore, run_id: str, path: str | Path) -> Path:
    report = build_run_report(store, run_id)
    integrity = build_run_integrity_manifest(report, store)
    run = report["run"]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append(f"# Procedure Run Report")
    lines.append("")
    lines.append(f"- Run ID: `{run['run_id']}`")
    lines.append(f"- Procedure: `{run['procedure_id']}` - {run['name']}")
    lines.append(f"- Status: **{run['status']}**")
    lines.append(f"- Started: {run['started_at']}")
    lines.append(f"- Ended: {run.get('ended_at') or ''}")
    lines.append(f"- Metadata: `{json.dumps(run.get('metadata_json', {}))}`")
    lines.append(f"- Integrity: `{integrity['algorithm']} {integrity['digest']}`")
    lines.append(f"- Authentication: `{integrity.get('authentication', 'none')} {integrity.get('signature', '')}`")
    lines.append(f"- Audit chain: **{'VALID' if report['audit_integrity']['valid'] else 'INVALID'}** - {report['audit_integrity']['message']}")
    lines.append("")
    lines.append("## Step Events")
    lines.append("")
    lines.append("| Time | Step | Type | Status | Message |")
    lines.append("|---|---|---|---|---|")
    for step in report["steps"]:
        msg = (step.get("message") or "").replace("\n", " ").replace("|", "\\|")
        lines.append(f"| {step['created_at']} | {step['step_id']} | {step['step_type']} | {step['status']} | {msg} |")
    lines.append("")
    lines.append("## Runtime Events")
    lines.append("")
    lines.append("| Time | Event | Step | Message | Data |")
    lines.append("|---|---|---|---|---|")
    for event in report["events"]:
        msg = (event.get("message") or "").replace("\n", " ").replace("|", "\\|")
        data = json.dumps(event.get("data_json", {})).replace("|", "\\|")
        lines.append(f"| {event['created_at']} | {event['event_type']} | {event.get('step_id') or ''} | {msg} | `{data}` |")
    lines.append("")
    lines.append("## Operator Messages")
    lines.append("")
    lines.append("| Occurred | Acknowledged | Operator | Severity | Step | Message | Comment |")
    lines.append("|---|---|---|---|---|---|---|")
    for message in report["operator_messages"]:
        text = (message.get("message") or "").replace("\n", " ").replace("|", "\\|")
        comment = (message.get("acknowledgement_comment") or "").replace("\n", " ").replace("|", "\\|")
        lines.append(
            f"| {message['occurred_at']} | {message.get('acknowledged_at') or ''} | "
            f"{message.get('acknowledged_by') or ''} | {message['severity']} | "
            f"{message.get('step_id') or ''} | {text} | {comment} |"
        )
    lines.append("")
    lines.append("## Tag Writes")
    lines.append("")
    lines.append("| Time | Tag | Value |")
    lines.append("|---|---|---|")
    for write in report["tag_writes"]:
        lines.append(f"| {write['created_at']} | {write['tag']} | `{json.dumps(write['value_json'])}` |")
    lines.append("")
    markdown = "\n".join(lines) + "\n"
    _atomic_write_text(path, markdown)
    _write_integrity_sidecar(path, markdown, store)
    return path



def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def build_metrics(store: SQLiteStore, run_id: str) -> dict[str, Any]:
    report = build_run_report(store, run_id)
    run = report["run"]
    started = _parse_time(run["started_at"])
    ended = _parse_time(run["ended_at"]) if run.get("ended_at") else None
    duration = (ended - started).total_seconds() if ended else None
    step_counts: dict[str, int] = {}
    active_times: dict[str, str] = {}
    step_durations: dict[str, float] = {}
    actual_path: list[str] = []
    for step in report["steps"]:
        step_counts[step["status"]] = step_counts.get(step["status"], 0) + 1
        if step["status"] == "ACTIVE" and step["step_id"] not in active_times:
            active_times[step["step_id"]] = step["created_at"]
            actual_path.append(step["step_id"])
        elif step["status"] in {"PASSED", "FAILED", "SKIPPED", "WARNING", "ALARM", "HELD", "ABORTED"}:
            active_at = active_times.get(step["step_id"])
            if active_at and step["step_id"] not in step_durations:
                step_durations[step["step_id"]] = (
                    _parse_time(step["created_at"]) - _parse_time(active_at)
                ).total_seconds()
    messages = report["operator_messages"]
    unacknowledged = [message for message in messages if not message.get("acknowledged_at")]
    branch_events = [event for event in report["events"] if event["event_type"] == "BRANCH"]
    designed_steps: list[str] = []
    source_path = (run.get("metadata_json") or {}).get("source_path")
    if source_path and Path(source_path).is_file():
        try:
            from .core.procedure_loader import load_procedure

            designed_steps = [step.id for step in load_procedure(source_path, validate_contract=False).steps]
        except Exception:
            designed_steps = []
    return {
        "run_id": run_id,
        "procedure_id": run["procedure_id"],
        "status": run["status"],
        "duration_sec": duration,
        "step_event_count": len(report["steps"]),
        "runtime_event_count": len(report["events"]),
        "tag_write_count": len(report["tag_writes"]),
        "operator_message_count": len(messages),
        "unacknowledged_message_count": len(unacknowledged),
        "branch_count": len(branch_events),
        "step_status_counts": step_counts,
        "step_durations_sec": step_durations,
        "actual_path": actual_path,
        "designed_step_count": len(designed_steps),
        "unexecuted_designed_steps": [step_id for step_id in designed_steps if step_id not in set(actual_path)],
        "latest_checkpoint": report["latest_checkpoint"],
    }


def export_csv_report(store: SQLiteStore, run_id: str, path: str | Path) -> Path:
    import csv
    report = build_run_report(store, run_id)
    path = Path(path)
    def safe_cell(value: Any) -> Any:
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\0")):
            return "'" + value
        return value

    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["section", "time", "step_or_tag", "type", "status_or_value", "message"])
    rows: list[list[Any]] = []
    rows.extend(["step", step["created_at"], step["step_id"], step["step_type"], step["status"], step.get("message") or ""] for step in report["steps"])
    rows.extend(["event", event["created_at"], event.get("step_id") or "", event["event_type"], "", event.get("message") or ""] for event in report["events"])
    rows.extend([
        "operator_message", message["occurred_at"], message.get("step_id") or "",
        message["event_type"], message.get("acknowledged_by") or "UNACKNOWLEDGED",
        message.get("message") or "",
    ] for message in report["operator_messages"])
    rows.extend(["tag_write", write["created_at"], write["tag"], "write", json.dumps(write.get("value_json")), ""] for write in report["tag_writes"])
    writer.writerows([[safe_cell(cell) for cell in row] for row in rows])
    csv_text = output.getvalue()
    _atomic_write_text(path, csv_text)
    _write_integrity_sidecar(path, csv_text, store)
    return path


def build_replay_timeline(store: SQLiteStore, run_id: str) -> list[dict[str, Any]]:
    report = build_run_report(store, run_id)
    events: list[dict[str, Any]] = []
    for step in report["steps"]:
        events.append({"time": step["created_at"], "kind": "step", "label": step["step_id"], "detail": step["status"], "message": step.get("message") or ""})
    for event in report["events"]:
        events.append({"time": event["created_at"], "kind": "event", "label": event.get("step_id") or "", "detail": event["event_type"], "message": event.get("message") or ""})
    for write in report["tag_writes"]:
        events.append({"time": write["created_at"], "kind": "tag_write", "label": write["tag"], "detail": write.get("value_json"), "message": ""})
    for message in report["operator_messages"]:
        events.append({
            "time": message["occurred_at"],
            "kind": "operator_message",
            "label": message.get("step_id") or "",
            "detail": message["event_type"],
            "message": message.get("message") or "",
        })
        if message.get("acknowledged_at"):
            events.append({
                "time": message["acknowledged_at"],
                "kind": "acknowledgement",
                "label": message.get("step_id") or "",
                "detail": message.get("acknowledged_by") or "",
                "message": message.get("acknowledgement_comment") or "",
            })
    return sorted(events, key=lambda x: x["time"])
