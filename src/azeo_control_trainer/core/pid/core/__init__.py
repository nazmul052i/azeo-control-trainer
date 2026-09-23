"""Core PID engine — no UI dependencies."""

from .pid_block_core import (
    PIDBlock,
    Mode,
    PIDForm,
    Structure,
    LimitStatus,
    SignalStatus,
    ScaleRange,
    BlockError,
    AlarmState,
    validate_pid_config,
    LinearizationType,
    ShedOption,
    TrackOption,
    ProcessType,
)

__all__ = [
    "PIDBlock", "Mode", "PIDForm", "Structure",
    "LimitStatus", "SignalStatus", "ScaleRange",
    "BlockError", "AlarmState", "validate_pid_config",
    "LinearizationType", "ShedOption", "TrackOption", "ProcessType",
]
