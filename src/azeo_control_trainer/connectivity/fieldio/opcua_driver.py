"""OPC UA client as a field transport — the EIOC's job, behind the store.

The real PK cannot consume OPC UA natively; in a Azeo system that role
belongs to the EIOC, which subscribes to third-party OPC UA servers and
hands the signals to control as ordinary I/O. This driver is that role for
the trainer, sitting exactly where `modbus_driver.ModbusLink` sits — on the
far side of `SharedDataStore`, so nothing in the control half knows which
transport is feeding it.

The signal map comes from the area's `_project.json` under `field_io`:

    "field_io": {
      "type": "opcua",
      "endpoint": "opc.tcp://host:4840/path",
      "signals": {
        "LI-101.PV":  {"node": "ns=2;s=...", "direction": "read"},
        "FV-101.OUT": {"node": "ns=2;s=...", "direction": "write"}
      }
    }

— written by hand or by the OPC UA browser dialog (`Controller ▸ OPC UA
Device…`), which is the EIOC's "online browsing of signals" flow.

Behaviour rules, all inherited from the Modbus link:

- **Reads are subscriptions**, not polls: the server pushes changes, the
  driver publishes value, source timestamp, and quality into the store. A
  Bad value does not replace the last trustworthy value — the tag goes stale
  and the block reports what a dead transmitter should.
- **Writes drain the store's queue**: `AO`/`DO` outputs and operator
  writes land on `queue_write`; this driver applies the queue (it is the
  executive on the field side) and forwards mapped tags to the server.
- **A lost session stops publishing.** No invented values, no zero-fill;
  reconnection is retried in the background.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
import logging
import threading

log = logging.getLogger("fieldio.opcua")

# asyncua narrates at INFO — every internal publish, every monitored item,
# and a screenful of harmless address-space notes at import. That is
# library telemetry, not trainer information, and it drowned the log the
# engineer actually reads (user-pasted evidence). Its WARNINGs still land.
logging.getLogger("asyncua").setLevel(logging.WARNING)

RECONNECT_S = 3.0
DRAIN_S = 0.25


class OpcUaLink:
    """A subscription-fed OPC UA client bound to the store."""

    def __init__(self, store, endpoint: str, signals: dict):
        self.store = store
        self.endpoint = endpoint
        #: tag -> {"node": ..., "direction": "read"|"write"}
        self.signals = dict(signals or {})
        self.read_map = {tag: spec["node"] for tag, spec in
                         self.signals.items()
                         if spec.get("direction", "read") == "read"
                         and spec.get("node")}
        self.write_map = {tag: spec["node"] for tag, spec in
                          self.signals.items()
                          if spec.get("direction") == "write"
                          and spec.get("node")}
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stopping = threading.Event()
        self._ready = threading.Event()
        self.connected = False
        self.reads_published = 0
        self.writes_applied = 0

    def _mark_reads_stale(self) -> None:
        """Invalidate every configured subscription without inventing data.

        A disconnected EIOC used to leave the last scalar in
        ``SharedDataStore`` with no acquisition metadata, so TAG blocks kept
        reporting Good indefinitely. Preserve each last real value and source
        timestamp, but make the provider failure explicit.
        """
        for tag in self.read_map:
            _mark_bad_sample(self.store, tag)

    # ------------------------------------------------------------ lifecycle
    def start(self, timeout: float = 10.0) -> bool:
        if self._thread is not None:
            return self.connected
        self._stopping.clear()
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="opcua-link")
        self._thread.start()
        self._ready.wait(timeout)
        if self.connected:
            log.info("OPC UA link to %s — %d read signal(s), %d write",
                     self.endpoint, len(self.read_map), len(self.write_map))
        else:
            log.warning("OPC UA link to %s not connected yet — retrying in "
                        "the background", self.endpoint)
        return self.connected

    def stop(self) -> bool:
        if self._thread is None:
            return True
        self._stopping.set()
        self._thread.join(timeout=5.0)
        if self._thread.is_alive():
            log.warning("OPC UA link is still stopping")
            return False
        self._thread = None
        self._loop = None
        return True

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._session_loop())
        finally:
            loop.close()

    # -------------------------------------------------------------- session
    async def _session_loop(self) -> None:
        from asyncua import Client

        while not self._stopping.is_set():
            try:
                client = Client(self.endpoint)
                await client.connect()
            except Exception as error:              # noqa: BLE001
                self.connected = False
                self._mark_reads_stale()
                self._ready.set()
                log.debug("OPC UA connect failed: %s", error)
                await asyncio.sleep(RECONNECT_S)
                continue
            try:
                await self._serve_session(client)
            except Exception as error:              # noqa: BLE001
                log.warning("OPC UA session lost: %s — reconnecting",
                            error)
            finally:
                self.connected = False
                self._mark_reads_stale()
                try:
                    await client.disconnect()
                except Exception:                   # noqa: BLE001
                    pass
            if not self._stopping.is_set():
                await asyncio.sleep(RECONNECT_S)

    async def _serve_session(self, client) -> None:
        nodes_by_tag = {tag: client.get_node(node_id)
                        for tag, node_id in self.read_map.items()}
        tag_by_key = {node.nodeid.to_string(): tag
                      for tag, node in nodes_by_tag.items()}
        if nodes_by_tag:
            subscription = await client.create_subscription(
                250, _ReadHandler(self, tag_by_key))
            await subscription.subscribe_data_change(
                list(nodes_by_tag.values()))
        write_nodes = {tag: client.get_node(node_id)
                       for tag, node_id in self.write_map.items()}
        self.connected = True
        self._ready.set()
        while not self._stopping.is_set():
            await self._apply_queued_writes(client, write_nodes)
            await asyncio.sleep(DRAIN_S)

    async def _apply_queued_writes(self, client, write_nodes) -> None:
        """Drain the store's write queue: apply everything to the store
        (this is the field-side executive — see the Modbus link), and
        forward the mapped tags to the server."""
        drain = getattr(self.store, "drain_writes", None)
        if not callable(drain):
            return
        for item in drain() or ():
            try:
                key, value = item
            except (TypeError, ValueError):         # pragma: no cover
                continue
            self.store.set(key, value)
            node = write_nodes.get(key)
            if node is None:
                continue
            try:
                from asyncua import ua

                variant = (ua.Variant(bool(value), ua.VariantType.Boolean)
                           if isinstance(value, bool)
                           else ua.Variant(float(value),
                                           ua.VariantType.Double))
                await node.write_value(ua.DataValue(variant))
                self.writes_applied += 1
            except Exception as error:              # noqa: BLE001
                log.warning("OPC UA write %s failed: %s", key, error)


class _ReadHandler:
    """Subscription callback: server pushes, the store receives.

    Bad notifications preserve the last trustworthy value and make its sample
    stale. Uncertain notifications remain live with Uncertain quality; hiding
    that distinction would deprive control and diagnostics of useful status.
    """

    def __init__(self, link: OpcUaLink, tag_by_key: dict):
        self._link = link
        self._tags = tag_by_key

    def datachange_notification(self, node, value, data) -> None:
        tag = self._tags.get(node.nodeid.to_string())
        if tag is None:
            return
        data_value = getattr(
            getattr(data, "monitored_item", None), "Value", None
        )
        code = getattr(data_value, "StatusCode_", None)
        quality = _status_quality(code)
        timestamp = _source_timestamp(data_value)
        if quality == "BAD":
            # Keep the last trustworthy value on an established channel. A
            # first Bad notification may still carry a useful diagnostic
            # value; publish that value as explicitly stale rather than
            # fabricating zero or silently omitting the provider status.
            _mark_bad_sample(
                self._link.store,
                tag,
                value=value,
                timestamp=timestamp,
            )
            log.debug("OPC UA %s Bad — sample marked stale", tag)
            return
        set_sample = getattr(self._link.store, "set_sample", None)
        if callable(set_sample):
            set_sample(
                tag,
                value,
                quality=quality,
                timestamp=timestamp,
            )
        else:  # compatibility for a small third-party store implementation
            self._link.store.set(tag, value)
        self._link.reads_published += 1


def _status_quality(status) -> str:
    """Return provider-neutral quality for an OPC UA ``StatusCode``."""
    if status is None:
        return "GOOD"
    for method, quality in (
        ("is_good", "GOOD"),
        ("is_uncertain", "UNCERTAIN"),
        ("is_bad", "BAD"),
    ):
        check = getattr(status, method, None)
        if callable(check):
            try:
                if check():
                    return quality
            except Exception:  # noqa: BLE001 - foreign provider object
                break
    # OPC UA severity occupies the two high bits. Treat reserved severity as
    # Bad; an unknown status is never evidence that an instrument is Good.
    try:
        raw = int(getattr(status, "value", status))
    except (TypeError, ValueError):
        return "BAD"
    severity = (raw >> 30) & 0x3
    return "GOOD" if severity == 0 else "UNCERTAIN" if severity == 1 else "BAD"


_NO_VALUE = object()


def _mark_bad_sample(store, tag: str, *, value=_NO_VALUE,
                     timestamp: float | None = None):
    """Make one configured read explicitly Bad while retaining real data."""
    mark_stale = getattr(store, "mark_sample_stale", None)
    sample = (mark_stale(tag, quality="BAD")
              if callable(mark_stale) else None)
    if sample is not None:
        return sample
    set_sample = getattr(store, "set_sample", None)
    if not callable(set_sample):
        return None
    if value is _NO_VALUE:
        getter = getattr(store, "get", None)
        value = getter(tag, None) if callable(getter) else None
    # ``None`` records that the configured channel has no usable value. It is
    # materially different from inventing a plausible process zero.
    return set_sample(
        tag,
        value,
        quality="BAD",
        timestamp=timestamp,
        stale=True,
    )


def _source_timestamp(data_value) -> float | None:
    """Extract the source (or server) timestamp as epoch seconds."""
    if data_value is None:
        return None
    raw = (getattr(data_value, "SourceTimestamp", None)
           or getattr(data_value, "ServerTimestamp", None))
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.timestamp()
    try:
        return float(raw)
    except (TypeError, ValueError, OverflowError):
        return None
