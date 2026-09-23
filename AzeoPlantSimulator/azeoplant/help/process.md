# Process description

## Flowsheet

Fresh feed and recycle collect in surge drum **D1**. Charge pumps **P-101A/B**
send liquid through **FCV-1001** to fired heater **H1**, which raises it to
reaction temperature before reactor **R1**. Separator **D3** splits the reactor
effluent: liquid goes forward to columns **T1** and **T2**, and off-gas returns
to the **fuel gas header**. T2's overhead is the R2 light product and its
bottoms leave as the heavy product rundown; the old recycle line to D1
remains valved out at MOV-1002. Boiler
**B1** draws on the same fuel header and supplies MP steam to both reboilers.

Three couplings do the pedagogical work:

- **Shared fuel header.** Fire the boiler harder and the heater's fuel pressure
  falls. Change reactor severity and the off-gas rate moves, which moves the
  import demand, which moves the header pressure, which upsets both fired units.
- **Shared steam header.** Both reboilers compete for the same steam.
- **Liquid recycle.** T1 bottoms return to D1 with a transport delay, which is
  what makes the snowball effect possible. T2 bottoms leave as heavy product.

Only the first coupling is live in this build, because it needs U010 and U300,
which are modelled. The other two need units that are not.

---

## U010 — fuel gas header

Isothermal ideal gas in a fixed 60 m³ volume:

```
dP/dt = (F_in - F_out) × P_std / (3600 × V)
```

with flows in Nm³/h. Import enters through **PCV-0101** from a 32 bara battery
limit; **PCV-0102** relieves to flare; **FCV-0101** admits D3 off-gas;
**PCV-0103** and **PCV-0104** reduce pressure to the H1 burner header and the B1
boiler.

Gas flow through every valve uses the standard metric sizing relation with an
expansion factor and a critical pressure ratio of 0.7, so valves choke properly
instead of passing unlimited flow as the downstream pressure falls.

B1 is not modelled. It is represented as a **demand** that draws its own header
down, so the competition for fuel between the two fired units is real even
though the boiler itself is not there.

**Typical steady state:** header 15.3 barg, import 709 Nm³/h, off-gas
1125 Nm³/h.

---

## U100 — feed surge drum and charge pumps

Two pumps in a duty-standby pair, each behind a control-room operable motor
operated valve, with minimum flow protection and a field manual drain.

**Hydraulics** are solved explicitly from the previous step rather than by an
algebraic loop: the pump develops head at last step's flow, that gives a
discharge pressure, the control valve passes flow at the resulting differential,
and the result goes through a short lag. The lag is what makes it stable. An
implicit solve would be marginally more accurate and considerably more likely to
oscillate at large step sizes, which is the wrong trade here.

**Suction pressure** falls as the square of flow divided by the square of the
MOV opening. Close the suction valve on a running pump and the pump cavitates:
head drops to 45 %, vibration climbs from 12 to 40 µm, and the bearing warms.
The DCS interlock should have prevented it; the model does not care whether the
interlock was defeated.

**Minimum flow.** `FCV-1002` recycles to the drum when forward flow falls below
the pump's minimum continuous stable flow of 25 m³/h. This is a genuine override
control example that is not a constraint controller.

**Typical steady state:** level 55.8 %, charge 129.6 m³/h, discharge 16.8 barg,
suction 2.2 barg.

---

## U300 — fired heater H1

A two-pass charge heater firing gas from the shared header, with fuel oil as the
split-range trim.

**Burner header.** `PCV-0103` fills a 5 m³ volume that `FCV-3001` draws from.
This is why the heater has a fuel pressure that sags rather than an infinitely
stiff supply, and it is what makes the low-low fuel pressure trip meaningful.

**Combustion.** Duty is fuel energy times efficiency, and efficiency falls away
from the optimum excess air in *both* directions — too little air gives
incomplete combustion, too much carries heat up the stack. Peak is at 15 %
excess air. That single curve is what makes an O2 trim exercise worth doing.
Oxygen follows excess air; CO stays near 20 ppm until excess air goes below
2 %, then rises steeply.

**Combustion air** is a fan against a damper, not a valve across a pressure
drop. Modelling it as a valve gives numbers wrong by orders of magnitude,
because the available differential is only tens of millibar.

**Outlet temperature** is a steady-state energy balance followed by a 105 s lag
and a 28 s dead time. Both are exact discrete forms, so the model is stable at
any speed factor and at **zero charge flow** — the operating point that breaks
naive implementations, because duty over flow goes to infinity. Below 0.5 kg/s
the temperature rise is capped rather than divided.

**Tube skin** follows pass outlet temperature plus a term in duty over pass
flow, so it responds to the thing that actually damages tubes: firing hard into
a low flow.

**Pass balancing.** `FCV-3005` and `FCV-3006` split the charge. Unbalance them
and the pass outlets diverge while the combined outlet barely moves, which is
exactly the situation pass balancing control exists to fix.

**Typical steady state:** burner 2.73 barg, fuel gas 325 Nm³/h, air 4.0 kNm³/h,
O2 4.4 mol%, CO 20 ppm, outlet 106.9 °C, tube skin 112.7 °C, stack 173.5 °C,
duty 2.95 MW.

---

## Numerical approach

Every dynamic primitive is **unconditionally stable** for any positive step:

- lags use the exact zero-order-hold solution, so they cannot oscillate or
  diverge however large the step becomes;
- dead time is a ring buffer, which is exact;
- integrators clamp to physical limits every step, so a runaway input cannot
  drive a level to 1e300;
- noise is band-limited, so its apparent amplitude does not change with the step
  size and tuning transfers between speed settings.

This is a deliberate constraint. A simulator that blows up when the instructor
sets 5× speed is worthless, and explicit integration of a fast lag is the usual
cause. The verification suite checks that outlet temperature at a 0.05 s step
and a 0.2 s step agree within one degree.
