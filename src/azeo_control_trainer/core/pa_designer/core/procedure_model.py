from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from .enums import ProcedureMode

StepType = Literal[
    "check",
    "permissive",
    "instruction",
    "wait_until",
    "write_tag",
    "read_tag",
    "calculate",
    "user_event",
    "ramp_tag",
    "operator_confirm",
    "operator_input",
    "operator_comment",
    "delay",
    "warning",
    "alarm",
    "hold",
    "abort",
    "complete",
    "watchdog",
    "subprocedure",
]
TagAccess = Literal["read", "write", "read_write"]
OnFailure = Literal["fail", "alarm", "hold", "abort", "skip"]
FlowNodeKind = Literal[
    "start",
    "action",
    "transition",
    "choice",
    "merge",
    "loop",
    "parallel_fork",
    "parallel_join",
    "end",
]
CompletionMode = Literal["process", "operator", "process_or_operator", "process_and_operator"]
FlowOutcome = Literal["always", "passed", "failed", "timeout", "warning", "alarm", "skipped"]


def _require_finite_values(value: Any, *, label: str) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{label} contains a non-finite numeric value")
    if isinstance(value, dict):
        for key, item in value.items():
            _require_finite_values(key, label=label)
            _require_finite_values(item, label=label)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            _require_finite_values(item, label=label)
    return value


def _require_relative_application_path(value: str, *, label: str) -> str:
    text = value.strip()
    if not text:
        return text
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} must remain within the procedure library")
    return text


class StrictProcedureModel(BaseModel):
    """Fail closed when a controlled YAML document contains an unknown key."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class CanvasNodeMetadata(StrictProcedureModel):
    step_id: str = Field(min_length=1, max_length=256)
    x: int
    y: int


class CanvasSectionMetadata(StrictProcedureModel):
    title: str = Field(min_length=1, max_length=256)
    x: int
    y: int
    width: int = Field(ge=120)
    height: int = Field(ge=48)
    move_contents: bool = False


class CanvasPageMetadata(StrictProcedureModel):
    title: str = Field(min_length=1, max_length=128)
    width: int = Field(default=1600, ge=800, le=6000)
    height: int = Field(default=2400, ge=600, le=8000)


class EngineeringAnnotation(StrictProcedureModel):
    """Non-executable engineering context retained with a procedure revision."""

    id: str = Field(min_length=1, max_length=128)
    kind: Literal["note", "phase", "rectangle", "swimlane"] = "note"
    text: str = Field(default="", max_length=2000)
    x: int = 0
    y: int = 0
    width: int = Field(default=300, ge=80, le=4000)
    height: int = Field(default=120, ge=40, le=4000)
    color: str = Field(default="#607D8B", pattern=r"^#[0-9A-Fa-f]{6}$")


class ProcedureMetadata(StrictProcedureModel):
    """Human/change-management metadata for a procedure revision."""

    version: str = "0.1.0"
    author: str = ""
    owner: str = ""
    approved_by: str = ""
    approved_at: str = ""
    reviewed_by: str = ""
    reviewed_at: str = ""
    released_by: str = ""
    released_at: str = ""
    approval_status: Literal["draft", "review", "approved", "released", "retired"] = "draft"
    revision_note: str = ""
    safety_class: Literal["training", "advisory", "semi_auto", "auto_sim_only"] = "training"
    created_at: str = ""
    modified_at: str = ""
    canvas_layout: list[CanvasNodeMetadata] = Field(default_factory=list, max_length=5000)
    section_layout: list[CanvasSectionMetadata] = Field(default_factory=list, max_length=500)
    canvas_page: CanvasPageMetadata | None = None
    annotations: list[EngineeringAnnotation] = Field(default_factory=list, max_length=500)


class ControlledReference(StrictProcedureModel):
    """Source material linked to a controlled procedure revision."""

    reference_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    path: str = Field(min_length=1)
    revision: str = ""
    kind: Literal["sop", "drawing", "datasheet", "video", "other"] = "sop"
    required: bool = True
    source_hash: str = ""

    @field_validator("path")
    @classmethod
    def bounded_path(cls, value: str) -> str:
        return _require_relative_application_path(value, label="controlled reference path")


class ConnectivitySettings(StrictProcedureModel):
    """Read-only project binding used for readiness and preparation."""

    profile: str = ""
    namespace: str = ""
    mapping_path: str = ""
    stale_after_sec: float = Field(default=60.0, gt=0)
    read_only: bool = True

    @field_validator("mapping_path")
    @classmethod
    def bounded_mapping_path(cls, value: str) -> str:
        return _require_relative_application_path(value, label="connectivity mapping_path")

    @model_validator(mode="after")
    def enforce_live_write_boundary(self):
        if not self.read_only:
            raise ValueError("PADesigner connectivity must remain read-only for live endpoints")
        if not math.isfinite(self.stale_after_sec):
            raise ValueError("connectivity stale_after_sec must be finite")
        return self


class TriggerPolicy(StrictProcedureModel):
    """How a procedure becomes eligible to start or repeat."""

    mode: Literal["manual", "condition", "continuous"] = "manual"
    condition: str = ""
    reset_condition: str = ""
    poll_sec: float = Field(default=1.0, gt=0)
    max_cycles: int = Field(default=1, ge=1, le=10000)

    @model_validator(mode="after")
    def validate_trigger(self):
        if self.mode != "manual" and not self.condition.strip():
            raise ValueError(f"{self.mode} trigger requires condition")
        if not math.isfinite(self.poll_sec):
            raise ValueError("trigger poll_sec must be finite")
        if self.mode == "manual" and self.max_cycles != 1:
            raise ValueError("manual trigger max_cycles must be 1")
        return self


class RecoveryPolicy(StrictProcedureModel):
    """Explicit operator policy after interruption or failed execution."""

    mode: Literal["restart", "manual_recovery", "recovery_procedure"] = "manual_recovery"
    recovery_procedure_path: str = ""
    require_readiness_recheck: bool = True
    instructions: str = "Review the last durable checkpoint and place equipment in a verified safe state."

    @field_validator("recovery_procedure_path")
    @classmethod
    def bounded_recovery_path(cls, value: str) -> str:
        return _require_relative_application_path(value, label="recovery procedure path")

    @model_validator(mode="after")
    def validate_recovery(self):
        if self.mode == "recovery_procedure" and not self.recovery_procedure_path.strip():
            raise ValueError("recovery_procedure mode requires recovery_procedure_path")
        return self


class GovernanceRecord(StrictProcedureModel):
    from_status: str
    to_status: str
    actor: str
    role: str
    occurred_at: str
    reason: str = ""
    content_hash: str
    authentication_source: str = "legacy"
    previous_record_hash: str = ""
    record_hash: str = ""
    integrity_hmac: str = ""


class TagDeclaration(StrictProcedureModel):
    """Declared tag contract used by validation and future OPC mapping."""

    tag: str = Field(min_length=1)
    access: TagAccess = "read_write"
    data_type: Literal["float", "int", "bool", "str", "any"] = "any"
    description: str = ""
    initial_value: Optional[Any] = None
    units: str = ""
    min_value: Optional[float] = None
    max_value: Optional[float] = None

    @model_validator(mode="after")
    def validate_numeric_contract(self):
        _require_finite_values(self.initial_value, label=f"tag {self.tag} initial_value")
        for name, value in (("min_value", self.min_value), ("max_value", self.max_value)):
            if value is not None and not math.isfinite(value):
                raise ValueError(f"tag {self.tag} {name} must be finite")
        if self.min_value is not None and self.max_value is not None and self.min_value > self.max_value:
            raise ValueError(f"tag {self.tag} min_value cannot exceed max_value")
        if self.data_type in {"float", "int"} and self.initial_value is not None:
            if isinstance(self.initial_value, bool):
                raise ValueError(f"tag {self.tag} initial_value must be {self.data_type}")
            try:
                number = float(self.initial_value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"tag {self.tag} initial_value must be {self.data_type}") from exc
            if not math.isfinite(number):
                raise ValueError(f"tag {self.tag} initial_value must be finite")
            if self.data_type == "int" and not number.is_integer():
                raise ValueError(f"tag {self.tag} initial_value must be an integer")
            if self.min_value is not None and number < self.min_value:
                raise ValueError(f"tag {self.tag} initial_value is below min_value")
            if self.max_value is not None and number > self.max_value:
                raise ValueError(f"tag {self.tag} initial_value is above max_value")
        return self

    def can_read(self) -> bool:
        return self.access in {"read", "read_write"}

    def can_write(self) -> bool:
        return self.access in {"write", "read_write"}


class ProcedureVariable(StrictProcedureModel):
    name: str = Field(min_length=1)
    value: Any
    description: str = ""

    @field_validator("value")
    @classmethod
    def finite_value(cls, value: Any) -> Any:
        return _require_finite_values(value, label="procedure variable")


class FlowNode(StrictProcedureModel):
    id: str = Field(min_length=1)
    kind: FlowNodeKind
    label: str = ""
    step_id: Optional[str] = None
    condition: Optional[str] = None
    completion_mode: CompletionMode = "process"
    operator_prompt: str = ""
    timeout_sec: float = 60.0
    poll_sec: float = 1.0
    join_mode: Literal["and", "or"] = "and"

    @model_validator(mode="after")
    def validate_by_kind(self):
        if self.kind == "action" and not self.step_id:
            raise ValueError(f"action flow node '{self.id}' requires step_id")
        if self.kind != "action" and self.step_id:
            raise ValueError(f"{self.kind} flow node '{self.id}' cannot reference step_id")
        if self.kind == "transition" and self.completion_mode in {"process", "process_or_operator", "process_and_operator"} and not self.condition:
            raise ValueError(f"transition flow node '{self.id}' requires condition for {self.completion_mode}")
        if not math.isfinite(self.timeout_sec) or self.timeout_sec <= 0:
            raise ValueError("flow node timeout_sec must be finite and positive")
        if not math.isfinite(self.poll_sec) or self.poll_sec <= 0:
            raise ValueError("flow node poll_sec must be finite and positive")
        return self


class FlowWaypoint(StrictProcedureModel):
    x: float
    y: float

    @model_validator(mode="after")
    def validate_coordinates(self):
        if not math.isfinite(self.x) or not math.isfinite(self.y):
            raise ValueError("flow waypoint coordinates must be finite")
        return self


class FlowEdge(StrictProcedureModel):
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    label: str = ""
    condition: Optional[str] = None
    outcome: FlowOutcome = "always"
    priority: int = 100
    is_default: bool = False
    line_width: float = Field(default=2.5, ge=1.0, le=8.0)
    waypoints: list[FlowWaypoint] = Field(default_factory=list, max_length=8)


class ProcedureFlow(StrictProcedureModel):
    nodes: list[FlowNode] = Field(min_length=2, max_length=5000)
    edges: list[FlowEdge] = Field(min_length=1, max_length=10000)
    visit_limit: Optional[int] = Field(
        default=None,
        gt=0,
        description="Maximum total node visits before the run fails as looping; None uses the engine default. Raise for long-cycling monitoring procedures.",
    )

    @model_validator(mode="after")
    def validate_topology_references(self):
        ids = [node.id for node in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("Flow node IDs must be unique")
        node_ids = set(ids)
        starts = [node.id for node in self.nodes if node.kind == "start"]
        ends = [node.id for node in self.nodes if node.kind == "end"]
        if len(starts) != 1:
            raise ValueError(f"Procedure flow requires exactly one start node, found {len(starts)}")
        if not ends:
            raise ValueError("Procedure flow requires at least one end node")
        missing = {value for edge in self.edges for value in (edge.source, edge.target) if value not in node_ids}
        if missing:
            raise ValueError(f"Flow edges reference missing node IDs: {sorted(missing)}")
        return self


class Step(StrictProcedureModel):
    id: str = Field(min_length=1)
    type: StepType
    library_block_id: str = ""
    library_block_version: str = ""
    description: str = ""
    condition: Optional[str] = None
    tag: Optional[str] = None
    value: Optional[Any] = None
    expression: Optional[str] = None
    event_name: str = ""
    event_payload: Optional[Any] = None
    section: str = ""
    unit_procedure: str = ""
    equipment: str = ""
    display_text: str = ""
    operator_guidance: str = ""
    audible: bool = False
    document_link: str = ""
    video_link: str = ""
    start: Optional[float] = None
    end: Optional[float] = None
    rate_per_sec: Optional[float] = None
    timeout_sec: float = 60.0
    poll_sec: float = 1.0
    delay_sec: Optional[float] = None
    require_confirmation: bool = False
    on_timeout: Literal["fail", "alarm", "hold", "abort"] = "fail"
    on_failure: OnFailure = "fail"
    skip_if: Optional[str] = None
    goto_on_pass: Optional[str] = None
    goto_on_fail: Optional[str] = None
    goto_on_timeout: Optional[str] = None
    severity: Literal["info", "warning", "alarm", "critical"] = "info"
    comment_prompt: str = ""
    operator_comment: str = ""
    variable: Optional[str] = None
    input_type: Literal["float", "int", "bool", "str", "selection"] = "float"
    choices: list[Any] = Field(default_factory=list)
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    notes: str = ""
    subprocedure_path: Optional[str] = None
    prefix: Optional[str] = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    tag_aliases: dict[str, str] = Field(default_factory=dict)
    subprocedure_return: bool = Field(default=False, exclude=True, repr=False)

    @field_validator("subprocedure_path")
    @classmethod
    def bounded_subprocedure_path(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return _require_relative_application_path(value, label="subprocedure path")

    @model_validator(mode="after")
    def validate_by_type(self):
        for label, value in (
            ("step value", self.value),
            ("event payload", self.event_payload),
            ("operator choices", self.choices),
            ("subprocedure parameters", self.parameters),
        ):
            _require_finite_values(value, label=f"step {self.id} {label}")
        if self.type in {"check", "permissive", "wait_until", "watchdog"} and not self.condition:
            raise ValueError(f"{self.type} step '{self.id}' requires condition")
        if self.type == "instruction" and not (self.description or self.display_text or self.operator_guidance):
            raise ValueError(f"instruction step '{self.id}' requires description, display_text, or operator_guidance")
        if self.type == "write_tag":
            if not self.tag:
                raise ValueError(f"write_tag step '{self.id}' requires tag")
            if self.value is None:
                raise ValueError(f"write_tag step '{self.id}' requires value")
        if self.type == "read_tag" and (not self.tag or not self.variable):
            raise ValueError(f"read_tag step '{self.id}' requires tag and variable")
        if self.type == "calculate" and (not self.expression or not self.variable):
            raise ValueError(f"calculate step '{self.id}' requires expression and variable")
        if self.type == "user_event" and not self.event_name:
            raise ValueError(f"user_event step '{self.id}' requires event_name")
        if self.type == "ramp_tag":
            missing = [k for k in ["tag", "start", "end", "rate_per_sec"] if getattr(self, k) is None]
            if missing:
                raise ValueError(f"ramp_tag step '{self.id}' missing {missing}")
            if not all(math.isfinite(value) for value in (self.start, self.end, self.rate_per_sec)):
                raise ValueError("ramp values must be finite")
            if self.rate_per_sec <= 0:
                raise ValueError("rate_per_sec must be positive")
        if self.type == "delay":
            if self.delay_sec is None:
                raise ValueError(f"delay step '{self.id}' requires delay_sec")
            if not math.isfinite(self.delay_sec) or self.delay_sec < 0:
                raise ValueError("delay_sec must be finite and non-negative")
        if self.type == "operator_comment" and not (self.comment_prompt or self.description):
            raise ValueError(f"operator_comment step '{self.id}' needs comment_prompt or description")
        if self.type == "operator_input":
            if not self.variable:
                raise ValueError(f"operator_input step '{self.id}' requires variable")
            if self.input_type == "selection" and not self.choices:
                raise ValueError(f"operator_input step '{self.id}' requires choices for selection input")
            if self.min_value is not None and self.max_value is not None and self.min_value > self.max_value:
                raise ValueError(f"operator_input step '{self.id}' has min_value greater than max_value")
            if any(value is not None and not math.isfinite(value) for value in (self.min_value, self.max_value)):
                raise ValueError(f"operator_input step '{self.id}' bounds must be finite")
        if self.type == "subprocedure" and not self.subprocedure_path:
            raise ValueError(f"subprocedure step '{self.id}' requires subprocedure_path")
        if self.type != "subprocedure" and (self.parameters or self.tag_aliases):
            raise ValueError(f"step '{self.id}' declares parameters/tag_aliases but is not a subprocedure step")
        if not math.isfinite(self.timeout_sec) or self.timeout_sec <= 0:
            raise ValueError("timeout_sec must be finite and positive")
        if not math.isfinite(self.poll_sec) or self.poll_sec <= 0:
            raise ValueError("poll_sec must be finite and positive")
        return self


class Procedure(StrictProcedureModel):
    procedure_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    name: str = Field(min_length=1)
    unit: str = ""
    mode: ProcedureMode = ProcedureMode.SEMI_AUTO
    description: str = ""
    metadata: ProcedureMetadata = Field(default_factory=ProcedureMetadata)
    references: list[ControlledReference] = Field(default_factory=list)
    connectivity: ConnectivitySettings = Field(default_factory=ConnectivitySettings)
    trigger: TriggerPolicy = Field(default_factory=TriggerPolicy)
    recovery: RecoveryPolicy = Field(default_factory=RecoveryPolicy)
    governance_history: list[GovernanceRecord] = Field(default_factory=list)
    tags: list[TagDeclaration] = Field(default_factory=list)
    variables: list[ProcedureVariable] = Field(default_factory=list)
    steps: list[Step] = Field(min_length=1, max_length=5000)
    flow: Optional[ProcedureFlow] = None
    _source_path: Optional[str] = PrivateAttr(default=None)

    @model_validator(mode="after")
    def unique_step_ids_and_tags(self):
        ids = [s.id for s in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("Step IDs must be unique")
        tag_names = [t.tag for t in self.tags]
        if len(tag_names) != len(set(tag_names)):
            raise ValueError("Tag declarations must be unique")
        var_names = [v.name for v in self.variables]
        if len(var_names) != len(set(var_names)):
            raise ValueError("Procedure variable names must be unique")
        reference_ids = [reference.reference_id for reference in self.references]
        if len(reference_ids) != len(set(reference_ids)):
            raise ValueError("Controlled reference IDs must be unique")
        referenced = set()
        for s in self.steps:
            for target in [s.goto_on_pass, s.goto_on_fail, s.goto_on_timeout]:
                if target:
                    referenced.add(target)
        missing = referenced - set(ids)
        if missing:
            raise ValueError(f"Branch target step IDs do not exist: {sorted(missing)}")
        if self.flow:
            action_refs = {node.step_id for node in self.flow.nodes if node.kind == "action"}
            missing_actions = action_refs - set(ids)
            if missing_actions:
                raise ValueError(f"Flow action nodes reference missing step IDs: {sorted(missing_actions)}")
        missing_variables = {
            step.variable
            for step in self.steps
            if step.type in {"operator_input", "read_tag", "calculate"} and step.variable and step.variable not in set(var_names)
        }
        if missing_variables:
            raise ValueError(f"Operator input steps reference undeclared variables: {sorted(missing_variables)}")
        return self

    @property
    def source_path(self) -> Optional[str]:
        return self._source_path

    def set_source_path(self, path: str) -> None:
        self._source_path = path

    def declared_tag_map(self) -> dict[str, TagDeclaration]:
        return {t.tag: t for t in self.tags}

    def initial_tag_values(self) -> dict[str, Any]:
        return {t.tag: t.initial_value for t in self.tags if t.initial_value is not None}

    def variable_map(self) -> dict[str, Any]:
        return {v.name: v.value for v in self.variables}
