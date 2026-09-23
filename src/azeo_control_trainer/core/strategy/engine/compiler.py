"""Compile a StrategyGraph into an execution order via topological sort.

Two classes of wire are treated as **feedback edges** — they propagate
at runtime but are excluded from the topological ordering so that
cycles they form do not block compilation:

1. **BKCAL (back-calculation) wires** — explicit one-scan-delay
   feedback in cascade / override loops.
2. **SFC control-flow wires** — every wire whose source *and*
   destination are both Sequential-Function-Chart blocks (STEP,
   TRANSITION, SFC_ACTION, INITIAL_STEP, END_STEP, PARALLEL_SPLIT,
   PARALLEL_JOIN, SELECTOR_BRANCH). State machines are intrinsically
   cyclic (transitions loop back to earlier steps); treating these
   edges as one-scan delay is exactly what real DCS compilers do.

Composite (user-defined) blocks hold their own sub-strategy. Compiling
the outer graph therefore also compiles every composite's interior,
recursively, down to :data:`MAX_COMPOSITE_DEPTH` nesting levels — a
composite whose interior is never compiled never executes, and drives
its outputs (and any valve downstream) to 0.
"""
from __future__ import annotations
from collections import deque
import logging

from ..model.block_base import BlockCategory
from ..model.strategy_graph import StrategyGraph
from ..model.wire import Wire

log = logging.getLogger("strategy.compiler")


# Block categories whose internal wires (src AND dst in the same set)
# are allowed to form cycles. SFC is intrinsically cyclic; future
# additions might include OPC-UA feedback or alarm-acknowledgement
# loops if they ever need similar treatment.
_CYCLE_BREAK_CATEGORIES = {BlockCategory.SFC}


# Azeo supports six composite levels. Level 1 is the interior of a
# composite sitting on the top-level strategy; the seventh is rejected as a
# compile error so a download can never succeed with an inert inner module.
MAX_COMPOSITE_DEPTH = 6


class CompileError(Exception):
    """Raised when strategy cannot be compiled."""
    pass


class CompiledStrategy:
    """Result of compiling a StrategyGraph — holds execution order and wire map."""

    def __init__(self, graph: StrategyGraph, exec_order: list[str],
                 forward_wires: list[Wire], bkcal_wires: list[Wire]):
        self.graph = graph
        self.exec_order = exec_order          # block IDs in execution sequence
        self.forward_wires = forward_wires    # normal data wires
        self.bkcal_wires = bkcal_wires        # back-calculation wires (one-scan delay)

    def __repr__(self):
        return (f"<CompiledStrategy blocks={len(self.exec_order)} "
                f"fwd_wires={len(self.forward_wires)} bkcal_wires={len(self.bkcal_wires)}>")


def compile_strategy(graph: StrategyGraph,
                     _composite_depth: int = 0) -> CompiledStrategy:
    """Compile strategy graph into execution order.

    Uses Kahn's algorithm for topological sort. BKCAL wires are excluded
    from the dependency graph (treated as feedback edges with one-scan delay).

    Every COMPOSITE block found in ``graph`` also gets its interior
    sub-strategy compiled (see :func:`_compile_composite_interiors`), so
    the runtime can scan it.

    ``_composite_depth`` is internal: 0 for a top-level strategy, N for a
    graph that is the interior of a composite nested N levels deep.

    Raises CompileError if a non-BKCAL cycle is detected.
    """
    errors = graph.validate()
    if errors:
        raise CompileError(f"Graph validation failed: {'; '.join(errors)}")

    # Separate forward and BKCAL wires
    forward_wires = []
    bkcal_wires = []
    for w in graph.wires.values():
        # A wire is BKCAL if either the wire itself is marked, or
        # the source terminal or destination terminal is marked as bkcal
        src_block = graph.blocks.get(w.src_block_id)
        dst_block = graph.blocks.get(w.dst_block_id)
        is_bkcal = w.is_bkcal
        if not is_bkcal and src_block and w.src_terminal in src_block.outputs:
            is_bkcal = src_block.outputs[w.src_terminal].is_bkcal
        if not is_bkcal and dst_block and w.dst_terminal in dst_block.inputs:
            is_bkcal = dst_block.inputs[w.dst_terminal].is_bkcal

        if is_bkcal:
            bkcal_wires.append(w)
        else:
            forward_wires.append(w)

    # Build adjacency list and in-degree count (forward wires only).
    # Wires whose source AND destination are both in a cycle-break
    # category (currently SFC) propagate at runtime but are excluded
    # from the topological ordering — same treatment as BKCAL.
    #
    # Adjacency uses insertion-ordered dicts (as ordered sets), NOT
    # `set`: Python randomises string hashing per process, so a set of
    # block ids iterates in a different order in every run, which would
    # make the emitted execution order — and therefore `_exec_order` and
    # the scan sequence of independent blocks — non-deterministic.
    in_degree: dict[str, int] = {bid: 0 for bid in graph.blocks}
    adjacency: dict[str, dict[str, None]] = {bid: {} for bid in graph.blocks}
    sfc_feedback_count = 0

    for w in forward_wires:
        if w.dst_block_id == w.src_block_id:
            continue   # skip self-loops
        src_blk = graph.blocks.get(w.src_block_id)
        dst_blk = graph.blocks.get(w.dst_block_id)
        if (src_blk and dst_blk
                and src_blk.category in _CYCLE_BREAK_CATEGORIES
                and dst_blk.category in _CYCLE_BREAK_CATEGORIES):
            sfc_feedback_count += 1
            continue
        adjacency[w.src_block_id][w.dst_block_id] = None

    # Compute in-degrees
    for src_id, dst_ids in adjacency.items():
        for dst_id in dst_ids:
            in_degree[dst_id] += 1

    # Kahn's algorithm
    queue = deque(bid for bid, deg in in_degree.items() if deg == 0)
    exec_order: list[str] = []

    while queue:
        bid = queue.popleft()
        exec_order.append(bid)
        for dst_id in adjacency[bid]:
            in_degree[dst_id] -= 1
            if in_degree[dst_id] == 0:
                queue.append(dst_id)

    if len(exec_order) != len(graph.blocks):
        # Find cycle participants
        remaining = set(graph.blocks.keys()) - set(exec_order)
        names = [graph.blocks[bid].instance_name for bid in remaining]
        raise CompileError(
            f"Cycle detected in strategy (not through BKCAL wires). "
            f"Blocks in cycle: {', '.join(names)}"
        )

    # Set execution order on blocks
    for i, bid in enumerate(exec_order):
        graph.blocks[bid]._exec_order = i

    # Warn about BKCAL wires that form cycles (potential one-scan-delay issues
    # in tightly-coupled cascade loops).
    if bkcal_wires:
        # Build set of block pairs connected by BKCAL
        bkcal_pairs = {(w.src_block_id, w.dst_block_id) for w in bkcal_wires}
        # Check if any forward path exists from dst → src (making a true cycle)
        for src_id, dst_id in bkcal_pairs:
            # Simple reachability check via forward adjacency
            visited = set()
            frontier = list(adjacency.get(dst_id, ()))
            while frontier:
                node = frontier.pop()
                if node == src_id:
                    src_name = graph.blocks[src_id].instance_name
                    dst_name = graph.blocks[dst_id].instance_name
                    log.warning(
                        "BKCAL cycle detected: %s → ... → %s → (BKCAL) → %s. "
                        "One-scan delay applies; verify cascade tuning is stable.",
                        dst_name, src_name, dst_name)
                    break
                if node not in visited:
                    visited.add(node)
                    frontier.extend(adjacency.get(node, ()))

    # Compile the interior of every composite in this graph so the
    # runtime can execute it (recurses into nested composites).
    n_composites = _compile_composite_interiors(graph, _composite_depth)

    log.info("Compiled strategy '%s': %d blocks, %d forward wires "
             "(%d SFC feedback edges), %d BKCAL wires, %d composites",
             graph.name, len(exec_order), len(forward_wires),
             sfc_feedback_count, len(bkcal_wires), n_composites)

    return CompiledStrategy(graph, exec_order, forward_wires, bkcal_wires)


def _compile_composite_interiors(graph: StrategyGraph, depth: int) -> int:
    """Compile the inner sub-strategy of every COMPOSITE block in ``graph``.

    ``depth`` is the nesting level of ``graph`` itself (0 = top-level
    strategy), so the interiors compiled here sit at ``depth + 1``.

    A composite whose interior fails to compile (bad wire, cycle) aborts the
    outer compile. Allowing the outer strategy to download while silently
    leaving a composite inert is an unsafe partial success. Nesting deeper
    than :data:`MAX_COMPOSITE_DEPTH` is rejected the same way.

    Returns the number of composites whose interior compiled.
    """
    from ..blocks.composite_blocks import CompositeBlock

    inner_depth = depth + 1
    compiled = 0

    for block in graph.blocks.values():
        if not isinstance(block, CompositeBlock):
            continue

        if inner_depth > MAX_COMPOSITE_DEPTH:
            block._inner_compiled = None
            raise CompileError(
                f"Composite '{block.instance_name}' is nested "
                f"{inner_depth} levels deep; maximum is "
                f"{MAX_COMPOSITE_DEPTH}"
            )

        try:
            block.compile_inner(depth=inner_depth)
        except CompileError as e:
            block._inner_compiled = None
            raise CompileError(
                f"Composite '{block.instance_name}' interior failed to "
                f"compile: {e}"
            ) from e
        except Exception as e:                      # pragma: no cover
            block._inner_compiled = None
            raise CompileError(
                f"Composite '{block.instance_name}' interior compile raised "
                f"{type(e).__name__}: {e}"
            ) from e
        else:
            compiled += 1

    return compiled
