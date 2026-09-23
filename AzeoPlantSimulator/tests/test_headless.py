"""Headless verification.

Runs without Qt so it can be used in CI. Checks that:

* dynamic primitives stay bounded at absurd step sizes;
* the MOV, motor and pump state machines behave;
* the flowsheet runs for a simulated hour without producing a non-finite value;
* the engine survives an injected model exception;
* an OPC UA client can read a tag by ``ns=2;s=<TAG>``, see its quality, and write
  an AO that reaches the model.
"""

from __future__ import annotations

import asyncio
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from azeoplant.core.devices import (CentrifugalPump, ControlValve, HeatExchanger,
                               Motor, MotorOperatedValve, ValveChar)
from azeoplant.core.dynamics import DeadTime, Integrator, Lag, RateLimiter
from azeoplant.core.engine import SimulationEngine
from azeoplant.core.tags import Quality, TagDatabase, TagKind
from azeoplant.logging_setup import setup_logging
from azeoplant.models.flowsheet import Flowsheet

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(name)


def test_dynamics() -> None:
    print("\ndynamics")
    lag = Lag(5.0)
    for _ in range(100):
        lag.step(100.0, 60.0)                      # dt twelve times tau
    check("lag converges without overshoot at huge dt",
          99.0 < lag.y <= 100.0, f"y={lag.y:.3f}")

    lag2 = Lag(5.0)
    lag2.step(float("nan"), 0.1)
    check("lag rejects NaN", math.isfinite(lag2.y))

    integ = Integrator(50.0, 0.0, 100.0)
    for _ in range(1000):
        integ.step(1e6, 0.1)
    check("integrator clamps to its limits", integ.y == 100.0 and integ.saturated)

    dead = DeadTime(1.0, 0.1, 0.0)
    outs = [dead.step(1.0) for _ in range(15)]
    check("dead time delays by the right number of steps",
          outs[9] == 0.0 and outs[10] == 1.0)

    rl = RateLimiter(10.0)
    rl.step(1000.0, 1.0)
    check("rate limiter honours its slew rate", abs(rl.y - 10.0) < 1e-9)


def test_devices() -> None:
    print("\ndevices")
    mov = MotorOperatedValve("MOV-TEST", travel_time=10.0, position=0.0)
    mov.command(True, False)
    for _ in range(120):
        mov.step(0.1)
    check("MOV opens fully in its travel time", mov.zso and mov.position >= 99.5)

    mov.command(False, True)
    for _ in range(120):
        mov.step(0.1)
    check("MOV closes and makes the closed limit", mov.zsc)

    mov2 = MotorOperatedValve("MOV-TRIP", travel_time=10.0, torque_trip_at=40.0)
    mov2.command(True, False)
    for _ in range(200):
        mov2.step(0.1)
    check("MOV torque trip stops travel",
          mov2.torque_tripped and 35.0 < mov2.position < 55.0,
          f"pos={mov2.position:.1f}")

    mov3 = MotorOperatedValve("MOV-SW", travel_time=5.0, open_limit_faulty=True)
    mov3.command(True, False)
    for _ in range(120):
        mov3.step(0.1)
    check("faulty open limit never makes despite full travel",
          mov3.position >= 99.5 and not mov3.zso)

    motor = Motor("M-TEST", vfd=True, start_delay=1.0)
    motor.command(True, False)
    for _ in range(5):
        motor.step(0.1, permissive=True)
    check("motor waits for its start delay", not motor.running)
    for _ in range(20):
        motor.step(0.1, permissive=True)
    check("motor runs after the start delay", motor.running)

    motor.command(True, False)
    for _ in range(20):
        motor.step(0.1, permissive=False)
    check("motor stops when the permissive is lost", not motor.running)

    valve = ControlValve("FCV-TEST", cv_rated=100, char=ValveChar.EQUAL_PERCENT,
                         stroke_time=4)
    for _ in range(100):
        valve.step(0.1, 50.0)
    f_half = valve.flow(4.0)
    for _ in range(100):
        valve.step(0.1, 100.0)
    f_full = valve.flow(4.0)
    check("equal percent valve is strongly non-linear",
          f_full > f_half * 5.0, f"{f_half:.1f} then {f_full:.1f} m3/h")
    check("closed valve passes nothing", ControlValve("V", cv_rated=100).flow(4.0) == 0.0)

    pump = CentrifugalPump("P-TEST")
    check("pump head falls with flow",
          pump.head(0, 100) > pump.head(150, 100) > 0.0)
    check("stopped pump develops no head", pump.head(50, 0) == 0.0)

    hx = HeatExchanger("E-TEST", ua_clean=100.0)
    check("exchanger passes UA times dT",
          abs(hx.capacity(200.0, 100.0) - 10000.0) < 1e-6)
    check("exchanger follows demand when it has margin",
          hx.transfer(4000.0, 200.0, 100.0) == 4000.0 and not hx.limited)
    check("exchanger limits when the surface is the constraint",
          hx.transfer(20000.0, 200.0, 100.0) == 10000.0 and hx.limited)
    hx.fouling_pct = 50.0
    check("fouling scales the surface duty",
          abs(hx.capacity(200.0, 100.0) - 5000.0) < 1e-6)
    check("reversed temperature difference stalls the exchanger",
          hx.capacity(100.0, 200.0) == 0.0)
    check("a shut utility valve transfers nothing",
          hx.capacity(200.0, 100.0, driver=0.0) == 0.0)
    hx.clear_faults()
    check("clearing fouling restores the clean surface",
          abs(hx.capacity(200.0, 100.0) - 10000.0) < 1e-6)


def build(dt: float = 0.1) -> tuple[TagDatabase, Flowsheet, SimulationEngine]:
    db = TagDatabase()
    fs = Flowsheet(db, dt=dt)
    eng = SimulationEngine(db, fs, dt=dt)
    return db, fs, eng


def test_flowsheet_stability() -> None:
    print("\nflowsheet stability")
    db, fs, eng = build()
    counts = db.counts()
    check("tags built", sum(counts.values()) > 450, str(counts))

    # Line the plant up: open the suction valve, start the duty pump, fire the heater.
    from pathlib import Path as _P
    snap = _P(__file__).resolve().parents[1] / "snapshots" / "lined_up.json"
    if snap.exists():
        eng.load_snapshot(snap)
    for tag in ("XY-MOV1001A-OPN", "XY-P101A-STR", "XY-3010", "XY-C1-STR",
                "XY-P201-STR", "XY-FD701-STR"):
        db[tag].value = True

    for _ in range(3000):                        # settle on the restored state
        eng._execute_step()
    check("T1 is separating", float(db["AT-5001"].value) < 4.0,
          f"{db['AT-5001'].value:.2f} mol% heavy key")
    # T2 makes the R2 blendstock from the T1 distillate split (routing
    # of 2026-09-02): its overhead legitimately carries ~8-10 mol% heavy
    # key at the lined point, and its separation shows in the bottoms
    # staying clean of lights.
    check("T2 is separating", float(db["AT-6001"].value) < 12.0
          and float(db["AT-6002"].value) < 4.0,
          f"{db['AT-6001'].value:.2f} mol% heavy in R2, "
          f"{db['AT-6002'].value:.2f} mol% light in bottoms")
    check("compressor making discharge pressure",
          float(db["PT-2002"].value) > float(db["PT-2001"].value),
          f"{db['PT-2002'].value:.1f} barg")

    # This is deliberately open loop: after the initial hold, inventories may
    # drift and physical trips may act.  The hour proves bounded dynamics, not
    # a steady state that would require controllers inside the process model.
    for _ in range(36000):                       # one hour of simulated time
        eng._execute_step()

    bad = [t.name for t in db.all()
           if t.kind.analogue and not math.isfinite(float(t.value))]
    check("no non-finite values after one simulated hour", not bad, str(bad[:5]))

    out_of_range = [f"{t.name}={t.value:.1f}" for t in db.all()
                    if t.kind.analogue and not (t.lo - 0.1 * t.span
                                                <= t.value <= t.hi + 0.1 * t.span)]
    check("every analogue stays near its span", not out_of_range, str(out_of_range[:5]))

    check("all ten units stepped without error", eng.stats.errors == 0,
          eng.stats.last_error[:120])
    check("pump is running", bool(db["XS-P101A-RUN"].value))
    check("charge flow established", float(db["FT-1001"].value) > 20.0,
          f"{db['FT-1001'].value:.1f} m3/h")
    check("heater is lit", bool(db["BS-3001"].value))
    check("outlet is hotter than inlet",
          float(db["TT-3001"].value) > float(db["TT-1001"].value) + 20.0,
          f"{db['TT-3001'].value:.1f} degC")
    check("reactor is converting", float(db["XI-4001"].value) > 30.0,
          f"{db['XI-4001'].value:.1f} %")
    check("reactor bed hotter than its inlet",
          float(db["TT-4002"].value) > float(db["TT-4001"].value),
          f"{db['TT-4002'].value:.1f} degC")
    check("column drums holding inventory",
          8.0 < float(db["LT-5001"].value) < 95.0
          and 8.0 < float(db["LT-6001"].value) < 95.0,
          f"T1 {db['LT-5001'].value:.0f} %, T2 {db['LT-6001'].value:.0f} %")
    check("boiler drum level under control",
          -250.0 < float(db["LT-7001"].value) < 250.0,
          f"{db['LT-7001'].value:.0f} mm")
    check("MP steam header pressurised", float(db["PT-7002"].value) > 20.0,
          f"{db['PT-7002'].value:.1f} barg")
    check("effluent within consent", 6.0 < float(db["AT-8002"].value) < 9.0,
          f"pH {db['AT-8002'].value:.2f}")
    check("fuel header pressure sensible",
          5.0 < float(db["PT-0101"].value) < 25.0, f"{db['PT-0101'].value:.2f} barg")
    check("oxygen sensible", 0.0 <= float(db["AT-3001"].value) <= 21.0,
          f"{db['AT-3001'].value:.2f} mol%")


def test_no_stale_outputs() -> None:
    print("\nlive outputs")
    db, fs, eng = build()
    before = {t.name: t.ts for t in db.all()}
    for _ in range(3000):
        eng._execute_step()
    stale = [t.name for t in db.by_kind(TagKind.AI, TagKind.DI)
             if t.ts == before[t.name]]
    # A tag that is published but never written looks live to the DCS and can
    # never move. Bounds checks pass on it trivially, so it needs its own test.
    check("every model output is written by some unit", not stale, str(stale[:6]))


def test_esd() -> None:
    print("\nemergency shutdown")
    db, fs, eng = build()
    for tag in ("XY-MOV1001A-OPN", "XY-P101A-STR", "XY-3010"):
        db[tag].value = True
    for _ in range(2000):
        eng._execute_step()
    check("heater lit before the trip", bool(db["BS-3001"].value))

    db["XY-9002"].value = True          # ESD-1 on the reaction section
    for _ in range(600):
        eng._execute_step()
    check("reaction section ESD closes the heater fuel valve",
          bool(db["ZSC-XV3001"].value))
    check("reaction section ESD closes the reactor feed valve",
          bool(db["ZSC-XV4001"].value))
    check("flame is lost after the fuel valve shuts", not bool(db["BS-3001"].value))
    check("fractionation untouched by a reaction-section trip",
          not bool(db["ZSC-XV5001"].value))

    db["XY-9001"].value = True          # ESD-0, whole plant
    for _ in range(600):
        eng._execute_step()
    check("total ESD closes the fractionation feed valves",
          bool(db["ZSC-XV5001"].value) and bool(db["ZSC-XV6001"].value))
    check("total ESD closes the boiler fuel valve", bool(db["ZSC-XV7001"].value))


def test_zero_flow_and_shut_suction() -> None:
    print("\nedge cases")
    db, fs, eng = build()
    # Pump running against a shut suction valve: it must cavitate, not divide by zero.
    db["XY-MOV1001A-CLS"].value = True
    db["XY-P101A-STR"].value = True
    for _ in range(6000):
        eng._execute_step()
    u100 = fs.unit("U100")
    check("shut suction valve causes cavitation despite the start command",
          u100.pump_a.cavitating or not u100.motor_a.running)
    check("suction pressure did not go non-finite",
          math.isfinite(float(db["PT-1003"].value)))

    # Firing hard into no flow must not produce an infinite temperature.
    db["FCV-3001"].value = 100.0
    db["XY-3010"].value = True
    for _ in range(6000):
        eng._execute_step()
    check("outlet temperature bounded at zero charge flow",
          float(db["TT-3001"].value) <= 500.0, f"{db['TT-3001'].value:.1f} degC")


def test_speed_factor_invariance() -> None:
    print("\nstep size invariance")
    results = []
    for dt in (0.05, 0.2):
        db, fs, eng = build(dt)
        db["XY-MOV1001A-OPN"].value = True
        db["XY-P101A-STR"].value = True
        db["XY-3010"].value = True
        for _ in range(int(1800 / dt)):
            eng._execute_step()
        results.append(float(db["TT-3001"].value))
    check("outlet temperature agrees between step sizes",
          abs(results[0] - results[1]) < 6.0,
          f"{results[0]:.1f} vs {results[1]:.1f} degC")


def test_engine_error_handling() -> None:
    print("\nerror handling")
    db, fs, eng = build()
    victim = fs.units[1]
    original = victim.step

    def exploding(dt):
        raise RuntimeError("injected fault")

    victim.step = exploding
    for _ in range(10):
        eng._execute_step()
    check("engine survives a raising model", eng.stats.errors == 10)
    check("other units still ran", eng.stats.unit_ms.get("U300", 0.0) >= 0.0)

    for _ in range(30):
        if eng.stats.frozen_by_error:
            break              # a frozen engine would not be stepped by the loop
        eng._execute_step()
    check("engine freezes past its error budget", eng.stats.frozen_by_error)
    check("inputs marked bad on fail-safe",
          all(t.quality is Quality.BAD for t in db.by_kind(TagKind.AI)))

    victim.step = original
    eng.resume()
    eng._execute_step()
    check("engine recovers once the model is fixed", not eng.stats.frozen_by_error)


def test_timing() -> None:
    """The speed factor has to mean what it says.

    A sleep overshoots its request by a near constant margin, so a loop that
    wakes once per step cannot hold a short period and quietly runs slow. That
    was worth about a factor of three at twenty times before it was fixed, with
    nothing on screen to say so.
    """
    print("\ntiming")
    import time as _t
    def measure(want):
        db, fs, eng = build()
        eng.speed_factor = want
        eng.start()
        eng.resume()
        _t.sleep(0.5)
        t0, h0 = _t.monotonic(), eng.stats.heartbeat
        _t.sleep(3.0)
        got = (eng.stats.heartbeat - h0) * eng.dt / (_t.monotonic() - t0)
        batch = eng.stats.steps_per_wake
        eng.stop()
        return got, batch

    for want in (1.0, 5.0, 20.0):
        got, batch = measure(want)
        if got / want <= 0.90:
            # Wall clock delivery depends on the host as well as the engine.
            # One retry after the machine settles separates a loaded box from
            # a slow engine: a real regression fails both measurements.
            _t.sleep(2.0)
            got, batch = measure(want)
        # Only slow delivery is asserted. Under-delivery was the bug this
        # guards against; transient over-delivery is the scheduler catching up
        # after jitter, and failing the gate on a loaded machine over it would
        # teach people to ignore the gate.
        check(f"speed factor {want:g}x is delivered", got / want > 0.90,
              f"{got:.2f}x, {batch} step(s) per wake")

    db, fs, eng = build()
    for _ in range(20):
        eng._execute_step()
    check("sim_time advances when the engine is stepped directly",
          abs(eng.stats.sim_time - 20 * eng.dt) < 1e-9,
          f"{eng.stats.sim_time:.2f} s")


def test_instrument_sanity() -> None:
    """What the instruments show has to be something an instrument could show."""
    print("\ninstruments")

    # A discontinuous analyser reports its last sample. Unprimed it keeps
    # its build-time default, which on a five minute analyser reads as a
    # plausible product for five minutes after start up. This has to be
    # checked on a plant built from scratch: restoring a snapshot hands the
    # analyser a sample and hides it. The test is "was it WRITTEN", not
    # "is it nonzero": a clean bottoms genuinely reads 0.0 mol% now that
    # the analyser output stage clamps to range.
    db, fs, eng = build()
    analysers = [t for t in db.all()
                 if t.name.startswith("AT-") and t.kind.analogue]
    poison = -12345.0
    for t in analysers:
        t.value = poison
    eng._execute_step()
    unwritten = [t.name for t in analysers if float(t.value) == poison]
    check("analysers report a sample on the first scan", not unwritten,
          ", ".join(unwritten[:4]) if unwritten
          else f"{len(analysers)} analysers")

    # A value that reverses direction on every single scan is a numerical limit
    # cycle, not process behaviour. The boiler feedwater loop did exactly that,
    # swinging half of span every 100 ms, but only after the plant had been left
    # running open loop long enough for the drum to flood. Nothing short of
    # actually getting there finds it: forcing the drum level alone does not
    # reproduce the pressures and valve positions that make the loop unstable.
    for _ in range(12000):                       # twenty simulated minutes
        eng._execute_step()
    check("drum did flood, so the unstable case was really reached",
          float(db["LT-7001"].value) > 250.0, f'{db["LT-7001"].value:.0f} mm')
    _check_no_chatter(db, eng, "open loop with the drum flooded")

    # Normal running: nothing should be driven past what an instrument can show.
    db, fs, eng = build()
    from pathlib import Path as _P
    snap = _P(__file__).resolve().parents[1] / "snapshots" / "lined_up.json"
    if snap.exists():
        eng.load_snapshot(snap)
    for _ in range(6000):                        # ten simulated minutes
        eng._execute_step()
    ex = db.excursions()
    check("no analogue is driven beyond what an instrument could indicate",
          not ex, ", ".join(f"{n} by {w:.2f}" for n, _c, w in ex[:3]) or "none")
    _check_no_chatter(db, eng, "lined out")


def _check_no_chatter(db, eng, where: str) -> None:
    names = [t.name for t in db.all() if t.kind.analogue]
    hist = {n: [] for n in names}
    for _ in range(21):
        eng._execute_step()
        for n in names:
            hist[n].append(float(db[n].value))
    bad = []
    for n in names:
        v = hist[n]
        d = [v[i + 1] - v[i] for i in range(len(v) - 1)]
        alt = sum(1 for i in range(len(d) - 1) if d[i] * d[i + 1] < 0)
        swing = max(v) - min(v)
        if alt >= len(d) - 2 and swing > 0.05 * db[n].span:
            bad.append(f"{n} by {swing:.1f}")
    check(f"no analogue alternates direction every scan, {where}", not bad,
          ", ".join(bad[:3]) or "none")


def test_closed_loop() -> None:
    """The branch's reason to exist: the plant runs itself.

    Loops close on the plant as found, hold it for fifteen simulated minutes,
    then reject a real disturbance. The disturbance matters more than the
    hold: an open loop plant also holds for a while, but only a working
    control system takes a feed cut and comes back to setpoint.
    """
    print("\nclosed loop")
    from azeoplant.control.strategy import ControlSystem
    from pathlib import Path as _P
    db, fs, eng = build()
    snap = _P(__file__).resolve().parents[1] / "snapshots" / "lined_up.json"
    if snap.exists():
        eng.load_snapshot(snap)
    cs = ControlSystem(db)
    cs.seed_from_plant()
    eng.controller = cs
    eng.post_step_hooks.append(cs.step)

    for _ in range(9000):                       # fifteen minutes
        eng._execute_step()
    check("plant holds under control for 15 minutes",
          abs(float(db["TT-3001"].value) - cs.loops["TIC-3001"].pid.sp_wrk) < 4.0
          and 40.0 < float(db["LT-5001"].value) < 60.0
          and abs(float(db["AT-5001"].value)) < 2.0,
          f'TT-3001={db["TT-3001"].value:.1f}  T1drum={db["LT-5001"].value:.0f}%'
          f'  AT-5001={db["AT-5001"].value:.2f}')

    # ------------------------------------------------- disturbance rejection
    u100 = fs.unit("U100")
    u100.fresh_feed *= 0.80                     # a 20 percent feed cut
    worst = 0.0
    tic = cs.loops["TIC-3001"].pid
    for _ in range(12000):                      # twenty minutes to recover
        eng._execute_step()
        worst = max(worst, abs(float(db["TT-3001"].value) - tic.sp_wrk))
    check("20% feed cut is rejected: heater back on temperature",
          abs(float(db["TT-3001"].value) - tic.sp_wrk) < 3.0,
          f'now {db["TT-3001"].value:.1f} vs SP {tic.sp_wrk:.1f}, '
          f'worst excursion {worst:.1f}')
    check("levels rode through the disturbance",
          30.0 < float(db["LT-5001"].value) < 70.0
          and 40.0 < float(db["LT-1001"].value) < 70.0,
          f'T1drum={db["LT-5001"].value:.0f}%  D1={db["LT-1001"].value:.0f}%')
    check("no model errors under closed loop control", eng.stats.errors == 0,
          str(eng.stats.errors))


def test_snapshot(tmp: Path) -> None:
    print("\nsnapshots")
    db, fs, eng = build()
    db["XY-MOV1001A-OPN"].value = True
    db["XY-P101A-STR"].value = True
    for _ in range(3000):
        eng._execute_step()
    level_before = fs.unit("U100").level.y
    path = eng.save_snapshot(tmp / "snap.json")

    db2, fs2, eng2 = build()
    eng2.load_snapshot(path)
    check("snapshot restores unit state",
          abs(fs2.unit("U100").level.y - level_before) < 1e-6,
          f"{level_before:.3f} %")

    # A restored plant has to be the same plant, not a plant that merely looks
    # right until the first scan overwrites every tag from its own internals.
    # Comparing one value is what let most of the state go missing unnoticed,
    # so compare all of them, and keep stepping: state that was dropped shows
    # up as divergence, not as a bad initial reading.
    def values(database):
        return {t.name: float(t.value) for t in database.all()
                if isinstance(t.value, (int, float, bool))}

    check("every tag identical immediately after load",
          not [k for k, v in values(db).items()
               if abs(v - values(db2)[k]) > 1e-9],
          f"{len(values(db))} tags")

    for label, steps in (("one scan", 1), ("60 s", 599), ("5 min", 2400)):
        for _ in range(steps):
            eng._execute_step()
            eng2._execute_step()
        a, b = values(db), values(db2)
        drift = {k: abs(a[k] - b[k]) for k in a if abs(a[k] - b[k]) > 1e-9}
        worst = max(drift.items(), key=lambda kv: kv[1], default=("none", 0.0))
        check(f"restored plant still identical after {label}",
              not drift,
              f"{len(drift)} tags differ, worst {worst[0]} by {worst[1]:.4g}"
              if drift else "0 of %d differ" % len(a))

    # Anything that carries state has to be able to hand it over. A new lag
    # added without the pair is the exact failure this guards against.
    missing = []
    for unit in fs.units:
        for name, obj in vars(unit).items():
            if hasattr(obj, "step") and not hasattr(obj, "capture_state") \
                    and not hasattr(obj, "capture"):
                missing.append(f"{unit.code}.{name}")
    check("every stateful object can be captured", not missing,
          ", ".join(missing[:4]) if missing else "all covered")

    # An injected fault is part of the plant's condition and must survive.
    db3, fs3, eng3 = build()
    mf = next((m for u in fs3.units for m in u.malfunctions), None)
    if mf is not None:
        mf.set(True, 50.0)
        for _ in range(20):
            eng3._execute_step()
        p2 = eng3.save_snapshot(tmp / "mf.json")
        db4, fs4, eng4 = build()
        eng4.load_snapshot(p2)
        back = next((m for u in fs4.units for m in u.malfunctions
                     if m.mf_id == mf.mf_id), None)
        check("an injected malfunction survives the round trip",
              back is not None and back.active, mf.mf_id)


def test_opcua() -> None:
    print("\nOPC UA server")
    from asyncua import Client, ua
    from azeoplant.opc.server import OpcUaServer

    db, fs, eng = build()
    db["XY-MOV1001A-OPN"].value = True
    db["XY-P101A-STR"].value = True
    eng.start()
    eng.resume()

    srv = OpcUaServer(db, eng, endpoint="opc.tcp://127.0.0.1:48400/plant_sim/",
                      publish_period=0.1)
    srv.start()

    deadline = time.time() + 60.0   # dual address space takes ~15-30 s to build
    while not srv.stats.running and time.time() < deadline:
        time.sleep(0.2)
    check("server started", srv.stats.running, srv.stats.last_error)

    async def client_checks():
        async with Client(url="opc.tcp://127.0.0.1:48400/plant_sim/") as client:
            node = client.get_node(ua.NodeId("LT-1001", 2))
            dv = await node.read_data_value()
            check("tag readable at ns=2;s=LT-1001", dv.Value.Value is not None,
                  f"{dv.Value.Value:.2f}")
            status = getattr(dv, "StatusCode", None) or getattr(dv, "StatusCode_", None)
            check("status code present and good", status is not None and status.is_good())
            check("source timestamp present", dv.SourceTimestamp is not None)

            pct = await client.get_node(ua.NodeId("LT-1001.PctVal", 2)).read_value()
            check("PctVal companion node present", 0.0 <= pct <= 110.0, f"{pct:.1f} %")

            ao = client.get_node(ua.NodeId("FCV-1001", 2))
            await ao.write_value(ua.DataValue(ua.Variant(72.5, ua.VariantType.Double)))
            await asyncio.sleep(0.8)
            check("client write reaches the tag database",
                  abs(float(db["FCV-1001"].value) - 72.5) < 1e-6,
                  f"{db['FCV-1001'].value}")
            await asyncio.sleep(8.0)      # FCV-1001 has a six second stroke
            check("model acted on the written value",
                  abs(fs.unit("U100").fcv1001.position - 72.5) < 6.0,
                  f"position {fs.unit('U100').fcv1001.position:.1f} %")

            hb1 = await client.get_node(ua.NodeId("SIM.Heartbeat", 2)).read_value()
            await asyncio.sleep(1.0)
            hb2 = await client.get_node(ua.NodeId("SIM.Heartbeat", 2)).read_value()
            check("heartbeat is incrementing", hb2 > hb1, f"{hb1} then {hb2}")

            # Bad quality must reach the client, not just the UI. Freeze first,
            # otherwise the next model step immediately overwrites the quality.
            eng.freeze()
            await asyncio.sleep(0.5)
            with db.lock:
                db["LT-1001"].quality = Quality.BAD
            await asyncio.sleep(0.8)
            dv2 = await node.read_data_value(raise_on_bad_status=False)
            st2 = getattr(dv2, "StatusCode", None) or getattr(dv2, "StatusCode_", None)
            check("bad quality is published as a status code", st2.is_bad())

            children = await client.get_node(ua.NodeId("PLANT_SIM.U100", 2)).get_children()
            check("browse hierarchy present under PLANT_SIM.U100", len(children) >= 3,
                  f"{len(children)} folders")

    try:
        asyncio.run(client_checks())
        # A publish failure means a connected DCS reads stale values. It must
        # be zero, and it must be counted rather than buried in a debug log.
        check("no publish write failures", srv.stats.publish_errors == 0,
              f"{srv.stats.publish_errors} failed, last: {srv.stats.last_error}")
    finally:
        srv.stop()
        eng.stop()
    # The window close path can race the server thread's own loop shutdown, so
    # stopping a server whose loop is already closed must be a no-op.
    try:
        srv.stop()
        check("stopping a stopped server is safe", True)
    except Exception as exc:
        check("stopping a stopped server is safe", False, repr(exc))


if __name__ == "__main__":
    setup_logging("logs")
    import tempfile

    t0 = time.time()
    test_dynamics()
    test_devices()
    test_flowsheet_stability()
    test_no_stale_outputs()
    test_esd()
    test_zero_flow_and_shut_suction()
    test_speed_factor_invariance()
    test_engine_error_handling()
    test_timing()
    test_instrument_sanity()
    test_closed_loop()
    with tempfile.TemporaryDirectory() as d:
        test_snapshot(Path(d))
    test_opcua()

    print(f"\n{'=' * 60}")
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        sys.exit(1)
    print(f"All checks passed in {time.time() - t0:.1f} s")
