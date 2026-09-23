"""One revision's station-owned run context; presentations do not own it."""
from __future__ import annotations

from copy import deepcopy
import logging
import math
import time
import weakref
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from azeo_control_trainer.core.procedures.hmi import reference, response_value
from azeo_control_trainer.core.procedures.model import load_definition
from azeo_control_trainer.core.procedures.runtime import ProcedureRun
from .procedure_observations import ProcedureObservations

log = logging.getLogger("operator.procedures")


class ProcedureSession(QObject):
    events = Signal(object)

    def __init__(self, station, *, store=None, memory=None, own_timer=True):
        super().__init__(station)
        self._station = weakref.ref(station)
        self.library = Path(station.simulation_service.project_dir) / "procedures"
        from azeo_control_trainer.core.datastore.memory_tags import MemoryTagStore
        self.run = ProcedureRun(self.library / "runtime" / "history.sqlite",
                                memory=memory if memory is not None else MemoryTagStore(self.library.parent),
                                store=store)
        station.live_source.memory = self.run.memory
        self.observations = ProcedureObservations(station)
        self.definition = None
        self.ref = ""
        self.observation = None
        self.context = None
        self.runtime_identity = None
        self.prompt = None
        self.last_finished = ""
        self.status = "Ready"
        self.instruction = ""
        self.progress = ""
        self.conditions = ()
        self.memory_values = {}
        self.parameter_values = {}
        self._pending_cache = {}
        self._history_cache = {}
        self._trend_cache = {}
        self._step_rows = {}
        self.step_states = {}
        self.flow_path = None
        self.flow_states = {}
        self.recent_events = []
        self._snapshots = {}
        self.catalog = {}
        self.digests = {}
        self.visible_refs = {}
        self.actor = ""
        self._procedure_alarms = {}
        self.catalog_observations = {}
        self.generation = 0
        self.closed = False
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.setProperty("keepRunningWhenHidden", True)
        self.timer.timeout.connect(self.poll)
        if own_timer:
            self.timer.start()

    @property
    def station(self):
        return self._station()

    def prepare(self, ref):
        ref = reference(ref)
        if ref not in self.catalog:
            path = (self.library / ref).resolve()
            if not path.is_relative_to(self.library.resolve()):
                raise ValueError("Procedure revision must remain inside the project library")
            self.catalog[ref] = load_definition(path, self.library)
            self.digests[ref] = self.catalog[ref].digest
        return self.catalog[ref]

    def select(self, definition, ref=""):
        if self.run.active:
            return
        if self.definition is definition and self.ref == ref:
            return
        self.definition = definition
        self.ref = ref
        self.prompt = None
        self.last_finished = ""
        self.step_states = {}
        self.status = "Ready"
        self.memory_values = {}
        self.parameter_values = {}
        self.instruction = ""
        self.progress = ""
        self.conditions = ()
        self.generation += 1

    def poll(self):
        self._snapshots.clear()
        if self.closed:
            return
        try:
            for ref, definition in tuple(self.catalog.items()):
                if ref == self.ref or time.monotonic() - self.visible_refs.get(ref, 0) < 2:
                    self.catalog_observations[ref] = self.observations.capture(definition.bindings)
            if self.definition is not None:
                observation = self.catalog_observations.get(self.ref)
                if observation is None:
                    observation = self.observations.capture(self.definition.bindings)
                self.observation = observation
                fault = observation.fault
                if self.run.active and (self.context != observation.context or
                                        self.runtime_identity != observation.runtime_identity):
                    fault = "Training session or controller configuration changed; start a new procedure"
                if self.run.active and self.actor != self.station.settings.user:
                    fault = "Operator identity changed; start a new procedure"
                self.run.observe(observation.sim_time, observation.samples,
                                 paused=observation.paused, fault=fault)
        except Exception as error:
            log.exception("Procedure observation failed")
            self.observation = None
            self.catalog_observations.clear()
            self.run.control.stop(f"Station observation failed: {error}")
            self.status = f"Unavailable: {error}"
        try:
            self.consume_events()
        except Exception:
            log.exception("Procedure event reduction failed")
            self.run.control.stop("Station could not record procedure events")
            self.status = "Unavailable: station could not record procedure events"

    def consume_events(self):
        batch = self.run.drain()
        for event in batch:
            kind = event["kind"]
            if kind == "started":
                self.status = "Running"
                self.flow_path = None
                self.flow_states = {}
            elif kind == "step":
                self.step_states[event["step"]] = event["status"]
                if event["status"] == "ACTIVE":
                    self.progress = ""
                    self.conditions = ()
                    step = next((s for s in self.definition.procedure.steps if s.id == event["step"]), None)
                    if step is None:
                        node = next((n for n in self.definition.procedure.flow.nodes if n.id == event["step"]), None) if self.definition.procedure.flow else None
                        self.instruction = node.label or node.id if node else event["step"]
                    else:
                        self.instruction = step.description or step.type
                        if getattr(step, "condition", "") and step.type != "wait_until":
                            self.instruction += f" Verify: {step.condition}"
                        if step.type == "write_tag":
                            self.instruction += f" Governed output: {step.tag} = {step.value}. Authorization and actual feedback are required."
            elif kind == "prompt":
                if event["prompt_kind"] == "output_apply":
                    if self.run.control.is_abort_requested:
                        from azeo_control_trainer.core.hmi.binding import WriteResult
                        result = WriteResult(False, "Procedure observations are no longer valid")
                    else:
                        result = self.station._write(event["path"], event["value"])
                    self.run.answer(event["prompt_id"], result)
                else:
                    self.prompt = deepcopy(event)
                    self.instruction = event["prompt"]
            elif kind == "prompt_closed":
                if self.prompt and self.prompt["prompt_id"] == event["prompt_id"]:
                    self.prompt = None
            elif kind == "flow":
                payload = event["payload"]
                if event["event"] == "FLOW_EDGE":
                    self.flow_path = (payload["source"], payload["target"])
                    self.flow_states[payload["source"]] = "PASSED"
                    self.flow_states[payload["target"]] = "ACTIVE"
                elif event["event"] == "FLOW_NODE_ACTIVE":
                    self.flow_states[payload["node_id"]] = "ACTIVE"
                elif event["event"] in {"PARALLEL_JOIN", "FLOW_COMPLETE"} and payload.get("node_id"):
                    self.flow_states[payload["node_id"]] = "PASSED"
            elif kind == "progress":
                self.conditions = tuple(dict(row, tooltip=f"{row['condition']}\n{row.get('value', '')}\n{row.get('quality', '')} · {row['state']} · {row['timer']}\n{row.get('reason', '')}")
                                        for row in event.get("conditions", ()))
                self.progress = (f"{event.get('match', 'ALL')} conditions; stable "
                                 f"{event['stable']:.1f} / {event['dwell']:g} s; "
                                 f"timeout {event['elapsed']:.1f} / {event.get('timeout', 0):g} s")
            elif kind == "memory":
                self.memory_values.update(event["values"])
            elif kind == "message" and event["message"].get("audible"):
                from PySide6.QtWidgets import QApplication
                QApplication.beep()
            elif kind == "hmi_alarm":
                alarm = event["alarm"]
                severity = {"info": 1, "warning": 2, "alarm": 3, "critical": 4}.get(
                    str(alarm.get("severity", "alarm")), 3
                )
                self._procedure_alarms[alarm["step_id"]] = (
                    f"{alarm['message']} [{alarm['step_id']}]", severity, None
                )
                self.station.alarm_state.observe(
                    "PROCEDURE", event["run_id"], self._procedure_alarms.values()
                )
            elif kind == "parameter_applied":
                change = event["change"]
                self.parameter_values[change["parameter"]] = change["value"]
                self._pending_cache.clear()
                self.conditions = ()
                self.progress = "Parameter applied; condition qualification restarts on fresh observations"
            elif kind == "finished":
                self.last_finished = event["status"]
                self.status = event["status"].title()
                self.instruction = event["message"]
                self.prompt = None
                terminal = "PASSED" if event["status"] == "COMPLETE" else event["status"]
                self.flow_states = {key: terminal if value == "ACTIVE" else value for key, value in self.flow_states.items()}
                self.step_states = {key: terminal if value == "ACTIVE" else value for key, value in self.step_states.items()}
                self.station.alarm_state.observe("PROCEDURE", event["run_id"], ())
                self._procedure_alarms.clear()
            if kind not in {"progress", "message", "memory"}:
                self._history_cache.clear()
                self.generation += 1
                detail = (f"{event['change']['parameter']}: {event['change'].get('previous', '—')} → {event['change']['value']} ({event['change']['effective']})" if kind.startswith("parameter_") else
                          "Response required" if kind == "prompt" else
                          str(event.get("status", event.get("name", event.get("step", "")))))
                self.recent_events.append({"time": f"{event['sim_time']:.1f}",
                                           "event": kind.replace("_", " ").title(), "detail": detail})
                self.recent_events = self.recent_events[-100:]
                self.record(event)
        if batch:
            self._snapshots.clear()
            self.events.emit(batch)

    def record(self, event):
        training = self.station.training_session
        action = "procedure_" + event["kind"]
        if training and training.active and self.context and training.identity == self.context["training_session"]:
            training.event(action, event["run_id"], event)
        else:
            self.station._history_workbench_event(action, event["run_id"], event, event["sim_time"])

    def start(self):
        self.poll()
        if not self.station.settings.write_authority:
            raise ValueError("View-only station cannot operate procedures")
        if not self.definition or not self.observation or not self.observation.ready:
            raise ValueError(self.observation.detail if self.observation else "Waiting for live controller observations")
        self.context = self.observation.context
        self.actor = self.station.settings.user
        self.runtime_identity = self.observation.runtime_identity
        self.last_finished = ""
        self.prompt = None
        self.step_states = {}
        self.recent_events = []
        self.progress = ""
        self.parameter_values = {}
        # The quick loop check can run before it has a saved library revision;
        # only saved revisions can own retained next-run parameter requests.
        pending = self.pending(self.ref) if self.ref else {}
        tuning = [dict(row, ref=self.ref, queued_event=row["event_id"]) for row in pending.values()]
        self.run.start(
            self.definition,
            actor=self.station.settings.user,
            context=self.context,
            tuning=tuning,
            enable_live_outputs=True,
            enable_hmi_actions=True,
        )
        self.generation += 1
        self.poll()

    def open_hmi_target(self, target, ref=""):
        """Open only a display or equipment reference owned by the procedure."""
        definition = self.prepare(ref) if ref else self.definition
        if definition is None:
            raise ValueError("No procedure is selected")
        target = str(target or "").strip()
        equipment = {"/".join(path.split("/")[:2]) for path in definition.bindings.values()}
        if target in equipment:
            from .procedure_actions import open_equipment
            return open_equipment(self.station, self, target)
        display = target.removeprefix("displays/").strip("/")
        if not display or not self.station.show_display(display):
            raise ValueError(f"No published procedure display or mapped equipment: {target}")
        return True

    def token(self):
        return (self.run.run_id, self.prompt["prompt_id"] if self.prompt else "", self.generation)

    def pending(self, ref):
        from azeo_control_trainer.core.procedures.parameters import pending_parameters
        definition = self.prepare(ref)
        if ref not in self._pending_cache:
            self._pending_cache[ref] = pending_parameters(self.run.store, ref, definition.digest)
        return self._pending_cache[ref]

    def tune(self, ref, expected, parameter, value, effective):
        from azeo_control_trainer.core.procedures.parameters import parameters
        self.poll()
        if tuple(expected) != self.token():
            raise ValueError("Procedure changed; review the parameter again")
        if not self.station.settings.write_authority:
            raise ValueError("View-only station cannot tune procedures")
        definition = self.prepare(ref)
        spec = parameters(definition.procedure).get(parameter)
        if spec is None:
            raise ValueError("This parameter is not exposed for operator tuning")
        queued = self.pending(ref).get(parameter) if effective == "cancel" else None
        if effective == "cancel" and not queued:
            raise ValueError("No queued change for this parameter")
        # Cancellation identifies the signed queued value, not an unfinished
        # proposal still being edited in the dialog.
        value = queued["value"] if queued else spec.checked(value)
        request = dict(ref=ref, definition=definition.digest, parameter=parameter, value=value,
                       actor=self.station.settings.user, effective=effective)
        if effective == "live":
            if spec.access != "live" or ref != self.ref or not self.observation or not self.observation.ready:
                raise ValueError("Live tuning requires an exposed live parameter and fresh observations for this run")
            self.run.request_tuning(request)
        elif effective == "next_run":
            self.run.store.log_event("tuning", "PA_TUNING_QUEUED", ref, "Apply once at the next run", request)
        elif effective == "cancel":
            request["queued_event"] = queued["event_id"]
            self.run.store.log_event("tuning", "PA_TUNING_CANCELLED", ref, "Queued change cancelled", request)
        else:
            raise ValueError("Choose Live or Next run")
        self._pending_cache.clear()
        self._snapshots.clear()
        self.generation += 1
        self.station._history_workbench_event("procedure_parameter_" + effective, ref, request,
                                              self.run.control.sim_time)

    def memory_result(self, ref, name):
        from azeo_control_trainer.core.hmi.binding.result import BindingResult, UNRESOLVED
        from azeo_control_trainer.core.strategy.model.terminal import Quality
        definition = self.prepare(ref)
        spec = next((v for v in definition.procedure.variables if v.name == name), None)
        if spec is None:
            return UNRESOLVED
        if spec.tag_path:
            return self.station.live_source.base.read(spec.tag_path)
        # A local value outside its run is not current process evidence.
        if ref != self.ref or not self.run.active or name not in self.memory_values:
            return UNRESOLVED
        return BindingResult(value=self.memory_values[name], quality=Quality.GOOD)

    def trend_snapshot(self, ref):
        from .procedure_monitor import trend_snapshot
        self.visible_refs[ref] = time.monotonic()
        cached = self._trend_cache.get(ref)
        if cached is None or time.monotonic() - cached[0] >= 1:
            cached = (time.monotonic(), trend_snapshot(self, ref))
            self._trend_cache[ref] = cached
        return cached[1]

    def execute(self, command, expected, value=None, ref=None):
        # A second view must never answer a later prompt using an earlier click.
        if tuple(expected) != self.token():
            raise ValueError("Procedure changed; review its current state before operating")
        if not self.station.settings.write_authority:
            raise ValueError("View-only station cannot operate procedures")
        if command == "start":
            if self.run.active:
                raise ValueError("This procedure is already running")
            if ref is not None:
                self.select(self.prepare(ref), ref)
            self.start()
            return
        if ref is not None and ref != self.ref:
            raise ValueError("This display does not reference the active procedure")
        if not self.run.active:
            raise ValueError("No active procedure; Held is a terminal result")
        self.poll()
        if tuple(expected) != self.token():
            raise ValueError("Procedure changed; review its current state before operating")
        if command == "abort":
            self.run.control.stop("Operator aborted the procedure")
        elif command in {"pause", "break"}:
            if self.run.control.is_paused:
                raise ValueError("Procedure is already paused")
            if command == "break":
                reason = str(value or "").strip()
                if not reason or len(reason) > 500:
                    raise ValueError("Break requires a reason of at most 500 characters")
                self.run.store.log_event(self.run.run_id, "OPERATOR_BREAK", "", reason,
                                         {"actor": self.station.settings.user, "reason": reason})
            self.run.control.pause()
            self.run.emit("paused")
        elif command in {"resume", "respond"}:
            if not self.observation or not self.observation.ready:
                raise ValueError("Wait for fresh controller scans and resume the simulation")
            if command == "resume":
                if not self.run.control.is_paused:
                    raise ValueError("Procedure is not paused")
                self.run.control.resume()
                self.run.emit("resumed")
            else:
                if self.run.control.is_paused or self.prompt is None:
                    raise ValueError("No response is available while paused or between prompts")
                answer = response_value(self.prompt, value)
                self.run.answer(self.prompt["prompt_id"], answer)
                self.prompt = None
        else:
            raise ValueError("Unsupported procedure command")
        self.generation += 1
        self.consume_events()

    def snapshot(self, ref):
        self.visible_refs[ref] = time.monotonic()
        cache_key = (ref, self.generation, self.station.settings.write_authority, self.run.active)
        if cache_key in self._snapshots:
            return self._snapshots[cache_key]
        definition = self.prepare(ref)
        selected = ref == self.ref
        observation = self.catalog_observations.get(ref)
        active = selected and self.run.active
        ready = bool(observation and observation.ready)
        permitted = bool(self.station.settings.write_authority)
        paused = active and self.run.control.is_paused
        states = self.step_states if selected else {}
        steps = definition.procedure.steps
        state = ("Paused" if paused else "Waiting for operator" if active and self.prompt else
                 self.status if selected else "Ready")
        condition = observation.detail if observation else "Waiting for completed controller scans"
        if not ready and not (selected and self.last_finished):
            state = "Unavailable" if not observation or observation.fault else "Simulation paused"
        prompt = self.prompt if selected else None
        row_key = (id(steps), self.generation, selected)
        cached_rows = self._step_rows.get(ref)
        if cached_rows is None or cached_rows[0] != row_key:
            flow = definition.procedure.flow
            paths = {}
            if flow:
                outgoing = {}
                for edge in flow.edges:
                    outgoing.setdefault(edge.source, []).append(f"{edge.label or edge.outcome}: {edge.target}")
                for node in flow.nodes:
                    if node.step_id:
                        paths[node.step_id] = " · ".join(outgoing.get(node.id, ()))
            rows = tuple({"step": step.id, "type": step.type, "instruction": step.description or step.type,
                      "graph": bool(flow), "next_paths": paths.get(step.id, ""), "scope": step.unit_procedure,
                      "heading": f"{index + 1:02d}  {step.id}",
                      "equipment": "/".join((definition.bindings.get(getattr(step, "equipment", "")) or
                                                definition.bindings.get(getattr(step, "tag", "")) or "").split("/")[:2]),
                          "state": states.get(step.id, "PENDING")} for index, step in enumerate(steps))
            self._step_rows[ref] = (row_key, rows)
        else:
            rows = cached_rows[1]
        values = []
        shared_values = self.run.memory.read_many() if any(v.tag_path for v in definition.procedure.variables) else {}
        for memory in definition.procedure.variables:
            value = (shared_values.get(memory.tag_path) if memory.tag_path else
                     self.memory_values.get(memory.name, memory.value) if selected else memory.value)
            values.append({"tag": memory.name, "path": memory.tag_path or "Local variable / " + memory.name,
                           "value": str(value), "quality": ("Good" if memory.tag_path in shared_values else "Missing") if memory.tag_path else "Local"})
        for tag, sample in (observation.samples.items() if observation else ()):
            quality = sample.quality
            if sample.value is None:
                quality = "Missing"
            elif isinstance(sample.value, float) and not math.isfinite(sample.value):
                quality = "Invalid"
            values.append({"tag": tag, "path": definition.bindings[tag],
                           "value": (f"{sample.value:.6g}" if type(sample.value) is float else str(sample.value)) if quality == "Good" else "—",
                           "tooltip": f"{definition.bindings[tag]}\n{sample.value} · {quality}", "quality": quality})
        result = {
            "TITLE": definition.procedure.name, "STATE": state, "SOURCE": condition,
            "INSTRUCTION": self.instruction if selected and self.instruction else "Review the procedure and current operating condition. Start when ready; use Equipment to inspect its faceplate.",
            "PROGRESS": self.progress if selected else "", "RUN_ID": self.run.run_id if selected else "",
            "RESULT": self.last_finished if selected else "", "TOKEN": self.token(),
            "REVISION": self.digests.get(ref, ""),
            "CONDITIONS": self.conditions if selected else (),
            "PROMPT": deepcopy(prompt), "STEPS": rows, "VALUES": tuple(values),
            "EVENTS": tuple(reversed(self.recent_events)) if selected else (),
            "COMPLETION": 100 if selected and self.last_finished == "COMPLETE" else
                          100 * sum(states.get(s.id) in {"PASSED", "SKIPPED"} for s in steps) / max(1, len(steps)),
            "CAN_START": permitted and ready and not self.run.active,
            "CAN_PAUSE": permitted and active and not paused,
            "CAN_BREAK": permitted and active and not paused,
            "CAN_RESUME": permitted and active and paused and ready,
            "CAN_ABORT": permitted and active,
            "CAN_RESPOND": permitted and active and ready and not paused and prompt is not None,
            "REASON": "View-only station" if not permitted else condition,
        }
        current = next((step for step in steps if states.get(step.id) == "ACTIVE"), None)
        equipment = (definition.bindings.get(getattr(current, "equipment", ""))
                     or definition.bindings.get(getattr(current, "tag", ""))
                     or next(iter(definition.bindings.values()), ""))
        equipment = "/".join(equipment.split("/")[:2])
        from .procedure_actions import equipment_class
        result["CAN_EQUIPMENT"] = bool(equipment and equipment_class(self.station, equipment))
        from .procedure_monitor import parameter_rows, history_rows
        result["PARAMETERS"] = parameter_rows(self, ref, shared_values)
        result["HISTORY"] = history_rows(self, ref)
        result["CAN_TUNE"] = permitted and bool(result["PARAMETERS"])
        result["CAN_TRENDS"] = any(value.data_type in {"float", "int", "bool"}
                                    for value in [*definition.procedure.tags, *definition.procedure.variables])
        result["CAN_HISTORY"] = result["CAN_HELP"] = True
        result["CAN_WORKFLOW"] = True
        result["CONTEXT"] = {"ref": ref, "token": result["TOKEN"], "prompt": result["PROMPT"], "equipment": equipment}
        reasons = {
            "START": "This procedure is already running" if self.run.active else condition,
            "PAUSE": "Already paused" if paused else "No active run for this procedure",
            "BREAK": "Already paused" if paused else "No active run for this procedure",
            "RESUME": condition if paused else "This procedure is not paused",
            "ABORT": "No active run for this procedure",
            "RESPOND": "Resume the procedure first" if paused else condition if not ready else "No operator response is pending",
            "EQUIPMENT": "No equipment reference is mapped",
            "TUNE": "No parameters are exposed; configure operator tuning in PA Designer",
            "TRENDS": "No numeric observations or memory tags are configured",
            "HISTORY": "Open signed run history and reports", "HELP": "Open procedure help",
            "WORKFLOW": "Open live workflow and locate the active call or operator response",
        }
        for command, reason in reasons.items():
            result["REASON_" + command] = (command.title() if result["CAN_" + command] else
                                          "View-only station" if not permitted else reason)
        self._snapshots[cache_key] = result
        return result

    def close(self):
        if self.closed:
            return True
        if not self.run.close(timeout=0.05):
            self.status = "Closing procedure; waiting for its final audit record"
            return False
        self.consume_events()
        if self.run.events.qsize():
            return False
        self.timer.stop()
        self.closed = True
        return True
