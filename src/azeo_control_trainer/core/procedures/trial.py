"""Deterministic procedure trials whose writes cannot reach a controller."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from azeo_control_trainer.core.pa_designer.connectors.tag_provider import TagProvider
from azeo_control_trainer.core.pa_designer.connectors.tag_value import TagValue
from azeo_control_trainer.core.pa_designer.core.execution_engine import ExecutionResult, ProcedureExecutionEngine

from .audit import ProcedureStore
from .model import ProcedureDefinition


class IsolatedTrialProvider(TagProvider):
    """An in-memory tag provider with an inspectable output journal."""

    def __init__(self, values: dict[str, Any]):
        self.values = dict(values)
        self.writes: list[dict[str, Any]] = []

    def read(self, tag: str) -> Any:
        if tag not in self.values:
            raise ValueError(f"Trial input has no value for {tag}")
        return self.values[tag]

    def read_value(self, tag: str) -> TagValue:
        return TagValue(tag, self.read(tag), source="isolated procedure trial")

    def write(self, tag: str, value: Any) -> None:
        self.values[tag] = value
        self.writes.append({"tag": tag, "value": value})

    def snapshot(self) -> dict[str, Any]:
        return dict(self.values)


class _TrialEngine(ProcedureExecutionEngine):
    """Advance procedure time without making a wall-clock trial wait."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, sleep_fn=lambda _seconds: None, **kwargs)
        self._trial_time = 0.0
        self._user_events = set()

    @staticmethod
    def _source_id(step):
        from .library import block_for_step
        return block_for_step(step).source_id

    def _checked_variable(self, name, value):
        spec = next(row for row in self._procedure.variables if row.name == name)
        return spec.checked(value)

    def _now(self) -> float:
        return self._trial_time

    def _sleep(self, seconds: float) -> None:
        self._trial_time += max(0.0, seconds)

    def _step_write_tag(self, run_id, step):
        from .outputs import confirm_tag_output
        confirm_tag_output(self, run_id, step, f"Set {step.tag} to {step.value!r}")
        return super()._step_write_tag(run_id, step)

    def _step_ramp_tag(self, run_id, step):
        from .outputs import confirm_tag_output
        confirm_tag_output(
            self,
            run_id,
            step,
            f"Ramp {step.tag} from {step.start:g} to {step.end:g} at "
            f"{step.rate_per_sec:g} per second",
        )
        return super()._step_ramp_tag(run_id, step)

    def _step_user_event(self, run_id, step):
        result = super()._step_user_event(run_id, step)
        self._user_events.add(step.event_name)
        return result

    def _adapter_inputs(self, run_id, step):
        return {
            key: self._eval_expression(value[1:], run_id=run_id)
            if isinstance(value, str) and value.startswith("=") else value
            for key, value in step.adapter_inputs.items()
        }

    def _run_adapter(self, run_id, step):
        from .adapters import execute_adapter
        inputs = self._adapter_inputs(run_id, step)
        results = execute_adapter(
            step.adapter_id,
            step.adapter_version,
            inputs,
            run_id=run_id,
            step_id=self._prefixed(step.id),
            actor=self.operator_identity,
            variables=self.runtime_variables,
            timeout_sec=step.adapter_timeout_sec,
        )
        published = {}
        for result_name, variable in step.adapter_results.items():
            if result_name not in results:
                raise ValueError(f"Adapter {step.adapter_id} did not return {result_name}")
            published[variable] = self._checked_variable(variable, results[result_name])
        self.runtime_variables.update(published)
        self.store.log_event(
            run_id, "APPLICATION_ADAPTER", self._prefixed(step.id), step.description,
            {"adapter_id": step.adapter_id, "adapter_version": step.adapter_version,
             "inputs": inputs, "results": results, "published": published},
        )
        return "passed"

    def _step_instruction(self, run_id, step):
        source_id = self._source_id(step)
        if source_id == "integration.legacy_script":
            value = self._checked_variable(
                step.variable, self._eval_expression(step.expression, run_id=run_id)
            )
            self.runtime_variables[step.variable] = value
            self.store.log_event(
                run_id, "SAFE_SCRIPT", self._prefixed(step.id), step.description,
                {"expression": step.expression, "variable": step.variable, "value": value},
            )
            return "passed"
        if source_id == "integration.user_application":
            return self._run_adapter(run_id, step)
        if source_id == "integration.activex_opc_com":
            if step.integration_operation == "read":
                return self._step_read_tag(run_id, step.model_copy(update={"type": "read_tag"}))
            if step.integration_operation == "write":
                return self._step_write_tag(run_id, step.model_copy(update={"type": "write_tag"}))
            return self._run_adapter(run_id, step)
        if source_id == "message.hmi_window":
            target = step.hmi_target or step.equipment or step.document_link
            self.store.log_event(
                run_id, "HMI_WINDOW_REQUEST", self._prefixed(step.id), step.description,
                {"target": target, "accepted": True, "trial": True},
            )
            return "passed"
        return super()._step_instruction(run_id, step)

    def _step_operator_confirm(self, run_id, step):
        if step.operator_guidance and not step.display_text:
            step = step.model_copy(update={"display_text": step.operator_guidance})
        return super()._step_operator_confirm(run_id, step)

    def _notification(self, run_id, step, event_type, outcome):
        message = step.display_text or step.operator_guidance or step.description
        self.store.log_event(
            run_id, event_type, self._prefixed(step.id), message,
            {"severity": step.severity},
        )
        message_id = self._create_operator_message(
            run_id, step, event_type, message, severity=step.severity
        )
        if step.require_confirmation:
            confirmed = self.auto_confirm or bool(
                self.confirm_fn and self.confirm_fn(step, message)
            )
            self.store.log_event(
                run_id, f"{event_type}_ACK", self._prefixed(step.id), message,
                {"acknowledged": confirmed, "auto_confirm": self.auto_confirm},
            )
            if not confirmed:
                raise RuntimeError(f"Operator did not acknowledge {step.id}")
            self._acknowledge_operator_message(message_id)
        return outcome

    def _step_warning(self, run_id, step):
        return self._notification(run_id, step, "WARNING", "warning")

    def _step_alarm(self, run_id, step):
        return self._notification(run_id, step, "ALARM", "alarm")

    def _step_hold(self, run_id, step):
        if self._source_id(step) != "flow.pause":
            return super()._step_hold(run_id, step)
        self.store.log_event(
            run_id, "PROCEDURE_PAUSED", self._prefixed(step.id), step.resume_prompt,
            {"authored": True, "trial": True},
        )
        self.store.log_event(
            run_id, "PROCEDURE_RESUMED", self._prefixed(step.id), step.resume_prompt,
            {"authored": True, "trial": True},
        )
        return "passed"

    def _step_watchdog(self, run_id, step):
        if self._source_id(step) not in {"monitor.process_value", "monitor.device_state"}:
            return super()._step_watchdog(run_id, step)
        started = self._now()
        while self._now() - started < step.monitor_duration_sec:
            if not self._eval_condition(step.condition, run_id=run_id):
                return super()._step_watchdog(run_id, step)
            self._sleep(min(step.poll_sec, step.monitor_duration_sec - (self._now() - started)))
        self.store.log_event(
            run_id, "MONITOR_COMPLETE", self._prefixed(step.id), step.description,
            {"condition": step.condition, "duration_sec": self._now() - started},
        )
        return "passed"

    def _step_calculate(self, run_id, step):
        if not step.calculation_rows:
            return super()._step_calculate(run_id, step)
        declared = {value.name: value for value in self._procedure.variables}
        before = dict(self.runtime_variables)
        try:
            for row in step.calculation_rows:
                value = self._eval_expression(row.expression, run_id=run_id)
                self.runtime_variables[row.variable] = declared[row.variable].checked(value)
                self.store.log_event(
                    run_id,
                    "CALCULATION",
                    self._prefixed(step.id),
                    f"Calculated {row.variable}",
                    {"expression": row.expression, "variable": row.variable, "value": value},
                )
        except Exception:
            self.runtime_variables.clear()
            self.runtime_variables.update(before)
            raise
        return "passed"

    def _step_wait_until(self, run_id, step):
        if self._source_id(step) == "flow.user_event_wait":
            started = self._now()
            while step.event_name not in self._user_events:
                if self._now() - started >= step.timeout_sec:
                    raise TimeoutError(f"Timed out waiting for user event {step.event_name}")
                self._sleep(step.poll_sec)
            self.store.log_event(
                run_id, "USER_EVENT_RECEIVED", self._prefixed(step.id), step.description,
                {"event_name": step.event_name, "elapsed": self._now() - started},
            )
            return "passed"
        from .conditions import condition_clauses
        from .logic import ConditionRow, ContinuousConditions, row_logic

        start = self._now()
        match, clauses = condition_clauses(step.condition)
        rows = step.condition_rows or [
            ConditionRow(id=f"C{index + 1}", expression=clause)
            for index, clause in enumerate(clauses)
        ]
        logic = (
            row_logic(rows, step.condition_match, step.condition_logic)
            if step.condition_rows
            else row_logic(rows, match)
        )
        tracker = ContinuousConditions(rows, logic, step.stable_for_sec)
        while True:
            if step.calculation_rows:
                self._step_calculate(run_id, step)
            progress = tracker.observe(
                self._now(),
                lambda expression: self._eval_condition(expression, run_id=run_id),
            )
            progress.update(
                elapsed=self._now() - start,
                match=logic if step.condition_logic else (
                    step.condition_match if step.condition_rows else match
                ),
                timeout=step.timeout_sec,
            )
            if self.wait_progress_fn:
                self.wait_progress_fn(step, progress)
            if progress["complete"]:
                return "passed"
            if self._now() - start >= step.timeout_sec:
                raise TimeoutError(f"Timed out waiting for {step.description or step.condition}")
            self._sleep(step.poll_sec)


@dataclass(frozen=True)
class TrialResult:
    execution: ExecutionResult
    final_values: dict[str, Any]
    writes: tuple[dict[str, Any], ...]


def run_isolated_trial(
    definition: ProcedureDefinition,
    inputs: dict[str, Any],
    history_path: str | Path,
    *,
    operator_identity: str = "Procedure trial",
) -> TrialResult:
    """Run a reviewed revision against presets and internal-only outputs.

    This is the isolated offline trial boundary: inputs are supplied by
    the caller, every output changes only this provider, and the normal signed
    run evidence is retained for inspection.
    """
    definition.validate()
    procedure = definition.procedure
    declared = procedure.declared_tag_map()
    unknown = set(inputs) - set(declared)
    if unknown:
        raise ValueError("Unknown trial inputs: " + ", ".join(sorted(unknown)))
    values = {
        tag.tag: tag.initial_value
        for tag in procedure.tags
        if tag.initial_value is not None
    }
    values.update(inputs)
    provider = IsolatedTrialProvider(values)
    store = ProcedureStore(Path(history_path))
    engine = _TrialEngine(
        provider,
        store,
        auto_confirm=True,
        operator_identity=operator_identity,
        prepared_metadata={
            "execution_environment": "isolated_trial",
            "live_outputs_enabled": False,
            "document_sha256": definition.digest,
        },
    )
    execution = engine.run(procedure)
    return TrialResult(execution, provider.snapshot(), tuple(provider.writes))


__all__ = ["IsolatedTrialProvider", "TrialResult", "run_isolated_trial"]
