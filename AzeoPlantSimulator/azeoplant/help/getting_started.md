# Getting started

## Starting the simulator

```
python run.py
```

Useful options:

| Option | Effect |
|---|---|
| `--autorun` | Start the engine immediately instead of frozen |
| `--endpoint opc.tcp://0.0.0.0:48420/plant_sim/` | Change the OPC UA endpoint |
| `--dt 0.1` | Fixed integration step in seconds |
| `--no-opc` | Run without the OPC UA server, for model work |
| `--headless` | Engine and server with no window, for a rack-mounted rig |
| `--snapshot snapshots/lined_up.json` | Restore an initial condition on startup |
| `--verbose` | Console logging at DEBUG |

The engine starts **frozen**. Press **F5** or use **Simulation ▸ Run**. This is
deliberate: it gives you time to attach the DCS before the process starts
moving, so the first thing the controller sees is a steady plant.

## Lining the plant up

Straight from the built-in initial condition, U010 and U300 are already running
and the charge pumps are stopped. To get flow:

1. Open **MOV-1001A** — write `XY-MOV1001A-OPN` true, or click the valve on the
   U100 drawing and use the faceplate. It takes 30 seconds of travel.
2. Wait for `ZSO-MOV1001A`. The DCS start permissive should be looking at this.
3. Start **P-101A** — `XY-P101A-STR`. Run feedback appears after the start delay.
4. Charge flow settles near 130 m³/h with `FCV-1001` at 68 % and the VFD at
   100 %.

The heater should already be firing. If `BS-3001` is off, energise `XY-3010` to
prove the pilot.

## The interface

**P&ID tabs.** Overview plus one drawing per unit. Ctrl+wheel zooms, drag pans,
**Home** fits. Clicking any symbol or readout opens its faceplate.

**Trends.** Select up to six pens from the list. The window is adjustable from
two minutes to four hours.

**Tags.** Every signal with its live value, quality and OPC UA NodeId. Filter by
tag, unit or description. Double-click a row for the faceplate.

**Malfunctions.** Instructor fault injection. Tick to activate, set the severity
in the parameter column.

**Field operations.** The things only a field operator can touch: the manual
drain valve, the fuel oil isolation, local resets for tripped MOVs and motors,
and the local/remote selector. None of these have DCS command tags, which is
what makes a two-person exercise worth running.

**OPC UA.** Session state, node counts and write counters.

**Log.** Tail of the application log, filterable by level. The full log with
tracebacks is in `logs/azeoplant.log`.

## The status bar

Left to right: engine state, speed factor, scan time, simulated time, OPC UA
state and node count, error and overrun counts, then host CPU, host RAM and this
process's resident memory.

Watch the **scan time** against the period implied by your step and speed
factor. At `dt` 0.1 s and 1x, the period is 100 ms; a scan time approaching that
means the machine is at its limit and the overrun counter will start climbing.
