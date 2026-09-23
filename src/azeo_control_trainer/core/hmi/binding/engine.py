"""The binding engine — the piece that makes this a builder (§9).

Three binding kinds:

- **direct** — a literal `MODULE/BLOCK/TERM` path;
- **indirect** — a template resolved against parameters, `"{path}/PV"`
  with `params={"path": "FIC-101/PID1"}`: the mechanism that lets ONE
  PVM class serve every controller;
- **expression** — arithmetic over named refs (see `expression.py`),
  each ref itself a direct/indirect path or a previously bound name.

Cycles are rejected **at bind time** with a readable message, never
discovered at evaluation time. `poll()` is the frame tick: it
re-evaluates every binding against the source, retains last-good history
(display state 1 needs the value and its timestamp after quality goes
Bad — a stateful requirement, held here and nowhere else), and fires
`on_change` only for bindings whose result actually moved — a burst of
source changes coalesces into one notification per binding per poll.

The engine never touches Qt and never touches a socket: sources do the
reading (`source.py`), and an in-process source is snapshot-driven while
a UA source would map monitored items onto the same invalidation path.
"""
from __future__ import annotations

import dataclasses
import string
import time

from azeo_control_trainer.core.strategy.model.terminal import Quality
from . import expression as expr
from .result import UNRESOLVED, BindingResult

_formatter = string.Formatter()


def format_template(template: str, params: dict) -> str:
    """`str.format` with dotted field names taken literally.

    A PVM configuration property placeholder can be a subproperty —
    `{Link.FB}` — which plain `.format()` would parse as attribute
    access on a field called Link. Here every field name is a literal
    key into `params`; a missing key raises KeyError exactly like
    `.format()` does, so bind() keeps refusing typos at bind time.
    """
    out = []
    for literal, field, _spec, _conv in _formatter.parse(template):
        out.append(literal)
        if field is None:
            continue
        if field not in params:
            raise KeyError(f"'{field}'")
        out.append(str(params[field]))
    return "".join(out)
class BindingError(ValueError):
    """A binding refused at bind time — the message says why."""


class Binding:
    """One live binding. Read `.result`; subscribe with `on_change`."""

    def __init__(self, key: str, kind: str, path: str = "",
                 expression: str = "", refs: dict | None = None,
                 on_change=None):
        self.key = key
        self.kind = kind                    # direct | expression
        self.path = path
        self.expression = expression
        self.refs = dict(refs or {})
        self.on_change = on_change
        self.result: BindingResult = UNRESOLVED
        self.revision = 0
        self._last_good: tuple = (None, None)

    def _retain(self, result: BindingResult) -> BindingResult:
        """Fold last-good history into the result.

        The timestamp advances only when the good value MOVES — restamping
        an unchanged value every poll would make every result compare
        unequal and defeat change coalescing.
        """
        if result.quality != Quality.BAD and result.value is not None \
                and result.value != self._last_good[0]:
            self._last_good = (result.value, time.time())
        good_value, good_at = self._last_good
        return dataclasses.replace(result, last_good_value=good_value,
                                   last_good_at=good_at)


class BindingEngine:
    def __init__(self, source):
        self._source = source
        self._bindings: dict[str, Binding] = {}
        self._counter = 0

    # ------------------------------------------------------------- binding
    @property
    def monitored_count(self) -> int:
        return len(self._bindings)

    def bind(self, template: str, params: dict | None = None,
             on_change=None, name: str | None = None) -> Binding:
        """Bind a direct or indirect path. `{param}` placeholders resolve
        against `params` at bind time; a placeholder with no parameter is
        refused here, not discovered as Bad on screen."""
        try:
            path = format_template(template, params or {})
        except (KeyError, IndexError) as error:
            raise BindingError(
                f"unresolved placeholder {error} in '{template}' — "
                f"supply it in params") from None
        binding = Binding(self._key(name), "direct", path=path,
                          on_change=on_change)
        self._bindings[binding.key] = binding
        self._evaluate(binding, notify=False)
        return binding

    def bind_expression(self, expression_text: str, refs: dict,
                        params: dict | None = None, on_change=None,
                        name: str | None = None) -> Binding:
        """Bind an expression over named refs.

        Each ref maps a name to a path template, or to the name of an
        existing expression binding. Cycles among expression bindings are
        rejected NOW, with the chain in the message.
        """
        resolved_refs: dict[str, str] = {}
        for ref_name, target in refs.items():
            if target in self._bindings \
                    and self._bindings[target].kind == "expression":
                resolved_refs[ref_name] = target        # binding ref
            else:
                try:
                    resolved_refs[ref_name] = format_template(
                        target, params or {})
                except (KeyError, IndexError) as error:
                    raise BindingError(
                        f"unresolved placeholder {error} in ref "
                        f"'{ref_name}' ('{target}')") from None
        problem = expr.check(expression_text, set(resolved_refs))
        if problem:
            raise BindingError(problem)

        binding = Binding(self._key(name), "expression",
                          expression=expression_text,
                          refs=resolved_refs, on_change=on_change)
        cycle = self._find_cycle(binding)
        if cycle:
            raise BindingError(
                "cyclic expression binding: " + " -> ".join(cycle)
                + " — a display value cannot depend on itself")
        self._bindings[binding.key] = binding
        self._evaluate(binding, notify=False)
        return binding

    def unbind(self, binding: Binding) -> None:
        self._bindings.pop(binding.key, None)

    # ------------------------------------------------------------- writing
    def can_write(self, path: str):
        """Ask the source whether an operator may write this path."""
        checker = getattr(self._source, "can_write", None)
        if checker is None:
            from .source import WriteResult
            return WriteResult(False, "this data source is read-only")
        return checker(path)

    def write(self, path: str, value):
        """Send an operator write through the source's checked path."""
        writer = getattr(self._source, "write", None)
        if writer is None:
            from .source import WriteResult
            return WriteResult(False, "this data source is read-only")
        result = writer(path, value)
        # Re-evaluate now so a control's feedback does not wait for the
        # next 250 ms display tick when the block accepted synchronously.
        self.poll()
        return result

    # ------------------------------------------------------------ the tick
    def poll(self, bindings=None) -> int:
        """Refresh all bindings, or one owner's bindings and their dependencies.

        Each selected binding notifies at most once. Omitting the selection
        retains the full refresh used for immediate operator-write feedback.
        """
        from contextlib import nullcontext
        ordered = None if bindings is None else self._dependency_order(bindings)
        if ordered == []:
            return 0
        snapshot = getattr(self._source, "snapshot", nullcontext)
        with snapshot():
            return self._poll(ordered)

    def _dependency_order(self, bindings):
        ordered, seen = [], set()
        pending = [(binding, False) for binding in reversed(tuple(bindings))]
        while pending:
            binding, ready = pending.pop()
            if ready:
                ordered.append(binding)
                continue
            if self._bindings.get(binding.key) is not binding or binding.key in seen:
                continue
            seen.add(binding.key)
            pending.append((binding, True))
            if binding.kind == "expression":
                for target in reversed(tuple(binding.refs.values())):
                    dependency = self._bindings.get(target)
                    if dependency is not None:
                        pending.append((dependency, False))
        return ordered

    def _poll(self, ordered=None) -> int:
        changed = 0
        # Expressions evaluate after directs so a same-poll dependency
        # sees this poll's values, and chained expressions resolve in
        # dependency order (cycles were rejected at bind).
        if ordered is None:
            ordered = sorted(self._bindings.values(),
                             key=lambda b: b.kind == "expression")
        for binding in ordered:
            # A notification may close another owner or replace its binding.
            if self._bindings.get(binding.key) is not binding:
                continue
            if self._evaluate(binding, notify=True):
                changed += 1
        return changed

    # ------------------------------------------------------------ internals
    def _key(self, name: str | None) -> str:
        if name:
            if name in self._bindings:
                raise BindingError(f"binding name '{name}' already bound")
            return name
        self._counter += 1
        return f"_b{self._counter}"

    def _find_cycle(self, candidate: Binding) -> list[str] | None:
        graph = {key: [t for t in b.refs.values() if t in self._bindings
                       or t == candidate.key]
                 for key, b in self._bindings.items()
                 if b.kind == "expression"}
        graph[candidate.key] = [t for t in candidate.refs.values()
                                if t in self._bindings
                                or t == candidate.key]
        path: list[str] = []
        visiting: set[str] = set()

        def visit(key: str) -> list[str] | None:
            if key in visiting:
                return path[path.index(key):] + [key]
            if key not in graph:
                return None
            visiting.add(key)
            path.append(key)
            for target in graph[key]:
                found = visit(target)
                if found:
                    return found
            path.pop()
            visiting.discard(key)
            return None

        return visit(candidate.key)

    def _evaluate(self, binding: Binding, notify: bool) -> bool:
        if binding.kind == "direct":
            raw = self._source.read(binding.path)
        else:
            raw = self._evaluate_expression(binding)
        result = binding._retain(raw)
        moved = result != binding.result
        binding.result = result
        if moved:
            binding.revision += 1
        if moved and notify and binding.on_change is not None:
            try:
                binding.on_change(result)
            except Exception:                       # noqa: BLE001
                pass
        return moved

    def _evaluate_expression(self, binding: Binding) -> BindingResult:
        values: dict[str, float] = {}
        worst = Quality.GOOD
        any_forced = False
        for name, target in binding.refs.items():
            if target in self._bindings:
                ref_result = self._bindings[target].result
            else:
                ref_result = self._source.read(target)
            if ref_result.quality.value > worst.value:
                worst = ref_result.quality
            any_forced = any_forced or ref_result.forced
            try:
                values[name] = float(ref_result.value)
            except (TypeError, ValueError):
                worst = Quality.BAD
                values[name] = 0.0
        if worst == Quality.BAD:
            return BindingResult(quality=Quality.BAD, forced=any_forced)
        try:
            value = expr.evaluate(binding.expression, values)
        except Exception:                           # noqa: BLE001
            return BindingResult(quality=Quality.BAD, forced=any_forced)
        return BindingResult(value=value, quality=worst,
                             forced=any_forced)
