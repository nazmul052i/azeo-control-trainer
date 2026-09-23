"""Tag sources the binding engine reads through.

The engine is source-agnostic: it asks a source for the full record at a
`MODULE/BLOCK/TERM` path and never learns where the record came from.
`LiveGraphSource` is the in-process source — live function blocks, the
same objects the runtime scans (the compiler does not copy graphs). A UA
client source slots in later with the same shape; that is the seam that
lets a display built against the trainer bind to a remote PK unchanged.
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, replace

from azeo_control_trainer.core.strategy.model.terminal import DataType, LimitStatus, Quality
from .result import UNRESOLVED, BindingResult

#: Azeo alarm priorities, worst condition first — the same table the
#: PK server's ALARMS summary uses.
_ALARM_CONDITIONS = (("HI_HI", 15, "HI_HI_LIM"), ("LO_LO", 15, "LO_LO_LIM"),
                     ("HI", 11, "HI_LIM"), ("LO", 11, "LO_LIM"))

_SUBSCRIPT = re.compile(r"\[(-?\d+)\]")


def split_parameter_path(path: str) -> tuple[str, tuple[int, ...]]:
    """Normalize ``<zone>%`` and peel integer array subscripts.

    The zone is routing metadata in Azeo.  This in-process source has
    one namespace, so the part before ``%`` is accepted and discarded;
    the container after it remains the module name used for lookup.
    """
    normalized = str(path or "")
    if "%" in normalized:
        _zone, normalized = normalized.split("%", 1)
    indexes = tuple(int(value) for value in _SUBSCRIPT.findall(normalized))
    return _SUBSCRIPT.sub("", normalized), indexes


def indexed_value(value, indexes: tuple[int, ...]):
    """Resolve array members without turning a bad index into zero."""
    for index in indexes:
        try:
            value = value[index]
        except (IndexError, KeyError, TypeError):
            return None, False
    return value, True


@dataclass(frozen=True)
class WriteResult:
    """The outcome of one operator write.

    A refusal is data, not an exception: the control remains on screen
    disabled with the reason the operator can act on. Exceptions are
    reserved for programming faults in the caller.
    """

    success: bool
    error: str = ""


class LiveGraphSource:
    """Reads bindings from live strategy graphs.

    `graphs_provider` returns {module_name: StrategyGraph} on every call,
    so modules going on/off scan are picked up without re-binding. Pass
    `from_store(store)` the trainer's store to follow the runtimes it
    tracks.
    """

    def __init__(self, graphs_provider, alarm_state=None, memory=None):
        self._graphs = graphs_provider
        self.memory = memory
        self._snapshot = None
        if alarm_state is None:
            from .alarm_state import RuntimeAlarmRegistry
            alarm_state = RuntimeAlarmRegistry()
        self.alarm_state = alarm_state

    def _graph_map(self):
        return self._snapshot if self._snapshot is not None else self._graphs()

    @contextmanager
    def snapshot(self):
        """Resolve one refresh against one current module inventory."""
        previous = self._snapshot
        if previous is None:
            self._snapshot = self._graphs()
        try:
            yield
        finally:
            self._snapshot = previous

    @classmethod
    def from_store(cls, store) -> "LiveGraphSource":
        def provider() -> dict:
            graphs = {}
            try:
                runtimes = list(store.get_strategy_runtimes())
            except Exception:                       # noqa: BLE001
                runtimes = []
            for runtime in runtimes:
                graph = getattr(getattr(runtime, "compiled", None),
                                "graph", None)
                if graph is not None:
                    graphs[graph.name] = graph
            return graphs
        return cls(provider, memory=getattr(getattr(store, "tagdb", None), "memory", None))

    # ------------------------------------------------------------- reading
    def poll_alarms(self, block_paths) -> int:
        """Observe each monitored block once, without reading its PV rows.

        Background display rollups need alarm conditions, not a second live
        copy of every value on every hidden display. Visible views retain
        their normal bindings and quality/last-good handling.
        """
        observed = 0
        with self.snapshot():
            graphs = self._graph_map()
            for path in set(block_paths):
                normalized, _indexes = split_parameter_path(path)
                module, separator, name = normalized.partition("/")
                graph = graphs.get(module) if separator else None
                if graph is None:
                    continue
                block = next((one for one in graph.blocks.values()
                              if one.instance_name == name), None)
                if block is not None:
                    self.alarm_state.observe(module, name, self._alarm_conditions(block))
                    observed += 1
        return observed

    def read(self, path: str) -> BindingResult:
        if path.startswith("MEMORY/"):
            try:
                return BindingResult(value=self.memory.read(path), quality=Quality.GOOD)
            except (ValueError, AttributeError, OSError, sqlite3.Error):
                return UNRESOLVED
        normalized, indexes = split_parameter_path(path)
        parts = normalized.split("/")
        if len(parts) == 4 and parts[2] == "CONFIG":
            result = self._read_config(parts[0], parts[1], parts[3])
            if result is UNRESOLVED or not indexes:
                # Copying the sentinel made a missing CONFIG path look resolved
                # to authoring preflight and preview-state checks.
                return result
            value, okay = indexed_value(result.value, indexes)
            return replace(result, value=value) if okay else UNRESOLVED
        if len(parts) == 3 and parts[1].upper() == "PARAMETERS":
            result = self._read_module_parameter(parts[0], parts[2])
            if not indexes:
                return result
            value, okay = indexed_value(result.value, indexes)
            return replace(result, value=value) if okay else UNRESOLVED
        if len(parts) != 3:
            return UNRESOLVED
        module, block_name, term_name = parts
        # Azeo data links may address a field on the parameter. The
        # field is display metadata, not a different terminal, so peel
        # it off before graph lookup. Treating `PV.CV` as a literal
        # terminal name made a Studio-valid path fail only online.
        field = ""
        if "." in term_name:
            candidate, suffix = term_name.rsplit(".", 1)
            if suffix.upper() in ("CV", "F_CV", "ST", "STR",
                                  "TARGET", "ACTUAL"):
                term_name = candidate
                field = suffix.upper()
        if field in ("TARGET", "ACTUAL") and term_name.upper() != "MODE":
            return UNRESOLVED
        graph = self._graph_map().get(module)
        if graph is None:
            return replace(UNRESOLVED, module_running=False)
        block = next((b for b in graph.blocks.values()
                      if b.instance_name == block_name), None)
        if block is None:
            return UNRESOLVED
        if term_name == "CONDITIONS" \
                and hasattr(block, "condition_table"):
            # The §8.6 table, same shape the PK server serves.
            import json
            try:
                return BindingResult(
                    value=json.dumps(block.condition_table()),
                    quality=Quality.GOOD)
            except Exception:                       # noqa: BLE001
                return UNRESOLVED
        terminal = block.outputs.get(term_name) \
            or block.inputs.get(term_name)
        if terminal is None:
            return UNRESOLVED

        conditions = self._alarm_conditions(block)
        self.alarm_state.observe(module, block_name, conditions)
        alarm = self.alarm_state.block_summary(module, block_name)
        target, actual, normal = self._mode_pair(block)
        result = BindingResult(
            value=terminal.value,
            quality=terminal.status,
            limit=terminal.limit,
            forced=bool(terminal.forced),
            units=getattr(terminal, "units", "") or "",
            eu_range=getattr(terminal, "eu_range", None),
            alarm_active=alarm["active"],
            alarm_acked=alarm["acked"],
            alarm_priority=alarm["priority"],
            alarm_condition=alarm["condition"],
            breached_limit=alarm["breached_limit"],
            alarm_count=alarm["count"],
            alarm_suppressed=alarm["suppressed"],
            module_running=True,
            mode_target=target,
            mode_actual=actual,
            mode_normal=normal,
            # last_good_* stay unset here: retention is engine state
            # (§9.2), one owner, or the history restarts per source read.
        )
        if indexes:
            value, okay = indexed_value(result.value, indexes)
            if not okay:
                return UNRESOLVED
            result = replace(result, value=value)
        if field == "STR":
            return replace(result, value=str(result.value))
        if field == "ST":
            return replace(result, value=result.quality.name)
        if field == "TARGET":
            return replace(result, value=target)
        if field == "ACTUAL":
            return replace(result, value=actual)
        # CV and F_CV both expose the terminal's current value in this
        # in-process source. A field transport may distinguish raw and
        # floating representations without changing the binding API.
        return result

    # ------------------------------------------------------------- writing
    def can_write(self, path: str) -> WriteResult:
        """Best-effort Azeo-style write check for one parameter path.

        The graph is the live object the scan executes, so writes go
        through a block's operator methods whenever it has them. Generic
        writes are deliberately limited to declared CONFIG parameters and
        unwired input terminals; an output with no operator API is owned by
        the algorithm and would be overwritten on the next scan even if
        assignment appeared to work.
        """
        if path.startswith("MEMORY/"):
            try:
                self.memory.read(path)
                return WriteResult(True)
            except (ValueError, AttributeError, OSError, sqlite3.Error) as error:
                return WriteResult(False, str(error))
        located, error = self._write_target(path)
        if located is None:
            return WriteResult(False, error)
        block, terminal, name, field = located
        if field == "MODULE_PARAMETER":
            spec = terminal if isinstance(terminal, dict) else {}
            access = str(spec.get("access") or "internal_read").lower()
            if access == "input":
                return WriteResult(True)
            return WriteResult(
                False, f"module parameter {name} is {access} and read-only")
        if field == "CONFIG":
            return WriteResult(True)
        if field in ("ST", "STR", "ACTUAL", "F_CV"):
            return WriteResult(False, f".{field} is read-only")
        upper = name.upper()
        if field == "TARGET" and upper != "MODE":
            return WriteResult(False, ".TARGET is only valid for MODE")
        if upper == "OUT_D" and block.block_type == "DO":
            actual = str(self._mode_pair(block)[1] or "").upper().rsplit(".", 1)[-1]
            return WriteResult(actual == "MAN", "" if actual == "MAN" else
                               f"OUT_D is owned by the block in {actual}")
        if field == "TARGET" or upper == "MODE":
            if callable(getattr(block, "set_mode", None)):
                return WriteResult(True)
            return WriteResult(False, "the block exposes no target-mode handle")
        if upper in ("SP", "OUT", "OP"):
            checker = getattr(block, "accepts_operator_write", None)
            if callable(checker):
                allowed, why = checker(upper)
                return WriteResult(bool(allowed), str(why or ""))
            method = getattr(
                block, "set_sp" if upper == "SP" else "set_manual_output",
                None)
            if callable(method):
                actual = str(self._mode_pair(block)[1] or "").upper()
                actual = actual.rsplit(".", 1)[-1]
                if upper == "SP" and actual in (
                        "OOS", "MAN", "MANUAL", "IMAN", "CAS", "RCAS",
                        "ROUT"):
                    return WriteResult(
                        False, f"SP is not operator-owned in {actual}")
                if upper in ("OUT", "OP") and actual \
                        and actual not in ("MAN", "MANUAL", "IMAN", "ROUT"):
                    return WriteResult(
                        False, f"OUT is owned by the block in {actual}")
                return WriteResult(True)
        if any(candidate is terminal for candidate in block.inputs.values()):
            if terminal.connected:
                return WriteResult(
                    False, f"{name} is wired and owned by upstream control")
            return WriteResult(True)
        return WriteResult(
            False, f"{name} is an algorithm output with no operator handle")

    def write(self, path: str, value) -> WriteResult:
        """Write one operator value, preserving the block's ownership rules."""
        if path.startswith("MEMORY/"):
            try:
                self.memory.write(path, value)
                return WriteResult(True)
            except (ValueError, AttributeError, OSError, sqlite3.Error) as error:
                return WriteResult(False, str(error))
        allowed = self.can_write(path)
        if not allowed.success:
            return allowed
        located, error = self._write_target(path)
        if located is None:
            return WriteResult(False, error)
        block, terminal, name, field = located
        upper = name.upper()
        try:
            if upper == "OUT_D" and block.block_type == "DO":
                # DO exposes the same manual-output API as AO, but its native
                # parameter is OUT_D. Writing IN would be overwritten each scan.
                block.set_manual_output(self._coerce_terminal(DataType.BOOL, value))
                return WriteResult(True)
            if field == "MODULE_PARAMETER":
                parameters = block.module_parameters()
                current = parameters[name]
                spec = current if isinstance(current, dict) else {
                    "value": current,
                    "access": "internal_read",
                    "description": "",
                }
                spec["value"] = self._coerce_module_parameter(spec, value)
                parameters[name] = spec
                return WriteResult(True)
            if field == "CONFIG":
                schema = block.get_config_schema() or {}
                declared = schema.get(name, (type(value),))[0]
                choices = type(block).choices_for(name)
                coerced = self._coerce_config(declared, value, choices)
                missing = object()
                previous = block.config.params.get(name, missing)
                block.config.params[name] = coerced
                try:
                    block._apply_config()
                except Exception:
                    if previous is missing:
                        block.config.params.pop(name, None)
                    else:
                        block.config.params[name] = previous
                    block._apply_config()
                    raise
                return WriteResult(True)
            if field == "TARGET" or upper == "MODE":
                block.set_mode(str(value))
                return WriteResult(True)
            if upper in ("SP", "OUT", "OP"):
                writer = getattr(block, "write_operator_value", None)
                if callable(writer):
                    accepted, why = writer(value, upper)
                    return WriteResult(bool(accepted), str(why or ""))
                method = getattr(
                    block,
                    "set_sp" if upper == "SP" else "set_manual_output")
                method(float(value))
                return WriteResult(True)
            terminal.value = self._coerce_terminal(terminal.data_type, value)
            terminal.status = Quality.GOOD
            return WriteResult(True)
        except (TypeError, ValueError) as exc:
            return WriteResult(False, str(exc) or "value has the wrong type")
        except Exception as exc:                   # noqa: BLE001
            return WriteResult(False, f"write failed: {exc}")

    def _write_target(self, path: str):
        normalized, indexes = split_parameter_path(path)
        if indexes:
            return None, "array members are display-readable but not operator-writable"
        parts = normalized.split("/")
        is_config = len(parts) == 4 and parts[2] == "CONFIG"
        is_module_parameter = (
            len(parts) == 3 and parts[1].upper() == "PARAMETERS"
        )
        if not is_config and len(parts) != 3:
            return None, (
                "write target must be MODULE/BLOCK/PARAMETER or "
                "MODULE/PARAMETERS/NAME"
            )
        if is_config:
            module, block_name, _config, term_name = parts
        else:
            module, block_name, term_name = parts
        field = ""
        if not is_config and not is_module_parameter and "." in term_name:
            term_name, field = term_name.rsplit(".", 1)
            field = field.upper()
            if field not in ("CV", "F_CV", "ST", "STR", "TARGET", "ACTUAL"):
                return None, f"unknown parameter field .{field}"
        graph = self._graph_map().get(module)
        if graph is None:
            return None, f"module {module!r} is not online"
        if is_module_parameter:
            parameters = graph.module_parameters()
            actual_name = next(
                (key for key in parameters
                 if key.casefold() == term_name.casefold()), None)
            if actual_name is None:
                return None, f"module parameter {path!r} does not exist"
            spec = parameters[actual_name]
            return (graph, spec, actual_name, "MODULE_PARAMETER"), ""
        block = next((one for one in graph.blocks.values()
                      if one.instance_name == block_name), None)
        if block is None:
            return None, f"block {module}/{block_name} does not exist"
        if is_config:
            schema = block.get_config_schema() or {}
            schema_name = next(
                (key for key in schema if key.lower() == term_name.lower()),
                None)
            if schema_name is None:
                return None, f"configuration parameter {path!r} does not exist"
            actual_name = next(
                (key for key in block.config.params
                 if key.lower() == term_name.lower()), schema_name)
            return (block, None, actual_name, "CONFIG"), ""
        terminal = block.outputs.get(term_name) or block.inputs.get(term_name)
        # MODE.TARGET often addresses a block property rather than a
        # material terminal. MODE still has to exist for the path to be a
        # real parameter, but fall back to either terminal pool for blocks
        # whose mode is published as an input.
        if terminal is None:
            return None, f"parameter {path!r} does not exist"
        return (block, terminal, term_name, field), ""

    @staticmethod
    def _coerce_terminal(dtype: DataType, value):
        if dtype is DataType.BOOL:
            if isinstance(value, str):
                text = value.strip().lower()
                if text in ("true", "on", "yes", "1"):
                    return True
                if text in ("false", "off", "no", "0"):
                    return False
                raise ValueError("value must be true or false")
            return bool(value)
        if dtype is DataType.INT:
            return int(value)
        if dtype is DataType.FLOAT:
            return float(value)
        return str(value)

    @staticmethod
    def _coerce_config(declared, value, choices=()):
        """Coerce an online detail edit using the block's config schema."""
        if declared is bool:
            if isinstance(value, str):
                text = value.strip().lower()
                if text in ("true", "on", "yes", "1"):
                    return True
                if text in ("false", "off", "no", "0"):
                    return False
                raise ValueError("value must be true or false")
            return bool(value)
        if declared is int:
            return int(value)
        if declared is float:
            return float(value)
        text = str(value)
        if choices:
            canonical = next(
                (choice for choice in choices
                 if str(choice).lower() == text.strip().lower()), None)
            if canonical is None:
                raise ValueError(
                    "value must be one of " + ", ".join(map(str, choices)))
            return canonical
        return text

    @staticmethod
    def _coerce_module_parameter(spec: dict, value):
        """Coerce an HMI write using the module declaration's data type."""
        declared = spec.get("data_type")
        kind = getattr(declared, "name", declared)
        kind = str(kind or "").upper()
        if not kind:
            current = spec.get("value")
            if isinstance(current, bool):
                kind = "BOOL"
            elif isinstance(current, int):
                kind = "INT"
            elif isinstance(current, float):
                kind = "FLOAT"
            else:
                kind = "STRING"
        if kind in ("FLOAT", "REAL", "DOUBLE"):
            return float(value)
        if kind in ("INT", "INTEGER", "DINT", "UINT"):
            return int(value)
        if kind in ("BOOL", "BOOLEAN"):
            if isinstance(value, str):
                text = value.strip().lower()
                if text in ("true", "on", "yes", "1"):
                    return True
                if text in ("false", "off", "no", "0"):
                    return False
                raise ValueError("value must be true or false")
            return bool(value)
        if kind == "ENUM":
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                return value
            raise ValueError("value must be an enum name or integer")
        return str(value)

    def _read_config(self, module: str, block_name: str,
                     param: str) -> BindingResult:
        """`MODULE/BLOCK/CONFIG/PARAM` — the §6 path scheme's config leg.

        Config is Good quality with its declared unit and case-tolerant
        lookup (the panel shows params uppercased). Detail displays may tune
        declared writable values online; project-file synchronization remains
        an engineering workflow rather than an implicit operator-side save."""
        graph = self._graph_map().get(module)
        if graph is None:
            return UNRESOLVED
        block = next((b for b in graph.blocks.values()
                      if b.instance_name == block_name), None)
        if block is None:
            return UNRESOLVED
        params = block.config.params
        value = params.get(param)
        if value is None:
            lowered = param.lower()
            value = next((v for k, v in params.items()
                          if k.lower() == lowered), None)
        if value is None:
            schema = {}
            try:
                schema = block.get_config_schema() or {}
            except Exception:                       # noqa: BLE001
                pass
            spec = schema.get(param) or next(
                (s for k, s in schema.items()
                 if k.lower() == param.lower()), None)
            if spec is None:
                return UNRESOLVED
            value = spec[1] if isinstance(spec, tuple) \
                and len(spec) > 1 else None
        return BindingResult(value=value, quality=Quality.GOOD,
                             units=type(block).unit_for(param))

    def _read_module_parameter(self, module: str, name: str) -> BindingResult:
        """Read ``MODULE/PARAMETERS/NAME`` without confusing NAME for a block.

        Module declarations do not carry a separate live terminal object.
        Their value is still a normal Good-quality binding while the owning
        module is online; the parameter blocks transfer control quality inside
        the scan graph.
        """
        graph = self._graph_map().get(module)
        if graph is None:
            return replace(UNRESOLVED, module_running=False)
        parameters = graph.module_parameters()
        actual_name = next(
            (key for key in parameters if key.casefold() == name.casefold()),
            None,
        )
        if actual_name is None:
            return UNRESOLVED
        raw_spec = parameters[actual_name]
        spec = raw_spec if isinstance(raw_spec, dict) else {"value": raw_spec}
        try:
            quality = Quality[str(spec.get("status", "GOOD")).upper()]
        except KeyError:
            quality = Quality.GOOD
        raw_limit = str(spec.get("limit", "NOT_LIMITED")).upper()
        try:
            limit = LimitStatus[raw_limit]
        except KeyError:
            try:
                limit = LimitStatus(raw_limit)
            except ValueError:
                limit = LimitStatus.NOT_LIMITED
        return BindingResult(
            value=spec.get("value"),
            quality=quality,
            limit=limit,
            units=str(spec.get("unit") or spec.get("units") or ""),
            module_running=True,
        )

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _alarm_conditions(block) -> tuple[tuple[str, int, float | None], ...]:
        rows = []
        for condition, priority, limit_param in _ALARM_CONDITIONS:
            terminal = block.outputs.get(f"{condition}_ACT")
            if terminal is not None and bool(terminal.value):
                try:
                    breached = float(
                        block.config.params.get(limit_param, 0.0))
                except (TypeError, ValueError):
                    breached = None
                rows.append((condition, priority, breached))
        return tuple(rows)

    @staticmethod
    def _mode_pair(block) -> tuple[str, str, str]:
        """(target, actual, normal) for a block, as far as it can say.

        NORMAL comes from the block's `normal_mode` config, and falls
        back to the configured INITIAL mode when that is blank — which
        is the sane default: a block is normally in the mode it was
        configured to start in. Blank on both means no normal-mode
        claim at all, and `mode_mismatch` then answers on target
        alone rather than inventing a normal.
        """
        params = getattr(getattr(block, "config", None), "params", {}) or {}
        normal = str(params.get("normal_mode", "") or "").upper()
        if not normal:
            normal = str(params.get("mode", "") or "").upper()
        target = getattr(block, "mode_target", None)
        actual = getattr(block, "mode_actual", None)
        if isinstance(target, str) and isinstance(actual, str):
            return target, actual, normal
        mode_out = block.outputs.get("MODE")
        actual = str(mode_out.value) if mode_out is not None else ""
        return "", actual, normal


__all__ = ["BindingResult", "LimitStatus", "LiveGraphSource", "Quality",
           "WriteResult"]
