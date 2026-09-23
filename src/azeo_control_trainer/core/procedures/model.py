"""Project procedures retain PA Designer's YAML and tag-mapping formats."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from azeo_control_trainer.core.pa_designer.connectors.tag_mapping import TagMappingTable
from azeo_control_trainer.core.pa_designer.core.procedure_model import Procedure, ProcedureMetadata, Step, StrictProcedureModel
from azeo_control_trainer.core.pa_designer.core.tag_references import referenced_tags_in_expression
from azeo_control_trainer.core.pa_designer.core.validator import validate_procedure_contract
from azeo_control_trainer.core.pa_designer.core.yaml_loader import load_bounded_yaml_file

from .library import block_for_step, canonical_block_id, validate_block_identity
from .logic import CalculationRow, ConditionRow, MemoryValue, expanded_condition, row_logic, validate_expression
from .flow import AdvisoryFlow, validate_flow


class AdvisoryStep(Step):
    operator_skip_policy: str = Field(default="never", pattern="^(never|reason)$")
    result_variables: dict[str, str] = Field(default_factory=dict)
    stable_for_sec: float = Field(default=0.0, ge=0)
    condition_rows: list[ConditionRow] = Field(default_factory=list, max_length=32)
    condition_match: str = Field(default="ALL", pattern="^(ALL|ANY)$")
    condition_logic: str = Field(default="", max_length=4096)
    calculation_rows: list[CalculationRow] = Field(default_factory=list, max_length=32)
    timer_tuning: str = Field(default="none", pattern="^(none|next_run|live)$")
    feedback_tag: str = ""
    resume_prompt: str = ""
    hmi_target: str = ""
    monitor_duration_sec: float = Field(default=0.0, ge=0)
    adapter_id: str = ""
    adapter_version: str = ""
    adapter_timeout_sec: float = Field(default=10.0, ge=0.1, le=60.0)
    adapter_inputs: dict[str, object] = Field(default_factory=dict)
    adapter_results: dict[str, str] = Field(default_factory=dict)
    integration_operation: str = Field(default="read", pattern="^(read|write|adapter)$")

    @model_validator(mode="before")
    @classmethod
    def derived_legacy_fields(cls, data):
        if isinstance(data, dict):
            data = dict(data)
            if data.get("library_block_id"):
                data["library_block_id"] = canonical_block_id(data["library_block_id"])
            if data.get("condition_rows"):
                rows = [ConditionRow.model_validate(row).model_dump() for row in data["condition_rows"]]
                data["condition"] = expanded_condition(rows, data.get("condition_match", "ALL"), data.get("condition_logic", ""))
            if data.get("calculation_rows") and data.get("type") == "calculate":
                first = CalculationRow.model_validate(data["calculation_rows"][0])
                data.update(expression=first.expression, variable=first.variable)
        return data

    @field_validator("stable_for_sec", "monitor_duration_sec", "adapter_timeout_sec")
    @classmethod
    def finite_dwell(cls, value):
        if not math.isfinite(value):
            raise ValueError("Stable dwell must be finite")
        return value


class CanvasRoutePoint(StrictProcedureModel):
    x: float = Field(ge=-1000000, le=1000000, allow_inf_nan=False)
    y: float = Field(ge=-1000000, le=1000000, allow_inf_nan=False)


class CanvasRoute(StrictProcedureModel):
    source: str = Field(min_length=1, max_length=512)
    target: str = Field(min_length=1, max_length=512)
    points: list[CanvasRoutePoint] = Field(max_length=64)


class AdvisoryMetadata(ProcedureMetadata):
    # Native linear procedures have no flow.edges to carry wire geometry.
    # Keep cosmetic routes beside the existing native canvas_layout metadata.
    canvas_routes: list[CanvasRoute] = Field(default_factory=list, max_length=5001)


class AdvisoryProcedure(Procedure):
    flow: AdvisoryFlow | None = None
    steps: list[AdvisoryStep] = Field(min_length=1, max_length=5000)
    metadata: AdvisoryMetadata = Field(default_factory=AdvisoryMetadata)
    variables: list[MemoryValue] = Field(default_factory=list)

    @model_validator(mode="after")
    def memory_inputs(self):
        memory = {row.name: row for row in self.variables}
        declared = self.declared_tag_map()
        paths = [v.tag_path for v in self.variables if v.tag_path]
        if len(paths) != len(set(paths)):
            raise ValueError("Use one variable alias for each shared memory tag")
        for step in self.steps:
            source_id = block_for_step(step).source_id
            if step.feedback_tag and step.feedback_tag not in declared:
                raise ValueError(f"Step {step.id} uses undeclared feedback tag {step.feedback_tag}")
            if step.type == "ramp_tag":
                duration = abs(step.end - step.start) / step.rate_per_sec
                updates = math.ceil(duration / step.poll_sec)
                if duration > 86400 or updates > 10000:
                    raise ValueError(
                        f"Ramp {step.id} exceeds 86400 simulation seconds or 10000 updates"
                    )
            if step.type == "operator_input" and step.variable in memory:
                value = memory[step.variable]
                if step.input_type == "selection":
                    for choice in step.choices:
                        value.checked(choice)
                elif value.data_type != "any" and step.input_type != value.data_type:
                    raise ValueError(f"Input {step.id} must use memory type {value.data_type}")
                lows = [v for v in (step.min_value, value.min_value) if v is not None]
                highs = [v for v in (step.max_value, value.max_value) if v is not None]
                step.min_value = max(lows) if lows else None
                step.max_value = min(highs) if highs else None
                if lows and highs and step.min_value > step.max_value:
                    raise ValueError(f"Input {step.id} limits do not intersect memory limits")
            result_names = set(step.adapter_results.values())
            if source_id == "integration.legacy_script" and step.variable:
                result_names.add(step.variable)
            if source_id == "integration.activex_opc_com" and step.integration_operation == "read" and step.variable:
                result_names.add(step.variable)
            missing_results = result_names - set(memory)
            if missing_results:
                raise ValueError(f"Step {step.id} uses undeclared result variables: {sorted(missing_results)}")
        return self


@dataclass(frozen=True)
class ProcedureDefinition:
    procedure: AdvisoryProcedure
    bindings: dict[str, str]
    authored_document: dict | None = None

    def validate(self, *, allow_calls=False):
        procedure = self.procedure
        validate_procedure_contract(procedure).raise_for_errors()
        if procedure.mode.value != "advisory":
            raise ValueError("Operator Station currently runs advisory procedures only")
        if procedure.trigger.mode != "manual":
            raise ValueError("Operator-guided procedures require an explicit manual start")
        validate_flow(procedure)
        declared = {tag.tag for tag in procedure.tags}
        if declared != set(self.bindings):
            raise ValueError("Every declared procedure tag needs exactly one explicit Trainer binding")
        for path in self.bindings.values():
            parts = path.split("/")
            if len(parts) not in {3, 4} or any(not part or part in {".", ".."} for part in parts):
                raise ValueError(f"Invalid Trainer parameter path: {path}")
            if len(parts) == 4 and parts[2] != "CONFIG":
                raise ValueError(f"Expected MODULE/BLOCK/CONFIG/PARAMETER: {path}")
        for index, step in enumerate(procedure.steps):
            validate_block_identity(step)
            source_id = block_for_step(step).source_id
            if step.operator_skip_policy != "never" and step.type not in {
                "instruction", "operator_confirm", "operator_comment"
            }:
                raise ValueError(
                    f"Operator skip at {step.id} is limited to authored guidance, confirmation or comments"
                )
            if step.timer_tuning != "none" and step.type != "wait_until":
                raise ValueError("Operator timer tuning requires a Wait for condition block")
            memory = {row.name: row for row in procedure.variables}
            if step.condition_rows:
                if step.type not in {"wait_until", "check", "permissive", "watchdog"}:
                    raise ValueError("Condition rows belong to condition blocks")
                row_logic(step.condition_rows, step.condition_match, step.condition_logic)
                if step.type != "wait_until" and any(row.stable_for_sec for row in step.condition_rows):
                    raise ValueError("Row hold timers require Wait for condition")
                for row in step.condition_rows:
                    validate_expression(row.expression, memory, procedure.declared_tag_map())
            if step.calculation_rows and step.type not in {"calculate", "wait_until"}:
                raise ValueError("Calculation rows belong to Calculate or Wait for condition")
            for row in step.calculation_rows:
                if row.variable not in memory:
                    raise ValueError(f"Undeclared result memory: {row.variable}")
                validate_expression(row.expression, memory, procedure.declared_tag_map())
            for expression in (step.condition, step.expression):
                if expression:
                    validate_expression(expression, memory, procedure.declared_tag_map())
            if source_id == "flow.user_event_wait" and not step.event_name.strip():
                raise ValueError("User Event Wait requires an event name")
            if source_id == "flow.pause" and not step.resume_prompt.strip():
                raise ValueError("Pause Procedure requires a resume prompt")
            if source_id == "message.hmi_window" and not (
                step.hmi_target.strip() or step.equipment.strip() or step.document_link.strip()
            ):
                raise ValueError("HMI Window Reference requires a display or equipment target")
            if source_id in {"monitor.process_value", "monitor.device_state"}:
                if not math.isfinite(step.monitor_duration_sec) or step.monitor_duration_sec <= 0:
                    raise ValueError("Continuous monitoring duration must be finite and positive")
            if source_id == "integration.legacy_script":
                if not step.expression or not step.variable:
                    raise ValueError("Legacy Script migration block requires a safe expression and result variable")
                if step.variable not in memory:
                    raise ValueError(f"Undeclared safe-script result variable: {step.variable}")
            if source_id == "integration.user_application":
                if not step.adapter_id or not step.adapter_version:
                    raise ValueError("User Application requires a registered adapter identity and version")
                from .adapters import require_adapter
                require_adapter(step.adapter_id, step.adapter_version)
                for value in step.adapter_inputs.values():
                    if isinstance(value, str) and value.startswith("="):
                        validate_expression(value[1:], memory, procedure.declared_tag_map())
                for result in step.adapter_results.values():
                    if result not in memory:
                        raise ValueError(f"Undeclared adapter result variable: {result}")
            if source_id == "integration.activex_opc_com":
                if step.integration_operation == "read" and (not step.tag or not step.variable):
                    raise ValueError("Host connector read requires a logical tag and result variable")
                if step.integration_operation == "write" and (not step.tag or step.value is None):
                    raise ValueError("Host connector write requires a logical tag and value")
                if step.integration_operation == "adapter" and (not step.adapter_id or not step.adapter_version):
                    raise ValueError("Host adapter operation requires an adapter identity and version")
                if step.integration_operation == "adapter":
                    from .adapters import require_adapter
                    require_adapter(step.adapter_id, step.adapter_version)
                    for value in step.adapter_inputs.values():
                        if isinstance(value, str) and value.startswith("="):
                            validate_expression(value[1:], memory, procedure.declared_tag_map())
            if step.type == "subprocedure" and not allow_calls:
                raise ValueError(f"{step.type} is not supported by this advisory integration yet")
            if step.result_variables and step.type != "subprocedure":
                raise ValueError("Result mappings belong to a reusable subprocedure call")
            if step.skip_if:
                validate_expression(step.skip_if, memory, procedure.declared_tag_map())
            if step.goto_on_pass or step.goto_on_fail or step.goto_on_timeout:
                raise ValueError("Use explicit workflow connections for branch outcomes")
            if step.on_failure in {"skip", "alarm"} or step.on_timeout == "alarm":
                raise ValueError("A failed required step must stop, hold or abort the run")
            if step.stable_for_sec and step.type != "wait_until":
                raise ValueError("Stable dwell belongs to a wait_until step")
            if step.type == "complete" and not procedure.flow and index != len(procedure.steps) - 1:
                raise ValueError("Complete must be the final step")
            requires_feedback = step.type in {"write_tag", "ramp_tag"} or (
                source_id == "integration.activex_opc_com" and step.integration_operation == "write"
            )
            feedback_tag = step.feedback_tag or step.tag
            if requires_feedback and not procedure.flow and not any(
                    later.type == "wait_until" and feedback_tag in referenced_tags_in_expression(later.condition or "False")
                    for later in procedure.steps[index + 1:]):
                raise ValueError(f"Output {step.id} requires a subsequent actual-value wait for {feedback_tag}")
        return self

    def document(self):
        if self.authored_document is not None:
            return self.authored_document
        return {"procedure": self.procedure.model_dump(mode="json"), "bindings": dict(self.bindings)}

    @property
    def digest(self):
        return hashlib.sha256(json.dumps(self.document(), sort_keys=True).encode()).hexdigest()


def load_definition(path: Path, library: Path) -> ProcedureDefinition:
    library, path = library.resolve(), path.resolve()
    if not path.is_relative_to(library):
        raise ValueError("Select a procedure inside this project's procedure library")
    procedure = AdvisoryProcedure.model_validate(load_bounded_yaml_file(path, label="Procedure"))
    mapping_path = (path.parent / procedure.connectivity.mapping_path).resolve()
    if not procedure.connectivity.mapping_path or not mapping_path.is_relative_to(library):
        raise ValueError("The procedure needs a mapping file inside its project library")
    table = TagMappingTable.from_yaml(mapping_path)
    from .composition import build_definition
    return build_definition(procedure, {row.logical_tag: row.connector_tag for row in table.rows}, path, library)


def loop_verification(loop: str, target: float, tolerance: float, dwell: float) -> ProcedureDefinition:
    if any(not math.isfinite(value) for value in (target, tolerance, dwell)) or tolerance <= 0 or dwell < 0:
        raise ValueError("Enter a finite target, positive tolerance and nonnegative dwell")
    procedure = AdvisoryProcedure.model_validate({
        "procedure_id": "loop_verification", "name": "Verify a flow loop", "unit": loop,
        "mode": "advisory", "metadata": {"version": "1.0.0", "safety_class": "advisory"},
        "tags": [
            {"tag": "LOOP.PV", "access": "read", "data_type": "float"},
            {"tag": "LOOP.SP", "access": "read", "data_type": "float"},
            {"tag": "LOOP.MODE", "access": "read", "data_type": "str"},
        ],
        "steps": [
            {"id": "lineup", "type": "instruction", "require_confirmation": True,
             "description": f"Review the starting condition for {loop}. Open its faceplate and confirm you are ready."},
            {"id": "auto", "type": "wait_until", "condition": "LOOP.MODE == 'AUTO'", "timeout_sec": 600,
             "description": "Use the faceplate to select AUTO. Waiting for actual AUTO mode."},
            {"id": "target", "type": "wait_until", "condition": f"abs(LOOP.SP - {target!r}) <= 0.0001",
             "timeout_sec": 600, "description": f"Use the faceplate to set SP to {target:g}. Waiting for actual setpoint readback."},
            {"id": "settled", "type": "wait_until", "condition": f"abs(LOOP.PV - {target!r}) <= {tolerance!r}",
             "stable_for_sec": dwell, "timeout_sec": max(600, dwell * 3), "poll_sec": 0.1,
             "description": f"Verify PV stays within {tolerance:g} EU of {target:g} for {dwell:g} simulation seconds."},
            {"id": "observations", "type": "operator_comment", "comment_prompt": "Record your observations.",
             "description": "Record the result of the loop verification."},
            {"id": "done", "type": "complete", "description": "Actual mode, setpoint and observed PV dwell verified."},
        ],
    })
    return ProcedureDefinition(procedure, {"LOOP.PV": f"{loop}/PV", "LOOP.SP": f"{loop}/SP",
                                           "LOOP.MODE": f"{loop}/MODE.ACTUAL"}).validate()
