"""Qt-free engineering report model and HTML generator for control modules.

The report is deliberately built from the same ``StrategyGraph`` and live
``StrategyRuntime`` objects the designer uses.  It is not a second module
database: configuration comes from block schemas, live values come from the
terminals, alarms come from their configured limits and ``*_ACT`` state, and
execution order comes from the compiler (or the running compiled strategy).

Keeping this file Qt-free makes report generation usable in qualification
tests, command-line tools, and headless exports.  The dialog/printing adapter
lives in :mod:`dialogs.module_report_dialog`.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
import math
from pathlib import Path
from typing import Any, Iterable

from azeo_control_trainer.core.strategy.engine.compiler import (
    CompileError,
    CompiledStrategy,
    compile_strategy,
)


@dataclass(frozen=True)
class ModuleReportOptions:
    """Sections and detail levels included in a rendered report."""

    identity: bool = True
    hierarchy: bool = True
    configuration: bool = True
    live_values: bool = True
    alarms: bool = True
    execution: bool = True
    validation: bool = True
    include_default_configuration: bool = True
    include_inactive_alarms: bool = True


@dataclass(frozen=True)
class ConfigurationValue:
    block_path: str
    block_type: str
    name: str
    value: Any
    unit: str = ""
    origin: str = "Configured"
    description: str = ""


@dataclass(frozen=True)
class LiveValue:
    block_path: str
    block_type: str
    direction: str
    name: str
    value: Any
    data_type: str
    quality: str
    limit: str
    unit: str = ""
    forced: bool = False


@dataclass(frozen=True)
class BlockHierarchyRow:
    path: str
    parent: str
    depth: int
    block_type: str
    category: str
    description: str
    execution_order: int | None
    scan_rate: int
    status: str
    bypassed: bool
    inputs: int
    outputs: int


@dataclass(frozen=True)
class AlarmRow:
    block_path: str
    block_type: str
    alarm: str
    enabled: bool
    limit: Any
    unit: str
    priority: str
    state: str | None


@dataclass(frozen=True)
class ExecutionStep:
    scope: str
    order: int
    block_path: str
    block_type: str
    scan_rate: int
    last_us: float
    average_us: float
    maximum_us: float
    execution_count: int


@dataclass(frozen=True)
class ExecutionSummary:
    compilable: bool
    compile_message: str
    module_scan_ms: int
    online: bool
    scan_count: int | None
    last_scan_ms: float | None
    forward_wires: int
    feedback_wires: int
    block_type_counts: tuple[tuple[str, int], ...]
    category_counts: tuple[tuple[str, int], ...]
    steps: tuple[ExecutionStep, ...]


@dataclass(frozen=True)
class ValidationMessage:
    severity: str
    scope: str
    text: str


@dataclass(frozen=True)
class ModuleReport:
    """Complete, immutable snapshot used by preview and every export format."""

    module_name: str
    generated_at: datetime
    identity: tuple[tuple[str, str], ...]
    hierarchy: tuple[BlockHierarchyRow, ...]
    configuration: tuple[ConfigurationValue, ...]
    live_available: bool
    live_values: tuple[LiveValue, ...]
    alarms: tuple[AlarmRow, ...]
    execution: ExecutionSummary
    validation: tuple[ValidationMessage, ...]

    @property
    def error_count(self) -> int:
        return sum(row.severity == "ERROR" for row in self.validation)

    @property
    def warning_count(self) -> int:
        return sum(row.severity == "WARNING" for row in self.validation)


@dataclass(frozen=True)
class _AlarmSpec:
    label: str
    limit: str
    enable: tuple[str, ...]
    state: tuple[str, ...]
    priority: str


# Public Azeo alarm names.  Alias resolution below maps these onto blocks
# that use internal spellings (PID lower-case limits, ALARM DEV_HI/HH_EN, etc.).
_ALARM_SPECS = (
    _AlarmSpec("Hi Hi", "HI_HI_LIM", ("HI_HI_ENAB", "HH_EN"),
               ("HI_HI_ACT", "HI_HI"), "P1"),
    _AlarmSpec("Hi", "HI_LIM", ("HI_ENAB", "HI_EN"),
               ("HI_ACT", "HI"), "P2"),
    _AlarmSpec("Deviation Hi", "DV_HI_LIM", ("DV_HI_ENAB", "DEV_EN"),
               ("DV_HI_ACT", "DEV_HI"), "P2"),
    _AlarmSpec("Deviation Lo", "DV_LO_LIM", ("DV_LO_ENAB", "DEV_EN"),
               ("DV_LO_ACT", "DEV_LO"), "P2"),
    _AlarmSpec("Lo", "LO_LIM", ("LO_ENAB", "LO_EN"),
               ("LO_ACT", "LO"), "P2"),
    _AlarmSpec("Lo Lo", "LO_LO_LIM", ("LO_LO_ENAB", "LL_EN"),
               ("LO_LO_ACT", "LO_LO"), "P1"),
    _AlarmSpec("Rate of Change", "ROC_LIM", ("ROC_ENAB", "ROC_EN"),
               ("ROC_ACT", "ROC"), "P3"),
)


def build_module_report(
    graph,
    *,
    module_name: str = "",
    file_path: str | Path | None = None,
    runtime=None,
    project_name: str = "",
    controller_name: str = "",
    dirty: bool = False,
    downloaded_at: float | None = None,
    modified_since_download: bool = False,
    generated_at: datetime | None = None,
) -> ModuleReport:
    """Build one deterministic report snapshot from a graph and runtime.

    ``runtime`` is optional.  When it is absent or offline, the report says
    live data is unavailable rather than presenting terminal defaults as
    process measurements.
    """

    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    name = str(module_name or getattr(graph, "name", "") or "Untitled")
    online = bool(runtime is not None and getattr(runtime, "is_online", False))

    root_messages = _validation_messages(graph, name)
    compiled, compile_message = _compiled_for_report(graph, runtime, root_messages)

    hierarchy: list[BlockHierarchyRow] = []
    configuration: list[ConfigurationValue] = []
    live_values: list[LiveValue] = []
    alarms: list[AlarmRow] = []
    steps: list[ExecutionStep] = []
    messages = list(root_messages)

    for param_name, entry in sorted((graph.module_parameters() or {}).items()):
        info = entry if isinstance(entry, dict) else {"value": entry}
        access = str(info.get("access", "internal_read")).replace("_", " ")
        description = str(info.get("description", "")).strip()
        if description:
            description = f"{description} ({access})"
        else:
            description = access
        configuration.append(ConfigurationValue(
            block_path=name,
            block_type="MODULE",
            name=str(param_name),
            value=info.get("value", ""),
            origin="Module parameter",
            description=description,
        ))

    _walk_graph(
        graph,
        scope=name,
        parent_path="",
        depth=0,
        compiled=compiled,
        live_available=online,
        hierarchy=hierarchy,
        configuration=configuration,
        live_values=live_values,
        alarms=alarms,
        steps=steps,
        messages=messages,
    )

    all_blocks = list(_iter_blocks(graph))
    type_counts = Counter(block.block_type for _path, block in all_blocks)
    category_counts = Counter(
        getattr(getattr(block, "category", None), "value", "Other")
        for _path, block in all_blocks
    )
    forward_count = len(compiled.forward_wires) if compiled else sum(
        not getattr(wire, "is_bkcal", False) for wire in graph.wires.values())
    feedback_count = len(compiled.bkcal_wires) if compiled else sum(
        bool(getattr(wire, "is_bkcal", False)) for wire in graph.wires.values())

    scan_count = int(getattr(runtime, "scan_count", 0)) if online else None
    last_scan_ms = float(getattr(runtime, "last_scan_ms", 0.0)) if online else None
    execution = ExecutionSummary(
        compilable=compiled is not None,
        compile_message=compile_message,
        module_scan_ms=int(getattr(graph, "scan_ms", 500)),
        online=online,
        scan_count=scan_count,
        last_scan_ms=last_scan_ms,
        forward_wires=forward_count,
        feedback_wires=feedback_count,
        block_type_counts=tuple(sorted(type_counts.items())),
        category_counts=tuple(sorted(category_counts.items())),
        steps=tuple(steps),
    )

    path_text = str(Path(file_path).resolve()) if file_path else "(unsaved)"
    identity: list[tuple[str, str]] = [
        ("Module", name),
        ("Graph", str(getattr(graph, "name", ""))),
        ("Description", str(getattr(graph, "description", "") or "")),
        ("File", path_text),
        ("Module scan period", f"{execution.module_scan_ms} ms"),
        ("State", "On scan" if online else "Off scan"),
        ("Blocks", str(len(graph.blocks))),
        ("Connections", str(len(graph.wires))),
        ("Unsaved edits", "Yes" if dirty else "No"),
    ]
    if project_name:
        identity.insert(1, ("Project / area", str(project_name)))
    if controller_name:
        identity.insert(2, ("Controller", str(controller_name)))
    if downloaded_at is not None:
        stamp = datetime.fromtimestamp(float(downloaded_at), timezone.utc)
        identity.append(("Downloaded", stamp.isoformat(timespec="seconds")))
        identity.append(("Modified since download",
                         "Yes" if modified_since_download else "No"))

    # Keep validation deterministic and put the actionable failures first.
    severity_order = {"ERROR": 0, "WARNING": 1, "INFO": 2}
    messages.sort(key=lambda row: (
        severity_order.get(row.severity, 9), row.scope.lower(), row.text.lower()))

    return ModuleReport(
        module_name=name,
        generated_at=generated,
        identity=tuple(identity),
        hierarchy=tuple(hierarchy),
        configuration=tuple(configuration),
        live_available=online,
        live_values=tuple(live_values),
        alarms=tuple(alarms),
        execution=execution,
        validation=tuple(messages),
    )


def _compiled_for_report(graph, runtime, messages):
    current = getattr(runtime, "compiled", None) if runtime is not None else None
    if current is not None and getattr(current, "graph", None) is graph:
        return current, "Using the execution image currently loaded in the runtime."
    if any(row.severity == "ERROR" for row in messages):
        return None, "Compilation was not attempted because validation failed."
    try:
        return compile_strategy(graph), "Compilation successful."
    except CompileError as error:
        messages.append(ValidationMessage("ERROR", str(graph.name), str(error)))
        return None, f"Compilation failed: {error}"


def _walk_graph(
    graph,
    *,
    scope: str,
    parent_path: str,
    depth: int,
    compiled: CompiledStrategy | None,
    live_available: bool,
    hierarchy: list[BlockHierarchyRow],
    configuration: list[ConfigurationValue],
    live_values: list[LiveValue],
    alarms: list[AlarmRow],
    steps: list[ExecutionStep],
    messages: list[ValidationMessage],
) -> None:
    order = {block_id: index for index, block_id in enumerate(
        compiled.exec_order if compiled else ())}
    blocks = sorted(
        graph.blocks.values(),
        key=lambda block: (order.get(block.id, 10**9),
                           block.instance_name.lower(), block.id),
    )
    for block in blocks:
        path = f"{parent_path}/{block.instance_name}" if parent_path \
            else block.instance_name
        category = getattr(getattr(block, "category", None), "value", "Other")
        block_status = getattr(getattr(block, "status", None), "value", "Unknown")
        hierarchy.append(BlockHierarchyRow(
            path=path,
            parent=parent_path,
            depth=depth,
            block_type=str(block.block_type),
            category=str(category),
            description=str(getattr(block, "description", "") or ""),
            execution_order=order.get(block.id),
            scan_rate=int(getattr(block, "scan_rate", 1)),
            status=str(block_status),
            bypassed=bool(getattr(block, "bypassed", False)),
            inputs=len(block.inputs),
            outputs=len(block.outputs),
        ))
        _append_configuration(block, path, configuration, messages)
        if live_available:
            _append_live_values(block, path, live_values)
        _append_alarms(block, path, live_available, alarms)

        if block.id in order:
            steps.append(ExecutionStep(
                scope=scope,
                order=order[block.id],
                block_path=path,
                block_type=str(block.block_type),
                scan_rate=int(getattr(block, "scan_rate", 1)),
                last_us=float(getattr(block, "_last_exec_us", 0.0)),
                average_us=float(getattr(block, "avg_exec_us", 0.0)),
                maximum_us=float(getattr(block, "_max_exec_us", 0.0)),
                execution_count=int(getattr(block, "_exec_count", 0)),
            ))

        inner = getattr(block, "inner_graph", None)
        if inner is None or not getattr(inner, "blocks", None):
            continue
        inner_compiled = getattr(block, "_inner_compiled", None)
        inner_scope = f"{scope}/{block.instance_name}"
        for row in _validation_messages(inner, inner_scope):
            messages.append(row)
        _walk_graph(
            inner,
            scope=inner_scope,
            parent_path=path,
            depth=depth + 1,
            compiled=inner_compiled,
            live_available=live_available,
            hierarchy=hierarchy,
            configuration=configuration,
            live_values=live_values,
            alarms=alarms,
            steps=steps,
            messages=messages,
        )


def _append_configuration(block, path, rows, messages) -> None:
    try:
        schema = dict(block.get_config_schema() or {})
    except Exception as error:  # a broken schema belongs in the report
        messages.append(ValidationMessage(
            "ERROR", path, f"Configuration schema failed: {error}"))
        schema = {}
    explicit = dict(getattr(getattr(block, "config", None), "params", {}) or {})
    for name, spec in schema.items():
        default = spec[1] if len(spec) > 1 else ""
        description = str(spec[2]) if len(spec) > 2 else ""
        rows.append(ConfigurationValue(
            block_path=path,
            block_type=str(block.block_type),
            name=str(name),
            value=explicit.get(name, default),
            unit=str(block.unit_for(name) or ""),
            origin="Configured" if name in explicit else "Default",
            description=description,
        ))

    aliases = dict(getattr(block, "config_aliases", {}) or {})
    metadata = set(getattr(block, "_METADATA_KEYS", ()))
    valid = set(schema) | set(aliases) | set(aliases.values()) | metadata
    for name in sorted(set(explicit) - valid):
        messages.append(ValidationMessage(
            "WARNING", path,
            f"Configured parameter '{name}' is not in the block schema and has no effect."))
        rows.append(ConfigurationValue(
            block_path=path,
            block_type=str(block.block_type),
            name=str(name),
            value=explicit[name],
            origin="Unknown",
            description="Not recognized by this block type",
        ))


def _append_live_values(block, path, rows) -> None:
    for direction, terminals in (("Input", block.inputs), ("Output", block.outputs)):
        for name, terminal in sorted(terminals.items()):
            rows.append(LiveValue(
                block_path=path,
                block_type=str(block.block_type),
                direction=direction,
                name=str(name),
                value=terminal.value,
                data_type=str(getattr(terminal.data_type, "value", terminal.data_type)),
                quality=str(getattr(terminal.status, "name", terminal.status)),
                limit=str(getattr(terminal.limit, "value", terminal.limit)),
                unit=str(getattr(terminal, "units", "") or ""),
                forced=bool(getattr(terminal, "forced", False)),
            ))


def _append_alarms(block, path, live_available, rows) -> None:
    try:
        schema = dict(block.get_config_schema() or {})
    except Exception:
        return
    for spec in _ALARM_SPECS:
        limit_key = _config_key(block, schema, spec.limit)
        if limit_key is None:
            continue
        limit_spec = schema.get(limit_key, ())
        limit = block.config.params.get(
            limit_key, limit_spec[1] if len(limit_spec) > 1 else "")
        enable_key = next((key for public in spec.enable
                           if (key := _config_key(block, schema, public))), None)
        if enable_key:
            enable_spec = schema.get(enable_key, ())
            enabled = bool(block.config.params.get(
                enable_key, enable_spec[1] if len(enable_spec) > 1 else False))
        else:
            enabled = _finite(limit)
        state = None
        if live_available:
            state_value = _alarm_state(block, spec.state)
            state = "ACTIVE" if bool(state_value) else "Normal"
            if not enabled:
                state = "Disabled"
        rows.append(AlarmRow(
            block_path=path,
            block_type=str(block.block_type),
            alarm=spec.label,
            enabled=enabled,
            limit=limit,
            unit=str(block.unit_for(limit_key) or ""),
            priority=spec.priority,
            state=state,
        ))


def _config_key(block, schema: dict, public: str) -> str | None:
    by_upper = {str(name).upper(): str(name) for name in schema}
    direct = by_upper.get(public.upper())
    if direct is not None:
        return direct
    for alias, canonical in (getattr(block, "config_aliases", {}) or {}).items():
        if str(alias).upper() != public.upper():
            continue
        if canonical in schema:
            return str(canonical)
        match = by_upper.get(str(canonical).upper())
        if match is not None:
            return match
    return None


def _alarm_state(block, candidates: Iterable[str]):
    by_upper = {str(name).upper(): terminal
                for name, terminal in block.outputs.items()}
    for public in candidates:
        terminal = by_upper.get(public.upper())
        if terminal is not None:
            return terminal.value
        try:
            canonical = block.resolve_terminal(public, output=True)
        except Exception:
            canonical = public
        terminal = block.outputs.get(canonical)
        if terminal is not None:
            return terminal.value

    # PID keeps alarm state in its tested algorithm object rather than
    # duplicating six discrete output terminals on the FBD surface.
    core = getattr(block, "pid_core_block", None)
    alarm_state = getattr(core, "alarm_state", None)
    if alarm_state is not None:
        for public in candidates:
            attr = public.lower().removesuffix("_act") + "_act"
            if hasattr(alarm_state, attr):
                return getattr(alarm_state, attr)
    return False


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return bool(value)


def _validation_messages(graph, scope: str) -> list[ValidationMessage]:
    rows = [ValidationMessage("ERROR", scope, text)
            for text in graph.validate()]
    rows.extend(ValidationMessage("WARNING", scope, text)
                for text in graph.type_mismatches())
    if not graph.blocks:
        rows.append(ValidationMessage("INFO", scope,
                                      "The module contains no function blocks."))
    return rows


def _iter_blocks(graph, parent: str = ""):
    for block in graph.blocks.values():
        path = f"{parent}/{block.instance_name}" if parent else block.instance_name
        yield path, block
        inner = getattr(block, "inner_graph", None)
        if inner is not None and getattr(inner, "blocks", None):
            yield from _iter_blocks(inner, path)


def render_module_report_html(
    report: ModuleReport,
    options: ModuleReportOptions | None = None,
) -> str:
    """Render a self-contained, printable HTML5 engineering report."""

    options = options or ModuleReportOptions()
    sections: list[str] = []
    if options.identity:
        sections.append(_identity_html(report))
    if options.hierarchy:
        sections.append(_hierarchy_html(report))
    if options.configuration:
        sections.append(_configuration_html(report, options))
    if options.live_values:
        sections.append(_live_html(report))
    if options.alarms:
        sections.append(_alarms_html(report, options))
    if options.execution:
        sections.append(_execution_html(report))
    if options.validation:
        sections.append(_validation_html(report))

    status = "VALID" if report.error_count == 0 else "INVALID"
    run_state = "ONLINE" if report.execution.online else "OFFLINE"
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>{_e(report.module_name)} - Module Report</title>
<style>{_REPORT_CSS}</style></head><body>
<header>
  <div class="brand">AZEO CONTROL DESIGNER</div>
  <h1>Engineering Module Report</h1>
  <div class="module">{_e(report.module_name)}</div>
  <div class="badges"><span class="badge">{run_state}</span>
  <span class="badge {'ok' if status == 'VALID' else 'error'}">{status}</span></div>
</header>
<main>{''.join(sections)}</main>
<footer>Generated {_e(report.generated_at.isoformat(timespec='seconds'))} ·
Configuration and live state are a point-in-time engineering snapshot.</footer>
</body></html>"""


def write_module_report_html(
    report: ModuleReport,
    path: str | Path,
    options: ModuleReportOptions | None = None,
) -> Path:
    """Write an HTML report to an explicit path (safe in headless sessions)."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_module_report_html(report, options), encoding="utf-8")
    return target


def _identity_html(report) -> str:
    rows = "".join(f"<tr><th>{_e(key)}</th><td>{_e(value)}</td></tr>"
                   for key, value in report.identity)
    return _section("Module identity", f"<table class='keyvalue'>{rows}</table>")


def _hierarchy_html(report) -> str:
    body = []
    for row in report.hierarchy:
        order = "-" if row.execution_order is None else str(row.execution_order + 1)
        flags = []
        if row.bypassed:
            flags.append("BYPASSED")
        if row.scan_rate != 1:
            flags.append(f"1:{row.scan_rate} scan")
        body.append("<tr>"
                    f"<td class='tree depth-{min(row.depth, 6)}'>{_e(row.path)}</td>"
                    f"<td>{_e(row.block_type)}</td><td>{_e(row.category)}</td>"
                    f"<td>{order}</td><td>{_e(row.status)}</td>"
                    f"<td>{_e(', '.join(flags) or '-')}</td>"
                    f"<td>{row.inputs} / {row.outputs}</td></tr>")
    table = _table(("Block hierarchy", "Type", "Category", "Order",
                    "Status", "Execution flags", "I/O"), body)
    return _section("Block hierarchy", table)


def _configuration_html(report, options) -> str:
    body = []
    for row in report.configuration:
        if (not options.include_default_configuration
                and row.origin == "Default"):
            continue
        body.append("<tr>"
                    f"<td>{_e(row.block_path)}</td><td>{_e(row.block_type)}</td>"
                    f"<td><code>{_e(row.name)}</code></td>"
                    f"<td class='value'>{_e(_format_value(row.value))}</td>"
                    f"<td>{_e(row.unit)}</td><td>{_e(row.origin)}</td>"
                    f"<td>{_e(row.description)}</td></tr>")
    empty = "<p class='empty'>No configurable values match this selection.</p>"
    table = _table(("Block", "Type", "Parameter", "Value", "Unit",
                    "Origin", "Description"), body) if body else empty
    return _section("Configurable values", table)


def _live_html(report) -> str:
    if not report.live_available:
        return _section("Live values and quality",
                        "<p class='notice'>Live values are unavailable because "
                        "the module is off scan. Configuration defaults are not "
                        "reported as process measurements.</p>")
    body = []
    for row in report.live_values:
        quality_class = " bad" if row.quality.upper() == "BAD" else ""
        body.append("<tr>"
                    f"<td>{_e(row.block_path)}</td><td>{_e(row.direction)}</td>"
                    f"<td><code>{_e(row.name)}</code></td>"
                    f"<td class='value'>{_e(_format_value(row.value))}</td>"
                    f"<td>{_e(row.unit)}</td><td>{_e(row.data_type)}</td>"
                    f"<td class='{quality_class.strip()}'>{_e(row.quality)}</td>"
                    f"<td>{_e(row.limit)}</td>"
                    f"<td>{'YES' if row.forced else ''}</td></tr>")
    return _section("Live values and quality", _table(
        ("Block", "Direction", "Parameter", "Value", "Unit", "Type",
         "Quality", "Limit status", "Forced"), body))


def _alarms_html(report, options) -> str:
    body = []
    for row in report.alarms:
        if (not options.include_inactive_alarms
                and (row.state != "ACTIVE" or not row.enabled)):
            continue
        state = row.state if row.state is not None else "Unavailable"
        cls = " class='alarm'" if state == "ACTIVE" else ""
        body.append("<tr>"
                    f"<td>{_e(row.block_path)}</td><td>{_e(row.block_type)}</td>"
                    f"<td>{_e(row.alarm)}</td>"
                    f"<td>{'Yes' if row.enabled else 'No'}</td>"
                    f"<td class='value'>{_e(_format_value(row.limit))}</td>"
                    f"<td>{_e(row.unit)}</td><td>{_e(row.priority)}</td>"
                    f"<td{cls}>{_e(state)}</td></tr>")
    empty = "<p class='empty'>No alarms match this selection.</p>"
    table = _table(("Block", "Type", "Alarm", "Enabled", "Limit", "Unit",
                    "Priority", "Live state"), body) if body else empty
    return _section("Alarm configuration and state", table)


def _execution_html(report) -> str:
    summary = report.execution
    scan = "Unavailable" if summary.scan_count is None else str(summary.scan_count)
    last = "Unavailable" if summary.last_scan_ms is None \
        else f"{summary.last_scan_ms:.3f} ms"
    counts = ", ".join(f"{kind} x {count}"
                       for kind, count in summary.block_type_counts) or "None"
    categories = ", ".join(f"{kind} x {count}"
                           for kind, count in summary.category_counts) or "None"
    key_rows = (
        ("Compilation", summary.compile_message),
        ("Module scan period", f"{summary.module_scan_ms} ms"),
        ("Runtime scans", scan),
        ("Last scan time", last),
        ("Forward / feedback wires",
         f"{summary.forward_wires} / {summary.feedback_wires}"),
        ("Algorithm block types", counts),
        ("Block categories", categories),
    )
    key_table = "<table class='keyvalue'>" + "".join(
        f"<tr><th>{_e(key)}</th><td>{_e(value)}</td></tr>"
        for key, value in key_rows) + "</table>"
    body = []
    for row in summary.steps:
        body.append("<tr>"
                    f"<td>{_e(row.scope)}</td><td>{row.order + 1}</td>"
                    f"<td>{_e(row.block_path)}</td><td>{_e(row.block_type)}</td>"
                    f"<td>1:{row.scan_rate}</td><td>{row.last_us:.2f}</td>"
                    f"<td>{row.average_us:.2f}</td><td>{row.maximum_us:.2f}</td>"
                    f"<td>{row.execution_count}</td></tr>")
    order_table = _table(("Scope", "Order", "Block", "Type", "Rate",
                          "Last us", "Avg us", "Max us", "Executions"), body)
    return _section("Algorithm and execution summary", key_table + order_table)


def _validation_html(report) -> str:
    if not report.validation:
        return _section("Validation",
                        "<p class='pass'>No validation errors or warnings.</p>")
    body = []
    for row in report.validation:
        body.append("<tr>"
                    f"<td class='severity {row.severity.lower()}'>{_e(row.severity)}</td>"
                    f"<td>{_e(row.scope)}</td><td>{_e(row.text)}</td></tr>")
    return _section("Validation messages", _table(
        ("Severity", "Scope", "Message"), body))


def _section(title: str, body: str) -> str:
    return f"<section><h2>{_e(title)}</h2>{body}</section>"


def _table(headers: Iterable[str], rows: Iterable[str]) -> str:
    head = "".join(f"<th>{_e(header)}</th>" for header in headers)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "+infinity" if value > 0 else "-infinity"
        return f"{value:.8g}"
    if value is None:
        return ""
    return str(value)


def _e(value: Any) -> str:
    return escape(str(value), quote=True)


_REPORT_CSS = """
@page { size: landscape; margin: 12mm; }
* { box-sizing: border-box; }
body { margin: 0; color: #1d2b36; background: #fff;
       font-family: "Segoe UI", Arial, sans-serif; font-size: 9pt; }
header { position: relative; padding: 18px 22px 15px; color: #fff;
         background: #163f63; border-bottom: 5px solid #18a1a8; }
.brand { font-size: 8pt; font-weight: 700; letter-spacing: 1.5px; opacity: .86; }
h1 { margin: 5px 0 2px; font-size: 20pt; font-weight: 600; }
.module { font: 700 12pt Consolas, monospace; }
.badges { position: absolute; right: 22px; bottom: 17px; }
.badge { display: inline-block; margin-left: 6px; padding: 4px 9px;
         border: 1px solid #9eb5c7; border-radius: 3px; background: #315b7b; }
.badge.ok { background: #176b60; } .badge.error { background: #a52d34; }
main { padding: 12px 18px 18px; }
section { margin: 0 0 14px; page-break-inside: auto; }
h2 { margin: 0; padding: 6px 9px; color: #163f63; background: #e6edf2;
     border-left: 4px solid #18a1a8; font-size: 11pt; }
table { width: 100%; margin: 0 0 8px; border-collapse: collapse;
        page-break-inside: auto; }
thead { display: table-header-group; }
tr { page-break-inside: avoid; page-break-after: auto; }
th, td { padding: 4px 6px; border: 1px solid #b7c4ce; vertical-align: top; }
thead th { color: #fff; background: #315b7b; font-weight: 600; text-align: left; }
tbody tr:nth-child(even) { background: #f5f7f9; }
.keyvalue th { width: 22%; color: #29465e; background: #e8eef3; }
.value, code { font-family: Consolas, "Courier New", monospace; }
.tree { font-weight: 600; } .depth-1 { padding-left: 20px; }
.depth-2 { padding-left: 36px; } .depth-3 { padding-left: 52px; }
.depth-4 { padding-left: 68px; } .depth-5 { padding-left: 84px; }
.depth-6 { padding-left: 100px; }
.notice, .empty, .pass { margin: 0; padding: 9px; border: 1px solid #b7c4ce; }
.notice { color: #5b4a18; background: #fff8dc; }
.empty { color: #62717c; background: #f5f7f9; }
.pass { color: #155c51; background: #e8f5f1; }
.alarm, .severity.error { color: #a3141d; font-weight: 700; }
.severity.warning { color: #9a5a00; font-weight: 700; }
.severity.info { color: #315b7b; font-weight: 700; }
.bad { color: #a3141d; font-weight: 700; }
footer { padding: 8px 18px; color: #61717d; border-top: 1px solid #b7c4ce;
         font-size: 8pt; }
@media print { header { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
               h2, thead th { -webkit-print-color-adjust: exact;
                              print-color-adjust: exact; } }
"""


__all__ = [
    "AlarmRow",
    "BlockHierarchyRow",
    "ConfigurationValue",
    "ExecutionStep",
    "ExecutionSummary",
    "LiveValue",
    "ModuleReport",
    "ModuleReportOptions",
    "ValidationMessage",
    "build_module_report",
    "render_module_report_html",
    "write_module_report_html",
]
