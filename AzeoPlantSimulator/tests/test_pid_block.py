"""The PID function block against the documented PID behavior and numerical invariants."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from azeoplant.control.pid import PID, Mode, Structure
from azeoplant.core.dynamics import Lag

ok = fail = 0
def check(name, cond, detail=""):
    global ok, fail
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    ok, fail = ok + cond, fail + (not cond)


def make(**kw):
    kw.setdefault("gain", 0.8); kw.setdefault("reset", 20.0)
    return PID(name="TIC", pv_eu0=0, pv_eu100=100, **kw)


class Proc:
    """First order process, gain 1, tau 15 s: PV chases OUT."""
    def __init__(self): self.lag = Lag(15.0, 30.0)
    def step(self, out, dt): return self.lag.step(out, dt)


# 1. closed loop reaches setpoint with zero offset (integral works)
c, p = make(), Proc()
c.set_mode(Mode.AUTO); c.sp = 60.0
pv = 30.0
for _ in range(6000):
    pv = p.step(c.step(0.1, pv), 0.1)
check("PI reaches SP with zero offset", abs(pv - 60.0) < 0.2, f"pv={pv:.2f}")

# 2. bumpless Man -> Auto: with SP tracking PV, the switch must not move OUT
c, p = make(), Proc()
c.set_mode(Mode.MAN); c.out = 45.0
pv = 30.0
for _ in range(3000):
    pv = p.step(c.step(0.1, pv), 0.1)          # settles at pv ~= 45
out_before = c.out
c.set_mode(Mode.AUTO)
pv2 = p.step(c.step(0.1, pv), 0.1)
check("Man->Auto is bumpless (SP tracked PV)", abs(c.out - out_before) < 0.5,
      f"jump {abs(c.out - out_before):.3f}%")
check("SP-PV tracking held SP at PV in Man", abs(c.sp - pv) < 0.5)

# 3. clamped anti-windup: hold PV far below SP so OUT rails, then step SP to
#    PV. Recovery must begin within a couple of reset times, not after the
#    integral has spent minutes walking back from an unbounded sum.
c = make(reset=10.0)
c.set_mode(Mode.AUTO); c.sp = 90.0
for _ in range(3000):                          # 5 min railed at 100 %
    c.step(0.1, 20.0)
check("output railed high while starved", c.out >= 99.9, f"{c.out:.1f}")
t_off = None
for i in range(1200):
    c.step(0.1, 95.0)                          # PV crosses above SP
    if c.out < 99.0:
        t_off = i * 0.1
        break
check("no windup: OUT leaves the rail promptly when the error reverses",
      t_off is not None and t_off < 1.0, f"{t_off}s")

# 4. external reset: a limited slave freezes the master's integral
mas = make(gain=1.0, reset=15.0)
mas.set_mode(Mode.AUTO); mas.sp = 80.0
fb = 40.0                                       # slave stuck: bkcal constant
for _ in range(3000):
    mas.step(0.1, 40.0, bkcal_in=fb, bkcal_in_limit="high")
# with external reset the integral term converges on the slave's 40 and
# stops; OUT = P + 40, not the rail.
expected = mas.gain * (80.0 - 40.0) + fb
check("external reset: master waits at P + slave value, not the rail",
      abs(mas.out - expected) < 1.0, f"out={mas.out:.1f} vs {expected:.1f}")

# 5. direct acting reverses the sign
c = make(direct_acting=True)
c.set_mode(Mode.AUTO); c.sp = 50.0; c.out = 50.0
o1 = c.step(0.1, 70.0)                          # first scan: bumpless at 50
for _ in range(50):
    o2 = c.step(0.1, 70.0)                      # then reset drives OUT up
check("direct acting: first closed scan is bumpless", abs(o1 - 50.0) < 0.1,
      f"{o1:.1f}")
check("direct acting: PV above SP then drives OUT up", o2 > 52.0, f"{o2:.1f}")

# 6. structure I-on-error gives no proportional kick on SP change
c = make(structure=Structure.I_ERROR_PD_PV, reset=30.0)
c.set_mode(Mode.AUTO); c.sp = 50.0; c.out = 50.0; c._reset_fb.reset(50.0)
c.step(0.1, 50.0)
c.sp = 70.0
o = c.step(0.1, 50.0)
check("I on error: no P kick on SP step", abs(o - 50.0) < 0.7, f"{o:.2f}")
c2 = make(structure=Structure.PID_ON_ERROR, rate=0.0)
c2.set_mode(Mode.AUTO); c2.sp = 50.0; c2.out = 50.0; c2._reset_fb.reset(50.0)
c2.step(0.1, 50.0); c2.sp = 70.0
o2 = c2.step(0.1, 50.0)
check("PID on error: P kick present on SP step", o2 - 50.0 > 10.0, f"{o2:.1f}")

# 7. SP rate limiting walks the setpoint
c = make(sp_rate_up=1.0)
c.set_mode(Mode.AUTO); c.sp = 50.0
c.step(0.1, 50.0)
c.sp = 90.0
for _ in range(100):                            # 10 s at 1 EU/s
    c.step(0.1, 50.0)
check("SP rate limit: 40 EU step walks at 1 EU/s",
      59.0 < c.sp_wrk < 61.0, f"sp_wrk={c.sp_wrk:.1f}")

# 8. bad PV sheds actual to Man, target survives, control resumes
c, p = make(), Proc()
c.set_mode(Mode.AUTO); c.sp = 60.0
pv = 30.0
for _ in range(2000):
    pv = p.step(c.step(0.1, pv), 0.1)
frozen = c.out
for _ in range(50):
    c.step(0.1, 999.0, pv_good=False)
check("bad PV: actual sheds to Man, OUT frozen",
      c.actual_mode is Mode.MAN and abs(c.out - frozen) < 1e-9)
check("target mode preserved through the failure", c.target_mode is Mode.AUTO)
c.step(0.1, pv, pv_good=True)
check("control resumes when the PV returns", c.actual_mode is Mode.AUTO)

# 9. cascade: master SP drives slave in Cas, slave leaving Cas parks master
mas = make(gain=1.5, reset=25.0, use_pv_for_bkcal=False)
slv = make(gain=0.9, reset=8.0)
mas.set_mode(Mode.AUTO); mas.sp = 70.0
slv.set_mode(Mode.CAS)
p1, p2 = Lag(30.0, 30.0), Lag(6.0, 40.0)        # outer and inner process
pv1, pv2 = 30.0, 40.0
for _ in range(9000):
    m_out = mas.step(0.1, pv1, bkcal_in=slv.bkcal_out,
                     bkcal_in_limit=slv.bkcal_limit)
    s_out = slv.step(0.1, pv2, cas_in=m_out)
    pv2 = p2.step(s_out, 0.1)
    pv1 = p1.step(pv2, 0.1)
check("cascade regulates the outer PV to SP", abs(pv1 - 70.0) < 0.5,
      f"pv1={pv1:.2f}")

# 10. alarms with hysteresis
c = make(hi_lim=80.0, alarm_hys=2.0)
c.set_mode(Mode.AUTO); c.sp = 50.0
c.step(0.1, 81.0)
check("HI alarm sets above the limit", c.alarms.hi)
c.step(0.1, 79.0)
check("and holds inside the hysteresis band", c.alarms.hi)
c.step(0.1, 77.0)
check("and clears past it", not c.alarms.hi)

print(f"\n{fail} failures of {ok + fail}")
sys.exit(1 if fail else 0)
