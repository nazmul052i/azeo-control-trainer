"""Tag database — every addressable point in a strategy area.

A DCS does not make you type tag names from memory. Every module parameter is
addressable, and the tools browse that namespace: the HMI binds a symbol to a
point, OPC UA exposes it, cross-reference finds who writes it. This module
builds that namespace automatically by walking the loaded modules and the
configured external-I/O catalogue — nothing is hand-maintained, so it cannot
drift from the configuration.

Four kinds of entry come out of a module, with shared memory alongside them:

``FIELD``
    An I/O point an ``AI`` / ``AO`` / ``DI`` / ``DO`` block uses, a read-only
    EIOC ``TAG*`` monitor member, plus external channels configured in
    ``_project.json`` but not yet referenced by a module. These are the tags
    that exist *outside* the controller, in the store, on the wire, or at the
    OPC UA server.

``TERMINAL``
    Every block input and output, addressed Azeo-style as
    ``<module>/<block>/<terminal>``. This is the live signal namespace: what a
    watch window subscribes to and what an HMI value binding reads.

``PARAMETER``
    Every configuration parameter of every block, from its own config schema —
    tuning constants, alarm limits, options, timers. Addressed as
    ``<module>/<block>/CONFIG/<parameter>`` so a configured value can never
    collide with a live terminal of the same name.

``MODULE``
    The module itself, so a faceplate binding has something to name.

``MEMORY``
    Typed project memory stored in ``tagdb/memory.sqlite3`` and addressed as
    ``MEMORY/<name>/VALUE``. Control Designer and PA Designer share its
    definition and retained value; it is not a physical I/O channel.

Paths use ``/`` throughout, matching Azeo's ``//MODULE/BLOCK/PARAM``
convention minus the leading slashes.

Typical use::

    db = TagDatabase.from_area("src/strategies/azeo_training")
    db.field_tags()                       # what the plant must supply
    db.search("MTR-102")                  # everything about one motor
    db.lookup("MTR-102/CND2/CONFIG/TIME_TRUE")  # one configured value
    db.writable()                         # what an HMI command may write
"""
from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Iterable, Iterator

log = logging.getLogger("strategy.tagdb")

#: Blocks that own a field I/O point, and the direction it faces.
_IO_DIRECTION = {
    "AI": "input", "DI": "input",
    "AO": "output", "DO": "output",
}

_DISCRETE_IO_TYPES = frozenset({"DI", "DO"})

# A TAG monitor block reads a structured PLC control tag. Its configured base is
# not itself a sampled scalar: runtime consumes members such as ``.Val`` and
# ``.SrcQ``.  Explorer and OPC UA therefore index the concrete members too.
_TAG_MONITOR_TYPES = frozenset({"TAGAI", "TAGAO", "TAGDI", "TAGDO"})

#: Config keys that name a field point rather than tune behaviour.
_IO_TAG_KEYS = ("tag",)


class EntryKind:
    """Kinds of addressable point. Plain strings so they serialize as-is."""

    FIELD = "field"
    TERMINAL = "terminal"
    PARAMETER = "parameter"
    MODULE = "module"
    MEMORY = "memory"


@dataclass(frozen=True)
class TagEntry:
    """One addressable point."""

    path: str                      #: MODULE/BLOCK/NAME
    kind: str                      #: EntryKind
    module: str
    block: str = ""
    block_type: str = ""
    name: str = ""
    data_type: str = ""
    unit: str = ""
    description: str = ""
    #: For FIELD entries, the store tag the block reads or writes.
    io_tag: str = ""
    #: "input" / "output" for field points and terminals; "" otherwise.
    direction: str = ""
    #: True when something outside the controller may write this point.
    writable: bool = False
    #: Present for enumerated parameters, so a browser can offer the choices.
    choices: tuple = ()
    value: object = None
    #: Engineering range for analog terminals (HMI §7.1) — populated from
    #: `Terminal.eu_range` where the block stamped one; None otherwise.
    eu_range: tuple | None = None

    def __str__(self) -> str:                              # pragma: no cover
        return self.path


class TagDatabase:
    """The addressable namespace of a strategy area."""

    def __init__(self, area_name: str = ""):
        self.area_name = area_name
        self._entries: dict[str, TagEntry] = {}
        #: Field tag -> paths of the blocks touching it, for cross-reference.
        self._by_io_tag: dict[str, list[str]] = {}
        #: Terminal path -> the terminal paths wired into it, and out of it.
        #: Walked from the wires for the same reason as everything else here:
        #: derived, so it cannot disagree with the diagram.
        self._writers: dict[str, list[str]] = {}
        self._readers: dict[str, list[str]] = {}
        self.memory = None

    # ------------------------------------------------------------ building
    @classmethod
    def from_graphs(cls, graphs: Iterable, area_name: str = "") -> "TagDatabase":
        db = cls(area_name)
        for graph in graphs:
            db.add_module(graph)
        return db

    @classmethod
    def from_area(cls, area: str | Path) -> "TagDatabase":
        """Build from every module JSON under ``area``.

        Walks ``control/``, ``sequence/`` and ``equipment/`` plus any module at
        the area root. Equipment descriptors carry no blocks and are skipped —
        they describe modules rather than being one.
        """
        from .serialization.strategy_io import load_strategy

        area = Path(area)
        db = cls(area.name)
        # Displays, deployment metadata and revision stores also contain JSON,
        # but they are not control modules.  Scanning the whole project made
        # opening Tag Database try to deserialize `_layouts.json` and every
        # PVM draft as a strategy.  Keep the walk aligned with the documented
        # engineering namespaces, while retaining legacy root-level modules.
        paths = list(area.glob("*.json"))
        for namespace in ("control", "sequence", "equipment"):
            root = area / namespace
            if root.is_dir():
                paths.extend(root.rglob("*.json"))
        for path in sorted(set(paths)):
            if path.name == "_project.json" or "versions" in path.parts:
                continue
            try:
                # Inventory reads must not rewrite the seat's last-opened
                # module for every catalog entry during application startup.
                graph, _comments = load_strategy(str(path), remember=False)
            except Exception as exc:                       # noqa: BLE001
                log.warning("tagdb: skipping %s — %s", path.name, exc)
                continue
            if not graph.blocks:
                continue                                   # equipment descriptor
            db.add_module(graph)
        db._add_configured_field_io(area / "_project.json")
        from azeo_control_trainer.core.datastore.memory_tags import MemoryTagStore
        db.memory = MemoryTagStore(area)
        db.reload_memory()
        return db

    def reload_memory(self):
        self._entries = {path: entry for path, entry in self._entries.items() if entry.kind != EntryKind.MEMORY}
        if self.memory is not None:
            values = self.memory.read_many()
            for spec in self.memory.definitions():
                self._put(TagEntry(path=spec.path, kind=EntryKind.MEMORY, module="MEMORY", block=spec.name,
                                   name="VALUE", data_type={"float": "FLOAT", "int": "INT", "bool": "BOOL", "str": "STRING"}[spec.data_type], writable=True,
                                   description=spec.description, value=values[spec.path],
                                   eu_range=(spec.min_value, spec.max_value)
                                   if spec.min_value is not None and spec.max_value is not None else None))

    def _add_configured_field_io(self, project_path: Path) -> None:
        """Index configured EIOC signals that no control module yet uses.

        An external-device catalogue is engineering configuration in its own
        right. Keeping unreferenced channels visible is what lets Explorer
        identify the difference between "configured" and "used"; inventing
        placeholder AI/AO blocks would put inactive commands on scan.
        """
        try:
            project = json.loads(project_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for area in project.get("areas", ()):
            virtual_io = area.get("virtual_io")
            configured: list[tuple[str, dict]] = []
            if isinstance(virtual_io, dict):
                # The same loader used by the live driver is the authority;
                # Explorer and Tag Database must not require a second copied
                # 536-point map merely to browse catalog-backed channels.
                try:
                    from azeo_control_trainer.connectivity.fieldio.local_virtual_io import load_signal_routes

                    routes = load_signal_routes(
                        virtual_io, project_path.parent)
                except Exception as error:              # noqa: BLE001
                    log.warning("tagdb: invalid Virtual I/O catalog — %s", error)
                    routes = {}
                configured = [
                    (tag, {
                        "direction": route.direction,
                        "kind": route.kind,
                        "data_type": route.data_type,
                        "unit": route.unit,
                        "description": route.description,
                        "plant_unit": route.plant_unit,
                        "range": route.eu_range,
                    })
                    for tag, route in routes.items()
                ]
            else:
                field_io = (area.get("eioc") or {}).get("field_io") \
                    or area.get("field_io") or {}
                configured = list((field_io.get("signals") or {}).items())
            for io_tag, raw_spec in configured:
                io_tag = str(io_tag).strip()
                spec = dict(raw_spec or {})
                if not io_tag:
                    continue
                # A module-owned field point remains the canonical entry. The
                # configured map still contributes the same key only once.
                if io_tag in self._by_io_tag:
                    continue
                direction = str(spec.get("direction") or "read").lower()
                input_direction = direction in {
                    "read", "input", "sim_to_dcs",
                }
                block_type = str(spec.get("kind") or "").upper()
                data_type = str(spec.get("data_type") or
                                ("BOOL" if block_type in ("DI", "DO")
                                 else "FLOAT")).upper()
                raw_range = spec.get("range")
                eu_range = None
                if isinstance(raw_range, (list, tuple)) and len(raw_range) == 2:
                    eu_range = (raw_range[0], raw_range[1])
                self._put(TagEntry(
                    path=f"FIELD/{io_tag}",
                    kind=EntryKind.FIELD,
                    module="",
                    block="",
                    block_type=block_type,
                    name=io_tag,
                    data_type=data_type,
                    unit=str(spec.get("unit") or ""),
                    description=str(spec.get("description") or ""),
                    io_tag=io_tag,
                    direction="input" if input_direction else "output",
                    writable=input_direction,
                    eu_range=eu_range,
                ))
                self._by_io_tag[io_tag] = []

    def add_module(self, graph) -> None:
        """Index one compiled-or-loaded :class:`StrategyGraph`."""
        module = graph.name or "UNNAMED"
        self._put(TagEntry(
            path=module,
            kind=EntryKind.MODULE,
            module=module,
            name=module,
            description=getattr(graph, "description", "") or "",
        ))

        # Module parameters live beside blocks, not inside one.  Keeping an
        # explicit PARAMETERS leg prevents a legal parameter called PID1 from
        # colliding with a block of the same name in the address namespace.
        for name, raw_spec in graph.module_parameters().items():
            spec = raw_spec if isinstance(raw_spec, dict) \
                else {"value": raw_spec}
            value = spec.get("value")
            access = str(spec.get("access") or "internal_read").lower()
            direction = access if access in {"input", "output"} else "internal"
            self._put(TagEntry(
                path=f"{module}/PARAMETERS/{name}",
                kind=EntryKind.PARAMETER,
                module=module,
                name=str(name),
                data_type=_module_parameter_type(spec.get("data_type"), value),
                unit=str(spec.get("unit") or spec.get("units") or ""),
                description=str(spec.get("description") or ""),
                direction=direction,
                # Public inputs are the module's external command surface.
                # Outputs and both internal connection types remain owned by
                # controller execution even if their current value is visible.
                writable=(access == "input"),
                value=value,
            ))

        for block in graph.blocks.values():
            self._add_block(module, block)
        self._add_wires(module, graph)

    def _add_wires(self, module: str, graph) -> None:
        """Index the wires so a point can say who writes it and who reads it.

        This is the question an engineer asks before changing anything, and it
        is the one thing the entry list alone cannot answer.
        """
        for wire in getattr(graph, "wires", {}).values():
            src = graph.blocks.get(wire.src_block_id)
            dst = graph.blocks.get(wire.dst_block_id)
            if src is None or dst is None:
                continue
            src_path = (f"{module}/{src.instance_name or src.id}"
                        f"/{wire.src_terminal}")
            dst_path = (f"{module}/{dst.instance_name or dst.id}"
                        f"/{wire.dst_terminal}")
            self._writers.setdefault(dst_path, []).append(src_path)
            self._readers.setdefault(src_path, []).append(dst_path)

    def _add_block(self, module: str, block) -> None:
        bname = block.instance_name or block.id
        btype = block.block_type
        params = dict(block.config.params)

        # ── field I/O point ────────────────────────────────────────────
        if btype in _TAG_MONITOR_TYPES:
            # Every mapped PLC member is a real external point.  Keep the
            # provider-facing source beside its block-facing terminal so the
            # browser shows exactly what runtime reads, including explicit
            # SOURCE_<member> overrides.
            for mapping in getattr(block, "mappings", ()):
                io_tag = str(block.source_tag(mapping.plc_member) or "").strip()
                if not io_tag:
                    continue
                path = f"{module}/{bname}/FIELD/{mapping.plc_member}"
                terminal = block.outputs.get(mapping.terminal)
                self._put(TagEntry(
                    path=path,
                    kind=EntryKind.FIELD,
                    module=module,
                    block=bname,
                    block_type=btype,
                    name=mapping.plc_member,
                    data_type=_dtype_name(mapping.data_type),
                    unit=getattr(terminal, "units", "") or "",
                    description=mapping.description,
                    io_tag=io_tag,
                    direction="input",
                    # External ownership permits a provider/simulator write;
                    # DataBridge still treats every TAG block as read-only.
                    writable=True,
                    value=getattr(terminal, "value", mapping.default),
                    eu_range=getattr(terminal, "eu_range", None),
                ))
                self._by_io_tag.setdefault(io_tag, []).append(path)

        direction = _IO_DIRECTION.get(btype, "")
        for key in _IO_TAG_KEYS:
            io_tag = str(params.get(key, "") or "").strip()
            if not io_tag or not direction:
                continue
            self._put(TagEntry(
                path=f"{module}/{bname}",
                kind=EntryKind.FIELD,
                module=module, block=bname, block_type=btype,
                name=bname,
                data_type="BOOL" if btype in _DISCRETE_IO_TYPES else "FLOAT",
                unit=str(params.get("eng_units", "") or ""),
                description=str(params.get("label", "")
                                or params.get("description", "") or ""),
                io_tag=io_tag,
                direction=direction,
                # An output block's tag is driven by the controller; an input
                # block's tag is supplied to it. Only the latter is something
                # the outside world writes.
                writable=(direction == "input"),
            ))
            self._by_io_tag.setdefault(io_tag, []).append(f"{module}/{bname}")

        # ── terminals: the live signal namespace ───────────────────────
        # EU stamps are already present: `from_dict` ends with
        # `_apply_config()`, which is where blocks stamp them. Never call
        # `_apply_config` from here to "refresh" — this derivation also
        # runs over LIVE on-scan graphs (the tag browser), and not every
        # block's apply is bumpless.
        # A few blocks carry the same name as both an input and an output —
        # a PID's SP is written by the operator and read back as the working
        # setpoint; an ALARM's PV likewise. Azeo treats those as *one*
        # parameter, so they merge into a single "inout" point rather than
        # colliding and losing one of the two.
        seen_terms: dict[str, str] = {}
        for terminals, tdir in ((block.inputs, "input"), (block.outputs, "output")):
            for tname, term in terminals.items():
                path = f"{module}/{bname}/{tname}"
                if tname in seen_terms:
                    prior = self._entries[path]
                    self._entries[path] = replace(
                        prior,
                        direction="inout",
                        # The output side carries the block's own computed
                        # value, which is the one worth showing.
                        value=term.value if tdir == "output" else prior.value,
                        description=prior.description or term.description or "",
                    )
                    continue
                seen_terms[tname] = tdir
                self._put(TagEntry(
                    path=path,
                    kind=EntryKind.TERMINAL,
                    module=module, block=bname, block_type=btype,
                    name=tname,
                    data_type=_dtype_name(term.data_type),
                    unit=getattr(term, "units", "") or "",
                    description=term.description or "",
                    direction=tdir,
                    value=term.value,
                    eu_range=getattr(term, "eu_range", None),
                ))

        # ── parameters: the configuration namespace ────────────────────
        try:
            schema = block.get_config_schema() or {}
        except Exception:                                  # noqa: BLE001
            schema = {}
        units = {k.upper(): v for k, v in (getattr(block, "config_units", {}) or {}).items()}
        choices = {k.upper(): v for k, v in (getattr(block, "config_choices", {}) or {}).items()}
        for pname, spec in schema.items():
            ptype, default, desc = _unpack_spec(spec)
            self._put(TagEntry(
                # ALARM legitimately owns both a DEV_HI output (the live
                # alarm state) and a DEV_HI configured limit.  Keeping the
                # CONFIG leg explicit preserves both instead of silently
                # discarding whichever entry is indexed second.
                path=f"{module}/{bname}/CONFIG/{pname}",
                kind=EntryKind.PARAMETER,
                module=module, block=bname, block_type=btype,
                name=pname,
                data_type=_pytype_name(ptype),
                unit=str(units.get(pname.upper(), "")),
                description=desc,
                # Tuning and limits are what an engineer or a supervisory layer
                # writes at run time.
                writable=True,
                choices=tuple(choices.get(pname.upper(), ()) or ()),
                value=params.get(pname, default),
            ))

    def _put(self, entry: TagEntry) -> None:
        if entry.path in self._entries:
            # Two blocks with the same instance name inside one module: the
            # graph allows it, but the namespace cannot address both. Say so
            # rather than silently keeping one.
            log.warning("tagdb: duplicate path %s (%s) — keeping the first",
                        entry.path, entry.block_type)
            return
        self._entries[entry.path] = entry

    # ------------------------------------------------------------ querying
    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[TagEntry]:
        return iter(self._entries.values())

    def __contains__(self, path: str) -> bool:
        return path in self._entries

    def lookup(self, path: str) -> TagEntry | None:
        return self._entries.get(path)

    def of_kind(self, kind: str) -> list[TagEntry]:
        return [e for e in self._entries.values() if e.kind == kind]

    def modules(self) -> list[str]:
        return [e.name for e in self.of_kind(EntryKind.MODULE)]

    def field_tags(self) -> dict[str, list[str]]:
        """Store tags the area touches, mapped to the blocks that touch them.

        This is the contract with whatever supplies data: every key here must
        exist, or the block reading it publishes Bad.
        """
        return dict(self._by_io_tag)

    def writable(self) -> list[TagEntry]:
        return [e for e in self._entries.values() if e.writable]

    def search(self, text: str, kind: str | None = None) -> list[TagEntry]:
        """Case-insensitive substring match over path, description and I/O tag."""
        needle = text.lower().strip()
        out = []
        for e in self._entries.values():
            if kind and e.kind != kind:
                continue
            if (needle in e.path.lower()
                    or needle in e.description.lower()
                    or needle in e.io_tag.lower()):
                out.append(e)
        return out

    def for_module(self, module: str) -> list[TagEntry]:
        return [e for e in self._entries.values() if e.module == module]

    def references(self, path: str) -> tuple[list[str], list[str]]:
        """``(written_by, read_by)`` for one point.

        For a **terminal** these are the wires: what drives it, and what it
        drives. For a **field** point they are the I/O blocks on either side of
        the store tag — an ``AO``/``DO`` writes it, an ``AI``/``DI`` reads it —
        which is how the example area's ``LI-101.PV`` shows up as read by two
        separate modules. A **parameter** is written by whoever configures it,
        which is not something the configuration can know, so it reports only
        the block that owns it.
        """
        entry = self._entries.get(path)
        if entry is None:
            return [], []

        if entry.kind == EntryKind.FIELD:
            written, read = [], []
            for other in self._by_io_tag.get(entry.io_tag, []):
                peer = self._entries.get(other)
                if peer is None:
                    continue
                # The controller writes an output block's tag; the outside
                # world supplies an input block's.
                (read if peer.direction == "input" else written).append(other)
            return written, read

        if entry.kind == EntryKind.PARAMETER:
            return [], [f"{entry.module}/{entry.block}"] if entry.block else []

        return (list(self._writers.get(path, ())),
                list(self._readers.get(path, ())))

    # ------------------------------------------------------------ export
    def to_dict(self) -> dict:
        return {
            "area": self.area_name,
            "generated_from": "strategy modules",
            "counts": {k: len(self.of_kind(k)) for k in
                       (EntryKind.MODULE, EntryKind.FIELD,
                        EntryKind.TERMINAL, EntryKind.PARAMETER)},
            "entries": [asdict(e) for e in self._entries.values()],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False,
                          default=str)

    def to_csv(self) -> str:
        cols = ["path", "kind", "module", "block", "block_type", "name",
                "data_type", "unit", "io_tag", "direction", "writable",
                "description"]
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore",
                           lineterminator="\n")
        w.writeheader()
        for e in self._entries.values():
            w.writerow({c: getattr(e, c) for c in cols})
        return buf.getvalue()

    def save(self, path: str | Path) -> Path:
        """Write the database beside the area. Format follows the suffix."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = self.to_csv() if path.suffix.lower() == ".csv" else self.to_json()
        path.write_text(text, encoding="utf-8")
        return path


# ---------------------------------------------------------------- helpers
def _dtype_name(dtype) -> str:
    return getattr(dtype, "name", str(dtype)).upper()


def _pytype_name(ptype) -> str:
    return {bool: "BOOL", int: "INT", float: "FLOAT", str: "STRING"}.get(
        ptype, getattr(ptype, "__name__", str(ptype)).upper())


def _module_parameter_type(declared, value) -> str:
    """Return the controller type declared by a module parameter.

    Historical module files carried only a value.  Inferring in that case
    keeps their namespace useful without rewriting those files merely because
    Tag Database was opened.
    """
    if declared not in (None, ""):
        if isinstance(declared, type):
            return _pytype_name(declared)
        return _dtype_name(declared)
    return _pytype_name(type(value)) if value is not None else "STRING"


def _unpack_spec(spec):
    """Config schema entries are ``(type, default, description)`` tuples."""
    if isinstance(spec, (tuple, list)):
        ptype = spec[0] if len(spec) > 0 else str
        default = spec[1] if len(spec) > 1 else None
        desc = spec[2] if len(spec) > 2 else ""
        return ptype, default, str(desc)
    return str, spec, ""
