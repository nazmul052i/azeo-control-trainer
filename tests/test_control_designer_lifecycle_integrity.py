"""Focused contracts for parsing, parameter sync, checkpoints, and logging."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

import azeo_control_trainer.core.strategy.blocks  # noqa: E402,F401
from azeo_control_trainer.core.strategy.blocks.action_block import (  # noqa: E402
    ActionBlock, compile_action_source,
)
from azeo_control_trainer.core.strategy.blocks.io_blocks import AIBlock  # noqa: E402
from azeo_control_trainer.core.strategy.blocks.pid_block import PIDBlock  # noqa: E402
from azeo_control_trainer.core.strategy.model.strategy_graph import (  # noqa: E402
    StrategyGraph,
)
from azeo_control_trainer.core.strategy.serialization import (  # noqa: E402
    checkpoint_io, strategy_io,
)
from azeo_control_trainer.azeo_control_designer.dialogs.compare_dialog import (  # noqa: E402
    ParameterDiff, apply_project_values_to_runtime,
)
from azeo_control_trainer.azeo_control_designer.dialogs.datalog_config import (  # noqa: E402
    DataLogConfigDialog, data_logging_capability, is_data_logging_available,
)
from azeo_control_trainer.azeo_control_designer.dialogs.upload_dialog import (  # noqa: E402
    ParameterChange, save_and_sync_parameter_changes, save_parameter_changes,
)
from azeo_control_trainer.azeo_control_designer.widgets.expression_editor_ribbon import (  # noqa: E402
    RibbonExpressionEditor, RibbonExpressionEditorDialog,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_parse_command_and_act_runtime_share_the_restricted_parser() -> None:
    kind, _ = compile_action_source(
        "IF IN1 > 10 THEN\nOUT1 := IN1;\nELSE\nOUT1 := 0;\nEND_IF", 1)
    assert kind == "iec_st"
    assert compile_action_source("IN1 >= 10", 1)[0] == "expression"

    for invalid, mode in (("IF IN1 THEN\nOUT1 := 1", 1),
                          ("import os\nOUT1 = 1", 2)):
        try:
            compile_action_source(invalid, mode)
        except ValueError:
            pass
        else:  # pragma: no cover - makes a false-positive parser unmistakable
            raise AssertionError(f"unsafe/incomplete source accepted: {invalid}")

    block = ActionBlock("ACT1")
    block.config.params.update({"SCRIPT_MODE": 1, "EXPRESSION": "IN1 >= 10"})
    block._apply_config()
    block.inputs["IN1"].value = 12.0
    block.execute(0.1)
    assert block.get_output("OUT1") == 1.0


def test_ribbon_parse_blocks_invalid_accept_and_names_extension() -> None:
    _app()
    editor = RibbonExpressionEditor(
        "IF IN1 > 1 THEN\nOUT1 := IN1;\nEND_IF", mode="expression")
    assert editor.parse_now() is True
    assert "restricted IEC ST" in editor.parser_output.toPlainText()
    editor.set_mode("script")
    assert "AZEO PYTHON EXTENSION" in editor._mode_lbl.text()
    editor.set_text("import os")
    assert editor.parse_now() is False

    dialog = RibbonExpressionEditorDialog("import os", mode="script")
    emitted: list[tuple[str, int]] = []
    dialog.textApplied.connect(lambda text, mode: emitted.append((text, mode)))
    dialog._on_accept()
    assert not emitted
    assert dialog.result() == 0
    dialog.deleteLater()
    editor.deleteLater()


def test_controller_upload_uses_flat_graph_config_and_versioned_save(
        tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(strategy_io, "_SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(strategy_io, "STRATEGY_DIR", tmp_path)
    graph = StrategyGraph("MODULE")
    graph.extra = {"vendor_extension": {"keep": True}}
    block = AIBlock("AI1")
    block.config.params = {"tag": "FIELD.ONE", "scale_lo": 0.0,
                           "scale_hi": 100.0}
    graph.add_block(block)
    path = tmp_path / "module.json"
    comments = [{"text": "keep me", "x": 1, "y": 2}]
    strategy_io.save_strategy(graph, path=path, comments=comments)

    change = ParameterChange(
        module="MODULE", file_path=path, block_id=block.id,
        instance_name="AI1", block_type="AI", parameter="tag",
        file_value="FIELD.ONE", runtime_value="FIELD.TWO",
        category="configuration",
    )
    assert save_parameter_changes(path, [change]) == 1

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["blocks"][0]["config"]["tag"] == "FIELD.TWO"
    assert "params" not in document["blocks"][0]["config"]
    assert document["comments"] == comments
    assert document["vendor_extension"] == {"keep": True}
    assert len(list((tmp_path / "versions").glob("module_*.json"))) == 2
    assert not list(tmp_path.glob(".*.tmp"))


def test_upload_syncs_open_canvas_so_later_save_cannot_revert_it(
        tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(strategy_io, "_SETTINGS_PATH", tmp_path / "settings.json")
    graph = StrategyGraph("MODULE")
    block = AIBlock("AI1")
    block.config.params = {"tag": "FIELD.ONE", "scale_lo": 0.0,
                           "scale_hi": 100.0}
    graph.add_block(block)
    path = tmp_path / "module.json"
    strategy_io.save_strategy(graph, path=path)

    open_graph, _ = strategy_io.load_strategy(path, remember=False)
    open_block = open_graph.blocks[block.id]
    refreshed: list[str] = []
    item = SimpleNamespace(refresh=lambda: refreshed.append(open_block.id))
    scene = SimpleNamespace(
        graph=open_graph,
        get_block_item=lambda block_id: item if block_id == open_block.id else None,
    )
    canvas = SimpleNamespace(
        file_path=str(path), scene=scene, dirty=True,
        downloaded_at=11.0, modified_since_download=True,
    )
    tabs = SimpleNamespace(count=lambda: 1, widget=lambda _index: canvas)
    designer = SimpleNamespace(_canvas_tabs=tabs)
    change = ParameterChange(
        module="MODULE", file_path=path, block_id=block.id,
        instance_name="AI1", block_type="AI", parameter="tag",
        file_value="FIELD.ONE", runtime_value="FIELD.TWO",
        category="configuration",
    )

    assert save_and_sync_parameter_changes(designer, path, [change]) == 1
    assert open_block.config.params["tag"] == "FIELD.TWO"
    assert refreshed == [open_block.id]
    assert canvas.dirty
    assert canvas.downloaded_at == 11.0
    assert canvas.modified_since_download

    # The ordinary canvas save serializes the synchronized graph rather than
    # silently writing the pre-upload value back over the project file.
    strategy_io.save_strategy(open_graph, path=path)
    reloaded, _ = strategy_io.load_strategy(path, remember=False)
    assert reloaded.blocks[block.id].config.params["tag"] == "FIELD.TWO"


def test_project_to_controller_compare_is_generic_for_block_config() -> None:
    graph = StrategyGraph("MODULE")
    block = AIBlock("AI1")
    block.config.params = {"tag": "RUNTIME.TAG"}
    graph.add_block(block)
    change = ParameterChange(
        module="MODULE", file_path=Path("module.json"), block_id=block.id,
        instance_name="AI1", block_type="AI", parameter="tag",
        file_value="PROJECT.TAG", runtime_value="RUNTIME.TAG",
        category="configuration",
    )
    assert apply_project_values_to_runtime(
        graph, [ParameterDiff(change, "MISMATCH")]) == 1
    assert block.config.params["tag"] == "PROJECT.TAG"


class _Store:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.writes: list[tuple[str, object]] = []

    def get(self, key, default=None):
        return self.values.get(key, default)

    def queue_write(self, path, value):
        self.writes.append((path, value))


def test_checkpoint_declares_only_restorable_write_paths(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(checkpoint_io, "_checkpoint_dir", lambda: tmp_path)
    graph = StrategyGraph("LOOP")
    block = PIDBlock("PID1")
    graph.add_block(block)
    store = _Store({
        "ctrl.PID1.SP": 42.0,
        "ctrl.PID1.MODE": "Auto",
        "ctrl.PID1.GAIN": 1.5,
    })
    runtime = SimpleNamespace(
        compiled=SimpleNamespace(graph=graph), is_online=True,
        get_status=lambda: {"scan_count": 10},
    )
    canvas = SimpleNamespace(runtime=runtime, file_path="loop.json")
    path = checkpoint_io.save_checkpoint(
        "operator state", [("LOOP", canvas)], store)
    saved = checkpoint_io.load_checkpoint(path)
    assert saved["schema_version"] == 2
    assert "diagnostic-only" in saved["restore_contract"]
    writes = saved["modules"][0]["restore_writes"]
    assert {write["path"] for write in writes} == {
        "ctrl.PID1.wb.SP", "ctrl.PID1.wb.Mode", "ctrl.PID1.wb.GAIN",
    }
    assert not any("OUT." in write["path"] for write in writes)

    result = checkpoint_io.restore_checkpoint(saved, store)
    assert result == {"LOOP": 3}
    assert store.writes == [(write["path"], write["value"])
                            for write in writes]


def test_version_two_checkpoint_never_infers_writes_from_readbacks() -> None:
    store = _Store()
    checkpoint = {
        "schema_version": 2,
        "modules": [{
            "tab_name": "M1",
            "parameters": {"PID1": {"SP": 99, "OUT.OUT1": 88}},
            "restore_writes": [{"path": "ctrl.PID1.wb.SP", "value": 12}],
        }],
    }
    assert checkpoint_io.restore_checkpoint(checkpoint, store) == {"M1": 1}
    assert store.writes == [("ctrl.PID1.wb.SP", 12)]


def test_data_log_requires_a_real_consumer_contract() -> None:
    assert not is_data_logging_available()
    assert "No data-log consumer" in data_logging_capability().reason
    _app()
    unavailable = DataLogConfigDialog()
    assert not unavailable._tabs.isEnabled()
    assert not unavailable._btn_apply.isEnabled()
    assert "Unavailable" in unavailable._lbl_status.text()
    unavailable.deleteLater()

    class Consumer:
        def apply_data_log_configuration(self, config, settings, store=None):
            return None

        def take_data_log_snapshot(self, **kwargs):
            return 0

    capability = data_logging_capability(Consumer())
    assert capability.available
    assert capability.consumer_name == "Consumer"
