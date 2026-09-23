"""Device PVM classes — DEVCTL and the motor interlock's condition table.

The interlock faceplate's table tab reuses
`MotorInterlockBlock.condition_table()` through the CONDITIONS document
(§8.6) — "reuse the table for any condition-bearing block" is the
catalog's own instruction.
"""
from __future__ import annotations

from .base import Bind, PvmClass, register_pvm


@register_pvm
class DeviceFaceplate(PvmClass):
    block_type = "DEVCTL"
    role = "faceplate"
    display_name = "Device Control"
    FACEPLATE_LAYOUT = ("title", "value", "mode", "buttons")

    bindings = (
        Bind("pv.value", "{path}/STATE"),
        Bind("pv.quality", "{path}/STATE", prop="StatusCode"),
        Bind("field.value", "{path}/RUNNING"),
        Bind("state.value", "{path}/STATE"),
        Bind("state.running", "{path}/RUNNING"),
        Bind("state.fail", "{path}/FAIL_ACTIVE"),
        Bind("state.fail_code", "{path}/FAIL"),
        Bind("state.locked", "{path}/LOCKED"),
        Bind("state.paused", "{path}/PAUSED"),
        Bind("elapsed", "{path}/ELAPSED"),
        Bind("time.start_limit", "{path}/CONFIG/START_TIMEOUT",
             writable=True, persists=True),
        Bind("time.stop_limit", "{path}/CONFIG/STOP_TIMEOUT",
             writable=True, persists=True),
        Bind("mode", "{path}/MODE"),
        Bind("cmd.start", "{path}/START_CMD", writable=True),
        Bind("cmd.stop", "{path}/STOP_CMD", writable=True),
        Bind("cmd.reset", "{path}/RESET", writable=True),
        Bind("state.quality", "{path}/STATE", prop="StatusCode"),
    )


@register_pvm
class DeviceDetail(PvmClass):
    """DC_dt — device command, feedback, permits and failure detail."""

    block_type = "DEVCTL"
    role = "detail"
    display_name = "Device Control Detail"
    bindings = DeviceFaceplate.bindings + (
        Bind("command.output", "{path}/DO_START"),
        Bind("feedback.running", "{path}/RUN_FB"),
        Bind("permit.start", "{path}/PERMISSIVE_D"),
        Bind("permit.interlock", "{path}/INTERLOCK"),
        Bind("shutdown", "{path}/SHUTDOWN_D"),
        Bind("failure.code", "{path}/FAIL"),
        Bind("paused", "{path}/PAUSED"),
    )


@register_pvm
class MotorInterlockFaceplate(PvmClass):
    """The interlock summary — Azeo's DCC_fp shape.

    Declares a LAYOUT rather than falling to the generic shell. The
    shell renders one row per binding key, so an operator was being
    shown `cmd.start OFF` and `first_out.channel 17` — internal names
    and a channel number, on the screen someone reads while deciding
    whether it is safe to start a motor.
    """

    block_type = "MOTOR_INTERLOCK"
    role = "faceplate"
    display_name = "Motor Interlock"
    FACEPLATE_LAYOUT = ("title", "conditions", "buttons")

    bindings = (
        Bind("state.name", "{path}/STATE_NAME"),
        Bind("state.tripped", "{path}/TRIPPED"),
        Bind("state.ready", "{path}/READY_TO_START"),
        Bind("first_out.channel", "{path}/FIRST_OUT"),
        Bind("first_out.desc", "{path}/FIRST_OUT_DESC"),
        Bind("bypassed.any", "{path}/ANY_BYPASSED"),
        Bind("cmd.start", "{path}/START_REQ", writable=True),
        Bind("cmd.stop", "{path}/STOP_REQ", writable=True),
        Bind("cmd.reset", "{path}/RESET", writable=True),
        Bind("cmd.ack", "{path}/ACK", writable=True),
        # The Interlocks tab: the §8.6 table, one document.
        Bind("conditions", "{path}/CONDITIONS"),
    )


@register_pvm
class DeviceSymbol(PvmClass):
    """DEVCTL on a display IS its P&ID symbol — valve or motor by
    DEVICE_KIND — not a value card. The state text and running fill
    come from the block; the silhouette from the core symbol library."""

    block_type = "DEVCTL"
    role = "dynamo_compact"
    display_name = "Device"

    bindings = (
        Bind("state.running", "{path}/RUNNING"),
        Bind("state.fail", "{path}/FAIL_ACTIVE"),
        Bind("mode", "{path}/MODE"),
        Bind("kind", "{path}/CONFIG/DEVICE_KIND"),
        Bind("state.quality", "{path}/RUNNING", prop="StatusCode"),
        Bind("cond.state", "{path}/STATE"),
        Bind("cond.permissive", "{path}/PERMISSIVE_D"),
        Bind("cond.interlock", "{path}/INTERLOCK"),
    )


@register_pvm
class MotorInterlockSymbol(PvmClass):
    block_type = "MOTOR_INTERLOCK"
    role = "dynamo_compact"
    display_name = "Motor"

    bindings = (
        Bind("state.name", "{path}/STATE_NAME"),
        Bind("state.tripped", "{path}/TRIPPED"),
        Bind("state.quality", "{path}/STATE_NAME", prop="StatusCode"),
        Bind("cond.bypassed", "{path}/ANY_BYPASSED"),
    )


@register_pvm
class MotorInterlockDetail(PvmClass):
    block_type = "MOTOR_INTERLOCK"
    role = "detail"
    display_name = "Interlock Detail"

    bindings = MotorInterlockFaceplate.bindings + (
        Bind("permit.start", "{path}/START_PERMIT"),
        Bind("permit.run", "{path}/RUN_PERMIT"),
        Bind("perms.ok", "{path}/PERMS_OK"),
        Bind("trip.active", "{path}/TRIP_ACTIVE"),
    )
