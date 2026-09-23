"""Repeatable exercises coordinated through the existing simulation service."""
from __future__ import annotations

from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass, field
from html import escape
import json
import logging
import math
from pathlib import Path
import sqlite3
import time
import uuid

from .workbench import _write_json


@dataclass
class Exercise:
    name: str = "Flow-loop troubleshooting"
    loop: str = ""
    input_path: str = ""
    objectives: list[str] = field(default_factory=lambda: [
        "Confirm the operating mode and normal PV/SP/OUT response",
        "Identify the disturbed or failed input using trend and quality evidence",
        "Explain the control response and record the corrective action",
        "Restore the input and verify stable operation",
    ])
    snapshot: str = ""
    paths: list[str] = field(default_factory=list)
    configuration: dict = field(default_factory=dict)

    def validate(self):
        if not self.name.strip() or not self.objectives or not self.loop:
            raise ValueError("An exercise needs a name, loop and objectives")
        if not self.snapshot or not Path(self.snapshot).is_file():
            raise ValueError("Capture or select a starting snapshot first")


class SessionArchive:
    """Persistent samples and events indexed by a session, beside the journal."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS training_sessions (
                    id TEXT PRIMARY KEY, started REAL, title TEXT, metadata TEXT);
                CREATE TABLE IF NOT EXISTS training_samples (
                    session TEXT, time REAL, data TEXT);
                CREATE INDEX IF NOT EXISTS training_sample_time
                    ON training_samples(session, time);
                CREATE TABLE IF NOT EXISTS training_events (
                    id INTEGER PRIMARY KEY, session TEXT, time REAL,
                    action TEXT, target TEXT, data TEXT);
                CREATE INDEX IF NOT EXISTS training_event_time
                    ON training_events(session, time);
            """)

    @contextmanager
    def connect(self):
        # sqlite's transaction context does not close its Windows file handle.
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            with db:
                yield db

    def save(self, identity, metadata):
        with self.connect() as db:
            db.execute("INSERT INTO training_sessions VALUES (?, ?, ?, ?) "
                       "ON CONFLICT(id) DO UPDATE SET metadata=excluded.metadata",
                       (identity, metadata["started"], metadata["exercise"]["name"],
                        json.dumps(metadata, allow_nan=False)))

    def append(self, identity, elapsed, samples, events):
        with self.connect() as db:
            if samples is not None:
                db.execute("INSERT INTO training_samples VALUES (?, ?, ?)",
                           (identity, elapsed, json.dumps(samples, allow_nan=False)))
            db.executemany("INSERT INTO training_events "
                           "(session, time, action, target, data) VALUES (?, ?, ?, ?, ?)",
                           [(identity, row.get("time", elapsed), row["action"],
                             row.get("target", ""), json.dumps(row, default=str))
                            for row in events])

    def sessions(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT id, started, title FROM training_sessions ORDER BY started DESC")]

    def read(self, identity):
        with self.connect() as db:
            row = db.execute("SELECT metadata FROM training_sessions WHERE id=?",
                             (identity,)).fetchone()
            if row is None:
                raise ValueError("Training session does not exist")
            data = json.loads(row[0])
            data["samples"] = [{"time": r["time"], "values": json.loads(r["data"])}
                               for r in db.execute("SELECT * FROM training_samples "
                                                   "WHERE session=? ORDER BY time", (identity,))]
            data["events"] = [dict(json.loads(r["data"]), time=r["time"])
                              for r in db.execute("SELECT * FROM training_events "
                                                  "WHERE session=? ORDER BY time, id", (identity,))]
        return data


class TrainingSession:
    def __init__(self, workbench, source, alarms, root):
        self.workbench, self.source, self.alarms = workbench, source, alarms
        self.root = Path(root)
        self.archive = SessionArchive(self.root / "sessions.sqlite")
        self.identity = ""
        self.metadata = {}
        self.exercise = None
        self.active = False
        self._last_sample = -1.0
        self._alarm_cursor = 0
        self._journal_cursor = 0
        self._last_values = {}
        self._fault_before = {}
        self._config_values = {}
        self._input_paths = set()
        self._recorded_paths = []
        self.history_event_sink = None

    def save_exercise(self, exercise):
        exercise.validate()
        baseline = self.workbench.load_snapshot_document(exercise.snapshot)
        if baseline.get("process") is None or not self.workbench.process_capabilities().get("snapshot_supported"):
            raise ValueError("The starting snapshot must contain a restorable process state")
        identity = uuid.uuid5(uuid.NAMESPACE_URL, exercise.name).hex
        return _write_json(self.root / "exercises" / f"{identity}.json", asdict(exercise))

    def exercises(self):
        return [Exercise(**json.loads(path.read_text(encoding="utf-8")))
                for path in sorted((self.root / "exercises").glob("*.json"))]

    def capture_baseline(self):
        if self.active:
            raise ValueError("Finish the current session before changing its baseline")
        caps = self.workbench.process_capabilities()
        if not caps.get("snapshot_supported"):
            raise ValueError("This provider cannot restore the process; training requires a process snapshot")
        path = self.root / "baselines" / f"{uuid.uuid4().hex}.json"
        return self.workbench.save_snapshot(path, name="Training starting condition")

    def start(self, exercise):
        if self.active:
            raise ValueError("Finish the current session before starting another")
        exercise.validate()
        self._check_configuration(exercise)
        baseline = self.workbench.load_snapshot_document(exercise.snapshot)
        if baseline.get("process") is None or not self.workbench.process_capabilities().get("snapshot_supported"):
            raise ValueError("The starting snapshot must contain a restorable process state")
        for suffix in ("PV", "SP", "OUT"):
            value = self.source.read(f"{exercise.loop}/{suffix}")
            if value is None or getattr(value, "value", None) is None:
                raise ValueError(f"The loop has no readable {suffix} parameter")
        input_paths = set()
        if exercise.input_path:
            input_paths.add(self.input_parameter(exercise.input_path))
        self.workbench.restore_snapshot(exercise.snapshot)
        self.exercise = exercise
        # Discovered first-out signals are recording scope, not authored
        # exercise content. Mutating the definition breaks baseline Restart.
        self._recorded_paths = list(exercise.paths)
        module = exercise.loop.partition("/")[0]
        for owner, block in self.workbench._blocks():
            if owner == module:
                for name in block.outputs:
                    if name.startswith("FIRST_OUT"):
                        path = f"{owner}/{block.instance_name}/{name}"
                        if path not in self._recorded_paths:
                            self._recorded_paths.append(path)
        self.identity = uuid.uuid4().hex
        self._origin = self.workbench._sim_time()
        self._last_sample = -1.0
        self._last_values = {}
        self._fault_before = {}
        self._config_values = {}
        self._input_paths = input_paths
        self._alarm_cursor = self.alarms.event_sequence
        rows = self.workbench.journal.rows()
        self._journal_cursor = rows[-1]["id"] if rows else 0
        self._prior_recording = self.workbench.recording
        self.workbench.recording = True
        self.metadata = {"started": time.time(), "exercise": asdict(exercise),
                         "status": "running", "clock": "simulation seconds",
                         "configuration": self.configuration_context(),
                         "objectives": {}, "notes": ""}
        self.archive.save(self.identity, self.metadata)
        self.active = True
        self.event("session_started", exercise.loop, exercise.name)
        self.tick()

    def elapsed(self):
        return max(0.0, self.workbench._sim_time() - self._origin)

    def _graphs(self):
        return [runtime.compiled.graph for runtime in self.workbench._runtimes()]

    def configuration_context(self):
        return {graph.name: dict(graph._configuration_identity) for graph in self._graphs()
                if getattr(graph, "_configuration_identity", None)}

    def _check_configuration(self, exercise):
        if not exercise.configuration:
            return
        import hashlib
        context = exercise.configuration
        if context.get("provider") and context["provider"] != getattr(self.workbench.driver, "_configuration_provider_identity", {}):
            raise ValueError("The installed training process differs from the baseline provider build")
        from ..configuration.release_manifest import fingerprint
        if context.get("exercise_hash") and context["exercise_hash"] != fingerprint(
                {key: getattr(exercise, key) for key in ("name", "loop", "input_path", "objectives", "paths")}):
            raise ValueError("The inherited exercise definition has changed; create a new baseline for that exercise")
        if hashlib.sha256(Path(exercise.snapshot).read_bytes()).hexdigest() != context["snapshot_hash"]:
            raise ValueError("The immutable exercise starting snapshot has changed")
        graphs = self._graphs()
        actual = {getattr(graph, "_configuration_identity", {}).get("object_id"):
                  getattr(graph, "_configuration_identity", {}).get("object_digest") for graph in graphs}
        if actual != context.get("modules") or any(
                getattr(graph, "_configuration_identity", {}).get("project_id") != context["project_id"] for graph in graphs):
            raise ValueError("Load the trainee baseline configuration before restoring its process starting condition")

    def event(self, action, target="", detail=""):
        if self.active:
            evidence = {"session_id": self.identity, "baseline": self.exercise.configuration,
                        "configuration": self.configuration_context(), "wall_time": time.time(),
                        "sim_time": self.workbench._sim_time()}
            self.archive.append(self.identity, self.elapsed(), None,
                                [{"action": action, "target": target, "detail": detail, **evidence}])
            if self.history_event_sink is not None:
                try:
                    self.history_event_sink(action, target, {"detail": detail, **evidence}, self.workbench._sim_time())
                except Exception:  # noqa: BLE001
                    logging.getLogger("hmi.historian").exception("Could not record training history event")

    def inject_input(self, path, value, quality="GOOD"):
        if not self.active:
            raise ValueError("Start a session before introducing a fault")
        module, separator, name = path.partition("/")
        if not separator:
            raise ValueError("Select an input block")
        block = self.workbench._find_block(module, name)
        if block.block_type not in {"AI", "DI"}:
            raise ValueError("Training faults target input blocks only")
        if block.block_type == "DI" and quality != "GOOD":
            raise ValueError("This discrete input supports value substitution only")
        if not math.isfinite(float(value)):
            raise ValueError("Enter a finite fault value")
        keys = ("simulate_enabled", "SIMULATE", "SIMULATE_STATUS_GOOD",
                "SIMULATE_D", "SIMULATE_VAL")
        self._fault_before.setdefault(path, {
            key: (key in block.config.params, block.config.params.get(key)) for key in keys})
        self.workbench.set_io_value(module, name, value, quality)
        self.workbench.set_block_simulation_enabled(module, name, True)
        self._input_paths.add(self.input_parameter(path))
        self.event("fault_started", path, {"value": value, "quality": quality})

    def input_parameter(self, path):
        module, _, name = path.partition("/")
        block = self.workbench._find_block(module, name)
        if block.block_type not in {"AI", "DI"}:
            raise ValueError("Choose an AI or DI input for the exercise")
        suffix = "OUT" if "OUT" in block.outputs else "OUT_D"
        return f"{path}/{suffix}"

    def clear_faults(self):
        for path, before in tuple(self._fault_before.items()):
            module, _, name = path.partition("/")
            block = self.workbench._find_block(module, name)
            for key, (present, value) in before.items():
                if present:
                    block.config.params[key] = value
                else:
                    block.config.params.pop(key, None)
            del self._fault_before[path]
            self.event("fault_cleared", path)

    def tick(self):
        if not self.active:
            return
        elapsed = self.elapsed()
        events = []
        alarm_cursor, journal_cursor = self._alarm_cursor, self._journal_cursor
        config_values, last_values = dict(self._config_values), dict(self._last_values)
        for row in self.alarms.events:
            if row["sequence"] > self._alarm_cursor:
                events.append({"action": "alarm_" + row["action"],
                               "target": row["key"], "detail": row})
                alarm_cursor = row["sequence"]
        for row in self.workbench.journal.rows(after_id=self._journal_cursor):
            events.append({"action": row["action"], "target": row["target"],
                           "detail": row["value"], "actor": row["actor"],
                           "time": max(0.0, row["sim_time"] - self._origin)})
            journal_cursor = row["id"]
        samples = None
        if elapsed - self._last_sample >= 1.0:
            from ..configuration.identities import context_for_path
            graphs = self._graphs()
            paths = list(dict.fromkeys([
                *(f"{self.exercise.loop}/{suffix}" for suffix in ("PV", "SP", "OUT")),
                *sorted(self._input_paths),
                *self._recorded_paths]))
            samples = {}
            for path in paths:
                result = self.source.read(path)
                value = getattr(result, "value", None)
                if isinstance(value, float) and not math.isfinite(value):
                    value = None
                if not isinstance(value, (str, int, float, bool, type(None))):
                    value = str(value)
                quality = getattr(getattr(result, "quality", None), "name", "BAD")
                samples[path] = {"value": value, "quality": quality,
                                 "wall_time": time.time(), "sim_time": self.workbench._sim_time(),
                                 **context_for_path(graphs, path),
                                 "units": getattr(result, "units", ""),
                                 "mode_target": getattr(result, "mode_target", ""),
                                 "mode_actual": getattr(result, "mode_actual", "")}
                if "/CONFIG/" in path:
                    if path in config_values and config_values[path] != value:
                        events.append({"action": "configuration_observed", "target": path,
                                       "detail": {"before": config_values[path], "after": value}})
                    config_values[path] = value
                elif "/FIRST_OUT" in path:
                    if config_values.get(path) != value:
                        events.append({"action": "first_out_observed", "target": path, "detail": value})
                    config_values[path] = value
                tracked = (samples[path]["mode_target"], samples[path]["mode_actual"])
                if path in last_values and last_values[path] != tracked:
                    events.append({"action": "mode_observed", "target": path,
                                   "detail": {"target": tracked[0], "actual": tracked[1]}})
                last_values[path] = tracked
        if samples is not None or events:
            context = self.configuration_context()
            if context != self.metadata.get("configuration", {}):
                events.append({"action": "release_configuration_observed", "target": self.exercise.loop,
                               "detail": {"before": self.metadata.get("configuration", {}), "after": context}})
                self.metadata["configuration"] = context
                self.archive.save(self.identity, self.metadata)
            for event in events:
                event.update(session_id=self.identity, wall_time=time.time(), configuration=context,
                             baseline=self.exercise.configuration, sim_time=self._origin + event.get("time", elapsed))
            self.archive.append(self.identity, elapsed, samples, events)
            self._alarm_cursor, self._journal_cursor = alarm_cursor, journal_cursor
            self._config_values, self._last_values = config_values, last_values
            if samples is not None:
                self._last_sample = elapsed

    def objective(self, index, complete, note=""):
        if not self.active or not 0 <= index < len(self.exercise.objectives):
            raise ValueError("Select an objective in the running exercise")
        self.metadata["objectives"][str(index)] = {"complete": bool(complete), "note": note}
        self.archive.save(self.identity, self.metadata)
        self.event("objective_reviewed", str(index), note)

    def finish(self, notes=""):
        if not self.active:
            return self.identity
        self.clear_faults()
        self.tick()
        self.event("session_finished", detail=notes)
        self.metadata.update(status="finished", notes=notes, duration=self.elapsed())
        self.archive.save(self.identity, self.metadata)
        self.active = False
        self.workbench.recording = self._prior_recording
        return self.identity

    def restart(self):
        exercise = self.exercise
        if exercise is None:
            raise ValueError("Start an exercise first")
        self.finish("Restarted from the starting condition")
        self.start(exercise)

    def report(self, identity=None):
        data = self.archive.read(identity or self.identity)
        exercise = data["exercise"]
        lines = ["<html><meta charset='utf-8'><body>",
                 f"<h1>{escape(exercise['name'])}</h1>",
                 f"<p>{escape(data['status'])} · {escape(data['clock'])}</p>",
                 "<h2>Objective review</h2><ul>"]
        if exercise.get("configuration"):
            context = exercise["configuration"]
            lines.insert(3, f"<p>Baseline: {escape(context['baseline_id'])} · Trainee project: {escape(context['project_id'])}</p>")
        for i, title in enumerate(exercise["objectives"]):
            review = data["objectives"].get(str(i), {})
            state = "Reviewed complete" if review.get("complete") else "Not completed"
            lines.append(f"<li>{escape(title)} — {state}: {escape(review.get('note', ''))}</li>")
        lines.extend(["</ul>", f"<p>{escape(data.get('notes', ''))}</p>",
                      "<h2>Recorded events</h2><table><tr><th>Seconds</th>"
                      "<th>Event</th><th>Target</th><th>Evidence</th></tr>"])
        for row in data["events"]:
            lines.append(f"<tr><td>{row['time']:.2f}</td><td>{escape(row['action'])}</td>"
                         f"<td>{escape(row.get('target', ''))}</td>"
                         f"<td>{escape(str(row.get('detail', '')))}</td></tr>")
        lines.append(f"</table><p>{len(data['samples'])} recorded observations. "
                     "Objective completion is reviewed by the instructor; no automatic grade is assigned.</p></body></html>")
        return "\n".join(lines)
