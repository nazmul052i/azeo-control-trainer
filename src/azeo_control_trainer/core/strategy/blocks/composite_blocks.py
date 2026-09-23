"""Composite blocks with nested strategies and explicit boundary ports.

Three block types:
- INPORT:     Boundary input — defines an external input terminal on the composite
- OUTPORT:    Boundary output — defines an external output terminal on the composite
- COMPOSITE:  Container block holding an internal sub-strategy (StrategyGraph)

Composites appear as a single block on the parent canvas with terminals
derived from the INPORT/OUTPORT blocks inside. Double-click opens the
internal strategy in a new editor tab.
"""
from __future__ import annotations

from copy import deepcopy
import logging

from ..model.block_base import FunctionBlock, BlockCategory
from ..model.block_registry import register_block
from ..model.terminal import DataType, LimitStatus, Quality

log = logging.getLogger("strategy.composite")


# ════════════════════════════════════════════════════════════════════
# INPORT — boundary input block (lives inside a composite)
# ════════════════════════════════════════════════════════════════════

@register_block
class InportBlock(FunctionBlock):
    """Boundary input port inside a composite.

    Each INPORT creates a corresponding input terminal on the parent
    composite block.  The port_name config param is the terminal name
    visible on the parent.
    """

    block_type = "INPORT"
    category = BlockCategory.IO
    display_name = "Inport"
    description = "Composite boundary input — exposes a terminal on the parent block"

    def _define_terminals(self):
        self.add_output("OUT", DataType.FLOAT, 0.0, "Value passed from parent input")

    def _apply_config(self):
        port_name = self.config.params.get("port_name", "")
        if port_name:
            self.instance_name = port_name
        # Support BKCAL inports (for back-calculation chains crossing boundary)
        self._is_bkcal = self.config.params.get("is_bkcal", False)
        _configure_boundary_terminal(self.outputs["OUT"], self.config.params)

    def execute(self, dt: float):
        # Value is injected by the composite's execute(); nothing to compute.
        pass

    def get_config_schema(self) -> dict:
        return {
            "port_name": (str, "IN", "Terminal name on parent composite"),
            "is_bkcal": (bool, False, "Back-calculation port (feedback)"),
            "data_type": (str, "FLOAT", "Data type (FLOAT, BOOL, INT, STRING, ENUM)"),
            "description": (str, "", "Port description"),
        }


# ════════════════════════════════════════════════════════════════════
# OUTPORT — boundary output block (lives inside a composite)
# ════════════════════════════════════════════════════════════════════

@register_block
class OutportBlock(FunctionBlock):
    """Boundary output port inside a composite.

    Each OUTPORT creates a corresponding output terminal on the parent
    composite block.  The value received at IN is propagated to the
    parent's output terminal.
    """

    block_type = "OUTPORT"
    category = BlockCategory.IO
    display_name = "Outport"
    description = "Composite boundary output — exposes a terminal on the parent block"

    def _define_terminals(self):
        self.add_input("IN", DataType.FLOAT, 0.0, "Value to expose on parent output")

    def _apply_config(self):
        port_name = self.config.params.get("port_name", "")
        if port_name:
            self.instance_name = port_name
        self._is_bkcal = self.config.params.get("is_bkcal", False)
        _configure_boundary_terminal(self.inputs["IN"], self.config.params)

    def execute(self, dt: float):
        # Value is read by the composite's execute() after inner scan.
        pass

    def get_config_schema(self) -> dict:
        return {
            "port_name": (str, "OUT", "Terminal name on parent composite"),
            "is_bkcal": (bool, False, "Back-calculation port (feedback)"),
            "data_type": (str, "FLOAT", "Data type (FLOAT, BOOL, INT, STRING, ENUM)"),
            "description": (str, "", "Port description"),
        }


# ════════════════════════════════════════════════════════════════════
# COMPOSITE — container block with internal sub-strategy
# ════════════════════════════════════════════════════════════════════

@register_block
class CompositeBlock(FunctionBlock):
    """A container block holding an internal sub-strategy.

    Terminals are dynamically created from the INPORT/OUTPORT blocks
    found inside the internal graph.  Execution delegates to the
    internal blocks in compiled order.

    Serialization stores the full internal graph inline in config.
    """

    block_type = "COMPOSITE"
    category = BlockCategory.COMPOSITE
    display_name = "Composite"
    description = "Container block with internal sub-strategy (like Azeo Module)"

    def __init__(self, instance_name: str = ""):
        # Internal graph — must exist before _define_terminals
        from ..model.strategy_graph import StrategyGraph
        self._inner_graph = StrategyGraph("Composite")
        self._inner_compiled = None  # CompiledStrategy after compilation
        self._port_map_in: dict[str, str] = {}   # terminal_name -> inport block_id
        self._port_map_out: dict[str, str] = {}  # terminal_name -> outport block_id
        self._parameter_map_in: dict[str, str] = {}
        self._parameter_map_out: dict[str, str] = {}
        # A linked instance still embeds its effective graph. Controllers can
        # therefore execute the last downloaded revision when the engineering
        # library is offline; these fields add refreshability without making
        # runtime execution depend on a filesystem lookup.
        self.definition_id: str = ""
        self.definition_revision: int = 0
        self.definition_digest: str = ""
        self._definition_snapshot: dict | None = None
        self.public_parameter_overrides: dict[str, object] = {}
        super().__init__(instance_name)

    def _define_terminals(self):
        # Terminals are rebuilt dynamically from inner INPORT/OUTPORT blocks
        self._rebuild_terminals()

    def _rebuild_terminals(self):
        """Build composite pins from boundary ports and public parameters."""
        # Clear existing dynamic terminals (preserve any manually added ones)
        self.inputs.clear()
        self.outputs.clear()
        self._port_map_in.clear()
        self._port_map_out.clear()
        self._parameter_map_in.clear()
        self._parameter_map_out.clear()

        for block in self._inner_graph.blocks.values():
            if block.block_type == "INPORT":
                block._apply_config()
                port_name = block.config.params.get("port_name", block.instance_name)
                is_bkcal = block.config.params.get("is_bkcal", False)
                dt_str = block.config.params.get("data_type", "FLOAT")
                dt = _parse_data_type(dt_str)
                desc = block.config.params.get("description", "")
                self.add_input(
                    port_name, dt, _default_for_data_type(dt), desc,
                    is_bkcal=is_bkcal,
                )
                self._port_map_in[port_name] = block.id

            elif block.block_type == "OUTPORT":
                block._apply_config()
                port_name = block.config.params.get("port_name", block.instance_name)
                is_bkcal = block.config.params.get("is_bkcal", False)
                dt_str = block.config.params.get("data_type", "FLOAT")
                dt = _parse_data_type(dt_str)
                desc = block.config.params.get("description", "")
                self.add_output(
                    port_name, dt, _default_for_data_type(dt), desc,
                    is_bkcal=is_bkcal,
                )
                self._port_map_out[port_name] = block.id

        # Azeo public Input/Output Parameters automatically become the
        # containing module block's pins. Internal Read/Write remain private.
        for name, spec in self._inner_graph.module_parameters().items():
            if not isinstance(spec, dict):
                continue
            access = spec.get("access")
            if access not in ("input", "output"):
                continue
            dtype = _parse_data_type(spec.get("data_type", "FLOAT"))
            value = _coerce_boundary_value(
                spec.get("value", _default_for_data_type(dtype)), dtype)
            description = str(spec.get("description", "") or "")
            units = str(spec.get("units", "") or "")
            if access == "input":
                if name in self.inputs:
                    log.error("Composite %s: public input %s collides with a port",
                              self.instance_name, name)
                    continue
                terminal = self.add_input(name, dtype, value, description)
                terminal.units = units
                self._parameter_map_in[name] = name
            else:
                if name in self.outputs:
                    log.error("Composite %s: public output %s collides with a port",
                              self.instance_name, name)
                    continue
                terminal = self.add_output(name, dtype, value, description)
                terminal.units = units
                self._parameter_map_out[name] = name

    @property
    def inner_graph(self):
        return self._inner_graph

    @inner_graph.setter
    def inner_graph(self, graph):
        self._inner_graph = graph
        self._inner_compiled = None
        self._rebuild_terminals()

    @property
    def is_linked(self) -> bool:
        """Whether this instance follows a versioned library definition."""
        return bool(self.definition_id)

    @property
    def definition_snapshot(self) -> dict | None:
        """A defensive copy of the pristine last-known definition record."""
        return deepcopy(self._definition_snapshot)

    @classmethod
    def from_definition(cls, definition, instance_name: str = "", *,
                        overrides: dict[str, object] | None = None
                        ) -> CompositeBlock:
        """Create a linked instance from a committed definition."""
        block = cls(instance_name or definition.name)
        block.link_to_definition(definition, overrides=overrides)
        return block

    def link_to_definition(self, definition, *,
                           overrides: dict[str, object] | None = None) -> None:
        """Link (or relink) this block to a committed definition revision."""
        from ..composites.library import CompositeDefinition

        if not isinstance(definition, CompositeDefinition):
            raise TypeError("definition must be a CompositeDefinition")
        normalized = CompositeDefinition.from_dict(
            deepcopy(definition.to_dict()))
        supplied = _normalized_overrides(
            normalized, dict(overrides or {}), allow_unknown=False)
        effective = _effective_graph(normalized, supplied)
        self._install_definition(normalized, supplied, effective)

    def definition_state(self, library=None) -> str:
        """Return ``embedded``, ``current``, ``stale``, or ``missing``.

        A damaged/unavailable library is deliberately indistinguishable from
        a missing one at the instance boundary: either way the embedded graph
        remains the safe executable fallback.
        """
        if not self.is_linked:
            return "embedded"
        if library is None:
            from ..composites.library import project_composite_library
            library = project_composite_library()
        try:
            current = library.get(self.definition_id)
        except (KeyError, OSError, TypeError, ValueError):
            return "missing"
        if (current.revision == self.definition_revision
                and current.digest == self.definition_digest):
            return "current"
        return "stale"

    def is_definition_stale(self, library=None) -> bool:
        """True only when a newer/different definition is available."""
        return self.definition_state(library) == "stale"

    def refresh_from_library(self, library=None, *,
                             expected_revision: int | None = None) -> bool:
        """Refresh from the latest library revision, preserving overrides.

        ``expected_revision`` protects a caller that began an edit/refresh
        against a different instance revision. A missing library returns
        ``False`` and leaves the embedded last-known graph untouched.
        """
        if not self.is_linked:
            return False
        if expected_revision is not None:
            self._check_expected_revision(expected_revision)
        if library is None:
            from ..composites.library import project_composite_library
            library = project_composite_library()
        try:
            definition = library.get(self.definition_id)
        except (KeyError, OSError, TypeError, ValueError):
            return False
        self.refresh_from_definition(
            definition, expected_revision=expected_revision)
        return True

    def refresh_from_definition(self, definition, *,
                                expected_revision: int | None = None) -> None:
        """Install a supplied successor definition atomically."""
        from ..composites.library import CompositeDefinition

        if not self.is_linked:
            raise ValueError("embedded composites cannot be refreshed")
        if expected_revision is not None:
            self._check_expected_revision(expected_revision)
        if not isinstance(definition, CompositeDefinition):
            raise TypeError("definition must be a CompositeDefinition")
        normalized = CompositeDefinition.from_dict(
            deepcopy(definition.to_dict()))
        if normalized.id != self.definition_id:
            raise ValueError(
                f"definition {normalized.id!r} does not match linked "
                f"instance {self.definition_id!r}")
        # Removed public properties become inert but remain recorded. This is
        # intentional: an engineer can recover an instance value if a later
        # definition revision restores the property.
        overrides = _normalized_overrides(
            normalized, dict(self.public_parameter_overrides),
            allow_unknown=True)
        effective = _effective_graph(normalized, overrides)
        self._install_definition(normalized, overrides, effective)

    def set_public_parameter_override(self, name: str, value) -> None:
        """Set one explicit instance override and rebuild the effective graph."""
        definition = self._snapshot_definition()
        parameter = _public_parameter(definition, name)
        overrides = dict(self.public_parameter_overrides)
        overrides[parameter.name] = _coerce_public_value(
            value, parameter.data_type)
        effective = _effective_graph(definition, overrides)
        self.public_parameter_overrides = overrides
        self.inner_graph = effective

    def clear_public_parameter_override(self, name: str) -> bool:
        """Return a public property to its definition default."""
        definition = self._snapshot_definition()
        parameter = _public_parameter(definition, name)
        overrides = dict(self.public_parameter_overrides)
        existed = parameter.name in overrides
        overrides.pop(parameter.name, None)
        effective = _effective_graph(definition, overrides)
        self.public_parameter_overrides = overrides
        self.inner_graph = effective
        return existed

    def public_parameter_value(self, name: str):
        """Return the effective value of a linked public parameter."""
        definition = self._snapshot_definition()
        parameter = _public_parameter(definition, name)
        return self.public_parameter_overrides.get(
            parameter.name,
            _coerce_public_value(parameter.default, parameter.data_type),
        )

    def unlink_to_embedded(self) -> bool:
        """Keep the effective graph but sever all definition semantics."""
        if not self.is_linked:
            return False
        self.definition_id = ""
        self.definition_revision = 0
        self.definition_digest = ""
        self._definition_snapshot = None
        self.public_parameter_overrides = {}
        return True

    def _snapshot_definition(self):
        from ..composites.library import CompositeDefinition

        if not self.is_linked or not self._definition_snapshot:
            raise RuntimeError(
                "linked composite has no definition snapshot; refresh it "
                "before editing public parameters")
        return CompositeDefinition.from_dict(
            deepcopy(self._definition_snapshot))

    def _check_expected_revision(self, expected_revision: int) -> None:
        from ..composites.library import CompositeRevisionConflict

        if self.definition_revision != int(expected_revision):
            raise CompositeRevisionConflict(
                f"instance is revision {self.definition_revision}; "
                f"the editor started from {expected_revision}")

    def _install_definition(self, definition, overrides: dict[str, object],
                            effective_graph) -> None:
        """Commit a fully prepared definition/effective graph to the instance."""
        self.definition_id = definition.id
        self.definition_revision = definition.revision
        self.definition_digest = definition.digest
        self._definition_snapshot = deepcopy(definition.to_dict())
        self.public_parameter_overrides = deepcopy(overrides)
        self.inner_graph = effective_graph

    def compile_inner(self, depth: int = 0):
        """Compile the internal sub-strategy for execution.

        Called by the compiler for every composite in a graph, so a
        composite's interior is compiled whenever the outer strategy is
        (without it the interior never runs and every output stays 0).

        ``depth`` is this interior's nesting level (1 = interior of a
        composite on the top-level strategy). It is passed straight
        through to :func:`compile_strategy`, which compiles any composite
        found *inside* this interior at ``depth + 1`` and stops at
        ``MAX_COMPOSITE_DEPTH``.
        """
        from ..engine.compiler import compile_strategy
        self._inner_compiled = compile_strategy(self._inner_graph,
                                                _composite_depth=depth)
        return self._inner_compiled

    def reset(self):
        """Reset own terminals *and* every interior block.

        The runtime resets top-level blocks before going online; without
        this the interior would carry stale values from the previous run
        into the first scan.
        """
        super().reset()
        for block in self._inner_graph.blocks.values():
            try:
                block.reset()
            except Exception as e:                  # pragma: no cover
                log.error("Composite %s: reset of inner block %s failed: %s",
                          self.instance_name, block.instance_name, e)

    def execute(self, dt: float):
        """Execute the internal sub-strategy for one scan.

        1. Copy parent input value/status/limit → INPORT block outputs
        2. Propagate forward wires
        3. Execute inner blocks in compiled order
        4. Propagate BKCAL wires
        5. Copy OUTPORT input value/status/limit → parent output terminals
        """
        if self._inner_compiled is None:
            return

        graph = self._inner_compiled.graph

        # Public parameter pins are graph state rather than INPORT/OUTPORT
        # blocks. Inject their complete signal state before any source item
        # executes so value, quality and limit cross the module boundary.
        for port_name, parameter_name in self._parameter_map_in.items():
            terminal = self.inputs.get(port_name)
            spec = graph.module_parameters().get(parameter_name)
            if terminal is not None and isinstance(spec, dict):
                _copy_terminal_to_parameter(terminal, spec)

        # 1. Inject parent inputs into INPORT outputs
        for port_name, block_id in self._port_map_in.items():
            inport = graph.blocks.get(block_id)
            if inport and port_name in self.inputs:
                _copy_terminal_state(
                    self.inputs[port_name], inport.outputs["OUT"]
                )

        # Pre-build a {dst_block_id -> [wire,...]} lookup so we can pull
        # each consumer's inputs *immediately before* it executes. Doing
        # a single up-front forward-wire pass would leave OUTPORTs (and
        # any other late consumers) one scan behind their producers.
        wires_by_dst: dict[str, list] = {}
        for wire in self._inner_compiled.forward_wires:
            wires_by_dst.setdefault(wire.dst_block_id, []).append(wire)

        # 2 + 3. Per-block: propagate this block's forward inputs, then execute.
        for block_id in self._inner_compiled.exec_order:
            block = graph.blocks.get(block_id)
            if block is None:
                continue
            for wire in wires_by_dst.get(block_id, ()):
                src_block = graph.blocks.get(wire.src_block_id)
                if not src_block:
                    continue
                src_term = src_block.outputs.get(wire.src_terminal)
                dst_term = block.inputs.get(wire.dst_terminal)
                if src_term and dst_term:
                    _copy_terminal_state(src_term, dst_term)
            # Same per-block housekeeping the top-level runtime does:
            # the runtime only ever sees the composite, so forces and the
            # runtime context have to be applied to the interior here.
            # Nested composites inherit the context through this too.
            block.runtime_context = self.runtime_context
            block._module_graph = graph
            block.tick_force_timers(dt)
            block.apply_forces()
            if getattr(block, "_bypassed", False):
                continue
            if not block.due_this_scan():
                continue
            try:
                block.execute(dt)
            except Exception as e:
                log.error("Composite %s inner block %s error: %s",
                          self.instance_name, block.instance_name, e)
            # Keep forced inputs pinned for diagnostics; outputs are not
            # forceable and always remain the algorithm's actual result.
            block.apply_forces()

        # 4. Propagate BKCAL wires (one-scan delay)
        for wire in self._inner_compiled.bkcal_wires:
            src_block = graph.blocks.get(wire.src_block_id)
            dst_block = graph.blocks.get(wire.dst_block_id)
            if src_block and dst_block:
                src_term = src_block.outputs.get(wire.src_terminal)
                dst_term = dst_block.inputs.get(wire.dst_terminal)
                if src_term and dst_term:
                    _copy_terminal_state(src_term, dst_term)

        # 5. Read OUTPORT inputs into parent outputs
        for port_name, block_id in self._port_map_out.items():
            outport = graph.blocks.get(block_id)
            if outport and port_name in self.outputs:
                _copy_terminal_state(
                    outport.inputs["IN"], self.outputs[port_name]
                )
        for port_name, parameter_name in self._parameter_map_out.items():
            terminal = self.outputs.get(port_name)
            spec = graph.module_parameters().get(parameter_name)
            if terminal is not None and isinstance(spec, dict):
                _copy_parameter_to_terminal(spec, terminal)

    # ── Serialization ──

    def to_dict(self) -> dict:
        d = super().to_dict()
        # The effective graph is always inline: a controller/runtime must not
        # need the engineering library just to execute a downloaded module.
        d["inner_graph"] = self._inner_graph.to_dict()
        if self.is_linked:
            d["definition_id"] = self.definition_id
            d["definition_revision"] = self.definition_revision
            d["definition_digest"] = self.definition_digest
            d["definition_snapshot"] = deepcopy(self._definition_snapshot)
            d["public_parameter_overrides"] = deepcopy(
                self.public_parameter_overrides)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> CompositeBlock:
        block = cls(instance_name=data.get("instance_name", ""))
        block.id = data["id"]
        block.x = data.get("x", 0)
        block.y = data.get("y", 0)
        block.config.params = deepcopy(data.get("config", {}))

        block.definition_id = str(data.get("definition_id", "") or "")
        try:
            block.definition_revision = int(
                data.get("definition_revision", 0) or 0)
        except (TypeError, ValueError):
            block.definition_revision = 0
        block.definition_digest = str(
            data.get("definition_digest", "") or "")
        snapshot = data.get("definition_snapshot")
        block._definition_snapshot = (
            deepcopy(snapshot) if isinstance(snapshot, dict) else None)
        raw_overrides = data.get("public_parameter_overrides", {})
        block.public_parameter_overrides = (
            deepcopy(raw_overrides) if isinstance(raw_overrides, dict) else {})

        # Restore custom UI size
        if "ui_width" in data:
            block._ui_width = data["ui_width"]
        if "ui_height" in data:
            block._ui_height = data["ui_height"]

        # Prefer the serialized effective graph. The definition snapshot is a
        # recovery source for early linked files that omitted it.
        inner_data = data.get("inner_graph", {})
        if inner_data:
            block._inner_graph = _deserialize_inner_graph(inner_data)
            block._rebuild_terminals()
        elif block._definition_snapshot:
            from ..composites.library import CompositeDefinition
            definition = CompositeDefinition.from_dict(
                deepcopy(block._definition_snapshot))
            block._inner_graph = _effective_graph(
                definition, block.public_parameter_overrides)
            block._rebuild_terminals()

        # These fields must be restored after dynamic boundary terminals have
        # been rebuilt. The old order silently lost hidden composite ports.
        for name in data.get("hidden_terminals", []):
            if name in block.inputs:
                block.inputs[name].hidden = True
            elif name in block.outputs:
                block.outputs[name].hidden = True
        for name, value in (data.get("forced_terminals") or {}).items():
            block.force_terminal(name, value)
        block._bypassed = data.get("bypassed", False)
        block.scan_rate = data.get("scan_rate", 1)

        ignored = block.normalize_config()
        if ignored:
            log.warning(
                "%s '%s': config keys have no effect: %s",
                block.block_type, block.instance_name,
                ", ".join(sorted(ignored)),
            )
        block._apply_config()
        return block

    def _apply_config(self):
        pass

    def get_config_schema(self) -> dict:
        return {
            "description": (str, "", "Composite block description"),
            "color": (str, "", "Custom header color (hex, e.g. #7B1FA2)"),
        }

    def get_inner_block_count(self) -> int:
        """Return number of blocks inside (excluding INPORT/OUTPORT)."""
        return sum(1 for b in self._inner_graph.blocks.values()
                   if b.block_type not in ("INPORT", "OUTPORT"))

    def get_inner_blocks(self) -> list[FunctionBlock]:
        """Return all inner blocks (for bridge iteration)."""
        return list(self._inner_graph.blocks.values())

    def get_all_inner_blocks_recursive(self) -> list[FunctionBlock]:
        """Return all blocks recursively (flattens nested composites)."""
        result = []
        for block in self._inner_graph.blocks.values():
            result.append(block)
            if isinstance(block, CompositeBlock):
                result.extend(block.get_all_inner_blocks_recursive())
        return result


# ════════════════════════════════════════════════════════════════════
# Helper functions
# ════════════════════════════════════════════════════════════════════

def _public_parameter(definition, name: str):
    """Resolve a public parameter without making names case-sensitive."""
    wanted = str(name).strip().casefold()
    matches = [parameter for parameter in definition.public_parameters
               if parameter.name.casefold() == wanted]
    if not matches:
        raise KeyError(f"unknown public parameter {name!r}")
    if len(matches) > 1:
        raise ValueError(f"ambiguous public parameter {name!r}")
    return matches[0]


def _normalized_overrides(definition, overrides: dict, *,
                          allow_unknown: bool) -> dict[str, object]:
    """Canonicalize and type-check explicit instance overrides.

    Unknown values are retained only during refresh. A property can disappear
    for one definition revision and return in a later one; retaining its last
    instance value makes that transition lossless without letting a new typo
    into a freshly linked instance.
    """
    declared: dict[str, object] = {}
    for parameter in definition.public_parameters:
        folded = parameter.name.casefold()
        if folded in declared:
            raise ValueError(
                f"public parameter names differ only by case: "
                f"{parameter.name!r}")
        declared[folded] = parameter

    normalized: dict[str, object] = {}
    for raw_name, raw_value in overrides.items():
        name = str(raw_name).strip()
        parameter = declared.get(name.casefold())
        if parameter is None:
            if allow_unknown:
                normalized[name] = deepcopy(raw_value)
                continue
            raise ValueError(f"unknown public parameter {raw_name!r}")
        normalized[parameter.name] = _coerce_public_value(
            raw_value, parameter.data_type)
    return normalized


def _coerce_public_value(value, data_type: str):
    """Coerce a public value using the definition's declared scalar type."""
    kind = str(data_type or "ANY").strip().upper()
    try:
        if kind in {"FLOAT", "REAL", "DOUBLE", "NUMBER"}:
            return float(value)
        if kind in {"INT", "INTEGER"}:
            return int(value)
        if kind in {"BOOL", "BOOLEAN"}:
            if isinstance(value, str):
                folded = value.strip().casefold()
                if folded in {"true", "yes", "on", "1"}:
                    return True
                if folded in {"false", "no", "off", "0"}:
                    return False
                raise ValueError(f"not a boolean: {value!r}")
            if isinstance(value, (bool, int, float)):
                return bool(value)
            raise ValueError(f"not a boolean: {value!r}")
        if kind in {"STRING", "TEXT"}:
            return str(value)
        if kind == "ENUM":
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                return deepcopy(value)
            raise ValueError(f"not an enum scalar: {value!r}")
        if kind in {"ANY", "OBJECT"}:
            return deepcopy(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"cannot convert {value!r} to public type {kind}") from exc
    raise ValueError(f"unsupported public parameter type {data_type!r}")


def _effective_graph(definition, overrides: dict):
    """Build an independent executable graph with defaults/overrides applied."""
    graph = _deserialize_inner_graph(deepcopy(definition.graph))
    for parameter in definition.public_parameters:
        raw_value = overrides.get(parameter.name, parameter.default)
        value = _coerce_public_value(raw_value, parameter.data_type)
        _assign_public_parameter(graph, parameter.path, value)
    return graph


def _assign_public_parameter(graph, path: str, value) -> None:
    """Assign one definition property to a block config or module parameter.

    Canonical paths are ``BLOCK/CONFIG/PARAM`` and
    ``BLOCK/IDENTITY/INSTANCE_NAME`` or ``MODULE/PARAMETERS/PARAM``.
    ``BLOCK/PARAM``, ``PARAMETERS/PARAM`` and a bare module parameter are
    accepted for authored/legacy definitions.

    A complete Control Module class needs an instance-specific control-block
    identity as well as instance-specific tag configuration.  Without the
    identity path, two linked motor instances both publish a generic
    ``ctrl.DEVICE`` surface and collide even though their field tags differ.
    Block ids remain stable while the public identity is applied, so class
    definitions should use ids for other public paths when renaming a block.
    """
    text = str(path).strip().replace("\\", "/")
    parts = [part.strip() for part in text.split("/") if part.strip()]
    if len(parts) == 1 and "." in parts[0]:
        parts = [part.strip() for part in parts[0].split(".") if part.strip()]
    if not parts:
        raise ValueError("public parameter path cannot be empty")

    # Full controller-style paths can retain the module name as their first
    # component. A reusable definition itself starts at the block component.
    if len(parts) >= 4 and parts[0].casefold() in {
            graph.name.casefold(), "module"}:
        parts = parts[1:]

    if len(parts) == 1:
        _assign_module_parameter(graph, parts[0], value, path)
        return
    if (len(parts) == 2
            and parts[0].casefold() in {"module", "parameters", "config"}):
        _assign_module_parameter(graph, parts[1], value, path)
        return
    if (len(parts) == 3
            and parts[0].casefold() == "module"
            and parts[1].casefold() in {"parameters", "config"}):
        _assign_module_parameter(graph, parts[2], value, path)
        return

    if (len(parts) == 3
            and parts[1].casefold() == "identity"
            and parts[2].casefold() in {"instance_name", "name"}):
        block = _resolve_public_block(graph, parts[0], path)
        name = str(value).strip()
        if not name:
            raise ValueError(
                f"public parameter path {path!r} needs a non-empty name")
        duplicate = next((
            candidate for candidate in graph.blocks.values()
            if candidate.id != block.id
            and candidate.instance_name.casefold() == name.casefold()
        ), None)
        if duplicate is not None:
            raise ValueError(
                f"public parameter path {path!r} duplicates block name "
                f"{name!r}")
        block.instance_name = name
        return

    if len(parts) == 2:
        block_ref, parameter_name = parts
    elif len(parts) == 3 and parts[1].casefold() == "config":
        block_ref, parameter_name = parts[0], parts[2]
    else:
        raise ValueError(f"unsupported public parameter path {path!r}")
    block = _resolve_public_block(graph, block_ref, path)
    canonical = _resolve_config_parameter(block, parameter_name, path)
    block.config.params[canonical] = deepcopy(value)
    ignored = block.normalize_config()
    if canonical in ignored:
        raise ValueError(
            f"public parameter path {path!r} targets an unused config key")
    block._apply_config()


def _resolve_public_block(graph, reference: str, path: str):
    exact = graph.blocks.get(reference)
    if exact is not None:
        return exact
    folded = reference.casefold()
    matches = [block for block in graph.blocks.values()
               if block.instance_name.casefold() == folded]
    if not matches:
        raise ValueError(
            f"public parameter path {path!r} names no block {reference!r}")
    if len(matches) > 1:
        raise ValueError(
            f"public parameter path {path!r} has ambiguous block "
            f"{reference!r}")
    return matches[0]


def _resolve_config_parameter(block, reference: str, path: str) -> str:
    folded = reference.casefold()
    candidates: set[str] = set(block.config.params)
    try:
        candidates.update(block.get_config_schema())
    except Exception:  # pragma: no cover - third-party block defensive path
        pass
    candidates.update(block.config_aliases)
    matches = [name for name in candidates if name.casefold() == folded]
    if not matches:
        raise ValueError(
            f"public parameter path {path!r} names no configuration "
            f"parameter {reference!r} on {block.instance_name!r}")
    canonical = sorted(matches)[0]
    for alias, target in block.config_aliases.items():
        if canonical.casefold() == alias.casefold():
            canonical = target
            break
    return canonical


def _assign_module_parameter(graph, reference: str, value, path: str) -> None:
    parameters = graph.module_parameters()
    folded = reference.casefold()
    matches = [name for name in parameters if name.casefold() == folded]
    if not matches:
        raise ValueError(
            f"public parameter path {path!r} names no module parameter "
            f"{reference!r}")
    if len(matches) > 1:
        raise ValueError(
            f"public parameter path {path!r} has ambiguous module parameter "
            f"{reference!r}")
    name = matches[0]
    spec = parameters[name]
    if isinstance(spec, dict):
        updated = deepcopy(spec)
        updated["value"] = deepcopy(value)
        parameters[name] = updated
    else:
        parameters[name] = {
            "value": deepcopy(value),
            "access": "internal_read",
            "description": "",
        }


def _parse_data_type(dt_str: str) -> DataType:
    """Parse a data type string into a DataType enum."""
    _map = {
        "FLOAT": DataType.FLOAT,
        "BOOL": DataType.BOOL,
        "INT": DataType.INT,
        "STRING": DataType.STRING,
        "ENUM": DataType.ENUM,
    }
    return _map.get(dt_str.upper(), DataType.FLOAT)


def _default_for_data_type(data_type: DataType):
    """Return a correctly typed neutral value for a boundary terminal."""
    return {
        DataType.FLOAT: 0.0,
        DataType.BOOL: False,
        DataType.INT: 0,
        DataType.STRING: "",
        DataType.ENUM: 0,
    }[data_type]


def _coerce_boundary_value(value, data_type: DataType):
    """Coerce retained runtime state after a port's configured type changes."""
    try:
        if data_type is DataType.BOOL:
            return bool(value)
        if data_type in (DataType.INT, DataType.ENUM):
            return int(value)
        if data_type is DataType.FLOAT:
            return float(value)
        return str(value)
    except (TypeError, ValueError):
        return _default_for_data_type(data_type)


def _configure_boundary_terminal(terminal, params: dict) -> None:
    """Apply INPORT/OUTPORT metadata to its real internal terminal.

    The parent composite has always used ``data_type`` from configuration,
    but the internal boundary terminal used to remain FLOAT. That split let
    the canvas present a typed port while the compiler validated a different
    type inside the composite.
    """
    data_type = _parse_data_type(str(params.get("data_type", "FLOAT")))
    old_default = terminal.default_value
    current_value = terminal.value
    forced_value = terminal.forced_value
    new_default = _default_for_data_type(data_type)
    terminal.data_type = data_type
    terminal.default_value = new_default
    terminal.value = (
        new_default if current_value == old_default
        else _coerce_boundary_value(current_value, data_type)
    )
    terminal.forced_value = (
        new_default if forced_value == old_default
        else _coerce_boundary_value(forced_value, data_type)
    )
    # A blank optional port description means "use the block's useful
    # built-in description", not "erase it".  Erasing it made the type
    # catalog and engineering property panes lose the only explanation of
    # what the boundary terminal carries.
    configured_description = str(params.get("description", "")).strip()
    if configured_description:
        terminal.description = configured_description
    terminal.is_bkcal = bool(params.get("is_bkcal", False))


def _copy_terminal_state(source, destination) -> None:
    """Transfer the Azeo value/status/limit triple across a boundary."""
    destination.value = source.value
    destination.status = source.status
    destination.limit = source.limit


def _copy_terminal_to_parameter(source, spec: dict) -> None:
    """Transfer a public composite input into its module parameter record."""
    dtype = _parse_data_type(str(spec.get("data_type", "FLOAT")))
    spec["value"] = _coerce_boundary_value(source.value, dtype)
    spec["status"] = source.status.name
    spec["limit"] = source.limit.value


def _copy_parameter_to_terminal(spec: dict, destination) -> None:
    """Transfer a public module output back to the containing block pin."""
    dtype = destination.data_type
    destination.value = _coerce_boundary_value(spec.get("value"), dtype)
    try:
        destination.status = Quality[str(spec.get("status", "GOOD")).upper()]
    except KeyError:
        destination.status = Quality.GOOD
    raw_limit = str(spec.get("limit", "NOT_LIMITED")).upper()
    try:
        destination.limit = LimitStatus[raw_limit]
    except KeyError:
        try:
            destination.limit = LimitStatus(raw_limit)
        except ValueError:
            destination.limit = LimitStatus.NOT_LIMITED


def _deserialize_inner_graph(data: dict):
    """Deserialize an inner graph dict into a StrategyGraph."""
    from ..model.strategy_graph import StrategyGraph
    from ..model.block_registry import registry
    from ..model.wire import Wire

    graph = StrategyGraph(name=data.get("name", "Composite"))
    graph.description = data.get("description", "") or ""
    modeled = {"name", "description", "blocks", "wires"}
    graph.extra = deepcopy(
        {key: value for key, value in data.items() if key not in modeled})

    for bdata in data.get("blocks", []):
        block_type = bdata["block_type"]
        block_cls = registry.get(block_type)
        if block_cls is None:
            log.warning("Composite inner: unknown block type '%s'", block_type)
            continue
        block = block_cls.from_dict(bdata)
        graph.add_block(block, declare_parameters=False)

    for wdata in data.get("wires", []):
        wire = Wire.from_dict(wdata)
        graph.wires[wire.id] = wire
        # Restore connected flags
        src = graph.blocks.get(wire.src_block_id)
        dst = graph.blocks.get(wire.dst_block_id)
        if src and wire.src_terminal in src.outputs:
            src.outputs[wire.src_terminal].connected = True
        if dst and wire.dst_terminal in dst.inputs:
            dst.inputs[wire.dst_terminal].connected = True

    return graph


def collapse_to_composite(
    parent_graph,
    block_ids: list[str],
    composite_name: str = "Composite",
) -> CompositeBlock | None:
    """Collapse a selection of blocks into a new CompositeBlock.

    Moves the selected blocks into a new composite's inner graph.
    Wires between selected blocks become internal wires.
    Wires crossing the boundary become INPORT/OUTPORT connections.

    Args:
        parent_graph: The StrategyGraph containing the selected blocks
        block_ids: IDs of blocks to collapse
        composite_name: Name for the new composite

    Returns:
        The new CompositeBlock (already added to parent_graph), or None on error
    """
    from ..model.strategy_graph import StrategyGraph

    selected = set(block_ids)
    if not selected:
        return None

    # Gather selected blocks and classify wires
    # Preserve document order. Iterating the set made otherwise-identical
    # grouping operations derive different boundary-port suffixes between
    # processes because Python hash order is intentionally not stable.
    blocks_to_move = [block for bid, block in parent_graph.blocks.items()
                      if bid in selected]
    if not blocks_to_move:
        return None
    if any(getattr(block, "is_special_palette_item", False)
           for block in blocks_to_move):
        # A module parameter belongs to its current module namespace. Moving
        # its icon without an explicit promote/remap operation would either
        # orphan the record or silently change the public interface.
        log.warning("Parameter Special Items cannot be grouped implicitly")
        return None

    # Compute bounding box for positioning
    min_x = min(b.x for b in blocks_to_move)
    min_y = min(b.y for b in blocks_to_move)
    max_x = max(b.x for b in blocks_to_move)
    max_y = max(b.y for b in blocks_to_move)
    center_x = (min_x + max_x) / 2
    center_y = (min_y + max_y) / 2

    # Classify all wires touching selected blocks
    internal_wires = []       # both ends inside selection
    incoming_wires = []       # src outside, dst inside
    outgoing_wires = []       # src inside, dst outside

    for wire in list(parent_graph.wires.values()):
        src_in = wire.src_block_id in selected
        dst_in = wire.dst_block_id in selected
        if src_in and dst_in:
            internal_wires.append(wire)
        elif not src_in and dst_in:
            incoming_wires.append(wire)
        elif src_in and not dst_in:
            outgoing_wires.append(wire)

    # Create the composite
    composite = CompositeBlock(instance_name=composite_name)
    composite.x = center_x
    composite.y = center_y

    inner_graph = StrategyGraph(composite_name)

    # Move blocks to inner graph (adjust positions relative to composite)
    for block in blocks_to_move:
        block.x -= min_x - 100  # offset so blocks start at ~(100, 100) inside
        block.y -= min_y - 100
        inner_graph.blocks[block.id] = block

    # Move internal wires to inner graph
    for wire in internal_wires:
        inner_graph.wires[wire.id] = wire

    # Create INPORT blocks for incoming wires
    inport_counter = {}
    incoming_port_names: dict[str, str] = {}
    for wire in incoming_wires:
        dst_block = inner_graph.blocks.get(wire.dst_block_id)
        if not dst_block:
            continue
        port_name = f"{wire.dst_terminal}"
        # Ensure unique port names
        if port_name in inport_counter:
            inport_counter[port_name] += 1
            port_name = f"{port_name}_{inport_counter[port_name]}"
        else:
            inport_counter[port_name] = 0
        incoming_port_names[wire.id] = port_name

        destination = dst_block.inputs[wire.dst_terminal]

        inport = InportBlock(instance_name=port_name)
        inport.config.params.update({
            "port_name": port_name,
            "is_bkcal": wire.is_bkcal,
            "data_type": destination.data_type.value,
            "description": destination.description,
        })
        inport._apply_config()
        inport.x = 20
        inport.y = 100 + len(inner_graph.blocks) * 80
        inner_graph.add_block(inport)

        # Wire INPORT.OUT → original destination inside
        inner_graph.add_wire(
            inport.id,
            "OUT",
            wire.dst_block_id,
            wire.dst_terminal,
            is_bkcal=wire.is_bkcal,
        )

    # Create OUTPORT blocks for outgoing wires
    outport_counter = {}
    outgoing_port_names: dict[str, str] = {}
    for wire in outgoing_wires:
        src_block = inner_graph.blocks.get(wire.src_block_id)
        if not src_block:
            continue
        port_name = f"{wire.src_terminal}"
        if port_name in outport_counter:
            outport_counter[port_name] += 1
            port_name = f"{port_name}_{outport_counter[port_name]}"
        else:
            outport_counter[port_name] = 0
        outgoing_port_names[wire.id] = port_name

        source = src_block.outputs[wire.src_terminal]

        outport = OutportBlock(instance_name=port_name)
        outport.config.params.update({
            "port_name": port_name,
            "is_bkcal": wire.is_bkcal,
            "data_type": source.data_type.value,
            "description": source.description,
        })
        outport._apply_config()
        outport.x = max_x - min_x + 200
        outport.y = 100 + len(inner_graph.blocks) * 80
        inner_graph.add_block(outport)

        # Wire original source inside → OUTPORT.IN
        inner_graph.add_wire(
            wire.src_block_id,
            wire.src_terminal,
            outport.id,
            "IN",
            is_bkcal=wire.is_bkcal,
        )

    # Set the inner graph on the composite (rebuilds terminals)
    composite.inner_graph = inner_graph

    # Remove moved blocks and their wires from parent graph
    for wire in internal_wires + incoming_wires + outgoing_wires:
        parent_graph.wires.pop(wire.id, None)
    for bid in selected:
        parent_graph.blocks.pop(bid, None)

    # Add composite to parent graph
    parent_graph.add_block(composite)

    # Re-wire incoming connections to composite input terminals
    for wire in incoming_wires:
        dst_block = composite._inner_graph.blocks.get(wire.dst_block_id)
        if not dst_block:
            continue
        # Find the INPORT that was created for this wire
        port_name = incoming_port_names.get(wire.id, "")
        if port_name in composite.inputs:
            parent_graph.add_wire(
                wire.src_block_id,
                wire.src_terminal,
                composite.id,
                port_name,
                is_bkcal=wire.is_bkcal,
                allow_type_mismatch=True,
            )

    # Re-wire outgoing connections from composite output terminals
    for wire in outgoing_wires:
        src_block = composite._inner_graph.blocks.get(wire.src_block_id)
        if not src_block:
            continue
        port_name = outgoing_port_names.get(wire.id, "")
        if port_name in composite.outputs:
            parent_graph.add_wire(
                composite.id,
                port_name,
                wire.dst_block_id,
                wire.dst_terminal,
                is_bkcal=wire.is_bkcal,
                allow_type_mismatch=True,
            )

    log.info("Collapsed %d blocks into composite '%s' with %d inputs, %d outputs",
             len(blocks_to_move), composite_name,
             len(composite.inputs), len(composite.outputs))

    return composite


def explode_composite(parent_graph, composite_id: str) -> list[str] | None:
    """Inverse of :func:`collapse_to_composite` — ungroup a composite back
    into the parent.

    Moves every non-INPORT/OUTPORT block from the composite's inner
    graph back into ``parent_graph``. Internal wires come with them.
    INPORT and OUTPORT blocks are dropped; the wires that connected
    *through* them are re-routed so the external segments now talk
    directly to the original interior terminals.

    Args:
        parent_graph: the StrategyGraph that owns the composite
        composite_id: the composite block's id

    Returns:
        List of block ids that were promoted into the parent (in case
        the UI wants to select them), or None if no composite found.
    """
    from ..model.wire import Wire

    composite = parent_graph.blocks.get(composite_id)
    if composite is None or not isinstance(composite, CompositeBlock):
        return None

    inner_graph = composite._inner_graph
    cx, cy = composite.x, composite.y

    # Bounding box of the interior so we can place blocks relative to where
    # the composite sat on the parent canvas.
    interior = [b for b in inner_graph.blocks.values()
                if b.block_type not in ("INPORT", "OUTPORT")]
    if interior:
        min_x = min(b.x for b in interior)
        min_y = min(b.y for b in interior)
        # Center the cloud of promoted blocks roughly where the composite was.
        max_x = max(b.x for b in interior)
        max_y = max(b.y for b in interior)
        ox = cx - (min_x + max_x) / 2
        oy = cy - (min_y + max_y) / 2
    else:
        ox = oy = 0.0

    # ──────────────────────────────────────────────────────────────
    # Build maps:
    #   inport_id  -> [(outer_src_block, outer_src_term, outer_wire), ...]
    #     i.e. parent wires that fed *into* this inport
    #   inport_id  -> [(inner_dst_block, inner_dst_term, inner_wire), ...]
    #     i.e. inner wires from INPORT.OUT -> something
    # Same for outports in the reverse direction.
    # ──────────────────────────────────────────────────────────────

    inport_consumers: dict[str, list[tuple[str, str, "Wire"]]] = {}
    outport_producers: dict[str, list[tuple[str, str, "Wire"]]] = {}

    for w in list(inner_graph.wires.values()):
        src = inner_graph.blocks.get(w.src_block_id)
        dst = inner_graph.blocks.get(w.dst_block_id)
        if src and src.block_type == "INPORT":
            inport_consumers.setdefault(src.id, []).append(
                (w.dst_block_id, w.dst_terminal, w))
        elif dst and dst.block_type == "OUTPORT":
            outport_producers.setdefault(dst.id, []).append(
                (w.src_block_id, w.src_terminal, w))

    # Outer wires going *into* this composite (one per parent input pin)
    outer_in: dict[str, list[Wire]] = {}    # pin_name -> [outer wire,...]
    outer_out: dict[str, list[Wire]] = {}   # pin_name -> [outer wire,...]
    for w in list(parent_graph.wires.values()):
        if w.dst_block_id == composite_id:
            outer_in.setdefault(w.dst_terminal, []).append(w)
        elif w.src_block_id == composite_id:
            outer_out.setdefault(w.src_terminal, []).append(w)

    promoted_ids: list[str] = []

    # ──────────────────────────────────────────────────────────────
    # Promote interior blocks (translate position) into parent_graph.
    # ──────────────────────────────────────────────────────────────
    for blk in interior:
        blk.x += ox
        blk.y += oy
        parent_graph.blocks[blk.id] = blk
        promoted_ids.append(blk.id)

    # Promote internal wires that don't touch an INPORT/OUTPORT.
    for w in list(inner_graph.wires.values()):
        src = inner_graph.blocks.get(w.src_block_id)
        dst = inner_graph.blocks.get(w.dst_block_id)
        if src and dst and src.block_type not in ("INPORT", "OUTPORT") \
                and dst.block_type not in ("INPORT", "OUTPORT"):
            parent_graph.wires[w.id] = w

    # ──────────────────────────────────────────────────────────────
    # Re-route outer wires through where the INPORT/OUTPORT was.
    # Each outer wire on pin P fans out to every inner consumer that
    # the INPORT P fed. Symmetric for outports.
    # ──────────────────────────────────────────────────────────────
    for inport_blk in [b for b in inner_graph.blocks.values()
                        if b.block_type == "INPORT"]:
        pin = inport_blk.config.params.get("port_name", inport_blk.instance_name)
        outer_wires = outer_in.get(pin, [])
        consumers = inport_consumers.get(inport_blk.id, [])
        for ow in outer_wires:
            parent_graph.wires.pop(ow.id, None)
            for inner_dst_id, inner_dst_term, _ in consumers:
                new_w = Wire(
                    src_block_id=ow.src_block_id,
                    src_terminal=ow.src_terminal,
                    dst_block_id=inner_dst_id,
                    dst_terminal=inner_dst_term,
                    is_bkcal=ow.is_bkcal,
                )
                parent_graph.wires[new_w.id] = new_w

    for outport_blk in [b for b in inner_graph.blocks.values()
                         if b.block_type == "OUTPORT"]:
        pin = outport_blk.config.params.get("port_name", outport_blk.instance_name)
        outer_wires = outer_out.get(pin, [])
        producers = outport_producers.get(outport_blk.id, [])
        for ow in outer_wires:
            parent_graph.wires.pop(ow.id, None)
            for inner_src_id, inner_src_term, _ in producers:
                new_w = Wire(
                    src_block_id=inner_src_id,
                    src_terminal=inner_src_term,
                    dst_block_id=ow.dst_block_id,
                    dst_terminal=ow.dst_terminal,
                    is_bkcal=ow.is_bkcal,
                )
                parent_graph.wires[new_w.id] = new_w

    # Drop the composite itself (and any wires still pointing at it).
    for w in list(parent_graph.wires.values()):
        if w.src_block_id == composite_id or w.dst_block_id == composite_id:
            parent_graph.wires.pop(w.id, None)
    parent_graph.blocks.pop(composite_id, None)

    log.info("Exploded composite '%s' — promoted %d blocks into parent",
             composite.instance_name, len(promoted_ids))
    return promoted_ids
