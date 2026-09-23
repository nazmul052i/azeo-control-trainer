"""SFC as a chart in one block — adapted from azeo_dcs's design.

The trainer's original SFC is blocks pretending to be a chart: TRANSITION
blocks wired through the generic FBD canvas, with the compiler's cycle
exemption papering over the mismatch (SEQ-101 is 20 blocks and 29 wires).
Azeo's SFC is nothing like that — and Azeo is also explicit that its
two languages *mix on one sheet*, which means an SFC is properly a
**block**, not a different kind of module. That is the design lifted here
from the sibling project (`azeo_dcs/azeo/control_designer/sfc.py`, same
author): a chart of steps and transitions living inside a single block's
configuration, executing natively — no generated wiring, no fake graph.

The chart follows the Azeo manual's worked example: steps carrying
**actions** with the two qualifiers the exercise uses —

``P`` (Pulse)
    runs once, on the scan the step goes active; after that the assigned
    destination keeps its value.
``N`` (Non-stored)
    runs every scan the step is active — for an action that must retry
    ("it waits until the actual mode is Auto; it would fail on the first
    try and never be set").

— and transitions carrying **conditions**. Both are written in the
trainer's own expression language (the CND/ACT one, with its editor and
its Parse button): conditions read via ``param('PID1/PV')`` /
``tag('LI-101.PV')`` / bare module parameters; actions act via
``set_param('PID1/SP', 50)``, ``write_param('SPHI', 1)`` or
``set_tag(...)``. ``set_param`` refuses a *wired* input terminal with the
reason logged — a wire re-asserts the value next scan (hard-won item 27),
so accepting the write would be a sequence that silently does nothing.

Execution semantics carried over intact, each one load-bearing:

- **first true transition wins**, in declaration order — a chart that
  could take two at once has a race in it; ordering makes it repeatable;
- an **empty target is termination**: the chart completes and holds, and
  only RESET_D restarts it — otherwise it would loop the next scan;
- **EN_D low holds** the sequence exactly where it is, doing nothing,
  which is what an operator pressing HOLD is asking for;
- the **completed path and visit counts persist in block state**, because
  a sequence display must say how the procedure got here, not only where
  it is.

Divergences from azeo_dcs, named: named sets are not carried (our
expression language has no ``'SET:STATE'`` literal form yet), and
operator start-gating is a plain module parameter the chart's own first
transition can test, rather than a special-cased gate.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from ..model.block_registry import register_block
from ..model.block_base import BlockCategory, BlockStatus, FunctionBlock
from ..model.terminal import DataType, LimitStatus, Quality

log = logging.getLogger("strategy.sfc_chart")

#: Action qualifier -> what it means, for the UI and the help. Keeping the
#: semantics in the model (rather than merely adding editor choices) matters:
#: a qualifier that only renders is an unsafe promise to an engineer.
QUALIFIERS = {
    "P": "Pulse — runs once, on the scan the step goes active",
    "N": "Non-stored — runs every scan while the step is active",
    "S": "Stored — runs every scan until a matching Reset action",
    "R": "Reset — stops the stored action named by Target",
    "L": "Limited — runs while active for at most Time seconds",
    "D": "Delayed — starts after Time seconds while the step is active",
}


@dataclass
class ChartAction:
    """One thing a step does."""

    id: str
    name: str = ""
    description: str = ""
    qualifier: str = "P"
    expression: str = ""
    time_s: float = 0.0
    target: str = ""


@dataclass
class ChartStep:
    """One state of the sequence."""

    id: str
    name: str = ""
    description: str = ""
    initial: bool = False
    actions: list[ChartAction] = field(default_factory=list)
    x: float = 0.0
    y: float = 0.0


@dataclass
class ChartTransition:
    """The condition that moves the sequence on.

    An empty ``target`` is a termination — the chart is done and holds.
    """

    id: str
    name: str = ""
    description: str = ""
    condition: str = ""
    source: str = ""
    target: str = ""


@dataclass
class Chart:
    """A whole sequence."""

    steps: list[ChartStep] = field(default_factory=list)
    transitions: list[ChartTransition] = field(default_factory=list)

    def step(self, step_id: str) -> ChartStep | None:
        return next((s for s in self.steps if s.id == step_id), None)

    def initial_step(self) -> ChartStep | None:
        marked = next((s for s in self.steps if s.initial), None)
        return marked or (self.steps[0] if self.steps else None)

    def leaving(self, step_id: str) -> list[ChartTransition]:
        return [t for t in self.transitions if t.source == step_id]

    def reachable(self) -> set[str]:
        """Every step the sequence can actually get to — an unreachable
        step is dead logic reading as live logic."""
        start = self.initial_step()
        if start is None:
            return set()
        seen, queue = {start.id}, [start.id]
        while queue:
            for transition in self.leaving(queue.pop()):
                if transition.target and transition.target not in seen:
                    seen.add(transition.target)
                    queue.append(transition.target)
        return seen

    def problems(self) -> list[str]:
        """Chart-legality findings, model-level (the validator's food)."""
        out = []
        if not self.steps:
            out.append("the chart has no steps")
            return out
        ids = {s.id for s in self.steps}
        for transition in self.transitions:
            if transition.source not in ids:
                out.append(f"transition {transition.id!r} leaves unknown "
                           f"step {transition.source!r}")
            if transition.target and transition.target not in ids:
                out.append(f"transition {transition.id!r} targets unknown "
                           f"step {transition.target!r}")
            if not transition.condition.strip():
                out.append(f"transition {transition.id!r} has no "
                           "condition — it would never fire")
        unreachable = ids - self.reachable()
        for step_id in sorted(unreachable):
            out.append(f"step {step_id!r} is unreachable from the "
                       "initial step")
        return out

    # ------------------------------------------------------- (de)serialise
    def to_dict(self) -> dict:
        return {
            "steps": [{
                "id": s.id, "name": s.name, "description": s.description,
                "initial": s.initial, "x": s.x, "y": s.y,
                "actions": [vars(a) for a in s.actions],
            } for s in self.steps],
            "transitions": [vars(t) for t in self.transitions],
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "Chart":
        data = data or {}
        return cls(
            steps=[ChartStep(
                **{**{k: v for k, v in s.items() if k != "actions"},
                   "actions": [ChartAction(**a)
                               for a in s.get("actions", [])]})
                for s in data.get("steps", [])],
            transitions=[ChartTransition(**t)
                         for t in data.get("transitions", [])])


@register_block
class SfcChartBlock(FunctionBlock):
    """A chart, executing — one block, mixing with FBD on the sheet."""

    block_type = "SFC_CHART"
    category = BlockCategory.SFC
    display_name = "SFC Chart (SFC_CHART)"
    description = "Steps, actions and transitions as one chart-in-a-block"

    def __init__(self, instance_name: str = ""):
        self._chart: Chart | None = None
        self._chart_raw = ""
        self._active = ""
        self._elapsed = 0.0
        self._first = True
        self._done = False
        self._completed: list[str] = []
        self._visits: dict[str, int] = {}
        # Online-debug state is runtime-only. Disabling a step while
        # diagnosing a live chart must not rewrite the engineering definition.
        self._debug_stopped = False
        self._disabled_steps: set[str] = set()
        self._disabled_actions: set[str] = set()
        self._disabled_transitions: set[str] = set()
        self._forced_transition: str = ""
        self._last_transition_evaluations: list[dict[str, Any]] = []
        self._stored_actions: dict[str, ChartAction] = {}
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("EN_D", DataType.BOOL, True,
                       "False = hold the sequence where it is")
        self.add_input("RESET_D", DataType.BOOL, False,
                       "True = return to the initial step")
        self.add_output("ACTIVE", DataType.STRING, "",
                        "Name of the active step")
        self.add_output("STEP_TIME", DataType.FLOAT, 0.0,
                        "Seconds the active step has been active")
        self.add_output("STEP_NO", DataType.FLOAT, 0.0,
                        "1-based index of the active step (0 = none)")
        self.add_output("DONE", DataType.BOOL, False,
                        "True after a termination transition fired")
        self.add_output("HELD", DataType.BOOL, False,
                        "True while EN_D holds the sequence")
        self.add_output("FAULT", DataType.BOOL, False,
                        "True when the chart or an expression is broken")

    def get_config_schema(self):
        return {
            "CHART": (str, "",
                      "The chart as JSON — edited in the SFC chart "
                      "editor, never by hand"),
        }

    # -------------------------------------------------------------- chart
    def chart(self) -> Chart:
        raw = str(self.config.params.get("CHART", "") or "")
        if raw != self._chart_raw or self._chart is None:
            self._chart_raw = raw
            try:
                self._chart = Chart.from_dict(json.loads(raw) if raw
                                              else None)
            except (ValueError, TypeError) as error:
                log.warning("%s: chart JSON is broken: %s",
                            self.instance_name, error)
                self._chart = Chart()
        return self._chart

    def set_chart(self, chart: Chart) -> None:
        self.config.params["CHART"] = json.dumps(chart.to_dict())
        self._chart = chart
        self._chart_raw = self.config.params["CHART"]
        # Stored actions point at action objects from the previous definition.
        # A live edit/download starts with the new definition, never ghost
        # actions retained from an object no longer present in the chart.
        self._stored_actions.clear()

    def reset(self):
        super().reset()
        self._reset_chart_state()
        self._debug_stopped = False
        self._disabled_steps.clear()
        self._disabled_actions.clear()
        self._disabled_transitions.clear()
        self._forced_transition = ""
        self._last_transition_evaluations = []

    def _reset_chart_state(self) -> None:
        """Reset sequence execution without changing terminal/debug setup."""
        self._active = ""
        self._elapsed = 0.0
        self._first = True
        self._done = False
        self._completed = []
        self._visits = {}
        self._stored_actions = {}

    # ------------------------------------------------------- online debug
    @property
    def debug_stopped(self) -> bool:
        return self._debug_stopped

    def debug_stop(self) -> None:
        """Stop this chart level; elapsed time pauses until ``debug_start``."""
        self._debug_stopped = True

    def debug_start(self) -> None:
        self._debug_stopped = False

    def debug_reset(self) -> None:
        """Reset the level to its initial state without clearing disables."""
        self._reset_chart_state()
        self._last_transition_evaluations = []

    def debug_disable_step(self, step_id: str, disabled: bool = True) -> bool:
        if self.chart().step(step_id) is None:
            return False
        self._set_debug_membership(self._disabled_steps, step_id, disabled)
        return True

    def debug_disable_action(self, action_id: str,
                             disabled: bool = True) -> bool:
        exists = any(action.id == action_id for step in self.chart().steps
                     for action in step.actions)
        if not exists:
            return False
        self._set_debug_membership(self._disabled_actions, action_id, disabled)
        return True

    def debug_disable_transition(self, transition_id: str,
                                 disabled: bool = True) -> bool:
        exists = any(t.id == transition_id for t in self.chart().transitions)
        if not exists:
            return False
        self._set_debug_membership(
            self._disabled_transitions, transition_id, disabled)
        if disabled and self._forced_transition == transition_id:
            self._forced_transition = ""
        return True

    @staticmethod
    def _set_debug_membership(values: set[str], key: str,
                              enabled: bool) -> None:
        if enabled:
            values.add(key)
        else:
            values.discard(key)

    def debug_force_transition(self, transition_id: str) -> bool:
        """Request one active transition for the next scan.

        The force is momentary and applies only to a transition leaving the
        currently active step. Refusing an inactive or disabled transition
        prevents a latent request firing much later in an unrelated state.
        """
        transition = next((t for t in self.chart().transitions
                           if t.id == transition_id), None)
        if transition is None or transition.source != self._active \
                or transition_id in self._disabled_transitions:
            return False
        self._forced_transition = transition_id
        return True

    def transition_evaluation_snapshot(self) -> list[dict[str, Any]]:
        """Return last-scan transition results for an evaluation-tree UI."""
        return [dict(row) for row in self._last_transition_evaluations]

    # ------------------------------------------------------ the namespace
    def _namespace(self) -> dict:
        from .action_block import SAFE_MATH_FUNCTIONS
        from .script_functions import get_script_functions

        ns: dict[str, Any] = dict(SAFE_MATH_FUNCTIONS)
        ns.update(get_script_functions())
        graph = getattr(self, "_module_graph", None)
        context = getattr(self, "runtime_context", None)
        ns["dt"] = self._last_dt
        ns["step_time"] = self._elapsed

        def param(path):
            if graph is None:
                raise ValueError("param(): module not on scan yet")
            name, _, item = str(path).strip("/").partition("/")
            other = next((b for b in graph.blocks.values()
                          if b.instance_name == name), None)
            if other is None:
                raise ValueError(f"param(): no block {name!r} in module")
            terminal = other.outputs.get(item) or other.inputs.get(item)
            if terminal is not None:
                return terminal.value
            if item in other.config.params:
                return other.config.params[item]
            raise ValueError(
                f"param(): {name} has no terminal or parameter {item!r}")

        def set_param(path, value):
            """Write another block's *unwired input* terminal or config.

            A wired input is refused with the wire named: the wire wins
            next scan (item 27), so accepting the write would be a
            sequence action that silently does nothing.
            """
            if graph is None:
                raise ValueError("set_param(): module not on scan yet")
            name, _, item = str(path).strip("/").partition("/")
            other = next((b for b in graph.blocks.values()
                          if b.instance_name == name), None)
            if other is None:
                raise ValueError(f"set_param(): no block {name!r}")
            terminal = other.inputs.get(item)
            if terminal is not None:
                for wire in graph.wires.values():
                    if wire.dst_block_id == other.id \
                            and wire.dst_terminal == item:
                        raise ValueError(
                            f"set_param(): {name}/{item} is wired from "
                            f"{wire.src_block_id}.{wire.src_terminal} — "
                            "the wire would re-assert it next scan")
                terminal.value = value
                return value
            if item in other.config.params \
                    or item in (other.get_config_schema() or {}):
                other.config.params[item] = value
                other._apply_config()
                return value
            raise ValueError(
                f"set_param(): {name} has no input or parameter {item!r}")

        def tag(key, default=None):
            if context is None:
                raise ValueError("tag(): no runtime context")
            value = context.read(str(key))
            if value is None:
                if default is not None:
                    # A command tag exists only after its first write;
                    # the default is what "nobody has pressed it" reads.
                    return default
                raise ValueError(f"tag(): {key!r} not in the store")
            return value

        def set_tag(key, value):
            """Write a store tag down the controller's own output path.

            Deliberately NOT `context.write`: that gate is the ACT-script
            allow-list, built for arbitrary user Python. A chart action is
            controller-resident module logic in the restricted expression
            language — its writes are the module's outputs, exactly what
            the DO/AO blocks it replaces put on `queue_write` (and an
            engineer can already write any tag by typing it into a DO).
            The live wire proved the difference: behind the gate, every
            sequence action was silently refused.
            """
            store = getattr(context, "store", None)
            if store is None:
                raise ValueError("set_tag(): no store attached")
            if hasattr(store, "queue_write"):
                store.queue_write(str(key), value)
            else:
                store.set(str(key), value)
            return value

        def write_param(name, value):
            if graph is None:
                raise ValueError("write_param(): module not on scan yet")
            spec = graph.module_parameters().get(str(name))
            if spec is None:
                raise ValueError(
                    f"write_param(): no module parameter {name!r}")
            if not graph.module_parameter_is_writeable(spec):
                raise ValueError(
                    f"write_param(): {name!r} is not writeable")
            spec["value"] = value
            # Later actions in the same SFC scan see the value just written.
            # Without updating the evaluation namespace, two actions both
            # computed from the scan-start snapshot and the latter silently
            # overwrote the former.
            ns[str(name)] = value
            return value

        ns.update(param=param, set_param=set_param, tag=tag,
                  set_tag=set_tag, write_param=write_param)
        if graph is not None:
            for name, spec in graph.module_parameters().items():
                ns.setdefault(name, spec.get("value"))
        return ns

    # ------------------------------------------------------------ execute
    def _record_completed(self):
        if self._active and (not self._completed or self._completed[-1] != self._active):
            self._completed.append(self._active)
            # Looping charts run indefinitely; keep recent route history,
            # while per-step visit counters retain completion information.
            del self._completed[:-1024]

    def _enter(self, step_id: str) -> None:
        self._record_completed()
        self._active = step_id
        self._elapsed = 0.0
        self._first = True
        if step_id:
            self._visits[step_id] = self._visits.get(step_id, 0) + 1

    def _run_action(self, action: ChartAction, namespace: dict,
                    evaluator, step_id: str) -> bool:
        """Execute one action and return False on an expression error."""
        if action.id in self._disabled_actions or not action.expression.strip():
            return True
        try:
            evaluator(action.expression, namespace)
        except Exception as error:                       # noqa: BLE001
            log.warning("%s step %s action %s: %s",
                        self.instance_name, step_id, action.id, error)
            return False
        return True

    @staticmethod
    def _stored_key(action: ChartAction) -> str:
        """Stable association key used by the S/R qualifier pair."""
        return (action.name or action.id).strip()

    def execute(self, dt: float):
        from .action_block import _safe_eval

        self._last_dt = dt
        chart = self.chart()
        problems = chart.problems()
        if problems:
            self.set_output("FAULT", True)
            self.set_output("ACTIVE", "")
            self.set_output_status("ACTIVE", Quality.BAD,
                                   LimitStatus.NOT_LIMITED)
            self.status = BlockStatus.BAD
            return
        self.set_output("FAULT", False)

        if bool(self.get_input("RESET_D")):
            self.debug_reset()
        if not self._active and not self._done:
            start = chart.initial_step()
            self._enter(start.id if start else "")

        held = not bool(self.get_input("EN_D")) or self._debug_stopped
        self.set_output("HELD", held)
        step = chart.step(self._active)
        if step is None or self._done or held:
            self._publish(chart, step)
            return

        self._elapsed += dt
        ns = self._namespace()
        fault = False

        # Stored actions continue independently of the step that set them.
        # Take a snapshot because an R action below may remove one while this
        # scan is executing.
        ran_stored: set[str] = set()
        for key, action in list(self._stored_actions.items()):
            ran_stored.add(key)
            if not self._run_action(action, ns, _safe_eval, step.id):
                fault = True

        step_disabled = step.id in self._disabled_steps
        if not step_disabled:
            for action in step.actions:
                qualifier = (action.qualifier or "P").strip().upper()
                if qualifier == "R":
                    if action.id not in self._disabled_actions:
                        target = (action.target or action.name).strip()
                        if target:
                            self._stored_actions.pop(target, None)
                    continue
                if qualifier == "S":
                    key = self._stored_key(action)
                    self._stored_actions[key] = action
                    # An action already present at scan start has run above.
                    if key in ran_stored:
                        continue
                elif qualifier == "P" and not self._first:
                    continue
                elif qualifier == "L" and action.time_s > 0 \
                        and self._elapsed > action.time_s:
                    continue
                elif qualifier == "D" and self._elapsed < max(
                        0.0, action.time_s):
                    continue
                if not self._run_action(action, ns, _safe_eval, step.id):
                    fault = True
        self._first = False

        # Evaluate every outgoing transition so the online evaluation tree can
        # explain a false/disabled path. Selection still uses the first true
        # transition in declaration order.
        evaluations: list[dict[str, Any]] = []
        selected: ChartTransition | None = None
        for transition in chart.leaving(step.id):
            disabled = transition.id in self._disabled_transitions
            forced = transition.id == self._forced_transition
            error_text = ""
            try:
                value = bool(_safe_eval(transition.condition, ns))
            except Exception as error:              # noqa: BLE001
                log.warning("%s transition %s: %s", self.instance_name,
                            transition.id, error)
                fault = True
                value = False
                error_text = str(error)
            can_fire = not step_disabled and not disabled
            fired = can_fire and (forced or value)
            evaluations.append({
                "id": transition.id,
                "name": transition.name or transition.id,
                "condition": transition.condition,
                "value": value,
                "fired": fired,
                "forced": forced,
                "disabled": disabled,
                "sourceStepDisabled": step_disabled,
                "error": error_text,
            })
            if fired and selected is None:
                selected = transition
        self._last_transition_evaluations = evaluations
        self._forced_transition = ""       # a force is always one scan only

        if selected is not None:
            if selected.target:
                self._enter(selected.target)
            else:
                self._record_completed()
                self._done = True
                self._active = ""
        self.set_output("FAULT", fault)
        self._publish(chart, chart.step(self._active))

    def _publish(self, chart: Chart, step: ChartStep | None) -> None:
        self.set_output("ACTIVE",
                        (step.name or step.id) if step else "")
        self.set_output("STEP_TIME", self._elapsed if step else 0.0)
        self.set_output("STEP_NO",
                        float(chart.steps.index(step) + 1) if step else 0.0)
        self.set_output("DONE", self._done)

    # ---------------------------------------------------------- the HMI
    def sequence_snapshot(self) -> dict:
        """One bounded, semantic contract for a sequence display —
        the display must never reverse-engineer private block state."""
        chart = self.chart()
        completed = set(self._completed) | set(self._visits)
        rows = []
        for step in chart.steps:
            rows.append({
                "id": step.id,
                "name": step.name or step.id,
                "status": ("ACTIVE" if step.id == self._active else
                           "COMPLETE" if step.id in completed else
                           "PENDING"),
                "visits": self._visits.get(step.id, 0),
                "actions": len(step.actions),
            })
        return {
            "mode": ("FAULTED" if bool(self.get_output("FAULT")) else
                     "STOPPED" if self._debug_stopped else
                     "HELD" if bool(self.get_output("HELD")) else
                     "COMPLETE" if self._done else "AUTO"),
            "activeStepId": self._active,
            "elapsed": self._elapsed,
            "completedPath": list(self._completed),
            "disabledSteps": sorted(self._disabled_steps),
            "disabledActions": sorted(self._disabled_actions),
            "disabledTransitions": sorted(self._disabled_transitions),
            "storedActions": sorted(self._stored_actions),
            "transitionEvaluations": self.transition_evaluation_snapshot(),
            "steps": rows,
        }
