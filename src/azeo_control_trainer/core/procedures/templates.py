"""Original advisory authoring examples; no assumed plant tags or commands."""
from .authoring import ProcedureDraft


def readiness_workflow():
    steps = [
        {"id": "review", "type": "instruction", "description": "Training workflow example: review two readiness observations. This example uses local operator inputs; bind real feedback before plant use."},
        {"id": "unit_a", "type": "operator_input", "description": "Is unit A ready?", "variable": "a_ready", "input_type": "bool"},
        {"id": "unit_b", "type": "operator_input", "description": "Is unit B ready?", "variable": "b_ready", "input_type": "bool"},
        {"id": "ready", "type": "check", "description": "Both operator observations must be ready", "condition": "a_ready and b_ready"},
        {"id": "stable", "type": "wait_until", "description": "Demonstrate continuous qualification; replace local inputs with actual process feedback for use",
         "condition_rows": [{"id": "A", "expression": "a_ready", "stable_for_sec": 1},
                            {"id": "B", "expression": "b_ready", "stable_for_sec": 1}],
         "stable_for_sec": 2, "timeout_sec": 30, "poll_sec": .1},
        {"id": "escalate", "type": "operator_input", "description": "Readiness was not achieved within two attempts. Choose the review outcome.",
         "variable": "review_outcome", "input_type": "selection", "choices": ["Hold for review", "Abort guidance"]},
        {"id": "comment", "type": "operator_comment", "comment_prompt": "Record the failed readiness observations and the reason for your decision."},
        {"id": "held", "type": "hold", "description": "Readiness needs review. Equipment remains under operator control."},
        {"id": "aborted", "type": "abort", "description": "Operator ended readiness guidance."},
        {"id": "complete", "type": "complete", "description": "Readiness example completed with qualified observations."},
    ]
    from .library import block_for_type
    for step in steps:
        block = block_for_type(step["type"])
        step.update(library_block_id=block.block_id, library_block_version=block.version)
    nodes = [{"id": "start", "kind": "start"},
             *[{"id": s["id"], "kind": "action", "step_id": s["id"]} for s in steps],
             {"id": "attempts", "kind": "loop", "label": "Maximum two attempts", "max_iterations": 2},
             {"id": "parallel", "kind": "parallel_fork", "label": "Independent readiness checks"},
             {"id": "joined", "kind": "parallel_join", "join_mode": "and"},
             {"id": "decision", "kind": "choice", "label": "Operator review outcome"},
             {"id": "end", "kind": "end"}]
    edges = [{"source": a, "target": b} for a, b in (
        ("start", "review"), ("review", "attempts"), ("parallel", "unit_a"), ("parallel", "unit_b"),
        ("unit_a", "joined"), ("unit_b", "joined"), ("joined", "ready"), ("stable", "complete"),
        ("complete", "end"), ("escalate", "comment"), ("comment", "decision"), ("held", "end"), ("aborted", "end"))]
    edges += [{"source": "attempts", "target": "parallel", "condition": "True", "label": "Next bounded attempt"},
              {"source": "attempts", "target": "escalate", "is_default": True, "label": "Attempts exhausted"},
              {"source": "ready", "target": "stable", "outcome": "passed", "label": "Both ready"},
              {"source": "ready", "target": "attempts", "outcome": "failed", "label": "Review next attempt"},
              {"source": "stable", "target": "attempts", "outcome": "timeout", "label": "Qualification timed out"},
              {"source": "decision", "target": "held", "condition": "review_outcome == 'Hold for review'"},
              {"source": "decision", "target": "aborted", "is_default": True}]
    return ProcedureDraft({"procedure_id": "readiness_example", "name": "Parallel readiness with bounded retry", "mode": "advisory",
        "description": "Editable training workflow: parallel operator observations, bounded retries, timed qualification and explicit review outcomes.",
        "variables": [{"name": key, "value": False, "data_type": "bool"} for key in ("a_ready", "b_ready")]
                     + [{"name": "review_outcome", "value": "Hold for review", "data_type": "str"}],
        "steps": steps, "flow": {"nodes": nodes, "edges": edges, "visit_limit": 200}}, {})
