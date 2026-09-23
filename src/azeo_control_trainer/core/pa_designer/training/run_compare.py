from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..storage.sqlite_store import SQLiteStore
from ..reports import build_metrics


@dataclass
class RunComparison:
    left_run_id: str
    right_run_id: str
    left_status: str
    right_status: str
    metric_delta: dict[str, Any]
    step_status_changes: list[dict[str, Any]]


def compare_runs(store: SQLiteStore, left_run_id: str, right_run_id: str) -> RunComparison:
    left = store.get_run(left_run_id) or {}
    right = store.get_run(right_run_id) or {}
    lm = build_metrics(store, left_run_id)
    rm = build_metrics(store, right_run_id)
    deltas: dict[str, Any] = {}
    for key in sorted(set(lm) | set(rm)):
        lv = lm.get(key)
        rv = rm.get(key)
        if isinstance(lv, (int, float)) and isinstance(rv, (int, float)):
            deltas[key] = rv - lv
        elif lv != rv:
            deltas[key] = {"left": lv, "right": rv}
    def final_steps(run_id: str) -> dict[str, str]:
        final: dict[str, str] = {}
        for row in store.run_steps(run_id):
            final[row["step_id"]] = row["status"]
        return final
    ls = final_steps(left_run_id)
    rs = final_steps(right_run_id)
    changes = []
    for step_id in sorted(set(ls) | set(rs)):
        if ls.get(step_id) != rs.get(step_id):
            changes.append({"step_id": step_id, "left": ls.get(step_id), "right": rs.get(step_id)})
    return RunComparison(left_run_id, right_run_id, left.get("status", "UNKNOWN"), right.get("status", "UNKNOWN"), deltas, changes)


def format_run_comparison(comparison: RunComparison) -> str:
    lines = [
        f"Run comparison: {comparison.left_run_id} -> {comparison.right_run_id}",
        f"Status: {comparison.left_status} -> {comparison.right_status}",
        "Metric deltas:",
    ]
    if comparison.metric_delta:
        for k, v in comparison.metric_delta.items():
            lines.append(f"  {k}: {v}")
    else:
        lines.append("  no metric differences")
    lines.append("Step status changes:")
    if comparison.step_status_changes:
        for row in comparison.step_status_changes:
            lines.append(f"  {row['step_id']}: {row.get('left')} -> {row.get('right')}")
    else:
        lines.append("  no step status changes")
    return "\n".join(lines)
