"""Azeo EIOC Tag I/O monitor blocks.

``TAGAI``, ``TAGAO``, ``TAGDI`` and ``TAGDO`` are deliberately *not*
aliases for the ordinary AI/AO/DI/DO blocks. The Azeo function-block contract
describes them as read-only EIOC blocks which mirror a subset of parameters
from Rockwell PlantPAx control tags.  They perform no control calculation and
never write to the PLC.  ``TAGIO`` is the unassigned authoring placeholder;
assigning a supported monitor definition converts it to one of the four
concrete blocks.

The mapping tables below define the five supported monitor families beginning at
"Tag Analog Input (TAGAI) function block".  A configured ``tag`` is a base
path in :class:`SharedDataStore`; mapped PLC members are appended with
``TAG_SEPARATOR``.  Every source can also be overridden explicitly with a
serializable ``SOURCE_<PLC member>`` string, so transport naming is
configuration rather than code.

Each mirrored parameter carries terminal ``Quality``.  A missing or invalid
source holds the last value and becomes Bad/NotLimited, matching the help's
``BadNot SpecificNotLimited`` rule.  A Bad primary value also raises the only
documented block error, ``Bad PV``.  The three generic diagnostic parameters
are hidden from the FBD surface by default because the reference block figures
show only the mapped PLC parameters; they remain available to the debugger and
tag database.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable

from ..model.block_base import (
    BlockCategory,
    BlockStatus,
    DataType,
    FunctionBlock,
)
from ..model.block_registry import register_block
from ..model.terminal import LimitStatus, Quality


_MISSING = object()
_BAD_PV_MASK = 0x01


def _identity(value: Any) -> Any:
    return value


def _as_bool(value: Any) -> bool:
    """Coerce a PLC Boolean without treating the string ``"0"`` as True."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().upper()
    if text in {"1", "TRUE", "ON", "YES"}:
        return True
    if text in {"0", "FALSE", "OFF", "NO"}:
        return False
    raise ValueError(f"not a PLC Boolean: {value!r}")


def _inverse_bool(value: Any) -> bool:
    # TAGDI DEV_ALMLIM is explicitly the inverse of PlantPAx Inp_Target.
    return not _as_bool(value)


def _as_quality(raw: Any) -> Quality:
    """Translate provider-neutral sample quality to terminal quality."""
    if isinstance(raw, Quality):
        return raw
    name = str(getattr(raw, "name", raw)).strip().upper()
    if "BAD" in name:
        return Quality.BAD
    if "UNCERTAIN" in name or name in {"1", "WARNING"}:
        return Quality.UNCERTAIN
    if "GOOD" in name or name in {"", "0", "OK", "NONE"}:
        return Quality.GOOD
    try:
        level = int(raw)
    except (TypeError, ValueError):
        # An unknown provider status is not evidence that a signal is Good.
        return Quality.BAD
    return (Quality.GOOD if level <= 0 else
            Quality.UNCERTAIN if level == 1 else Quality.BAD)


@dataclass(frozen=True)
class TagParameterMapping:
    """One PLC control-tag member mirrored onto one block output."""

    plc_member: str
    terminal: str
    data_type: DataType
    default: float | bool | int | str
    description: str
    convert: Callable[[Any], Any] = _identity


class _TagMonitorBlock(FunctionBlock):
    """Shared read-only runtime for the four concrete EIOC tag monitors."""

    category = BlockCategory.IO
    mappings: tuple[TagParameterMapping, ...] = ()
    primary_terminal = "OUT"

    config_aliases = {
        "control_tag": "tag",
        "CONTROL_TAG": "tag",
        "separator": "TAG_SEPARATOR",
    }

    def __init__(self, instance_name: str = "") -> None:
        self._bridge_snapshot_ready = False
        super().__init__(instance_name)

    def _define_terminals(self) -> None:
        for mapping in self.mappings:
            self.add_output(
                mapping.terminal,
                mapping.data_type,
                mapping.default,
                mapping.description,
            )
        self.add_output(
            "BLOCK_ERR", DataType.STRING, "",
            "Block error; the tag monitors define Bad PV",
        ).hidden = True
        self.add_output(
            "BAD_ACTIVE", DataType.BOOL, False,
            "A BLOCK_ERR condition selected by BAD_MASK is active",
        ).hidden = True
        self.add_output(
            "ABNORM_ACTIVE", DataType.BOOL, False,
            "A BLOCK_ERR condition not selected by BAD_MASK is active",
        ).hidden = True

    @staticmethod
    def _override_key(plc_member: str) -> str:
        return f"SOURCE_{plc_member.upper()}"

    def get_config_schema(self) -> dict[str, tuple[type, Any, str]]:
        schema: dict[str, tuple[type, Any, str]] = {
            "tag": (
                str, "",
                "Assigned PLC control-tag base path in SharedDataStore",
            ),
            "TAG_SEPARATOR": (
                str, ".",
                "Separator between the configured tag and PLC member",
            ),
            "BAD_MASK": (
                int, _BAD_PV_MASK,
                "Bit mask selecting Bad PV as a user-defined Bad condition",
            ),
            "SHOW_DIAGNOSTIC_PINS": (
                bool, False,
                "Show BLOCK_ERR, BAD_ACTIVE and ABNORM_ACTIVE on the FBD",
            ),
        }
        for mapping in self.mappings:
            schema[self._override_key(mapping.plc_member)] = (
                str, "",
                f"Optional absolute source tag for PLC {mapping.plc_member}",
            )
        return schema

    def _apply_config(self) -> None:
        show = bool(self.config.params.get("SHOW_DIAGNOSTIC_PINS", False))
        for name in ("BLOCK_ERR", "BAD_ACTIVE", "ABNORM_ACTIVE"):
            self.outputs[name].hidden = not show

    def source_tag(self, plc_member: str) -> str:
        """Resolve one mapped PLC member to a configured store key."""
        override = str(self.config.params.get(
            self._override_key(plc_member), ""
        ) or "").strip()
        if override:
            return override
        base = str(self.config.params.get("tag", "") or "").strip()
        if not base:
            return ""
        separator = str(self.config.params.get("TAG_SEPARATOR", "."))
        return f"{base}{separator}{plc_member}" if separator else f"{base}{plc_member}"

    @staticmethod
    def _sample_quality(sample: Any) -> Quality:
        if sample is None:
            return Quality.GOOD
        if bool(getattr(sample, "stale", False)):
            return Quality.BAD
        return _as_quality(getattr(sample, "quality", None))

    def _update_mapping(
        self,
        mapping: TagParameterMapping,
        data: dict[str, Any],
        samples: dict[str, Any],
    ) -> None:
        key = self.source_tag(mapping.plc_member)
        raw = data.get(key, _MISSING) if key else _MISSING
        quality = Quality.BAD
        converted = _MISSING
        if raw is not _MISSING and raw is not None:
            quality = self._sample_quality(samples.get(key))
            try:
                converted = mapping.convert(raw)
                # Identity mappings still need their declared public type.
                if mapping.data_type is DataType.FLOAT:
                    converted = float(converted)
                elif mapping.data_type is DataType.INT:
                    converted = int(converted)
                elif mapping.data_type is DataType.BOOL:
                    converted = _as_bool(converted)
                elif mapping.data_type is DataType.STRING:
                    converted = str(converted)
            except (TypeError, ValueError, OverflowError):
                quality = Quality.BAD
                converted = _MISSING

        # A bad transport status may accompany a useful last value. Preserve
        # that value for diagnosis; only a missing/invalid value holds the
        # terminal's previous value.
        if converted is not _MISSING:
            self.set_output(mapping.terminal, converted)
        self.set_output_status(
            mapping.terminal, quality, LimitStatus.NOT_LIMITED
        )

    def update_from_snapshot(
        self,
        data: dict[str, Any],
        samples: dict[str, Any] | None = None,
    ) -> None:
        """Mirror one coherent PLC snapshot onto all public parameters.

        The method is public so ``DataBridge`` can service the block without
        exposing transport details to it.  ``execute`` calls the same method
        through ``RuntimeContext``; either integration path therefore has
        identical status and conversion behavior.
        """
        sample_map = samples or {}
        for mapping in self.mappings:
            self._update_mapping(mapping, data, sample_map)

        primary = self.outputs[self.primary_terminal]
        bad_pv = primary.status is Quality.BAD
        self.set_output("BLOCK_ERR", "Bad PV" if bad_pv else "")
        try:
            bad_mask = int(self.config.params.get("BAD_MASK", _BAD_PV_MASK))
        except (TypeError, ValueError):
            bad_mask = _BAD_PV_MASK
        selected = bool(bad_mask & _BAD_PV_MASK)
        self.set_output("BAD_ACTIVE", bad_pv and selected)
        self.set_output("ABNORM_ACTIVE", bad_pv and not selected)
        self.status = (
            BlockStatus.BAD if primary.status is Quality.BAD
            else BlockStatus.UNCERTAIN
            if primary.status is Quality.UNCERTAIN
            else BlockStatus.GOOD
        )
        self._update_engineering_metadata()

    def update_from_store(self, store: Any) -> None:
        """Take one provider-neutral store snapshot and mirror it."""
        if store is None:
            self.update_from_snapshot({})
            return
        try:
            data = store.get_all()
        except Exception:  # a failed provider is a Bad signal, not a scan crash
            data = {}
        try:
            getter = getattr(store, "get_samples", None)
            samples = getter() if callable(getter) else {}
        except Exception:
            samples = {}
        self.update_from_snapshot(data, samples)

    def sample_from_bridge(
        self,
        data: dict[str, Any],
        samples: dict[str, Any] | None = None,
    ) -> None:
        """Apply the controller's one coherent pre-scan input snapshot.

        The marker prevents ``execute`` from reading the RuntimeContext store
        a second time later in the same scan.  Without it, downstream wires
        carry the first value while the block face reports a newer one, and N
        TAG blocks perform N redundant full-store copies.
        """
        self.update_from_snapshot(data, samples)
        self._bridge_snapshot_ready = True

    def execute(self, dt: float) -> None:
        del dt  # read-only mirror; module scan time does not alter values
        if self._bridge_snapshot_ready:
            self._bridge_snapshot_ready = False
            return
        context = self.runtime_context
        # DataBridge owns ordinary controller scans and has already applied one
        # coherent snapshot before execution.  Only a standalone runtime with
        # an explicit RuntimeContext reads here; treating a missing context as
        # an empty store would overwrite the bridge sample with Bad every scan.
        if context is not None:
            self.update_from_store(getattr(context, "store", None))

    def reset(self) -> None:
        super().reset()
        self._bridge_snapshot_ready = False

    def _update_engineering_metadata(self) -> None:
        """Hook for analog monitors whose scale is supplied by the PLC."""


@register_block
class TagAnalogInputBlock(_TagMonitorBlock):
    """TAGAI: read-only monitor of a PlantPAx ``P_AIn`` control tag."""

    block_type = "TAGAI"
    display_name = "Tag Analog Input"
    description = "Read-only EIOC monitor for a PLC P_AIn control tag"
    mappings = (
        TagParameterMapping("Val", "OUT", DataType.FLOAT, 0.0,
                            "Final PLC analog input value"),
        TagParameterMapping("SrcQ", "STATUS", DataType.INT, 0,
                            "PLC control-tag source quality code"),
        TagParameterMapping("PSet_HiHiLim", "DEV_HIHILIM", DataType.FLOAT, 0.0,
                            "Configured PLC high-high alarm limit"),
        TagParameterMapping("PSet_HiLim", "DEV_HILIM", DataType.FLOAT, 0.0,
                            "Configured PLC high alarm limit"),
        TagParameterMapping("PSet_LoLim", "DEV_LOLIM", DataType.FLOAT, 0.0,
                            "Configured PLC low alarm limit"),
        TagParameterMapping("PSet_LoLoLim", "DEV_LOLOLIM", DataType.FLOAT, 0.0,
                            "Configured PLC low-low alarm limit"),
        TagParameterMapping("Cfg_PVEUMin", "EUMIN", DataType.FLOAT, 0.0,
                            "Configured PLC engineering scale low"),
        TagParameterMapping("Cfg_PVEUMax", "EUMAX", DataType.FLOAT, 100.0,
                            "Configured PLC engineering scale high"),
        TagParameterMapping("Cfg_EU", "EUSTR", DataType.STRING, "",
                            "Configured PLC engineering-units string"),
    )

    def _update_engineering_metadata(self) -> None:
        units = str(self.get_output("EUSTR"))
        scale = (float(self.get_output("EUMIN")), float(self.get_output("EUMAX")))
        for name in (
            "OUT", "DEV_HIHILIM", "DEV_HILIM", "DEV_LOLIM",
            "DEV_LOLOLIM", "EUMIN", "EUMAX",
        ):
            self.set_terminal_eu(name, units, scale)


@register_block
class TagAnalogOutputBlock(_TagMonitorBlock):
    """TAGAO: read-only monitor of a PlantPAx ``P_AOut`` control tag."""

    block_type = "TAGAO"
    display_name = "Tag Analog Output"
    description = "Read-only EIOC monitor for a PLC P_AOut control tag"
    mappings = (
        TagParameterMapping("Val_CVOut", "OUT", DataType.FLOAT, 0.0,
                            "Final limited PLC analog output value"),
        TagParameterMapping("Out_CV", "OUT_CV", DataType.FLOAT, 0.0,
                            "Raw PLC analog output value"),
        TagParameterMapping("SrcQ", "STATUS", DataType.INT, 0,
                            "PLC control-tag source quality code"),
        TagParameterMapping("Cfg_MaxCV", "SP_HILIM", DataType.FLOAT, 100.0,
                            "Configured PLC high setpoint limit"),
        TagParameterMapping("Cfg_MinCV", "SP_LOLIM", DataType.FLOAT, 0.0,
                            "Configured PLC low setpoint limit"),
        TagParameterMapping("Cfg_CVEUMin", "EUMIN", DataType.FLOAT, 0.0,
                            "Configured PLC engineering scale low"),
        TagParameterMapping("Cfg_CVEUMax", "EUMAX", DataType.FLOAT, 100.0,
                            "Configured PLC engineering scale high"),
        TagParameterMapping("Cfg_EU", "EUSTR", DataType.STRING, "",
                            "Configured PLC engineering-units string"),
    )

    def _update_engineering_metadata(self) -> None:
        units = str(self.get_output("EUSTR"))
        scale = (float(self.get_output("EUMIN")), float(self.get_output("EUMAX")))
        for name in ("OUT", "OUT_CV", "SP_HILIM", "SP_LOLIM", "EUMIN", "EUMAX"):
            self.set_terminal_eu(name, units, scale)


@register_block
class TagDiscreteInputBlock(_TagMonitorBlock):
    """TAGDI: read-only monitor of a PlantPAx ``P_DIn`` control tag."""

    block_type = "TAGDI"
    display_name = "Tag Discrete Input"
    description = "Read-only EIOC monitor for a PLC P_DIn control tag"
    primary_terminal = "OUT_D"
    mappings = (
        TagParameterMapping("Sts", "OUT_D", DataType.BOOL, False,
                            "Final PLC discrete input state", _as_bool),
        TagParameterMapping("SrcQ", "STATUS", DataType.INT, 0,
                            "PLC control-tag source quality code"),
        TagParameterMapping("Inp_Target", "DEV_ALMLIM", DataType.BOOL, False,
                            "Inverse of the PLC expected target", _inverse_bool),
    )


@register_block
class TagDiscreteOutputBlock(_TagMonitorBlock):
    """TAGDO: read-only monitor of a PlantPAx ``P_DOut`` control tag."""

    block_type = "TAGDO"
    display_name = "Tag Discrete Output"
    description = "Read-only EIOC monitor for a PLC P_DOut control tag"
    primary_terminal = "OUT_D"
    mappings = (
        TagParameterMapping("Out", "OUT_D", DataType.BOOL, False,
                            "Final PLC discrete output state", _as_bool),
        TagParameterMapping("SrcQ", "STATUS", DataType.INT, 0,
                            "PLC control-tag source quality code"),
        TagParameterMapping("Val_Cmd", "VAL_CMD", DataType.INT, 0,
                            "Commanded output value reported by the PLC"),
        TagParameterMapping("Val_Fdbk", "VAL_FDBK", DataType.INT, 0,
                            "Feedback value reported by the PLC"),
    )


@register_block
class TagIoBlock(FunctionBlock):
    """TAGIO authoring placeholder prior to control-tag assignment.

    The block has only ``OUT`` before assignment. It is intentionally
    non-executable: a retained placeholder publishes Bad until Control Designer
    converts it to the concrete monitor selected by ``TAG_DEFINITION``.
    """

    block_type = "TAGIO"
    category = BlockCategory.IO
    display_name = "Tag I/O"
    description = "Generic PLC control-tag assignment placeholder"
    config_aliases = {"control_tag": "tag", "CONTROL_TAG": "tag"}
    config_choices = {
        "TAG_DEFINITION": (
            "UNASSIGNED", "TagAI_MONITOR", "TagAO_MONITOR",
            "TagDI_MONITOR", "TagDO_MONITOR",
        ),
    }

    def _define_terminals(self) -> None:
        self.add_output("OUT", description="Unassigned tag output")

    def get_config_schema(self) -> dict[str, tuple[type, Any, str]]:
        return {
            "tag": (str, "", "Assigned PLC control-tag base path"),
            "TAG_DEFINITION": (
                str, "UNASSIGNED",
                "Explorer monitor definition used to select TAGAI/AO/DI/DO",
            ),
        }

    def execute(self, dt: float) -> None:
        del dt
        self.set_output_status("OUT", Quality.BAD, LimitStatus.NOT_LIMITED)
        self.status = BlockStatus.BAD

    def convert_assignment(
        self,
        tag_definition: str | None = None,
        *,
        config: Mapping[str, Any] | None = None,
    ) -> FunctionBlock:
        """Return the concrete monitor selected by an Explorer assignment.

        Identity, placement, scan rate, bypass state and applicable primitive
        configuration survive the replacement, so an authoring command can
        substitute the returned block atomically without changing wires or
        debugger breakpoints.
        """
        source_config = dict(self.config.params if config is None else config)
        definition = str(
            tag_definition
            if tag_definition is not None
            else source_config.get("TAG_DEFINITION", "UNASSIGNED")
        ).strip().upper()
        target_cls = _TAG_DEFINITION_TYPES.get(definition)
        if target_cls is None:
            raise ValueError(
                "TAGIO assignment must be TagAI_MONITOR, TagAO_MONITOR, "
                "TagDI_MONITOR or TagDO_MONITOR"
            )
        converted = target_cls(self.instance_name)
        converted.id = self.id
        converted.x = self.x
        converted.y = self.y
        converted.bypassed = self.bypassed
        converted.scan_rate = self.scan_rate
        for attr in ("_ui_width", "_ui_height"):
            if hasattr(self, attr):
                setattr(converted, attr, getattr(self, attr))
        converted.config.params = {
            key: value for key, value in source_config.items()
            if key != "TAG_DEFINITION"
        }
        converted.normalize_config()
        converted._apply_config()
        return converted


_TAG_DEFINITION_TYPES: dict[str, type[_TagMonitorBlock]] = {
    "TAGAI_MONITOR": TagAnalogInputBlock,
    "P_AIN": TagAnalogInputBlock,
    "TAGAI": TagAnalogInputBlock,
    "TAGAO_MONITOR": TagAnalogOutputBlock,
    "P_AOUT": TagAnalogOutputBlock,
    "TAGAO": TagAnalogOutputBlock,
    "TAGDI_MONITOR": TagDiscreteInputBlock,
    "P_DIN": TagDiscreteInputBlock,
    "TAGDI": TagDiscreteInputBlock,
    "TAGDO_MONITOR": TagDiscreteOutputBlock,
    "P_DOUT": TagDiscreteOutputBlock,
    "TAGDO": TagDiscreteOutputBlock,
}


__all__ = [
    "TagAnalogInputBlock",
    "TagAnalogOutputBlock",
    "TagDiscreteInputBlock",
    "TagDiscreteOutputBlock",
    "TagIoBlock",
    "TagParameterMapping",
]
