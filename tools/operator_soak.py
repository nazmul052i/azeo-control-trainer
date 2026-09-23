"""Long lifecycle exercise composed with the native Operator task verifier."""
from __future__ import annotations

import json
import statistics
import time
import weakref
from collections import Counter

from PySide6.QtCore import QTimer, Qt
import shiboken6


class OperatorSoak:
    def __init__(self, run, seconds, interval=30):
        self.run = run
        self.seconds = seconds
        self.interval = interval
        self.active = self.finished = False
        self.closed = []
        self.rows = []
        self.transitions = self.cycles = 0
        self.started = None

    def start(self):
        if self.active or self.finished:
            return
        self.active = True
        self.started = time.perf_counter()
        self.run.phase = "soak"
        self.run.inventory("soak-start")
        self.baseline_host = len(self.run.host_samples)
        self.deadline = QTimer(self.run)
        self.deadline.setSingleShot(True)
        self.deadline.setTimerType(Qt.PreciseTimer)
        self.deadline.timeout.connect(self.finish)
        self.deadline.start(int(self.seconds * 1000))
        self._next()

    def _next(self):
        if not self.active:
            return
        from operator_responsiveness import TARGETS
        self.run.request(TARGETS[self.transitions % len(TARGETS)])

    def painted(self, row):
        row["soak_elapsed_s"] = time.perf_counter() - self.started
        self.rows.append(row)
        self.transitions += 1
        # Return from paint before changing top-level faceplate ownership.
        QTimer.singleShot(0, self._cycle)

    def _cycle(self):
        if not self.active:
            return
        try:
            station = self.run.station
            if self.cycles < 50:
                key, widget = station.faceplates[0]
                pvm = self.run.soak_pvms[key]
                self.closed.append(weakref.ref(widget))
                widget.close()

                def reopen():
                    if not self.active:
                        return
                    try:
                        station.open_faceplate(pvm)
                        replacement = next(fp for candidate, fp in station.faceplates if candidate == key)
                        if not replacement.pinned:
                            replacement.context_title.pin.click()
                        self.cycles += 1
                        self._schedule()
                    except Exception:
                        self.run.fail()

                QTimer.singleShot(0, reopen)
            else:
                self._schedule()
        except Exception:
            self.run.fail()

    def _schedule(self):
        if not self.active:
            return
        if self.transitions % 10 == 0:
            self.run.inventory(f"soak-{self.transitions}")
        # Keep the heartbeat file small. Rewriting every retained host sample
        # during the run adds growing disk work to the task being measured.
        progress = dict(elapsed_s=time.perf_counter() - self.started, requested_s=self.seconds,
                        transitions=self.transitions, faceplate_cycles=self.cycles,
                        surviving_closed_windows=sum(ref() is not None and shiboken6.isValid(ref())
                                                     for ref in self.closed))
        (self.run.output / "soak-progress.json").write_text(json.dumps(progress, indent=2), encoding="utf-8")
        if self.transitions % 10 == 0:
            print(f"Soak {progress['elapsed_s'] / 60:.1f} min: {self.transitions} transitions, "
                  f"{self.cycles} faceplate cycles, {progress['surviving_closed_windows']} closed survivors", flush=True)
        QTimer.singleShot(int(self.interval * 1000), self._next)

    def result(self):
        from operator_responsiveness import distribution
        elapsed = time.perf_counter() - self.started if self.started is not None else 0
        first = [row["painted_ms"] for row in self.rows if row["soak_elapsed_s"] <= 1800]
        last = [row["painted_ms"] for row in self.rows if row["soak_elapsed_s"] >= max(0, elapsed - 1800)]
        samples = self.run.host_samples[self.baseline_host:] if self.started is not None else []
        points = self.run.station.historian.available_points().values()
        survivors = sum(ref() is not None and shiboken6.isValid(ref()) for ref in self.closed)
        windows = self.run.metadata.get("inventories", {})
        inventories = [value for name, value in windows.items() if name.startswith("soak-")]
        ownership = []
        for value in inventories:
            row = {key: value[key] for key in ("faceplates", "historians", "cached_views", "python_threads")}
            # The soak deadline is a verifier timer added after the first
            # inventory. Count application timers, including inactive ones,
            # so closing a window cannot conceal leaked stopped timers.
            # A continuation/pulse can legitimately be active in one snapshot
            # and idle in the next. Its lifetime, not that phase, must be stable.
            counts = Counter((timer["owner"], timer["interval_ms"], timer["single_shot"])
                             for timer in value["timers"] if timer["owner"] != "ResponsivenessRun")
            row["timers"] = sorted((*key, count) for key, count in counts.items())
            row["qt_workers"] = sorted(worker["class"] for worker in value["qt_workers"])
            row["python_threads"] = sorted(row["python_threads"])
            ownership.append(row)
        stable_ownership = bool(ownership) and all(row == ownership[0] for row in ownership)
        # Report resource trends without claiming that raw-history retention is
        # a leak or that a short run establishes the two-hour plateau allowance.
        trends = {}
        if len(samples) > 1:
            width = min(60, max(1, len(samples) // 4))
            for key in ("private_bytes", "working_set_bytes", "handle_count"):
                before = [row[key] for row in samples[:width] if row.get(key) is not None]
                after = [row[key] for row in samples[-width:] if row.get(key) is not None]
                if before and after:
                    trends[key] = dict(initial_median=statistics.median(before),
                                       final_median=statistics.median(after),
                                       growth=statistics.median(after) - statistics.median(before))
        return dict(elapsed_s=elapsed, requested_s=self.seconds,
                    transitions=self.transitions, faceplate_cycles=self.cycles,
                    surviving_closed_windows=survivors,
                    first_30_minutes=distribution(first), final_30_minutes=distribution(last),
                    history_samples_retained=sum(len(point.times) for point in points),
                    ownership=ownership, bounded_ownership_passed=stable_ownership, resource_trends=trends,
                    plateau_qualification="Requires retained-history accounting and review; not inferred from a short run",
                    host_samples=samples,
                    duration_and_lifecycle_passed=elapsed >= 7200 and self.transitions >= 200
                    and self.cycles >= 50 and survivors == 0 and stable_ownership)

    def finish(self):
        if not self.active:
            return
        self.active = False
        self.finished = True
        self.run.metadata["soak"] = self.result()
        self.run.finish()

    def stop(self):
        self.active = False
        if hasattr(self, "deadline"):
            self.deadline.stop()
