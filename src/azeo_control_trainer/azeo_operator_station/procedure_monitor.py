"""Read models for authored PA surfaces, using the station's existing historian."""
from contextlib import closing
import ast
import math
from numbers import Real

from azeo_control_trainer.core.procedures.parameters import parameters


def parameter_rows(session, ref, shared_values):
    selected = ref == session.ref
    pending = session.pending(ref)
    rows = []
    for spec in parameters(session.prepare(ref).procedure).values():
        value = (shared_values.get(spec.path) if spec.path else
                 session.memory_values.get(spec.id.split("/", 1)[1], spec.value)
                 if selected and spec.id.startswith("memory/") else
                 session.parameter_values.get(spec.id, spec.value) if selected else spec.value)
        queued = pending.get(spec.id)
        rows.append(dict(id=spec.id, parameter=spec.label, value=value, unit=spec.unit,
                         minimum=spec.minimum, maximum=spec.maximum, type=spec.data_type,
                         limits=f"{spec.minimum if spec.minimum is not None else '—'} … {spec.maximum if spec.maximum is not None else '—'}",
                         access="Live / next run" if spec.access == "live" else "Next run",
                         pending=queued["value"] if queued else "—", description=spec.description))
    return tuple(rows)


def history_rows(session, ref):
    if ref not in session._history_cache:
        procedure = session.prepare(ref).procedure
        with closing(session.run.store._connect()) as con:
            records = con.execute("SELECT run_id,started_at,status,name FROM procedure_runs WHERE procedure_id=? ORDER BY started_at DESC LIMIT 100",
                                  (procedure.procedure_id,)).fetchall()
        session._history_cache[ref] = tuple(dict(run_id=r[0], time=r[1], state=r[2], name=r[3]) for r in records)
    return session._history_cache[ref]


def trend_paths(session, ref):
    definition = session.prepare(ref)
    historian = session.station.historian
    from azeo_control_trainer.core.configuration.identities import context_for_path
    paths = []
    for tag, path in definition.bindings.items():
        result = session.station.live_source.read(path)
        if not isinstance(result.value, Real):
            continue
        context = context_for_path(session.station.graphs_provider().values(), path)
        span = result.eu_range or (0, 100)
        historian.add_point(path, label=tag, unit=result.units or "", lo=span[0], hi=span[1],
                            module=path.partition("/")[0], **context)
        paths.append(path)
    for spec in definition.procedure.variables:
        if spec.data_type not in {"float", "int", "bool"}:
            continue
        path = spec.tag_path or f"@procedure/{ref}/MEMORY:{spec.name}"
        historian.add_point(path, label=spec.name, unit=spec.engineering_units,
                            lo=spec.min_value if spec.min_value is not None else 0,
                            hi=spec.max_value if spec.max_value is not None else 1 if spec.data_type == "bool" else 100,
                            module="Procedure memory")
        paths.append(path)
    return tuple(dict.fromkeys(paths))


def trend_snapshot(session, ref):
    """Bounded min/max-preserving history; no second sample buffer in the PVM."""
    paths = trend_paths(session, ref)
    historian = session.station.historian
    end = historian.now() / 60
    start = max(0, end - 10)
    series = []
    criteria = current_criteria(session, ref)
    for path in paths[:10]:
        point = historian.TAGS[path]
        times, values, qualities = historian.get_plot_series(path, start, end, budget=240)
        points = [(float(t), float(v) if math.isfinite(v) and q == "GOOD" else None)
                  for t, v, q in zip(times, values, qualities)]
        quality = str(qualities[-1]) if len(qualities) else "No samples"
        latest = f"{values[-1]:g}" if len(values) and math.isfinite(values[-1]) and quality == "GOOD" else "—"
        series.append(dict(path=path, label=point.label, unit=point.unit, lo=point.lo, hi=point.hi,
                           points=points, quality=quality,
                           thresholds=criteria.get(path, ()),
                           legend=f"{point.label}\n{latest} {point.unit} · {quality}\n{point.lo:g} … {point.hi:g}"))
    events = [dict(time=e["time"] / 60, label=e["action"])
              for e in list(historian.events)[-200:]
              if start <= e["time"] / 60 <= end and
              (e["target"] == session.run.run_id or e["target"] == ref)]
    return dict(start=start, end=max(start + .01, end), series=series, events=events,
                axis_label=f"{start:.1f} → {end:.1f} wall-clock minutes from station start · dashed lines: procedure events",
                message=f"Last 10 minutes · {min(10, len(paths))} of {len(paths)} pens · individual engineering scales")


def current_criteria(session, ref):
    """Only simple, known numeric comparisons become labelled current guides."""
    from azeo_control_trainer.core.procedures.logic import prepared_expression
    definition = session.prepare(ref)
    active = next((step for step in definition.procedure.steps if ref == session.ref and session.step_states.get(step.id) == "ACTIVE"), None)
    if active is None or active.type != "wait_until":
        return {}
    shared = session.run.memory.read_many()
    memory = {spec.name: shared.get(spec.tag_path) if spec.tag_path else session.memory_values.get(spec.name)
              for spec in definition.procedure.variables}
    result = {}
    expressions = [row.expression for row in active.condition_rows] or [active.condition]
    for expression in expressions:
        normalized, refs, _ = prepared_expression(expression)
        refs = dict(refs)
        for node in ast.walk(ast.parse(normalized, mode="eval")):
            if not isinstance(node, ast.Compare) or len(node.ops) != 1 or not isinstance(node.left, ast.Name) or node.left.id not in refs:
                continue
            right = node.comparators[0]
            value = right.value if isinstance(right, ast.Constant) else memory.get(right.id) if isinstance(right, ast.Name) else None
            op = {ast.Lt: "<", ast.LtE: "≤", ast.Gt: ">", ast.GtE: "≥", ast.Eq: "="}.get(type(node.ops[0]))
            if op and type(value) in {float, int} and math.isfinite(value):
                path = definition.bindings[refs[node.left.id]]
                result.setdefault(path, []).append(dict(value=value, label=f"Current criterion {op} {value:g}"))
    return result
