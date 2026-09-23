# Azeo Plant Virtual Controller

This is the isolated, reproducibly configured Control Trainer engineering
project for running the Azeo Plant through **local virtual I/O**. Its 160
assigned modules (158 control and 2 sequence modules) execute inside the one
`APVC-CTRL-1` controller boundary. `APVC-VIO-1` exchanges 612 configured routes
with the embedded AzeoPlantSimulator through `LocalAdapter`.

No separately started simulator, OPC server, EIOC, or hard-coded communication
path is required. The optional OPC UA interoperability profile is disabled and
contains no network address.

For the illustrated setup, validation, Explorer verification, commissioning,
shutdown, and external-plant distinction, follow the
[Local Virtual I/O configuration tutorial](../../docs/tutorials/virtual_io_configuration/README.md).

## Architecture

| Component | Responsibility |
| --- | --- |
| `APVC-CTRL-1` | PK750 executing all 160 assigned modules; 750-DST capacity for the 612-point inventory |
| `APVC-VIO-1` | In-process LocalAdapter boundary for 612 AI/AO/DI/DO routes |
| Embedded AzeoPlantSimulator | Integrates the process at 100 ms from the stabilized v2 snapshot |
| Simulator SIS | Remains independent and active while the simulator BPCS is open-loop |
| `engineering/control_modules.json` | Auditable projection of the 80 executable regulatory loops |
| `engineering/control_schemes.json` | Generated cross-module, external-reset, ownership, and startup contract |

The 80 executable PID modules run every 200 ms. The project extends the
source inventory with nine linked CT1 module-class instances and retains
`SIC-7001` as a monitor after
`FIC-7003` becomes the evidenced owner of `SC-7001`. `open_loop: true` disables
the simulator's internal BPCS so it cannot compete with `APVC-CTRL-1`; it does
not disable the simulator's independent SIS.

## Safe lined-up commissioning

A full project download adopts the stabilized snapshot without taking control:

- every PID, AO, and DO starts in `MAN` and retains its engineered
  `normal_mode` (`AUTO`, `CAS`, or `MAN`);
- AO/DO manual values adopt physical output readback;
- direct PID outputs adopt the as-found final-element demand, cascade masters
  adopt the first slave's as-found PV normalized to percent, and override
  selectors start on their documented released side;
- ordinary PID setpoints adopt the snapshot PV, while constraints keep their
  engineering limits and released outputs rather than being armed at an
  arbitrary snapshot value; and
- all 19 cross-module setpoint paths are armed, but cannot own a demand until
  the operator explicitly transfers the downstream loop to its engineered
  `CAS` mode.

The constraint exceptions are `PIC-1001`, `PIC-2002`, `TIC-2001`, `TIC-3004`,
`TIC-4002`, `PDIC-5001`, `PDIC-6001`, `AIC-7002`, and `PIC-7002`. `PIC-5001`
also retains its engineered 8.5 bar setpoint and starts at the neutral 50%
split point. That neutral output deliberately does not reconstruct the lined-up
`PCV-5001=100%` leg; its AOs remain in Manual and an operator must verify the
pressure response before transfer. The exact SP/output pairs are in
[`engineering/control_schemes.json`](engineering/control_schemes.json).

This is a commissioning hold, not the intended steady-state mode. Transfer one
loop, equipment module, and unit at a time after verifying PV, SP, output,
permissives, interlocks, and downstream response. Untaken outputs continue to
hold the lined-up value; no LocalAdapter-specific runtime branch selects modes
or invents setpoints.

## Installed control topology

The project contains 19 single-owner, percent-to-engineering-unit cascade
paths. Seventeen distinct masters receive external reset from the first
downstream slave's typed `BKCAL_OUT`; every slave is configured to return its
working PV for that contract. Cross-module values use `REMOTE_ANALOG`, including
quality and limit companions, rather than hidden reads or duplicated
communication configuration. Feedforward reaches `PID.FF_VAL` and is explicitly
excluded from the external-reset path.

The installed schemes include:

- U100 charge-flow low select (`LIC-1001` versus `PIC-1001`);
- CT1 pressure, supply-temperature, basin-level and conductivity loops, staged
  fan cells, and lead/standby pump-discharge-valve assemblies;
- U200 recycle high select (`UIC-2001`, `PIC-2002`, and `TIC-2001`);
- H1 and B1 fuel/air cross-limits, including excess-air trim, supply limits,
  skin-temperature limit, and the boiler CO air floor;
- U400 runaway quench high select;
- dual-column pressure compensation, feedforward, reflux decoupling, drum
  cutback, flooding low select, and 16.5 t/h steam cap;
- exact fuel-gas and heater split ranges plus the three-region pH dosing split;
  and
- stability-critical SP rates and output limits from the reference strategy.

See [CONTROL_TOPOLOGY.md](CONTROL_TOPOLOGY.md) for the complete ownership table,
formulas, typed remote sources, startup exceptions, and evidence boundaries.

## Launch

From the `azeo_control_trainer` repository root, open the project in Explorer:

```powershell
& .\.venv\Scripts\python.exe run.py projects\AzeoPlantVirtualController
```

In **Azeo Explorer**, use **Tools > Virtual I/O > Start Simulator**. Start the
Operator Station from Explorer only after the Virtual I/O node reports
**Running**. Stop or restart the simulator from that same menu; no separate
simulator process or workstation path is required.

Provider behavior is configuration-driven. The project resolves the
`azeoplant.embedding:create_embedded_plant` factory through
`../../AzeoPlantSimulator`. Source and portable runs resolve that path beside
the project; an installed workspace resolves the same relative path against
the matching immutable project in the versioned application bundle. Catalog
and snapshot resources use
`${PROJECT_DIR}/virtual_io/...`. Mapping call style, output-claim policy,
freshness and staleness limits, autorun, and speed are all project settings;
APVC-specific trainer code contains no communication endpoint or tag map.

## Clean stop and diagnostics

Close the Trainer window or use the normal Qt quit command. Shutdown stops the
Local Virtual-I/O worker and embedded plant, releases its output-holder claim,
and marks retained field samples stale. Explorer reports `APVC-VIO-1` health,
route counts, startup readbacks, queued demands, actual output readbacks,
failures, and provider health. Persistent diagnostics are written below
`logs/applications/` and `logs/system/`; set `AZEO_LOG_DIR` for a controlled
qualification-log location.

AI ranges and units come only from the project catalog. Regulatory PV identity,
action, normal mode, tuning, startup policy, and scheme topology come from the
generated engineering files. Generation fails instead of guessing when those
sources disagree with a graph.

All 173 writable routes have an explicit owner in
`virtual_io.output_ownership`: 157 are written by named strategy AO/DO blocks,
six (`XY-9001` through `XY-9006`) remain owned by the simulator SIS, and 10
are reserved/inactive because the current APVC engineering basis does not
assign them a producer. `FIC-7003`
commands `SC-7001`; the separate `FCV-7003` actuator has no evidenced command
producer. Reserved routes retain restored values or, when absent from the
older startup snapshot, their open-loop model defaults.

Three reference concepts have no equation in either authoritative source and
therefore remain truthful, non-writing monitors: `FY-0101` heating-value
compensation, `FY-2001` molecular-weight/surge calculation, and `PY-0101`
off-gas import bias. `SIC-7001` is also monitor-only because `FIC-7003`
supersedes it as the `SC-7001` owner. No substitute formula is fabricated.

## Engineering basis and qualification

The checked-in `engineering/control_modules.json` is the project-local basis
for the 80 regulatory loops. Regenerate its human-readable schedule after
reviewing basis changes:

```powershell
& .\.venv\Scripts\python.exe tools\render_control_module_basis.py
```

Then verify the project:

```powershell
& .\.venv\Scripts\python.exe tests\_smoke_virtual_io_project.py
```

See [CONTROL_STRATEGY_BASIS.md](CONTROL_STRATEGY_BASIS.md) for execution,
ownership, timing, and acceptance assumptions.

## Operator display hierarchy

The thirteen `Overview - …` and `Uxxx - L2 …` displays are the ISA-101 L1-L4
starting templates configured with this project's tags. Their content lives in
`engineering/display_hierarchy.json`; regenerate, check and publish them with

```powershell
& .\.venv\Scripts\python.exe tools\apply_hierarchy_templates.py `
    projects\AzeoPlantVirtualController\engineering\display_hierarchy.json --publish
```

The tool keeps every display exactly on its template (only text, bindings,
trend pens, alarm scope and table rows may differ), resolves each binding
against the tag database and publishes through the Graphics Designer release
gate. Operator Station shows the new revision after **Refresh**. The
`Plant - …` safe-landing displays below are separately authored procedure
workspaces and are not part of this spec.

## Guided Safe Landing

Operator Station's **Displays → L2 → Plant - L2 Shutdown Monitoring** opens
the vessel/AI/trend overview and SAFE LANDING procedure PVM. The paired
faceplate and detail show instructions, individual conditions, dwell and
timeouts. Manual shutdown control pages stay available while guidance is
running, paused or ended.

The current draft is `procedures/pa_safe_landing/rev-003/procedure.yaml`.
It exposes the existing wait timers for one-time next-run tuning, retaining
revision 002's numeric defaults. The PA PVM opens editable Workflow, Conditions,
Tuning, Trends and History pages through the same Graphics Designer class family.
It is operator-guided and performs no process writes. Completion means a
monitored hot, pressurized training hold. Fresh feed isolation requires the
explicit upstream simulator action (MF-031), verified through the added
FT-1004 indication in FIC-1001. The generator retains this indication;
the PA and graphics remain separately authored project content.

See the [PA Designer guide](../../docs/PA_DESIGNER.md) for procedure editing,
validation and operator workflow.
