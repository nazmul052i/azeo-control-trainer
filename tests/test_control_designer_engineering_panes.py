"""The Control Designer engineering panes derive from the active graph."""
from __future__ import annotations

import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from azeo_control_trainer.core.strategy.blocks.io_blocks import AIBlock  # noqa: E402
from azeo_control_trainer.core.strategy.model.strategy_graph import (  # noqa: E402
    StrategyGraph,
)
from azeo_control_trainer.azeo_control_designer.panels.alarm_view import (  # noqa: E402
    AlarmViewPanel,
)
from azeo_control_trainer.azeo_control_designer.panels.module_hierarchy import (  # noqa: E402
    ModuleHierarchyPanel,
)


def _app():
    return QApplication.instance() or QApplication([])


def _graph():
    graph = StrategyGraph("UNIT_100")
    graph.set_module_parameter("RATE", 12.5, access="internal_read")
    ai = AIBlock("FT_101")
    ai.config.params.update({
        "HI_HI_LIM": 90.0,
        "HI_LIM": 80.0,
        "LO_LIM": 20.0,
        "LO_LO_LIM": 10.0,
        "HI_HI_ENAB": True,
        "HI_ENAB": True,
        "LO_ENAB": True,
        "LO_LO_ENAB": True,
    })
    graph.add_block(ai)
    return graph, ai


def test_module_hierarchy_shows_active_graph_components_and_parameters():
    _app()
    graph, block = _graph()
    panel = ModuleHierarchyPanel()
    panel.set_graph(graph)

    root = panel.tree.topLevelItem(0)
    assert root.text(0) == "UNIT_100"
    all_text = []
    stack = [root]
    while stack:
        item = stack.pop()
        all_text.append(item.text(0))
        stack.extend(item.child(i) for i in range(item.childCount()))
    assert "RATE" in all_text
    assert "FT_101" in all_text
    panel.select_block(block.id)
    assert panel.tree.currentItem().data(0, panel.ROLE_BLOCK_ID) == block.id


def test_alarm_view_edits_the_block_configuration_and_reads_live_state():
    _app()
    graph, block = _graph()
    panel = AlarmViewPanel()
    changes = []
    panel.configEditRequested.connect(
        lambda block_id, key, value: changes.append((block_id, key, value)))
    panel.set_graph(graph)

    assert panel.table.rowCount() == 4
    panel.table.item(0, 3).setText("91.5")
    assert changes[-1] == (block.id, "HI_HI_LIM", 91.5)

    block.outputs["HI_HI_ACT"].value = True
    panel.refresh_live()
    assert panel.table.item(0, 5).text() == "ACTIVE"
    assert panel.summary.text().startswith("1 active")
