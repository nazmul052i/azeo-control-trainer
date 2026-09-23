# AzeoPlant Process Simulator

A dynamic process training simulator with a complete software DCS built to
the implemented control-block contracts, and both operating modes in one build:

- **Closed loop** (the default): 80 control modules hold the plant -
  cascades, cross-limited firing, overrides, dual-composition columns and
  the 24-cause ESD, with Azeo faceplates and displays for all of
  it.
- **Open loop** (Simulation menu, F9): the regulatory layer goes passive,
  outputs hold, and the plant is free for hand operation or for an
  external DCS connected over OPC UA - the trainee configures and tunes
  real control on a plant that pushes back. The SIS stays armed either
  way.

## Help contents

- [Getting started](getting_started.md) — starting the simulator, lining the plant up, the panels.
- [Operating the HMI](operating.md) — displays, faceplates, alarms, and the
  open / closed loop switch.
- [Control system and ESD](control.md) — the 80 modules, what keeps the
  plant stable, and how the trip system behaves.
- [Connecting a DCS](connecting.md) — endpoint, node identifiers, quality, and the two ways
  to wire this to a controller.
- [Process description](process.md) — what each unit does and how the units interact.
- [Malfunctions and exercises](exercises.md) — the faults an instructor can inject and
  worked exercises for each.
- [Troubleshooting](troubleshooting.md) — when the engine freezes, when a tag will not move, when
  the client cannot connect.

## What is in this build

All ten units are modelled: the fuel gas header (U010), feed surge drum (U100),
recycle gas compressor (U200), fired heater (U300), reactor and separator
(U400), columns T1 and T2 (U500 and U600), steam boiler (U700), effluent
treatment (U800) and the safety system initiators (U900). The 612-tag model
includes the plant-wide CT1 cooling-water system and its machine monitoring.

The engineering documentation is issued in `docs/`: the P&ID set
(`docs/PID/`), the generated I/O list (`docs/IO_List.md`) and the control
module schedule with the stability narrative (`docs/Control_Modules.md`).

The gasoline blender from the original flowsheet is not included, and the
MPC / optimiser layer of the design schedule is intentionally out of scope.

## Starting from a lined-out plant

Levels and header pressures are integrators, so there is no fixed set of
valve positions that holds the plant steady open loop. Start from the saved
initial condition instead:

    python run.py --snapshot snapshots/lined_up.json --autorun

If you change model sizing, regenerate it with `python tools/lineup.py`. That
script closes 31 loops temporarily to drive the plant out and then saves the
result; its controllers live in the script and never in the application.

## Conventions used throughout

- **Analogue inputs (AI) and discrete inputs (DI)** are produced by the model.
  They cannot be forced from the simulator, because forcing them would hide the
  process behaviour they exist to show.
- **Analogue outputs (AO) and discrete outputs (DO)** are consumed by the model.
  They normally come from the DCS. With no DCS attached, a faceplate can put a
  **local override** on one so that the simulator is usable standalone.
- **Interlocks and physics are independent.** The DCS should refuse to start a
  pump against a shut suction valve. If that interlock is defeated, the model
  still collapses suction pressure and cavitates the pump.
