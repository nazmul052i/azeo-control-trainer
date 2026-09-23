"""The native core switch.

The C++ core (``cpp/``, built as ``azeocore.dll`` with the Python bridge
``azeoplant/_azeocore`` linking it) is the
default since phase 7 of docs/Cpp_Core_Migration_Plan.md: when the
extension is built, the package import swaps it in and everything runs
on it. ``AZEO_NATIVE=0`` keeps the Python core, which remains the
reference implementation every parity test compares against; the parity
tests set that for themselves. Without the extension the Python core
runs for reference imports, with a warning; the Trainer's embedded factory
refuses startup until built unless the reference was explicitly selected.
This module is the one place that decides.

    from azeoplant.core.native import enabled, module
    if enabled():
        Lag = module().Lag

Phase 3 of docs/Cpp_Core_Migration_Plan.md is complete: every unit has a
bit-exact native twin. ``activate()`` swaps the whole core in: the tag
database, the I/O bus, the process bus and its specs, the balance
readings and the unit classes the flowsheet builds. The package import
calls it once when ``AZEO_NATIVE=1``, so ``run.py``, the tests and the
tools run on the native core with nothing but the environment variable;
``tools/run_native_oracle.py`` calls it with an explicit unit list for a
mixed plant. The Python core remains the explicitly selectable reference.

The parity tests pin ``AZEO_NATIVE=0`` before importing the package:
they build the Python twin themselves and activate the native core for
the other side.
"""

from __future__ import annotations

import atexit
import importlib
from importlib.machinery import EXTENSION_SUFFIXES
import logging
import os
from pathlib import Path
import platform
import struct
import sys
from types import ModuleType
from typing import Optional

log = logging.getLogger(__name__)

_module: Optional[ModuleType] = None
_tried = False
_load_error: str | None = None
_activated: dict = {}

#: unit code -> native class name; every unit of the flowsheet has its twin
NATIVE_UNITS = {"U010": "FuelGasHeader", "U100": "FeedSection", "U200": "RecycleCompressor",
                "U300": "FiredHeater", "U400": "ReactorSection", "U500": "ColumnT1",
                "U600": "ColumnT2", "U700": "SteamBoiler", "U800": "EffluentTreatment",
                "U900": "SafetySystem"}


def available() -> bool:
    """Is the extension built and importable?"""
    return module() is not None


_warned = False


def requested() -> bool:
    """The default is the native core; ``AZEO_NATIVE=0`` (or false, no,
    off) asks for the Python reference instead."""
    return os.environ.get("AZEO_NATIVE", "1").strip().lower() not in ("0", "false", "no", "off")


def enabled() -> bool:
    """Requested and available. The only question the facades ask."""
    global _warned
    if not requested():
        return False
    if not available():
        if not _warned:
            _warned = True
            log.warning("%s", unavailable_message())
        return False
    return True


def module() -> Optional[ModuleType]:
    global _module, _tried, _load_error
    if not _tried:
        _tried = True
        try:
            _module = importlib.import_module("azeoplant._azeocore")
        except (ImportError, OSError) as error:
            _module = None
            # A missing bridge, wrong Python ABI and missing CRT used to become
            # the same error, leaving the destination PC impossible to diagnose.
            _load_error = f"{type(error).__name__}: {error}"
        if _module is not None:
            _register(_module)
    return _module


def diagnostics() -> dict:
    """Serializable loader evidence, without discarding the original failure."""
    loaded = module()
    directory = Path(__file__).resolve().parents[1]
    return {
        "available": loaded is not None,
        "python": sys.version.split()[0],
        "architecture": f"{struct.calcsize('P') * 8}-bit {platform.machine()}",
        "executable": sys.executable,
        "expected_bridge": str(directory / ("_azeocore" + EXTENSION_SUFFIXES[0])),
        "bridge_files": sorted(path.name for path in directory.glob("_azeocore*")),
        "core_dll": str(directory / "azeocore.dll"),
        "core_dll_exists": (directory / "azeocore.dll").is_file(),
        "load_error": _load_error,
    }


def unavailable_message() -> str:
    details = diagnostics()
    return (
        "The configured C++ plant core is not available.\n"
        f"{details['load_error'] or 'The native core was not activated.'}\n"
        f"Python {details['python']} ({details['architecture']})\n"
        f"Interpreter: {details['executable']}\n"
        f"Expected bridge: {details['expected_bridge']}\n"
        f"Found bridges: {', '.join(details['bridge_files']) or 'none'}\n"
        f"Core DLL: {details['core_dll']} "
        f"({'present' if details['core_dll_exists'] else 'missing'})\n"
        "Use the complete Windows app folder, or rebuild with this Python and "
        "tools/build_plant_core.py. Restart the application after repairing the files."
    )


def _register(mod: ModuleType) -> None:
    """Hand the native module the Python types it must give back or raise.

    Native tags return ``Quality`` and ``TagKind`` members, so identity
    checks such as ``quality is Quality.GOOD`` keep working; the native
    I/O bus returns ``SignalSample`` and ``ForceRecord`` and raises
    ``OwnershipViolation``; the native process bus raises
    ``BusContractError``. Bus log lines go to the Python loggers the
    modules already use.
    """
    from azeoplant.core.tags import Quality, TagKind
    from azeoplant.io.forcing import ForceRecord
    from azeoplant.io.ownership import OwnershipViolation
    from azeoplant.io.sample import SignalSample
    from azeoplant.models.accountability import BusContractError

    bus_log = logging.getLogger("azeoplant.io.bus")

    def emit(level: str, message: str) -> None:
        getattr(bus_log, level if level != "exception" else "error")(message)

    from azeoplant.models.accountability import ParameterSpec

    mod.register_enums(Quality, TagKind)
    mod.register_io_types(SignalSample, OwnershipViolation, BusContractError,
                          ForceRecord, emit)
    mod.register_model_types(ParameterSpec)
    from azeoplant.control.pid import Mode, Structure
    mod.register_control_enums(Mode, Structure)
    mod.set_log_sink(_log_sink)
    # The native sink owns a Python callable. Its static C++ destructor runs
    # after interpreter finalization: provider tests passed, then exited with
    # an access violation. Release that callable while Python is still alive.
    atexit.register(mod.set_log_sink, None)


def _log_sink(level: int, logger: str, message: str) -> None:
    """Every native log line lands on the Python logger of the same name.

    The native core's level numbers are Python's (10 debug to 50
    critical), so handlers, filters and the per-module levels an
    operator has configured apply to native units exactly as to Python
    ones. A unit's calculation trace (``unit.trace_enabled = True``)
    arrives here at DEBUG under ``azeoplant.models.<code>``.
    """
    logging.getLogger(logger).log(level, message)


def trace(unit, on: bool = True, every: int = 10) -> None:
    """Switch a native unit's calculation trace on or off.

    While on, the unit records its intermediates each step (pressures,
    valve flows, inventory in, out and net, balance residuals) in
    ``unit.trace`` and logs the frame every ``every`` steps at DEBUG.
    Off, it costs one boolean test per value.
    """
    unit.trace_enabled = bool(on)
    unit.trace_every = int(every)


def activate(units=None) -> dict:
    """Swap the native core into the Python modules that name its classes.

    ``units`` is the list of unit codes to run natively (default: all of
    them; an empty list swaps the primitives only). Returns a dictionary
    of what was rebound, ``{"azeoplant.core.tags.TagDatabase":
    "TagDatabase", ..., "U010": "FuelGasHeader", ...}``. Idempotent.

    Order matters and is handled here: the module is loaded and its
    Python-side types registered first, then the names are rebound in
    every module that imported them, and last the flowsheet's unit table
    and bus specs. Anything imported after this sees the native classes
    under the Python names.
    """
    cc = module()
    if cc is None:
        raise RuntimeError("native core not built: cmake -S cpp -B cpp/build")

    import azeoplant.core.engine as engine
    import azeoplant.core.tags as tags
    import azeoplant.io as io_pkg
    import azeoplant.io.bus as io_bus
    import azeoplant.models.accountability as acc
    import azeoplant.models.base as base
    import azeoplant.models.flowsheet as flowsheet
    import azeoplant.control.pid as pid
    import azeoplant.control.strategy as strategy

    swaps = {
        (tags, "TagDatabase"): cc.TagDatabase,
        (tags, "Tag"): cc.Tag,
        (flowsheet, "TagDatabase"): cc.TagDatabase,
        (engine, "TagDatabase"): cc.TagDatabase,
        (base, "TagDatabase"): cc.TagDatabase,
        (base, "Tag"): cc.Tag,
        (io_bus, "VirtualIOBus"): cc.VirtualIOBus,
        (io_pkg, "VirtualIOBus"): cc.VirtualIOBus,
        (io_bus, "TagDatabase"): cc.TagDatabase,
        (io_bus, "Tag"): cc.Tag,
        (acc, "ProcessBus"): cc.ProcessBus,
        (acc, "BusSignalSpec"): cc.BusSignalSpec,
        (acc, "BalanceReading"): cc.BalanceReading,
        (flowsheet, "ProcessBus"): cc.ProcessBus,
        (flowsheet, "BusSignalSpec"): cc.BusSignalSpec,
        (base, "BalanceReading"): cc.BalanceReading,
        # phase 5: the PID block; the strategy imported it by name
        (pid, "PID"): cc.PID,
        (pid, "Alarms"): cc.PidAlarms,
        (strategy, "PID"): cc.PID,
    }
    done = {}
    for (mod, name), new in swaps.items():
        if hasattr(mod, name):
            setattr(mod, name, new)
            done[f"{mod.__name__}.{name}"] = new.__name__
    # the flowsheet builds its bus from a module-level tuple of specs; they
    # become native specs so the native ProcessBus receives its own type
    if hasattr(flowsheet, "BUS_SIGNALS"):
        flowsheet.BUS_SIGNALS = tuple(
            s if isinstance(s, cc.BusSignalSpec) else
            cc.BusSignalSpec(s.name, s.default, s.eu, s.producer, tuple(s.consumers),
                             s.lo, s.hi, s.tear, s.description)
            for s in flowsheet.BUS_SIGNALS)
        done["azeoplant.models.flowsheet.BUS_SIGNALS"] = "native specs"

    wanted = set(NATIVE_UNITS) if units is None else set(units)
    classes = list(flowsheet.UNIT_CLASSES)
    for i, cls in enumerate(classes):
        code = getattr(cls, "code", None)
        if code in wanted:
            if code not in NATIVE_UNITS:
                raise RuntimeError(f"no native twin for {code}")
            classes[i] = getattr(cc, NATIVE_UNITS[code])
            done[code] = NATIVE_UNITS[code]
    missing = wanted - {getattr(c, "code", None) for c in classes}
    if missing:
        raise RuntimeError(f"units not in the flowsheet: {sorted(missing)}")
    flowsheet.UNIT_CLASSES = classes
    # phase 6: the strategy's scan runs on the native scanner
    from azeoplant.control import native_scan
    native_scan.install(strategy.ControlSystem)
    done["azeoplant.control.strategy.ControlSystem.step"] = "native_scan"
    _activated.clear()
    _activated.update(done)
    swapped = [f"{k} ({v})" for k, v in sorted(done.items()) if k.startswith("U")]
    log.info("Native core active: %s", ", ".join(swapped) or "primitives only")
    return done


def active() -> dict:
    """What ``activate()`` rebound; empty when the Python core is running."""
    return dict(_activated)
