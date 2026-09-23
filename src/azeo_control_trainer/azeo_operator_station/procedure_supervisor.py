"""Station-owned run contexts with one audit store and independent operator state."""
from __future__ import annotations

from pathlib import Path
import time
import weakref

from PySide6.QtCore import QObject, QTimer, Signal

from azeo_control_trainer.core.datastore.memory_tags import MemoryTagStore
from azeo_control_trainer.core.procedures.audit import ProcedureStore
from azeo_control_trainer.core.procedures.hmi import reference

from .procedure_session import ProcedureSession


def reserved_targets(definition) -> frozenset[str]:
    """Reserve all possible authored writes, including flattened child calls."""
    if definition is None:
        return frozenset()
    tags = {tag.tag for tag in definition.procedure.tags if tag.access == "read_write"}
    for step in definition.procedure.steps:
        if step.type in {"write_tag", "ramp_tag"} or step.integration_operation == "write":
            if step.tag:
                tags.add(step.tag)
    paths = {definition.bindings[tag] for tag in tags if tag in definition.bindings}
    paths.update(value.tag_path for value in definition.procedure.variables if value.tag_path)
    return frozenset(paths)


class ProcedureSupervisor(QObject):
    events = Signal(object)
    max_active = 8

    def __init__(self, station):
        super().__init__(station)
        self._station = weakref.ref(station)
        self.library = Path(station.simulation_service.project_dir) / "procedures"
        self.store = ProcedureStore(self.library / "runtime" / "history.sqlite")
        self.memory = MemoryTagStore(self.library.parent)
        self._contexts: dict[str, ProcedureSession] = {}
        self._selected = ""
        self._closed = False
        self._new_context("")
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.setProperty("keepRunningWhenHidden", True)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    @property
    def station(self):
        return self._station()

    def _new_context(self, ref):
        context = ProcedureSession(
            self.station, store=self.store, memory=self.memory, own_timer=False
        )
        context.events.connect(self.events.emit)
        self._contexts[ref] = context
        return context

    def context_for(self, ref):
        ref = reference(ref) if ref else ""
        context = self._contexts.get(ref)
        if context is None:
            context = self._new_context(ref)
        if ref and context.definition is None:
            context.select(context.prepare(ref), ref)
            # Establish the scan-counter baseline on selection; the next
            # completed scan, not opening the view, proves live readiness.
            context.poll()
        return context

    @property
    def selected(self):
        return self._contexts[self._selected]

    @property
    def run(self):
        return self.selected.run

    @property
    def active_runs(self):
        return tuple((ref, context) for ref, context in self._contexts.items() if context.run.active)

    def __getattr__(self, name):
        contexts = self.__dict__.get("_contexts")
        if contexts is None:
            raise AttributeError(name)
        return getattr(self.selected, name)

    def focus(self, ref):
        self.context_for(ref)
        self._selected = ref or ""
        return self.selected

    def select(self, definition, ref=""):
        ref = reference(ref) if ref else ""
        context = self._contexts.get(ref) or self._new_context(ref)
        self._selected = ref
        if ref and not context.run.active:
            context.catalog[ref] = definition
            context.digests[ref] = definition.digest
        context.select(definition, ref)
        context.poll()

    def prepare(self, ref):
        return self.context_for(ref).prepare(ref)

    def _start_error(self, ref):
        context = self.context_for(ref)
        if context.run.active:
            return "This procedure is already running"
        active = self.active_runs
        if len(active) >= self.max_active:
            return f"At most {self.max_active} supervised procedures may run together"
        if not active:
            return ""
        wanted = reserved_targets(context.definition)
        for other_ref, other in active:
            overlap = wanted & reserved_targets(other.definition)
            if overlap:
                return (f"Output or shared-memory target is reserved by {other_ref or other.definition.procedure.name}: "
                        + ", ".join(sorted(overlap)[:3]))
        return ""

    def can_start(self, ref=""):
        return not self._start_error(ref)

    def start(self):
        reason = self._start_error(self._selected)
        if reason:
            raise ValueError(reason)
        return self.selected.start()

    def execute(self, command, expected, value=None, ref=None):
        context = self.context_for(ref) if ref is not None else self.selected
        if command == "start":
            reason = self._start_error(ref if ref is not None else self._selected)
            if reason:
                raise ValueError(reason)
        return context.execute(command, expected, value, ref)

    def snapshot(self, ref):
        context = self.context_for(ref)
        result = context.snapshot(ref)
        reason = self._start_error(ref)
        if reason and result["CAN_START"]:
            result = dict(result)
            result["CAN_START"] = False
            result["REASON_START"] = reason
        return result

    def memory_result(self, ref, name):
        return self.context_for(ref).memory_result(ref, name)

    def trend_snapshot(self, ref):
        return self.context_for(ref).trend_snapshot(ref)

    def tune(self, ref, expected, parameter, value, effective):
        return self.context_for(ref).tune(ref, expected, parameter, value, effective)

    def pending(self, ref):
        return self.context_for(ref).pending(ref)

    def open_hmi_target(self, target, ref=""):
        return self.context_for(ref).open_hmi_target(target, ref)

    def poll(self):
        if self._closed:
            return
        now = time.monotonic()
        for ref, context in tuple(self._contexts.items()):
            if (ref == self._selected or context.run.active or context.run.events.qsize() or
                    now - context.visible_refs.get(ref, 0) < 2):
                context.poll()

    def close(self):
        if self._closed:
            return True
        complete = True
        for context in tuple(self._contexts.values()):
            complete = context.close() and complete
        if complete:
            self.timer.stop()
            self._closed = True
        return complete
