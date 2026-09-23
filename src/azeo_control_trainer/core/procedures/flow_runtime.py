"""Azeo supervision for the retained workflow engine."""
from copy import deepcopy
import queue
import threading
import time

from azeo_control_trainer.core.pa_designer.core.execution_engine import _BranchCancelled
from azeo_control_trainer.core.pa_designer.core.runtime_control import RuntimeAbortRequested
from azeo_control_trainer.core.pa_designer.core.tag_references import referenced_tags_in_expression


class StepRejected(RuntimeError):
    """An observed rejection, distinct from data/storage/runtime faults."""


class FlowRuntime:
    def _run_step(self, run_id, step):
        if step.event_name == "SUBPROCEDURE_ENTER":
            scope = step.event_payload["scope"]
            with self._loop_lock:
                self._loop_visits = {key: value for key, value in self._loop_visits.items() if not key.startswith(scope)}
            self.store.log_event(run_id, "SUBPROCEDURE_START", step.id, step.description, {"scope": scope})
        try:
            return super()._run_step(run_id, step)
        except _BranchCancelled:
            self._notify_step(step, "CANCELLED")
            self.store.log_event(run_id, "PARALLEL_STEP_CANCELLED", step.id, "Pending branch step cancelled")
            raise

    def _step_check(self, run_id, step):
        if not self._eval_condition(step.condition, run_id=run_id):
            raise StepRejected(f"Check failed for step {step.id}: {step.condition}")
        return "passed"

    def _condition_check(self, run_id, step, event, message_kind):
        ready = self._eval_condition(step.condition, run_id=run_id)
        self.store.log_event(run_id, event + ("_OK" if ready else "_ALARM" if event == "WATCHDOG" else "_BLOCKED"), step.id,
                             step.description, {"condition": step.condition})
        if not ready:
            self._create_operator_message(run_id, step, message_kind, step.description or step.condition, severity=step.severity)
            raise StepRejected(step.description or f"Condition failed: {step.condition}")
        return "passed"

    def _step_permissive(self, run_id, step):
        return self._condition_check(run_id, step, "PERMISSIVE", "PERMISSIVE")

    def _step_watchdog(self, run_id, step):
        return self._condition_check(run_id, step, "WATCHDOG", "WATCHDOG")

    def _run_flow_action(self, run_id, step, edges):
        outcomes = {e.outcome for e in edges}
        try:
            return self._run_step(run_id, step)[0]
        except TimeoutError:
            # Missing/bad feedback must not masquerade as a process timeout.
            for tag in referenced_tags_in_expression(step.condition or "False"):
                self.tag_provider.read_value(tag)
            if outcomes & {"timeout", "failed"}:
                return "timeout"
            raise
        except StepRejected:
            if "failed" in outcomes:
                return "failed"
            raise

    def _select_guarded_flow_edge(self, run_id, node, edges):
        if node.kind == "loop":
            with self._loop_lock:
                visits = self._loop_visits.get(node.id, 0)
                self._loop_visits[node.id] = visits + 1
            if visits >= node.max_iterations:
                self.store.log_event(run_id, "FLOW_RETRY_LIMIT", node.id, "Retry limit reached",
                                     {"node_id": node.id, "attempts": visits, "limit": node.max_iterations})
                return next(edge for edge in edges if edge.is_default)
        return super()._select_guarded_flow_edge(run_id, node, edges)

    def _wait_for_flow_condition(self, run_id, node):
        from .model import AdvisoryStep
        step = AdvisoryStep(id=node.id, type="wait_until", description=node.label,
                            condition=node.condition, stable_for_sec=node.stable_for_sec,
                            timeout_sec=node.timeout_sec, poll_sec=node.poll_sec)
        self._step_wait_until(run_id, step)
        return True

    def _run_flow_transition(self, run_id, node):
        from .model import AdvisoryStep
        step = AdvisoryStep(id=node.id, type="operator_confirm", description=node.label or node.id)
        self._notify_step(step, "ACTIVE")
        if node.completion_mode != "operator":
            self._wait_for_flow_condition(run_id, node)
        if node.completion_mode != "process":
            if not self._confirm_flow_transition(run_id, node):
                raise StepRejected("Operator did not confirm transition " + node.id)
            if node.completion_mode == "process_and_operator":
                # Equipment may change while the human reads the prompt.
                self._wait_for_flow_condition(run_id, node)
        self._notify_step(step, "PASSED")
        self.store.log_event(run_id, "FLOW_TRANSITION_COMPLETE", node.id, node.label or node.id,
                             {"node_id": node.id, "completion_mode": node.completion_mode})

    def _run_parallel_fork(self, run_id, state, node):
        edges = state.outgoing[node.id]
        cancel = threading.Event()
        stack = list(getattr(self._local, "cancel_stack", ()) or ()) + [cancel]
        base = deepcopy(self.runtime_variables)
        results = queue.Queue(maxsize=len(edges))
        self.store.log_event(run_id, "PARALLEL_FORK", node.id, node.label or node.id,
                             {"node_id": node.id, "branches": [e.target for e in edges]})

        def work(edge):
            self._local.cancel_stack = stack
            self._local.runtime_variables = deepcopy(base)
            self.runtime_control.branch_scope.events = stack
            try:
                arrival = self._traverse_flow(run_id, state, edge.target)
                results.put((edge.target, arrival, dict(self.runtime_variables), None))
            except BaseException as error:
                results.put((edge.target, None, None, error))

        threads = [threading.Thread(target=work, args=(edge,), name="azeo-pa-branch-" + edge.target,
                                    daemon=True) for edge in edges]
        started = []
        arrivals, failure, join_id = [], None, None
        pending = len(threads)
        deadline = self._now() + node.timeout_sec
        try:
            for thread in threads:
                thread.start()
                started.append(thread)
                with self._loop_lock:
                    self._parallel_threads.add(thread)
            while pending:
                self._checkpoint()
                if self._now() > deadline:
                    raise TimeoutError(f"Parallel group {node.id} exceeded its convergence timeout")
                try:
                    branch, arrival, values, error = results.get(timeout=.05)
                except queue.Empty:
                    continue
                pending -= 1
                if isinstance(error, _BranchCancelled):
                    self.store.log_event(run_id, "PARALLEL_BRANCH_CANCELLED", branch,
                                         "Branch cancelled after join", {"fork_id": node.id})
                    continue
                if error is not None:
                    raise error
                if arrival is None or state.nodes[arrival].kind != "parallel_join":
                    raise RuntimeError("Parallel branch ended without its join")
                if join_id is not None and join_id != arrival:
                    raise RuntimeError("Parallel branches reached different joins")
                join_id = arrival
                arrivals.append((branch, values))
                if state.nodes[join_id].join_mode == "or":
                    cancel.set()
        except BaseException as error:
            failure = error
        finally:
            cancel.set()
            # Completion is not published while an audit-writing branch is
            # still alive. SQLite operations have their own bounded timeout.
            stop_deadline = time.monotonic() + 35
            for thread in started:
                thread.join(timeout=max(0, stop_deadline - time.monotonic()))
            with self._loop_lock:
                self._parallel_threads.difference_update(t for t in threads if not t.is_alive())
            if any(thread.is_alive() for thread in threads):
                self.runtime_control.stop("A parallel worker did not stop")
                raise RuntimeAbortRequested("Parallel worker shutdown exceeded the storage timeout")
        if failure is not None:
            raise failure
        if join_id is None:
            raise RuntimeError("Parallel group produced no join")
        selected = arrivals[:1] if state.nodes[join_id].join_mode == "or" else arrivals
        changes = {}
        for branch, values in selected:
            for name, value in values.items():
                if base.get(name) != value:
                    if name in changes:
                        raise RuntimeError(f"Parallel branches both changed {name}; give each branch its own result")
                    changes[name] = value
        self.runtime_variables.update(changes)
        self._publish_memory()
        edge = state.outgoing[join_id][0]
        self.store.log_event(run_id, "PARALLEL_JOIN", join_id, "Parallel group joined",
                             {"node_id": join_id, "join_mode": state.nodes[join_id].join_mode,
                              "arrivals": len(selected)})
        self.store.log_event(run_id, "FLOW_EDGE", join_id, edge.label or "Continue after join",
                             {"source": join_id, "target": edge.target, "outcome": "passed"})
        return edge.target
