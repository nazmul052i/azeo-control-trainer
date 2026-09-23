"""A Control Designer memory block and a PA use the same persisted Tag DB value."""
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtWidgets import QApplication

from azeo_control_trainer.core.datastore.memory_tags import MemoryTag, MemoryTagStore
from azeo_control_trainer.core.datastore.shared_data_store import SharedDataStore
from azeo_control_trainer.core.hmi.binding.source import LiveGraphSource
from azeo_control_trainer.core.procedures.authoring import ProcedureDraft
from azeo_control_trainer.core.procedures.runtime import ProcedureRun
from azeo_control_trainer.core.strategy.blocks.utility_blocks import MemoryFloatBlock, MemoryBoolBlock, MemoryIntBlock, MemoryStringBlock
from azeo_control_trainer.core.strategy.engine.bridge import DataBridge
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
from azeo_control_trainer.core.strategy.model.terminal import Quality
from azeo_control_trainer.core.strategy.tagdb import TagDatabase
from test_procedure_integration import wait_for


def test_tagdb_persistence_duplicate_types_and_atomic_batch(tmp_path):
    memory = MemoryTagStore(tmp_path)
    assert memory.definitions() == []
    assert not memory.path.exists()  # Opening a project creates no artifacts.
    for name, dtype, value in (("limit", "float", 5), ("ready", "bool", False),
                               ("count", "int", 0), ("note", "str", "")):
        memory.create(dict(name=name, data_type=dtype, value=value))
    with pytest.raises(ValueError, match="already exists"):
        memory.create(dict(name="LIMIT", data_type="float", value=1))
    with pytest.raises(ValueError, match="requires bool"):
        memory.write_many({"MEMORY/limit/VALUE": 99, "MEMORY/ready/VALUE": "false"})
    assert memory.read("MEMORY/limit/VALUE") == 5
    memory.write("MEMORY/note/VALUE", "Shutdown reviewed")
    database = TagDatabase.from_area(tmp_path)
    assert database.lookup("MEMORY/note/VALUE").kind == "memory"
    assert database.memory.read("MEMORY/note/VALUE") == "Shutdown reviewed"
    source = LiveGraphSource(lambda: {}, memory=database.memory)
    assert source.read("MEMORY/ready/VALUE").quality == Quality.GOOD
    assert source.write("MEMORY/ready/VALUE", True).success
    assert not source.write("MEMORY/ready/VALUE", 1).success
    with pytest.raises(ValueError, match="8192"):
        memory.write("MEMORY/note/VALUE", "x" * 8193)


@pytest.mark.parametrize("block_class,dtype,value,output", [
    (MemoryFloatBlock, "float", 12.5, "OUT"), (MemoryBoolBlock, "bool", True, "OUT_D"),
    (MemoryIntBlock, "int", 12, "OUT"), (MemoryStringBlock, "str", "Review complete", "OUT"),
])
def test_control_block_reads_and_writes_same_project_memory(tmp_path, block_class, dtype, value, output):
    store = SharedDataStore()
    store.tagdb = TagDatabase.from_area(tmp_path)
    spec = MemoryTag(name="operator_value", data_type=dtype, value=value)
    store.tagdb.memory.create(spec)
    graph = StrategyGraph("CONTROL")
    block = block_class("MEM1")
    graph.add_block(block)
    block.config.params["memory_tag"] = spec.path
    bridge = DataBridge(store)
    bridge.read_inputs(graph)
    block.execute(.1)
    assert block.get_output(output) == value
    assert block.outputs[output].status == Quality.GOOD
    if dtype == "bool":
        block.inputs["RESET_D"].value = True
        wanted = False
    else:
        block.inputs["WRITE_EN"].value = True
        wanted = "Control Designer" if dtype == "str" else 7 if dtype == "int" else 7.5
        block.inputs["IN"].value = wanted
    block.execute(.1)
    assert MemoryTagStore(tmp_path).read(spec.path) == wanted
    block.config.params["memory_tag"] = "MEMORY/missing/VALUE"
    block.execute(.1)
    assert block.outputs[output].status == Quality.BAD


def test_operator_input_persists_and_calculation_is_visible_to_control(tmp_path):
    memory = MemoryTagStore(tmp_path)
    spec = memory.create(dict(name="limit", data_type="float", value=20, min_value=0, max_value=100))
    result = memory.create(dict(name="threshold", data_type="float", value=0))
    draft = ProcedureDraft.new()
    draft.use_memory(spec.model_dump())
    draft.use_memory(result.model_dump())
    draft.data["steps"] = [dict(id="input", type="operator_input", variable="limit", input_type="float"),
                           dict(id="calc", type="calculate", calculation_rows=[dict(variable="threshold", expression="limit / 2")])]
    run = ProcedureRun(tmp_path / "history.sqlite", memory=memory)
    events = []
    def collect():
        events.extend(run.drain())
    try:
        for response, initial in ((18, 20), (24, 18)):
            events.clear()
            run.observe(0, {})
            run.start(draft.definition(), actor="Operator")
            wait_for(lambda: any(e["kind"] == "prompt" for e in events), collect)
            prompt = next(e for e in events if e["kind"] == "prompt")
            assert prompt["step"]["value"] == initial
            run.answer(prompt["prompt_id"], response)
            wait_for(lambda: not run.active)
            assert run.result.status.value == "COMPLETE", run.result.message
            assert MemoryTagStore(tmp_path).read(spec.path) == response
            assert memory.read(result.path) == response / 2
        assert run.store.verify_audit_chain()[0]
    finally:
        run.close()


def test_control_designer_creation_is_discoverable_by_pa(tmp_path):
    from types import SimpleNamespace
    from azeo_control_trainer.core.presentation.tagdb_browser import TagDatabaseBrowser
    from azeo_control_trainer.azeo_pa_designer.symbols import SymbolPicker, resolve_symbol
    app = QApplication.instance() or QApplication([])
    database = TagDatabase.from_area(tmp_path)
    browser = TagDatabaseBrowser(store=SimpleNamespace(tagdb=database))
    browser.new_memory()
    dialog = browser.memory_dialog
    dialog.name.setText("operator_limit")
    dialog.value.setText("25")
    dialog.apply()
    assert database.lookup("MEMORY/operator_limit/VALUE") is not None
    draft = ProcedureDraft.new()
    picker = SymbolPicker(draft, TagDatabase.from_area(tmp_path), memory_only=True)
    picker.memory.setCurrentRow(0)
    picker.choose_memory()
    assert resolve_symbol(draft, picker.database, picker.reference) == "operator_limit"
    assert draft.data["variables"][0]["tag_path"] == "MEMORY/operator_limit/VALUE"
    picker.close()
    browser.close()
    app.processEvents()


def test_shared_memory_false_pulse_resets_pa_hold(tmp_path):
    memory = MemoryTagStore(tmp_path)
    spec = memory.create(dict(name="permit", data_type="bool", value=True))
    draft = ProcedureDraft.new()
    draft.use_memory(spec.model_dump())
    draft.data["steps"] = [dict(id="wait", type="wait_until", condition="permit",
                                stable_for_sec=5, timeout_sec=30, poll_sec=5)]
    run = ProcedureRun(tmp_path / "audit.sqlite", memory=memory)
    events = []
    def collect():
        events.extend(run.drain())
    try:
        run.observe(0, {})
        run.start(draft.definition(), actor="Operator")
        wait_for(lambda: any(e["kind"] == "progress" for e in events), collect)
        memory.write(spec.path, False)
        run.observe(2, {})
        memory.write(spec.path, True)
        run.observe(3, {})
        run.observe(5, {})
        wait_for(lambda: any(e["kind"] == "progress" and e["elapsed"] == 5 for e in events), collect)
        assert run.active
        assert [e for e in events if e["kind"] == "progress"][-1]["stable"] == 2
        run.observe(10, {})
        wait_for(lambda: not run.active)
        assert run.result.status.value == "COMPLETE", run.result.message
    finally:
        run.close()
