from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Iterable

from .expression_engine import ExpressionError, SafeExpressionEvaluator
from .procedure_model import Procedure, Step
from .tag_references import normalize_tag_expression, referenced_tags_in_expression

NUMERIC_TYPES = {"float", "int"}
_EXPRESSION_EVALUATOR = SafeExpressionEvaluator()


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_for_errors(self) -> None:
        if self.errors:
            raise ValueError("Procedure validation failed:\n" + "\n".join(f"- {e}" for e in self.errors))


def referenced_tags_in_step(step: Step) -> set[str]:
    tags: set[str] = set()
    for expr in [step.condition, step.expression, step.skip_if]:
        if expr:
            try:
                tags.update(referenced_tags_in_expression(expr))
            except SyntaxError:
                pass
    if step.tag:
        tags.add(step.tag)
    # Subprocedure alias targets are real tags the parent touches through the child.
    tags.update(step.tag_aliases.values())
    return tags


def referenced_tags(procedure: Procedure) -> set[str]:
    found: set[str] = set()
    for step in procedure.steps:
        found.update(referenced_tags_in_step(step))
    if procedure.flow:
        for node in procedure.flow.nodes:
            if node.condition:
                try:
                    found.update(referenced_tags_in_expression(node.condition))
                except SyntaxError:
                    pass
        for edge in procedure.flow.edges:
            if edge.condition:
                try:
                    found.update(referenced_tags_in_expression(edge.condition))
                except SyntaxError:
                    pass
    for expression in (procedure.trigger.condition, procedure.trigger.reset_condition):
        if expression:
            try:
                found.update(referenced_tags_in_expression(expression))
            except SyntaxError:
                pass
    return found


def _validate_expression(location: str, expression: str, variable_names: set[str], report: ValidationReport) -> None:
    try:
        normalized, var_to_tag = normalize_tag_expression(expression)
        _EXPRESSION_EVALUATOR.validate(normalized, variable_names | set(var_to_tag))
    except (SyntaxError, ExpressionError) as exc:
        report.errors.append(f"{location} has invalid expression: {exc}")


def validate_procedure_contract(procedure: Procedure) -> ValidationReport:
    report = ValidationReport()
    declared = procedure.declared_tag_map()
    refs = referenced_tags(procedure)
    step_ids = {s.id for s in procedure.steps}
    variable_names = {variable.name for variable in procedure.variables}

    for label, expression in (
        ("Procedure trigger", procedure.trigger.condition),
        ("Procedure reset trigger", procedure.trigger.reset_condition),
    ):
        if expression:
            _validate_expression(label, expression, variable_names, report)

    for step in procedure.steps:
        for attr in ["goto_on_pass", "goto_on_fail", "goto_on_timeout"]:
            target = getattr(step, attr)
            if target and target not in step_ids:
                report.errors.append(f"Step {step.id} has invalid {attr}: {target}")
        if step.type in {"warning", "alarm", "hold", "abort", "instruction", "permissive"} and not (step.description or step.display_text):
            report.warnings.append(f"Step {step.id} is {step.type} but has no description/message.")
        for expr in [step.condition, step.expression, step.skip_if]:
            if expr:
                _validate_expression(f"Step {step.id}", expr, variable_names, report)

    if procedure.flow:
        for node in procedure.flow.nodes:
            if node.condition:
                _validate_expression(f"Flow node {node.id}", node.condition, variable_names, report)
        for edge in procedure.flow.edges:
            if edge.condition:
                _validate_expression(
                    f"Flow edge {edge.source}->{edge.target}", edge.condition, variable_names, report
                )

    if declared:
        for tag in sorted(refs - set(declared)):
            report.errors.append(f"Referenced tag is not declared: {tag}")

        for tag in sorted(set(declared) - refs):
            report.warnings.append(f"Declared tag is not used by any step: {tag}")

        for step in procedure.steps:
            for expr in [step.condition, step.expression, step.skip_if]:
                if expr:
                    try:
                        expr_tags = referenced_tags_in_expression(expr)
                    except SyntaxError:
                        continue
                    for tag in expr_tags:
                        decl = declared.get(tag)
                        if decl and not decl.can_read():
                            report.errors.append(f"Step {step.id} reads write-only tag: {tag}")
            if step.tag:
                decl = declared.get(step.tag)
                if decl and step.type in {"write_tag", "ramp_tag"} and not decl.can_write():
                    report.errors.append(f"Step {step.id} writes read-only tag: {step.tag}")
                if decl and step.type == "read_tag" and not decl.can_read():
                    report.errors.append(f"Step {step.id} reads write-only tag: {step.tag}")
                if decl and step.type == "write_tag":
                    report.errors.extend(_validate_write_value_type(step, decl.data_type))
                if decl and decl.data_type in NUMERIC_TYPES:
                    values: Iterable[float] = []
                    if step.type == "write_tag" and _is_number(step.value):
                        values = [float(step.value)]
                    elif step.type == "ramp_tag" and step.start is not None and step.end is not None:
                        values = [float(step.start), float(step.end)]
                    for value in values:
                        if decl.min_value is not None and value < decl.min_value:
                            report.errors.append(f"Step {step.id} writes {step.tag} below min {decl.min_value}: {value}")
                        if decl.max_value is not None and value > decl.max_value:
                            report.errors.append(f"Step {step.id} writes {step.tag} above max {decl.max_value}: {value}")

        if procedure.flow:
            flow_expressions = [
                (f"Flow node {node.id}", node.condition)
                for node in procedure.flow.nodes
                if node.condition
            ] + [
                (f"Flow edge {edge.source}->{edge.target}", edge.condition)
                for edge in procedure.flow.edges
                if edge.condition
            ]
            for location, expression in flow_expressions:
                try:
                    expression_tags = referenced_tags_in_expression(expression)
                except SyntaxError:
                    continue
                for tag in expression_tags:
                    decl = declared.get(tag)
                    if decl and not decl.can_read():
                        report.errors.append(f"{location} reads write-only tag: {tag}")

    if procedure.mode.value == "auto" and procedure.metadata.safety_class != "auto_sim_only":
        report.warnings.append("AUTO mode procedure should normally use safety_class=auto_sim_only in this project.")

    metadata = procedure.metadata
    if metadata.approval_status in {"approved", "released"} and not metadata.approved_by:
        report.warnings.append(f"{metadata.approval_status.upper()} revision does not identify an approver.")
    if metadata.approval_status == "released" and not metadata.released_by:
        report.errors.append("RELEASED revision must identify released_by.")
    if procedure.trigger.mode == "continuous" and procedure.trigger.max_cycles < 2:
        report.warnings.append("Continuous trigger is configured for only one execution cycle.")

    if not any(step.type == "complete" for step in procedure.steps):
        report.warnings.append("Procedure does not include an explicit complete step.")

    if procedure.flow:
        _validate_flow(procedure, report)

    return report


def _validate_flow(procedure: Procedure, report: ValidationReport) -> None:
    assert procedure.flow is not None
    nodes = {node.id: node for node in procedure.flow.nodes}
    outgoing = {node_id: [] for node_id in nodes}
    incoming = {node_id: [] for node_id in nodes}
    for edge in procedure.flow.edges:
        outgoing[edge.source].append(edge)
        incoming[edge.target].append(edge)

    start = next(node for node in procedure.flow.nodes if node.kind == "start")
    if incoming[start.id]:
        report.errors.append(f"Flow start node {start.id} cannot have incoming edges.")

    for node in procedure.flow.nodes:
        out_count = len(outgoing[node.id])
        in_count = len(incoming[node.id])
        if node.kind == "end":
            if out_count:
                report.errors.append(f"Flow end node {node.id} cannot have outgoing edges.")
            continue
        if node.kind in {"start", "transition", "merge", "parallel_join"} and out_count != 1:
            report.errors.append(f"Flow {node.kind} node {node.id} requires exactly one outgoing edge, found {out_count}.")
        if node.kind == "action":
            if out_count < 1:
                report.errors.append(f"Flow action node {node.id} requires at least one outgoing edge.")
            if out_count > 1:
                always_edges = [edge for edge in outgoing[node.id] if edge.outcome == "always"]
                if len(always_edges) > 1:
                    report.errors.append(f"Flow action node {node.id} has more than one 'always' outcome edge.")
        if node.kind in {"choice", "loop", "parallel_fork"} and out_count < 2:
            report.errors.append(f"Flow {node.kind} node {node.id} requires at least two outgoing edges, found {out_count}.")
        if node.kind in {"choice", "loop"}:
            defaults = [edge for edge in outgoing[node.id] if edge.is_default]
            if len(defaults) > 1:
                report.errors.append(f"Flow {node.kind} node {node.id} has more than one default edge.")
            for edge in outgoing[node.id]:
                if edge.is_default and edge.condition:
                    report.errors.append(f"Default flow edge {edge.source}->{edge.target} cannot also define a condition.")
                if not edge.is_default and not edge.condition:
                    report.errors.append(f"Flow edge {edge.source}->{edge.target} requires a condition or default marker.")
        if node.kind == "parallel_fork":
            for edge in outgoing[node.id]:
                if edge.condition:
                    report.warnings.append(
                        f"Flow edge {edge.source}->{edge.target} carries a condition, but every parallel fork branch always runs."
                    )
        if node.kind == "parallel_join" and in_count < 2:
            report.errors.append(f"Flow parallel_join node {node.id} requires at least two incoming edges, found {in_count}.")

    _validate_parallel_structure(
        nodes, outgoing, {step.id: step for step in procedure.steps}, report
    )

    action_step_ids = {node.step_id for node in procedure.flow.nodes if node.kind == "action"}
    for step in procedure.steps:
        if step.id in action_step_ids and any((step.goto_on_pass, step.goto_on_fail, step.goto_on_timeout)):
            report.errors.append(f"Flow-controlled step {step.id} cannot also define goto_on_pass/goto_on_fail/goto_on_timeout.")
        if step.id not in action_step_ids:
            report.errors.append(
                f"Step {step.id} is not referenced by any flow action node, so it would silently never execute."
            )
    for node in procedure.flow.nodes:
        if node.kind not in {"action", "choice", "loop"}:
            continue
        outcomes = [edge.outcome for edge in outgoing[node.id] if edge.outcome != "always"]
        if len(outcomes) != len(set(outcomes)):
            report.errors.append(f"Flow node {node.id} has duplicate outcome edges.")

    visited: set[str] = set()
    queue = [start.id]
    while queue:
        node_id = queue.pop(0)
        if node_id in visited:
            continue
        visited.add(node_id)
        queue.extend(edge.target for edge in outgoing[node_id])
    unreachable = sorted(set(nodes) - visited)
    if unreachable:
        report.errors.append(f"Flow contains unreachable nodes: {unreachable}")


def _reachable_distances(start: str, outgoing: dict[str, list]) -> dict[str, int]:
    distances = {start: 0}
    pending = [start]
    while pending:
        node_id = pending.pop(0)
        for edge in outgoing[node_id]:
            if edge.target in distances:
                continue
            distances[edge.target] = distances[node_id] + 1
            pending.append(edge.target)
    return distances


def _parallel_arrivals(
    start: str,
    expected_join: str,
    pairings: dict[str, str],
    nodes: dict,
    outgoing: dict[str, list],
    visited: frozenset[str] = frozenset(),
) -> set[str]:
    """Return joins/endpoints a branch can hit before its expected join.

    Nested forks are treated as structured units and traversed from their own
    join's outgoing edge, matching the runtime token behavior.
    """
    if start == expected_join:
        return {expected_join}
    if start in visited:
        return set()
    node = nodes[start]
    if node.kind == "end":
        return {f"end:{start}"}
    if node.kind == "parallel_join":
        return {f"join:{start}"}
    if node.kind == "parallel_fork":
        nested_join = pairings.get(start)
        if not nested_join:
            return {f"fork:{start}"}
        join_edges = outgoing[nested_join]
        if len(join_edges) != 1:
            return {f"join:{nested_join}"}
        return _parallel_arrivals(
            join_edges[0].target,
            expected_join,
            pairings,
            nodes,
            outgoing,
            visited | {start},
        )
    arrivals: set[str] = set()
    for edge in outgoing[start]:
        arrivals.update(
            _parallel_arrivals(
                edge.target,
                expected_join,
                pairings,
                nodes,
                outgoing,
                visited | {start},
            )
        )
    return arrivals


def _branch_mutations(
    start: str,
    join: str,
    nodes: dict,
    outgoing: dict[str, list],
    steps: dict,
) -> set[str]:
    pending = [start]
    visited: set[str] = set()
    mutations: set[str] = set()
    while pending:
        node_id = pending.pop()
        if node_id == join or node_id in visited:
            continue
        visited.add(node_id)
        node = nodes[node_id]
        if node.kind == "action" and node.step_id in steps:
            step = steps[node.step_id]
            if step.type in {"read_tag", "calculate", "operator_input"} and step.variable:
                mutations.add(f"variable:{step.variable}")
            if step.type in {"write_tag", "ramp_tag"} and step.tag:
                mutations.add(f"tag:{step.tag}")
        pending.extend(edge.target for edge in outgoing[node_id])
    return mutations


def _validate_parallel_structure(
    nodes: dict, outgoing: dict[str, list], steps: dict, report: ValidationReport
) -> None:
    forks = [node for node in nodes.values() if node.kind == "parallel_fork"]
    joins = {node.id for node in nodes.values() if node.kind == "parallel_join"}
    pairings: dict[str, str] = {}

    for fork in forks:
        branch_targets = [edge.target for edge in outgoing[fork.id]]
        if len(branch_targets) > 32:
            report.errors.append(
                f"Flow parallel_fork node {fork.id} exceeds the maximum of 32 branches."
            )
        if len(branch_targets) < 2:
            continue
        distances = [_reachable_distances(target, outgoing) for target in branch_targets]
        common = set(distances[0])
        for branch_distances in distances[1:]:
            common.intersection_update(branch_distances)
        candidates = common & joins
        if not candidates:
            report.errors.append(
                f"Flow parallel_fork node {fork.id} requires every branch to converge at one common parallel_join."
            )
            continue
        ranked = sorted(
            candidates,
            key=lambda candidate: (
                max(branch[candidate] for branch in distances),
                sum(branch[candidate] for branch in distances),
                candidate,
            ),
        )
        pairings[fork.id] = ranked[0]

    owners: dict[str, list[str]] = {}
    for fork_id, join_id in pairings.items():
        owners.setdefault(join_id, []).append(fork_id)
    for join_id, fork_ids in owners.items():
        if len(fork_ids) > 1:
            report.errors.append(
                f"Flow parallel_join node {join_id} is paired with multiple forks: {sorted(fork_ids)}."
            )
    for join_id in sorted(joins - set(owners)):
        report.errors.append(f"Flow parallel_join node {join_id} is not paired with a parallel_fork.")

    for fork in forks:
        expected_join = pairings.get(fork.id)
        if not expected_join:
            continue
        resources = {
            edge.target: _branch_mutations(
                edge.target, expected_join, nodes, outgoing, steps
            )
            for edge in outgoing[fork.id]
        }
        targets = sorted(resources)
        for index, left in enumerate(targets):
            for right in targets[index + 1:]:
                conflicts = sorted(resources[left] & resources[right])
                if conflicts:
                    report.errors.append(
                        f"Flow parallel branches {fork.id}->{left} and {fork.id}->{right} "
                        f"mutate the same resources: {conflicts}."
                    )
        for edge in outgoing[fork.id]:
            arrivals = _parallel_arrivals(edge.target, expected_join, pairings, nodes, outgoing)
            unexpected = sorted(arrival for arrival in arrivals if arrival != expected_join)
            if expected_join not in arrivals or unexpected:
                detail = f"; unexpected endpoints: {unexpected}" if unexpected else ""
                report.errors.append(
                    f"Flow branch {fork.id}->{edge.target} must converge at parallel_join {expected_join}{detail}."
                )


def _is_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return math.isfinite(number)


def _validate_write_value_type(step: Step, data_type: str) -> list[str]:
    value = step.value
    if data_type == "any":
        return []
    if data_type == "float":
        if not _is_number(value):
            return [f"Step {step.id} writes non-numeric value to float tag {step.tag}: {value!r}"]
        return []
    if data_type == "int":
        if not _is_number(value) or float(value) % 1 != 0:
            return [f"Step {step.id} writes non-integer value to int tag {step.tag}: {value!r}"]
        return []
    if data_type == "bool" and not isinstance(value, bool):
        return [f"Step {step.id} writes non-bool value to bool tag {step.tag}: {value!r}"]
    if data_type == "str" and not isinstance(value, str):
        return [f"Step {step.id} writes non-string value to str tag {step.tag}: {value!r}"]
    return []
