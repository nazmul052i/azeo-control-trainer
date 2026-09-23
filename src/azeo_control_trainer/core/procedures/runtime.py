"""Advisory execution over immutable observations and a simulation clock."""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
from copy import deepcopy
from collections import deque
import math
from pathlib import Path
import queue
import threading
import time
import uuid

from azeo_control_trainer.core.pa_designer.connectors.advisory_tag_provider import AdvisoryTagProvider, BadTagQualityError
from azeo_control_trainer.core.pa_designer.connectors.live_monitor import tag_age_seconds, _MAX_FUTURE_SKEW_SECONDS
from azeo_control_trainer.core.pa_designer.connectors.read_only_connector import ReadOnlyTagConnector
from azeo_control_trainer.core.pa_designer.connectors.tag_value import TagValue
from azeo_control_trainer.core.pa_designer.core.execution_engine import HoldRequested, ProcedureExecutionEngine, _BranchCancelled
from azeo_control_trainer.core.pa_designer.core.runtime_control import RuntimeAbortRequested, RuntimeControl

from .model import ProcedureDefinition
from .audit import ProcedureStore
from .logic import PreparedEvaluator, prepared_expression
from .flow_runtime import FlowRuntime


class ObservationChanged(RuntimeError):
    """A pause/resume invalidated an observation while it was being evaluated."""


class CapturedTagProvider(AdvisoryTagProvider):
    def read_value(self, tag):
        observed_at = getattr(self.connector._local, "observed_at", None)
        if observed_at is None:
            return super().read_value(tag)
        sample = self.connector.read_value(tag)
        self.read_cache[tag] = sample
        # A recorded scan is historical evidence. Its timestamp must be fresh
        # at capture, not at the later instant that its audit transaction runs.
        # Keep the original timestamp in the evidence; live reads retain the
        # upstream wall-clock check and the host still requires fresh scans.
        uncertain = self.allow_uncertain_quality and sample.quality.strip().casefold() == "uncertain"
        if self.require_good_quality and not sample.is_good and not uncertain:
            raise BadTagQualityError(f"Bad quality for tag {tag}: {sample.quality}")
        age = tag_age_seconds(sample, observed_at)
        if self.require_timestamp and age is None:
            raise BadTagQualityError(f"Missing or invalid source timestamp for tag {tag}")
        if age is not None and age < -_MAX_FUTURE_SKEW_SECONDS:
            raise BadTagQualityError(f"Future source timestamp for tag {tag}")
        if not self.allow_stale and age is not None and age > self.stale_after_seconds:
            raise BadTagQualityError(f"Stale value for tag {tag} at capture: age {age:.1f}s")
        if isinstance(sample.value, float) and not math.isfinite(sample.value):
            raise BadTagQualityError(f"Non-finite numeric value for tag {tag}")
        return sample


class SimulationControl(RuntimeControl):
    def __init__(self):
        super().__init__(poll_interval=0.02)
        self.lock = threading.RLock()
        self.branch_scope = threading.local()
        self.elapsed = 0.0
        self.sim_time = None
        self.host_paused = False
        self.updated = time.monotonic()
        self.reason = "Operator aborted the procedure"
        self.observation_grace_deadline = 0.0
        self.observation_epoch = 0
        self._awaiting_resume_observation = False
        self._pause_observed = False
        self._exclude_resume_delta = False
        self.authored_pause = False

    def suspend_for_presentation(self):
        # First-time Qt faceplate construction may stop the GUI-owned scan
        # executive briefly. Wait for new evidence without crediting that gap.
        with self.lock:
            self.host_paused = True
            self.observation_epoch += 1
            self.observation_grace_deadline = time.monotonic() + 30

    def pause(self):
        # Manual takeover interrupts continuous evidence even when process
        # values happen to be unchanged when guidance resumes.
        with self.lock:
            if not self.is_paused:
                self.observation_epoch += 1
                self._pause_observed = False
            super().pause()

    def resume(self):
        with self.lock:
            if self.is_paused:
                self.observation_epoch += 1
                self._awaiting_resume_observation = True
                self._exclude_resume_delta = not self._pause_observed
            super().resume()

    def begin_authored_pause(self):
        """Freeze procedure time while its Pause block waits for Resume."""
        with self.lock:
            self.authored_pause = True
            self.observation_epoch += 1

    def end_authored_pause(self):
        with self.lock:
            self.authored_pause = False
            self.observation_epoch += 1
            self._awaiting_resume_observation = True

    def observe(self, sim_time, *, paused=False):
        with self.lock:
            self.updated = time.monotonic()
            if not math.isfinite(sim_time):
                self.stop("Simulation time is unavailable")
                return
            if self.sim_time is not None:
                delta = sim_time - self.sim_time
                if delta < -1e-7:
                    self.stop("Simulation time rewound; start a new procedure from the restored condition")
                elif not self.is_paused and not self.host_paused and not self.authored_pause \
                        and not self._exclude_resume_delta:
                    self.elapsed += max(0, delta)
            self.sim_time = sim_time
            if self.is_paused:
                self._pause_observed = True
            self._exclude_resume_delta = False
            if paused and not self.host_paused:
                self.observation_epoch += 1
            self.host_paused = paused
            if not paused and not self.is_paused:
                self._awaiting_resume_observation = False
            self.observation_grace_deadline = 0.0

    def stop(self, reason):
        self.reason = reason
        self.abort()

    def adjusted_monotonic(self):
        with self.lock:
            return self.elapsed

    def checkpoint(self):
        while True:
            if any(event.is_set() for event in getattr(self.branch_scope, "events", ())):
                raise _BranchCancelled()
            if self.is_abort_requested:
                raise RuntimeAbortRequested(self.reason)
            if time.monotonic() - self.updated > 10 and time.monotonic() > self.observation_grace_deadline:
                self.stop("Procedure observations stopped arriving from the station")
                continue
            if not self.is_paused and not self.host_paused and not self._awaiting_resume_observation:
                return
            self._abort_event.wait(self.poll_interval)

    def before_step(self, step_id):
        self._active_step = step_id
        self.checkpoint()
        return False

    def controlled_sleep(self, seconds):
        end = self.adjusted_monotonic() + max(0, seconds)
        epoch = self.observation_epoch
        while self.adjusted_monotonic() < end:
            self.checkpoint()
            if epoch != self.observation_epoch:
                return
            self._abort_event.wait(self.poll_interval)
        self.checkpoint()


class ObservationConnector(ReadOnlyTagConnector):
    name = "trainer_controller"

    def __init__(self, control):
        self.control = control
        self._lock = threading.Lock()
        self._samples = {}
        self._local = threading.local()
        self._sequence = 0
        self._observations = deque(maxlen=1024)

    def update(self, samples):
        with self._lock:
            self._samples = deepcopy(samples)

    def record(self):
        with self._lock:
            if self.control.is_paused or self.control.host_paused:
                return
            if self._observations and self._observations[-1][1:4] == (
                    self.control.elapsed, self.control.observation_epoch, self._samples):
                return
            self._sequence += 1
            self._observations.append((self._sequence, self.control.elapsed,
                                       self.control.observation_epoch, dict(self._samples), datetime.now(timezone.utc)))

    def observations_since(self, sequence):
        with self._lock:
            gap = bool(self._observations and sequence < self._observations[0][0] - 1)
            return [row for row in self._observations if row[0] > sequence], gap

    def restart_observations(self):
        with self._lock:
            self._observations.clear()
        self.record()

    def read_value(self, tag):
        self.control.checkpoint()
        frozen = getattr(self._local, "samples", None)
        if frozen is not None:
            if self._local.epoch != self.control.observation_epoch:
                raise ObservationChanged()
            if tag not in frozen:
                raise ValueError(f"No observation for {tag}")
            return frozen[tag]
        with self._lock:
            if tag not in self._samples:
                raise ValueError(f"No observation for {tag}")
            return self._samples[tag]

    def snapshot_values(self):
        frozen = getattr(self._local, "samples", None)
        if frozen is not None:
            return dict(frozen)
        with self._lock:
            return dict(self._samples)

    @contextmanager
    def frozen(self, samples, *, epoch=None, observed_at=None):
        self._local.samples = samples
        self._local.epoch = self.control.observation_epoch if epoch is None else epoch
        self._local.observed_at = observed_at
        try:
            yield
        finally:
            self._local.samples = None
            self._local.observed_at = None


class TrainerProcedureEngine(FlowRuntime, ProcedureExecutionEngine):
    @property
    def _memory_samples(self):
        return getattr(self._local, "memory_samples", {})

    @_memory_samples.setter
    def _memory_samples(self, value):
        self._local.memory_samples = value

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.evaluator = PreparedEvaluator()
        self._loop_lock = threading.Lock()
        self._loop_visits = {}
        self._parallel_threads = set()
        self._tuning_lock = threading.Lock()
        self._user_event_lock = threading.Lock()
        self._user_events = set()
        self.output_authorize_fn = None
        self.output_apply_fn = None
        self.output_bindings = {}
        self.hmi_request_fn = None
        self.hmi_alarm_fn = None

    @staticmethod
    def _source_id(step):
        from .library import block_for_step
        return block_for_step(step).source_id

    def _create_operator_message(
        self, run_id, step, event_type, message, *, severity=None, data=None
    ):
        """Publish the authored audible flag that the imported core omits."""
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
                "audible": step.audible,
                "operator": "",
                "acknowledged": False,
            })
        return message_id

    def _load_memory(self, samples=None):
        procedure = getattr(self, "_procedure", None)
        linked = [v for v in procedure.variables if v.tag_path] if procedure else []
        if not linked:
            return
        values = self.memory_store.read_many() if samples is None else {
            v.tag_path: samples["@memory/" + v.name].value for v in linked}
        for spec in linked:
            self.runtime_variables[spec.name] = spec.checked(values[spec.tag_path])
        self._memory_samples = ({v.name: samples["@memory/" + v.name] for v in linked} if samples is not None else
                                {v.name: TagValue(v.name, values[v.tag_path], "Good", datetime.now(timezone.utc).isoformat(),
                                                  "Shared project memory", v.tag_path) for v in linked})

    def _variables_for_condition(self, condition, run_id=None):
        normalized, references, names = prepared_expression(condition)
        variables = dict(self.runtime_variables)
        for name, tag in references:
            sample = self.tag_provider.read_value(tag)
            variables[name] = sample.value
            if run_id:
                self.store.log_read(run_id, self._audit_tag(tag), sample.value, sample.quality,
                                    sample.source, sample.timestamp, sample.message)
        if run_id and getattr(self, "_memory_samples", None):
            for name, sample in self._memory_samples.items():
                if name in names:
                    source = "Shared project memory" if variables[name] == sample.value else "PA calculation result"
                    self.store.log_read(run_id, sample.message, variables[name], sample.quality,
                                        source, sample.timestamp, "Memory variable " + name)
        return normalized, variables

    def _checkpoint(self):
        super()._checkpoint()
        self.apply_pending_tuning()
        self._load_memory()

    def apply_pending_tuning(self):
        with self._tuning_lock:
            self._apply_pending_tuning()

    def _apply_pending_tuning(self):
        from .parameters import apply_parameter, parameter_value, parameters
        requests = getattr(self, "tuning_requests", None)
        if requests is None or getattr(self, "_procedure", None) is None:
            return
        # Capture a finite batch: a producer must not extend this checkpoint
        # forever and starve condition evaluation or an operator abort.
        for _ in range(min(32, requests.qsize())):
            self.runtime_control.checkpoint()
            try:
                request = requests.get_nowait()
            except queue.Empty:
                break
            payload = dict(request)
            try:
                spec = parameters(self._procedure).get(request["parameter"])
                if spec is None or (request["effective"] == "live" and spec.access != "live"):
                    raise ValueError("This parameter is not exposed for this tuning mode")
                value = spec.checked(request["value"])
                old = self.memory_store.read(spec.path) if spec.path else parameter_value(
                    self._procedure, spec.id, self.runtime_variables)
                payload.update(previous=old, value=value)
            except (ValueError, KeyError) as error:
                payload["error"] = str(error)
                self.store.log_event(self._run_id, "PA_TUNING_REJECTED", request["ref"], str(error), payload)
                self.tuning_event_fn("parameter_rejected", payload)
                continue
            # A storage failure after mutation must end guidance, not claim
            # rejection and continue using hold evidence from the old value.
            self.store.log_event(self._run_id, "PA_TUNING_REQUESTED", request["ref"], "Parameter change requested", payload)
            if spec.path:
                self.memory_store.write(spec.path, value)
            apply_parameter(self._procedure, self.runtime_variables, spec.id, value)
            with self.runtime_control.lock:
                self.runtime_control.observation_epoch += 1
                self.runtime_control._awaiting_resume_observation = True
            self.store.log_event(self._run_id, "PA_TUNING_APPLIED", request["ref"], "Parameter change applied", payload)
            self.tuning_event_fn("parameter_applied", payload)
            self._publish_memory()

    def _save_memory(self, names):
        linked = [v for v in self._procedure.variables if v.tag_path and v.name in names]
        if linked:
            self.memory_store.write_many({v.tag_path: self.runtime_variables[v.name] for v in linked},
                                         types={v.tag_path: v.data_type for v in linked})

    def _observed_condition(self, clause, run_id):
        samples = self.tag_provider.connector.snapshot_values()
        if any(tag not in samples or not samples[tag].is_good or samples[tag].value is None
               for _, tag in prepared_expression(clause)[1]):
            return None
        return self._eval_condition(clause, run_id=run_id)

    def _checked_memory(self, name, value):
        memory = next(row for row in self._procedure.variables if row.name == name)
        return memory.checked(value)

    def _coerce_operator_input(self, step, raw_value):
        return self._checked_memory(step.variable, super()._coerce_operator_input(step, raw_value))

    def _publish_memory(self):
        if getattr(self._local, "cancel_stack", None):
            return  # Branch-local results become visible together at the join.
        values = dict(self.runtime_variables)
        if values != getattr(self, "_last_memory", None) and getattr(self, "memory_event_fn", None):
            self._last_memory = deepcopy(values)
            self.memory_event_fn(values)

    def _step_operator_input(self, run_id, step):
        step = step.model_copy(update={"value": self.runtime_variables[step.variable]})
        result = super()._step_operator_input(run_id, step)
        self._save_memory({step.variable})
        self._publish_memory()
        return result

    def _step_write_tag(self, run_id, step):
        from .outputs import apply_checked_output, authorize_tag_output
        operation = f"Set {step.tag} to {step.value!r}"
        live = authorize_tag_output(self, run_id, step, operation)
        if live:
            apply_checked_output(self, run_id, step, step.value)
            return "passed"
        return super()._step_write_tag(run_id, step)

    def _step_ramp_tag(self, run_id, step):
        from .outputs import apply_checked_output, authorize_tag_output
        operation = (
            f"Ramp {step.tag} from {step.start:g} to {step.end:g} at "
            f"{step.rate_per_sec:g} per second"
        )
        direction = 1 if step.end >= step.start else -1
        duration = abs(step.end - step.start) / step.rate_per_sec
        live = authorize_tag_output(
            self,
            run_id,
            step,
            operation,
            schedule={"start": step.start, "end": step.end,
                      "rate_per_sec": step.rate_per_sec, "poll_sec": step.poll_sec,
                      "duration_sec": duration},
        )
        if not live:
            return super()._step_ramp_tag(run_id, step)
        started = self._now()
        scheduled_elapsed = 0.0
        current = step.start
        apply_checked_output(self, run_id, step, current)
        while (direction == 1 and current < step.end) or (direction == -1 and current > step.end):
            scheduled_elapsed = min(duration, scheduled_elapsed + step.poll_sec)
            self._sleep(max(0.0, started + scheduled_elapsed - self._now()))
            elapsed = min(duration, max(scheduled_elapsed, self._now() - started))
            current = step.end if elapsed >= duration else step.start + direction * step.rate_per_sec * elapsed
            apply_checked_output(self, run_id, step, round(current, 6))
        return "passed"

    def _step_user_event(self, run_id, step):
        result = super()._step_user_event(run_id, step)
        with self._user_event_lock:
            self._user_events.add(step.event_name)
        return result

    def _wait_for_user_event(self, run_id, step):
        started = self._now()
        while True:
            self._checkpoint()
            with self._user_event_lock:
                if step.event_name in self._user_events:
                    self.store.log_event(
                        run_id, "USER_EVENT_RECEIVED", self._prefixed(step.id),
                        step.description or step.event_name,
                        {"event_name": step.event_name, "elapsed": self._now() - started},
                    )
                    return "passed"
            if self._now() - started >= step.timeout_sec:
                raise TimeoutError(f"Timed out waiting for user event {step.event_name}")
            self._sleep(step.poll_sec)

    def _adapter_inputs(self, run_id, step):
        values = {}
        for key, value in step.adapter_inputs.items():
            values[key] = self._eval_expression(value[1:], run_id=run_id) \
                if isinstance(value, str) and value.startswith("=") else value
        return values

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
            cancel_check=self._checkpoint,
        )
        updates = {}
        for result_name, variable in step.adapter_results.items():
            if result_name not in results:
                raise ValueError(f"Adapter {step.adapter_id} did not return {result_name}")
            updates[variable] = self._checked_memory(variable, results[result_name])
        self.runtime_variables.update(updates)
        self._save_memory(set(updates))
        self._publish_memory()
        self.store.log_event(
            run_id, "APPLICATION_ADAPTER", self._prefixed(step.id),
            step.description or step.adapter_id,
            {"adapter_id": step.adapter_id, "adapter_version": step.adapter_version,
             "inputs": inputs, "results": results,
             "published": updates},
        )
        return "passed"

    def _step_instruction(self, run_id, step):
        source_id = self._source_id(step)
        if source_id == "integration.legacy_script":
            value = self._checked_memory(step.variable, self._eval_expression(step.expression, run_id=run_id))
            self.runtime_variables[step.variable] = value
            self._save_memory({step.variable})
            self._publish_memory()
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
        if source_id == "message.hmi_window" and callable(self.hmi_request_fn):
            target = step.hmi_target or step.equipment or step.document_link
            accepted = bool(self.hmi_request_fn(step, target))
            self.store.log_event(
                run_id, "HMI_WINDOW_REQUEST", self._prefixed(step.id), step.description,
                {"target": target, "accepted": accepted},
            )
            if not accepted:
                raise RuntimeError(f"Operator declined HMI target for step {step.id}")
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
        if self._source_id(step) == "message.hmi_alarm" and callable(self.hmi_alarm_fn):
            message = step.display_text or step.operator_guidance or step.description
            self.hmi_alarm_fn(step, {
                "run_id": run_id,
                "step_id": self._prefixed(step.id),
                "message": message,
                "severity": step.severity,
                "equipment": step.equipment,
            })
            self.store.log_event(
                run_id, "HMI_ALARM_RAISED", self._prefixed(step.id), message,
                {"severity": step.severity, "equipment": step.equipment},
            )
        return self._notification(run_id, step, "ALARM", "alarm")

    def _step_watchdog(self, run_id, step):
        if self._source_id(step) not in {"monitor.process_value", "monitor.device_state"}:
            return super()._step_watchdog(run_id, step)
        started = self._now()
        while True:
            self._checkpoint()
            if not self._eval_condition(step.condition, run_id=run_id):
                return super()._step_watchdog(run_id, step)
            elapsed = self._now() - started
            if elapsed >= step.monitor_duration_sec:
                self.store.log_event(
                    run_id, "MONITOR_COMPLETE", self._prefixed(step.id), step.description,
                    {"condition": step.condition, "duration_sec": elapsed},
                )
                return "passed"
            self._sleep(min(step.poll_sec, step.monitor_duration_sec - elapsed))

    def _step_hold(self, run_id, step):
        if self._source_id(step) != "flow.pause":
            return super()._step_hold(run_id, step)
        prompt = step.resume_prompt or step.description or f"Resume procedure at {step.id}"
        self.runtime_control.begin_authored_pause()
        message_id = self._create_operator_message(
            run_id, step, "PAUSE", prompt, severity="warning"
        )
        self.store.log_event(
            run_id, "PROCEDURE_PAUSED", self._prefixed(step.id), prompt,
            {"authored": True},
        )
        resumed = False
        try:
            resumed = self.auto_confirm or bool(self.confirm_fn and self.confirm_fn(step, prompt))
            if not resumed:
                raise HoldRequested(step.description or f"Procedure held at step {step.id}")
            self._acknowledge_operator_message(message_id, comment="Procedure resumed")
            self.store.log_event(
                run_id, "PROCEDURE_RESUMED", self._prefixed(step.id), prompt,
                {"authored": True},
            )
            return "passed"
        finally:
            self.runtime_control.end_authored_pause()
            self.tag_provider.connector.restart_observations()

    def _step_read_tag(self, run_id, step):
        old = self.runtime_variables[step.variable]
        try:
            result = super()._step_read_tag(run_id, step)
            self.runtime_variables[step.variable] = self._checked_memory(step.variable, self.runtime_variables[step.variable])
            self._save_memory({step.variable})
            self._publish_memory()
            return result
        except Exception:
            self.runtime_variables[step.variable] = old
            raise

    def _calculate_rows(self, run_id, step, rows):
        before = dict(self.runtime_variables)
        try:
            for row in rows:
                value = self._checked_memory(row.variable, self._eval_expression(row.expression, run_id=run_id))
                self.runtime_variables[row.variable] = value
                self.store.log_event(run_id, "CALCULATION", self._prefixed(step.id),
                                     f"Calculated {row.variable}",
                                     {"expression": row.expression, "variable": row.variable, "value": value})
        except Exception:
            self.runtime_variables.clear()
            self.runtime_variables.update(before)
            raise

    def _step_calculate(self, run_id, step):
        from .logic import CalculationRow
        rows = step.calculation_rows or [CalculationRow(variable=step.variable, expression=step.expression)]
        while True:
            self._checkpoint()
            before = dict(self.runtime_variables)
            epoch = self.runtime_control.observation_epoch
            try:
                with self.tag_provider.connector.frozen(self.tag_provider.connector.snapshot_values()), self.store.observation_batch():
                    self._calculate_rows(run_id, step, rows)
            except ObservationChanged:
                self.runtime_variables = before
                continue
            if epoch == self.runtime_control.observation_epoch:
                break
            self.runtime_variables = before
        self._save_memory({row.variable for row in rows})
        self._publish_memory()
        return "passed"

    def _sleep(self, seconds):
        # The upstream wall-clock chunk loop oversleeps when a single host
        # observation advances several simulated seconds. Keep one deadline.
        end = self._now() + max(0, seconds)
        epoch = self.runtime_control.observation_epoch
        while self._now() < end:
            self._checkpoint()
            if epoch != self.runtime_control.observation_epoch:
                return
            self.runtime_control._abort_event.wait(self.runtime_control.poll_interval)
        self._checkpoint()

    def _step_wait_until(self, run_id, step):
        if self._source_id(step) == "flow.user_event_wait":
            return self._wait_for_user_event(run_id, step)
        from .conditions import condition_clauses
        from .logic import ConditionRow, ContinuousConditions, row_logic
        start = self._now()
        match, clauses = condition_clauses(step.condition)
        rows = step.condition_rows or [ConditionRow(id=f"C{i + 1}", expression=clause) for i, clause in enumerate(clauses)]
        logic = row_logic(rows, step.condition_match, step.condition_logic) if step.condition_rows else row_logic(rows, match)
        tracker = ContinuousConditions(rows, logic, step.stable_for_sec)
        epoch = self.runtime_control.observation_epoch
        connector = self.tag_provider.connector
        sequence = connector._sequence - 1
        pending = []
        while True:
            self._checkpoint()
            if not pending:
                with self.runtime_control.lock:
                    pending, gap = connector.observations_since(sequence)
                if gap:
                    tracker.reset()
            if not pending:
                self._sleep(step.poll_sec)
                continue
            observations, pending = pending[:32], pending[32:]
            progress = None
            timed_out = False
            # Bound the transaction so audit publication cannot monopolize the
            # station. One durable commit covers every read in these frames.
            try:
                with self.store.observation_batch():
                    for sequence, now, observed_epoch, samples, observed_at in observations:
                        if self.runtime_control._abort_event.is_set():
                            raise RuntimeAbortRequested("Procedure cancelled")
                        if epoch != observed_epoch:
                            tracker.reset()
                            epoch = observed_epoch
                        if observed_epoch != self.runtime_control.observation_epoch:
                            tracker.reset()
                            continue
                        with connector.frozen(samples, epoch=observed_epoch, observed_at=observed_at):
                            self._load_memory(samples)
                            if step.calculation_rows:
                                self._calculate_rows(run_id, step, step.calculation_rows)
                            tracker.dwell = step.stable_for_sec
                            progress = tracker.observe(now, lambda clause: self._observed_condition(clause, run_id))
                        progress.update(elapsed=now - start, match=logic if step.condition_logic else
                                        step.condition_match if step.condition_rows else match, timeout=step.timeout_sec)
                        timed_out = now - start > step.timeout_sec or (now - start >= step.timeout_sec and not progress["complete"])
                        if timed_out:
                            break
            except ObservationChanged:
                tracker.reset()
                sequence = connector._sequence - 1
                pending = []
                continue
            if progress is None or epoch != self.runtime_control.observation_epoch:
                tracker.reset()
                pending = []
                continue
            if timed_out:
                raise TimeoutError(f"Timed out waiting for {step.description or step.condition}")
            self._save_memory({row.variable for row in step.calculation_rows})
            self._publish_memory()
            if self.wait_progress_fn:
                # The UI receives one update per bounded batch. Formatting
                # discarded intermediate rows can make observation processing
                # fall behind; every scan still earns its own evaluated audit.
                from .parameters import observation_detail
                for row in progress["conditions"]:
                    row["value"], row["quality"] = observation_detail(
                        row["condition"], samples, self.runtime_variables)
                    row["reason"] = {"Uncertain": "Waiting for good observations",
                                     "Waiting": "Expression is false", "Timing": "Continuous hold in progress",
                                     "Satisfied": "Row qualified"}[row["state"]]
                self.wait_progress_fn(step, progress)
            with self.runtime_control.lock:
                caught_up = (not pending and epoch == self.runtime_control.observation_epoch
                             and not self.runtime_control.is_paused and not self.runtime_control.host_paused)
                has_new_observations = connector._sequence > sequence
            # A queued later false observation invalidates an earlier true
            # snapshot. Finish the captured frontier across bounded batches;
            # extending it on every commit starves waits under continuous load.
            if caught_up and progress["complete"]:
                return "passed"
            if caught_up and not has_new_observations:
                self._sleep(step.poll_sec)



class ProcedureRun:
    def __init__(self, history_path: Path, *, memory=None, store=None):
        self.store = store if store is not None else ProcedureStore(history_path)
        self.memory = memory
        self.memory_links = []
        self.events = queue.Queue(maxsize=2048)
        self.thread = None
        self.control = SimulationControl()
        self.connector = ObservationConnector(self.control)
        self.run_id = ""
        self.result = None
        self._answers = {}
        self._pending_prompt = None
        self._answer_lock = threading.Lock()
        self._prompt_queue = deque()
        self.tuning_requests = queue.Queue(maxsize=64)
        self._engine = None

    @property
    def active(self):
        if self.thread is not None and self.thread.is_alive():
            return True
        engine = self._engine
        if engine is not None:
            with engine._loop_lock:
                return any(thread.is_alive() for thread in engine._parallel_threads)
        return False

    def observe(self, sim_time, samples, *, paused=False, fault=""):
        samples = dict(samples)
        if self.memory_links:
            values = self.memory.read_many(timeout=0.02)
            for spec in self.memory_links:
                samples["@memory/" + spec.name] = TagValue("@memory/" + spec.name,
                    spec.checked(values[spec.tag_path]), "Good", datetime.now(timezone.utc).isoformat(),
                    "Shared project memory", spec.tag_path)
        with self.control.lock:
            self.connector.update(samples)
            self.control.observe(sim_time, paused=paused)
            self.connector.record()
        if fault and self.active:
            self.control.stop(fault)

    def emit(self, kind, **payload):
        event = {"kind": kind, "run_id": self.run_id,
                 "sim_time": self.control.sim_time,
                 "procedure_elapsed": self.control.adjusted_monotonic(),
                 "wall_time": datetime.now(timezone.utc).isoformat(), **payload}
        if kind in {"step", "prompt", "finished", "paused", "resumed"} and not payload.get("audit_failed"):
            # Native step/read rows keep wall timestamps. Record the host clock
            # as well so accelerated training can be reconstructed accurately.
            if self.store.get_run(self.run_id) is not None:
                self.store.log_event(self.run_id, "TRAINER_" + kind.upper(),
                                     payload.get("step", "") if kind == "step" else "",
                                     kind, event)
        try:
            self.events.put_nowait(event)
        except queue.Full:
            self.control.stop("Procedure event backlog exceeded; station is not consuming updates")
            if kind != "finished":
                raise RuntimeError(self.control.reason) from None
            # Always deliver terminal status, including when an unavailable
            # consumer caused the failure. Detailed evidence stays in SQLite.
            try:
                self.events.get_nowait()
            except queue.Empty:
                pass
            self.events.put_nowait(event)

    def drain(self, limit=256):
        answer = []
        for _ in range(min(limit, self.events.qsize())):
            try:
                answer.append(self.events.get_nowait())
            except queue.Empty:
                break
        return answer

    def start(
        self,
        definition: ProcedureDefinition,
        *,
        actor,
        context=None,
        tuning=(),
        enable_live_outputs=False,
        enable_hmi_actions=False,
    ):
        if self.active:
            raise ValueError("A procedure is already running")
        definition.validate()
        self.memory_links = [v for v in definition.procedure.variables if v.tag_path]
        if self.memory_links:
            if self.memory is None:
                raise ValueError("A shared Tag DB is required by this procedure")
            definitions = {spec.path: spec for spec in self.memory.definitions()}
            for spec in self.memory_links:
                current = definitions.get(spec.tag_path)
                if current is None or any(getattr(current, key) != getattr(spec, key)
                                          for key in ("data_type", "min_value", "max_value")):
                    raise ValueError(f"Memory definition changed or missing: {spec.tag_path}; refresh its PA link")
        if self.control.sim_time is None or self.control.host_paused:
            raise ValueError("Resume the simulation and wait for completed controller scans")
        if time.monotonic() - self.control.updated > 10:
            raise ValueError("Wait for fresh completed controller scans before starting")
        with self.control.lock:
            # A new run has no pause to resume. Calling resume() retained the
            # previous run's evidence wait and blocked start on the Qt thread.
            self.control.reset()
            self.control._awaiting_resume_observation = False
            self.control._exclude_resume_delta = False
            self.control._pause_observed = False
            self.control.authored_pause = False
            self.control.reason = "Operator aborted the procedure"
            self.control.observation_epoch += 1
            self.control.elapsed = 0.0
            self.connector.restart_observations()
        provider = CapturedTagProvider(self.connector, stale_after_seconds=10)
        for tag in definition.bindings:
            provider.read_value(tag)
        with self._answer_lock:
            self._answers.clear()
            self._pending_prompt = None
            self._prompt_queue.clear()
        self.tuning_requests = queue.Queue(maxsize=64)
        for request in tuning:
            try:
                self.tuning_requests.put_nowait(dict(request, effective="next_run", requested_by=request.get("actor", actor), actor=actor))
            except queue.Full:
                raise ValueError("Too many pending parameter changes; maximum is 64") from None
        self.drain(limit=self.events.maxsize)
        self.run_id, self.result = uuid.uuid4().hex, None
        procedure = definition.procedure.model_copy(deep=True)
        if self.memory_links:
            values = self.memory.read_many()
            for spec in procedure.variables:
                if spec.tag_path:
                    spec.value = spec.checked(values[spec.tag_path])
            self.observe(self.control.sim_time, self.connector.snapshot_values())
        metadata = {"document": definition.document(), "document_sha256": definition.digest,
                    "clock": "simulation seconds", "context": dict(context or {})}

        def work():
            try:
                from .library import block_for_step

                def confirm(step, prompt):
                    timed = block_for_step(step).source_id in {
                        "field.confirmation", "message.confirmation"
                    }
                    return bool(self.ask(
                        step,
                        prompt,
                        "confirm",
                        timeout_sec=step.timeout_sec if timed else None,
                    ))

                engine = TrainerProcedureEngine(
                    provider, self.store, runtime_control=self.control,
                    sleep_fn=self.control.controlled_sleep, operator_identity=actor,
                    prepared_metadata=metadata,
                    confirm_fn=confirm,
                    comment_fn=lambda step, prompt: str(self.ask(step, prompt, "comment")),
                    input_fn=lambda step, prompt: self.ask(step, prompt, "input"),
                    step_event_fn=lambda step, status: self.emit("step", step=step.id, status=status),
                    wait_progress_fn=lambda step, progress: self.emit("progress", step=step.id, **progress),
                    message_event_fn=lambda message: self.emit("message", message=message),
                )
                self.emit("started", name=procedure.name, revision=procedure.metadata.version,
                          document_sha256=definition.digest)
                engine.memory_event_fn = lambda values: self.emit("memory", values=values)
                engine.operator_skip_fn = lambda step: self.ask(
                    step, f"Review {step.id}: execute this step or skip it with a reason?", "skip_gate"
                )
                engine.memory_store = self.memory
                engine.tuning_requests = self.tuning_requests
                engine._run_id = self.run_id
                engine.tuning_event_fn = lambda kind, change: self.emit(kind, change=change)
                engine.output_bindings = dict(definition.bindings)
                if enable_live_outputs:
                    engine.output_authorize_fn = lambda step, prompt, payload: self.ask(
                        step, prompt, "output", **payload
                    )
                    engine.output_apply_fn = lambda step, path, value: self.ask(
                        step,
                        f"Apply checked output {path} = {value!r}",
                        "output_apply",
                        path=path,
                        value=value,
                        logical_tag=step.tag,
                    )
                if enable_hmi_actions:
                    engine.hmi_request_fn = lambda step, target: self.ask(
                        step,
                        step.display_text or step.description or f"Open {target}",
                        "hmi_window",
                        target=target,
                    )
                    engine.hmi_alarm_fn = lambda step, alarm: self.emit(
                        "hmi_alarm", step=step.id, alarm=alarm
                    )
                self.store.set_flow_handler(
                    self.run_id, lambda event, payload: self.emit("flow", event=event, payload=payload)
                )
                self._engine = engine
                self.emit("memory", values=procedure.variable_map())
                self.result = engine.run(procedure, self.run_id)
                self.emit("finished", status=self.result.status.value, message=self.result.message,
                          proposals=list(provider.proposed_writes))
            except Exception as error:  # contain storage/initialization failures at the worker boundary
                self.emit("finished", status="FAILED", message=str(error),
                          proposals=list(provider.proposed_writes), audit_failed=True)
            finally:
                with self._answer_lock:
                    self._pending_prompt = None
                    self._answers.clear()
                self.memory_links = []
                current = self._engine
                if current is not None:
                    with current._loop_lock:
                        if not any(t.is_alive() for t in current._parallel_threads):
                            self._engine = None
                self.store.set_flow_handler(self.run_id, None)

        self.thread = threading.Thread(target=work, name="azeo-advisory-procedure", daemon=True)
        self.thread.start()
        return self.run_id

    def ask(self, step, prompt, kind, *, timeout_sec=None, **payload):
        identity = uuid.uuid4().hex
        started = self.control.adjusted_monotonic()
        with self._answer_lock:
            self._prompt_queue.append(identity)
        shown = False
        try:
            while True:
                self.control.checkpoint()
                if self._engine is not None:
                    self._engine.apply_pending_tuning()
                with self._answer_lock:
                    show = not shown and self._pending_prompt is None and self._prompt_queue[0] == identity
                    if show:
                        self._pending_prompt = identity
                if show:
                    shown = True
                    self.emit("prompt", prompt_id=identity, prompt=prompt, prompt_kind=kind,
                              step=step.model_dump(mode="json"),
                              response_timeout_sec=timeout_sec, **payload)
                with self._answer_lock:
                    if identity in self._answers:
                        return self._answers.pop(identity)
                if timeout_sec is not None \
                        and self.control.adjusted_monotonic() - started >= timeout_sec:
                    raise TimeoutError(f"Timed out waiting for operator response at {step.id}")
                self.control._abort_event.wait(0.02)
        finally:
            with self._answer_lock:
                if self._pending_prompt == identity:
                    self._pending_prompt = None
                if identity in self._prompt_queue:
                    self._prompt_queue.remove(identity)
            if shown:
                self.emit("prompt_closed", prompt_id=identity)

    def answer(self, prompt_id, value):
        with self._answer_lock:
            if (self._pending_prompt is None or prompt_id != self._pending_prompt
                    or prompt_id in self._answers or self.control.is_abort_requested):
                return False
            self._answers[prompt_id] = value
            return True

    def request_tuning(self, request):
        if not self.active or self.control.is_paused:
            raise ValueError("Live tuning requires a running, unpaused procedure")
        try:
            self.tuning_requests.put_nowait(dict(request, effective="live"))
        except queue.Full:
            raise ValueError("Too many pending parameter changes; wait for the current batch") from None

    def close(self, timeout=35):
        if self.active:
            self.control.stop("Procedure closed by the operator")
            # SQLite/anchor publication can legitimately exceed two seconds
            # on a busy Windows disk. Let its 30-second lock timeout resolve
            # before releasing the runner and losing the final audit record.
            self.thread.join(timeout=timeout)
        return not self.active
