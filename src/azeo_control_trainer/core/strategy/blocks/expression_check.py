"""Parse-time checking for CND/ACT expressions — the Parse button's truth.

Azeo's expression editor has a Parse button whose output pane says what
is wrong *before* the module is downloaded. This module is that check for
the trainer's expression language: the same AST shapes `_safe_eval`
executes, validated without executing anything, plus name checking against
the namespace the block will actually provide — so a typo like `INP1` is
caught at edit time instead of surfacing as a Bad verdict on scan 1.
"""
from __future__ import annotations

import ast

#: Names every CND/ACT expression namespace carries besides functions.
BUILTIN_NAMES = (
    tuple(f"IN{i}" for i in range(1, 9))
    + tuple(f"b{i}" for i in range(1, 9))
    + ("x", "y", "z", "dt", "time", "step_time", "True", "False",
       "None")
)

#: Reference functions (the Azeo-style "no wires needed" access):
#: name -> (min args, max args). The first argument is always a quoted
#: reference string; tag()'s optional second is a default, the writers'
#: second is the value.
REFERENCE_ARITY = {
    "param": (1, 1),
    "tag": (1, 2),
    "set_param": (2, 2),
    "set_tag": (2, 2),
    "write_param": (2, 2),
    "get_tag": (1, 2),
}
REFERENCE_FUNCTIONS = tuple(REFERENCE_ARITY)

_ALLOWED_NODES = (
    ast.Expression, ast.Constant, ast.Name, ast.Load,
    ast.BinOp, ast.UnaryOp, ast.Compare, ast.BoolOp, ast.IfExp,
    ast.Call, ast.keyword,
    # operators
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd, ast.Not,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.And, ast.Or, ast.BitAnd, ast.BitOr, ast.BitXor,
)


def known_functions() -> dict:
    """Every callable the expression may use, with its doc for the UI."""
    from .action_block import SAFE_MATH_FUNCTIONS
    from .script_functions import get_script_functions

    out: dict = {}
    merged = dict(SAFE_MATH_FUNCTIONS)
    merged.update(get_script_functions())
    for name, function in sorted(merged.items()):
        doc = (getattr(function, "__doc__", "") or "").strip().splitlines()
        out[name] = doc[0] if doc else ""
    for name in REFERENCE_FUNCTIONS:
        out.setdefault(name, "")
    out["param"] = ("param('BLOCK/TERMINAL') — read another block's "
                    "terminal or parameter in this module")
    out["tag"] = ("tag('STORE.TAG'[, default]) — read a store point; "
                  "the default is what an unwritten tag reads as")
    out["set_param"] = ("set_param('BLOCK/TERMINAL', value) — write an "
                        "UNWIRED input terminal or a config parameter")
    out["set_tag"] = "set_tag('STORE.TAG', value) — write a store point"
    out["write_param"] = ("write_param('NAME', value) — assign a "
                          "writeable module parameter")
    return out


def check_expression(expression: str,
                     extra_names: tuple = ()) -> str | None:
    """None when the expression parses clean; else a message with position.

    The message style is the Azeo parser output's: what is wrong, where,
    and — for an unknown name — what the legal vocabulary is, because the
    fix is almost always a spelling.
    """
    expression = (expression or "").strip()
    if not expression:
        return "The expression is empty (Configuration Error on scan)."
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as error:
        return (f"Syntax error at line {error.lineno}, "
                f"column {error.offset}: {error.msg}")

    functions = known_functions()
    names = set(BUILTIN_NAMES) | set(extra_names) | set(functions)

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            kind = type(node).__name__
            line = getattr(node, "lineno", "?")
            return (f"'{kind}' is not part of the expression language "
                    f"(line {line}) — expressions are arithmetic, "
                    "comparisons, and/or/not, a conditional, and calls to "
                    "the function library.")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                return (f"Only named function calls are allowed "
                        f"(line {node.lineno}).")
            if node.func.id not in functions:
                return (f"Unknown function '{node.func.id}' "
                        f"(line {node.lineno}) — see the Function "
                        "Library for what is available.")
            if node.func.id in REFERENCE_FUNCTIONS:
                fewest, most = REFERENCE_ARITY[node.func.id]
                if not (fewest <= len(node.args) <= most) \
                        or not isinstance(node.args[0], ast.Constant) \
                        or not isinstance(node.args[0].value, str):
                    return (f"{node.func.id}(...) takes a quoted "
                            f"reference string first "
                            f"({fewest}-{most} argument(s), "
                            f"line {node.lineno}).")
        elif isinstance(node, ast.Name):
            if node.id not in names:
                return (f"Unknown variable '{node.id}' "
                        f"(line {node.lineno}) — inputs are IN1..IN8 "
                        "(b1..b8 as booleans), x/y/z, dt, time; use "
                        "param('BLOCK/TERMINAL') or tag('STORE.TAG') "
                        "for direct references.")
    return None
