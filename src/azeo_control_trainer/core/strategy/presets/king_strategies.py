"""Fired heater control strategies — built as StrategyGraphs.

Generates all 5 schemes (A–E) as pre-wired function-block strategies
that can be loaded directly into the strategy designer canvas.

Each strategy uses real tag names and tuning values from
``config.control_tuning.TUNING``.
"""
from __future__ import annotations

import logging
from pathlib import Path

from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
from azeo_control_trainer.core.strategy.model.block_registry import registry
from azeo_control_trainer.core.strategy.serialization.strategy_io import (
    save_strategy, STRATEGY_DIR,
)

log = logging.getLogger("strategy.presets")

# Ensure all block types are registered
import azeo_control_trainer.core.strategy.blocks  # noqa: F401


# ── Layout constants ─────────────────────────────────────────────────
# Column x-positions for clean left-to-right layout
_X_AI   = -400   # AI blocks (inputs)
_X_PID1 = -120   # Primary controllers
_X_MID  =  100   # Mid-processing (selectors, ratios)
_X_PID2 =  300   # Secondary controllers
_X_AO   =  560   # AO blocks (outputs)

_Y_START = -200
_Y_STEP  =  140   # vertical spacing between rows


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
# Scheme A — Direct Temperature Control
# =====================================================================
def build_scheme_a() -> StrategyGraph:
    """Scheme A: TIC-101 directly drives fuel valve. No cascade.

    Blocks:
      AI(COT) → TIC-101 → MIN_SEL → AO(valve_fuel)
      AI(P_fuel_gas) → PIC-102 ──┘
      AI(fuel_rate) → RATIO → AO(FIC103_SP) (simple air ratio)
      AI(air_rate) → FIC-103 → AO(valve_air)
      AI(P_draft) → PIC-101 → AO(valve_damper)
    """
    g = StrategyGraph("Scheme A — Direct Temperature Control")

    # ── AI blocks ──
    ai_cot   = _make_block(g, "AI", "AI_COT",   _X_AI, _Y_START + 0*_Y_STEP,
                           {"tag": "COT"})
    ai_fuel  = _make_block(g, "AI", "AI_FUEL",  _X_AI, _Y_START + 2*_Y_STEP,
                           {"tag": "fuel_rate"})
    ai_pfuel = _make_block(g, "AI", "AI_PFUEL", _X_AI, _Y_START + 1*_Y_STEP,
                           {"tag": "P_fuel_gas"})
    ai_air   = _make_block(g, "AI", "AI_AIR",   _X_AI, _Y_START + 3*_Y_STEP,
                           {"tag": "air_rate"})
    ai_draft = _make_block(g, "AI", "AI_DRAFT", _X_AI, _Y_START + 4*_Y_STEP,
                           {"tag": "P_draft"})

    # ── Controllers ──
    tic101 = _make_block(g, "PID", "TIC-101", _X_PID1, _Y_START + 0*_Y_STEP, {
        "GAIN": 0.02, "RESET": 120.0, "RATE": 0.0, "action": "reverse",
        "out_lo": 0.0, "out_hi": 1.0, "sp_init": 700.0,
        "sp_lo": 672.0, "sp_hi": 728.0, "beta": 0.7,
    })
    pic102 = _make_block(g, "PID", "PIC-102", _X_PID1, _Y_START + 1*_Y_STEP, {
        "GAIN": 2e-5, "RESET": 9999.0, "action": "direct",
        "out_lo": 0.0, "out_hi": 1.0, "sp_init": 70000.0,
    })
    pic101 = _make_block(g, "PID", "PIC-101", _X_PID1, _Y_START + 4*_Y_STEP, {
        "GAIN": 0.005, "RESET": 120.0, "action": "direct",
        "out_lo": 0.1, "out_hi": 1.0,
    })
    fic103 = _make_block(g, "PID", "FIC-103", _X_PID2, _Y_START + 3*_Y_STEP, {
        "GAIN": 0.015, "RESET": 15.0, "action": "reverse",
        "out_lo": 0.0, "out_hi": 1.0,
    })

    # ── Ratio (air = fuel × ratio) ──
    ratio = _make_block(g, "RATIO", "AIR_RATIO", _X_MID, _Y_START + 2*_Y_STEP, {
        "ratio": 1.15, "bias": 0.0,
    })

    # ── MIN selector (TIC101 output vs PIC102 override) ──
    minsel = _make_block(g, "MIN_SELECT", "MIN_SEL", _X_MID, _Y_START + 0*_Y_STEP)

    # ── AO blocks ──
    ao_fuel   = _make_block(g, "AO", "AO_FUEL",   _X_AO, _Y_START + 0*_Y_STEP,
                            {"tag": "valve_fuel"})
    ao_air    = _make_block(g, "AO", "AO_AIR",    _X_AO, _Y_START + 3*_Y_STEP,
                            {"tag": "valve_air"})
    ao_damper = _make_block(g, "AO", "AO_DAMPER", _X_AO, _Y_START + 4*_Y_STEP,
                            {"tag": "valve_damper"})

    # ── Wiring ──
    _wire(g, ai_cot,   "OUT", tic101,  "IN")
    _wire(g, ai_pfuel, "OUT", pic102,  "IN")
    _wire(g, tic101,   "OUT", minsel,  "IN1")
    _wire(g, pic102,   "OUT", minsel,  "IN2")
    _wire(g, minsel,   "OUT", ao_fuel, "CAS_IN")

    _wire(g, ai_fuel, "OUT", ratio,  "IN")
    _wire(g, ratio,   "OUT", fic103, "SP")
    _wire(g, ai_air,  "OUT", fic103, "IN")
    _wire(g, fic103,  "OUT", ao_air, "CAS_IN")

    _wire(g, ai_draft, "OUT", pic101,    "IN")
    _wire(g, pic101,   "OUT", ao_damper, "CAS_IN")

    return g


# =====================================================================
# Scheme B — Simple Cascade
# =====================================================================
def build_scheme_b() -> StrategyGraph:
    """Scheme B: TIC-101 → FIC-102 cascade. No cross-limiting.

    TIC-101 output becomes FIC-102 SP via cascade.
    """
    g = StrategyGraph("Scheme B — Simple Cascade")

    # ── AI ──
    ai_cot   = _make_block(g, "AI", "AI_COT",   _X_AI, _Y_START + 0*_Y_STEP,
                           {"tag": "COT"})
    ai_fuel  = _make_block(g, "AI", "AI_FUEL",  _X_AI, _Y_START + 1*_Y_STEP,
                           {"tag": "fuel_rate"})
    ai_pfuel = _make_block(g, "AI", "AI_PFUEL", _X_AI, _Y_START + 2*_Y_STEP,
                           {"tag": "P_fuel_gas"})
    ai_air   = _make_block(g, "AI", "AI_AIR",   _X_AI, _Y_START + 3*_Y_STEP,
                           {"tag": "air_rate"})
    ai_draft = _make_block(g, "AI", "AI_DRAFT", _X_AI, _Y_START + 4*_Y_STEP,
                           {"tag": "P_draft"})

    # ── Controllers ──
    tic101 = _make_block(g, "PID", "TIC-101", _X_PID1, _Y_START + 0*_Y_STEP, {
        "GAIN": 0.02, "RESET": 120.0, "action": "reverse",
        "out_lo": 0.0, "out_hi": 1.0, "sp_init": 700.0,
        "sp_lo": 672.0, "sp_hi": 728.0, "beta": 0.7, "mode": "AUTO",
    })
    fic102 = _make_block(g, "PID", "FIC-102", _X_PID2, _Y_START + 1*_Y_STEP, {
        "GAIN": 0.8, "RESET": 8.0, "action": "reverse",
        "out_lo": 0.0, "out_hi": 1.0, "mode": "CASCADE",
    })
    pic102 = _make_block(g, "PID", "PIC-102", _X_PID1, _Y_START + 2*_Y_STEP, {
        "GAIN": 2e-5, "RESET": 9999.0, "action": "direct",
        "out_lo": 0.0, "out_hi": 1.0, "sp_init": 70000.0,
    })
    fic103 = _make_block(g, "PID", "FIC-103", _X_PID2, _Y_START + 3*_Y_STEP, {
        "GAIN": 0.015, "RESET": 15.0, "action": "reverse",
    })
    pic101 = _make_block(g, "PID", "PIC-101", _X_PID1, _Y_START + 4*_Y_STEP, {
        "GAIN": 0.005, "RESET": 120.0, "action": "direct", "out_lo": 0.1,
    })

    # ── Ratio ──
    ratio = _make_block(g, "RATIO", "AIR_RATIO", _X_MID, _Y_START + 2.5*_Y_STEP, {
        "ratio": 1.15,
    })

    # ── MIN selector ──
    minsel = _make_block(g, "MIN_SELECT", "MIN_SEL", _X_MID + 160, _Y_START + 1*_Y_STEP)

    # ── AO ──
    ao_fuel   = _make_block(g, "AO", "AO_FUEL",   _X_AO, _Y_START + 1*_Y_STEP,
                            {"tag": "valve_fuel"})
    ao_air    = _make_block(g, "AO", "AO_AIR",    _X_AO, _Y_START + 3*_Y_STEP,
                            {"tag": "valve_air"})
    ao_damper = _make_block(g, "AO", "AO_DAMPER", _X_AO, _Y_START + 4*_Y_STEP,
                            {"tag": "valve_damper"})

    # ── Wiring ──
    # Cascade: TIC-101 OUT → FIC-102 CAS_IN
    _wire(g, ai_cot,  "OUT", tic101, "IN")
    _wire(g, tic101,  "OUT", fic102, "CAS_IN")
    _wire(g, fic102,  "BKCAL_OUT", tic101, "BKCAL_IN", bkcal=True)
    _wire(g, ai_fuel, "OUT", fic102, "IN")

    # MIN selector
    _wire(g, fic102, "OUT", minsel, "IN1")
    _wire(g, ai_pfuel, "OUT", pic102, "IN")
    _wire(g, pic102, "OUT", minsel, "IN2")
    _wire(g, minsel, "OUT", ao_fuel, "CAS_IN")

    # Air ratio
    _wire(g, ai_fuel, "OUT", ratio, "IN")
    _wire(g, ratio,   "OUT", fic103, "SP")
    _wire(g, ai_air,  "OUT", fic103, "IN")
    _wire(g, fic103,  "OUT", ao_air, "CAS_IN")

    # Draft
    _wire(g, ai_draft, "OUT", pic101, "IN")
    _wire(g, pic101, "OUT", ao_damper, "CAS_IN")

    return g


# =====================================================================
# Scheme C — Cascade + Cross-Limiting (Industry Standard)
# =====================================================================
def build_scheme_c() -> StrategyGraph:
    """Scheme C: Full cascade TIC-101 → FIC-102 with cross-limiting.

    Air/fuel lead-lag cross-limiting, O2 trim via AIC-101,
    PIC-102 burner pressure override.
    """
    g = StrategyGraph("Scheme C — Cascade + Cross-Limiting")

    # ── AI ──
    ai_cot   = _make_block(g, "AI", "AI_COT",   _X_AI, _Y_START + 0*_Y_STEP,
                           {"tag": "COT"})
    ai_fuel  = _make_block(g, "AI", "AI_FUEL",  _X_AI, _Y_START + 1*_Y_STEP,
                           {"tag": "fuel_rate"})
    ai_pfuel = _make_block(g, "AI", "AI_PFUEL", _X_AI, _Y_START + 2*_Y_STEP,
                           {"tag": "P_fuel_gas"})
    ai_air   = _make_block(g, "AI", "AI_AIR",   _X_AI, _Y_START + 3*_Y_STEP,
                           {"tag": "air_rate"})
    ai_o2    = _make_block(g, "AI", "AI_O2",    _X_AI, _Y_START + 4*_Y_STEP,
                           {"tag": "O2_pct"})
    ai_co    = _make_block(g, "AI", "AI_CO",    _X_AI, _Y_START + 5*_Y_STEP,
                           {"tag": "CO_ppm"})
    ai_draft = _make_block(g, "AI", "AI_DRAFT", _X_AI, _Y_START + 6*_Y_STEP,
                           {"tag": "P_draft"})

    # ── Primary controllers ──
    tic101 = _make_block(g, "PID", "TIC-101", _X_PID1, _Y_START + 0*_Y_STEP, {
        "GAIN": 0.02, "RESET": 120.0, "action": "reverse",
        "out_lo": 0.0, "out_hi": 1.0, "sp_init": 700.0,
        "sp_lo": 672.0, "sp_hi": 728.0, "beta": 0.7,
    })
    pic102 = _make_block(g, "PID", "PIC-102", _X_PID1, _Y_START + 2*_Y_STEP, {
        "GAIN": 2e-5, "RESET": 9999.0, "action": "direct",
        "out_lo": 0.0, "out_hi": 1.0, "sp_init": 70000.0,
    })
    aic101 = _make_block(g, "PID", "AIC-101", _X_PID1, _Y_START + 4*_Y_STEP, {
        "GAIN": 0.05, "RESET": 300.0, "action": "reverse",
        "out_lo": -0.10, "out_hi": 0.10, "sp_init": 3.0,
    })
    aic102 = _make_block(g, "PID", "AIC-102", _X_PID1, _Y_START + 5*_Y_STEP, {
        "GAIN": 0.001, "RESET": 60.0, "action": "direct",
        "out_lo": 0.0, "out_hi": 0.20, "sp_init": 200.0,
    })
    pic101 = _make_block(g, "PID", "PIC-101", _X_PID1, _Y_START + 6*_Y_STEP, {
        "GAIN": 0.005, "RESET": 120.0, "action": "direct", "out_lo": 0.1,
    })

    # ── Secondary controllers ──
    fic102 = _make_block(g, "PID", "FIC-102", _X_PID2, _Y_START + 1*_Y_STEP, {
        "GAIN": 0.8, "RESET": 8.0, "action": "reverse", "mode": "CASCADE",
    })
    fic103 = _make_block(g, "PID", "FIC-103", _X_PID2, _Y_START + 3*_Y_STEP, {
        "GAIN": 0.015, "RESET": 15.0, "action": "reverse",
    })

    # ── Cross-limiting: Lead-Lag on fuel demand for air ──
    lead_lag = _make_block(g, "LEAD_LAG", "XL_LEAD_LAG", _X_MID, _Y_START + 2.5*_Y_STEP, {
        "lead_time": 15.0, "lag_time": 5.0,
    })

    # ── Ratio (air = fuel × (base_ratio + O2_trim + CO_correction)) ──
    ratio = _make_block(g, "RATIO", "AIR_RATIO", _X_MID, _Y_START + 3*_Y_STEP, {
        "ratio": 1.15,
    })

    # ── Summer (base ratio + O2 trim + CO correction) ──
    summer = _make_block(g, "SUMMER", "RATIO_TRIM", _X_MID, _Y_START + 4*_Y_STEP, {
        "gain_1": 1.0, "gain_2": 1.0, "gain_3": 1.0, "bias": 1.15,
    })

    # ── MIN / MAX selectors for cross-limiting ──
    minsel = _make_block(g, "MIN_SELECT", "MIN_SEL", _X_MID + 160, _Y_START + 1*_Y_STEP)
    maxsel = _make_block(g, "MAX_SELECT", "MAX_AIR", _X_MID + 160, _Y_START + 3*_Y_STEP)

    # ── AO ──
    ao_fuel   = _make_block(g, "AO", "AO_FUEL",   _X_AO, _Y_START + 1*_Y_STEP,
                            {"tag": "valve_fuel"})
    ao_air    = _make_block(g, "AO", "AO_AIR",    _X_AO, _Y_START + 3*_Y_STEP,
                            {"tag": "valve_air"})
    ao_damper = _make_block(g, "AO", "AO_DAMPER", _X_AO, _Y_START + 6*_Y_STEP,
                            {"tag": "valve_damper"})

    # ── Wiring ──
    # Cascade: TIC-101 → FIC-102
    _wire(g, ai_cot,  "OUT", tic101, "IN")
    _wire(g, tic101,  "OUT", fic102, "CAS_IN")
    _wire(g, fic102,  "BKCAL_OUT", tic101, "BKCAL_IN", bkcal=True)
    _wire(g, ai_fuel, "OUT", fic102, "IN")

    # MIN selector (FIC-102 vs PIC-102)
    _wire(g, fic102,  "OUT", minsel, "IN1")
    _wire(g, ai_pfuel, "OUT", pic102, "IN")
    _wire(g, pic102,  "OUT", minsel, "IN2")
    _wire(g, minsel,  "OUT", ao_fuel, "CAS_IN")

    # Cross-limiting lead-lag on fuel demand for air
    _wire(g, fic102, "OUT", lead_lag, "IN")

    # O2 trim + CO correction → ratio adjustment
    _wire(g, ai_o2, "OUT", aic101, "IN")
    _wire(g, ai_co, "OUT", aic102, "IN")
    _wire(g, aic101, "OUT", summer, "IN1")
    _wire(g, aic102, "OUT", summer, "IN2")

    # Air ratio from fuel measurement × adjusted ratio
    _wire(g, lead_lag, "OUT", ratio, "IN")
    _wire(g, summer,   "OUT", ratio, "RATIO_IN")  # dynamic ratio from O2/CO trim
    _wire(g, ratio,    "OUT", fic103, "SP")
    _wire(g, ai_air,   "OUT", fic103, "IN")
    _wire(g, fic103,   "OUT", maxsel, "IN1")
    _wire(g, ratio,    "OUT", maxsel, "IN2")
    _wire(g, maxsel,   "OUT", ao_air, "CAS_IN")

    # Draft
    _wire(g, ai_draft, "OUT", pic101, "IN")
    _wire(g, pic101, "OUT", ao_damper, "CAS_IN")

    return g


# =====================================================================
# Scheme D — Fired Duty Control
# =====================================================================
def build_scheme_d() -> StrategyGraph:
    """Scheme D: SG-compensated fired duty measurement.

    Same cascade as C but fuel metered in energy units via fuel_sg
    and fuel_nhv compensation.
    """
    g = StrategyGraph("Scheme D — Fired Duty Control")

    # ── AI ──
    ai_cot   = _make_block(g, "AI", "AI_COT",   _X_AI, _Y_START + 0*_Y_STEP,
                           {"tag": "COT"})
    ai_fuel  = _make_block(g, "AI", "AI_FUEL",  _X_AI, _Y_START + 1*_Y_STEP,
                           {"tag": "fuel_rate"})
    ai_pfuel = _make_block(g, "AI", "AI_PFUEL", _X_AI, _Y_START + 2*_Y_STEP,
                           {"tag": "P_fuel_gas"})
    ai_air   = _make_block(g, "AI", "AI_AIR",   _X_AI, _Y_START + 3*_Y_STEP,
                           {"tag": "air_rate"})
    ai_o2    = _make_block(g, "AI", "AI_O2",    _X_AI, _Y_START + 4*_Y_STEP,
                           {"tag": "O2_pct"})
    ai_co    = _make_block(g, "AI", "AI_CO",    _X_AI, _Y_START + 5*_Y_STEP,
                           {"tag": "CO_ppm"})
    ai_draft = _make_block(g, "AI", "AI_DRAFT", _X_AI, _Y_START + 6*_Y_STEP,
                           {"tag": "P_draft"})
    ai_sg    = _make_block(g, "AI", "AI_SG",    _X_AI, _Y_START + 7*_Y_STEP,
                           {"tag": "fuel_sg"})
    ai_nhv   = _make_block(g, "AI", "AI_NHV",   _X_AI, _Y_START + 8*_Y_STEP,
                           {"tag": "fuel_nhv"})

    # ── Fired duty calculation: corrected_flow × NHV ──
    # corrected_flow = fuel_rate / sqrt(SG)   (SG compensation)
    # fired_duty = corrected_flow × NHV
    duty_mult = _make_block(g, "MULTIPLIER", "DUTY_CALC", _X_MID - 60, _Y_START + 1*_Y_STEP)

    # ── Primary controllers ──
    tic101 = _make_block(g, "PID", "TIC-101", _X_PID1, _Y_START + 0*_Y_STEP, {
        "GAIN": 0.02, "RESET": 120.0, "action": "reverse",
        "out_lo": 0.0, "out_hi": 1.0, "sp_init": 700.0,
        "sp_lo": 672.0, "sp_hi": 728.0, "beta": 0.7,
    })
    fic102 = _make_block(g, "PID", "FIC-102", _X_PID2, _Y_START + 1*_Y_STEP, {
        "GAIN": 0.8, "RESET": 8.0, "action": "reverse", "mode": "CASCADE",
    })
    pic102 = _make_block(g, "PID", "PIC-102", _X_PID1, _Y_START + 2*_Y_STEP, {
        "GAIN": 2e-5, "RESET": 9999.0, "action": "direct",
        "sp_init": 70000.0,
    })
    aic101 = _make_block(g, "PID", "AIC-101", _X_PID1, _Y_START + 4*_Y_STEP, {
        "GAIN": 0.05, "RESET": 300.0, "action": "reverse",
        "out_lo": -0.10, "out_hi": 0.10, "sp_init": 3.0,
    })
    aic102 = _make_block(g, "PID", "AIC-102", _X_PID1, _Y_START + 5*_Y_STEP, {
        "GAIN": 0.001, "RESET": 60.0, "action": "direct",
        "out_lo": 0.0, "out_hi": 0.20, "sp_init": 200.0,
    })
    pic101 = _make_block(g, "PID", "PIC-101", _X_PID1, _Y_START + 6*_Y_STEP, {
        "GAIN": 0.005, "RESET": 120.0, "action": "direct", "out_lo": 0.1,
    })
    fic103 = _make_block(g, "PID", "FIC-103", _X_PID2, _Y_START + 3*_Y_STEP, {
        "GAIN": 0.015, "RESET": 15.0, "action": "reverse",
    })

    # ── Cross-limiting and ratio ──
    lead_lag = _make_block(g, "LEAD_LAG", "XL_LEAD_LAG", _X_MID, _Y_START + 2.5*_Y_STEP, {
        "lead_time": 15.0, "lag_time": 5.0,
    })
    ratio = _make_block(g, "RATIO", "AIR_RATIO", _X_MID, _Y_START + 3*_Y_STEP, {
        "ratio": 1.15,
    })
    summer = _make_block(g, "SUMMER", "RATIO_TRIM", _X_MID, _Y_START + 4*_Y_STEP, {
        "gain_1": 1.0, "gain_2": 1.0, "bias": 1.15,
    })
    minsel = _make_block(g, "MIN_SELECT", "MIN_SEL", _X_MID + 160, _Y_START + 1*_Y_STEP)
    maxsel = _make_block(g, "MAX_SELECT", "MAX_AIR", _X_MID + 160, _Y_START + 3*_Y_STEP)

    # ── AO ──
    ao_fuel   = _make_block(g, "AO", "AO_FUEL",   _X_AO, _Y_START + 1*_Y_STEP,
                            {"tag": "valve_fuel"})
    ao_air    = _make_block(g, "AO", "AO_AIR",    _X_AO, _Y_START + 3*_Y_STEP,
                            {"tag": "valve_air"})
    ao_damper = _make_block(g, "AO", "AO_DAMPER", _X_AO, _Y_START + 6*_Y_STEP,
                            {"tag": "valve_damper"})

    # ── Wiring ──
    # Duty calculation: fuel × NHV → duty PV for FIC-102
    _wire(g, ai_fuel, "OUT", duty_mult, "IN1")
    _wire(g, ai_nhv,  "OUT", duty_mult, "IN2")

    # Cascade with duty-compensated PV
    _wire(g, ai_cot,    "OUT", tic101, "IN")
    _wire(g, tic101,    "OUT", fic102, "CAS_IN")
    _wire(g, fic102,    "BKCAL_OUT", tic101, "BKCAL_IN", bkcal=True)
    _wire(g, duty_mult, "OUT", fic102, "IN")

    # MIN selector
    _wire(g, fic102,  "OUT", minsel, "IN1")
    _wire(g, ai_pfuel, "OUT", pic102, "IN")
    _wire(g, pic102,  "OUT", minsel, "IN2")
    _wire(g, minsel,  "OUT", ao_fuel, "CAS_IN")

    # Cross-limiting
    _wire(g, fic102,  "OUT", lead_lag, "IN")
    _wire(g, ai_o2, "OUT", aic101, "IN")
    _wire(g, ai_co, "OUT", aic102, "IN")
    _wire(g, aic101, "OUT", summer, "IN1")
    _wire(g, aic102, "OUT", summer, "IN2")

    # Air
    _wire(g, lead_lag, "OUT", ratio, "IN")
    _wire(g, summer,   "OUT", ratio, "RATIO_IN")
    _wire(g, ratio,    "OUT", fic103, "SP")
    _wire(g, ai_air,   "OUT", fic103, "IN")
    _wire(g, fic103,   "OUT", maxsel, "IN1")
    _wire(g, ratio,    "OUT", maxsel, "IN2")
    _wire(g, maxsel,   "OUT", ao_air, "CAS_IN")

    # Draft
    _wire(g, ai_draft, "OUT", pic101, "IN")
    _wire(g, pic101, "OUT", ao_damper, "CAS_IN")

    return g


# =====================================================================
# Scheme E — Fuel Pressure Control
# =====================================================================
def build_scheme_e() -> StrategyGraph:
    """Scheme E: TIC-101 → PIC-102 cascade (fuel pressure secondary).

    Educational: shows why pressure control is problematic.
    """
    g = StrategyGraph("Scheme E — Fuel Pressure Control")

    # ── AI ──
    ai_cot   = _make_block(g, "AI", "AI_COT",   _X_AI, _Y_START + 0*_Y_STEP,
                           {"tag": "COT"})
    ai_fuel  = _make_block(g, "AI", "AI_FUEL",  _X_AI, _Y_START + 1*_Y_STEP,
                           {"tag": "fuel_rate"})
    ai_pfuel = _make_block(g, "AI", "AI_PFUEL", _X_AI, _Y_START + 2*_Y_STEP,
                           {"tag": "P_fuel_gas"})
    ai_air   = _make_block(g, "AI", "AI_AIR",   _X_AI, _Y_START + 3*_Y_STEP,
                           {"tag": "air_rate"})
    ai_draft = _make_block(g, "AI", "AI_DRAFT", _X_AI, _Y_START + 4*_Y_STEP,
                           {"tag": "P_draft"})

    # ── Controllers ──
    tic101 = _make_block(g, "PID", "TIC-101", _X_PID1, _Y_START + 0*_Y_STEP, {
        "GAIN": 0.02, "RESET": 120.0, "action": "reverse",
        "out_lo": 0.0, "out_hi": 1.0, "sp_init": 700.0,
        "sp_lo": 672.0, "sp_hi": 728.0, "beta": 0.7,
    })
    pic102 = _make_block(g, "PID", "PIC-102", _X_PID2, _Y_START + 1*_Y_STEP, {
        "GAIN": 2e-5, "RESET": 9999.0, "action": "direct",
        "out_lo": 0.0, "out_hi": 1.0, "sp_init": 70000.0, "mode": "CASCADE",
    })
    fic103 = _make_block(g, "PID", "FIC-103", _X_PID2, _Y_START + 3*_Y_STEP, {
        "GAIN": 0.015, "RESET": 15.0, "action": "reverse",
    })
    pic101 = _make_block(g, "PID", "PIC-101", _X_PID1, _Y_START + 4*_Y_STEP, {
        "GAIN": 0.005, "RESET": 120.0, "action": "direct", "out_lo": 0.1,
    })

    # ── Ratio ──
    ratio = _make_block(g, "RATIO", "AIR_RATIO", _X_MID, _Y_START + 2.5*_Y_STEP, {
        "ratio": 1.15,
    })

    # ── AO ──
    ao_fuel   = _make_block(g, "AO", "AO_FUEL",   _X_AO, _Y_START + 1*_Y_STEP,
                            {"tag": "valve_fuel"})
    ao_air    = _make_block(g, "AO", "AO_AIR",    _X_AO, _Y_START + 3*_Y_STEP,
                            {"tag": "valve_air"})
    ao_damper = _make_block(g, "AO", "AO_DAMPER", _X_AO, _Y_START + 4*_Y_STEP,
                            {"tag": "valve_damper"})

    # ── Wiring ──
    # Cascade: TIC-101 → PIC-102 (fuel pressure as secondary!)
    _wire(g, ai_cot,   "OUT", tic101, "IN")
    _wire(g, tic101,   "OUT", pic102, "CAS_IN")
    _wire(g, pic102,   "BKCAL_OUT", tic101, "BKCAL_IN", bkcal=True)
    _wire(g, ai_pfuel, "OUT", pic102, "IN")
    _wire(g, pic102,   "OUT", ao_fuel, "CAS_IN")

    # Simple air ratio (no cross-limiting — per King's warning)
    _wire(g, ai_fuel, "OUT", ratio, "IN")
    _wire(g, ratio,   "OUT", fic103, "SP")
    _wire(g, ai_air,  "OUT", fic103, "IN")
    _wire(g, fic103,  "OUT", ao_air, "CAS_IN")

    # Draft
    _wire(g, ai_draft, "OUT", pic101, "IN")
    _wire(g, pic101, "OUT", ao_damper, "CAS_IN")

    return g


# =====================================================================
# BMS — Burner Management System Strategy
# =====================================================================

def build_bms_strategy() -> StrategyGraph:
    """Build a complete BMS/SIS strategy with NFPA 85/86 sequencing.

    Layout:
      Row 0: DI(draft) → BLOWER → FLAME_DET(pilot) ──┐
      Row 1: DI(blower_cmd) ──────────────────────────→│
      Row 2:                          BURNER_SEQ ←─────┘
      Row 3:                             ├── FUEL_VALVE(pilot) → DO
      Row 4:                             ├── FUEL_VALVE(main)  → DO
      Row 5:                             └── DO(igniter)
      Row 6: DI(start) ──→ SEQ
      Row 7: DI(stop)  ──→ SEQ
      Row 8: DI(trip)  ──→ SEQ
    """
    g = StrategyGraph("BMS — Burner Management System")
    X_IN = -500
    X_BLK = -200
    X_SEQ = 100
    X_VLV = 400
    X_OUT = 650
    Y0 = -300
    YS = 120

    # ── Input blocks ──
    di_draft = _make_block(g, "DI", "DI_DRAFT", X_IN, Y0,
                           {"tag": "P_draft"})
    di_blower_cmd = _make_block(g, "DI", "DI_BLOWER_CMD", X_IN, Y0 + YS,
                                {"tag": "bms.blower_cmd"})
    di_start = _make_block(g, "DI", "DI_START", X_IN, Y0 + 6*YS,
                           {"tag": "bms.startup_cmd"})
    di_stop = _make_block(g, "DI", "DI_STOP", X_IN, Y0 + 7*YS,
                          {"tag": "bms.shutdown_cmd"})
    di_trip = _make_block(g, "DI", "DI_TRIP", X_IN, Y0 + 8*YS,
                          {"tag": "bms.trip_cmd"})

    # ── Blower ──
    blower = _make_block(g, "BLOWER", "FD_BLOWER", X_BLK, Y0,
                         {"start_delay": 3.0, "draft_setpoint": -20.0})
    _wire(g, di_blower_cmd, "OUT", blower, "START_CMD")
    _wire(g, di_draft, "OUT", blower, "DRAFT_PV")

    # ── Flame Detectors ──
    flame_pilot = _make_block(g, "FLAME_DET", "FLAME_PILOT", X_BLK, Y0 + 2*YS,
                              {"num_sensors": 1, "min_votes": 1})
    # Pilot flame sensor reads from BMS model
    di_pilot_ir = _make_block(g, "DI", "DI_PILOT_IR", X_IN, Y0 + 2*YS,
                              {"tag": "bms.flame_ir"})
    _wire(g, di_pilot_ir, "OUT", flame_pilot, "SENSOR_1")

    flame_main = _make_block(g, "FLAME_DET", "FLAME_MAIN", X_BLK, Y0 + 3*YS,
                             {"num_sensors": 1, "min_votes": 1})
    di_main_uv = _make_block(g, "DI", "DI_MAIN_UV", X_IN, Y0 + 3*YS,
                             {"tag": "bms.flame_uv"})
    _wire(g, di_main_uv, "OUT", flame_main, "SENSOR_1")

    # ── Burner Sequencer ──
    seq = _make_block(g, "BURNER_SEQ", "BMS_SEQ", X_SEQ, Y0 + 2*YS,
                      {"purge_time": 60.0, "ignition_trial": 10.0,
                       "stabilize_time": 15.0, "post_purge_time": 30.0})

    _wire(g, di_start, "OUT", seq, "START")
    _wire(g, di_stop, "OUT", seq, "STOP")
    _wire(g, di_trip, "OUT", seq, "TRIP")
    _wire(g, blower, "PROVEN", seq, "DRAFT_OK")
    _wire(g, flame_pilot, "FLAME_OK", seq, "PILOT_FLAME")
    _wire(g, flame_main, "FLAME_OK", seq, "MAIN_FLAME")

    # ── Fuel Valves ──
    pilot_vlv = _make_block(g, "FUEL_VALVE", "PILOT_SSOV", X_VLV, Y0 + 3*YS,
                            {"stroke_time": 1.5})
    _wire(g, seq, "PILOT_FUEL_CMD", pilot_vlv, "OPEN_CMD")

    main_vlv = _make_block(g, "FUEL_VALVE", "MAIN_SSOV", X_VLV, Y0 + 4*YS,
                           {"stroke_time": 2.0})
    _wire(g, seq, "MAIN_FUEL_CMD", main_vlv, "OPEN_CMD")

    # ── Output blocks ──
    do_blower = _make_block(g, "DO", "DO_BLOWER", X_OUT, Y0,
                            {"tag": "bms.blower_cmd"})
    _wire(g, seq, "BLOWER_CMD", do_blower, "IN")

    do_igniter = _make_block(g, "DO", "DO_IGNITER", X_OUT, Y0 + 2*YS,
                             {"tag": "bms.igniter_on"})
    _wire(g, seq, "IGNITER_CMD", do_igniter, "IN")

    do_pilot_fuel = _make_block(g, "DO", "DO_PILOT_FUEL", X_OUT, Y0 + 3*YS,
                                {"tag": "bms.pilot_fuel_open"})
    _wire(g, pilot_vlv, "OPEN", do_pilot_fuel, "IN")

    do_main_fuel = _make_block(g, "DO", "DO_MAIN_FUEL", X_OUT, Y0 + 4*YS,
                               {"tag": "bms.main_fuel_open"})
    _wire(g, main_vlv, "OPEN", do_main_fuel, "IN")

    # State output (for HMI display)
    ao_state_timer = _make_block(g, "AO", "AO_STATE_TIMER", X_OUT, Y0 + 5*YS,
                                 {"tag": "bms.state_timer"})
    _wire(g, seq, "TIMER", ao_state_timer, "CAS_IN")

    return g


# =====================================================================
# Combined Heater Strategy — Full Plant Control
# =====================================================================

def build_combined_heater() -> StrategyGraph:
    """Combined fired heater strategy: safety + pass balance + COT cascade + fuel/air ratio.

    This is the complete plant control strategy combining all subsystems:

    1. BMS SAFETY (NFPA 85/86)
       BLOWER → FLAME_DET → BURNER_SEQ → FUEL_VALVE(pilot) → DO
                                        → FUEL_VALVE(main)  → DO

    2. COT CASCADE (Scheme C — industry standard)
       AI(COT) → TIC-101 → FIC-102 → MIN_SEL ← PIC-102 → AO(fuel)

    3. CROSS-LIMITED FUEL/AIR RATIO with O2 trim + CO safety
       FIC-102.OUT → LEAD_LAG → RATIO ← (AIC-101 + AIC-102) → SUMMER
       RATIO.OUT → FIC-103 → MAX_SEL → AO(air)

    4. PASS TEMPERATURE BALANCE (4 passes)
       AI(T_fluid_n) → TIC-101n → FIC-101n ← AI(feed_rate_pass_n) → AO(pass_valve_n)

    5. DRAFT PRESSURE
       AI(P_draft) → PIC-101 → AO(damper)

    Layout (row groups):
      Rows  0-6:  Combustion control (COT cascade, fuel/air, O2/CO, draft)
      Rows  7-14: Pass balance (4× TIC→FIC cascade)
      Rows 15-23: BMS safety (blower, flame detect, sequencer, fuel valves)
    """
    g = StrategyGraph("Combined Heater — Full Plant Control")

    # Layout columns
    X_IN  = -600     # AI/DI inputs
    X_P1  = -250     # Primary controllers / BMS blocks
    X_MID =   50     # Mid-processing (selectors, ratios, lead-lag)
    X_P2  =  300     # Secondary controllers / fuel valves
    X_OUT =  560     # AO/DO outputs
    Y0    = -400
    YS    =  130     # row spacing

    # ================================================================
    # SECTION 1: COT CASCADE + FUEL/AIR RATIO + O2/CO + DRAFT
    # ================================================================

    # ── AI blocks (combustion) ──
    ai_cot   = _make_block(g, "AI", "AI_COT",   X_IN, Y0 + 0*YS,
                           {"tag": "COT"})
    ai_fuel  = _make_block(g, "AI", "AI_FUEL",  X_IN, Y0 + 1*YS,
                           {"tag": "fuel_rate"})
    ai_pfuel = _make_block(g, "AI", "AI_PFUEL", X_IN, Y0 + 2*YS,
                           {"tag": "P_fuel_gas"})
    ai_air   = _make_block(g, "AI", "AI_AIR",   X_IN, Y0 + 3*YS,
                           {"tag": "air_rate"})
    ai_o2    = _make_block(g, "AI", "AI_O2",    X_IN, Y0 + 4*YS,
                           {"tag": "O2_pct"})
    ai_co    = _make_block(g, "AI", "AI_CO",    X_IN, Y0 + 5*YS,
                           {"tag": "CO_ppm"})
    ai_draft = _make_block(g, "AI", "AI_DRAFT", X_IN, Y0 + 6*YS,
                           {"tag": "P_draft"})

    # ── Primary controllers ──
    tic101 = _make_block(g, "PID", "TIC-101", X_P1, Y0 + 0*YS, {
        "GAIN": 0.02, "RESET": 120.0, "action": "reverse",
        "out_lo": 0.0, "out_hi": 100.0, "sp_init": 700.0,
        "sp_lo": 672.0, "sp_hi": 728.0, "beta": 0.7,
    })
    pic102 = _make_block(g, "PID", "PIC-102", X_P1, Y0 + 2*YS, {
        "GAIN": 2e-5, "RESET": 9999.0, "action": "direct",
        "out_lo": 0.0, "out_hi": 100.0, "sp_init": 70000.0,
    })
    aic101 = _make_block(g, "PID", "AIC-101", X_P1, Y0 + 4*YS, {
        "GAIN": 0.05, "RESET": 300.0, "action": "reverse",
        "out_lo": -10.0, "out_hi": 10.0, "sp_init": 3.0,
    })
    aic102 = _make_block(g, "PID", "AIC-102", X_P1, Y0 + 5*YS, {
        "GAIN": 0.001, "RESET": 60.0, "action": "direct",
        "out_lo": 0.0, "out_hi": 20.0, "sp_init": 200.0,
    })
    pic101 = _make_block(g, "PID", "PIC-101", X_P1, Y0 + 6*YS, {
        "GAIN": 0.005, "RESET": 120.0, "action": "direct",
        "out_lo": 10.0, "out_hi": 100.0,
    })

    # ── Secondary controllers ──
    fic102 = _make_block(g, "PID", "FIC-102", X_P2, Y0 + 1*YS, {
        "GAIN": 0.8, "RESET": 8.0, "action": "reverse",
        "out_lo": 0.0, "out_hi": 100.0, "mode": "CASCADE",
    })
    fic103 = _make_block(g, "PID", "FIC-103", X_P2, Y0 + 3*YS, {
        "GAIN": 0.015, "RESET": 15.0, "action": "reverse",
        "out_lo": 0.0, "out_hi": 100.0,
    })

    # ── Cross-limiting: Lead-Lag on fuel demand for air ──
    lead_lag = _make_block(g, "LEAD_LAG", "XL_LEAD_LAG", X_MID, Y0 + 2*YS, {
        "lead_time": 15.0, "lag_time": 5.0,
    })

    # ── Air/fuel ratio (air = fuel × (base_ratio + O2 trim + CO correction)) ──
    ratio = _make_block(g, "RATIO", "AIR_RATIO", X_MID, Y0 + 3*YS, {
        "ratio": 1.15,
    })

    # ── Summer: base ratio + O2 trim + CO correction ──
    summer = _make_block(g, "SUMMER", "RATIO_TRIM", X_MID, Y0 + 4.5*YS, {
        "gain_1": 1.0, "gain_2": 1.0, "gain_3": 1.0, "bias": 1.15,
    })

    # ── MIN selector (FIC-102 vs PIC-102 override) ──
    minsel = _make_block(g, "MIN_SELECT", "FUEL_MIN_SEL", X_MID + 120, Y0 + 1*YS)

    # ── MAX selector (air demand: controller vs ratio floor) ──
    maxsel = _make_block(g, "MAX_SELECT", "AIR_MAX_SEL", X_MID + 120, Y0 + 3*YS)

    # ── AO blocks (combustion) ──
    ao_fuel   = _make_block(g, "AO", "AO_FUEL",   X_OUT, Y0 + 1*YS,
                            {"tag": "valve_fuel"})
    ao_air    = _make_block(g, "AO", "AO_AIR",    X_OUT, Y0 + 3*YS,
                            {"tag": "valve_air"})
    ao_damper = _make_block(g, "AO", "AO_DAMPER", X_OUT, Y0 + 6*YS,
                            {"tag": "valve_damper"})

    # ── Wiring: COT cascade TIC-101 → FIC-102 ──
    _wire(g, ai_cot,  "OUT", tic101, "IN")
    _wire(g, tic101,  "OUT", fic102, "CAS_IN")
    _wire(g, fic102,  "BKCAL_OUT", tic101, "BKCAL_IN", bkcal=True)
    _wire(g, ai_fuel, "OUT", fic102, "IN")

    # ── Wiring: Fuel MIN selector (FIC-102 vs PIC-102 pressure override) ──
    _wire(g, fic102,  "OUT", minsel, "IN1")
    _wire(g, ai_pfuel, "OUT", pic102, "IN")
    _wire(g, pic102,  "OUT", minsel, "IN2")
    _wire(g, minsel,  "OUT", ao_fuel, "CAS_IN")

    # ── Wiring: Cross-limiting lead-lag ──
    _wire(g, fic102, "OUT", lead_lag, "IN")

    # ── Wiring: O2 trim + CO safety → ratio adjustment ──
    _wire(g, ai_o2,  "OUT", aic101, "IN")
    _wire(g, ai_co,  "OUT", aic102, "IN")
    _wire(g, aic101, "OUT", summer, "IN1")
    _wire(g, aic102, "OUT", summer, "IN2")

    # ── Wiring: Air ratio from cross-limited fuel × adjusted ratio ──
    _wire(g, lead_lag, "OUT", ratio, "IN")
    _wire(g, summer,   "OUT", ratio, "RATIO_IN")
    _wire(g, ratio,    "OUT", fic103, "SP")
    _wire(g, ai_air,   "OUT", fic103, "IN")
    _wire(g, fic103,   "OUT", maxsel, "IN1")
    _wire(g, ratio,    "OUT", maxsel, "IN2")
    _wire(g, maxsel,   "OUT", ao_air, "CAS_IN")

    # ── Wiring: Draft pressure ──
    _wire(g, ai_draft, "OUT", pic101, "IN")
    _wire(g, pic101,   "OUT", ao_damper, "CAS_IN")

    # ================================================================
    # SECTION 2: PASS TEMPERATURE BALANCE (4 passes)
    # ================================================================
    Y_PASS = Y0 + 8 * YS   # start below combustion section

    _pass_tags = [
        ("A", "T_fluid_1", "feed_rate_pass_1", "valve_pass_1"),
        ("B", "T_fluid_2", "feed_rate_pass_2", "valve_pass_2"),
        ("C", "T_fluid_3", "feed_rate_pass_3", "valve_pass_3"),
        ("D", "T_fluid_4", "feed_rate_pass_4", "valve_pass_4"),
    ]

    for i, (suffix, pv_tag, flow_tag, valve_tag) in enumerate(_pass_tags):
        y = Y_PASS + i * 2 * YS   # 2 rows per pass (TIC + FIC)

        # AI: pass temperature
        ai_t = _make_block(g, "AI", f"AI_T{suffix}", X_IN, y,
                           {"tag": pv_tag})
        # AI: pass flow
        ai_f = _make_block(g, "AI", f"AI_F{suffix}", X_IN, y + YS,
                           {"tag": flow_tag})

        # TIC: pass temperature controller (primary, cascade master)
        tic = _make_block(g, "PID", f"TIC-101{suffix}", X_P1, y, {
            "GAIN": 0.015, "RESET": 90.0, "action": "direct",
            "out_lo": 0.0, "out_hi": 100.0, "sp_init": 700.0,
            "sp_lo": 672.0, "sp_hi": 728.0, "beta": 0.7,
        })

        # FIC: pass flow controller (secondary, cascade slave)
        fic = _make_block(g, "PID", f"FIC-101{suffix}", X_P2, y + YS, {
            "GAIN": 0.015, "RESET": 20.0, "action": "reverse",
            "out_lo": 5.0, "out_hi": 100.0, "mode": "CASCADE",
        })

        # AO: pass valve
        ao_pass = _make_block(g, "AO", f"AO_PASS{suffix}", X_OUT, y + YS,
                              {"tag": valve_tag})

        # Wiring: TIC → FIC cascade
        _wire(g, ai_t, "OUT", tic, "IN")
        _wire(g, tic,  "OUT", fic, "CAS_IN")
        _wire(g, fic,  "BKCAL_OUT", tic, "BKCAL_IN", bkcal=True)
        _wire(g, ai_f, "OUT", fic, "IN")
        _wire(g, fic,  "OUT", ao_pass, "CAS_IN")

    # ================================================================
    # SECTION 3: BMS SAFETY (NFPA 85/86)
    # ================================================================
    Y_BMS = Y_PASS + 9 * YS   # start below pass balance section

    # ── Input blocks ──
    di_draft = _make_block(g, "DI", "DI_DRAFT", X_IN, Y_BMS,
                           {"tag": "P_draft"})
    di_blower_cmd = _make_block(g, "DI", "DI_BLOWER_CMD", X_IN, Y_BMS + YS,
                                {"tag": "bms.blower_cmd"})
    di_start = _make_block(g, "DI", "DI_START", X_IN, Y_BMS + 5*YS,
                           {"tag": "bms.startup_cmd"})
    di_stop = _make_block(g, "DI", "DI_STOP", X_IN, Y_BMS + 6*YS,
                          {"tag": "bms.shutdown_cmd"})
    di_trip = _make_block(g, "DI", "DI_TRIP", X_IN, Y_BMS + 7*YS,
                          {"tag": "bms.trip_cmd"})

    # ── Blower ──
    blower = _make_block(g, "BLOWER", "FD_BLOWER", X_P1, Y_BMS,
                         {"start_delay": 3.0, "draft_setpoint": -20.0})
    _wire(g, di_blower_cmd, "OUT", blower, "START_CMD")
    _wire(g, di_draft, "OUT", blower, "DRAFT_PV")

    # ── Flame detectors ──
    di_pilot_ir = _make_block(g, "DI", "DI_PILOT_IR", X_IN, Y_BMS + 2*YS,
                              {"tag": "bms.flame_ir"})
    flame_pilot = _make_block(g, "FLAME_DET", "FLAME_PILOT", X_P1, Y_BMS + 2*YS,
                              {"num_sensors": 1, "min_votes": 1})
    _wire(g, di_pilot_ir, "OUT", flame_pilot, "SENSOR_1")

    di_main_uv = _make_block(g, "DI", "DI_MAIN_UV", X_IN, Y_BMS + 3*YS,
                             {"tag": "bms.flame_uv"})
    flame_main = _make_block(g, "FLAME_DET", "FLAME_MAIN", X_P1, Y_BMS + 3*YS,
                             {"num_sensors": 1, "min_votes": 1})
    _wire(g, di_main_uv, "OUT", flame_main, "SENSOR_1")

    # ── Burner Sequencer (NFPA 85/86) ──
    seq = _make_block(g, "BURNER_SEQ", "BMS_SEQ", X_MID, Y_BMS + 2*YS,
                      {"purge_time": 60.0, "ignition_trial": 10.0,
                       "stabilize_time": 15.0, "post_purge_time": 30.0})

    _wire(g, di_start, "OUT", seq, "START")
    _wire(g, di_stop,  "OUT", seq, "STOP")
    _wire(g, di_trip,  "OUT", seq, "TRIP")
    _wire(g, blower, "PROVEN", seq, "DRAFT_OK")
    _wire(g, flame_pilot, "FLAME_OK", seq, "PILOT_FLAME")
    _wire(g, flame_main,  "FLAME_OK", seq, "MAIN_FLAME")

    # ── Fuel valves (safety shutoff) ──
    pilot_vlv = _make_block(g, "FUEL_VALVE", "PILOT_SSOV", X_P2, Y_BMS + 3*YS,
                            {"stroke_time": 1.5})
    _wire(g, seq, "PILOT_FUEL_CMD", pilot_vlv, "OPEN_CMD")

    main_vlv = _make_block(g, "FUEL_VALVE", "MAIN_SSOV", X_P2, Y_BMS + 4*YS,
                           {"stroke_time": 2.0})
    _wire(g, seq, "MAIN_FUEL_CMD", main_vlv, "OPEN_CMD")

    # ── Output blocks ──
    do_blower = _make_block(g, "DO", "DO_BLOWER", X_OUT, Y_BMS,
                            {"tag": "bms.blower_cmd"})
    _wire(g, seq, "BLOWER_CMD", do_blower, "IN")

    do_igniter = _make_block(g, "DO", "DO_IGNITER", X_OUT, Y_BMS + 2*YS,
                             {"tag": "bms.igniter_on"})
    _wire(g, seq, "IGNITER_CMD", do_igniter, "IN")

    do_pilot_fuel = _make_block(g, "DO", "DO_PILOT_FUEL", X_OUT, Y_BMS + 3*YS,
                                {"tag": "bms.pilot_fuel_open"})
    _wire(g, pilot_vlv, "OPEN", do_pilot_fuel, "IN")

    do_main_fuel = _make_block(g, "DO", "DO_MAIN_FUEL", X_OUT, Y_BMS + 4*YS,
                               {"tag": "bms.main_fuel_open"})
    _wire(g, main_vlv, "OPEN", do_main_fuel, "IN")

    return g


# =====================================================================
# Generic Strategy Presets (Templates)
# =====================================================================

def build_single_loop() -> StrategyGraph:
    """Single Loop PID: AI -> PID -> AO.

    Simplest possible control loop -- one measurement, one controller,
    one output.  Good starting point for learning.
    """
    g = StrategyGraph("Single Loop PID")

    ai = _make_block(g, "AI", "AI_PV", -300, 0, {"tag": "COT"})
    pid = _make_block(g, "PID", "PID-1", 0, 0, {
        "GAIN": 1.0, "RESET": 60.0, "RATE": 0.0, "action": "reverse",
        "out_lo": 0.0, "out_hi": 1.0, "sp_init": 50.0,
    })
    ao = _make_block(g, "AO", "AO_OUT", 300, 0, {"tag": "valve_fuel"})

    _wire(g, ai, "OUT", pid, "IN")
    _wire(g, pid, "OUT", ao, "CAS_IN")

    return g


def build_cascade_control() -> StrategyGraph:
    """Cascade Control: AI_primary -> PID_primary -> PID_secondary <- AI_secondary -> AO.

    Primary (slow) controller sets SP of secondary (fast) controller.
    BKCAL wiring ensures bumpless transfer between modes.
    """
    g = StrategyGraph("Cascade Control")

    # AI blocks
    ai_primary = _make_block(g, "AI", "AI_PRIMARY", -400, -80, {"tag": "COT"})
    ai_secondary = _make_block(g, "AI", "AI_SECONDARY", -400, 80, {"tag": "fuel_rate"})

    # Controllers
    pid_primary = _make_block(g, "PID", "PID-PRIMARY", -80, -80, {
        "GAIN": 0.5, "RESET": 120.0, "RATE": 0.0, "action": "reverse",
        "out_lo": 0.0, "out_hi": 1.0, "sp_init": 50.0, "mode": "AUTO",
    })
    pid_secondary = _make_block(g, "PID", "PID-SECONDARY", 220, 80, {
        "GAIN": 1.0, "RESET": 10.0, "RATE": 0.0, "action": "reverse",
        "out_lo": 0.0, "out_hi": 1.0, "mode": "CASCADE",
    })

    # Output
    ao = _make_block(g, "AO", "AO_OUT", 520, 80, {"tag": "valve_fuel"})

    # Wiring
    _wire(g, ai_primary, "OUT", pid_primary, "IN")
    _wire(g, pid_primary, "OUT", pid_secondary, "CAS_IN")    # cascade link
    _wire(g, pid_secondary, "BKCAL_OUT", pid_primary, "BKCAL_IN", bkcal=True)
    _wire(g, ai_secondary, "OUT", pid_secondary, "IN")
    _wire(g, pid_secondary, "OUT", ao, "CAS_IN")

    return g


def build_ratio_control() -> StrategyGraph:
    """Ratio Control: AI_wild -> RATIO -> FIC <- AI_controlled, FIC -> AO.

    Maintains a fixed ratio between a wild (uncontrolled) flow and a
    controlled flow.  Common for air/fuel ratio.
    """
    g = StrategyGraph("Ratio Control")

    ai_wild = _make_block(g, "AI", "AI_WILD", -400, -60, {"tag": "fuel_rate"})
    ai_ctrl = _make_block(g, "AI", "AI_CONTROLLED", -400, 100, {"tag": "air_rate"})
    ratio = _make_block(g, "RATIO", "RATIO", -80, -60, {"ratio": 1.15, "bias": 0.0})
    fic = _make_block(g, "PID", "FIC-RATIO", 220, 100, {
        "GAIN": 0.8, "RESET": 15.0, "action": "reverse",
        "out_lo": 0.0, "out_hi": 1.0,
    })
    ao = _make_block(g, "AO", "AO_CTRL", 520, 100, {"tag": "valve_air"})

    _wire(g, ai_wild, "OUT", ratio, "IN")
    _wire(g, ratio, "OUT", fic, "SP")
    _wire(g, ai_ctrl, "OUT", fic, "IN")
    _wire(g, fic, "OUT", ao, "CAS_IN")

    return g


def build_override_select() -> StrategyGraph:
    """Override Select: Two PIDs -> MIN_SELECT -> AO.

    Two controllers compete; the one requesting the lower output wins.
    Used for constraint / override control (e.g., temperature vs pressure).
    """
    g = StrategyGraph("Override Select (Low)")

    ai_1 = _make_block(g, "AI", "AI_TEMP", -400, -100, {"tag": "COT"})
    ai_2 = _make_block(g, "AI", "AI_PRESS", -400, 100, {"tag": "P_fuel_gas"})

    pid_1 = _make_block(g, "PID", "TIC-OVR", -80, -100, {
        "GAIN": 0.5, "RESET": 120.0, "action": "reverse",
        "out_lo": 0.0, "out_hi": 1.0, "sp_init": 50.0,
    })
    pid_2 = _make_block(g, "PID", "PIC-OVR", -80, 100, {
        "GAIN": 1.0, "RESET": 60.0, "action": "direct",
        "out_lo": 0.0, "out_hi": 1.0, "sp_init": 70000.0,
    })

    minsel = _make_block(g, "MIN_SELECT", "LOW_SEL", 220, 0)
    ao = _make_block(g, "AO", "AO_OUT", 520, 0, {"tag": "valve_fuel"})

    _wire(g, ai_1, "OUT", pid_1, "IN")
    _wire(g, ai_2, "OUT", pid_2, "IN")
    _wire(g, pid_1, "OUT", minsel, "IN1")
    _wire(g, pid_2, "OUT", minsel, "IN2")
    _wire(g, minsel, "OUT", ao, "CAS_IN")

    return g


def build_split_range() -> StrategyGraph:
    """Split Range: PID -> two AOs with different output ranges.

    One controller drives two valves.  First AO takes 0-50% of PID output,
    second AO takes 50-100%.  Common for heating/cooling split.
    """
    g = StrategyGraph("Split Range")

    ai = _make_block(g, "AI", "AI_PV", -400, 0, {"tag": "COT"})
    pid = _make_block(g, "PID", "PID-SPLIT", -80, 0, {
        "GAIN": 1.0, "RESET": 60.0, "action": "reverse",
        "out_lo": 0.0, "out_hi": 1.0, "sp_init": 50.0,
    })

    # Two AO blocks for split range
    ao_1 = _make_block(g, "AO", "AO_HEAT", 220, -80, {"tag": "valve_fuel"})
    ao_2 = _make_block(g, "AO", "AO_COOL", 220, 80, {"tag": "valve_air"})

    _wire(g, ai, "OUT", pid, "IN")
    _wire(g, pid, "OUT", ao_1, "CAS_IN")
    _wire(g, pid, "OUT", ao_2, "CAS_IN")

    return g


# =====================================================================
# Registry of all schemes
# =====================================================================

SCHEME_BUILDERS = {
    "COMBINED": ("Combined Heater — Full Plant Control", build_combined_heater),
    "A": ("Scheme A — Direct Temperature Control", build_scheme_a),
    "B": ("Scheme B — Simple Cascade", build_scheme_b),
    "C": ("Scheme C — Cascade + Cross-Limiting", build_scheme_c),
    "D": ("Scheme D — Fired Duty Control", build_scheme_d),
    "E": ("Scheme E — Fuel Pressure Control", build_scheme_e),
    "BMS": ("BMS — Burner Management System", build_bms_strategy),
    "SINGLE": ("Single Loop PID", build_single_loop),
    "CASCADE": ("Cascade Control", build_cascade_control),
    "RATIO": ("Ratio Control", build_ratio_control),
    "OVERRIDE": ("Override Select (Low)", build_override_select),
    "SPLIT": ("Split Range", build_split_range),
}


def generate_all_presets(output_dir: Path | str | None = None) -> list[Path]:
    """Generate all preset strategy JSON files.

    Args:
        output_dir: Where to save. Defaults to STRATEGY_DIR.

    Returns:
        List of saved file paths.
    """
    out = Path(output_dir) if output_dir else STRATEGY_DIR
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for key, (label, builder) in SCHEME_BUILDERS.items():
        graph = builder()
        path = save_strategy(graph, out / f"King_Scheme_{key}.json")
        paths.append(path)
        log.info("Generated preset: %s -> %s", label, path)
    return paths


def get_preset_graph(scheme_key: str) -> StrategyGraph | None:
    """Build and return a preset strategy graph by key (A-E)."""
    entry = SCHEME_BUILDERS.get(scheme_key.upper())
    if entry is None:
        return None
    _, builder = entry
    return builder()
