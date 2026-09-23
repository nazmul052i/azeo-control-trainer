"""APC engagement preset strategies.

Builds complete APC engagement strategies for the strategy designer.
Each strategy contains the full APC-DCS communication chain:

  APC_CONTROL → WATCHDOG → SHED_LOGIC → SP_HANDOFF (per MV) → MV_CLAMP (per MV)

These are standalone APC strategies that wire to existing PID controllers
via the data store (not direct block wiring).  The SP_HANDOFF blocks
read/write PID SPs through store tags.
"""
from __future__ import annotations

import logging

from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
from azeo_control_trainer.core.strategy.model.block_registry import registry

# Ensure all block types are registered
import azeo_control_trainer.core.strategy.blocks  # noqa: F401

log = logging.getLogger("strategy.presets.apc")

# ── Layout constants ─────────────────────────────────────────────────
_X_APC    = -400   # APC_CONTROL block
_X_WD     = -100   # WATCHDOG
_X_SHED   =  200   # SHED_LOGIC
_X_HANDOFF =  500  # SP_HANDOFF blocks
_X_CLAMP  =  800   # MV_CLAMP blocks

_Y_START  = -300
_Y_STEP   =  160   # vertical spacing between MV rows


def _make_block(graph, btype, name, x, y, config=None):
    """Create a block, set position and config, add to graph."""
    block = registry.create(btype)
    if block is None:
        raise ValueError(f"Unknown block type: {btype}")
    block.instance_name = name
    block.x = x
    block.y = y
    if config:
        block.config.params.update(config)
        block._apply_config()
    graph.add_block(block)
    return block


def _wire(graph, src, src_term, dst, dst_term, bkcal=False):
    """Add wire between blocks by reference."""
    return graph.add_wire(src.id, src_term, dst.id, dst_term, bkcal)


# =====================================================================
# TE APC Engagement (5 MVs matching TE Base Regulatory)
# =====================================================================

def build_te_apc_engagement() -> StrategyGraph:
    """APC engagement strategy for Tennessee Eastman process.

    5 MV channels matching TE Base Regulatory controllers:
      MV1: TIC109 — reactor temp → xmv_10 (reactor CW)
      MV2: TIC111 — separator temp → xmv_11 (condenser CW)
      MV3: LIC112 — separator level → xmv_7 (sep liquid)
      MV4: LIC115 — stripper level → xmv_8 (str product)
      MV5: PIC107 — reactor pressure → xmv_6 (purge)

    Layout:
      APC_CONTROL → WATCHDOG → SHED_LOGIC
                                  ├── SP_HANDOFF_1 → MV_CLAMP_1
                                  ├── SP_HANDOFF_2 → MV_CLAMP_2
                                  ├── SP_HANDOFF_3 → MV_CLAMP_3
                                  ├── SP_HANDOFF_4 → MV_CLAMP_4
                                  └── SP_HANDOFF_5 → MV_CLAMP_5
    """
    g = StrategyGraph("TE APC Engagement")

    mv_configs = [
        {"name": "TIC109", "ramp_rate": 0.5, "hi": 100.0, "lo": 0.0},
        {"name": "TIC111", "ramp_rate": 0.5, "hi": 100.0, "lo": 0.0},
        {"name": "LIC112", "ramp_rate": 1.0, "hi": 100.0, "lo": 0.0},
        {"name": "LIC115", "ramp_rate": 1.0, "hi": 100.0, "lo": 0.0},
        {"name": "PIC107", "ramp_rate": 0.5, "hi": 100.0, "lo": 0.0},
    ]

    # ── Core APC blocks ──
    apc = _make_block(g, "APC_CONTROL", "DMC_TE", _X_APC, _Y_START + 2 * _Y_STEP, {
        "init_time": 10.0,
        "cycle_period": 60.0,
    })

    wd = _make_block(g, "WATCHDOG", "WD_TE", _X_WD, _Y_START + 2 * _Y_STEP, {
        "timeout": 120.0,
    })

    shed = _make_block(g, "SHED_LOGIC", "SHED_TE", _X_SHED, _Y_START + 2 * _Y_STEP, {
        "partial_threshold": 2,
    })

    # Wire APC → WATCHDOG → SHED
    _wire(g, apc, "HEARTBEAT", wd, "HEARTBEAT")
    _wire(g, apc, "ACTIVE", wd, "ENABLE")
    _wire(g, wd, "HEALTHY", shed, "WATCHDOG_OK")
    _wire(g, apc, "ACTIVE", shed, "ENABLE")

    # ── Per-MV SP_HANDOFF + MV_CLAMP ──
    for i, mv_cfg in enumerate(mv_configs, start=1):
        y = _Y_START + i * _Y_STEP

        handoff = _make_block(g, "SP_HANDOFF", f"SPH_{mv_cfg['name']}",
                              _X_HANDOFF, y, {
                                  "controller_tag": mv_cfg["name"],
                                  "ramp_rate": mv_cfg["ramp_rate"],
                                  "hold_on_shed": True,
                              })

        clamp = _make_block(g, "MV_CLAMP", f"MVC_{mv_cfg['name']}",
                            _X_CLAMP, y, {
                                "controller_tag": mv_cfg["name"],
                                "hi_default": mv_cfg["hi"],
                                "lo_default": mv_cfg["lo"],
                                "deadband": 0.5,
                            })

        # Wire APC MV target → SP_HANDOFF
        _wire(g, apc, f"MV_SP_{i}", handoff, "APC_SP")
        _wire(g, shed, "ACTIVE", handoff, "ACTIVE")

        # SP_HANDOFF → MV_CLAMP
        _wire(g, handoff, "TARGET_SP", clamp, "MV_IN")
        _wire(g, shed, "ACTIVE", clamp, "ACTIVE")

        # MV_CLAMP status → SHED_LOGIC
        _wire(g, clamp, "MV_OK", shed, f"MV_STATUS_{i}")

    return g


# =====================================================================
# Heater APC Engagement (3 MVs: fuel, air, damper)
# =====================================================================

def build_heater_apc_engagement() -> StrategyGraph:
    """APC engagement strategy for fired heater.

    3 MV channels matching heater control loops:
      MV1: TIC-101 — COT → valve_fuel
      MV2: FIC-103 — air rate → valve_air
      MV3: PIC-101 — draft pressure → valve_damper

    Same pattern as TE but with 3 MVs.
    """
    g = StrategyGraph("Heater APC Engagement")

    mv_configs = [
        {"name": "TIC101", "ramp_rate": 0.2, "hi": 1.0, "lo": 0.0},
        {"name": "FIC103", "ramp_rate": 0.5, "hi": 1.0, "lo": 0.0},
        {"name": "PIC101", "ramp_rate": 0.3, "hi": 1.0, "lo": 0.1},
    ]

    # ── Core APC blocks ──
    apc = _make_block(g, "APC_CONTROL", "DMC_HTR", _X_APC, _Y_START + 1 * _Y_STEP, {
        "init_time": 10.0,
        "cycle_period": 60.0,
    })

    wd = _make_block(g, "WATCHDOG", "WD_HTR", _X_WD, _Y_START + 1 * _Y_STEP, {
        "timeout": 120.0,
    })

    shed = _make_block(g, "SHED_LOGIC", "SHED_HTR", _X_SHED, _Y_START + 1 * _Y_STEP, {
        "partial_threshold": 2,
    })

    # Wire APC → WATCHDOG → SHED
    _wire(g, apc, "HEARTBEAT", wd, "HEARTBEAT")
    _wire(g, apc, "ACTIVE", wd, "ENABLE")
    _wire(g, wd, "HEALTHY", shed, "WATCHDOG_OK")
    _wire(g, apc, "ACTIVE", shed, "ENABLE")

    # ── Per-MV SP_HANDOFF + MV_CLAMP ──
    for i, mv_cfg in enumerate(mv_configs, start=1):
        y = _Y_START + (i + 1) * _Y_STEP

        handoff = _make_block(g, "SP_HANDOFF", f"SPH_{mv_cfg['name']}",
                              _X_HANDOFF, y, {
                                  "controller_tag": mv_cfg["name"],
                                  "ramp_rate": mv_cfg["ramp_rate"],
                                  "hold_on_shed": True,
                              })

        clamp = _make_block(g, "MV_CLAMP", f"MVC_{mv_cfg['name']}",
                            _X_CLAMP, y, {
                                "controller_tag": mv_cfg["name"],
                                "hi_default": mv_cfg["hi"],
                                "lo_default": mv_cfg["lo"],
                                "deadband": 0.02,
                            })

        _wire(g, apc, f"MV_SP_{i}", handoff, "APC_SP")
        _wire(g, shed, "ACTIVE", handoff, "ACTIVE")

        _wire(g, handoff, "TARGET_SP", clamp, "MV_IN")
        _wire(g, shed, "ACTIVE", clamp, "ACTIVE")

        _wire(g, clamp, "MV_OK", shed, f"MV_STATUS_{i}")

    return g


# =====================================================================
# Generic APC Engagement Template
# =====================================================================

def build_apc_template() -> StrategyGraph:
    """Generic APC engagement template with 2 MV channels.

    Minimal template for learning the APC engagement pattern.
    User can add more SP_HANDOFF/MV_CLAMP pairs as needed.
    """
    g = StrategyGraph("APC Engagement Template")

    apc = _make_block(g, "APC_CONTROL", "DMC_1", _X_APC, 0, {
        "init_time": 10.0,
        "cycle_period": 60.0,
    })

    wd = _make_block(g, "WATCHDOG", "WD_1", _X_WD, 0, {
        "timeout": 120.0,
    })

    shed = _make_block(g, "SHED_LOGIC", "SHED_1", _X_SHED, 0, {
        "partial_threshold": 2,
    })

    _wire(g, apc, "HEARTBEAT", wd, "HEARTBEAT")
    _wire(g, apc, "ACTIVE", wd, "ENABLE")
    _wire(g, wd, "HEALTHY", shed, "WATCHDOG_OK")
    _wire(g, apc, "ACTIVE", shed, "ENABLE")

    for i in range(1, 3):
        y = i * _Y_STEP

        handoff = _make_block(g, "SP_HANDOFF", f"SPH_MV{i}",
                              _X_HANDOFF, y, {
                                  "ramp_rate": 1.0,
                                  "hold_on_shed": True,
                              })

        clamp = _make_block(g, "MV_CLAMP", f"MVC_MV{i}",
                            _X_CLAMP, y, {
                                "hi_default": 100.0,
                                "lo_default": 0.0,
                                "deadband": 0.5,
                            })

        _wire(g, apc, f"MV_SP_{i}", handoff, "APC_SP")
        _wire(g, shed, "ACTIVE", handoff, "ACTIVE")
        _wire(g, handoff, "TARGET_SP", clamp, "MV_IN")
        _wire(g, shed, "ACTIVE", clamp, "ACTIVE")
        _wire(g, clamp, "MV_OK", shed, f"MV_STATUS_{i}")

    return g


# =====================================================================
# Hot Oil APC Engagement (10 MVs: supply temp, O2, draft, 7 HX flows)
# =====================================================================

def build_hotoil_apc_engagement() -> StrategyGraph:
    """APC engagement strategy for cumene hot oil heater.

    6 MV channels (max supported by APC_CONTROL block):
      MV1: TIC400 — supply temperature (most impactful)
      MV2: AIC410 — O2 trim (combustion optimization)
      MV3: PIC410 — draft pressure
      MV4: FIC441 — EA451 #1 cumene col. reboiler flow (35,790 BPD)
      MV5: FIC444 — EA462 #1 rectifier col. reboiler flow (68,679 BPD)
      MV6: FIC446 — EA464 #1 rectifier col. preheater flow (36,529 BPD)

    The 3 largest HX flows are included; remaining 4 smaller HX loops
    stay in local AUTO.

    Same APC_CONTROL → WATCHDOG → SHED_LOGIC → SP_HANDOFF → MV_CLAMP
    pattern as other plugins.
    """
    g = StrategyGraph("Hot Oil APC Engagement")

    mv_configs = [
        # Heater firing MVs (output 0-1 fraction)
        {"name": "TIC400", "ramp_rate": 0.5, "hi": 1.0, "lo": 0.0},
        {"name": "AIC410", "ramp_rate": 0.1, "hi": 1.0, "lo": 0.0},
        {"name": "PIC410", "ramp_rate": 0.3, "hi": 1.0, "lo": 0.1},
        # 3 largest HX flow MVs (SPs in BPD)
        {"name": "FIC441", "ramp_rate": 1000.0, "hi": 55000.0, "lo": 10000.0},
        {"name": "FIC444", "ramp_rate": 2000.0, "hi": 100000.0, "lo": 20000.0},
        {"name": "FIC446", "ramp_rate": 1000.0, "hi": 55000.0, "lo": 10000.0},
    ]

    # ── Core APC blocks ──
    apc = _make_block(g, "APC_CONTROL", "DMC_HOTOIL", _X_APC, _Y_START + 3 * _Y_STEP, {
        "init_time": 10.0,
        "cycle_period": 60.0,
    })

    wd = _make_block(g, "WATCHDOG", "WD_HOTOIL", _X_WD, _Y_START + 3 * _Y_STEP, {
        "timeout": 120.0,
    })

    shed = _make_block(g, "SHED_LOGIC", "SHED_HOTOIL", _X_SHED, _Y_START + 3 * _Y_STEP, {
        "partial_threshold": 2,
    })

    # Wire APC → WATCHDOG → SHED
    _wire(g, apc, "HEARTBEAT", wd, "HEARTBEAT")
    _wire(g, apc, "ACTIVE", wd, "ENABLE")
    _wire(g, wd, "HEALTHY", shed, "WATCHDOG_OK")
    _wire(g, apc, "ACTIVE", shed, "ENABLE")

    # ── Per-MV SP_HANDOFF + MV_CLAMP ──
    for i, mv_cfg in enumerate(mv_configs, start=1):
        y = _Y_START + i * _Y_STEP

        handoff = _make_block(g, "SP_HANDOFF", f"SPH_{mv_cfg['name']}",
                              _X_HANDOFF, y, {
                                  "controller_tag": mv_cfg["name"],
                                  "ramp_rate": mv_cfg["ramp_rate"],
                                  "hold_on_shed": True,
                              })

        clamp = _make_block(g, "MV_CLAMP", f"MVC_{mv_cfg['name']}",
                            _X_CLAMP, y, {
                                "controller_tag": mv_cfg["name"],
                                "hi_default": mv_cfg["hi"],
                                "lo_default": mv_cfg["lo"],
                                "deadband": 0.5,
                            })

        # Wire APC MV target → SP_HANDOFF
        _wire(g, apc, f"MV_SP_{i}", handoff, "APC_SP")
        _wire(g, shed, "ACTIVE", handoff, "ACTIVE")

        # SP_HANDOFF → MV_CLAMP
        _wire(g, handoff, "TARGET_SP", clamp, "MV_IN")
        _wire(g, shed, "ACTIVE", clamp, "ACTIVE")

        # MV_CLAMP status → SHED_LOGIC
        _wire(g, clamp, "MV_OK", shed, f"MV_STATUS_{i}")

    return g


# =====================================================================
# Registry
# =====================================================================

APC_PRESET_BUILDERS = {
    "APC_TE": ("TE APC Engagement", build_te_apc_engagement),
    "APC_HEATER": ("Heater APC Engagement", build_heater_apc_engagement),
    "APC_HOTOIL": ("Hot Oil APC Engagement", build_hotoil_apc_engagement),
    "APC_TEMPLATE": ("APC Engagement Template", build_apc_template),
}
