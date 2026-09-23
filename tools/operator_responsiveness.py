"""Native Operator task measurements; imported by verify_operator_workspace.

Every request returns to Qt before the next one. Paint completion is the
return from QGraphicsView.paintEvent, not a claim about desktop presentation.
The probes exist only in this verifier process, never the shipping app.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import nullcontext
from functools import wraps
import hashlib
import json
import math
import platform
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QThread, QTimer, qVersion
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
REQUEST = QEvent.Type(QEvent.registerEventType())
TARGETS = ("Overview - L1 Plant", "U100 - L2 Feed Preparation",
           "U300 - L2 Charge Heating")


def distribution(values):
    ordered = sorted(values)
    if not ordered:
        return {"count": 0}
    return {"count": len(values), "p50_ms": ordered[math.ceil(len(values) * .5) - 1],
            "p95_ms": ordered[math.ceil(len(values) * .95) - 1],
            "max_ms": ordered[-1], "samples_ms": values}


class ResponsivenessRun(QObject):
    def __init__(self, report, output):
        super().__init__()
        self.report = report
        self.output = output
        self.active = False
        self.spans = defaultdict(list)
        self.completed = {}
        self.raw = []
        self.current = None
        self.phase = "cold"
        self.batch = self.sample = 0
        self.comparison = "--catalog-comparison" in sys.argv
        self.arm = "uncached" if self.comparison else "scoped"
        self.arm_index = 0
        self.profile_dwell = "--profile-dwell" in sys.argv
        self.history_workload = "--history-workload" in sys.argv or self.profile_dwell
        self.installed = []
        self.thread_diagnostics = None
        self.minimal_probes = "--minimal-probes" in sys.argv
        self.soak = None
        if "--soak-seconds" in sys.argv:
            from operator_soak import OperatorSoak
            seconds = float(sys.argv[sys.argv.index("--soak-seconds") + 1])
            interval = float(sys.argv[sys.argv.index("--soak-interval") + 1]) if "--soak-interval" in sys.argv else 30
            self.soak = OperatorSoak(self, seconds, interval)
            self.history_workload = True

    def install_probes(self):
        from azeo_control_trainer.azeo_control_designer.executive import ControllerExecutive
        from azeo_control_trainer.azeo_operator_station.console import LiveStation
        from azeo_control_trainer.azeo_operator_station.alarm_rollup import DisplayAlarmRollup
        from azeo_control_trainer.azeo_operator_station.deployment import PvmDeployment
        from azeo_control_trainer.core.hmi.pvms.rendering.viewer import PvmDisplayView
        from azeo_control_trainer.core.hmi.pvms.render import PvmFaceplateWidget
        from azeo_control_trainer.core.hmi.binding import BindingEngine
        from azeo_control_trainer.core.hmi.history.historian import ContinuousHistorian
        from azeo_control_trainer.core.hmi.history.view import ProcessHistoryView
        from azeo_control_trainer.core.strategy.engine.runtime import StrategyRuntime

        def measure(kind, name):
            original = getattr(kind, name)
            key = f"{kind.__name__}.{name}"

            @wraps(original)
            def call(obj, *args, **kwargs):
                started = time.perf_counter()
                try:
                    return original(obj, *args, **kwargs)
                finally:
                    finished = time.perf_counter()
                    if self.active:
                        span_key = f"{self.arm}:{key}" if self.comparison else key
                        self.spans[span_key].append((finished - started) * 1000)
            self.installed.append((kind, name, original))
            setattr(kind, name, call)

        for kind, names in (
            (ControllerExecutive, ("_advance_scan",)),
            (LiveStation, ("show_display", "tick", "_timer_tick", "_configure_historian", "sync_chrome", "sync_status")),
            (PvmDisplayView, ("__init__", "refresh", "_apply_animations")),
            (PvmFaceplateWidget, ("refresh",)),
            (BindingEngine, ("poll",)),
            (DisplayAlarmRollup, ("poll", "refresh_documents")),
            (PvmDeployment, ("displays",)),
            (ContinuousHistorian, ("collect",)),
            (ProcessHistoryView, ("_refresh",)),
        ):
            for name in names:
                if not self.minimal_probes:
                    measure(kind, name)

        original_scan = StrategyRuntime.execute_scan

        @wraps(original_scan)
        def scan(runtime, *args, **kwargs):
            before = runtime.scan_count
            result = original_scan(runtime, *args, **kwargs)
            if runtime.scan_count > before:
                self.completed[id(runtime)] = time.perf_counter()
            return result
        self.installed.append((StrategyRuntime, "execute_scan", original_scan))
        StrategyRuntime.execute_scan = scan

        original_paint = PvmDisplayView.paintEvent

        def paint(view, event):
            try:
                original_paint(view, event)
                if self.active and self.current is not None and "handler_ms" in self.current \
                        and self.station.view is view and view._cache_key[1] == self.current["target"]:
                    self.painted()
            except Exception:
                self.report["error"] = traceback.format_exc()
                self.active = False
                # Return from Qt's paint callback before closing its view.
                QTimer.singleShot(0, lambda: self.finish(failed=True))
        self.installed.append((PvmDisplayView, "paintEvent", original_paint))
        PvmDisplayView.paintEvent = paint
        if "--thread-diagnostics" in sys.argv:
            from thread_diagnostics import ThreadDiagnostics
            self.thread_diagnostics = ThreadDiagnostics(lambda: self.active)
            self.thread_diagnostics.install()

    def start(self, station):
        self.setParent(station)
        self.station = station
        self.select_arm()
        from azeo_control_trainer.core.presentation import headless
        headless.is_headless = lambda: False
        station.show_display(TARGETS[-1])
        self.metadata = {
            "python": platform.python_version(), "qt": qVersion(),
            "python_thread_switch_ms": sys.getswitchinterval() * 1000,
            "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "module_count": len(station.simulation_service._runtimes()),
            "method": "posted request to completed Qt viewport paint; normal event loop; three batches of 30 each; no forced repaint/processEvents",
            "cold": "evict inactive views before request; accepted revision and filesystem cache retained",
            "warm": "all three targets retained before measurements",
            "screen": {"width": station.width(), "height": station.height(),
                       "dpr": station.devicePixelRatioF()},
            "source_hashes": {},
            "catalog_comparison": self.comparison,
            "history_workload": self.history_workload,
            "diagnostic_profile": self.profile_dwell,
            "thread_diagnostics": self.thread_diagnostics is not None,
            "minimal_probes": self.minimal_probes,
        }
        for relative in ("azeo_operator_station/console.py", "azeo_operator_station/deployment.py",
                         "azeo_operator_station/alarm_rollup.py",
                         "core/hmi/pvms/rendering/viewer.py", "core/hmi/binding/source.py",
                         "core/hmi/history/historian.py", "core/hmi/pvms/render.py",
                         "core/hmi/binding/engine.py", "azeo_operator_station/release_catalog.py",
                         "azeo_control_designer/executive.py", "connectivity/fieldio/local_virtual_io.py"):
            path = ROOT / "src/azeo_control_trainer" / relative
            self.metadata["source_hashes"][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.heartbeat = QTimer(self)
        self.heartbeat.setInterval(50)
        self.heartbeat.timeout.connect(self.heartbeat_sample)
        self.heartbeat_due = time.perf_counter() + .05
        self.delivery = []
        self.heartbeat.start()
        self.active = True
        self.host_samples = []
        self.host_timer = QTimer(self)
        try:
            from performance_host import BackgroundHostSampler
            self.host_sampler = BackgroundHostSampler()
            self.metadata["host_sampling_thread"] = "ui-host-sampling"
            self.sample_host()
            self.host_timer.setInterval(1000)
            self.host_timer.timeout.connect(self.sample_host)
            self.host_timer.start()
        except (AttributeError, OSError) as error:
            self.metadata["host_sampling_error"] = str(error)
        self.inventory("O-01")
        QTimer.singleShot(500, self.multiwindow if self.history_workload else self.prepare)

    def sample_host(self):
        try:
            self.host_samples.extend(self.host_sampler.drain())
            if self.host_sampler.error:
                self.metadata["host_sampling_error"] = self.host_sampler.error
                self.host_timer.stop()
        except OSError as error:
            self.metadata["host_sampling_error"] = str(error)
            self.host_timer.stop()

    def event(self, event):
        if event.type() == REQUEST:
            try:
                row = self.current
                row["delivery_ms"] = (time.perf_counter() - row["requested_at"]) * 1000
                before = time.perf_counter()
                assert self.station.show_display(row["target"])
                row["handler_ms"] = (time.perf_counter() - before) * 1000
                # Newly exposed/changed view paints normally after event return.
                self.station.view.viewport().update()
            except Exception:
                self.fail()
            return True
        return super().event(event)

    def heartbeat_sample(self):
        now = time.perf_counter()
        self.delivery.append(max(0., (now - self.heartbeat_due) * 1000))
        self.heartbeat_due = now + .05

    def prepare(self):
        try:
            if self.phase == "cold":
                self.station._clear_view_cache()
            target = TARGETS[self.sample % len(TARGETS)]
            # A separate callback gives eviction's deleteLater a normal turn.
            QTimer.singleShot(0, lambda: self.request(target))
        except Exception:
            self.fail()

    def request(self, target):
        self.current = {"phase": self.phase, "batch": self.batch + 1,
                        "arm": self.arm, "target": target, "requested_at": time.perf_counter()}
        QCoreApplication.postEvent(self, QEvent(REQUEST))

    def painted(self):
        row, self.current = self.current, None
        now = time.perf_counter()
        row["painted_ms"] = (now - row.pop("requested_at")) * 1000
        runtimes = self.station.simulation_service._runtimes()
        ages = [(now - self.completed[id(runtime)]) * 1000 for runtime in runtimes
                if runtime.is_online and id(runtime) in self.completed]
        row["oldest_completed_scan_age_ms"] = max(ages) if ages else None
        row["modules_with_completion"] = len(ages)
        row["source_state"] = self.station.status.status.source_state
        self.raw.append(row)
        if self.soak is not None and self.soak.active:
            self.soak.painted(row)
            return
        self.sample += 1
        if self.sample == 30:
            values = [one["painted_ms"] for one in self.raw
                      if one["phase"] == self.phase and one["batch"] == self.batch + 1 and one["arm"] == self.arm]
            print(f"{self.phase} {self.arm} batch {self.batch + 1}: p95 {distribution(values)['p95_ms']:.1f} ms", flush=True)
            self.sample = 0
            if self.comparison and self.arm_index == 0:
                self.arm_index = 1
                self.select_arm()
                QTimer.singleShot(150, self.prepare)
                return
            self.arm_index = 0
            self.batch += 1
            if self.batch == 3:
                if self.phase == "warm":
                    QTimer.singleShot(200, self.pause_source if self.history_workload else self.multiwindow)
                    return
                self.phase, self.batch = "warm", 0
                self.select_arm()
                self.warm_targets = iter(TARGETS)
                QTimer.singleShot(150, self.prime)
                return
            self.select_arm()
        QTimer.singleShot(150, self.prepare)

    def select_arm(self):
        if not self.comparison:
            return
        order = ("uncached", "scoped") if self.batch % 2 == 0 else ("scoped", "uncached")
        self.arm = order[self.arm_index]
        if self.arm == "scoped":
            self.station.deployment.__dict__.pop("catalog_snapshot", None)
        else:
            self.station.deployment.catalog_snapshot = nullcontext

    def prime(self):
        try:
            target = next(self.warm_targets, None)
            if target is None:
                QTimer.singleShot(150, self.prepare)
            else:
                self.station.show_display(target)
                QTimer.singleShot(150, self.prime)
        except Exception:
            self.fail()

    def inventory(self, name):
        station = self.station
        timers, seen = [], set()
        workers = []
        for root in QApplication.topLevelWidgets():
            for timer in root.findChildren(QTimer):
                if id(timer) in seen:
                    continue
                seen.add(id(timer))
                timers.append({"owner": type(timer.parent()).__name__, "active": timer.isActive(),
                               "interval_ms": timer.interval(), "single_shot": timer.isSingleShot()})
            workers.extend({"class": type(worker).__name__, "running": worker.isRunning()}
                           for worker in root.findChildren(QThread))
        self.metadata.setdefault("inventories", {})[name] = {
            "faceplates": len(station.faceplates), "historians": len(station.process_history_views),
            "active_views": len({id(view) for view in (*station.views.values(), station.view) if view}),
            "cached_views": len(station._view_cache), "timers": timers, "qt_workers": workers,
            "python_threads": [thread.name for thread in threading.enumerate()]}

    def multiwindow(self):
        try:
            from azeo_control_trainer.core.hmi.pvms.rendering.items import PvmItem
            from azeo_control_trainer.core.hmi.history.view import ProcessHistoryView
            self.station.show_display("U300 - L2 Charge Heating")
            pvms = [item for item in self.station.view.scene().items()
                    if isinstance(item, PvmItem) and item.pvm.block_type == "PID"][:4]
            assert len(pvms) == 4
            self.soak_pvms = {}
            for item in pvms:
                self.station.open_faceplate(item.pvm)
                faceplate = self.station.faceplates[-1][1]
                self.soak_pvms[self.station.faceplates[-1][0]] = item.pvm
                faceplate.context_title.pin.click()
                assert faceplate.pinned
            tags = list(self.station.historian.TAGS)[:20]
            assert len(tags) == 20
            for index in range(2):
                view = ProcessHistoryView(self.station.historian, "", self.station)
                self.station._retain_window(self.station.process_history_views, view)
                view.set_pens(tags[index * 10:(index + 1) * 10])
                self.station._present(view, retain=False)
            if self.profile_dwell:
                self.inventory("O-02-start")
                QTimer.singleShot(1000, self.start_profile_dwell)
            elif self.history_workload:
                self.inventory("O-02-start")
                self.phase = "warm"
                self.warm_targets = iter(TARGETS)
                QTimer.singleShot(1000, self.prime)
            else:
                QTimer.singleShot(3000, self.finish)
        except Exception:
            self.fail()

    def start_profile_dwell(self):
        import cProfile
        try:
            # CPython 3.13 monitoring can include other process threads.
            # Treat this as a call inventory, not exclusive GUI-time attribution.
            self.metadata["profile_scope"] = "diagnostic process call inventory; not exclusive GUI time"
            self.profile = cProfile.Profile()
            self.profile.enable()
            QTimer.singleShot(30000, self.end_profile_dwell)
        except Exception:
            self.fail()

    def end_profile_dwell(self):
        import pstats
        try:
            self.profile.disable()
            self.profile.dump_stats(str(self.output / "workload-dwell.prof"))
            with (self.output / "workload-dwell.txt").open("w", encoding="utf-8") as output:
                pstats.Stats(self.profile, stream=output).strip_dirs().sort_stats("cumulative").print_stats(100)
            self.finish()
        except Exception:
            self.fail()

    def pause_source(self):
        try:
            self.station.simulation_service.pause()
            self.station.tick()
            assert self.station.status.status.source_state == "PAUSED"
            self.metadata["pause_resume"] = {"paused_state": "PAUSED", "observations": []}
            QTimer.singleShot(1000, self.resume_source)
        except Exception:
            self.fail()

    def resume_source(self):
        try:
            self.resume_counts = {id(runtime): runtime.scan_count
                                  for runtime in self.station.simulation_service._runtimes() if runtime.is_online}
            self.metadata["pause_resume"].update(
                criterion="LIVE and all initially online modules complete a new scan after resume",
                expected_modules=len(self.resume_counts))
            self.station.simulation_service.resume()
            self.resumed_at = time.perf_counter()
            QTimer.singleShot(100, self.observe_resume)
        except Exception:
            self.fail()

    def observe_resume(self):
        try:
            self.station.sync_status(time.time())
            elapsed = (time.perf_counter() - self.resumed_at) * 1000
            state = self.station.status.status.source_state
            advanced = sum(runtime.scan_count > self.resume_counts.get(id(runtime), runtime.scan_count)
                           for runtime in self.station.simulation_service._runtimes() if runtime.is_online)
            fresh = state == "LIVE" and bool(self.resume_counts) and advanced == len(self.resume_counts)
            resume = self.metadata["pause_resume"]
            resume["observations"].append({"elapsed_ms": elapsed, "state": state, "advanced_modules": advanced})
            if fresh or elapsed >= 5000:
                resume.update(live=fresh, elapsed_ms=elapsed,
                              budget_passed=fresh and elapsed <= 2000)
                QTimer.singleShot(0, self.finish)
            else:
                QTimer.singleShot(100, self.observe_resume)
        except Exception:
            self.fail()

    def fail(self):
        self.report["error"] = traceback.format_exc()
        print(self.report["error"], flush=True)
        self.finish(failed=True)

    def finish(self, failed=False):
        if self.soak is not None:
            if not failed and not self.soak.finished:
                self.soak.start()
                return
            self.soak.stop()
            if self.soak.started is not None:
                self.metadata["soak"] = self.soak.result()
        if getattr(self, "profile", None) is not None:
            self.profile.disable()
        self.active = False
        self.heartbeat.stop()
        self.host_timer.stop()
        if hasattr(self, "host_sampler"):
            self.host_sampler.close()
            self.sample_host()
        self.inventory("O-02")
        self.station.grab().save(str(self.output / "station.png"))
        if self.station.faceplates:
            self.station.faceplates[0][1].grab().save(str(self.output / "faceplate.png"))
        if self.station.process_history_views:
            self.station.process_history_views[0].grab().save(str(self.output / "historian.png"))
        self.metadata["navigation"] = {}
        conditions = (("warm", 150),) if self.history_workload else (("cold", 500), ("warm", 150))
        for phase, budget in conditions:
            batches = []
            for batch in range(1, 4):
                rows = [row for row in self.raw if row["phase"] == phase and row["batch"] == batch
                        and row["arm"] == "scoped"]
                batches.append({field: distribution([row[field] for row in rows if row.get(field) is not None])
                                for field in ("painted_ms", "delivery_ms", "handler_ms", "oldest_completed_scan_age_ms")})
            passed = all(row["painted_ms"].get("count") == 30 and row["painted_ms"]["p95_ms"] <= budget
                         for row in batches)
            self.metadata["navigation"][phase] = {"batches": batches, "budget_ms": budget,
                                                   "all_batches_passed": passed}
        self.metadata["heartbeat_overdue"] = distribution(self.delivery)
        self.metadata["spans"] = {key: distribution(values) for key, values in self.spans.items()}
        self.metadata["rows"] = self.raw
        self.metadata["host_samples"] = self.host_samples
        self.metadata["scan_observation_counts"] = dict(Counter(row["modules_with_completion"] for row in self.raw))
        if self.thread_diagnostics is not None:
            (self.output / "threads.json").write_text(
                json.dumps(self.thread_diagnostics.export(), indent=2), encoding="utf-8")
            self.thread_diagnostics.close()
        self.report["responsiveness"] = self.metadata
        (self.output / "responsiveness.json").write_text(json.dumps(self.metadata, indent=2), encoding="utf-8")
        for kind, name, original in reversed(self.installed):
            setattr(kind, name, original)
        if self.comparison:
            self.station.deployment.__dict__.pop("catalog_snapshot", None)
        passed = all(row["all_batches_passed"] for row in self.metadata["navigation"].values())
        if self.history_workload:
            passed &= self.metadata.get("pause_resume", {}).get("budget_passed", False)
        if self.soak is not None:
            passed &= self.metadata.get("soak", {}).get("duration_and_lifecycle_passed", False)
        self.station.close()
        QApplication.instance().exit(1 if failed or not passed else 0)
