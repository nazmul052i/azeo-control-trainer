"""The PK's OPC UA server: the module namespace, served with quality.

The real PK embeds an OPC UA server whose address space *is* the module
hierarchy — no tag mapping step, because the configuration is the
namespace. This is that server for the trainer, derived the way everything
here is derived: from `store.tagdb`, so it cannot drift from what the
controller runs.

What is published, and what deliberately is not:

- **Every block terminal**, live: `Modules/<MODULE>/<BLOCK>/<TERMINAL>`,
  values refreshed from the online runtimes' graphs each period, with the
  terminal's `Quality` carried as the OPC UA **StatusCode** — the thing
  Modbus structurally cannot say. A Bad transmitter reads Bad on the wire,
  never as a plausible number (invariant I6, across the network).
- **Writable field points** under `FieldIO/`, writable per the tag
  database's own rule (`entry.writable`) — the input/output split that
  stops an external client commanding a valve the controller owns. Client
  writes land on `store.queue_write`; an executive makes them real.
- **Not** the blocks' internal configuration parameters. GAIN and RESET
  are build-time values an external client has no business polling;
  publishing them would triple the node count to serve constants. They
  can join later behind an opt-in if a real use appears.

Terminals are read-only at the protocol level: a wire re-asserts a written
terminal on the next scan (hard-won item 27), so accepting the write would
be offering a control that silently does nothing.
"""
from __future__ import annotations

import asyncio
import logging
import threading

log = logging.getLogger("opcua.pk_server")

# asyncua narrates at INFO — every internal publish, every monitored item,
# and a screenful of harmless address-space notes at import. That is
# library telemetry, not trainer information, and it drowned the log the
# engineer actually reads (user-pasted evidence). Its WARNINGs still land.
logging.getLogger("asyncua").setLevel(logging.WARNING)

DEFAULT_PORT = 4840
#: Localhost by default: the endpoint is anonymous (a trainer, not a
#: plant), so a wider bind is an explicit choice in `_project.json`.
DEFAULT_HOST = "127.0.0.1"
NAMESPACE_URI = "urn:azeo:pk-controller"
REFRESH_S = 0.5


def _quality_code(quality, limit=None):
    """StatusCode carrying severity AND the DataValue limit bits (§8.2).

    The mapping table lives in `status_mapping.to_ua_status`, tested over
    all twelve combinations without a server; this is just the ua wrapper.
    """
    from asyncua import ua

    from .status_mapping import to_ua_status

    return ua.StatusCode(to_ua_status(quality, limit))


def _variant(value, data_type: str):
    from asyncua import ua

    data_type = _canonical_ua_type(data_type)
    if data_type == "bool":
        return ua.Variant(bool(value), ua.VariantType.Boolean)
    if data_type in ("int", "enum"):
        try:
            return ua.Variant(int(value), ua.VariantType.Int64)
        except (TypeError, ValueError):
            return ua.Variant(0, ua.VariantType.Int64)
    if data_type == "string":
        return ua.Variant("" if value is None else str(value),
                          ua.VariantType.String)
    try:
        return ua.Variant(float(value), ua.VariantType.Double)
    except (TypeError, ValueError):
        return ua.Variant(0.0, ua.VariantType.Double)


def _canonical_ua_type(data_type) -> str:
    """Map model/catalog spellings onto the four UA scalar families.

    The tag database intentionally emits engineering-facing upper-case names,
    while type descriptions historically used lower case.  Comparing those
    strings directly silently exposed Boolean and integer points as Double.
    """
    name = str(getattr(data_type, "name", data_type) or "").strip().lower()
    if name in {"bool", "boolean"}:
        return "bool"
    if name in {"int", "integer", "enum"}:
        return "int"
    if name in {"str", "string"}:
        return "string"
    return "float"


class PKOpcUaServer:
    """Serve the module namespace over OPC UA, from the tag database."""

    def __init__(self, store, *, host: str = DEFAULT_HOST,
                 port: int = DEFAULT_PORT, tagdb=None):
        self.store = store
        self.host = host
        self.port = int(port)
        self.tagdb = tagdb if tagdb is not None else getattr(store, "tagdb",
                                                             None)
        if self.tagdb is None:
            raise ValueError("PKOpcUaServer needs a tag database")
        self.endpoint = f"opc.tcp://{host}:{self.port}/azeo/pk"
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server = None
        self._stopping = threading.Event()
        self._started = threading.Event()
        self._failed = False
        #: node -> (module, block, name, data_type) for terminals
        self._terminal_nodes: list = []
        #: node key -> (store tag, data_type), for writable field points
        self._field_nodes: dict = {}
        #: last value we published per terminal node.
        self._published: dict = {}
        #: last *store* value pushed per field node — the refresh only
        #: republishes when the store moved, so it cannot clobber a client
        #: write that the executive has not drained yet.
        self._field_pushed: dict = {}
        #: our own in-flight field writes; the data-change handler discards
        #: exactly these instead of queueing them back as client writes.
        self._self_tokens: set = set()
        self._seen_initial: set = set()
        #: node key -> (module, block, param, python type) for CONFIG
        #: parameters (HMI §8.3 — the write path with different semantics).
        self._config_nodes: dict = {}
        #: terminal node key -> Forced-property node (I5).
        self._forced_nodes: dict = {}
        #: (module, block) -> {name: node} for the derived ALARMS summary
        #: (HMI §8.5 minimal read-only alarm state).
        self._alarm_nodes: dict = {}
        #: (module, block) -> ISO time the current alarm went active.
        self._alarm_since: dict = {}
        #: graphs a client has written config into — the designer's cue
        #: that the module is dirty from outside.
        self.externally_modified: set = set()
        #: (module, block) -> CONDITIONS node for MOTOR_INTERLOCK blocks.
        self._interlock_nodes: dict = {}
        self.terminal_count = 0
        self.writable_count = 0
        self.config_count = 0
        self.type_count = 0

    # ------------------------------------------------------------ lifecycle
    def start(self, timeout: float = 20.0) -> bool:
        if self._thread is not None:
            return self._started.is_set()
        self._stopping.clear()
        self._started.clear()
        self._failed = False

        def _run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            try:
                loop.run_until_complete(self._serve())
            except Exception as error:              # noqa: BLE001
                log.error("OPC UA server stopped: %s", error)
                self._failed = True
                self._started.set()
            finally:
                loop.close()

        self._thread = threading.Thread(target=_run, daemon=True,
                                        name="opcua-pk-server")
        self._thread.start()
        self._started.wait(timeout)
        ok = self._started.is_set() and not self._failed
        if ok:
            log.info("OPC UA server at %s — %d terminal(s), %d writable "
                     "field point(s)", self.endpoint, self.terminal_count,
                     self.writable_count)
            if self.host == "0.0.0.0":
                # Bound on every interface; tell the engineer the address a
                # remote client actually types. asyncua rewrites endpoint
                # hostnames to whatever address the client connected with
                # (match_discovery_client_ip), so any reachable IP works.
                import socket as _socket

                try:
                    hostname = _socket.gethostname()
                    address = _socket.gethostbyname(hostname)
                    log.info("Reachable from remote machines at "
                             "opc.tcp://%s:%d/azeo/pk (host %s) — allow "
                             "TCP %d through the firewall",
                             address, self.port, hostname, self.port)
                except OSError:
                    pass
        return ok

    def stop(self) -> bool:
        if self._thread is None:
            return True
        self._stopping.set()
        self._thread.join(timeout=5.0)
        if self._thread.is_alive():
            log.warning("OPC UA server is still stopping")
            return False
        self._thread = None
        self._loop = None
        self._server = None
        return True

    # ------------------------------------------------------------ the space
    async def _build_space(self) -> None:
        from asyncua import ua

        from azeo_control_trainer.core.strategy.tagdb import EntryKind

        index = await self._server.register_namespace(NAMESPACE_URI)
        objects = self._server.nodes.objects
        modules_folder = await objects.add_folder(
            ua.NodeId("Modules", index), ua.QualifiedName("Modules", index))

        # One UA ObjectType per block type the area actually uses (HMI
        # §8.1): the type layer reaching the address space. The type node
        # carries the terminal set as variable declarations WITHOUT
        # modelling rules — deliberate: instantiation must not generate
        # children, because instances keep the stable `ns=2;s=path`
        # NodeIds an integrator already relies on. A client learns the
        # type from HasTypeDefinition and browses <TYPE>Type for the
        # terminal vocabulary.
        type_nodes: dict = {}
        area_types = sorted({e.block_type
                             for e in self.tagdb.of_kind(EntryKind.TERMINAL)
                             if e.block_type})
        try:
            from azeo_control_trainer.core.strategy.model.block_registry import BlockRegistry
            from azeo_control_trainer.core.strategy.type_catalog import describe_type

            base_type = self._server.nodes.base_object_type
            registry = BlockRegistry()
            for block_type in area_types:
                block_cls = registry.get(block_type)
                if block_cls is None:
                    continue
                definition = describe_type(block_cls)
                type_node = await base_type.add_object_type(
                    ua.NodeId(f"Type:{block_type}", index),
                    ua.QualifiedName(f"{block_type}Type", index))
                for direction, terms in (("in", definition["inputs"]),
                                         ("out", definition["outputs"])):
                    for term in terms:
                        await type_node.add_variable(
                            ua.NodeId(f"Type:{block_type}/{direction}/"
                                      f"{term['name']}", index),
                            ua.QualifiedName(term["name"], index),
                            _variant(term.get("default"),
                                     "bool" if term["data_type"] == "bool"
                                     else "string"
                                     if term["data_type"] == "string"
                                     else "float"))
                type_nodes[block_type] = type_node
        except Exception as error:                  # noqa: BLE001
            log.warning("ObjectType emission skipped: %s", error)
        self.type_count = len(type_nodes)

        module_folders: dict = {}
        block_folders: dict = {}
        block_types: dict = {}
        for entry in self.tagdb.of_kind(EntryKind.TERMINAL):
            if entry.module not in module_folders:
                module_folders[entry.module] = await modules_folder.add_folder(
                    ua.NodeId(entry.module, index),
                    ua.QualifiedName(entry.module, index))
            block_key = (entry.module, entry.block)
            if block_key not in block_folders:
                block_types[block_key] = entry.block_type
                type_node = type_nodes.get(entry.block_type)
                parent = module_folders[entry.module]
                node_id = ua.NodeId(f"{entry.module}/{entry.block}", index)
                bname = ua.QualifiedName(entry.block, index)
                if type_node is not None:
                    # Typed instance: HasTypeDefinition -> <TYPE>Type.
                    # No children instantiate (no modelling rules above),
                    # so the stable per-terminal NodeIds below stand.
                    block_folders[block_key] = await parent.add_object(
                        node_id, bname, objecttype=type_node.nodeid)
                else:
                    block_folders[block_key] = await parent.add_folder(
                        node_id, bname)
            node = await block_folders[block_key].add_variable(
                ua.NodeId(entry.path, index),
                ua.QualifiedName(entry.name, index),
                _variant(None, entry.data_type))
            # Terminals are read-only on the wire: a wire re-asserts a
            # written terminal next scan, so the write would be a lie.
            self._terminal_nodes.append(
                (node, entry.module, entry.block, entry.name,
                 entry.data_type))
            # AnalogItemType, structurally (HMI §8.1): EURange and
            # EngineeringUnits as properties with the standard browse
            # names, so UaExpert shows range and unit with no client
            # configuration. Static — the block's config is their source.
            if entry.eu_range is not None:
                await node.add_property(
                    ua.NodeId(f"{entry.path}.EURange", index),
                    ua.QualifiedName("EURange", 0),
                    ua.Range(Low=float(entry.eu_range[0]),
                             High=float(entry.eu_range[1])))
            if entry.unit:
                await node.add_property(
                    ua.NodeId(f"{entry.path}.EngineeringUnits", index),
                    ua.QualifiedName("EngineeringUnits", 0),
                    ua.EUInformation(
                        DisplayName=ua.LocalizedText(entry.unit),
                        Description=ua.LocalizedText(entry.unit)))
            # Forced state as a sibling property (I5): a forced terminal
            # drawn as a normal value is a safety defect, and Modbus-style
            # transports cannot say it — this one can.
            forced_node = await node.add_property(
                ua.NodeId(f"{entry.path}.Forced", index),
                ua.QualifiedName("Forced", index),
                ua.Variant(False, ua.VariantType.Boolean))
            self._forced_nodes[node.nodeid.to_string()] = forced_node
        self.terminal_count = len(self._terminal_nodes)

        # CONFIG folder per block (HMI §8.3): config writes have different
        # semantics from terminal writes — they update `config.params`,
        # re-apply, and mark the module externally modified. The namespace
        # split is what makes that distinction visible to clients.
        config_folders: dict = {}
        for entry in self.tagdb.of_kind(EntryKind.PARAMETER):
            block_key = (entry.module, entry.block)
            if block_key not in block_folders:
                continue
            if block_key not in config_folders:
                config_folders[block_key] = await block_folders[
                    block_key].add_folder(
                    ua.NodeId(f"{entry.module}/{entry.block}/CONFIG", index),
                    ua.QualifiedName("CONFIG", index))
            value = entry.value
            data_type = ("bool" if isinstance(value, bool)
                         else "string" if isinstance(value, str)
                         else "float")
            node = await config_folders[block_key].add_variable(
                ua.NodeId(f"{entry.module}/{entry.block}/CONFIG/"
                          f"{entry.name}", index),
                ua.QualifiedName(entry.name, index),
                _variant(value, data_type))
            await node.set_writable()
            self._config_nodes[node.nodeid.to_string()] = (
                entry.module, entry.block, entry.name, data_type)
        self.config_count = len(self._config_nodes)

        # Minimal alarm state per AI block (HMI §8.5): the *_ACT terminals
        # are already on the wire; this is the consolidated summary a PVM
        # reads — active, priority, condition, breached limit, since when.
        for block_key, folder in block_folders.items():
            module, block_name = block_key
            entry = self.tagdb.lookup(f"{module}/{block_name}/HI_HI_ACT")
            if entry is None or entry.block_type != "AI":
                continue
            alarms = await folder.add_folder(
                ua.NodeId(f"{module}/{block_name}/ALARMS", index),
                ua.QualifiedName("ALARMS", index))
            nodes = {}
            for name, initial, dtype in (
                    ("ACTIVE", False, "bool"), ("ACKED", False, "bool"),
                    ("PRIORITY", 0, "int"), ("CONDITION", "", "string"),
                    ("BREACHED_LIMIT", 0.0, "float"),
                    ("SINCE", "", "string")):
                nodes[name] = await alarms.add_variable(
                    ua.NodeId(f"{module}/{block_name}/ALARMS/{name}",
                              index),
                    ua.QualifiedName(name, index),
                    _variant(initial, dtype))
            self._alarm_nodes[block_key] = nodes

        # Interlock condition tables (HMI §8.6): a table, not scalars —
        # served as one JSON document per MOTOR_INTERLOCK block, refreshed
        # from `condition_table()`. The valve/motor faceplate needs rows
        # (name, delays, timer, state, bypass, first-out), and per-row
        # nodes would triple the space for one consumer.
        for block_key, folder in block_folders.items():
            if block_types.get(block_key) != "MOTOR_INTERLOCK":
                continue
            module, block_name = block_key
            node = await folder.add_variable(
                ua.NodeId(f"{module}/{block_name}/CONDITIONS", index),
                ua.QualifiedName("CONDITIONS", index),
                _variant("[]", "string"))
            self._interlock_nodes[block_key] = node

        field_folder = await objects.add_folder(
            ua.NodeId("FieldIO", index), ua.QualifiedName("FieldIO", index))
        seen: set = set()
        for entry in self.tagdb.of_kind(EntryKind.FIELD):
            if not entry.writable or not entry.io_tag \
                    or entry.io_tag in seen:
                continue
            seen.add(entry.io_tag)
            data_type = _canonical_ua_type(entry.data_type)
            # The NodeId is the bare store tag — `ns=2;s=LI-101.PV` — the
            # convention an integrator types into any UA client from the
            # tag list alone, no browsing needed. The FieldIO folder is
            # only the browse home; the identity is the tag.
            node = await field_folder.add_variable(
                ua.NodeId(entry.io_tag, index),
                ua.QualifiedName(entry.io_tag, index),
                _variant(self.store.get(entry.io_tag), data_type))
            await node.set_writable()
            self._field_nodes[node.nodeid.to_string()] = (entry.io_tag,
                                                          data_type)
        self.writable_count = len(self._field_nodes)

    # ----------------------------------------------------------- the values
    def _live_graphs(self) -> dict:
        graphs = {}
        try:
            runtimes = list(self.store.get_strategy_runtimes())
        except Exception:                           # noqa: BLE001
            runtimes = []
        for runtime in runtimes:
            graph = getattr(getattr(runtime, "compiled", None), "graph",
                            None)
            if graph is not None:
                graphs[graph.name] = graph
        return graphs

    async def _refresh(self) -> None:
        """Publish live terminal values, with quality, changed ones only."""
        from asyncua import ua

        graphs = self._live_graphs()
        blocks: dict = {}
        for name, graph in graphs.items():
            for block in graph.blocks.values():
                blocks[(name, block.instance_name)] = block

        for node, module, block_name, term, data_type in \
                self._terminal_nodes:
            block = blocks.get((module, block_name))
            if block is None:
                continue                    # module not on scan: last value
            terminal = block.outputs.get(term) or block.inputs.get(term)
            if terminal is None:
                continue
            key = node.nodeid.to_string()
            stamp = (terminal.value, getattr(terminal.status, "name", ""),
                     getattr(terminal.limit, "name", ""),
                     bool(terminal.forced))
            if self._published.get(key) == stamp:
                continue
            self._published[key] = stamp
            await self._server.write_attribute_value(
                node.nodeid,
                ua.DataValue(Value=_variant(terminal.value, data_type),
                             StatusCode_=_quality_code(terminal.status,
                                                       terminal.limit)))
            forced_node = self._forced_nodes.get(key)
            if forced_node is not None:
                await self._server.write_attribute_value(
                    forced_node.nodeid,
                    ua.DataValue(Value=ua.Variant(
                        bool(terminal.forced), ua.VariantType.Boolean)))

        await self._refresh_alarms(blocks)

        for key, (tag, data_type) in self._field_nodes.items():
            value = self.store.get(tag)
            if key in self._field_pushed                     and self._field_pushed[key] == value:
                continue
            self._field_pushed[key] = value
            variant = _variant(value, data_type)
            self._self_tokens.add((key, repr(variant.Value)))
            node = self._server.get_node(key)
            await self._server.write_attribute_value(
                node.nodeid, ua.DataValue(Value=variant))

        # Config values follow the engineer's edits: what the wire serves
        # is always the block's current config, token-marked so our own
        # refresh is never mistaken for a client write.
        for key, (module, block_name, param, dtype) in \
                self._config_nodes.items():
            block = blocks.get((module, block_name))
            if block is None:
                continue
            value = block.config.params.get(param)
            ckey = f"cfg:{key}"
            if ckey in self._published and self._published[ckey] == value:
                continue
            self._published[ckey] = value
            variant = _variant(value, dtype)
            self._self_tokens.add((key, repr(variant.Value)))
            await self._server.write_attribute_value(
                self._server.get_node(key).nodeid,
                ua.DataValue(Value=variant))

    # The Azeo alarm priorities the trainer uses throughout: HI_HI/LO_LO
    # are CRITICAL (15), HI/LO are WARNING (11). Order = severity: the
    # summary names the worst active condition.
    _ALARM_CONDITIONS = (("HI_HI", 15, "HI_HI_LIM"), ("LO_LO", 15, "LO_LO_LIM"),
                         ("HI", 11, "HI_LIM"), ("LO", 11, "LO_LIM"))

    async def _refresh_alarms(self, blocks: dict) -> None:
        """Derive the per-AI ALARMS summary (HMI §8.5, read-only)."""
        import datetime

        from asyncua import ua

        for block_key, nodes in self._alarm_nodes.items():
            block = blocks.get(block_key)
            if block is None:
                continue
            active_name, priority, breached = "", 0, 0.0
            for condition, prio, limit_param in self._ALARM_CONDITIONS:
                terminal = block.outputs.get(f"{condition}_ACT")
                if terminal is not None and bool(terminal.value):
                    active_name, priority = condition, prio
                    try:
                        breached = float(
                            block.config.params.get(limit_param, 0.0))
                    except (TypeError, ValueError):
                        breached = 0.0
                    break
            if active_name and block_key not in self._alarm_since:
                self._alarm_since[block_key] = datetime.datetime.now(
                    datetime.timezone.utc).isoformat(timespec="seconds")
            elif not active_name:
                self._alarm_since.pop(block_key, None)
            state = (active_name, priority, breached,
                     self._alarm_since.get(block_key, ""))
            key = f"alarm:{block_key}"
            if self._published.get(key) == state:
                continue
            self._published[key] = state
            for name, value, dtype in (
                    ("ACTIVE", bool(active_name), "bool"),
                    ("PRIORITY", priority, "int"),
                    ("CONDITION", active_name, "string"),
                    ("BREACHED_LIMIT", breached, "float"),
                    ("SINCE", state[3], "string")):
                await self._server.write_attribute_value(
                    nodes[name].nodeid,
                    ua.DataValue(Value=_variant(value, dtype)))

        import json as _json

        for block_key, node in self._interlock_nodes.items():
            block = blocks.get(block_key)
            if block is None or not hasattr(block, "condition_table"):
                continue
            try:
                document = _json.dumps(block.condition_table(),
                                       separators=(",", ":"))
            except Exception:                       # noqa: BLE001
                continue
            key = f"ilk:{block_key}"
            if self._published.get(key) == document:
                continue
            self._published[key] = document
            await self._server.write_attribute_value(
                node.nodeid,
                ua.DataValue(Value=_variant(document, "string")))

    async def _serve(self) -> None:
        from asyncua import Server

        self._server = Server()
        await self._server.init()
        self._server.set_endpoint(self.endpoint)
        self._server.set_server_name("Azeo PK Controller")
        await self._build_space()

        async with self._server:
            # Client writes to FieldIO arrive through the subscription; a
            # change we did not publish ourselves is a client's.
            subscription = await self._server.create_subscription(
                200, _WriteWatcher(self))
            watched = list(self._field_nodes) + list(self._config_nodes)
            if watched:
                await subscription.subscribe_data_change(
                    [self._server.get_node(k) for k in watched])
            self._started.set()
            while not self._stopping.is_set():
                try:
                    await self._refresh()
                except Exception as error:          # noqa: BLE001
                    log.warning("OPC UA refresh failed: %s", error)
                await asyncio.sleep(REFRESH_S)


class _WriteWatcher:
    """Turns a client's write into a queued store write — and nothing else.

    The subscription also sees the server's own refreshes; `_published`
    is the ledger that tells the two apart, which is what stops every
    plant-driven field change echoing back into the write queue forever.
    """

    def __init__(self, server: PKOpcUaServer):
        self._server = server

    def datachange_notification(self, node, value, data) -> None:
        server = self._server
        key = node.nodeid.to_string()
        if key in server._config_nodes:
            self._config_change(server, key, value)
            return
        mapped = server._field_nodes.get(key)
        if mapped is None:
            return
        tag, _data_type = mapped
        # Mark "seen" unconditionally and FIRST: the initial notification
        # usually carries a value the refresh already pushed, so it also
        # matches a self-token — and consuming the token without marking
        # left the *next* genuine client write mistaken for the initial
        # one and silently swallowed.
        first = key not in server._seen_initial
        server._seen_initial.add(key)
        token = (key, repr(value))
        if token in server._self_tokens:
            server._self_tokens.discard(token)      # our own refresh
            return
        if first:
            # The subscription's initial notification carries the current
            # value; treating it as a write flooded the queue with every
            # field point's startup value.
            return
        # A genuine client write. Remember it as pushed, so the refresh
        # does not clobber the wire with the stale store value while the
        # executive has not drained the queue yet.
        server._field_pushed[key] = value
        server.store.queue_write(tag, value)
        log.info("OPC UA client write: %s = %r", tag, value)

    @staticmethod
    def _config_change(server: PKOpcUaServer, key: str, value) -> None:
        """A CONFIG write (HMI §8.3): apply to the live block, re-apply
        config, and mark the module externally modified.

        Deliberately NOT the terminal path: config changes touch
        `config.params` and `_apply_config()` — a tuning constant written
        from a client that reverted on reload would be a serious defect,
        so the graph is flagged for the engineer to save. Saving stays an
        engineering action (rule 6: nothing auto-saves shipped modules).
        """
        module, block_name, param, _dtype = server._config_nodes[key]
        first = key not in server._seen_initial
        server._seen_initial.add(key)
        token = (key, repr(value))
        if token in server._self_tokens:
            server._self_tokens.discard(token)
            return
        if first:
            return                      # the subscription's initial echo
        graphs = server._live_graphs()
        graph = graphs.get(module)
        block = None
        if graph is not None:
            block = next((b for b in graph.blocks.values()
                          if b.instance_name == block_name), None)
        if block is None:
            log.warning("OPC UA config write ignored — %s/%s not on scan",
                        module, block_name)
            return
        current = block.config.params.get(param)
        if isinstance(current, bool):
            value = bool(value)
        elif isinstance(current, int) and not isinstance(current, bool):
            try:
                value = int(value)
            except (TypeError, ValueError):
                pass
        block.config.params[param] = value
        try:
            block._apply_config()
        except Exception as error:                  # noqa: BLE001
            log.warning("apply_config after client write failed: %s",
                        error)
        server.externally_modified.add(module)
        log.info("OPC UA client CONFIG write: %s/%s.%s = %r",
                 module, block_name, param, value)
