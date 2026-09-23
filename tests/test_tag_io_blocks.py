from __future__ import annotations

import json

import pytest

from azeo_control_trainer.core.datastore.shared_data_store import SharedDataStore
from azeo_control_trainer.core.strategy.blocks.dv_tag_io_blocks import (
    TagAnalogInputBlock,
    TagAnalogOutputBlock,
    TagDiscreteInputBlock,
    TagDiscreteOutputBlock,
    TagIoBlock,
)
from azeo_control_trainer.core.strategy.engine.bridge import DataBridge
from azeo_control_trainer.core.strategy.engine.compiler import compile_strategy
from azeo_control_trainer.core.strategy.engine.runtime import StrategyRuntime
from azeo_control_trainer.core.strategy.engine.runtime_context import RuntimeContext
from azeo_control_trainer.core.strategy.engine.validator import validate_strategy
from azeo_control_trainer.core.strategy.model.block_base import (
    BlockCategory,
    BlockStatus,
    DataType,
    FunctionBlock,
)
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
from azeo_control_trainer.core.strategy.model.terminal import LimitStatus, Quality
from azeo_control_trainer.core.strategy.tagdb import EntryKind, TagDatabase


def _configured(block, tag: str = "PLC.TAG"):
    block.config.params["tag"] = tag
    block._apply_config()
    return block


def _runtime(block, store: SharedDataStore):
    graph = StrategyGraph("TAG-MONITOR")
    graph.add_block(block)
    runtime = StrategyRuntime()
    runtime.set_context(RuntimeContext(store=store, plugin_id="test"))
    runtime.load(compile_strategy(graph), DataBridge(store))
    assert runtime.go_online()
    return runtime


class _CaptureBlock(FunctionBlock):
    block_type = "_TAG_CAPTURE"
    category = BlockCategory.SIGNAL

    def _define_terminals(self):
        self.add_input("IN")
        self.add_output("OUT")

    def execute(self, dt: float):
        del dt
        self.set_output("OUT", self.get_input("IN"))
        self.propagate_status(inputs=("IN",), outputs=("OUT",))


class _CountingStore(SharedDataStore):
    def __init__(self):
        super().__init__()
        self.get_all_calls = 0
        self.get_samples_calls = 0

    def get_all(self):
        self.get_all_calls += 1
        return super().get_all()

    def get_samples(self):
        self.get_samples_calls += 1
        return super().get_samples()


def test_terminal_surfaces_match_block_contract():
    expected = {
        TagAnalogInputBlock: (
            ("OUT", DataType.FLOAT),
            ("STATUS", DataType.INT),
            ("DEV_HIHILIM", DataType.FLOAT),
            ("DEV_HILIM", DataType.FLOAT),
            ("DEV_LOLIM", DataType.FLOAT),
            ("DEV_LOLOLIM", DataType.FLOAT),
            ("EUMIN", DataType.FLOAT),
            ("EUMAX", DataType.FLOAT),
            ("EUSTR", DataType.STRING),
        ),
        TagAnalogOutputBlock: (
            ("OUT", DataType.FLOAT),
            ("OUT_CV", DataType.FLOAT),
            ("STATUS", DataType.INT),
            ("SP_HILIM", DataType.FLOAT),
            ("SP_LOLIM", DataType.FLOAT),
            ("EUMIN", DataType.FLOAT),
            ("EUMAX", DataType.FLOAT),
            ("EUSTR", DataType.STRING),
        ),
        TagDiscreteInputBlock: (
            ("OUT_D", DataType.BOOL),
            ("STATUS", DataType.INT),
            ("DEV_ALMLIM", DataType.BOOL),
        ),
        TagDiscreteOutputBlock: (
            ("OUT_D", DataType.BOOL),
            ("STATUS", DataType.INT),
            ("VAL_CMD", DataType.INT),
            ("VAL_FDBK", DataType.INT),
        ),
    }
    diagnostics = {"BLOCK_ERR", "BAD_ACTIVE", "ABNORM_ACTIVE"}
    for block_cls, public in expected.items():
        block = block_cls("MON")
        assert block.inputs == {}
        assert tuple(
            (name, terminal.data_type)
            for name, terminal in block.outputs.items()
            if name not in diagnostics
        ) == public
        assert all(block.outputs[name].hidden for name in diagnostics)

    placeholder = TagIoBlock("UNASSIGNED")
    assert placeholder.inputs == {}
    assert tuple(placeholder.outputs) == ("OUT",)


def test_plc_member_mapping_matches_tag_monitor_contract():
    assert tuple(
        (mapping.plc_member, mapping.terminal)
        for mapping in TagAnalogInputBlock.mappings
    ) == (
        ("Val", "OUT"),
        ("SrcQ", "STATUS"),
        ("PSet_HiHiLim", "DEV_HIHILIM"),
        ("PSet_HiLim", "DEV_HILIM"),
        ("PSet_LoLim", "DEV_LOLIM"),
        ("PSet_LoLoLim", "DEV_LOLOLIM"),
        ("Cfg_PVEUMin", "EUMIN"),
        ("Cfg_PVEUMax", "EUMAX"),
        ("Cfg_EU", "EUSTR"),
    )
    assert tuple(
        (mapping.plc_member, mapping.terminal)
        for mapping in TagAnalogOutputBlock.mappings
    ) == (
        ("Val_CVOut", "OUT"),
        ("Out_CV", "OUT_CV"),
        ("SrcQ", "STATUS"),
        ("Cfg_MaxCV", "SP_HILIM"),
        ("Cfg_MinCV", "SP_LOLIM"),
        ("Cfg_CVEUMin", "EUMIN"),
        ("Cfg_CVEUMax", "EUMAX"),
        ("Cfg_EU", "EUSTR"),
    )
    assert tuple(
        (mapping.plc_member, mapping.terminal)
        for mapping in TagDiscreteInputBlock.mappings
    ) == (
        ("Sts", "OUT_D"),
        ("SrcQ", "STATUS"),
        ("Inp_Target", "DEV_ALMLIM"),
    )
    assert tuple(
        (mapping.plc_member, mapping.terminal)
        for mapping in TagDiscreteOutputBlock.mappings
    ) == (
        ("Out", "OUT_D"),
        ("SrcQ", "STATUS"),
        ("Val_Cmd", "VAL_CMD"),
        ("Val_Fdbk", "VAL_FDBK"),
    )


def test_tagai_runtime_mirrors_exact_plc_mapping_and_dynamic_scale():
    store = SharedDataStore()
    store.set_many({
        "PLC.AI.Val": 57.25,
        "PLC.AI.SrcQ": 0,
        "PLC.AI.PSet_HiHiLim": 95.0,
        "PLC.AI.PSet_HiLim": 90.0,
        "PLC.AI.PSet_LoLim": 10.0,
        "PLC.AI.PSet_LoLoLim": 5.0,
        "PLC.AI.Cfg_PVEUMin": -20.0,
        "PLC.AI.Cfg_PVEUMax": 120.0,
        "PLC.AI.Cfg_EU": "degC",
    })
    block = _configured(TagAnalogInputBlock("AI_MON"), "PLC.AI")
    runtime = _runtime(block, store)

    assert runtime.execute_scan(0.1)
    assert block.get_output("OUT") == pytest.approx(57.25)
    assert block.get_output("STATUS") == 0
    assert block.get_output("DEV_HIHILIM") == pytest.approx(95.0)
    assert block.get_output("DEV_HILIM") == pytest.approx(90.0)
    assert block.get_output("DEV_LOLIM") == pytest.approx(10.0)
    assert block.get_output("DEV_LOLOLIM") == pytest.approx(5.0)
    assert block.get_output("EUMIN") == pytest.approx(-20.0)
    assert block.get_output("EUMAX") == pytest.approx(120.0)
    assert block.get_output("EUSTR") == "degC"
    assert block.outputs["OUT"].units == "degC"
    assert block.outputs["OUT"].eu_range == (-20.0, 120.0)
    assert block.outputs["OUT"].status is Quality.GOOD
    assert block.get_output("BLOCK_ERR") == ""
    assert block.status is BlockStatus.GOOD


def test_tag_monitors_hold_values_and_raise_bad_pv_from_primary_status():
    store = SharedDataStore()
    store.set_sample("PLC.AO.Val_CVOut", 61.0, quality="GOOD")
    store.set_many({
        "PLC.AO.Out_CV": 64.0,
        "PLC.AO.SrcQ": 0,
        "PLC.AO.Cfg_MaxCV": 88.0,
        "PLC.AO.Cfg_MinCV": 12.0,
        "PLC.AO.Cfg_CVEUMin": 0.0,
        "PLC.AO.Cfg_CVEUMax": 100.0,
        "PLC.AO.Cfg_EU": "%",
    })
    block = _configured(TagAnalogOutputBlock("AO_MON"), "PLC.AO")
    runtime = _runtime(block, store)
    assert runtime.execute_scan(0.1)
    assert block.get_output("OUT") == pytest.approx(61.0)
    assert block.get_output("OUT_CV") == pytest.approx(64.0)
    assert block.get_output("SP_HILIM") == pytest.approx(88.0)
    assert block.get_output("SP_LOLIM") == pytest.approx(12.0)
    assert store.drain_writes() == []

    store.set_sample("PLC.AO.Val_CVOut", 62.0, quality="UNCERTAIN")
    assert runtime.execute_scan(0.1)
    assert block.get_output("OUT") == pytest.approx(62.0)
    assert block.outputs["OUT"].status is Quality.UNCERTAIN
    assert block.get_output("BLOCK_ERR") == ""
    assert block.status is BlockStatus.UNCERTAIN

    store.mark_sample_stale("PLC.AO.Val_CVOut")
    assert runtime.execute_scan(0.1)
    assert block.get_output("OUT") == pytest.approx(62.0)
    assert block.outputs["OUT"].status is Quality.BAD
    assert block.outputs["OUT"].limit is LimitStatus.NOT_LIMITED
    assert block.get_output("BLOCK_ERR") == "Bad PV"
    assert block.get_output("BAD_ACTIVE") is True
    assert block.get_output("ABNORM_ACTIVE") is False
    assert block.status is BlockStatus.BAD

    block.config.params["BAD_MASK"] = 0
    assert runtime.execute_scan(0.1)
    assert block.get_output("BAD_ACTIVE") is False
    assert block.get_output("ABNORM_ACTIVE") is True


def test_missing_or_invalid_companion_is_bad_without_falsifying_primary():
    block = _configured(TagAnalogInputBlock("AI_MON"), "PLC.AI")
    block.update_from_snapshot({
        "PLC.AI.Val": 1.25,
        "PLC.AI.SrcQ": "not-an-int",
    })
    assert block.outputs["OUT"].status is Quality.GOOD
    assert block.outputs["STATUS"].status is Quality.BAD
    assert block.outputs["DEV_HIHILIM"].status is Quality.BAD
    assert block.get_output("BLOCK_ERR") == ""
    assert block.status is BlockStatus.GOOD

    block.update_from_snapshot({})
    assert block.get_output("OUT") == pytest.approx(1.25)
    assert block.outputs["OUT"].status is Quality.BAD
    assert block.get_output("BLOCK_ERR") == "Bad PV"


def test_tagdi_inverts_expected_target_and_tagdo_coerces_plc_values():
    di = _configured(TagDiscreteInputBlock("DI_MON"), "PLC.DI")
    di.update_from_snapshot({
        "PLC.DI.Sts": "ON",
        "PLC.DI.SrcQ": 0,
        "PLC.DI.Inp_Target": "TRUE",
    })
    assert di.get_output("OUT_D") is True
    assert di.get_output("DEV_ALMLIM") is False
    assert di.outputs["OUT_D"].status is Quality.GOOD

    do = _configured(TagDiscreteOutputBlock("DO_MON"), "PLC.DO")
    do.update_from_snapshot({
        "PLC.DO.Out": "0",
        "PLC.DO.SrcQ": "0",
        "PLC.DO.Val_Cmd": "2",
        "PLC.DO.Val_Fdbk": 1.0,
    })
    assert do.get_output("OUT_D") is False
    assert do.get_output("STATUS") == 0
    assert do.get_output("VAL_CMD") == 2
    assert do.get_output("VAL_FDBK") == 1
    assert do.status is BlockStatus.GOOD


def test_source_overrides_and_json_round_trip_are_primitive_and_lossless():
    block = _configured(TagAnalogInputBlock("AI_MON"), "PLC.AI")
    block.id = "tagai-fixed"
    block.x = 12.5
    block.y = -4.0
    block.scan_rate = 3
    block.config.params.update({
        "TAG_SEPARATOR": "/",
        "SOURCE_VAL": "external/value",
        "SHOW_DIAGNOSTIC_PINS": True,
    })
    block._apply_config()
    payload = block.to_dict()
    encoded = json.dumps(payload)
    restored = TagAnalogInputBlock.from_dict(json.loads(encoded))

    assert restored.to_dict() == payload
    assert restored.source_tag("Val") == "external/value"
    assert restored.source_tag("SrcQ") == "PLC.AI/SrcQ"
    assert restored.scan_rate == 3
    assert restored.outputs["BLOCK_ERR"].hidden is False
    # Azeo tag monitors are read-only outputs; forcing them must be refused.
    assert restored.force_terminal("OUT", 99.0) is False
    assert restored.forced_terminals() == []


def test_tagio_assignment_conversion_preserves_authoring_identity():
    placeholder = TagIoBlock("PLC_MON")
    placeholder.id = "fixed-id"
    placeholder.x = 17.0
    placeholder.y = 29.0
    placeholder.scan_rate = 4
    placeholder.config.params = {
        "tag": "PLC.DI",
        "TAG_DEFINITION": "TagDI_MONITOR",
    }
    placeholder.execute(0.1)
    assert placeholder.outputs["OUT"].status is Quality.BAD
    assert placeholder.status is BlockStatus.BAD
    converted = placeholder.convert_assignment()

    assert isinstance(converted, TagDiscreteInputBlock)
    assert converted.id == "fixed-id"
    assert converted.instance_name == "PLC_MON"
    assert (converted.x, converted.y) == (17.0, 29.0)
    assert converted.scan_rate == 4
    assert converted.config.params == {"tag": "PLC.DI"}
    with pytest.raises(ValueError, match="TAGIO assignment"):
        placeholder.convert_assignment("UNKNOWN")


def test_verifier_and_tag_database_understand_read_only_tag_monitors():
    missing_graph = StrategyGraph("TAG-MISSING")
    missing = TagAnalogInputBlock("AI_MON")
    missing_graph.add_block(missing)
    assert any(
        item.severity == "ERROR" and "no PLC control tag" in item.message
        for item in validate_strategy(missing_graph)
    )

    placeholder_graph = StrategyGraph("TAG-PLACEHOLDER")
    placeholder_graph.add_block(TagIoBlock("UNASSIGNED"))
    assert any(
        item.severity == "ERROR" and "must be assigned" in item.message
        for item in validate_strategy(placeholder_graph)
    )

    graph = StrategyGraph("TAG-INDEX")
    monitor = _configured(TagDiscreteOutputBlock("DO_MON"), "PLC.DO")
    graph.add_block(monitor)
    assert not [
        item for item in validate_strategy(graph) if item.severity == "ERROR"
    ]
    database = TagDatabase.from_graphs((graph,))
    assert database.lookup("TAG-INDEX/DO_MON") is None
    fields = {
        entry.name: entry
        for entry in database.of_kind(EntryKind.FIELD)
    }
    assert set(fields) == {"Out", "SrcQ", "Val_Cmd", "Val_Fdbk"}
    assert fields["Out"].path == "TAG-INDEX/DO_MON/FIELD/Out"
    assert fields["Out"].io_tag == "PLC.DO.Out"
    assert fields["Out"].direction == "input"
    assert fields["Out"].data_type == "BOOL"
    assert fields["SrcQ"].data_type == "INT"
    assert fields["Val_Cmd"].io_tag == "PLC.DO.Val_Cmd"
    assert set(database.field_tags()) == {
        "PLC.DO.Out", "PLC.DO.SrcQ", "PLC.DO.Val_Cmd", "PLC.DO.Val_Fdbk",
    }


def test_tag_database_indexes_source_overrides_not_an_inert_base_scalar():
    graph = StrategyGraph("TAG-OVERRIDE")
    monitor = _configured(TagAnalogInputBlock("AI_MON"), "PLC.AI")
    monitor.config.params["SOURCE_VAL"] = "ns=7;s=Area1.AI.Value"
    monitor._apply_config()
    graph.add_block(monitor)

    database = TagDatabase.from_graphs((graph,))

    value = database.lookup("TAG-OVERRIDE/AI_MON/FIELD/Val")
    assert value is not None
    assert value.io_tag == "ns=7;s=Area1.AI.Value"
    assert "PLC.AI" not in database.field_tags()
    assert "ns=7;s=Area1.AI.Value" in database.field_tags()


def test_runtime_debugger_executes_tag_monitor_once_and_exposes_outputs():
    store = SharedDataStore()
    store.set_many({
        "PLC.DI.Sts": True,
        "PLC.DI.SrcQ": 0,
        "PLC.DI.Inp_Target": False,
    })
    block = _configured(TagDiscreteInputBlock("DI_MON"), "PLC.DI")
    runtime = _runtime(block, store)

    assert runtime.debug_pause()
    assert runtime.debug_step_block(0.2) == block.id
    debug = runtime.get_debug_status()
    assert debug["last_block"]["type"] == "TAGDI"
    assert debug["last_outcome"] == "executed"
    assert runtime.scan_count == 1
    assert block.get_output("OUT_D") is True
    assert block.get_output("DEV_ALMLIM") is True
    assert tuple(block.outputs) == (
        "OUT_D", "STATUS", "DEV_ALMLIM",
        "BLOCK_ERR", "BAD_ACTIVE", "ABNORM_ACTIVE",
    )


def test_data_bridge_services_tag_monitor_without_runtime_context():
    """The ordinary controller runtime does not require a plugin context."""
    store = SharedDataStore()
    store.set_many({
        "PLC.AI.Val": 23.75,
        "PLC.AI.SrcQ": 0,
        "PLC.AI.PSet_HiHiLim": 95.0,
        "PLC.AI.PSet_HiLim": 90.0,
        "PLC.AI.PSet_LoLim": 10.0,
        "PLC.AI.PSet_LoLoLim": 5.0,
        "PLC.AI.Cfg_MinScale": 0.0,
        "PLC.AI.Cfg_MaxScale": 100.0,
        "PLC.AI.Cfg_EU": "%",
    })
    block = _configured(TagAnalogInputBlock("AI_MON"), "PLC.AI")
    graph = StrategyGraph("TAG-BRIDGE")
    graph.add_block(block)
    runtime = StrategyRuntime()
    runtime.load(compile_strategy(graph), DataBridge(store))
    assert runtime.go_online()

    assert runtime.execute_scan(0.1)
    assert block.get_output("OUT") == pytest.approx(23.75)
    assert block.outputs["OUT"].status is Quality.GOOD
    assert block.status is BlockStatus.GOOD


def test_bridge_and_runtime_context_share_one_snapshot_per_scan():
    store = _CountingStore()
    store.set_many({
        "PLC.DI.Sts": True,
        "PLC.DI.SrcQ": 0,
        "PLC.DI.Inp_Target": False,
        "PLC.DO.Out": False,
        "PLC.DO.SrcQ": 0,
        "PLC.DO.Val_Cmd": 1,
        "PLC.DO.Val_Fdbk": 1,
    })
    graph = StrategyGraph("TAG-ONE-SNAPSHOT")
    graph.add_block(_configured(TagDiscreteInputBlock("DI_MON"), "PLC.DI"))
    graph.add_block(_configured(TagDiscreteOutputBlock("DO_MON"), "PLC.DO"))
    runtime = StrategyRuntime()
    runtime.set_context(RuntimeContext(store=store, plugin_id="test"))
    runtime.load(compile_strategy(graph), DataBridge(store))
    assert runtime.go_online()
    store.get_all_calls = 0
    store.get_samples_calls = 0

    assert runtime.execute_scan(0.1)
    assert store.get_all_calls == 1
    assert store.get_samples_calls == 1


def test_runtime_wire_carries_tag_value_and_quality_on_normal_scan_boundary():
    store = SharedDataStore()
    store.set_sample("PLC.AI.Val", 42.5, quality="BAD")
    block = _configured(TagAnalogInputBlock("AI_MON"), "PLC.AI")
    sink = _CaptureBlock("SINK")
    graph = StrategyGraph("TAG-WIRE")
    graph.add_block(block)
    graph.add_block(sink)
    assert graph.add_wire(block.id, "OUT", sink.id, "IN") is not None
    runtime = StrategyRuntime()
    runtime.load(compile_strategy(graph), DataBridge(store))
    assert runtime.go_online()

    # Like AI/DI, a store-driven TAG monitor is sampled before forward-wire
    # propagation, so its immediate consumer sees one coherent current scan.
    assert runtime.execute_scan(0.1)
    assert block.get_output("OUT") == pytest.approx(42.5)
    assert block.outputs["OUT"].status is Quality.BAD
    assert sink.get_input("IN") == pytest.approx(42.5)
    assert sink.inputs["IN"].status is Quality.BAD
    assert sink.outputs["OUT"].status is Quality.BAD

    store.set_sample("PLC.AI.Val", 44.0, quality="GOOD")
    assert runtime.execute_scan(0.1)
    assert sink.get_input("IN") == pytest.approx(44.0)
    assert sink.inputs["IN"].status is Quality.GOOD
    assert sink.outputs["OUT"].status is Quality.GOOD
