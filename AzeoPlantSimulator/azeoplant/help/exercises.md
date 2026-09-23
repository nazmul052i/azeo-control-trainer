# Malfunctions and exercises

Malfunctions are injected from the **Malfunctions** panel. Tick to activate and
set the severity in the parameter column. **Simulation ▸ Clear all malfunctions**
(Ctrl+Shift+C) resets everything.

## Available in this build

| ID | Target | Fault | Parameter |
|---|---|---|---|
| MF-001 | FCV-1001 | Control valve stiction | Stick band 0–10 % |
| MF-002 | FCV-1001 | Control valve hysteresis | Deadband 0–10 % |
| MF-003 | FCV-3001 | Control valve seat leakage | Leakage 0–15 % |
| MF-007 | MOV-1001A | Fails to open on command | on/off |
| MF-008 | MOV-1001A | Torque switch trips mid-travel | Trip at 0–100 % travel |
| MF-009 | MOV-1001A | Open limit switch fails to make | on/off |
| MF-010 | MOV-1001A | Slow travel | Factor 1–6 |
| MF-011 | MOV-1001B | Drifts closed without command | on/off |
| MF-012 | FT-1001 | Transmitter drift | −5 to +5 % per hour |
| MF-013 | FT-1001 | Transmitter noise increase | Sigma 0–5 % |
| MF-014 | LT-1001 | Transmitter frozen at last value | on/off |
| MF-016 | TT-3001 | Thermocouple burnout upscale | on/off |
| MF-019 | AT-3001 | Oxygen analyser slow response | Extra lag 0–120 s |
| MF-021 | P-101A | Motor trip on overload | on/off |
| MF-022 | P-101A | Impeller wear | Head loss 0–40 % |
| MF-023 | P-101A | VFD communication fault | on/off |
| MF-028 | U010 | Fuel gas heating value swing | 30–45 MJ/Nm³ |
| MF-029 | U010 | Loss of natural gas import | on/off |
| MF-030 | H1 | Burner fouling | Efficiency loss 0–20 % |
| MF-031 | D1 | Fresh feed rate step | 0–160 m³/h |
| MF-041 | U010 | Instrument air failure | on/off |

---

## Exercise 1 — the MOV that will not open

**Setup.** Plant lined up, P-101A running. Activate **MF-009**.

Ask the trainee to swap to the standby pump. They close MOV-1001A, stop P-101A,
open MOV-1001B and start P-101B — fine. Then ask them to swap back.

MOV-1001A travels fully open but `ZSO-MOV1001A` never makes. The start
permissive stays blocked. A trainee who has built the `XC-` module properly
already has a discrepancy alarm telling them the valve travelled without making
its limit; one who has not will stare at a start command that does nothing.

**Teaching point.** A travel timer and a discrepancy alarm are not decoration.
Without them, a limit switch failure is indistinguishable from a valve failure,
and the two have completely different responses.

---

## Exercise 2 — cavitation past a defeated interlock

**Setup.** P-101A running at 130 m³/h. Have the trainee defeat the suction
permissive in their motor control module, then close MOV-1001A.

Suction pressure collapses from 2.2 barg toward the vapour pressure. The pump
cavitates: head falls to 45 %, charge flow collapses, vibration climbs from 12
to 40 µm, bearing temperature rises 15 °C. Downstream, H1 is still firing into a
falling flow, so the outlet temperature and tube skin climb.

**Teaching point.** Interlocks are not what protects the pump; they are what
prevents the operator from having to. The physics does not check whether the
interlock was bypassed.

---

## Exercise 3 — O2 trim and the efficiency curve

**Setup.** Heater firing steadily. Have the trainee build `AIC-3001` trimming
`FIC-3003`, then sweep the damper by hand from 10 % to 40 %.

Efficiency peaks near 15 % excess air, around 3 mol% O2. Below 2 % excess air CO
rises steeply. Above the optimum, stack temperature climbs and duty falls even
though nothing about the fuel changed.

Then activate **MF-019** with 90 s of extra analyser lag and ask them to retune.
The cascade that was comfortable is now marginal.

**Teaching point.** The optimum is a peak, not a limit — you can be wrong in
both directions. And an analyser's dynamics belong in the tuning, not in the
apology afterwards.

---

## Exercise 4 — heating value swing

**Setup.** `TIC-3001` cascaded to `FIC-3001` and holding outlet temperature.
Activate **MF-028** and ramp heating value from 38.5 to 33 MJ/Nm³.

Fuel *flow* holds at setpoint while the *energy* falls. Outlet temperature sags
and the master has to walk the flow setpoint up. Now have them add heating value
compensation and repeat.

**Teaching point.** Flow control is not energy control. This is the argument for
`FY-0101` in one demonstration.

---

## Exercise 5 — shared header competition

**Setup.** Heater firing. Increase the B1 fuel demand on the bus, or open
`PCV-0104` further.

Header pressure falls, the burner header follows, and `FCV-3001` has to open
further for the same fuel flow. Push it far enough and `PSLL-3001` trips the
heater on low fuel gas pressure — an event that started in a unit the trainee
was not looking at.

**Teaching point.** Unit-by-unit thinking fails on a real plant. This is the
case for plant-wide constraint control.

---

## Exercise 6 — stiction and the limit cycle

**Setup.** `FIC-1001` tuned and stable. Activate **MF-001** at 4 %.

The loop begins a limit cycle: the valve sticks, error builds, integral action
grows until it breaks free, the valve jumps past, and it repeats. Ask the
trainee to retune. They cannot fix it — reducing gain lengthens the period
without removing the cycle.

**Teaching point.** Recognising a stiction cycle by its square wave on the
output and saw-tooth on the PV, and knowing that the answer is a maintenance
work order rather than a tuning change.

---

## Exercise 7 — silent instrument drift

**Setup.** `FIC-1001` in automatic. Activate **MF-012** at +3 % per hour and
leave it running at 5× speed.

The controller holds its indicated flow perfectly while true flow falls. Nothing
alarms. It surfaces only as an inventory imbalance: D1 level rises when it
should be steady.

**Teaching point.** A controller cannot detect a fault in its own measurement.
Cross-checking against an independent inventory is the only way this is caught,
and that is a habit rather than a configuration.
