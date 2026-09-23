"""OPC UA server.

Address space
-------------
Every tag is published at the workbook-defined canonical identifier
``ns=2;s=PLANT_SIM.<unit>.<type>.<tag>``. A flat ``ns=2;s=<tag>`` compatibility
alias is also kept for existing exercises and saved controller references::

    Objects
      PLANT_SIM
        U100
          AI
            LT-1001            ns=2;s=PLANT_SIM.U100.AI.LT-1001
            LT-1001 (legacy)   ns=2;s=LT-1001
          AO
            FCV-1001           ns=2;s=FCV-1001
        SIM
          Run, SpeedFactor, Heartbeat, ScanTime, SimTime, SnapshotCmd

Quality
-------
Each analogue and discrete value is written as a full ``DataValue`` carrying a
status code and a source timestamp, so a client sees genuine Bad and Uncertain
quality rather than a frozen number. The ``Status`` companion node repeats the
quality as an integer for clients that cannot reach the status code easily.

``asyncua`` 1.x named the ``DataValue`` field ``StatusCode_`` and 2.x renamed it
to ``StatusCode``. :func:`_make_datavalue` handles both so this module does not
break on a version bump.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import math
import threading
import time
from dataclasses import dataclass

from asyncua import Server, ua

from ..core.tags import Quality, Tag, TagDatabase, TagKind
from ..errors import OpcServerError
from ..product import OPC_SERVER_NAME

log = logging.getLogger(__name__)

NAMESPACE_URI = "urn:plant_sim:simulator"
NAMESPACE_INDEX = 2

_STATUS = {
    Quality.GOOD: ua.StatusCodes.Good,
    Quality.UNCERTAIN: ua.StatusCodes.UncertainLastUsableValue,
    Quality.BAD: ua.StatusCodes.BadNoCommunication,
}


def _make_datavalue(variant: ua.Variant, quality: Quality, ts: _dt.datetime) -> ua.DataValue:
    """Build an immutable DataValue, tolerating asyncua 1.x and 2.x.

    asyncua 1.1 made ``DataValue`` a frozen dataclass.  Mutating a freshly
    created instance therefore fails even though compatibility properties make
    both status-field spellings appear to exist.  Constructor keywords are the
    only reliable compatibility boundary.
    """
    status = ua.StatusCode(_STATUS[quality])
    try:
        return ua.DataValue(
            Value=variant,
            StatusCode_=status,
            SourceTimestamp=ts,
        )
    except TypeError:
        return ua.DataValue(
            Value=variant,
            StatusCode=status,
            SourceTimestamp=ts,
        )


@dataclass
class ServerStats:
    running: bool = False
    endpoint: str = ""
    node_count: int = 0
    writes_in: int = 0
    writes_out: int = 0
    adjusted_writes: int = 0
    rejected_writes: int = 0
    publish_errors: int = 0
    last_publish_time: float = 0.0
    last_error: str = ""
    started: bool = False
    namespace_index: int = NAMESPACE_INDEX


class _WriteHandler:
    """Receives client writes to AO and DO nodes and applies them to the database."""

    def __init__(self, server: OpcUaServer) -> None:
        self.server = server
        self._primed: set[str] = set()

    def datachange_notification(self, node, val, data) -> None:
        identifier = str(node.nodeid.Identifier)
        try:
            tag = self.server.node_to_tag.get(identifier)
            if tag is None:
                return
            # asyncua sends the current value once when a monitored item is
            # created. It is not a DCS command and must not inflate write counts.
            if identifier not in self._primed:
                self._primed.add(identifier)
                return
            if identifier in self.server._server_writebacks:
                self.server._server_writebacks.discard(identifier)
                return
            bus = getattr(self.server, "bus", None)
            if bus is not None and not bus.authorize_dcs_write(
                    tag.name, source="opcua"):
                self.server.stats.rejected_writes += 1
                self.server.stats.last_error = (
                    f"{tag.name}: rejected by the virtual I/O bus "
                    f"(owner or holder)")
                log.warning("Rejected OPC UA write to %s: bus arbitration "
                            "(holder=%s)", tag.name, bus.holder)
                self.server._queue_writeback(tag, tag.value, identifier)
                return
            with self.server.db.lock:
                previous = tag.value
                try:
                    applied, adjusted = tag.set_from_dcs(val)
                except (TypeError, ValueError, PermissionError) as exc:
                    self.server.stats.rejected_writes += 1
                    self.server.stats.last_error = str(exc)
                    log.warning("Rejected OPC UA write to %s: %s", tag.name, exc)
                    self.server._queue_writeback(tag, previous, identifier)
                    return
            self.server.stats.writes_in += 1
            if adjusted:
                self.server.stats.adjusted_writes += 1
                log.warning(
                    "Clamped OPC UA write to %s from %r to %r within [%g, %g]",
                    tag.name,
                    val,
                    applied,
                    tag.lo,
                    tag.hi,
                )
            self.server._sync_command_aliases(tag, applied, identifier)
        except Exception:
            log.exception("Failed to apply client write to %s", node)


class OpcUaServer:
    """Runs an asyncua server on its own thread with its own event loop."""

    #: The virtual I/O bus, set by the runtime; write arbitration goes
    #: through it when present.
    bus = None

    def __init__(
        self,
        db: TagDatabase,
        engine,
        endpoint: str = "opc.tcp://0.0.0.0:48420/plant_sim/",
        publish_period: float = 0.25,
        namespace_uri: str = NAMESPACE_URI,
        namespace_index: int = NAMESPACE_INDEX,
    ) -> None:
        self.db = db
        self.engine = engine
        self.endpoint = endpoint
        self.publish_period = max(float(publish_period), 0.05)
        if not math.isfinite(self.publish_period):
            raise ValueError("publish_period must be finite")
        self.namespace_uri = str(namespace_uri)
        self.namespace_index = int(namespace_index)
        self.stats = ServerStats(endpoint=endpoint, namespace_index=self.namespace_index)

        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._async_stop: asyncio.Event | None = None
        self._stop = threading.Event()

        self.value_nodes: dict[str, object] = {}
        self.canonical_value_nodes: dict[str, object] = {}
        self.pct_nodes: dict[str, object] = {}
        self.canonical_pct_nodes: dict[str, object] = {}
        self.status_nodes: dict[str, object] = {}
        self.canonical_status_nodes: dict[str, object] = {}
        self.value_nodes_by_id: dict[str, object] = {}
        self.node_to_tag: dict[str, Tag] = {}
        self.sim_nodes: dict[str, object] = {}
        self._last_run_cmd: bool | None = None
        self._last_speed_cmd: float | None = None
        self._server_writebacks: set[str] = set()

    # ------------------------------------------------------------------ control
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.stats.started = False
        self.stats.running = False
        self.stats.last_error = ""
        self._thread = threading.Thread(target=self._run, name="opcua-server", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._loop is not None and self._async_stop is not None:
            try:
                self._loop.call_soon_threadsafe(self._async_stop.set)
            except RuntimeError:
                pass          # loop already closed; the thread is on its way out
        if self._thread:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                self.stats.last_error = f"OPC UA server did not stop within {timeout:.1f} seconds"
                log.error(self.stats.last_error)
                return
        self.stats.running = False
        log.info("OPC UA server stopped")

    # --------------------------------------------------------------- thread body
    def _run(self) -> None:
        try:
            asyncio.run(self._serve())
        except Exception as exc:
            self.stats.last_error = f"{type(exc).__name__}: {exc}"
            self.stats.running = False
            log.exception("OPC UA server thread terminated")

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._async_stop = asyncio.Event()
        server = Server()
        await server.init()
        server.set_endpoint(self.endpoint)
        server.set_server_name(OPC_SERVER_NAME)
        server.set_security_policy([ua.SecurityPolicyType.NoSecurity])

        if "127.0.0.1" not in self.endpoint and "localhost" not in self.endpoint:
            log.warning(
                "OPC UA NoSecurity endpoint is exposed beyond loopback at %s; "
                "use only on an isolated training network",
                self.endpoint,
            )

        idx = await server.register_namespace(self.namespace_uri)
        if idx != self.namespace_index:
            raise OpcServerError(
                f"namespace {self.namespace_uri!r} registered at index {idx}, "
                f"but configuration requires {self.namespace_index}"
            )

        await self._build_address_space(server)
        self.stats.node_count = len(self.value_nodes)

        async with server:
            self.stats.running = True
            self.stats.started = True
            log.info("OPC UA server listening on %s with %d tag nodes", self.endpoint, self.stats.node_count)

            writable = []
            for tag in self.db.by_kind(TagKind.AO, TagKind.DO):
                writable.extend((self.value_nodes[tag.name], self.canonical_value_nodes[tag.name]))
            sub = None
            try:
                sub = await server.create_subscription(200, _WriteHandler(self))
                await sub.subscribe_data_change(writable)
                log.info("Subscribed to %d writable nodes for client writes", len(writable))
            except Exception:
                log.exception(
                    "Could not subscribe to writable nodes; client writes will not reach the models"
                )

            try:
                while not self._stop.is_set():
                    try:
                        await self._publish()
                    except Exception:
                        log.exception("Publish cycle failed")
                        self.stats.last_error = "publish cycle failed, see log"
                    try:
                        await asyncio.wait_for(self._async_stop.wait(), timeout=self.publish_period)
                    except TimeoutError:
                        pass
            finally:
                if sub is not None:
                    try:
                        await sub.delete()
                    except Exception:
                        pass
        self.stats.running = False
        self._async_stop = None
        self._loop = None

    # ------------------------------------------------------------ address space
    async def _build_address_space(self, server: Server) -> None:
        objects = server.nodes.objects
        root = await objects.add_folder(ua.NodeId("PLANT_SIM", self.namespace_index), "PLANT_SIM")

        for unit_code in self.db.units():
            unit_folder = await root.add_folder(
                ua.NodeId(f"PLANT_SIM.{unit_code}", self.namespace_index), unit_code
            )
            for kind in (TagKind.AI, TagKind.AO, TagKind.DI, TagKind.DO):
                tags = [t for t in self.db.by_unit(unit_code) if t.kind is kind]
                if not tags:
                    continue
                kind_folder = await unit_folder.add_folder(
                    ua.NodeId(f"PLANT_SIM.{unit_code}.{kind.value}", self.namespace_index), kind.value
                )
                for tag in tags:
                    await self._add_tag(kind_folder, tag)

        sim = await root.add_folder(ua.NodeId("PLANT_SIM.SIM", self.namespace_index), "SIM")
        await self._add_sim_nodes(sim)

    async def _add_tag(self, parent, tag: Tag) -> None:
        vtype = ua.VariantType.Double if tag.kind.analogue else ua.VariantType.Boolean
        initial = float(tag.value) if tag.kind.analogue else bool(tag.value)

        canonical_id = tag.node_id.partition(";s=")[2]
        if not canonical_id:
            canonical_id = f"PLANT_SIM.{tag.unit}.{tag.kind.value}.{tag.name}"
        canonical = await parent.add_variable(
            ua.NodeId(canonical_id, self.namespace_index),
            tag.name,
            ua.Variant(initial, vtype),
            varianttype=vtype,
        )
        await canonical.write_attribute(
            ua.AttributeIds.Description,
            ua.DataValue(ua.Variant(ua.LocalizedText(tag.desc), ua.VariantType.LocalizedText)),
        )

        node = await parent.add_variable(
            ua.NodeId(tag.name, self.namespace_index),
            f"{tag.name} (legacy)",
            ua.Variant(initial, vtype),
            varianttype=vtype,
        )
        await node.write_attribute(
            ua.AttributeIds.Description,
            ua.DataValue(ua.Variant(ua.LocalizedText(tag.desc), ua.VariantType.LocalizedText)),
        )

        if tag.kind.dcs_writable:
            await canonical.set_writable(True)
            await node.set_writable(True)
            self.node_to_tag[tag.name] = tag
            self.node_to_tag[canonical_id] = tag

        self.value_nodes[tag.name] = node
        self.canonical_value_nodes[tag.name] = canonical
        self.value_nodes_by_id[tag.name] = node
        self.value_nodes_by_id[canonical_id] = canonical

        status = await parent.add_variable(
            ua.NodeId(f"{tag.name}.Status", self.namespace_index),
            "Status",
            ua.Variant(0, ua.VariantType.UInt16),
            varianttype=ua.VariantType.UInt16,
        )
        self.status_nodes[tag.name] = status
        canonical_status = await parent.add_variable(
            ua.NodeId(f"{canonical_id}.Status", self.namespace_index),
            "Status",
            ua.Variant(0, ua.VariantType.UInt16),
            varianttype=ua.VariantType.UInt16,
        )
        self.canonical_status_nodes[tag.name] = canonical_status

        if tag.kind.analogue:
            pct = await parent.add_variable(
                ua.NodeId(f"{tag.name}.PctVal", self.namespace_index),
                "PctVal",
                ua.Variant(0.0, ua.VariantType.Double),
                varianttype=ua.VariantType.Double,
            )
            self.pct_nodes[tag.name] = pct
            canonical_pct = await parent.add_variable(
                ua.NodeId(f"{canonical_id}.PctVal", self.namespace_index),
                "PctVal",
                ua.Variant(0.0, ua.VariantType.Double),
                varianttype=ua.VariantType.Double,
            )
            self.canonical_pct_nodes[tag.name] = canonical_pct
            eu = await parent.add_variable(
                ua.NodeId(f"{tag.name}.EU", self.namespace_index),
                "EU",
                ua.Variant(tag.eu, ua.VariantType.String),
                varianttype=ua.VariantType.String,
            )
            await eu.set_writable(False)
            canonical_eu = await parent.add_variable(
                ua.NodeId(f"{canonical_id}.EU", self.namespace_index),
                "EU",
                ua.Variant(tag.eu, ua.VariantType.String),
                varianttype=ua.VariantType.String,
            )
            await canonical_eu.set_writable(False)

    async def _add_sim_nodes(self, parent) -> None:
        spec = [
            ("Run", False, ua.VariantType.Boolean, True),
            ("SpeedFactor", 1.0, ua.VariantType.Double, True),
            ("Heartbeat", 0, ua.VariantType.UInt32, False),
            ("ScanTimeMs", 0.0, ua.VariantType.Double, False),
            ("SimTime", 0.0, ua.VariantType.Double, False),
            ("Overruns", 0, ua.VariantType.UInt32, False),
            ("ModelErrors", 0, ua.VariantType.UInt32, False),
        ]
        for name, default, vtype, writable in spec:
            node = await parent.add_variable(
                ua.NodeId(f"SIM.{name}", self.namespace_index),
                name,
                ua.Variant(default, vtype),
                varianttype=vtype,
            )
            if writable:
                await node.set_writable(True)
            self.sim_nodes[name] = node

    # -------------------------------------------------------------- publishing
    async def _publish(self) -> None:
        with self.db.lock:
            outputs = [(t, t.value, t.quality, t.pct, t.ts) for t in self.db.by_kind(TagKind.AI, TagKind.DI)]

        successful = 0
        failures: list[str] = []
        for tag, value, quality, pct, timestamp in outputs:
            vtype = ua.VariantType.Double if tag.kind.analogue else ua.VariantType.Boolean
            payload = float(value) if tag.kind.analogue else bool(value)
            try:
                source_timestamp = _dt.datetime.fromtimestamp(float(timestamp), _dt.UTC)
                await self.value_nodes[tag.name].write_value(
                    _make_datavalue(ua.Variant(payload, vtype), quality, source_timestamp)
                )
                await self.canonical_value_nodes[tag.name].write_value(
                    _make_datavalue(ua.Variant(payload, vtype), quality, source_timestamp)
                )
                await self.status_nodes[tag.name].write_value(ua.Variant(int(quality), ua.VariantType.UInt16))
                await self.canonical_status_nodes[tag.name].write_value(
                    ua.Variant(int(quality), ua.VariantType.UInt16)
                )
                if tag.kind.analogue:
                    await self.pct_nodes[tag.name].write_value(ua.Variant(float(pct), ua.VariantType.Double))
                    await self.canonical_pct_nodes[tag.name].write_value(
                        ua.Variant(float(pct), ua.VariantType.Double)
                    )
                successful += 1
            except Exception as exc:
                failures.append(f"{tag.name}: {type(exc).__name__}: {exc}")
        self.stats.writes_out += successful
        self.stats.last_publish_time = time.time()
        if failures:
            self.stats.publish_errors += len(failures)
            self.stats.last_error = f"{len(failures)} tag publish failure(s); first: {failures[0]}"
            if self.stats.publish_errors == len(failures) or self.stats.publish_errors % 100 == 0:
                log.error(self.stats.last_error)
        elif "tag publish failure" in self.stats.last_error or self.stats.last_error.startswith(
            "publish cycle"
        ):
            self.stats.last_error = ""

        await self._publish_sim()

    def _queue_writeback(self, tag: Tag, value: float | bool, identifier: str) -> None:
        if self._loop is None or self._loop.is_closed():
            return
        self._loop.create_task(self._writeback(tag, value, identifier))

    def _sync_command_aliases(self, tag: Tag, value: float | bool, source_identifier: str) -> None:
        """Keep canonical and legacy writable nodes coherent after a client write."""
        canonical_id = tag.node_id.partition(";s=")[2]
        other = tag.name if source_identifier == canonical_id else canonical_id
        self._queue_writeback(tag, value, other)

    async def _writeback(self, tag: Tag, value: float | bool, identifier: str) -> None:
        """Replace a rejected/clamped client value with the applied value."""
        try:
            self._server_writebacks.add(identifier)
            if self._loop is not None:
                self._loop.call_later(1.0, self._server_writebacks.discard, identifier)
            vtype = ua.VariantType.Double if tag.kind.analogue else ua.VariantType.Boolean
            payload = float(value) if tag.kind.analogue else bool(value)
            await self.value_nodes_by_id[identifier].write_value(ua.Variant(payload, vtype))
        except Exception:
            self._server_writebacks.discard(identifier)
            log.exception("Failed to restore OPC UA node %s after invalid write", identifier)

    async def _publish_sim(self) -> None:
        st = self.engine.stats
        try:
            # Run and SpeedFactor are bidirectional: a client write commands the
            # engine, and an engine change made from the UI is published back.
            # Comparing against the last known command distinguishes the two, so
            # the node's power-on default can never silently freeze a running
            # engine, and a UI freeze is still reflected to the client.
            desired = bool(await self.sim_nodes["Run"].read_value())
            if self._last_run_cmd is None or desired != self._last_run_cmd:
                if self._last_run_cmd is not None:
                    self.engine.resume() if desired else self.engine.freeze()
                    self._last_run_cmd = desired
                else:
                    await self.sim_nodes["Run"].write_value(
                        ua.Variant(bool(st.running), ua.VariantType.Boolean)
                    )
                    self._last_run_cmd = bool(st.running)
            elif st.running != desired:
                await self.sim_nodes["Run"].write_value(ua.Variant(bool(st.running), ua.VariantType.Boolean))
                self._last_run_cmd = bool(st.running)

            speed = float(await self.sim_nodes["SpeedFactor"].read_value())
            if self._last_speed_cmd is None:
                await self.sim_nodes["SpeedFactor"].write_value(
                    ua.Variant(float(self.engine.speed_factor), ua.VariantType.Double)
                )
                self._last_speed_cmd = self.engine.speed_factor
            elif abs(speed - self._last_speed_cmd) > 1e-6:
                try:
                    self.engine.speed_factor = speed
                except (TypeError, ValueError) as exc:
                    self.stats.rejected_writes += 1
                    self.stats.last_error = f"Rejected SIM.SpeedFactor: {exc}"
                    log.warning(self.stats.last_error)
                    await self.sim_nodes["SpeedFactor"].write_value(
                        ua.Variant(float(self.engine.speed_factor), ua.VariantType.Double)
                    )
                else:
                    applied_speed = self.engine.speed_factor
                    if abs(applied_speed - speed) > 1e-6:
                        self.stats.adjusted_writes += 1
                        log.warning(
                            "Clamped SIM.SpeedFactor from %r to %.3f",
                            speed,
                            applied_speed,
                        )
                        await self.sim_nodes["SpeedFactor"].write_value(
                            ua.Variant(applied_speed, ua.VariantType.Double)
                        )
                    self._last_speed_cmd = applied_speed
            elif abs(self.engine.speed_factor - speed) > 1e-6:
                await self.sim_nodes["SpeedFactor"].write_value(
                    ua.Variant(float(self.engine.speed_factor), ua.VariantType.Double)
                )
                self._last_speed_cmd = self.engine.speed_factor

            await self.sim_nodes["Heartbeat"].write_value(
                ua.Variant(int(st.heartbeat) & 0xFFFFFFFF, ua.VariantType.UInt32)
            )
            await self.sim_nodes["ScanTimeMs"].write_value(
                ua.Variant(float(st.scan_time_ms), ua.VariantType.Double)
            )
            await self.sim_nodes["SimTime"].write_value(ua.Variant(float(st.sim_time), ua.VariantType.Double))
            await self.sim_nodes["Overruns"].write_value(ua.Variant(int(st.overruns), ua.VariantType.UInt32))
            await self.sim_nodes["ModelErrors"].write_value(ua.Variant(int(st.errors), ua.VariantType.UInt32))
        except Exception:
            log.debug("SIM node publish failed", exc_info=True)
