# Azeo Core C++ SDK 1.0.0

`azeocore.dll` contains the same process physics, ten unit classes, devices,
tag database, Virtual I/O rules and PID primitives used by the Azeo simulator.
The Python `_azeocore` bridge links this DLL; it does not embed another core.
Native C++ applications use the DLL directly without Python or Qt.

## Run the example

Copy the entire SDK folder to a Windows x64 machine and run
`bin/azeocore_example.exe`. It drives a dynamic process through the checked
Virtual I/O bus, verifies a Good flow measurement and refuses an input write.
Keep the accompanying runtime DLLs beside your executable and `azeocore.dll`.

## Link an application

Use the SDK's matching headers and import library. The shipped Windows build
targets Windows 10/11 x64 with the MSVC compiler/toolset recorded in
`build.json`, Release mode and the shared
C/C++ runtime (`/MD`, `_ITERATOR_DEBUG_LEVEL=0`). Do not mix Debug/STL layouts,
MinGW binaries or headers from another SDK release. This is a C++ interface;
it is not a C#/P/Invoke or language-neutral simulation API.

From the SDK folder, in an x64 Visual Studio C++ developer terminal with CMake
and Ninja available:

```powershell
cmake -S examples -B example-build -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH="$PWD"
cmake --build example-build --config Release
.\example-build\azeocore_example.exe
```

In your own CMake project:

```cmake
find_package(AzeoCore 1.0.0 EXACT CONFIG REQUIRED)
target_link_libraries(my_application PRIVATE Azeo::Core)
```

`Azeo::Core` provides the include path, import library and C++20 requirement.
For another build system, add `include`, link `lib/azeocore.lib`, and deploy
the files from `bin` beside your executable (the example EXE is optional).
`azeocore/version.h` exposes runtime version/ABI identification.

## Interface and ownership

| Header | API |
| --- | --- |
| `tags.hpp` | TagDatabase, tag kind/quality, engineering ranges and sample values |
| `iobus.hpp` | IOBus, single output holder, checked AO/DO writes, queue, input simulation, snapshots |
| `bus.hpp` | ProcessBus, declared producer/consumer contracts and tear streams |
| `units/*.hpp` | U010, U100, U200, U300, U400, U500, U600, U700, U800, U900 |
| `devices.hpp`, `packages.hpp` | Transmitters, valves, motors, pumps, heat exchangers and device packages |
| `dynamics.hpp` | Lags, dead time, integration, limits and reproducible noise |
| `control/pid.hpp` | PID primitive for a host's control implementation |
| `state.hpp` | In-memory state values and capture/apply interfaces |
| `log.hpp` | Optional logging callback and calculation traces |

The caller owns scheduling and object lifetimes. Create the tag database and
process bus before their units; destroy the units first. Hold `db.lock` around
multi-object steps and coherent reads. Send external controller writes through
`IOBus`, never directly into AI/DI values. Release registered callbacks before
unloading the DLL. C++ exceptions can cross this matching C++ ABI and should be
caught by the host. The API does not create threads or perform host UI work.

This SDK exposes the existing native component layer. The Trainer still owns
its Python flowsheet assembly, scheduling, snapshot files, independent SIS
cause/effect logic, and the UI. `units::SafetySystem` is the process-side
initiator/effect model, not that SIS evaluator. Native hosts must supply their
own orchestration and control/SIS logic; linking the DLL alone does not recreate
the Trainer's complete configured plant. The example is a small standalone rig.

## Build the DLL without Python

From the component's `cpp` source directory, in a Visual Studio C++ developer
terminal:

```powershell
cmake -S . -B build-sdk -G Ninja -DCMAKE_BUILD_TYPE=Release -DAZEO_BUILD_PYTHON=OFF
cmake --build build-sdk --config Release
ctest --test-dir build-sdk -C Release --output-on-failure
cmake --install build-sdk --config Release --prefix ./sdk
```

Only a C++20 compiler and CMake are needed. There is no Python, pybind11 or Qt
dependency in this build. A Windows DLL runs on its matching Windows CPU
architecture; other operating systems/architectures require their own build.
Source builds on those platforms are supported by CMake but are not qualified
by the Windows verification shipped with this SDK.
