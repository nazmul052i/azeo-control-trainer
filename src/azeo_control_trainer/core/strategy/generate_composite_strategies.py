"""Generate composite-based strategies for all simulators.

Each control loop is encapsulated in a CompositeBlock, making the
top-level strategy view much cleaner (6-8 blocks instead of 30+).

Run:
    python -m azeo_control_trainer.core.strategy.generate_composite_strategies
"""
from __future__ import annotations

import json
import os
import sys

# Ensure src is on path
from azeo_control_trainer.config.paths import src_dir, strategies_dir as _strategies_dir_fn
_src = str(src_dir())
if _src not in sys.path:
    sys.path.insert(0, _src)

from azeo_control_trainer.core.strategy.blocks.composite_templates import (
    single_pid_loop, cascade_pid_loop,
)
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph

STRATEGIES_DIR = str(_strategies_dir_fn())


def _save(graph: StrategyGraph, subdir: str, filename: str):
    out_dir = os.path.join(STRATEGIES_DIR, subdir)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, filename)
    data = graph.to_dict()
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    n_blocks = len(data["blocks"])
    n_wires = len(data["wires"])
    print(f"  Saved {path}  ({n_blocks} blocks, {n_wires} wires)")


# ═══════════════════════════════════════════════════════════════════
#  Distillation — 6 composite loops + 2 monitoring AIs
# ═══════════════════════════════════════════════════════════════════

def generate_distillation():
    print("Distillation Column Composite Strategy")
    g = StrategyGraph("Distillation Composite Control")

    # 1. TDC Reflux Cascade: dT_top -> TDC_TOP -> FIC_REFL -> mv_reflux
    c1 = cascade_pid_loop(
        instance_name="TDC_Reflux",
        outer_pv_tag="dT_top",       inner_pv_tag="LT",
        mv_tag="mv_reflux",
        outer_pid_name="TDC_TOP",    inner_pid_name="FIC_REFL",
        outer_kp=0.5, outer_ti=600,
        inner_kp=0.5, inner_ti=60,
        outer_action="direct",       inner_action="reverse",
        outer_pv_lo=0, outer_pv_hi=90,      outer_sp_init=1.99,
        inner_pv_lo=0, inner_pv_hi=20000,   inner_sp_init=12971.0,
        mv_lo=0, mv_hi=100,
        outer_pv_unit="degF",        inner_pv_unit="BPD",
    )
    c1.x, c1.y = 100, -400
    g.add_block(c1)

    # 2. LIC Accumulator Cascade: M_cond -> LIC_ACC -> FIC_DIST -> mv_distillate
    c2 = cascade_pid_loop(
        instance_name="LIC_Accumulator",
        outer_pv_tag="M_cond",       inner_pv_tag="D",
        mv_tag="mv_distillate",
        outer_pid_name="LIC_ACC",    inner_pid_name="FIC_DIST",
        outer_kp=0.2, outer_ti=600,
        inner_kp=0.5, inner_ti=60,
        outer_action="direct",       inner_action="reverse",
        outer_pv_lo=0, outer_pv_hi=100,     outer_sp_init=50.0,
        inner_pv_lo=0, inner_pv_hi=5000,    inner_sp_init=2838.0,
        mv_lo=0, mv_hi=100,
        outer_pv_unit="%",           inner_pv_unit="BPD",
    )
    c2.x, c2.y = 100, -200
    g.add_block(c2)

    # 3. LIC Bottoms Cascade: M_bot -> LIC_BOT -> FIC_BTMS -> mv_bottoms
    c3 = cascade_pid_loop(
        instance_name="LIC_Bottoms",
        outer_pv_tag="M_bot",        inner_pv_tag="B",
        mv_tag="mv_bottoms",
        outer_pid_name="LIC_BOT",    inner_pid_name="FIC_BTMS",
        outer_kp=0.2, outer_ti=600,
        inner_kp=0.5, inner_ti=60,
        outer_action="direct",       inner_action="reverse",
        outer_pv_lo=0, outer_pv_hi=100,     outer_sp_init=50.0,
        inner_pv_lo=0, inner_pv_hi=5000,    inner_sp_init=2162.0,
        mv_lo=0, mv_hi=100,
        outer_pv_unit="%",           inner_pv_unit="BPD",
    )
    c3.x, c3.y = 100, 0
    g.add_block(c3)

    # 4. FIC Feed: F -> FIC_FEED -> mv_feed
    c4 = single_pid_loop(
        instance_name="FIC_Feed",
        pv_tag="F", mv_tag="mv_feed",
        pid_name="FIC_FEED",
        kp=1.0, ti=20, action="reverse",
        pv_lo=0, pv_hi=10000, sp_init=5000.0,
        mv_lo=0, mv_hi=100,
        pv_unit="BPD",
    )
    c4.x, c4.y = 100, 200
    g.add_block(c4)

    # 5. PIC Column: P_psig -> PIC_COL -> mv_cw
    c5 = single_pid_loop(
        instance_name="PIC_Column",
        pv_tag="P_psig", mv_tag="mv_cw",
        pid_name="PIC_COL",
        kp=5.0, ti=60, action="reverse",
        pv_lo=-5, pv_hi=30, sp_init=0.0,
        mv_lo=0, mv_hi=100,
        pv_unit="psig", pv_ftime=4.0,
    )
    c5.x, c5.y = 100, 400
    g.add_block(c5)

    # 6. TDC Boilup Cascade: dT_bot -> TDC_BOT -> TIC_REB -> mv_boilup
    c6 = cascade_pid_loop(
        instance_name="TDC_Boilup",
        outer_pv_tag="dT_bot",       inner_pv_tag="T_reboiler",
        mv_tag="mv_boilup",
        outer_pid_name="TDC_BOT",    inner_pid_name="TIC_REB",
        outer_kp=0.5, outer_ti=600,
        inner_kp=3.0, inner_ti=180,
        outer_action="reverse",      inner_action="reverse",
        outer_pv_lo=0, outer_pv_hi=90,      outer_sp_init=2.24,
        inner_pv_lo=50, inner_pv_hi=150,    inner_sp_init=93.0,
        mv_lo=0, mv_hi=100,
        outer_pv_unit="degF",        inner_pv_unit="degF",
        inner_pv_ftime=4.0,
    )
    c6.x, c6.y = 100, 600
    g.add_block(c6)

    _save(g, "distillation", "Distillation_Composite_Control.json")


# ═══════════════════════════════════════════════════════════════════
#  Tennessee Eastman — 5 composite loops
# ═══════════════════════════════════════════════════════════════════

def generate_te():
    print("Tennessee Eastman Composite Strategy")
    g = StrategyGraph("TE Composite Regulatory")

    # 1. TIC109: Reactor Temperature -> CW valve
    c1 = single_pid_loop(
        instance_name="TIC109_Reactor_Temp",
        pv_tag="xmeas_9", mv_tag="xmv_10",
        pid_name="TIC109",
        kp=8.0, ti=450, action="reverse",
        pv_lo=100, pv_hi=180, sp_init=120.4,
        mv_lo=0, mv_hi=100,
        pv_unit="C", pv_ftime=4.0,
    )
    c1.x, c1.y = 100, -400
    g.add_block(c1)

    # 2. TIC111: Separator Temperature -> Condenser CW
    c2 = single_pid_loop(
        instance_name="TIC111_Separator_Temp",
        pv_tag="xmeas_11", mv_tag="xmv_11",
        pid_name="TIC111",
        kp=4.0, ti=900, action="reverse",
        pv_lo=50, pv_hi=150, sp_init=80.1,
        mv_lo=0, mv_hi=100,
        pv_unit="C", pv_ftime=4.0,
    )
    c2.x, c2.y = 100, -200
    g.add_block(c2)

    # 3. LIC112: Separator Level -> Separator Liquid
    c3 = single_pid_loop(
        instance_name="LIC112_Separator_Level",
        pv_tag="xmeas_12", mv_tag="xmv_7",
        pid_name="LIC112",
        kp=2.0, ti=300, action="direct",
        pv_lo=0, pv_hi=100, sp_init=50.0,
        mv_lo=0, mv_hi=100,
        pv_unit="%",
    )
    c3.x, c3.y = 100, 0
    g.add_block(c3)

    # 4. LIC115: Stripper Level -> Stripper Product
    c4 = single_pid_loop(
        instance_name="LIC115_Stripper_Level",
        pv_tag="xmeas_15", mv_tag="xmv_8",
        pid_name="LIC115",
        kp=2.0, ti=300, action="direct",
        pv_lo=0, pv_hi=100, sp_init=50.0,
        mv_lo=0, mv_hi=100,
        pv_unit="%",
    )
    c4.x, c4.y = 100, 200
    g.add_block(c4)

    # 5. PIC107: Reactor Pressure -> Purge
    c5 = single_pid_loop(
        instance_name="PIC107_Reactor_Pressure",
        pv_tag="xmeas_7", mv_tag="xmv_6",
        pid_name="PIC107",
        kp=1.0, ti=120, action="direct",
        pv_lo=2500, pv_hi=3100, sp_init=2705,
        mv_lo=0, mv_hi=100,
        pv_unit="kPa", pv_ftime=4.0,
    )
    c5.x, c5.y = 100, 400
    g.add_block(c5)

    _save(g, "te", "TE_Composite_Regulatory.json")


# ═══════════════════════════════════════════════════════════════════
#  Fired Heater — 1 composite loop (COT control)
# ═══════════════════════════════════════════════════════════════════

def generate_heater():
    print("Fired Heater Composite Strategy")
    g = StrategyGraph("Heater Composite COT Control")

    # TIC101: COT -> Fuel Valve
    # Note: Heater AO uses 0-1 range (not 0-100)
    c1 = single_pid_loop(
        instance_name="TIC101_COT",
        pv_tag="COT", mv_tag="valve_fuel",
        pid_name="TIC101",
        kp=1.5, ti=120, td=12,
        action="reverse",
        pv_lo=473, pv_hi=773, sp_init=623,
        mv_lo=0, mv_hi=1,
        pv_unit="K", pv_ftime=4.0,
        structure="pid_on_error",
    )
    c1.x, c1.y = 100, 0
    g.add_block(c1)

    _save(g, "heater", "Heater_Composite_COT.json")


# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("Generating composite strategies...")
    print()
    generate_distillation()
    print()
    generate_te()
    print()
    generate_heater()
    print()
    print("Done.")
