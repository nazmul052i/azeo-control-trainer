"""The trainer as a Modbus TCP server — the PK's other native endpoint.

The client half (`modbus_driver.py`) makes the trainer a Modbus master
driving a field device. This is the opposite face, PK phase 2: the
controller *serves* its data, so an external HMI, a historian, or a second
trainer instance can read the process and issue supervisory writes — the
two-students-one-process exercise.

The register map is **derived from the tag database**, the same discipline
as everything else in this repo: field points (the I/O boundary walked out
of the loaded modules) are served read-only; only entries the tag database
marks `writable` accept writes — the input/output distinction the database
already enforces is what stops an external HMI commanding a valve the
controller owns.

Encodings, stated once and stated in the exported map:

- **Analog values are float32 across two registers**, big-endian word
  order. No per-point scale table to disagree with anybody — the number on
  the wire is the number in the store.
- **Discrete values are single bits** (coils when writable, discrete
  inputs when not).
- **Modbus has no quality.** A tag the store does not hold serves 0/false —
  the protocol cannot say Bad, and inventing an in-band magic number would
  be worse. The OPC UA server is the transport that carries quality.

Reads are answered from the store **at request time** via the SimDevice
action hook — there is no refresh loop to lag behind the process. Writes
land on `store.queue_write`, the store's one external-write channel, which
every executive drains (see CLAUDE.md, the settled drain design).
"""
from __future__ import annotations

import asyncio
import logging
import struct
import threading
from dataclasses import dataclass

log = logging.getLogger("fieldio.modbus_server")

DEFAULT_PORT = 5020

#: Function codes, by what they do.
_READ_COILS, _READ_DISCRETE, _READ_HOLDING, _READ_INPUT = 1, 2, 3, 4
_WRITE_COIL, _WRITE_HOLDING = 5, 6
_WRITE_COILS, _WRITE_HOLDINGS = 15, 16

#: Which table a function code touches.
_TABLE_OF = {_READ_COILS: "coil", _WRITE_COIL: "coil", _WRITE_COILS: "coil",
             _READ_DISCRETE: "discrete",
             _READ_HOLDING: "holding", _WRITE_HOLDING: "holding",
             _WRITE_HOLDINGS: "holding",
             _READ_INPUT: "input"}

_DISCRETE_BLOCKS = frozenset(("DI", "DO"))


@dataclass(frozen=True)
class MapEntry:
    """One served point: where it lives on the wire, and why."""

    tag: str
    table: str           # "coil" | "discrete" | "holding" | "input"
    address: int         # zero-based, as the protocol carries it
    words: int           # 1 for bits, 2 for float32
    writable: bool
    blocks: str          # the controller blocks touching the tag


def build_map(tagdb) -> list[MapEntry]:
    """The served map, derived — never hand-maintained — from the tag DB.

    Grouped by store tag (several blocks may touch one tag), sorted by
    name so the layout is deterministic: the same area always produces the
    same map, which is what lets the exported CSV be a contract.
    """
    from azeo_control_trainer.core.strategy.tagdb import EntryKind

    by_tag: dict[str, list] = {}
    for entry in tagdb.of_kind(EntryKind.FIELD):
        if entry.io_tag:
            by_tag.setdefault(entry.io_tag, []).append(entry)

    entries: list[MapEntry] = []
    counters = {"coil": 0, "discrete": 0, "holding": 0, "input": 0}
    for tag in sorted(by_tag):
        touching = by_tag[tag]
        discrete = all(e.block_type in _DISCRETE_BLOCKS for e in touching)
        writable = any(e.writable for e in touching)
        if discrete:
            table = "coil" if writable else "discrete"
            words = 1
        else:
            table = "holding" if writable else "input"
            words = 2
        entries.append(MapEntry(
            tag=tag, table=table, address=counters[table], words=words,
            writable=writable,
            blocks=", ".join(f"{e.module}/{e.block}" for e in touching)))
        counters[table] += words
    return entries


def map_to_csv(entries: list[MapEntry]) -> str:
    lines = ["tag,table,address,words,encoding,writable,blocks"]
    for e in entries:
        encoding = "bit" if e.words == 1 else "float32 (2 regs, big-endian)"
        lines.append(f"{e.tag},{e.table},{e.address},{e.words},"
                     f"{encoding},{e.writable},\"{e.blocks}\"")
    return "\n".join(lines) + "\n"


def _float_words(value: float) -> tuple[int, int]:
    raw = struct.pack(">f", float(value))
    return struct.unpack(">HH", raw)


def _words_float(high: int, low: int) -> float:
    return struct.unpack(">f", struct.pack(">HH", high & 0xFFFF,
                                           low & 0xFFFF))[0]


class StoreModbusServer:
    """Serve the store over Modbus TCP, mapped by the tag database.

    Built on pymodbus 3.15's SimDevice with an ``action`` hook — the one
    place every request passes through, so reads are refreshed from the
    store as they are answered and writes are decoded as they land.
    """

    def __init__(self, store, *, host: str = "0.0.0.0",
                 port: int = DEFAULT_PORT, tagdb=None):
        self.store = store
        self.host = host
        self.port = int(port)
        tagdb = tagdb if tagdb is not None else getattr(store, "tagdb", None)
        if tagdb is None:
            raise ValueError("StoreModbusServer needs a tag database — "
                             "attach store.tagdb or pass tagdb=")
        self.map = build_map(tagdb)
        self._by_table: dict[str, list[MapEntry]] = {}
        for entry in self.map:
            self._by_table.setdefault(entry.table, []).append(entry)
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server = None
        self._started = threading.Event()
        self.writes_applied = 0

    # ----------------------------------------------------------- the wire
    def _encoded(self, entry: MapEntry) -> list:
        """The entry's current store value, as its on-the-wire words."""
        value = self.store.get(entry.tag)
        if entry.words == 1:
            return [bool(value)]
        high, low = _float_words(0.0 if value is None else value)
        return [high, low]

    def _serve_reads(self, table: str, registers: list,
                     start: int) -> None:
        """Refresh the requested window from the store, at request time.

        `registers` is only the **window** the request covers, starting at
        `start` — not the whole block. Writing outside it walks off the
        list, which is exactly the defect the first live test caught.
        """
        for entry in self._by_table.get(table, ()):
            words = None
            for offset in range(entry.words):
                index = entry.address + offset - start
                if 0 <= index < len(registers):
                    if words is None:
                        words = self._encoded(entry)
                    registers[index] = words[offset]

    def _apply_writes(self, table: str, registers: list, start: int,
                      address: int, set_values: list) -> None:
        """Decode a client write into `store.queue_write`.

        A float spans two registers and a client may legally write one of
        them; the pair is decoded from the written words overlaid on the
        current ones — and a word outside the request window is taken from
        the store's own encoding, so a half-write still produces a whole
        number rather than half a float.
        """
        span = range(address, address + len(set_values))
        for entry in self._by_table.get(table, ()):
            if not entry.writable:
                continue
            if not any(entry.address <= i < entry.address + entry.words
                       for i in span):
                continue
            current = self._encoded(entry)
            words = []
            for offset in range(entry.words):
                absolute = entry.address + offset
                if absolute in span:
                    words.append(set_values[absolute - address])
                elif 0 <= absolute - start < len(registers):
                    words.append(registers[absolute - start])
                else:
                    words.append(current[offset])
            if entry.words == 1:
                value = bool(words[0])
            else:
                value = round(_words_float(words[0], words[1]), 6)
            self.store.queue_write(entry.tag, value)
            self.writes_applied += 1
            log.info("Modbus server write: %s = %r", entry.tag, value)

    async def _action(self, function_code, start_address, address, count,
                      current_registers, set_values):
        """Every request passes through here — the SimDevice hook."""
        table = _TABLE_OF.get(function_code)
        if table is None:
            return None
        if set_values is None:
            self._serve_reads(table, current_registers, start_address)
        else:
            self._apply_writes(table, current_registers, start_address,
                               address, list(set_values))
        return None

    # ------------------------------------------------------------ lifecycle
    def _build_device(self):
        from pymodbus.simulator.simdata import DataType, SimData
        from pymodbus.simulator.simdevice import SimDevice

        def size(table: str) -> int:
            entries = self._by_table.get(table, ())
            return max((e.address + e.words for e in entries), default=1)

        co = [SimData(0, count=size("coil"), values=False,
                      datatype=DataType.BITS)]
        di = [SimData(0, count=size("discrete"), values=False,
                      datatype=DataType.BITS)]
        hr = [SimData(0, count=size("holding"), values=0,
                      datatype=DataType.REGISTERS)]
        ir = [SimData(0, count=size("input"), values=0,
                      datatype=DataType.REGISTERS)]
        return SimDevice(0, simdata=(co, di, hr, ir), action=self._action)

    def start(self, timeout: float = 5.0) -> bool:
        """Serve in a daemon thread; True when the socket is listening."""
        if self._thread is not None:
            return True
        from pymodbus.server import ModbusTcpServer

        device = self._build_device()

        def _run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop

            async def _serve() -> None:
                # Constructed here, not in start(): the transport captures
                # the *running* event loop at __init__.
                self._server = ModbusTcpServer(
                    device, address=(self.host, self.port))
                task = asyncio.ensure_future(self._server.serve_forever())
                for _ in range(200):
                    if self._server.is_active():
                        break
                    await asyncio.sleep(0.01)
                self._started.set()
                await task
            try:
                loop.run_until_complete(_serve())
            except Exception as error:              # noqa: BLE001
                log.error("Modbus server stopped: %s", error)
                self._started.set()
            finally:
                loop.close()

        self._thread = threading.Thread(target=_run, daemon=True,
                                        name="modbus-server")
        self._thread.start()
        self._started.wait(timeout)
        if self._started.is_set():
            log.info("Modbus TCP server on %s:%d — %d point(s) served, "
                     "%d writable", self.host, self.port, len(self.map),
                     sum(1 for e in self.map if e.writable))
        return self._started.is_set()

    def stop(self) -> bool:
        if self._server is None or self._loop is None:
            return self._thread is None or not self._thread.is_alive()
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._server.shutdown(), self._loop)
            future.result(timeout=3.0)
        except Exception:                           # noqa: BLE001
            log.warning("Modbus server did not stop cleanly")
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            if self._thread.is_alive():
                log.warning("Modbus server is still stopping")
                return False
        self._thread = None
        self._server = None
        self._loop = None
        return True

    # -------------------------------------------------------------- the map
    def save_map(self, path) -> None:
        """The register table, written to be read — like `modbus_map.py`
        is for the client side. This file is the contract an external
        HMI builds against."""
        from pathlib import Path

        Path(path).write_bytes(map_to_csv(self.map).encode("utf-8"))
