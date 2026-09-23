"""Authored condition rows, ordered calculations and typed memory references."""
import ast
import math
import operator
from functools import lru_cache

from pydantic import Field, model_validator
from azeo_control_trainer.core.pa_designer.core.expression_engine import SafeExpressionEvaluator, ExpressionError
from azeo_control_trainer.core.pa_designer.core.procedure_model import StrictProcedureModel
from azeo_control_trainer.core.datastore.memory_tags import MemoryTag, memory_name
from azeo_control_trainer.core.pa_designer.core.tag_references import normalize_tag_expression


class PreparedEvaluator(SafeExpressionEvaluator):
    # The upstream interpreter never mutates its AST. Cache syntax, never
    # evaluated values, so long waits still observe every changing input.
    @staticmethod
    @lru_cache(maxsize=256)
    def _parse(expression):
        return SafeExpressionEvaluator._parse(expression)

    @staticmethod
    def _validate_result(value):
        # Preserve the imported engine while bounding total traversal, not
        # just each container: aliased/cyclic inputs defeat per-list limits.
        pending = [(value, 0)]
        count = characters = 0
        while pending:
            item, depth = pending.pop()
            count += 1
            if count > 2048 or depth > 16:
                raise ExpressionError("Expression value exceeds the nesting/item budget")
            kind = type(item)
            if kind in (list, tuple, dict):
                if len(item) > 128:
                    raise ExpressionError("Expression container exceeds 128 items")
                values = (*item.keys(), *item.values()) if kind is dict else item
                pending.extend((child, depth + 1) for child in values)
            else:
                if kind not in (type(None), bool, int, float, str):
                    raise ExpressionError(f"Expression value type {kind.__name__} is not supported")
                SafeExpressionEvaluator._validate_result(item)
                if kind is str:
                    characters += len(item)
                    if characters > 65536:
                        raise ExpressionError("Expression value exceeds the character budget")

    @staticmethod
    def _apply_binop(op, left, right):
        PreparedEvaluator._validate_result(left)
        PreparedEvaluator._validate_result(right)
        if isinstance(op, ast.Mod) and (type(left) not in (int, float) or type(right) not in (int, float)):
            raise ExpressionError("Expression modulo is limited to numeric operands")
        if isinstance(op, ast.Mult):
            sequence, count = (left, right) if type(left) in (str, list, tuple) else (right, left)
            if type(sequence) in (str, list, tuple) and isinstance(count, int):
                limit = 8192 if type(sequence) is str else 128
                if len(sequence) * max(count, 0) > limit:
                    raise ExpressionError("Expression multiplication exceeds the allocation budget")
        result = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                  ast.Div: operator.truediv, ast.Mod: operator.mod}[type(op)](left, right)
        PreparedEvaluator._validate_result(result)
        return result


@lru_cache(maxsize=512)
def prepared_expression(expression):
    normalized, references = normalize_tag_expression(expression)
    tree = PreparedEvaluator._parse(normalized)
    names = frozenset(node.id for node in ast.walk(tree) if isinstance(node, ast.Name))
    return normalized, tuple(references.items()), names


class MemoryValue(MemoryTag):
    # Legacy variables remain local unless explicitly linked to the Tag DB.
    tag_path: str = ""
    operator_tuning: str = Field(default="none", pattern="^(none|next_run|live)$")
    engineering_units: str = Field(default="", max_length=32)

    @model_validator(mode="after")
    def validate_link(self):
        if self.tag_path:
            memory_name(self.tag_path)
            if self.data_type == "any":
                raise ValueError("Shared memory requires an explicit type")
        if self.operator_tuning != "none" and self.data_type == "any":
            raise ValueError("Operator tuning requires an explicit memory type")
        return self

    def checked(self, value):
        PreparedEvaluator._validate_result(value)
        return super().checked(value)


class ConditionRow(StrictProcedureModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$", max_length=48)
    expression: str = Field(min_length=1, max_length=4096)
    stable_for_sec: float = Field(default=0, ge=0, allow_inf_nan=False)


class CalculationRow(StrictProcedureModel):
    variable: str = Field(min_length=1)
    expression: str = Field(min_length=1, max_length=4096)


def row_logic(rows, match="ALL", custom=""):
    ids = [row.id if isinstance(row, ConditionRow) else row["id"] for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("Condition row IDs must be unique")
    expression = custom.strip() or (" and " if match == "ALL" else " or ").join(ids)
    if not expression or len(expression) > 4096:
        raise ValueError("Enter a condition group")
    tree = ast.parse(expression, mode="eval")
    nodes = list(ast.walk(tree))
    if len(nodes) > 256 or any(not isinstance(node, (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.Name, ast.Load)) for node in nodes):
        raise ValueError("Group logic accepts row IDs, AND/OR (and/or), and parentheses only")
    used = {node.id for node in nodes if isinstance(node, ast.Name)}
    if used != set(ids):
        raise ValueError("Group logic must reference every condition row and no unknown rows")
    return expression


def expanded_condition(rows, match="ALL", custom=""):
    expression = row_logic(rows, match, custom)
    values = {row["id"]: row["expression"] for row in rows}

    class Expand(ast.NodeTransformer):
        def visit_Name(self, node):  # noqa: N802
            return ast.parse(values[node.id], mode="eval").body

    return ast.unparse(Expand().visit(ast.parse(expression, mode="eval")))


def validate_expression(expression, memory, tags):
    normalized, references = normalize_tag_expression(expression)
    SafeExpressionEvaluator().validate(normalized, set(memory) | set(references))
    for tag in references.values():
        if tag not in tags:
            raise ValueError(f"Declare and map referenced tag: {tag}")
        if not tags[tag].can_read():
            raise ValueError(f"Cannot read write-only tag: {tag}")


class ContinuousConditions:
    """Each row must qualify independently before the combined dwell starts."""
    def __init__(self, rows, logic, dwell):
        self.rows, self.logic, self.dwell = rows, logic, dwell
        self.since = {}
        self.group_since = None
        self.evaluator = PreparedEvaluator()

    def reset(self):
        self.since.clear()
        self.group_since = None

    def observe(self, now, evaluate):
        if not math.isfinite(now):
            raise ValueError("Condition clock must be finite")
        results, rows, known = {}, [], True
        for row in self.rows:
            value = evaluate(row.expression)
            if value is None:
                known = False
            if not value:
                self.since.pop(row.id, None)
            else:
                self.since.setdefault(row.id, now)
            elapsed = max(0, now - self.since[row.id]) if row.id in self.since else 0
            qualified = bool(value) and elapsed >= row.stable_for_sec
            results[row.id] = qualified
            state = "Uncertain" if value is None else "Satisfied" if qualified else "Timing" if value else "Waiting"
            rows.append({"id": row.id, "condition": row.expression, "state": state,
                         "stable": elapsed, "dwell": row.stable_for_sec,
                         "timer": f"{elapsed:.1f} / {row.stable_for_sec:g} s"})
        satisfied = known and bool(self.evaluator.evaluate(self.logic, results))
        if satisfied:
            if self.group_since is None:
                self.group_since = now
        else:
            self.group_since = None
        stable = max(0, now - self.group_since) if self.group_since is not None else 0
        return {"satisfied": satisfied, "stable": stable, "dwell": self.dwell,
                "conditions": tuple(rows), "complete": satisfied and stable >= self.dwell}
