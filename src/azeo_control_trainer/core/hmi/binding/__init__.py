"""The Phase 2 binding engine (HMI proposal §9) — Qt-free.

`BindingEngine` over a `LiveGraphSource` is how a PVM reaches a block:
one path in, a full `BindingResult` out — value, quality, limit, forced,
EU metadata, alarm summary, last-good history, mode pair. See
`docs/new_hmi_studio/IMPLEMENTATION_NOTES.md`.
"""
from .engine import Binding, BindingEngine, BindingError
from .alarm_state import RuntimeAlarm, RuntimeAlarmRegistry
from .result import UNRESOLVED, BindingResult
from .source import LiveGraphSource, WriteResult

__all__ = ["Binding", "BindingEngine", "BindingError", "BindingResult",
           "LiveGraphSource", "RuntimeAlarm", "RuntimeAlarmRegistry",
           "UNRESOLVED", "WriteResult"]
