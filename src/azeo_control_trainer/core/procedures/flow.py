"""Bounded advisory workflow contract shared by the editor and runtime."""
from collections import Counter, deque

from pydantic import Field
from azeo_control_trainer.core.pa_designer.core.procedure_model import FlowNode, ProcedureFlow

from .logic import validate_expression


class AdvisoryFlowNode(FlowNode):
    timeout_sec: float = Field(default=600, gt=0, allow_inf_nan=False)
    max_iterations: int = Field(default=3, ge=1, le=1000)
    stable_for_sec: float = Field(default=0, ge=0, allow_inf_nan=False)


class AdvisoryFlow(ProcedureFlow):
    nodes: list[AdvisoryFlowNode] = Field(min_length=2, max_length=1000)
    visit_limit: int = Field(default=10000, ge=1, le=100000)


def linear_flow(steps):
    ids = ["$start", *("step:" + s["id"] for s in steps), "$end"]
    return {"nodes": [{"id": ids[0], "kind": "start"},
                      *[{"id": "step:" + s["id"], "kind": "action", "step_id": s["id"]} for s in steps],
                      {"id": ids[-1], "kind": "end"}],
            "edges": [{"source": a, "target": b} for a, b in zip(ids, ids[1:])], "visit_limit": 10000}


def layout_flow(flow):
    """Stable breadth ranks keep sibling paths separate and loops finite."""
    outgoing = {n["id"]: [] for n in flow["nodes"]}
    for edge in flow["edges"]:
        if edge.get("source") in outgoing and edge.get("target") in outgoing:
            outgoing[edge["source"]].append(edge["target"])
    start = next((n["id"] for n in flow["nodes"] if n["kind"] == "start"), next(iter(outgoing), ""))
    depth, pending = {start: 0}, deque([start])
    while pending:
        key = pending.popleft()
        for target in outgoing[key]:
            if target not in depth:
                depth[target] = depth[key] + 1
                pending.append(target)
    counts, positions = Counter(), {}
    for index, node in enumerate(flow["nodes"]):
        rank = depth.get(node["id"], max(depth.values(), default=0) + 1 + index)
        positions[node["id"]] = (60 + counts[rank] * 390, 30 + rank * 165)
        counts[rank] += 1
    return positions


def validate_flow(procedure):
    flow = procedure.flow
    if flow is None:
        return
    nodes = {n.id: n for n in flow.nodes}
    outgoing = {key: [] for key in nodes}
    for edge in flow.edges:
        outgoing[edge.source].append(edge)
        if edge.condition:
            validate_expression(edge.condition, procedure.variable_map(), procedure.declared_tag_map())
    pairs = [(e.source, e.target) for e in flow.edges]
    if len(set(pairs)) != len(pairs):
        raise ValueError("Use one connection per node pair; combine its condition or insert a merge")
    if len({n.step_id for n in flow.nodes if n.kind == "action"}) != sum(n.kind == "action" for n in flow.nodes):
        raise ValueError("Each action block belongs to one workflow node; a loop may revisit it")
    for node in nodes.values():
        edges = outgoing[node.id]
        if node.condition:
            validate_expression(node.condition, procedure.variable_map(), procedure.declared_tag_map())
        if node.completion_mode == "process_or_operator":
            raise ValueError("Operator confirmation cannot bypass a process condition; use process_and_operator")
        if node.kind in {"choice", "loop"} and sum(e.is_default for e in edges) != 1:
            raise ValueError(f"{node.id}: provide exactly one default exit")
        if node.kind == "loop" and next(e.target for e in edges if e.is_default) == node.id:
            raise ValueError("A bounded loop's default connection must exit the loop")
        if node.kind not in {"choice", "loop"} and any(e.condition or e.is_default for e in edges):
            raise ValueError(f"{node.id}: conditions/default exits belong to Decision or Retry loop nodes")
        if node.kind == "parallel_fork" and len(edges) > 8:
            raise ValueError("A parallel fork supports at most eight branches")
    # Removing bounded loop entries must leave a DAG. Otherwise a choice/action
    # cycle could monopolize the worker without ever consuming a retry budget.
    remaining = {key for key, node in nodes.items() if node.kind != "loop"}
    indegree = Counter(e.target for e in flow.edges if e.source in remaining and e.target in remaining)
    ready = deque(key for key in remaining if not indegree[key])
    count = 0
    while ready:
        key = ready.popleft()
        count += 1
        for edge in outgoing[key]:
            if edge.target in remaining:
                indegree[edge.target] -= 1
                if not indegree[edge.target]:
                    ready.append(edge.target)
    if count != len(remaining):
        raise ValueError("Every cycle must pass through a bounded Retry loop")
    # A fixed global fork budget bounds nested threads even in authored loops.
    forks = [n for n in nodes.values() if n.kind == "parallel_fork"]
    if sum(len(outgoing[n.id]) for n in forks) > 32:
        raise ValueError("A procedure supports at most 32 parallel branch entries")
    steps = {s.id: s for s in procedure.steps}
    from .library import block_for_step
    for node in nodes.values():
        step = steps.get(node.step_id)
        if step and step.type == "complete" and any(nodes[e.target].kind != "end" for e in outgoing[node.id]):
            raise ValueError("Complete must lead directly to End; use Record event for an intermediate milestone")
    shared = {v.name for v in procedure.variables if v.tag_path or v.operator_tuning == "live"}
    from azeo_control_trainer.core.pa_designer.core.validator import _reachable_distances
    for fork in forks:
        distances = [_reachable_distances(e.target, outgoing) for e in outgoing[fork.id]]
        common = set.intersection(*(set(d) for d in distances))
        join = min((key for key in common if nodes[key].kind == "parallel_join"),
                   key=lambda key: (max(d[key] for d in distances), sum(d[key] for d in distances), key))
        branch_writes = set()
        for branch in outgoing[fork.id]:
            seen, pending, mutations = set(), [branch.target], set()
            while pending:
                key = pending.pop()
                if key in seen or key == join:
                    continue
                if key == fork.id:
                    raise ValueError("A parallel branch cannot re-enter its owning fork before joining")
                seen.add(key)
                node = nodes[key]
                step = steps.get(node.step_id)
                if step:
                    writes = {step.variable} if step.type in {"calculate", "operator_input", "read_tag"} else set()
                    writes.update(row.variable for row in step.calculation_rows)
                    if writes & shared:
                        raise ValueError("Parallel branches must write local, untuned memory; publish shared results after the join")
                    source_id = block_for_step(step).source_id
                    if step.type in {"write_tag", "ramp_tag"} or (
                        source_id == "integration.activex_opc_com"
                        and step.integration_operation == "write"
                    ):
                        raise ValueError("Place equipment outputs before/after a parallel group, not in cancellable branches")
                    mutations.update(writes)
                # Continue through nested joins until this fork's own join;
                # otherwise writes after an inner join escape the checks.
                pending.extend(e.target for e in outgoing[key])
            if mutations & branch_writes:
                raise ValueError("Parallel branches must use distinct result variables, including every calculation row")
            branch_writes.update(mutations)
    # A proposal may never reach successful completion without a matching
    # actual-value wait. Checking the serialized list misses alternate paths.
    from azeo_control_trainer.core.pa_designer.core.tag_references import referenced_tags_in_expression
    for node in nodes.values():
        step = steps.get(node.step_id)
        if not step:
            continue
        source_id = block_for_step(step).source_id
        if step.type not in {"write_tag", "ramp_tag"} and not (
            source_id == "integration.activex_opc_com"
            and step.integration_operation == "write"
        ):
            continue
        feedback_tag = step.feedback_tag or step.tag
        seen, pending = set(), [e.target for e in outgoing[node.id]]
        while pending:
            key = pending.pop()
            if key in seen:
                continue
            seen.add(key)
            current = nodes[key]
            action = steps.get(current.step_id)
            if action and action.type in {"hold", "abort"}:
                continue
            if (action and action.type == "wait_until" and not action.skip_if
                    and feedback_tag in referenced_tags_in_expression(action.condition or "False")):
                pending.extend(e.target for e in outgoing[key] if e.outcome in {"failed", "timeout", "skipped"})
                continue
            if current.kind == "end" or (action and action.type == "complete"):
                raise ValueError(f"Output {step.id}: every completion path requires actual-value verification for {feedback_tag}")
            pending.extend(e.target for e in outgoing[key])
