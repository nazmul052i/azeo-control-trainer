"""Portable raw history, event and measurement evidence exports."""
from __future__ import annotations

import base64
import csv
from datetime import datetime, timezone
from html import escape
import json
import math
from pathlib import Path
import tempfile


def _utc(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat(timespec="milliseconds")


def _cell(value):
    text = str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@")) else text


def memory_rows(source, paths, start, end):
    rows = []
    for path in paths:
        point = source.TAGS.get(path)
        if point is None:
            continue
        for t, value, quality, wall, sim, run in zip(point.times, point.values, point.qualities,
                                                   point.wall_times, point.sim_times, point.runs):
            if start <= t <= end:
                rows.append((path, t, value, quality, wall if wall is not None else source.origin + t, sim, run))
    return sorted(rows, key=lambda r: (r[1], r[0]))


def export_history(target, *, paths, metadata, origin, start, end, archive=None,
                   rows=(), events=(), png=b"", notes=""):
    """Stream raw disk data; plotting envelopes never become exported measurements."""
    target = Path(target).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    html = target.suffix.lower() == ".html"
    stem = target.stem
    csv_name = f"{stem}.samples.csv" if html else target.name
    stats = {path: {"count": 0, "good": 0, "total": 0.0, "minimum": math.inf, "maximum": -math.inf}
             for path in paths}
    provenance = {path: {} for path in paths}
    units = {path: set() for path in paths}
    if archive:
        events = archive.read((), start, end)["events"]
    else:
        events = [row for row in events if start <= row["time"] <= end]
    with tempfile.TemporaryDirectory(prefix=".history-export-", dir=target.parent) as scratch:
        scratch = Path(scratch)
        with (scratch / csv_name).open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(("recorded_time_utc", "elapsed_seconds", "simulation_seconds", "run_id",
                             "path", "description", "value", "quality", "unit", "point_id",
                             "recorded_path", "configuration_revision", "release_id"))

            def write(samples):
                for sample in samples:
                    path, t, value, quality, wall, sim, run = sample[:7]
                    recorded, identity, context = sample[7:] if len(sample) > 7 else (path, "", "{}")
                    context = json.loads(context or "{}")
                    info = {**metadata.get(path, {}), **context}
                    units[path].add(info.get("unit", ""))
                    if identity:
                        key = json.dumps([recorded, context], sort_keys=True)
                        epoch = provenance[path].setdefault(key, {"recorded_path": recorded, "point_id": identity,
                                                               "configuration": context, "first_sample": t, "last_sample": t})
                        epoch["last_sample"] = t
                    finite = value is not None and math.isfinite(value)
                    writer.writerow((_utc(wall), f"{t:.9f}", sim if sim is not None else "", run,
                                     _cell(path), _cell(info.get("label", path)), value if finite else "",
                                     quality, _cell(info.get("unit", "")), identity, _cell(recorded),
                                     context.get("revision", ""), context.get("release_id", "")))
                    item = stats[path]
                    item["count"] += 1
                    if finite and quality == "GOOD":
                        item["good"] += 1
                        item["total"] += value
                        item["minimum"] = min(item["minimum"], value)
                        item["maximum"] = max(item["maximum"], value)

            if archive and paths:
                with archive.connect() as db:
                    # Query each retained identity across all historical aliases.
                    # Reading by today's address alone joins unrelated equipment.
                    for path in paths:
                        identity = metadata.get(path, {}).get("point_id", "")
                        predicate = "c.point_id=?" if identity else "s.path=? AND c.point_id IS NULL"
                        query = ("SELECT ?,s.time,s.value,s.quality,s.wall_time,s.sim_time,s.run,s.path,c.point_id,c.configuration "
                                 "FROM history_samples s LEFT JOIN history_sample_context c ON c.path=s.path AND c.time=s.time "
                                 "WHERE " + predicate + " AND s.time BETWEEN ? AND ? ORDER BY s.time")
                        write(db.execute(query, (path, identity or metadata.get(path, {}).get("legacy_path") or path, start, end)))
            else:
                write(rows)
        for path, item in stats.items():
            item["average"] = item.pop("total") / item["good"] if item["good"] else None
            for key in ("minimum", "maximum"):
                if not math.isfinite(item[key]):
                    item[key] = None
            if len(units[path]) > 1:
                item.update(average=None, minimum=None, maximum=None,
                            note="Units changed in this interval; aggregate measurements are unavailable")
        event_name = f"{stem}.events.csv"
        with (scratch / event_name).open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(("recorded_time_utc", "simulation_seconds", "category", "event", "target", "evidence", "run_id"))
            for row in events:
                writer.writerow((_utc(row["wall_time"]), row.get("sim_time", ""), _cell(row["category"]),
                                 _cell(row["action"]), _cell(row.get("target", "")),
                                 _cell(json.dumps(row.get("detail", ""), default=str)), row.get("run", "")))
        manifest = {"origin_utc": _utc(origin), "from_utc": _utc(origin + start), "to_utc": _utc(origin + end),
                    "points": metadata, "statistics": stats, "notes": notes,
                    "configuration_periods": {path: list(epochs.values()) for path, epochs in provenance.items()},
                    "timing": "Collection and observed event timestamps; not a controller sequence-of-events recorder",
                    "samples": csv_name, "events": event_name}
        (scratch / f"{stem}.metadata.json").write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding="utf-8")
        if html:
            image = f'<img alt="Selected trend interval" src="data:image/png;base64,{base64.b64encode(png).decode()}">' if png else ""
            lines = ["<!doctype html><html><meta charset='utf-8'><title>Process History report</title>",
                     "<style>body{font:15px Segoe UI,sans-serif;color:#2b323b;margin:40px;max-width:1400px}"
                     "h1,h2{color:#004487}img{width:100%;height:auto}table{border-collapse:collapse;width:100%}"
                     "td,th{padding:8px;border-bottom:1px solid #d8dadc;text-align:left}th{background:#e3eef7}"
                     "p{white-space:pre-wrap}</style><h1>Process History report</h1>",
                     f"<p>{escape(manifest['from_utc'])} through {escape(manifest['to_utc'])}</p>", image,
                     f"<p>{escape(notes)}</p><h2>Raw sample statistics</h2><table>"
                     "<tr><th>Point</th><th>Unit</th><th>Good / total</th><th>Minimum</th><th>Maximum</th><th>Average</th></tr>"]
            for path, stat in stats.items():
                cells = (path, metadata[path].get("unit", ""), f"{stat['good']} / {stat['count']}",
                         stat["minimum"], stat["maximum"], stat["average"])
                lines.append("<tr>" + "".join(f"<td>{escape(str(value) if value is not None else 'Unavailable')}</td>" for value in cells) + "</tr>")
            lines.append("</table><h2>Observed events</h2><table><tr><th>UTC time</th><th>Event</th><th>Equipment</th><th>Evidence</th></tr>")
            for row in events:
                cells = (_utc(row["wall_time"]), row["action"], row.get("target", ""), row.get("detail", ""))
                lines.append("<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in cells) + "</tr>")
            lines.append(f"</table><p>{escape(manifest['timing'])}</p></html>")
            (scratch / target.name).write_text("\n".join(lines), encoding="utf-8")
        for path in scratch.iterdir():
            path.replace(target.parent / path.name)
    return target
