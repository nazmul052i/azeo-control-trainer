"""Semantic find-usages and rename operations for procedure documents."""
from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import dataclass
import keyword
import re


@dataclass(frozen=True)
class SymbolUsage:
    symbol_kind: str
    symbol: str
    location: str
    field: str
    preview: str


_STEP_EXPRESSIONS = ("condition", "expression", "skip_if")


def _offsets(text: str) -> list[int]:
    result, total = [0], 0
    for line in text.splitlines(keepends=True):
        total += len(line)
        result.append(total)
    return result


def _expression_spans(expression: str, kind: str, symbol: str) -> list[tuple[int, int]]:
    """Locate semantic references without changing matching text in strings."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return []
    starts = _offsets(expression)
    spans = []
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    for node in ast.walk(tree):
        match = False
        if kind == "variable" and isinstance(node, ast.Name) and node.id == symbol:
            parent = parents.get(node)
            match = not isinstance(parent, ast.Attribute) and not (
                isinstance(parent, ast.Call) and parent.func is node
            )
        elif kind == "tag" and isinstance(node, ast.Attribute) and not isinstance(parents.get(node), ast.Attribute):
            try:
                match = ast.unparse(node) == symbol
            except Exception:
                match = False
        if not match or not hasattr(node, "end_lineno"):
            continue
        start = starts[node.lineno - 1] + node.col_offset
        end = starts[node.end_lineno - 1] + node.end_col_offset
        spans.append((start, end))
    return sorted(set(spans))


def rewrite_expression(expression: str, kind: str, old: str, new: str) -> tuple[str, bool]:
    spans = _expression_spans(expression, kind, old)
    result = expression
    for start, end in reversed(spans):
        result = result[:start] + new + result[end:]
    return result, bool(spans)


def _expressions(data: dict):
    trigger = data.get("trigger", {})
    for field in ("condition", "reset_condition"):
        if trigger.get(field):
            yield trigger, field, "Procedure trigger"
    for step in data.get("steps", []):
        location = f"Step {step.get('id', '?')}"
        for field in _STEP_EXPRESSIONS:
            if step.get(field):
                yield step, field, location
        for collection in ("condition_rows", "calculation_rows"):
            for index, row in enumerate(step.get(collection, []), 1):
                if row.get("expression"):
                    yield row, "expression", f"{location} / {collection.replace('_', ' ')} {index}"
        for key, value in step.get("adapter_inputs", {}).items():
            if isinstance(value, str) and value.startswith("="):
                yield step["adapter_inputs"], key, f"{location} / adapter input {key}"
        for key, value in step.get("parameters", {}).items():
            if isinstance(value, dict) and isinstance(value.get("expression"), str):
                yield value, "expression", f"{location} / parameter {key}"
    flow = data.get("flow") or {}
    for node in flow.get("nodes", []):
        if node.get("condition"):
            yield node, "condition", f"Flow node {node.get('id', '?')}"
    for edge in flow.get("edges", []):
        if edge.get("condition"):
            yield edge, "condition", f"Flow edge {edge.get('source', '?')} -> {edge.get('target', '?')}"


def find_usages(data: dict, bindings: dict[str, str], kind: str, symbol: str) -> list[SymbolUsage]:
    if kind not in {"tag", "variable", "step"}:
        raise ValueError("Symbol kind must be tag, variable or step")
    uses: list[SymbolUsage] = []
    if kind == "tag":
        if any(row.get("tag") == symbol for row in data.get("tags", [])):
            uses.append(SymbolUsage(kind, symbol, "Tag declarations", "tag", symbol))
        if symbol in bindings:
            uses.append(SymbolUsage(kind, symbol, "Tag mappings", "binding", bindings[symbol]))
        for step in data.get("steps", []):
            location = f"Step {step.get('id', '?')}"
            for field in ("tag", "feedback_tag", "equipment"):
                if step.get(field) == symbol:
                    uses.append(SymbolUsage(kind, symbol, location, field, symbol))
            for child, parent in step.get("tag_aliases", {}).items():
                if parent == symbol:
                    uses.append(SymbolUsage(kind, symbol, location, f"tag alias {child}", parent))
    elif kind == "variable":
        if any(row.get("name") == symbol for row in data.get("variables", [])):
            uses.append(SymbolUsage(kind, symbol, "Memory declarations", "name", symbol))
        for step in data.get("steps", []):
            location = f"Step {step.get('id', '?')}"
            for field in ("variable",):
                if step.get(field) == symbol:
                    uses.append(SymbolUsage(kind, symbol, location, field, symbol))
            for index, row in enumerate(step.get("calculation_rows", []), 1):
                if row.get("variable") == symbol:
                    uses.append(SymbolUsage(kind, symbol, location, f"calculation result {index}", symbol))
            for child, parent in step.get("result_variables", {}).items():
                if parent == symbol:
                    uses.append(SymbolUsage(kind, symbol, location, f"result {child}", parent))
            for key, value in step.get("adapter_results", {}).items():
                if value == symbol:
                    uses.append(SymbolUsage(kind, symbol, location, f"adapter result {key}", value))
    else:
        if any(row.get("id") == symbol for row in data.get("steps", [])):
            uses.append(SymbolUsage(kind, symbol, f"Step {symbol}", "id", symbol))
        for node in (data.get("flow") or {}).get("nodes", []):
            if node.get("step_id") == symbol:
                uses.append(SymbolUsage(kind, symbol, f"Flow node {node.get('id', '?')}", "step_id", symbol))
        for row in data.get("metadata", {}).get("canvas_layout", []):
            if row.get("step_id") == symbol:
                uses.append(SymbolUsage(kind, symbol, "Canvas layout", "step_id", symbol))
        for row in data.get("metadata", {}).get("canvas_routes", []):
            for field in ("source", "target"):
                if row.get(field) == "step:" + symbol:
                    uses.append(SymbolUsage(kind, symbol, "Canvas route", field, row[field]))
    if kind in {"tag", "variable"}:
        for record, field, location in _expressions(data):
            value = record[field][1:] if location.startswith("Step ") and "/ adapter input" in location else record[field]
            if _expression_spans(value, kind, symbol):
                uses.append(SymbolUsage(kind, symbol, location, field, value))
    return uses


def rename_symbol(data: dict, bindings: dict[str, str], kind: str, old: str, new: str) -> tuple[dict, dict, list[SymbolUsage]]:
    old, new = old.strip(), new.strip()
    if not old or not new or old == new:
        raise ValueError("Enter two different non-empty symbol names")
    if kind == "tag" and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+", new):
        raise ValueError("A logical tag must use dotted identifier parts, for example UNIT.PV")
    if kind == "variable" and (not new.isidentifier() or keyword.iskeyword(new)):
        raise ValueError("A variable must be a valid non-keyword expression identifier")
    if kind == "step" and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", new):
        raise ValueError("A step ID must start with a letter and use letters, numbers, underscore, dot or hyphen")
    before = find_usages(data, bindings, kind, old)
    if not before:
        raise ValueError(f"{kind.title()} {old!r} is not used in this procedure")
    if kind == "tag" and (new in bindings or any(row.get("tag") == new for row in data.get("tags", []))):
        raise ValueError(f"Tag {new!r} already exists")
    if kind == "variable" and any(row.get("name") == new for row in data.get("variables", [])):
        raise ValueError(f"Variable {new!r} already exists")
    if kind == "step" and any(row.get("id") == new for row in data.get("steps", [])):
        raise ValueError(f"Step {new!r} already exists")
    result, mapped = deepcopy(data), deepcopy(bindings)
    if kind == "tag":
        for row in result.get("tags", []):
            if row.get("tag") == old:
                row["tag"] = new
        if old in mapped:
            mapped[new] = mapped.pop(old)
        for step in result.get("steps", []):
            for field in ("tag", "feedback_tag", "equipment"):
                if step.get(field) == old:
                    step[field] = new
            step["tag_aliases"] = {key: new if value == old else value for key, value in step.get("tag_aliases", {}).items()}
    elif kind == "variable":
        for row in result.get("variables", []):
            if row.get("name") == old:
                row["name"] = new
        for step in result.get("steps", []):
            if step.get("variable") == old:
                step["variable"] = new
            for row in step.get("calculation_rows", []):
                if row.get("variable") == old:
                    row["variable"] = new
            step["result_variables"] = {key: new if value == old else value for key, value in step.get("result_variables", {}).items()}
            step["adapter_results"] = {key: new if value == old else value for key, value in step.get("adapter_results", {}).items()}
    else:
        for row in result.get("steps", []):
            if row.get("id") == old:
                row["id"] = new
        for node in (result.get("flow") or {}).get("nodes", []):
            if node.get("step_id") == old:
                node["step_id"] = new
        metadata = result.get("metadata", {})
        for row in metadata.get("canvas_layout", []):
            if row.get("step_id") == old:
                row["step_id"] = new
        for route in metadata.get("canvas_routes", []):
            for field in ("source", "target"):
                if route.get(field) == "step:" + old:
                    route[field] = "step:" + new
    if kind in {"tag", "variable"}:
        for record, field, location in _expressions(result):
            prefix = "=" if location.startswith("Step ") and "/ adapter input" in location else ""
            value = record[field][1:] if prefix else record[field]
            changed, _ = rewrite_expression(value, kind, old, new)
            record[field] = prefix + changed
    return result, mapped, before
