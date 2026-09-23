# Control system and ESD

The closeloop build carries a complete software DCS built to the implemented
control-block contracts: 80 control modules matching the design
schedule's Control_Modules sheet, scanned on a 200 ms period. The full
module table with wiring and tuning is issued as
`docs/Control_Modules.md`; this page is the working summary.

## The strategies, unit by unit

- **U010** — header pressure PIC-0101 split range (import low, flare
  high), off-gas recovery FIC-0101, and the burner supply reducers.
- **U100** — averaging drum level LIC-1001 cascading to charge flow
  FIC-1001, low-selected under the discharge pressure limiter PIC-1001;
  pump minimum flow; the VFD speed loop.
- **U200** — suction pressure PIC-2001 cascading to the SIC-2001 speed
  slave; anti-surge UIC-2001 high-selected with the discharge pressure
  and temperature constraints onto the recycle valve.
- **U300** — outlet temperature TIC-3001 to fuel flow FIC-3001 with the
  charge-rate feedforward; combustion air FIC-3003 on the damper;
  fuel-air **cross limits** every scan (air leads up, fuel leads down);
  AIC-3001 oxygen trim on the excess-air target; TIC-3004 tube-skin
  low-select; supply-pressure cutback; pass balancing; the fuel oil
  split range under the ZC-3001 valve position controller; draft
  PIC-3002.
- **U400** — the chain AIC-4001 (impurity, slow against the analyser
  dead time) to TIC-4001 (high select of both beds) to FIC-4001 quench
  flow, with TIC-4002 the fast runaway override forcing the valve past
  the flow loop.
- **U500 / U600** — dual composition with every master writing a flow
  setpoint: drum level cascades to distillate flow (LIC-n001 to
  FIC-n006), sump level to bottoms flow (LIC-n002 to FIC-n007, kept
  averaging), the pressure-compensated tray temperature to reflux flow,
  and the tray differential temperature TDIC-n001 (lower tray minus
  upper tray, a derived PV that needs no pressure compensation) to
  reboiler steam flow, with the analysers trimming the temperature and
  dT targets, feed feedforward and a static decoupler on the heat
  balance pair, PDIC flooding low-selects, condenser CW loops, minimum
  flows, and T1's split-range pressure with the hot gas bypass.
- **U700** — three-element drum level through the FIC-7001 feedwater
  slave; the PIC-7001 boiler master driving cross-limited FIC-7002 fuel
  and FIC-7003 air, with O2 trim, the CO air floor, and the PIC-7002
  letdown split above the master; superheat spray; conductivity
  blowdown.
- **U800** — gain-relevant pH split range across three reagent valves,
  tank level cascading to the discharge flow slave.

## What keeps it stable

- Feedforward is removed from the external-reset feedback before it is
  integrated - otherwise every FF loop ramps by the whole FF term once
  per reset time.
- The column quality masters are near-integral-only with rate- and
  band-limited temperature targets, so a discontinuous analyser step
  becomes a bounded excursion.
- Reflux demand is cut back with drum level below 30 percent; reboiler
  steam is capped so the boiler drum keeps margin to its trip.
- Overrides park on the released side of their selectors and keep their
  engineering setpoints across a restore.
- Closing the loop adopts the plant as found: SP = PV, outputs live,
  O2 trims at the as-found excess air, anchors re-seeded.

## The ESD

The 24-cause matrix runs as the DCS-side SIS modules (ESD-9000 total,
ESD-9200 reaction, ESD-9500 fractionation, ESD-9700 boiler, plus
reactor depressuring). Causes confirm for 2 seconds, latch, and the
first cause is held as the **first-out** - shown on the overview banner.
The plant model applies the group trips independently, so a trip has
real consequences whether or not any regulatory loop is closed.

Reset: clear the cause, then give the field reset (XS-9002, available
as an instructor malfunction). The reset refuses while any cause still
stands, and a tripped burner needs relighting before its flame-failure
cause clears - which is the point.

The alarm schedule (78 configured alarms from the design workbook)
drives the loop and monitor limits and priorities; regenerate the
tables with `tools/make_control_narrative.py` and
`tools/make_io_list.py` whenever the build changes.
