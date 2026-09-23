"""The plant control strategy: every regulatory loop, wired like a DCS.

This is the layer the open-loop simulator deliberately leaves to a real DCS.
On this branch it is provided in software, built from the PID function block
in :mod:`azeoplant.control.pid`, so the plant runs closed loop out of the box
and every loop presents a Azeo faceplate.

The process models are untouched: this module reads PVs from the tag
database and writes AO tags exactly as an external DCS would over OPC UA.
Interlocks stay wherever they already live; a loop here can still be put in
Man and the plant will still cavitate, flood or trip on the consequence.

Loop gains follow the commissioning script's values, converted to the
block's normalised form (percent of PV span per percent of OUT). Cascades
carry BKCAL back up, so a railed slave stops its master's reset by the
external-reset mechanism rather than by special cases.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from ..core.tags import TagDatabase
from .alarm_table import ALARMS
from .esd import EsdLogic
from .pid import PID, Mode, Structure
from . import shaping

log = logging.getLogger(__name__)

# Cross-limit leads keep the air side just ahead of fuel during normal firing.
# With identical air and fuel targets, transmitter/filter lag left the low
# selector permanently active: the heater master then saw a standing external-
# reset limit and could not remove its temperature offset.  Two percent is
# enough selector clearance without changing the O2 target; the analyser trim
# removes the same amount from its excess-air ratio at steady state.
_CROSS_LIMIT_LEAD = 1.02

# A control module executes on a 200 ms to 1 s period, not on the
# simulator's 100 ms integration step. Scanning at 200 ms is both the
# faithful behaviour and what keeps the 20x speed factor deliverable.
# Each module carries its own scan class (``Loop.scan_period``, one of
# SCAN_CLASSES); the default is this, and the control system ticks at
# the fastest class in use.
SCAN_PERIOD = 0.2
SCAN_CLASSES = (0.1, 0.2, 0.5, 1.0, 2.0)


@dataclass
class Loop:
    """One control module: a PID block bound to plant tags."""

    module: str                       # e.g. "FIC-1001"
    pv_tag: str
    out_tag: str
    pid: PID
    description: str = ""
    master: Optional[str] = None      # module name whose OUT is this SP
    ff_tag: str = ""                  # feedforward source
    # split range: (tag, out_lo, out_hi, valve_lo, valve_hi) per leg
    split: List[tuple] = field(default_factory=list)
    enabled: bool = True
    # the module's scan class, seconds, and the time accumulated towards
    # its next execution when it is slower than the system tick
    scan_period: float = SCAN_PERIOD
    _scan_accum: float = 0.0
    # Whitehouse-parity loop shaping, wrapped AROUND the unchanged PID
    # block: a per-scan gain scheduler (error-squared, gap, banded,
    # programmed adaptive, linearised-PV-equivalent) and an optional
    # deadtime predictor correcting the PV ahead of the block.
    gain_fn: Optional[Callable] = None
    predictor: Optional[object] = None
    # output conditioning between block and valve, the anti-equal-
    # percentage characterizer: OP* = 100(1-k)OP / (100 - k*OP)
    out_cond: float = 0.0


def _norm_gain(gain_eu: float, pv_span: float) -> float:
    """Commissioning gains are percent-of-output per EU; the block wants
    percent per percent of PV span."""
    return gain_eu * pv_span / 100.0


class ControlSystem:
    """All the plant's loops, scanned once per engine step."""

    def __init__(self, db: TagDatabase, bus=None) -> None:
        self.db = db
        # The virtual I/O bus, when the runtime provides one: outputs then
        # go through the checked doorway and the control system holds the
        # output side while the loop is closed.
        self.bus = bus
        if bus is not None:
            bus.register_dcs("internal")
        self.loops: Dict[str, Loop] = {}
        self._order: List[Loop] = []
        self.esd = EsdLogic(db, bus)
        self._accum = 0.0          # engine time since the last module scan
        self._base_period = SCAN_PERIOD   # the system tick: the fastest class in use
        # Closed loop by default. Open loop makes the whole DCS passive:
        # nothing is written, the plant is free for hand operation or an
        # external controller, and every block tracks so closing the loop
        # again starts from the truth.
        self.enabled = True
        self._build()

    # ------------------------------------------------------------- building
    def _add(self, module: str, pv: str, out: str, sp: float, gain: float,
             reset: float, direct: bool = False, description: str = "",
             master: Optional[str] = None, bkcal_pv: bool = False,
             rate: float = 0.0,
             ff_tag: str = "", ff_gain: float = 0.0,
             sp_rate: float = 0.0, out_lo: float = 0.0,
             out_hi: float = 100.0, split: Optional[List[tuple]] = None,
             mode: Mode = Mode.AUTO,
             pv_span: Optional[tuple] = None,
             pv_filter: Optional[float] = None) -> Loop:
        t = self.db[pv]
        # Flow transmitters are noisy and a PID with no PV filter passes
        # the noise straight to the valve: the four-hour soak measured the
        # worst flow slaves reversing direction every second at up to 16 %
        # of stroke per second. One second of filtering is what a real AI
        # block carries. The combustion cross-limit pairs and the
        # anti-surge controller stay unfiltered: their speed is their
        # protection.
        if pv_filter is None:
            pv_filter = (0.0 if module in
                         ("FIC-3002", "FIC-3003", "FIC-7002", "FIC-7003",
                          "UIC-2001") else 1.0)
        # A derived PV (a differential temperature, say) is not scaled like
        # the transmitter it nominally hangs off: pv_span overrides.
        lo, hi = pv_span if pv_span is not None else (t.lo, t.hi)
        pid = PID(name=module, description=description or t.desc,
                  pv_eu0=lo, pv_eu100=hi,
                  gain=_norm_gain(gain, hi - lo), reset=reset, rate=rate,
                  direct_acting=direct, out_lo_lim=out_lo, out_hi_lim=out_hi,
                  sp_hi_lim=hi, sp_lo_lim=lo,
                  sp_rate_up=sp_rate, sp_rate_dn=sp_rate,
                  ff_enable=bool(ff_tag), ff_gain=ff_gain,
                  pv_ftime=pv_filter,
                  use_pv_for_bkcal=bkcal_pv,
                  arw_hi_lim=out_hi, arw_lo_lim=out_lo,
                  hi_hi_lim=lo + 0.95 * (hi - lo),
                  hi_lim=lo + 0.90 * (hi - lo),
                  lo_lim=lo + 0.10 * (hi - lo),
                  lo_lo_lim=lo + 0.05 * (hi - lo))
        pid.sp = pid.sp_wrk = sp
        pid.out = float(self.db[out].value) if out and out in self.db else 0.0
        pid.set_mode(Mode.CAS if master else mode)
        loop = Loop(module, pv, out, pid, description or t.desc,
                    master=master, ff_tag=ff_tag, split=split or [])
        self.loops[module] = loop
        return loop

    def _build(self) -> None:
        A = self._add

        # ------------------------------------------------------------ U010
        # Header pressure runs split range per the schedule: low pressure
        # opens the import, high pressure opens the flare, and the off-gas
        # recovery loop keeps D3 gas flowing into the header ahead of both.
        A("PIC-0101", "PT-0101", "", 16.0, 6.0, 240,
          description="Fuel gas header pressure",
          split=[("PCV-0102", 0.0, 50.0, 100.0, 0.0),
                 ("PCV-0101", 50.0, 100.0, 0.0, 100.0)])
        A("FIC-0101", "FT-0102", "FCV-0101", 1800.0, 0.06, 60,
          description="D3 off-gas to fuel header")
        A("PIC-0102", "PT-0102", "PCV-0103", 3.2, 12.0, 120,
          description="Fuel gas to H1")
        A("PIC-0103", "PT-0103", "PCV-0104", 6.0, 8.0, 150,
          description="Fuel gas to B1")

        # ------------------------------------------------------------ U100
        # The averaging-level cascade off the P&ID: drum level trims the
        # charge flow controller's setpoint.
        A("FIC-1001", "FT-1001", "FCV-1001", 125.0, 0.9, 60,
          description="Charge flow", master="LIC-1001", bkcal_pv=True)
        # The level master may only ask for charge the heater can actually make
        # 349 degrees at: level rides within the band instead of demanding a
        # flow that drags the outlet temperature down. Averaging level, made
        # explicit by the output ceiling.
        # The heater-capable-charge override (PIC-1001 and the TIC-3001
        # constraint through the selector) is the protection; a static 72 %
        # ceiling on top of it just capped D1's ONLY outflow below the
        # fresh-feed-plus-recycle inflow and guaranteed a flood on every
        # multi-hour run.
        # Slowed again for the live recycle (2026-09-02): with T1 bottoms
        # returning to D1, a level master that integrates 40 m3/h of
        # charge per hour per 2 % of level error RELAYS every recycle
        # slug back into T1's feed, and the plant-wide wave grows on each
        # pass (measured: charge 90 -> 133 in two hours, T1 distillate
        # stalling on every crest). The drum is a surge drum; let it
        # surge. Static convergence of the recycle loop is untouched -
        # the integral still trims, over hours.
        A("LIC-1001", "LT-1001", "", 55.0, 0.6, 2400, direct=True,
          description="D1 level to charge flow", sp_rate=0.5, out_hi=92.0)
        A("FIC-1002", "FT-1003", "FCV-1002", 24.0, 1.5, 30, direct=False,
          description="Charge pump minimum flow", out_lo=0.0)
        A("LIC-1002", "LT-1001", "LCV-1001", 55.0, 1.0, 900,
          description="D1 level via off-spec import", out_hi=60.0)
        # Discharge pressure limiter: an override whose output is the most
        # charge flow the header may carry, low-selected into FIC-1001's
        # cascade setpoint. It rides at full scale until the constraint nears.
        A("PIC-1001", "PT-1002", "", 24.0, 2.0, 180,
          description="P-101 discharge pressure limiter")
        A("SIC-1001", "ST-1001", "SC-1001", 100.0, 2.0, 20,
          description="P-101A VFD speed control")

        # ------------------------------------------------------------ U200
        A("SIC-2001", "ST-2001", "SC-2001", 9800.0, 0.015, 15,
          description="C1 VFD speed control", master="PIC-2001",
          bkcal_pv=True)
        A("PIC-2001", "PT-2001", "", 9.0, 5.0, 200, direct=True,
          description="C1 suction pressure to speed", out_lo=25.0)
        A("UIC-2001", "UY-2001", "FCV-2001", 15.0, 0.8, 120,
          description="C1 anti-surge margin")
        A("LIC-2001", "LT-2001", "LCV-2001", 30.0, 2.0, 300, direct=True,
          description="V-201 KO drum level")
        A("TIC-2002", "TT-2002", "FCV-2003", 60.0, 1.5, 180, direct=True,
          description="C1 discharge temperature")
        # Discharge pressure constraint: high-selected with the anti-surge
        # controller onto the recycle valve, so a blocked discharge opens
        # recycle even with healthy surge margin.
        A("PIC-2002", "PT-2002", "", 24.0, 2.0, 120, direct=True,
          description="C1 discharge pressure constraint")
        A("TIC-2001", "TT-2002", "", 185.0, 1.5, 120, direct=True,
          description="C1 discharge temperature constraint")
        # Loop-inventory control, the H2-makeup half of the gas balance:
        # the reactor consumes hydrogen, the makeup replaces it, and the
        # loop's pressure (taken on the discharge, below the PIC-2002
        # constraint) is the inventory measure. Reverse acting: pressure
        # sagging below target opens the header makeup. The 18 barg
        # target is what the machine's real curve delivers at a 1.9
        # ratio - the old dead-ended discharge read 52 barg only because
        # nothing drained it; attach the forward line to H1 and that
        # number is exposed as integrator memory, not head.
        A("PIC-2003", "PT-2002", "PCV-2003", 18.0, 1.5, 400,
          description="C1 loop inventory via H2 makeup")

        # ------------------------------------------------------------ U300
        # The full fired-heater scheme of the schedule: TIC-3001 outlet
        # temperature cascades to FIC-3001 fuel flow, with the FY-3001
        # charge-rate feedforward riding the master so a feed change moves
        # fuel before the temperature ever sees it. FIC-3003 holds combustion
        # air on the damper; the FY-3002 cross-limits are applied in the
        # scan (air demand is the HIGHER of what the firing demand and the
        # measured fuel need, fuel demand the LOWER of what is asked and
        # what the measured air can burn). AIC-3001 trims the excess-air
        # target from flue oxygen, TIC-3004 low-selects fuel away on tube
        # skin, and a sagging fuel supply pressure cuts firing back before
        # the burners starve.
        t1001 = self.db["FT-1001"]
        A("FIC-3001", "FT-3001", "FCV-3001", 1600.0, 0.062, 30,
          description="H1 fuel gas flow", master="TIC-3001", bkcal_pv=True)
        A("TIC-3001", "TT-3001", "", 349.0, 1.0, 300,
          description="H1 outlet temperature", sp_rate=0.5,
          ff_tag="FT-1001", ff_gain=1.0 * 100.0 / (t1001.hi - t1001.lo))
        A("FIC-3003", "FT-3003", "FCV-3004", 22.0, 0.93, 45,
          description="H1 combustion air flow", master="TIC-3001",
          bkcal_pv=True, out_lo=20.0)
        # High oxygen means too much air, so the trim is reverse acting:
        # getting this sign wrong is positive feedback that starves the box.
        A("AIC-3001", "AT-3001", "", 3.3, 4.0, 240,
          description="H1 flue oxygen trims the air ratio")
        # E5 charge preheat: the bypass holds the exchanger outlet, so
        # H1 fires only the last leg. More bypass = colder = direct.
        A("TIC-3005", "TT-3007", "TCV-3002", 160.0, 1.2, 240, direct=True,
          description="E5 charge preheat via bypass")
        # 560, not 600: the impingement runaway crosses 100 degrees a
        # minute, and a constraint that engages 40 under the SIS trip is
        # a constraint that has already lost. 80 degrees of margin lets
        # it actually cut fuel before C13 does it the hard way.
        A("TIC-3004", "TT-3005", "", 560.0, 2.0, 120,
          description="H1 tube skin constraint on firing")
        A("PIC-3002", "PT-3003", "SC-3001", -2.5, 3.0, 120, direct=True,
          description="H1 draft to ID fan", out_lo=25.0)
        A("TIC-3002", "TT-3002", "FCV-3005", 349.0, 1.0, 300, direct=True,
          description="H1 pass 1 outlet, pass balancing")
        A("TIC-3003", "TT-3003", "FCV-3006", 349.0, 1.0, 300, direct=True,
          description="H1 pass 2 outlet, pass balancing")
        # Fuel oil header pressure runs split range under the valve
        # position controller: ZC-3001 trims the pressure target so valve A
        # rides at a workable opening whenever oil is in service.
        A("PIC-3001", "PT-3002", "", 8.0, 4.0, 200, direct=True,
          description="H1 fuel oil header pressure", master="ZC-3001",
          bkcal_pv=True,
          split=[("FCV-3002", 0.0, 50.0, 0.0, 100.0),
                 ("FCV-3003", 50.0, 100.0, 0.0, 100.0)])
        A("ZC-3001", "ZT-3002", "", 75.0, 0.2, 900, direct=True,
          description="FCV-3002 valve position controller")
        self.loops["PIC-3001"].pid.sp_lo_lim = 4.0

        # ------------------------------------------------------------ U400
        # The reactor chain of the schedule: AIC-4001 impurity (a slow,
        # dead-timed analyser) trims the bed temperature setpoint, TIC-4001
        # bed temperature (high select of both beds) cascades to FIC-4001
        # quench flow, and TIC-4002 is the runaway override that forces the
        # quench valve open past the flow loop on an excursion.
        A("FIC-4001", "FT-4001", "FCV-4001", 8.0, 0.8, 20,
          description="R1 quench gas flow", master="TIC-4001", bkcal_pv=True)
        A("TIC-4001", "TT-4002", "", 378.0, 0.5, 320, direct=True,
          description="R1 bed temperature via quench", master="AIC-4001",
          bkcal_pv=True)
        # The reactor's QUALITY axis (the reference's RC2/FC4): additive
        # dosing ratioed to the charge, its ratio trimmed from three
        # units downstream by QIC-5001 - a valve-position controller on
        # the T1 quality master's OUTPUT. When the column works hard
        # against heavies, the trim lightens the feed at its source, and
        # the correction walks back through the reactor, D3 and the
        # column before the analyser ever confirms it: the deadtime-
        # compensation laboratory, plant-wide.
        A("FIC-4002", "FT-4005", "FCV-4003", 25.0, 2.0, 60,
          description="R1 additive injection flow", master="RC-4001",
          bkcal_pv=True)
        A("RC-4001", "FT-1001", "", 0.0, 1.0, 60,
          description="Additive to charge ratio station")
        # Reverse acting: when the T1 quality master is working below its
        # mid-range target (dirty distillate asks for more reflux), the VPC
        # must INCREASE additive and lighten the feed.  Direct action did the
        # opposite and closed a slow positive-feedback loop through R1/D3/T1:
        # clean product drove still more additive, T1 draw fell, and both
        # columns walked toward their quality limits over an eight-hour soak.
        # The VPC is the outermost loop in a three-level hierarchy and must
        # be slower than the analyser and tray-temperature loops beneath it.
        # A one-hour reset outran the quality master and walked additive for
        # hours before the analyser path could answer.  Four hours keeps the
        # valve-position objective without turning it into a composition
        # disturbance generator.
        A("QIC-5001", "AT-5001", "", 50.0, 0.1, 14400,
          pv_span=(0.0, 100.0),
          description="T1 quality demand balances additive (VPC)")
        # Impurity falls as conversion rises, so more impurity asks for more
        # bed temperature: direct acting, and slow enough that the analyser's
        # seven-minute cycle and dead time cannot wind it up.
        A("AIC-4001", "AT-4001", "", 90.0, 0.3, 1800, direct=True,
          description="R1 product impurity trims bed temperature")
        A("TIC-4002", "TT-4002", "", 392.0, 3.0, 60, direct=True,
          description="R1 runaway override, fast quench")
        A("LIC-4001", "LT-4001", "LCV-4001", 50.0, 1.5, 420, direct=True,
          description="D3 level to T1")
        A("LIC-4002", "LT-4002", "LCV-4002", 30.0, 1.6, 420, direct=True,
          description="D3 interface, sour water draw")
        A("PIC-4001", "PT-4002", "PCV-4001", 39.0, 3.0, 320, direct=True,
          description="D3 pressure")
        A("TIC-4005", "TT-4005", "FCV-4002", 190.0, 1.2, 240, direct=True,
          description="D3 temperature via E-401 CW")

        # ------------------------------------------------- U500 / U600
        # The dual-composition scheme, both towers, with every master
        # writing a FLOW setpoint rather than a valve: drum level cascades
        # to distillate flow, sump level to bottoms flow, the tray
        # temperature master to reflux flow, and the stripping-section
        # differential temperature master to reboiler steam flow. The
        # analysers trim the temperature targets, the FY-n002 feed
        # feedforward and FY-n001 decoupler correct both heat-balance flow
        # setpoints in the scan, and PDIC-n001 low-selects steam away as
        # the column approaches flood. The differential temperature is a
        # derived PV, lower tray minus upper tray (TT-n004 - TT-n002): the
        # spread across the sections collapses when lights slip down the
        # column, it is monotonic in the steam as long as the top stays
        # clean (which the reflux cascade owns), and the pressure
        # correction is the same on both trays, so the dT master needs no
        # pressure compensation.
        # cold-start defaults measured at the rundown-topology line-out;
        # a snapshot adoption overrides all of them with the plant as found
        for n, tsp, dtsp, dtspan, psp, dsp, bsp in (
                (5, 129.0, 115.0, 200.0, 8.0, 18.0, 43.0),
                (6, 108.0, 94.0, 200.0, 5.5, 31.0, 22.0)):
            c = f"T{n-4}"
            # Averaging on the reflux drums (2026-09-02): a tight drum
            # master recovers level by slamming the draw far past the
            # feed's light content, the material balance answers with a
            # dirty overhead, the temperature cascade answers THAT with
            # reflux the condenser cannot cover, and the drum drains into
            # the next slam - a measured 50-minute plant-wide oscillation
            # through the recycle. The drum absorbs; the draw drifts.
            A(f"LIC-{n}001", f"LT-{n}001", "", 50.0,
              1.2 if n == 5 else 0.5, 900 if n == 5 else 2400,
              direct=True,
              description=f"{c} reflux drum level to distillate flow")
            A(f"FIC-{n}006", f"FT-{n}003", f"FCV-{n}002", dsp, 0.7, 45,
              description=f"{c} distillate flow", master=f"LIC-{n}001",
              bkcal_pv=True)
            # Averaging on the sumps: the T1 bottoms is the recycle back
            # to D1, and a tight sump loop is exactly how the snowball gets
            # pumped around the plant instead of riding out in the vessel.
            A(f"LIC-{n}002", f"LT-{n}002", "", 50.0,
              0.9 if n == 5 else 0.3, 600 if n == 5 else 2400,
              direct=True,
              description=f"{c} sump level to bottoms flow")
            A(f"FIC-{n}007", f"FT-{n}004", f"FCV-{n}003", bsp, 0.69, 45,
              description=f"{c} bottoms flow", master=f"LIC-{n}002",
              bkcal_pv=True)
            A(f"FIC-{n}001", f"FT-{n}002", f"FCV-{n}001", 90.0, 0.35, 45,
              description=f"{c} reflux flow", master=f"TIC-{n}001",
              bkcal_pv=True)
            # Upper tray temperature high means heavies climbing: more
            # reflux. The UPPER tray, deliberately: the middle tray blends
            # the rectifying and stripping states, so a dirty bottoms holds
            # it hot no matter what the reflux does and the master rails
            # chasing a variable its handle cannot move - which is exactly
            # how T2 ate its whole condensate as reflux and exported
            # nothing while its overhead read clean.
            A(f"TIC-{n}001", f"TT-{n}002", "", tsp,
              0.35 if n == 5 else 0.10, 1500 if n == 5 else 3600,
              direct=True,
              description=f"{c} upper tray temperature to reflux",
              master=f"AIC-{n}001", bkcal_pv=True, sp_rate=0.15)
            # Heavy key rising in the distillate asks for a COLDER tray:
            # reverse acting quality master, slow against the analyser lag.
            # Slowed 2026-09-02: with the T1 recycle live, the columns sit
            # inside a plant-wide feedback loop, and analyser masters
            # that were stable open-chain limit-cycled at ~50 min once it
            # closed. Deadtime is minutes and product residence is hours;
            # the quality trim belongs on the residence timescale.
            # Both columns at T2's gain (2026-09-05): on the material
            # scheme the master's proportional kick alone, 0.25 x 17 % of
            # span, was ten degrees of tray setpoint on T1 against an
            # in-spec composition range of a degree and a half.
            A(f"AIC-{n}001", f"AT-{n}001", "", 0.6,
              0.08, 7200 if n == 5 else 14400,
              description=f"{c} distillate quality trims tray temperature")
            A(f"FIC-{n}002", f"FT-{n}005", f"FCV-{n}004", 9.0, 3.6, 45,
              description=f"{c} reboiler steam flow", master=f"TDIC-{n}001",
              bkcal_pv=True)
            # Steam sharpens the profile and widens the spread: positive
            # process gain, reverse acting. A collapsing spread (lights
            # walking down the column) asks for more steam, and every rail
            # in the chain lands on the MORE-steam side, where the flood
            # override and the boiler cap already stand guard.
            # T2's light distillate feed runs its bottoms at the clean
            # clamp, where the dT-to-steam process gain flattens and can
            # invert (more steam only dirties the overhead and NARROWS
            # the spread).  Its inner dT controller is therefore direct
            # acting, and the outer bottoms analyser is reversed with it so
            # the cascade retains negative feedback.  The reboiler low-level
            # override guards the inventory while the deliberately slow
            # analyser master works through its dead time.
            A(f"TDIC-{n}001", f"TT-{n}004", "", dtsp,
              0.14 if n == 5 else 0.0083, 2000 if n == 5 else 12000,
              direct=n == 6,
              description=f"{c} tray differential temperature to steam",
              master=f"AIC-{n}002", bkcal_pv=True,
              sp_rate=0.1, pv_span=(0.0, dtspan))
            # Light key rising in the bottoms asks for a WIDER spread.
            # T2's bottoms leave at ~5 m3/h from a 24 m3 sump - five
            # hours of residence - so its quality master trims over
            # hours; trimming it on analyser-minutes whipsawed the dT
            # target rail to rail on the first re-routed soaks.
            A(f"AIC-{n}002", f"AT-{n}002", "", 1.2,
              0.25 if n == 5 else 0.08, 7200 if n == 5 else 14400,
              direct=n == 5,
              description=f"{c} bottoms quality trims the dT target")
            if n == 5:
                # T1 pressure runs split range: low output opens the hot
                # gas bypass to HOLD pressure, high output opens the vent.
                A("PIC-5001", "PT-5001", "", psp, 12.0, 120, direct=True,
                  description="T1 pressure, vent and hot gas bypass",
                  split=[("PCV-5002", 0.0, 50.0, 100.0, 0.0),
                         ("PCV-5001", 50.0, 100.0, 0.0, 100.0)])
            else:
                # T2 has no hot gas bypass, so its pressure is held through
                # CONDENSER DUTY: the high half of the master's output opens
                # the vent, the low half cuts the cooling water setpoint
                # (see _selectors). Without the low side the CW headroom
                # over-condenses and drags the column down to starvation.
                A(f"PIC-{n}001", f"PT-{n}001", "", psp, 1.75, 260,
                  direct=True, description=f"{c} pressure, vent and "
                  "condenser duty",
                  split=[(f"PCV-{n}001", 50.0, 100.0, 0.0, 100.0)])
            if n == 6:
                A(f"FIC-{n}003", f"FT-{n}006", f"FCV-{n}005", 300.0, 0.125, 60,
                  description=f"{c} condenser cooling water",
                  master=f"PIC-{n}001", bkcal_pv=True)
            else:
                A(f"FIC-{n}003", f"FT-{n}006", f"FCV-{n}005", 300.0, 0.125, 60,
                  description=f"{c} condenser cooling water")
            A(f"PDIC-{n}001", f"PDT-{n}001", "", 340.0 if n == 5 else 330.0,
              0.5, 60, description=f"{c} flooding constraint on steam")
            A(f"FIC-{n}004", f"FT-{n}007", f"FCV-{n}006", 15.0, 3.0, 30,
              description=f"P-{n}01 minimum flow protection")
            A(f"FIC-{n}005", f"FT-{n}008", f"FCV-{n}007", 12.0, 3.0, 30,
              description=f"P-{n}02 minimum flow protection")
        A("LIC-5003", "LT-5003", "LCV-5001", 30.0, 1.6, 420, direct=True,
          description="T1 drum water boot")
        # The reboilers may not be asked for more steam than the boiler can
        # carry with margin to its drum trip: a bottoms-quality master
        # winding against a separation limit saturates here instead of
        # walking B1 into PSHH-7001.
        for m in ("FIC-5002", "FIC-6002"):
            self.loops[m].pid.sp_hi_lim = self.STEAM_CAP_TPH
        self._ratio = {"RC-4001": 25.0 / 90.0}

        # ------------------------------------------------------------ U700
        # Three element drum level: level PI trimmed by steam flow
        # feedforward, the textbook boiler arrangement.
        # Three element drum level, done properly: the level master and the
        # steam feedforward set a feedwater FLOW target, and a flow slave
        # holds it. Without the slave the loop acts through the raw pump
        # curve, where falling drum pressure means more water at the same
        # valve: a flood lowers the pressure, which feeds the flood. The flow
        # controller is what breaks that feedback.
        t7 = self.db["FT-7001"]
        A("FIC-7001", "FT-7002", "FCV-7001", 37.0, 2.0, 40,
          description="B1 feedwater flow", master="LIC-7001", bkcal_pv=True)
        A("LIC-7001", "LT-7001", "", 0.0, 0.22, 240,
          description="B1 drum level, three element",
          ff_tag="FT-7001", ff_gain=100.0 / (t7.hi - t7.lo))
        # The boiler master of the schedule: PIC-7001 holds header pressure
        # by setting a firing demand that FIC-7002 fuel and FIC-7003 air
        # both follow, cross-limited the same way as the heater, with the
        # oxygen trim on the ratio and the CO override flooring the air.
        # PIC-7002 is the letdown, split-ranged above the master's setpoint
        # so it only lifts when firing alone cannot hold the header down.
        A("FIC-7002", "FT-7003", "FCV-7002", 2400.0, 0.027, 30,
          description="B1 fuel gas flow", master="PIC-7001", bkcal_pv=True)
        A("PIC-7001", "PT-7002", "", 36.0, 2.0, 200,
          description="MP steam header pressure, boiler master")
        A("FIC-7003", "FT-7004", "SC-7001", 28.0, 1.2, 45,
          description="B1 combustion air flow", master="PIC-7001",
          bkcal_pv=True, out_lo=25.0)
        # Total-duty firing, the reference's BC22: the PV is ENERGY -
        # gas flow times the SG-INFERRED heating value plus the oil -
        # and the output trims FUEL OIL against whatever the wild gas
        # cannot hold. Reverse acting; parked shut by its protective
        # zero setpoint until set_boiler_mode arms it. The SG inference
        # (their QY11) reads the paraffin line, so it is honestly WRONG
        # when the header runs hydrogen-rich - watch the oil trim carry
        # the inference error.
        A("FIC-7004", "FT-7005", "FCV-7005", 0.0, 1.0, 45,
          description="B1 fuel oil flow", master="BIC-7001",
          bkcal_pv=True)
        A("BIC-7001", "FT-7001", "", 0.0, 1.5, 300,
          description="B1 total firing duty via oil trim",
          pv_span=(0.0, 40.0))
        A("AIC-7001", "AT-7001", "", 3.0, 4.0, 240,
          description="B1 flue oxygen trims the air ratio")
        A("AIC-7002", "AT-7002", "", 400.0, 1.5, 90, direct=True,
          description="B1 carbon monoxide floors the air demand")
        A("PIC-7002", "PT-7002", "PCV-7001", 37.5, 3.0, 120, direct=True,
          description="MP header letdown, split with the boiler master")
        A("TIC-7001", "TT-7001", "TCV-7001", 360.0, 1.0, 200, direct=True,
          description="Superheat via desuperheater spray")
        A("LIC-7002", "LT-7002", "LCV-7001", 55.0, 1.5, 300,
          description="Deaerator level")
        A("CIC-7001", "CT-7001", "FCV-7004", 1800.0, 0.02, 600, direct=True,
          description="Drum conductivity via blowdown")

        # ------------------------------------------------------------ U800
        A("FIC-8001", "FT-8001", "FCV-8004", 40.0, 1.5, 60,
          description="Effluent discharge flow", master="LIC-8001",
          bkcal_pv=True)
        A("LIC-8001", "LT-8001", "", 55.0, 2.0, 300, direct=True,
          description="Neutralisation tank level")
        A("TIC-8001", "TT-8001", "TCV-8001", 45.0, 2.0, 240, direct=True,
          description="E4 effluent cooler")
        A("AIC-8001", "AT-8002", "", 7.0, 8.0, 240,
          description="Outlet pH, split range reagents",
          split=[("FCV-8003", 0.0, 45.0, 100.0, 0.0),      # acid, more when low OUT
                 ("FCV-8002", 55.0, 80.0, 0.0, 100.0),     # caustic fine
                 ("FCV-8001", 80.0, 100.0, 0.0, 100.0)])   # caustic coarse

        # Pressure-compensated temperature for the stripping loops: a tray
        # temperature only means a composition at the pressure it was chosen
        # for, so the working target rides with the column pressure at 7.5
        # degrees per bar. This is what unhooks the steam loop from the
        # pressure loop instead of letting them fight.
        # Pressure-compensated temperatures, applied as a correction on the
        # cascade setpoint the quality master sends down: a tray temperature
        # only means a composition at the pressure it was chosen for. The
        # anchors are set when the strategy adopts the running plant.
        self._pct_comp = {}
        self._pct_cas = {"TIC-5001": ["PT-5001", 8.0],
                         "TIC-6001": ["PT-6001", 5.5]}
        # Derived PVs: differential temperature masters read the spread
        # between two transmitters, not a single point.
        self._pv_diff = {"TDIC-5001": ("TT-5004", "TT-5002"),
                         "TDIC-6001": ("TT-6004", "TT-6002")}
        # General computed PVs (callable of the tag database), used by the
        # selectable anti-surge formulations and the VPC.
        self._pv_fn: Dict[str, Callable] = {}
        # the VPC's process variable IS another controller's output
        self._pv_fn["QIC-5001"] = (
            lambda d: float(self.loops["AIC-5001"].pid.out))

        # total firing duty in MW: gas at the SG-inferred heating value
        # (the paraffin line - deliberately blind to hydrogen) plus oil
        def _b1_duty(d):
            hv = max(20.0, min(50.0, 30.0
                               + (float(d["AT-0102"].value) - 0.55)
                               / 0.28 * 15.0))
            return (float(d["FT-7003"].value) * hv
                    + float(d["FT-7005"].value) * 41.0) / 3600.0
        self._pv_fn["BIC-7001"] = _b1_duty
        # Loop-shaping configuration on record, replayed by apply_state:
        # {module: {alg/comp/asc selections + the base config to restore}}
        self._shaping: Dict[str, dict] = {}
        # Which actuator the compressor capacity master moves (C1_MVS)
        self._c1_mv = "speed"
        # Each column's selected control scheme (COLUMN_SCHEMES) and its
        # Ryskamp flag, keyed by unit number: 5 is T1, 6 is T2.
        self._col_scheme: Dict[int, str] = {5: "energy", 6: "energy"}
        self._ryskamp_col: Dict[int, bool] = {5: False, 6: False}
        # Named soft sensors (shaping.Inferential) and which loop uses
        # which - the reference's build-your-own-inferential screen.
        self._inferentials: Dict[str, tuple] = {}
        self._infer_used: Dict[str, str] = {}
        # B1 operating mode and the drum-level swell compensation gain
        self._b1_mode = "pressure"
        self._lvl_comp = 0.0
        # heating-value anchor for the H1 energy-firing feedforward
        self._lhv_anchor = 38.5
        # FY-n002 feed feedforward and FY-n001 decoupler anchors per column:
        # [feed, reflux, steam] at adoption. Corrections are ratios on the
        # anchored operating point, so they are exactly zero at adoption.
        self._col_ff = {5: [130.0, 90.0, 9.0, 59.0], 6: [80.0, 70.0, 8.0, 51.0]}   # feed, reflux, steam, draw
        # Feed-temperature and (T2) feed-composition feedforward anchors,
        # adopted at seed like the flow anchors above.
        self._ff_anchors: Dict[int, dict] = {}

        # Protective flow loops hold their ENGINEERING setpoints across a
        # plant adoption. Seeding SP = PV on a lined-up plant, whose
        # recycle valves are shut, would arm every pump's minimum-flow
        # protection at zero - silently disarmed, discovered the day the
        # charge flow is cut and the pump deadheads.
        self._protective = {"FIC-1002", "FIC-5004", "FIC-5005",
                            "FIC-6004", "FIC-6005",
                            # T2 pressure holds its DESIGN setpoint: adopting
                            # an under-pressured snapshot as the target would
                            # commission the starvation spiral it exists to
                            # prevent
                            "PIC-6001",
                            # anti-surge holds the DESIGN margin: adopting a
                            # comfortable as-found margin as the target would
                            # open recycle at healthy operating points and
                            # waste the machine's turndown
                            "UIC-2001",
                            # loop inventory holds its DESIGN pressure: the
                            # H2 makeup exists to restore the loop, not to
                            # ratify whatever inventory it woke up with
                            "PIC-2003",
                            # the VPC's target is mid-range BY DEFINITION:
                            # adopting the as-found demand as the target
                            # would freeze whatever imbalance it woke with
                            "QIC-5001",
                            # the duty trim stays PARKED at zero until
                            # set_boiler_mode arms it: adopting the
                            # as-found gas duty as its target would fire
                            # oil on every gas dip from the first scan
                            "BIC-7001"}
        # Product specifications are not as-found operating targets.  A
        # snapshot may be taken during an off-spec excursion, but closing the
        # DCS must continue to drive the analysers toward the commissioned
        # quality.  OUT is still adopted below for bumpless transfer; only the
        # engineering SP survives the adoption.
        self._commissioned_targets = {
            "AIC-5001", "AIC-5002", "AIC-6001", "AIC-6002"}
        # T2 condenser-duty base, set at adoption (see _selectors)
        self._cw_base: Dict[int, float] = {}

        # Constraint PVs that are the high select of two transmitters.
        self._pv_select = {"TIC-3004": ("TT-3005", "TT-3006"),
                           "TIC-4001": ("TT-4002", "TT-4003"),
                           "TIC-4002": ("TT-4002", "TT-4003")}
        # Override controllers keep their engineering setpoints across a
        # snapshot restore and park on the released side of their selector:
        # limiters that low-select ride at 100, floors that high-select at 0.
        self._constraints = {"TIC-3004": 100.0, "PIC-1001": 100.0,
                             "TIC-4002": 0.0, "PIC-2002": 0.0,
                             "TIC-2001": 0.0,
                             "AIC-7002": 0.0, "PIC-7002": 0.0,
                             "PDIC-5001": 100.0, "PDIC-6001": 100.0}

        # The configured alarm schedule overrides the default percent-of-
        # span limits wherever the Alarms sheet names the loop's PV, and the
        # sheet's priorities ride along for the faceplates.
        pri_map = {"Critical": "CRITICAL", "High": "WARNING",
                   "Advisory": "ADVISORY"}
        key = {"HI_HI": "hi_hi", "HI": "hi", "LO": "lo", "LO_LO": "lo_lo"}
        for loop in self.loops.values():
            pid = loop.pid
            pid.alarm_priority = {"hi_hi": "CRITICAL", "hi": "WARNING",
                                  "lo": "WARNING", "lo_lo": "CRITICAL"}
            delays = {}
            for typ, sp, pri, dead, delay in ALARMS.get(loop.pv_tag, []):
                if typ not in key:
                    continue
                setattr(pid, f"{key[typ]}_lim", float(sp))
                pid.alarm_priority[key[typ]] = pri_map.get(pri, "WARNING")
                if delay:
                    delays[key[typ]] = float(delay)
            # the block's own indication waits the table's on-delay, as the
            # alarm system does, so a faceplate never annunciates first
            pid.alarm_delay = delays

        # Speed loops legitimately live at the top of their span: the
        # default percent-of-span alarms would stand forever.
        for m in ("SIC-1001", "SIC-2001"):
            pid = self.loops[m].pid
            pid.hi_lim = pid.hi_hi_lim = float("inf")
        # Low carbon monoxide is clean combustion, not an alarm.
        co = self.loops["AIC-7002"].pid
        co.lo_lim = co.lo_lo_lim = float("-inf")

        # scan order: masters after slaves have published BKCAL, slaves after
        # masters have computed the setpoint. A simple two-pass ordering,
        # masters first, matches how a control module executes a cascade pair.
        masters = [l for l in self.loops.values() if l.master is None]
        slaves = [l for l in self.loops.values() if l.master is not None]
        self._order = masters + slaves

        # A master's external reset comes from its FIRST slave in build
        # order (the demand path). Resolved once here: searching the loop
        # list for it on every scan of every loop is quadratic in loops and
        # what made the 20x speed factor unreachable.
        self._bk_source = {}
        self._cas_offset = getattr(self, "_cas_offset", {})   # survives a rebuild, like the blocks
        for other in self._order:
            if other.master and other.master not in self._ratio:
                self._bk_source.setdefault(other.master, other)

        self._init_master_outputs()
        # Both columns are commissioned on the material-balance pairing:
        # upper tray temperature manipulates the distillate draw and the
        # reflux-drum level manipulates reflux. It is the only pairing
        # that can steer the draw fraction toward the feed's light content;
        # on the energy pairing the draw is whatever boilup minus reflux
        # leaves, and at z = 0.46 T1 drifted to a 41 % draw whose best
        # possible bottoms was 7.5 mol% - every closed-loop run then ended
        # in the LSLL-5001 trip at 200 minutes, and lifting the steam cap
        # only turned the trip into a permanent off-spec limit cycle
        # (2026-09-05). Energy and Ryskamp remain live, bumpless classroom
        # alternatives on either column.
        for n in (5, 6):
            self.set_column_scheme("material", column=n)

    def _init_master_outputs(self) -> None:
        """A master's OUT is its slave's setpoint. Born at zero it would slam
        the slave's SP to the bottom of scale on the first scan, which on
        the heater means driving the fuel valve shut and losing the flame.
        Initialise every master to reproduce the slave's as-found PV.

        Called at build AND from seed_from_plant: the application builds
        the controller before the snapshot is restored, so the build-time
        pass reads default tag values and the compressor's speed master
        (among others) adopted a near-zero demand, settling the machine
        into a collapsed-discharge equilibrium that external reset then
        held self-consistently.
        """
        for slave in (l for l in self._order if l.master is not None):
            m = self.loops.get(slave.master)
            if m is None or slave.master in self._ratio:
                continue
            # air slaves take their demand from the cross-limit calc, not
            # from the master's raw output: they must not initialise it.
            if slave.module in ("FIC-3003", "FIC-7003"):
                continue
            # a master re-pointed at a direct actuator (the compressor MV
            # menu) adopts the actuator position, not the slave image
            if m.out_tag or m.split:
                continue
            s = slave.pid
            pv = float(self.db[slave.pv_tag].value)
            if slave.module in self._pv_diff:
                a, b = self._pv_diff[slave.module]
                pv = float(self.db[a].value) - float(self.db[b].value)
            m.pid.out = max(0.0, min(100.0,
                                     (pv - s.pv_eu0) / s.pv_span * 100.0))

    # ------------------------------------------------------------- scanning
    SCAN_PERIOD = SCAN_PERIOD
    SCAN_CLASSES = SCAN_CLASSES

    def set_scan_class(self, module: str, period: float) -> float:
        """Give one module its scan class, in seconds (0.1 to 2 s).

        The system tick becomes the fastest class in use, never slower
        than the default, so the SIS and the tracking run at least as
        often as they always did. A module slower than the tick executes
        every ``period`` seconds of engine time and its block integrates
        over that period, exactly as a control module on a slower class.
        """
        loop = self.loops[module]
        p = float(period)
        if not (0.05 <= p <= 5.0):
            raise ValueError(f"scan class {p:g} s is outside 0.05 to 5 s")
        loop.scan_period = p
        loop._scan_accum = 0.0
        self._base_period = min([SCAN_PERIOD] + [l.scan_period for l in self._order])
        return p

    def scan_classes(self) -> Dict[str, float]:
        """Every module's scan class, seconds."""
        return {l.module: l.scan_period for l in self._order}

    def step(self, dt: float) -> None:
        db = self.db
        self._accum += dt
        # the system tick; not ``base``, which the cascade code below
        # rebinds to the master's demand
        tick = self._base_period
        if self._accum < tick - 1e-9:
            return
        dt, self._accum = self._accum, 0.0
        # The SIS is independent of the regulatory layer, exactly as on a
        # real plant: open loop makes the CONTROL passive, never the trips.
        self.esd.step(dt)
        if not self.enabled:
            for loop in self._order:
                pid = loop.pid
                tag = db.get(loop.pv_tag)
                if tag is not None:
                    pid.pv = float(tag.value)
                if loop.module in self._pv_diff:
                    a, b = self._pv_diff[loop.module]
                    pid.pv = float(db[a].value) - float(db[b].value)
                if loop.module in self._pv_fn:
                    pid.pv = float(self._pv_fn[loop.module](db))
                if loop.out_tag and loop.out_tag in db:
                    pid.out = float(db[loop.out_tag].value)
                pid._actual = pid.target_mode
            return
        for loop in self._order:
            if not loop.enabled:
                continue
            # a module on a slower class than the tick waits its turn and
            # then integrates over the time it waited
            if loop.scan_period > tick + 1e-9:
                loop._scan_accum += dt
                if loop._scan_accum < loop.scan_period - 1e-9:
                    continue
                ldt, loop._scan_accum = loop._scan_accum, 0.0
            else:
                ldt = dt
            pid = loop.pid

            # ratio stations: OUT is simply ratio * PV, published as a
            # cascade SP for the flow slave. Kept as a degenerate block so
            # the faceplate machinery treats it like everything else.
            if loop.module in self._ratio:
                pv = float(db[loop.pv_tag].value)
                slave_span = None
                pid.pv = pv
                r = self._ratio[loop.module]
                if loop.module == "RC-4001":
                    # the plant-wide VPC trims the additive ratio +-50 %
                    r *= 0.5 + self.loops["QIC-5001"].pid.out / 100.0
                pid.out = r * pv
                continue

            t = db[loop.pv_tag]
            pv = float(t.value)
            pv_good = int(t.quality) == 0
            if loop.module in self._pv_select:
                a, b = self._pv_select[loop.module]
                pv = max(float(db[a].value), float(db[b].value))
            if loop.module in self._pv_diff:
                a, b = self._pv_diff[loop.module]
                pv = float(db[a].value) - float(db[b].value)
                pv_good = pv_good and int(db[b].quality) == 0
            if loop.module in self._pv_fn:
                pv = float(self._pv_fn[loop.module](db))

            # loop shaping around the block: scheduled gain, then the
            # deadtime predictor's PV correction (percent domain)
            if loop.gain_fn is not None:
                pid.gain = float(loop.gain_fn(pid))
            if loop.predictor is not None:
                comp = self._shaping.get(loop.module, {}).get("comp", {})
                atag = comp.get("adapt_tag")
                if atag:
                    # adaptive model: transport deadtime follows flow
                    f = max(float(db[atag].value), 1.0)
                    loop.predictor.set_theta(
                        comp["theta"] * comp["adapt_ref"] / f)
                pvp = (pv - pid.pv_eu0) / pid.pv_span * 100.0
                pvp = loop.predictor.correct(pvp, pid.out, ldt)
                pv = pid.pv_eu0 + pvp / 100.0 * pid.pv_span

            if loop.module in self._pct_comp:
                ptag, pnom, base = self._pct_comp[loop.module]
                pid.sp = base + 7.5 * (float(db[ptag].value) - pnom)

            cas = None
            if loop.master:
                m = self.loops[loop.master].pid
                # master OUT is percent; a cascade SP arrives in the slave's
                # engineering units, spanned over the slave's PV scale.
                if loop.master in self._ratio:
                    cas = self.loops[loop.master].pid.out
                else:
                    cas = pid.pv_eu0 + m.out / 100.0 * pid.pv_span
                base = cas
                cas = self._selectors(loop, cas)
                # What the selectors ADDED to the master's own demand
                # (feedforwards, pressure compensation, overrides). It is
                # taken back out of the BKCAL the master receives, below:
                # external reset re-initialises the master to the slave's
                # setpoint every scan, and an offset left in that image is
                # added again next scan - a constant feedforward ratcheted
                # T2's steam 0.17 t/h every hour for two days and held T1's
                # on its cap (docs/Refinery_Basis_Campaign.md, the 72-hour
                # soak). The block does the same for its own FF input.
                self._cas_offset[loop.module] = cas - base

            ff = float(db[loop.ff_tag].value) if loop.ff_tag else 0.0

            # a master with a slave gets the slave's BKCAL for external
            # reset - unless it has been re-pointed at a direct actuator
            # (the compressor MV menu), where the slave image is stale
            bk = bk_lim = None
            src = (None if (loop.out_tag or loop.split)
                   else self._bk_source.get(loop.module))
            if src is not None:
                s = src.pid
                if loop.module == "PIC-6001":
                    # this cascade SP passes through the condenser-duty
                    # selector, so the BKCAL must come back through the
                    # selector's INVERSE - the linear span map hands the
                    # reset network the wrong image of the slave (86 m3/h
                    # reads as 12 %, which through the selector means a
                    # quartered CW setpoint) and external reset then drags
                    # the master into the very runaway it should prevent.
                    bk = self._cw_out_from_flow(s.bkcal_out)
                elif loop.module == "TIC-3001":
                    # The fuel slave's cascade demand is corrected for the
                    # inferred heating value in _selectors.  Return BKCAL
                    # through that map's inverse; otherwise a normal change
                    # in fuel density looks like a downstream limit and the
                    # temperature master retains a standing offset.  Actual
                    # air/skin/supply constraints still return the lower
                    # achieved flow and therefore still stop reset.
                    lhv_scale = max(0.85, min(
                        1.25, self._lhv_anchor /
                        max(float(db["AY-3001"].value), 20.0)))
                    bk = ((s.bkcal_out / lhv_scale - s.pv_eu0)
                          / s.pv_span * 100.0)
                else:
                    bk = ((s.bkcal_out - self._cas_offset.get(src.module, 0.0)
                           - s.pv_eu0) / s.pv_span * 100.0)
                bk_lim = s.bkcal_limit

            out = pid.step(ldt, pv, pv_good, cas_in=cas,
                           bkcal_in=bk, bkcal_in_limit=bk_lim or "",
                           ff_val=ff)

            if loop.out_cond:
                # conditioning shapes the valve signal only; the block,
                # its BKCAL and the faceplate keep the raw OUT
                kc = loop.out_cond
                out = 100.0 * (1.0 - kc) * out / max(100.0 - kc * out,
                                                     1e-6)

            if loop.split:
                for tag, lo, hi, v0, v1 in loop.split:
                    if out <= lo:
                        val = v0
                    elif out >= hi:
                        val = v1
                    else:
                        f = (out - lo) / max(hi - lo, 1e-9)
                        val = v0 + f * (v1 - v0)
                    if loop.module == "AIC-8001" and tag != "FCV-8003":
                        # the RC-8001 structure of the reference: caustic
                        # dosing rides the effluent flow, so a load change
                        # is answered by ratio before pH ever moves
                        val = min(100.0, val * max(0.3, min(
                            1.6, float(db["FT-8001"].value) / 40.0)))
                    self._write(tag, val)
            elif loop.out_tag:
                # High-select overrides land at the valve, past the loop that
                # normally owns it: the runaway override can force the quench
                # open and the discharge constraint can force recycle open.
                if loop.module == "UIC-2001":
                    out = max(out, self.loops["PIC-2002"].pid.out,
                              self.loops["TIC-2001"].pid.out)
                elif loop.module == "FIC-4001":
                    out = max(out, self.loops["TIC-4002"].pid.out)
                self._write(loop.out_tag, out)

    def _write(self, tag: str, value: float) -> None:
        """Outputs leave through the bus when one is wired."""
        if self.bus is not None:
            self.bus.write_from_dcs(tag, value, source="internal")
        else:
            self.db[tag].value = value

    # ------------------------------------------------- selectors and limits
    def _cw_out_from_flow(self, flow: float) -> float:
        """Inverse of the FIC-6003 condenser-duty selector: the PIC-6001
        output percent whose scaled CW setpoint equals ``flow``. Used to
        seed the master and to hand its external-reset network the right
        image of the slave."""
        base = self._cw_base.get(6, 0.0)
        if base <= 1e-6:
            return 50.0
        scale = flow / base
        if scale <= 1.0:
            return 50.0 * max(scale, 0.15)
        return 50.0 + 50.0 * min((scale - 1.0) / 0.6, 1.0)

    def _excess(self, module: str) -> float:
        """The O2 trim output mapped onto the excess-air target, 1.05-1.25."""
        return 1.05 + 0.002 * self.loops[module].pid.out

    def _selectors(self, loop: Loop, cas: float) -> float:
        """The FY-3002 / FY-7002 cross-limits and the override selectors,
        applied to a slave's incoming cascade setpoint each scan."""
        db, name, pid = self.db, loop.module, loop.pid
        if (name in ("FIC-5001", "FIC-6001")
                and self._ryskamp_col[int(name[4])]):
            # Ryskamp: the temperature master's output is a REFLUX RATIO
            # demand, so the reflux setpoint rides the measured draw -
            # a drum-level move takes the reflux with it and never
            # disturbs the composition loop.
            n = name[4]
            m = self.loops[f"TIC-{n}001"].pid
            cas = (m.out / 100.0 * self.RYSKAMP_RMAX
                   * float(db[f"FT-{n}003"].value))
        if name in ("FIC-5002", "FIC-6002"):
            # Reboiler low-level protection: you cannot boil liquid the
            # sump does not have. Below 30 % sump the steam setpoint is
            # walked down proportionally, reaching zero on an empty sump.
            # This is the clean-side counterpart of the PDIC flood
            # override, and it is what arrests the dT cascade's one-sided
            # regime on T2's light feed: with the bottoms already at the
            # clean clamp, extra steam cannot raise the lower tray
            # temperature - it only dirties the overhead, LOWERS the dT,
            # and the reverse-acting master answers with more steam while
            # the sump boils dry (measured: 50 % to empty in 45 minutes,
            # ending in the C21 fractionation trip).
            sump = float(db[f"LT-{name[4]}002"].value)
            if sump < 30.0:
                cas = min(cas, pid.pv_eu0 + max(sump, 0.0) / 30.0
                          * max(cas - pid.pv_eu0, 0.0))
        if name == "FIC-6007":
            # Minimum bottoms draw: a stopped rundown reads d_frac = 1 at
            # the split solver and rails the bottoms analyser, whipping
            # the quality cascade. Real plants hold a minimum rundown for
            # the same reason a sample line is never dead-ended. The sump
            # guard on the steam side protects the inventory.
            cas = max(cas, 2.0)
        if name == "FIC-1001":
            # Firing-limited charge, the dynamic form of the old static
            # 72 % ceiling: as the heater's temperature master runs out of
            # fuel authority, the charge demand is walked back below the
            # current flow until firing recovers. Without this the level
            # master drags the outlet down, TIC-3001 winds to maximum
            # fuel, and over-firing runs the tube skin from 375 to the
            # 640 degree SIS trip in five minutes.
            fuel_frac = self.loops["TIC-3001"].pid.out / 100.0
            if fuel_frac > 0.88:
                charge_pv = float(db["FT-1001"].value)
                cas = min(cas, charge_pv
                          + (0.92 - fuel_frac) * 0.5 * pid.pv_span)
            lim = self.loops["PIC-1001"].pid
            return min(cas, pid.pv_eu0 + lim.out / 100.0 * pid.pv_span)
        if name == "FIC-5006":
            # Plant-wide inventory: a filling feed drum high-selects the T1
            # distillate demand past the drum-level master - accumulation
            # can only leave through the distillate train.
            d1 = float(db["LT-1001"].value)
            if d1 > 70.0:
                floor = pid.pv_eu0 + (d1 - 70.0) / 25.0 * pid.pv_span
                cas = max(cas, min(floor, pid.pv_eu100))
        if name in ("FIC-5006", "FIC-6006"):
            # The material balance caps the draw. T1 makes a CLEAN
            # distillate, so its draw must stay under the feed's light
            # content - but with the additive axis live that content
            # MOVES, and a fixed cap flooded the reflux drum to HI_HI
            # the first time the feed came in light. The measured
            # overhead is the honest arbiter: a clean analyser earns
            # more draw, a dirty one tightens the cap - the balance
            # violation announces itself before it can persist. T2's R2
            # draw legitimately runs above its feed composition, so its
            # cap is only a wide safety net.
            n = name[4]
            if self._col_scheme.get(int(n)) == "material":
                # In the material scheme the temperature master moves the
                # draw, and a pure top pins the tray temperature where the
                # master cannot see a feed change; the draw rides the feed
                # by the seeded ratio, as reflux and steam already do, and
                # the master trims composition around that. Without it the
                # 10,000 bpd lineup left T1's light key to leave in the
                # bottoms while the draw sat at its seeded value.
                ff = self._col_ff[int(n)]
                if len(ff) > 3:
                    f0, d0 = ff[0], ff[3]
                    feed = float(db[f"FT-{n}001"].value)
                    # 60 % of the ratio, as the reflux and steam feedforwards
                    # are: the full ratio drove the recycle loop into a
                    # half-hour composition cycle in the 8 h soak
                    corr = self.DRAW_FF * (feed - f0) * d0 / max(f0, 1.0)
                    cas = cas + max(-0.3 * d0, min(0.3 * d0, corr))
            if n == "5":
                # Infer the feed's light content from the additive dosing the
                # DCS itself commands (both flows measured, design law 0.46
                # at 0.278 L/m3).  The old ``z - 0.02`` cap made the bottoms
                # specification impossible by construction: too much light
                # key was forced below the feed tray.  The physical upper
                # bound is z/xD, where xD is the commissioned overhead purity;
                # this leaves the quality cascades a narrow but feasible
                # material-balance window without permitting an impossible
                # overhead draw.
                add = float(db["FT-4005"].value)
                chg = max(float(db["FT-1001"].value), 1.0)
                z_inf = 0.46 + 0.5 * (add / chg - 25.0 / 90.0)
                impurity = max(0.001, min(
                    0.10, self.loops["AIC-5001"].pid.sp / 100.0))
                frac = max(0.40, min(0.50, z_inf / (1.0 - impurity)))
            else:
                # T2's feed is the T1 distillate at a fixed 0.64 light
                # content in the model; the same honest cap as T1, at the
                # composition the analyser is asked for, keeps the draw
                # from exceeding the light key in the feed.
                impurity = max(0.001, min(
                    0.10, self.loops["AIC-6001"].pid.sp / 100.0))
                frac = min(0.85, 0.64 / (1.0 - impurity))
            cap = frac * float(db[f"FT-{n}001"].value)
            cas = min(cas, max(cap, 2.0))
            return cas
        if name == "FIC-6003":
            # T2 condenser-duty pressure control around PIC-6001: output
            # below 50 % scales the CW setpoint down from its headroom
            # base, output above 50 % scales it UP to 1.6x. The vent only
            # passes non-condensables, so overpressure relief must come
            # from condenser duty - capping the scale at the base forced
            # the column to buy the missing duty with +7.5 degC/bar of
            # overhead temperature, which is an 11 barg equilibrium and a
            # standing HI_HI, measured on the first re-routed soak.
            base = self._cw_base.get(6, cas)
            m = self.loops["PIC-6001"].pid
            if m.out <= 50.0:
                return base * max(m.out / 50.0, 0.15)
            return base * (1.0 + 0.6 * (m.out - 50.0) / 50.0)
        if name == "FIC-3001":
            # Firing on ENERGY, not volume: the inferred heating value
            # scales the volumetric fuel demand (their BC1/TY13, on our
            # gas system) - a lean header asks for more Nm3 before the
            # temperature ever sags. ANCHORED at adoption like every
            # other feedforward, so it compensates CHANGES: an absolute
            # 38.5-referenced scale handed the closure a standing +25 %
            # fuel bias and ran the outlet 6 degrees hot. The cross-
            # limit still caps against measured air AFTER the scaling.
            cas = cas * max(0.85, min(1.25, self._lhv_anchor / max(
                float(db["AY-3001"].value), 20.0)))
            # Fuel side: never more fuel than the measured air burns at the
            # trimmed excess; skin override and supply pressure both cut.
            air = float(db["FT-3003"].value)                     # kNm3/h
            cas = min(cas, air * 1000.0 / (9.6 * self._excess("AIC-3001")))
            cas = min(cas,
                      self.loops["TIC-3004"].pid.out / 100.0 * pid.pv_eu100)
            avail = max(0.0, min(1.0, float(db["PT-0102"].value) / 2.0))
            cas = min(cas, pid.pv_eu100 * avail)
        elif name == "FIC-3003":
            # Air side: the HIGHER of the firing demand and the actual fuel,
            # with a small lead so the fuel low-selector is clear at steady
            # firing and only engages during a real upward transition.
            f = self.loops["FIC-3001"].pid
            demand = (f.pv_eu0 +
                      self.loops["TIC-3001"].pid.out / 100.0 * f.pv_span)
            need = max(demand, float(db["FT-3001"].value))       # Nm3/h
            cas = (need * 9.6 * self._excess("AIC-3001")
                   * _CROSS_LIMIT_LEAD / 1000.0)
        elif name == "FIC-7002":
            # the oil burns its share of the measured air first: the gas
            # allowance is what remains at the trimmed excess
            air = float(db["FT-7004"].value)                     # kNm3/h
            oil_air = float(db["FT-7005"].value) * 11.4
            ex = self._excess("AIC-7001")
            cas = min(cas, max(air * 1000.0 / ex - oil_air, 0.0) / 9.6)
            avail = max(0.0, min(1.0, float(db["PT-0103"].value) / 3.0))
            cas = min(cas, pid.pv_eu100 * avail)
        elif name == "FIC-7003":
            f = self.loops["FIC-7002"].pid
            demand = (f.pv_eu0 +
                      self.loops["PIC-7001"].pid.out / 100.0 * f.pv_span)
            need = max(demand, float(db["FT-7003"].value))
            cas = ((need * 9.6 + float(db["FT-7005"].value) * 11.4)
                   * self._excess("AIC-7001") * _CROSS_LIMIT_LEAD / 1000.0)
            # The CO constraint floors the air demand: smoke means more air
            # regardless of what the cross-limit believes is enough.
            co = self.loops["AIC-7002"].pid
            cas = max(cas, co.out / 100.0 * pid.pv_eu100)
        elif name in self._pct_cas:
            # Pressure compensation rides on the quality master's demand.
            ptag, anchor = self._pct_cas[name]
            cas = cas + 7.5 * (float(db[ptag].value) - anchor)
        elif name[:4] == "FIC-" and name[4] in "56" and name[5:] in ("001",
                                                                    "002"):
            n = int(name[4])
            f0, r0, s0 = self._col_ff[n][:3]
            feed = float(db[f"FT-{n}001"].value)
            if name.endswith("001"):
                # FY-n002 feed feedforward, reflux side, deliberately under-
                # compensated so measured-flow noise cannot whip the reflux,
                # and BOUNDED to a quarter of the anchored reflux: a swinging
                # upstream column must not be multiplied into this one.
                # ENERGY scheme only: there the reflux is the quality
                # handle. On the material and Ryskamp pairings it is the
                # drum-level output, and a feed feedforward on it moves the
                # reflux before the level has, the level loop takes it
                # back, and the exchange went round the recycle as a
                # two-hour limit cycle on both columns at 10,000 bpd (the
                # decoupler lesson again, docs/Refinery_Basis_Campaign.md).
                if self._col_scheme.get(n) == "energy":
                    corr = 0.6 * (feed - f0) * r0 / max(f0, 1.0)
                    cas = cas + max(-0.25 * r0, min(0.25 * r0, corr))
                # Reflux may not outrun the condensate: below thirty percent
                # drum level the demand is cut back with the inventory, which
                # is what keeps an upset from draining the drum and losing
                # reflux entirely.
                drum = float(db[f"LT-{n}001"].value)
                if drum < 30.0:
                    cas = min(cas, r0 * max(0.15, drum / 30.0))
            else:
                # Feedforward on the steam side plus the FY-n001 decoupler:
                # reflux moved by the quality loops (beyond what the feed
                # change explains) drags a little boilup with it. Bounded
                # like the reflux side.
                corr = 0.6 * (feed - f0) * s0 / max(f0, 1.0)
                cas = cas + max(-0.25 * s0, min(0.25 * s0, corr))
                if self._col_scheme.get(n) == "energy":
                    # Only where reflux IS the quality handle. On the
                    # material and Ryskamp pairings the reflux is the
                    # drum-level output, so it rises with the boilup;
                    # dragging steam after it closes a positive loop -
                    # boilup, condensate, level, reflux, steam - that
                    # ramped T2's reflux from 50 to 154 m3/h in 22 hours
                    # at a fixed draw, loaded the column liquid with
                    # light key, and let the stripping section go the
                    # hour the steam reached its 16.5 t/h cap: 0.01 mol%
                    # heavy overhead and 4.7 mol% light bottoms. Gating
                    # it cut the ramp five-fold on the same snapshot
                    # (24 h soak and experiment, 2026-09-05).
                    reflux = float(db[f"FT-{n}002"].value)
                    cas = cas + 0.12 * (s0 / max(r0, 1.0)) * (
                        reflux - r0 * feed / max(f0, 1.0))
                anch = self._ff_anchors.get(n, {})
                if "tf" in anch:
                    # feed-temperature feedforward: a colder feed
                    # condenses vapour at the tray, so the steam answers
                    # before the dT master ever sees the sag. Bounded.
                    corr_t = 0.010 * s0 * (anch["tf"]
                                           - float(db[f"TT-{n}001"].value))
                    cas = cas + max(-0.15 * s0, min(0.15 * s0, corr_t))
                if n == 6 and "z" in anch:
                    # feed-composition feedforward from the UPSTREAM
                    # analyser: AT-5001 measures the heavy content of
                    # what T2 is about to be fed. Bounded.
                    corr_z = 0.05 * s0 * (float(db["AT-5001"].value)
                                          - anch["z"])
                    cas = cas + max(-0.15 * s0, min(0.15 * s0, corr_z))
                # PDIC-n001 flooding: steam is low-selected away on dP.
                pd = self.loops[f"PDIC-{n}001"].pid
                cas = min(cas, pd.out / 100.0 * pid.pv_eu100)
        return cas

    # ---------------------------------------------------------- operations
    def loop_for_tag(self, tag: str) -> Optional[Loop]:
        """The module whose PV or OUT is this tag, for faceplate lookup."""
        for loop in self.loops.values():
            if tag in (loop.pv_tag, loop.out_tag, loop.module):
                return loop
        return None

    def all_auto(self) -> None:
        for loop in self.loops.values():
            if loop.module in self._ratio:
                continue
            loop.pid.set_mode(Mode.CAS if loop.master else Mode.AUTO)

    def set_loop_enabled(self, module: str, on: bool) -> bool:
        """Take one coded loop out of the scan, or put it back: a module
        downloaded from the builder that writes this loop's output replaces
        it, and one output has one writer. The block keeps its state, so
        the loop returns bumplessly. Both scans honour the flag; the native
        scanner recompiles on the next step."""
        loop = self.loops.get(module)
        if loop is None:
            return False
        if on and not loop.enabled and loop.out_tag and not loop.split                 and loop.out_tag in self.db:
            # the module moved the valve while the loop was passive: the
            # block adopts the field as found, as close_loop does, so the
            # first scan back continues from the live output
            self._adopt(loop.pid, out=float(self.db[loop.out_tag].value))
            master = self.loops.get(loop.master) if loop.master else None
            if master is not None and not (master.out_tag or master.split)                     and loop.master not in self._ratio and loop.pv_tag in self.db:
                # a cascade slave comes back at the flow it finds, and its
                # master re-adopts that flow as its demand, exactly as the
                # build-time pass does: no kick from a master that wound
                # while its slave was passive
                pv = float(self.db[loop.pv_tag].value)
                if loop.module in self._pv_diff:
                    a, b = self._pv_diff[loop.module]
                    pv = float(self.db[a].value) - float(self.db[b].value)
                s = loop.pid
                s.sp = s.sp_wrk = pv
                self._adopt(master.pid,
                            out=max(0.0, min(100.0, (pv - s.pv_eu0) / s.pv_span * 100.0)))
        loop.enabled = bool(on)
        self._native_dirty = True
        log.info("Loop %s %s", module, "enabled" if on else "taken passive for a downloaded module")
        return True

    @staticmethod
    def _adopt(pid, out: float) -> None:
        """Hand a block an output and let its next closed scan initialise
        the reset network to reproduce it, the bumpless entry the block
        performs whenever a loop closes. Through the state dictionary, so
        the native block does the same."""
        st = pid.capture_state()
        st["out"] = float(out)
        st["closed"] = False
        pid.apply_state(st)

    def all_manual(self) -> None:
        for loop in self.loops.values():
            loop.pid.set_mode(Mode.MAN)

    # -------------------------------------------------------- loop opening
    def open_loop(self) -> None:
        """Make the DCS passive: outputs hold, blocks track, SIS included."""
        self.all_manual()
        self.enabled = False
        if self.bus is not None:
            self.bus.release_dcs("internal")
        log.info("Control OPEN LOOP: DCS passive, outputs held as-is")

    def close_loop(self) -> None:
        """Adopt the running plant as found and take over, bumpless."""
        if self.bus is not None and not self.bus.register_dcs("internal"):
            log.warning("Cannot close the loop: output side held by %s",
                        self.bus.holder)
            return
        self.seed_from_plant()
        self.all_auto()
        self.enabled = True
        log.info("Control CLOSED LOOP: adopted as-found, %d loops in service",
                 len(self.loops))

    def seed_from_plant(self) -> None:
        """Adopt the running plant as-found: SP = PV, OUT = live valve.

        Called once after a snapshot restore so closing every loop is
        bumpless; operators then walk setpoints to targets at their own pace,
        the way a real handover from manual running is done.
        """
        for loop in self.loops.values():
            pid = loop.pid
            if loop.out_tag and loop.out_tag in self.db:
                pid.out = float(self.db[loop.out_tag].value)
            if loop.module in self._ratio:
                continue
            if loop.module in self._constraints:
                # Overrides keep their engineering setpoints; seeding SP = PV
                # would arm every constraint at whatever the plant happened
                # to be doing. They park on the released side instead.
                if not loop.out_tag:
                    pid.out = self._constraints[loop.module]
                continue
            if loop.module in self._protective:
                # minimum-flow protection keeps its commissioned setpoint;
                # adopting the shut recycle as the target would disarm it
                continue
            if loop.module in self._commissioned_targets:
                continue
            pv = float(self.db[loop.pv_tag].value)
            if loop.module in self._pv_diff:
                a, b = self._pv_diff[loop.module]
                pv = float(self.db[a].value) - float(self.db[b].value)
            if loop.module in self._pv_fn:
                pv = float(self._pv_fn[loop.module](self.db))
            if loop.master is None:
                pid.sp = pid.sp_wrk = pv
        # Masters re-adopt their slaves' RESTORED PVs: the build-time pass
        # ran before the snapshot existed. The specialty seeds below (O2
        # trims, split-range reproduction) overwrite their own masters
        # afterwards, so they still win.
        self._init_master_outputs()
        # O2 trims adopt the as-found excess-air ratio, so the air demand
        # the cross-limit computes on the first scan is the air the plant
        # is already getting: no oxygen dip on restore.
        for m, ftag, atag in (("AIC-3001", "FT-3001", "FT-3003"),
                              ("AIC-7001", "FT-7003", "FT-7004")):
            fuel = float(self.db[ftag].value)
            air = float(self.db[atag].value)
            if fuel > 1.0:
                x = air * 1000.0 / (9.6 * fuel)
                self.loops[m].pid.out = max(
                    0.0, min(100.0, (x - 1.05) / 0.002))
        # The split-range header master reproduces whichever valve is away
        # from its rest position, flare leg first.
        p01 = self.loops["PIC-0101"].pid
        flare = float(self.db["PCV-0102"].value)
        imp = float(self.db["PCV-0101"].value)
        p01.out = ((100.0 - flare) / 2.0 if flare > 1.0
                   else 50.0 + imp / 2.0)
        # T1 pressure: the lineup now lines the column AT its 8.5 design
        # point holding it with the hot gas bypass alone, so the seed
        # reproduces the as-found split position instead of forcing the
        # neutral middle (which slammed a part-open HGB shut at closure
        # and kicked the compensated temperature cascade half a bar's
        # worth on every start).
        p51 = self.loops["PIC-5001"].pid
        hgb = float(self.db["PCV-5002"].value)
        vnt = float(self.db["PCV-5001"].value)
        p51.out = ((100.0 - hgb) / 2.0 if hgb > 1.0
                   else 50.0 + vnt / 2.0)
        p51.sp = 8.5

        # The pressure compensated temperatures anchor to the as-found
        # pressures, so compensation starts at zero and the loops hold what
        # they find; the column feedforward and decoupler anchor to the
        # as-found feed, reflux and steam for the same reason.
        for mod, pa in self._pct_cas.items():
            pa[1] = float(self.db[pa[0]].value)
        self._lhv_anchor = max(20.0, float(self.db["AY-3001"].value))
        for n in list(self._col_ff):
            self._col_ff[n] = [max(1.0, float(self.db[f"FT-{n}001"].value)),
                               float(self.db[f"FT-{n}002"].value),
                               float(self.db[f"FT-{n}005"].value),
                               max(0.5, float(self.db[f"FT-{n}003"].value))]
            self._ff_anchors[n] = {
                "tf": float(self.db[f"TT-{n}001"].value)}
            if n == 6:
                self._ff_anchors[n]["z"] = float(self.db["AT-5001"].value)
            # Condenser cooling water holds headroom above the as-found
            # duty. The old temperature loop matched CW to duty implicitly;
            # a flow loop seeded exactly at duty caps condensation, and the
            # first boilup increase then drains the reflux drum.
            cw = self.loops[f"FIC-{n}003"].pid
            # 20 % over the as-found flow: 35 % put the 10,000 bpd seed past
            # what the water valve passes fully open (1,340 m3/h against a
            # 1,520 setpoint) and the flow loop was born saturated.
            base = min(cw.pv_eu100 * 0.95,
                       1.2 * float(self.db[f"FT-{n}006"].value))
            if n == 6:
                # T2's CW runs in cascade under the pressure master; the
                # selector's base must cover the DESIGN condenser duty, not
                # merely 1.35 times a lightly loaded handover.  The latter
                # saved 105 m3/h and capped PIC-6001 at 169 m3/h just as the
                # reboiler rose to 14 t/h: the pressure master railed, column
                # pressure climbed and product went off specification.  A
                # 300 m3/h base gives the master a 45-480 m3/h duty range;
                # _cw_out_from_flow still inverse-maps a low handover flow for
                # a bumpless closure.
                base = max(300.0, base)
                self._cw_base[6] = base
                # PIC-6001's output speaks through that selector, not the
                # linear cascade map _init_master_outputs assumes: seed it
                # so the scaled CW setpoint equals the as-found flow.
                # Seeded linearly (12 % for an 86 m3/h flow on a 700
                # span), closing the loop cut condensation to the
                # selector floor and T2 spiked 7 bar in minutes, dragging
                # the compensated temperature cascade to its walk ceiling
                # and doubling the steam draw - measured 2026-09-02.
                p6 = self.loops["PIC-6001"].pid
                p6.out = self._cw_out_from_flow(
                    float(self.db["FT-6006"].value))
            else:
                cw.sp = cw.sp_wrk = base
            # The temperature masters may only be walked three degrees
            # either side of the adopted operating point. The upper tray
            # reads bubble(0.5 (y_top + 0.85)), so the whole in-spec
            # composition range - heavy key 0 to 2.5 mol% in the overhead -
            # spans about 1.6 degrees of tray temperature at a fixed
            # profile; the quality master's output maps to a 0-250 degC
            # setpoint, one per cent of it 2.5 degrees. A twenty-degree
            # walk on that variable is a windup waiting to happen (sized
            # 2026-09-05; the 24 h soak's hour-22 event itself was the
            # steam decoupler, see FIC-n002, and the tray setpoint moved
            # under a degree during it). Three degrees rather than the
            # 1.6 because the profile is not fixed: the bottoms master
            # moves the whole column with the steam, and a master held to
            # a degree and a half could not reach the balance from a
            # handover a few tenths of a mol% off it. Beyond three the
            # operator decides, not the trim.
            tp = self.loops[f"TIC-{n}001"].pid
            pv = float(self.db[f"TT-{n}002"].value)
            tp.sp_lo_lim = pv - 3.0
            tp.sp_hi_lim = pv + 3.0
            # The dT master gets a bounded walk too, asymmetric: for a
            # reverse-acting master a setpoint BELOW the process cuts
            # steam, so the unsafe direction is down. Generous upwards
            # (more steam, caught by the flood override and boiler cap).
            td = self.loops[f"TDIC-{n}001"].pid
            a, b = self._pv_diff[f"TDIC-{n}001"]
            dpv = float(self.db[a].value) - float(self.db[b].value)
            td.sp_lo_lim = max(td.pv_eu0, dpv - 0.08 * td.pv_span)
            td.sp_hi_lim = min(td.pv_eu100, dpv + 0.40 * td.pv_span)
        # The VPC starts balanced: its output at mid-scale means "the
        # additive ratio as adopted", and the ratio adoption below then
        # reproduces the as-found dosing exactly.
        self.loops["QIC-5001"].pid.out = 50.0
        # Ratio stations adopt the as-found ratio for the same reason.
        for mod in self._ratio:
            src = self.loops[mod]
            f = float(self.db[src.pv_tag].value)
            slave = next(l for l in self.loops.values() if l.master == mod)
            d = float(self.db[slave.pv_tag].value)
            if f > 1.0:
                self._ratio[mod] = max(0.05, min(0.9, d / f))

        # Prime every block's DISPLAYED state to the adopted plant. The
        # first scan overwrites all of it, but until that scan runs a
        # restored display otherwise reads PV 0.00 in MAN across the
        # board - a healthy plant that looks dead on open.
        for loop in self.loops.values():
            pid = loop.pid
            pv = float(self.db[loop.pv_tag].value)
            if loop.module in self._pv_select:
                a, b = self._pv_select[loop.module]
                pv = max(float(self.db[a].value), float(self.db[b].value))
            if loop.module in self._pv_diff:
                a, b = self._pv_diff[loop.module]
                pv = float(self.db[a].value) - float(self.db[b].value)
            if loop.module in self._pv_fn:
                pv = float(self._pv_fn[loop.module](self.db))
            pid.pv = pv
            pid._actual = pid.target_mode

    # ------------------------------------------ loop shaping (WHC parity)
    # The Whitehouse-class algorithm menu, implemented AROUND the block:
    # nonlinear options schedule the block's GAIN parameter per scan,
    # deadtime compensation corrects the PV ahead of pid.step, and the
    # anti-surge formulations recompute the PV in a different coordinate
    # frame. The PID equation itself never changes.

    def set_algorithm(self, module: str, kind: str = "linear", **kw):
        """Select the loop's control algorithm.

        kinds: ``linear`` (default - restores the base gain),
        ``error_squared`` (mix_c), ``gap`` (gap_lo, gap_hi, k_gap),
        ``banded`` (bands=[[break_pct, gain], ...]), ``programmed``
        (k1, k2), ``slope`` (source, kp_target) where ``source`` names
        an entry in shaping registered slope sources - the linearised-PV
        strategy for the pH loop.
        """
        loop = self.loops[module]
        pid = loop.pid
        rec = self._shaping.setdefault(module, {})
        base = rec.setdefault("base_gain", pid.gain)
        if kind == "linear":
            loop.gain_fn = None
            pid.gain = base
            rec.pop("alg", None)
            if not rec or list(rec) == ["base_gain"]:
                self._shaping.pop(module, None)
            return
        if kind == "error_squared":
            loop.gain_fn = shaping.error_squared(
                base, float(kw.get("mix_c", 0.9)))
        elif kind == "gap":
            loop.gain_fn = shaping.gap(
                base, float(kw.get("gap_lo", 10.0)),
                float(kw.get("gap_hi", 10.0)), float(kw.get("k_gap", 0.1)))
        elif kind == "banded":
            bands = [(float(b), float(g)) for b, g in kw["bands"]]
            loop.gain_fn = shaping.banded(bands)
        elif kind == "programmed":
            loop.gain_fn = shaping.programmed(
                base, float(kw["k1"]), float(kw["k2"]))
        elif kind == "slope":
            fn = shaping.SLOPE_SOURCES[kw["source"]]
            loop.gain_fn = shaping.slope_scheduled(
                fn, float(kw.get("kp_target", 1.0)))
        else:
            raise ValueError(f"unknown algorithm {kind!r}")
        rec["alg"] = dict(kind=kind, **kw)

    def set_compensator(self, module: str, kind: str = "none",
                        k: float = 1.0, theta: float = 10.0,
                        tau: float = 60.0, lam: Optional[float] = None,
                        adapt_tag: str = "", adapt_ref: float = 0.0):
        """Deadtime compensation for the loop: ``smith`` (predictor,
        existing tuning kept), ``dahlin`` (predictor + synthesis tuning),
        ``imc`` (lambda tuning on the raw PV), ``none`` (restore).
        The FOPDT model is in loop units: k in %span/%out, times in s.
        ``adapt_tag``/``adapt_ref`` make the predictor ADAPTIVE: theta
        is the model deadtime AT the reference flow, and it scales
        inversely with the named flow tag each scan - transport delay
        follows throughput."""
        loop = self.loops[module]
        pid = loop.pid
        rec = self._shaping.setdefault(module, {})
        rec.setdefault("base_tuning", [pid.gain, pid.reset])
        if kind == "none":
            loop.predictor = None
            pid.gain, pid.reset = rec.pop("base_tuning")
            rec.pop("comp", None)
            if not rec:
                self._shaping.pop(module, None)
            return
        model = shaping.FOPDT(float(k), float(theta), float(tau))
        lam = float(lam) if lam is not None else max(0.5 * model.theta,
                                                     0.1 * model.tau)
        pred, g, r = shaping.make_compensator(kind, model, lam)
        loop.predictor = pred
        if g is not None:
            pid.gain, pid.reset = g, r
        rec["comp"] = dict(kind=kind, k=model.k, theta=model.theta,
                           tau=model.tau, lam=lam)
        if adapt_tag:
            rec["comp"]["adapt_tag"] = adapt_tag
            rec["comp"]["adapt_ref"] = float(adapt_ref)

    def set_output_conditioning(self, module: str, k: float = 0.0):
        """Characterize the valve signal: OP* = 100(1-k)OP/(100-k*OP),
        the classic counter to an equal-percentage installed
        characteristic. k in [0, 0.95]; 0 removes it. The block, its
        BKCAL and the faceplate keep the raw OUT."""
        k = float(k)
        if not 0.0 <= k <= 0.95:
            raise ValueError("k must be in [0, 0.95]")
        self.loops[module].out_cond = k
        rec = self._shaping.setdefault(module, {})
        if k:
            rec["ocond"] = k
        else:
            rec.pop("ocond", None)
            if not rec:
                self._shaping.pop(module, None)

    # The four-handle compressor capacity menu: which actuator PIC-2001
    # moves. Each entry: (out_tag, split, gain, reset, out_lo) - the
    # per-MV tuning rows of the reference's config dialog. Guide vanes
    # act through a split map because MORE degrees is LESS capacity.
    C1_MVS = {
        "speed":     ("", None, 5.0, 200.0, 25.0),
        "suction":   ("FCV-2002", None, 2.0, 240.0, 0.0),
        "discharge": ("FCV-2004", None, 2.5, 240.0, 0.0),
        "vanes":     ("", [("GV-2001", 0.0, 100.0, 40.0, 0.0)],
                      3.0, 240.0, 0.0),
    }

    def set_compressor_mv(self, kind: str = "speed",
                          _park: bool = True) -> None:
        """Select the manipulated variable for C1 capacity control.

        ``speed`` (default) restores the PIC-2001 -> SIC-2001 cascade.
        The other three re-point PIC-2001 at a direct actuator, hold the
        speed loop in AUTO at its as-found setpoint, and park the
        actuators the selection is not using at their full-capacity
        rest (valves wide, vanes at zero degrees).
        """
        if kind not in self.C1_MVS:
            raise ValueError(f"unknown compressor MV {kind!r}")
        pic = self.loops["PIC-2001"]
        sic = self.loops["SIC-2001"].pid
        out_tag, split, gain, reset, out_lo = self.C1_MVS[kind]
        # park every non-selected handle at full capacity first (skipped
        # on a snapshot replay, where the positions are restored state)
        if _park:
            rests = {"FCV-2002": 100.0, "FCV-2004": 100.0, "GV-2001": 0.0}
            keep = out_tag or (split[0][0] if split else "")
            for tag, rest in rests.items():
                if tag != keep:
                    self._write(tag, rest)
        pic.out_tag = out_tag
        pic.split = split or []
        pic.pid.gain, pic.pid.reset = gain, reset
        pic.pid.out_lo_lim = out_lo
        if kind == "speed":
            # back to the cascade: reproduce the as-found speed and
            # hand the slave its master again
            spd = float(self.db["ST-2001"].value)
            pic.pid.out = max(0.0, min(100.0,
                                       (spd - sic.pv_eu0) / sic.pv_span
                                       * 100.0))
            sic.set_mode(Mode.CAS)
        else:
            # hold speed where it is; adopt the actuator as-found
            sic.sp = sic.sp_wrk = float(self.db["ST-2001"].value)
            sic.set_mode(Mode.AUTO)
            if split:
                pos = float(self.db[split[0][0]].value)
                lo, hi, v0, v1 = split[0][1], split[0][2], split[0][3], \
                    split[0][4]
                f = (pos - v0) / max(v1 - v0, 1e-9) if v1 != v0 else 0.0
                pic.pid.out = max(0.0, min(100.0, lo + f * (hi - lo)))
            else:
                pic.pid.out = float(self.db[out_tag].value)
        self._c1_mv = kind

    def set_boiler_mode(self, kind: str = "pressure",
                        duty_mw: float = 24.0) -> None:
        """B1's operating mode. ``pressure`` (default, commissioned):
        the header master fires the gas, oil parked, B2 invisible.
        ``baseload``: the gas is FROZEN at its as-found flow, BIC-7001
        holds total duty at ``duty_mw`` by trimming fuel oil, and the
        header settles the half-bar down to where B2's pressure support
        picks up the swing - which is what base-loading a boiler on a
        shared header really does."""
        gas = self.loops["FIC-7002"].pid
        duty = self.loops["BIC-7001"].pid
        if kind == "pressure":
            duty.sp = duty.sp_wrk = 0.0
            gas.set_mode(Mode.CAS)
            self.loops["PIC-7001"].pid.set_mode(Mode.AUTO)
        elif kind == "baseload":
            gas.sp = gas.sp_wrk = float(self.db["FT-7003"].value)
            gas.set_mode(Mode.AUTO)
            duty.sp = duty.sp_wrk = float(duty_mw)
            # the header master's demand path also feeds the air
            # cross-limit: park it at the frozen gas image so the air
            # side follows the real firing, not a winding pressure PI
            pic = self.loops["PIC-7001"].pid
            pic.set_mode(Mode.MAN)
            pic.out = max(0.0, min(100.0, float(self.db["FT-7003"].value)
                                   / gas.pv_span * 100.0))
        else:
            raise ValueError(f"unknown boiler mode {kind!r}")
        self._b1_mode = kind

    def set_level_compensation(self, k: float = 6.0) -> None:
        """Swell compensation on the drum-level master: the control PV
        is the indicated level minus k mm per t/h of steam-flow
        DEVIATION (a 40 s washout), so an inverse-response swell no
        longer drives the feedwater the wrong way first. k = 0 removes
        it and the loop runs on the raw transmitter; the estimate never
        matches the drum's true swell gain exactly, which is the
        exercise."""
        if k <= 0.0:
            self._pv_fn.pop("LIC-7001", None)
            self._lvl_comp = 0.0
            return
        wash = shaping.Lag(40.0, float(self.db["FT-7001"].value))

        def fn(d, k=float(k), wash=wash):
            steam = float(d["FT-7001"].value)
            return (float(d["LT-7001"].value)
                    - k * (steam - wash.step(steam, self.SCAN_PERIOD)))
        self._pv_fn["LIC-7001"] = fn
        self._lvl_comp = float(k)

    def define_inferential(self, name: str, terms, transform: str = "none",
                           bias_tag: str = "", bias_filter: float = 0.98,
                           theta: float = 0.0, lag1: float = 0.0,
                           lag2: float = 0.0) -> None:
        """Build (or rebuild) a named soft sensor from tags and algebra -
        the reference's inferential-definition screen. See
        shaping.Inferential for the term syntax, the transform and the
        analyser bias update."""
        cfg = dict(terms=[[str(e), float(c)] for e, c in terms],
                   transform=transform, bias_tag=bias_tag,
                   bias_filter=bias_filter, theta=theta,
                   lag1=lag1, lag2=lag2)
        self._inferentials[name] = (shaping.Inferential(**cfg), cfg)

    def use_inferential(self, module: str, name: str) -> None:
        """Point a loop's PV at a defined inferential (its faceplate
        keeps the loop's engineering units - range them to match).
        Pass name='' to restore the loop's own transmitter."""
        if not name:
            self._pv_fn.pop(module, None)
            self._infer_used.pop(module, None)
            return
        inf = self._inferentials[name][0]
        self._pv_fn[module] = (
            lambda d, inf=inf: inf.value(d, self.SCAN_PERIOD))
        self._infer_used[module] = name

    def inferential_value(self, name: str) -> float:
        """Last computed value of a named inferential."""
        return float(self._inferentials[name][0].last)

    COLUMN_SCHEMES = ("energy", "material", "ryskamp")
    T2_SCHEMES = COLUMN_SCHEMES
    RYSKAMP_RMAX = 4.0        # TIC-n001 output at 100 % means L/D = 4
    # The reboiler steam cap, a margin against B1's drum: 16.5 t/h was
    # T1's cap at the 46 m3/h basis (a 65 t/h boiler). At the 10,000 bpd
    # basis T1 needs about 27 t/h to hold its 45 % draw at a reflux ratio
    # of 4.5 (scaling the old cap by the feed gave 24, and the drum
    # level then cut the draw and sent the light key out the bottom);
    # 30 t/h against the 80 t/h boiler keeps the same margin.
    STEAM_CAP_TPH = 30.0
    # Share of a feed change the draw follows at once in the material
    # scheme (reflux and steam ride the feed at 60 %). The scanner
    # reads it when it compiles, so a change needs a recompile.
    DRAW_FF = 0.6
    # Re-sizing to the 10,000 bpd basis (docs/Refinery_Basis_Campaign.md)
    # changed process gains: a slave flow loop's valve Cv, and the range a
    # master's percent output maps onto. Each affected gain above was
    # divided by that factor so every loop keeps the gain it was
    # commissioned with; the lineup re-seeds outputs from the plant.

    @property
    def _t2_scheme(self) -> str:
        """T2's scheme, the reference classroom's menu."""
        return self._col_scheme[6]

    @property
    def _ryskamp(self) -> bool:
        return self._ryskamp_col[6]

    def set_column_scheme(self, kind: str = "energy", column: int = 6
                          ) -> None:
        """A column's control-scheme menu, the reference classroom's core.

        ``column`` is the unit number, 5 for T1 and 6 for T2; T2 is the
        default because it is the classroom's reference column.
        ``energy``: drum level draws distillate,
        the temperature master moves REFLUX. ``material``: the pairings
        swap - temperature moves the DRAW (composition by material
        balance), level takes the reflux; this is the commissioned default
        on both columns. ``ryskamp``: level on the
        draw, and the temperature master commands the REFLUX RATIO
        L/D - reflux follows the draw automatically, which decouples
        the composition loop from drum-level moves. Switching is
        bumpless: the swapped masters re-initialise to reproduce their
        new slaves' as-found flows, and Ryskamp adopts the as-found
        L/D. Steam-side pairing, the dT master, PCT and every override
        stay exactly as commissioned.
        """
        if kind not in self.COLUMN_SCHEMES:
            raise ValueError(f"unknown scheme {kind!r}")
        n = int(column)
        if n not in self._col_scheme:
            raise ValueError(f"unknown column {column!r}")
        c = f"T{n - 4}"
        dist = self.loops[f"FIC-{n}006"]
        refl = self.loops[f"FIC-{n}001"]
        lic_loop = self.loops[f"LIC-{n}001"]
        tic_loop = self.loops[f"TIC-{n}001"]
        lic = lic_loop.pid
        tic = tic_loop.pid
        self._ryskamp_col[n] = kind == "ryskamp"
        # Reflux has negative process gain to upper-tray temperature, while
        # distillate draw has positive gain.  Swapping the manipulated flow
        # without swapping controller action turns the material scheme into
        # positive feedback.
        tic.direct_acting = kind != "material"
        if kind == "material":
            dist.master, refl.master = f"TIC-{n}001", f"LIC-{n}001"
            lic_desc = f"{c} reflux drum level to reflux flow"
            tic_desc = f"{c} upper tray temperature to distillate flow"
        else:
            dist.master, refl.master = f"LIC-{n}001", f"TIC-{n}001"
            lic_desc = f"{c} reflux drum level to distillate flow"
            tic_desc = (f"{c} upper tray temperature to reflux ratio"
                        if kind == "ryskamp" else
                        f"{c} upper tray temperature to reflux flow")
        lic_loop.description = lic.description = lic_desc
        tic_loop.description = tic.description = tic_desc
        # external reset follows the new demand paths
        self._bk_source = {}
        self._cas_offset = getattr(self, "_cas_offset", {})   # survives a rebuild, like the blocks
        for other in self._order:
            if other.master and other.master not in self._ratio:
                self._bk_source.setdefault(other.master, other)

        def img(m, slave):
            s = slave.pid
            m.out = max(0.0, min(100.0, (
                float(self.db[slave.pv_tag].value) - s.pv_eu0)
                / s.pv_span * 100.0))

        if self._ryskamp_col[n]:
            ld = (float(self.db[f"FT-{n}002"].value)
                  / max(float(self.db[f"FT-{n}003"].value), 0.5))
            tic.out = max(0.0, min(100.0,
                                   ld / self.RYSKAMP_RMAX * 100.0))
            img(lic, dist)
        elif kind == "material":
            img(tic, dist)
            img(lic, refl)
        else:
            img(lic, dist)
            img(tic, refl)
        # The process path (and, for TIC, its action) just changed.  Rebuild
        # each reset bias on its first scan so the advertised live scheme
        # change is genuinely bumpless.
        lic._was_closed = False
        tic._was_closed = False
        self._col_scheme[n] = kind

    ASC_FORMS = ("margin", "flow_speed", "flow_dp", "pressure_ratio",
                 "dp", "pd", "power_flow")

    def set_asc_formulation(self, kind: str = "margin", _cal=None):
        """Anti-surge PV formulation for UIC-2001, the Whitehouse menu
        computed from OUR transmitters.

        ``margin`` (default) - UY-2001, the model's MW-compensated
        surge margin. ``flow_speed`` - suction flow above the surge
        line drawn against SPEED (blind to gas MW shifts, which is the
        lesson). ``flow_dp`` - flow-squared against polytropic head
        taken as Pd-Ps. ``pressure_ratio`` - flow-squared against
        pressure ratio (sensitive to suction-pressure moves). The
        alternative frames are calibrated against the true margin at
        switchover, and the loop is re-ranged and re-tuned to keep the
        same loop-gain product; the block simply sees a different PV.
        """
        db = self.db
        loop = self.loops["UIC-2001"]
        pid = loop.pid
        rec = self._shaping.setdefault("UIC-2001", {})
        base = rec.setdefault(
            "base_asc", [pid.pv_eu0, pid.pv_eu100, pid.sp,
                         pid.gain, pid.reset])
        e0, e1, sp0, g0, r0 = [float(x) for x in base]
        span0 = e1 - e0
        alims = rec.setdefault(
            "base_alims", [pid.hi_hi_lim, pid.hi_lim,
                           pid.lo_lim, pid.lo_lo_lim])

        # alarm limits live in the frame's engineering units: remap the
        # finite ones through the margin <-> frame relation
        def _remap(margin_to_frame):
            import math as _m
            vals = [margin_to_frame(v) if _m.isfinite(v) else v
                    for v in [float(x) for x in alims]]
            (pid.hi_hi_lim, pid.hi_lim,
             pid.lo_lim, pid.lo_lo_lim) = vals

        if kind == "margin":
            self._pv_fn.pop("UIC-2001", None)
            pid.pv_eu0, pid.pv_eu100 = e0, e1
            pid.sp, pid.gain, pid.reset = sp0, g0, r0
            _remap(lambda v: v)
            rec.pop("asc", None)
            new_pv = float(db["UY-2001"].value)
        elif kind == "flow_speed":
            if _cal is None:
                m = float(db["UY-2001"].value)
                q = max(float(db["FT-2001"].value), 0.5)
                spd = max(float(db["ST-2001"].value), 100.0)
                qs = q / max(1.0 + m / 100.0, 0.1)   # surge flow now
                _cal = {"c": qs / spd, "sp": sp0 / 100.0 * qs}
            c = float(_cal["c"])
            self._pv_fn["UIC-2001"] = (
                lambda d, c=c: float(d["FT-2001"].value)
                - c * float(d["ST-2001"].value))
            pid.pv_eu0, pid.pv_eu100 = -10.0, 30.0
            pid.sp = float(_cal["sp"])
            # dPV/dmargin = qs/100; keep gain x span sensitivity constant
            qs_ref = float(_cal["sp"]) / (sp0 / 100.0)
            pid.gain = g0 * 40.0 * 100.0 / (span0 * max(qs_ref, 0.5))
            pid.reset = r0
            _remap(lambda v: v / 100.0 * max(qs_ref, 0.5))
        elif kind in ("flow_dp", "pressure_ratio"):
            def head(d):
                if kind == "flow_dp":
                    return max(float(d["PT-2002"].value)
                               - float(d["PT-2001"].value), 0.1)
                return max((float(d["PT-2002"].value) + 1.013)
                           / max(float(d["PT-2001"].value) + 1.013, 0.1)
                           - 1.0, 0.01)
            if _cal is None:
                m = float(db["UY-2001"].value)
                q = max(float(db["FT-2001"].value), 0.5)
                qs2 = (q / max(1.0 + m / 100.0, 0.1)) ** 2
                _cal = {"c": qs2 / head(db)}
            c = float(_cal["c"])
            self._pv_fn["UIC-2001"] = (
                lambda d, c=c: 100.0
                * (float(d["FT-2001"].value) ** 2 - c * head(d))
                / max(c * head(d), 1e-6))
            pid.pv_eu0, pid.pv_eu100 = -50.0, 300.0
            # margin in the squared frame: (1+m)^2 - 1
            pid.sp = 100.0 * ((1.0 + sp0 / 100.0) ** 2 - 1.0)
            pid.gain = g0 * 350.0 / (span0 * 2.0 * (1.0 + sp0 / 100.0))
            pid.reset = r0
            _remap(lambda v: 100.0 * ((1.0 + max(v, -99.0) / 100.0) ** 2
                                      - 1.0))
        elif kind == "dp":
            # plain orifice-dP (flow-squared) with a FIXED surge point
            # frozen at calibration: the cheapest formulation, and blind
            # to both speed and gas MW - which is the lesson.
            if _cal is None:
                m = float(db["UY-2001"].value)
                q = max(float(db["FT-2001"].value), 0.5)
                _cal = {"qs2": (q / max(1.0 + m / 100.0, 0.1)) ** 2}
            qs2 = float(_cal["qs2"])
            self._pv_fn["UIC-2001"] = (
                lambda d, qs2=qs2:
                100.0 * (float(d["FT-2001"].value) ** 2 - qs2)
                / max(qs2, 1e-6))
            pid.pv_eu0, pid.pv_eu100 = -50.0, 300.0
            pid.sp = 100.0 * ((1.0 + sp0 / 100.0) ** 2 - 1.0)
            pid.gain = g0 * 350.0 / (span0 * 2.0 * (1.0 + sp0 / 100.0))
            pid.reset = r0
            _remap(lambda v: 100.0 * ((1.0 + max(v, -99.0) / 100.0) ** 2
                                      - 1.0))
        elif kind == "pd":
            # discharge pressure alone: surge is where the head curve
            # tops out, so PV is the calibrated distance below that
            # ceiling. Moves with anything that moves the ceiling.
            if _cal is None:
                pdn = max(float(db["PT-2002"].value), 1.0)
                m0 = max(float(db["UY-2001"].value), 5.0)
                _cal = {"pds": pdn * 1.25, "w": 0.25 * pdn, "m0": m0}
            pds, w, m0 = (float(_cal["pds"]), float(_cal["w"]),
                          float(_cal["m0"]))
            self._pv_fn["UIC-2001"] = (
                lambda d, pds=pds, w=w:
                100.0 * (pds - float(d["PT-2002"].value)) / max(w, 1e-6))
            pid.pv_eu0, pid.pv_eu100 = -50.0, 150.0
            pid.sp = 100.0 * sp0 / m0
            pid.gain = g0 * 200.0 / span0 * (m0 / 100.0)
            pid.reset = r0
            _remap(lambda v: 100.0 * v / m0)
        elif kind == "power_flow":
            # shaft power over sqrt(suction pressure): the no-new-taps
            # formulation, riding the motor instead of the process - and
            # therefore load-dependent in every way the margin is not.
            def xval(d):
                return (float(d["JT-2001"].value)
                        / max(max(float(d["PT-2001"].value), 0.1) ** 0.5,
                              1e-3))
            if _cal is None:
                m = float(db["UY-2001"].value)
                _cal = {"xs": xval(db) / max(1.0 + m / 100.0, 0.1)}
            xs = float(_cal["xs"])
            self._pv_fn["UIC-2001"] = (
                lambda d, xs=xs: 100.0 * (xval(d) - xs) / max(xs, 1e-6))
            pid.pv_eu0, pid.pv_eu100 = -50.0, 150.0
            pid.sp = sp0
            pid.gain = g0 * 200.0 / span0
            pid.reset = r0
            _remap(lambda v: v)
        else:
            raise ValueError(f"unknown formulation {kind!r}")

        if kind != "margin":
            rec["asc"] = {"kind": kind, "cal": _cal}
            new_pv = float(self._pv_fn["UIC-2001"](db))
        # bumpless: adopt the new frame's PV as-found and re-prime the
        # block's PV filter so the frame change is not seen as a step
        pid.pv = new_pv
        pid.sp_wrk = pid.sp
        pid._pv_filter_primed = False

    # --------------------------------------------------------- persistence
    def capture_state(self) -> dict:
        out = {m: l.pid.capture_state() for m, l in self.loops.items()}
        # The anchors are strategy state as much as any block's reset is: a
        # restored plant must chase the same compensated setpoints and ratios
        # it was saved with, or it is a different control system.
        out["__pct"] = {m: list(v) for m, v in self._pct_comp.items()}
        out["__pctc"] = {m: list(v) for m, v in self._pct_cas.items()}
        out["__colff"] = {str(n): list(v) for n, v in self._col_ff.items()}
        out["__cwb"] = {str(n): v for n, v in self._cw_base.items()}
        out["__shape"] = {m: dict(r) for m, r in self._shaping.items()}
        out["__c1mv"] = self._c1_mv
        out["__t1s"] = self._col_scheme[5]
        out["__t2s"] = self._col_scheme[6]
        out["__ffa"] = {str(n): dict(v)
                        for n, v in self._ff_anchors.items()}
        out["__b1m"] = self._b1_mode
        out["__lvlc"] = self._lvl_comp
        out["__lhva"] = self._lhv_anchor
        out["__infer"] = {
            "defs": {nm: dict(cfg, bias=inf.bias)
                     for nm, (inf, cfg) in self._inferentials.items()},
            "used": dict(self._infer_used)}
        out["__ratio"] = dict(self._ratio)
        out["__esd"] = self.esd.capture_state()
        out["__accum"] = self._accum
        out["__enabled"] = self.enabled
        # the scan classes and where each slow module is in its period:
        # a restored plant must execute its 1 s modules on the same scans
        out["__scan"] = {l.module: [l.scan_period, l._scan_accum] for l in self._order
                         if l.scan_period != SCAN_PERIOD or l._scan_accum != 0.0}
        if self.bus is not None:
            out["__bus"] = self.bus.capture_state()
        return out

    def apply_state(self, s: dict) -> None:
        for m, v in (s or {}).get("__pct", {}).items():
            if m in self._pct_comp:
                self._pct_comp[m] = (v[0], float(v[1]), float(v[2]))
        for m, v in (s or {}).get("__ratio", {}).items():
            if m in self._ratio:
                self._ratio[m] = float(v)
        for m, v in (s or {}).get("__pctc", {}).items():
            if m in self._pct_cas:
                self._pct_cas[m] = [v[0], float(v[1])]
        for k, v in (s or {}).get("__colff", {}).items():
            if int(k) in self._col_ff:
                self._col_ff[int(k)] = [float(x) for x in v]
        for k, v in (s or {}).get("__cwb", {}).items():
            n = int(k)
            # Migrate snapshots made before the T2 pressure controller had
            # enough condenser authority.  Keeping the undersized saved base
            # would silently reintroduce the pressure/quality runaway even
            # though the live strategy has the corrected design range.
            self._cw_base[n] = max(300.0, float(v)) if n == 6 else float(v)
        self.esd.apply_state((s or {}).get("__esd", {}))
        self._accum = float((s or {}).get("__accum", 0.0))
        self.enabled = bool((s or {}).get("__enabled", True))
        if "__scan" in (s or {}):
            scan = s["__scan"] or {}
            for l in self._order:
                rec = scan.get(l.module)
                if isinstance(rec, (list, tuple)) and len(rec) == 2:
                    l.scan_period, l._scan_accum = float(rec[0]), float(rec[1])
                else:
                    l.scan_period, l._scan_accum = SCAN_PERIOD, 0.0
            self._base_period = min([SCAN_PERIOD] + [l.scan_period for l in self._order])
        if self.bus is not None and "__bus" in (s or {}):
            self.bus.apply_state(s["__bus"])
        for m, sub in (s or {}).items():
            if m in self.loops and isinstance(sub, dict):
                self.loops[m].pid.apply_state(sub)
        # replay loop-shaping selections AFTER the block states so the
        # re-ranging and synthesis tunings land on top of them
        for m, rec in (s or {}).get("__shape", {}).items():
            if m not in self.loops:
                continue
            keep = {k: rec[k] for k in ("base_gain", "base_tuning",
                                        "base_asc") if k in rec}
            self._shaping[m] = dict(keep)
            if "alg" in rec:
                self.set_algorithm(m, **rec["alg"])
            if "comp" in rec:
                self.set_compensator(m, **rec["comp"])
            if "ocond" in rec:
                self.set_output_conditioning(m, rec["ocond"])
            if "asc" in rec:
                self.set_asc_formulation(rec["asc"]["kind"],
                                         _cal=rec["asc"].get("cal"))
        mv = (s or {}).get("__c1mv", "speed")
        if mv != self._c1_mv:
            self.set_compressor_mv(mv, _park=False)
        for k, v in (s or {}).get("__ffa", {}).items():
            self._ff_anchors[int(k)] = {a: float(b) for a, b in v.items()}
        for n, key in ((5, "__t1s"), (6, "__t2s")):
            scheme = (s or {}).get(key, "material")
            if scheme != self._col_scheme[n]:
                self.set_column_scheme(scheme, column=n)
        inf = (s or {}).get("__infer", {})
        for nm, cfg in inf.get("defs", {}).items():
            cfg = dict(cfg)
            bias = cfg.pop("bias", 0.0)
            self.define_inferential(nm, cfg.pop("terms"), **cfg)
            self._inferentials[nm][0].bias = float(bias)
        for module, nm in inf.get("used", {}).items():
            if nm in self._inferentials and module in self.loops:
                self.use_inferential(module, nm)
        # the boiler mode flag rides the pid states (modes and setpoints
        # were captured with them); only the compensation is re-armed
        self._b1_mode = (s or {}).get("__b1m", "pressure")
        self._lhv_anchor = float((s or {}).get("__lhva", 38.5))
        k = float((s or {}).get("__lvlc", 0.0))
        if k > 0.0:
            self.set_level_compensation(k)
