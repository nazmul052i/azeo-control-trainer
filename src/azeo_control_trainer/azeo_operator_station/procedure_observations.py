"""Capture controller evidence on the station thread before handing it to a worker."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import time
import weakref

from azeo_control_trainer.core.pa_designer.connectors.tag_value import TagValue

from .freshness import ScanFreshness


@dataclass
class Observation:
    sim_time: float
    samples: dict
    paused: bool
    ready: bool
    detail: str
    fault: str
    context: dict
    runtime_identity: tuple


class ProcedureObservations:
    def __init__(self, station):
        self._station = weakref.ref(station)
        self.freshness = ScanFreshness()
        self._was_paused = False
        self._resume_at = None

    def wait_for_scan(self):
        self._resume_at = time.time()

    @property
    def station(self):
        return self._station()

    def capture(self, bindings):
        station = self.station
        service = station.simulation_service
        caps = service.process_capabilities()
        sim_time = float(caps.get("sim_time", float("nan")))
        paused = bool(caps.get("paused"))
        wanted = {path.partition("/")[0] for path in bindings.values() if not path.startswith("MEMORY/")}
        runtimes = [runtime for runtime in service._runtimes()
                    if getattr(getattr(runtime, "compiled", None), "graph", None) is not None]
        if wanted:
            runtimes = [runtime for runtime in runtimes if runtime.compiled.graph.name in wanted]
        present = {runtime.compiled.graph.name for runtime in runtimes if runtime.is_online}
        now = time.time()
        state = self.freshness.sample(runtimes, now, paused=paused)
        paused = paused or state.state == "PAUSED"
        if self._was_paused and not paused:
            self._resume_at = now
        self._was_paused = paused
        if self._resume_at is not None and state.last_update is not None and state.last_update >= self._resume_at:
            self._resume_at = None
        awaiting_scan = self._resume_at is not None and now - self._resume_at < 10
        fault = ""
        if not caps.get("available") or not math.isfinite(sim_time):
            fault = "No simulation clock is available"
        elif wanted - present:
            fault = "Controller modules offline: " + ", ".join(sorted(wanted - present))
        elif state.state in {"DISCONNECTED", "STALE", "PARTLY PAUSED"} and not (
                state.state == "STALE" and awaiting_scan):
            fault = state.detail
        elif not paused and not caps.get("running"):
            fault = "Simulation execution has stopped"
        stamp = (datetime.fromtimestamp(state.last_update, timezone.utc).isoformat()
                 if state.last_update is not None else "")
        if not stamp and not fault:
            fault = state.detail
        samples = {}
        uncertain = ""
        with station.live_source.snapshot():
            for logical, path in bindings.items():
                row = station.live_source.read(path)
                quality = row.quality.name.title()
                samples[logical] = TagValue(logical, deepcopy(row.value), quality, stamp,
                                            "Trainer completed controller scan", path)
                if quality == "Uncertain":
                    uncertain = f"{path}: Uncertain feedback; continuous dwell must restart"
                if quality not in {"Good", "Uncertain"} or row.value is None:
                    fault = f"{path}: {quality} quality or missing value"
                elif isinstance(row.value, float) and not math.isfinite(row.value):
                    fault = f"{path}: non-finite value"
        session = station.training_session
        context = {
            "project": str(service.project_dir.resolve()),
            "training_session": session.identity if session and session.active else "",
            "configuration": {runtime.compiled.graph.name:
                deepcopy(getattr(runtime.compiled.graph, "_configuration_identity", {}))
                for runtime in runtimes},
        }
        identities = tuple(sorted((runtime.compiled.graph.name, id(runtime), id(runtime.compiled))
                                  for runtime in runtimes))
        ready = not fault and not uncertain and state.state == "LIVE" and not paused and not awaiting_scan
        detail = "Waiting for a completed scan after resume" if awaiting_scan else uncertain or state.detail
        return Observation(sim_time, samples, paused or awaiting_scan, ready, fault or detail,
                           fault, context, identities)
