from __future__ import annotations

import logging
import math
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .enums import ProcedureStatus, StepStatus
from .procedure_model import FlowEdge, FlowNode, Procedure, Step
from .expression_engine import SafeExpressionEvaluator
from .tag_references import normalize_tag_expression
from .validator import validate_procedure_contract
from .runtime_control import RuntimeAbortRequested, RuntimeControl
from ..connectors.aliased_tag_provider import AliasedTagProvider
from ..connectors.tag_provider import TagProvider
from ..storage.sqlite_store import SQLiteStore

log = logging.getLogger(__name__)

# Sleep requests are split into chunks this large so parallel-branch
# cancellation and abort are observed promptly inside long waits.
_SLEEP_CHUNK_SEC = 0.25
_MAX_PARALLEL_BRANCHES = 32

# Outcome fallback routing for action nodes with outcome-labeled edges.
# A skipped or alarm-and-continue step follows the normal path unless a
# dedicated edge exists; timeout falls back to the failure path.
_OUTCOME_FALLBACKS: dict[str, list[str]] = {
    "passed": ["passed", "always"],
    "skipped": ["skipped", "passed", "always"],
    "warning": ["warning", "passed", "always"],
    "alarm": ["alarm", "passed", "always"],
    "failed": ["failed", "always"],
    "timeout": ["timeout", "failed", "always"],
    "always": ["always"],
}


class HoldRequested(RuntimeError):
    pass


class AbortRequested(RuntimeError):
    pass


class _BranchCancelled(BaseException):
    """Internal: a losing parallel branch was cancelled by an OR join.

    Derives from BaseException so step-level `except Exception` failure
    handling never converts a cancellation into a step failure.
    """


@dataclass
class ExecutionResult:
    run_id: str
    status: ProcedureStatus
    message: str


class _FlowRunState:
    """Shared state for one flow run; visit counting is thread-safe."""

    def __init__(
        self,
        nodes: dict[str, FlowNode],
        steps: dict[str, Step],
        outgoing: dict[str, list[FlowEdge]],
        visit_limit: int,
    ):
        self.nodes = nodes
        self.steps = steps
        self.outgoing = outgoing
        self.visit_limit = visit_limit
        self.visits = 0
        self.lock = threading.Lock()
        self.executed_steps: set[str] = set()

    def count_visit(self) -> None:
        with self.lock:
            self.visits += 1
            if self.visits > self.visit_limit:
                raise RuntimeError(
                    "Procedure flow exceeded the maximum node visits; "
                    "check loop exit conditions or raise flow.visit_limit"
                )

    def mark_executed(self, step_id: str) -> None:
        with self.lock:
            self.executed_steps.add(step_id)


class ProcedureExecutionEngine:
    def __init__(
        self,
        tag_provider: TagProvider,
        store: SQLiteStore,
        sleep_fn: Callable[[float], None] = time.sleep,
        auto_confirm: bool = False,
        fail_on_validation_warning: bool = False,
        auto_comment: str = "Auto-generated training comment",
        confirm_fn: Callable[[Step, str], bool] | None = None,
        comment_fn: Callable[[Step, str], str] | None = None,
        input_fn: Callable[[Step, str], Any] | None = None,
        step_event_fn: Callable[[Step, str], None] | None = None,
        wait_progress_fn: Callable[[Step, dict[str, Any]], None] | None = None,
        message_event_fn: Callable[[dict[str, Any]], None] | None = None,
        runtime_control: RuntimeControl | None = None,
        step_id_prefix: str = "",
        active_subprocedures: set[str] | None = None,
        operator_identity: str = "Operator",
        prepared_metadata: dict[str, Any] | None = None,
        parallel_join_timeout_sec: float = 600.0,
        dependency_root: str | Path | None = None,
    ):
        self.tag_provider = tag_provider
        self.store = store
        self.sleep_fn = sleep_fn
        self.auto_confirm = auto_confirm
        self.fail_on_validation_warning = fail_on_validation_warning
        self.auto_comment = auto_comment
        self.confirm_fn = confirm_fn
        self.comment_fn = comment_fn
        self.input_fn = input_fn
        self.step_event_fn = step_event_fn
        self.wait_progress_fn = wait_progress_fn
        self.message_event_fn = message_event_fn
        self.runtime_control = runtime_control
        self.step_id_prefix = step_id_prefix
        self.operator_identity = operator_identity.strip() or "Operator"
        self.prepared_metadata = dict(prepared_metadata or {})
        if not math.isfinite(parallel_join_timeout_sec) or parallel_join_timeout_sec <= 0:
            raise ValueError("parallel_join_timeout_sec must be finite and positive")
        self.evaluator = SafeExpressionEvaluator()
        self._runtime_variables: dict[str, Any] = {}
        self._procedure: Procedure | None = None
        self._active_subprocedures: set[str] = set(active_subprocedures or set())
        self._local = threading.local()
        self.parallel_join_timeout_sec = parallel_join_timeout_sec
        self._dependency_root = Path(dependency_root).resolve() if dependency_root else None

    @property
    def runtime_variables(self) -> dict[str, Any]:
        return getattr(self._local, "runtime_variables", self._runtime_variables)

    @runtime_variables.setter
    def runtime_variables(self, values: dict[str, Any]) -> None:
        self._runtime_variables = values

    # ------------------------------------------------------------------
    # Namespacing, clock, and control helpers

    def _prefixed(self, step_id: str) -> str:
        return f"{self.step_id_prefix}{step_id}"

    def _log_step(self, run_id: str, step: Step, status: str, message: str | None) -> None:
        self.store.log_step(run_id, self._prefixed(step.id), step.type, status, message)
        if status in {"PASSED", "FAILED", "SKIPPED", "WARNING", "ALARM", "HELD", "ABORTED"}:
            self.store.save_checkpoint(
                run_id,
                self._prefixed(step.id),
                status,
                self.runtime_variables,
            )

    def _notify_step(self, step: Step, status: str) -> None:
        if not self.step_event_fn:
            return
        if self.step_id_prefix:
            step = step.model_copy(update={"id": self._prefixed(step.id)})
        self.step_event_fn(step, status)

    def _create_operator_message(
        self,
        run_id: str,
        step: Step,
        event_type: str,
        message: str,
        *,
        severity: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> int:
        message_id = self.store.create_operator_message(
            run_id,
            event_type,
            message,
            step_id=self._prefixed(step.id),
            severity=severity or step.severity,
            data={
                "equipment": step.equipment,
                "section": step.section,
                "document_link": step.document_link,
                "video_link": step.video_link,
            }
            | (data or {}),
        )
        if self.message_event_fn:
            self.message_event_fn({
                "message_id": message_id,
                "run_id": run_id,
                "step_id": self._prefixed(step.id),
                "event_type": event_type,
                "severity": severity or step.severity,
                "message": message,
                "operator": "",
                "acknowledged": False,
            })
        return message_id

    def _acknowledge_operator_message(self, message_id: int, *, comment: str = "") -> None:
        changed = self.store.acknowledge_operator_message(message_id, self.operator_identity, comment)
        if changed and self.message_event_fn:
            self.message_event_fn({
                "message_id": message_id,
                "operator": self.operator_identity,
                "comment": comment,
                "acknowledged": True,
            })

    def _now(self) -> float:
        if self.runtime_control:
            return self.runtime_control.adjusted_monotonic()
        return time.monotonic()

    def _checkpoint(self) -> None:
        cancels = getattr(self._local, "cancel_stack", None)
        if cancels and any(event.is_set() for event in cancels):
            raise _BranchCancelled()
        if self.runtime_control:
            self.runtime_control.checkpoint()

    def checkpoint(self) -> None:
        """Public cancellation checkpoint for blocking operator UI callbacks."""
        self._checkpoint()

    def _sleep(self, seconds: float) -> None:
        remaining = max(seconds, 0.0)
        while True:
            cancels = getattr(self._local, "cancel_stack", None)
            if cancels and any(event.is_set() for event in cancels):
                raise _BranchCancelled()
            chunk = min(remaining, _SLEEP_CHUNK_SEC)
            self.sleep_fn(chunk)
            remaining -= chunk
            if remaining <= 0:
                return

    # ------------------------------------------------------------------
    # Run entry points

    def run(self, procedure: Procedure, run_id: str | None = None) -> ExecutionResult:
        run_id = run_id or str(uuid.uuid4())
        self.store.start_run(
            run_id,
            procedure.procedure_id,
            procedure.name,
            ProcedureStatus.RUNNING.value,
            metadata={
                "unit": procedure.unit,
                "mode": procedure.mode.value,
                "version": procedure.metadata.version,
                "safety_class": procedure.metadata.safety_class,
                "operator_identity": self.operator_identity,
            } | self.prepared_metadata,
        )
        log.info("Starting procedure %s (%s), run_id=%s", procedure.name, procedure.procedure_id, run_id)
        try:
            self._execute_body(run_id, procedure)
            self.store.finish_run(run_id, ProcedureStatus.COMPLETE.value)
            log.info("Procedure complete: %s", run_id)
            return ExecutionResult(run_id, ProcedureStatus.COMPLETE, "Procedure completed")
        except HoldRequested as exc:
            self.store.finish_run(run_id, ProcedureStatus.HELD.value)
            log.warning("Procedure held: %s", run_id)
            return ExecutionResult(run_id, ProcedureStatus.HELD, str(exc))
        except AbortRequested as exc:
            self.store.finish_run(run_id, ProcedureStatus.ABORTED.value)
            log.warning("Procedure aborted: %s", run_id)
            return ExecutionResult(run_id, ProcedureStatus.ABORTED, str(exc))
        except RuntimeAbortRequested as exc:
            self.store.finish_run(run_id, ProcedureStatus.ABORTED.value)
            log.warning("Procedure aborted by runtime control: %s", run_id)
            return ExecutionResult(run_id, ProcedureStatus.ABORTED, str(exc))
        except Exception as exc:
            self.store.log_event(
                run_id,
                "RECOVERY_REQUIRED",
                None,
                procedure.recovery.instructions,
                {
                    "mode": procedure.recovery.mode,
                    "recovery_procedure_path": procedure.recovery.recovery_procedure_path,
                    "require_readiness_recheck": procedure.recovery.require_readiness_recheck,
                },
            )
            self.store.finish_run(run_id, ProcedureStatus.FAILED.value)
            log.exception("Procedure failed: %s", run_id)
            return ExecutionResult(run_id, ProcedureStatus.FAILED, str(exc))

    def _execute_body(
        self,
        run_id: str,
        procedure: Procedure,
        parameter_overrides: dict[str, Any] | None = None,
    ) -> None:
        report = validate_procedure_contract(procedure)
        report.raise_for_errors()
        if self.fail_on_validation_warning and report.warnings:
            raise ValueError("Validation warnings were configured as fatal: " + "; ".join(report.warnings))
        self._procedure = procedure
        if self._dependency_root is None and procedure.source_path:
            self._dependency_root = Path(procedure.source_path).resolve().parent
        self.runtime_variables = procedure.variable_map()
        if parameter_overrides:
            self.runtime_variables.update(parameter_overrides)
        if procedure.flow:
            self._run_flow(run_id, procedure)
        else:
            self._run_linear(run_id, procedure)

    def _run_linear(self, run_id: str, procedure: Procedure) -> None:
        idx_by_id = {step.id: i for i, step in enumerate(procedure.steps)}
        idx = 0
        visited_count = 0
        max_visits = max(1000, len(procedure.steps) * 50)
        while idx < len(procedure.steps):
            if visited_count > max_visits:
                raise RuntimeError("Procedure appears to be looping; maximum step visits exceeded")
            visited_count += 1
            step = procedure.steps[idx]
            _, target = self._run_step(run_id, step)
            if target == "__END__":
                break
            if target:
                idx = idx_by_id[target]
            else:
                idx += 1

    # ------------------------------------------------------------------
    # Flow execution

    def _run_flow(self, run_id: str, procedure: Procedure) -> None:
        assert procedure.flow is not None
        nodes = {node.id: node for node in procedure.flow.nodes}
        steps = {step.id: step for step in procedure.steps}
        outgoing: dict[str, list[FlowEdge]] = {node_id: [] for node_id in nodes}
        for edge in procedure.flow.edges:
            outgoing[edge.source].append(edge)
        for edges in outgoing.values():
            edges.sort(key=lambda edge: edge.priority)

        visit_limit = procedure.flow.visit_limit or max(1000, len(nodes) * 100)
        state = _FlowRunState(nodes, steps, outgoing, visit_limit)
        start_id = next(node.id for node in procedure.flow.nodes if node.kind == "start")
        self._traverse_flow(run_id, state, start_id)
        for step in state.steps.values():
            if step.id in state.executed_steps:
                continue
            self._log_step(run_id, step, StepStatus.SKIPPED.value, "Not selected by flow topology")
            self._notify_step(step, StepStatus.SKIPPED.value)

    def _traverse_flow(self, run_id: str, state: _FlowRunState, node_id: str) -> str | None:
        """Advance one token until an end node (returns None) or a
        parallel_join node (returns its id, consumed by the owning fork)."""
        current = node_id
        while True:
            self._checkpoint()
            state.count_visit()
            node = state.nodes[current]
            if node.kind == "parallel_join":
                return current
            self.store.log_event(
                run_id,
                "FLOW_NODE_ACTIVE",
                self._prefixed(node.step_id) if node.step_id else None,
                node.label or node.id,
                {"node_id": node.id, "kind": node.kind},
            )
            if node.kind == "end":
                self.store.log_event(run_id, "FLOW_COMPLETE", None, node.label or node.id, {"node_id": node.id})
                return None
            if node.kind == "parallel_fork":
                next_id = self._run_parallel_fork(run_id, state, node)
                if next_id is None:
                    return None
                current = next_id
                continue

            outcome = "always"
            if node.kind == "action":
                step = state.steps[node.step_id or ""]
                state.mark_executed(step.id)
                outcome = self._run_flow_action(run_id, step, state.outgoing[node.id])
            elif node.kind == "transition":
                self._run_flow_transition(run_id, node)
                outcome = "passed"

            if node.kind in {"choice", "loop"}:
                edge = self._select_guarded_flow_edge(run_id, node, state.outgoing[node.id])
            else:
                edge = self._select_outcome_flow_edge(node, state.outgoing[node.id], outcome)
            self.store.log_event(
                run_id,
                "FLOW_EDGE",
                self._prefixed(node.step_id) if node.step_id else None,
                edge.label or f"{edge.source} -> {edge.target}",
                {
                    "source": edge.source,
                    "target": edge.target,
                    "outcome": outcome,
                    "condition": edge.condition or "",
                    "priority": edge.priority,
                },
            )
            current = edge.target

    def _run_flow_action(self, run_id: str, step: Step, edges: list[FlowEdge]) -> str:
        """Run a flow-controlled step, mapping failures/timeouts to outcome
        edges when the node declares them, so judgment-style branches work."""
        declared_outcomes = {edge.outcome for edge in edges}
        try:
            outcome, _ = self._run_step(run_id, step)
            return outcome
        except (HoldRequested, AbortRequested, RuntimeAbortRequested):
            raise
        except TimeoutError as exc:
            if declared_outcomes & {"timeout", "failed"}:
                self.store.log_event(
                    run_id, "BRANCH", self._prefixed(step.id),
                    "Timeout routed to flow outcome edge", {"reason": str(exc)},
                )
                return "timeout"
            raise
        except Exception as exc:
            if "failed" in declared_outcomes:
                self.store.log_event(
                    run_id, "BRANCH", self._prefixed(step.id),
                    "Failure routed to flow outcome edge", {"reason": str(exc)},
                )
                return "failed"
            raise

    def _run_parallel_fork(self, run_id: str, state: _FlowRunState, node: FlowNode) -> str | None:
        """Run each outgoing branch in its own thread; converge at the join.

        AND joins wait for every branch. OR joins continue on the first branch
        to arrive and cancel the rest at their next step/sleep boundary.
        Returns the node id after the join, or None if a branch reached end.
        """
        edges = state.outgoing[node.id]
        if len(edges) > _MAX_PARALLEL_BRANCHES:
            raise RuntimeError(
                f"Parallel fork {node.id} has {len(edges)} branches; maximum is {_MAX_PARALLEL_BRANCHES}"
            )
        parent_stack = list(getattr(self._local, "cancel_stack", None) or [])
        branch_base = dict(self.runtime_variables)
        cancel = threading.Event()
        results: queue.Queue[tuple[str, str, Any]] = queue.Queue()
        self.store.log_event(
            run_id, "PARALLEL_FORK", None, node.label or node.id,
            {"node_id": node.id, "branches": [edge.target for edge in edges]},
        )

        def run_branch(edge: FlowEdge) -> None:
            self._local.cancel_stack = parent_stack + [cancel]
            self._local.runtime_variables = dict(branch_base)
            try:
                arrival = self._traverse_flow(run_id, state, edge.target)
                results.put((
                    "end" if arrival is None else "ok",
                    edge.target,
                    (arrival, dict(self.runtime_variables)),
                ))
            except _BranchCancelled:
                results.put(("cancelled", edge.target, None))
            except BaseException as exc:  # noqa: BLE001 - marshalled to the fork thread
                results.put(("err", edge.target, exc))

        threads = [
            threading.Thread(target=run_branch, args=(edge,), name=f"pp-branch-{edge.target}", daemon=True)
            for edge in edges
        ]
        for thread in threads:
            thread.start()

        arrivals: list[str] = []
        branch_variables: dict[str, dict[str, Any]] = {}
        errors: list[BaseException] = []
        structural_errors: list[RuntimeError] = []
        cancelled: list[str] = []
        cancel_reason = "parallel cancellation"
        join_id: str | None = None
        pending = len(threads)
        convergence_deadline = time.monotonic() + self.parallel_join_timeout_sec
        convergence_timeout: TimeoutError | None = None
        while pending:
            try:
                remaining = convergence_deadline - time.monotonic()
                if remaining <= 0:
                    raise queue.Empty
                kind, branch_start, payload = results.get(timeout=remaining)
            except queue.Empty as exc:
                cancel.set()
                convergence_timeout = TimeoutError(
                    f"Parallel fork {node.id} did not converge within "
                    f"{self.parallel_join_timeout_sec:g} seconds"
                )
                convergence_timeout.__cause__ = exc
                break
            pending -= 1
            if kind == "cancelled":
                cancelled.append(branch_start)
                continue
            if kind == "err":
                errors.append(payload)
                continue
            if kind == "end":
                structural_errors.append(
                    RuntimeError(
                        f"Parallel branch {node.id}->{branch_start} reached an end node before converging at a parallel_join"
                    )
                )
                cancel_reason = "branch ended before join"
                cancel.set()
                continue
            arrival, variables = payload
            arrivals.append(arrival)
            branch_variables[branch_start] = variables
            if join_id is None:
                join_id = arrival
                if state.nodes[join_id].join_mode == "or":
                    cancel_reason = f"or-join {join_id} completed"
                    cancel.set()
        for thread in threads:
            thread.join(timeout=1.0)
        stranded = [thread.name for thread in threads if thread.is_alive()]
        if stranded:
            raise RuntimeError(f"Parallel branches did not stop after cancellation: {stranded}")
        if convergence_timeout is not None:
            raise convergence_timeout

        for branch in cancelled:
            self.store.log_event(
                run_id, "PARALLEL_BRANCH_CANCELLED", None,
                f"Branch at {branch} cancelled: {cancel_reason}",
                {"fork_id": node.id, "reason": cancel_reason},
            )
        control_errors = [e for e in errors if isinstance(e, (AbortRequested, RuntimeAbortRequested, HoldRequested))]
        if control_errors:
            raise control_errors[0]
        if structural_errors:
            raise structural_errors[0]
        join_node = state.nodes[join_id] if join_id else None
        if join_node is None:
            if errors:
                raise errors[0]
            raise RuntimeError(f"Parallel fork {node.id} produced no join arrivals")
        selected = branch_variables
        if join_node.join_mode == "or" and branch_variables:
            first = next(branch for branch in branch_variables if branch_variables[branch] is not None)
            selected = {first: branch_variables[first]}
        changes: dict[str, tuple[str, Any]] = {}
        for branch_start in sorted(selected):
            for name, value in selected[branch_start].items():
                if branch_base.get(name) == value:
                    continue
                if name in changes:
                    owner, previous = changes[name]
                    raise RuntimeError(
                        f"Parallel branches {owner} and {branch_start} both changed runtime variable {name!r}"
                    )
                changes[name] = (branch_start, value)
        for name, (_owner, value) in sorted(changes.items()):
            self.runtime_variables[name] = value
        if join_node.join_mode == "and" and errors:
            raise errors[0]
        if errors:
            for exc in errors:
                self.store.log_event(
                    run_id, "PARALLEL_BRANCH_FAILED", None, str(exc),
                    {"fork_id": node.id, "join_mode": join_node.join_mode},
                )
        mismatched = {arrival for arrival in arrivals if arrival != join_id}
        if mismatched:
            raise RuntimeError(
                f"Parallel fork {node.id} branches converged at different joins: {sorted(mismatched | {join_id})}"
            )

        state.count_visit()
        self.store.log_event(
            run_id, "FLOW_NODE_ACTIVE", None, join_node.label or join_node.id,
            {"node_id": join_node.id, "kind": join_node.kind},
        )
        self.store.log_event(
            run_id, "PARALLEL_JOIN", None, join_node.label or join_node.id,
            {"node_id": join_node.id, "join_mode": join_node.join_mode, "arrivals": len(arrivals)},
        )
        out_edge = state.outgoing[join_node.id][0]
        self.store.log_event(
            run_id, "FLOW_EDGE", None, out_edge.label or f"{out_edge.source} -> {out_edge.target}",
            {
                "source": out_edge.source,
                "target": out_edge.target,
                "outcome": "always",
                "condition": out_edge.condition or "",
                "priority": out_edge.priority,
            },
        )
        return out_edge.target

    def _select_outcome_flow_edge(self, node: FlowNode, edges: list[FlowEdge], outcome: str) -> FlowEdge:
        for candidate in _OUTCOME_FALLBACKS.get(outcome, [outcome, "always"]):
            matching = [edge for edge in edges if edge.outcome == candidate]
            if matching:
                return matching[0]
        raise RuntimeError(f"Flow node {node.id} has no edge for outcome {outcome!r}")

    def _select_guarded_flow_edge(self, run_id: str, node: FlowNode, edges: list[FlowEdge]) -> FlowEdge:
        default_edge = next((edge for edge in edges if edge.is_default), None)
        for edge in edges:
            if edge.is_default:
                continue
            assert edge.condition is not None
            if self._eval_condition(edge.condition, run_id=run_id):
                return edge
        if default_edge:
            return default_edge
        raise RuntimeError(f"No condition matched at flow {node.kind} node {node.id}")

    def _run_flow_transition(self, run_id: str, node: FlowNode) -> None:
        mode = node.completion_mode
        process_ready = mode == "operator"
        if mode in {"process", "process_and_operator"}:
            process_ready = self._wait_for_flow_condition(run_id, node)
        elif mode == "process_or_operator":
            assert node.condition is not None
            process_ready = self._eval_condition(node.condition, run_id=run_id)

        operator_required = mode == "operator" or mode == "process_and_operator" or (mode == "process_or_operator" and not process_ready)
        if operator_required and not self._confirm_flow_transition(run_id, node):
            if self.runtime_control and self.runtime_control.is_abort_requested:
                raise RuntimeAbortRequested(f"Operator aborted at flow transition {node.id}")
            raise RuntimeError(f"Operator did not confirm flow transition {node.id}")
        self.store.log_event(
            run_id,
            "FLOW_TRANSITION_COMPLETE",
            None,
            node.label or node.id,
            {"node_id": node.id, "completion_mode": mode, "condition": node.condition or ""},
        )

    def _wait_for_flow_condition(self, run_id: str, node: FlowNode) -> bool:
        assert node.condition is not None
        deadline = self._now() + node.timeout_sec
        while True:
            if self._eval_condition(node.condition, run_id=run_id):
                return True
            if self._now() > deadline:
                raise TimeoutError(f"Timeout waiting for flow transition {node.id}: {node.condition}")
            self._sleep(node.poll_sec)

    def _confirm_flow_transition(self, run_id: str, node: FlowNode) -> bool:
        prompt = node.operator_prompt or node.label or f"Confirm transition {node.id}"
        if self.auto_confirm:
            confirmed = True
        elif self.confirm_fn:
            synthetic_step = Step(id=node.id, type="operator_confirm", description=prompt)
            confirmed = self.confirm_fn(synthetic_step, prompt)
        else:
            response = input(f"CONFIRM TRANSITION {node.id}: {prompt} [y/N]: ").strip().lower()
            confirmed = response in {"y", "yes"}
        self.store.log_event(
            run_id,
            "FLOW_OPERATOR_CONFIRM",
            None,
            prompt,
            {"node_id": node.id, "confirmed": confirmed, "auto_confirm": self.auto_confirm},
        )
        return confirmed

    # ------------------------------------------------------------------
    # Step execution

    def _run_step(self, run_id: str, step: Step) -> tuple[str, str | None]:
        if self.runtime_control:
            cancels = getattr(self._local, "cancel_stack", None)
            if cancels and any(event.is_set() for event in cancels):
                raise _BranchCancelled()
            self.runtime_control.before_step(self._prefixed(step.id))
        else:
            self._checkpoint()
        if step.skip_if and self._eval_condition(step.skip_if, run_id=run_id):
            self._log_step(run_id, step, StepStatus.SKIPPED.value, f"Skipped because: {step.skip_if}")
            self._notify_step(step, StepStatus.SKIPPED.value)
            return "skipped", step.goto_on_pass

        self._log_step(run_id, step, StepStatus.ACTIVE.value, step.description)
        self._notify_step(step, StepStatus.ACTIVE.value)
        log.info("Step active: %s [%s] %s", self._prefixed(step.id), step.type, step.description)
        handler = getattr(self, f"_step_{step.type}")
        try:
            skip_gate = getattr(self, "operator_skip_fn", None)
            if skip_gate and getattr(step, "operator_skip_policy", "never") == "reason":
                decision = skip_gate(step)
                if decision["decision"] == "skip":
                    reason = decision["reason"]
                    self.store.log_event(run_id, "OPERATOR_SKIP", self._prefixed(step.id), reason,
                                         {"actor": self.operator_identity, "reason": reason})
                    self._log_step(run_id, step, StepStatus.SKIPPED.value, reason)
                    self._notify_step(step, StepStatus.SKIPPED.value)
                    return "skipped", step.goto_on_pass
            outcome = handler(run_id, step) or "passed"
        except TimeoutError as exc:
            self._log_step(run_id, step, StepStatus.FAILED.value, str(exc))
            self._notify_step(step, StepStatus.FAILED.value)
            if step.goto_on_timeout:
                self.store.log_event(run_id, "BRANCH", self._prefixed(step.id), f"Timeout branch to {step.goto_on_timeout}", {"reason": str(exc)})
                return "timeout", step.goto_on_timeout
            if step.on_timeout == "hold":
                raise HoldRequested(str(exc)) from exc
            if step.on_timeout == "abort":
                raise AbortRequested(str(exc)) from exc
            if step.on_timeout == "alarm":
                self.store.log_event(run_id, "ALARM", self._prefixed(step.id), str(exc), {"severity": "alarm"})
                return "alarm", step.goto_on_pass
            raise
        except HoldRequested:
            self._log_step(run_id, step, StepStatus.HELD.value, "Procedure held at this step")
            self._notify_step(step, StepStatus.HELD.value)
            raise
        except (AbortRequested, RuntimeAbortRequested):
            self._log_step(run_id, step, StepStatus.ABORTED.value, "Procedure aborted at this step")
            self._notify_step(step, StepStatus.ABORTED.value)
            raise
        except Exception as exc:
            self._log_step(run_id, step, StepStatus.FAILED.value, str(exc))
            self._notify_step(step, StepStatus.FAILED.value)
            if step.goto_on_fail:
                self.store.log_event(run_id, "BRANCH", self._prefixed(step.id), f"Failure branch to {step.goto_on_fail}", {"reason": str(exc)})
                return "failed", step.goto_on_fail
            if step.on_failure == "skip":
                return "skipped", step.goto_on_pass
            if step.on_failure == "hold":
                raise HoldRequested(str(exc)) from exc
            if step.on_failure == "abort":
                raise AbortRequested(str(exc)) from exc
            if step.on_failure == "alarm":
                self.store.log_event(run_id, "ALARM", self._prefixed(step.id), str(exc), {"severity": "alarm"})
                return "alarm", step.goto_on_pass
            raise

        status = StepStatus.PASSED.value
        if outcome == "warning":
            status = StepStatus.WARNING.value
        elif outcome == "alarm":
            status = StepStatus.ALARM.value
        self._log_step(run_id, step, status, f"Step {outcome}")
        self._notify_step(step, status)
        log.info("Step %s: %s", outcome, self._prefixed(step.id))
        if step.type == "complete" and not step.subprocedure_return:
            return outcome, "__END__"
        return outcome, step.goto_on_pass

    def _audit_tag(self, tag: str) -> str:
        """Resolve generic subprocedure tag names to real tags for audit rows."""
        resolver = getattr(self.tag_provider, "resolve", None)
        return resolver(tag) if callable(resolver) else tag

    def _variables_for_condition(self, condition: str, run_id: str | None = None) -> tuple[str, dict[str, Any]]:
        variables = dict(self.runtime_variables)
        normalized, var_to_tag = normalize_tag_expression(condition)
        for var, tag in var_to_tag.items():
            if hasattr(self.tag_provider, "read_value"):
                tag_value = self.tag_provider.read_value(tag)  # type: ignore[attr-defined]
                variables[var] = tag_value.value
                if run_id:
                    self.store.log_read(run_id, self._audit_tag(tag), tag_value.value, tag_value.quality, tag_value.source, tag_value.timestamp, tag_value.message)
            else:
                value = self.tag_provider.read(tag)
                variables[var] = value
                if run_id:
                    self.store.log_read(run_id, self._audit_tag(tag), value)
        return normalized, variables

    def _eval_condition(self, condition: str, run_id: str | None = None) -> bool:
        return bool(self._eval_expression(condition, run_id=run_id))

    def _eval_expression(self, expression: str, run_id: str | None = None) -> Any:
        normalized, variables = self._variables_for_condition(expression, run_id=run_id)
        return self.evaluator.evaluate(normalized, variables)

    def _step_check(self, run_id: str, step: Step) -> str:
        assert step.condition is not None
        if not self._eval_condition(step.condition, run_id=run_id):
            raise RuntimeError(f"Check failed for step {step.id}: {step.condition}")
        return "passed"

    def _step_permissive(self, run_id: str, step: Step) -> str:
        assert step.condition is not None
        if self._eval_condition(step.condition, run_id=run_id):
            self.store.log_event(
                run_id,
                "PERMISSIVE_OK",
                self._prefixed(step.id),
                step.description or f"Permissive satisfied: {step.condition}",
                {"condition": step.condition, "section": step.section, "equipment": step.equipment},
            )
            return "passed"
        message = step.description or f"Permissive not satisfied: {step.condition}"
        self.store.log_event(
            run_id,
            "PERMISSIVE_BLOCKED",
            self._prefixed(step.id),
            message,
            {"condition": step.condition, "section": step.section, "equipment": step.equipment},
        )
        raise RuntimeError(message)

    def _step_wait_until(self, run_id: str, step: Step) -> str:
        assert step.condition is not None
        started = self._now()
        deadline = started + step.timeout_sec
        _, var_to_tag = normalize_tag_expression(step.condition)
        while True:
            normalized, variables = self._variables_for_condition(step.condition, run_id=run_id)
            satisfied = bool(self.evaluator.evaluate(normalized, variables))
            if self.wait_progress_fn:
                self.wait_progress_fn(step, {
                    "step_id": self._prefixed(step.id),
                    "condition": step.condition,
                    "values": {tag: variables.get(var) for var, tag in var_to_tag.items()},
                    "elapsed": self._now() - started,
                    "timeout": step.timeout_sec,
                    "satisfied": satisfied,
                })
            if satisfied:
                return "passed"
            if self._now() > deadline:
                raise TimeoutError(f"Timeout waiting for condition in step {step.id}: {step.condition}")
            self._sleep(step.poll_sec)

    def _step_write_tag(self, run_id: str, step: Step) -> str:
        assert step.tag is not None
        self.tag_provider.write(step.tag, step.value)
        self.store.log_write(run_id, self._audit_tag(step.tag), step.value)
        if hasattr(self.tag_provider, "proposed_writes"):
            self.store.log_event(run_id, "ADVISORY_WRITE_PROPOSAL", self._prefixed(step.id), f"Proposed write to {step.tag}", {"tag": self._audit_tag(step.tag), "value": step.value})
        return "passed"

    def _step_read_tag(self, run_id: str, step: Step) -> str:
        assert step.tag is not None and step.variable is not None
        if hasattr(self.tag_provider, "read_value"):
            tag_value = self.tag_provider.read_value(step.tag)  # type: ignore[attr-defined]
            value = tag_value.value
            self.store.log_read(
                run_id, self._audit_tag(step.tag), value, tag_value.quality, tag_value.source,
                tag_value.timestamp, tag_value.message,
            )
            if not tag_value.is_good:
                detail = f" {tag_value.message}" if tag_value.message else ""
                raise RuntimeError(
                    f"Read blocked for {step.tag}: quality is {tag_value.quality}.{detail}".rstrip()
                )
            if isinstance(value, float) and not math.isfinite(value):
                raise RuntimeError(f"Read blocked for {step.tag}: value is non-finite.")
        else:
            value = self.tag_provider.read(step.tag)
            self.store.log_read(run_id, self._audit_tag(step.tag), value)
        self.runtime_variables[step.variable] = value
        self.store.log_event(
            run_id,
            "PCS_INPUT",
            self._prefixed(step.id),
            step.description or f"Read {step.tag}",
            {"tag": step.tag, "variable": step.variable, "value": value},
        )
        return "passed"

    def _step_calculate(self, run_id: str, step: Step) -> str:
        assert step.expression is not None and step.variable is not None
        value = self._eval_expression(step.expression, run_id=run_id)
        self.runtime_variables[step.variable] = value
        self.store.log_event(
            run_id,
            "CALCULATION",
            self._prefixed(step.id),
            step.description or f"Calculated {step.variable}",
            {"expression": step.expression, "variable": step.variable, "value": value},
        )
        return "passed"

    def _step_user_event(self, run_id: str, step: Step) -> str:
        self.store.log_event(
            run_id,
            "USER_EVENT",
            self._prefixed(step.id),
            step.description or step.event_name,
            {"event_name": step.event_name, "payload": step.event_payload},
        )
        return "passed"

    def _step_ramp_tag(self, run_id: str, step: Step) -> str:
        assert step.tag is not None
        assert step.start is not None and step.end is not None and step.rate_per_sec is not None
        direction = 1 if step.end >= step.start else -1
        distance = abs(step.end - step.start)
        duration = distance / step.rate_per_sec
        started = self._now()
        scheduled_elapsed = 0.0
        current = step.start
        self.tag_provider.write(step.tag, current)
        self.store.log_write(run_id, self._audit_tag(step.tag), current)
        if hasattr(self.tag_provider, "proposed_writes"):
            self.store.log_event(run_id, "ADVISORY_WRITE_PROPOSAL", self._prefixed(step.id), f"Proposed ramp write to {step.tag}", {"tag": step.tag, "value": current})
        while (direction == 1 and current < step.end) or (direction == -1 and current > step.end):
            scheduled_elapsed = min(duration, scheduled_elapsed + step.poll_sec)
            due = started + scheduled_elapsed
            self._sleep(max(0.0, due - self._now()))
            # Use the measured clock when the scheduler or connector is late;
            # the scheduled floor also keeps deterministic test clocks valid.
            elapsed = min(duration, max(scheduled_elapsed, self._now() - started))
            current = step.start + direction * step.rate_per_sec * elapsed
            if elapsed >= duration:
                current = step.end
            value = round(current, 6)
            self.tag_provider.write(step.tag, value)
            self.store.log_write(run_id, self._audit_tag(step.tag), value)
            if hasattr(self.tag_provider, "proposed_writes"):
                self.store.log_event(run_id, "ADVISORY_WRITE_PROPOSAL", self._prefixed(step.id), f"Proposed ramp write to {step.tag}", {"tag": step.tag, "value": value})
        return "passed"

    def _step_operator_confirm(self, run_id: str, step: Step) -> str:
        message = step.display_text or step.description
        message_id = self._create_operator_message(run_id, step, "CONFIRMATION", message)
        if self.auto_confirm:
            self.store.log_event(run_id, "OPERATOR_CONFIRM", self._prefixed(step.id), "Auto-confirmed", {"auto_confirm": True})
            self._acknowledge_operator_message(message_id, comment="Training auto-acknowledgement")
            return "passed"
        if self.confirm_fn:
            confirmed = self.confirm_fn(step, message)
        else:
            response = input(f"CONFIRM STEP {step.id}: {step.description} [y/N]: ").strip().lower()
            confirmed = response in {"y", "yes"}
        if not confirmed:
            if self.runtime_control and self.runtime_control.is_abort_requested:
                raise RuntimeAbortRequested(f"Operator aborted at confirmation step {step.id}")
            raise RuntimeError(f"Operator did not confirm step {step.id}")
        self.store.log_event(run_id, "OPERATOR_CONFIRM", self._prefixed(step.id), "Operator confirmed", {"auto_confirm": False})
        self._acknowledge_operator_message(message_id)
        return "passed"

    def _step_instruction(self, run_id: str, step: Step) -> str:
        message = step.display_text or step.operator_guidance or step.description
        message_id = self._create_operator_message(run_id, step, "GUIDANCE", message)
        data = {
            "auto_confirm": self.auto_confirm,
            "section": step.section,
            "unit_procedure": step.unit_procedure,
            "equipment": step.equipment,
            "display_text": step.display_text,
            "operator_guidance": step.operator_guidance,
            "document_link": step.document_link,
            "video_link": step.video_link,
        }
        if self.auto_confirm:
            self.store.log_event(run_id, "OPERATOR_INSTRUCTION", self._prefixed(step.id), message, data | {"acknowledged": True})
            self._acknowledge_operator_message(message_id, comment="Training auto-acknowledgement")
            return "passed"
        if self.confirm_fn:
            acknowledged = self.confirm_fn(step, message)
        else:
            response = input(f"INSTRUCTION STEP {step.id}: {message} [Enter/ok to acknowledge, cancel to stop]: ").strip().lower()
            acknowledged = response not in {"cancel", "c", "no", "n"}
        if not acknowledged:
            if self.runtime_control and self.runtime_control.is_abort_requested:
                raise RuntimeAbortRequested(f"Operator aborted at instruction step {step.id}")
            raise RuntimeError(f"Operator did not acknowledge instruction step {step.id}")
        self.store.log_event(run_id, "OPERATOR_INSTRUCTION", self._prefixed(step.id), message, data | {"acknowledged": True})
        self._acknowledge_operator_message(message_id)
        return "passed"

    def _step_operator_comment(self, run_id: str, step: Step) -> str:
        prompt = step.comment_prompt or step.description or f"Comment for {step.id}"
        message_id = self._create_operator_message(run_id, step, "COMMENT", prompt)
        if self.auto_confirm:
            comment = step.operator_comment or self.auto_comment
        elif self.comment_fn:
            comment = self.comment_fn(step, prompt).strip()
        else:
            comment = input(f"COMMENT STEP {step.id}: {prompt}: ").strip()
            if not comment:
                raise RuntimeError(f"Operator comment required for step {step.id}")
        self.store.log_event(run_id, "OPERATOR_COMMENT", self._prefixed(step.id), prompt, {"comment": comment})
        self._acknowledge_operator_message(message_id, comment=comment)
        return "passed"

    def _step_operator_input(self, run_id: str, step: Step) -> str:
        assert step.variable is not None
        prompt = step.display_text or step.description or f"Enter {step.variable}"
        message_id = self._create_operator_message(run_id, step, "SETTING", prompt)
        if self.auto_confirm:
            raw_value = step.value if step.value is not None else self.runtime_variables[step.variable]
        elif self.input_fn:
            raw_value = self.input_fn(step, prompt)
        else:
            raw_value = input(f"INPUT STEP {step.id}: {prompt}: ").strip()
        value = self._coerce_operator_input(step, raw_value)
        self.runtime_variables[step.variable] = value
        self.store.log_event(
            run_id,
            "OPERATOR_INPUT",
            self._prefixed(step.id),
            prompt,
            {
                "variable": step.variable,
                "value": value,
                "input_type": step.input_type,
                "min_value": step.min_value,
                "max_value": step.max_value,
                "auto_confirm": self.auto_confirm,
            },
        )
        self._acknowledge_operator_message(message_id, comment=f"{step.variable} = {value!r}")
        return "passed"

    @staticmethod
    def _coerce_operator_input(step: Step, raw_value: Any) -> Any:
        try:
            if step.input_type == "float":
                value: Any = float(raw_value)
            elif step.input_type == "int":
                numeric = float(raw_value)
                if not numeric.is_integer():
                    raise ValueError("value is not a whole number")
                value = int(numeric)
            elif step.input_type == "bool":
                if isinstance(raw_value, bool):
                    value = raw_value
                elif str(raw_value).strip().lower() in {"true", "yes", "1", "on"}:
                    value = True
                elif str(raw_value).strip().lower() in {"false", "no", "0", "off"}:
                    value = False
                else:
                    raise ValueError("expected true/false")
            elif step.input_type == "selection":
                value = raw_value if raw_value in step.choices else str(raw_value)
            else:
                value = str(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid {step.input_type} input for {step.id}: {raw_value!r} ({exc})") from exc
        if step.input_type == "selection" and value not in step.choices:
            raise ValueError(f"Input for {step.id} must be one of {step.choices!r}")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if step.min_value is not None and value < step.min_value:
                raise ValueError(f"Input for {step.id} must be at least {step.min_value}")
            if step.max_value is not None and value > step.max_value:
                raise ValueError(f"Input for {step.id} must be at most {step.max_value}")
        return value

    def _step_delay(self, run_id: str, step: Step) -> str:
        assert step.delay_sec is not None
        self._sleep(step.delay_sec)
        return "passed"

    def _step_warning(self, run_id: str, step: Step) -> str:
        self.store.log_event(run_id, "WARNING", self._prefixed(step.id), step.description, {"severity": step.severity})
        self._create_operator_message(run_id, step, "WARNING", step.description, severity=step.severity)
        return "warning"

    def _step_alarm(self, run_id: str, step: Step) -> str:
        self.store.log_event(run_id, "ALARM", self._prefixed(step.id), step.description, {"severity": step.severity})
        self._create_operator_message(run_id, step, "ALARM", step.description, severity=step.severity)
        return "alarm"

    def _step_watchdog(self, run_id: str, step: Step) -> str:
        assert step.condition is not None
        if self._eval_condition(step.condition, run_id=run_id):
            self.store.log_event(run_id, "WATCHDOG_OK", self._prefixed(step.id), step.description, {"condition": step.condition})
            return "passed"
        message = step.description or f"Watchdog condition failed: {step.condition}"
        self.store.log_event(run_id, "WATCHDOG_ALARM", self._prefixed(step.id), message, {"condition": step.condition, "severity": step.severity})
        self._create_operator_message(run_id, step, "WATCHDOG", message, severity=step.severity)
        raise RuntimeError(message)

    def _step_hold(self, run_id: str, step: Step) -> str:
        self.store.log_event(run_id, "HOLD", self._prefixed(step.id), step.description, {"severity": step.severity})
        self._create_operator_message(run_id, step, "HOLD", step.description or f"Procedure held at {step.id}", severity="warning")
        raise HoldRequested(step.description or f"Procedure held at step {step.id}")

    def _step_abort(self, run_id: str, step: Step) -> str:
        self.store.log_event(run_id, "ABORT", self._prefixed(step.id), step.description, {"severity": step.severity})
        self._create_operator_message(run_id, step, "ABORT", step.description or f"Procedure aborted at {step.id}", severity="critical")
        raise AbortRequested(step.description or f"Procedure aborted at step {step.id}")

    def _step_complete(self, run_id: str, step: Step) -> str:
        return "passed"

    # ------------------------------------------------------------------
    # Nested subprocedures (namespaced flow composition)

    def _step_subprocedure(self, run_id: str, step: Step) -> str:
        from .procedure_loader import load_procedure

        assert step.subprocedure_path is not None
        source = self._procedure.source_path if self._procedure else None
        base = Path(source).parent if source else Path.cwd()
        from .safe_paths import resolve_within

        root = self._dependency_root or base.resolve()
        child_path = resolve_within(
            root,
            base.resolve().relative_to(root) / step.subprocedure_path,
            label="subprocedure_path",
        )
        key = str(child_path)
        if key in self._active_subprocedures:
            raise RuntimeError(f"Recursive subprocedure call detected: {child_path}")
        child = load_procedure(child_path)
        declared_variables = {variable.name for variable in child.variables}
        unknown = set(step.parameters) - declared_variables
        if unknown:
            raise RuntimeError(
                f"Subprocedure step {step.id} passes parameters not declared as variables in {child_path.name}: {sorted(unknown)}"
            )
        provider: TagProvider = self.tag_provider
        if step.tag_aliases:
            provider = AliasedTagProvider(self.tag_provider, step.tag_aliases)
        prefix = f"{self.step_id_prefix}{step.prefix or step.id}."
        child_engine = ProcedureExecutionEngine(
            tag_provider=provider,
            store=self.store,
            sleep_fn=self.sleep_fn,
            auto_confirm=self.auto_confirm,
            fail_on_validation_warning=self.fail_on_validation_warning,
            auto_comment=self.auto_comment,
            confirm_fn=self.confirm_fn,
            comment_fn=self.comment_fn,
            input_fn=self.input_fn,
            step_event_fn=self.step_event_fn,
            wait_progress_fn=self.wait_progress_fn,
            message_event_fn=self.message_event_fn,
            runtime_control=self.runtime_control,
            step_id_prefix=prefix,
            active_subprocedures=self._active_subprocedures | {key},
            parallel_join_timeout_sec=self.parallel_join_timeout_sec,
            dependency_root=root,
            operator_identity=self.operator_identity,
            prepared_metadata=self.prepared_metadata,
        )
        # The child runs synchronously in this thread; inherit any parallel
        # branch cancellation scope so an or-join can stop it too.
        child_engine._local.cancel_stack = getattr(self._local, "cancel_stack", None)
        self.store.log_event(
            run_id,
            "SUBPROCEDURE_START",
            self._prefixed(step.id),
            step.description or f"Starting subprocedure {child.name}",
            {"path": key, "parameters": step.parameters, "tag_aliases": step.tag_aliases},
        )
        child_engine._execute_body(run_id, child, parameter_overrides=step.parameters)
        self.store.log_event(
            run_id,
            "SUBPROCEDURE_COMPLETE",
            self._prefixed(step.id),
            f"Subprocedure {child.name} complete",
            {"path": key},
        )
        return "passed"
