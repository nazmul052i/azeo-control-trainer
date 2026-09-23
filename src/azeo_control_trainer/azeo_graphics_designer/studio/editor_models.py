"""Transactional models for structured data and interaction editors.

The display document remains intentionally dictionary based.  These helpers
put a typed, testable boundary in front of that grammar without introducing a
second serialization format.  Editors always work on copies and only hand a
candidate back after every issue has been resolved.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from math import isfinite
from typing import Callable, Iterable, Mapping

from azeo_control_trainer.core.hmi.pvms.elements import (
    ACTION_KINDS,
    ADD_TO_WATCH,
    ALARM_LIST,
    CHART,
    DATE_TIME,
    DRAG,
    MOUSE_EVENTS,
    MULTI_POINT,
    OPEN_DETAIL,
    OPEN_DISPLAY,
    OPEN_FACEPLATE,
    OPEN_USER_FACEPLATE,
    OPEN_USER_DETAIL,
    PROCEDURE_COMMAND,
    RADAR_PLOT,
    SCRIPT,
    SHOW_TOOLTIP,
    TABLE,
    TAB,
    WRITE_VALUE,
    Action,
    UserEntry,
    element_paths,
    validate_data_element,
)


@dataclass(frozen=True)
class EditorIssue:
    field: str
    message: str
    severity: str = "error"


DATA_KEYS = {
    CHART: ("pens", "lo", "hi", "window_seconds", "series_path"),
    ALARM_LIST: ("priority_min", "path_prefix", "max_rows"),
    MULTI_POINT: ("parameters",),
    RADAR_PLOT: ("parameters",),
    TAB: ("tabs", "active_tab"),
    DATE_TIME: ("timezone", "format", "culture"),
    TABLE: ("columns", "rows", "rows_path", "row_help", "row_height", "header_height", "presentation", "row_action", "command_context", "empty_text"),
    "symbol": ("ports",),
}


def data_payload(data: Mapping) -> dict:
    """Return the editable part of an item, preserving extension fields."""
    kind = str(data.get("kind", ""))
    return {key: deepcopy(data[key]) for key in DATA_KEYS.get(kind, ()) if key in data}


def merge_data_payload(original: Mapping, payload: Mapping) -> dict:
    """Replace known settings transactionally and retain unknown extensions."""
    candidate = deepcopy(dict(original))
    keys = DATA_KEYS.get(str(candidate.get("kind", "")), ())
    for key in keys:
        candidate.pop(key, None)
    for key, value in payload.items():
        if key in keys:
            candidate[key] = deepcopy(value)
    return candidate


def data_issues(data: Mapping) -> tuple[EditorIssue, ...]:
    """Detailed validation layered over the legacy first-error validator."""
    issues: list[EditorIssue] = []
    kind = data.get("kind")
    try:
        problem = validate_data_element(dict(data))
    except (AttributeError, TypeError, ValueError):
        # The legacy validator assumes row dictionaries.  The structured
        # boundary must diagnose malformed imported JSON rather than let it
        # escape into a Qt callback.
        problem = "configuration contains malformed rows"
    if problem:
        issues.append(EditorIssue(str(kind or "data"), problem))
    rows_name = "pens" if kind == CHART else "parameters"
    if kind in (CHART, MULTI_POINT, RADAR_PLOT):
        rows = data.get(rows_name, ())
        if not isinstance(rows, (list, tuple)):
            issues.append(EditorIssue(rows_name, "must be a list"))
        else:
            for index, row in enumerate(rows):
                if not isinstance(row, dict):
                    issues.append(EditorIssue(f"{rows_name}[{index}]", "must be an object"))
    for lo_name, hi_name in (("lo", "hi"),):
        if lo_name in data and hi_name in data:
            try:
                lo, hi = float(data[lo_name]), float(data[hi_name])
                if not (isfinite(lo) and isfinite(hi) and lo < hi):
                    raise ValueError
            except (TypeError, ValueError):
                issues.append(EditorIssue("range", "minimum must be below maximum"))
    if kind == CHART and "window_seconds" in data:
        try:
            if float(data["window_seconds"]) <= 0:
                raise ValueError
        except (TypeError, ValueError):
            issues.append(EditorIssue("window_seconds", "must be positive"))
    if kind == ALARM_LIST:
        for key, low, high in (("priority_min", 0, 255), ("max_rows", 1, 1000)):
            if key not in data:
                continue
            try:
                value = int(data[key])
                if not low <= value <= high:
                    raise ValueError
            except (TypeError, ValueError):
                issues.append(EditorIssue(key, f"must be {low}..{high}"))
    if kind == TAB:
        tabs = data.get("tabs", ())
        try:
            active = int(data.get("active_tab", 0))
            if tabs and not 0 <= active < len(tabs):
                raise ValueError
        except (TypeError, ValueError):
            issues.append(EditorIssue("active_tab", "must identify an existing tab"))
    if kind == TABLE:
        keys = {str(col.get("key", "")) for col in data.get("columns", ()) if isinstance(col, dict)}
        for row_index, row in enumerate(data.get("rows", ())):
            if isinstance(row, dict):
                unknown = set(row) - keys
                if unknown:
                    issues.append(
                        EditorIssue(
                            f"rows[{row_index}]", "unknown columns: " + ", ".join(sorted(unknown))
                        )
                    )
        for name in ("row_height", "header_height"):
            if name in data:
                try:
                    if float(data[name]) <= 0:
                        raise ValueError
                except (TypeError, ValueError):
                    issues.append(EditorIssue(name, "must be positive"))
    # De-duplicate a general legacy error and a more precise copy.
    return tuple(dict.fromkeys(issues))


def data_binding_issues(
    data: Mapping, resolver: Callable[[str], bool] | None = None
) -> tuple[EditorIssue, ...]:
    """Validate every live path a compound element contributes at publish."""
    issues = []
    for index, path in enumerate(element_paths(dict(data))):
        if not path:
            issues.append(EditorIssue(f"paths[{index}]", "path is empty"))
        elif resolver is not None and not resolver(path):
            issues.append(EditorIssue(f"paths[{index}]", f"unresolved path {path}"))
    return tuple(issues)


def property_descriptor_issues(
    descriptor: Mapping,
    *,
    field: str = "property",
    resolver: Callable[[str], bool] | None = None,
) -> tuple[EditorIssue, ...]:
    """Validate the persisted ``props`` grammar without constructing Qt.

    Expression references are deliberately checked separately from the
    authored expression.  A syntactically valid expression over an unresolved
    tag is still an invalid published display.
    """
    if not isinstance(descriptor, Mapping):
        return (EditorIssue(field, "descriptor must be an object"),)
    kind = str(descriptor.get("kind", ""))
    issues: list[EditorIssue] = []
    if kind in {"animation", "blink"}:
        key = "path" if kind == "animation" else "condition"
        path = str(descriptor.get(key, "")).strip()
        if not path:
            issues.append(EditorIssue(f"{field}.{key}", "a path is required"))
        elif resolver is not None and not descriptor.get("indirect") and not resolver(path):
            issues.append(EditorIssue(f"{field}.{key}", f"unresolved path {path}"))
    elif kind == "expression":
        expression = str(descriptor.get("expr", "")).strip()
        if not expression:
            issues.append(EditorIssue(f"{field}.expr", "an expression is required"))
        refs = descriptor.get("refs", {})
        if refs is not None and not isinstance(refs, Mapping):
            issues.append(EditorIssue(f"{field}.refs", "references must be an object"))
        elif isinstance(refs, Mapping):
            for name, source in refs.items():
                path = str(source).strip()
                if not str(name).strip() or not path:
                    issues.append(
                        EditorIssue(f"{field}.refs", "reference names and paths are required")
                    )
                elif resolver is not None and not resolver(path):
                    issues.append(EditorIssue(f"{field}.refs.{name}", f"unresolved path {path}"))
    elif kind in {"pvm", "property", "standard", "variable"}:
        if not str(descriptor.get("ref", "")).strip():
            issues.append(EditorIssue(f"{field}.ref", "a reference is required"))
    else:
        issues.append(EditorIssue(field, f"unsupported descriptor kind {kind!r}"))
    return tuple(issues)


def user_entry_issues(entry: UserEntry) -> tuple[EditorIssue, ...]:
    issues = []
    problem = entry.validate()
    if problem:
        issues.append(EditorIssue("entry", problem))
    if entry.options:
        if any(len(option) != 2 for option in entry.options):
            issues.append(EditorIssue("options", "each option needs value and caption"))
        values = [repr(option[0]) for option in entry.options if len(option) == 2]
        if len(values) != len(set(values)):
            issues.append(EditorIssue("options", "option values must be unique"))
    if entry.kind in ("slew", "slider") and not (isfinite(entry.lo) and isfinite(entry.hi)):
        issues.append(EditorIssue("range", "range values must be finite"))
    return tuple(issues)


_STATION_ACTIONS = {OPEN_DISPLAY, OPEN_USER_FACEPLATE, OPEN_USER_DETAIL, WRITE_VALUE, SCRIPT, PROCEDURE_COMMAND}


def action_issues(
    action: Action,
    *,
    operator_target: bool = True,
    display_names: Iterable[str] = (),
    faceplate_names: Iterable[str] = (),
) -> tuple[EditorIssue, ...]:
    issues: list[EditorIssue] = []
    if action.event not in MOUSE_EVENTS:
        issues.append(EditorIssue("event", f"unsupported event {action.event!r}"))
    elif action.event == DRAG:
        issues.append(
            EditorIssue("event", "drag actions are not dispatched by the current runtime")
        )
    if action.kind not in ACTION_KINDS:
        issues.append(EditorIssue("kind", f"unsupported action {action.kind!r}"))
        return tuple(issues)
    if operator_target and action.kind not in _STATION_ACTIONS:
        issues.append(
            EditorIssue(
                "kind", "this action is Studio-only and will not run at an operator station"
            )
        )
    required_target = {
        OPEN_FACEPLATE,
        OPEN_USER_FACEPLATE,
        OPEN_USER_DETAIL,
        OPEN_DETAIL,
        OPEN_DISPLAY,
        WRITE_VALUE,
        SHOW_TOOLTIP,
    }
    if action.kind in required_target and not action.target.strip():
        issues.append(EditorIssue("target", "a target is required"))
    if action.kind == SCRIPT and not action.source.strip():
        issues.append(EditorIssue("source", "script source is required"))
    displays = set(display_names)
    if action.kind == OPEN_DISPLAY and displays and action.target not in displays:
        issues.append(EditorIssue("target", "display does not exist"))
    faceplates = set(faceplate_names)
    if action.kind in {OPEN_USER_FACEPLATE, OPEN_USER_DETAIL} and faceplates and action.target not in faceplates:
        issues.append(EditorIssue("target", "faceplate class does not exist"))
    if action.kind == PROCEDURE_COMMAND:
        from azeo_control_trainer.core.procedures.hmi import COMMANDS
        if action.target not in COMMANDS:
            issues.append(EditorIssue("target", "choose a procedure command: " + ", ".join(COMMANDS)))
        if not action.source.startswith("@procedure/") or not action.source.endswith("/CONTEXT"):
            issues.append(EditorIssue("source", "use @procedure/{ProcedureRef}/CONTEXT as the displayed command context"))
        if action.value is not None:
            issues.append(EditorIssue("value", "procedure responses must be entered by the operator at runtime"))
    if action.kind == WRITE_VALUE and action.value is None:
        issues.append(EditorIssue("value", "a write value is required"))
    if action.kind == ADD_TO_WATCH and action.target:
        issues.append(
            EditorIssue(
                "target",
                "watch uses the selected element binding; target is only a label",
                "warning",
            )
        )
    return tuple(issues)


def actions_issues(
    rows: Iterable[Mapping],
    *,
    operator_target: bool = True,
    display_names: Iterable[str] = (),
    faceplate_names: Iterable[str] = (),
) -> tuple[EditorIssue, ...]:
    """Validate an action list and retain an indexed document location."""
    issues = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            issues.append(EditorIssue(f"actions[{index}]", "must be an object"))
            continue
        for issue in action_issues(
            Action.from_dict(dict(row)),
            operator_target=operator_target,
            display_names=display_names,
            faceplate_names=faceplate_names,
        ):
            issues.append(
                EditorIssue(f"actions[{index}].{issue.field}", issue.message, issue.severity)
            )
    return tuple(issues)


def merge_actions(original: Iterable[Mapping], edited: Iterable[Action]) -> list[dict]:
    """Preserve per-row extension keys while replacing the known contract."""
    originals = [deepcopy(dict(row)) for row in original]
    known = {"event", "kind", "target", "active", "value", "source"}
    out = []
    for index, action in enumerate(edited):
        row = originals[index] if index < len(originals) else {}
        for key in known:
            row.pop(key, None)
        row.update(action.to_dict())
        out.append(row)
    return out


__all__ = [
    "DATA_KEYS",
    "EditorIssue",
    "action_issues",
    "actions_issues",
    "data_issues",
    "data_binding_issues",
    "data_payload",
    "merge_actions",
    "merge_data_payload",
    "property_descriptor_issues",
    "user_entry_issues",
]
