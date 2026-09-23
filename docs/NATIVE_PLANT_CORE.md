# Embedded C++ plant core

The APVC plant runs the C++ core included under `AzeoPlantSimulator/cpp`.
The imported revision and each source file's original SHA-256 are recorded in
[`NATIVE_CORE_SOURCE.json`](../AzeoPlantSimulator/NATIVE_CORE_SOURCE.json).
The trainer carries the source and matching Python facades locally; a
deployment does not depend on a sibling checkout.

## Runtime boundary

`azeoplant.embedding:create_embedded_plant` remains the configured provider.
Package initialization selects the native tag database, Virtual I/O bus,
process bus, and all ten unit implementations. The provider's health detail
reports `core: cpp` and `native_units: 10` when those implementations are active.

Control Designer's existing module compiler, PID implementation and scan executor
continue to own BPCS control through `SharedDataStore`. The embedded simulator's
software BPCS stays disabled; its independent SIS remains active. This change
does not replace the trainer controller with the simulator's standalone DCS.

The SI catalog contains 612 signals, including the 54-point CT1 cooling-water
system. APVC uses 160 modules on its PK750 controller. Four project-local
Control Module classes provide nine linked CT1 instances: four regulatory
loops, two fan cells, two pump/discharge-valve assemblies, and one tower
performance monitor. The upstream CT1 software-DCS remains excluded: the
embedded plant runs open loop and APVC owns the 16 CT1 commands through those
instances. The retained startup snapshot remains backward compatible because
the new CT1 tags and state use model defaults.

## Build and selection

```powershell
& .\.venv\Scripts\python.exe -m pip install ".[native]"
& .\.venv\Scripts\python.exe tools/build_plant_core.py
& .\.venv\Scripts\python.exe run.py
```

The helper uses CMake, Ninja and the running interpreter. Windows uses Visual
Studio C++ tools, with strict floating-point settings retained from upstream.
The shared `azeocore.dll` and Python bridge are built below
`AzeoPlantSimulator/azeoplant`. Both the Windows x64 DLL and its matching
`_azeocore.cp313-win_amd64.pyd` bridge are versioned, so an ordinary Git checkout
includes both. This prebuilt pair requires standard 64-bit CPython 3.13,
the project's Python dependencies and the matching Microsoft C++ runtime
(the SDK includes app-local runtime DLLs). No compiler is needed to use that
pair. Other Python ABIs and intermediate build files remain gitignored.

The bridge links the DLL rather than embedding a static copy of the core.
Deploy both files together. Rebuild after native source or Python ABI changes;
for Windows CPython 3.13, commit the updated DLL, bridge and SDK together.

### Reusable C++ DLL and SDK

The complete Trainer UI requires its matched Python bridge and application
dependencies. A qualified Windows application package can bundle these for
machines without Python or build tools; this source repository does not itself
contain a packaged application. The C++ SDK below serves native C++ clients.

The same core can be linked into another C++ application without Python or Qt.
Build and qualify a Windows x64 SDK ZIP under `dist/` with:

```powershell
& .\.venv\Scripts\python.exe tools/build_plant_core.py --sdk
```

The SDK contains the DLL, import library, public headers, app-local MSVC runtime
DLLs, a CMake package and an independently buildable native client. Packaging
rebuilds that client using only installed SDK files and runs it with a
Windows-only PATH. It also inspects the DLL for Python/Qt dependencies.

The qualified `dist/azeocore-sdk-windows-x64.zip` is committed with
`AzeoPlantSimulator/azeoplant/azeocore.dll`. After native source changes, run the
command above and commit both updated binaries with their source changes.

`--cpp-only --sdk` builds the SDK in `cpp/build-sdk` without finding Python or
pybind11. Direct CMake users select `-DAZEO_BUILD_PYTHON=OFF`. The default remains
ON, so an ordinary trainer build still requires and builds its Python bridge.

This is the existing C++ component API, with a matching compiler/STL ABI, not
a language-neutral full-plant C API. The host supplies scheduling and plant
assembly; Trainer-specific UI, snapshot files and SIS cause/effect evaluation
remain in their existing Python owners. See the
[SDK interface and example](../AzeoPlantSimulator/cpp/SDK.md) for the ownership,
runtime and platform requirements. The Windows x64 DLL is not a binary for
other CPU architectures or operating systems.

Native is the default. A missing extension fails an embedded plant start with
the original import/Windows loader error, interpreter version and architecture,
and the expected bridge/core paths instead of silently running another backend.
For an explicit
reference-core diagnostic session, set `$env:AZEO_NATIVE = '0'` before launch;
remove that variable to resume the default. It must be selected before import.

## Trainer adaptations to preserve

The focused `run_simulator.py` view is hosted by Simulation Workbench. The
provider exposes model metadata and bounded disturbance commands as plain
data through Local Virtual I/O. It does not expose native objects to widgets
or import the original standalone UI. See the [workflow](SIMULATION_WORKBENCH_GUIDE.md#focused-plant-simulator).

- The embedded lifecycle, Simulation Workbench controls and snapshot methods
  remain in `embedding.py`; the upstream standalone embedding file lacks some
  of these host features.
- Local VIO publishes SI tag values. Snapshot presentation preferences cannot
  change that contract.
- Local Adapter reads enter the database's Python lock facade before calling
  native sample methods. Its wait releases the GIL, preventing a reader from
  blocking the snapshot owner while that owner is reading a file. Single and
  bulk concurrent reads have a bounded subprocess regression.
- Restoring a standalone snapshot keeps the simulator's software BPCS disabled
  under the engine's database lock, preserving the host's output ownership.
- Native `IOBus::simulate_input` preserves the trainer's audited AI/DI value
  and quality overrides across ticks and snapshots. It refuses AO/DO inputs.
  The Python reference keeps the same existing behavior. Native lock acquisition
  releases the GIL while waiting for the engine's database lock.
- The default CMake build requires the Python development module and pybind11,
  so a successful host build cannot accidentally omit the extension. Only an
  explicit `AZEO_BUILD_PYTHON=OFF` requests a standalone native SDK.
- The native logger releases its Python callback before interpreter
  finalization. Without this cleanup the provider assertions passed but the
  process exited with a native access violation. A subprocess regression checks
  that final exit status.

## Verification

The build helper runs `azeocore_tests`. The imported
`AzeoPlantSimulator/tests/test_native_parity*.py` scripts compare C++ and Python
primitives, devices, buses, units and control state. Run each in a fresh process;
they select the Python reference before importing their native twin.

`AzeoPlantSimulator/tests/test_native_embedding.py` checks the active backend,
all ten unit types, external output ownership, engineering input simulation and
snapshot restoration. Run it once with the default environment and once with
`AZEO_NATIVE=0` to exercise both sides.

`tests/_smoke_virtual_io_project.py` checks the 612-point project and deterministic
regeneration. `tests/_smoke_virtual_controller_boot.py` starts the actual station,
asserts the requested backend, waits for all 160 modules and a Good live binding,
and checks provider/thread cleanup. The full repository smoke suite remains the
application contract. No duration qualification is implied by these bounded tests.

### Verified on 9 September 2026

Built with MSVC in Release mode for the repository's Windows CPython 3.13
environment on an Intel Core i7-7600U, using the revision and trainer
adaptations recorded in `AzeoPlantSimulator/NATIVE_CORE_SOURCE.json`.

| Check | Result |
| --- | --- |
| Native C++ executable checks | Zero failures |
| Focused native provider, Virtual I/O, Workbench and controller tests | 96 passed, clean process exit |
| Python reference provider and simulation tests | 24 passed |
| Native/Python parity programs | All 16 passed without skips; includes all ten plant units |
| Native thread progress check | Passed |
| Standalone I/O ownership, forcing, stale detection and reclosure | Passed on both backends |
| Application smoke suite | All 11 passed |
| Actual Operator Station boot | C++ backend, ten native units, 151 modules scanned, Good live binding, clean shutdown |
| Required Graphics/PVM lint and changed integration Python lint | Passed |

Controller integration was reverified on 22 September 2026 after adding the
nine linked CT1 class instances. All eleven application smokes passed; the
actual Operator Station boot scanned 160 modules with a Good live binding and
clean shutdown. The focused Simulation Workbench also started all ten native
units, 612 signals and 160 modules and completed its 24-click responsiveness
exercise with a largest scheduled-click interval of 206 ms.

The I/O test uses the matching upstream fixture: it restores the controller
state with the lineup and returns the heater fuel command after testing output
clamping. Both backends finish the reclosure check with a 0.11 °C deviation,
within the existing 6 °C limit.

`tools/benchmark_plant_core.py` compares fresh embedded providers against the
same updated lineup, with software BPCS disabled and SIS active. After 20 warmup
steps, it measures three batches of 300 steps per backend, including the engine,
plant and I/O work. Best batch averages were **4.514 ms/step for Python** and
**0.511 ms/step for C++**, an **8.83× speedup**. Median batch averages were
4.566 ms and 0.609 ms respectively. This measures simulation stepping, not UI
navigation latency or an end-to-end station speedup.

Local run logs are under `logs/plant-*`; `logs/plant-core-verification.json`
indexes the final results and superseded failures without deleting them.
Reproduce the benchmark with the repository venv:

```powershell
& .\.venv\Scripts\python.exe tools/benchmark_plant_core.py
```

### Simulator status and journal responsiveness

The embedded provider publishes a complete model-status DTO after a process
step, at most every 250 ms. Disturbance commands, explicit single steps and
snapshot restores publish immediately. Readers receive private copies of the
latest status and cached catalog without acquiring the engine database lock.
`sampled_at` is a monotonic timestamp; the focused UI reports a delayed update
after three seconds while running instead of waiting inside a Qt callback.

Product journal writes and rotation run on the `journal-writer` thread.
The ordinary diagnostic backlog is capped at 20,000 pending records; skipped
diagnostics are counted and reported. Audit records and errors are retained.
Explicit flush and shutdown drain pending records. The focused plant view
also omits playback-journal queries from its refresh timer.

`tests/test_simulator_stalls.py` deliberately blocks the disk writer and holds
the plant lock. Both caller-progress regressions failed before these changes.
The focused UI smoke keeps production logging enabled and exercises 24 tab
clicks after the ten-unit plant and 151 controller modules start.

Live Virtual I/O tables use resizable columns and batched cell changes instead
of continuous `ResizeToContents`. Workbench refreshes reuse existing cells and
keep row metadata only on the identity cell. A full-plant interaction check
reproduced a 28.67-second stall on the old table refresh; after this change,
all 24 scheduled tab clicks were accepted, with a largest interval of 176 ms
(150 ms requested). This is a bounded UI observation, not duration qualification.
