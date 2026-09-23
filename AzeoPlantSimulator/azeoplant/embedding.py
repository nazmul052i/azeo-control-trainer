"""Public in-process embedding surface for a virtual controller.

This module is intentionally the only factory a host application needs to
import.  It builds the same current plant, bus and safety system as ``run.py``
without constructing an OPC UA server or a Qt application.  Communication
identity, timing and artifact paths come from the caller's provider options;
there are no workstation-specific paths in this package.
"""

from __future__ import annotations

import json
import logging
import copy
import time
import math
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .control.strategy import ControlSystem
from .core.engine import SimulationEngine
from .core.tags import TagDatabase
from .core import native
from .io import VirtualIOBus
from .io.adapters import AdapterHealth, LocalAdapter, SignalSample
from .io.catalog import build_catalog
from .models.flowsheet import Flowsheet

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger(__name__)


def _path(value: object, root: Path) -> Path | None:
    if value in (None, ""):
        return None
    result = Path(str(value)).expanduser()
    return result.resolve() if result.is_absolute() else (root / result).resolve()


def _boolean(value: object, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normal = value.strip().lower()
        if normal in {"true", "yes", "on", "1"}:
            return True
        if normal in {"false", "no", "off", "0"}:
            return False
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise ValueError(f"{name} must be true or false")


@dataclass(frozen=True)
class EmbeddedPlantConfig:
    """Validated configuration for one in-process plant session."""

    source: str
    dt: float
    model_root: Path = _PACKAGE_ROOT
    snapshot: Path | None = None
    catalog: Path | None = None
    stale_timeout_s: float = 2.0
    speed_factor: float = 1.0
    autorun: bool = True
    open_loop: bool = True

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("embedded plant source must not be empty")
        if not 0.001 <= float(self.dt) <= 0.25:
            raise ValueError("embedded plant dt must be between 0.001 and 0.25 s")
        if float(self.stale_timeout_s) <= 0.0:
            raise ValueError("stale_timeout_s must be positive")
        if not 0.1 <= float(self.speed_factor) <= 20.0:
            raise ValueError("speed_factor must be between 0.1 and 20")
        if not self.open_loop:
            raise ValueError(
                "embedded LocalAdapter sessions must run BPCS open loop")

    @classmethod
    def from_mapping(cls, options: Mapping[str, Any], *,
                     base_dir: Path | str | None = None,
                     **overrides: Any) -> "EmbeddedPlantConfig":
        """Build from provider options, rejecting misspelled fields.

        ``embedding`` and ``local_adapter`` wrappers are accepted so the same
        file can contain settings for more than one provider.  Relative model
        artifacts resolve below ``model_root``; ``model_root`` itself resolves
        beside the configuration file (or package when a mapping is supplied
        directly).
        """
        raw: dict[str, Any] = dict(options)
        for section in ("embedding", "local_adapter"):
            nested = raw.get(section)
            if isinstance(nested, Mapping):
                raw = dict(nested)
                break
        raw.update(overrides)
        allowed = {
            "source", "dt", "model_root", "snapshot", "catalog",
            "stale_timeout_s", "speed_factor", "autorun", "open_loop",
        }
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise ValueError("unknown embedded plant option(s): "
                             + ", ".join(unknown))
        missing = [name for name in ("source", "dt") if name not in raw]
        if missing:
            raise ValueError("missing embedded plant option(s): "
                             + ", ".join(missing))

        base = Path(base_dir).resolve() if base_dir else _PACKAGE_ROOT
        root = _path(raw.get("model_root"), base) or base
        return cls(
            source=str(raw["source"]),
            dt=float(raw["dt"]),
            model_root=root,
            snapshot=_path(raw.get("snapshot"), root),
            catalog=_path(raw.get("catalog"), root),
            stale_timeout_s=float(raw.get("stale_timeout_s", 2.0)),
            speed_factor=float(raw.get("speed_factor", 1.0)),
            autorun=_boolean(raw.get("autorun", True), "autorun"),
            open_loop=_boolean(raw.get("open_loop", True), "open_loop"),
        )

    @classmethod
    def from_file(cls, path: Path | str, **overrides: Any
                  ) -> "EmbeddedPlantConfig":
        path = Path(path).expanduser().resolve()
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".json":
            raw = json.loads(text)
        else:
            try:
                import yaml
            except ImportError as exc:  # pragma: no cover - installation error
                raise RuntimeError(
                    "PyYAML is required to read embedded plant YAML") from exc
            raw = yaml.safe_load(text)
        if not isinstance(raw, Mapping):
            raise ValueError(f"{path} must contain a configuration mapping")
        return cls.from_mapping(raw, base_dir=path.parent, **overrides)


class EmbeddedPlantRuntime:
    """One provider-neutral LocalAdapter session and its plant executive."""

    def __init__(self, config: EmbeddedPlantConfig, db: TagDatabase,
                 flowsheet: Flowsheet, engine: SimulationEngine,
                 bus: VirtualIOBus, controller: ControlSystem,
                 adapter: LocalAdapter, catalog: tuple[dict, ...]) -> None:
        self.config = config
        self.db = db
        self.flowsheet = flowsheet
        self.engine = engine
        self.bus = bus
        self.controller = controller
        self.adapter = adapter
        self.catalog = catalog
        active = native.active()
        self._core = "cpp" if active else "python"
        self._native_units = sum(unit.code in active for unit in flowsheet.units)
        self._model_parameters = tuple(asdict(parameter) for unit in flowsheet.units
                                       for parameter in unit.model_parameters())
        self._started = False
        self._lifecycle_lock = threading.RLock()
        self._model_catalog = self._capture_model_catalog()
        self._model_state: dict[str, Any] = {}
        self._publish_model_state(force=True)
        engine.post_step_hooks.append(self._publish_model_state)

    @property
    def started(self) -> bool:
        return self._started

    def start(self) -> None:
        """Claim the output side and start the fixed-step executive once."""
        with self._lifecycle_lock:
            if self._started:
                return
            self.adapter.start()
            try:
                self.engine.start()
                if self.config.autorun:
                    self.engine.resume()
            except Exception:
                self.engine.stop()
                self.adapter.stop()
                raise
            self._started = True

    def stop(self) -> None:
        """Stop the executive and release the configured holder once."""
        with self._lifecycle_lock:
            if not self._started:
                return
            try:
                self.engine.stop()
            finally:
                self.adapter.stop()
                self._started = False

    def read_sample(self, tag: str) -> SignalSample:
        return self.adapter.read_sample(tag)

    def read_samples(self, tags: Iterable[str] | None = None
                     ) -> dict[str, SignalSample]:
        return self.adapter.read_samples(tags)

    def write(self, tag: str, value: float | bool) -> None:
        self.adapter.write(tag, value)

    def set_simulated_input(self, tag: str, value: float | bool,
                            quality: str = "GOOD") -> None:
        """Optional provider contract used by the engineering I/O tool."""
        self.bus.simulate_input(tag, value, quality)

    def clear_simulated_input(self, tag: str) -> None:
        """Release one engineering input simulation."""
        self.bus.clear_simulated_input(tag)

    def health(self) -> AdapterHealth:
        health = self.adapter.health()
        health.detail.update(
            engine_running=self.engine.stats.running,
            heartbeat=self.engine.stats.heartbeat,
            sim_time=self.engine.stats.sim_time,
            tag_count=len(self.db.all()),
            dt=self.engine.dt,
            bpcs_enabled=self.controller.enabled,
            sis_active=True,
            core=self._core,
            native_units=self._native_units,
        )
        return health

    def step(self, count: int = 1) -> None:
        """Advance a frozen session deterministically for tests and scripts."""
        if count < 0:
            raise ValueError("step count must not be negative")
        if self.engine.stats.running:
            raise RuntimeError("cannot step while the background engine is running")
        for _ in range(count):
            self.engine._execute_step()
        if count:
            with self.db.lock:
                self._publish_model_state(force=True)

    # These methods are the optional provider-neutral process-simulation
    # surface consumed by the host's Simulation Workbench.  Keeping the host
    # behind this small protocol avoids teaching Explorer about this package's
    # engine, database, or thread implementation.
    def simulation_control_capabilities(self) -> dict[str, Any]:
        return {
            "available": self._started,
            "running": bool(self.engine.stats.running),
            "paused": not bool(self.engine.stats.running),
            "speed_factor": float(self.engine.speed_factor),
            "speed_min": 0.1,
            "speed_max": 20.0,
            "dt": float(self.engine.dt),
            "sim_time": float(self.engine.stats.sim_time),
            "heartbeat": int(self.engine.stats.heartbeat),
            "step_supported": True,
            "snapshot_supported": True,
            "model_diagnostics_supported": True,
            "disturbances_supported": True,
            "core": self._core,
            "native_units": self._native_units,
            "reason": "" if self._started else "Start the plant provider first.",
        }

    def pause_simulation(self) -> None:
        if not self._started:
            raise RuntimeError("plant provider is not started")
        self.engine.freeze()

    def simulation_model_catalog(self) -> dict[str, Any]:
        """Plain metadata only; no UI receives mutable native model objects."""
        return copy.deepcopy(self._model_catalog)

    def _capture_model_catalog(self) -> dict[str, Any]:
        with self.db.lock:
            return {
                "units": [{"unit": unit.code, "name": unit.name,
                           "signals": len(unit.tags)} for unit in self.flowsheet.units],
                "parameters": [dict(row) for row in self._model_parameters],
                "disturbances": [
                    {"unit": unit.code, "id": fault.mf_id, "target": fault.target,
                     "description": fault.description, "category": fault.category,
                     "parameter": fault.param_label, "minimum": fault.param_min,
                     "maximum": fault.param_max}
                    for unit in self.flowsheet.units for fault in unit.malfunctions],
            }

    def simulation_model_state(self) -> dict[str, Any]:
        # The GUI must not wait for physics, logging or snapshot I/O inside
        # the engine's lock. Publish a complete DTO, then replace its reference.
        return copy.deepcopy(self._model_state)

    def _publish_model_state(self, _dt: float = 0.0, *, force=False) -> None:
        # Called by the engine under its existing lock, or by a locked command.
        now = time.monotonic()
        if not force and now - self._model_state.get("sampled_at", 0.0) < .25:
            return
        self._model_state = {
            "sampled_at": now,
            "disturbances": [{"id": fault.mf_id, "active": fault.active,
                              "value": fault.value}
                             for unit in self.flowsheet.units
                             for fault in unit.malfunctions],
            "units": [{"unit": unit.code,
                       "active_disturbances": sum(fault.active for fault in unit.malfunctions),
                       "bad_signals": sum(tag.quality.name == "BAD" for tag in unit.tags.values())}
                      for unit in self.flowsheet.units],
        }

    def set_simulation_disturbance(self, identity: str, active: bool,
                                  value: float | None = None) -> None:
        if not self._started:
            raise RuntimeError("plant provider is not started")
        with self.db.lock:
            fault = next((fault for fault in self.flowsheet.malfunctions()
                          if fault.mf_id == identity), None)
            if fault is None:
                raise ValueError(f"Unknown plant disturbance: {identity}")
            setting = fault.value if value is None else float(value)
            if active and fault.param_label and (
                    not math.isfinite(setting) or not fault.param_min <= setting <= fault.param_max):
                raise ValueError(f"{fault.param_label} must be between {fault.param_min:g} and {fault.param_max:g}")
            if not math.isfinite(setting):
                raise ValueError("Disturbance value must be finite")
            fault.set(bool(active), setting)
            self._publish_model_state(force=True)
            log.info("Simulation disturbance %s active=%s value=%g", identity, bool(active), setting)

    def resume_simulation(self) -> None:
        if not self._started:
            raise RuntimeError("plant provider is not started")
        self.engine.resume()

    def set_simulation_speed(self, factor: float) -> float:
        if not self._started:
            raise RuntimeError("plant provider is not started")
        self.engine.speed_factor = factor
        return float(self.engine.speed_factor)

    def save_simulation_snapshot(self, path: Path | str) -> Path:
        if not self._started:
            raise RuntimeError("plant provider is not started")
        return self.engine.save_snapshot(path)

    def load_simulation_snapshot(self, path: Path | str) -> dict[str, object]:
        if not self._started:
            raise RuntimeError("plant provider is not started")
        # A standalone lineup carries an enabled software DCS. Keep the
        # executive locked until that flag is cleared, so restoring physics
        # cannot briefly start a second BPCS beside the host controller.
        with self.db.lock:
            result = self.engine.load_snapshot(path)
            self.controller.open_loop()
            self._publish_model_state(force=True)
            return result

    def __enter__(self) -> "EmbeddedPlantRuntime":
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()


def _read_catalog(path: Path | None, db: TagDatabase,
                  flowsheet: Flowsheet) -> tuple[dict, ...]:
    if path is None:
        units = {unit.code: unit.name for unit in flowsheet.units}
        return tuple(build_catalog(db, units))
    if not path.is_file():
        raise FileNotFoundError(f"embedded plant catalog not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("tags") if isinstance(payload, Mapping) else payload
    if not isinstance(records, list):
        raise ValueError(f"{path} does not contain a tag catalogue")
    live = {tag.name for tag in db.all()}
    configured = {str(record.get("name", "")) for record in records
                  if isinstance(record, Mapping)}
    if configured != live or len(records) != len(live):
        missing = sorted(live - configured)
        extra = sorted(configured - live)
        duplicates = len(records) - len(configured)
        raise ValueError(
            f"catalog does not match live plant: {len(missing)} missing, "
            f"{len(extra)} extra, {duplicates} duplicate")
    return tuple(dict(record) for record in records)


def create_embedded_plant(
    options: EmbeddedPlantConfig | Mapping[str, Any] | Path | str | None = None,
    **kwargs: Any,
) -> EmbeddedPlantRuntime:
    """Create a stopped in-process plant session from provider options.

    The signature accepts the common plugin forms ``factory(options)`` and
    ``factory(**options)``.  A YAML/JSON path is also accepted for standalone
    use.  The returned session owns no socket and starts no thread until its
    idempotent :meth:`EmbeddedPlantRuntime.start` is called.
    """
    if isinstance(options, EmbeddedPlantConfig):
        if kwargs:
            raise ValueError("cannot override an EmbeddedPlantConfig instance")
        config = options
    elif isinstance(options, Mapping):
        config = EmbeddedPlantConfig.from_mapping(options, **kwargs)
    elif isinstance(options, (str, Path)):
        config = EmbeddedPlantConfig.from_file(options, **kwargs)
    elif options is None:
        config = EmbeddedPlantConfig.from_mapping(kwargs)
    else:
        raise TypeError("embedded plant options must be a mapping, path or config")

    if native.requested() and not native.active():
        raise RuntimeError(native.unavailable_message())
    db = TagDatabase()
    flowsheet = Flowsheet(db, dt=config.dt)
    engine = SimulationEngine(db, flowsheet, dt=config.dt)
    # Local VIO carries the tag database's SI values; a snapshot's standalone
    # presentation preference must not change the controller's I/O contract.
    engine.units_pinned = True
    bus = VirtualIOBus(db, stale_timeout_s=config.stale_timeout_s)
    controller = ControlSystem(db, bus)
    engine.bus = bus
    engine.controller = controller
    engine.post_step_hooks.extend((bus.tick, controller.step))

    if config.snapshot is not None:
        if not config.snapshot.is_file():
            raise FileNotFoundError(
                f"embedded plant snapshot not found: {config.snapshot}")
        engine.load_snapshot(config.snapshot)

    # A restored closed-loop snapshot may re-enable the software BPCS.  The
    # external virtual controller must be the only regulatory writer, while
    # ControlSystem.step remains attached because it scans the independent SIS
    # before returning from the disabled BPCS branch.
    controller.seed_from_plant()
    controller.open_loop()
    engine.speed_factor = config.speed_factor

    catalog = _read_catalog(config.catalog, db, flowsheet)
    adapter = LocalAdapter(bus, source=config.source)
    engine.adapter = adapter
    return EmbeddedPlantRuntime(config, db, flowsheet, engine, bus,
                                controller, adapter, catalog)


__all__ = [
    "EmbeddedPlantConfig", "EmbeddedPlantRuntime", "create_embedded_plant",
]
