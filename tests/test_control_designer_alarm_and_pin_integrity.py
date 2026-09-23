"""Regression tests for alarm aliases and terminal-visibility engineering edits."""
from __future__ import annotations

import os
from pathlib import Path
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import azeo_control_trainer.core.strategy.blocks  # noqa: E402,F401
from azeo_control_trainer.core.strategy.model.block_registry import registry  # noqa: E402
from azeo_control_trainer.core.strategy.model.strategy_graph import (  # noqa: E402
    StrategyGraph,
)
from azeo_control_trainer.azeo_control_designer.canvas.strategy_scene import (  # noqa: E402
    StrategyScene,
)
from azeo_control_trainer.azeo_control_designer.panels.alarm_view import (  # noqa: E402
    AlarmViewPanel,
)
from azeo_control_trainer.azeo_control_designer.designer_tab import (  # noqa: E402
    StrategyDesignerTab,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _rows(panel: AlarmViewPanel) -> dict[str, int]:
    return {
        panel.table.item(row, 1).text(): row
        for row in range(panel.table.rowCount())
    }


def test_alarm_view_resolves_alarm_block_aliases_and_editable_enables() -> None:
    _app()
    graph = StrategyGraph("ALARM_ENGINEERING")
    block = registry.create("ALARM", "ALM_101")
    assert block is not None
    block.config.params.update({"DEV_EN": True, "ROC_EN": True})
    graph.add_block(block)

    panel = AlarmViewPanel()
    changes: list[tuple[str, str, object]] = []
    panel.configEditRequested.connect(
        lambda block_id, key, value: changes.append((block_id, key, value)))
    panel.set_graph(graph)

    rows = _rows(panel)
    assert set(rows) == {
        "HI HI", "HI", "DV HI", "DV LO", "LO", "LO LO", "ROC",
    }
    expected_enables = {
        "HI HI": "HH_EN",
        "HI": "HI_EN",
        "DV HI": "DEV_EN",
        "DV LO": "DEV_EN",
        "LO": "LO_EN",
        "LO LO": "LL_EN",
        "ROC": "ROC_EN",
    }
    for label, key in expected_enables.items():
        item = panel.table.item(rows[label], 2)
        assert item.data(panel.ROLE_ENABLE_KEY) == key
        assert item.flags() & Qt.ItemIsUserCheckable

    # Public deviation names resolve to the block's canonical persisted keys.
    dv_hi_row = rows["DV HI"]
    assert panel.table.item(
        dv_hi_row, 3).data(panel.ROLE_CONFIG_KEY) == "DEV_HI"
    panel.table.item(dv_hi_row, 3).setText("6.25")
    assert changes[-1] == (block.id, "DEV_HI", 6.25)

    panel.table.item(rows["HI HI"], 2).setCheckState(Qt.Unchecked)
    assert changes[-1] == (block.id, "HH_EN", False)

    # The actual output terminals use HI_HI/DEV_HI, not *_ACT.
    block.outputs["HI_HI"].value = True
    block.outputs["DEV_HI"].value = True
    panel.table.item(rows["HI HI"], 2).setCheckState(Qt.Checked)
    panel.refresh_live()
    assert panel.table.item(rows["HI HI"], 5).text() == "ACTIVE"
    assert panel.table.item(rows["DV HI"], 5).text() == "ACTIVE"
    assert panel.summary.text().startswith("2 active")
    panel.deleteLater()


def test_alarm_view_reads_pid_core_state_without_inventing_enable_keys() -> None:
    _app()
    graph = StrategyGraph("PID_ENGINEERING")
    block = registry.create("PID", "FIC_101")
    assert block is not None
    block.config.params["hi_lim"] = 75.0
    graph.add_block(block)

    panel = AlarmViewPanel()
    panel.set_graph(graph)
    rows = _rows(panel)
    assert set(rows) == {"HI HI", "HI", "DV HI", "DV LO", "LO", "LO LO"}
    hi_enabled = panel.table.item(rows["HI"], 2)
    assert hi_enabled.checkState() == Qt.Checked
    assert not (hi_enabled.flags() & Qt.ItemIsUserCheckable)

    block.pid_core_block.alarm_state.hi_act = True
    panel.refresh_live()
    assert panel.table.item(rows["HI"], 5).text() == "ACTIVE"
    assert panel.summary.text().startswith("1 active")
    panel.deleteLater()


def test_pin_visibility_is_undoable_marks_dirty_and_keeps_connected_pins() -> None:
    _app()
    scene = StrategyScene()
    block = registry.create("ABS", "CALC_101")
    assert block is not None
    item = scene.add_block(block, QPointF(0, 0))
    scene.undo_stack.clear()
    modified: list[bool] = []
    scene.strategyModified.connect(lambda: modified.append(True))

    item._hide_unused_pins()
    assert scene.undo_stack.count() == 1
    assert scene.undo_stack.undoText() == "Hide Unused Pins"
    assert modified == [True]
    assert all(terminal.hidden for terminal in block.inputs.values())
    assert all(terminal.hidden for terminal in block.outputs.values())

    scene.undo_stack.undo()
    assert not any(terminal.hidden for terminal in block.inputs.values())
    assert not any(terminal.hidden for terminal in block.outputs.values())
    assert len(modified) == 2

    scene.undo_stack.redo()
    item._show_all_pins()
    assert scene.undo_stack.count() == 2
    assert scene.undo_stack.undoText() == "Show All Pins"
    assert not any(terminal.hidden for terminal in block.inputs.values())
    assert not any(terminal.hidden for terminal in block.outputs.values())

    output_name = next(iter(block.outputs))
    item._toggle_terminal("output", output_name, True)
    assert block.outputs[output_name].hidden
    assert scene.undo_stack.undoText() == f"Hide Output Pin {output_name}"
    scene.undo_stack.undo()
    assert not block.outputs[output_name].hidden

    input_name = next(iter(block.inputs))
    block.inputs[input_name].connected = True
    count = scene.undo_stack.count()
    item._toggle_terminal("input", input_name, True)
    assert not block.inputs[input_name].hidden
    assert scene.undo_stack.count() == count
    scene.deleteLater()


def test_diagram_wide_pin_visibility_is_one_atomic_undo_step() -> None:
    _app()
    scene = StrategyScene()
    blocks = [registry.create("ABS", f"CALC_{index}") for index in (1, 2)]
    assert all(block is not None for block in blocks)
    for index, block in enumerate(blocks):
        scene.add_block(block, QPointF(index * 160, 0))
    scene.undo_stack.clear()

    status_messages: list[str] = []
    tab = SimpleNamespace(
        _active_scene=lambda: scene,
        _status_bar=SimpleNamespace(
            showMessage=lambda message, _timeout: status_messages.append(message)
        ),
    )
    StrategyDesignerTab._set_all_pins(tab, hide_unused=True)

    assert scene.undo_stack.count() == 1
    assert scene.undo_stack.undoText() == "Hide Unused Pins"
    assert all(
        terminal.hidden
        for block in blocks
        for terminal in (*block.inputs.values(), *block.outputs.values())
    )
    scene.undo_stack.undo()
    assert not any(
        terminal.hidden
        for block in blocks
        for terminal in (*block.inputs.values(), *block.outputs.values())
    )
    assert status_messages[-1] == "Hid unconnected pins on 2 block(s)"
    scene.deleteLater()
