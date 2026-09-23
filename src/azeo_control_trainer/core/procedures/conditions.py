"""Expose the same Boolean clauses the runner evaluates to the operator HMI."""
import ast
from functools import lru_cache


@lru_cache(maxsize=512)
def condition_clauses(expression):
    tree = ast.parse(expression, mode="eval").body
    if isinstance(tree, ast.BoolOp):
        return ("ALL" if isinstance(tree.op, ast.And) else "ANY",
                tuple(ast.unparse(value) for value in tree.values))
    return "ALL", (expression,)


def evaluate_conditions(expression, evaluate):
    match, clauses = condition_clauses(expression)
    # Evaluate every displayed condition, including unsatisfied OR branches.
    # A short-circuited expression must not paint an unobserved row as healthy.
    values = tuple(evaluate(clause) for clause in clauses)
    rows = tuple({"condition": clause, "state": "Uncertain" if value is None else "Satisfied" if value else "Waiting"}
                 for clause, value in zip(clauses, values))
    satisfied = all(value is not None for value in values) and (all(values) if match == "ALL" else any(values))
    return satisfied, match, rows
