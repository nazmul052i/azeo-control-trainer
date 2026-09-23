# Azeo Simulation Workbench

The Simulation Workbench is the system-level checkout surface for a project
that uses Local Virtual I/O. It coordinates the control modules already loaded
by Control Designer, the existing `SharedDataStore`, and the configured dynamic
process provider. It does not create a second controller or plant engine.

Open it from **Azeo Explorer → Applications → Simulation Workbench** or
**Tools → Virtual I/O → Open Simulation Workbench**. Start the configured plant
provider first when you need process-clock, Virtual I/O, or process-snapshot
functions. Controller-module inventory remains visible when the process is
stopped.

## Focused plant simulator

From the trainer checkout:

```powershell
& .\.venv\Scripts\python.exe run_simulator.py
# The same view through the shared launcher:
& .\.venv\Scripts\python.exe run.py --simulator
```

The launcher opens only the plant view, starts the project's Local Virtual I/O
provider in the background, and puts the existing Control Designer modules on
scan. The hidden Control Designer host owns regulatory control; the simulator's
software BPCS remains disabled. Closing the application stops its provider and
controller session normally. An unavailable provider is reported in the window.

This view uses only the trainer's included simulator component. It neither launches
nor modifies an external simulator checkout. The standalone simulator
UI and its Control Builder, display builder, tuning lab, operator faceplates,
and historian are not imported. Engineering and operations remain in the
trainer's corresponding products. The full `--simulation` checkout workbench
continues to provide the controller commissioning workflow below.

| Page | Use |
| --- | --- |
| Plant units | Inspect the active backend, signal counts, bad signal counts and active disturbances for each unit. |
| Disturbances | Filter by unit, select an equipment or process fault, enter its setting within the displayed limits, then choose **Active** and **Apply disturbance**. **Clear selected** removes that fault. |
| Model parameters | Search read-only model constants, units and their engineering basis. These are not runtime tuning fields. |
| Virtual I/O | Inspect actual signal values and quality; open the existing Signal Simulator for input overrides. Controller-owned outputs remain read-only. |
| Snapshots | Save a checkpoint or restore the selected process condition, including active disturbances. The focused view restores process state; controller operating and tuning settings are not selected. |
| Diagnostics | Inspect provider, simulation clock and controller execution health. |

**Run**, **Pause**, **Step**, and **Speed** operate the same shared training
clock as the full workbench. Polling preserves a disturbance value while it is
being edited. Menu actions and row context menus use the shared blue engineering
chrome. Fault commands use the provider protocol under its database lock and
are logged; no model objects are handed to the UI.

Right-click a **Plant unit** to view its signals, configure its disturbances,
or inspect its model parameters. These commands open the appropriate tab with
that unit already filtered. Clear the filter to return to all units.
Right-click a **Virtual I/O signal** and choose **Open in Signal Simulator**
to inspect that exact signal or configure an eligible input. Outputs remain
controller-owned. Right-click a **disturbance** for **Apply disturbance** or
**Clear selected**, and a **snapshot** for **Restore Selected**. Commands retain
the clicked target and refuse a row that has disappeared or been replaced.
Opening a menu preserves pending disturbance edits.

**File > Save Snapshot**, **View > Open Signal Simulator**, and **View > Refresh**
provide global access. The duplicated Actions menu has been removed. Run, Pause,
Step and Speed remain visible. Row menus also support copying values and,
where multiple selection is available, copying the selected rows.

The source-built C++ extension is required by default; see
[native core setup](NATIVE_PLANT_CORE.md). `tests/_smoke_simulator_ui.py` boots
the actual project and checks the backend, host control ownership, visible
windows, live I/O, clock actions and shutdown. It also saves UI captures locally
under `logs/` for layout review.

Development verification also covers disturbance snapshot persistence and
invalid-value rejection on both cores, plus subprocess regressions for native
read/snapshot locking and clean shutdown. The two repository-wide import-boundary
checks currently report six pre-existing dependencies in the configuration,
release and controller features. Comparing their AST results with commit
`02de8e5` confirms this simulator change introduces none; those dependencies
remain outside this UI change.

Verified on 9 September 2026: all eleven existing application smokes and the
new focused simulator smoke passed, along with 25 focused native tests and ten
Python-reference provider tests. The read-lock regressions fail against the
old adapter and pass with the corrected lock entry. Shared Graphics/PVM lint
and changed integration Python lint passed. No project displays or original
plant-repository source files were changed.

## 1. Select the checkout scope

The left tree has **Entire control system** and one row per loaded module.
Every command in the Setup tab applies only to this selection. Use a module
scope while commissioning one loop; use the system scope only for deliberate
bulk transitions.

## 2. Prepare controller I/O

The **I/O Blocks** tab lists the AI, AO, DI, and DO blocks in scope, including
their field tag, simulation state/value, quality, actual mode, output, and
status.

1. Select a row.
2. Check **Sim**, enter a typed simulation value, and choose `GOOD` or `BAD`
   for an analog block.
3. Enter the requested block mode when the block supports modes.
4. Choose **Apply Selected Row**.

For repeatable plant-side input patterns, open **Virtual I/O** and choose
**Open Signal Simulator**. That editor owns static, sawtooth, square, and sine
profiles with Good, Uncertain, or Bad acquisition quality. Virtual AI/DI are
field-owned and simulatable; AO/DO remain controller-owned and read-only.

## 3. Use Setup and Normal modes

- **Enable/Disable Simulate** changes every I/O block in the current scope.
- **Setup Mode** requests each mode-capable block's configured `setup_mode`,
  using Manual when none is configured.
- **Normal Mode** requests `normal_mode`, then the configured initial `mode`,
  then Auto as the final fallback.
- **Initialize Dynamic Blocks** resets stateful block algorithms without
  recompiling the module. Use it only at a known commissioning boundary.

## 4. Control simulation time

The header reports process state and simulation time. **Pause**, **Run**,
**Step**, and **Speed** coordinate both the provider-neutral process clock and
the existing controller executive. A single step advances the process,
exchanges Virtual I/O, scans each online controller module once at its nominal
module interval, then exchanges the resulting outputs. A provider that does
not implement process controls is reported as unavailable instead of
presenting an inert control.

To reproduce a boundary exactly: pause, save a snapshot, make the change, then
step one scan at a time. Speed changes pacing, not the fixed model integration
step.

## 5. Save and restore a complete state

The **Snapshots** tab stores one JSON document under
`<project>/core/simulation/snapshots/`. It contains:

- controller operating values, modes, and block-simulation settings;
- controller tuning and configured limits;
- active Virtual I/O input profiles; and
- the provider's full process snapshot when supported.

Save briefly crosses the same coordinated pause boundary as Restore, captures
all layers from one simulation instant, and then returns each clock to its
entry state. The JSON writer is atomic, so a disk or application failure cannot
leave a half-written checkpoint with the requested filename.

At restore time select **Operating**, **Tuning**, and/or **Process**. The file is
fully validated before mutation. The process is paused during the exchange,
and all selected parts roll back to their entry state if any restore operation
fails. A process that was running before restore is resumed afterward; loading
a snapshot does not silently start a process that was already paused.

## 6. Record and replay changes

In **Operator Playback**, choose **Start Recording**, perform workbench I/O,
mode, or process-clock operations, and add named markers at important lesson
boundaries. **Restart Playback** returns to the first record and **Apply Next
Event** applies the next actionable event. Markers and snapshot audit records
are skipped because they describe a boundary rather than command the plant.

The journal is append-only SQLite runtime data at
`<application-data>/core/simulation/<project>/operator_changes.sqlite`. Keeping it
outside the engineering project prevents an operator session from modifying
shipped control-module sources. Operator surfaces can call the
service's `record_tag_write(tag, value)` hook so ordinary setpoint writes use
the same deterministic playback path.

## 7. Diagnose the session

The **Diagnostics** tab combines:

- loaded/online module counts;
- controller and simulated-I/O counts;
- Local Virtual I/O channel and provider health;
- process running state, simulation time, heartbeat, and speed; and
- recording/event counts in the full workbench.

The focused Plant Simulator displays the latest completed plant status without
waiting for a running model or snapshot operation. If updates are delayed for
more than three seconds while running, the status area says it is waiting for
the plant and retains the last received values. Paused status is retained until
the next step, disturbance command, restore or resume.

If process controls are unavailable, first verify that the project declares a
Local Virtual I/O provider, then start it from Explorer. If a signal cannot be
simulated, verify that it is configured as an AI/DI input; controller-owned
AO/DO signals intentionally cannot be overridden in the plant-side editor.

## Safety boundary

Simulation is engineering functionality. It never weakens output ownership,
quality propagation, or write arbitration. Controller simulation changes a
function block's configured simulation path. Virtual I/O simulation changes a
provider input. Those are intentionally separate operations and both remain
visible in the workbench.
