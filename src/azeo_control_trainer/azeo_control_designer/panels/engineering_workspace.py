"""The module-scoped engineering workspace beside the FBD canvas."""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI

from PySide6.QtWidgets import QTabWidget

from .alarm_view import AlarmViewPanel
from .block_palette import BlockPalette
from .module_hierarchy import ModuleHierarchyPanel
from .problems_panel import ProblemsPanel


class EngineeringWorkspacePanel(QTabWidget):
    """Blocks, hierarchy and alarms in one stable-width pane.

    The project browser remains above this widget because it describes the
    controller/project.  These three tabs all describe the *active module*;
    grouping them prevents three independent docks from consuming the canvas.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDocumentMode(True)
        self.setMovable(False)
        self.palette = BlockPalette()
        self.hierarchy = ModuleHierarchyPanel()
        self.alarms = AlarmViewPanel()
        self.problems = ProblemsPanel()
        self.addTab(self.palette, "Blocks")
        self.addTab(self.hierarchy, "Hierarchy")
        self.addTab(self.alarms, "Alarms")
        self.addTab(self.problems, "Problems")
        self.setTabToolTip(0, "Place function blocks and reusable templates")
        self.setTabToolTip(1, "Browse the active module's logical structure")
        self.setTabToolTip(2, "Configure and monitor alarms in the active module")
        self.setTabToolTip(3, "Live validation findings and undoable quick fixes")
        self.setStyleSheet(
            "QTabBar::tab { padding: 4px 7px; min-width: 46px; }"
            f"QTabBar::tab:selected {{ color: {UI.blue}; font-weight: 700; "
            f"border-bottom: 2px solid {UI.blue}; }}"
        )

    def set_graph(self, graph) -> None:
        self.hierarchy.set_graph(graph)
        self.alarms.set_graph(graph)

    def set_scene(self, scene) -> None:
        """Bind all module tools, including edit-capable Problems, at once."""
        self.set_graph(scene.graph if scene is not None else None)
        self.problems.set_scene(scene)

    def select_block(self, block_id: str | None) -> None:
        self.hierarchy.select_block(block_id)

    def refresh_structure(self) -> None:
        self.hierarchy.rebuild()
        self.alarms.rebuild()
        self.problems.refresh()

    def refresh_live(self) -> None:
        self.alarms.refresh_live()
