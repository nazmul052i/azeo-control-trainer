import ast
import math
import operator as op
import statistics
from typing import Any, Iterable, Mapping

_ALLOWED_BINOPS = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv, ast.Mod: op.mod}
_ALLOWED_CMPOPS = {ast.Eq: op.eq, ast.NotEq: op.ne, ast.Lt: op.lt, ast.LtE: op.le, ast.Gt: op.gt, ast.GtE: op.ge}
_ALLOWED_BOOLOPS = {ast.And: all, ast.Or: any}
_ALLOWED_UNARYOPS = {ast.Not: op.not_, ast.USub: op.neg, ast.UAdd: op.pos}
_ALLOWED_FUNCTIONS = {
    "abs": abs,
    "avg": statistics.fmean,
    "len": len,
    "max": max,
    "min": min,
    "round": round,
    "sum": sum,
}
_MAX_EXPRESSION_CHARS = 4096
_MAX_AST_NODES = 256
_MAX_CONTAINER_ITEMS = 128
_MAX_STRING_CHARS = 8192
_MAX_INTEGER_BITS = 256

class ExpressionError(ValueError):
    pass

class SafeExpressionEvaluator:
    """Small safe evaluator for procedure conditions."""

    def validate(self, expression: str, variable_names: Iterable[str]) -> None:
        """Validate syntax, allowed operations, calls, and variable names without executing."""
        try:
            tree = self._parse(expression)
            self._validate_node(tree.body, set(variable_names))
        except Exception as exc:
            if isinstance(exc, ExpressionError):
                raise
            raise ExpressionError(f"Invalid expression '{expression}': {exc}") from exc

    def evaluate(self, expression: str, variables: Mapping[str, Any]) -> Any:
        try:
            tree = self._parse(expression)
            result = self._eval(tree.body, variables)
            self._validate_result(result)
            return result
        except Exception as exc:
            if isinstance(exc, ExpressionError):
                raise
            raise ExpressionError(f"Invalid expression '{expression}': {exc}") from exc

    @staticmethod
    def _parse(expression: str) -> ast.Expression:
        if len(expression) > _MAX_EXPRESSION_CHARS:
            raise ExpressionError(
                f"Expression exceeds {_MAX_EXPRESSION_CHARS} characters"
            )
        tree = ast.parse(expression, mode="eval")
        count = sum(1 for _ in ast.walk(tree))
        if count > _MAX_AST_NODES:
            raise ExpressionError(f"Expression exceeds {_MAX_AST_NODES} syntax nodes")
        return tree

    @staticmethod
    def _validate_result(value: Any) -> None:
        if not isinstance(value, (type(None), bool, int, float, str, list, tuple, dict)):
            raise ExpressionError(
                f"Expression value type {type(value).__name__} is not supported"
            )
        if isinstance(value, float) and not math.isfinite(value):
            raise ExpressionError("Expression produced a non-finite numeric result")
        if isinstance(value, str) and len(value) > _MAX_STRING_CHARS:
            raise ExpressionError(f"Expression string result exceeds {_MAX_STRING_CHARS} characters")
        if isinstance(value, int) and not isinstance(value, bool) and value.bit_length() > _MAX_INTEGER_BITS:
            raise ExpressionError(f"Expression integer result exceeds {_MAX_INTEGER_BITS} bits")
        if isinstance(value, (list, tuple)):
            if len(value) > _MAX_CONTAINER_ITEMS:
                raise ExpressionError(f"Expression container exceeds {_MAX_CONTAINER_ITEMS} items")
            for item in value:
                SafeExpressionEvaluator._validate_result(item)
        if isinstance(value, dict):
            if len(value) > _MAX_CONTAINER_ITEMS:
                raise ExpressionError(f"Expression mapping exceeds {_MAX_CONTAINER_ITEMS} items")
            for key, item in value.items():
                SafeExpressionEvaluator._validate_result(key)
                SafeExpressionEvaluator._validate_result(item)

    @staticmethod
    def _apply_binop(operator_node: ast.operator, left: Any, right: Any) -> Any:
        if isinstance(operator_node, ast.Mod) and not (
            isinstance(left, (int, float)) and not isinstance(left, bool)
            and isinstance(right, (int, float)) and not isinstance(right, bool)
        ):
            raise ExpressionError("Expression modulo is limited to numeric operands")
        if isinstance(operator_node, ast.Mult):
            sequence, count = (left, right) if isinstance(left, (str, list, tuple)) else (right, left)
            if isinstance(sequence, (str, list, tuple)) and isinstance(count, int):
                projected = len(sequence) * max(count, 0)
                limit = _MAX_STRING_CHARS if isinstance(sequence, str) else _MAX_CONTAINER_ITEMS
                if projected > limit:
                    raise ExpressionError(f"Expression multiplication result exceeds {limit} items")
        result = _ALLOWED_BINOPS[type(operator_node)](left, right)
        SafeExpressionEvaluator._validate_result(result)
        return result

    def _validate_node(self, node: ast.AST, variable_names: set[str]) -> None:
        if isinstance(node, ast.Constant):
            self._validate_result(node.value)
            return
        if isinstance(node, ast.Name):
            if node.id not in variable_names:
                raise ExpressionError(f"Unknown variable '{node.id}'")
            return
        if isinstance(node, (ast.List, ast.Tuple)):
            if len(node.elts) > _MAX_CONTAINER_ITEMS:
                raise ExpressionError(f"Expression literal exceeds {_MAX_CONTAINER_ITEMS} items")
            for item in node.elts:
                self._validate_node(item, variable_names)
            return
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCTIONS:
                raise ExpressionError("Function is not allowed")
            if node.keywords:
                raise ExpressionError("Keyword arguments are not allowed")
            for argument in node.args:
                self._validate_node(argument, variable_names)
            return
        if isinstance(node, ast.BoolOp):
            if type(node.op) not in _ALLOWED_BOOLOPS:
                raise ExpressionError("Boolean operator not allowed")
            for value in node.values:
                self._validate_node(value, variable_names)
            return
        if isinstance(node, ast.BinOp):
            if type(node.op) not in _ALLOWED_BINOPS:
                raise ExpressionError("Binary operator not allowed")
            self._validate_node(node.left, variable_names)
            self._validate_node(node.right, variable_names)
            return
        if isinstance(node, ast.UnaryOp):
            if type(node.op) not in _ALLOWED_UNARYOPS:
                raise ExpressionError("Unary operator not allowed")
            self._validate_node(node.operand, variable_names)
            return
        if isinstance(node, ast.Compare):
            self._validate_node(node.left, variable_names)
            for operator_node, comparator in zip(node.ops, node.comparators):
                if type(operator_node) not in _ALLOWED_CMPOPS:
                    raise ExpressionError("Comparison operator not allowed")
                self._validate_node(comparator, variable_names)
            return
        raise ExpressionError(f"Unsupported expression element: {type(node).__name__}")

    def _eval(self, node: ast.AST, variables: Mapping[str, Any]) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in variables:
                raise ExpressionError(f"Unknown variable '{node.id}'")
            value = variables[node.id]
            self._validate_result(value)
            return value
        if isinstance(node, (ast.List, ast.Tuple)):
            return [self._eval(item, variables) for item in node.elts]
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCTIONS:
                raise ExpressionError("Function is not allowed")
            if node.keywords:
                raise ExpressionError("Keyword arguments are not allowed")
            arguments = [self._eval(argument, variables) for argument in node.args]
            result = _ALLOWED_FUNCTIONS[node.func.id](*arguments)
            self._validate_result(result)
            return result
        if isinstance(node, ast.BoolOp):
            if type(node.op) not in _ALLOWED_BOOLOPS:
                raise ExpressionError("Boolean operator not allowed")
            result = self._eval(node.values[0], variables)
            for value_node in node.values[1:]:
                if isinstance(node.op, ast.And) and not result:
                    return result
                if isinstance(node.op, ast.Or) and result:
                    return result
                result = self._eval(value_node, variables)
            return result
        if isinstance(node, ast.BinOp):
            if type(node.op) not in _ALLOWED_BINOPS:
                raise ExpressionError("Binary operator not allowed")
            return self._apply_binop(
                node.op,
                self._eval(node.left, variables),
                self._eval(node.right, variables),
            )
        if isinstance(node, ast.UnaryOp):
            if type(node.op) not in _ALLOWED_UNARYOPS:
                raise ExpressionError("Unary operator not allowed")
            return _ALLOWED_UNARYOPS[type(node.op)](self._eval(node.operand, variables))
        if isinstance(node, ast.Compare):
            left = self._eval(node.left, variables)
            for operator_node, comparator in zip(node.ops, node.comparators):
                if type(operator_node) not in _ALLOWED_CMPOPS:
                    raise ExpressionError("Comparison operator not allowed")
                right = self._eval(comparator, variables)
                if not _ALLOWED_CMPOPS[type(operator_node)](left, right):
                    return False
                left = right
            return True
        raise ExpressionError(f"Unsupported expression element: {type(node).__name__}")
