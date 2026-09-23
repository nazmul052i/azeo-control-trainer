"""Timed, render-only scenarios. No controller or field write belongs here."""
from __future__ import annotations

import copy
import math
from numbers import Real


STATES = ("Normal", "Alarm", "Acknowledged", "Recovered", "Bad quality", "Communication loss", "Forced", "Manual")


def validate_steps(steps):
    if not steps:
        raise ValueError("Add at least one step")
    result = copy.deepcopy(steps)
    seen = set()
    for step in result:
        at = float(step["at"])
        if not math.isfinite(at) or not 0 <= at <= 86400:
            raise ValueError("Step time must be between 0 and 86400 seconds")
        step["at"] = at
        path = str(step["path"]).strip()
        if len(path.split("/")) < 3:
            raise ValueError("Use a complete MODULE/BLOCK/PARAMETER path")
        step["path"] = path
        if (at, path) in seen:
            raise ValueError("Two steps cannot set the same tag at the same time")
        seen.add((at, path))
        if step.get("state", "Normal") not in STATES:
            raise ValueError("Unknown preview state")
        if step.get("quality", "GOOD") not in ("GOOD", "UNCERTAIN", "BAD"):
            raise ValueError("Unknown signal quality")
        value = step.get("value")
        if isinstance(value, Real) and not math.isfinite(value):
            raise ValueError("Preview values must be finite")
    return sorted(result, key=lambda step: (step["at"], step["path"]))


def sequence_values(steps, elapsed):
    current, upcoming = {}, {}
    for step in steps:
        path = step["path"]
        if step["at"] <= elapsed:
            current[path] = step
        elif path not in upcoming:
            upcoming[path] = step
    result = {}
    for path, step in current.items():
        value = step.get("value")
        next_step = upcoming.get(path)
        if next_step and next_step.get("ramp"):
            end = next_step.get("value")
            if all(isinstance(v, Real) and not isinstance(v, bool) for v in (value, end)):
                fraction = (elapsed - step["at"]) / (next_step["at"] - step["at"])
                value += (end - value) * fraction
        state = step.get("state", "Normal")
        result[path] = dict(value=value, quality="BAD" if state == "Bad quality" else step.get("quality", "GOOD"),
                            alarm_active=state in ("Alarm", "Acknowledged"),
                            alarm_acked=state == "Acknowledged", alarm_priority=15 if state in ("Alarm", "Acknowledged") else 0,
                            alarm_condition="HI_HI" if state in ("Alarm", "Acknowledged") else "",
                            forced=state == "Forced", unresolved=state == "Communication loss",
                            mode_actual="MAN" if state == "Manual" else "AUTO",
                            mode_target="MAN" if state == "Manual" else "AUTO", mode_normal="AUTO")
    return result


def alarm_sequence(path):
    return [dict(at=i * 2, path=path, value=value,
                 quality="BAD" if state in ("Bad quality", "Communication loss") else "GOOD", state=state, ramp=False)
            for i, (state, value) in enumerate((("Normal", 50), ("Alarm", 90), ("Acknowledged", 90),
                                               ("Recovered", 50), ("Bad quality", 50), ("Communication loss", 50), ("Normal", 50)))]
