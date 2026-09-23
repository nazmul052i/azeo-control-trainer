"""Machine views pair a speed regulator with its independent run sequencer.

The controller owns every command, limit and protection. These classes only
present the existing PID and DEVCTL interfaces through checked HMI writes.
"""
from dataclasses import replace
import math

from .base import Bind, PvmClass, register_pvm
from .control import PIDFaceplate, PIDInline
from .device import DeviceFaceplate


class MachineView(PvmClass):
    PARAMS = ("path", "device")
    EXCEPTION = "A machine pairs a PID speed regulator with a separate DEVCTL run sequencer."
    PARAM_LABELS = {"path": "Speed controller (PID)", "device": "Run control (DEVCTL)"}
    PARAM_TYPES = {"path": "PID", "device": "DEVCTL"}
    DETAIL_PARAM = "path"


_DEVICE_BINDS = tuple(replace(spec, key="device." + spec.key,
                              path=spec.path.replace("{path}", "{device}"))
                      for spec in DeviceFaceplate.bindings) + (
    Bind("device.permit", "{device}/PERMISSIVE_D"),
    Bind("device.interlock", "{device}/INTERLOCK"),
)


def _classes(variant, title):
    # Stable class identities keep configuration revisions and registry
    # lookup distinct from the generic PID faceplate.
    visual = type(title.replace(" ", "") + "Pvm", (MachineView,), dict(
        block_type="PID", role="dynamo_inline", variant=variant,
        display_name=title, DEFAULT_SIZE=(180.0, 140.0),
        bindings=PIDInline.bindings + (
            Bind("state.running", "{device}/RUNNING"),
            Bind("state.fail", "{device}/FAIL_ACTIVE"),
            Bind("cond.state", "{device}/STATE"),
            Bind("cond.permissive", "{device}/PERMISSIVE_D"),
            Bind("cond.interlock", "{device}/INTERLOCK"),
        )))
    faceplate = type(title.replace(" ", "") + "Faceplate", (MachineView,), dict(
        block_type="PID", role="faceplate", variant=variant,
        display_name=title, FACEPLATE_LAYOUT=PIDFaceplate.FACEPLATE_LAYOUT,
        bindings=PIDFaceplate.bindings + _DEVICE_BINDS))
    return register_pvm(visual), register_pvm(faceplate)


VFDSpeedPvm, VFDSpeedFaceplate = _classes("vfd", "VFD Speed")
TurbineSpeedPvm, TurbineSpeedFaceplate = _classes("turbine_speed", "Turbine Speed")
CompressorSpeedPvm, CompressorSpeedFaceplate = _classes("compressor_speed", "Compressor Speed")

MACHINE_VARIANTS = ("vfd", "turbine_speed", "compressor_speed")


def machine_readout(pvm, rows, primary):
    """Format on binding refresh, never in the per-frame painter."""
    def number(key):
        binding = rows.get(key)
        result = binding.result if binding else None
        if result is None or result.quality.name != "GOOD":
            return "---"
        try:
            return f"{float(result.value):.4g}" if math.isfinite(float(result.value)) else "---"
        except (TypeError, ValueError):
            return "---"

    running = rows.get("RUNNING")
    value = running.result if running else None
    status = ("RUNNING" if value.value else "STOPPED") if value is not None \
        and value.quality.name == "GOOD" and isinstance(value.value, bool) else "STATUS UNAVAILABLE"
    failed = rows.get("FAIL")
    fault = failed is not None and failed.result.quality.name == "GOOD" and failed.result.value is True
    label = pvm.label or pvm.params.get("path", "").split("/")[0]
    units = str(primary.units or "") if primary is not None else ""
    reading = f"PV {number('PV')} / SP {number('SP')} {units}".rstrip()
    return label, reading, "FAULT" if fault else status, fault
