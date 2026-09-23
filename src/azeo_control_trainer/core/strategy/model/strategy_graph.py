"""Strategy graph -- collection of blocks and wires forming a control strategy."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Optional
import logging

from .block_base import FunctionBlock
from .terminal import DataType
from .wire import Wire

log = logging.getLogger("strategy.graph")


# ── Wire data-type policy ────────────────────────────────────────────────
# Directed (src_type, dst_type) pairs that the runtime casts implicitly and
# that therefore make a legal connection.  Everything not listed here (and
# not an exact type match) is refused by :meth:`StrategyGraph.add_wire`.
#
#   FLOAT ↔ INT     long-standing numeric cast (counts, indices, analog values)
#   BOOL  → FLOAT   a discrete drives an analog as 0.0/1.0 — the standard SFC
#                   step-gating pattern (STEP.ACTIVE → ACT.IN1) and DI → analog
#   BOOL  → INT     same, as an integer 0/1
#   ENUM  ↔ INT/FLOAT   enum selections are numeric codes
#
# Refused (information-destroying or meaningless):
#   FLOAT/INT/ENUM → BOOL   analog silently truncated to a truthiness bit —
#                           the defect this policy exists to stop
#   STRING ↔ anything       no meaningful cast in either direction
_IMPLICIT_CASTS: frozenset[tuple[DataType, DataType]] = frozenset({
    (DataType.FLOAT, DataType.INT),
    (DataType.INT, DataType.FLOAT),
    (DataType.BOOL, DataType.FLOAT),
    (DataType.BOOL, DataType.INT),
    (DataType.ENUM, DataType.INT),
    (DataType.INT, DataType.ENUM),
    (DataType.ENUM, DataType.FLOAT),
    (DataType.FLOAT, DataType.ENUM),
})


def types_compatible(src_dtype: DataType, dst_dtype: DataType) -> bool:
    """True when a wire may carry ``src_dtype`` into ``dst_dtype``."""
    if src_dtype == dst_dtype:
        return True
    return (src_dtype, dst_dtype) in _IMPLICIT_CASTS


class StrategyGraph:
    """Container for a complete control strategy.

    Holds blocks and wires, provides graph operations for the compiler
    and serialization for save/load.
    """

    def __init__(self, name: str = "Untitled Strategy"):
        self.name: str = name
        self.description: str = ""
        self.blocks: dict[str, FunctionBlock] = {}  # id -> block
        self.wires: dict[str, Wire] = {}  # id -> wire
        # Top-level keys the loader saw but this class doesn't model. Kept so a
        # save round-trip is lossless for anything a newer/older version wrote.
        self.extra: dict = {}

    #: Azeo capability: each control module has its own scan rate. Stored in
    #: `extra` so persistence is free through the lossless round trip — and
    #: the default is deliberately *not* stored, so a module that never set
    #: a rate stays byte-identical on disk (the same contract Block Scan
    #: Rate keeps).
    _DEFAULT_SCAN_MS = 500

    # ---- module parameters (Azeo capability) --------------------------
    # A module owns named parameters — `MAX_MOVE`, `SP_HI_LIM` — declared
    # in its parameter list, referenced *bare* in CND/ACT expressions, and
    # written by ACT logic when access is "write". Stored in `extra`
    # ("parameters" top-level key), so persistence rides the lossless
    # round trip and a module that declares none stays byte-identical.
    #: Azeo parameter connection types.  These describe where a parameter
    #: appears on the diagram; write permission is a separate property.
    PARAM_ACCESS = ("input", "output", "internal_read", "internal_write")
    PARAM_WRITE_ACCESS = (
        "not_writeable",
        "writeable",
        "not_operator_writeable_when_locked",
    )

    def module_parameters(self) -> dict:
        """Return the live, mutable module parameter declaration table.

        Historical entries contain ``value/access/description``. New entries
        may additionally carry ``data_type``, ``units``, ``write_access``,
        signal status/limit, and an owning diagram ``item_id``.
        """
        params = self.extra.get("parameters")
        if not isinstance(params, dict):
            params = {}
            if "parameters" in self.extra or params:
                self.extra["parameters"] = params
        return params

    def set_module_parameter(self, name: str, value,
                             access: str = "internal_read",
                             description: str | None = None, *,
                             data_type: str | None = None,
                             units: str | None = None,
                             write_access: str | None = None) -> None:
        """Create or update one module-level parameter declaration.

        ``access`` is the graphical connection type, not authorization.
        Older modules used ``internal_write`` as both; when no explicit
        ``write_access`` exists we preserve that compatibility while new
        declarations keep the two concerns separate.
        """
        name = str(name).strip()
        if (not name or not name.replace("_", "").isalnum()
                or name[0].isdigit()):
            raise ValueError(f"{name!r} is not a legal parameter name")
        if access not in self.PARAM_ACCESS:
            raise ValueError(f"{access!r} is not a legal connection type")
        if write_access is not None \
                and write_access not in self.PARAM_WRITE_ACCESS:
            raise ValueError(f"{write_access!r} is not a legal write access")
        params = self.extra.setdefault("parameters", {})
        existing_name = next(
            (key for key in params if key.casefold() == name.casefold()),
            name,
        )
        spec = dict(params.get(existing_name, {}))
        spec["value"] = value
        spec["access"] = access
        if description is not None or "description" not in spec:
            spec["description"] = str(description or "")
        if data_type is not None:
            spec["data_type"] = str(data_type).upper()
        if units is not None:
            if units:
                spec["units"] = str(units)
            else:
                spec.pop("units", None)
        if write_access is not None:
            spec["write_access"] = write_access
        elif "write_access" not in spec:
            spec["write_access"] = (
                "writeable" if access == "internal_write"
                else "not_writeable"
            )
        if existing_name != name:
            params.pop(existing_name, None)
        params[name] = spec

    def remove_module_parameter(self, name: str) -> None:
        params = self.extra.get("parameters")
        if isinstance(params, dict):
            params.pop(name, None)
        if params == {}:
            # An empty table is not written — byte-idempotence.
            self.extra.pop("parameters", None)

    @staticmethod
    def module_parameter_is_writeable(spec: dict) -> bool:
        """Whether module logic may assign ``spec``.

        Old files have no explicit permission and used ``internal_write`` as
        both connection direction and authorization.  New files keep those
        concepts independent without invalidating the old contract.
        """
        explicit = spec.get("write_access")
        if explicit is not None:
            return explicit == "writeable"
        return spec.get("access") == "internal_write"

    @property
    def scan_ms(self) -> int:
        """This module's scan period in ms; 500 when the file is silent."""
        try:
            value = int(self.extra.get("scan_ms", self._DEFAULT_SCAN_MS))
        except (TypeError, ValueError):
            return self._DEFAULT_SCAN_MS
        return value if value > 0 else self._DEFAULT_SCAN_MS

    @scan_ms.setter
    def scan_ms(self, value: int) -> None:
        value = int(value)
        if value == self._DEFAULT_SCAN_MS or value <= 0:
            self.extra.pop("scan_ms", None)
        else:
            self.extra["scan_ms"] = value

    def add_block(self, block: FunctionBlock, *,
                  declare_parameters: bool = True) -> str:
        """Attach ``block`` and run its optional graph lifecycle hook.

        Parameter Special Items need an owning graph while they are edited,
        not only while the runtime happens to execute them.  The loader sets
        ``declare_parameters=False`` so a damaged file is reported by the
        verifier instead of being silently repaired during deserialization.
        """
        self.blocks[block.id] = block
        block._owner_graph = self
        hook = getattr(block, "on_added_to_graph", None)
        try:
            if callable(hook):
                hook(self, declare=declare_parameters)
        except Exception:
            self.blocks.pop(block.id, None)
            block._owner_graph = None
            raise
        return block.id

    def remove_block(self, block_id: str):
        # Remove all wires connected to this block
        wires_to_remove = [
            w.id
            for w in self.wires.values()
            if w.src_block_id == block_id or w.dst_block_id == block_id
        ]
        for wid in wires_to_remove:
            self.remove_wire(wid)
        block = self.blocks.pop(block_id, None)
        if block is not None:
            hook = getattr(block, "on_removed_from_graph", None)
            if callable(hook):
                hook(self)
            block._owner_graph = None

    def replace_block(
        self,
        block_id: str,
        replacement: FunctionBlock,
        *,
        input_terminal_map: Mapping[str, str] | None = None,
        output_terminal_map: Mapping[str, str] | None = None,
    ) -> FunctionBlock:
        """Atomically replace one block while retaining its graph identity.

        Assignment-style blocks such as Azeo ``TAGIO`` change their real
        block type after an engineering selection.  Removing and re-adding
        the block is not equivalent: it mints a new identity and drops every
        connection.  This operation validates *all* remapped endpoints and
        their data types before it mutates either the block table or a wire,
        then swaps the block under the existing UUID and wire IDs.

        Returns the previous block object so an editor can use the same
        operation for undo.  A failed validation leaves the graph untouched.
        """
        previous = self.blocks.get(block_id)
        if previous is None:
            raise KeyError(f"block {block_id!r} is not in the strategy")
        if replacement is previous:
            raise ValueError("replacement must be a different block object")

        input_map = dict(input_terminal_map or {})
        output_map = dict(output_terminal_map or {})
        remapped: list[tuple[Wire, str, str]] = []

        # Validate the complete proposed graph first.  A half-converted block
        # is worse than a refused conversion: it can save successfully while
        # one of its former wires silently no longer executes.
        for wire in self.get_wires_for_block(block_id):
            src_name = wire.src_terminal
            dst_name = wire.dst_terminal
            src_block = self.blocks.get(wire.src_block_id)
            dst_block = self.blocks.get(wire.dst_block_id)
            if wire.src_block_id == block_id:
                src_block = replacement
                src_name = output_map.get(src_name, src_name)
            if wire.dst_block_id == block_id:
                dst_block = replacement
                dst_name = input_map.get(dst_name, dst_name)
            if src_block is None or dst_block is None:
                raise ValueError(f"wire {wire.id} has a missing endpoint block")
            if src_name not in src_block.outputs:
                raise ValueError(
                    f"replacement output '{src_name}' required by wire "
                    f"{wire.id} does not exist"
                )
            if dst_name not in dst_block.inputs:
                raise ValueError(
                    f"replacement input '{dst_name}' required by wire "
                    f"{wire.id} does not exist"
                )
            src_dtype = src_block.outputs[src_name].data_type
            dst_dtype = dst_block.inputs[dst_name].data_type
            if not types_compatible(src_dtype, dst_dtype):
                raise ValueError(
                    f"wire {wire.id} would become incompatible: "
                    f"{src_dtype.value} -> {dst_dtype.value}"
                )
            remapped.append((wire, src_name, dst_name))

        replacement.id = block_id
        for terminal in list(previous.inputs.values()) + list(previous.outputs.values()):
            terminal.connected = False
        for terminal in list(replacement.inputs.values()) + list(replacement.outputs.values()):
            terminal.connected = False

        old_hook = getattr(previous, "on_removed_from_graph", None)
        if callable(old_hook):
            old_hook(self)
        previous._owner_graph = None
        self.blocks[block_id] = replacement
        replacement._owner_graph = self
        new_hook = getattr(replacement, "on_added_to_graph", None)
        if callable(new_hook):
            new_hook(self, declare=True)
        for wire, src_name, dst_name in remapped:
            wire.src_terminal = src_name
            wire.dst_terminal = dst_name
            self.blocks[wire.src_block_id].outputs[src_name].connected = True
            self.blocks[wire.dst_block_id].inputs[dst_name].connected = True
        return previous

    def check_connection(
        self,
        src_block_id: str,
        src_terminal: str,
        dst_block_id: str,
        dst_terminal: str,
    ) -> Optional[str]:
        """Why this connection cannot be made, or None if it is legal.

        Returns a human-readable sentence so callers (the canvas) can show the
        operator exactly what was refused instead of dropping the wire
        silently.  Terminal names are resolved through the blocks' aliases
        first, exactly as :meth:`add_wire` does.
        """
        src_block = self.blocks.get(src_block_id)
        dst_block = self.blocks.get(dst_block_id)
        if not src_block or not dst_block:
            return "block not found"

        src_terminal = src_block.resolve_terminal(src_terminal, output=True)
        dst_terminal = dst_block.resolve_terminal(dst_terminal, output=False)
        if src_terminal not in src_block.outputs:
            return (f"output '{src_terminal}' does not exist on "
                    f"{src_block.instance_name}")
        if dst_terminal not in dst_block.inputs:
            return (f"input '{dst_terminal}' does not exist on "
                    f"{dst_block.instance_name}")

        # Only one wire per input
        for w in self.wires.values():
            if w.dst_block_id == dst_block_id and w.dst_terminal == dst_terminal:
                return (f"input {dst_block.instance_name}.{dst_terminal} is "
                        f"already connected")

        src_dtype = src_block.outputs[src_terminal].data_type
        dst_dtype = dst_block.inputs[dst_terminal].data_type
        if not types_compatible(src_dtype, dst_dtype):
            return (
                f"incompatible data types — {src_block.instance_name}."
                f"{src_terminal} is {src_dtype.value} but "
                f"{dst_block.instance_name}.{dst_terminal} is {dst_dtype.value}"
            )
        return None

    def add_wire(
        self,
        src_block_id: str,
        src_terminal: str,
        dst_block_id: str,
        dst_terminal: str,
        is_bkcal: bool = False,
        allow_type_mismatch: bool = False,
    ) -> Optional[str]:
        """Add a wire. Returns wire id or None if invalid.

        A connection whose source and destination data types are genuinely
        incompatible (see :data:`_IMPLICIT_CASTS`) is **refused** — building it
        anyway used to leave, e.g., an analog PV silently truncated to a
        boolean.  ``allow_type_mismatch=True`` restores the old permissive
        behaviour for callers that are rebuilding an already-saved graph and
        must not drop wires (loading a strategy file, undo/redo).
        """
        src_block = self.blocks.get(src_block_id)
        dst_block = self.blocks.get(dst_block_id)
        if not src_block or not dst_block:
            log.warning("add_wire: block not found")
            return None
        # Accept legacy/alternate terminal spellings (IN1→IN_D1, Q→OUT, …);
        # without this the wire is dropped and the logic silently goes inert.
        src_terminal = src_block.resolve_terminal(src_terminal, output=True)
        dst_terminal = dst_block.resolve_terminal(dst_terminal, output=False)
        if src_terminal not in src_block.outputs:
            log.warning(
                "add_wire: src terminal '%s' not found on %s",
                src_terminal,
                src_block,
            )
            return None
        if dst_terminal not in dst_block.inputs:
            log.warning(
                "add_wire: dst terminal '%s' not found on %s",
                dst_terminal,
                dst_block,
            )
            return None

        # Check for existing connection to same input (only one wire per input)
        for w in self.wires.values():
            if w.dst_block_id == dst_block_id and w.dst_terminal == dst_terminal:
                log.warning(
                    "add_wire: input '%s.%s' already connected",
                    dst_block.instance_name,
                    dst_terminal,
                )
                return None

        # Refuse genuinely incompatible data types (FLOAT → BOOL and friends)
        src_dtype = src_block.outputs[src_terminal].data_type
        dst_dtype = dst_block.inputs[dst_terminal].data_type
        if not types_compatible(src_dtype, dst_dtype):
            if not allow_type_mismatch:
                log.warning(
                    "add_wire: REFUSED type mismatch %s.%s (%s) → %s.%s (%s)",
                    src_block.instance_name, src_terminal, src_dtype.value,
                    dst_block.instance_name, dst_terminal, dst_dtype.value,
                )
                return None
            log.warning(
                "add_wire: type mismatch %s.%s (%s) → %s.%s (%s) "
                "— kept (rebuilding an existing graph)",
                src_block.instance_name, src_terminal, src_dtype.value,
                dst_block.instance_name, dst_terminal, dst_dtype.value,
            )

        wire = Wire(src_block_id, src_terminal, dst_block_id, dst_terminal, is_bkcal)
        self.wires[wire.id] = wire

        # Mark terminals as connected
        src_block.outputs[src_terminal].connected = True
        dst_block.inputs[dst_terminal].connected = True

        return wire.id

    def remove_wire(self, wire_id: str):
        wire = self.wires.pop(wire_id, None)
        if not wire:
            return
        # Check if terminals are still connected to other wires
        src = self.blocks.get(wire.src_block_id)
        dst = self.blocks.get(wire.dst_block_id)
        if src and wire.src_terminal in src.outputs:
            still = any(
                w.src_block_id == wire.src_block_id
                and w.src_terminal == wire.src_terminal
                for w in self.wires.values()
            )
            src.outputs[wire.src_terminal].connected = still
        if dst and wire.dst_terminal in dst.inputs:
            still = any(
                w.dst_block_id == wire.dst_block_id
                and w.dst_terminal == wire.dst_terminal
                for w in self.wires.values()
            )
            dst.inputs[wire.dst_terminal].connected = still

    def get_wires_for_block(self, block_id: str) -> list[Wire]:
        return [
            w
            for w in self.wires.values()
            if w.src_block_id == block_id or w.dst_block_id == block_id
        ]

    def get_input_wires(self, block_id: str) -> list[Wire]:
        return [w for w in self.wires.values() if w.dst_block_id == block_id]

    def get_output_wires(self, block_id: str) -> list[Wire]:
        return [w for w in self.wires.values() if w.src_block_id == block_id]

    def type_mismatches(self) -> list[str]:
        """Wires whose data types are incompatible, as readable messages.

        Existing saved strategies predate the :meth:`add_wire` type check and
        may still contain such wires (they are loaded straight into
        ``self.wires``, bypassing ``add_wire``, so they keep working).  These
        are reported as *warnings* rather than validation errors so those
        modules still compile and run; new interactive connections are refused
        at the source instead.
        """
        out: list[str] = []
        for w in self.wires.values():
            src_block = self.blocks.get(w.src_block_id)
            dst_block = self.blocks.get(w.dst_block_id)
            if not src_block or not dst_block:
                continue
            if (w.src_terminal not in src_block.outputs
                    or w.dst_terminal not in dst_block.inputs):
                continue
            src_dtype = src_block.outputs[w.src_terminal].data_type
            dst_dtype = dst_block.inputs[w.dst_terminal].data_type
            if not types_compatible(src_dtype, dst_dtype):
                out.append(
                    f"Wire {w.id}: type mismatch "
                    f"{src_block.instance_name}.{w.src_terminal} "
                    f"({src_dtype.value}) → "
                    f"{dst_block.instance_name}.{w.dst_terminal} "
                    f"({dst_dtype.value})"
                )
        return out

    def validate(self) -> list[str]:
        """Validate graph, return list of error messages.

        Checks:
        - Wire endpoint blocks exist
        - Wire endpoint terminals exist on their blocks
        - Data types are compatible across wires (logged as warnings, not
          errors — see :meth:`type_mismatches`)
        """
        errors = []
        parameters = self.module_parameters()
        parameter_names: dict[str, str] = {}
        for name, spec in parameters.items():
            if (not str(name) or not str(name).replace("_", "").isalnum()
                    or str(name)[0].isdigit()):
                errors.append(f"Module parameter name {name!r} is not legal")
            folded = str(name).casefold()
            prior = parameter_names.get(folded)
            if prior is not None:
                errors.append(
                    f"Duplicate module parameter '{name}' conflicts with "
                    f"'{prior}' (names are case-insensitive)")
            else:
                parameter_names[folded] = str(name)
            if not isinstance(spec, dict):
                errors.append(f"Module parameter '{name}' is malformed")
                continue
            access = spec.get("access", "internal_read")
            if access not in self.PARAM_ACCESS:
                errors.append(
                    f"Module parameter '{name}' has invalid connection type "
                    f"'{access}'")
            data_type = str(spec.get("data_type", "FLOAT")).upper()
            if data_type not in {member.value for member in DataType}:
                errors.append(
                    f"Module parameter '{name}' has invalid data type "
                    f"'{data_type}'")
            write_access = spec.get("write_access")
            if write_access is not None \
                    and write_access not in self.PARAM_WRITE_ACCESS:
                errors.append(
                    f"Module parameter '{name}' has invalid write access "
                    f"'{write_access}'")

        placed: dict[str, str] = {}
        for block in self.blocks.values():
            access = getattr(block, "parameter_access", None)
            if access is None:
                continue
            name = str(getattr(block, "parameter_name", "") or "").strip()
            if not name:
                errors.append(
                    f"{block.display_name} '{block.instance_name}' has no "
                    "parameter name")
                continue
            key = parameter_names.get(name.casefold())
            spec = parameters.get(key) if key is not None else None
            if not isinstance(spec, dict):
                errors.append(
                    f"{block.display_name} '{block.instance_name}' references "
                    f"missing module parameter '{name}'")
                continue
            if spec.get("access", "internal_read") != access:
                errors.append(
                    f"{block.display_name} '{block.instance_name}' expects "
                    f"'{access}' but parameter '{key}' is "
                    f"'{spec.get('access')}'")
            dtype = str(spec.get("data_type", "FLOAT")).upper()
            terminal_pool = block.outputs if getattr(
                block, "parameter_source", False) else block.inputs
            terminal = terminal_pool.get("VALUE")
            if terminal is not None and terminal.data_type.value != dtype:
                errors.append(
                    f"{block.display_name} '{block.instance_name}' is "
                    f"{terminal.data_type.value} but parameter '{key}' is "
                    f"{dtype}")
            previous = placed.get(name.casefold())
            if previous is not None and previous != block.id:
                errors.append(
                    f"Module parameter '{key}' is placed more than once on "
                    "the diagram")
            else:
                placed[name.casefold()] = block.id
            owner = spec.get("item_id")
            if owner not in (None, block.id):
                errors.append(
                    f"Module parameter '{key}' belongs to a different "
                    "diagram item")
        for msg in self.type_mismatches():
            log.warning("validate: %s", msg)
        # Duplicate instance names are a HARD error, not a warning (HMI
        # §7.3): path-based binding (MODULE/BLOCK/TERM) silently resolves
        # an ambiguous name to whichever block wins — the worst kind of
        # wrong. `__init__` already generates unique defaults; this catches
        # copy-paste and hand-edited JSON.
        seen: dict[str, str] = {}
        for b in self.blocks.values():
            name = b.instance_name
            if name in seen:
                errors.append(
                    f"Duplicate block name '{name}' (blocks {seen[name]} "
                    f"and {b.id}) — paths must be unambiguous; rename one")
            else:
                seen[name] = b.id
        for w in self.wires.values():
            if w.src_block_id not in self.blocks:
                errors.append(f"Wire {w.id}: source block {w.src_block_id} missing")
                continue
            if w.dst_block_id not in self.blocks:
                errors.append(
                    f"Wire {w.id}: destination block {w.dst_block_id} missing"
                )
                continue
            src_block = self.blocks[w.src_block_id]
            dst_block = self.blocks[w.dst_block_id]
            if w.src_terminal not in src_block.outputs:
                errors.append(
                    f"Wire {w.id}: terminal '{w.src_terminal}' not found "
                    f"on {src_block.instance_name} outputs"
                )
            if w.dst_terminal not in dst_block.inputs:
                errors.append(
                    f"Wire {w.id}: terminal '{w.dst_terminal}' not found "
                    f"on {dst_block.instance_name} inputs"
                )
            if (w.src_terminal in src_block.outputs
                    and w.dst_terminal in dst_block.inputs
                    and (getattr(src_block, "is_special_palette_item", False)
                         or getattr(dst_block, "is_special_palette_item", False))):
                src_type = src_block.outputs[w.src_terminal].data_type
                dst_type = dst_block.inputs[w.dst_terminal].data_type
                if not types_compatible(src_type, dst_type):
                    errors.append(
                        f"Wire {w.id}: module parameter type mismatch "
                        f"{src_type.value} -> {dst_type.value}")
        return errors

    def clear(self):
        for block in list(self.blocks.values()):
            hook = getattr(block, "on_removed_from_graph", None)
            if callable(hook):
                hook(self)
            block._owner_graph = None
        self.blocks.clear()
        self.wires.clear()

    def to_dict(self) -> dict:
        """Serialize the graph.

        Anything the loader saw but this class doesn't model is carried through
        `extra` — previously `to_dict` emitted only name/blocks/wires, so saving
        a module destroyed its `description` (9 shipped modules carry one) and
        any other top-level key a future version might add.
        """
        d = dict(getattr(self, "extra", {}) or {})
        d["name"] = self.name
        if getattr(self, "description", ""):
            d["description"] = self.description
        d["blocks"] = [b.to_dict() for b in self.blocks.values()]
        d["wires"] = [w.to_dict() for w in self.wires.values()]
        return d

    def __repr__(self):
        return (
            f"<StrategyGraph '{self.name}' "
            f"blocks={len(self.blocks)} wires={len(self.wires)}>"
        )
