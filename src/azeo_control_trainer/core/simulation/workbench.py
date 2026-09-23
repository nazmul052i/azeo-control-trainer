"""Provider-neutral orchestration for system-level simulation checkout.

The workbench deliberately coordinates the existing strategy runtimes,
``SharedDataStore`` and Local Virtual I/O driver.  It is not another control
or plant engine.  The service is Qt-free so tests, scripts and Explorer all
exercise the same scoped actions and snapshot contract.
"""
from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import tempfile
import time
from contextlib import closing, contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping


SNAPSHOT_SCHEMA_VERSION = 1
IO_TYPES = frozenset({"AI", "AO", "DI", "DO"})
DYNAMIC_TYPES = frozenset({
    "AI", "AO", "DI", "DO", "PID", "FILTER", "INTEGRATOR", "RATE_LIM",
    "RATIO", "SPLITTER", "DEADTIME", "DTC", "FLC", "MPC", "PIN",
})
_TUNING_WORDS = (
    "gain", "reset", "rate", "filter", "ftime", "deadband", "bias",
    "alpha", "beta", "gamma", "hyst", "limit", "_lim", "scale", "span",
)
_OPERATING_KEYS = frozenset({
    "simulate", "simulate_enabled", "simulate_d", "simulate_val",
    "simulate_status_good", "mode", "normal_mode", "sp", "sp_init",
    "out_init", "manout", "manual_output",
})


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, float) and not math.isfinite(value):
        return {"$float": "Infinity" if value > 0 else "-Infinity"}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return str(value)


def _restore_value(value: Any) -> Any:
    if isinstance(value, Mapping) and set(value) == {"$float"}:
        if value["$float"] == "Infinity":
            return float("inf")
        if value["$float"] == "-Infinity":
            return float("-inf")
    if isinstance(value, Mapping):
        return {key: _restore_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_restore_value(item) for item in value]
    return value


def _write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(_json_value(payload), stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return path


@dataclass(frozen=True)
class SimulationScope:
    """A stable module selection used by every bulk command."""

    modules: tuple[str, ...] = ()

    @property
    def all_modules(self) -> bool:
        return not self.modules


class OperatorChangeJournal:
    """Append-only, replayable engineering/operator change journal."""

    def __init__(self, path: Path | str):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS changes ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, wall_time REAL NOT NULL, "
                "sim_time REAL NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, "
                "target TEXT NOT NULL, value_json TEXT NOT NULL, "
                "marker TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '')")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS settings ("
                "name TEXT PRIMARY KEY, value TEXT NOT NULL)")

    @contextmanager
    def _connect(self):
        # Transaction completion alone leaves SQLite handles open on Windows.
        with closing(sqlite3.connect(self.path, timeout=5.0)) as connection:
            connection.row_factory = sqlite3.Row
            with connection:
                yield connection

    def append(self, *, sim_time: float, actor: str, action: str,
               target: str, value: Any = None, marker: str = "",
               note: str = "") -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO changes "
                "(wall_time, sim_time, actor, action, target, value_json, marker, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (time.time(), float(sim_time), str(actor), str(action),
                 str(target), json.dumps(_json_value(value)), str(marker), str(note)),
            )
            return int(cursor.lastrowid)

    def rows(self, *, after_id: int = 0) -> list[dict[str, Any]]:
        with self._connect() as connection:
            records = connection.execute(
                "SELECT * FROM changes WHERE id > ? ORDER BY id", (after_id,),
            ).fetchall()
        answer = []
        for record in records:
            row = dict(record)
            row["value"] = _restore_value(json.loads(row.pop("value_json")))
            answer.append(row)
        return answer

    def clear(self) -> int:
        with self._connect() as connection:
            count = int(connection.execute(
                "SELECT COUNT(*) FROM changes").fetchone()[0])
            connection.execute("DELETE FROM changes")
        return count

    def count(self) -> int:
        with self._connect() as connection:
            return int(connection.execute(
                "SELECT COUNT(*) FROM changes").fetchone()[0])

    @property
    def recording(self) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE name='recording'").fetchone()
        return bool(row and str(row[0]).lower() == "true")

    @recording.setter
    def recording(self, enabled: bool) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO settings (name, value) VALUES ('recording', ?) "
                "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                ("true" if enabled else "false",),
            )


class SimulationWorkbenchService:
    """Coordinate controller, process and I/O simulation as one system."""

    def __init__(self, store, driver=None, project_dir: Path | str = ".",
                 actor: str = "engineer", executive=None,
                 journal_path: Path | str | None = None):
        self.store = store
        self.driver = driver
        self.project_dir = Path(project_dir).expanduser().resolve()
        self.actor = str(actor or "engineer")
        self.executive = executive
        self._resume_controller = False
        self._replaying = False
        self.history_event_sink = None
        journal_path = journal_path or (
            self.project_dir / "simulation" / "operator_changes.sqlite")
        self.journal = OperatorChangeJournal(journal_path)

    @property
    def recording(self) -> bool:
        return self.journal.recording

    @recording.setter
    def recording(self, enabled: bool) -> None:
        self.journal.recording = bool(enabled)

    # --------------------------------------------------------------- inventory
    def _runtimes(self) -> list[Any]:
        getter = getattr(self.store, "get_strategy_runtimes", None)
        return list(getter() or ()) if callable(getter) else []

    @staticmethod
    def _module_name(runtime: Any) -> str:
        compiled = getattr(runtime, "compiled", None)
        graph = getattr(compiled, "graph", None)
        return str(getattr(graph, "name", "") or "Unnamed module")

    def modules(self) -> list[dict[str, Any]]:
        result = []
        for runtime in self._runtimes():
            compiled = getattr(runtime, "compiled", None)
            graph = getattr(compiled, "graph", None)
            if graph is None:
                continue
            result.append({
                "name": self._module_name(runtime),
                "online": bool(getattr(runtime, "is_online", False)),
                "paused": bool(getattr(runtime, "is_debug_paused", False)),
                "blocks": len(getattr(graph, "blocks", {})),
                "runtime": runtime,
            })
        return sorted(result, key=lambda row: row["name"].lower())

    def _selected_runtimes(self, scope: SimulationScope | None) -> list[Any]:
        scope = scope or SimulationScope()
        wanted = set(scope.modules)
        return [runtime for runtime in self._runtimes()
                if not wanted or self._module_name(runtime) in wanted]

    def _blocks(self, scope: SimulationScope | None = None) \
            -> Iterable[tuple[str, Any]]:
        for runtime in self._selected_runtimes(scope):
            graph = runtime.compiled.graph
            for block in graph.blocks.values():
                yield self._module_name(runtime), block

    def io_rows(self, scope: SimulationScope | None = None) \
            -> list[dict[str, Any]]:
        rows = []
        for module, block in self._blocks(scope):
            block_type = str(getattr(block, "block_type", "")).upper()
            if block_type not in IO_TYPES:
                continue
            params = block.config.params
            discrete = block_type in {"DI", "DO"}
            sim_key = "SIMULATE_D" if discrete else "simulate_enabled"
            value_key = "SIMULATE_VAL" if discrete else "SIMULATE"
            mode = getattr(block, "mode_actual", None)
            if mode is None:
                mode = getattr(block, "_actual_mode", None)
            if isinstance(mode, Enum):
                mode = mode.value
            out = block.outputs.get("OUT") or block.outputs.get("OUT_D")
            quality = getattr(getattr(out, "status", None), "name", "")
            rows.append({
                "module": module,
                "block_id": str(block.id),
                "block": str(block.instance_name),
                "type": block_type,
                "tag": str(params.get("tag") or ""),
                "simulate": bool(params.get(sim_key, False)),
                "sim_value": params.get(value_key, False if discrete else 0.0),
                "sim_quality": ("GOOD" if params.get(
                    "SIMULATE_STATUS_GOOD", True) else "BAD"),
                "mode": str(mode or ""),
                "normal_mode": str(params.get("normal_mode") or
                                   params.get("mode") or "AUTO"),
                "out": getattr(out, "value", None),
                "quality": quality,
                "status": getattr(getattr(block, "status", None), "value", ""),
            })
        return sorted(rows, key=lambda row: (row["module"], row["block"]))

    def other_rows(self, scope: SimulationScope | None = None) \
            -> list[dict[str, Any]]:
        rows = []
        for module, block in self._blocks(scope):
            block_type = str(getattr(block, "block_type", "")).upper()
            if block_type in IO_TYPES:
                continue
            outputs = {name: _json_value(terminal.value)
                       for name, terminal in block.outputs.items()}
            rows.append({
                "module": module,
                "block_id": str(block.id),
                "block": str(block.instance_name),
                "type": block_type,
                "status": getattr(getattr(block, "status", None), "value", ""),
                "outputs": outputs,
            })
        return sorted(rows, key=lambda row: (row["module"], row["block"]))

    # --------------------------------------------------------- scoped commands
    def _find_block(self, module: str, block_ref: str):
        for found_module, block in self._blocks(SimulationScope((module,))):
            if found_module == module and block_ref in {
                    str(block.id), str(block.instance_name)}:
                return block
        raise KeyError(f"block {module}/{block_ref} is not loaded")

    def set_simulation_enabled(self, scope: SimulationScope | None,
                               enabled: bool) -> int:
        changed = 0
        for module, block in self._blocks(scope):
            block_type = str(block.block_type).upper()
            if block_type not in IO_TYPES:
                continue
            key = "SIMULATE_D" if block_type in {"DI", "DO"} \
                else "simulate_enabled"
            before = bool(block.config.params.get(key, False))
            if before == bool(enabled):
                continue
            block.config.params[key] = bool(enabled)
            changed += 1
            self._record("simulation_enabled",
                         f"{module}/{block.instance_name}", bool(enabled))
        return changed

    def set_block_simulation_enabled(self, module: str, block_ref: str,
                                     enabled: bool) -> None:
        block = self._find_block(module, block_ref)
        block_type = str(block.block_type).upper()
        if block_type not in IO_TYPES:
            raise ValueError(f"{block.instance_name} is not an I/O block")
        key = "SIMULATE_D" if block_type in {"DI", "DO"} \
            else "simulate_enabled"
        block.config.params[key] = bool(enabled)
        self._record("simulation_enabled",
                     f"{module}/{block.instance_name}", bool(enabled))

    def set_io_value(self, module: str, block_ref: str, value: Any,
                     quality: str = "GOOD") -> None:
        block = self._find_block(module, block_ref)
        block_type = str(block.block_type).upper()
        if block_type not in IO_TYPES:
            raise ValueError(f"{block.instance_name} is not an I/O block")
        discrete = block_type in {"DI", "DO"}
        key = "SIMULATE_VAL" if discrete else "SIMULATE"
        converted = bool(value) if discrete else float(value)
        if not discrete:
            normal = str(quality).upper()
            if normal not in {"GOOD", "BAD"}:
                raise ValueError("controller block simulation quality is GOOD or BAD")
        block.config.params[key] = converted
        if not discrete:
            block.config.params["SIMULATE_STATUS_GOOD"] = normal == "GOOD"
        self._record("io_value", f"{module}/{block.instance_name}", {
            "value": converted, "quality": str(quality).upper()})

    def set_mode(self, module: str, block_ref: str, mode: str) -> None:
        block = self._find_block(module, block_ref)
        setter = getattr(block, "set_mode", None)
        if not callable(setter):
            raise ValueError(f"{block.instance_name} has no operator mode")
        setter(str(mode))
        self._record("mode", f"{module}/{block.instance_name}", str(mode))

    def set_setup_mode(self, scope: SimulationScope | None) -> int:
        changed = 0
        for module, block in self._blocks(scope):
            setter = getattr(block, "set_mode", None)
            if not callable(setter):
                continue
            mode = str(block.config.params.get("setup_mode") or "MAN")
            setter(mode)
            changed += 1
            self._record("mode", f"{module}/{block.instance_name}", mode)
        return changed

    def set_normal_mode(self, scope: SimulationScope | None) -> int:
        changed = 0
        for module, block in self._blocks(scope):
            setter = getattr(block, "set_mode", None)
            if not callable(setter):
                continue
            mode = str(block.config.params.get("normal_mode") or
                       block.config.params.get("mode") or "AUTO")
            setter(mode)
            changed += 1
            self._record("mode", f"{module}/{block.instance_name}", mode)
        return changed

    def initialize_dynamic_blocks(self, scope: SimulationScope | None) -> int:
        count = 0
        for module, block in self._blocks(scope):
            block_type = str(block.block_type).upper()
            reset = getattr(block, "reset", None)
            if block_type not in DYNAMIC_TYPES or not callable(reset):
                continue
            reset()
            count += 1
            self._record("initialize", f"{module}/{block.instance_name}", None)
        return count

    # ---------------------------------------------------------- process clock
    def process_capabilities(self) -> dict[str, Any]:
        describe = getattr(self.driver, "process_simulation_capabilities", None)
        return describe() if callable(describe) else {
            "available": False, "running": False, "paused": True,
            "reason": "No process-simulation provider is attached.",
        }

    def pause(self) -> None:
        self.driver.pause_process_simulation()
        if self.executive is not None \
                and bool(getattr(self.executive, "is_running", False)):
            self.executive.stop()
            self._resume_controller = True
        self._record("process_pause", "process", None)

    def resume(self) -> None:
        started_controller = False
        if self.executive is not None and self._resume_controller:
            self.executive.start()
            started_controller = True
        try:
            self.driver.resume_process_simulation()
        except Exception:
            if started_controller:
                self.executive.stop()
            raise
        self._resume_controller = False
        self._record("process_resume", "process", None)

    def step(self, count: int = 1) -> None:
        count = int(count)
        if count < 1:
            raise ValueError("step count must be positive")
        if self.executive is not None \
                and bool(getattr(self.executive, "is_running", False)):
            raise RuntimeError("pause the simulated system before stepping")
        for _ in range(count):
            self.driver.step_process_simulation(1)
            scan = getattr(self.driver, "scan_once", None)
            if callable(scan):
                scan()
            controller_step = getattr(
                self.executive, "simulation_step_once", None)
            if callable(controller_step):
                controller_step()
            if callable(scan):
                scan()
        self._record("process_step", "process", int(count))

    def set_speed(self, factor: float) -> float:
        result = self.driver.set_process_simulation_speed(float(factor))
        set_controller_speed = getattr(
            self.executive, "set_simulation_speed_factor", None)
        if callable(set_controller_speed):
            set_controller_speed(result)
        self._record("process_speed", "process", result)
        return result

    def _synchronize_controller_speed(self) -> None:
        set_controller_speed = getattr(
            self.executive, "set_simulation_speed_factor", None)
        if not callable(set_controller_speed):
            return
        factor = float(self.process_capabilities().get("speed_factor") or 1.0)
        set_controller_speed(factor)

    # --------------------------------------------------------------- snapshots
    @staticmethod
    def _parameter_category(name: str) -> str | None:
        low = str(name).lower()
        if low in _OPERATING_KEYS:
            return "operating"
        if any(word in low for word in _TUNING_WORDS):
            return "tuning"
        return None

    def _capture_controller(self) -> list[dict[str, Any]]:
        modules = []
        for runtime in self._runtimes():
            compiled = getattr(runtime, "compiled", None)
            if compiled is None:
                continue
            blocks = []
            for block in compiled.graph.blocks.values():
                parameters = {"operating": {}, "tuning": {}}
                for name, value in block.config.params.items():
                    category = self._parameter_category(name)
                    if category:
                        parameters[category][name] = _json_value(value)
                mode = getattr(block, "mode_actual", None)
                if mode is None:
                    mode = getattr(block, "_actual_mode", None)
                if isinstance(mode, Enum):
                    mode = mode.value
                operating_state = {}
                for attr in ("_man_out", "_sp", "_sp_d"):
                    if hasattr(block, attr):
                        operating_state[attr] = _json_value(getattr(block, attr))
                blocks.append({
                    "id": str(block.id), "name": str(block.instance_name),
                    "type": str(block.block_type), "mode": _json_value(mode),
                    "parameters": parameters, "operating_state": operating_state,
                })
            modules.append({
                "name": self._module_name(runtime),
                "online": bool(getattr(runtime, "is_online", False)),
                "blocks": blocks,
            })
        return modules

    def _capture_virtual_io(self) -> list[dict[str, Any]]:
        snapshot = getattr(self.driver, "signal_simulation_snapshot", None)
        if not callable(snapshot):
            return []
        return [row["simulation"] for row in snapshot()
                if row.get("simulation")]

    def _capture_process_payload(self) -> dict[str, Any] | None:
        caps = self.process_capabilities()
        if not caps.get("snapshot_supported"):
            return None
        with tempfile.TemporaryDirectory(prefix="azeo-workbench-") as directory:
            path = Path(directory) / "process.json"
            self.driver.save_process_simulation_snapshot(path)
            return json.loads(path.read_text(encoding="utf-8"))

    def save_snapshot(self, path: Path | str, *, name: str = "Snapshot",
                      note: str = "") -> Path:
        caps = self.process_capabilities()
        process_was_running = bool(caps.get("running"))
        controller_was_running = bool(
            getattr(self.executive, "is_running", False))
        if process_was_running:
            self.driver.pause_process_simulation()
        if controller_was_running:
            self.executive.stop()
        try:
            payload = {
                "schema_version": SNAPSHOT_SCHEMA_VERSION,
                "type": "azeo.simulation_workbench_snapshot",
                "name": str(name), "note": str(note), "created_at": time.time(),
                "controller": self._capture_controller(),
                "virtual_io": self._capture_virtual_io(),
                "process": self._capture_process_payload(),
                "provider": getattr(self.driver, "_configuration_provider_identity", {}),
            }
        finally:
            if controller_was_running:
                self.executive.start()
            try:
                if process_was_running:
                    self.driver.resume_process_simulation()
            except Exception:
                if controller_was_running:
                    self.executive.stop()
                raise
        result = _write_json(Path(path), payload)
        self._record("snapshot_save", str(result), {"name": name})
        return result

    @staticmethod
    def load_snapshot_document(path: Path | str) -> dict[str, Any]:
        path = Path(path).expanduser().resolve()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return SimulationWorkbenchService.validate_snapshot_document(payload)

    @staticmethod
    def validate_snapshot_document(payload: dict) -> dict[str, Any]:
        if not isinstance(payload, dict) or payload.get("type") != \
                "azeo.simulation_workbench_snapshot":
            raise ValueError("file is not an Azeo Simulation Workbench snapshot")
        if int(payload.get("schema_version", 0)) != SNAPSHOT_SCHEMA_VERSION:
            raise ValueError("unsupported Simulation Workbench snapshot schema")
        if not isinstance(payload.get("controller"), list):
            raise ValueError("snapshot controller section must be a list")
        if not isinstance(payload.get("virtual_io", []), list):
            raise ValueError("snapshot Virtual I/O section must be a list")
        if payload.get("process") is not None \
                and not isinstance(payload.get("process"), dict):
            raise ValueError("snapshot process section must be an object")
        for module in payload["controller"]:
            if not isinstance(module, dict) or not str(module.get("name") or ""):
                raise ValueError("each controller snapshot needs a module name")
            if not isinstance(module.get("blocks", []), list):
                raise ValueError("each controller module needs a blocks list")
            for block in module.get("blocks", []):
                if not isinstance(block, dict) or not str(block.get("type") or ""):
                    raise ValueError("each controller block needs a type")
                parameters = block.get("parameters", {})
                if not isinstance(parameters, dict) or any(
                        not isinstance(parameters.get(kind, {}), dict)
                        for kind in ("operating", "tuning")):
                    raise ValueError(
                        "controller block parameters must be grouped objects")
                if not isinstance(block.get("operating_state", {}), dict):
                    raise ValueError("controller operating state must be an object")
        for profile in payload.get("virtual_io", []):
            if not isinstance(profile, dict) or not str(
                    profile.get("store_tag") or ""):
                raise ValueError(
                    "each Virtual I/O profile needs a store_tag")
        return _restore_value(payload)

    def list_snapshots(self) -> list[dict[str, Any]]:
        folder = self.project_dir / "simulation" / "snapshots"
        rows = []
        for path in sorted(folder.glob("*.json"),
                           key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                payload = self.load_snapshot_document(path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            rows.append({
                "path": path, "name": payload.get("name") or path.stem,
                "note": payload.get("note") or "",
                "created_at": float(payload.get("created_at") or 0.0),
                "has_process": payload.get("process") is not None,
                "modules": len(payload.get("controller") or ()),
            })
        return rows

    @contextmanager
    def _process_payload_file(self, payload: Mapping[str, Any]):
        with tempfile.TemporaryDirectory(prefix="azeo-workbench-restore-") as directory:
            path = Path(directory) / "process.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            yield path

    def _restore_controller(self, modules: list[dict[str, Any]], *,
                            operating: bool, tuning: bool) -> int:
        by_module = {self._module_name(runtime): runtime
                     for runtime in self._runtimes()}
        count = 0
        for module_state in modules:
            runtime = by_module.get(str(module_state.get("name") or ""))
            if runtime is None or runtime.compiled is None:
                continue
            graph_blocks = runtime.compiled.graph.blocks
            by_name = {block.instance_name: block
                       for block in graph_blocks.values()}
            for record in module_state.get("blocks") or ():
                block = graph_blocks.get(str(record.get("id") or "")) \
                    or by_name.get(str(record.get("name") or ""))
                if block is None or str(block.block_type) != str(record.get("type")):
                    continue
                parameters = record.get("parameters") or {}
                configuration_changed = False
                if operating:
                    for key, value in (parameters.get("operating") or {}).items():
                        block.config.params[str(key)] = _restore_value(value)
                        count += 1
                        configuration_changed = True
                if tuning:
                    for key, value in (parameters.get("tuning") or {}).items():
                        block.config.params[str(key)] = _restore_value(value)
                        count += 1
                        configuration_changed = True
                if configuration_changed:
                    apply_config = getattr(block, "_apply_config", None)
                    if callable(apply_config):
                        apply_config()
                if operating:
                    mode = record.get("mode")
                    setter = getattr(block, "set_mode", None)
                    if mode and callable(setter):
                        setter(str(mode))
                        count += 1
                    for attr, value in (record.get("operating_state") or {}).items():
                        if attr in {"_man_out", "_sp", "_sp_d"} \
                                and hasattr(block, attr):
                            setattr(block, attr, _restore_value(value))
                            count += 1
        return count

    def _restore_virtual_io(self, rows: list[dict[str, Any]]) -> int:
        clear = getattr(self.driver, "clear_all_signal_simulations", None)
        configure = getattr(self.driver, "configure_signal_simulation", None)
        if not callable(clear) or not callable(configure):
            return 0
        clear()
        for row in rows:
            values = dict(row)
            tag = str(values.pop("store_tag"))
            configure(tag, **values)
        return len(rows)

    def restore_snapshot(self, path: Path | str, *, operating: bool = True,
                         tuning: bool = True, process: bool = True) -> dict[str, int]:
        """Restore the selected categories, rolling the entire system back.

        Validation occurs before mutation.  The process clock is paused while
        state is exchanged and resumes only if it was running on entry.
        """
        payload = self.load_snapshot_document(path)
        caps = self.process_capabilities()
        process_was_running = bool(caps.get("running"))
        controller_was_running = bool(
            getattr(self.executive, "is_running", False))
        if process_was_running:
            self.driver.pause_process_simulation()
        if controller_was_running:
            self.executive.stop()
        try:
            # Capture rollback only after both clocks have crossed a real pause
            # boundary; capturing a live plant and pausing later loses the
            # steps in between if a restore must be rolled back.
            before_controller = self._capture_controller()
            before_virtual_io = self._capture_virtual_io()
            before_process = self._capture_process_payload() if process else None
            try:
                if process and payload.get("process") is not None:
                    with self._process_payload_file(
                            payload["process"]) as process_path:
                        self.driver.load_process_simulation_snapshot(process_path)
                    self._synchronize_controller_speed()
                controller_count = self._restore_controller(
                    payload["controller"], operating=operating, tuning=tuning)
                io_count = self._restore_virtual_io(
                    payload.get("virtual_io") or ()) if operating else 0
            except Exception:
                if before_process is not None:
                    with self._process_payload_file(
                            before_process) as rollback_path:
                        self.driver.load_process_simulation_snapshot(rollback_path)
                    self._synchronize_controller_speed()
                self._restore_controller(before_controller,
                                         operating=operating, tuning=tuning)
                if operating:
                    self._restore_virtual_io(before_virtual_io)
                raise
        finally:
            if controller_was_running:
                self.executive.start()
            try:
                if process_was_running:
                    self.driver.resume_process_simulation()
            except Exception:
                if controller_was_running:
                    self.executive.stop()
                raise
        result = {"controller_values": controller_count,
                  "virtual_io_profiles": io_count,
                  "process": int(bool(process and payload.get("process") is not None))}
        self._record("snapshot_restore", str(Path(path).resolve()), result)
        return result

    # --------------------------------------------------------------- playback
    def _sim_time(self) -> float:
        try:
            return float(self.process_capabilities().get("sim_time") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _record(self, action: str, target: str, value: Any) -> None:
        if self.history_event_sink is not None:
            try:
                self.history_event_sink(action, target, value, self._sim_time())
            except Exception:  # noqa: BLE001 - completed process actions must not be retried for an audit failure
                logging.getLogger("hmi.historian").exception("Could not record Workbench history event")
        if self.recording and not self._replaying:
            self.journal.append(
                sim_time=self._sim_time(), actor=self.actor, action=action,
                target=target, value=value)

    def add_marker(self, name: str, note: str = "") -> int:
        return self.journal.append(
            sim_time=self._sim_time(), actor=self.actor, action="marker",
            target="", value=None, marker=name, note=note)

    def record_tag_write(self, tag: str, value: Any) -> int | None:
        """Public hook for operator surfaces that want replayable writes."""
        if not self.recording or self._replaying:
            return None
        return self.journal.append(
            sim_time=self._sim_time(), actor=self.actor, action="tag_write",
            target=str(tag), value=value)

    def replay_event(self, event: Mapping[str, Any]) -> bool:
        action, target, value = (str(event.get("action") or ""),
                                 str(event.get("target") or ""),
                                 event.get("value"))
        module, separator, block = target.partition("/")
        self._replaying = True
        try:
            if action == "simulation_enabled" and separator:
                self.set_block_simulation_enabled(module, block, bool(value))
            elif action == "io_value" and separator:
                values = value if isinstance(value, Mapping) else {"value": value}
                self.set_io_value(module, block, values.get("value"),
                                  str(values.get("quality") or "GOOD"))
            elif action == "mode" and separator:
                self.set_mode(module, block, str(value))
            elif action == "tag_write":
                self.store.queue_write(target, value)
            elif action == "process_speed":
                self.set_speed(float(value))
            elif action == "process_pause":
                self.pause()
            elif action == "process_resume":
                self.resume()
            elif action == "process_step":
                self.step(int(value or 1))
            elif action == "plant_disturbance":
                self.set_plant_disturbance(target, bool(value["active"]), value.get("value"))
            elif action in {"marker", "initialize", "snapshot_save",
                            "snapshot_restore"}:
                return False
            else:
                raise ValueError(f"unsupported playback action {action!r}")
        finally:
            self._replaying = False
        return True

    # ------------------------------------------------------------- diagnostics
    def set_plant_disturbance(self, identity: str, active: bool,
                              value: float | None = None) -> None:
        self.driver.set_process_disturbance(identity, active, value)
        self._record("plant_disturbance", identity, {"active": active, "value": value})

    def diagnostics(self, *, include_journal: bool = True) -> dict[str, Any]:
        modules = self.modules()
        io = self.io_rows()
        process = self.process_capabilities()
        driver_health = getattr(self.driver, "health", None)
        health = driver_health() if callable(driver_health) else {}
        return {
            "modules": len(modules),
            "online_modules": sum(bool(row["online"]) for row in modules),
            "controller_io": len(io),
            "simulated_controller_io": sum(bool(row["simulate"]) for row in io),
            "virtual_io_channels": len(getattr(self.driver, "signals", ())),
            "process": process,
            "field_io": health,
            **({"recording": self.recording,
                "recorded_changes": self.journal.count()} if include_journal else {}),
            "controller_executive": (
                "RUNNING" if bool(getattr(self.executive, "is_running", False))
                else "PAUSED" if self.executive is not None else "UNAVAILABLE"),
        }


__all__ = [
    "IO_TYPES", "OperatorChangeJournal", "SimulationScope",
    "SimulationWorkbenchService",
]
