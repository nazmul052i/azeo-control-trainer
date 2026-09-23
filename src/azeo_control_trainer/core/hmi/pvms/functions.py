"""Conversion functions — Azeo Operator Station's typed formula objects, Qt-free.

The papers' mechanism for value-driven animation without scripting:
"functions provide a means to create logic that converts values of one
type into values of a different type" (graphics paper p.13), and
"color animations require the use of conversion functions in handling
the color table lookup" (working-pvms p.20). A function is a
configured object — inputs, algorithm, typed return — published on its
own like a standard, never per display.

Two algorithms cover the classroom cases:

- **threshold** — the colour-table lookup: ordered ``(limit, output)``
  rows; the value takes the output of the highest limit it meets,
  else the default. `PV >= 80 → red, >= 60 → amber, else grey`.
- **scale** — linear in→out mapping, clamped: the fill-percent
  animation's arithmetic (`0..2000 gal → 0..100 %`).

Evaluation is total: a broken definition or a non-numeric value
returns None, and the caller treats None as "no animation this scan"
— never an invented output.
"""
from __future__ import annotations

import json
import ast
import operator
from pathlib import Path

MAX_INPUTS = 5
MAX_CALCULATIONS = 5
FUNCTION_TYPES = ("Boolean", "Color", "Font", "Measurement", "Number",
                  "String")
LOGIC_RULE, LOGIC_VALUE = "rule", "value"

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Mod: operator.mod,
        ast.Pow: operator.pow}
_CMP = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
        ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}


def _formula(text: str, values: dict):
    """Evaluate declarative function arithmetic without Python execution."""
    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in values:
                raise ValueError(node.id)
            return values[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            return _BIN[type(node.op)](visit(node.left), visit(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(
                node.op, (ast.UAdd, ast.USub, ast.Not)):
            value = visit(node.operand)
            return (+value if isinstance(node.op, ast.UAdd) else
                    -value if isinstance(node.op, ast.USub) else not value)
        if isinstance(node, ast.BoolOp):
            values_ = [bool(visit(one)) for one in node.values]
            return all(values_) if isinstance(node.op, ast.And) else any(values_)
        if isinstance(node, ast.Compare):
            left = visit(node.left)
            for op, comparator in zip(node.ops, node.comparators):
                right = visit(comparator)
                if type(op) not in _CMP or not _CMP[type(op)](left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            return visit(node.body if visit(node.test) else node.orelse)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in ("abs", "min", "max", "round"):
            return {"abs": abs, "min": min, "max": max,
                    "round": round}[node.func.id](
                        *(visit(arg) for arg in node.args))
        raise ValueError(type(node).__name__)

    return visit(ast.parse(str(text), mode="eval"))


def _coerce(value, value_type: str):
    if value_type == "Boolean":
        return bool(value)
    if value_type in ("Measurement", "Number"):
        return float(value)
    return str(value)


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class FunctionStore:
    """`_functions.json` beside the displays — like the standards."""

    def __init__(self, root):
        self.path = Path(root) / "_functions.json"
        self.entries: dict = {}
        self._stamp: object = False     # never a real mtime
        self._reload()

    def _mtime(self):
        try:
            return self.path.stat().st_mtime_ns
        except OSError:
            return None

    def _reload(self) -> None:
        """Fresh disk state. Every mutator reloads first — stores are
        constructed freely (window, pane, dialog), and a stale
        snapshot writing itself back whole would silently drop what
        another instance authored in between."""
        self._stamp = self._mtime()
        self.entries = {}
        if self.path.exists():
            try:
                loaded = json.loads(
                    self.path.read_text(encoding="utf-8"))
            except Exception:                       # noqa: BLE001
                loaded = None
            # A hand-edited file can hold anything; only a mapping is
            # a function table, and `eval` promises never to invent an
            # answer from one it cannot read.
            self.entries = loaded if isinstance(loaded, dict) else {}

    def refresh(self) -> bool:
        """Reload only if the file changed. The studio polls this four
        times a second while an animation is on the display, and
        re-reading and re-parsing the whole document every tick is
        I/O spent on a document that changes when an Author saves."""
        if self._mtime() == self._stamp:
            return False
        self._reload()
        return True

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.entries, indent=2, ensure_ascii=False)
            + "\n", encoding="utf-8", newline="\n")
        self._stamp = self._mtime()     # our own write is not a change

    def names(self) -> list:
        return sorted(self.entries)

    def add_threshold(self, name: str, rows, default) -> dict:
        """rows: [(limit, output), …] — stored sorted descending so
        evaluation order is the definition, not the author's typing
        order."""
        self._reload()
        entry = {"type": "threshold",
                 "rows": sorted(([float(limit), output]
                                 for limit, output in rows),
                                key=lambda r: -r[0]),
                 "default": default}
        self.entries[name] = entry
        self.save()
        return entry

    def add_scale(self, name: str, in_lo, in_hi, out_lo,
                  out_hi) -> dict:
        self._reload()
        entry = {"type": "scale",
                 "in_lo": float(in_lo), "in_hi": float(in_hi),
                 "out_lo": float(out_lo), "out_hi": float(out_hi)}
        self.entries[name] = entry
        self.save()
        return entry

    def add_formula(self, name: str, inputs, *, calculations=(),
                    expression: str = "", rules=(), default=None,
                    logic: str = LOGIC_VALUE,
                    return_type: str = "Number") -> dict:
        """Add the manual's typed, up-to-five-input function form."""
        inputs = [dict(row) for row in inputs][:MAX_INPUTS]
        calculations = [dict(row) for row in calculations][
            :MAX_CALCULATIONS]
        if not inputs or any(not row.get("name") for row in inputs):
            raise ValueError("a function needs named inputs")
        if len({row["name"] for row in inputs}) != len(inputs):
            raise ValueError("function input names must be unique")
        if logic not in (LOGIC_RULE, LOGIC_VALUE):
            raise ValueError("logic must be rule or value")
        if return_type not in FUNCTION_TYPES:
            raise ValueError(f"unknown return type {return_type}")
        entry = {"type": "formula", "logic": logic,
                 "inputs": inputs, "calculations": calculations,
                 "return_type": return_type}
        if logic == LOGIC_RULE:
            entry["rules"] = [dict(row) for row in rules]
            entry["default"] = default
        else:
            entry["expression"] = expression
        self._reload()
        self.entries[name] = entry
        self.save()
        return entry

    def remove(self, name: str) -> bool:
        self._reload()
        if name in self.entries:
            del self.entries[name]
            self.save()
            return True
        return False

    def eval(self, name: str, value=None, **supplied):
        """One value through one function; None means no answer —
        the caller must not invent one.

        Total by construction: a hand-edited entry missing its `type`,
        its rows or a scale bound answers None like any other
        unreadable definition, rather than raising into a paint loop.
        """
        entry = self.entries.get(name)
        if not isinstance(entry, dict):
            return None
        kind = entry.get("type")
        if kind == "formula":
            try:
                values = dict(value) if isinstance(value, dict) else {}
                if not values and entry.get("inputs"):
                    values[entry["inputs"][0]["name"]] = value
                values.update(supplied)
                for spec in entry.get("inputs", ()):
                    raw = values.get(spec["name"], spec.get("default"))
                    values[spec["name"]] = _coerce(
                        raw, spec.get("type", "Number"))
                for calculation in entry.get("calculations", ()):
                    values[calculation["name"]] = _formula(
                        calculation.get("expression", ""), values)
                if entry.get("logic") == LOGIC_RULE:
                    answer = entry.get("default")
                    for rule in entry.get("rules", ()):
                        if bool(_formula(rule.get("when", "False"), values)):
                            answer = rule.get("value")
                            if rule.get("expression"):
                                answer = _formula(rule["expression"], values)
                            break
                else:
                    answer = _formula(entry.get("expression", ""), values)
                return _coerce(answer, entry.get("return_type", "Number"))
            except (KeyError, TypeError, ValueError, ZeroDivisionError,
                    SyntaxError, OverflowError):
                return None
        number = _to_float(value)
        if number is None:
            return None
        if kind == "threshold":
            for row in entry.get("rows") or []:
                try:
                    limit, output = row
                    limit = float(limit)
                except (TypeError, ValueError):
                    continue
                if number >= limit:
                    return output
            return entry.get("default")
        if kind == "scale":
            try:
                in_lo = float(entry["in_lo"])
                in_hi = float(entry["in_hi"])
                out_lo = float(entry["out_lo"])
                out_hi = float(entry["out_hi"])
            except (KeyError, TypeError, ValueError):
                return None
            span = in_hi - in_lo
            if abs(span) < 1e-12:
                return None
            fraction = max(0.0, min(1.0, (number - in_lo) / span))
            return out_lo + fraction * (out_hi - out_lo)
        return None
