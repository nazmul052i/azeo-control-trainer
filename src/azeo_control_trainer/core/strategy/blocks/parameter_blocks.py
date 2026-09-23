"""Azeo module-parameter Special Items.

These four objects are diagram connection points, not algorithms.  The
module's parameter table remains authoritative; each item owns (or links to)
one record in that table and exposes exactly one typed wire endpoint.

``INPORT``/``OUTPORT`` are intentionally separate: those are the repository's
legacy composite-boundary blocks, while these are the user-visible Azeo
Input/Output/Internal Read/Internal Write Parameter items.
"""
from __future__ import annotations

from typing import Any

from ..model.block_base import BlockCategory, BlockStatus, FunctionBlock
from ..model.block_registry import register_block
from ..model.terminal import DataType, LimitStatus, Quality


_DATA_TYPES = tuple(member.value for member in DataType)
_WRITE_ACCESS = (
    "not_writeable",
    "writeable",
    "not_operator_writeable_when_locked",
)


def _data_type(value: Any) -> DataType:
    try:
        return DataType(str(value or "FLOAT").upper())
    except ValueError:
        return DataType.FLOAT


def _default(dtype: DataType):
    if dtype is DataType.BOOL:
        return False
    if dtype in (DataType.INT, DataType.ENUM):
        return 0
    if dtype is DataType.STRING:
        return ""
    return 0.0


def _coerce(value: Any, dtype: DataType):
    """Convert a configured JSON value into the terminal's declared type."""
    try:
        if dtype is DataType.BOOL:
            if isinstance(value, str):
                text = value.strip().casefold()
                if text in {"true", "yes", "on", "1"}:
                    return True
                if text in {"false", "no", "off", "0", ""}:
                    return False
                raise ValueError
            return bool(value)
        if dtype in (DataType.INT, DataType.ENUM):
            return int(float(value))
        if dtype is DataType.STRING:
            return str(value)
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return _default(dtype)


def _quality(value: Any) -> Quality:
    if isinstance(value, Quality):
        return value
    try:
        return Quality[str(value).upper()]
    except (KeyError, TypeError):
        return Quality.GOOD


def _limit(value: Any) -> LimitStatus:
    if isinstance(value, LimitStatus):
        return value
    text = str(value or "NOT_LIMITED").upper()
    try:
        return LimitStatus[text]
    except KeyError:
        try:
            return LimitStatus(text)
        except ValueError:
            return LimitStatus.NOT_LIMITED


def _case_key(parameters: dict, name: str) -> str | None:
    folded = name.casefold()
    return next((key for key in parameters if key.casefold() == folded), None)


class _ParameterItem(FunctionBlock):
    """Shared graph ownership and typed-value behavior for parameter items."""

    category = BlockCategory.IO
    is_special_palette_item = True
    counts_as_algorithm = False
    parameter_access = "internal_read"
    parameter_source = True
    _name_prefix = "PARAM"

    config_choices = {
        "data_type": _DATA_TYPES,
        "write_access": _WRITE_ACCESS,
    }

    def _define_terminals(self):
        if self.parameter_source:
            self.add_output("VALUE", DataType.FLOAT, 0.0,
                            "Module parameter value")
        else:
            self.add_input("VALUE", DataType.FLOAT, 0.0,
                           "Module parameter value")

    @property
    def parameter_name(self) -> str:
        return str(self.config.params.get("parameter_name") or "").strip()

    def get_config_schema(self) -> dict:
        return {
            "parameter_name": (
                str, "", "Name in this module's parameter table"),
            "data_type": (
                str, "FLOAT", "FLOAT, BOOL, INT, STRING, or ENUM"),
            "default_value": (
                str, "0.0", "Configured value used before a live value exists"),
            "units": (str, "", "Engineering units"),
            "description": (str, "", "Parameter description"),
            "write_access": (
                str, self._default_write_access(),
                "Write permission (independent of diagram direction)"),
        }

    def _default_write_access(self) -> str:
        # Legacy internal-write parameters were writable by definition.
        # Keeping that default avoids changing existing ACT/SFC behavior;
        # engineers can now choose permission independently.
        return "writeable" if self.parameter_access == "internal_write" \
            else "not_writeable"

    def _apply_config(self):
        # A parameter boundary must participate every module scan.  Persisted
        # legacy bypass/scan-rate flags are normalized away rather than
        # allowing a connector to present stale data.
        self._bypassed = False
        self._scan_rate = 1
        dtype = _data_type(self.config.params.get("data_type", "FLOAT"))
        terminal = (self.outputs if self.parameter_source else self.inputs)[
            "VALUE"
        ]
        configured = _coerce(
            self.config.params.get("default_value", _default(dtype)), dtype)
        terminal.data_type = dtype
        terminal.default_value = configured
        if not terminal.connected:
            terminal.value = configured
        terminal.units = str(self.config.params.get("units", "") or "")

        if self.parameter_name:
            self.instance_name = self.parameter_name
        graph = getattr(self, "_owner_graph", None)
        if graph is not None:
            self._reconcile_declaration(graph, create=True)

    def on_added_to_graph(self, graph, *, declare: bool = True) -> None:
        """Attach to the parameter record; create it only for authoring adds."""
        self._owner_graph = graph
        if declare and not self.parameter_name:
            parameters = graph.module_parameters()
            index = 1
            while f"{self._name_prefix}{index}".casefold() in {
                    name.casefold() for name in parameters}:
                index += 1
            name = f"{self._name_prefix}{index}"
            self.config.params["parameter_name"] = name
            self.instance_name = name
        elif declare:
            parameters = graph.module_parameters()
            key = _case_key(parameters, self.parameter_name)
            owner = parameters.get(key, {}).get("item_id") \
                if key is not None and isinstance(parameters.get(key), dict) \
                else None
            if owner not in (None, self.id):
                # Copy/paste creates a new parameter, not a second icon that
                # secretly writes the original record. Keep the engineer's
                # base name recognizable while making the namespace legal.
                base = self.parameter_name
                index = 2
                candidate = f"{base}_{index}"
                while _case_key(parameters, candidate) is not None:
                    index += 1
                    candidate = f"{base}_{index}"
                self.config.params["parameter_name"] = candidate
                self.instance_name = candidate
        self._reconcile_declaration(graph, create=declare)

    def on_removed_from_graph(self, graph) -> None:
        """Remove only the declaration this diagram item itself owns."""
        parameters = graph.module_parameters()
        owned = next((name for name, spec in parameters.items()
                      if isinstance(spec, dict)
                      and spec.get("item_id") == self.id), None)
        if owned is not None:
            graph.remove_module_parameter(owned)
        self._owner_graph = None

    def _reconcile_declaration(self, graph, *, create: bool) -> None:
        self._parameter_error = ""
        name = self.parameter_name
        if not name:
            self._parameter_error = "Parameter name is required"
            return
        if (not name.replace("_", "").isalnum() or name[0].isdigit()):
            self._parameter_error = f"{name!r} is not a legal parameter name"
            return
        parameters = graph.module_parameters()
        owned_key = next((key for key, value in parameters.items()
                          if isinstance(value, dict)
                          and value.get("item_id") == self.id), None)
        key = _case_key(parameters, name)

        # A properties edit can rename an owned item.  Move its declaration
        # only after proving the destination is free; otherwise retain the
        # old, valid record and let validation explain the refused collision.
        if owned_key is not None and key != owned_key:
            if key is not None:
                self._parameter_error = (
                    f"Module parameter '{name}' already exists")
                return
            parameters[name] = parameters.pop(owned_key)
            key = name

        if key is not None:
            current = parameters.get(key)
            if not isinstance(current, dict):
                self._parameter_error = f"Module parameter '{name}' is malformed"
                return
            owner = current.get("item_id")
            if owner not in (None, self.id):
                self._parameter_error = (
                    f"Module parameter '{name}' is already placed on the diagram")
                return
            if str(current.get("access", "internal_read")) \
                    != self.parameter_access:
                self._parameter_error = (
                    f"Module parameter '{name}' is {current.get('access')}, not "
                    f"{self.parameter_access}")
                return
        elif not create:
            self._parameter_error = f"Module parameter '{name}' is not declared"
            return

        try:
            graph.set_module_parameter(
                name,
                _coerce(self.config.params.get("default_value", "0.0"),
                        _data_type(self.config.params.get("data_type"))),
                access=self.parameter_access,
                description=str(self.config.params.get("description", "") or ""),
                data_type=_data_type(
                    self.config.params.get("data_type")).value,
                units=str(self.config.params.get("units", "") or ""),
                write_access=str(self.config.params.get(
                    "write_access", self._default_write_access())),
            ) if create else None
        except ValueError as error:
            self._parameter_error = str(error)
            return

        parameters = graph.module_parameters()
        key = _case_key(parameters, name)
        if key is None:
            self._parameter_error = f"Module parameter '{name}' is not declared"
            return
        spec = parameters[key]
        if not isinstance(spec, dict):
            self._parameter_error = f"Module parameter '{name}' is malformed"
            return
        if create:
            spec["item_id"] = self.id
            spec["data_type"] = _data_type(
                self.config.params.get("data_type")).value
            spec["description"] = str(
                self.config.params.get("description", "") or "")
            units = str(self.config.params.get("units", "") or "")
            if units:
                spec["units"] = units
            else:
                spec.pop("units", None)
            write_access = str(self.config.params.get(
                "write_access", self._default_write_access()))
            if write_access in _WRITE_ACCESS:
                spec["write_access"] = write_access

    def execute(self, dt: float):
        graph = getattr(self, "_module_graph", None)
        if graph is None:
            self._mark_bad("No owning module graph")
            return
        key = _case_key(graph.module_parameters(), self.parameter_name)
        if key is None:
            self._mark_bad(f"Parameter '{self.parameter_name}' is missing")
            return
        spec = graph.module_parameters().get(key)
        if not isinstance(spec, dict) \
                or spec.get("access", "internal_read") != self.parameter_access:
            self._mark_bad(f"Parameter '{self.parameter_name}' has wrong access")
            return

        terminal = (self.outputs if self.parameter_source else self.inputs)[
            "VALUE"
        ]
        dtype = terminal.data_type
        if self.parameter_source:
            terminal.value = _coerce(spec.get("value"), dtype)
            terminal.status = _quality(spec.get("status"))
            terminal.limit = _limit(spec.get("limit"))
        elif terminal.connected:
            # An unconnected sink holds its declared value.  Silently writing
            # a terminal's default zero would make an unwired output look real.
            spec["value"] = _coerce(terminal.value, dtype)
            spec["status"] = terminal.status.name
            spec["limit"] = terminal.limit.value
        self.status = BlockStatus.GOOD

    def _mark_bad(self, reason: str) -> None:
        self._parameter_runtime_error = reason
        self.status = BlockStatus.BAD
        if self.parameter_source:
            self.outputs["VALUE"].status = Quality.BAD


@register_block
class InputParameterBlock(_ParameterItem):
    block_type = "INPUT_PARAMETER"
    display_name = "Input Parameter"
    description = "Public module input parameter"
    parameter_access = "input"
    parameter_source = True
    _name_prefix = "INPUT"


@register_block
class OutputParameterBlock(_ParameterItem):
    block_type = "OUTPUT_PARAMETER"
    display_name = "Output Parameter"
    description = "Public module output parameter"
    parameter_access = "output"
    parameter_source = False
    _name_prefix = "OUTPUT"


@register_block
class InternalReadParameterBlock(_ParameterItem):
    block_type = "INTERNAL_READ_PARAMETER"
    display_name = "Internal Read Parameter"
    description = "Internal typed source; use a fixed value as a constant"
    parameter_access = "internal_read"
    parameter_source = True
    _name_prefix = "READ"


@register_block
class InternalWriteParameterBlock(_ParameterItem):
    block_type = "INTERNAL_WRITE_PARAMETER"
    display_name = "Internal Write Parameter"
    description = "Internal typed sink parameter"
    parameter_access = "internal_write"
    parameter_source = False
    _name_prefix = "WRITE"
