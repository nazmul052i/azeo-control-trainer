"""The High Performance hover window.

"When hovering the mouse on the PVM, shows a pop-up window that
provides additional information about the PVM." The manual lists what
it may carry; this builds those lines from what the bindings actually
resolved.

**A line is omitted, never invented.** The hover window is where an
operator goes when the PVM itself is not enough, so a row reading `SP
0.0` for a block that has no setpoint is worse than no row — it answers
a question the operator asked with something that was never true. Rows
appear only when their binding resolved.
"""
from __future__ import annotations

from ...binding.result import UNRESOLVED
from .marks import CONDITION_TIPS
from .state import AlarmBoxState
from .tag import format_data_field, module_of

#: The order the manual lists, so the window reads the same on every
#: PVM class and the operator's eye learns one layout.
VALUE_ROWS = (("PV", "PV"), ("SP", "SP"), ("OUT", "OUT"))
LIMIT_ROWS = (("HH", "HIHI"), ("HI", "HI"), ("LO", "LO"), ("LL", "LOLO"))


def _resolved(binding):
    if binding is None:
        return None
    result = getattr(binding, "result", None)
    if result is None or result is UNRESOLVED:
        return None
    return result


def hover_lines(pvm, result=None, rows: dict | None = None,
                state: AlarmBoxState | None = None,
                description: str = "") -> list[str]:
    """The hover window's content, as `label: value` lines."""
    rows = rows or {}
    lines: list[str] = []
    module = module_of(pvm)
    if module:
        lines.append(module)
    if description:
        lines.append(description)
    if result is not None and result is not UNRESOLVED:
        if result.mode_actual:
            mode = result.mode_actual
            if result.mode_target and result.mode_target != mode:
                # Both, when they disagree: which mode it is IN and
                # which it was ASKED for is the whole content of a
                # mode alarm, and one without the other cannot say it.
                mode = f"{mode} (target {result.mode_target})"
            lines.append(f"Mode: {mode}")
    units = str(getattr(result, "units", "") or "") if result else ""
    for key, label in VALUE_ROWS:
        row = _resolved(rows.get(key))
        if row is None:
            continue
        suffix = f" {units}" if units and key != "OUT" else ""
        lines.append(f"{label}: "
                     f"{format_data_field(row.value).strip()}{suffix}")
    limits = [f"{label} {format_data_field(row.value).strip()}"
              for key, label in LIMIT_ROWS
              if (row := _resolved(rows.get(key))) is not None]
    if limits:
        lines.append("Limits: " + "  ".join(limits))
    if state is not None:
        if state.condition:
            lines.append(f"Top alarm: {state.condition}")
        if state.alarm_count:
            lines.append(f"Total alarms: {state.alarm_count}")
        conditions = [CONDITION_TIPS.get(field, field)
                      for field in state.visible_conditions()]
        if conditions:
            lines.append("Status: " + ", ".join(conditions))
    return lines


def hover_text(pvm, result=None, rows: dict | None = None,
               state: AlarmBoxState | None = None,
               description: str = "") -> str:
    """The hover window as one tooltip string."""
    return "\n".join(hover_lines(pvm, result, rows, state, description))
