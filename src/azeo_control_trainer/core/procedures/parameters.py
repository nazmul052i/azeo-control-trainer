"""Explicit operator parameters; queued values are signed history events."""
from dataclasses import dataclass
import json
import math


@dataclass(frozen=True)
class Parameter:
    id: str
    label: str
    value: object
    data_type: str
    unit: str
    minimum: float | None
    maximum: float | None
    access: str
    path: str = ""
    description: str = ""

    def checked(self, value):
        from ..datastore.memory_tags import MemoryTag
        return MemoryTag(name="parameter", data_type=self.data_type,
                         value=value, min_value=self.minimum, max_value=self.maximum).checked(value)


def parameters(procedure):
    result = {}
    for spec in procedure.variables:
        if spec.operator_tuning != "none":
            key = "memory/" + spec.name
            result[key] = Parameter(key, spec.name, spec.value, spec.data_type,
                                    spec.engineering_units, spec.min_value, spec.max_value,
                                    spec.operator_tuning, spec.tag_path, spec.description)
    for step in procedure.steps:
        if step.timer_tuning == "none":
            continue
        for key, label, value, minimum, maximum in (
                ("hold", "Combined hold", step.stable_for_sec, 0, 86400),
                ("timeout", "Overall timeout", step.timeout_sec, .1, 86400)):
            identity = f"step/{step.id}/{key}"
            result[identity] = Parameter(identity, f"{step.id} · {label}", value, "float", "s",
                                         minimum, maximum, step.timer_tuning, description=step.description)
        for row in step.condition_rows:
            identity = f"step/{step.id}/row/{row.id}"
            result[identity] = Parameter(identity, f"{step.id} · {row.id} hold", row.stable_for_sec,
                                         "float", "s", 0, 86400, step.timer_tuning, description=row.expression)
    return result


def parameter_value(procedure, identity, variables=None):
    spec = parameters(procedure)[identity]
    return (variables.get(identity.split("/", 1)[1], spec.value)
            if variables is not None and identity.startswith("memory/") else spec.value)


def apply_parameter(procedure, variables, identity, value):
    spec = parameters(procedure).get(identity)
    if spec is None:
        raise ValueError("This parameter is not exposed for operator tuning")
    value = spec.checked(value)
    parts = identity.split("/")
    if parts[0] == "memory":
        variables[parts[1]] = value
    else:
        step = next(step for step in procedure.steps if step.id == parts[1])
        if parts[2] == "row":
            next(row for row in step.condition_rows if row.id == parts[3]).stable_for_sec = value
        else:
            setattr(step, "stable_for_sec" if parts[2] == "hold" else "timeout_sec", value)
    return value


def pending_parameters(store, ref, digest):
    from contextlib import closing
    pending = {}
    with closing(store._connect()) as connection:
        rows = connection.execute("SELECT id,event_type,data_json FROM run_events WHERE step_id=? "
                                  "AND event_type IN ('PA_TUNING_QUEUED','PA_TUNING_APPLIED','PA_TUNING_CANCELLED') ORDER BY id", (ref,))
        for event_id, kind, encoded in rows:
            data = json.loads(encoded)
            if data.get("definition") != digest:
                continue
            key = data["parameter"]
            if kind == "PA_TUNING_QUEUED":
                pending[key] = dict(data, event_id=event_id)
            elif key in pending and pending[key]["event_id"] == data.get("queued_event"):
                pending.pop(key)
    return pending


def observation_detail(expression, samples, memory):
    """Human-readable evidence, including unknown values, for a condition row."""
    from .logic import prepared_expression
    _, refs, names = prepared_expression(expression)
    values, qualities = [], []
    for _, tag in refs:
        sample = samples.get(tag)
        quality = sample.quality if sample else "Missing"
        value = sample.value if sample else None
        if isinstance(value, float) and not math.isfinite(value):
            quality = "Invalid"
        shown = f"{value:.6g}" if type(value) is float else str(value)
        values.append(f"{tag} = {shown if quality == 'Good' and value is not None else '—'}")
        qualities.append(quality if value is not None else "Missing")
    for name in sorted(names & memory.keys()):
        value = memory[name]
        shown = f"{value:.6g}" if type(value) is float else str(value)
        values.append(f"{name} = {shown}")
    return "; ".join(values) or "Constant expression", ("Good" if all(q == "Good" for q in qualities) else
                                                       ", ".join(dict.fromkeys(q for q in qualities if q != "Good")))
