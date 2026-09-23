"""In-process Virtual-I/O transport selected entirely by project data.

``LocalVirtualIoDriver`` is the controller-side peer of a provider's local
adapter.  It drains controller demands, enqueues only configured AO/DO routes,
applies unrelated writes locally, and publishes the provider's actual AI/DI
and AO/DO readbacks with their original quality and timestamp.  No simulator
package, checkout path, signal name, or endpoint is compiled into the trainer.
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from string import Template
from typing import Any

from .dynamic_provider import (
    ProviderConfigurationError,
    ProviderSession,
    load_provider,
)
from .simulation import SignalSimulation, read_profile, write_profile
from .virtual_io_config import (
    positive_finite,
    validate_virtual_io_runtime_config,
)

log = logging.getLogger("fieldio.local_virtual_io")

_READ_DIRECTIONS = {"read", "input", "sim_to_dcs"}
_WRITE_DIRECTIONS = {"write", "output", "dcs_to_sim"}
_KINDS_BY_DIRECTION = {
    "read": {"AI", "DI"},
    "write": {"AO", "DO"},
}
_MAX_FUTURE_TIMESTAMP_S = 1.0


@dataclass(frozen=True)
class SignalRoute:
    """One configured store-to-provider route."""

    store_tag: str
    signal: str
    direction: str
    kind: str = ""
    stale_timeout_s: float | None = None
    data_type: str = ""
    unit: str = ""
    description: str = ""
    plant_unit: str = ""
    eu_range: tuple[Any, Any] | None = None


@dataclass(frozen=True)
class ProviderSample:
    """Transport-neutral sample extracted from a provider result."""

    value: Any
    quality: Any = "GOOD"
    timestamp: float = 0.0
    stale: bool = False


def _timestamp(value: Any, fallback: float) -> float:
    if value is None:
        return fallback
    if isinstance(value, datetime):
        answer = value.timestamp()
    else:
        try:
            answer = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"invalid provider timestamp {value!r}") from error
    if (not math.isfinite(answer) or answer <= 0.0
            or answer > fallback + _MAX_FUTURE_TIMESTAMP_S):
        raise ValueError(f"invalid provider timestamp {value!r}")
    return answer


def _provider_sample(value: Any, now: float) -> ProviderSample:
    if isinstance(value, ProviderSample):
        return ProviderSample(
            value.value,
            value.quality,
            _timestamp(value.timestamp, now),
            value.stale,
        )
    if isinstance(value, Mapping):
        return ProviderSample(
            value.get("value"),
            value.get("quality", "GOOD"),
            _timestamp(value.get("timestamp", value.get("ts")), now),
            bool(value.get("stale", False)),
        )
    if isinstance(value, (tuple, list)):
        return ProviderSample(
            value[0] if value else None,
            value[1] if len(value) > 1 else "GOOD",
            _timestamp(value[2] if len(value) > 2 else None, now),
            bool(value[3]) if len(value) > 3 else False,
        )
    if hasattr(value, "value"):
        return ProviderSample(
            getattr(value, "value"),
            getattr(value, "quality", "GOOD"),
            _timestamp(getattr(value, "timestamp", getattr(value, "ts", None)),
                       now),
            bool(getattr(value, "stale", False)),
        )
    return ProviderSample(value, "GOOD", now, False)


def _config_path(raw: str, project_dir: Path) -> Path:
    variables = {
        "PROJECT_DIR": str(project_dir),
        "AREA_DIR": str(project_dir),
        "CONFIG_DIR": str(project_dir),
    }
    expanded = os.path.expandvars(Template(raw).safe_substitute(variables))
    path = Path(expanded)
    return (path if path.is_absolute() else project_dir / path).resolve()


def _normalise_direction(value: Any) -> str:
    direction = str(value or "").strip().lower()
    if direction in _READ_DIRECTIONS:
        return "read"
    if direction in _WRITE_DIRECTIONS:
        return "write"
    raise ProviderConfigurationError(f"unsupported signal direction {value!r}")


def _route(store_tag: str, spec: Mapping[str, Any]) -> SignalRoute:
    signal = str(spec.get("signal") or spec.get("source")
                 or spec.get("tag") or spec.get("name")
                 or store_tag).strip()
    if not store_tag or not signal:
        raise ProviderConfigurationError(
            "each signal route needs a store tag and provider signal")
    timeout = spec.get("stale_timeout_s", spec.get("stale_timeout_sec"))
    if timeout is not None:
        timeout = positive_finite(timeout, "signal stale_timeout_s")
    raw_range = spec.get("range")
    if raw_range is None and "lo" in spec and "hi" in spec:
        raw_range = (spec.get("lo"), spec.get("hi"))
    eu_range = None
    if raw_range is not None:
        if not isinstance(raw_range, (list, tuple)) or len(raw_range) != 2:
            raise ProviderConfigurationError(
                f"signal route {store_tag!r} range must contain two values")
        eu_range = (raw_range[0], raw_range[1])
    return SignalRoute(
        store_tag=store_tag,
        signal=signal,
        direction=_normalise_direction(spec.get("direction")),
        kind=str(spec.get("kind") or "").upper(),
        stale_timeout_s=timeout,
        data_type=str(spec.get("data_type") or "").upper(),
        unit=str(spec.get("unit") or spec.get("eu") or ""),
        description=str(spec.get("description") or ""),
        plant_unit=str(spec.get("plant_unit") or ""),
        eu_range=eu_range,
    )


def _catalog_routes(config: Mapping[str, Any], project_dir: Path, *, catalog_loader=None) \
        -> dict[str, SignalRoute]:
    # ``catalog`` is the concise project spelling; ``signal_catalog`` stays
    # available when a configuration also has other kinds of catalogues.
    if config.get("signal_catalog") and config.get("catalog"):
        raise ProviderConfigurationError(
            "declare signal_catalog or catalog, not both")
    catalog_config = config.get("signal_catalog") or config.get("catalog")
    if not catalog_config:
        return {}
    if isinstance(catalog_config, str):
        catalog_config = {"path": catalog_config}
    if not isinstance(catalog_config, Mapping):
        raise ProviderConfigurationError("signal_catalog must be a path or object")
    raw_path = str(catalog_config.get("path") or "").strip()
    if not raw_path:
        raise ProviderConfigurationError("signal_catalog.path is required")
    try:
        # Snapshot indexing supplies captured bytes, never a service-side path.
        document = (catalog_loader(raw_path) if catalog_loader is not None else
                    json.loads(_config_path(raw_path, project_dir).read_text(encoding="utf-8")))
    except (OSError, ValueError) as error:
        raise ProviderConfigurationError(
            f"cannot read signal catalog {raw_path}: {error}") from error
    records = document.get("tags", ()) if isinstance(document, Mapping) \
        else document
    if not isinstance(records, list):
        raise ProviderConfigurationError("signal catalog must contain a tags list")

    template = str(catalog_config.get("store_tag_template") or "{name}")
    routes: dict[str, SignalRoute] = {}
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise ProviderConfigurationError(
                f"signal catalog record {index} must be an object")
        name = str(record.get("name") or "").strip()
        if not name:
            raise ProviderConfigurationError(
                f"signal catalog record {index} needs a name")
        kind = str(record.get("kind") or "").upper()
        direction = record.get("direction")
        if not direction:
            if kind in {"AI", "DI"}:
                direction = "read"
            elif kind in {"AO", "DO"}:
                direction = "write"
            else:
                raise ProviderConfigurationError(
                    f"signal catalog record {name!r} needs a supported kind "
                    "or direction")
        try:
            store_tag = template.format_map({
                "name": name,
                "kind": kind,
                "plant_unit": str(record.get("plant_unit") or ""),
            })
        except (KeyError, ValueError) as error:
            raise ProviderConfigurationError(
                f"invalid signal_catalog.store_tag_template: {error}") from error
        route_spec = dict(record)
        route_spec.update({"signal": name, "direction": direction})
        route = _route(store_tag, route_spec)
        if route.store_tag in routes:
            raise ProviderConfigurationError(
                f"duplicate store tag {route.store_tag!r} in signal catalog")
        routes[route.store_tag] = route
    return routes


def _validate_routes(routes: Mapping[str, SignalRoute]) -> None:
    provider_signals: dict[str, str] = {}
    for route in routes.values():
        if route.kind and route.kind not in _KINDS_BY_DIRECTION[route.direction]:
            raise ProviderConfigurationError(
                f"{route.store_tag!r} kind {route.kind!r} contradicts its "
                f"{route.direction!r} direction")
        other = provider_signals.get(route.signal)
        if other is not None:
            raise ProviderConfigurationError(
                f"provider signal {route.signal!r} is routed by both "
                f"{other!r} and {route.store_tag!r}")
        provider_signals[route.signal] = route.store_tag


def load_signal_routes(config: Mapping[str, Any], project_dir: Path, *, catalog_loader=None) \
        -> dict[str, SignalRoute]:
    """Load one catalog authority plus optional sparse transport aliases."""
    routes = _catalog_routes(config, project_dir, catalog_loader=catalog_loader)
    has_catalog = bool(routes)
    signals_declared = "signals" in config and config.get("signals") is not None
    signals = config.get("signals") if signals_declared else {}
    if not isinstance(signals, Mapping):
        raise ProviderConfigurationError("field_io.signals must be an object")
    for store_tag, raw_spec in signals.items():
        key = str(store_tag)
        inherited = routes.get(key)
        if has_catalog and inherited is None:
            raise ProviderConfigurationError(
                f"explicit signal {key!r} is absent from the authoritative "
                "catalog")
        raw = raw_spec if isinstance(raw_spec, Mapping) \
            else {"signal": raw_spec}
        spec = dict(raw)
        if inherited is not None:
            explicit_direction = spec.get("direction")
            if (explicit_direction is not None
                    and _normalise_direction(explicit_direction)
                    != inherited.direction):
                raise ProviderConfigurationError(
                    f"explicit signal {key!r} direction drifts from catalog")
            explicit_kind = str(spec.get("kind") or "").upper()
            if explicit_kind and explicit_kind != inherited.kind:
                raise ProviderConfigurationError(
                    f"explicit signal {key!r} kind drifts from catalog")
            spec.setdefault("signal", inherited.signal)
            spec.setdefault("direction", inherited.direction)
            spec.setdefault("kind", inherited.kind)
            spec.setdefault("data_type", inherited.data_type)
            spec.setdefault("unit", inherited.unit)
            spec.setdefault("description", inherited.description)
            spec.setdefault("plant_unit", inherited.plant_unit)
            if inherited.eu_range is not None:
                spec.setdefault("range", inherited.eu_range)
            if inherited.stale_timeout_s is not None:
                spec.setdefault("stale_timeout_s", inherited.stale_timeout_s)
        route = _route(key, spec)
        routes[route.store_tag] = route
    if not routes:
        raise ProviderConfigurationError(
            "local_virtual_io requires signals or a signal_catalog")
    _validate_routes(routes)
    return routes


class LocalVirtualIoDriver:
    """Move configured signals between a store and an in-process provider."""

    STOP_JOIN_TIMEOUT_S = 5.0

    def __init__(self, store, config: Mapping[str, Any], project_dir: Path):
        self.store = store
        self.config = dict(config)
        self.project_dir = Path(project_dir).resolve()
        routes = load_signal_routes(self.config, self.project_dir)
        has_inputs = any(route.direction == "read" for route in routes.values())
        has_outputs = any(route.direction == "write" for route in routes.values())
        runtime_config = validate_virtual_io_runtime_config(
            self.config,
            has_inputs=has_inputs,
            has_outputs=has_outputs,
            project_dir=self.project_dir,
        )
        # A project-level acquisition freshness policy protects every input;
        # a per-route timeout may still tighten it for a critical signal.
        if runtime_config.input_stale_timeout_s is not None:
            routes = {
                tag: replace(
                    route,
                    stale_timeout_s=(route.stale_timeout_s
                                     or runtime_config.input_stale_timeout_s),
                ) if route.direction == "read" else route
                for tag, route in routes.items()
            }
        self.routes = routes
        self.signals = tuple(self.routes.values())
        self.read_routes = tuple(route for route in self.routes.values()
                                 if route.direction == "read")
        self.write_routes = {route.store_tag: route for route
                             in self.routes.values()
                             if route.direction == "write"}
        self.startup_mode = runtime_config.startup_mode
        self.period_s = runtime_config.period_s
        self.source = runtime_config.source
        self.claim_outputs = runtime_config.claim_outputs
        self.provider_manages_claim = runtime_config.provider_manages_claim
        self.name = str(self.config.get("name") or "Local Virtual I/O")
        self._session: ProviderSession | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._claimed = False
        self._lock = threading.RLock()
        self._simulation_lock = threading.RLock()
        self._simulations: dict[str, SignalSimulation] = {}
        self.running = False
        self.reads_published = 0
        self.outputs_seeded = 0
        self.writes_enqueued = 0
        self.output_readbacks_published = 0
        self.output_readback_failures = 0
        self.write_failures = 0
        self.local_writes_applied = 0
        self.read_failures = 0
        self.last_error = ""

    @property
    def transport(self):
        return None if self._session is None else self._session.transport

    def start(self, timeout: float = 0.0) -> bool:  # noqa: ARG002
        """Load, claim and seed the transport; repeated starts are harmless."""
        with self._lock:
            if self.running:
                return True
            self._stop.clear()
            # A recovered provider must not keep advertising the fault from
            # its previous attempt.  Any new startup/read fault below replaces
            # this marker before control returns to Explorer.
            self.last_error = ""
            try:
                session = load_provider(self.config, self.project_dir)
                self._session = session
                transport = session.transport
                if hasattr(transport, "source"):
                    # The holder name is project configuration, not an
                    # adapter default.  Keeping these identical is what makes
                    # the provider's arbitration meaningful.
                    transport.source = self.source
                # Driver-managed arbitration must be secured before any
                # provider runtime starts; otherwise an executive can consume
                # output commands during an unowned startup window.
                if (self.claim_outputs
                        and not self.provider_manages_claim
                        and not self._claim()):
                    raise RuntimeError(
                        f"provider output side is held by another source; "
                        f"{self.source!r} was not registered")
                session.start()
                if (self.claim_outputs
                        and self.provider_manages_claim
                        and not self._claim()):
                    raise RuntimeError(
                        f"provider output side is held by another source; "
                        f"{self.source!r} was not registered")
                # The controller initialises after this method returns. Seed
                # every AO/DO from its actual lined-up value first, otherwise
                # block defaults (usually zero/False) create a startup bump.
                self._seed_output_readbacks()
                self.scan_once()
            except Exception as error:  # noqa: BLE001
                self.last_error = str(error)
                log.exception("Local Virtual I/O did not start: %s", error)
                # Release while the session still exposes its transport.
                # Cleanup first used to erase the only object capable of
                # releasing a driver-managed claim.
                self._release()
                self._cleanup_session()
                return False
            self.running = True
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name="local-virtual-io",
            )
            self._thread.start()
            log.info(
                "Local Virtual I/O attached: %d input(s), %d output(s), "
                "source=%s", len(self.read_routes), len(self.write_routes),
                self.source,
            )
            return True

    def stop(self) -> None:
        """Stop cleanly; safe before start and safe to call more than once."""
        with self._lock:
            self._stop.set()
            thread = self._thread
        if thread is threading.current_thread():
            self.last_error = (
                "field I/O scan worker cannot tear down its own provider")
            log.error(self.last_error)
            return
        if thread is not None:
            thread.join(timeout=min(
                self.STOP_JOIN_TIMEOUT_S,
                max(2.0, self.period_s * 3.0),
            ))
            if thread.is_alive():
                # Releasing the lease or stopping its transport while a scan
                # is still inside foreign read/write code is a use-after-stop
                # race.  Leave ownership and the diagnostic marker attached;
                # a later stop can finish once the worker returns.
                self.last_error = (
                    "field I/O scan worker did not stop; provider remains "
                    "attached")
                log.error(self.last_error)
                return
        with self._lock:
            self._thread = None
            self.running = False
            self._release()
            self._cleanup_session()
            # Values remain useful for diagnosis after disconnect, but they
            # must stop looking live.  Preserving the last value while
            # changing its acquisition state is the same contract used for a
            # failed read during a running session.
            for route in self.signals:
                self.store.mark_sample_stale(route.store_tag)
            # A stopped configured field side remains the queue owner.  If it
            # disappeared here, the executive would apply AO/DO demands as
            # ordinary local writes and Explorer could no longer restart it.

    def _cleanup_session(self) -> None:
        session, self._session = self._session, None
        if session is not None:
            try:
                session.stop()
            except Exception:  # noqa: BLE001
                log.exception("Local Virtual I/O provider stop failed")

    def _claim(self) -> bool:
        if self.provider_manages_claim:
            # session.start() is the acquire operation and session.stop() is
            # its matching release.  Registering the bus again can make a
            # second adapter appear to share a lease by source name.
            self._claimed = True
            return True
        targets = [self._session.product, self.transport]
        for target in targets:
            claim = getattr(target, "claim", None)
            if callable(claim):
                accepted = claim(self.source)
                self._claimed = accepted is not False
                return self._claimed
        bus = getattr(self.transport, "bus", None)
        claim = getattr(bus, "register_dcs", None)
        if callable(claim):
            self._claimed = claim(self.source) is not False
            return self._claimed
        # Providers without arbitration may explicitly opt out.  Silently
        # pretending to claim one would defeat the one-holder contract.
        return not self.claim_outputs

    def _release(self) -> None:
        if not self._claimed:
            return
        if self.provider_manages_claim:
            # The provider's stop lifecycle performs the matching release.
            self._claimed = False
            return
        targets = [self._session.product if self._session else None,
                   self.transport]
        for target in targets:
            release = getattr(target, "release", None)
            if callable(release):
                release(self.source)
                self._claimed = False
                return
        bus = getattr(self.transport, "bus", None)
        release = getattr(bus, "release_dcs", None)
        if callable(release):
            release(self.source)
        self._claimed = False

    def _run(self) -> None:
        while not self._stop.wait(self.period_s):
            try:
                self.scan_once()
            except Exception as error:  # noqa: BLE001
                self.last_error = str(error)
                log.exception("Local Virtual I/O scan failed: %s", error)

    def scan_once(self) -> None:
        """One deterministic store exchange; public for qualification tests."""
        transport = self.transport
        if transport is None:
            self.service_pending_writes()
            return
        self._service_simulations()
        self._drain_store_writes(transport)
        publish = getattr(transport, "publish_inputs", None)
        inputs_available = True
        if callable(publish):
            try:
                publish()
            except Exception as error:  # noqa: BLE001
                self.read_failures += len(self.read_routes)
                self.last_error = f"input publication failed: {error}"
                for route in self.read_routes:
                    self.store.mark_sample_stale(route.store_tag)
                inputs_available = False
        now = time.time()
        scan_routes = (tuple(self.read_routes) if inputs_available else ()) \
            + tuple(self.write_routes.values())
        samples, failures = self._read_many(scan_routes, now)
        if inputs_available:
            for route in self.read_routes:
                error = failures.get(route.signal)
                if error is not None:
                    self.read_failures += 1
                    self.last_error = f"{route.signal}: {error}"
                    self.store.mark_sample_stale(route.store_tag)
                    continue
                self._publish_sample(route, samples[route.signal], now)
                self.reads_published += 1
        # AO/DO store state is authoritative provider readback, not the value
        # merely requested by the controller.  A queued provider can clamp,
        # reject or apply the demand on a later plant step.
        for route in self.write_routes.values():
            error = failures.get(route.signal)
            if error is not None:
                self.output_readback_failures += 1
                self.last_error = f"{route.signal}: {error}"
                self.store.mark_sample_stale(route.store_tag)
                continue
            try:
                sample = samples[route.signal]
            except KeyError:
                error = RuntimeError("provider omitted batch readback")
                self.output_readback_failures += 1
                self.last_error = f"{route.signal}: {error}"
                self.store.mark_sample_stale(route.store_tag)
                continue
            self._publish_sample(route, sample, now)
            self.output_readbacks_published += 1

    # ----------------------------------------------------- signal simulation
    def _simulation_target(self):
        return self._session.product if self._session is not None else None

    def simulation_capabilities(self) -> dict[str, Any]:
        """Describe the optional input-simulation contract for Explorer."""
        target = self._simulation_target()
        available = bool(
            self.running
            and callable(getattr(target, "set_simulated_input", None))
            and callable(getattr(target, "clear_simulated_input", None))
        )
        with self._simulation_lock:
            active = len(self._simulations)
        return {
            "available": available,
            "running": self.running,
            "inputs": len(self.read_routes),
            "outputs": len(self.write_routes),
            "modes": ("static", "sawtooth", "square", "sine"),
            "qualities": ("GOOD", "UNCERTAIN", "BAD"),
            "active": active,
            "reason": "" if available else (
                "Start the provider to simulate input channels."
                if not self.running else
                "The configured provider does not support input simulation."
            ),
        }

    def _apply_simulation(self, profile: SignalSimulation,
                          now: float | None = None) -> None:
        route = self.routes.get(profile.store_tag)
        if route is None or route.direction != "read":
            raise ValueError(
                f"{profile.store_tag!r} is not a configured input channel")
        target = self._simulation_target()
        setter = getattr(target, "set_simulated_input", None)
        if not self.running or not callable(setter):
            raise RuntimeError(
                self.simulation_capabilities()["reason"])
        value = profile.sample(
            time.monotonic() if now is None else now,
            discrete=route.kind == "DI",
        )
        setter(route.signal, value, profile.quality)

    def _service_simulations(self) -> None:
        now = time.monotonic()
        with self._simulation_lock:
            profiles = tuple(self._simulations.values())
        for profile in profiles:
            self._apply_simulation(profile, now)

    def configure_signal_simulation(
        self,
        store_tag: str,
        *,
        mode: str = "static",
        value: float | bool = 0.0,
        low: float | None = None,
        high: float | None = None,
        period_s: float = 10.0,
        quality: str = "GOOD",
    ) -> SignalSimulation:
        """Configure one AI/DI without coupling callers to the provider."""
        route = self.routes.get(str(store_tag))
        if route is None or route.direction != "read":
            raise ValueError(f"{store_tag!r} is not a configured input")
        route_low, route_high = route.eu_range or (0.0, 1.0)
        profile = SignalSimulation(
            store_tag=route.store_tag,
            mode=mode,
            low=float(route_low if low is None else low),
            high=float(route_high if high is None else high),
            period_s=float(period_s),
            value=value,
            quality=quality,
            started_at=time.monotonic(),
        )
        self._apply_simulation(profile)
        with self._simulation_lock:
            self._simulations[route.store_tag] = profile
        log.info(
            "Virtual I/O simulation configured: %s mode=%s quality=%s",
            route.store_tag, profile.mode, profile.quality,
        )
        return profile

    def clear_signal_simulation(self, store_tag: str) -> bool:
        """Release one simulated input and expose its live physics again."""
        route = self.routes.get(str(store_tag))
        if route is None or route.direction != "read":
            raise ValueError(f"{store_tag!r} is not a configured input")
        with self._simulation_lock:
            existed = self._simulations.pop(route.store_tag, None) is not None
        target = self._simulation_target()
        clear = getattr(target, "clear_simulated_input", None)
        if callable(clear):
            clear(route.signal)
        if existed:
            log.info("Virtual I/O simulation released: %s", route.store_tag)
        return existed

    def clear_all_signal_simulations(self) -> int:
        with self._simulation_lock:
            names = tuple(self._simulations)
        for name in names:
            self.clear_signal_simulation(name)
        return len(names)

    def signal_simulation_snapshot(self) -> list[dict[str, Any]]:
        """Return all channels with live value and simulation state."""
        samples = self.store.get_samples() if callable(
            getattr(self.store, "get_samples", None)) else {}
        with self._simulation_lock:
            profiles = dict(self._simulations)
        rows: list[dict[str, Any]] = []
        for route in self.signals:
            sample = samples.get(route.store_tag)
            quality = getattr(sample, "quality", "") if sample else ""
            quality = getattr(quality, "name", quality)
            profile = profiles.get(route.store_tag)
            rows.append({
                "store_tag": route.store_tag,
                "signal": route.signal,
                "direction": route.direction,
                "kind": route.kind,
                "data_type": route.data_type,
                "unit": route.unit,
                "plant_unit": route.plant_unit,
                "description": route.description,
                "range": route.eu_range,
                "value": getattr(sample, "value", self.store.get(
                    route.store_tag, None)),
                "quality": str(quality or ""),
                "stale": bool(getattr(sample, "stale", False)),
                "simulation": profile.document() if profile else None,
                "simulatable": route.direction == "read",
            })
        return rows

    # ------------------------------------------------ process simulation host
    def process_simulation_capabilities(self) -> dict[str, Any]:
        """Describe an optional dynamic-process control surface.

        The workbench talks to this driver, never to a simulator package. A
        different Local Virtual I/O provider can opt in by implementing the
        same methods on its product object.
        """
        target = self._simulation_target()
        describe = getattr(target, "simulation_control_capabilities", None)
        if callable(describe):
            result = dict(describe())
            result.setdefault("available", bool(self.running))
            result.setdefault("reason", "")
            return result
        return {
            "available": False,
            "running": False,
            "paused": True,
            "speed_factor": 1.0,
            "step_supported": False,
            "snapshot_supported": False,
            "reason": (
                "Start the provider first." if not self.running else
                "The configured provider does not expose process controls."
            ),
        }

    def _process_command(self, method: str, *args):
        target = self._simulation_target()
        command = getattr(target, method, None)
        if not self.running or not callable(command):
            raise RuntimeError(self.process_simulation_capabilities()["reason"])
        return command(*args)

    def pause_process_simulation(self) -> None:
        self._process_command("pause_simulation")

    def process_model_catalog(self) -> dict[str, Any]:
        return dict(self._process_command("simulation_model_catalog"))

    def process_model_state(self) -> dict[str, Any]:
        return dict(self._process_command("simulation_model_state"))

    def set_process_disturbance(self, identity: str, active: bool,
                               value: float | None = None) -> None:
        self._process_command("set_simulation_disturbance", identity, active, value)

    def resume_process_simulation(self) -> None:
        self._process_command("resume_simulation")

    def step_process_simulation(self, count: int = 1) -> None:
        self._process_command("step", int(count))

    def set_process_simulation_speed(self, factor: float) -> float:
        return float(self._process_command("set_simulation_speed", factor))

    def save_process_simulation_snapshot(self, path: Path | str) -> Path:
        return Path(self._process_command(
            "save_simulation_snapshot", path)).resolve()

    def load_process_simulation_snapshot(self, path: Path | str) -> dict:
        result = self._process_command("load_simulation_snapshot", path)
        return dict(result or {})

    def save_simulation_profile(self, path: Path | str) -> Path:
        with self._simulation_lock:
            profiles = dict(self._simulations)
        return write_profile(path, profiles)

    def load_simulation_profile(self, path: Path | str) -> int:
        """Validate a whole scenario, then replace the active set."""
        loaded = read_profile(path, started_at=time.monotonic())
        for name in loaded:
            route = self.routes.get(name)
            if route is None or route.direction != "read":
                raise ValueError(
                    f"scenario signal {name!r} is not a configured input")
        if loaded and not self.simulation_capabilities()["available"]:
            raise RuntimeError(self.simulation_capabilities()["reason"])
        with self._simulation_lock:
            previous = dict(self._simulations)
        self.clear_all_signal_simulations()
        applied: list[str] = []
        try:
            for profile in loaded.values():
                self._apply_simulation(profile)
                applied.append(profile.store_tag)
        except Exception:
            for name in applied:
                try:
                    self.clear_signal_simulation(name)
                except Exception:  # noqa: BLE001
                    log.exception(
                        "Could not unwind Virtual I/O scenario signal %s",
                        name,
                    )
            for profile in previous.values():
                try:
                    self._apply_simulation(profile)
                except Exception:  # noqa: BLE001
                    log.exception(
                        "Could not restore Virtual I/O simulation %s",
                        profile.store_tag,
                    )
            with self._simulation_lock:
                self._simulations = previous
            raise
        with self._simulation_lock:
            self._simulations = loaded
        return len(loaded)

    def _seed_output_readbacks(self) -> None:
        """Publish every output's actual state once, before controller scan."""
        now = time.time()
        failed: list[str] = []
        samples, failures = self._read_many(
            tuple(self.write_routes.values()), now)
        for route in self.write_routes.values():
            try:
                error = failures.get(route.signal)
                if error is not None:
                    raise error
                sample = samples[route.signal]
                self._validate_output_seed(route, sample, now)
            except Exception as error:  # noqa: BLE001
                failed.append(f"{route.signal}: {error}")
                continue
            self._publish_sample(route, sample, now)
            self.outputs_seeded += 1
        if failed:
            preview = "; ".join(failed[:5])
            if len(failed) > 5:
                preview += f"; and {len(failed) - 5} more"
            raise RuntimeError(
                "cannot seed configured output readback(s): " + preview)

    @staticmethod
    def _validate_output_seed(route: SignalRoute, sample: ProviderSample,
                              now: float) -> None:
        quality = getattr(sample.quality, "name", sample.quality)
        if isinstance(quality, str):
            good = quality.strip().upper() in {"GOOD", "OK"}
        elif isinstance(quality, bool):
            good = False
        else:
            try:
                good = int(quality) == 0
            except (TypeError, ValueError):
                good = False
        stale = sample.stale
        if route.stale_timeout_s is not None:
            stale = stale or now - sample.timestamp > route.stale_timeout_s
        if stale or not good:
            raise ValueError(
                f"startup readback quality is not good for {route.signal}")
        if route.kind == "DO":
            if type(sample.value) is not bool:  # noqa: E721
                raise ValueError(
                    f"startup readback for {route.signal} must be Boolean")
            return
        if route.kind == "AO" or not route.kind:
            if isinstance(sample.value, bool):
                if route.kind == "AO":
                    raise ValueError(
                        f"startup readback for {route.signal} must be finite")
                return
            try:
                value = float(sample.value)
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"startup readback for {route.signal} must be finite") \
                    from error
            if not math.isfinite(value):
                raise ValueError(
                    f"startup readback for {route.signal} must be finite")

    def _publish_sample(self, route: SignalRoute, sample: ProviderSample,
                        now: float) -> None:
        stale = sample.stale
        if route.stale_timeout_s is not None:
            stale = stale or now - sample.timestamp > route.stale_timeout_s
        self.store.set_sample(
            route.store_tag,
            sample.value,
            quality="BAD" if stale else sample.quality,
            timestamp=sample.timestamp,
            stale=stale,
        )

    def _read(self, signal: str, _now: float) -> ProviderSample:
        transport = self.transport
        read_sample = getattr(transport, "read_sample", None)
        if callable(read_sample):
            raw_sample = read_sample(signal)
            # A provider read can block behind the plant's snapshot lock.  Its
            # source timestamp is consequently allowed to be newer than the
            # scan time captured before that wait; validate it against the
            # time at which the sample actually crossed the boundary.
            return _provider_sample(raw_sample, time.time())
        bus = getattr(transport, "bus", None)
        bus_read = getattr(bus, "read", None)
        if callable(bus_read):
            raw_sample = bus_read(signal)
            return _provider_sample(raw_sample, time.time())
        raw_sample = transport.read(signal)
        return _provider_sample(raw_sample, time.time())

    def _read_many(self, routes: tuple[SignalRoute, ...], now: float) \
            -> tuple[dict[str, ProviderSample], dict[str, Exception]]:
        """Acquire one coherent route set, falling back per signal safely."""
        if not routes:
            return {}, {}
        transport = self.transport
        read_samples = getattr(transport, "read_samples", None)
        if not callable(read_samples):
            samples: dict[str, ProviderSample] = {}
            failures: dict[str, Exception] = {}
            for route in routes:
                try:
                    samples[route.signal] = self._read(route.signal, now)
                except Exception as error:  # noqa: BLE001
                    failures[route.signal] = error
            return samples, failures

        signals = [route.signal for route in routes]
        try:
            answer = read_samples(signals)
        except Exception as error:  # noqa: BLE001
            return {}, {signal: error for signal in signals}
        observed_at = time.time()
        if isinstance(answer, Mapping):
            raw_samples = {signal: answer[signal] for signal in signals
                           if signal in answer}
        elif isinstance(answer, (list, tuple)) and len(answer) == len(signals):
            raw_samples = dict(zip(signals, answer, strict=True))
        else:
            error = RuntimeError(
                "provider read_samples must return a mapping or aligned list")
            return {}, {signal: error for signal in signals}

        samples = {}
        failures = {}
        for signal in signals:
            if signal not in raw_samples:
                failures[signal] = RuntimeError(
                    "provider omitted batch readback")
                continue
            try:
                samples[signal] = _provider_sample(
                    raw_samples[signal], observed_at)
            except Exception as error:  # noqa: BLE001
                failures[signal] = error
        return samples, failures

    def service_pending_writes(self) -> int:
        """Apply local writes while a failed provider remains declared.

        The driver stays attached after a failed start so control cannot fall
        through to a fake local field.  It still owns the destructive queue;
        this service rejects its known AO/DO routes and applies unrelated
        faceplate/configuration writes so those controls do not deadlock.
        """
        with self._lock:
            if self.running or self._session is not None:
                return 0
            return self._drain_store_writes(None)

    def _drain_store_writes(self, transport) -> int:
        drain = getattr(self.store, "drain_writes", None)
        if not callable(drain):
            return 0
        applied_before = self.local_writes_applied
        unavailable: list[str] = []
        for item in drain() or ():
            try:
                key, value = item
            except (TypeError, ValueError):  # pragma: no cover - corrupt peer
                continue
            route = self.write_routes.get(key)
            if route is None:
                # The queue also carries faceplate setpoints and tuning
                # write-backs.  A field driver must not swallow those merely
                # because it owns the destructive drain.
                self.store.set(key, value)
                self.local_writes_applied += 1
                continue
            if transport is None:
                self.write_failures += 1
                unavailable.append(route.signal)
                continue
            try:
                accepted = transport.write(route.signal, value)
                if accepted is False:
                    raise RuntimeError("provider did not accept the demand")
            except Exception as error:  # noqa: BLE001
                # The destructive queue has already been drained.  Record an
                # honest failed attempt and continue so a field failure cannot
                # swallow unrelated setpoint/tuning writes later in the same
                # batch.  Retrying here could duplicate a write from a
                # provider that applied it before raising.
                self.write_failures += 1
                self.last_error = f"{route.signal}: {error}"
                log.error("Local Virtual I/O output write failed: %s",
                          self.last_error)
                continue
            self.writes_enqueued += 1
        if unavailable:
            self.last_error = (
                "provider unavailable: rejected "
                f"{len(unavailable)} field output demand(s)")
            log.warning("Local Virtual I/O %s", self.last_error)
        return self.local_writes_applied - applied_before

    def status(self) -> str:
        state = "running" if self.running else "stopped"
        return (f"{state}: {len(self.read_routes)} input(s), "
                f"{len(self.write_routes)} output(s), "
                f"{self.writes_enqueued} write demand(s) enqueued")

    def health(self) -> dict[str, Any]:
        """Provider-neutral health for Explorer and diagnostics."""
        provider_health: Any = {}
        # A composed runtime can add engine heartbeat/SIS detail beyond the
        # adapter's counters, so prefer it while keeping a plain adapter valid.
        product = self._session.product if self._session is not None else None
        transport = self.transport
        health = getattr(product, "health", None) \
            or getattr(transport, "health", None)
        if callable(health):
            try:
                answer = health()
                provider_health = (dict(answer) if isinstance(answer, Mapping)
                                   else dict(vars(answer))
                                   if hasattr(answer, "__dict__") else answer)
            except Exception as error:  # noqa: BLE001
                provider_health = {"last_error": str(error)}
        return {
            "name": self.name,
            "type": "local_virtual_io",
            "running": self.running,
            "can_start": True,
            "source": self.source,
            "signals": len(self.signals),
            "inputs": len(self.read_routes),
            "outputs": len(self.write_routes),
            "reads_published": self.reads_published,
            "outputs_seeded": self.outputs_seeded,
            "writes_enqueued": self.writes_enqueued,
            "output_readbacks_published": self.output_readbacks_published,
            "output_readback_failures": self.output_readback_failures,
            "write_failures": self.write_failures,
            "local_writes_applied": self.local_writes_applied,
            "read_failures": self.read_failures,
            "last_error": self.last_error,
            "write_queue": self.store.write_queue_health()
            if callable(getattr(self.store, "write_queue_health", None))
            else {},
            "provider": provider_health,
        }


class UnavailableLocalVirtualIoDriver:
    """Non-running marker that prevents fallback after invalid VIO config."""

    def __init__(self, store, config: Mapping[str, Any], error: Exception,
                 project_dir: Path | None = None):
        self.store = store
        self.config = dict(config)
        self.name = str(self.config.get("name") or "Local Virtual I/O")
        self.source = str(self.config.get("source") or "")
        self.startup_mode = str(
            self.config.get("startup_mode") or "automatic").strip().lower()
        routes: dict[str, SignalRoute] = {}
        if project_dir is not None:
            try:
                routes = load_signal_routes(self.config, Path(project_dir))
            except Exception:  # noqa: BLE001
                # Preserve the original configuration error.  Raw explicit
                # routes below still let an unavailable driver distinguish
                # field outputs from local faceplate/configuration writes.
                pass
        if not routes:
            raw_signals = self.config.get("signals") or {}
            if isinstance(raw_signals, Mapping):
                for store_tag, raw_spec in raw_signals.items():
                    spec = raw_spec if isinstance(raw_spec, Mapping) \
                        else {"signal": raw_spec}
                    try:
                        route = _route(str(store_tag), spec)
                    except ProviderConfigurationError:
                        continue
                    routes[route.store_tag] = route
        self.routes = routes
        self.signals = tuple(routes.values())
        self.write_routes = {
            route.store_tag: route for route in routes.values()
            if route.direction == "write"
        }
        self.running = False
        self.last_error = str(error)
        self.write_failures = 0
        self.local_writes_applied = 0

    def start(self, timeout: float = 0.0) -> bool:  # noqa: ARG002
        return False

    def stop(self) -> None:
        # This marker is still the declared field side while unavailable; it
        # must continue rejecting its AO/DO routes instead of permitting the
        # executive's local-write fallback.
        return None

    def service_pending_writes(self) -> int:
        drain = getattr(self.store, "drain_writes", None)
        if not callable(drain):
            return 0
        applied = 0
        rejected = 0
        for item in drain() or ():
            try:
                key, value = item
            except (TypeError, ValueError):  # pragma: no cover - corrupt peer
                continue
            if key in self.write_routes:
                self.write_failures += 1
                rejected += 1
                continue
            self.store.set(key, value)
            self.local_writes_applied += 1
            applied += 1
        if rejected:
            log.warning(
                "Unavailable Local Virtual I/O rejected %d field output "
                "demand(s)", rejected)
        return applied

    def status(self) -> str:
        return f"unavailable: {self.last_error}"

    def health(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": "local_virtual_io",
            "running": False,
            "can_start": False,
            "source": self.source,
            "signals": len(self.signals),
            "outputs": len(self.write_routes),
            "write_failures": self.write_failures,
            "local_writes_applied": self.local_writes_applied,
            "last_error": self.last_error,
            "write_queue": self.store.write_queue_health()
            if callable(getattr(self.store, "write_queue_health", None))
            else {},
            "provider": {},
        }
