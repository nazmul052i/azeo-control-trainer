from __future__ import annotations

import ast
from dataclasses import dataclass


@dataclass(frozen=True)
class TagReference:
    tag: str
    start: int
    end: int


def _attribute_name(node: ast.AST) -> str | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    else:
        return None
    if len(parts) < 2:
        return None
    return ".".join(reversed(parts))


def extract_tag_references(expression: str) -> list[TagReference]:
    """Return dotted tag references from a condition without matching strings."""

    tree = ast.parse(expression, mode="eval")
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    refs: list[TagReference] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if isinstance(parents.get(node), ast.Attribute):
            continue
        tag = _attribute_name(node)
        if tag is None or node.end_col_offset is None:
            continue
        refs.append(TagReference(tag=tag, start=node.col_offset, end=node.end_col_offset))
    return sorted(refs, key=lambda ref: (ref.start, ref.end))


def referenced_tags_in_expression(expression: str) -> set[str]:
    return {ref.tag for ref in extract_tag_references(expression)}


def normalize_tag_expression(expression: str) -> tuple[str, dict[str, str]]:
    """Replace dotted tag references with safe variable names.

    Returns the normalized expression and a mapping of variable name to original tag.
    """

    refs = extract_tag_references(expression)
    tag_to_var: dict[str, str] = {}
    var_to_tag: dict[str, str] = {}
    replacements: list[tuple[int, int, str]] = []
    for ref in refs:
        var_name = tag_to_var.get(ref.tag)
        if var_name is None:
            var_name = f"tag_{len(tag_to_var)}"
            tag_to_var[ref.tag] = var_name
            var_to_tag[var_name] = ref.tag
        replacements.append((ref.start, ref.end, var_name))

    normalized = expression
    for start, end, replacement in sorted(replacements, reverse=True):
        normalized = normalized[:start] + replacement + normalized[end:]
    return normalized, var_to_tag
