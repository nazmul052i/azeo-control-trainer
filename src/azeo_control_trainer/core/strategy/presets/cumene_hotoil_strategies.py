"""Cumene hot oil heater control strategies — built as StrategyGraphs.

Generates the base regulatory control preset for the cumene plant hot oil
heater: supply temperature cascade, air-fuel cross-limiting with O2/CO
trim, draft control, surge drum level, back pressure control (PIC669),
and 7 HX flow controllers.

Total: 44 blocks, 44 wires (base regulatory).
"""
from __future__ import annotations

from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
from azeo_control_trainer.core.strategy.model.block_registry import registry

# Ensure all block types are registered
import azeo_control_trainer.core.strategy.blocks  # noqa: F401


# ── Layout constants ─────────────────────────────────────────────────
_X_AI   = -400   # AI blocks (inputs)
_X_PID1 = -120   # Primary controllers
_X_MID  =  100   # Mid-processing (selectors, ratios, lead-lag)
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
# Base Regulatory — Complete Cumene Hot Oil Heater Control
# =====================================================================

def build_base_regulatory() -> StrategyGraph:
    """Complete base regulatory for cumene hot oil heater.

    Groups:
      1. Heater Firing — TIC400 → AO_FUEL (direct), measured fuel flow
         → lead-lag → ratio × (base + O2/CO trims) → FIC411 air flow
      2. Draft Control — PIC410
      3. Surge Drum Level — LIC405
      4. HX Flow Controllers — FIC440..FIC446 (7 loops, EA453 standby excluded)

    Blocks: 14 AI, 13 PID, 1 LEAD_LAG, 1 RATIO, 1 SUMMER,
            12 AO = 44 total
    """
    g = StrategyGraph("Cumene Hot Oil — Base Regulatory")

    # ==================================================================
    # GROUP 1: HEATER FIRING
    # TIC400 → fuel valve (direct), air tracks measured fuel via ratio
    # ==================================================================

    # ── AI blocks (firing) ──
    ai_t_supply = _make_block(g, "AI", "AI_T_SUPPLY", _X_AI, _Y_START + 0 * _Y_STEP,
                              {"tag": "T_supply"})
    ai_fuel     = _make_block(g, "AI", "AI_FUEL", _X_AI, _Y_START + 1 * _Y_STEP,
                              {"tag": "fuel_rate"})
    ai_air      = _make_block(g, "AI", "AI_AIR", _X_AI, _Y_START + 3 * _Y_STEP,
                              {"tag": "air_rate"})
    ai_o2       = _make_block(g, "AI", "AI_O2", _X_AI, _Y_START + 4 * _Y_STEP,
                              {"tag": "O2_pct"})
    ai_co       = _make_block(g, "AI", "AI_CO", _X_AI, _Y_START + 5 * _Y_STEP,
                              {"tag": "CO_ppm"})

    # ── Primary controllers (firing) ──
    # TIC400: Supply temperature master (reverse — higher temp → lower fuel demand)
    tic400 = _make_block(g, "PID", "TIC400", _X_PID1, _Y_START + 0 * _Y_STEP, {
        "GAIN": 0.5, "RESET": 300.0, "RATE": 0.0, "action": "reverse",
        "out_lo": 0.0, "out_hi": 100.0, "sp_init": 665.0,
        "sp_lo": 600.0, "sp_hi": 700.0, "beta": 0.7,
        "pv_scale_lo": 500.0, "pv_scale_hi": 750.0,
    })

    # AIC410: O2 trim (reverse — higher O2 → decrease air/fuel ratio)
    # Output adds to base MSCFH ratio (12.16); range ±1.3 gives ±10% trim
    aic410 = _make_block(g, "PID", "AIC410", _X_PID1, _Y_START + 4 * _Y_STEP, {
        "GAIN": 1.0, "RESET": 300.0, "action": "reverse",
        "out_lo": -1.3, "out_hi": 1.3, "sp_init": 3.0,
        "sp_lo": 0.0, "sp_hi": 10.0,
        "pv_scale_lo": 0.0, "pv_scale_hi": 10.0,
    })

    # AIC411: CO correction (direct — higher CO → increase air/fuel ratio)
    # Output adds to MSCFH ratio; range 0-2 allows up to +16% air increase
    aic411 = _make_block(g, "PID", "AIC411", _X_PID1, _Y_START + 5 * _Y_STEP, {
        "GAIN": 0.1, "RESET": 60.0, "action": "direct",
        "out_lo": 0.0, "out_hi": 2.0, "sp_init": 200.0,
        "sp_lo": 0.0, "sp_hi": 1000.0,
        "pv_scale_lo": 0.0, "pv_scale_hi": 1000.0,
    })

    # ── Secondary controller (air flow) ──
    # FIC411: Air flow (reverse — higher air flow → close damper)
    # PV scale 0-3100 MSCFH; design ~1077 MSCFH.
    # GAIN scaled for PV_SPAN: old 0.5×30/100=0.15 → 0.005×3100/100=0.155
    fic411 = _make_block(g, "PID", "FIC411", _X_PID2, _Y_START + 3 * _Y_STEP, {
        "GAIN": 0.005, "RESET": 15.0, "action": "reverse",
        "out_lo": 0.0, "out_hi": 100.0,
        "sp_lo": 0.0, "sp_hi": 3100.0,
        "pv_scale_lo": 0.0, "pv_scale_hi": 3100.0,
    })

    # ── Cross-limiting: Lead-Lag on measured fuel flow for air ──
    # Air leads fuel on increase (15s lead, 5s lag)
    lead_lag = _make_block(g, "LEAD_LAG", "XL_LEAD_LAG",
                           _X_MID, _Y_START + 2.5 * _Y_STEP, {
                               "T_lead": 15.0, "T_lag": 5.0,
                           })

    # ── Air/fuel ratio (air_SP = fuel_flow × adjusted_ratio) ──
    # Both fuel and air are now in MSCFH.  Mass ratio is 18.83 kg_air/kg_fuel;
    # volumetric ratio = 18.83 × (MSCFH_air/kgs) / (MSCFH_fuel/kgs)
    #                  = 18.83 × 104 / 161 ≈ 12.16 MSCFH_air / MSCFH_fuel.
    ratio = _make_block(g, "RATIO", "AIR_RATIO",
                        _X_MID, _Y_START + 3 * _Y_STEP, {
                            "RATIO": 12.16,
                            "RATIO_LO": 9.0,
                            "RATIO_HI": 17.0,
                        })

    # ── Summer: base ratio + O2 trim + CO correction ──
    # OUT = bias + gain_1*IN1 + gain_2*IN2 = 12.16 + O2_trim + CO_correction
    summer = _make_block(g, "SUMMER", "RATIO_TRIM",
                         _X_MID, _Y_START + 4.5 * _Y_STEP, {
                             "G1": 1.0, "G2": 1.0, "BIAS": 12.16,
                         })

    # ── AO blocks (firing) — out_hi=100 for 0-100% valve range ──
    ao_fuel = _make_block(g, "AO", "AO_FUEL", _X_AO, _Y_START + 1 * _Y_STEP,
                          {"tag": "valve_fuel", "out_hi": 100.0})
    ao_air  = _make_block(g, "AO", "AO_AIR", _X_AO, _Y_START + 3 * _Y_STEP,
                          {"tag": "valve_air", "out_hi": 100.0})

    # ── Wiring: TIC400 → AO_FUEL (direct valve control) ──
    # TIC400 output (0-100%) drives fuel valve directly.  Measured fuel flow
    # feeds the ratio chain for air, keeping air/fuel ratio correct.
    _wire(g, ai_t_supply, "OUT", tic400, "IN")
    _wire(g, tic400,      "OUT", ao_fuel, "CAS_IN")
    _wire(g, ao_fuel, "BKCAL_OUT", tic400, "BKCAL_IN", bkcal=True)

    # ── Wiring: Cross-limiting lead-lag on measured fuel flow ──
    # Uses measured fuel (kg/s) so ratio output is in air flow units (kg/s)
    # matching FIC411 PV.  Lead-lag provides dynamic compensation.
    _wire(g, ai_fuel, "OUT", lead_lag, "IN")

    # ── Wiring: O2 trim + CO correction → ratio adjustment ──
    _wire(g, ai_o2,  "OUT", aic410, "IN")
    _wire(g, ai_co,  "OUT", aic411, "IN")
    _wire(g, aic410, "OUT", summer, "IN1")
    _wire(g, aic411, "OUT", summer, "IN2")

    # ── Wiring: Air ratio from cross-limited fuel × adjusted ratio ──
    # ratio.OUT = fuel_flow(kg/s) × ratio ≈ air_flow_SP(kg/s)
    _wire(g, lead_lag, "OUT", ratio,  "IN")
    _wire(g, summer,   "OUT", ratio,  "RATIO_IN")
    _wire(g, ratio,    "OUT", fic411, "SP")
    _wire(g, ai_air,   "OUT", fic411, "IN")
    _wire(g, fic411,   "OUT", ao_air, "CAS_IN")
    _wire(g, ao_air, "BKCAL_OUT", fic411, "BKCAL_IN", bkcal=True)

    # ==================================================================
    # GROUP 2: DRAFT CONTROL
    # ==================================================================

    ai_draft = _make_block(g, "AI", "AI_DRAFT",
                           _X_AI, _Y_START + 7 * _Y_STEP,
                           {"tag": "P_draft_inH2O"})

    # PIC410: Draft pressure (direct — higher pressure → open damper more)
    # PV in inH2O; normal range -0.5 to 0.0
    pic410 = _make_block(g, "PID", "PIC410",
                         _X_PID1, _Y_START + 7 * _Y_STEP, {
                             "GAIN": 2.0, "RESET": 120.0, "action": "direct",
                             "out_lo": 10.0, "out_hi": 100.0, "sp_init": -0.30,
                             "sp_lo": -1.0, "sp_hi": 0.0,
                             "pv_scale_lo": -1.0, "pv_scale_hi": 0.0,
                         })

    ao_damper = _make_block(g, "AO", "AO_DAMPER",
                            _X_AO, _Y_START + 7 * _Y_STEP,
                            {"tag": "valve_damper", "out_hi": 100.0})

    _wire(g, ai_draft, "OUT", pic410,    "IN")
    _wire(g, pic410,   "OUT", ao_damper, "CAS_IN")
    _wire(g, ao_damper, "BKCAL_OUT", pic410, "BKCAL_IN", bkcal=True)

    # ==================================================================
    # GROUP 3: SURGE DRUM LEVEL
    # ==================================================================

    ai_level = _make_block(g, "AI", "AI_LEVEL",
                           _X_AI, _Y_START + 8 * _Y_STEP,
                           {"tag": "surge_drum_level"})

    # LIC405: Surge drum level (direct — higher level → increase pump speed)
    # Very slow tuning — surge drum is a thermal expansion buffer
    # out_lo=0.5 ensures minimum circulation
    lic405 = _make_block(g, "PID", "LIC405",
                         _X_PID1, _Y_START + 8 * _Y_STEP, {
                             "GAIN": 0.5, "RESET": 600.0, "action": "direct",
                             "out_lo": 50.0, "out_hi": 100.0, "sp_init": 50.0,
                         })

    ao_pump = _make_block(g, "AO", "AO_PUMP",
                          _X_AO, _Y_START + 8 * _Y_STEP,
                          {"tag": "valve_pump", "out_hi": 100.0})

    _wire(g, ai_level, "OUT", lic405,  "IN")
    _wire(g, lic405,   "OUT", ao_pump, "CAS_IN")
    _wire(g, ao_pump, "BKCAL_OUT", lic405, "BKCAL_IN", bkcal=True)

    # ==================================================================
    # GROUP 5: BACK PRESSURE CONTROL (PIC669)
    # Maintains low-temp header pressure at 80 psig for proper flow
    # distribution to low-temp HXs (EA462, EA434, EA464).
    # Direct-acting: higher pressure → open BPV more → relieve pressure
    # ==================================================================

    ai_p_lo = _make_block(g, "AI", "AI_P_LO_HEADER",
                           _X_AI, _Y_START + 9 * _Y_STEP,
                           {"tag": "P_lo_header_psig"})

    pic669 = _make_block(g, "PID", "PIC669",
                          _X_PID1, _Y_START + 9 * _Y_STEP, {
                              "GAIN": 2.0, "RESET": 60.0, "action": "direct",
                              "out_lo": 10.0, "out_hi": 100.0, "sp_init": 80.0,
                              "sp_lo": 0.0, "sp_hi": 150.0,
                              "pv_scale_lo": 0.0, "pv_scale_hi": 150.0,
                          })

    ao_bpv = _make_block(g, "AO", "AO_BPV",
                          _X_AO, _Y_START + 9 * _Y_STEP,
                          {"tag": "valve_bpv", "out_hi": 100.0})

    _wire(g, ai_p_lo, "OUT", pic669, "IN")
    _wire(g, pic669,  "OUT", ao_bpv, "CAS_IN")
    _wire(g, ao_bpv, "BKCAL_OUT", pic669, "BKCAL_IN", bkcal=True)

    # ==================================================================
    # GROUP 4: HX FLOW CONTROLLERS (7 loops)
    # EA453 (standby, valve_hx_7) excluded — no FIC for standby HX
    # ==================================================================

    _hx_loops = [
        # (controller_name, hx_tag, pv_tag, mv_tag, design_flow_bpd, pv_hi)
        ("FIC440", "EA442", "flow_hx_0", "valve_hx_0", 15636,  50000),
        ("FIC441", "EA451", "flow_hx_1", "valve_hx_1", 35985, 100000),
        ("FIC442", "EA428", "flow_hx_2", "valve_hx_2",  8706,  30000),
        ("FIC443", "EA426", "flow_hx_3", "valve_hx_3",  5861,  20000),
        ("FIC444", "EA462", "flow_hx_4", "valve_hx_4", 68679, 150000),
        ("FIC445", "EA434", "flow_hx_5", "valve_hx_5",  1674,   5000),
        ("FIC446", "EA464", "flow_hx_6", "valve_hx_6", 33926, 100000),
    ]

    for i, (ctrl_name, _hx_tag, pv_tag, mv_tag, sp_init, pv_hi) in enumerate(_hx_loops):
        row = 10 + i   # rows 10..16

        ai_flow = _make_block(g, "AI", f"AI_{pv_tag.upper()}",
                              _X_AI, _Y_START + row * _Y_STEP,
                              {"tag": pv_tag})

        # gain_a = GAIN × PV_SPAN / OUT_SPAN.  Target gain_a ≈ 0.02 for
        # flow control → GAIN = 0.02 × OUT_SPAN / PV_SPAN = 2.0 / pv_hi
        hx_gain = 2.0 / float(pv_hi)

        fic = _make_block(g, "PID", ctrl_name,
                          _X_PID1, _Y_START + row * _Y_STEP, {
                              "GAIN": hx_gain, "RESET": 15.0, "action": "reverse",
                              "out_lo": 0.0, "out_hi": 100.0,
                              "sp_init": float(sp_init), "mode": "AUTO",
                              "sp_lo": 0.0, "sp_hi": float(pv_hi),
                              "pv_scale_lo": 0.0, "pv_scale_hi": float(pv_hi),
                          })

        ao_hx = _make_block(g, "AO", f"AO_{mv_tag.upper()}",
                            _X_AO, _Y_START + row * _Y_STEP,
                            {"tag": mv_tag, "out_hi": 100.0})

        _wire(g, ai_flow, "OUT", fic,   "IN")
        _wire(g, fic,     "OUT", ao_hx, "CAS_IN")
        _wire(g, ao_hx, "BKCAL_OUT", fic, "BKCAL_IN", bkcal=True)

    return g


# =====================================================================
# Predictive Control — Base Regulatory + Heat Balance Predictor
# =====================================================================

_X_PRED = 800   # predictor block column (right of AO)

def build_predictive_control() -> StrategyGraph:
    """Base regulatory + heat balance feedforward predictor.

    Extends the base regulatory strategy by adding:
    - 9 additional AI blocks for predictor inputs
    - 1 HEAT_BAL_PREDICTOR block (first-principles model + online
      data reconciliation + feedforward SP calculation)

    The predictor reads all 7 HX flows, 7 HX outlet temps, supply/return
    header temps, fired duty, pump flow, draft controller output, and fuel
    valve position.  It outputs IDEAL_SP which can be used by the APC
    engagement layer to cascade into TIC400 SP.
    """
    # Start from the base regulatory strategy
    g = build_base_regulatory()
    g.name = "Cumene Hot Oil — Predictive Control"

    # ── Additional AI blocks for predictor inputs ──
    pred_y = _Y_START + 17 * _Y_STEP  # below HX controllers

    ai_t_supply_pred = _make_block(g, "AI", "AI_T_SUPPLY_PRED",
                                   _X_AI, pred_y + 0 * _Y_STEP,
                                   {"tag": "T_supply"})
    ai_t_return = _make_block(g, "AI", "AI_T_RETURN",
                              _X_AI, pred_y + 1 * _Y_STEP,
                              {"tag": "T_return"})
    ai_t_lo_supply = _make_block(g, "AI", "AI_T_LO_SUPPLY",
                                 _X_AI, pred_y + 2 * _Y_STEP,
                                 {"tag": "T_lo_supply"})
    ai_q_fired = _make_block(g, "AI", "AI_Q_FIRED",
                             _X_AI, pred_y + 3 * _Y_STEP,
                             {"tag": "Q_fired"})
    ai_pump_flow = _make_block(g, "AI", "AI_PUMP_FLOW",
                               _X_AI, pred_y + 4 * _Y_STEP,
                               {"tag": "pump_flow_bpd"})
    ai_v_fuel = _make_block(g, "AI", "AI_V_FUEL",
                            _X_AI, pred_y + 5 * _Y_STEP,
                            {"tag": "valve_fuel"})

    # AI blocks for HX outlet temperatures (HX flows already have AIs
    # from base strategy; outlet temps need new AIs)
    ai_t_hx = []
    for i in range(7):
        ai = _make_block(g, "AI", f"AI_T_HX_{i}",
                         _X_AI - 200, pred_y + (6 + i) * _Y_STEP,
                         {"tag": f"T_hx_{i}"})
        ai_t_hx.append(ai)

    # ── Re-read the existing HX flow AIs from base strategy ──
    # (find them by instance_name)
    flow_tag_to_ai_name = {
        0: "AI_FLOW_HX_0", 1: "AI_FLOW_HX_1", 2: "AI_FLOW_HX_2",
        3: "AI_FLOW_HX_3", 4: "AI_FLOW_HX_4", 5: "AI_FLOW_HX_5",
        6: "AI_FLOW_HX_6",
    }

    # Create duplicate AI blocks for HX flows feeding predictor
    # (simpler than searching the graph for existing blocks)
    ai_flow_hx = []
    for i in range(7):
        ai = _make_block(g, "AI", f"AI_FLOW_HX_PRED_{i}",
                         _X_AI - 200, pred_y + (13 + i) * _Y_STEP,
                         {"tag": f"flow_hx_{i}"})
        ai_flow_hx.append(ai)

    # AI for draft controller output (PIC410.OUT)
    ai_pic410_out = _make_block(g, "AI", "AI_PIC410_OUT",
                                _X_AI, pred_y + 20 * _Y_STEP,
                                {"tag": "ctrl.PIC410.OUT"})

    # AI for plant production rate
    ai_prod_rate = _make_block(g, "AI", "AI_PROD_RATE",
                                _X_AI, pred_y + 21 * _Y_STEP,
                                {"tag": "production_rate"})

    # ── HEAT_BAL_PREDICTOR block ──
    pred = _make_block(g, "HEAT_BAL_PREDICTOR", "HBFF_PRED",
                       _X_PRED, pred_y + 6 * _Y_STEP, {
                           "ff_gain": 1.0,
                           "filter_tau": 30.0,
                           "tgt_nominal": 665.0,
                           "tgt_hi": 695.0,
                           "tgt_lo": 620.0,
                           "constraint_hi": 0.85,
                           "constraint_lo": 0.30,
                           "fuel_limit": 0.92,
                           "adapt_rate": 0.25,
                           "recon_enable": True,
                           "recon_period": 60.0,
                           "lambda_forget": 0.995,
                           "init_time": 30.0,
                           "dqdt_gain": 0.0,
                           "prod_ff_weight": 0.6,
                       })

    # ── Wire scalar inputs to predictor ──
    _wire(g, ai_t_supply_pred, "OUT", pred, "T_SUPPLY")
    _wire(g, ai_t_return,      "OUT", pred, "T_RETURN")
    _wire(g, ai_t_lo_supply,   "OUT", pred, "T_LO_SUPPLY")
    _wire(g, ai_q_fired,       "OUT", pred, "Q_FIRED")
    _wire(g, ai_pump_flow,     "OUT", pred, "PUMP_FLOW")
    _wire(g, ai_v_fuel,        "OUT", pred, "FUEL_VALVE")
    _wire(g, ai_pic410_out,    "OUT", pred, "CONSTRAINT_OP")
    _wire(g, ai_prod_rate,     "OUT", pred, "PROD_RATE")

    # ── Wire per-HX inputs ──
    for i in range(7):
        _wire(g, ai_flow_hx[i], "OUT", pred, f"FLOW_{i}")
        _wire(g, ai_t_hx[i],    "OUT", pred, f"TOUT_{i}")

    return g


# =====================================================================
# DMC Control — Base Regulatory + DMC Controller + APC Engagement
# =====================================================================

_X_DMC  = 800   # DMC controller column
_X_SPH  = 1100  # SP_HANDOFF column
_X_MVC  = 1350  # MV_CLAMP column

def build_dmc_control() -> StrategyGraph:
    """Base regulatory + DMC (Dynamic Matrix Control) supervisory layer.

    Extends base regulatory (44 blocks) with:
    - 17 AI blocks: 7 CV tags + 6 MV readbacks + 4 DV tags
    - 1 DMC_CONTROLLER block
    - 1 WATCHDOG + 1 SHED_LOGIC
    - 6 SP_HANDOFF + 6 MV_CLAMP

    Total: 44 + 32 = 76 blocks, ~50 wires.

    Wiring:
      AI_DMC_CVi.OUT   → DMC.CV_{i+1}
      AI_DMC_MVi.OUT   → DMC.MV_FB_{i+1}
      AI_DMC_DVi.OUT   → DMC.DV_{i+1}
      DMC.HEARTBEAT    → WD.HEARTBEAT
      DMC.ACTIVE       → WD.ENABLE, SHED.ENABLE
      WD.HEALTHY       → SHED.WATCHDOG_OK
      DMC.MV_SP_{i+1}  → SPH_{name}.APC_SP
      SHED.ACTIVE      → SPH_{name}.ACTIVE, MVC_{name}.ACTIVE
      SPH.TARGET_SP    → MVC.MV_IN
      MVC.MV_OK        → SHED.MV_STATUS_{i+1}
    """
    # Start from base regulatory
    g = build_base_regulatory()
    g.name = "Cumene Hot Oil — DMC Control"

    dmc_y = _Y_START + 17 * _Y_STEP  # below HX controllers

    # ── CV AI blocks (7) ──
    _cv_tags = [
        ("T_supply", "T_SUPPLY"),
        ("O2_pct", "O2_PCT"),
        ("CO_ppm", "CO_PPM"),
        ("P_draft_inH2O", "DRAFT"),
        ("surge_drum_level", "LEVEL"),
        ("Q_absorbed", "Q_ABS"),
        ("efficiency", "EFF"),
    ]
    ai_cvs = []
    for i, (tag, label) in enumerate(_cv_tags):
        ai = _make_block(g, "AI", f"AI_DMC_{label}",
                         _X_AI - 200, dmc_y + i * _Y_STEP,
                         {"tag": tag})
        ai_cvs.append(ai)

    # ── MV feedback AI blocks (6) ──
    # Read the PID SPs to track what DMC is commanding
    _mv_tags = [
        ("ctrl.TIC400.SP", "MV_TIC400"),
        ("ctrl.AIC410.SP", "MV_AIC410"),
        ("ctrl.PIC410.SP", "MV_PIC410"),
        ("ctrl.FIC441.SP", "MV_FIC441"),
        ("ctrl.FIC444.SP", "MV_FIC444"),
        ("ctrl.FIC446.SP", "MV_FIC446"),
    ]
    ai_mvs = []
    for i, (tag, label) in enumerate(_mv_tags):
        ai = _make_block(g, "AI", f"AI_DMC_{label}",
                         _X_AI - 200, dmc_y + (N_CV + i) * _Y_STEP,
                         {"tag": tag})
        ai_mvs.append(ai)

    # ── DV AI blocks (4) ──
    _dv_tags = [
        ("production_rate", "DV_PROD"),
        ("ambient_temp_offset", "DV_AMB"),
        ("fuel_rate", "DV_FUEL"),
        ("air_rate", "DV_AIR"),
    ]
    N_CV_local = 7
    N_MV_local = 6
    ai_dvs = []
    for i, (tag, label) in enumerate(_dv_tags):
        ai = _make_block(g, "AI", f"AI_DMC_{label}",
                         _X_AI - 200, dmc_y + (N_CV_local + N_MV_local + i) * _Y_STEP,
                         {"tag": tag})
        ai_dvs.append(ai)

    # ── DMC_CONTROLLER block ──
    dmc = _make_block(g, "DMC_CONTROLLER", "DMC_HOT_OIL",
                      _X_DMC, dmc_y + 3 * _Y_STEP, {
                          "sample_period": 60.0,
                          "prediction_horizon": 60,
                          "control_horizon": 5,
                          "model_horizon": 120,
                          "init_time": 10.0,
                          "filter_alpha": 0.3,
                          "ramp_rotation": 0.98,
                          "ss_slack_penalty": 1000.0,
                          # Use default FOPTD model (empty model_json)
                      })

    # ── Wire CV/MV/DV inputs to DMC ──
    for i, ai in enumerate(ai_cvs):
        _wire(g, ai, "OUT", dmc, f"CV_{i + 1}")
    for i, ai in enumerate(ai_mvs):
        _wire(g, ai, "OUT", dmc, f"MV_FB_{i + 1}")
    for i, ai in enumerate(ai_dvs):
        _wire(g, ai, "OUT", dmc, f"DV_{i + 1}")

    # ── WATCHDOG ──
    wd = _make_block(g, "WATCHDOG", "WD_DMC",
                     _X_DMC + 200, dmc_y + 0 * _Y_STEP, {
                         "timeout": 120.0,
                     })
    _wire(g, dmc, "HEARTBEAT", wd, "HEARTBEAT")
    _wire(g, dmc, "ACTIVE", wd, "ENABLE")

    # ── SHED_LOGIC ──
    shed = _make_block(g, "SHED_LOGIC", "SHED_DMC",
                       _X_DMC + 200, dmc_y + 2 * _Y_STEP, {
                           "partial_threshold": 2,
                       })
    _wire(g, dmc, "ACTIVE", shed, "ENABLE")
    _wire(g, wd, "HEALTHY", shed, "WATCHDOG_OK")

    # ── SP_HANDOFF + MV_CLAMP for each MV ──
    _mv_ctrl_tags = [
        ("TIC400", "400"),
        ("AIC410", "410"),
        ("PIC410", "P410"),
        ("FIC441", "441"),
        ("FIC444", "444"),
        ("FIC446", "446"),
    ]

    for i, (ctrl_tag, suffix) in enumerate(_mv_ctrl_tags):
        sph_y = dmc_y + (i + 7) * _Y_STEP  # below CV rows

        # SP_HANDOFF
        sph = _make_block(g, "SP_HANDOFF", f"SPH_{suffix}",
                          _X_SPH, sph_y, {
                              "controller_tag": ctrl_tag,
                              "ramp_rate": 1.0,
                              "hold_on_shed": True,
                          })

        # MV_CLAMP
        mvc = _make_block(g, "MV_CLAMP", f"MVC_{suffix}",
                          _X_MVC, sph_y, {
                              "controller_tag": ctrl_tag,
                          })

        # DMC → SP_HANDOFF
        _wire(g, dmc, f"MV_SP_{i + 1}", sph, "APC_SP")
        _wire(g, shed, "ACTIVE", sph, "ACTIVE")

        # SP_HANDOFF → MV_CLAMP
        _wire(g, sph, "TARGET_SP", mvc, "MV_IN")
        _wire(g, shed, "ACTIVE", mvc, "ACTIVE")

        # MV_CLAMP → SHED_LOGIC feedback
        _wire(g, mvc, "MV_OK", shed, f"MV_STATUS_{i + 1}")

    return g


# Use local constant for tag count reference
N_CV = 7

# ── Registry ──────────────────────────────────────────────────────────

HOTOIL_PRESET_BUILDERS = {
    "BASE": ("Cumene Hot Oil — Base Regulatory", build_base_regulatory),
    "PREDICTIVE": ("Cumene Hot Oil — Predictive Control", build_predictive_control),
    "DMC": ("Cumene Hot Oil — DMC Control", build_dmc_control),
}
