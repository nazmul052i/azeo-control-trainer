"""Versioned Azeo profiles for core primitives and comparison-catalog blocks."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
import json

import yaml


CATALOG_BLOCK_NAMESPACE = "azeo.procedure.catalog."
_LEGACY_CATALOG_BLOCK_NAMESPACE = "azeo.procedure." + "exa" + "pilot."


def canonical_block_id(block_id: str) -> str:
    """Return the current Azeo identity for a persisted library block."""
    if block_id.startswith(_LEGACY_CATALOG_BLOCK_NAMESPACE):
        return CATALOG_BLOCK_NAMESPACE + block_id.removeprefix(
            _LEGACY_CATALOG_BLOCK_NAMESPACE
        )
    return block_id


@dataclass(frozen=True)
class ProcedureBlock:
    block_id: str
    version: str
    step_type: str
    label: str
    category: str
    icon: str
    description: str
    source_id: str
    source_version: str
    _template_json: str

    def instantiate(self, step_id: str) -> dict:
        # Each insertion owns its nested values; editing one cannot alter the palette.
        return dict(json.loads(self._template_json), id=step_id,
                    library_block_id=self.block_id, library_block_version=self.version)

    @property
    def help_key(self) -> str:
        return self.step_type if self.block_id == f"azeo.procedure.{self.step_type}" else self.block_id


@lru_cache(maxsize=1)
def _source_blocks() -> dict[str, dict]:
    source = {}
    for name in ("core_blocks.yaml", "consolidated_catalog.yaml"):
        document = yaml.safe_load(files("azeo_control_trainer.core.pa_designer").joinpath("library", "definitions", name).read_text(encoding="utf-8"))
        source.update((entry["block_id"], entry) for entry in document["blocks"])
    return source


@lru_cache(maxsize=1)
def core_block_library() -> tuple[ProcedureBlock, ...]:
    source = _source_blocks()
    # These profiles describe the Azeo caller, not the upstream simulator's wider
    # capabilities. In particular a generic watchdog runs once, while the named
    # monitor profiles below remain active for their configured duration.
    profiles = (
        ("instruction", "Instruction", "Operator", "comment", "Present an instruction and wait for acknowledgement."),
        ("operator_confirm", "Operator confirmation", "Operator", "compile", "Wait for the operator to confirm before continuing."),
        ("operator_input", "Operator input", "Operator", "values", "Collect a typed value in a declared procedure variable."),
        ("operator_comment", "Operator comment", "Operator", "comment", "Require an observation and record it in the run history."),
        ("check", "Check condition", "Conditions", "compile", "Check once. True passes; False applies the failure action."),
        ("permissive", "Prerequisite check", "Conditions", "compile", "Check once. True grants the prerequisite; False blocks progression."),
        ("watchdog", "Envelope check", "Conditions", "diagnostics", "Check the envelope once at this step; a violation records a procedure alarm and applies the failure action."),
        ("wait_until", "Wait for condition", "Timing", "pause", "Wait for a condition and continuous dwell using simulation time, bounded by a timeout."),
        ("delay", "Delay", "Timing", "pause", "Wait for the configured number of simulation seconds."),
        ("write_tag", "Governed output", "Data", "params", "Apply a confirmed value through the Operator Station's checked-write service; a later wait verifies actual readback."),
        ("ramp_tag", "Governed ramp", "Data", "params", "Apply an operator-authorized bounded ramp through checked writes and verify actual readback."),
        ("read_tag", "Read parameter", "Data", "values", "Read a Good-quality mapped parameter into a declared variable."),
        ("calculate", "Calculate", "Data", "params", "Evaluate a restricted expression and store its result in a declared variable."),
        ("user_event", "Record event", "Data", "datalog", "Record a named event and its detail in the procedure history."),
        ("subprocedure", "Reusable procedure", "Composition", "step_block", "Run a pinned library revision with typed input parameters, tag aliases and returned results."),
        ("warning", "Procedure warning", "Run control", "comment", "Record a procedure warning and continue. Add confirmation when acknowledgement is required."),
        ("alarm", "Procedure alarm", "Run control", "diagnostics", "Record a procedure alarm and continue. Add a hold or confirmation to stop progression."),
        ("hold", "Hold", "Run control", "pause", "Hold the procedure for operator review."),
        ("abort", "Abort", "Run control", "deactivate", "End the procedure as aborted and record the reason."),
        ("complete", "Complete", "Run control", "compile", "Mark successful completion. This must be the final step."),
    )
    blocks = []
    for kind, label, category, mark, description in profiles:
        original = source[f"core.{kind}"]
        template = dict(original["template"])
        template.update(description=label, audible=False)
        # A hidden generic display_text would mask the engineer's instruction.
        template.pop("display_text", None)
        if kind == "wait_until":
            template.update(stable_for_sec=0, poll_sec=0.1)
        blocks.append(ProcedureBlock(
            f"azeo.procedure.{kind}", "1.0.0", kind, label, category, mark, description,
            original["block_id"], original["version"], json.dumps(template)))
    return tuple(blocks)


_CATALOG_TEMPLATE_OVERRIDES = {
    "equipment.pump_start": {"require_confirmation": True, "feedback_tag": "PUMP.RUNNING"},
    "equipment.pump_stop": {"require_confirmation": True, "feedback_tag": "PUMP.RUNNING"},
    "equipment.set_valve_opening": {"require_confirmation": True, "feedback_tag": "VALVE.POS"},
    "equipment.ramp_up_down": {"require_confirmation": True, "feedback_tag": "LOOP.SP"},
    "pcs.set_data": {"require_confirmation": True, "feedback_tag": "TAG.SP"},
    "pcs.block_mode_setting": {
        "tag": "LOOP.MODE.TARGET",
        "feedback_tag": "LOOP.MODE.ACTUAL",
        "require_confirmation": True,
    },
    "flow.user_event_wait": {
        "condition": "True",
        "event_name": "procedure_event",
    },
    "flow.pause": {"resume_prompt": "Review the condition, then resume the procedure."},
    "message.hmi_window": {"hmi_target": "process_overview"},
    "monitor.process_value": {"monitor_duration_sec": 60},
    "monitor.device_state": {"monitor_duration_sec": 60},
    "integration.legacy_script": {
        "description": "Evaluate the reviewed restricted migration expression.",
        "expression": "0",
        "variable": "script_result",
        "display_text": "Execute the reviewed safe-expression unit.",
    },
    "integration.user_application": {
        "description": "Invoke the reviewed, version-pinned application adapter.",
        "adapter_id": "azeo.identity",
        "adapter_version": "1.0.0",
        "adapter_inputs": {},
        "adapter_results": {},
        "display_text": "Execute the reviewed application adapter.",
    },
    "integration.activex_opc_com": {
        "description": "Use the governed host connector or reviewed adapter.",
        "integration_operation": "read",
        "tag": "TAG.PV",
        "variable": "connector_value",
        "display_text": "Read through the governed host connector.",
    },
}

_CATALOG_DESCRIPTION_OVERRIDES = {
    "pcs.set_data": (
        "Applies a typed setpoint, parameter or output through the operator-authorized "
        "checked-write boundary."
    ),
    "message.hmi_alarm": (
        "Raises an auditable procedure-origin notification in the shared HMI alarm registry."
    ),
    "message.hmi_window": (
        "Requests operator-approved navigation to a published display or mapped equipment faceplate."
    ),
    "integration.legacy_script": (
        "Executes a reviewed restricted expression as the safe legacy-script migration block."
    ),
    "integration.user_application": (
        "Executes an exact-version allowlisted adapter with bounded inputs, outputs and runtime."
    ),
    "integration.activex_opc_com": (
        "Uses governed host reads, checked writes or an allowlisted adapter without ActiveX or COM execution."
    ),
}


@lru_cache(maxsize=1)
def comparison_block_library() -> tuple[ProcedureBlock, ...]:
    """Every imported comparison block as a first-class Azeo palette entry."""
    document = yaml.safe_load(
        files("azeo_control_trainer.core.pa_designer")
        .joinpath("library", "definitions", "consolidated_catalog.yaml")
        .read_text(encoding="utf-8")
    )
    marks = {block.step_type: block.icon for block in core_block_library()}
    blocks = []
    for entry in document["blocks"]:
        template = dict(entry["template"])
        template.update(_CATALOG_TEMPLATE_OVERRIDES.get(entry["block_id"], {}))
        blocks.append(
            ProcedureBlock(
                f"{CATALOG_BLOCK_NAMESPACE}{entry['block_id']}",
                "1.0.0",
                entry["step_type"],
                entry.get("palette_label") or entry["name"],
                "Procedure catalog · " + entry["category"],
                marks[entry["step_type"]],
                _CATALOG_DESCRIPTION_OVERRIDES.get(
                    entry["block_id"], entry["description"]
                ),
                entry["block_id"],
                entry["version"],
                json.dumps(template),
            )
        )
    return tuple(blocks)


@lru_cache(maxsize=1)
def block_library() -> tuple[ProcedureBlock, ...]:
    return core_block_library() + comparison_block_library()


def block_for_type(kind: str) -> ProcedureBlock:
    return next(block for block in core_block_library() if block.step_type == kind)


def block_for_id(block_id: str) -> ProcedureBlock:
    block_id = canonical_block_id(block_id)
    return next(block for block in block_library() if block.block_id == block_id)


def block_for_source(source_id: str) -> ProcedureBlock:
    return next(block for block in comparison_block_library() if block.source_id == source_id)


def block_for_token(token: str) -> ProcedureBlock:
    token = canonical_block_id(token)
    block = next((row for row in block_library() if row.block_id == token), None)
    return block if block is not None else block_for_type(token)


def block_for_step(step) -> ProcedureBlock:
    block_id = step.get("library_block_id", "") if isinstance(step, dict) else step.library_block_id
    if block_id:
        canonical_id = canonical_block_id(block_id)
        block = next((row for row in block_library() if row.block_id == canonical_id), None)
        if block is not None:
            return block
    kind = step["type"] if isinstance(step, dict) else step.type
    return block_for_type(kind)


def validate_block_identity(step) -> None:
    if not step.library_block_id.startswith("azeo.procedure."):
        return  # Legacy and imported documents retain their original provenance.
    canonical_id = canonical_block_id(step.library_block_id)
    block = next((block for block in block_library() if block.block_id == canonical_id), None)
    if block is None or (block.version, block.step_type) != (step.library_block_version, step.type):
        raise ValueError(f"Step {step.id}: unknown or mismatched Azeo block identity/version")


def parameter_specs(kind: str, block: ProcedureBlock | None = None):
    """The inspector and block reference expose the same supported parameters."""
    specs = [("id", "Step ID", "text", ""), ("description", "Instruction", "multiline", "")]
    source_id = block.source_id if block else ""
    if kind in {"instruction", "operator_confirm", "operator_input", "warning", "alarm", "hold"}:
        specs += [("display_text", "Operator prompt (optional)", "multiline", "")]
    if kind in {"instruction", "operator_confirm", "warning", "alarm", "hold", "ramp_tag"}:
        specs += [("operator_guidance", "Operator guidance", "multiline", "")]
    if kind in {"check", "permissive", "wait_until", "watchdog"}:
        specs += [("condition", "Condition", "text", "")]
    if kind == "wait_until":
        specs += [("timeout_sec", "Timeout (simulation s)", "number", 60),
                  ("poll_sec", "Poll interval (simulation s)", "number", 0.1),
                  ("stable_for_sec", "Continuous dwell (s)", "number", 0),
                  ("timer_tuning", "Operator timer tuning", ("none", "next_run", "live"), "none"),
                  ("on_timeout", "On timeout", ("fail", "hold", "abort"), "fail")]
    if kind in {"write_tag", "ramp_tag", "read_tag"}:
        specs += [("tag", "Logical tag", "text", "")]
    if kind == "write_tag":
        specs += [("value", "Requested value", "scalar", ""),
                  ("feedback_tag", "Actual feedback tag", "text", ""),
                  ("require_confirmation", "Confirm before output", "bool", True)]
    if kind == "ramp_tag":
        specs += [("start", "Start value", "number", 0),
                  ("end", "End value", "number", 1),
                  ("rate_per_sec", "Rate per simulation second", "number", 1),
                  ("poll_sec", "Update interval (simulation s)", "number", 1),
                  ("feedback_tag", "Actual feedback tag", "text", ""),
                  ("require_confirmation", "Confirm before output", "bool", True)]
    if kind in {"read_tag", "calculate", "operator_input"}:
        specs += [("variable", "Result variable", "text", "")]
    if kind == "calculate":
        specs += [("expression", "Expression", "text", "")]
    if kind == "operator_input":
        specs += [("input_type", "Input type", ("float", "int", "bool", "str", "selection"), "float"),
                  ("choices", "Choices (one per line)", "choices", []),
                  ("min_value", "Minimum (optional)", "optional_number", None),
                  ("max_value", "Maximum (optional)", "optional_number", None)]
    if kind == "operator_comment":
        specs += [("comment_prompt", "Comment prompt", "multiline", "")]
    if kind == "delay":
        specs += [("delay_sec", "Delay (simulation s)", "number", 1)]
    if kind == "user_event":
        specs += [("event_name", "Event name", "text", ""), ("event_payload", "Event detail", "scalar", "")]
    if kind == "subprocedure":
        specs += [("subprocedure_path", "Library revision", "text", ""),
                  ("prefix", "Step prefix (optional)", "text", ""),
                  ("parameters", "Input parameters (JSON)", "scalar", {}),
                  ("tag_aliases", "Tag aliases (JSON)", "scalar", {}),
                  ("result_variables", "Returned results (JSON)", "scalar", {})]
    if source_id == "flow.user_event_wait":
        specs += [("event_name", "User event name", "text", "procedure_event")]
    if source_id == "flow.pause":
        specs += [("resume_prompt", "Resume prompt", "multiline", "Review, then resume.")]
    if source_id == "message.hmi_window":
        specs += [("hmi_target", "Display or equipment target", "text", "")]
    if source_id in {"monitor.process_value", "monitor.device_state"}:
        specs += [("monitor_duration_sec", "Monitoring duration (simulation s)", "number", 60),
                  ("poll_sec", "Poll interval (simulation s)", "number", 1)]
    if source_id in {"field.confirmation", "message.confirmation"}:
        specs += [("timeout_sec", "Response timeout (simulation s)", "number", 60)]
    if source_id == "integration.legacy_script":
        specs += [("expression", "Reviewed safe expression", "text", "0"),
                  ("variable", "Result variable", "text", "script_result")]
    if source_id == "integration.user_application":
        specs += [("adapter_id", "Registered adapter", "text", "azeo.identity"),
                  ("adapter_version", "Adapter version", "text", "1.0.0"),
                  ("adapter_timeout_sec", "Adapter timeout (wall s)", "number", 10),
                  ("adapter_inputs", "Adapter inputs (JSON)", "scalar", {}),
                  ("adapter_results", "Result mapping (JSON)", "scalar", {})]
    if source_id == "integration.activex_opc_com":
        specs += [("integration_operation", "Host operation", ("read", "write", "adapter"), "read"),
                  ("tag", "Logical tag", "text", ""),
                  ("variable", "Read result variable", "text", ""),
                  ("value", "Write value", "scalar", ""),
                  ("feedback_tag", "Actual feedback tag", "text", ""),
                  ("adapter_id", "Registered adapter", "text", ""),
                  ("adapter_version", "Adapter version", "text", "1.0.0"),
                  ("adapter_timeout_sec", "Adapter timeout (wall s)", "number", 10),
                  ("adapter_inputs", "Adapter inputs (JSON)", "scalar", {}),
                  ("adapter_results", "Result mapping (JSON)", "scalar", {})]
    if kind in {"warning", "alarm", "watchdog", "hold", "abort"}:
        specs += [("severity", "Severity", ("info", "warning", "alarm", "critical"), "info")]
    if kind in {"instruction", "operator_confirm", "warning", "alarm", "hold"}:
        specs += [("audible", "Audible notification", "bool", False)]
    if kind in {"instruction", "warning", "alarm"}:
        specs += [("require_confirmation", "Require acknowledgement", "bool", False)]
    specs += [("unit_procedure", "Unit procedure", "text", ""),
              ("document_link", "Controlled document", "text", ""),
              ("video_link", "Controlled video", "text", "")]
    specs += [("skip_if", "Already satisfied (skip if)", "text", ""),
              ("operator_skip_policy", "Operator skip", ("never", "reason"), "never"),
              ("on_failure", "On failure", ("fail", "hold", "abort"), "fail"),
              ("section", "Section", "text", ""), ("equipment", "Equipment", "text", ""),
              ("notes", "Author notes", "multiline", "")]
    return specs


__all__ = [
    "CATALOG_BLOCK_NAMESPACE",
    "ProcedureBlock",
    "block_for_id",
    "block_for_source",
    "block_for_step",
    "block_for_token",
    "block_for_type",
    "block_library",
    "canonical_block_id",
    "comparison_block_library",
    "core_block_library",
    "parameter_specs",
    "validate_block_identity",
]
