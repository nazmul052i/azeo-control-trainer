"""Measured loop responses, with missing data kept out of the arithmetic."""
from __future__ import annotations

import math


def aligned_response(rows, alignment="start", events=()):
    """Keep pre-event context for plotting and measure only the response after it."""
    rows = [dict(row) for row in rows]
    if not rows:
        raise ValueError("The selection contains no response samples")
    times = [float(row["time"]) for row in rows]
    if any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError("Response samples must advance in time; choose one simulation run")
    anchor = times[0]
    if alignment == "setpoint":
        changes = [row["time"] for previous, row in zip(rows, rows[1:])
                   if previous.get("quality") == row.get("quality") == "GOOD"
                   and previous.get("sp") is not None and row.get("sp") is not None
                   and abs(float(row["sp"]) - float(previous["sp"])) > 1e-9]
        if not changes:
            raise ValueError("No recorded setpoint change in this selection")
        anchor = float(changes[0])
    elif alignment == "fault":
        changes = [float(row["time"]) for row in events if row.get("action") in {"fault_started", "fault_injected"}
                   and times[0] <= float(row["time"]) <= times[-1]]
        if not changes:
            raise ValueError("No recorded fault introduction in this selection")
        anchor = min(changes)
    shifted = [dict(row, time=float(row["time"]) - anchor) for row in rows]
    return {"anchor": anchor, "plot": shifted, "measurement": [row for row in shifted if row["time"] >= 0]}


def history_response_rows(source, loop, start, end):
    paths = [f"{loop}/{suffix}" for suffix in ("PV", "SP", "OUT")]
    values = {}
    runs = set()
    for path in paths:
        point = source.TAGS.get(path)
        if point is None:
            raise ValueError(f"No collected history for {path}")
        values[path] = {float(t): (v, q) for t, v, q in zip(point.times, point.values, point.qualities)
                        if start <= t <= end}
        runs.update(run for t, run in zip(point.times, point.runs) if start <= t <= end and run)
    if len(runs) > 1:
        raise ValueError("This interval crosses a restart or simulation reset; choose one run")
    timeline = sorted({t for samples in values.values() for t in samples})
    rows = []
    for t in timeline:
        points = [values[path].get(t, (None, "BAD")) for path in paths]
        rows.append({"time": t, "pv": points[0][0], "sp": points[1][0], "out": points[2][0],
                     "quality": "GOOD" if all(p[1] == "GOOD" for p in points) else "BAD"})
    return rows


def session_response_rows(data):
    loop = data["exercise"]["loop"]
    rows = []
    for sample in data.get("samples", []):
        points = [sample["values"].get(f"{loop}/{suffix}", {}) for suffix in ("PV", "SP", "OUT")]
        rows.append({"time": sample["time"], "pv": points[0].get("value"),
                     "sp": points[1].get("value"), "out": points[2].get("value"),
                     "quality": "GOOD" if all(p.get("quality") == "GOOD" for p in points) else "BAD"})
    return rows


def response_metrics(samples, *, tolerance=1.0, minimum_hold=5.0):
    """Measure a selected response window in seconds and engineering units.

    Samples contain time, PV, SP, OUT and quality. A changing setpoint is not
    a single step response: report error integral, but no settling/overshoot.
    Missing samples break the integral rather than bridging a sensor outage.
    """
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("Tolerance must be a positive engineering-unit value")
    if not math.isfinite(minimum_hold) or minimum_hold <= 0:
        raise ValueError("Settling hold time must be positive and finite")
    rows = list(samples)
    valid = []
    area = 0.0
    duration = 0.0
    previous = None
    for row in rows:
        try:
            t, pv, sp = (float(row[k]) for k in ("time", "pv", "sp"))
            good = str(row.get("quality", "GOOD")).upper() == "GOOD"
            good = good and all(math.isfinite(v) for v in (t, pv, sp))
        except (KeyError, TypeError, ValueError):
            good = False
        if not good:
            previous = None
            continue
        current = (t, pv, sp)
        if previous is not None:
            dt = t - previous[0]
            if dt <= 0:
                raise ValueError("Response samples must advance in time")
            area += dt * (abs(pv - sp) + abs(previous[1] - previous[2])) / 2
            duration += dt
        previous = current
        valid.append(current)
    result = {"samples": len(valid), "total_samples": len(rows),
              "observed_seconds": duration, "iae": area if duration else None,
              "overshoot": None, "settling_seconds": None,
              "settled": False, "note": "Insufficient good-quality samples"}
    if len(valid) < 2:
        return result
    if len(valid) != len(rows):
        result["note"] = "Data gaps: step-response metrics unavailable"
        return result
    target = valid[0][2]
    if any(abs(row[2] - target) > 1e-9 for row in valid):
        result["note"] = "Setpoint changed inside this window; select one response"
        return result
    direction = 1 if target >= valid[0][1] else -1
    result["overshoot"] = max(0.0, max(direction * (pv - target)
                                         for _, pv, _ in valid))
    last_outside = max((i for i, (_, pv, _) in enumerate(valid)
                        if abs(pv - target) > tolerance), default=-1)
    index = last_outside + 1
    if index < len(valid) and valid[-1][0] - valid[index][0] >= minimum_hold:
        result["settled"] = True
        result["settling_seconds"] = valid[index][0] - valid[0][0]
        result["note"] = f"Within ±{tolerance:g} EU for at least {minimum_hold:g} s"
    else:
        result["note"] = "Not yet settled within the selected window"
    return result
