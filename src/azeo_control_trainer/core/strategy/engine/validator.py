"""Strategy validation — check a StrategyGraph for common errors before download.

Pure function: takes a graph, returns a list of ValidationResult objects.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph

_TAG_MONITOR_TYPES = frozenset({"TAGAI", "TAGAO", "TAGDI", "TAGDO"})


@dataclass
class ValidationResult:
    """Single validation finding."""

    severity: str  # "ERROR", "WARNING", "INFO"
    block_id: str  # block that has the issue (or "")
    message: str


def validate_strategy(graph: StrategyGraph) -> list[ValidationResult]:
    """Validate a strategy graph and return all findings.

    Checks performed:
        1. Empty strategy (ERROR)
        2. Required configuration — I/O without a tag, unassigned TAGIO, and
           PID without PV (ERROR)
        3. Duplicate AI/AO tags (ERROR)
        4. PID cascade without BKCAL wire (WARNING)
        5. Unconnected AO — no cascade input (WARNING)
        6. PID output limits invalid (WARNING)
        7. Orphan blocks — no wires at all (INFO). A one-block module and a
           tagged I/O block are not orphans; see the note at the check.
    """
    results: list[ValidationResult] = []

    # 1. Empty strategy
    if not graph.blocks:
        results.append(ValidationResult("ERROR", "", "Strategy has no blocks"))
        return results

    # Build lookup sets for wired terminals
    # dst_inputs: set of (block_id, terminal_name) that have an incoming wire
    dst_inputs: set[tuple[str, str]] = set()
    # src_outputs: set of (block_id, terminal_name) that have an outgoing wire
    src_outputs: set[tuple[str, str]] = set()
    # blocks that have any wire
    wired_blocks: set[str] = set()

    for w in graph.wires.values():
        dst_inputs.add((w.dst_block_id, w.dst_terminal))
        src_outputs.add((w.src_block_id, w.src_terminal))
        wired_blocks.add(w.src_block_id)
        wired_blocks.add(w.dst_block_id)

    # Collect AI and AO tags for duplicate detection
    ai_tags: dict[str, list[str]] = {}  # tag -> [instance_name, ...]
    ao_tags: dict[str, list[str]] = {}

    for block in graph.blocks.values():
        btype = block.block_type
        name = block.instance_name
        bid = block.id
        config = block.config.params

        # 2. Unwired required inputs
        if btype == "AI":
            tag = config.get("tag", "")
            if not tag:
                results.append(ValidationResult(
                    "ERROR", bid,
                    f"AI block '{name}' has no tag configured"))
            else:
                ai_tags.setdefault(tag, []).append(name)

        elif btype == "AO":
            tag = config.get("tag", "")
            if not tag:
                results.append(ValidationResult(
                    "ERROR", bid,
                    f"AO block '{name}' has no tag configured"))
            else:
                ao_tags.setdefault(tag, []).append(name)

            # 5. Unconnected AO — no cascade input
            if (bid, "CAS_IN") not in dst_inputs:
                results.append(ValidationResult(
                    "WARNING", bid,
                    f"AO '{name}' has no cascade input — will stay in manual"))

        elif btype in _TAG_MONITOR_TYPES:
            tag = str(config.get("tag", "") or "").strip()
            if not tag:
                results.append(ValidationResult(
                    "ERROR", bid,
                    f"{btype} block '{name}' has no PLC control tag configured"))

        elif btype == "TAGIO":
            results.append(ValidationResult(
                "ERROR", bid,
                f"TAGIO block '{name}' must be assigned to TAGAI, TAGAO, "
                "TAGDI, or TAGDO before download"))

        elif btype == "PID":
            # PID without PV input
            if (bid, "IN") not in dst_inputs:
                results.append(ValidationResult(
                    "ERROR", bid,
                    f"PID '{name}' has no process variable input"))

            # 6. PID output limits
            out_lo = config.get("out_lo", 0.0)
            out_hi = config.get("out_hi", 100.0)
            try:
                out_lo = float(out_lo)
                out_hi = float(out_hi)
                if out_lo >= out_hi:
                    results.append(ValidationResult(
                        "WARNING", bid,
                        f"PID '{name}' has invalid output limits "
                        f"(lo={out_lo} >= hi={out_hi})"))
            except (TypeError, ValueError):
                pass

    # 3. Duplicate tags
    for tag, names in ai_tags.items():
        if len(names) > 1:
            results.append(ValidationResult(
                "ERROR", "",
                f"Duplicate AI tag '{tag}' on blocks "
                f"'{names[0]}' and '{names[1]}'"))

    for tag, names in ao_tags.items():
        if len(names) > 1:
            results.append(ValidationResult(
                "ERROR", "",
                f"Duplicate AO tag '{tag}' on blocks "
                f"'{names[0]}' and '{names[1]}'"))

    # 4. PID cascade without BKCAL wire
    # Find wires where a PID OUT goes to another PID's CAS_IN
    for w in graph.wires.values():
        if w.dst_terminal != "CAS_IN":
            continue
        src_block = graph.blocks.get(w.src_block_id)
        dst_block = graph.blocks.get(w.dst_block_id)
        if not src_block or not dst_block:
            continue
        if src_block.block_type != "PID" or dst_block.block_type != "PID":
            continue
        # Check for BKCAL wire: slave BKCAL_OUT -> master BKCAL_IN
        has_bkcal = any(
            bw.src_block_id == w.dst_block_id
            and bw.src_terminal == "BKCAL_OUT"
            and bw.dst_block_id == w.src_block_id
            and bw.dst_terminal == "BKCAL_IN"
            for bw in graph.wires.values()
        )
        if not has_bkcal:
            results.append(ValidationResult(
                "WARNING", dst_block.id,
                f"PID '{dst_block.instance_name}' is cascade slave but has "
                f"no BKCAL wire back to '{src_block.instance_name}'"))

    # 7. Orphan blocks
    #
    # An orphan is a block stranded *among others* — it was placed and then
    # forgotten. Two cases are not orphans and reporting them is noise:
    #
    #   * a module that is one block. `LI-101` is a single `AI`: an
    #     indicator-only control module, which is a normal Azeo pattern.
    #     There is nothing for it to be wired to.
    #   * an I/O block carrying a field tag. It is doing its whole job —
    #     publishing to or reading from the field — with no wires at all.
    single_block_module = len(graph.blocks) == 1
    for block in graph.blocks.values():
        if block.id in wired_blocks or single_block_module:
            continue
        if block.block_type in ({"AI", "AO", "DI", "DO"}
                                | _TAG_MONITOR_TYPES) and \
                str(block.config.params.get("tag", "") or "").strip():
            continue
        results.append(ValidationResult(
            "INFO", block.id,
            f"Block '{block.instance_name}' has no connections"))

    return results
