#!/usr/bin/env python3
"""The loop-shaping layer: Whitehouse-parity algorithms wrapped AROUND
the unchanged PID block.

Pure math first (schedulers, titration slope, predictor, syntheses),
then the strategy wiring on the real plant: algorithm selection,
anti-surge formulation switchover, deadtime compensation on a column
analyser master, and persistence round-trip.
"""

from __future__ import annotations

import logging
import math
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

logging.disable(logging.CRITICAL)

from azeoplant.control import shaping                        # noqa: E402
from azeoplant.control.pid import PID, Mode                  # noqa: E402
from azeoplant.control.strategy import ControlSystem         # noqa: E402
from azeoplant.core.engine import SimulationEngine           # noqa: E402
from azeoplant.io import VirtualIOBus                        # noqa: E402
from azeoplant.models.flowsheet import Flowsheet, TagDatabase  # noqa: E402

FAILURES = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global FAILURES
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" +
          (f"  {detail}" if detail else ""))
    if not ok:
        FAILURES += 1


def fake_pid(pv, sp, span=100.0, eu0=0.0):
    return SimpleNamespace(pv=pv, sp_wrk=sp, pv_span=span, pv_eu0=eu0)


def math_checks() -> None:
    print("scheduler math")
    es = shaping.error_squared(2.0, mix_c=0.9)
    check("error-squared at SP is the mixed-down gain",
          abs(es(fake_pid(50.0, 50.0)) - 0.2) < 1e-9)
    check("error-squared at full error is the base gain",
          abs(es(fake_pid(150.0, 50.0)) - 2.0) < 1e-9)

    gp = shaping.gap(2.0, gap_lo=5.0, gap_hi=5.0, k_gap=0.1)
    check("gap gain inside the band", abs(gp(fake_pid(52.0, 50.0)) - 0.2) < 1e-9)
    check("gap gain outside the band", abs(gp(fake_pid(60.0, 50.0)) - 2.0) < 1e-9)

    bd = shaping.banded([(1.5, 0.1), (3.0, 0.5), (math.inf, 2.0)])
    check("banded picks inner/middle/outer",
          bd(fake_pid(51.0, 50.0)) == 0.1 and bd(fake_pid(52.0, 50.0)) == 0.5
          and bd(fake_pid(70.0, 50.0)) == 2.0)

    s7 = shaping.titration_slope(7.0)
    s10 = shaping.titration_slope(10.2)
    check("titration slope is 1 at neutrality, ~0.1 at pH 10.2",
          abs(s7 - 1.0) < 1e-9 and abs(s10 - 0.1) < 1e-3)
    check("titration slope is symmetric",
          abs(shaping.titration_slope(4.0)
              - shaping.titration_slope(10.0)) < 1e-12)

    sched = shaping.slope_scheduled(shaping.titration_slope, 1.0)
    g_at_7 = sched(fake_pid(7.0, 7.0, span=14.0))
    g_at_4 = sched(fake_pid(4.0, 7.0, span=14.0))
    check("slope scheduling raises gain where the curve flattens",
          g_at_4 > 5.0 * g_at_7)

    g, r = shaping.dahlin_tuning(shaping.FOPDT(2.0, 10.0, 60.0), 10.0)
    check("dahlin synthesis: tau/(K(lam+theta)), reset=tau",
          abs(g - 60.0 / (2.0 * 20.0)) < 1e-9 and r == 60.0)


def inferential_checks() -> None:
    print("inferential builder")

    class T:
        def __init__(self, v):
            self.value = v

    db = {"X": T(2.0), "A": T(6.0), "B": T(3.0), "AN": T(0.49)}
    inf = shaping.Inferential(
        [("const", -0.3), ("X", 0.5), ("A/B", 0.0)], transform="sqrt",
        bias_tag="AN", bias_filter=0.9)
    v = inf.value(db, 0.2)
    check("linear combo, ratio term and sqrt transform",
          abs(v - 0.49) < 1e-9, f"{v:.4f}")
    for _ in range(50):
        inf.value(db, 0.2)
    db["AN"].value = 0.69          # the analyser updates with a mismatch
    inf.value(db, 0.2)
    check("analyser update nudges the filtered bias",
          0.005 < inf.bias < 0.05, f"bias {inf.bias:.4f}")
    db["AN"].value = 0.69          # no change: bias must hold
    b = inf.bias
    inf.value(db, 0.2)
    check("a quiet analyser leaves the bias alone",
          abs(inf.bias - b) < 1e-12)


def predictor_checks() -> None:
    print("smith predictor on a synthetic FOPDT plant")
    K, THETA, TAU, DT = 1.5, 24.0, 40.0, 0.2

    def run(with_predictor: bool) -> float:
        # aggressive tuning only the predictor can afford on this
        # deadtime: lambda well below theta
        g, r = TAU / (K * 8.0), TAU
        pid = PID(name="TEST", gain=g, reset=r,
                  pv_eu0=0.0, pv_eu100=100.0)
        pid.sp = 50.0
        pid.set_mode(Mode.AUTO)
        pred = (shaping.SmithPredictor(shaping.FOPDT(K, THETA, TAU))
                if with_predictor else None)
        nbuf = int(THETA / DT)
        buf = [0.0] * nbuf
        y, iae = 0.0, 0.0
        for i in range(int(1200 / DT)):
            pv = y
            if pred is not None:
                pvp = pred.correct(pv, pid.out, DT)
                pv = pvp
            pid.step(DT, pv, True)
            buf.append(pid.out)
            u = buf.pop(0)
            y += (K * u - y) / TAU * DT
            iae += abs(50.0 - y) * DT
        return iae

    iae_plain = run(False)
    iae_smith = run(True)
    check("predictor beats the same tuning on the raw PV",
          iae_smith < 0.5 * iae_plain,
          f"IAE {iae_smith:.0f} vs {iae_plain:.0f}")
    # measured: settles monotonically at SP by t~240s, flat after (the
    # block's PV filter and SP structure stretch the approach a little)
    check("compensated loop actually settles", iae_smith < 3800.0,
          f"IAE {iae_smith:.0f}")


def plant_checks() -> None:
    print("strategy wiring on the real plant")
    db = TagDatabase()
    fs = Flowsheet(db)
    eng = SimulationEngine(db, fs, dt=0.1)
    snap = ROOT / "snapshots" / "lined_up.json"
    if snap.exists():
        eng.load_snapshot(snap)
    bus = VirtualIOBus(db)
    cs = ControlSystem(db, bus)
    cs.seed_from_plant()
    eng.controller = cs
    eng.post_step_hooks.append(bus.tick)
    eng.post_step_hooks.append(cs.step)

    def run(steps: int) -> None:
        for _ in range(steps):
            eng._execute_step()

    g = lambda n: float(db[n].value)   # noqa: E731
    run(100)

    # ---- algorithm selection
    lic = cs.loops["LIC-1001"].pid
    base_gain = lic.gain
    cs.set_algorithm("LIC-1001", "error_squared", mix_c=0.9)
    run(20)
    e_pct = abs(lic.pv - lic.sp_wrk) / lic.pv_span * 100.0
    expect = base_gain * (0.1 + 0.9 * min(e_pct, 100.0) / 100.0)
    check("error-squared schedules the block gain each scan",
          abs(lic.gain - expect) < 0.05 * base_gain + 1e-6,
          f"gain {lic.gain:.4f} expect {expect:.4f}")
    cs.set_algorithm("LIC-1001", "linear")
    check("linear restores the base gain and clears the record",
          abs(lic.gain - base_gain) < 1e-9
          and "LIC-1001" not in cs._shaping)

    cs.set_algorithm("AIC-8001", "slope", source="u800_titration",
                     kp_target=57.0)
    run(20)
    aic8 = cs.loops["AIC-8001"].pid
    check("pH slope strategy is live and finite",
          math.isfinite(aic8.gain) and aic8.gain > 0.0,
          f"gain {aic8.gain:.2f} at pH {aic8.pv:.2f}")
    cs.set_algorithm("AIC-8001", "linear")

    # ---- anti-surge formulation switchover
    uic = cs.loops["UIC-2001"].pid
    m_before = g("UY-2001")
    cs.set_asc_formulation("flow_speed")
    check("flow-speed frame re-ranges the block",
          uic.pv_eu0 == -10.0 and uic.pv_eu100 == 30.0
          and uic.sp > 0.0)
    run(300)
    check("compressor stays clear of surge in the flow-speed frame",
          g("UY-2001") > 0.0 and g("ST-2001") > 5000.0,
          f"margin {g('UY-2001'):.1f}% speed {g('ST-2001'):.0f}")
    cs.set_asc_formulation("flow_dp")
    run(300)
    check("compressor stays clear of surge in the flow-dp frame",
          g("UY-2001") > 0.0, f"margin {g('UY-2001'):.1f}%")
    cs.set_asc_formulation("margin")
    check("margin frame restores range, SP and PV source",
          uic.pv_eu0 == -50.0 and uic.pv_eu100 == 150.0
          and abs(uic.sp - 15.0) < 1e-6
          and "UIC-2001" not in cs._pv_fn)
    run(200)
    check("plant unharmed by the formulation tour",
          g("UY-2001") > 0.0 and abs(g("UY-2001") - m_before) < 60.0,
          f"margin {g('UY-2001'):.1f} was {m_before:.1f}")

    # ---- deadtime compensation on a column analyser master
    a51 = cs.loops["AIC-5001"].pid
    g0, r0 = a51.gain, a51.reset
    cs.set_compensator("AIC-5001", "smith", k=1.2, theta=90.0, tau=300.0)
    run(300)
    check("smith predictor runs on AIC-5001 without upsetting it",
          cs.loops["AIC-5001"].predictor is not None
          and math.isfinite(a51.pv) and 0.0 <= a51.out <= 100.0)
    cs.set_compensator("AIC-5001", "none")
    check("compensator removal restores base tuning",
          a51.gain == g0 and a51.reset == r0
          and cs.loops["AIC-5001"].predictor is None)

    # ---- the seven anti-surge formulations
    for form in ("dp", "pd", "power_flow"):
        cs.set_asc_formulation(form)
        run(200)
        check(f"{form} formulation runs and the machine stays clear",
              math.isfinite(uic.pv) and g("UY-2001") > 0.0,
              f"pv {uic.pv:.1f} margin {g('UY-2001'):.0f}%")
    cs.set_asc_formulation("margin")
    run(100)

    # ---- T2 control-scheme menu
    d6, r6 = cs.loops["FIC-6006"], cs.loops["FIC-6001"]
    tic6 = cs.loops["TIC-6001"].pid
    check("feedforward anchors adopted at seed",
          "tf" in cs._ff_anchors.get(6, {})
          and "z" in cs._ff_anchors.get(6, {}))
    cs.set_column_scheme("material")
    run(300)
    check("material-balance scheme holds T2",
          d6.master == "TIC-6001" and r6.master == "LIC-6001"
          and 25.0 < g("LT-6001") < 75.0 and g("PT-6001") < 8.0,
          f"drum {g('LT-6001'):.1f} P {g('PT-6001'):.2f}")
    cs.set_column_scheme("ryskamp")
    run(300)
    ld_dem = tic6.out / 100.0 * cs.RYSKAMP_RMAX * g("FT-6003")
    check("ryskamp: reflux rides the draw at the commanded ratio",
          cs._ryskamp and abs(g("FT-6002") - ld_dem) < 8.0
          and 25.0 < g("LT-6001") < 75.0,
          f"reflux {g('FT-6002'):.1f} demand {ld_dem:.1f}")
    cs.set_column_scheme("energy")
    run(300)
    check("energy scheme restored bumplessly",
          d6.master == "LIC-6001" and r6.master == "TIC-6001"
          and not cs._ryskamp and 25.0 < g("LT-6001") < 75.0
          and g("PT-6001") < 8.0,
          f"drum {g('LT-6001'):.1f} P {g('PT-6001'):.2f}")

    # ---- adaptive predictor model
    pred = shaping.SmithPredictor(shaping.FOPDT(1.0, 100.0, 60.0))
    pred.correct(0.0, 0.0, 0.2)
    pred.set_theta(105.0)
    check("small theta drift updates the model in place",
          pred._dead is not None and abs(pred.model.theta - 105.0) < 1e-9)
    pred.set_theta(200.0)
    check("large theta shift rebuilds the delay line",
          pred._dead is None and abs(pred.model.theta - 200.0) < 1e-9)

    # ---- plant-wide quality VPC and the additive ratio
    qic = cs.loops["QIC-5001"].pid
    rc = cs.loops["RC-4001"].pid
    f4 = cs.loops["FIC-4002"].pid
    check("VPC seeded balanced: mid-scale out, additive at as-found",
          abs(qic.out - 50.0) < 5.0 and abs(qic.sp - 50.0) < 1e-6
          and abs(f4.sp_wrk - rc.out) < 2.0,
          f"qic {qic.out:.1f} ratio-out {rc.out:.1f} "
          f"additive {g('FT-4005'):.1f}")
    run(300)
    check("additive rides the charge through the ratio station",
          abs(g("FT-4005") - rc.out) < 4.0 and g("FT-4005") > 5.0,
          f"additive {g('FT-4005'):.1f} demand {rc.out:.1f}")

    # ---- compressor MV menu
    sic = cs.loops["SIC-2001"].pid
    pic = cs.loops["PIC-2001"]
    spd0 = g("ST-2001")
    cs.set_compressor_mv("vanes")
    run(300)
    check("vanes MV: master re-pointed, speed parked in AUTO",
          pic.split and pic.split[0][0] == "GV-2001"
          and sic.target_mode == Mode.AUTO
          and abs(g("ST-2001") - spd0) < 400.0
          and abs(g("PT-2001") - 9.0) < 1.0,
          f"suction {g('PT-2001'):.2f} speed {g('ST-2001'):.0f} "
          f"vanes {g('GT-2001'):.1f}")
    cs.set_compressor_mv("suction")
    run(200)
    check("suction-valve MV holds the suction pressure",
          pic.out_tag == "FCV-2002" and abs(g("PT-2001") - 9.0) < 1.0,
          f"suction {g('PT-2001'):.2f} valve {g('FCV-2002'):.0f}")
    cs.set_compressor_mv("speed")
    run(300)
    check("speed MV restored: cascade back, plant recovered",
          not pic.out_tag and not pic.split
          and sic.target_mode == Mode.CAS
          and abs(g("PT-2001") - 9.0) < 1.0
          and g("GT-2001") < 2.0 and g("FCV-2002") > 95.0,
          f"suction {g('PT-2001'):.2f} vanes {g('GT-2001'):.1f}")

    # ---- boiler modes, duty trim and swell compensation
    bic = cs.loops["BIC-7001"].pid
    check("duty trim parked shut in pressure mode",
          bic.sp == 0.0 and g("FT-7005") < 5.0 and g("FT-7006") < 2.0,
          f"oil {g('FT-7005'):.0f} B2 {g('FT-7006'):.1f}")
    duty_now = cs._pv_fn["BIC-7001"](db)
    cs.set_boiler_mode("baseload", duty_mw=duty_now + 3.0)
    run(1200)
    check("baseload: oil trims the duty up, header rides bounded",
          g("FT-7005") > 100.0 and 33.0 < g("PT-7002") < 41.5,
          f"oil {g('FT-7005'):.0f} kg/h  hdr {g('PT-7002'):.1f}  "
          f"B2 {g('FT-7006'):.1f}")
    cs.set_boiler_mode("pressure")
    run(900)
    check("pressure mode restored: oil back out, header recovered",
          g("FT-7005") < 60.0 and g("PT-7002") > 34.0,
          f"oil {g('FT-7005'):.0f} hdr {g('PT-7002'):.1f}")

    cs.set_level_compensation(6.0)
    run(200)
    check("swell-compensated level runs and holds the drum",
          "LIC-7001" in cs._pv_fn and abs(g("LT-7001")) < 120.0,
          f"level {g('LT-7001'):.0f} mm")
    cs.set_level_compensation(0.0)
    check("compensation removal restores the raw transmitter",
          "LIC-7001" not in cs._pv_fn)

    # ---- output conditioning
    fic = cs.loops["FIC-8001"]
    cs.set_output_conditioning("FIC-8001", 0.8)
    run(5)
    fcv = g(fic.out_tag)
    exp = 100.0 * 0.2 * fic.pid.out / (100.0 - 0.8 * fic.pid.out)
    check("output conditioning shapes the valve, not the block",
          abs(fcv - exp) < 1.5 and fic.pid.out - fcv > 5.0,
          f"valve {fcv:.1f} expect {exp:.1f} raw {fic.pid.out:.1f}")
    cs.set_output_conditioning("FIC-8001", 0.0)
    run(60)
    check("conditioning removal restores the direct write",
          abs(g(fic.out_tag) - fic.pid.out) < 1.5)
    cs.set_output_conditioning("FIC-8001", 0.7)
    st = cs.capture_state()
    cs.set_output_conditioning("FIC-8001", 0.0)
    cs.apply_state(st)
    check("conditioning survives capture/apply",
          abs(cs.loops["FIC-8001"].out_cond - 0.7) < 1e-9)
    cs.set_output_conditioning("FIC-8001", 0.0)

    # ---- persistence round-trip
    cs.set_algorithm("LIC-1001", "gap", gap_lo=4.0, gap_hi=4.0, k_gap=0.15)
    cs.set_asc_formulation("flow_speed")
    cs.set_compensator("AIC-5001", "dahlin", k=1.2, theta=90.0,
                       tau=300.0, lam=45.0)
    cs.set_compressor_mv("discharge")
    state = cs.capture_state()
    cs.set_compressor_mv("speed")
    cs2 = ControlSystem(db, None)
    cs2.apply_state(state)
    u2 = cs2.loops["UIC-2001"].pid
    check("shaping selections survive capture/apply",
          cs2.loops["LIC-1001"].gain_fn is not None
          and "UIC-2001" in cs2._pv_fn
          and u2.pv_eu0 == -10.0
          and abs(u2.sp - uic.sp) < 1e-9
          and cs2.loops["AIC-5001"].predictor is not None)
    check("restored dahlin tuning matches the synthesis",
          abs(cs2.loops["AIC-5001"].pid.gain
              - 300.0 / (1.2 * (45.0 + 90.0))) < 1e-9)
    check("compressor MV selection survives capture/apply",
          cs2._c1_mv == "discharge"
          and cs2.loops["PIC-2001"].out_tag == "FCV-2004")
    cs.define_inferential(
        "q_t2", [("const", 0.0), ("TT-6002", 1.0)],
        bias_tag="AT-6002", theta=60.0, lag1=30.0)
    run(30)
    cs._pv_fn.pop("__probe", None)
    v = cs._inferentials["q_t2"][0].value(db, 0.2)
    check("strategy inferential computes from live tags",
          abs(v - float(db["TT-6002"].value)) < 10.0, f"{v:.1f}")
    st2 = cs.capture_state()
    cs3 = ControlSystem(db, None)
    cs3.apply_state(st2)
    check("inferential definitions survive capture/apply",
          "q_t2" in cs3._inferentials
          and cs3._inferentials["q_t2"][1]["theta"] == 60.0)
    # and the record still knows how to get back to base
    cs2.set_algorithm("LIC-1001", "linear")
    check("restored config can still revert to base gain",
          abs(cs2.loops["LIC-1001"].pid.gain - base_gain) < 1e-9)


def main() -> int:
    math_checks()
    inferential_checks()
    predictor_checks()
    plant_checks()
    print(f"\n{'ALL PASS' if FAILURES == 0 else f'{FAILURES} FAILURES'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
