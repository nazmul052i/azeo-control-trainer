"""The expression binding's evaluator — arithmetic ONLY, on purpose (§9.1).

Derived display values: deviation, percent of span, a difference of two
flows. The proposal says "resist growing a language", and the repo's own
The shared HMI expression model states the reason: the moment an
expression can decide something, it is control logic living in a picture.
So: arithmetic, comparisons for nothing — not even comparisons. Names
resolve to other bindings' values; quality is the worst of the inputs;
errors are Bad, never zero.

Hand-rolled AST whitelist rather than a filtered eval, same as everywhere
else in this repo: a sandbox is a thing that leaks.
"""
from __future__ import annotations

import ast

_ALLOWED = (ast.Expression, ast.Constant, ast.Name, ast.Load,
            ast.BinOp, ast.UnaryOp,
            ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
            ast.Pow, ast.USub, ast.UAdd,
            ast.Call)

_FUNCTIONS = {"abs": abs, "min": min, "max": max, "round": round}


def referenced_names(expression: str) -> set[str]:
    """The ref names an expression uses — the dependency edge set."""
    tree = ast.parse(expression, mode="eval")
    return {node.id for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id not in _FUNCTIONS}


def check(expression: str, known_refs: set[str]) -> str | None:
    """None when clean; else a readable message (bind-time, not scan-time)."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as error:
        return f"syntax error at column {error.offset}: {error.msg}"
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            return (f"'{type(node).__name__}' is not part of the display "
                    "expression language — arithmetic over refs only; "
                    "anything that decides is control logic and belongs "
                    "in a module")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) \
                    or node.func.id not in _FUNCTIONS:
                return ("only abs/min/max/round may be called in a "
                        "display expression")
        elif isinstance(node, ast.Name) and node.id not in _FUNCTIONS \
                and node.id not in known_refs:
            return (f"unknown ref '{node.id}' — declare it in the "
                    "binding's refs")
    return None


def evaluate(expression: str, refs: dict) -> float:
    """Evaluate against resolved ref values. Raises on any failure —
    the engine turns that into Bad quality, never into a number."""
    tree = ast.parse(expression, mode="eval")
    return _eval(tree.body, refs)


def _eval(node, refs):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"non-numeric constant {node.value!r}")
    if isinstance(node, ast.Name):
        if node.id in _FUNCTIONS:
            raise ValueError(f"'{node.id}' is a function, call it")
        return float(refs[node.id])
    if isinstance(node, ast.BinOp):
        left, right = _eval(node.left, refs), _eval(node.right, refs)
        op = type(node.op)
        if op is ast.Add:
            return left + right
        if op is ast.Sub:
            return left - right
        if op is ast.Mult:
            return left * right
        if op is ast.Div:
            return left / right
        if op is ast.FloorDiv:
            return left // right
        if op is ast.Mod:
            return left % right
        if op is ast.Pow:
            return left ** right
        raise ValueError(f"operator {op.__name__}")
    if isinstance(node, ast.UnaryOp):
        operand = _eval(node.operand, refs)
        return -operand if isinstance(node.op, ast.USub) else +operand
    if isinstance(node, ast.Call):
        return _FUNCTIONS[node.func.id](
            *[_eval(a, refs) for a in node.args])
    raise ValueError(f"node {type(node).__name__}")
