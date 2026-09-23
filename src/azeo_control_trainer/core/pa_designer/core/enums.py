from enum import Enum


class ProcedureStatus(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    INTERRUPTED = "INTERRUPTED"
    PAUSED = "PAUSED"
    HELD = "HELD"
    ABORTED = "ABORTED"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class StepStatus(str, Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    HELD = "HELD"
    ABORTED = "ABORTED"
    WARNING = "WARNING"
    ALARM = "ALARM"


class ProcedureMode(str, Enum):
    VIEW_ONLY = "view_only"
    ADVISORY = "advisory"
    SEMI_AUTO = "semi_auto"
    AUTO = "auto"
