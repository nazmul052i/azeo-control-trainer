"""Executable comparison inventory for Procedure Automation.

The inventory records equivalent outcomes. ``COMPOSED`` means a catalog
function is built from smaller reviewed Azeo blocks or a shared product
subsystem.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from importlib.resources import files

import yaml


class ParityStatus(str, Enum):
    IMPLEMENTED = "IMPLEMENTED"
    COMPOSED = "COMPOSED"
    PARTIAL = "PARTIAL"
    MISSING = "MISSING"
    DEPARTURE = "DEPARTURE"


@dataclass(frozen=True)
class ProcedureCapability:
    key: str
    group: str
    title: str
    status: ParityStatus
    implementation: str
    note: str = ""


_BLOCK_OVERRIDES = {
    "equipment.ramp_up_down": (
        ParityStatus.IMPLEMENTED,
        "azeo_control_trainer.core.procedures.runtime:TrainerProcedureEngine._step_ramp_tag",
        "One operator authorization covers a bounded schedule applied through checked Operator Station writes; actual feedback remains mandatory.",
    ),
    "flow.loop_repeat": (
        ParityStatus.IMPLEMENTED,
        "azeo_control_trainer.core.procedures.flow:AdvisoryFlowNode",
        "A bounded Retry node plus guarded connections implements repetition.",
    ),
    "flow.pause": (
        ParityStatus.IMPLEMENTED,
        "azeo_control_trainer.core.procedures.runtime:TrainerProcedureEngine._step_hold",
        "The authored Pause freezes procedure time, records pause/resume evidence and waits for fresh observations before continuing.",
    ),
    "message.hmi_window": (
        ParityStatus.IMPLEMENTED,
        "azeo_control_trainer.core.procedures.runtime:TrainerProcedureEngine._step_instruction",
        "The operator accepts a validated published-display or mapped-equipment request before the station opens it.",
    ),
    "message.hmi_alarm": (
        ParityStatus.IMPLEMENTED,
        "azeo_control_trainer.core.procedures.runtime:TrainerProcedureEngine._step_alarm",
        "Procedure alarms enter the shared operator alarm registry under a PROCEDURE source without impersonating controller or SIS alarms.",
    ),
    "integration.user_application": (
        ParityStatus.IMPLEMENTED,
        "azeo_control_trainer.core.procedures.adapters:execute_adapter",
        "Version-pinned allowlisted adapters have bounded data, concurrency and wall time and publish type-checked results; documents cannot dynamically import code.",
    ),
    "integration.legacy_script": (
        ParityStatus.IMPLEMENTED,
        "azeo_control_trainer.core.procedures.runtime:TrainerProcedureEngine._step_instruction",
        "The executable migration block uses the reviewed safe-expression runtime; arbitrary VBScript and Office automation remain excluded.",
    ),
    "integration.activex_opc_com": (
        ParityStatus.IMPLEMENTED,
        "azeo_control_trainer.core.procedures.runtime:TrainerProcedureEngine._step_instruction",
        "Read/write operations use declared host connector bindings and checked outputs; reviewed adapters replace ActiveX/COM automation.",
    ),
}


def _default_block_status(entry):
    note = entry.get("revision_note", "")
    if entry["step_type"] == "write_tag":
        note = "First-class block using authorized checked writes and mandatory actual feedback verification."
    return (
        ParityStatus.IMPLEMENTED,
        "azeo_control_trainer.core.procedures.library:comparison_block_library",
        note,
    )


@lru_cache(maxsize=1)
def block_capabilities() -> tuple[ProcedureCapability, ...]:
    document = yaml.safe_load(
        files("azeo_control_trainer.core.pa_designer")
        .joinpath("library", "definitions", "consolidated_catalog.yaml")
        .read_text(encoding="utf-8")
    )
    rows = []
    for entry in document["blocks"]:
        status, implementation, note = _BLOCK_OVERRIDES.get(
            entry["block_id"], _default_block_status(entry)
        )
        rows.append(
            ProcedureCapability(
                "block." + entry["block_id"],
                "Blocks",
                entry["name"],
                status,
                implementation,
                note,
            )
        )
    return tuple(rows)


_FEATURES = (
    ("builder.visual_flow", "Builder", "Icon-based visual flow authoring", "IMPLEMENTED", "azeo_control_trainer.azeo_pa_designer.canvas:ProcedureCanvas", "Linear and advanced graph authoring share one executable document."),
    ("builder.hierarchy", "Builder", "Main, group, subprocedure and unit-procedure hierarchy", "COMPOSED", "azeo_control_trainer.core.procedures.composition:build_definition", "Pinned reusable revisions and step scopes provide the hierarchy."),
    ("builder.templates", "Builder", "Reusable procedure modules and templates", "IMPLEMENTED", "azeo_control_trainer.azeo_pa_designer.new_procedure:procedure_from_template", "Guided creation templates and extracted selections produce native pinned procedure revisions."),
    ("builder.validation", "Builder", "Tag, type, variable and topology validation", "IMPLEMENTED", "azeo_control_trainer.azeo_pa_designer.problems:collect_findings", "Live structured diagnostics navigate to steps, flow nodes, connections, tags and properties."),
    ("builder.drawings", "Builder", "Free-form drawing annotations", "IMPLEMENTED", "azeo_control_trainer.azeo_pa_designer.canvas:ProcedureAnnotation", "Non-executable notes, phases, rectangles and swimlanes retain text, color, size and position."),
    ("builder.refactoring", "Builder", "Find usages and safe symbol rename", "IMPLEMENTED", "azeo_control_trainer.azeo_pa_designer.refactoring:rename_symbol", "AST-aware refactoring updates tags, variables and step identities without rewriting strings."),
    ("builder.revision_compare", "Builder", "Semantic revision compare and impact", "IMPLEMENTED", "azeo_control_trainer.azeo_pa_designer.revisions:compare_documents", "Procedure, workflow, step, tag, memory and mapping changes identify affected execution surfaces."),
    ("builder.self_document", "Builder", "Self-documenting printable SOP", "IMPLEMENTED", "azeo_control_trainer.core.procedures.documentation:export_sop", "Exports printable HTML or Markdown from the validated executable revision."),
    ("operation.live_progress", "Operation", "Live chart progress and current-step location", "IMPLEMENTED", "azeo_control_trainer.azeo_operator_station.procedure_workflow:ProcedureWorkflow", "Node, edge, branch and active-call state are displayed live."),
    ("operation.values", "Operation", "Operation data and procedure-variable display", "IMPLEMENTED", "azeo_control_trainer.azeo_operator_station.procedure_session:ProcedureSession.snapshot", "Quality-aware process values, memory, conditions and tuning are exposed."),
    ("operation.controls", "Operation", "Start, pause/resume and stop/abort", "IMPLEMENTED", "azeo_control_trainer.azeo_operator_station.procedure_session:ProcedureSession.execute", "Commands require current rendered context, authority and fresh observations."),
    ("operation.skip_break", "Operation", "Operator skip and break controls", "IMPLEMENTED", "azeo_control_trainer.core.pa_designer.core.execution_engine:ProcedureExecutionEngine._run_step", "Authored guidance can require an operator Continue/Skip decision with a reason; Break pauses a run with audited actor and reason. Outputs and required verification are not skippable."),
    ("operation.messages", "Operation", "Guidance, confirmation, alarm and comments", "IMPLEMENTED", "azeo_control_trainer.core.pa_designer.core.execution_engine:ProcedureExecutionEngine", "Prompts, typed answers, comments and severity-classified messages are audited."),
    ("operation.output_confirmation", "Operation", "PCS output confirmation", "IMPLEMENTED", "azeo_control_trainer.core.procedures.outputs:authorize_tag_output", "Explicit authorization occurs before the first live checked write; authored confirmation also gates isolated-trial output."),
    ("operation.concurrent_runs", "Operation", "Multiple simultaneous main procedures", "IMPLEMENTED", "azeo_control_trainer.azeo_operator_station.procedure_supervisor:ProcedureSupervisor", "Up to eight independent run contexts share one audit store; run-specific tokens and prompts isolate commands, while overlapping output/shared-memory targets are reserved."),
    ("operation.offline_trial", "Operation", "Offline trial with preset inputs and internal outputs", "IMPLEMENTED", "azeo_control_trainer.azeo_pa_designer.window:PADesignerWindow.run_isolated_trial", "A deterministic in-memory provider cannot reach SharedDataStore or a controller."),
    ("operation.live_input_trial", "Operation", "Trial with live inputs and suppressed outputs", "IMPLEMENTED", "azeo_control_trainer.core.procedures.runtime:CapturedTagProvider", "The runtime can consume captured live observations while output execution is disabled and journal requests without writing."),
    ("operation.automatic_trigger", "Operation", "Condition and continuous automatic triggers", "DEPARTURE", "azeo_control_trainer.core.pa_designer.core.procedure_model:TriggerPolicy", "The model imports trigger policy, but Azeo requires an explicit operator start for governed training runs."),
    ("operation.online_maintenance", "Operation", "Edit a subprocedure during an active run", "DEPARTURE", "azeo_control_trainer.core.procedures.composition:bundle_dependencies", "Active runs pin immutable dependency snapshots so evidence cannot drift during execution."),
    ("operation.dynamic_links", "Operation", "Dynamic messages, references and equipment links", "IMPLEMENTED", "azeo_control_trainer.azeo_operator_station.procedure_actions:dispatch", "Controlled document/video references, mapped equipment and current values are available."),
    ("monitoring.advanced_alarm", "Monitoring", "Advanced process and equipment monitoring", "COMPOSED", "azeo_control_trainer.core.procedures.flow_runtime:FlowRuntime", "Watchdogs, alarm blocks, bounded loops and parallel branches compose monitoring logic; SIS remains outside PA."),
    ("connectivity.opc", "Connectivity", "PCS connectivity including OPC UA", "COMPOSED", "azeo_control_trainer.connectivity.opcua", "The shared host connector owns OPC UA; procedures bind logical tags through the Tag DB instead of creating private OPC sessions."),
    ("connectivity.multiple_servers", "Connectivity", "Multiple PCS/server mappings", "COMPOSED", "azeo_control_trainer.core.procedures.authoring:ProcedureDraft", "A revision may map tags across host-managed modules and transports while keeping one read boundary."),
    ("governance.security", "Governance", "Role-based operating authority", "IMPLEMENTED", "azeo_control_trainer.azeo_operator_station.procedure_session:ProcedureSession.execute", "View-only stations cannot start, respond, tune or abort procedures."),
    ("governance.audit", "Governance", "Audit trail, messages and output history", "IMPLEMENTED", "azeo_control_trainer.core.procedures.audit:ProcedureStore", "Hash-chained run, read, output, response and tuning evidence is retained."),
    ("governance.reports", "Governance", "Run reports and comparison", "IMPLEMENTED", "azeo_control_trainer.core.procedures.reports:format_report", "Operator Station reviews, compares and exports signed run reports."),
    ("governance.backup", "Governance", "Procedure save, restore and revision history", "IMPLEMENTED", "azeo_control_trainer.core.procedures.authoring:ProcedureDraft.save_revision", "Immutable project revisions and bundled dependencies replace mutable database records."),
    ("governance.release", "Governance", "Review, approval and release evidence", "IMPLEMENTED", "azeo_control_trainer.core.procedures.governance:transition_revision", "Separated actors advance a SHA-256-bound, hash-chained sidecar without rewriting immutable procedure payloads."),
    ("capacity.nesting", "Capacity", "Deep subprocedure nesting", "DEPARTURE", "azeo_control_trainer.core.procedures.composition:build_definition", "Azeo caps call depth at 8 rather than the legacy 300-level product maximum."),
    ("legacy.excel", "Legacy options", "Excel add-in and workbook automation", "DEPARTURE", "", "Spreadsheet automation is not an execution dependency; data is exported in open formats."),
    ("legacy.email", "Legacy options", "Direct email notification", "DEPARTURE", "", "Procedure execution does not send external messages without a separately governed adapter."),
    ("legacy.vb_activex", "Legacy options", "VBScript, ActiveX and COM extension units", "DEPARTURE", "azeo_control_trainer.core.pa_designer.core.expression_engine:SafeExpressionEvaluator", "Arbitrary desktop code execution is excluded by the restricted-runtime boundary."),
    ("legacy.word", "Legacy options", "Word self-document output", "COMPOSED", "azeo_control_trainer.core.procedures.documentation:export_sop", "Printable HTML and Markdown replace proprietary Word automation."),
)


def feature_capabilities() -> tuple[ProcedureCapability, ...]:
    return tuple(
        ProcedureCapability(key, group, title, ParityStatus(status), implementation, note)
        for key, group, title, status, implementation, note in _FEATURES
    )


def capability_inventory() -> tuple[ProcedureCapability, ...]:
    return block_capabilities() + feature_capabilities()


def markdown_inventory() -> str:
    lines = [
        "# Procedure automation parity status",
        "",
        "Generated from `core/procedures/capabilities.py`. `COMPOSED` means the operator outcome is built from reviewed Azeo primitives or a shared product subsystem; `DEPARTURE` is an intentional product/security decision.",
        "",
    ]
    for group in dict.fromkeys(row.group for row in capability_inventory()):
        lines.extend((f"## {group}", "", "| Capability | Status | Implementation note |", "| --- | --- | --- |"))
        for row in (item for item in capability_inventory() if item.group == group):
            note = row.note.replace("|", "\\|")
            lines.append(f"| `{row.key}` — {row.title} | {row.status.value} | {note} |")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    print(markdown_inventory())


__all__ = [
    "ParityStatus",
    "ProcedureCapability",
    "block_capabilities",
    "capability_inventory",
    "feature_capabilities",
    "markdown_inventory",
]
