"""Operator-facing summary of the existing, integrity-checked audit report."""
from __future__ import annotations


def format_report(report):
    run = report["run"]
    metadata = run.get("metadata_json", {})
    lines = [run["name"], f"Outcome: {run['status']}",
             f"Operator: {metadata.get('operator_identity', '')}",
             f"Revision: {metadata.get('version', '')}",
             f"Started (UTC): {run['started_at']}",
             f"Ended (UTC): {run.get('ended_at') or 'In progress'}", "", "Step results"]
    descriptions = {step["id"]: step.get("description", "")
                    for step in metadata.get("document", {}).get("procedure", {}).get("steps", [])}
    final_steps = {step["step_id"]: step for step in report["steps"]}
    for name, step in final_steps.items():
        lines.append(f"{step['status']} · {name}: {descriptions.get(name) or step.get('message') or step['step_type']}")
    lines.extend(("", "Operator responses and simulation timing"))
    for event in report["events"]:
        data = event.get("data_json", {})
        if event["event_type"] == "OPERATOR_COMMENT":
            lines.append(f"{event.get('step_id')}: {data.get('comment', '')}")
        elif event["event_type"] == "OPERATOR_INPUT":
            lines.append(f"{data.get('variable')}: {data.get('value')}")
        elif event["event_type"] == "TRAINER_STEP":
            lines.append(f"Simulation {data.get('sim_time')} s · Procedure {data.get('procedure_elapsed')} s · "
                         f"{data.get('step')} {data.get('status')}")
        elif event["event_type"] == "TRAINER_FINISHED":
            lines.append(data.get("message", ""))
    lines.extend(("", f"Audit integrity: {'Valid' if report['audit_integrity']['valid'] else 'Invalid'}",
                  f"Run: {run['run_id']}", f"Document digest: {metadata.get('document_sha256', '')}"))
    return "\n".join(lines)
