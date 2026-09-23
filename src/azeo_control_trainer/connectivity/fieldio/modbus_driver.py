"""Modbus TCP master: the wire between `SharedDataStore` and a field device.

This is a **drop-in for `plant/driver.py`**, and that is the point. The plant
driver steps a Python model and exchanges its tags with the store; this polls
a device over TCP and exchanges its registers with the store. Neither is
visible to a control module: an `AI` block reads `LT-101.PV` and has no way
to find out whether that number came from a differential equation or off a
wire.

`CLAUDE.md` claims the control half must drive "a simulated process, an OPC UA
server, or real I/O without changing". This is the first thing that actually
tests the claim.

**The DCS is the master.** It polls; the device answers. That is the normal
arrangement and it decides the shape of a scan:

1. read every measurement the field publishes  → store
2. read every command the controller has written → device
3. write the heartbeat

The heartbeat is not decoration. The simulator's watchdog drives every
output to a safe state when it stops changing, so a DCS that forgets it gets
a plant that will not move and no error anywhere — which is exactly what
happened the first time this was wired up by hand.
"""
from __future__ import annotations

import logging
import time

from .modbus_map import HEARTBEAT, POINTS, Area, Point, reads, writes

log = logging.getLogger("fieldio.modbus")

#: How often the link polls, independent of the control scan. A DCS polling
#: its I/O faster than it executes control is normal — the scan then always
#: sees the most recent value rather than one a scan old.
DEFAULT_PERIOD_MS = 100

#: A read that fails leaves the store alone rather than writing a zero, and
#: after this many consecutive failures the points are marked bad so the
#: `AI` blocks go Bad through the normal path.
BAD_AFTER = 3


class ModbusFieldDriver:
    """Exchange store tags with a Modbus TCP device.

    Deliberately not a `QObject` and not timer-driven: `step_once` is called
    by whatever executive is running, the same way the plant driver is, so
    the whole loop stays on one clock and a test can step it by hand.
    """

    def __init__(self, store, host: str = "127.0.0.1", port: int = 1502,
                 unit_id: int = 1, timeout: float = 1.0):
        self.store = store
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.timeout = timeout
        self.client = None
        self.connected = False
        self.failures = 0
        self.scans = 0
        self._beat = 0
        self._last_error = ""
        #: Channel configuration: a disabled channel is
        #: not scanned — its store tag stops updating and goes Bad past
        #: the grace window, which is exactly what a disabled channel
        #: should look like. A re-tagged channel publishes under its new
        #: Device Tag; the old tag starves and the configuration that
        #: referenced it reports Bad — the same mismatch lesson Azeo
        #: teaches. Runtime state, never serialized.
        self.disabled_channels: set = set()
        self.device_tags: dict = {}

    # ---------------------------------------------------- channel config
    def device_tag(self, point) -> str:
        """The DST a channel currently publishes as (default: the
        map's own tag)."""
        return self.device_tags.get(point.tag, point.tag)

    def set_channel_enabled(self, tag: str, enabled: bool) -> None:
        if enabled:
            self.disabled_channels.discard(tag)
        else:
            self.disabled_channels.add(tag)

    def set_device_tag(self, tag: str, device_tag: str) -> None:
        if device_tag and device_tag != tag:
            self.device_tags[tag] = device_tag
        else:
            self.device_tags.pop(tag, None)

    # ------------------------------------------------------------ the link
    def open(self) -> bool:
        from pymodbus.client import ModbusTcpClient

        self.client = ModbusTcpClient(self.host, port=self.port,
                                      timeout=self.timeout)
        self.connected = bool(self.client.connect())
        if self.connected:
            log.info("Modbus master connected to %s:%d unit %d",
                     self.host, self.port, self.unit_id)
        else:
            self._last_error = f"could not connect to {self.host}:{self.port}"
            log.error(self._last_error)
        return self.connected

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
        self.connected = False

    @property
    def healthy(self) -> bool:
        return self.connected and self.failures < BAD_AFTER

    def status(self) -> str:
        if not self.connected:
            return f"disconnected — {self._last_error or 'not opened'}"
        if self.failures:
            return f"{self.failures} consecutive failure(s)"
        return f"connected to {self.host}:{self.port}, {self.scans} scan(s)"

    # ------------------------------------------------------------ one scan
    def step_once(self, dt: float = 0.0) -> bool:
        """One exchange. Returns whether it completed."""
        if not self.connected and not self.open():
            return False
        ok = self._read_measurements()
        self.apply_queued_writes()
        ok = self._write_commands() and ok
        ok = self._write_heartbeat() and ok
        self.scans += 1
        if ok:
            self.failures = 0
        else:
            self.failures += 1
            if self.failures == BAD_AFTER:
                # Say it once, not every scan: a log that repeats at the
                # poll rate is a log nobody reads.
                log.error("Modbus link unhealthy after %d failures: %s",
                          BAD_AFTER, self._last_error)
        return ok

    def _read_measurements(self) -> bool:
        """Field → store. **Never writes a value it did not read.**

        A failed read leaves the previous value in place and lets the failure
        count reach `BAD_AFTER`; writing a zero would put a plausible number
        on an operator's screen with nothing marking it as invented.
        """
        live = [p for p in reads()
                if p.tag not in self.disabled_channels]
        digital = [p for p in live if p.area is Area.DISCRETE_INPUT]
        analogue = [p for p in live if p.area is Area.INPUT]
        values: dict = {}

        if digital:
            span = max(p.offset for p in digital) + 1
            reply = self._call(self.client.read_discrete_inputs,
                               address=0, count=span)
            if reply is None:
                return False
            for point in digital:
                values[self.device_tag(point)] = bool(
                    reply.bits[point.offset])

        if analogue:
            span = max(p.offset for p in analogue) + 1
            reply = self._call(self.client.read_input_registers,
                               address=0, count=span)
            if reply is None:
                return False
            for point in analogue:
                values[self.device_tag(point)] = point.to_engineering(
                    reply.registers[point.offset])

        if values:
            self.store.set_many(values)
        return True

    def apply_queued_writes(self) -> int:
        """Move `AO`/`DO` outputs from the queue into the store.

        **This closes the loop**, and it belongs here because this is the
        executive on the field side — the thing standing between a
        controller and its I/O.

        `DataBridge.write_outputs` puts an output on
        `SharedDataStore.queue_write`, and `store.drain_writes()` *gets and
        clears* the queue without applying it. The name reads like it
        applies; it does not. Skip this and a controller computes a perfectly
        good valve demand, `store.get("FV-101.OUT")` returns `None`, no
        register is ever written, and the valve sits wherever it was last
        commanded — which looked exactly like a tuning problem for a while.
        """
        drain = getattr(self.store, "drain_writes", None)
        if not callable(drain):
            return 0
        applied = 0
        for item in drain() or ():
            try:
                key, value = item
            except (TypeError, ValueError):                # pragma: no cover
                continue
            self.store.set(key, value)
            applied += 1
        return applied

    def _write_commands(self) -> bool:
        """Store → field. Only what the controller actually decided.

        A tag the controller has never written is left alone rather than
        forced to zero: sending a command nobody issued is how a device gets
        started by a DCS that was only supposed to be reading.
        """
        ok = True
        for point in writes():
            if point.tag in self.disabled_channels:
                continue
            value = self.store.get(self.device_tag(point))
            if value is None:
                continue
            if point.area is Area.COIL:
                reply = self._call(self.client.write_coil,
                                   address=point.offset, value=bool(value))
            else:
                reply = self._call(self.client.write_register,
                                   address=point.offset,
                                   value=point.to_raw(value))
            ok = ok and reply is not None
        return ok

    def _write_heartbeat(self) -> bool:
        """Keep the field's watchdog satisfied.

        Owned by the link, not by a module. A heartbeat a control module had
        to remember to write would be a plant that fails safe only when its
        engineer remembered.
        """
        self._beat = (self._beat + 1) % 65535
        reply = self._call(self.client.write_register,
                           address=HEARTBEAT.offset, value=self._beat)
        return reply is not None

    def _call(self, function, **kwargs):
        """One request, with the unit id and the errors handled in one place."""
        try:
            reply = function(device_id=self.unit_id, **kwargs)
        except Exception as error:                         # noqa: BLE001
            self._last_error = f"{function.__name__}: {error}"
            return None
        if reply is None or reply.isError():
            self._last_error = f"{function.__name__}: {reply}"
            return None
        return reply

    # -------------------------------------------------------------- seeding
    def seed(self) -> None:
        """Publish one read before anything goes on scan.

        Without it the first control scan finds every input absent and every
        `AI` reports Bad — correct, but it makes a healthy start look like a
        broken one.
        """
        if self.connected or self.open():
            self._read_measurements()

    def points(self) -> tuple[Point, ...]:
        return POINTS


class ModbusLink:
    """`ModbusFieldDriver` on a timer, so it runs itself.

    Separate from the driver on purpose. The driver has no Qt in it and can
    be stepped by hand in a test; this is the thin part that owns a clock,
    and it is the only piece that needs an event loop.

    It polls faster than the control scan. A DCS reading its I/O more often
    than it executes control is the normal arrangement: the scan then always
    sees the most recent measurement rather than one a scan old.
    """

    def __init__(self, store, host: str = "127.0.0.1", port: int = 1502,
                 unit_id: int = 1, period_ms: int = DEFAULT_PERIOD_MS,
                 parent=None):
        from PySide6.QtCore import Qt, QTimer

        self.driver = ModbusFieldDriver(store, host=host, port=port,
                                        unit_id=unit_id)
        self._timer = QTimer(parent)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(int(period_ms))
        self._timer.timeout.connect(self._tick)
        self._last = 0.0

    def _tick(self) -> None:
        now = time.monotonic()
        dt = (now - self._last) if self._last else 0.0
        self._last = now
        self.driver.step_once(dt)

    @property
    def is_running(self) -> bool:
        return self._timer.isActive()

    def set_period_ms(self, period_ms: int) -> None:
        """Match `PlantDriver`'s shape, so an executive can hold either.

        The two are interchangeable by design: both are the thing on the far
        side of the store, and nothing above the store may be able to tell
        which one it got.
        """
        self._timer.setInterval(int(period_ms))

    def step_once(self, dt: float = 0.0) -> bool:
        """One exchange by hand — for a headless run or a test."""
        return self.driver.step_once(dt)

    def seed(self) -> None:
        self.driver.seed()

    def start(self) -> bool:
        if not self.driver.connected and not self.driver.open():
            return False
        self._last = time.monotonic()
        self._timer.start()
        return True

    def stop(self) -> None:
        self._timer.stop()
        self.driver.close()

    def status(self) -> str:
        return self.driver.status()
