# Configure and Commission Local Virtual I/O

This tutorial configures `AzeoPlantVirtualController` to control the canonical
in-repo `AzeoPlantSimulator` through the Local Virtual I/O adapter. The
supported operating workflow starts, stops, and restarts that embedded plant
from Azeo Explorer.

> [!IMPORTANT]
> The current Local Virtual I/O transport is **in-process**. Azeo Explorer
> owns the embedded `AzeoPlantSimulator`; do not start another simulator
> process. If a plant runs on another computer, use a network transport such
> as OPC UA. See
> [Connecting to a separately running plant](#connecting-to-a-separately-running-plant).

## What this project connects

| Component | Current setting | Purpose |
| --- | --- | --- |
| Controller | `APVC-CTRL-1` / PK750 | Runs the generated control modules |
| Virtual I/O node | `APVC-VIO-1` | Exchanges plant inputs and controller outputs |
| Provider | `azeoplant.embedding:create_embedded_plant` | Creates the plant and Local Adapter in the Trainer process |
| Signal catalog | `virtual_io/opcua_tag_catalog.json` | Defines 612 typed AI, AO, DI, and DO routes |
| Initial condition | `virtual_io/lined_up.snapshot.json` | Restores the lined-up plant state |
| Ordinary BPCS output holder | `APVC-CTRL-1` | Sole ordinary control source allowed to command claimed outputs |

The PK750 is intentional: the project consumes 612 DSTs, so a PK100 or PK300
does not have enough capacity.

## 1. Prerequisites

Open PowerShell at the repository root and use its virtual environment for
every command:

```powershell
$Python = ".\.venv\Scripts\python.exe"
```

No simulator environment variable or external checkout is required. Source
and portable runs resolve the `../../AzeoPlantSimulator` component beside the
project. Installed workspaces resolve the same path from the matching immutable
project in the versioned application bundle. Do **not** start a second copy of
the simulator for this Local VIO workflow.

## 2. Configure the Local VIO declaration

Open
[`projects/AzeoPlantVirtualController/_project.json`](../../../projects/AzeoPlantVirtualController/_project.json)
and configure the area's `virtual_io` object as follows. It is a sibling of
that area's `controller` object—not a child inside `controller`:

```json
"virtual_io": {
  "name": "APVC-VIO-1",
  "type": "local_virtual_io",
  "transport": "in_process",
  "startup_mode": "manual",
  "source": "APVC-CTRL-1",
  "claim_outputs": true,
  "provider_manages_claim": true,
  "provider": {
    "factory": "azeoplant.embedding:create_embedded_plant",
    "call_style": "mapping",
    "search_paths": [
      "../../AzeoPlantSimulator"
    ],
    "options": {
      "source": "APVC-CTRL-1",
      "dt": 0.1,
      "snapshot": "${PROJECT_DIR}/virtual_io/lined_up.snapshot.json",
      "catalog": "${PROJECT_DIR}/virtual_io/opcua_tag_catalog.json",
      "stale_timeout_s": 2.0,
      "speed_factor": 1.0,
      "autorun": true,
      "open_loop": true
    }
  },
  "dt": 0.1,
  "period_ms": 100,
  "input_stale_timeout_s": 2.0,
  "timing": {
    "integration_step_s": 0.1,
    "exchange_period_ms": 100,
    "regulatory_scan_rate_ms": 200,
    "scheduling": "independent_provider_and_controller_workers",
    "write_guarantee": "drain_queued_writes_between_plant_steps"
  },
  "snapshot": "virtual_io/lined_up.snapshot.json",
  "catalog": "virtual_io/opcua_tag_catalog.json"
}
```

The configuration validator enforces these relationships:

- `virtual_io.source` and `provider.options.source` must be identical.
- `claim_outputs`, `provider_manages_claim`, `autorun`, and `open_loop` must be
  JSON booleans, not strings.
- A catalog containing AO or DO routes requires `claim_outputs: true`.
- Provider-managed arbitration requires a provider source identity.
- The top-level and provider `catalog`, `snapshot`, and `dt` values must agree.
- `period_ms` must equal `timing.exchange_period_ms`.
- `dt` must equal `timing.integration_step_s`.
- Input routes require a positive stale timeout.

`open_loop: true` disables the simulator's normal BPCS control so that the
Trainer can take control. It does not disable the simulated SIS.

## 3. Configure the signal catalog

The authoritative route catalog is
[`projects/AzeoPlantVirtualController/virtual_io/opcua_tag_catalog.json`](../../../projects/AzeoPlantVirtualController/virtual_io/opcua_tag_catalog.json).
Do not manually duplicate its 612 routes in `_project.json`.

Each catalog entry supplies:

- a unique signal `name`;
- `kind`: `AI`, `AO`, `DI`, or `DO`;
- `direction`: `SIM_TO_DCS` or `DCS_TO_SIM`;
- data type, engineering unit, analog range, plant unit, and description;
- the allowed stale interval.

The route direction is ownership, not merely presentation:

| Kind | Direction | Owner | Trainer action |
| --- | --- | --- | --- |
| AI / DI | `SIM_TO_DCS` | Plant | Read and publish into `SharedDataStore` |
| AO / DO | `DCS_TO_SIM` | Declared output owner | Queue an authorized demand, then read back actual plant output |

For this project, the 173 writable routes have explicit ownership: 157 belong
to executable control strategies, six `XY-9001` through `XY-9006` routes
belong to the independent simulator SIS, and 10 are reserved inactive. The
16 CT1 commands are owned by nine linked instances of four project-local
Control Module classes. The simulator remains open-loop: these are APVC
controllers over plant I/O, not imported software-DCS physics. The SIS is
intentionally outside ordinary BPCS holder arbitration; do not describe or
operate those six safety routes as APVC-owned outputs.

The loader rejects duplicate store tags, duplicate provider signals, and
kind/direction contradictions before the controller goes online.

## 4. Validate before going online

Run the focused configuration and startup checks:

```powershell
& $Python run.py --list

& $Python -m pytest `
  tests\test_virtual_io_config.py `
  tests\test_local_virtual_io.py `
  tests\test_virtual_io_explorer.py -q

& $Python tests\_smoke_virtual_io_project.py
& $Python tests\_smoke_virtual_controller_boot.py
```

These checks verify provider loading, catalog and snapshot consistency, route
typing, sole output ownership, actual-output readback seeding, controller
capacity, and a no-bump startup posture.

## 5. Open Explorer, then start the embedded plant

```powershell
& $Python run.py projects\AzeoPlantVirtualController
```

In Azeo Explorer, choose **Tools > Virtual I/O > Start Simulator**. The
`APVC-VIO-1` node changes from **Stopped** to **Starting**, then **Running**.
Only after it reports Running should you download or place controller modules
on scan. The lifecycle is deliberately ordered:

1. Explorer loads and validates every route while leaving the provider stopped.
2. **Start Simulator** creates the provider and acquires the ordinary BPCS output claim. The
   independent simulator SIS remains outside that claim.
3. Read every actual AO and DO state.
4. Seed those readbacks into `SharedDataStore`.
5. Complete the first Virtual I/O exchange.
6. Start the independent VIO worker.
7. The engineer downloads the controller modules.

This order prevents a downloaded module from commanding a stale or assumed
output during startup.

**Verify:** the status bar identifies `APVC-CTRL-1 · PK750` and reports
`DSTs 612 / 750`.

## 6. Find the Virtual I/O node

In Azeo Explorer, expand:

`System Configuration → Physical Network → Control Network`

For a large project, press `Ctrl+F`, enter `APVC-VIO-1`, and select the result.

After the Start command, the node must read:

`APVC-VIO-1 · Local Virtual I/O · running`

Select or expand it. The right pane should identify the three parts of the
connection:

- output holder `APVC-CTRL-1`;
- provider factory `azeoplant.embedding:create_embedded_plant`;
- `Virtual I/O Signals (612)`.

## 7. Verify the live provider

Double-click `APVC-VIO-1`, or use **Tools > Virtual I/O > Provider Status**.
The same menu provides **Stop Simulator** and **Restart Simulator**; the node's
context menu exposes the identical commands.

Confirm all of the following:

- State is `Running`.
- Type is `local_virtual_io`.
- Source/holder is `APVC-CTRL-1`.
- Signals is `612`.
- Inputs published and output readbacks increase over time.
- Output demands enqueued increase when controller outputs execute.
- No **Last error** field is present.

The dialog is a point-in-time snapshot; it does not refresh while open. Record
the counters, close the dialog, wait a few seconds, and reopen it. The new
values should be larger. Their exact values will differ from the screenshot.

## 8. Verify a routed tag

Select **Tag Database** on the Explorer toolbar and search for an I/O tag, for
example `FT-0102`. Select the `field` row, not only the module or a parameter.

The details pane should show the canonical path (`FIC-0101/FT-0102`), field
tag, I/O kind, direction, data type, engineering unit, description, and the
modules that consume the point. A healthy online point should also have a
current value and Good quality in its runtime consumers.

The Tag Database contains module parameters as well as the 612 physical and
virtual routes, so its total point count is expected to be much larger than
the Virtual I/O signal count.

## 9. Commission the first control loop safely

The generated project starts every PID, AO, and DO block in `MAN`. Output
blocks adopt actual plant readback rather than assuming a configured demand.
Cascade connections are present but do not take authority until the downstream
slave is deliberately transferred to `CAS`.

Use this sequence:

1. Leave `open_loop: true` and confirm the Local VIO provider is Running.
2. Confirm PID, AO, and DO blocks are in `MAN`.
3. Confirm PV quality is Good and provider counters advance.
4. Confirm each manual output matches its actual plant readback.
5. Start with one simple loop, such as
   `FT-1001 → FIC-1001 → FCV-1001`.
6. Move the final element by only 2–3% in MAN.
7. Verify that PV responds in the correct direction and at a plausible rate.
8. Restore and track the as-found output, then confirm the PID output and
   downstream BKCAL state agree with it.
9. Transfer `FCV-1001` from MAN to its engineered `CAS` mode and verify that it
   accepts the upstream demand without a bump.
10. Transfer `FIC-1001` to `AUTO` only after checking PV, SP, OUT, action,
    limits, permissives, interlocks, and downstream acceptance.
11. Transfer an outer loop to `CAS` only after its slave is stable.
12. Commission one loop, then one equipment module, then one unit at a time.

`PIC-5001` is a documented exception: it starts at a neutral 50% output rather
than the lined-up `PCV-5001 = 100%` position. Verify pressure response before
transferring it.

## 10. Simulating field inputs and quality

Start the provider, then choose **Applications > Virtual I/O Simulator** or
double-click the Local Virtual I/O node in Explorer. The left tree groups the
configured catalog by plant unit and AI/DI/AO/DO type; the table shows the
current readback, acquisition quality, active simulation, and description.

1. Select an AI or DI row.
2. Choose **Configure Input…**.
3. Select Static, Sawtooth, Square, or Sine.
4. Enter the static value or pattern low/high/period.
5. Set Good, Uncertain, or Bad quality and apply.
6. Verify that the value and quality cross the Local Virtual I/O boundary and
   reach the corresponding control block.
7. Choose **Release** to expose live process physics again.

AO and DO rows are deliberately read-only. They are controller-owned signals;
using an input simulator to write them would bypass output-holder arbitration.
The process continues computing underneath an AI/DI simulation, so Release
returns to the current process value rather than the value present when the
simulation began.

Use **Save Scenario…** to write all active simulations to
`virtual_io/scenarios/*.json`. **Load Scenario…** validates the complete file
before replacing the active set. Pattern phase restarts at load time, making a
scenario repeatable. These JSON scenarios are commissioning artifacts; the
project backup includes them.

## 11. Changing the provider configuration

Explorer can browse and refresh Local VIO status, but it does not currently
hot-edit or hot-reload the provider. Stop and restart the application after
changing any of these:

- provider factory or search paths;
- source/holder identity or claim policy;
- catalog or snapshot path/content;
- route additions or removals;
- exchange period, integration step, speed factor, or stale timeout;
- `autorun` or `open_loop`.

`F5` refreshes the Explorer view only; it does not reconstruct the provider.

## 12. Clean shutdown and rollback

Before a planned stop:

1. Return transferred loops to MAN where practical.
2. Record important PV, SP, OUT, and holder state.
3. Close the Trainer through its normal Qt close action.
4. Wait for shutdown to finish before editing the project.

Normal shutdown stops the worker and provider, releases the output lease, and
marks retained samples stale so a disconnected provider cannot appear healthy.

For rollback, keep the application stopped and restore `_project.json`, the
catalog, and the snapshot as one known-good set. Re-run the validation commands
and launch offline first if the configuration changed substantially:

```powershell
& $Python run.py projects\AzeoPlantVirtualController
```

The provider remains stopped until the engineer uses Explorer's Start command,
and the controller modules remain off-scan until explicitly downloaded.

## Connecting to a separately running plant

If `AzeoPlantSimulator` is already running in another process, the Local VIO
configuration above is the wrong transport. Process boundaries require a
network protocol or a new process-neutral adapter.

For OPC UA, the deployment needs all of the following:

1. an OPC UA server enabled in the running plant;
2. a reachable endpoint URL, security policy, certificate trust, and
   credentials appropriate to the deployment;
3. a Trainer OPC UA client/provider profile that maps the same catalog names
   to server node IDs;
4. explicit AI/DI read ownership and AO/DO write ownership;
5. the same stale-data, quality, range, and sole-output-holder checks used by
   Local VIO;
6. commissioning tests before any module takes AUTO or CAS authority.

The current APVC optional OPC UA profile is disabled and has no endpoint, so
there is no supported configuration-only switch that attaches Local VIO to an
already-running external plant. Either let the Trainer own the embedded plant,
or complete and qualify the OPC UA network profile first.

## Troubleshooting

| Symptom | Check | Safe action |
| --- | --- | --- |
| Provider unavailable | In-repo `../../AzeoPlantSimulator` provider path | Restore the component, validate, and use **Restart Simulator** |
| Source mismatch | Both source fields equal `APVC-CTRL-1` | Correct configuration; do not bypass validation |
| Output held by another source | Simulator internal controller or another adapter | Stop the competing holder; never force arbitration |
| Catalog/snapshot mismatch | Both top-level and provider paths resolve to the same files | Restore or regenerate the matched pair |
| Startup readback rejected | AO/DO readback quality, timestamp, type, and finite analog value | Keep modules offline until actual outputs are valid |
| Inputs become stale | Provider Status counters and application log | Diagnose provider timing/path; do not substitute fabricated Good data |
| Output write does not land | Route is AO/DO, holder identity, limits, actual readback | Correct route/ownership and verify with a small MAN bump |
| Explorer shows a faulted provider | Application log and Provider Status | Treat field inputs as Bad; fix and restart |

Do not enable the simulator's internal closed-loop BPCS while APVC holds the
output side. The sole-holder rule exists to prevent two controllers from
driving the same plant.
