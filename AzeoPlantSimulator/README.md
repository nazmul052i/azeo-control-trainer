# Azeo Plant Simulator component

This directory contains the process-side implementation and engineering
artifacts used by the default `AzeoPlantVirtualController` project. It is part
of the Azeo Control Trainer repository; it is not a second application that an
operator starts separately.

## Supported lifecycle

Build the embedded native core from the Trainer root with its venv:

```powershell
& .\.venv\Scripts\python.exe -m pip install ".[native]"
& .\.venv\Scripts\python.exe tools/build_plant_core.py
```

This compiles the imported `cpp/` sources, runs their C++ checks, and puts the
Python bridge and its shared `azeocore.dll` beside `azeoplant/__init__.py`.
The generated DLL, Python bridge, SDK ZIP and intermediate build directories
remain local artifacts in this source repository. The imported source
revision is pinned in `NATIVE_CORE_SOURCE.json`;
see [native integration](../docs/NATIVE_PLANT_CORE.md) for verification.

For reuse in another C++ application, add `--sdk` to the build command. It
creates a verified Windows x64 SDK ZIP in the Trainer's `dist/` directory,
including the DLL, headers/import library, runtime DLLs and native example.
Build the matching DLL and SDK ZIP from the same source revision.
The native SDK does not require Python or Qt; the existing Trainer UI still
uses its Python bridge. See [`cpp/SDK.md`](cpp/SDK.md) for the public C++ contract
and `--cpp-only --sdk` for a build that omits the Python bridge entirely.

1. Launch the Trainer with the repository virtual environment:

   ```powershell
   & .\.venv\Scripts\python.exe run.py
   ```

2. In **Azeo Explorer**, choose **Tools > Virtual I/O > Start Simulator**.
3. Wait for `APVC-VIO-1` to report **Running** before putting modules on scan
   or opening the Operator Station.
4. Use the same menu to stop or restart the process provider.

The project resolves `azeoplant.embedding:create_embedded_plant` from this
directory through the relative path `../../AzeoPlantSimulator`. In an installed
workspace the loader evaluates that same path from the matching immutable
project in the versioned application bundle. No user environment variable,
sibling checkout, OPC UA endpoint, or standalone simulator process is required
for the Local Virtual I/O workflow.

### Focused simulator UI

From the **trainer root**, run `run_simulator.py` with the same venv, or
`run.py --simulator`. This launches the existing Simulation Workbench in its
plant view and starts the configured provider and host control modules. The
Control Designer host stays hidden. It does not launch the original standalone
plant application or import its UI, Control Builder, display builder, tuning
lab, faceplates or historian. The original sibling checkout is never used.

The plant view provides Run/Pause/Step/Speed, unit health, instructor
disturbances, read-only model constants, live Virtual I/O, process snapshots and
diagnostics. [Workflow and ownership](../docs/SIMULATION_WORKBENCH_GUIDE.md#focused-plant-simulator).

## Boundary and ownership

`azeoplant/` owns process physics, the tag database, the independent SIS, and
the Local Adapter. The Azeo controller owns ordinary BPCS decisions. The
provider is loaded dynamically from project configuration and exchanges data
only through `SharedDataStore`; control and UI code do not import this package.

- AI and DI signals flow from the plant to the controller.
- AO and DO demands flow from the controller to the plant.
- `APVC-CTRL-1` is the sole ordinary output holder.
- `open_loop: true` disables the simulator's software BPCS while retaining its
  independent SIS.
- Stop and failure mark retained field samples stale and keep the configured
  driver as the field-write queue owner.

## Layout

```text
azeoplant/              current embedded process/provider implementation
cpp/                    C++ core, Python bindings and native checks
config/                 simulator and embedded-provider examples
data/                   authoritative plant-side signal catalog
docs/                   process design, P&IDs, I/O, and VIO references
snapshots/              lined-up initial condition
tests/                  simulator algorithm and provider qualification
tools/                  engineering artifact generators
```

The previous `src/AzeoPlantSimulator` implementation and standalone launcher
were retired when the current `azeoplant` provider was integrated. Do not add a
second simulator package or a second runtime path.

## Verification

Use the repository venv and add this component root to the import path when
running its focused tests directly:

```powershell
$env:PYTHONPATH = "$PWD\AzeoPlantSimulator;$PWD\src"
& .\.venv\Scripts\python.exe -m pytest `
  AzeoPlantSimulator\tests\test_embedding.py `
  AzeoPlantSimulator\tests\test_io_bus.py `
  AzeoPlantSimulator\tests\test_write_queue_backpressure.py -q
```

The application-level qualification is:

```powershell
& .\.venv\Scripts\python.exe tests\_smoke_virtual_io_project.py
& .\.venv\Scripts\python.exe tests\_smoke_virtual_controller_boot.py
```

OPC UA remains available as an optional interoperability profile. It is not
the default APVC transport and does not replace Explorer ownership of the
embedded Local VIO lifecycle.
