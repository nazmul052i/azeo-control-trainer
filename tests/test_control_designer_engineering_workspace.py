from __future__ import annotations

from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
from azeo_control_trainer.azeo_control_designer.panels.engineering_workspace import (
    EngineeringWorkspacePanel,
)


def test_workspace_groups_module_tools_without_duplicating_models(qapp):
    workspace = EngineeringWorkspacePanel()
    assert [workspace.tabText(i) for i in range(workspace.count())] == [
        "Blocks", "Hierarchy", "Alarms", "Problems"
    ]
    graph = StrategyGraph("M-101")
    workspace.set_graph(graph)
    assert workspace.hierarchy._graph is graph
    assert workspace.alarms._graph is graph
