"""Self-documenting SOP output generated from the executable revision."""
from __future__ import annotations

from html import escape
from pathlib import Path

from .model import ProcedureDefinition


def _text(value) -> str:
    return str(value if value not in (None, "") else "—").replace("|", "\\|")


def format_sop_markdown(definition: ProcedureDefinition) -> str:
    procedure = definition.procedure
    metadata = procedure.metadata
    lines = [
        f"# {procedure.name}",
        "",
        procedure.description or "Executable operating procedure.",
        "",
        "## Document control",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Procedure ID | {_text(procedure.procedure_id)} |",
        f"| Revision | {_text(metadata.version)} |",
        f"| Owner | {_text(metadata.owner)} |",
        f"| Author | {_text(metadata.author)} |",
        f"| Approval | {_text(metadata.approval_status)} |",
        f"| Released by | {_text(metadata.released_by)} |",
        f"| Released at | {_text(metadata.released_at)} |",
        f"| Safety class | {_text(metadata.safety_class)} |",
        f"| Execution mode | {_text(procedure.mode.value)} |",
        f"| Content SHA-256 | `{definition.digest}` |",
        "",
        "## Procedure tags",
        "",
        "| Logical tag | Access | Type | Engineering binding | Description |",
        "| --- | --- | --- | --- | --- |",
    ]
    for tag in procedure.tags:
        lines.append(
            f"| {_text(tag.tag)} | {_text(tag.access)} | {_text(tag.data_type)} | "
            f"`{_text(definition.bindings.get(tag.tag))}` | {_text(tag.description)} |"
        )
    if not procedure.tags:
        lines.append("| — | — | — | — | No process tags declared |")
    lines.extend(("", "## Procedure variables", "", "| Name | Type | Initial value | Description |",
                  "| --- | --- | --- | --- |"))
    for variable in procedure.variables:
        lines.append(
            f"| {_text(variable.name)} | {_text(getattr(variable, 'data_type', 'any'))} | "
            f"{_text(variable.value)} | {_text(variable.description)} |"
        )
    if not procedure.variables:
        lines.append("| — | — | — | No procedure variables declared |")
    lines.extend(("", "## Executable steps", ""))
    for index, step in enumerate(procedure.steps, 1):
        lines.extend((f"### {index}. {step.id} — {step.type}", "", step.description or "No instruction text."))
        details = []
        for label, value in (
            ("Condition", step.condition), ("Tag", step.tag), ("Value", step.value),
            ("Expression", step.expression), ("Result variable", step.variable),
            ("Timeout", f"{step.timeout_sec:g} s" if step.type == "wait_until" else None),
            ("Failure action", step.on_failure if step.on_failure != "fail" else None),
            ("Already satisfied", step.skip_if), ("Equipment", step.equipment),
            ("Document", step.document_link), ("Video", step.video_link),
        ):
            if value not in (None, ""):
                details.append(f"- {label}: {_text(value)}")
        if step.type == "ramp_tag":
            details.append(
                f"- Ramp: {_text(step.start)} → {_text(step.end)} at "
                f"{_text(step.rate_per_sec)} per second; update every {_text(step.poll_sec)} s"
            )
        lines.extend(("", *(details or ["- No additional parameters"]), ""))
    if procedure.flow:
        lines.extend(("## Workflow connections", "", "| From | Outcome / condition | To |", "| --- | --- | --- |"))
        for edge in procedure.flow.edges:
            route = edge.condition or edge.label or edge.outcome
            lines.append(f"| {_text(edge.source)} | {_text(route)} | {_text(edge.target)} |")
        lines.append("")
    lines.extend(("## Controlled references", ""))
    if procedure.references:
        lines.extend(("| ID | Title | Revision | Path |", "| --- | --- | --- | --- |"))
        for reference in procedure.references:
            lines.append(
                f"| {_text(reference.reference_id)} | {_text(reference.title)} | "
                f"{_text(reference.revision)} | `{_text(reference.path)}` |"
            )
    else:
        lines.append("No controlled references declared.")
    lines.extend(("", "---", "", "Generated from the validated executable revision; do not edit this export as the source procedure.", ""))
    return "\n".join(lines)


def format_sop_html(definition: ProcedureDefinition) -> str:
    markdown = format_sop_markdown(definition)
    # A preformatted body keeps the export dependency-free and reliably
    # printable while preserving the exact generated Markdown evidence.
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>"
        + escape(definition.procedure.name)
        + "</title><style>body{font:14px/1.45 Segoe UI,Arial,sans-serif;max-width:980px;"
          "margin:32px auto;color:#202124}pre{white-space:pre-wrap;font:inherit}"
          "@media print{body{margin:12mm}}</style></head><body><pre>"
        + escape(markdown)
        + "</pre></body></html>"
    )


def export_sop(definition: ProcedureDefinition, path: str | Path) -> Path:
    target = Path(path)
    if target.suffix.lower() not in {".md", ".html", ".htm"}:
        raise ValueError("Procedure documentation must be Markdown or HTML")
    content = format_sop_markdown(definition) if target.suffix.lower() == ".md" else format_sop_html(definition)
    target.write_text(content, encoding="utf-8")
    return target


__all__ = ["export_sop", "format_sop_html", "format_sop_markdown"]
