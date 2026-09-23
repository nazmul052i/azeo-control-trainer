"""Strategy runtime — executes a compiled strategy at the scan rate.

The runtime:
1. Reads AI/DI values from SharedDataStore (via bridge)
2. Propagates forward wire values
3. Executes blocks in compiled order
4. Propagates BKCAL wire values (one-scan delay)
5. Writes AO/DO values back to SharedDataStore (via bridge)
"""
from __future__ import annotations
import logging
import math
import time

from .compiler import CompiledStrategy
from .bridge import DataBridge
from .runtime_context import RuntimeContext, build_context_from_plugin

log = logging.getLogger("strategy.runtime")


class StrategyRuntime:
    """Executes a compiled strategy on each scan."""

    def __init__(self):
        self._compiled: CompiledStrategy | None = None
        self._bridge: DataBridge | None = None
        self._online: bool = False
        self._scan_count: int = 0
        self._last_scan_ms: float = 0.0
        self._max_scan_ms: float = 0.0
        self._total_scan_ms: float = 0.0
        self._error_count: int = 0
        self._online_since: float = 0.0  # time.time() when went online
        self._context: RuntimeContext | None = None

        # Online FBD debugger. A paused module remains ONLINE: its compiled
        # graph, bridge and operator surfaces stay attached, but the normal
        # executive cannot advance it. The cursor splits one ordinary scan at
        # block boundaries; input/forward-wire preparation happens once, and
        # BKCAL/output publication happens only when that scan completes.
        self._debug_paused: bool = False
        self._debug_breakpoints: set[str] = set()
        self._debug_run_to: str | None = None
        self._debug_skip_break_once: str | None = None
        self._debug_scan_active: bool = False
        self._debug_cursor: int = 0
        self._debug_scan_dt: float = 0.0
        self._debug_scan_work_ms: float = 0.0
        self._debug_current_block_id: str | None = None
        self._debug_last_block_id: str | None = None
        self._debug_last_outcome: str = "idle"
        self._debug_pause_reason: str = "offline"

    @property
    def is_online(self) -> bool:
        return self._online

    @property
    def compiled(self) -> CompiledStrategy | None:
        return self._compiled

    @property
    def scan_count(self) -> int:
        return self._scan_count

    @property
    def last_scan_ms(self) -> float:
        return self._last_scan_ms

    @property
    def is_debug_paused(self) -> bool:
        """Whether online execution is stopped at a debugger boundary."""
        return self._debug_paused

    @property
    def debug_breakpoints(self) -> frozenset[str]:
        """Stable block IDs carrying persistent breakpoints."""
        return frozenset(self._debug_breakpoints)

    def has_debug_breakpoint(self, block_id: str) -> bool:
        """Allocation-free breakpoint query for block painters."""
        return block_id in self._debug_breakpoints

    def debug_marker_for(self, block_id: str) -> str | None:
        """Allocation-free ``current``/``next`` marker for block painters."""
        if block_id == self._debug_current_block_id:
            return "current"
        if self._debug_paused and block_id == self._debug_next_block_id():
            return "next"
        return None

    def set_context(self, context: RuntimeContext) -> None:
        """Install a :class:`RuntimeContext` that gets attached to every
        block before each ``execute(dt)`` call.

        Blocks that don't need it (PID, AI, AO, math) simply ignore
        ``self.runtime_context``. ACT scripts use it for ``get_tag`` /
        ``set_tag``.
        """
        self._context = context

    def set_context_from_plugin(self, store, plugin) -> None:
        """Convenience: build a context from a plugin's opt-in attributes."""
        self._context = build_context_from_plugin(store, plugin)

    def load(self, compiled: CompiledStrategy, bridge: DataBridge):
        """Load a compiled strategy and data bridge."""
        self._reset_debug_execution("reloaded")
        self._compiled = compiled
        self._bridge = bridge
        self._online = False
        self._scan_count = 0
        # A recompile commonly keeps block UUIDs. Retain those breakpoints,
        # but never let a deleted block leave an unreachable stop behind.
        self._debug_breakpoints.intersection_update(compiled.graph.blocks)
        log.info("Strategy loaded: %s", compiled)

    def go_online(self):
        """Put the strategy online — will execute on next scan."""
        if self._compiled is None:
            log.error("Cannot go online: no compiled strategy loaded")
            return False

        if getattr(self._compiled.graph, "_configuration_draft", False):
            log.warning("Repository working drafts require a validated release before download")
            return False

        # Safety net: a composite whose interior was never compiled would
        # silently execute nothing and hold its outputs (and any valve it
        # drives) at 0. compile_strategy() normally does this; catch the
        # case where the interior was edited after the outer compile.
        self._ensure_composites_compiled(self._compiled.graph)

        # Reset block terminal state to prevent stale values from prior run
        # (CompositeBlock.reset() recurses into its interior).
        for block in self._compiled.graph.blocks.values():
            block.reset()

        # Initialize AO/PID outputs from current store values (bumpless start)
        if self._bridge is not None:
            self._bridge.initialize_outputs(self._compiled.graph)

        self._online = True
        self._scan_count = 0
        self._error_count = 0
        self._max_scan_ms = 0.0
        self._total_scan_ms = 0.0
        self._online_since = time.time()
        self._reset_debug_execution("running")
        log.info("Strategy ONLINE: %s", self._compiled.graph.name)
        return True

    @staticmethod
    def _ensure_composites_compiled(graph, depth: int = 0) -> None:
        """Compile any composite interior that has no compiled sub-strategy.

        Normally :func:`compile_strategy` has already done this; this only
        catches a graph whose composite interior changed after compilation.
        A failed interior is a failed download: the exception deliberately
        propagates so the strategy cannot go online with an inert composite.
        Nesting is bounded by ``MAX_COMPOSITE_DEPTH``.
        """
        from ..blocks.composite_blocks import CompositeBlock
        from .compiler import CompileError, MAX_COMPOSITE_DEPTH

        inner_depth = depth + 1
        for block in graph.blocks.values():
            if not isinstance(block, CompositeBlock):
                continue
            if inner_depth > MAX_COMPOSITE_DEPTH:
                raise CompileError(
                    f"Composite '{block.instance_name}' is nested "
                    f"{inner_depth} levels deep; maximum is "
                    f"{MAX_COMPOSITE_DEPTH}"
                )
            if block._inner_compiled is None and block.inner_graph.blocks:
                block.compile_inner(depth=inner_depth)
                log.warning("Composite '%s' interior was not compiled with "
                            "the outer strategy — compiled it now.",
                            block.instance_name)
            else:
                StrategyRuntime._ensure_composites_compiled(
                    block.inner_graph, inner_depth)

    def go_offline(self):
        """Take the strategy offline — stops execution."""
        self._online = False
        self._reset_debug_execution("offline")
        log.info("Strategy OFFLINE")

    # ------------------------------------------------------------ debugger
    def _resolve_debug_block(self, block_ref: str) -> str:
        """Resolve a block UUID or instance name against the loaded graph."""
        if self._compiled is None:
            raise RuntimeError("No compiled strategy is loaded")
        ref = str(block_ref)
        if ref in self._compiled.graph.blocks:
            return ref
        matches = [
            block_id for block_id, block in self._compiled.graph.blocks.items()
            if block.instance_name == ref
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(f"Block name is ambiguous: {ref}")
        raise KeyError(f"Unknown block: {ref}")

    def set_debug_breakpoint(self, block_ref: str,
                             enabled: bool = True) -> str:
        """Set or clear a persistent breakpoint and return its block ID."""
        block_id = self._resolve_debug_block(block_ref)
        if enabled:
            self._debug_breakpoints.add(block_id)
        else:
            self._debug_breakpoints.discard(block_id)
        return block_id

    # Short spellings are convenient for context-menu and automation callers.
    set_breakpoint = set_debug_breakpoint

    def toggle_debug_breakpoint(self, block_ref: str) -> bool:
        """Toggle one breakpoint; return True when it is now enabled."""
        block_id = self._resolve_debug_block(block_ref)
        enabled = block_id not in self._debug_breakpoints
        self.set_debug_breakpoint(block_id, enabled)
        return enabled

    toggle_breakpoint = toggle_debug_breakpoint

    def clear_debug_breakpoints(self) -> None:
        self._debug_breakpoints.clear()

    clear_breakpoints = clear_debug_breakpoints

    def debug_pause(self) -> bool:
        """Pause at a safe block boundary while remaining online."""
        if not self._online or self._compiled is None:
            return False
        self._debug_paused = True
        self._debug_pause_reason = "manual"
        return True

    def debug_resume(self) -> bool:
        """Resume normal scans from the current debugger cursor."""
        if not self._online or self._compiled is None:
            return False
        if (self._debug_paused
                and self._debug_pause_reason in {"breakpoint", "run_to"}):
            # Continue THROUGH the boundary we stopped before. Without this
            # one-shot suppression Resume rediscovers the same persistent
            # breakpoint forever and the module cannot advance.
            self._debug_skip_break_once = self._debug_next_block_id()
        self._debug_paused = False
        self._debug_pause_reason = "running"
        return True

    def debug_abort(self) -> bool:
        """Discard a partial debug scan and remain paused at scan start."""
        if not self._online or self._compiled is None:
            return False
        self._reset_debug_execution("aborted", paused=True)
        return True

    def debug_run_to_block(self, block_ref: str) -> str:
        """Resume and pause immediately before ``block_ref`` executes."""
        if not self._online:
            raise RuntimeError("The strategy must be online to run to a block")
        block_id = self._resolve_debug_block(block_ref)
        self._debug_run_to = block_id
        self._debug_skip_break_once = None
        self._debug_paused = False
        self._debug_pause_reason = "running_to_block"
        return block_id

    run_to_block = debug_run_to_block

    def _default_debug_dt(self) -> float:
        graph = self._compiled.graph if self._compiled else None
        return max(0.001, float(getattr(graph, "scan_ms", 500)) / 1000.0)

    def debug_step_block(self, dt: float | None = None) -> str | None:
        """Execute at most one due, non-bypassed compiled block.

        Input sampling and forward-wire propagation begin a scan when needed.
        Bypassed and block-rate-skipped slots are advanced honestly, but the
        method never calls ``execute`` on more than one block. It remains
        paused afterwards and returns the executed/attempted block ID, or
        ``None`` when the scan contained no executable block.
        """
        if not self._online or self._compiled is None or self._bridge is None:
            return None
        self._debug_paused = True
        self._debug_pause_reason = "step"
        try:
            if not self._debug_scan_active:
                use_dt = self._default_debug_dt() if dt is None else dt
                self._begin_scan(use_dt)
            while self._debug_scan_active:
                block_id, outcome, attempted = self._advance_debug_slot()
                if not self._debug_scan_active:
                    self._debug_pause_reason = "scan_complete"
                if attempted:
                    return block_id
                # Step Block means one algorithm invocation, not merely one
                # cursor increment through a bypassed/rate-skipped slot.
                self._debug_last_outcome = outcome
            return None
        except Exception:
            self._reset_debug_execution("error", paused=True)
            raise

    step_block = debug_step_block

    def debug_run_scan(self, dt: float | None = None) -> int:
        """Complete the remaining partial scan, or one new full scan.

        Explicit Run Scan ignores persistent breakpoints and a pending run-to
        target. It performs exactly one scan and stays paused, which makes the
        command deterministic for commissioning work. Returns the number of
        block algorithms invoked.
        """
        if not self._online or self._compiled is None or self._bridge is None:
            return 0
        self._debug_paused = True
        self._debug_run_to = None
        self._debug_skip_break_once = None
        attempted = 0
        try:
            if not self._debug_scan_active:
                use_dt = self._default_debug_dt() if dt is None else dt
                self._begin_scan(use_dt)
            while self._debug_scan_active:
                _block_id, _outcome, did_attempt = self._advance_debug_slot()
                attempted += int(did_attempt)
            self._debug_pause_reason = "scan_complete"
            return attempted
        except Exception:
            self._reset_debug_execution("error", paused=True)
            raise

    run_scan = debug_run_scan

    def _debug_next_block_id(self) -> str | None:
        if self._compiled is None or not self._compiled.exec_order:
            return None
        if self._debug_scan_active:
            if self._debug_cursor >= len(self._compiled.exec_order):
                return None
            return self._compiled.exec_order[self._debug_cursor]
        return self._compiled.exec_order[0]

    def get_debug_status(self) -> dict:
        """Return an immutable-by-value debugger snapshot for UI polling."""
        graph = self._compiled.graph if self._compiled else None

        def describe(block_id: str | None) -> dict | None:
            block = graph.blocks.get(block_id) if graph and block_id else None
            if block is None:
                return None
            try:
                order = self._compiled.exec_order.index(block_id) + 1
            except ValueError:
                order = None
            return {
                "id": block_id,
                "name": block.instance_name,
                "type": block.block_type,
                "order": order,
            }

        next_id = self._debug_next_block_id()
        return {
            "online": self._online,
            "paused": self._debug_paused,
            "scan_active": self._debug_scan_active,
            "pause_reason": self._debug_pause_reason,
            "current_block": describe(self._debug_current_block_id),
            "last_block": describe(self._debug_last_block_id),
            "next_block": describe(next_id),
            "last_outcome": self._debug_last_outcome,
            "cursor": self._debug_cursor,
            "block_count": len(self._compiled.exec_order)
            if self._compiled else 0,
            "breakpoints": tuple(sorted(self._debug_breakpoints)),
            "run_to_block": describe(self._debug_run_to),
            "scan_count": self._scan_count,
        }

    debug_status = get_debug_status

    def _reset_debug_execution(self, reason: str,
                               *, paused: bool = False) -> None:
        """Abort transient debugger state without erasing breakpoints."""
        self._debug_paused = paused
        self._debug_run_to = None
        self._debug_skip_break_once = None
        self._debug_scan_active = False
        self._debug_cursor = 0
        self._debug_scan_dt = 0.0
        self._debug_scan_work_ms = 0.0
        self._debug_current_block_id = None
        self._debug_last_block_id = None
        self._debug_last_outcome = reason
        self._debug_pause_reason = reason

    def _should_debug_break(self, block_id: str) -> str | None:
        if self._debug_skip_break_once == block_id:
            self._debug_skip_break_once = None
            return None
        if self._debug_run_to == block_id:
            self._debug_run_to = None
            return "run_to"
        if block_id in self._debug_breakpoints:
            return "breakpoint"
        return None

    def _begin_scan(self, dt: float) -> None:
        """Run the input and forward-wire phases of one debug scan."""
        if self._compiled is None or self._bridge is None:
            raise RuntimeError("No compiled online strategy is loaded")
        self._validate_dt(dt)
        t0 = time.perf_counter()
        graph = self._compiled.graph
        self._bridge.read_inputs(graph)
        for block in graph.blocks.values():
            block.tick_force_timers(dt)
            block.apply_forces()
        for wire in self._compiled.forward_wires:
            src_block = graph.blocks.get(wire.src_block_id)
            dst_block = graph.blocks.get(wire.dst_block_id)
            if not src_block or not dst_block:
                continue
            src_term = src_block.outputs.get(wire.src_terminal)
            dst_term = dst_block.inputs.get(wire.dst_terminal)
            if src_term and dst_term:
                dst_term.value = src_term.value
                dst_term.status = src_term.status
                dst_term.limit = src_term.limit
            elif self._scan_count == 0:
                if not src_term:
                    log.warning(
                        "Wire %s: source terminal '%s' not found on block '%s'",
                        wire.id, wire.src_terminal, src_block.instance_name)
                if not dst_term:
                    log.warning(
                        "Wire %s: dest terminal '%s' not found on block '%s'",
                        wire.id, wire.dst_terminal, dst_block.instance_name)
        self._debug_scan_active = True
        self._debug_cursor = 0
        self._debug_scan_dt = float(dt)
        self._debug_scan_work_ms = (time.perf_counter() - t0) * 1000.0
        self._debug_current_block_id = None
        self._debug_last_outcome = "scan_started"
        if not self._compiled.exec_order:
            self._finish_debug_scan()

    def _advance_debug_slot(self) -> tuple[str | None, str, bool]:
        """Advance one execution-order slot; finish the scan at its end."""
        if not self._debug_scan_active or self._compiled is None:
            return None, "idle", False
        if self._debug_cursor >= len(self._compiled.exec_order):
            self._finish_debug_scan()
            return None, "scan_complete", False

        slot_t0 = time.perf_counter()
        graph = self._compiled.graph
        block_id = self._compiled.exec_order[self._debug_cursor]
        self._debug_cursor += 1
        self._debug_current_block_id = block_id
        self._debug_last_block_id = block_id
        block = graph.blocks.get(block_id)
        attempted = False
        outcome = "missing"

        if block is not None:
            if block.bypassed:
                outcome = "bypassed"
            elif not block.due_this_scan():
                outcome = "not_due"
            else:
                attempted = True
                outcome = "executed"
                block.runtime_context = self._context
                block._module_graph = graph
                block.apply_forces()
                block_t0 = time.perf_counter()
                try:
                    block.execute(self._debug_scan_dt)
                except Exception as exc:
                    log.error("Block %s execute error: %s",
                              block.instance_name, exc)
                    from ..model.block_base import BlockStatus
                    block.status = BlockStatus.BAD
                    from ..model.terminal import Quality
                    for terminal in block.outputs.values():
                        terminal.status = Quality.BAD
                    self._error_count += 1
                    outcome = "error"
                else:
                    dur_us = (time.perf_counter() - block_t0) * 1e6
                    # Parameter Special Items are scan-boundary connectors,
                    # not algorithms. Counting them made Diagnostics overstate
                    # the module's executable control burden.
                    if getattr(block, "counts_as_algorithm", True):
                        block._last_exec_us = dur_us
                        block._exec_total_us += dur_us
                        block._exec_count += 1
                        if dur_us > block._max_exec_us:
                            block._max_exec_us = dur_us
                block.apply_forces()

        self._debug_last_outcome = outcome
        self._debug_scan_work_ms += (time.perf_counter() - slot_t0) * 1000.0
        if self._debug_cursor >= len(self._compiled.exec_order):
            self._finish_debug_scan()
        return block_id, outcome, attempted

    def _finish_debug_scan(self) -> None:
        """Run BKCAL/output phases and account one completed debug scan."""
        if not self._debug_scan_active or self._compiled is None \
                or self._bridge is None:
            return
        t0 = time.perf_counter()
        graph = self._compiled.graph
        for wire in self._compiled.bkcal_wires:
            src_block = graph.blocks.get(wire.src_block_id)
            dst_block = graph.blocks.get(wire.dst_block_id)
            if not src_block or not dst_block:
                continue
            src_term = src_block.outputs.get(wire.src_terminal)
            dst_term = dst_block.inputs.get(wire.dst_terminal)
            if src_term and dst_term:
                dst_term.value = src_term.value
                dst_term.status = src_term.status
                dst_term.limit = src_term.limit
            elif self._scan_count == 0:
                if not src_term:
                    log.warning(
                        "BKCAL wire %s: source terminal '%s' not found on block '%s'",
                        wire.id, wire.src_terminal, src_block.instance_name)
                if not dst_term:
                    log.warning(
                        "BKCAL wire %s: dest terminal '%s' not found on block '%s'",
                        wire.id, wire.dst_terminal, dst_block.instance_name)
        self._bridge.write_outputs(graph)
        self._debug_scan_work_ms += (time.perf_counter() - t0) * 1000.0
        elapsed = self._debug_scan_work_ms
        self._scan_count += 1
        self._last_scan_ms = elapsed
        self._total_scan_ms += elapsed
        if elapsed > self._max_scan_ms:
            self._max_scan_ms = elapsed
        self._debug_scan_active = False
        self._debug_cursor = 0
        self._debug_scan_dt = 0.0
        self._debug_scan_work_ms = 0.0

    def execute_scan(self, dt: float) -> bool:
        """Execute or continue one scan; return True only when it completes."""
        if not self._online or self._compiled is None or self._bridge is None:
            return False
        if self._debug_paused:
            return False
        self._validate_dt(dt)
        # Keep the production hot path byte-for-byte in the legacy body when
        # no debugger feature is armed. Debugging pays the cursor cost;
        # ordinary control does not.
        if (not self._debug_scan_active and not self._debug_breakpoints
                and self._debug_run_to is None):
            self._execute_scan_legacy(dt)
            return True
        try:
            if not self._debug_scan_active:
                self._begin_scan(dt)
            while self._debug_scan_active:
                next_id = self._debug_next_block_id()
                if next_id is None:
                    self._finish_debug_scan()
                    break
                reason = self._should_debug_break(next_id)
                if reason is not None:
                    self._debug_paused = True
                    self._debug_pause_reason = reason
                    self._debug_last_outcome = reason
                    return False
                self._advance_debug_slot()
            return True
        except Exception:
            self._reset_debug_execution("error")
            raise

    @staticmethod
    def _validate_dt(dt):
        if not math.isfinite(dt) or dt <= 0:
            raise ValueError("Scan interval must be finite and positive")

    def _execute_scan_legacy(self, dt: float):
        """Execute one scan of the loaded strategy.

        Called by SimEngine at the control scan rate when strategy is online.
        """
        if not self._online or self._compiled is None or self._bridge is None:
            return

        t0 = time.perf_counter()
        graph = self._compiled.graph

        # 1. Read inputs from data store (AI/DI blocks)
        self._bridge.read_inputs(graph)

        # 1a. Apply forces to every block — terminals locked by an
        # operator override should win over store-driven AI inputs.
        for block in graph.blocks.values():
            block.tick_force_timers(dt)
            block.apply_forces()

        # 2. Propagate forward wire values (source → destination)
        for wire in self._compiled.forward_wires:
            src_block = graph.blocks.get(wire.src_block_id)
            dst_block = graph.blocks.get(wire.dst_block_id)
            if src_block and dst_block:
                src_term = src_block.outputs.get(wire.src_terminal)
                dst_term = dst_block.inputs.get(wire.dst_terminal)
                if src_term and dst_term:
                    # Quality and limit travel with the value — a block that
                    # never sets them stays Good / Not limited, so this is a
                    # no-op for anything that does not use status.
                    dst_term.value = src_term.value
                    dst_term.status = src_term.status
                    dst_term.limit = src_term.limit
                elif self._scan_count == 0:
                    # Log on first scan only to avoid log spam
                    if not src_term:
                        log.warning("Wire %s: source terminal '%s' not found on block '%s'",
                                    wire.id, wire.src_terminal, src_block.instance_name)
                    if not dst_term:
                        log.warning("Wire %s: dest terminal '%s' not found on block '%s'",
                                    wire.id, wire.dst_terminal, dst_block.instance_name)

        # 3. Execute blocks in compiled order
        for block_id in self._compiled.exec_order:
            block = graph.blocks.get(block_id)
            if block:
                # Skip bypassed blocks
                if block.bypassed:
                    continue
                # Azeo Block Scan Rate: a rate of N executes the block on
                # every Nth module scan. Skipped scans leave the outputs
                # holding their last computed value, which is what lets a
                # cascade's outer loop run slower than its inner loop in one
                # module. Rate 1 (the default) is always due.
                if not block.due_this_scan():
                    continue
                # Attach the runtime context so blocks (e.g. ACT) can read
                # or queue writes against the SharedDataStore. Blocks that
                # don't care ignore this attribute.
                block.runtime_context = self._context
                # The module graph, for expression blocks that reference
                # other blocks' parameters directly (param('PID1/OUT') in
                # CND/ACT) — Azeo's model, where the expression is the
                # wiring. Same non-copy graph the canvas holds.
                block._module_graph = graph
                # Pre-execute: input forces win over wire-propagated values
                block.apply_forces()
                _t_blk = time.perf_counter()
                try:
                    block.execute(dt)
                except Exception as e:
                    log.error("Block %s execute error: %s", block.instance_name, e)
                    from ..model.block_base import BlockStatus
                    block.status = BlockStatus.BAD
                    from ..model.terminal import Quality
                    for terminal in block.outputs.values():
                        terminal.status = Quality.BAD
                    self._error_count += 1
                else:
                    # Per-block scan-time stats (microseconds) — read by
                    # the DiagnosticsDialog. Cheap: ~50ns of perf_counter
                    # overhead per block. Skipped when execute raises.
                    dur_us = (time.perf_counter() - _t_blk) * 1e6
                    if getattr(block, "counts_as_algorithm", True):
                        block._last_exec_us = dur_us
                        block._exec_total_us += dur_us
                        block._exec_count += 1
                        if dur_us > block._max_exec_us:
                            block._max_exec_us = dur_us
                # Re-applying is harmless and keeps input forces pinned for
                # diagnostics; outputs are never forceable.
                block.apply_forces()

        # 4. Propagate BKCAL wire values (one-scan delay — uses current outputs)
        for wire in self._compiled.bkcal_wires:
            src_block = graph.blocks.get(wire.src_block_id)
            dst_block = graph.blocks.get(wire.dst_block_id)
            if src_block and dst_block:
                src_term = src_block.outputs.get(wire.src_terminal)
                dst_term = dst_block.inputs.get(wire.dst_terminal)
                if src_term and dst_term:
                    # The BKCAL path is where limit status matters most: a
                    # limited downstream block tells the upstream controller to
                    # stop winding reset into the limit.
                    dst_term.value = src_term.value
                    dst_term.status = src_term.status
                    dst_term.limit = src_term.limit
                elif self._scan_count == 0:
                    if not src_term:
                        log.warning("BKCAL wire %s: source terminal '%s' not found on block '%s'",
                                    wire.id, wire.src_terminal, src_block.instance_name)
                    if not dst_term:
                        log.warning("BKCAL wire %s: dest terminal '%s' not found on block '%s'",
                                    wire.id, wire.dst_terminal, dst_block.instance_name)

        # 5. Write outputs to data store (AO/DO blocks)
        self._bridge.write_outputs(graph)

        self._scan_count += 1
        elapsed = (time.perf_counter() - t0) * 1000.0
        self._last_scan_ms = elapsed
        self._total_scan_ms += elapsed
        if elapsed > self._max_scan_ms:
            self._max_scan_ms = elapsed

    def get_status(self) -> dict:
        """Return runtime status for UI display."""
        avg_ms = (self._total_scan_ms / self._scan_count
                  if self._scan_count > 0 else 0.0)
        online_secs = (time.time() - self._online_since
                       if self._online else 0.0)
        status = {
            "online": self._online,
            "scan_count": self._scan_count,
            "last_scan_ms": self._last_scan_ms,
            "max_scan_ms": self._max_scan_ms,
            "avg_scan_ms": avg_ms,
            "error_count": self._error_count,
            "online_duration": online_secs,
            "strategy_name": self._compiled.graph.name if self._compiled else "",
            "block_count": len(self._compiled.exec_order) if self._compiled else 0,
        }
        status["debug"] = self.get_debug_status()
        return status
