"""Faceplate sub-package — ISA-101 PID, analog and device faceplates.

Two families live here and the split is structural, not stylistic:

* **Tag-bound** faceplates (``PID``, ``AI``, ``AO``, valve) follow a point in
  the store and read it from there.
* **Block-bound** faceplates (``DEVCTL``, ``MOTOR_INTERLOCK``) follow a live
  function block, because their state — the running state, the first-out
  cause, which permissive is missing — lives on terminals and never reaches
  the store.

``BLOCK_FACEPLATES`` is the dispatch table for the second family;
``FaceplateManager.open_device_faceplate`` is what consults it.
"""
from .block_faceplate import BlockFaceplate
from .device_faceplate import DevctlFaceplate
from .interlock_faceplate import InterlockFaceplate
from .motor_faceplate import MotorInterlockFaceplate
from .start_blocked import StartBlockedDialog

#: ``block_type`` → block-bound faceplate class.
BLOCK_FACEPLATES: dict[str, type[BlockFaceplate]] = {
    MotorInterlockFaceplate.BLOCK_TYPE: MotorInterlockFaceplate,
    DevctlFaceplate.BLOCK_TYPE: DevctlFaceplate,
    InterlockFaceplate.BLOCK_TYPE: InterlockFaceplate,
}

__all__ = [
    "BLOCK_FACEPLATES",
    "BlockFaceplate",
    "DevctlFaceplate",
    "InterlockFaceplate",
    "MotorInterlockFaceplate",
    "StartBlockedDialog",
]
