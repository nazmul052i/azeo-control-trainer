"""Thread-safe data bridge between simulation, OPC UA, and dashboard.

All access goes through get/set methods which use a lock.
Trend data is stored in fixed-length deques.
"""
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Any

import numpy as np

from .communication_faults import CommunicationFailure


@dataclass(frozen=True)
class SignalSample:
    """One field observation, including the information control acts on.

    Field transports used to publish only ``value``.  A frozen number from a
    failed channel therefore looked Good forever.  Keeping the provider's
    quality and source timestamp beside the value lets ``DataBridge`` carry
    the actual channel status onto AI/DI terminals without teaching control
    blocks which transport supplied it.
    """

    value: Any
    quality: Any = "GOOD"
    timestamp: float = 0.0
    stale: bool = False


class SharedDataStore:
    """Thread-safe data bridge between simulation, OPC UA, and dashboard.

    All access goes through get/set methods which use a lock.
    Trend data is stored in fixed-length deques.
    """

    TREND_LENGTH = 3600  # 1 hour at 1 sample/s
    MAX_PENDING_WRITES = 4096

    def __init__(self):
        self._lock = threading.Lock()
        self._data = {}
        self._samples: dict[str, SignalSample] = {}
        self._trends = {}
        # Writes are state demands, not an event log.  Keeping only the
        # newest value for each tag prevents a stopped field transport from
        # replaying minutes of stale AO/DO history after it recovers.  The
        # distinct-tag limit still bounds a malformed peer that invents keys.
        self._external_writes: OrderedDict[str, Any] = OrderedDict()
        self._coalesced_writes = 0
        self._write_queue_rejections = 0
        self._pending_write_peak = 0
        self._alarms: list[dict] = []
        self._alarm_id_counter = 0
        self._sim_speed = 1.0
        self._sim_running = True
        self._sim_paused = False
        self._sim_time = 0.0
        self._sim_started = False  # True after simulation thread launched
        self._sim_engine = None    # Engine instance, set by the launcher
        self._strategy_runtime = None  # Set by StrategyDesignerTab
        self._strategy_runtimes: list = []  # Thread-safe via _lock

        # PV freeze/override support
        self._frozen: dict[str, float] = {}      # key -> frozen value
        self._overrides: dict[str, float] = {}    # key -> override value

        # Sensor noise + analyzer dead-time/sampling support.  Engines that
        # opt in install a SensorBank via store.sensor_bank = SensorBank(...);
        # the engine's publish path consults ``noise_enabled`` to decide
        # whether to apply transforms.  Default ON for runtime; tests that
        # depend on bit-exact step responses set this False.
        self.sensor_bank = None
        self.noise_enabled = True

        # Engineering-only communication failure injection.  Providers keep
        # publishing their real values underneath; the controller-facing read
        # holds the last delivered value with Bad/stale quality, and failed
        # output links discard demands before a transport can drain them.
        self._communication_failures: dict[str, CommunicationFailure] = {}
        self._communication_holds: dict[tuple[str, str], Any] = {}
        self._communication_hold_times: dict[tuple[str, str], float] = {}

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            if key in self._overrides:
                return self._overrides[key]
            if key in self._frozen:
                return self._frozen[key]
            failure = self._communication_failure_for(key, "input")
            if failure is not None:
                return self._communication_holds.get(
                    (failure.failure_id, key), self._data.get(key, default))
            return self._data.get(key, default)

    def set(self, key: str, value: Any):
        with self._lock:
            self._capture_communication_hold(key, value)
            # Skip writing to frozen/overridden keys so the sim
            # engine doesn't overwrite them during publish.
            if key in self._overrides or key in self._frozen:
                self._data[key] = value  # store real value internally
                return
            self._data[key] = value

    def get_all(self) -> dict:
        with self._lock:
            result = dict(self._data)
            for key in result:
                failure = self._communication_failure_for(key, "input")
                if failure is not None:
                    result[key] = self._communication_holds.get(
                        (failure.failure_id, key), result[key])
            # Layer overrides and freezes on top
            result.update(self._frozen)
            result.update(self._overrides)
            return result

    def set_many(self, updates: dict):
        with self._lock:
            for key, value in updates.items():
                self._capture_communication_hold(key, value)
            self._data.update(updates)

    def set_sample(
        self,
        key: str,
        value: Any,
        *,
        quality: Any = "GOOD",
        timestamp: float | None = None,
        stale: bool = False,
    ) -> SignalSample:
        """Atomically publish a field value and its acquisition metadata."""
        sample = SignalSample(
            value=value,
            quality=quality,
            timestamp=float(time.time() if timestamp is None else timestamp),
            stale=bool(stale),
        )
        with self._lock:
            self._capture_communication_hold(
                key, value, timestamp=sample.timestamp)
            # Preserve the existing freeze/override semantics: the real field
            # value is retained underneath, while ``get`` still presents the
            # engineering override to its callers.
            self._data[key] = value
            self._samples[key] = sample
        return sample

    def mark_sample_stale(
        self,
        key: str,
        *,
        quality: Any = "BAD",
        timestamp: float | None = None,
    ) -> SignalSample | None:
        """Mark an existing field observation stale without inventing a value."""
        with self._lock:
            previous = self._samples.get(key)
            if previous is None and key not in self._data:
                return None
            sample = SignalSample(
                value=(previous.value if previous is not None
                       else self._data[key]),
                quality=quality,
                timestamp=float(
                    previous.timestamp
                    if timestamp is None and previous is not None
                    else time.time() if timestamp is None else timestamp
                ),
                stale=True,
            )
            self._samples[key] = sample
            return sample

    def get_sample(self, key: str) -> SignalSample | None:
        """Return the latest immutable field sample for ``key``."""
        with self._lock:
            sample = self._samples.get(key)
            failure = self._communication_failure_for(key, "input")
            if failure is None:
                return sample
            if sample is None and key not in self._data:
                return None
            held = self._communication_holds.get(
                (failure.failure_id, key),
                sample.value if sample is not None else self._data[key])
            timestamp = self._communication_hold_times.get(
                (failure.failure_id, key),
                sample.timestamp if sample is not None else failure.injected_at)
            return SignalSample(
                value=held, quality="BAD", timestamp=timestamp, stale=True)

    def get_samples(self) -> dict[str, SignalSample]:
        """Return a snapshot of all field acquisition metadata."""
        with self._lock:
            keys = set(self._samples) | set(self._data)
            result: dict[str, SignalSample] = {}
            for key in keys:
                sample = self._samples.get(key)
                failure = self._communication_failure_for(key, "input")
                if failure is None:
                    if sample is not None:
                        result[key] = sample
                    continue
                held = self._communication_holds.get(
                    (failure.failure_id, key),
                    sample.value if sample is not None else self._data[key])
                result[key] = SignalSample(
                    value=held, quality="BAD",
                    timestamp=self._communication_hold_times.get(
                        (failure.failure_id, key),
                        sample.timestamp if sample is not None
                        else failure.injected_at),
                    stale=True)
            return result

    # --------------------------------------- communication failure simulation
    def inject_communication_failure(
        self, tags: str | list[str] | tuple[str, ...] = "*", *,
        direction: str = "both",
        reason: str = "Simulated communication failure",
    ) -> list[str]:
        """Inject provider-neutral input/output link failures.

        Input links hold the value visible at injection and report Bad/stale.
        Output links drop future queued demands.  ``*`` applies to all tags;
        exact tag scopes make a single-channel drill possible.
        """
        if isinstance(tags, str):
            requested = [tags]
        else:
            requested = list(tags)
        if not requested:
            raise ValueError("at least one tag or '*' is required")
        created: list[str] = []
        with self._lock:
            for tag in requested:
                failure = CommunicationFailure(
                    tag=str(tag), direction=direction, reason=reason)
                self._communication_failures[failure.failure_id] = failure
                if failure.direction in {"input", "both"}:
                    keys = list(self._data) if failure.tag == "*" else [failure.tag]
                    for key in keys:
                        if key in self._data:
                            token = (failure.failure_id, key)
                            self._communication_holds[token] = self._data[key]
                            sample = self._samples.get(key)
                            self._communication_hold_times[token] = (
                                sample.timestamp if sample is not None
                                else failure.injected_at)
                created.append(failure.failure_id)
        return created

    def clear_communication_failure(self, failure_id: str | None = None) -> int:
        """Clear one injected failure, or all failures when id is omitted."""
        with self._lock:
            if failure_id is None:
                ids = set(self._communication_failures)
            elif failure_id in self._communication_failures:
                ids = {failure_id}
            else:
                ids = set()
            for item in ids:
                self._communication_failures.pop(item, None)
            for key in list(self._communication_holds):
                if key[0] in ids:
                    self._communication_holds.pop(key, None)
                    self._communication_hold_times.pop(key, None)
            return len(ids)

    def communication_failures(self) -> list[dict]:
        """Return an audit-friendly snapshot of active injected failures."""
        with self._lock:
            return [failure.to_dict() for failure in
                    self._communication_failures.values()]

    def _communication_failure_for(
        self, tag: str, direction: str,
    ) -> CommunicationFailure | None:
        """Return the most recently injected matching failure.

        Caller holds ``_lock``.  Exact channel failures take precedence over
        a full-link failure so their reason and dropped count stay meaningful.
        """
        matches = [failure for failure in self._communication_failures.values()
                   if failure.affects(tag, direction)]
        if not matches:
            return None
        return max(matches, key=lambda failure: (
            failure.tag != "*", failure.injected_at))

    def _capture_communication_hold(
        self, tag: str, incoming: Any, *, timestamp: float | None = None,
    ) -> None:
        """Latch the first observation of a point created during an outage.

        Failure injection normally snapshots existing points immediately. A
        wildcard failure can also precede provider discovery, though. Without
        this late-discovery latch the controller would see every later value
        through the failed link, merely marked Bad, which is not a held input.
        Caller holds ``_lock``.
        """
        for failure in self._communication_failures.values():
            if not failure.affects(tag, "input"):
                continue
            token = (failure.failure_id, tag)
            if token in self._communication_holds:
                continue
            self._communication_holds[token] = self._data.get(tag, incoming)
            previous = self._samples.get(tag)
            self._communication_hold_times[token] = (
                previous.timestamp if previous is not None
                else float(timestamp if timestamp is not None
                           else failure.injected_at))

    # --------------------------------------------------------- PV freeze/override
    def freeze(self, key: str):
        """Freeze current value of key -- get() returns the frozen value."""
        with self._lock:
            self._frozen[key] = self._data.get(key, 0.0)

    def override(self, key: str, value: float):
        """Override a key with a fixed value -- get() returns the override."""
        with self._lock:
            self._overrides[key] = value

    def unfreeze(self, key: str):
        """Remove freeze on key."""
        with self._lock:
            self._frozen.pop(key, None)

    def clear_override(self, key: str):
        """Remove override on key."""
        with self._lock:
            self._overrides.pop(key, None)

    def release(self, key: str):
        """Remove both freeze and override on key."""
        with self._lock:
            self._frozen.pop(key, None)
            self._overrides.pop(key, None)

    def get_active_overrides(self) -> list[dict]:
        """Return list of active freezes and overrides for UI display."""
        with self._lock:
            result = []
            for key, value in self._frozen.items():
                result.append({"key": key, "type": "frozen", "value": value})
            for key, value in self._overrides.items():
                result.append({"key": key, "type": "override", "value": value})
            return result

    def clear_trends(self):
        """Clear all trend data."""
        with self._lock:
            self._trends.clear()

    def push_trend(self, key: str, value: float):
        with self._lock:
            if key not in self._trends:
                self._trends[key] = deque(maxlen=self.TREND_LENGTH)
            self._trends[key].append(value)

    def get_trend(self, key: str) -> list:
        with self._lock:
            if key in self._trends:
                return list(self._trends[key])
            return []

    def push_trends(self, data: dict):
        with self._lock:
            for key, value in data.items():
                if key not in self._trends:
                    self._trends[key] = deque(maxlen=self.TREND_LENGTH)
                self._trends[key].append(value)

    def queue_write(self, key: str, value: Any):
        """Queue the latest external demand for *key*.

        Repeated demands for one tag coalesce.  A new distinct tag beyond
        :attr:`MAX_PENDING_WRITES` is rejected explicitly instead of silently
        dropping an older command or growing memory without bound.
        """
        with self._lock:
            failure = self._communication_failure_for(key, "output")
            if failure is not None:
                failure.dropped_writes += 1
                return False
            if key in self._external_writes:
                # Move the replacement to its true chronological position;
                # ordering matters when a batch contains mode and setpoint
                # writes to different keys.
                del self._external_writes[key]
                self._coalesced_writes += 1
            elif len(self._external_writes) >= self.MAX_PENDING_WRITES:
                self._write_queue_rejections += 1
                raise BufferError(
                    "external write queue reached its distinct-tag capacity "
                    f"({self.MAX_PENDING_WRITES})"
                )
            self._external_writes[key] = value
            self._pending_write_peak = max(
                self._pending_write_peak, len(self._external_writes))
            return True

    def drain_writes(self) -> list:
        """Get and clear the newest pending demand for every tag."""
        with self._lock:
            writes = list(self._external_writes.items())
            self._external_writes.clear()
            return writes

    def write_queue_health(self) -> dict[str, int]:
        """Return bounded-queue diagnostics without exposing its contents."""
        with self._lock:
            return {
                "pending": len(self._external_writes),
                "capacity": self.MAX_PENDING_WRITES,
                "peak": self._pending_write_peak,
                "coalesced": self._coalesced_writes,
                "rejected_capacity": self._write_queue_rejections,
            }

    # Maximum number of alarms to retain (oldest CLEARED alarms are pruned)
    _MAX_ALARMS = 500

    def add_alarm(self, tag: str, message: str, severity: str = "INFO"):
        with self._lock:
            self._alarm_id_counter += 1
            self._alarms.append({
                'id': self._alarm_id_counter,
                'time': self._sim_time,
                'tag': tag,
                'message': message,
                'severity': severity,
                'state': 'UNACK',
                'ack_time': None,
                'clear_time': None,
                'shelve_time': None,
                'shelve_expiry': None,
            })
            # Prune oldest CLEARED alarms when the list grows too large
            if len(self._alarms) > self._MAX_ALARMS:
                self._alarms = [
                    a for a in self._alarms if a['state'] != 'CLEARED'
                ] + [
                    a for a in self._alarms if a['state'] == 'CLEARED'
                ][-50:]

    def get_alarms(self) -> list:
        with self._lock:
            return list(self._alarms)

    def acknowledge_alarm(self, alarm_id: int):
        with self._lock:
            for a in self._alarms:
                if a['id'] == alarm_id and a['state'] == 'UNACK':
                    a['state'] = 'ACK'
                    a['ack_time'] = self._sim_time
                    break

    def acknowledge_all(self):
        with self._lock:
            for a in self._alarms:
                if a['state'] == 'UNACK':
                    a['state'] = 'ACK'
                    a['ack_time'] = self._sim_time

    def shelve_alarm(self, alarm_id: int, duration_hours: float = 0):
        """Shelve an alarm, optionally with an expiry duration.

        Args:
            alarm_id: The alarm to shelve.
            duration_hours: Shelf duration in hours.  0 means indefinite.
        """
        with self._lock:
            for a in self._alarms:
                if a['id'] == alarm_id and a['state'] in ('UNACK', 'ACK'):
                    a['_prev_state'] = a['state']
                    a['state'] = 'SHELVED'
                    a['shelve_time'] = self._sim_time
                    if duration_hours > 0:
                        a['shelve_expiry'] = self._sim_time + duration_hours * 3600.0
                    else:
                        a['shelve_expiry'] = None
                    break

    def unshelve_alarm(self, alarm_id: int):
        with self._lock:
            for a in self._alarms:
                if a['id'] == alarm_id and a['state'] == 'SHELVED':
                    a['state'] = a.get('_prev_state', 'ACK')
                    a.pop('_prev_state', None)
                    a['shelve_time'] = None
                    a['shelve_expiry'] = None
                    break

    def check_shelve_expiry(self):
        """Auto-unshelve alarms whose shelve timer has expired."""
        with self._lock:
            for a in self._alarms:
                if a['state'] == 'SHELVED' and a.get('shelve_expiry') is not None:
                    if self._sim_time >= a['shelve_expiry']:
                        a['state'] = a.get('_prev_state', 'ACK')
                        a.pop('_prev_state', None)
                        a['shelve_time'] = None
                        a['shelve_expiry'] = None

    def clear_alarm(self, alarm_id: int):
        with self._lock:
            for a in self._alarms:
                if a['id'] == alarm_id and a['state'] == 'ACK':
                    a['state'] = 'CLEARED'
                    a['clear_time'] = self._sim_time
                    break

    def get_alarm_counts(self) -> dict:
        with self._lock:
            counts = {'UNACK': 0, 'ACK': 0, 'SHELVED': 0, 'CLEARED': 0}
            for a in self._alarms:
                s = a.get('state', 'UNACK')
                if s in counts:
                    counts[s] += 1
            return counts

    @property
    def sim_speed(self) -> float:
        with self._lock:
            return self._sim_speed

    @sim_speed.setter
    def sim_speed(self, value: float):
        with self._lock:
            self._sim_speed = np.clip(value, 0.1, 10.0)

    @property
    def sim_running(self) -> bool:
        with self._lock:
            return self._sim_running

    @sim_running.setter
    def sim_running(self, value: bool):
        with self._lock:
            self._sim_running = value

    @property
    def sim_paused(self) -> bool:
        with self._lock:
            return self._sim_paused

    @sim_paused.setter
    def sim_paused(self, value: bool):
        with self._lock:
            self._sim_paused = value

    @property
    def sim_time(self) -> float:
        with self._lock:
            return self._sim_time

    @sim_time.setter
    def sim_time(self, value: float):
        with self._lock:
            self._sim_time = value

    # --------------------------------------------- simulation lifecycle
    @property
    def sim_started(self) -> bool:
        """True once the simulation thread has been launched."""
        with self._lock:
            return self._sim_started

    @sim_started.setter
    def sim_started(self, value: bool):
        with self._lock:
            self._sim_started = bool(value)

    def try_mark_sim_started(self) -> bool:
        """Atomically claim the launch — returns False if already started.

        Use this (not the ``sim_started`` setter) when launching, so two
        callers can never both start a simulation thread.
        """
        with self._lock:
            if self._sim_started:
                return False
            self._sim_started = True
            return True

    @property
    def sim_engine(self):
        """The running engine instance, or None before launch."""
        with self._lock:
            return self._sim_engine

    @sim_engine.setter
    def sim_engine(self, engine):
        with self._lock:
            self._sim_engine = engine

    @property
    def strategy_runtime(self):
        """Legacy single strategy runtime (prefer get_strategy_runtimes)."""
        with self._lock:
            return self._strategy_runtime

    @strategy_runtime.setter
    def strategy_runtime(self, runtime):
        with self._lock:
            self._strategy_runtime = runtime

    # ------------------------------------------------- strategy runtime access
    def add_strategy_runtime(self, runtime, plugin=None) -> None:
        """Thread-safe: add a strategy runtime to the execution list.

        If ``plugin`` is supplied, a RuntimeContext is built from the
        plugin's opt-in attributes (``script_writable_tags`` /
        ``script_writable_patterns``) and attached to the runtime so ACT
        scripts can read / write tags from the store.
        """
        with self._lock:
            if runtime not in self._strategy_runtimes:
                self._strategy_runtimes.append(runtime)
        if plugin is not None and hasattr(runtime, "set_context_from_plugin"):
            try:
                runtime.set_context_from_plugin(self, plugin)
            except Exception:
                # Context attach is best-effort — never block runtime
                # registration if a plugin attribute is misshapen.
                pass

    def remove_strategy_runtime(self, runtime) -> None:
        """Thread-safe: remove a strategy runtime from the execution list."""
        with self._lock:
            try:
                self._strategy_runtimes.remove(runtime)
            except ValueError:
                pass

    def clear_strategy_runtimes(self) -> None:
        """Thread-safe: remove all strategy runtimes."""
        with self._lock:
            self._strategy_runtimes.clear()

    def get_strategy_runtimes(self) -> list:
        """Thread-safe: return a snapshot of strategy runtimes for iteration."""
        with self._lock:
            return list(self._strategy_runtimes)
