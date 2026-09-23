"""Simulation engine.

A dedicated thread runs the flowsheet on a fixed step, paced against a monotonic
clock. Fixed step matters for training: a variable step integrator makes the same
loop behave differently between runs, so tuning learned in the simulator does not
transfer to the plant.

Failure policy
--------------
A model that raises is caught, logged with full context, and counted. Below the
error budget the engine skips that unit for the step and carries on, because one
misbehaving unit should not stop a lesson. Above the budget the engine freezes
and marks every ``AI``/``DI`` tag bad, which is what a real DCS would see if the
field devices stopped answering. It never dies quietly.
"""

from __future__ import annotations

import json
import math
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .tags import Quality, TagDatabase, TagKind
from . import units as _units

log = logging.getLogger(__name__)


@dataclass
class EngineStats:
    running: bool = False
    heartbeat: int = 0
    sim_time: float = 0.0
    scan_time_ms: float = 0.0
    scan_time_peak_ms: float = 0.0
    speed_factor: float = 1.0
    overruns: int = 0
    steps_per_wake: int = 1
    out_of_range: int = 0
    errors: int = 0
    last_error: str = ""
    frozen_by_error: bool = False
    # set by the watchdog (azeoplant/resilience.py): the loop stopped
    # advancing while it should have been running; and how many times
    # a dead loop thread was started again
    stalled: bool = False
    restarts: int = 0
    unit_ms: Dict[str, float] = field(default_factory=dict)


class SimulationEngine:
    """Owns the flowsheet and steps it on a fixed cycle."""

    # Shortest cycle worth sleeping for. The sleep must stay long enough that
    # it can be shortened to absorb the overshoot of the previous one.
    MIN_SLEEP = 0.050

    ERROR_BUDGET = 25

    def __init__(self, db: TagDatabase, flowsheet, dt: float = 0.1) -> None:
        self.post_step_hooks = []
        # An attached control system (the closeloop branch's software
        # DCS). The engine only knows it can capture and apply state, so
        # snapshots carry every controller's mode, SP and reset exactly
        # as they carry the plant - including the rollback taken before
        # a load, so a failed restore puts the loops back too.
        self.controller = None
        self.db = db
        self.flowsheet = flowsheet
        self.dt = float(dt)
        self.stats = EngineStats(speed_factor=1.0)

        fs_dt = getattr(flowsheet, "dt", None)
        if fs_dt is not None and abs(float(fs_dt) - self.dt) > 1e-9:
            log.warning("Engine dt %.3f s differs from flowsheet dt %.3f s; "
                        "dead-time elements are sized for the flowsheet dt",
                        self.dt, float(fs_dt))

        # True when the command line chose the unit set: a snapshot's record
        # then no longer switches it (run.py --units)
        self.units_pinned = False
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._run = threading.Event()
        self._speed = 1.0
        self._consecutive_errors = 0
        self._step_once = 0

    # ------------------------------------------------------------------ control
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="sim-engine",
                                        daemon=True)
        self._thread.start()
        log.info("Simulation engine started, dt=%.3f s", self.dt)

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        self._run.set()                       # release a frozen loop so it can exit
        if self._thread:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                log.warning("Simulation engine did not stop within %.1f s", timeout)
        log.info("Simulation engine stopped")

    def resume(self) -> None:
        self._consecutive_errors = 0
        self.stats.frozen_by_error = False
        self._run.set()
        log.info("Simulation running")

    def freeze(self) -> None:
        self._run.clear()
        self.stats.running = False
        log.info("Simulation frozen")

    def step_once(self) -> None:
        """Advance exactly one step while frozen. Useful when debugging a model."""
        self._step_once += 1

    @property
    def speed_factor(self) -> float:
        return self._speed

    @speed_factor.setter
    def speed_factor(self, value: float) -> None:
        self._speed = min(max(float(value), 0.1), 20.0)
        self.stats.speed_factor = self._speed
        log.info("Speed factor set to %.2fx", self._speed)

    # --------------------------------------------------------------- main loop
    def _loop(self) -> None:
        next_wake = time.monotonic()
        while not self._stop.is_set():
            if not self._run.is_set() and self._step_once == 0:
                self.stats.running = False
                time.sleep(0.05)
                next_wake = time.monotonic()
                continue

            single = self._step_once > 0
            if single:
                self._step_once -= 1
            self.stats.running = self._run.is_set()

            # Below the timer resolution a sleep overshoots by more than the
            # period it was asked for: on this platform sleep(1 ms) really takes
            # about 4 ms. Waking once per step would then cap the achieved speed
            # far below the requested one, silently. Batch the steps instead, so
            # the loop always sleeps long enough for the sleep to mean something.
            period = self.dt / self._speed
            batch = 1 if single else max(1, math.ceil(self.MIN_SLEEP / period))
            self.stats.steps_per_wake = batch

            t0 = time.perf_counter()
            for _ in range(batch):
                self._execute_step()
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            self.stats.scan_time_ms = elapsed_ms / batch
            self.stats.scan_time_peak_ms = max(self.stats.scan_time_peak_ms,
                                               self.stats.scan_time_ms)

            if single:
                next_wake = time.monotonic()
                continue

            next_wake += period * batch
            slack = next_wake - time.monotonic()
            if slack > 0:
                time.sleep(slack)
            elif slack > -max(0.5, period * batch * 5.0):
                # A sleep overshoots its request by a near constant margin, so
                # the schedule runs a little late every cycle. ``next_wake``
                # already carries the ideal timeline, so simply not resetting it
                # makes the next sleep shorter and the error cancels out. Only a
                # real overrun, far enough behind that catching up is hopeless,
                # is worth resynchronising and counting: resetting on ordinary
                # jitter throws away the credit that keeps the clock honest.
                pass
            else:
                # Real time is being lost. Resynchronise rather than accumulate an
                # unbounded backlog, and count it so the operator can see it.
                self.stats.overruns += 1
                if self.stats.overruns % 50 == 1:
                    log.warning("Engine overrun: %d step(s) took %.1f ms, "
                                "budget %.1f ms at %.1fx",
                                batch, elapsed_ms, period * batch * 1000.0,
                                self._speed)
                next_wake = time.monotonic()

    def _execute_step(self) -> None:
        # Advanced here rather than in the loop: the commissioning script, the
        # tests and the drawing tools all drive the engine by calling this
        # directly, and a snapshot that says sim_time zero after three
        # simulated hours is worse than useless.
        self.stats.heartbeat += 1
        self.stats.sim_time += self.dt
        step_failed = False
        with self.db.lock:
            for unit in self.flowsheet.units:
                u0 = time.perf_counter()
                try:
                    step_unit = getattr(self.flowsheet, "step_unit", None)
                    if callable(step_unit):
                        step_unit(unit, self.dt)
                    else:
                        unit.step(self.dt)
                except Exception as exc:               # one unit must not stop the rest
                    step_failed = True
                    self._on_unit_error(unit, exc)
                self.stats.unit_ms[unit.code] = (time.perf_counter() - u0) * 1000.0
            # The budget counts consecutive *steps* that failed, not units. A
            # healthy unit running after a broken one must not reset it.
            if step_failed:
                self._consecutive_errors += 1
                # Fail-safe is applied after every unit has had its turn,
                # otherwise a unit downstream of the failure would immediately
                # overwrite the bad quality this is meant to present.
                if self._consecutive_errors >= self.ERROR_BUDGET:
                    self._fail_safe()
            else:
                self._consecutive_errors = 0
            try:
                self.flowsheet.after_step(self.dt)
            except Exception:
                log.exception("Flowsheet post-step failed")
            # Post-step hooks run inside the lock, after the models and the
            # tear streams: this is where a software DCS reads its PVs and
            # writes its outputs, exactly one scan behind the process the way
            # a real controller is.
            for hook in self.post_step_hooks:
                try:
                    hook(self.dt)
                except Exception:
                    log.exception("Post-step hook failed")

    def _on_unit_error(self, unit, exc: Exception) -> None:
        self.stats.errors += 1
        self.stats.last_error = f"{unit.code}: {type(exc).__name__}: {exc}"
        # Full traceback goes to the log once; repeats are summarised so a model
        # failing every cycle cannot fill the disk in a long session.
        if self._consecutive_errors < 3:
            log.exception("Model step failed in unit %s (consecutive step %d)",
                          unit.code, self._consecutive_errors + 1)
        elif self.stats.errors % 100 == 0:
            log.error("Model still failing in unit %s: %s (%d occurrences)",
                      unit.code, self.stats.last_error, self.stats.errors)

    def _fail_safe(self) -> None:
        """Freeze and present bad quality, the way a dead field bus would look."""
        self._run.clear()
        self.stats.running = False
        self.stats.frozen_by_error = True
        for tag in self.db.by_kind(TagKind.AI, TagKind.DI):
            tag.quality = Quality.BAD
        log.critical("Engine froze after %d consecutive model errors; all inputs "
                     "marked bad. Last error: %s",
                     self._consecutive_errors, self.stats.last_error)

    # --------------------------------------------------------------- snapshots
    def save_snapshot(self, path: Path | str) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.db.lock:
            payload = {
                "version": 2,
                "saved": time.strftime("%Y-%m-%d %H:%M:%S"),
                "sim_time": self.stats.sim_time,
                "speed_factor": self._speed,
                "running": self._run.is_set(),
                # the unit set the plant was being looked at in; values below
                # are SI regardless, so a snapshot reopens in the units it was
                # saved in without a conversion of anything stored
                "unit_set": _units.current(),
                "tags": self.db.save_state(),
                # save_state carries values only, and a tag that is reading
                # uncertain must come back uncertain or the first scan behaves
                # as though the instrument had healed itself.
                "quality": {t.name: int(t.quality) for t in self.db.all()},
                "units": {u.code: u.capture() for u in self.flowsheet.units},
                "bus": dict(self.flowsheet.bus),
                "control": (self.controller.capture_state()
                            if self.controller is not None else None),
            }
        path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        log.info("Snapshot saved to %s", path)
        return path

    def load_snapshot(self, path: Path | str) -> Dict[str, object]:
        """Restore a snapshot, all of it or none of it.

        The plant is captured before anything is written, so a payload that
        fails halfway can be put back. Without that the failure message shown
        to the operator, that the simulation is unchanged, would be false: the
        tags, the tear streams and some of the units would already have been
        overwritten and the plant left in a state that never existed.

        Returns a summary of what was applied so the caller can say so rather
        than claim a silent success.
        """
        path = Path(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        version = payload.get("version")
        if version not in (1, 2):
            raise ValueError(f"unsupported snapshot version {version!r}")

        with self.db.lock:
            rollback = {
                "version": 2,
                "tags": self.db.save_state(),
                "quality": {t.name: int(t.quality) for t in self.db.all()},
                "bus": dict(self.flowsheet.bus),
                "control": (self.controller.capture_state()
                            if self.controller is not None else None),
                "units": {u.code: u.capture() for u in self.flowsheet.units},
                "sim_time": self.stats.sim_time,
                "speed_factor": self._speed,
            }
            try:
                result = self._apply_payload(payload, path.name)
            except Exception:
                try:
                    self._apply_payload(rollback, "rollback")
                except Exception:                     # pragma: no cover
                    log.exception("Snapshot rollback failed; the plant may be "
                                  "in a mixed state. Restart is advised.")
                else:
                    log.warning("Snapshot %s failed to load; the plant was put "
                                "back as it was.", path.name)
                raise

        log.info("Snapshot %s restored: %d tags, %d units, %d bus values%s",
                 path.name, result["tags"], result["units"], result["bus"],
                 f", {len(result['skipped'])} skipped" if result["skipped"]
                 else "")
        return result

    def _apply_payload(self, payload: Dict, source: str) -> Dict[str, object]:
        """Write a snapshot payload into the plant. Caller holds the lock."""
        applied = self.db.load_state(payload.get("tags", {}))
        for name, q in (payload.get("quality") or {}).items():
            tag = self.db.get(name)
            if tag is not None:
                try:
                    tag.quality = Quality(int(q))
                except (TypeError, ValueError):
                    pass

        # Tear-stream values on the bus are part of the state: without them the
        # first step after a restore runs on the built-in defaults. Every key is
        # restored, including the ones a unit only creates on its first scan.
        bus = payload.get("bus", {})
        skipped: List[str] = []
        if isinstance(bus, dict):
            for key, value in bus.items():
                try:
                    restore = getattr(self.flowsheet.bus, "restore", None)
                    if callable(restore):
                        restore(key, value)
                    else:
                        self.flowsheet.bus[key] = float(value)
                except (KeyError, TypeError, ValueError, RuntimeError):
                    skipped.append(key)

        units = 0
        missing: List[str] = []
        for unit in self.flowsheet.units:
            state = payload.get("units", {}).get(unit.code)
            if state:
                unit.apply(state)
                units += 1
            elif unit.capture():
                # Only worth reporting when the unit actually has state to
                # restore. The safety system has none, and warning about it on
                # every load would train the reader to ignore the warning.
                missing.append(unit.code)

        self.stats.sim_time = float(payload.get("sim_time", 0.0))
        # Speed is part of how the plant was being run. The run and freeze state
        # is recorded but deliberately not applied: loading a file should not
        # start a stopped simulation under the operator.
        if "speed_factor" in payload:
            self.speed_factor = float(payload["speed_factor"])

        unit_set = payload.get("unit_set")
        if unit_set and unit_set != _units.current() and not self.units_pinned:
            try:
                _units.select(str(unit_set))
                log.info("Snapshot %s presents %s units", source, unit_set)
            except ValueError:
                log.warning("Snapshot %s names unknown unit set %r; keeping %s",
                            source, unit_set, _units.current())
        control_restored = bool(
            self.controller is not None and payload.get("control"))
        if control_restored:
            self.controller.apply_state(payload["control"])

        if skipped:
            log.warning("Snapshot %s: %d bus values were not numeric and were "
                        "left alone: %s", source, len(skipped),
                        ", ".join(sorted(skipped)))
        if missing:
            log.warning("Snapshot %s carries no state for %s; those units keep "
                        "their current state", source, ", ".join(missing))
        return {"tags": applied, "units": units, "bus": len(bus) - len(skipped),
                "control": control_restored, "unit_set": unit_set,
                "skipped": skipped, "missing": missing}

    def list_units(self) -> List[str]:
        return [u.code for u in self.flowsheet.units]
