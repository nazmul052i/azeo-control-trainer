"""Controller executive — what actually ticks a downloaded module.

``StrategyRuntime.execute_scan(dt)`` does one scan and no more; something has
to call it at the module scan rate. Upstream that caller was the simulation
engine, which stayed behind with the plant. Without it a module here reported
ONLINE, showed a compiled execution order, and never executed a single scan —
so wire values, faceplates and the watch window were all frozen at their
initialisation values.

This is the missing half: a timer that scans every online runtime the store
knows about. It owns no plant and no data; it only decides *when* a module
runs. Whatever supplies tag values still sits behind ``SharedDataStore``.

At the normal 1x time scale, ``dt`` is measured wall-clock elapsed time rather
than the nominal period, so a block's timers (``START_TIMEOUT``, a ``CND``
delay, PID reset) advance at the rate a student's watch does even when the UI
thread is busy.  The Simulation Workbench may explicitly scale that elapsed
time together with the plant provider.
"""
from __future__ import annotations

import logging
import time

from PySide6.QtCore import QObject, Qt, QTimer

log = logging.getLogger("strategy.executive")

#: Module scan period. Azeo's default is 1 s; half that keeps a faceplate
#: feeling live without costing anything measurable on 7 example modules.
DEFAULT_PERIOD_MS = 500

#: Cap on the ``dt`` handed to a scan. A blocked UI thread (a modal dialog, a
#: long save) must not deliver one enormous step that runs every delay timer
#: to completion at once — a real controller misses scans, it does not
#: fast-forward them.
MAX_DT_S = 2.0

# One module remains atomic. Yield between modules so a large controller
# cannot hold an operator click behind hundreds of milliseconds of scanning.
UI_SCAN_BUDGET_MS = 8.0


class ControllerExecutive(QObject):
    """Scans every online strategy runtime registered with the store."""

    def __init__(self, store, period_ms: int = DEFAULT_PERIOD_MS, parent=None):
        super().__init__(parent)
        self._store = store
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._timer_scan)
        self._continuation = QTimer(self)
        self._continuation.setSingleShot(True)
        self._continuation.timeout.connect(self._continue_scan)
        self._scan_iterator = None
        self._period_ms = int(period_ms)
        self._last_t: float | None = None
        self._scans = 0
        #: Scans whose *work* exceeded the period. A real controller
        #: reports overruns; a trainer that hides them teaches that scan
        #: budget is free. Counted against the period in force when the
        #: scan ran.
        self._overruns = 0
        self._last_work_ms = 0.0
        # Simulation Workbench pacing.  One is the production/default path;
        # only an explicit engineering simulation session changes it.
        self._simulation_speed_factor = 1.0

    # ------------------------------------------------------------- control
    @property
    def period_ms(self) -> int:
        return self._period_ms

    def set_period_ms(self, ms: int) -> None:
        self._period_ms = max(10, int(ms))
        if self._timer.isActive():
            self._timer.start(self._base_tick_ms())

    @property
    def simulation_speed_factor(self) -> float:
        return self._simulation_speed_factor

    def set_simulation_speed_factor(self, factor: float) -> float:
        factor = max(0.1, min(20.0, float(factor)))
        self._cancel_pending_scan()
        self._simulation_speed_factor = factor
        # Deadlines were expressed in the previous wall-clock scale. Start a
        # new schedule rather than creating a catch-up burst at the boundary.
        for runtime in self.online_runtimes():
            runtime._pk_due = None
            runtime._pk_last_t = None
        if self._timer.isActive():
            self._timer.start(self._base_tick_ms())
        log.info("Controller simulation speed set to %.2fx", factor)
        return factor

    @property
    def is_running(self) -> bool:
        return self._timer.isActive()

    @property
    def scan_count(self) -> int:
        return self._scans

    @property
    def overrun_count(self) -> int:
        return self._overruns

    @property
    def last_work_ms(self) -> float:
        """How long the last tick's scans actually took, in ms."""
        return self._last_work_ms

    def start(self) -> None:
        if self._timer.isActive():
            return
        self._last_t = None
        self._timer.start(self._base_tick_ms())
        log.info("Controller executive started (%d ms tick)",
                 self._timer.interval())

    def stop(self) -> None:
        if not self._timer.isActive():
            return
        self._timer.stop()
        self._cancel_pending_scan()
        log.info("Controller executive stopped after %d scans", self._scans)

    def _cancel_pending_scan(self) -> None:
        self._continuation.stop()
        pending, self._scan_iterator = self._scan_iterator, None
        if pending is not None:
            pending.close()
            # Completed modules may already have queued outputs. Pausing
            # cancels future work, not the writes those scans produced.
            self.apply_field_writes()

    def _timer_scan(self) -> None:
        if self._scan_iterator is None and self.is_running:
            self._scan_iterator = self._scan_pass()
            self._last_work_ms = 0.0
            self._continue_scan()

    def _continue_scan(self) -> None:
        if self._scan_iterator is not None and self.is_running:
            self._advance_scan(UI_SCAN_BUDGET_MS)

    def _advance_scan(self, budget_ms=None) -> int:
        started = time.perf_counter()
        try:
            while True:
                try:
                    next(self._scan_iterator)
                except StopIteration as completed:
                    self._scan_iterator = None
                    self._continuation.stop()
                    return completed.value
                if budget_ms is not None and (
                        time.perf_counter() - started) * 1000 >= budget_ms:
                    self._continuation.start(0)
                    return 0
        finally:
            # Exclude time yielded to operator input from controller work.
            self._last_work_ms += (time.perf_counter() - started) * 1000

    # ---------------------------------------------------------------- scan
    def online_runtimes(self) -> list:
        """Every online runtime the store knows about, without duplicates."""
        if self._store is None:
            return []
        out = []
        try:
            known = list(self._store.get_strategy_runtimes())
        except Exception:                                   # noqa: BLE001
            known = []
        legacy = getattr(self._store, "_strategy_runtime", None)
        if legacy is not None:
            known.append(legacy)
        for rt in known:
            if getattr(rt, "is_online", False) and rt not in out:
                out.append(rt)
        return out

    def _rate_ms(self, rt) -> int:
        """The module's own scan rate — PK parity: rates are per module,
        assigned on the module, read live so a rate change applies at the
        next deadline without a re-download."""
        graph = getattr(getattr(rt, "compiled", None), "graph", None)
        try:
            rate = int(getattr(graph, "scan_ms", 0) or 0)
        except (TypeError, ValueError):
            rate = 0
        return rate if rate > 0 else self._period_ms

    def _base_tick_ms(self) -> int:
        """Return the wall-clock timer period for the active time scale.

        An area holding only default modules yields exactly the old
        behaviour — one 500 ms tick.  At 1x the established 100 ms floor is
        preserved; an explicit Workbench speed-up scales that floor down to
        10 ms so controller time and process time advance together.
        """
        from math import gcd

        tick = 0
        for rt in self.online_runtimes():
            wall_rate = max(10, round(
                self._rate_ms(rt) / self._simulation_speed_factor))
            tick = gcd(tick, wall_rate)
        if tick <= 0:
            tick = self._period_ms
        wall_default = max(10, round(
            self._period_ms / self._simulation_speed_factor))
        minimum_tick = max(10, round(
            100 / self._simulation_speed_factor))
        return max(minimum_tick, min(tick, wall_default))

    def scan_once(self) -> int:
        """Synchronously finish one pass for explicit tests/manual callers."""
        self._continuation.stop()
        if self._scan_iterator is None:
            self._scan_iterator = self._scan_pass()
            self._last_work_ms = 0.0
        return self._advance_scan()

    def _scan_pass(self):
        """Scan every online runtime whose deadline has arrived.

        Deadline scheduling, per module (PK parity): each runtime carries
        its own `_pk_due` and `_pk_last_t`, *on the runtime* rather than in
        this executive — so when a different executive ticks the same
        module (the station drives its own scan), the schedule is honoured
        rather than restarted. Re-arming is drift-free (`due + rate`), and
        a deadline missed by more than a period is skipped, not caught up:
        a real controller misses scans, it does not fast-forward them.
        """
        now = time.perf_counter()
        self._last_t = now
        ran = 0
        for rt in self.online_runtimes():
            # A download/offline action can run while the pass yields. Never
            # execute a runtime retired by that operator/engineering action.
            if not rt.is_online:
                continue
            now = time.perf_counter()
            rate_s = self._rate_ms(rt) / 1000.0
            wall_rate_s = rate_s / self._simulation_speed_factor
            due = getattr(rt, "_pk_due", None)
            if due is None:
                due = now
            if now < due - 0.005:
                continue
            last = getattr(rt, "_pk_last_t", None)
            dt = rate_s if last is None else min(
                (now - last) * self._simulation_speed_factor, MAX_DT_S)
            rt._pk_last_t = now
            t_module = time.perf_counter()
            try:
                completed = rt.execute_scan(dt)
            except Exception as exc:                        # noqa: BLE001
                # One module's bad scan must not stop the others. The
                # runtime already traps per-block errors; this catches a
                # failure in the scan machinery itself.
                log.error("Executive: scan failed for %r — %s", rt, exc)
            else:
                # A debugger-paused runtime stays online but completes no
                # module scan. Counting its safe no-op as a scan made the
                # executive status advance while every block was frozen.
                if completed is not False:
                    ran += 1
                    work_ms = (time.perf_counter() - t_module) * 1000.0
                    if work_ms > wall_rate_s * 1000.0:
                        # Overruns are per module, against the module's own
                        # current wall-clock budget.  At accelerated time the
                        # same scan has less wall time available; hiding that
                        # miss would make a requested speed look achieved.
                        rt._pk_overruns = getattr(rt, "_pk_overruns", 0) + 1
                        self._overruns += 1
            due += wall_rate_s
            if due <= now:
                due = now + wall_rate_s
            rt._pk_due = due
            yield
        if ran:
            self._scans += 1
        # Drain even when nothing is online: the queue also carries
        # operator writes (AO/valve faceplates, checkpoint restores), and
        # gating the drain on a running module left those writes parked
        # exactly when an engineer is working offline and watching the
        # tag browser for the effect.
        self.apply_field_writes()
        # Follow the fastest online module: a 100 ms module needs a 100 ms
        # tick, and an area of default modules keeps the old 500 ms timer.
        if self._timer.isActive():
            tick = self._base_tick_ms()
            if self._timer.interval() != tick:
                self._timer.start(tick)
        return ran

    def simulation_step_once(self) -> int:
        """Execute one nominal scan of every online, non-debug-paused module.

        Used only while the Workbench has stopped the periodic executive. The
        normal deadline scheduler remains untouched and is re-established on
        resume, so single-step cannot create a later catch-up burst.
        """
        if self._timer.isActive():
            raise RuntimeError("pause the controller executive before stepping")
        ran = 0
        for runtime in self.online_runtimes():
            if getattr(runtime, "is_debug_paused", False):
                continue
            dt = self._rate_ms(runtime) / 1000.0
            if runtime.execute_scan(dt) is not False:
                ran += 1
            runtime._pk_due = None
            runtime._pk_last_t = None
        self.apply_field_writes()
        if ran:
            self._scans += 1
        return ran

    def apply_field_writes(self) -> int:
        """Publish everything the scan queued for the field.

        `DataBridge` writes an ``AO``/``DO`` block's value with
        ``store.queue_write``, which appends to a list. Upstream the simulation
        engine drained that list once per cycle; nothing in this repo did, so
        **every controller output stopped at the queue** — the plant drove the
        controller and the controller drove nothing. A valve commanded open
        never moved, and the only reason it was not obvious is that the tests
        had been setting the field tags directly.

        Draining here rather than changing the bridge keeps the write path in
        one place and fixes the other users of the queue at the same time,
        including the AO and valve faceplates' operator writes.
        """
        if self._store is None:
            return 0
        # A healthy field transport is the sole owner of this destructive
        # queue.  A declared but unavailable transport still has to service
        # unrelated faceplate/tuning writes, though; otherwise one bad I/O
        # provider deadlocks every local operator write.  Drivers expose this
        # narrow hook so they can reject their own field routes without
        # pretending the command reached a device.
        driver = getattr(self._store, "field_io_driver", None)
        if driver is not None:
            service = getattr(driver, "service_pending_writes", None)
            if callable(service):
                try:
                    return int(service())
                except Exception:                           # noqa: BLE001
                    log.exception(
                        "Executive: unavailable field driver could not "
                        "service pending local writes")
            return 0
        drain = getattr(self._store, "drain_writes", None)
        if not callable(drain):
            return 0
        writes = drain()
        if not writes:
            return 0
        try:
            self._store.set_many(dict(writes))
        except Exception:                                   # noqa: BLE001
            log.exception("Executive: could not apply %d field write(s)",
                          len(writes))
            return 0
        return len(writes)

    def stop_if_idle(self) -> bool:
        """Stop when nothing is online any more. Returns True if it stopped."""
        if self._timer.isActive() and not self.online_runtimes():
            self.stop()
            return True
        return False
