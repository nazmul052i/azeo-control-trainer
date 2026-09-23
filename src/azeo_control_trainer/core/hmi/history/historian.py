"""Continuous historian — the collection half of Process History View.

Azeo collects configured parameters continuously and Process History View
draws them; launching a trend from a faceplate opens a default chart for that
module type. This is the same shape, scaled to a trainer: a set of configured
points, a ring buffer each, and a query the existing
:class:`HistorianTrendWidget` can already read.

It deliberately implements that widget's expectations — a ``TAGS`` mapping and
``get_series(tag) -> (times, values)`` — rather than inventing a second trend
API for the repo to carry.

What gets collected is **derived, not authored**, the same principle as the tag
database: point the historian at the loaded modules and it configures itself
from what is actually there. An engineer can still add or drop points, but the
default is never stale.
"""
from __future__ import annotations

import csv
import io
import logging
import json
import math
import time
from collections import deque
from bisect import bisect_left, bisect_right
from itertools import islice
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass, field
import uuid

import numpy as np

log = logging.getLogger("hmi.historian")

#: Sample period. Azeo's continuous historian defaults to one second, which
#: is also the resolution the store's own trend buffers assume.
DEFAULT_PERIOD_S = 1.0

#: One hour at one sample a second, matching ``SharedDataStore.TREND_LENGTH``.
DEFAULT_CAPACITY = 3600

# Azeo Operator Station charts accept at most ten pens. Keeping the limit in the
# Qt-free history service gives every launcher and view one contract.
MAX_CHART_PENS = 10

#: Pen colours, in assignment order. High-contrast against the dark trend
#: ground, and distinguishable for the common colour-vision deficiencies —
#: no red/green pair adjacent.
PEN_COLOURS = (
    "#4FC3F7",  # cyan
    "#FFD54F",  # amber
    "#81C784",  # green
    "#F06292",  # pink
    "#BA68C8",  # purple
    "#FF8A65",  # orange
    "#90A4AE",  # grey-blue
    "#FFF176",  # pale yellow
    "#64B5F6",  # blue
    "#DCE775",  # lime
)

#: Terminals worth collecting per block type, in the order a default chart
#: should show them. This is what "a default chart for that module type" means.
DEFAULT_TERMINALS = {
    "PID": ("PV", "SP", "OUT"),
    "AI": ("OUT",),
    "AO": ("OUT",),
    "DEVCTL": ("STATE",),
    "MOTOR_INTERLOCK": ("STATE",),
}


@dataclass
class HistoryPoint:
    """One collected point."""

    path: str
    label: str = ""
    unit: str = ""
    lo: float = 0.0
    hi: float = 100.0
    colour: str = PEN_COLOURS[0]
    module: str = ""
    block_type: str = ""
    point_id: str = ""
    configuration: dict = field(default_factory=dict)
    active: bool = True
    legacy_path: str = ""
    times: deque = field(default_factory=lambda: deque(maxlen=DEFAULT_CAPACITY))
    values: deque = field(default_factory=lambda: deque(maxlen=DEFAULT_CAPACITY))
    qualities: deque = field(
        default_factory=lambda: deque(maxlen=DEFAULT_CAPACITY))
    wall_times: deque = field(default_factory=lambda: deque(maxlen=DEFAULT_CAPACITY))
    sim_times: deque = field(default_factory=lambda: deque(maxlen=DEFAULT_CAPACITY))
    runs: deque = field(default_factory=lambda: deque(maxlen=DEFAULT_CAPACITY))
    sample_units: deque = field(default_factory=lambda: deque(maxlen=DEFAULT_CAPACITY))
    metadata_conflict: bool = False
    version: int = 0
    _cache: tuple | None = field(default=None, repr=False)
    _cache_context: tuple | None = field(default=None, repr=False)
    _range_cache: tuple | None = field(default=None, repr=False)
    _last_good: tuple | None = field(default=None, repr=False)

    def sample(self, t: float, value, quality: str = "GOOD", *, wall_time=None,
               sim_time=None, run="") -> None:
        """Record a value; unreadable samples remain visible as gaps."""
        if run and self.runs and self.runs[-1] and run != self.runs[-1]:
            self.sample(math.nextafter(t, -math.inf), math.nan, "BAD", wall_time=wall_time,
                        sim_time=sim_time, run=self.runs[-1])
        try:
            number = 1.0 if isinstance(value, bool) else float(value)
        except (TypeError, ValueError):
            # A missing/bad sample must break the curve. Coercing it to zero
            # draws a process event that never happened.
            number = math.nan
            quality = "BAD"
        if not math.isfinite(number):
            quality = "BAD"
        quality = str(getattr(quality, "name", quality) or "BAD").upper()
        if quality not in {"GOOD", "UNCERTAIN"}:
            number, quality = math.nan, "BAD"
        self.times.append(t)
        self.values.append(number)
        self.qualities.append(quality)
        self.wall_times.append(wall_time)
        self.sim_times.append(sim_time)
        self.runs.append(run)
        self.sample_units.append(self.unit)
        if quality == "GOOD" and math.isfinite(number):
            self._last_good = (t, wall_time)
        self.version += 1
        self._cache = None
        self._range_cache = None

    def arrays(self):
        context = (self.unit, self.metadata_conflict)
        if self._cache is None or self._cache_context != context:
            self._cache = self.range_arrays()
            self._cache_context = context
        return self._cache

    def range_arrays(self, start_min=None, end_min=None, *, neighbours=False):
        """Copy only the requested raw interval from the retained ring buffer.

        Walking from the nearer deque end keeps a live 20-minute window cheap
        after a day of collection. One range cache per point bounds memory.
        Neighbours are for line clipping only; statistics never include them.
        """
        key = (self.version, self.unit, self.metadata_conflict, start_min, end_min, neighbours)
        if self._range_cache is not None and self._range_cache[0] == key:
            return self._range_cache[1]
        length = len(self.times)
        left = 0 if start_min is None else bisect_left(self.times, start_min, key=lambda t: t / 60)
        right = length if end_min is None else bisect_right(self.times, end_min, key=lambda t: t / 60)
        if neighbours:
            left, right = max(0, left - 1), min(length, right + 1)
        right = max(left, right)

        def array(data, dtype):
            if left > length - right:
                return np.asarray(list(islice(reversed(data), length - right, length - left)), dtype=dtype)[::-1].copy()
            return np.asarray(list(islice(data, left, right)), dtype=dtype)

        values = array(self.values, float)
        if self.metadata_conflict:
            values[:] = math.nan
        elif len(self.sample_units) == length:
            values[array(self.sample_units, str) != self.unit] = math.nan
        result = (array(self.times, float) / 60, values, array(self.qualities, str))
        self._range_cache = (key, result)
        return result


class ContinuousHistorian:
    """Collects configured points into ring buffers."""

    def __init__(self, store, resolver=None, period_s: float = DEFAULT_PERIOD_S,
                 capacity: int = DEFAULT_CAPACITY, *, archive_path=None,
                 clock_context=None):
        self._store = store
        self._resolver = resolver
        self._period_s = float(period_s)
        self._capacity = int(capacity)
        self._points: dict[str, HistoryPoint] = {}
        self._identity_points: dict[str, HistoryPoint] = {}
        self._chart_states: dict[str, dict] = {}
        self._t0 = time.perf_counter()
        self._last_sample = -math.inf
        self._samples = 0
        self.archive = None
        self.clock_context = clock_context
        self.origin = time.time()
        self._offset = 0.0
        self.run = uuid.uuid4().hex
        self.events = deque(maxlen=10000)
        self.event_version = 0
        self._point_changes = {}
        self._statistics_cache = {}
        self._last_context = None
        self._mode_values = {}
        self._last_time = 0.0
        self.read_only = False
        self.reduced = False
        if archive_path is not None:
            from .archive import HistoryArchive
            self.archive = HistoryArchive(archive_path)
            self.origin = self.archive.origin
            self._offset = time.time() - self.origin
            self._chart_states = deepcopy(self.archive.workspaces)
            for path, metadata in self.archive.points.items():
                self.add_point(path, **metadata)
            self.add_event("history", "Station history opened")

    def now(self):
        return self._offset + time.perf_counter() - self._t0

    @staticmethod
    def point_metadata(point):
        metadata = {key: getattr(point, key) for key in
                    ("label", "unit", "lo", "hi", "module", "block_type")}
        if point.point_id:
            metadata.update(point_id=point.point_id, configuration=deepcopy(point.configuration),
                            canonical_path=point.path, active=point.active)
        if point.legacy_path:
            metadata.update(legacy_path=point.legacy_path, active=False)
        return metadata

    def _remember_point(self, point):
        for alias, candidate in self._points.items():
            if candidate is point:
                self._point_changes[alias] = self.point_metadata(point)

    def point_key(self, identity):
        point = self._identity_points.get(identity)
        if point is None:
            return ""
        return point.path if self._points.get(point.path) is point else "@history:" + identity

    def available_points(self):
        """One selectable entry per identity, including explicitly retired history."""
        return {path: point for path, point in self._points.items()
                if path == (self.point_key(point.point_id) if point.point_id else point.path)}

    # ---------------------------------------------------- configuration
    @property
    def TAGS(self) -> dict:                                 # noqa: N802
        """Configured points. Upper-case to match the trend widget's use."""
        return self._points

    @property
    def sample_count(self) -> int:
        return self._samples

    def add_point(self, path: str, label: str = "", unit: str = "",
                  lo: float = 0.0, hi: float = 100.0, module: str = "",
                  block_type: str = "", *, point_id="", configuration=None,
                  canonical_path="", active=True, legacy_path="") -> HistoryPoint:
        existing = self._points.get(path)
        if existing is not None and not point_id:
            return existing
        if point_id and existing is not None and not existing.point_id:
            # Old samples have no repository provenance. Keep their address
            # history separately instead of silently attributing it to this ID.
            alias = "@legacy:" + path
            existing.path, existing.legacy_path, existing.active = alias, path, False
            self._points[alias] = existing
            for key, state in tuple(self._chart_states.items()):
                if path in state.get("pens", []) and path not in state.get("point_ids", {}):
                    remapped = deepcopy(state)
                    remapped["pens"] = [alias if p == path else p for p in remapped["pens"]]
                    for field in ("visible", "colours", "pen_styles", "scales", "axes"):
                        if path in remapped.get(field, {}):
                            remapped[field][alias] = remapped[field].pop(path)
                    self.save_chart_state(key, remapped)
            self._points.pop(path)
            self._remember_point(existing)
        canonical = canonical_path or path
        identified = self._identity_points.get(point_id) if point_id else None
        if identified is not None:
            previous_path = identified.path
            identified.path, identified.active = canonical, bool(active)
            identified.label, identified.module = label or canonical, module
            identified.unit, identified.lo, identified.hi = unit, lo, hi
            identified._cache = None
            identified.version += 1
            identified.configuration = deepcopy(configuration or {})
            self._points[path] = identified
            if active:
                self._points[canonical] = identified
            self._remember_point(identified)
            if previous_path != canonical:
                self.add_event("configuration", "Recorded point renamed", canonical,
                               {"previous_path": previous_path, "point_id": point_id,
                                "configuration": identified.configuration})
            return identified
        point = HistoryPoint(
            path=canonical, label=label or canonical, unit=unit, lo=lo, hi=hi,
            colour=PEN_COLOURS[len(self._points) % len(PEN_COLOURS)],
            module=module, block_type=block_type,
            point_id=point_id, configuration=deepcopy(configuration or {}), active=bool(active),
            legacy_path=legacy_path,
        )
        point.times = deque(maxlen=self._capacity)
        point.values = deque(maxlen=self._capacity)
        point.qualities = deque(maxlen=self._capacity)
        point.wall_times = deque(maxlen=self._capacity)
        point.sim_times = deque(maxlen=self._capacity)
        point.runs = deque(maxlen=self._capacity)
        point.sample_units = deque(maxlen=self._capacity)
        self._points[path] = point
        if point_id:
            self._identity_points[point_id] = point
            self._points["@history:" + point_id] = point
            if active:
                self._points[canonical] = point
        self._remember_point(point)
        return point

    def remove_point(self, path: str) -> bool:
        point = self._points.get(path)
        if point is None:
            return False
        if point.point_id:
            # Retain its query key: a saved chart must never inherit a replacement.
            point.active = False
            self._remember_point(point)
        else:
            self._points.pop(path, None)
        return True

    def configure_from(self, tagdb, graphs=None) -> int:
        """Configure from the loaded modules. Returns the point count.

        Collects every field I/O point, plus the terminals that make a module's
        default chart — a PID's PV, SP and OUT; a device's state. That is a
        few dozen points on the shipped area, not the whole 1,377.
        """
        if tagdb is not None:
            for tag, paths in tagdb.field_tags().items():
                entry = next((tagdb.lookup(p) for p in paths), None)
                if entry is None:
                    continue
                lo, hi = _scale_for(entry, tagdb)
                self.add_point(tag, label=entry.description or tag,
                               unit=entry.unit, lo=lo, hi=hi,
                               module=entry.module,
                               block_type=entry.block_type)

        from ...configuration.identities import graph_point_context
        configured_ids = set()
        requested = {}
        for point in self._identity_points.values():
            context = point.configuration
            key = (context.get("project_id"), context.get("object_id"), context.get("block_id"))
            member = str(context.get("member", ""))
            requested.setdefault(key, set()).add(("CONFIG/" if context.get("kind") == "parameter" else "") + member)
        repository_graphs = False
        for graph in (graphs or []):
            repository_graphs |= bool(getattr(graph, "_configuration_identity", None))
            module = graph.name or "UNNAMED"
            for block in graph.blocks.values():
                wanted = list(DEFAULT_TERMINALS.get(block.block_type, ()))
                source = getattr(graph, "_configuration_identity", {})
                key = (source.get("project_id"), source.get("object_id"), block.id)
                wanted += sorted(requested.get(key, set()) - set(wanted) - {""})
                if not wanted:
                    continue
                name = block.instance_name or block.id
                for terminal in wanted:
                    if terminal.startswith("CONFIG/"):
                        member = terminal[7:]
                        if member in block.config.params or member in block.get_config_schema():
                            context = graph_point_context(graph, block, member, "parameter")
                            if context:
                                configured_ids.add(context["point_id"])
                                previous = self._identity_points[context["point_id"]]
                                self.add_point(f"{module}/{name}/{terminal}", label=member, module=module,
                                               unit=previous.unit, lo=previous.lo, hi=previous.hi,
                                               block_type=block.block_type, **context)
                        continue
                    if terminal not in block.outputs and terminal not in block.inputs:
                        continue
                    path = f"{module}/{name}/{terminal}"
                    params = block.config.params
                    signal = block.outputs.get(terminal) or block.inputs[terminal]
                    output = block.block_type == "PID" and terminal == "OUT"
                    unit = getattr(signal, "units", "") or (
                        "%" if output else str(params.get("pv_unit", "") or params.get("eng_units", "") or ""))
                    span = getattr(signal, "eu_range", None) or (
                        (0.0, 100.0) if output else (
                            float(params.get("pv_scale_lo", 0.0) or 0.0),
                            float(params.get("pv_scale_hi", 100.0) or 100.0)))
                    context = graph_point_context(graph, block, terminal)
                    if context:
                        configured_ids.add(context["point_id"])
                    self.add_point(
                        path,
                        label=f"{module} {terminal}",
                        unit=unit, lo=span[0], hi=span[1],
                        module=module, block_type=block.block_type, **context,
                    )
        if graphs is not None and (repository_graphs or self._identity_points):
            for identity, point in self._identity_points.items():
                point.active = identity in configured_ids
                self._remember_point(point)
        log.info("Historian configured with %d points", len(self._points))
        return len(self._points)

    # --------------------------------------------------------- collection
    def collect(self, now: float | None = None, force: bool = False) -> int:
        """Sample every configured point. Returns how many were written."""
        if self.read_only:
            return 0
        t = self.now() if now is None else now
        if not force and (t - self._last_sample) < self._period_s:
            return 0
        # A detached history window can collect before the station timer does.
        # Own the inventory scope here so that path does not enumerate every
        # module again for every point. Values and quality are still read live.
        with getattr(self._resolver, "snapshot", nullcontext)():
            return self._collect_sample(t)

    def _collect_sample(self, t: float) -> int:
        self._last_sample = t
        self._last_time = t
        wall_time = self.origin + t
        try:
            context = self.clock_context() if self.clock_context else {}
        except Exception:  # noqa: BLE001 - history must survive a provider disconnect
            log.exception("Simulation clock unavailable to historian")
            context = {}
        sim_time = context.get("sim_time")
        self._observe_clock(context, t)

        data = self._store.get_all() if self._store is not None else {}
        written = 0
        samples = []
        contexts = []
        for point in {id(point): point for point in self._points.values()}.values():
            if not point.active:
                continue
            path = point.path
            value = data.get(path)
            result = None
            quality = "GOOD"
            missing = value is None
            if value is None and self._resolver is not None:
                result = self._resolver.read(path)
                if isinstance(result, tuple):
                    value, raw_quality, missing = result
                    quality = getattr(raw_quality, "name", raw_quality)
                else:
                    value = getattr(result, "value", None)
                    quality = getattr(getattr(result, "quality", None),
                                      "name", "BAD")
                    missing = value is None or quality == "BAD"
                quality_name = str(quality or "BAD").upper()
                if quality_name not in {"GOOD", "UNCERTAIN"}:
                    missing = True
            if missing:
                value = math.nan
                quality = "BAD"
            point.sample(t, value, str(quality), wall_time=wall_time, sim_time=sim_time, run=self.run)
            if self.archive is not None:
                samples.append((path, t, float(point.values[-1]) if math.isfinite(point.values[-1]) else None,
                                point.qualities[-1], wall_time, sim_time, self.run))
                if point.point_id:
                    configuration = {**point.configuration, "unit": point.unit,
                                     "lo": point.lo, "hi": point.hi, "label": point.label}
                    contexts.append((path, t, point.point_id, json.dumps(configuration, sort_keys=True)))
            if result is not None and path.endswith("/PV") and not isinstance(result, tuple):
                # Mode changes matter even when the PV barely moves. The same
                # read supplies mode metadata; no second controller read is needed.
                mode = getattr(result, "mode_actual", "")
                if mode:
                    previous = self._mode_values.get(path)
                    if previous is not None and previous != mode:
                        self.add_event("mode", "Mode changed", path.rsplit("/", 1)[0],
                                       {"before": previous, "after": mode}, t=t, sim_time=sim_time)
                    self._mode_values[path] = mode
            written += 1
        if written:
            self._samples += 1
        if self.archive is not None and (samples or self._point_changes):
            try:
                self.archive.append(dict(self._point_changes), samples, [], contexts=contexts)
                self._point_changes.clear()
            except RuntimeError:
                log.exception("Live history retained in memory; disk collection unavailable")
        return written

    # ------------------------------------------------------------ query
    def get_series(self, tag: str):
        """``(times_in_minutes, values)`` — the trend widget's contract."""
        point = self._points.get(tag)
        if point is None or not point.times:
            return np.array([]), np.array([])
        return point.arrays()[:2]

    def get_quality_series(self, tag: str) -> np.ndarray:
        """Return quality names aligned one-for-one with ``get_series``."""
        point = self._points.get(tag)
        if point is None or not point.qualities:
            return np.array([], dtype=str)
        return point.arrays()[2]

    def series_bounds(self, tag):
        point = self._points.get(tag)
        return (point.times[0] / 60, point.times[-1] / 60) if point and point.times else None

    def get_plot_series(self, tag, start_min=None, end_min=None, *, budget=2400):
        from .plot_data import plot_indices

        point = self._points.get(tag)
        if point is None:
            return np.array([]), np.array([]), np.array([], dtype=str)
        times, values, qualities = point.range_arrays(start_min, end_min, neighbours=True)
        indices = plot_indices(values, qualities, budget)
        return times[indices], values[indices], qualities[indices]

    def statistics(self, tag: str, start_min: float | None = None,
                   end_min: float | None = None) -> dict[str, float | int]:
        """Finite-value statistics for a visible chart interval."""
        if self.reduced:
            bounds = getattr(self, "_archive_bounds", (math.inf, -math.inf))
            start, end = start_min, end_min
            if ((start is None or math.isclose(start * 60, bounds[0], abs_tol=.001))
                    and (end is None or math.isclose(end * 60, bounds[1], abs_tol=.001))):
                if tag in self._archive_statistics:
                    return dict(self._archive_statistics[tag])
            # An envelope overweights extrema. Leave measurements unavailable
            # until the background query supplies raw statistics for this range.
            return dict(current=math.nan, minimum=math.nan, maximum=math.nan,
                        average=math.nan, delta=math.nan, count=0)
        point = self._points.get(tag)
        key = (id(point), point.version if point else -1,
               point.unit if point else None, point.metadata_conflict if point else None,
               start_min, end_min)
        cached = self._statistics_cache.get(tag)
        if cached is not None and cached[0] == key:
            return dict(cached[1])
        _, values, qualities = point.range_arrays(start_min, end_min) if point else (
            np.array([]), np.array([]), np.array([], dtype=str))
        current = float(values[-1]) if values.size else math.nan
        finite = values[np.isfinite(values) & (qualities == "GOOD")]
        if not finite.size:
            result = {
                "current": current, "minimum": math.nan,
                "maximum": math.nan, "average": math.nan,
                "delta": math.nan, "count": 0,
            }
        else:
            result = {
                "current": current,
                "minimum": float(np.min(finite)),
                "maximum": float(np.max(finite)),
                "average": float(np.mean(finite)),
                "delta": float(finite[-1] - finite[0]),
                "count": int(finite.size),
            }
        self._statistics_cache[tag] = (key, result)
        return dict(result)

    def nearest_values(
        self, paths: list[str], time_min: float,
    ) -> dict[str, tuple[float, str, float]]:
        """Return each pen's nearest value, quality and actual sample time."""
        answer: dict[str, tuple[float, str, float]] = {}
        target = float(time_min)
        for path in paths:
            point = self._points.get(path)
            if point is None or not point.times:
                continue
            times = point.times
            if target < times[0] / 60 or target > times[-1] / 60:
                continue
            index = bisect_left(times, target, key=lambda t: t / 60)
            candidates = [i for i in (index - 1, index) if 0 <= i < len(times)]
            nearest = min(candidates, key=lambda i: abs(times[i] / 60 - target))
            if abs(times[nearest] / 60 - target) * 60 > max(2.0, self._period_s * 2):
                continue
            qualities = point.qualities
            quality = str(qualities[nearest]) if nearest < len(qualities) else "BAD"
            value = point.values[nearest]
            if point.metadata_conflict or (len(point.sample_units) == len(times)
                                          and point.sample_units[nearest] != point.unit):
                value = math.nan
            answer[path] = (float(value), quality, times[nearest] / 60)
        return answer

    def span_s(self) -> float:
        """Seconds of history held, across all points."""
        starts, ends = [], []
        for point in self._points.values():
            if point.times:
                starts.append(point.times[0])
                ends.append(point.times[-1])
        return (max(ends) - min(starts)) if starts else 0.0

    def default_pens_for(self, module: str) -> list[str]:
        """The points a default chart for ``module`` should show.

        This is what launching a trend from a faceplate opens.
        """
        candidates = {p: pt for p, pt in self.available_points().items() if pt.active}
        exact = [p for p, pt in candidates.items() if pt.module == module]
        if not exact:
            exact = [p for p in candidates if p.startswith(f"{module}/")
                     or p.startswith(f"{module}.")]
        controllers = [path for path in exact if self._points[path].block_type == "PID"]
        if controllers:
            # A loop opens on PV/SP/OUT. Its transmitter and valve remain
            # available through Add Pen without forcing mixed-unit normalization.
            exact = controllers

        def order(path: str) -> tuple:
            point = self._points[path]
            wanted = DEFAULT_TERMINALS.get(point.block_type, ())
            terminal = path.rsplit("/", 1)[-1]
            return (wanted.index(terminal) if terminal in wanted else 99, path)

        return sorted(exact, key=order)[:MAX_CHART_PENS]

    def save_chart_state(self, key: str, state: dict) -> None:
        """Persist operator chart configuration for this application session."""
        if key:
            state = deepcopy(state)
            identities = {path: self._points[path].point_id for path in state.get("pens", [])
                          if path in self._points and self._points[path].point_id}
            if identities:
                state["point_ids"] = identities
            self._chart_states[key] = state
            if self.archive is not None:
                try:
                    self.archive.save_workspace(key, state)
                except (RuntimeError, OSError) as error:
                    # Closing a tool must remain safe even if its disk writer
                    # has failed. Keep the session state and surface the error.
                    self.archive.error = str(error)
                    log.exception("History workspace retained only in memory")

    def chart_state(self, key: str) -> dict | None:
        """Return an isolated copy so callers cannot mutate stored state."""
        state = self._chart_states.get(key)
        if state is None:
            return None
        state = deepcopy(state)
        mapped = {path: self.point_key(identity) or path for path, identity in state.get("point_ids", {}).items()}
        if "pens" in state:
            state["pens"] = [mapped.get(path, path) for path in state["pens"]]
        for key in ("visible", "colours", "scales", "axes", "pen_styles", "point_ids"):
            if isinstance(state.get(key), dict):
                state[key] = {mapped.get(path, path): value for path, value in state[key].items()}
        return state

    def latest(self, path, *, at=None):
        point = self._points.get(path)
        if point is None or not point.times:
            return {"value": math.nan, "quality": "NO DATA", "age": None, "last_good": None}
        last_good = (point._last_good[1] if point._last_good[1] is not None else self.origin + point._last_good[0]) if point._last_good else None
        age = max(0.0, (self.now() if at is None else at) - point.times[-1])
        quality = point.qualities[-1]
        if not self.read_only and age > max(3.0, self._period_s * 3):
            quality = "STALE"
        return {"value": point.values[-1], "quality": quality, "age": age,
                "time": point.times[-1], "wall_time": point.wall_times[-1],
                "sim_time": point.sim_times[-1], "last_good": last_good}

    def add_event(self, category, action, target="", detail="", *, t=None, sim_time=None):
        t = self.now() if t is None else float(t)
        row = {"id": uuid.uuid4().hex, "time": t, "wall_time": self.origin + t,
               "sim_time": sim_time, "category": category, "action": action,
               "target": target, "detail": deepcopy(detail), "run": self.run}
        self.events.append(row)
        self.event_version += 1
        if self.archive is not None:
            try:
                self.archive.append({}, [], [row])
            except RuntimeError:
                log.exception("Could not archive historian event")
        return row

    def _observe_clock(self, context, t):
        previous = self._last_context
        if previous is not None:
            if context.get("paused") != previous.get("paused"):
                self.add_event("clock", "Simulation paused" if context.get("paused") else "Simulation resumed",
                               t=t, sim_time=context.get("sim_time"))
            before, after = previous.get("sim_time"), context.get("sim_time")
            if before is not None and after is not None and after < before:
                self.run = uuid.uuid4().hex
                self.add_event("clock", "Simulation time reset / snapshot restored",
                               detail={"before": before, "after": after}, t=t, sim_time=after)
        self._last_context = dict(context)

    def query_async(self, paths, start_min, end_min, *, raw=False, as_snapshot=False):
        if self.archive is None:
            raise ValueError("Disk history is not configured for this station")
        paths = tuple(paths)
        metadata = {path: self.point_metadata(self._points[path]) for path in paths if path in self._points}
        transform = None
        if as_snapshot:
            origin = self.origin
            # Only immutable captured configuration crosses to the read worker.
            # A download during the query must not relabel its recorded values.
            def transform(result):
                return ContinuousHistorian._snapshot_from(result, metadata, origin)
        return self.archive.query(paths, start_min * 60, end_min * 60, 0 if raw else 12000,
                                  transform, metadata=metadata)

    def snapshot(self, result):
        metadata = {path: self.point_metadata(self._points[path]) for path in result["samples"] if path in self._points}
        return self._snapshot_from(result, metadata, self.origin)

    @staticmethod
    def _snapshot_from(result, captured_metadata, origin):
        snapshot = ContinuousHistorian(None, capacity=100000)
        snapshot.read_only = True
        snapshot.origin = origin
        snapshot.reduced = result.get("reduced", False)
        snapshot._archive_bounds = (result.get("start", 0), result.get("end", 0))
        snapshot._archive_statistics = result.get("statistics", {})
        snapshot.events.extend(result.get("events", []))
        snapshot.event_version = len(snapshot.events)
        snapshot.provenance = deepcopy(result.get("provenance", {}))
        loaded = set()
        for path, rows in result["samples"].items():
            metadata = dict(captured_metadata.get(path, {}))
            epochs = result.get("provenance", {}).get(path, [])
            units = {epoch["configuration"].get("unit", "") for epoch in epochs}
            if epochs:
                metadata["unit"] = next(iter(units)) if len(units) == 1 else "Mixed units"
                metadata["lo"] = min(epoch["configuration"].get("lo", metadata.get("lo", 0)) for epoch in epochs)
                metadata["hi"] = max(epoch["configuration"].get("hi", metadata.get("hi", 100)) for epoch in epochs)
            point = snapshot.add_point(path, **metadata)
            point.metadata_conflict = len(units) > 1
            if point.metadata_conflict:
                snapshot._archive_statistics[path] = dict(current=math.nan, minimum=math.nan, maximum=math.nan,
                                                           average=math.nan, delta=math.nan, count=0)
            if id(point) in loaded:
                continue
            loaded.add(id(point))
            for t, value, quality, wall, sim, run in rows:
                point.sample(t, value, quality, wall_time=wall, sim_time=sim, run=run)
        return snapshot

    def merge_live(self, historian):
        for path, point in self.available_points().items():
            current = (historian._identity_points.get(point.point_id) if point.point_id
                       else historian.TAGS.get(path))
            if current is None or not current.times:
                continue
            last = point.times[-1] if point.times else -math.inf
            if current.times[-1] <= last:
                continue
            start = bisect_right(current.times, last)
            for i in range(start, len(current.times)):
                if current.sample_units[i] != point.unit:
                    continue
                value = math.nan if current.metadata_conflict else current.values[i]
                point.sample(current.times[i], value, current.qualities[i], wall_time=current.wall_times[i],
                             sim_time=current.sim_times[i], run=current.runs[i])
        known = {row["id"] for row in self.events}
        rows = [row for row in historian.events if row["id"] not in known]
        self.events.extend(rows)
        self.event_version += len(rows)

    def close(self):
        if self.archive is not None:
            self.archive.close()

    def to_csv(self, paths: list[str] | None = None) -> str:
        """Export aligned samples without joining unrelated sample indices."""
        chosen = [p for p in (paths or list(self._points)) if p in self._points]
        if not chosen:
            return ""
        timeline = sorted({float(t) for path in chosen
                           for t in self._points[path].times})
        samples = {
            path: {float(t): float(value) for t, value in zip(
                self._points[path].times, self._points[path].values)}
            for path in chosen
        }
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(["time_min", *chosen])
        for timestamp in timeline:
            row = [f"{timestamp / 60.0:.4f}"]
            for path in chosen:
                value = samples[path].get(timestamp, math.nan)
                row.append(f"{value:g}" if math.isfinite(value) else "")
            writer.writerow(row)
        return output.getvalue()


def _scale_for(entry, tagdb) -> tuple[float, float]:
    """Best available engineering range for a field point."""
    if entry.data_type == "BOOL":
        return 0.0, 1.0
    # The owning I/O block's own scale parameters are already in the tag
    # database, so the range comes from the configuration rather than a guess.
    lo = tagdb.lookup(f"{entry.module}/{entry.block}/scale_lo")
    hi = tagdb.lookup(f"{entry.module}/{entry.block}/scale_hi")
    try:
        return (float(lo.value) if lo else 0.0,
                float(hi.value) if hi else 100.0)
    except (TypeError, ValueError):
        return 0.0, 100.0
