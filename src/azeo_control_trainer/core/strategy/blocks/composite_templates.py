"""Pre-built composite block templates for common control patterns.

These factory functions create ready-to-use CompositeBlock instances
with internal INPORT/OUTPORT blocks, PID, SCALER, AI, AO, and wiring.

Templates:
- single_pid_loop: AI -> PID -> SCALER -> AO with BKCAL chain
- cascade_pid_loop: AI_outer -> PID_outer -> AI_inner -> PID_inner -> SCALER -> AO with BKCAL
"""
from __future__ import annotations

import logging
import uuid

from ..model.strategy_graph import StrategyGraph
from ..model.wire import Wire
from ..model.block_registry import registry

log = logging.getLogger("strategy.composite_templates")


def _make_id() -> str:
    return uuid.uuid4().hex[:12]


def _add_wire(graph: StrategyGraph, src_id: str, src_term: str,
              dst_id: str, dst_term: str, is_bkcal: bool = False):
    """Helper to add a wire to a graph."""
    wire = Wire(src_id, src_term, dst_id, dst_term, is_bkcal=is_bkcal, wire_id=_make_id())
    graph.wires[wire.id] = wire
    src_block = graph.blocks.get(src_id)
    dst_block = graph.blocks.get(dst_id)
    if src_block and src_term in src_block.outputs:
        src_block.outputs[src_term].connected = True
    if dst_block and dst_term in dst_block.inputs:
        dst_block.inputs[dst_term].connected = True


def single_pid_loop(
    instance_name: str = "PID_Loop",
    pv_tag: str = "",
    mv_tag: str = "",
    pid_name: str = "PID",
    kp: float = 1.0,
    ti: float = 60.0,
    td: float = 0.0,
    action: str = "reverse",
    pv_lo: float = 0.0,
    pv_hi: float = 100.0,
    sp_init: float = 50.0,
    mv_lo: float = 0.0,
    mv_hi: float = 100.0,
    pv_unit: str = "",
    pv_ftime: float = 2.0,
    structure: str = "pi",
):
    """Create a single PID loop composite: AI -> PID -> SCALER -> AO + BKCAL.

    Returns a CompositeBlock with no exposed external terminals
    (self-contained loop — AI reads from store, AO writes to store).
    """
    from .composite_blocks import CompositeBlock

    composite = CompositeBlock(instance_name=instance_name)
    graph = StrategyGraph(instance_name)

    # AI block
    ai = registry.create("AI", f"AI_{pid_name}")
    ai.id = _make_id()
    ai.config.params = {
        "tag": pv_tag,
        "scale_lo": pv_lo,
        "scale_hi": pv_hi,
        "eng_units": pv_unit,
    }
    ai.x, ai.y = 100, 200
    graph.add_block(ai)

    # PID block
    pid = registry.create("PID", pid_name)
    pid.id = _make_id()
    pid.config.params = {
        "Kp": kp, "Ti": ti, "Td": td,
        "alpha": 0.1, "beta": 1.0, "gamma": 0.0,
        "action": action,
        "form": "standard",
        "structure": structure,
        "pv_scale_lo": pv_lo, "pv_scale_hi": pv_hi,
        "sp_lo": pv_lo, "sp_hi": pv_hi,
        "sp_init": sp_init,
        "out_lo": 0.0, "out_hi": 1.0,
        "arw_lo": -0.05, "arw_hi": 1.05,
        "pv_ftime": pv_ftime,
        "sp_ftime": 0.0,
        "mode": "auto",
    }
    pid.x, pid.y = 300, 200
    graph.add_block(pid)

    # SCALER (PID 0-1 -> AO 0-100%)
    scaler = registry.create("SCALER", f"SCL_{pid_name}")
    scaler.id = _make_id()
    scaler.config.params = {
        "in_lo": 0.0, "in_hi": 1.0,
        "out_lo": mv_lo, "out_hi": mv_hi,
    }
    scaler.x, scaler.y = 500, 200
    graph.add_block(scaler)

    # AO block
    ao = registry.create("AO", f"AO_{pid_name}")
    ao.id = _make_id()
    ao.config.params = {
        "tag": mv_tag,
        "out_lo": mv_lo, "out_hi": mv_hi,
    }
    ao.x, ao.y = 700, 200
    graph.add_block(ao)

    # Forward wires
    _add_wire(graph, ai.id, "OUT", pid.id, "IN")
    _add_wire(graph, pid.id, "OUT", scaler.id, "IN")
    _add_wire(graph, scaler.id, "OUT", ao.id, "CAS_IN")

    # BKCAL wires
    _add_wire(graph, ao.id, "BKCAL_OUT", scaler.id, "BKCAL_IN", is_bkcal=True)
    _add_wire(graph, scaler.id, "BKCAL_OUT", pid.id, "BKCAL_IN", is_bkcal=True)

    composite.inner_graph = graph
    return composite


def cascade_pid_loop(
    instance_name: str = "Cascade_Loop",
    outer_pv_tag: str = "",
    inner_pv_tag: str = "",
    mv_tag: str = "",
    outer_pid_name: str = "PID_Outer",
    inner_pid_name: str = "PID_Inner",
    outer_kp: float = 1.0,
    outer_ti: float = 120.0,
    outer_td: float = 0.0,
    inner_kp: float = 2.0,
    inner_ti: float = 30.0,
    inner_td: float = 0.0,
    outer_action: str = "reverse",
    inner_action: str = "direct",
    outer_pv_lo: float = 0.0,
    outer_pv_hi: float = 100.0,
    outer_sp_init: float = 50.0,
    inner_pv_lo: float = 0.0,
    inner_pv_hi: float = 100.0,
    inner_sp_init: float = 50.0,
    mv_lo: float = 0.0,
    mv_hi: float = 100.0,
    outer_pv_unit: str = "",
    inner_pv_unit: str = "",
    outer_pv_ftime: float = 10.0,
    inner_pv_ftime: float = 2.0,
    outer_structure: str = "pi",
    inner_structure: str = "pi",
):
    """Create a cascade PID loop composite.

    AI_outer -> PID_outer -> AI_inner -> PID_inner(CAS) -> SCALER -> AO + BKCAL.

    Self-contained — both AIs read from store, AO writes to store.
    Full BKCAL chain: AO -> SCL -> PID_inner -> PID_outer.
    """
    from .composite_blocks import CompositeBlock

    composite = CompositeBlock(instance_name=instance_name)
    graph = StrategyGraph(instance_name)

    # AI blocks
    ai_outer = registry.create("AI", f"AI_{outer_pid_name}")
    ai_outer.id = _make_id()
    ai_outer.config.params = {
        "tag": outer_pv_tag,
        "scale_lo": outer_pv_lo, "scale_hi": outer_pv_hi,
        "eng_units": outer_pv_unit,
    }
    ai_outer.x, ai_outer.y = 100, 150
    graph.add_block(ai_outer)

    ai_inner = registry.create("AI", f"AI_{inner_pid_name}")
    ai_inner.id = _make_id()
    ai_inner.config.params = {
        "tag": inner_pv_tag,
        "scale_lo": inner_pv_lo, "scale_hi": inner_pv_hi,
        "eng_units": inner_pv_unit,
    }
    ai_inner.x, ai_inner.y = 100, 350
    graph.add_block(ai_inner)

    # PID blocks
    pid_outer = registry.create("PID", outer_pid_name)
    pid_outer.id = _make_id()
    pid_outer.config.params = {
        "Kp": outer_kp, "Ti": outer_ti, "Td": outer_td,
        "alpha": 0.1, "beta": 1.0, "gamma": 0.0,
        "action": outer_action,
        "form": "standard",
        "structure": outer_structure,
        "pv_scale_lo": outer_pv_lo, "pv_scale_hi": outer_pv_hi,
        "sp_lo": outer_pv_lo, "sp_hi": outer_pv_hi,
        "sp_init": outer_sp_init,
        "out_lo": 0.0, "out_hi": 1.0,
        "arw_lo": -0.05, "arw_hi": 1.05,
        "pv_ftime": outer_pv_ftime,
        "sp_ftime": 0.0,
        "mode": "auto",
    }
    pid_outer.x, pid_outer.y = 300, 150
    graph.add_block(pid_outer)

    pid_inner = registry.create("PID", inner_pid_name)
    pid_inner.id = _make_id()
    pid_inner.config.params = {
        "Kp": inner_kp, "Ti": inner_ti, "Td": inner_td,
        "alpha": 0.1, "beta": 1.0, "gamma": 0.0,
        "action": inner_action,
        "form": "standard",
        "structure": inner_structure,
        "pv_scale_lo": inner_pv_lo, "pv_scale_hi": inner_pv_hi,
        "sp_lo": inner_pv_lo, "sp_hi": inner_pv_hi,
        "sp_init": inner_sp_init,
        "out_lo": 0.0, "out_hi": 1.0,
        "arw_lo": -0.05, "arw_hi": 1.05,
        "pv_ftime": inner_pv_ftime,
        "sp_ftime": 0.0,
        "mode": "cas",
    }
    pid_inner.x, pid_inner.y = 500, 350
    graph.add_block(pid_inner)

    # Cascade SCALER (outer PID 0-1 -> inner PID SP range)
    cas_scaler = registry.create("SCALER", f"SCL_CAS_{outer_pid_name}")
    cas_scaler.id = _make_id()
    cas_scaler.config.params = {
        "in_lo": 0.0, "in_hi": 1.0,
        "out_lo": inner_pv_lo, "out_hi": inner_pv_hi,
    }
    cas_scaler.x, cas_scaler.y = 400, 150
    graph.add_block(cas_scaler)

    # SCALER + AO (inner PID 0-1 -> valve 0-100%)
    scaler = registry.create("SCALER", f"SCL_{inner_pid_name}")
    scaler.id = _make_id()
    scaler.config.params = {
        "in_lo": 0.0, "in_hi": 1.0,
        "out_lo": mv_lo, "out_hi": mv_hi,
    }
    scaler.x, scaler.y = 700, 350
    graph.add_block(scaler)

    ao = registry.create("AO", f"AO_{inner_pid_name}")
    ao.id = _make_id()
    ao.config.params = {
        "tag": mv_tag,
        "out_lo": mv_lo, "out_hi": mv_hi,
    }
    ao.x, ao.y = 900, 350
    graph.add_block(ao)

    # Forward wires
    _add_wire(graph, ai_outer.id, "OUT", pid_outer.id, "IN")
    _add_wire(graph, pid_outer.id, "OUT", cas_scaler.id, "IN")       # outer PID -> cascade scaler
    _add_wire(graph, cas_scaler.id, "OUT", pid_inner.id, "CAS_IN")   # cascade scaler -> inner PID SP
    _add_wire(graph, ai_inner.id, "OUT", pid_inner.id, "IN")
    _add_wire(graph, pid_inner.id, "OUT", scaler.id, "IN")
    _add_wire(graph, scaler.id, "OUT", ao.id, "CAS_IN")

    # BKCAL chain: AO -> SCL -> PID_inner -> CAS_SCL -> PID_outer
    _add_wire(graph, ao.id, "BKCAL_OUT", scaler.id, "BKCAL_IN", is_bkcal=True)
    _add_wire(graph, scaler.id, "BKCAL_OUT", pid_inner.id, "BKCAL_IN", is_bkcal=True)
    _add_wire(graph, pid_inner.id, "BKCAL_OUT", cas_scaler.id, "BKCAL_IN", is_bkcal=True)
    _add_wire(graph, cas_scaler.id, "BKCAL_OUT", pid_outer.id, "BKCAL_IN", is_bkcal=True)

    composite.inner_graph = graph
    return composite
