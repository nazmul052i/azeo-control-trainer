from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from azeo_control_trainer.core.datastore.shared_data_store import SharedDataStore
from azeo_control_trainer.core.strategy.blocks.dv_enhanced_analog_blocks import (
    EnhancedControlSelectorBlock,
    EnhancedRampBlock,
)
from azeo_control_trainer.core.strategy.blocks.dv_tag_io_blocks import (
    TagAnalogInputBlock,
    TagAnalogOutputBlock,
    TagDiscreteInputBlock,
    TagDiscreteOutputBlock,
)
from azeo_control_trainer.core.strategy.blocks.sfc_chart_block import SfcChartBlock
from azeo_control_trainer.core.strategy.engine.bridge import DataBridge
from azeo_control_trainer.core.strategy.engine.compiler import CompiledStrategy
from azeo_control_trainer.core.strategy.engine.runtime import StrategyRuntime
from azeo_control_trainer.core.strategy.model.block_base import (
    BlockCategory,
    FunctionBlock,
)
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
from azeo_control_trainer.core.strategy.model.terminal import LimitStatus, Quality
from azeo_control_trainer.core.strategy.model.wire import Wire
from azeo_control_trainer.azeo_control_designer.executive import (
    ControllerExecutive,
)
from azeo_control_trainer.azeo_control_designer.dialogs.runtime_debugger import (
    RuntimeDebuggerDialog,
)
from azeo_control_trainer.azeo_control_designer.designer_tab import (
    StrategyDesignerTab,
)
from azeo_control_trainer.azeo_control_designer.items.block_item import BlockItem
from azeo_control_trainer.azeo_control_designer.panels.ribbon_bar import RibbonBar


class TrackingBlock(FunctionBlock):
    block_type = "DEBUG_TEST"
    category = BlockCategory.LOGIC

    def __init__(self, name: str, trace: list[str]):
        self.trace = trace
        self.calls = 0
        super().__init__(name)

    def _define_terminals(self):
        self.add_input("IN", default=0.0)
        self.add_output("OUT", default=0.0)

    def execute(self, dt: float):
        self.calls += 1
        self.trace.append(self.instance_name)
        if self.instance_name == "A":
            self.set_output("OUT", float(self.calls))
        else:
            self.set_output("OUT", self.get_input("IN"))


def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def make_runtime():
    trace: list[str] = []
    graph = StrategyGraph("DEBUG-MODULE")
    blocks = [TrackingBlock(name, trace) for name in ("A", "B", "C")]
    for block in blocks:
        graph.add_block(block)
    ab = Wire(blocks[0].id, "OUT", blocks[1].id, "IN")
    graph.wires[ab.id] = ab
    compiled = CompiledStrategy(
        graph,
        [block.id for block in blocks],
        [ab],
        [],
    )
    store = SharedDataStore()
    bridge = DataBridge(store)
    runtime = StrategyRuntime()
    runtime.load(compiled, bridge)
    assert runtime.go_online()
    return runtime, compiled, bridge, blocks, trace, store


def test_pause_step_remaining_scan_and_resume_preserve_scan_semantics():
    runtime, _compiled, _bridge, (a, b, c), trace, _store = make_runtime()

    assert runtime.debug_pause()
    assert runtime.is_online
    assert runtime.execute_scan(0.25) is False
    assert trace == []
    assert runtime.scan_count == 0

    assert runtime.debug_step_block(0.25) == a.id
    assert trace == ["A"]
    status = runtime.get_debug_status()
    assert status["paused"] is True
    assert status["scan_active"] is True
    assert status["current_block"]["name"] == "A"
    assert status["next_block"]["name"] == "B"

    # Forward wires are sampled once at scan start. Stepping A does not give
    # B a special debugger-only same-scan propagation path.
    assert a.get_output("OUT") == 1.0
    assert b.get_input("IN") == 0.0
    assert runtime.debug_step_block() == b.id
    assert trace == ["A", "B"]
    assert b.get_output("OUT") == 0.0

    assert runtime.debug_run_scan() == 1
    assert trace == ["A", "B", "C"]
    assert runtime.scan_count == 1
    assert runtime.get_debug_status()["scan_active"] is False
    assert runtime.is_debug_paused

    assert runtime.debug_resume()
    assert runtime.execute_scan(0.25) is True
    assert trace == ["A", "B", "C", "A", "B", "C"]
    assert runtime.scan_count == 2


def test_breakpoints_are_persistent_and_resume_crosses_one_boundary():
    runtime, _compiled, _bridge, (a, b, c), trace, _store = make_runtime()
    assert runtime.set_breakpoint("B") == b.id

    assert runtime.execute_scan(0.2) is False
    assert trace == ["A"]
    status = runtime.debug_status()
    assert status["pause_reason"] == "breakpoint"
    assert status["next_block"]["id"] == b.id
    assert status["breakpoints"] == (b.id,)
    assert runtime.scan_count == 0

    assert runtime.debug_resume()
    assert runtime.execute_scan(0.2) is True
    assert trace == ["A", "B", "C"]
    assert runtime.scan_count == 1

    # The stop was crossed once, not removed. It fires again next scan.
    assert runtime.execute_scan(0.2) is False
    assert trace == ["A", "B", "C", "A"]
    assert runtime.get_debug_status()["next_block"]["id"] == b.id
    assert runtime.debug_breakpoints == frozenset({b.id})


def test_run_to_block_is_one_shot_and_step_finishes_that_scan():
    runtime, _compiled, _bridge, (a, b, c), trace, _store = make_runtime()
    assert runtime.debug_pause()
    assert runtime.debug_run_to_block("C") == c.id

    assert runtime.execute_scan(0.2) is False
    assert trace == ["A", "B"]
    status = runtime.get_debug_status()
    assert status["pause_reason"] == "run_to"
    assert status["next_block"]["id"] == c.id
    assert status["run_to_block"] is None

    assert runtime.debug_step_block() == c.id
    assert trace == ["A", "B", "C"]
    assert runtime.scan_count == 1
    assert runtime.get_debug_status()["pause_reason"] == "scan_complete"

    assert runtime.debug_resume()
    assert runtime.execute_scan(0.2) is True
    assert trace[-3:] == ["A", "B", "C"]


def test_reload_and_offline_abort_partial_scan_but_keep_valid_breakpoints():
    runtime, compiled, bridge, (a, b, _c), trace, _store = make_runtime()
    runtime.set_breakpoint(b.id)
    assert runtime.execute_scan(0.2) is False
    assert trace == ["A"]
    assert runtime.get_debug_status()["scan_active"]

    runtime.load(compiled, bridge)
    status = runtime.get_debug_status()
    assert status["online"] is False
    assert status["paused"] is False
    assert status["scan_active"] is False
    assert status["current_block"] is None
    assert status["run_to_block"] is None
    assert status["breakpoints"] == (b.id,)

    assert runtime.go_online()
    assert runtime.execute_scan(0.2) is False
    runtime.go_offline()
    status = runtime.get_debug_status()
    assert status["online"] is False
    assert status["scan_active"] is False
    assert status["next_block"]["id"] == a.id
    assert status["breakpoints"] == (b.id,)


def test_executive_does_not_report_a_paused_runtime_as_scanned():
    runtime, _compiled, _bridge, _blocks, _trace, store = make_runtime()
    store.add_strategy_runtime(runtime)
    assert runtime.debug_pause()
    runtime._pk_due = 0.0
    executive = ControllerExecutive(store, period_ms=500)

    assert executive.scan_once() == 0
    assert executive.scan_count == 0
    assert runtime.scan_count == 0


def test_debugger_dialog_controls_the_live_runtime_without_owning_canvas():
    qt_app()
    runtime, _compiled, _bridge, (_a, b, _c), _trace, _store = make_runtime()

    class Canvas:
        def __init__(self):
            self.runtime = runtime
            self.scene = None
            self.view = None

    canvas = Canvas()
    dialog = RuntimeDebuggerDialog(canvas)
    assert dialog._state.text() == "RUNNING"
    assert dialog._table.rowCount() == 3
    assert not dialog._step.isEnabled()

    dialog._pause_resume.click()
    assert runtime.is_debug_paused
    assert dialog._step.isEnabled()
    dialog._table.setCurrentCell(1, 2)
    dialog._table.item(1, 0).setCheckState(Qt.Checked)
    assert runtime.has_debug_breakpoint(b.id)

    dialog._step.click()
    assert runtime.get_debug_status()["current_block"]["name"] == "A"
    assert runtime.get_debug_status()["next_block"]["name"] == "B"
    dialog.close()
    dialog.deleteLater()
    QApplication.processEvents()


def test_debugger_terminal_inspector_reads_tag_selector_and_ramp_runtime_state():
    qt_app()
    graph = StrategyGraph("PRIMARY-ONLINE-DIAGNOSTICS")
    tag_blocks = [
        TagAnalogInputBlock("PLC_AI"),
        TagAnalogOutputBlock("PLC_AO"),
        TagDiscreteInputBlock("PLC_DI"),
        TagDiscreteOutputBlock("PLC_DO"),
    ]
    selector = EnhancedControlSelectorBlock("OVERRIDE_SELECT")
    ramp = EnhancedRampBlock("SP_RAMP")
    blocks = [*tag_blocks, selector, ramp]
    for block in blocks:
        graph.add_block(block)
    compiled = CompiledStrategy(graph, [block.id for block in blocks], [], [])
    runtime = StrategyRuntime()
    runtime.load(compiled, DataBridge(SharedDataStore()))
    assert runtime.go_online()

    live_cases = (
        (tag_blocks[0], "OUT", 12.5, Quality.BAD, LimitStatus.NOT_LIMITED),
        (tag_blocks[1], "OUT", 23.75, Quality.UNCERTAIN,
         LimitStatus.HIGH_LIMITED),
        (tag_blocks[2], "OUT_D", True, Quality.GOOD,
         LimitStatus.NOT_LIMITED),
        (tag_blocks[3], "OUT_D", False, Quality.BAD,
         LimitStatus.LOW_LIMITED),
    )
    for block, terminal_name, value, quality, limit in live_cases:
        terminal = block.outputs[terminal_name]
        terminal.value = value
        terminal.status = quality
        terminal.limit = limit

    selector.inputs["SEL_1"].value = 42.0
    assert selector.force_terminal("SEL_1", 42.0)
    selector.outputs["OUT"].value = 42.0
    selector.outputs["OUT"].status = Quality.UNCERTAIN
    selector.outputs["OUT"].limit = LimitStatus.HIGH_LIMITED
    ramp.inputs["ERAMP_END_VALUE"].value = 88.0
    assert ramp.force_terminal("ERAMP_END_VALUE", 88.0)
    ramp.outputs["OUT"].value = 14.25
    ramp.outputs["OUT"].status = Quality.BAD
    ramp.outputs["OUT"].limit = LimitStatus.LOW_LIMITED

    class Canvas:
        def __init__(self):
            self.runtime = runtime
            self.scene = None
            self.view = None

    canvas = Canvas()
    dialog = RuntimeDebuggerDialog(canvas)

    def select(block):
        for row in range(dialog._table.rowCount()):
            if dialog._table.item(row, 0).data(dialog.BLOCK_ID_ROLE) == block.id:
                dialog._table.setCurrentCell(row, 2)
                dialog.refresh()
                break
        else:  # pragma: no cover - assertion below gives a clearer failure
            raise AssertionError(f"debug row missing for {block.instance_name}")
        return {
            dialog._terminals.item(row, 1).text(): tuple(
                dialog._terminals.item(row, column).text()
                for column in range(dialog._terminals.columnCount())
            )
            for row in range(dialog._terminals.rowCount())
        }

    expected_values = ("12.5", "23.75", "TRUE", "FALSE")
    for case, expected in zip(live_cases, expected_values):
        block, terminal_name, _value, quality, limit = case
        rows = select(block)
        assert block.block_type in dialog._terminal_title.text()
        assert rows[terminal_name] == (
            "OUT",
            terminal_name,
            expected,
            quality.name,
            limit.name.replace("_", " "),
            "—",
        )

    selector_rows = select(selector)
    assert selector_rows["SEL_1"] == (
        "IN", "SEL_1", "42", "GOOD", "NOT LIMITED", "YES")
    assert selector_rows["OUT"][2:5] == (
        "42", "UNCERTAIN", "HIGH LIMITED")

    ramp_rows = select(ramp)
    assert ramp_rows["ERAMP_END_VALUE"] == (
        "IN", "ERAMP_END_VALUE", "88", "GOOD", "NOT LIMITED", "YES")
    assert ramp_rows["OUT"][2:5] == ("14.25", "BAD", "LOW LIMITED")

    # The pane is live rather than a snapshot copied when the dialog opened.
    ramp.outputs["OUT"].value = 15.5
    dialog.refresh()
    assert select(ramp)["OUT"][2] == "15.5"
    dialog.close()
    dialog.deleteLater()
    QApplication.processEvents()


def test_ribbon_exposes_only_valid_debugger_operations_for_state():
    qt_app()
    ribbon = RibbonBar()
    routed: list[str] = []
    ribbon.debuggerRequested.connect(lambda: routed.append("open"))
    ribbon.debugPauseResumeRequested.connect(lambda: routed.append("pause"))
    ribbon.debugStepRequested.connect(lambda: routed.append("step"))
    ribbon.debugRunScanRequested.connect(lambda: routed.append("scan"))

    assert not ribbon._btn_debugger.isEnabled()
    ribbon.set_online_state(True)
    assert ribbon._btn_debugger.isEnabled()
    assert ribbon._btn_debug_pause.isEnabled()
    assert not ribbon._btn_debug_step.isEnabled()
    assert not ribbon._btn_debug_scan.isEnabled()
    ribbon._btn_debugger.click()

    ribbon.set_debug_state(online=True, paused=True, scan_active=True)
    assert ribbon._btn_debug_pause.text() == "Resume"
    assert ribbon._btn_debug_step.isEnabled()
    assert ribbon._btn_debug_scan.isEnabled()
    assert "PAUSED" in ribbon._status.text()
    assert "PARTIAL" in ribbon._status.text()
    ribbon._btn_debug_pause.click()
    ribbon._btn_debug_step.click()
    ribbon._btn_debug_scan.click()
    assert routed == ["open", "pause", "step", "scan"]
    ribbon.deleteLater()


def test_sfc_block_context_routes_to_chart_level_debugger():
    qt_app()
    item = BlockItem(SfcChartBlock("SEQ"))
    sentinel = object()
    with patch.object(
            BlockItem, "_open_sfc_debugger", autospec=True,
            return_value=sentinel) as open_sfc:
        assert item._open_runtime_debugger() is sentinel
        open_sfc.assert_called_once_with(item)


def test_block_live_refresh_updates_terminal_quality_surfaces():
    qt_app()
    block = TrackingBlock("PIN", [])
    item = BlockItem(block)
    terminals = tuple(item.terminal_items.values())
    with patch.object(type(terminals[0]), "refresh", autospec=True) as refresh:
        item.set_live_mode(True)
        assert refresh.call_count == len(terminals)
        item.refresh()
        assert refresh.call_count == len(terminals) * 2


def test_designer_ribbon_drives_active_canvas_debugger_end_to_end():
    qt_app()
    runtime, compiled, _bridge, (a, _b, _c), trace, _store = make_runtime()
    tab = StrategyDesignerTab()
    canvas = tab._create_canvas("DEBUG-MODULE")
    canvas.scene.load_graph(compiled.graph)
    canvas.runtime = runtime
    tab._refresh_debug_toolbar()

    assert tab._toolbar._btn_debug_pause.isEnabled()
    tab._toolbar._btn_debug_pause.click()
    assert runtime.is_debug_paused
    tab._toolbar._btn_debug_step.click()
    assert trace == ["A"]
    assert runtime.get_debug_status()["current_block"]["id"] == a.id

    dialog = tab._show_runtime_debugger()
    assert dialog is canvas._runtime_debugger_dialog
    assert tab._show_runtime_debugger() is dialog
    tab._take_canvas_offline(canvas)
    dialog.refresh()
    assert dialog._state.text() == "OFFLINE"
    dialog.close()
    tab.deleteLater()
    QApplication.processEvents()


def test_designer_redownload_retains_breakpoints_for_surviving_block_ids():
    qt_app()
    old_runtime, compiled, _bridge, (_a, b, _c), _trace, store = make_runtime()
    old_runtime.set_debug_breakpoint(b.id)
    old_runtime.go_offline()

    tab = StrategyDesignerTab(store=store)
    canvas = tab._create_canvas("DEBUG-MODULE")
    canvas.scene.load_graph(compiled.graph)
    canvas.runtime = old_runtime
    index = tab._canvas_tabs.indexOf(canvas)

    assert tab.go_online_single(index)
    assert canvas.runtime is not old_runtime
    assert canvas.runtime.has_debug_breakpoint(b.id)
    tab._take_canvas_offline(canvas)
    tab.deleteLater()
    QApplication.processEvents()


def test_active_offline_tab_does_not_inherit_background_online_commands():
    qt_app()
    online, compiled_a, _bridge, _blocks, _trace, _store = make_runtime()
    online.compiled.graph.name = "ONLINE-A"
    offline, compiled_b, _bridge, _blocks, _trace, _store = make_runtime()
    offline.compiled.graph.name = "OFFLINE-B"
    offline.go_offline()

    tab = StrategyDesignerTab()
    canvas_a = tab._create_canvas("ONLINE-A")
    canvas_a.scene.load_graph(compiled_a.graph)
    canvas_a.runtime = online
    canvas_b = tab._create_canvas("OFFLINE-B")
    canvas_b.scene.load_graph(compiled_b.graph)
    canvas_b.runtime = offline

    tab._canvas_tabs.setCurrentWidget(canvas_a)
    assert not tab._toolbar._btn_download.isEnabled()
    assert tab._toolbar._btn_module_offline.isEnabled()
    assert tab._props.is_online()
    assert not tab._status_bar.isHidden()
    assert tab._toolbar._scan_time.text() == "Scan: 0.0 ms"

    tab._canvas_tabs.setCurrentWidget(canvas_b)
    assert online.is_online  # background execution was not disturbed
    assert tab._toolbar._btn_download.isEnabled()
    assert not tab._toolbar._btn_module_offline.isEnabled()
    assert not tab._props.is_online()
    assert tab._status_bar.isHidden()
    assert not tab._toolbar._btn_debugger.isEnabled()
    assert tab._toolbar._scan_time.text() == ""

    tab._take_canvas_offline(canvas_a)
    tab.deleteLater()
    QApplication.processEvents()


def test_offlining_background_canvas_preserves_active_online_ui():
    qt_app()
    runtime_a, compiled_a, _bridge, _blocks, _trace, _store = make_runtime()
    runtime_a.compiled.graph.name = "BACKGROUND-A"
    runtime_b, compiled_b, _bridge, _blocks, _trace, _store = make_runtime()
    runtime_b.compiled.graph.name = "ACTIVE-B"

    tab = StrategyDesignerTab()
    canvas_a = tab._create_canvas("BACKGROUND-A")
    canvas_a.scene.load_graph(compiled_a.graph)
    canvas_a.runtime = runtime_a
    canvas_b = tab._create_canvas("ACTIVE-B")
    canvas_b.scene.load_graph(compiled_b.graph)
    canvas_b.runtime = runtime_b
    tab._sync_legacy_refs()

    tab._take_canvas_offline(canvas_a)

    assert not runtime_a.is_online
    assert runtime_b.is_online
    assert not tab._toolbar._btn_download.isEnabled()
    assert tab._toolbar._btn_module_offline.isEnabled()
    assert tab._props.is_online()
    assert not tab._status_bar.isHidden()
    assert tab._status_bar._name.text() == "ACTIVE-B"
    assert tab._toolbar._btn_debugger.isEnabled()

    tab._take_canvas_offline(canvas_b)
    tab.deleteLater()
    QApplication.processEvents()
