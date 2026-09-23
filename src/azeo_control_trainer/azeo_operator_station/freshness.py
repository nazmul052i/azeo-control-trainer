"""Observe completed scans without confusing a responsive UI with live data."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceState:
    state: str = ""
    last_update: float | None = None
    scan_ms: int = 1000
    detail: str = "Waiting for a completed controller scan"


class ScanFreshness:
    def __init__(self):
        self._observed: dict[str, tuple[object, int | None, float | None]] = {}

    def sample(self, runtimes, now: float, *, paused=False) -> SourceState:
        online = [runtime for runtime in runtimes if runtime.is_online]
        if not online:
            self._observed.clear()
            return SourceState("DISCONNECTED", detail="No controller modules are online")
        rows = []
        seen = set()
        for runtime in online:
            graph = getattr(getattr(runtime, "compiled", None), "graph", None)
            if graph is None:
                continue
            name = graph.name
            seen.add(name)
            count = getattr(runtime, "scan_count", None)
            previous = self._observed.get(name)
            # The first counter observation proves no age. An already frozen
            # controller must not look fresh merely because a station opened.
            at = previous[2] if previous and previous[0] is runtime else None
            if previous and previous[0] is runtime and count is not None \
                    and previous[1] is not None and count > previous[1]:
                at = now
            elif previous and count is not None and previous[1] is not None and count < previous[1]:
                at = None
            self._observed[name] = (runtime, count, at)
            period = max(100, int(getattr(graph, "scan_ms", 1000)))
            stopped = paused or bool(getattr(runtime, "is_debug_paused", False))
            rows.append((name, at, period, stopped))
        self._observed = {key: value for key, value in self._observed.items() if key in seen}
        if not rows:
            return SourceState()
        period = max(row[2] for row in rows)
        last = min((row[1] for row in rows if row[1] is not None), default=None)
        if all(row[3] for row in rows):
            return SourceState("PAUSED", last, period, "Controller execution is paused")
        stale = [row[0] for row in rows if row[1] is not None
                 and now - row[1] > max(2.0, 3 * row[2] / 1000)]
        waiting = [row[0] for row in rows if row[1] is None]
        paused_names = [row[0] for row in rows if row[3]]
        if stale:
            return SourceState("STALE", last, period, "No new scan: " + ", ".join(stale))
        if waiting:
            return SourceState("", None, period, "Waiting for scan: " + ", ".join(waiting))
        if paused_names:
            return SourceState("PARTLY PAUSED", last, period, ", ".join(paused_names))
        return SourceState("LIVE", last, period, f"Completed scans from {len(rows)} online modules")
