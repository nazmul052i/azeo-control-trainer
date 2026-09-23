"""Control Strategy Designer Tab — Visual strategy designer visual programming.

Layout:
    RibbonBar                (top)
    QSplitter(Horizontal):
        BlockPalette         (left, ~15%)
        QTabWidget(South)    (center, ~70%)  — multiple strategy canvases
        PropertiesPanel      (right, ~15%)
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI

import logging
import inspect
from html import escape
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QMessageBox as _QtMessageBox, QPushButton,
    QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from azeo_control_trainer.core.presentation.headless import is_headless
from azeo_control_trainer.core.presentation.menu_style import retain_menu, studio_menu
from azeo_control_trainer.core.presentation.tab_base import BaseTab
from .canvas.strategy_scene import StrategyScene
from .canvas.strategy_view import StrategyView
from .panels.engineering_workspace import EngineeringWorkspacePanel
from .panels.project_tree import ProjectTree
from .panels.properties_panel import PropertiesPanel
from .panels.ribbon_bar import RibbonBar

log = logging.getLogger("strategy.designer")


class _HeadlessSafeMessageBox(_QtMessageBox):
    """Keep engineering commands non-blocking without a display server.

    Control Designer has many command-result notices. Guarding only the dialog
    that launches a command is insufficient: compile, save, validation, and
    recovery can all reach a message box later in the same call. Centralising
    the guard here preserves every interactive message while making an
    offscreen invocation deterministic. Confirmation prompts deliberately
    answer No, so automation can never approve a destructive operation.
    """

    @staticmethod
    def information(parent, title, text, *args):
        if is_headless():
            log.info("%s: %s", title, text)
            return _QtMessageBox.Ok
        return _QtMessageBox.information(parent, title, text, *args)

    @staticmethod
    def warning(parent, title, text, *args):
        if is_headless():
            log.warning("%s: %s", title, text)
            return _QtMessageBox.No
        return _QtMessageBox.warning(parent, title, text, *args)

    @staticmethod
    def critical(parent, title, text, *args):
        if is_headless():
            log.error("%s: %s", title, text)
            return _QtMessageBox.Ok
        return _QtMessageBox.critical(parent, title, text, *args)

    @staticmethod
    def question(parent, title, text, *args):
        if is_headless():
            log.info("%s refused headlessly: %s", title, text)
            return _QtMessageBox.No
        return _QtMessageBox.question(parent, title, text, *args)


# Existing call sites retain the familiar Qt API and button constants while
# every route now shares the same headless contract.
QMessageBox = _HeadlessSafeMessageBox


# ────────────────────────────────────────────────────────────────────
# StrategyCanvas: lightweight container for one open strategy
# ────────────────────────────────────────────────────────────────────

class StrategyCanvas(QWidget):
    """Bundles a StrategyScene + StrategyView + runtime/bridge for one tab."""

    viewReady = Signal(object)

    def __init__(self, parent=None, *, defer_visuals=False):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.scene = StrategyScene()
        self._breadcrumb = QWidget()
        breadcrumb_layout = QHBoxLayout(self._breadcrumb)
        breadcrumb_layout.setContentsMargins(8, 3, 8, 3)
        breadcrumb_layout.setSpacing(6)
        self._back_button = QPushButton("← Parent")
        self._breadcrumb_label = QLabel()
        self._ports_button = QPushButton("Ports…")
        breadcrumb_layout.addWidget(self._back_button)
        breadcrumb_layout.addWidget(self._breadcrumb_label, 1)
        breadcrumb_layout.addWidget(self._ports_button)
        self._breadcrumb.setStyleSheet(
            f"background: {UI.chrome_alt}; border-bottom: 1px solid {UI.border};")
        self._breadcrumb.hide()
        layout.addWidget(self._breadcrumb)
        self._view = None
        if not defer_visuals:
            self.view

        # Per-canvas engine state
        self.runtime = None
        self.bridge = None

        # File path for this canvas (None = new/unsaved)
        self.file_path: str | None = None

        # Controller tracking
        self.downloaded_at: float | None = None    # time.time() of last download
        self.modified_since_download: bool = False  # edits since last download
        # Detached controller baseline used by Deployment Diff.  The runtime
        # graph is intentionally not the baseline: online tuning edits that
        # same object, which would make a real parameter change compare equal.
        self.download_snapshot: dict | None = None

        # Unsaved-edit tracking, independent of download state. Closing the
        # designer must never silently rewrite a module the user did not edit:
        # auto-save used to run for every open tab, which (once saves went to
        # the real file rather than a shadow copy) rewrote untouched source
        # modules — a UI test nudging a spin box corrupted 43 of them.
        self.dirty: bool = False
        self._navigator_dialog = None

    def set_composite_breadcrumb(self, label: str, back, ports):
        self._breadcrumb_label.setText(f"Module  ›  {label}")
        self._back_button.clicked.connect(back)
        self._ports_button.clicked.connect(ports)
        self._breadcrumb.show()

    def show_navigator(self):
        from .widgets.diagram_navigator import DiagramNavigatorDialog

        if self._navigator_dialog is not None:
            self._navigator_dialog.refresh()
            self._navigator_dialog.show()
            self._navigator_dialog.raise_()
            return self._navigator_dialog
        dialog = DiagramNavigatorDialog(self.scene, self.view, parent=self)
        self._navigator_dialog = dialog
        dialog.finished.connect(
            lambda _result: setattr(self, "_navigator_dialog", None))
        if not is_headless():
            dialog.show()
        return dialog

    @property
    def view(self):
        if self._view is None:
            self.scene.materialize_items()
            self._view = StrategyView(self.scene)
            self.layout().addWidget(self._view)
            self.viewReady.emit(self._view)
            self._view.request_initial_frame()
        return self._view


class StrategyDesignerTab(BaseTab):
    """Visual function-block strategy designer tab."""

    def __init__(self, store=None, provider=None, plugin=None, parent=None):
        super().__init__(store=store, provider=provider, parent=parent)
        self._plugin = plugin

        # Ensure block types are registered
        import azeo_control_trainer.core.strategy.blocks  # noqa: F401

        # Legacy attributes for backward compat — point to active canvas
        self._runtime = None
        self._bridge = None

        # Operator layer, owned only when there is no host to own it.
        self._faceplates = None
        self._executive = None
        self._tagdb_dialog = None
        self._module_report_dialog = None
        self._command_dialogs: dict[str, object] = {}
        self._shortcuts: list[QShortcut] = []
        self._command_palette = None
        self._redundancy_model = None

        self._build_ui()

    # ------------------------------------------------------------ build UI
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Ribbon bar (replaces flat toolbar)
        self._toolbar = RibbonBar(plugin=self._plugin)
        root.addWidget(self._toolbar)

        # Tab widget for multiple strategy canvases
        self._canvas_tabs = QTabWidget()
        self._canvas_tabs.setTabPosition(QTabWidget.South)
        self._canvas_tabs.setTabsClosable(True)
        self._canvas_tabs.setMovable(True)
        self._canvas_tabs.setDocumentMode(True)
        self._canvas_tabs.tabCloseRequested.connect(self._close_canvas_tab)
        self._canvas_tabs.currentChanged.connect(self._on_canvas_tab_changed)
        tab_bar = self._canvas_tabs.tabBar()
        tab_bar.setContextMenuPolicy(Qt.CustomContextMenu)
        tab_bar.customContextMenuRequested.connect(
            self._show_canvas_tab_context_menu)
        self._canvas_tabs.setStyleSheet(
            "QTabWidget::pane { border: none; }"
            f"QTabBar::tab {{ background: {UI.chrome_alt}; color: {UI.text_secondary}; "
            f"border: 1px solid {UI.disabled}; border-bottom: none; "
            "padding: 4px 12px; font-size: 9pt; min-width: 60px; }"
            f"QTabBar::tab:selected {{ background: {UI.chrome}; color: {UI.text}; "
            f"border-bottom: 2px solid {UI.blue}; font-weight: bold; }}"
            f"QTabBar::tab:hover {{ background: {UI.hover}; }}"
            "QTabBar::close-button { "
            "subcontrol-position: right; padding: 2px; }"
        )

        # Panels
        self._project_tree = ProjectTree(plugin=self._plugin)
        self._engineering_workspace = EngineeringWorkspacePanel()
        # Compatibility for callers/tests that address the historical block
        # palette directly. There is still exactly one BlockPalette instance;
        # it now lives in the module-scoped engineering workspace.
        self._palette = self._engineering_workspace.palette
        self._palette.setMaximumWidth(260)

        # Left panel: project tree (top) + block palette (bottom)
        self._left_splitter = QSplitter(Qt.Vertical)
        self._left_splitter.setChildrenCollapsible(True)
        self._left_splitter.addWidget(self._project_tree)
        self._left_splitter.addWidget(self._engineering_workspace)
        self._left_splitter.setSizes([250, 350])
        self._left_splitter.setMaximumWidth(280)

        # Properties panel — always visible (DV / Experion convention) so
        # selecting a block doesn't force the canvas to reflow / squeeze.
        # The panel shows a "No block selected" placeholder when empty;
        # operators can still collapse it via the splitter handle if they
        # want max canvas real-estate.
        self._props = PropertiesPanel()
        self._props.setMinimumWidth(240)
        self._props.setMaximumWidth(360)
        self._props.set_block(None)   # paint the placeholder up-front

        for pane in (self._project_tree, self._engineering_workspace, self._props):
            pane.setObjectName("engineering_pane")
            pane.setAttribute(Qt.WA_StyledBackground, True)

        # Splitter: left panel | canvas | properties
        self._splitter = QSplitter(Qt.Horizontal)
        self._splitter.setChildrenCollapsible(True)
        self._splitter.addWidget(self._left_splitter)
        self._splitter.addWidget(self._canvas_tabs)
        self._splitter.addWidget(self._props)
        self._splitter.setSizes([220, 780, 260])
        # Allow left panel + properties to collapse fully — but only when
        # the user explicitly drags the splitter handle. Selection no
        # longer touches visibility, so the canvas stays put.
        self._splitter.setCollapsible(0, True)   # left panel
        self._splitter.setCollapsible(1, False)  # canvas — never collapse
        self._splitter.setCollapsible(2, True)   # properties
        self._props_on_right = True
        root.addWidget(self._splitter, 1)

        # Runtime status bar (Azeo-style)
        from .widgets.runtime_status_bar import RuntimeStatusBar
        self._status_bar = RuntimeStatusBar()
        self._status_bar.hide()
        root.addWidget(self._status_bar)

        # Wire up toolbar signals
        self._toolbar.compileRequested.connect(self._compile)
        # Download and Go Online are distinct, as in Azeo: Download compiles
        # the diagram and installs it into the runtime (all three Download
        # affordances used to emit goOnlineRequested, so the two labels meant
        # exactly the same thing); Go Online attaches the live view to modules
        # that are already running, without recompiling them.
        self._toolbar.downloadRequested.connect(self._go_online)
        self._toolbar.goOnlineRequested.connect(self._attach_online_view)
        self._toolbar.goOfflineRequested.connect(self._go_offline)
        self._toolbar.saveRequested.connect(self._save)
        self._toolbar.loadRequested.connect(self._load)
        self._toolbar.newRequested.connect(self._new_strategy)
        self._toolbar.presetRequested.connect(self._load_preset)
        self._toolbar.zoomFitRequested.connect(
            lambda: self._active_view() and self._active_view().zoom_to_fit())
        self._toolbar.zoomInRequested.connect(
            lambda: self._active_view() and self._active_view().set_zoom(
                self._active_view()._zoom * 1.2))
        self._toolbar.zoomOutRequested.connect(
            lambda: self._active_view() and self._active_view().set_zoom(
                self._active_view()._zoom / 1.2))

        self._toolbar.addCommentRequested.connect(self._add_comment)
        self._toolbar.showValuesToggled.connect(self._toggle_show_values)
        self._toolbar.showExecOrderToggled.connect(self._toggle_exec_order)
        self._toolbar.searchRequested.connect(self._on_search)
        self._toolbar.compareRequested.connect(self._compare_strategies)
        self._toolbar.compareParametersRequested.connect(
            self._show_compare_parameters)
        self._toolbar.versionHistoryRequested.connect(self._version_history)
        self._toolbar.placeTemplateRequested.connect(self._place_template)
        self._toolbar.manageTemplatesRequested.connect(self._manage_templates)
        self._toolbar.controllerStatusRequested.connect(self._show_controller_status)
        self._toolbar.controllerPropertiesRequested.connect(
            self._show_controller_properties)
        self._toolbar.deploymentImpactRequested.connect(
            self._show_deployment_impact)
        self._toolbar.bulkEngineeringRequested.connect(
            self._show_bulk_engineering)
        self._toolbar.moduleClassesRequested.connect(
            self._show_module_classes)
        self._toolbar.redundancySimulatorRequested.connect(
            self._show_redundancy_simulator)

        # Alignment tools
        self._toolbar.alignRequested.connect(self._align_blocks)
        self._toolbar.distributeRequested.connect(self._distribute_blocks)
        self._toolbar.autoArrangeRequested.connect(self._auto_arrange)

        # Edit / deploy / monitoring actions. These ribbon buttons emitted
        # signals nobody listened to, so they were silent no-ops.
        self._toolbar.undoRequested.connect(self._undo)
        self._toolbar.redoRequested.connect(self._redo)
        self._toolbar.hideUnusedPinsRequested.connect(
            lambda: self._set_all_pins(hide_unused=True))
        self._toolbar.showAllPinsRequested.connect(
            lambda: self._set_all_pins(hide_unused=False))
        self._toolbar.saveAsRequested.connect(self._save_as)
        self._toolbar.selectAllRequested.connect(self._select_all)
        self._toolbar.deleteRequested.connect(self._delete_selection)
        self._toolbar.printRequested.connect(lambda: self._print_diagram(preview=False))
        self._toolbar.printPreviewRequested.connect(
            lambda: self._print_diagram(preview=True))
        self._toolbar.moduleReportRequested.connect(self._show_module_report)
        self._toolbar.modulePropertiesRequested.connect(
            lambda: self._show_module_info("properties"))
        self._toolbar.moduleParametersRequested.connect(
            lambda: self._show_module_info("parameters"))
        self._toolbar.crossReferenceRequested.connect(
            lambda: self._show_module_info("xref"))
        self._toolbar.namedSetsRequested.connect(
            lambda: self._show_module_info("namedsets"))
        self._toolbar.cutRequested.connect(self._cut)
        self._toolbar.copyRequested.connect(self._copy)
        self._toolbar.pasteRequested.connect(self._paste)
        self._toolbar.uploadRequested.connect(self._upload)
        self._toolbar.checkpointSaveRequested.connect(
            lambda: self._checkpoints("save"))
        self._toolbar.checkpointRestoreRequested.connect(
            lambda: self._checkpoints("restore"))
        self._toolbar.simulatorRequested.connect(self._controller_simulator)
        self._toolbar.faceplateRequested.connect(self._faceplate_for_selection)
        self._toolbar.debuggerRequested.connect(self._show_runtime_debugger)
        self._toolbar.debugPauseResumeRequested.connect(
            self._debug_pause_resume)
        self._toolbar.debugStepRequested.connect(self._debug_step_block)
        self._toolbar.debugRunScanRequested.connect(self._debug_run_scan)
        self._toolbar.monitoringRequested.connect(self._show_monitoring_tab)
        self._toolbar.tagDatabaseRequested.connect(self.show_tag_database)
        self._toolbar.diagnosticsRequested.connect(self._controller_diagnostics)
        self._toolbar.datalogConfigRequested.connect(self._datalog_config)

        # Properties panel signals
        self._props.configChanged.connect(self._on_config_changed)
        self._props.dockSideRequested.connect(self._move_props_panel)

        # Project tree signals
        self._project_tree.strategyLoadRequested.connect(self._load)
        self._project_tree.strategyDeleteRequested.connect(
            self._on_strategy_deleted)

        # The lower-left tools all describe the active module. Keep selection,
        # alarm engineering, and reusable-template placement on the same model
        # as the canvas rather than maintaining a parallel browser model.
        self._engineering_workspace.hierarchy.blockActivated.connect(
            self._activate_workspace_block)
        self._engineering_workspace.alarms.blockActivated.connect(
            self._activate_workspace_block)
        self._engineering_workspace.alarms.configEditRequested.connect(
            self._edit_alarm_configuration)
        self._palette.templatePlacementRequested.connect(self._place_template)

        # Alarm state is live data, but repainting its small table at the scan
        # rate adds no engineering value. One update per second is responsive
        # and remains modest even for a large module.
        self._engineering_live_timer = QTimer(self)
        self._engineering_live_timer.setInterval(1000)
        self._engineering_live_timer.timeout.connect(
            self._engineering_workspace.refresh_live)
        self._engineering_live_timer.start()

        command_shortcut = QShortcut(QKeySequence("Ctrl+Shift+P"), self)
        command_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        command_shortcut.activated.connect(self._show_command_palette)
        self._shortcuts.append(command_shortcut)
        navigator_shortcut = QShortcut(QKeySequence("Ctrl+Alt+M"), self)
        navigator_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        navigator_shortcut.activated.connect(
            lambda: self._active_canvas() and self._active_canvas().show_navigator())
        self._shortcuts.append(navigator_shortcut)

    def _palette_commands(self):
        """Build commands against the current canvas, never a stale tab."""
        from .dialogs.command_palette import PaletteCommand
        from azeo_control_trainer.core.strategy.model.block_registry import registry

        scene = self._active_scene()
        view = self._active_view()
        online = bool(scene and scene.structure_locked)
        commands = [
            PaletteCommand("Compile and Validate", "Module", self._compile,
                           "check problems errors"),
            PaletteCommand("Fit Diagram", "View",
                           lambda: self._active_view() and self._active_view().zoom_to_fit(),
                           "zoom all"),
            PaletteCommand("Auto Arrange", "Layout", self._auto_arrange,
                           "layout tidy", enabled=not online),
            PaletteCommand("Create Composite from Selection", "Refactor",
                           lambda: self._active_scene()
                           and self._active_scene().create_composite_from_selection(),
                           "subsystem group", enabled=not online),
            PaletteCommand("Guided Control Loop Wizard", "Insert",
                           self._show_control_loop_wizard,
                           "pid cascade override constraint generate pattern",
                           enabled=not online),
            PaletteCommand("Add Comment", "Annotations", self._add_comment,
                           "note documentation"),
            PaletteCommand("Show Problems", "View",
                           lambda: self._engineering_workspace.setCurrentWidget(
                               self._engineering_workspace.problems),
                           "validation errors warnings"),
            PaletteCommand("Open Diagram Navigator", "View",
                           lambda: self._active_canvas()
                           and self._active_canvas().show_navigator(),
                           "minimap bookmarks regions large diagram"),
            PaletteCommand("Go Online", "Runtime", self._attach_online_view,
                           "connect monitor", enabled=not online),
            PaletteCommand("Go Offline", "Runtime", self._go_offline,
                           "disconnect stop editing", enabled=online),
            PaletteCommand("Review Deployment Diff and Impact", "Deploy",
                           self._show_deployment_impact,
                           "download compare dependency affected restart risk"),
            PaletteCommand("Bulk Module Engineering", "Engineering",
                           self._show_bulk_engineering,
                           "generate template csv mass edit preview"),
            PaletteCommand("Control Module Classes", "Engineering",
                           self._show_module_classes,
                           "class linked instance inherit revision override deviation"),
            PaletteCommand("Redundancy and Communication Simulator", "Runtime",
                           self._show_redundancy_simulator,
                           "failover standby peer link fault outage"),
        ]
        if scene is not None and view is not None:
            center = view.mapToScene(view.viewport().rect().center())
            for block_type, block_cls in sorted(registry.all_types().items()):
                if getattr(block_cls, "is_special_palette_item", False):
                    continue
                label = str(getattr(block_cls, "display_name", block_type))
                category = str(getattr(block_cls, "category").value)
                commands.append(PaletteCommand(
                    f"Insert {label} [{block_type}]",
                    f"Blocks · {category}",
                    lambda bt=block_type, where=center: (
                        self._active_scene()
                        and self._active_scene().add_block_by_type(bt, where)
                    ),
                    f"place add {block_type} {category}",
                    enabled=not online,
                ))
        return commands

    def _show_control_loop_wizard(self):
        scene = self._active_scene()
        view = self._active_view()
        if scene is None or view is None or scene.structure_locked:
            return None
        from .dialogs.control_loop_wizard import ControlLoopWizard

        origin = view.mapToScene(view.viewport().rect().center())
        dialog = ControlLoopWizard(scene, origin, parent=self)
        self._command_dialogs["loop_wizard"] = dialog
        dialog.finished.connect(
            lambda _result: self._command_dialogs.pop("loop_wizard", None))
        if not is_headless():
            dialog.show()
        return dialog

    def _show_command_palette(self):
        if is_headless():
            return None
        from .dialogs.command_palette import CommandPaletteDialog

        if self._command_palette is not None:
            self._command_palette.close()
        dialog = CommandPaletteDialog(self._palette_commands(), parent=self)
        self._command_palette = dialog
        dialog.finished.connect(
            lambda _result, current=dialog: setattr(self, "_command_palette", None)
            if self._command_palette is current else None)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        return dialog

    # ------------------------------------------------------------ canvas helpers
    def _active_canvas(self) -> StrategyCanvas | None:
        """Return the currently active StrategyCanvas, or None."""
        w = self._canvas_tabs.currentWidget()
        return w if isinstance(w, StrategyCanvas) else None

    def _active_scene(self) -> StrategyScene | None:
        c = self._active_canvas()
        return c.scene if c else None

    def _active_view(self) -> StrategyView | None:
        c = self._active_canvas()
        return c.view if c else None

    def _create_canvas(self, label: str = "Untitled",
                       file_path: str | None = None, *,
                       defer_visuals: bool = False,
                       activate: bool = True) -> StrategyCanvas:
        """Create a new StrategyCanvas, add it as a tab, and wire signals."""
        canvas = StrategyCanvas(defer_visuals=defer_visuals)
        canvas.file_path = file_path

        idx = self._canvas_tabs.addTab(canvas, label)
        if activate:
            self._canvas_tabs.setCurrentIndex(idx)

        # Wire scene signals
        self._connect_canvas_signals(canvas)

        # A tab is inserted before its graph is loaded.  Requesting, rather
        # than immediately performing, the first frame lets Qt finish laying
        # out the splitter and tab viewport first; otherwise the diagram can
        # open against one edge or at a scale calculated from a tiny viewport.
        if canvas._view is not None:
            canvas.view.request_initial_frame()

        return canvas

    def _connect_canvas_signals(self, canvas: StrategyCanvas):
        """Connect a canvas's scene/view signals to shared handlers."""
        scene = canvas.scene
        self._props._scene = scene

        canvas.viewReady.connect(self._on_canvas_view_ready)
        if canvas._view is not None:
            self._connect_view_signals(canvas._view)

        scene.structureEditBlocked.connect(self._on_structure_edit_blocked)
        scene.connectionRefused.connect(self._on_connection_refused)
        scene.sceneNotice.connect(self._on_scene_notice)
        scene.selectionUpdated.connect(self._on_selection)
        scene.selectionChanged.connect(self._on_scene_selection_changed)
        scene.blockRemoved.connect(self._on_block_removed)
        scene.compileRequested.connect(self._compile)
        scene.strategyModified.connect(self._on_modified)
        scene.compositeDrillDownRequested.connect(
            lambda bid, s=scene: self._open_composite_in_tab(s, bid))
        scene.saveTemplateRequested.connect(self._save_selection_as_template)

        # Block item signals (connected per-block as they are added)
        scene.blockAdded.connect(lambda bid, s=scene: self._connect_block_signals(s, bid))

        # Record block use in palette for "Recently Used" section
        scene.blockAdded.connect(self._on_block_added_to_scene)

    def _connect_view_signals(self, view):
        view.searchActivated.connect(self._toolbar.focus_search)
        view.zoomChanged.connect(self._toolbar.update_zoom)
        view.templateDropped.connect(self._place_template)

    def _on_canvas_view_ready(self, view):
        self._connect_view_signals(view)
        self._connect_all_block_signals(view.scene())

    def _open_composite_in_tab(self, parent_scene, composite_id: str) -> None:
        """Open a composite block's inner graph in a new canvas tab.

        If a tab is already showing this composite's interior we just
        switch to it. Otherwise a fresh canvas is created and the
        composite's ``_inner_graph`` is loaded into its scene. Edits
        made in the tab mutate the same StrategyGraph object the
        composite holds — so changes propagate live.
        """
        from azeo_control_trainer.core.strategy.blocks.composite_blocks import CompositeBlock
        comp = parent_scene._graph.blocks.get(composite_id)
        if not isinstance(comp, CompositeBlock):
            return

        marker = f"composite://{composite_id}"
        # Re-focus an already-open interior tab
        for i in range(self._canvas_tabs.count()):
            w = self._canvas_tabs.widget(i)
            if isinstance(w, StrategyCanvas) and getattr(w, "_composite_marker",
                                                          None) == marker:
                self._canvas_tabs.setCurrentIndex(i)
                return

        label = f"[{comp.instance_name}]"
        canvas = self._create_canvas(label=label, file_path=None)
        canvas._composite_marker = marker
        canvas.scene.load_graph(comp.inner_graph)
        canvas._composite_parent_scene = parent_scene
        parent_canvas = next((
            self._canvas_tabs.widget(i)
            for i in range(self._canvas_tabs.count())
            if isinstance(self._canvas_tabs.widget(i), StrategyCanvas)
            and self._canvas_tabs.widget(i).scene is parent_scene
        ), None)

        def return_to_parent():
            if parent_canvas is not None:
                index = self._canvas_tabs.indexOf(parent_canvas)
                if index >= 0:
                    self._canvas_tabs.setCurrentIndex(index)

        def sync_boundary():
            comp._rebuild_terminals()
            outer_item = parent_scene.get_block_item(comp.id)
            if outer_item is not None:
                outer_item.rebuild_terminals()
            parent_scene.notify_modified()

        def edit_ports():
            from .dialogs.composite_ports import CompositePortsDialog

            existing = getattr(canvas, "_ports_dialog", None)
            if existing is not None:
                existing.show()
                existing.raise_()
                return existing
            dialog = CompositePortsDialog(
                comp, canvas.scene, parent_scene, parent=canvas)
            canvas._ports_dialog = dialog
            dialog.finished.connect(
                lambda _result: setattr(canvas, "_ports_dialog", None))
            if not is_headless():
                dialog.show()
            return dialog

        canvas.scene.strategyModified.connect(sync_boundary)
        canvas.set_composite_breadcrumb(comp.instance_name, return_to_parent, edit_ports)
        self._fit_canvas(canvas)
        # Composite interiors aren't standalone strategies — gray-out the
        # title to make that visible.
        idx = self._canvas_tabs.indexOf(canvas)
        if idx >= 0:
            self._canvas_tabs.setTabToolTip(
                idx,
                f"Interior of composite '{comp.instance_name}' — "
                f"edits live-mutate the parent")
        log.info("Opened composite '%s' interior in new tab", comp.instance_name)

    def _find_tab_by_path(self, path: str) -> int:
        """Return tab index for an already-open strategy file, or -1."""
        norm = str(Path(path).resolve())
        for i in range(self._canvas_tabs.count()):
            w = self._canvas_tabs.widget(i)
            if isinstance(w, StrategyCanvas) and w.file_path:
                if str(Path(w.file_path).resolve()) == norm:
                    return i
        return -1

    def _canvas_label(self, canvas: StrategyCanvas) -> str:
        """Return a module's display label without runtime/edit indicators."""
        index = self._canvas_tabs.indexOf(canvas)
        if index < 0:
            return "Module"
        label = self._canvas_tabs.tabText(index)
        if label.startswith("\u25cf "):
            label = label[2:]
        if label.endswith(" *"):
            label = label[:-2]
        return label

    def _build_canvas_tab_context_menu(self, canvas: StrategyCanvas):
        """Build the state-aware menu for the module tab that was clicked."""
        if self._canvas_tabs.indexOf(canvas) < 0:
            return None

        online = bool(canvas.runtime and canvas.runtime.is_online)
        state = "Online" if online else "Offline"
        menu = studio_menu(
            f"MODULE  {escape(self._canvas_label(canvas))}",
            f"{state} control module",
            self._canvas_tabs.tabBar(),
        )

        lifecycle = menu.addAction("Go Offline" if online else "Go Online")
        lifecycle.setEnabled(self._canvas_lifecycle_action_enabled(canvas))
        lifecycle.triggered.connect(
            lambda _checked=False, target=canvas:
            self._toggle_canvas_online(target))

        menu.addSeparator()
        close_module = menu.addAction("Close Module\tCtrl+F4")
        close_module.triggered.connect(
            lambda _checked=False, target=canvas:
            self._close_canvas_tabs([target]))

        other_canvases = self._canvas_tabs.count() - 1
        close_others = menu.addAction("Close Other Modules")
        close_others.setEnabled(other_canvases > 0)
        close_others.triggered.connect(
            lambda _checked=False, target=canvas:
            self._close_other_canvas_tabs(target))

        index = self._canvas_tabs.indexOf(canvas)
        close_right = menu.addAction("Close Modules to the Right")
        close_right.setEnabled(0 <= index < self._canvas_tabs.count() - 1)
        close_right.triggered.connect(
            lambda _checked=False, target=canvas:
            self._close_canvas_tabs_to_right(target))

        close_all = menu.addAction("Close All Modules")
        close_all.triggered.connect(
            lambda _checked=False: self._close_all_canvas_tabs())
        return menu

    def _show_canvas_tab_context_menu(self, position) -> None:
        """Show a non-modal context menu for the tab under ``position``."""
        tab_bar = self._canvas_tabs.tabBar()
        index = tab_bar.tabAt(position)
        canvas = self._canvas_tabs.widget(index) if index >= 0 else None
        if not isinstance(canvas, StrategyCanvas):
            return

        menu = self._build_canvas_tab_context_menu(canvas)
        if menu is None:
            return
        # popup() avoids a nested modal event loop. Retaining the Python
        # wrapper also prevents Qt ownership from invalidating the live menu.
        retain_menu(self, menu, "_canvas_tab_context_menu")
        menu.popup(tab_bar.mapToGlobal(position))

    def _canvas_lifecycle_action_enabled(self, canvas: StrategyCanvas) -> bool:
        if self._canvas_tabs.indexOf(canvas) < 0:
            return False
        controller = getattr(self._store, "controller", None)
        if controller is not None and getattr(controller, "keylock", False):
            return False
        if canvas.runtime and canvas.runtime.is_online:
            return True
        return self._store is not None and bool(canvas.scene.graph.blocks)

    def _toggle_canvas_online(self, canvas: StrategyCanvas) -> bool:
        """Apply the lifecycle action to the clicked module, not the active one."""
        index = self._canvas_tabs.indexOf(canvas)
        if index < 0:
            return False
        online = bool(canvas.runtime and canvas.runtime.is_online)
        if self._keylock_refuses("decommission" if online else "download"):
            return False

        if online:
            self.take_canvas_offline_by_index(index)
            return True

        if self._store is None or not canvas.scene.graph.blocks:
            return False

        active = [
            self._canvas_label(other)
            for i in range(self._canvas_tabs.count())
            if isinstance((other := self._canvas_tabs.widget(i)), StrategyCanvas)
            and other is not canvas
            and other.runtime
            and other.runtime.is_online
        ]
        if active:
            reply = QMessageBox.question(
                self, "Modules Already Running",
                f"{len(active)} module(s) are already online:\n"
                f"  {', '.join(active)}\n\n"
                "Adding this module will add it to the running scan cycle.\n\n"
                "Proceed?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply != QMessageBox.Yes:
                return False

        if self.go_online_single(index):
            return True
        QMessageBox.warning(
            self, "Download Failed",
            f"Failed to bring module '{self._canvas_label(canvas)}' online.\n"
            "Check the log for compilation errors.")
        return False

    def _confirm_canvas_tabs_close(self, canvases: list[StrategyCanvas]) -> bool:
        """Resolve one save/discard/cancel decision for a close operation."""
        dirty = [canvas for canvas in canvases if canvas.dirty]
        if not dirty:
            return True

        labels = [self._canvas_label(canvas) for canvas in dirty]
        if is_headless():
            log.warning(
                "Closing %d unsaved module tab(s) headlessly without saving: %s",
                len(labels), labels)
            return True

        names = "\n".join(f"  \u2022 {name}" for name in labels[:12])
        more = f"\n  \u2026 and {len(labels) - 12} more" if len(labels) > 12 else ""
        choice = QMessageBox.question(
            self, "Unsaved changes",
            f"{len(labels)} module(s) have unsaved changes:\n\n"
            f"{names}{more}\n\nSave them before closing?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if choice == QMessageBox.Cancel:
            return False
        if choice == QMessageBox.Save:
            for canvas in dirty:
                self._save(canvas)
            failed = [self._canvas_label(canvas) for canvas in dirty
                      if canvas.dirty]
            if failed:
                QMessageBox.warning(
                    self, "Modules Not Closed",
                    "These modules could not be saved and remain open:\n\n"
                    + "\n".join(f"  \u2022 {name}" for name in failed))
                return False
        return choice in (QMessageBox.Save, QMessageBox.Discard)

    def _dispose_canvas(self, canvas: StrategyCanvas) -> None:
        """Remove one already-confirmed canvas and release its runtime UI."""
        index = self._canvas_tabs.indexOf(canvas)
        if index < 0:
            return

        # Go offline if this canvas was online
        runtime = getattr(canvas, "runtime", None)
        if runtime and runtime.is_online:
            self._take_canvas_offline(canvas)
            log.info("Canvas went offline on tab close")

        debugger = getattr(canvas, "_runtime_debugger_dialog", None)
        if debugger is not None:
            try:
                debugger.close()
            except RuntimeError:
                pass

        self._canvas_tabs.removeTab(index)
        canvas.deleteLater()

    def _close_canvas_tabs(self, canvases: list[StrategyCanvas]) -> bool:
        """Close exactly ``canvases`` after one unsaved-changes decision."""
        unique: list[StrategyCanvas] = []
        seen: set[int] = set()
        for canvas in canvases:
            if (isinstance(canvas, StrategyCanvas)
                    and self._canvas_tabs.indexOf(canvas) >= 0
                    and id(canvas) not in seen):
                unique.append(canvas)
                seen.add(id(canvas))
        if not unique:
            return False
        if not self._confirm_canvas_tabs_close(unique):
            return False

        # Highest index first keeps the requested scope stable as tabs vanish.
        unique.sort(key=self._canvas_tabs.indexOf, reverse=True)
        for canvas in unique:
            self._dispose_canvas(canvas)
        self._sync_legacy_refs()
        return True

    def _close_canvas_tab(self, index: int) -> bool:
        """Close one tab through the shared save/offline/cleanup path."""
        canvas = self._canvas_tabs.widget(index)
        if not isinstance(canvas, StrategyCanvas):
            return False
        return self._close_canvas_tabs([canvas])

    def _close_other_canvas_tabs(self, target: StrategyCanvas) -> bool:
        canvases = [
            self._canvas_tabs.widget(i)
            for i in range(self._canvas_tabs.count())
            if self._canvas_tabs.widget(i) is not target
        ]
        return self._close_canvas_tabs(canvases)

    def _close_canvas_tabs_to_right(self, target: StrategyCanvas) -> bool:
        index = self._canvas_tabs.indexOf(target)
        if index < 0:
            return False
        canvases = [self._canvas_tabs.widget(i)
                    for i in range(index + 1, self._canvas_tabs.count())]
        return self._close_canvas_tabs(canvases)

    def _close_all_canvas_tabs(self) -> bool:
        canvases = [self._canvas_tabs.widget(i)
                    for i in range(self._canvas_tabs.count())]
        return self._close_canvas_tabs(canvases)

    def _on_canvas_tab_changed(self, index: int):
        """Handle switching between canvas tabs."""
        canvas = self._active_canvas()
        if canvas is None:
            self._props.set_block(None)
            self._engineering_workspace.set_scene(None)
            self._sync_legacy_refs()
            return

        # Realize only the selected module; its runtime and graph already exist.
        canvas.view

        # Update properties panel to the active canvas's selection.
        # Visibility is NOT toggled — the panel is a permanent fixture so
        # selection doesn't force the canvas to reflow.
        self._props._scene = canvas.scene
        selected = canvas.scene.get_selected_block()
        self._props.set_block(selected)
        self._engineering_workspace.set_scene(canvas.scene)
        self._engineering_workspace.select_block(
            selected.id if selected is not None else None)

        # Module controls describe this canvas; background runtimes remain
        # the executive's concern and do not leak into the active-tab UI.
        self._sync_legacy_refs()
        canvas.view.ensure_initial_frame()
        self._refresh_canvas_live(canvas)

    def _sync_legacy_refs(self):
        """Keep legacy self._runtime / self._bridge in sync with active tab."""
        canvas = self._active_canvas()
        if canvas:
            self._runtime = canvas.runtime
            self._bridge = canvas.bridge
        else:
            self._runtime = None
            self._bridge = None
        # Every download / go-offline path already funnels through here, so
        # this is the one place the scan timer has to be told to start or
        # stop.
        self._sync_executive()
        self._sync_active_canvas_ui()

    def _sync_active_canvas_ui(self) -> None:
        """Synchronise module commands and status to the selected canvas.

        Background modules deliberately keep scanning.  They must not make an
        offline active module look downloaded, lock its properties, or enable
        a debugger that would operate on a different module.
        """
        canvas = self._active_canvas()
        runtime = getattr(canvas, "runtime", None) if canvas else None
        online = bool(runtime and runtime.is_online)
        self._active_ui_online = online
        self._toolbar.set_online_state(online)
        self._props.set_online(online)
        if online:
            self._toolbar.update_scan_time(runtime.last_scan_ms)
            self._status_bar.update_status(runtime.get_status())
            self._status_bar.show()
        else:
            self._status_bar.set_offline()
            self._status_bar.hide()
        self._refresh_debug_toolbar()

    # ------------------------------------------------------------ FBD debugger
    def _active_debug_runtime(self):
        canvas = self._active_canvas()
        runtime = getattr(canvas, "runtime", None) if canvas else None
        return runtime if runtime and runtime.is_online else None

    def _refresh_debug_toolbar(self) -> None:
        """Reflect the active module, not an unrelated online background tab."""
        runtime = self._active_debug_runtime()
        if runtime is None:
            self._toolbar.set_debug_state(online=False, paused=False)
            return
        state = runtime.get_debug_status()
        self._toolbar.set_debug_state(
            online=True,
            paused=state["paused"],
            scan_active=state["scan_active"],
        )

    def _refresh_debug_surface(self, canvas: StrategyCanvas) -> None:
        self._refresh_debug_toolbar()
        for item in canvas.scene._block_items.values():
            item.update()
        dialog = getattr(canvas, "_runtime_debugger_dialog", None)
        if dialog is not None:
            try:
                dialog.refresh()
            except RuntimeError:
                canvas._runtime_debugger_dialog = None

    @staticmethod
    def _carry_debug_breakpoints(previous, current) -> None:
        """Carry engineering breakpoints across a compile/download replacement.

        Breakpoints are commissioning state, not process configuration, so
        they do not belong in the strategy JSON.  The canvas nevertheless
        retains them across a re-download when the block UUID still exists.
        """
        for block_id in getattr(previous, "debug_breakpoints", ()):
            try:
                current.set_debug_breakpoint(block_id)
            except KeyError:
                # Deleted blocks cannot retain a meaningful breakpoint.
                continue

    def _show_runtime_debugger(self):
        """Open one modeless debugger bound to the active canvas runtime."""
        import weakref

        canvas = self._active_canvas()
        runtime = self._active_debug_runtime()
        if canvas is None or runtime is None:
            if not is_headless():
                QMessageBox.information(
                    self, "Online FBD Debugger",
                    "Download the active module before opening its debugger.")
            return None
        existing = getattr(canvas, "_runtime_debugger_dialog", None)
        if existing is not None:
            try:
                existing.refresh()
                existing.show()
                existing.raise_()
                existing.activateWindow()
                return existing
            except RuntimeError:
                canvas._runtime_debugger_dialog = None

        from .dialogs.runtime_debugger import RuntimeDebuggerDialog
        dialog = RuntimeDebuggerDialog(canvas, parent=self)
        canvas._runtime_debugger_dialog = dialog
        canvas_ref = weakref.ref(canvas)

        def clear_dialog_reference(*_args):
            owner = canvas_ref()
            if owner is not None:
                owner._runtime_debugger_dialog = None

        dialog.destroyed.connect(clear_dialog_reference)
        if is_headless():
            dialog.setAttribute(Qt.WA_DontShowOnScreen, True)
        dialog.show()
        return dialog

    def _debug_pause_resume(self) -> bool:
        runtime = self._active_debug_runtime()
        canvas = self._active_canvas()
        if runtime is None or canvas is None:
            return False
        changed = (runtime.debug_resume() if runtime.is_debug_paused
                   else runtime.debug_pause())
        self._refresh_debug_surface(canvas)
        return changed

    def _debug_step_block(self) -> str | None:
        runtime = self._active_debug_runtime()
        canvas = self._active_canvas()
        if runtime is None or canvas is None or not runtime.is_debug_paused:
            return None
        block_id = runtime.debug_step_block()
        self._refresh_debug_surface(canvas)
        return block_id

    def _debug_run_scan(self) -> int:
        runtime = self._active_debug_runtime()
        canvas = self._active_canvas()
        if runtime is None or canvas is None or not runtime.is_debug_paused:
            return 0
        attempted = runtime.debug_run_scan()
        self._refresh_debug_surface(canvas)
        return attempted

    def _move_props_panel(self, side: str):
        """Move properties panel to left or right side of the splitter."""
        if side == "left" and self._props_on_right:
            self._splitter.insertWidget(0, self._props)
            self._splitter.setSizes([220, 220, 780])
            self._props_on_right = False
        elif side == "right" and not self._props_on_right:
            self._splitter.addWidget(self._props)
            self._splitter.setSizes([220, 780, 220])
            self._props_on_right = True

    # ------------------------------------------------------------ selection
    def _on_selection(self, block_id: str):
        # Panel stays visible regardless of selection — only its content changes.
        scene = self._active_scene()
        if scene is None or not scene.is_alive():
            return
        block = scene.graph.blocks.get(block_id)
        self._props.set_block(block)
        self._engineering_workspace.select_block(block_id if block else None)

    def _on_scene_selection_changed(self):
        """When nothing is selected, paint the panel's placeholder.
        Visibility is NOT toggled — the panel is a permanent fixture so
        the canvas doesn't reflow on selection changes."""
        scene = self._active_scene()
        if scene is None or not scene.is_alive():
            return
        from .items.block_item import BlockItem

        blocks = [item.block for item in scene.selectedItems()
                  if isinstance(item, BlockItem)]
        if len(blocks) > 1:
            self._props.set_blocks(blocks)
            self._engineering_workspace.select_block(None)
        elif not blocks:
            self._props.set_block(None)
            self._engineering_workspace.select_block(None)

    def _on_block_removed(self, block_id: str):
        """Clear properties if the removed block was displayed."""
        if self._props._block and self._props._block.id == block_id:
            self._props.set_block(None)
        self._engineering_workspace.refresh_structure()

    def _on_config_changed(self, block_id: str):
        scene = self._active_scene()
        if scene is None:
            return
        item = scene.get_block_item(block_id)
        if item:
            item.refresh()
        # A parameter edit is a real edit: mark the module unsaved, and
        # out-of-sync with what was downloaded. Retuning a live loop used to
        # leave no trace at all — no dirty flag, no "*" on the tab — so the
        # running controller and the saved module could differ silently.
        self._on_modified()

    def _activate_workspace_block(self, block_id: str) -> bool:
        """Select and reveal a hierarchy/alarm row's block on the canvas."""
        canvas = self._active_canvas()
        if canvas is None:
            return False
        item = canvas.scene.get_block_item(str(block_id))
        if item is None:
            return False
        canvas.scene.clearSelection()
        item.setSelected(True)
        item.setFocus(Qt.OtherFocusReason)
        canvas.view.centerOn(item)
        self._props.set_block(item.block)
        self._engineering_workspace.select_block(item.block.id)
        canvas.view.setFocus(Qt.OtherFocusReason)
        return True

    def _edit_alarm_configuration(
        self,
        block_id: str,
        config_key: str,
        value,
    ) -> bool:
        """Apply an alarm edit through normalization and the undo stack."""
        scene = self._active_scene()
        block = scene.graph.blocks.get(str(block_id)) if scene else None
        if block is None:
            return False

        old_config = dict(block.config.params)
        candidate = dict(old_config)
        requested_key = str(config_key)
        schema = dict(block.get_config_schema() or {})
        schema_casefold = {str(key).upper(): str(key) for key in schema}
        canonical_key = schema_casefold.get(requested_key.upper(), requested_key)
        for alias, target in (getattr(block, "config_aliases", {}) or {}).items():
            if str(alias).upper() == requested_key.upper():
                canonical_key = schema_casefold.get(
                    str(target).upper(), str(target))
                break
        candidate[canonical_key] = value

        # Normalize the candidate through the block's alias contract without
        # applying temporary values to the running algorithm. The undo command
        # performs the one real apply and preserves the exact before snapshot.
        live_config = block.config.params
        try:
            block.config.params = candidate
            block.normalize_config()
            normalized = dict(block.config.params)
        finally:
            block.config.params = live_config
        if normalized == old_config:
            return False

        from .undo import ChangeConfigCommand
        scene.undo_stack.push(ChangeConfigCommand(
            scene,
            block.id,
            old_config,
            normalized,
            description=f"Change {block.instance_name}.{config_key}",
        ))
        self._engineering_workspace.refresh_structure()
        return True

    def _on_modified(self):
        """Mark active canvas as modified since last download."""
        canvas = self._active_canvas()
        if canvas is None:
            return
        canvas.dirty = True
        # Only meaningful if module was previously downloaded
        if canvas.downloaded_at is not None and not canvas.modified_since_download:
            canvas.modified_since_download = True
            # Add modified indicator (* suffix) to tab text
            idx = self._canvas_tabs.indexOf(canvas)
            if idx >= 0:
                label = self._canvas_tabs.tabText(idx)
                if not label.endswith(" *"):
                    self._canvas_tabs.setTabText(idx, label + " *")
        self._engineering_workspace.refresh_structure()

    # ------------------------------------------------------------ comments
    def _add_comment(self):
        """Add a comment at the center of the current viewport."""
        view = self._active_view()
        scene = self._active_scene()
        if view and scene:
            center = view.mapToScene(view.viewport().rect().center())
            scene.add_comment("Comment", center)

    # ------------------------------------------------------------ wire values
    def _toggle_show_values(self, checked: bool):
        """Toggle live value display on wires."""
        scene = self._active_scene()
        if scene:
            scene.set_show_wire_values(checked)

    # ------------------------------------------------------------ exec order
    def _toggle_exec_order(self, checked: bool):
        """Toggle execution order badge display on blocks."""
        scene = self._active_scene()
        if scene:
            scene.set_show_exec_order(checked)

    # -------------------------------------------------------- alignment tools
    def _align_blocks(self, direction: str):
        """Align selected blocks (left/right/top/bottom/center_h/center_v)."""
        scene = self._active_scene()
        if scene:
            scene.align_selected(direction)

    def _distribute_blocks(self, axis: str):
        """Distribute selected blocks evenly (horizontal/vertical)."""
        scene = self._active_scene()
        if scene:
            scene.distribute_selected(axis)

    def _auto_arrange(self):
        """Auto-arrange blocks using topological layout."""
        scene = self._active_scene()
        if scene:
            scene.auto_arrange()

    # ------------------------------------------------- block signal helpers
    def _connect_all_block_signals(self, scene):
        """Connect signals on all existing block items in the scene."""
        for bid in list(scene._block_items.keys()):
            self._connect_block_signals(scene, bid)
        if scene is self._active_scene():
            self._engineering_workspace.set_graph(scene.graph)

    def _connect_block_signals(self, scene, block_id: str):
        """Connect a block item's Azeo-style signals."""
        item = scene.get_block_item(block_id)
        if item is None:
            return
        if hasattr(item, 'faceplateRequested'):
            item.faceplateRequested.connect(self._open_pvm_block_faceplate)
        if hasattr(item, 'trendRequested'):
            item.trendRequested.connect(self._open_block_trend)
        if hasattr(item, 'bypassToggled'):
            item.bypassToggled.connect(self._on_block_bypass)

    # ---------------------------------------------------- tag database
    def open_graphs(self) -> list:
        """The graph of every open module tab, newest tab last.

        The tag browser derives its namespace from these rather than from the
        files, so what it shows is the configuration actually loaded —
        including edits that have not been saved yet.
        """
        graphs = []
        for i in range(self._canvas_tabs.count()):
            canvas = self._canvas_tabs.widget(i)
            if not isinstance(canvas, StrategyCanvas):
                continue
            graph = canvas.scene.graph
            if graph.blocks and graph not in graphs:
                graphs.append(graph)
        return graphs

    def show_tag_database(self):
        """Open (or raise) the tag database browser."""
        from azeo_control_trainer.core.presentation.tagdb_browser import TagDatabaseDialog

        if self._tagdb_dialog is not None:
            self._tagdb_dialog.browser.reload()
            self._tagdb_dialog.show()
            if not is_headless():
                self._tagdb_dialog.raise_()
                self._tagdb_dialog.activateWindow()
            return self._tagdb_dialog

        area = getattr(self._plugin, "strategy_subdir", "") or ""
        dlg = TagDatabaseDialog(self.open_graphs, self._store, area, parent=self)
        dlg.finished.connect(self._on_tagdb_closed)
        self._tagdb_dialog = dlg
        if is_headless():
            # Preserve showEvent/timer semantics and child visibility while
            # avoiding another native offscreen top-level surface.
            dlg.setAttribute(Qt.WA_DontShowOnScreen, True)
        dlg.show()
        return dlg

    def _on_tagdb_closed(self, _result=0):
        # The dialog is parented to this tab so it follows the tab's lifetime;
        # a closed one must be released or every open/close cycle keeps a
        # whole browser (timers, models, tables) alive under the tab.
        dialog, self._tagdb_dialog = self._tagdb_dialog, None
        if dialog is not None:
            dialog.deleteLater()

    # ------------------------------------------------- controller executive
    def controller_executive(self):
        """The timer that scans downloaded modules.

        A host application drives its own scan loop; standalone nothing did,
        so a module reported ONLINE and never executed. The tab owns one only
        when the host has not already provided it.
        """
        host = self.window()
        theirs = getattr(host, "_executive", None)
        if theirs is not None and theirs is not self._executive:
            return theirs
        if self._executive is None:
            from .executive import ControllerExecutive
            self._executive = ControllerExecutive(self._store, parent=self)
        return self._executive

    def _sync_executive(self):
        """Run the executive while anything is online; stop it when nothing is."""
        ex = self.controller_executive()
        if ex is None:
            return
        if ex.online_runtimes():
            ex.start()
        else:
            ex.stop()

    # ------------------------------------------------------- faceplates
    def faceplate_manager(self):
        """The :class:`FaceplateManager` this designer opens faceplates through.

        Hosted inside the full application the host owns one, and every
        faceplate in the process comes from it. Standalone there is no host —
        the designer owns its own. Without that, ``Open Faceplate...`` walked
        up to a main window that does not exist, found a delegating stub, and
        silently did nothing even for the block types that have a faceplate.
        """
        host = self.window()
        mgr = getattr(host, "_faceplate_mgr", None)
        if mgr is not None:
            return mgr
        if self._faceplates is None:
            from azeo_control_trainer.azeo_control_designer.faceplates.faceplate_manager import (
                FaceplateManager,
            )
            self._faceplates = FaceplateManager(self._store)
            log.info("Designer owns its faceplate manager (no host window)")
        return self._faceplates

    def owns_faceplate_manager(self) -> bool:
        """True when this tab created the manager and must refresh/close it."""
        return (self._faceplates is not None
                and getattr(self.window(), "_faceplate_mgr", None) is None)

    @staticmethod
    def _field_tag(block) -> str:
        """The store tag an I/O block is wired to, or its name as a fallback."""
        return (str(block.config.params.get("tag", "") or "").strip()
                or block.instance_name)

    def _open_block_faceplate(self, block_id: str):
        """Open the faceplate registered for this block's type.

        Faceplates key on two different things and the split is not cosmetic:
        an ``AI``/``AO``/valve faceplate follows the *field tag* the block is
        wired to, while a controller or device faceplate follows the *block*
        itself, because its state lives on terminals rather than in the store.
        """
        scene = self._active_scene()
        if scene is None:
            return
        block = scene.graph.blocks.get(block_id)
        if block is None:
            return
        mgr = self.faceplate_manager()
        if mgr is None:
            return

        bt = block.block_type
        tag = block.instance_name
        module = scene.graph.name or ""
        desc = str(block.config.params.get("description", "")
                   or block.config.params.get("label", "") or "")
        try:
            if bt == "PID":
                self._register_pid(mgr, block)
                mgr.open_faceplate(tag, near_widget=self)
            elif bt == "AI":
                mgr.open_ai_faceplate(self._field_tag(block))
            elif bt == "AO":
                mgr.open_ao_faceplate(self._field_tag(block), description=desc)
            elif bt == "VLVCTL":
                mgr.open_valve_faceplate(self._field_tag(block), description=desc)
            elif bt in ("DMC_CONTROLLER", "APC_CONTROL"):
                mgr.open_dmc_faceplate(parent=self)
            elif bt in ("MOTOR_INTERLOCK", "DEVCTL"):
                mgr.open_device_faceplate(
                    block, graph=scene.graph, module=module, parent=self)
            else:
                log.info("No faceplate is registered for %s ('%s')", bt, tag)
                if not is_headless():
                    QMessageBox.information(
                        self, "Faceplate",
                        f"{bt} blocks do not have a faceplate yet.\n\n"
                        f"Open the block's Properties or the watch window to "
                        f"see {tag}'s parameters.")
        except Exception as exc:                            # noqa: BLE001
            log.exception("Opening the %s faceplate for '%s' failed", bt, tag)
            if not is_headless():
                QMessageBox.warning(
                    self, "Faceplate",
                    f"Could not open the {bt} faceplate for {tag}:\n{exc}")

    @staticmethod
    def _register_pid(mgr, block) -> None:
        """Make sure the manager knows this PID before its faceplate opens.

        ``open_faceplate`` discovers controllers from *online* runtimes and
        raises for anything else, so a PID on an offline module had no
        faceplate at all. The block carries its own scales and alarm limits,
        which is all the view config needs.
        """
        if block.instance_name in mgr.known_tags:
            return
        from azeo_control_trainer.azeo_control_designer.faceplates.faceplate_manager import (
            _config_from_pid_block,
        )
        mgr.register_controller(block.instance_name,
                                _config_from_pid_block(block))

    def _open_block_trend(self, block_id: str, tag_keys: list):
        """Open Process History View with every pen requested by the block."""
        main_win = self.window()
        opener = getattr(main_win, '_open_historian_popup', None)
        if callable(opener):
            scene = self._active_scene()
            module = scene.graph.name if scene is not None else ""
            return opener(tags=list(tag_keys or ()), module=module)

        message = "Process History View is not available from this host."
        log.error(message)
        if not is_headless():
            QMessageBox.warning(self, "Process History View", message)
        return None

    def _on_block_bypass(self, block_id: str, bypassed: bool):
        """Handle block bypass toggle."""
        scene = self._active_scene()
        if scene is None:
            return
        block = scene.graph.blocks.get(block_id)
        if block:
            block._bypassed = bypassed
            log.info("Block '%s' %s", block.instance_name,
                     "BYPASSED" if bypassed else "UN-BYPASSED")

    def _on_block_added_to_scene(self, block_id: str):
        """Record block type usage for the palette's Recently Used section."""
        scene = self._active_scene()
        if scene is None:
            return
        block = scene.graph.blocks.get(block_id)
        if block:
            self._palette.record_block_use(block.block_type)

    # ------------------------------------------------------------ search
    def _on_search(self, query: str):
        """Search for blocks by name/type/tag with highlighting and navigation."""
        scene = self._active_scene()
        view = self._active_view()
        if not scene or not view:
            self._toolbar.update_search_count("")
            return

        # Always clear previous highlights and selection
        scene.clear_search_highlights()
        scene.clearSelection()

        if not query:
            self._toolbar.update_search_count("")
            return

        matches = scene.find_blocks_by_name(query)

        if not matches:
            self._toolbar.update_search_count("0 matches")
            return

        # Highlight and select all matches. Going through the scene keeps the
        # lit set known, so clearing costs O(hits) rather than O(blocks) —
        # the per-keystroke clear used to re-allocate an effect on every block.
        for item in matches:
            item.setSelected(True)
        scene = self._active_scene()
        if scene is not None and hasattr(scene, "apply_search_highlights"):
            scene.apply_search_highlights(matches)
        else:
            for item in matches:
                item.set_search_highlight(True)

        count = len(matches)
        self._toolbar.update_search_count(
            f"{count} match" if count == 1 else f"{count} matches")

        if count == 1:
            # Center view on the single match
            view.centerOn(matches[0])
        else:
            # Fit view to show all matching blocks with some padding
            from PySide6.QtCore import QRectF
            combined = QRectF()
            for item in matches:
                item_rect = item.mapToScene(item.boundingRect()).boundingRect()
                combined = combined.united(item_rect)
            # Add padding around the combined rect
            pad = 80
            combined.adjust(-pad, -pad, pad, pad)
            view.fitInView(combined, Qt.KeepAspectRatio)

    def _on_strategy_deleted(self, path: str):
        """Handle strategy file deletion from project tree."""
        # If the deleted strategy is open in a tab, close that tab
        idx = self._find_tab_by_path(path)
        if idx >= 0:
            self._close_canvas_tab(idx)

    # ------------------------------------------------------------ compile
    def _compile(self):
        scene = self._active_scene()
        if scene is None:
            return None
        from azeo_control_trainer.core.strategy.engine.compiler import (
            compile_strategy, CompileError,
        )
        try:
            compiled = compile_strategy(scene.graph)
            QMessageBox.information(
                self, "Compile Successful",
                f"Strategy compiled successfully.\n\n"
                f"Execution order: {len(compiled.exec_order)} blocks\n"
                f"Forward wires: {len(compiled.forward_wires)}\n"
                f"BKCAL wires: {len(compiled.bkcal_wires)}")
            self._last_compiled = compiled
            return compiled
        except CompileError as e:
            QMessageBox.critical(self, "Compile Error", str(e))
            return None

    # ------------------------------------------------------------ online/offline
    def _ensure_runtimes_list(self):
        """Ensure the store has a _strategy_runtimes list (now built-in)."""
        pass  # _strategy_runtimes is now initialized in SharedDataStore

    @staticmethod
    def _capture_download_baseline(canvas: StrategyCanvas) -> None:
        """Freeze what the successful download installed in the controller."""
        from azeo_control_trainer.core.strategy.deployment_analysis import (
            snapshot_document,
        )

        canvas.download_snapshot = snapshot_document(canvas.scene.graph)

    def _keylock_refuses(self, action: str) -> bool:
        """Azeo carrier-key semantics: locked refuses download and
        decommission while operation and tuning continue. Runtime state
        only — never serialized, so a locked session cannot become a file
        that silently refuses downloads next week."""
        from azeo_control_trainer.core.configuration.workspace import draft_root
        from azeo_control_trainer.core.strategy.serialization import strategy_io
        if draft_root(strategy_io.STRATEGY_DIR):
            self._on_scene_notice("Working draft: check in through Shared changes. Repository downloads are not enabled yet.")
            return True
        controller = getattr(self._store, "controller", None)
        if controller is None or not getattr(controller, "keylock", False):
            return False
        message = (f"{controller.name}: the carrier key is locked "
                   f"(KeyLockStatus = TRUE) — {action} is refused. "
                   "Operation and parameter tuning continue.")
        log.warning("%s", message)
        if not is_headless():
            QMessageBox.warning(self, "Carrier keylock", message)
        return True

    def _go_online(self):
        """Show module selection dialog, then compile and bring selected
        modules online simultaneously."""
        if self._store is None:
            QMessageBox.warning(self, "Error", "No data store available.")
            return
        if self._keylock_refuses("download"):
            return

        self._ensure_runtimes_list()

        # Build canvas info for the download dialog
        canvas_info = []
        from azeo_control_trainer.core.strategy.deployment_analysis import (
            analyze_deployment,
        )
        project_documents = [canvas.scene.graph
                             for canvas in self._strategy_canvases()]
        for i in range(self._canvas_tabs.count()):
            w = self._canvas_tabs.widget(i)
            if isinstance(w, StrategyCanvas):
                is_online = (w.runtime is not None and w.runtime.is_online)
                label = self._canvas_tabs.tabText(i)
                # Strip online indicator if present
                if label.startswith("\u25cf "):
                    label = label[2:]
                impact = analyze_deployment(
                    w.download_snapshot, w.scene.graph,
                    project_documents=project_documents)
                canvas_info.append({
                    "index": i,
                    "label": label,
                    "online": is_online,
                    "block_count": len(w.scene.graph.blocks),
                    "risk": impact.risk,
                    "impact": impact.summary(),
                })

        if not canvas_info:
            QMessageBox.information(
                self, "Download", "No strategy modules are open.")
            return

        from .dialogs.module_selection import DownloadDialog
        dlg = DownloadDialog(canvas_info, parent=self)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.impactRequested.connect(self._show_deployment_impact)

        def _on_download_accepted():
            selected = set(dlg.selected_indices())
            self._do_download(selected)

        dlg.accepted.connect(_on_download_accepted)
        dlg.show()

    def _do_download(self, selected: set):
        # ── DST capacity (PK parity): warn loudly, proceed anyway ──
        # A trainer that refuses an over-subscribed download teaches less
        # than one that shows the red gauge and lets the student feel it.
        controller = getattr(self._store, "controller", None)
        if controller is not None:
            usage = controller.dst_usage(self._store)
            if usage > controller.dst_limit:
                message = (
                    f"{controller.name} ({controller.model.name}): "
                    f"{usage} DSTs configured against a capacity of "
                    f"{controller.dst_limit}. The download proceeds, but a "
                    "real controller of this size is over-subscribed — see "
                    "Controller Diagnostics for the field points counted.")
                log.warning("%s", message)
                if not is_headless():
                    QMessageBox.warning(self, "DST capacity", message)

        # ── Validate selected strategies before download ──
        from azeo_control_trainer.core.strategy.engine.validator import validate_strategy
        all_findings: list[tuple[str, object]] = []  # (tab_name, result)
        has_errors = False
        for i in selected:
            w = self._canvas_tabs.widget(i)
            if not isinstance(w, StrategyCanvas):
                continue
            if w.runtime and w.runtime.is_online:
                continue  # already online, skip validation
            tab_name = self._canvas_tabs.tabText(i)
            if tab_name.startswith("\u25cf "):
                tab_name = tab_name[2:]
            findings = validate_strategy(w.scene.graph)
            for f in findings:
                all_findings.append((tab_name, f))
                if f.severity == "ERROR":
                    has_errors = True

        if all_findings:
            # Build summary message
            lines = []
            for tab_name, f in all_findings:
                prefix = {"ERROR": "ERROR", "WARNING": "WARN", "INFO": "INFO"}
                lines.append(f"[{prefix.get(f.severity, f.severity)}] "
                             f"{tab_name}: {f.message}")
            summary = "\n".join(lines)

            if has_errors:
                reply = QMessageBox.warning(
                    self, "Strategy Validation",
                    f"Validation found issues:\n\n{summary}\n\n"
                    "Download anyway?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    return
            else:
                # Warnings/Info only — show in status bar, non-blocking
                main_win = self.window()
                if hasattr(main_win, 'statusBar'):
                    count_w = sum(1 for _, f in all_findings
                                  if f.severity == "WARNING")
                    count_i = sum(1 for _, f in all_findings
                                  if f.severity == "INFO")
                    parts = []
                    if count_w:
                        parts.append(f"{count_w} warning(s)")
                    if count_i:
                        parts.append(f"{count_i} info(s)")
                    main_win.statusBar().showMessage(
                        f"Strategy validation: {', '.join(parts)}", 8000)

        # Take offline any canvases that were deselected
        for i in range(self._canvas_tabs.count()):
            w = self._canvas_tabs.widget(i)
            if not isinstance(w, StrategyCanvas):
                continue
            if i not in selected and w.runtime and w.runtime.is_online:
                self._take_canvas_offline(w)

        # Compile and bring online the selected canvases
        from azeo_control_trainer.core.strategy.engine.compiler import (
            compile_strategy, CompileError,
        )
        from azeo_control_trainer.core.strategy.engine.runtime import StrategyRuntime
        from azeo_control_trainer.core.strategy.engine.bridge import DataBridge

        # Ensure simulation is running
        main = self.window()
        if hasattr(main, 'start_simulation'):
            main.start_simulation()

        success_count = 0
        errors = []
        for i in selected:
            w = self._canvas_tabs.widget(i)
            if not isinstance(w, StrategyCanvas):
                continue
            # Skip if already online
            if w.runtime and w.runtime.is_online:
                success_count += 1
                continue

            tab_name = self._canvas_tabs.tabText(i)
            try:
                compiled = compile_strategy(w.scene.graph)
            except CompileError as e:
                errors.append(f"{tab_name}: {e}")
                continue

            previous_runtime = w.runtime
            w.bridge = DataBridge(self._store)
            w.runtime = StrategyRuntime()
            w.runtime.load(compiled, w.bridge)
            self._carry_debug_breakpoints(previous_runtime, w.runtime)

            if w.runtime.go_online():
                import time as _time
                w.scene.set_structure_locked(True)
                self._store.add_strategy_runtime(w.runtime, plugin=self._plugin)
                for wire_item in w.scene._wire_items.values():
                    wire_item.set_show_value(True)
                    wire_item.update_live_value()
                # Enable live overlays on blocks
                w.scene.set_live_mode(True)
                w.scene.apply_exec_order(compiled.exec_order)
                log.info("Module '%s' is ONLINE", tab_name)
                success_count += 1
                # Track download time, clear modified flag
                w.downloaded_at = _time.time()
                w.modified_since_download = False
                self._capture_download_baseline(w)
                # Update tab indicator (strip trailing " *" if present)
                clean = tab_name.rstrip(" *") if tab_name.endswith(" *") else tab_name
                self._canvas_tabs.setTabText(
                    i, f"\u25cf {clean}")
            else:
                errors.append(f"{tab_name}: go_online() failed")

        # Update toolbar state
        if success_count > 0:
            # Auto-enable "Show Values" button so wire values are visible
            self._toolbar._btn_show_values.setChecked(True)

        self._sync_legacy_refs()

        # Raise the Operator tab in main window if any module went online
        if success_count > 0:
            # Navigate to the main HMI window's Operator tab
            main_win = getattr(main, '_main_window', main)
            if hasattr(main_win, '_tab_widget'):
                main_win._tab_widget.setCurrentIndex(1)
                main_win.raise_()
                main_win.activateWindow()

        if errors:
            QMessageBox.warning(
                self, "Download — Errors",
                f"{success_count} module(s) downloaded successfully.\n\n"
                f"Errors:\n" + "\n".join(errors))

    def _take_canvas_offline(self, canvas: StrategyCanvas):
        """Take a single canvas offline and remove from runtimes list."""
        if canvas.runtime:
            canvas.runtime.go_offline()
            if self._store:
                self._store.remove_strategy_runtime(canvas.runtime)
        # Structural editing is allowed again once off scan.
        canvas.scene.set_structure_locked(False)
        self._structure_lock_explained = False
        # Disable live value display
        canvas.scene.set_live_mode(False)
        for wire_item in canvas.scene._wire_items.values():
            wire_item.set_show_value(False)
        # Remove online indicator from tab text (preserve modified *)
        idx = self._canvas_tabs.indexOf(canvas)
        if idx >= 0:
            label = self._canvas_tabs.tabText(idx)
            if label.startswith("\u25cf "):
                label = label[2:]
            # Re-add modified indicator if still dirty
            if canvas.modified_since_download and not label.endswith(" *"):
                label = label + " *"
            self._canvas_tabs.setTabText(idx, label)
        self._sync_active_canvas_ui()
        log.info("Module offline")

    def go_online_single(self, canvas_index: int) -> bool:
        """Bring a single canvas online by tab index (used by controller dialog)."""
        if self._store is None:
            return False
        self._ensure_runtimes_list()

        w = self._canvas_tabs.widget(canvas_index)
        if not isinstance(w, StrategyCanvas):
            return False
        if w.runtime and w.runtime.is_online:
            self._sync_active_canvas_ui()
            return True  # already online

        from azeo_control_trainer.core.strategy.engine.compiler import (
            compile_strategy, CompileError,
        )
        from azeo_control_trainer.core.strategy.engine.runtime import StrategyRuntime
        from azeo_control_trainer.core.strategy.engine.bridge import DataBridge

        # Ensure simulation is running
        main = self.window()
        if hasattr(main, 'start_simulation'):
            main.start_simulation()

        tab_name = self._canvas_tabs.tabText(canvas_index)
        if tab_name.startswith("\u25cf "):
            tab_name = tab_name[2:]

        try:
            compiled = compile_strategy(w.scene.graph)
        except CompileError as e:
            log.error("Compile failed for '%s': %s", tab_name, e)
            return False

        previous_runtime = w.runtime
        w.bridge = DataBridge(self._store)
        w.runtime = StrategyRuntime()
        w.runtime.load(compiled, w.bridge)
        self._carry_debug_breakpoints(previous_runtime, w.runtime)

        if w.runtime.go_online():
            import time as _time
            w.scene.set_structure_locked(True)
            self._store.add_strategy_runtime(w.runtime, plugin=self._plugin)
            for wire_item in w.scene._wire_items.values():
                wire_item.set_show_value(True)
                wire_item.update_live_value()
            w.scene.set_live_mode(True)
            w.scene.apply_exec_order(compiled.exec_order)
            # Track download time, clear modified flag
            w.downloaded_at = _time.time()
            w.modified_since_download = False
            self._capture_download_baseline(w)
            clean = tab_name.rstrip(" *") if tab_name.endswith(" *") else tab_name
            self._canvas_tabs.setTabText(
                canvas_index, f"\u25cf {clean}")
            self._toolbar._btn_show_values.setChecked(True)
            self._sync_legacy_refs()
            log.info("Module '%s' brought ONLINE (single)", clean)
            return True
        else:
            log.error("go_online() failed for '%s'", tab_name)
            return False

    def redownload_single(self, canvas_index: int) -> bool:
        """Take a module offline, recompile, and bring back online."""
        w = self._canvas_tabs.widget(canvas_index)
        if not isinstance(w, StrategyCanvas):
            return False
        # Take offline first if running
        if w.runtime and w.runtime.is_online:
            self._take_canvas_offline(w)
        # Now bring online with fresh compile
        return self.go_online_single(canvas_index)

    def take_canvas_offline_by_index(self, canvas_index: int):
        """Take a single canvas offline by tab index (used by controller dialog)."""
        w = self._canvas_tabs.widget(canvas_index)
        if isinstance(w, StrategyCanvas) and w.runtime and w.runtime.is_online:
            self._take_canvas_offline(w)
            self._sync_legacy_refs()

    def _go_offline(self):
        """Take all online modules offline."""
        if self._keylock_refuses("decommission"):
            return
        self._ensure_runtimes_list()

        for i in range(self._canvas_tabs.count()):
            w = self._canvas_tabs.widget(i)
            if isinstance(w, StrategyCanvas) and w.runtime and w.runtime.is_online:
                self._take_canvas_offline(w)

        self._sync_legacy_refs()
        log.info("All modules OFFLINE")

    # ------------------------------------------------------------ save/load
    def _save(self, canvas=None):
        """Save a canvas back to the file it was opened from.

        Passing no path to ``save_strategy`` makes it invent
        ``STRATEGY_DIR/<graph name>.json``, which for a module opened from
        ``control/`` is a *different* file that ``_project.json`` never loads —
        the edit silently never reaches the runtime. Always write to
        ``canvas.file_path`` when we have one.
        """
        if canvas is None:
            canvas = self._active_canvas()
        if canvas is None:
            return
        from azeo_control_trainer.core.strategy.serialization.strategy_io import save_strategy
        target = getattr(canvas, "file_path", None)
        try:
            comments = canvas.scene.get_comments_data()
            path = save_strategy(canvas.scene.graph, path=target,
                                 comments=comments)
            canvas.file_path = str(path)
            canvas.dirty = False
            idx = self._canvas_tabs.indexOf(canvas)
            if idx >= 0:
                self._canvas_tabs.setTabText(idx, Path(path).stem)
            log.info("Strategy saved to %s", path)
            self._project_tree.refresh()
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))

    def _on_structure_edit_blocked(self, what: str):
        """Tell the user why a structural edit was refused while on scan.

        The status line is updated every time; the explanatory dialog is shown
        only once per online session so repeated attempts don't stack modal
        dialogs (and so automated runs are never blocked by one).
        """
        msg = (f"Cannot {what} while the module is on scan — "
               "take it offline first.")
        log.info("Structural edit refused: %s", what)
        show = getattr(self._status_bar, "showMessage", None)
        if callable(show):
            show(msg, 5000)
        if getattr(self, "_structure_lock_explained", False):
            return
        self._structure_lock_explained = True
        QMessageBox.information(
            self, "Module is on scan",
            f"You cannot {what} while this module is running.\n\n"
            "The running scan executes the compiled strategy, so editing the "
            "diagram live would corrupt the scan and leave the change out of "
            "what actually runs.\n\n"
            "Take the module offline (Module → Go Offline), make the "
            "change, then download it again. Tuning and parameter edits stay "
            "available while on scan.",
        )

    # ------------------------------------------------------- ribbon actions
    def _undo(self):
        scene = self._active_scene()
        if scene is not None and scene.undo_stack.canUndo():
            scene.undo_stack.undo()

    def _redo(self):
        scene = self._active_scene()
        if scene is not None and scene.undo_stack.canRedo():
            scene.undo_stack.redo()

    def _on_scene_notice(self, message: str):
        """Surface an action that did nothing, or only part of what was asked.

        Fires for things like a paste that dropped unknown block types or an
        Auto Arrange that fell back after a compile error — previously these
        returned silently. Non-modal on purpose.
        """
        log.info("Scene notice: %s", message)
        show = getattr(self._status_bar, "showMessage", None)
        if callable(show):
            show(message, 6000)

    def _on_connection_refused(self, reason: str):
        """Explain why a wire could not be drawn.

        Incompatible connections (e.g. a FLOAT output into a BOOL input) used
        to be logged and built anyway; they are now refused, so the user needs
        to be told rather than left wondering why the wire did not appear.
        """
        log.info("Connection refused: %s", reason)
        show = getattr(self._status_bar, "showMessage", None)
        if callable(show):
            show(reason, 6000)

    def _publish_module_states(self):
        """Tell the project tree which open modules are on scan / edited."""
        states: dict[str, str] = {}
        for i in range(self._canvas_tabs.count()):
            canvas = self._canvas_tabs.widget(i)
            if not isinstance(canvas, StrategyCanvas):
                continue
            path = getattr(canvas, "file_path", None)
            if not path:
                continue
            online = bool(canvas.runtime and canvas.runtime.is_online)
            if online:
                states[str(path)] = ("modified" if canvas.modified_since_download
                                     else "online")
            else:
                states[str(path)] = "offline"
        if states:
            try:
                self._project_tree.set_module_states(states)
            except Exception as exc:          # pragma: no cover — tree rebuild race
                log.debug("module state publish skipped: %s", exc)

    def _attach_online_view(self):
        """Show live values for modules that are already running.

        Azeo separates Download (send the configuration to the controller)
        from Go Online (attach the editor to what is running). Here that means:
        no recompile, no restart — just put the canvases whose runtime is
        already scanning into live mode. Modules that were auto-loaded and
        started at launch are the common case.
        """
        attached = 0
        for i in range(self._canvas_tabs.count()):
            canvas = self._canvas_tabs.widget(i)
            if not isinstance(canvas, StrategyCanvas):
                continue
            if canvas.runtime is None or not canvas.runtime.is_online:
                continue
            canvas.scene.set_live_mode(True)
            canvas.scene.set_structure_locked(True)
            for wire_item in canvas.scene._wire_items.values():
                wire_item.set_show_value(True)
                wire_item.update_live_value()
            attached += 1

        if attached:
            self._sync_active_canvas_ui()
            show = getattr(self._status_bar, "showMessage", None)
            if callable(show):
                show(f"Online — viewing {attached} running module(s)", 4000)
            log.info("Attached live view to %d running module(s)", attached)
        else:
            QMessageBox.information(
                self, "Nothing is running",
                "No module in this project is on scan.\n\n"
                "Use Download to compile and start a module; Go Online only "
                "attaches the live view to modules that are already running.",
            )

    def _fit_canvas(self, canvas=None):
        """Request a centred first frame after the canvas becomes visible."""
        canvas = canvas or self._active_canvas()
        if canvas is None:
            return
        try:
            canvas.view.request_initial_frame()
        except Exception as exc:            # pragma: no cover - view not realised yet
            log.debug("initial canvas frame skipped: %s", exc)

    def _set_all_pins(self, *, hide_unused: bool):
        """Hide unconnected pins (or show every pin) on the whole diagram.

        Blocks default to showing every terminal, which makes the large Azeo
        blocks very tall; Azeo shows only the pins the engineer exposes. This
        is the diagram-wide version of the per-block Show/Hide Pins menu. Pin
        visibility is UI state persisted as ``hidden_terminals``, so it never
        changes what the module executes.
        """
        scene = self._active_scene()
        if scene is None:
            return
        from .items.block_item import BlockItem
        items = [item for item in scene.items()
                 if isinstance(item, BlockItem)]
        description = "Hide Unused Pins" if hide_unused else "Show All Pins"
        changed = 0
        # A diagram-wide command is one engineering decision.  Without the
        # macro Qt exposed one undo entry per block, so Undo restored only
        # part of the drawing and left the rest in the requested state.
        scene.undo_stack.beginMacro(description)
        try:
            for item in items:
                changed += bool(
                    item._hide_unused_pins()
                    if hide_unused else item._show_all_pins()
                )
        finally:
            scene.undo_stack.endMacro()
        show = getattr(self._status_bar, "showMessage", None)
        if callable(show):
            show(("Hid unconnected pins on %d block(s)" if hide_unused
                  else "Showing all pins on %d block(s)") % changed, 4000)
        log.info("Pin visibility updated on %d/%d blocks (hide_unused=%s)",
                 changed, len(items), hide_unused)

    def _select_all(self):
        scene = self._active_scene()
        if scene is None:
            return
        from .items.block_item import BlockItem
        from .items.comment_item import CommentItem
        from .items.wire_item import WireItem
        for item in scene.items():
            if isinstance(item, (BlockItem, WireItem, CommentItem)):
                item.setSelected(True)

    def _delete_selection(self):
        """Delete every selected diagram object through its undoable API."""
        scene = self._active_scene()
        if scene is None:
            return
        scene.delete_selected()

    def _print_diagram(self, preview: bool = False):
        """Print the active diagram, or show a print preview."""
        # The Windows print stack can initialise COM/native spooler services
        # as soon as QtPrintSupport objects are constructed.  Headless callers
        # must leave before even inspecting a canvas so this remains a safe,
        # deterministic capability probe in smoke tests and service sessions.
        if is_headless():
            log.info("Diagram %s skipped in headless mode",
                     "preview" if preview else "print")
            return False
        canvas = self._active_canvas()
        if canvas is None or not canvas.scene.graph.blocks:
            QMessageBox.information(self, "Print", "Open a module first.")
            return False

        from PySide6.QtCore import QRectF, Qt
        from PySide6.QtGui import QPainter
        from PySide6.QtPrintSupport import (
            QPrintDialog, QPrinter, QPrintPreviewDialog,
        )

        scene = canvas.scene
        label = self._canvas_tabs.tabText(self._canvas_tabs.indexOf(canvas))

        def render(printer: QPrinter):
            painter = QPainter(printer)
            painter.setRenderHint(QPainter.Antialiasing)
            target = QRectF(printer.pageRect(QPrinter.DevicePixel))
            scene.render(painter, target,
                         scene.itemsBoundingRect().adjusted(-20, -20, 20, 20),
                         Qt.KeepAspectRatio)
            painter.end()

        printer = QPrinter(QPrinter.HighResolution)
        printer.setDocName(label)
        printer.setPageOrientation(printer.pageLayout().Orientation.Landscape)
        if preview:
            dlg = QPrintPreviewDialog(printer, self)
            dlg.paintRequested.connect(render)
            return bool(dlg.exec())
        if QPrintDialog(printer, self).exec():
            render(printer)
            return True
        return False

    def _show_module_report(self):
        """Open one reusable, configurable report for the active module."""
        canvas = self._active_canvas()
        if canvas is None:
            log.info("Module Report requested with no active module")
            if not is_headless():
                QMessageBox.information(self, "Module Report",
                                        "Open a module first.")
            return None

        existing = self._module_report_dialog
        try:
            if existing is not None and getattr(existing, "_canvas", None) is canvas:
                existing.refresh_report()
                existing.show()
                existing.raise_()
                existing.activateWindow()
                return existing
            if existing is not None:
                existing.close()
        except RuntimeError:
            self._module_report_dialog = None

        from .dialogs.module_report_dialog import ModuleReportDialog
        from .module_report import build_module_report

        label = str(canvas.scene.graph.name or "Untitled Module").strip()
        project_name = str(getattr(self._plugin, "display_name", "") or "")
        controller = getattr(self._store, "controller", None)
        controller_name = str(getattr(controller, "name", "") or "")

        def snapshot():
            return build_module_report(
                canvas.scene.graph,
                module_name=label,
                file_path=canvas.file_path,
                runtime=canvas.runtime,
                project_name=project_name,
                controller_name=controller_name,
                dirty=canvas.dirty,
                downloaded_at=canvas.downloaded_at,
                modified_since_download=canvas.modified_since_download,
            )

        dialog = ModuleReportDialog(snapshot, parent=self)
        dialog._canvas = canvas

        def clear_dialog(*_args):
            if self._module_report_dialog is dialog:
                self._module_report_dialog = None

        dialog.destroyed.connect(clear_dialog)
        self._module_report_dialog = dialog
        dialog.show()
        return dialog

    def _show_module_info(self, page: str = "properties"):
        """Read-only module information, Azeo's Module Properties family.

        Four views over data the module already carries: Properties (identity,
        counts, scan state), Parameters (every block's configured values),
        Cross Reference (which store tags the module reads and writes), and
        Named Sets (the enumerated parameters in use, with their valid values).
        """
        from .dialogs.module_info_dialog import ModuleInfoDialog

        canvas = self._active_canvas()
        if canvas is None:
            QMessageBox.information(self, "Module Information",
                                    "Open a module first.")
            return
        if is_headless():
            log.info("Module Information '%s' skipped in headless mode", page)
            return None
        label = self._canvas_tabs.tabText(self._canvas_tabs.indexOf(canvas))
        dlg = ModuleInfoDialog(canvas, label.strip(" *●○"), page=page, parent=self)
        dlg.exec()
        return dlg

    def _copy(self) -> bool:
        """Copy the current selection to the shared canvas clipboard."""
        scene, view = self._active_scene(), self._active_view()
        if scene is None or view is None:
            return False
        data = scene.copy_selected()
        if not data:
            return False
        type(view)._clipboard = data
        return True

    def _cut(self):
        scene = self._active_scene()
        if scene is None or not self._copy():
            return
        # Cut has exactly the same mixed-object semantics as Delete: blocks,
        # explicit wires and comments disappear together in one undo step.
        scene.delete_selected("Cut selection")

    def _paste(self):
        from PySide6.QtCore import QPointF
        scene, view = self._active_scene(), self._active_view()
        if scene is None or view is None:
            return
        data = getattr(type(view), "_clipboard", None)
        if data:
            scene.paste_clipboard(data, offset=QPointF(20, 20))

    def _upload(self):
        """Ribbon compatibility route for controller-to-project upload."""
        return self._show_upload_dialog()

    def _checkpoints(self, mode: str = "save"):
        """Ribbon compatibility route preserving the requested operation."""
        return self._show_checkpoint_dialog(mode)

    def _controller_simulator(self):
        """Ribbon compatibility route for the offline simulator."""
        return self._show_controller_simulator()

    def _controller_diagnostics(self):
        """Ribbon compatibility route for controller diagnostics."""
        return self._show_diagnostics()

    def _datalog_config(self):
        """Ribbon compatibility route for data-log configuration."""
        return self._show_datalog_config()

    def _faceplate_for_selection(self):
        """Open the faceplate of the selected controller block."""
        scene = self._active_scene()
        block = scene.get_selected_block() if scene is not None else None
        if block is None:
            QMessageBox.information(
                self, "Faceplate",
                "Select a controller block to open its faceplate.")
            return
        self._open_pvm_block_faceplate(block.id)

    def _open_pvm_block_faceplate(self, block_id: str):
        """Open the production PVM faceplate used by the operator station."""
        scene = self._active_scene()
        item = (scene._block_items.get(block_id)
                if scene is not None else None)
        if item is not None and item._pvm_faceplate_available(
                item.block.block_type):
            return item._open_pvm_faceplate()

        block = scene.graph.blocks.get(block_id) if scene is not None else None
        block_type = block.block_type if block is not None else "This block"
        message = f"{block_type} does not have a registered PVM faceplate."
        log.info(message)
        if not is_headless():
            QMessageBox.information(self, "Faceplate", message)
        return None

    def _save_as(self):
        """Save the active canvas to a new file chosen by the user."""
        canvas = self._active_canvas()
        if canvas is None:
            return False
        # A real native chooser cannot be answered offscreen.  Tests and
        # scripted integrations may replace it with a Python callable to
        # supply an explicit path; that is already a decision and is safe.
        if is_headless() and inspect.isbuiltin(QFileDialog.getSaveFileName):
            log.info("Save Module As chooser skipped in headless mode")
            return False
        from azeo_control_trainer.core.strategy.serialization import strategy_io
        start = canvas.file_path or str(
            Path(strategy_io.STRATEGY_DIR)
            / f"{canvas.scene.graph.name}.json")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Module As", start, "Strategy modules (*.json)")
        if not path:
            return False
        try:
            comments = canvas.scene.get_comments_data()
            saved = strategy_io.save_strategy(
                canvas.scene.graph, path=path, comments=comments)
            canvas.file_path = str(saved)
            # Save As establishes this document as the clean baseline.  The
            # old code renamed the tab but left ``dirty`` set, so closing the
            # freshly saved module still prompted to save it again.
            canvas.dirty = False
            idx = self._canvas_tabs.indexOf(canvas)
            if idx >= 0:
                online = bool(canvas.runtime and canvas.runtime.is_online)
                prefix = "\u25cf " if online else ""
                suffix = " *" if canvas.modified_since_download else ""
                self._canvas_tabs.setTabText(
                    idx, f"{prefix}{Path(saved).stem}{suffix}")
            log.info("Strategy saved to %s", saved)
            self._project_tree.refresh()
            self._engineering_workspace.refresh_structure()
            return True
        except Exception as e:
            if not is_headless():
                QMessageBox.critical(self, "Save Error", str(e))
            return False

    def _tab_label_for_path(self, path: str) -> str:
        """Derive a tab label from a strategy file path.

        Prefers the JSON ``name`` field; falls back to the file stem.
        """
        p = Path(path)
        try:
            import json
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            name = data.get("name")
            if name:
                return name
        except Exception:
            pass
        return p.stem

    def _load(self, path: str):
        # If already open, just switch to that tab
        idx = self._find_tab_by_path(path)
        if idx >= 0:
            self._canvas_tabs.setCurrentIndex(idx)
            return

        from azeo_control_trainer.core.strategy.serialization.strategy_io import load_strategy
        try:
            graph, comments = load_strategy(path)
        except Exception as e:
            QMessageBox.critical(self, "Load Error", str(e))
            return

        label = self._tab_label_for_path(path)
        canvas = self._create_canvas(label=label, file_path=path)
        canvas.scene.load_graph(graph, comments=comments)
        self._connect_all_block_signals(canvas.scene)
        self._props.set_block(None)
        canvas.dirty = False
        # Frame the module on open. Saved coordinates are absolute, so without
        # this a module can open scrolled off-screen (the AO of a 4-block loop
        # sat outside the viewport). _load_preset already did this.
        self._fit_canvas(canvas)
        log.info("Loaded strategy from %s", path)

    def _load_preset(self, scheme_key: str):
        canvas = self._active_canvas()
        if canvas and canvas.scene.graph.blocks:
            reply = QMessageBox.question(
                self, "Load Preset",
                "Discard current strategy and load preset?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return

        # Look up builder from plugin first, then fall back
        graph = None
        if self._plugin and self._plugin.preset_builders:
            entry = self._plugin.preset_builders.get(scheme_key.upper())
            if entry:
                _, builder = entry
                graph = builder()
        if graph is None:
            from azeo_control_trainer.core.strategy.presets.king_strategies import get_preset_graph
            graph = get_preset_graph(scheme_key)

        if graph:
            if canvas is None:
                canvas = self._create_canvas(
                    label=f"Preset-{scheme_key}")
            canvas.scene.load_graph(graph)
            self._connect_all_block_signals(canvas.scene)
            self._props.set_block(None)
            self._fit_canvas(canvas)
            log.info("Loaded preset: Scheme %s", scheme_key)

    def _new_strategy(self):
        """Create a new empty tab labeled 'Untitled'."""
        self._create_canvas(label="Untitled")

    def _show_controller_properties(self):
        """Open the controller configurator from its live ribbon action."""
        from .dialogs.controller_properties import ControllerPropertiesDialog

        dialog = ControllerPropertiesDialog(self._store, parent=self)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()

    def _strategy_canvases(self) -> list[StrategyCanvas]:
        return [canvas for index in range(self._canvas_tabs.count())
                if isinstance(
                    (canvas := self._canvas_tabs.widget(index)), StrategyCanvas)]

    def _show_deployment_impact(self):
        """Open the visual current-vs-downloaded impact review."""
        from .dialogs.deployment_impact import DeploymentImpactDialog

        canvases = self._strategy_canvases()

        def create_dialog():
            dialog = DeploymentImpactDialog(canvases, parent=self)
            dialog.navigateRequested.connect(self._navigate_deployment_impact)
            return dialog

        return self._show_retained_dialog(
            "deployment_impact",
            create_dialog,
            context=tuple(id(canvas) for canvas in canvases),
        )

    def _navigate_deployment_impact(self, module_name: str, block_id: str):
        for index in range(self._canvas_tabs.count()):
            canvas = self._canvas_tabs.widget(index)
            if not isinstance(canvas, StrategyCanvas) \
                    or canvas.scene.graph.name != module_name:
                continue
            self._canvas_tabs.setCurrentIndex(index)
            item = canvas.scene._block_items.get(block_id)
            if item is not None:
                canvas.scene.clearSelection()
                item.setSelected(True)
                canvas.view.centerOn(item)
            return

    def _show_bulk_engineering(self):
        """Open preview-first module generation and bulk configuration edit."""
        from .dialogs.bulk_engineering import BulkEngineeringDialog

        canvases = self._strategy_canvases()
        return self._show_retained_dialog(
            "bulk_engineering",
            lambda: BulkEngineeringDialog(
                self._project_tree, canvases, parent=self),
            context=tuple((id(canvas), canvas.file_path) for canvas in canvases),
        )

    def _show_module_classes(self):
        """Manage full Control Module Classes and linked instances."""
        from .dialogs.module_class_manager import ModuleClassManagerDialog

        canvases = self._strategy_canvases()
        active = self._active_canvas()

        def create_dialog():
            dialog = ModuleClassManagerDialog(
                self._project_tree, canvases, active_canvas=active,
                parent=self)
            dialog.documentRequested.connect(
                self._apply_module_class_document)
            return dialog

        return self._show_retained_dialog(
            "module_classes",
            create_dialog,
            context=(id(active) if active is not None else None,
                     tuple((id(canvas), canvas.file_path)
                           for canvas in canvases)),
        )

    def _apply_module_class_document(
        self, canvas: StrategyCanvas, document: dict, reason: str,
    ) -> None:
        """Install one reviewed class mutation into an offline canvas."""
        if canvas is None:
            raise ValueError("The target module is no longer open")
        if canvas.runtime is not None and canvas.runtime.is_online:
            raise ValueError(
                "Take the module offline before changing its class revision")
        from azeo_control_trainer.core.strategy.engine.compiler import (
            compile_strategy,
        )
        from azeo_control_trainer.core.strategy.serialization.strategy_io import (
            graph_from_document,
        )

        graph, embedded_comments = graph_from_document(document, strict=True)
        compile_strategy(graph)
        canvas.scene.load_graph(graph, comments=embedded_comments)
        self._connect_all_block_signals(canvas.scene)
        self._canvas_tabs.setCurrentWidget(canvas)
        self._on_modified()
        self._props.set_block(None)
        self._engineering_workspace.set_graph(canvas.scene.graph)
        self._sync_legacy_refs()
        self._fit_canvas(canvas)
        log.info("Module class change applied to %s: %s", graph.name, reason)

    def _show_redundancy_simulator(self):
        """Open the controller-pair and communication-failure drill."""
        from .dialogs.redundancy_simulator import RedundancySimulatorDialog
        from azeo_control_trainer.core.strategy.engine.redundancy import (
            RedundancySimulator,
        )

        executive = self.controller_executive()
        if executive is None or self._store is None:
            return None
        if self._redundancy_model is None \
                or self._redundancy_model.executive is not executive:
            self._redundancy_model = RedundancySimulator(
                self._store, executive)
        return self._show_retained_dialog(
            "redundancy_simulator",
            lambda: RedundancySimulatorDialog(
                self._store, executive, self._redundancy_model, parent=self),
        )

    # -------------------------------------------------------- controller status
    def _show_controller_status(self):
        """Open or raise the DCS-style controller status dialog."""
        from .dialogs.controller_status import ControllerStatusDialog
        if hasattr(self, '_ctrl_status_dlg') and self._ctrl_status_dlg.isVisible():
            self._ctrl_status_dlg.raise_()
            self._ctrl_status_dlg.activateWindow()
            return
        self._ctrl_status_dlg = ControllerStatusDialog(
            designer_tab=self, store=self._store, parent=self)
        self._ctrl_status_dlg.show()

    # -------------------------------------------------------- Honeywell features
    def _show_monitoring_tab(self):
        """Toggle the Monitoring Tab panel (Honeywell-style live parameter view)."""
        if not hasattr(self, '_monitoring_panel') or self._monitoring_panel is None:
            from .panels.monitoring_tab import MonitoringTab
            self._monitoring_panel = MonitoringTab(
                store=self._store, parent=None)
            self._monitoring_panel.set_designer_tab(self)
            self._monitoring_panel.faceplateRequested.connect(
                self._open_monitoring_faceplate)

        if self._monitoring_panel.isVisible():
            self._monitoring_panel.stop()
            self._monitoring_panel.hide()
        else:
            self._monitoring_panel.show()
            self._monitoring_panel.start()

    def _open_monitoring_faceplate(self, tag: str):
        """Open a faceplate from the monitoring tab."""
        main_win = self.window()
        if hasattr(main_win, '_open_faceplate'):
            main_win._open_faceplate(tag)

    def _show_retained_dialog(self, key: str, factory, *, prepare=None,
                              context=None):
        """Create or raise one parent-owned modeless command dialog.

        Control Designer historically had an ``exec()`` ribbon implementation
        beside a modeless menu implementation for the same five commands.
        Besides drifting arguments, the modal variants blocked scan/status UI.
        This one owner keeps the Python wrapper alive, applies command context
        before showing, and gives every caller exactly the same surface.
        """
        dialogs = self._command_dialogs
        dialog = dialogs.get(key)
        if dialog is not None:
            try:
                if context is not None and getattr(
                        dialog, "_command_context", None) is not context:
                    dialog.close()
                    if dialogs.get(key) is dialog:
                        dialogs.pop(key, None)
                    dialog = None
            except RuntimeError:
                dialogs.pop(key, None)
                dialog = None

        if dialog is None:
            dialog = factory()
            if dialog is None:
                return None
            dialog._command_context = context
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            dialogs[key] = dialog

            def clear_dialog(*_args):
                if dialogs.get(key) is dialog:
                    dialogs.pop(key, None)

            dialog.destroyed.connect(clear_dialog)

        if prepare is not None:
            prepare(dialog)
        if is_headless():
            dialog.setAttribute(Qt.WA_DontShowOnScreen, True)
        dialog.show()
        if not is_headless():
            dialog.raise_()
            dialog.activateWindow()
        return dialog

    @staticmethod
    def _prepare_checkpoint_dialog(dialog, mode: str) -> None:
        """Select the operation requested by either menu or ribbon."""
        mode = str(mode).strip().lower()
        if mode not in {"save", "restore"}:
            raise ValueError(f"Unsupported checkpoint mode: {mode}")
        dialog._requested_mode = mode

        # Retain compatibility with a future tabbed checkpoint surface and
        # older plug-ins that already expose one of these explicit selectors.
        for attr in (f"select_{mode}_tab", f"show_{mode}"):
            selector = getattr(dialog, attr, None)
            if callable(selector):
                selector()
                return

        refresh = getattr(dialog, "_refresh_list", None)
        if callable(refresh):
            refresh()
        target = (getattr(dialog, "_txt_name", None) if mode == "save"
                  else getattr(dialog, "_table", None))
        if target is not None:
            target.setFocus(Qt.OtherFocusReason)
            if mode == "restore" and target.rowCount() > 0:
                target.setCurrentCell(max(0, target.currentRow()), 0)

    def _show_checkpoint_dialog(self, mode: str = "save"):
        """Open or raise checkpoint management at save or restore."""
        normalized = str(mode).strip().lower()
        if normalized not in {"save", "restore"}:
            raise ValueError(f"Unsupported checkpoint mode: {mode}")
        from .dialogs.checkpoint_dialog import CheckpointDialog

        return self._show_retained_dialog(
            "checkpoint",
            lambda: CheckpointDialog(self, self._store, parent=self),
            prepare=lambda dialog: self._prepare_checkpoint_dialog(
                dialog, normalized),
        )

    def _show_compare_parameters(self):
        """Open the compare parameters dialog for the active canvas."""
        canvas = self._active_canvas()
        if canvas is None:
            QMessageBox.information(
                self, "Compare Parameters",
                "No strategy module is open.")
            return
        if not canvas.file_path:
            QMessageBox.information(
                self, "Compare Parameters",
                "Save the strategy first to compare against the file.")
            return
        if not (canvas.runtime and canvas.runtime.is_online):
            QMessageBox.information(
                self, "Compare Parameters",
                "Strategy must be online to compare runtime values.")
            return
        from .dialogs.compare_dialog import CompareParametersDialog
        dlg = CompareParametersDialog(canvas, self._store, parent=self)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.show()

    def _show_diagnostics(self):
        """Open the controller diagnostics dialog."""
        from .dialogs.diagnostics_dialog import ControllerDiagnosticsDialog

        return self._show_retained_dialog(
            "diagnostics",
            lambda: ControllerDiagnosticsDialog(
                self, self._store, parent=self),
        )

    def _show_upload_dialog(self):
        """Open the upload (controller -> project) dialog."""
        from .dialogs.upload_dialog import UploadDialog

        def create_dialog():
            dialog = UploadDialog(self, self._store, parent=self)
            dialog.accepted.connect(self._project_tree.refresh)
            return dialog

        return self._show_retained_dialog("upload", create_dialog)

    # -------------------------------------------------------- Tier 3 features
    def _show_knowledge_builder(self, block_type: str | None = None):
        """Open the Knowledge Builder help dialog, optionally focused on a block type."""
        from .dialogs.knowledge_builder import KnowledgeBuilderDialog
        dlg = KnowledgeBuilderDialog(parent=self)
        if block_type:
            # Select the block type in the combo if provided
            idx = dlg._block_combo.findText(block_type)
            if idx >= 0:
                dlg._block_combo.setCurrentIndex(idx)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.show()

    def _show_datalog_config(self):
        """Open the Data Log Configuration dialog."""
        from .dialogs.datalog_config import DataLogConfigDialog

        return self._show_retained_dialog(
            "datalog",
            lambda: DataLogConfigDialog(
                store=self._store, plugin=self._plugin, parent=self),
        )

    def _show_controller_simulator(self):
        """Open the offline Controller Simulator dialog."""
        canvas = self._active_canvas()
        if canvas is None:
            if not is_headless():
                QMessageBox.information(
                    self, "Controller Simulator",
                    "No strategy module is open.")
            return None
        from .dialogs.controller_simulator import ControllerSimulatorDialog

        return self._show_retained_dialog(
            "simulator",
            lambda: ControllerSimulatorDialog(canvas=canvas, parent=self),
            context=canvas,
        )

    # ------------------------------------------------------------ compare
    def _compare_strategies(self):
        from .dialogs.strategy_diff import StrategyDiffDialog
        dlg = StrategyDiffDialog(parent=self)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.show()

    # ------------------------------------------------------------ version history
    def _version_history(self):
        canvas = self._active_canvas()
        path = Path(canvas.file_path) if canvas and canvas.file_path else None
        if path is None:
            if not is_headless():
                QMessageBox.information(
                    self, "Version History",
                    "The active module has not been saved yet. Save it first.")
            return False
        runtime = getattr(canvas, "runtime", None)
        if runtime and runtime.is_online:
            message = ("Take the active module offline before restoring a "
                       "saved version.")
            log.info("Version restore refused for online module %s", path)
            if not is_headless():
                QMessageBox.information(self, "Version History", message)
            return False

        from .dialogs.version_history import VersionHistoryDialog
        dlg = VersionHistoryDialog(strategy_path=path, parent=self)
        dlg.setAttribute(Qt.WA_DeleteOnClose)

        def _on_version_accepted():
            restore_path = dlg.get_restore_path()
            if restore_path:
                self._restore_version(canvas, restore_path)

        dlg.accepted.connect(_on_version_accepted)
        dlg.show()
        return True

    def _restore_version(self, canvas: StrategyCanvas,
                         restore_path: Path | str) -> bool:
        """Restore an archived revision into ``canvas`` and its module file.

        A history file is evidence, not another project module.  Opening it in
        a new tab (the former behaviour) left the real module untouched and
        allowed an engineer to believe a restore had occurred.  We first save
        the exact current canvas to the canonical path, which also creates a
        recoverable auto-version, then transactionally write the selected
        revision and reload that same canvas.
        """
        if self._canvas_tabs.indexOf(canvas) < 0 or not canvas.file_path:
            return False
        runtime = getattr(canvas, "runtime", None)
        if runtime and runtime.is_online:
            log.info("Version restore refused: module is online")
            if not is_headless():
                QMessageBox.information(
                    self, "Version History",
                    "Take the active module offline before restoring a "
                    "saved version.")
            return False

        from azeo_control_trainer.core.strategy.serialization import strategy_io

        target = Path(canvas.file_path)
        selected = Path(restore_path)
        snapshot_saved = False
        try:
            # Validate the selected revision before changing the current file.
            restored_graph, restored_comments = strategy_io.load_strategy(
                selected, remember=False)

            current_comments = canvas.scene.get_comments_data()
            strategy_io.save_strategy(
                canvas.scene.graph, path=target, comments=current_comments)
            snapshot_saved = True
            strategy_io.save_strategy(
                restored_graph, path=target, comments=restored_comments)

            canvas.scene.load_graph(restored_graph, comments=restored_comments)
            self._connect_all_block_signals(canvas.scene)
            canvas.file_path = str(target)
            canvas.dirty = False
            # A restore is saved, but it is not what a previously downloaded
            # controller is running.  Preserve that distinction without
            # claiming an unsaved file edit.
            canvas.modified_since_download = canvas.downloaded_at is not None

            idx = self._canvas_tabs.indexOf(canvas)
            label = target.stem
            if canvas.modified_since_download:
                label += " *"
            self._canvas_tabs.setTabText(idx, label)
            if self._active_canvas() is canvas:
                self._props.set_block(None)
                self._engineering_workspace.set_graph(canvas.scene.graph)
                self._sync_legacy_refs()
            self._fit_canvas(canvas)
            self._project_tree.refresh()
            self._engineering_workspace.refresh_structure()
            log.info("Restored %s into module %s", selected, target)
            return True
        except Exception as exc:
            # If the selected revision could not be written after the snapshot
            # save, the canonical file still contains the current canvas and
            # is therefore clean/recoverable rather than half-restored.
            if snapshot_saved:
                canvas.dirty = False
            log.exception("Version restore failed: %s", exc)
            if not is_headless():
                QMessageBox.critical(self, "Version History", str(exc))
            return False

    # ------------------------------------------------------------ templates
    def _place_template(self, template_path: str, scene_pos=None):
        """Place a reusable template at its requested scene coordinate.

        Palette double-click and ribbon commands omit ``scene_pos`` and use
        the viewport centre. A canvas drag supplies an exact scene point; it
        must not be replaced by the viewport centre after the drop occurred.
        """
        canvas = self._active_canvas()
        if canvas is None:
            return False
        from azeo_control_trainer.core.strategy.serialization.strategy_io import (
            load_template, instantiate_template,
        )
        try:
            data = load_template(template_path)
            anchor = scene_pos if scene_pos is not None else \
                canvas.view.mapToScene(canvas.view.viewport().rect().center())
            blocks, wires = instantiate_template(
                data, offset_x=anchor.x(), offset_y=anchor.y())

            scene = canvas.scene
            undo_stack = scene.undo_stack
            start_index = undo_stack.index()
            was_dirty = canvas.dirty
            was_modified = canvas.modified_since_download
            tab_index = self._canvas_tabs.indexOf(canvas)
            old_label = (self._canvas_tabs.tabText(tab_index)
                         if tab_index >= 0 else "")

            placement_error = None
            undo_stack.beginMacro(
                f"Place Template {data.get('name', Path(template_path).stem)}")
            try:
                for block in blocks:
                    if scene.add_block(block) is None:
                        raise RuntimeError(
                            f"block '{block.instance_name}' was refused")
                for wire in wires:
                    if scene.add_wire(
                        wire.src_block_id, wire.src_terminal,
                        wire.dst_block_id, wire.dst_terminal,
                        is_bkcal=wire.is_bkcal,
                    ) is None:
                        raise RuntimeError(
                            "connection "
                            f"{wire.src_block_id}.{wire.src_terminal} -> "
                            f"{wire.dst_block_id}.{wire.dst_terminal} "
                            "was refused")
            except Exception as exc:
                placement_error = exc
            finally:
                undo_stack.endMacro()

            if placement_error is not None:
                # QUndoStack presents every child command as one macro after
                # endMacro().  Undo exactly that macro so a refused last wire
                # cannot leave an apparently valid half-template behind.
                if undo_stack.index() > start_index:
                    undo_stack.undo()
                    # Pushing an already-obsolete no-op trims the redo branch
                    # without adding a new history entry.  Without this,
                    # Ctrl+Y could resurrect the same partial template that
                    # the transaction just rejected.
                    from PySide6.QtGui import QUndoCommand
                    discard_redo = QUndoCommand()
                    discard_redo.setObsolete(True)
                    undo_stack.push(discard_redo)
                canvas.dirty = was_dirty
                canvas.modified_since_download = was_modified
                if tab_index >= 0:
                    self._canvas_tabs.setTabText(tab_index, old_label)
                self._engineering_workspace.refresh_structure()
                raise placement_error

            log.info("Placed template '%s': %d blocks, %d wires",
                     data.get("name", "?"), len(blocks), len(wires))
            self._engineering_workspace.refresh_structure()
            return True
        except Exception as e:
            log.exception("Template placement failed: %s", e)
            if not is_headless():
                QMessageBox.critical(self, "Template Error", str(e))
            return False

    def _manage_templates(self):
        from .dialogs.template_manager import TemplateManagerDialog
        dlg = TemplateManagerDialog(parent=self)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        def _on_template_accepted():
            selected = dlg.get_selected_path()
            if selected:
                self._place_template(str(selected))
        dlg.accepted.connect(_on_template_accepted)
        dlg.show()

    def _save_selection_as_template(self):
        """Save currently selected blocks as a reusable template."""
        canvas = self._active_canvas()
        if canvas is None:
            return
        from PySide6.QtWidgets import QInputDialog
        from azeo_control_trainer.core.strategy.serialization.strategy_io import save_template
        from .items.block_item import BlockItem

        selected_items = [
            item for item in canvas.scene.selectedItems()
            if isinstance(item, BlockItem)
        ]
        if not selected_items:
            QMessageBox.information(
                self, "Save Template",
                "Select one or more blocks first.")
            return

        if is_headless() and inspect.isbuiltin(QInputDialog.getText):
            log.info("Save Template name prompt skipped in headless mode")
            return False

        name, ok = QInputDialog.getText(
            self, "Save as Template", "Template name:")
        if not ok or not name:
            return

        block_ids = {item.block.id for item in selected_items}
        blocks_data = [
            canvas.scene.graph.blocks[bid].to_dict()
            for bid in block_ids
            if bid in canvas.scene.graph.blocks
        ]
        wires_data = [
            w.to_dict()
            for w in canvas.scene.graph.wires.values()
            if w.src_block_id in block_ids and w.dst_block_id in block_ids
        ]

        if blocks_data:
            min_x = min(b.get("x", 0) for b in blocks_data)
            min_y = min(b.get("y", 0) for b in blocks_data)
            for b in blocks_data:
                b["x"] = b.get("x", 0) - min_x
                b["y"] = b.get("y", 0) - min_y

        try:
            save_template(name, blocks_data, wires_data)
            QMessageBox.information(
                self, "Template Saved",
                f"Template '{name}' saved with {len(blocks_data)} blocks "
                f"and {len(wires_data)} wires.")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # ------------------------------------------------------------ resize event
    def on_tick(self):
        """Update the visible diagram and shared controller status at ~1 Hz."""
        active = self._active_canvas()
        # The executive updates every model. Painting hidden tabs delayed the
        # operator without changing what was visible; selection refreshes the
        # newly shown diagram immediately instead of waiting for this timer.
        if active is not None and active.isVisible():
            self._refresh_canvas_live(active)

        # Publish per-module state to the project tree so the hierarchy shows
        # which modules are on scan (Azeo shows this; ours showed nothing).
        self._publish_module_states()
        active_online = bool(
            active and active.runtime and active.runtime.is_online)
        if active_online != getattr(self, "_active_ui_online", None):
            # Also catches a controller/runtime transition initiated outside
            # this tab's Download and Offline commands.
            self._sync_active_canvas_ui()
        else:
            self._refresh_debug_toolbar()

        # Toolbar scan time, status bar, and props only for the active canvas
        if active_online:
            self._toolbar.update_scan_time(active.runtime.last_scan_ms)
            status = active.runtime.get_status()
            self._status_bar.update_status(status)
            if not self._status_bar.isVisible():
                self._status_bar.show()
            self._props.refresh()

        # Refresh our own faceplates. A host that owns the manager refreshes
        # it on its own tick; doing it twice would just double the work.
        if self.owns_faceplate_manager():
            try:
                self._faceplates.refresh_all()
            except Exception:                               # noqa: BLE001
                log.exception("Faceplate refresh failed")

    def _refresh_canvas_live(self, canvas):
        own_online = bool(canvas.runtime and canvas.runtime.is_online)
        interior = bool(getattr(canvas, "_composite_marker", None))
        if not own_online:
            if not interior or not any(
                isinstance(c := self._canvas_tabs.widget(i), StrategyCanvas)
                and c.runtime and c.runtime.is_online
                for i in range(self._canvas_tabs.count())
            ):
                return
            if not canvas.scene.is_live_mode():
                canvas.scene.set_live_mode(True)
                canvas.scene.set_show_wire_values(True)
        for item in canvas.scene._block_items.values():
            item.refresh()
        for wire_item in canvas.scene._wire_items.values():
            wire_item.update_live_value()

    def get_runtime(self) -> object:
        """Return the strategy runtime of the active tab (SimEngine integration)."""
        canvas = self._active_canvas()
        return canvas.runtime if canvas else None

    def auto_load_last_strategy(self):
        """Load the last saved strategy into a new tab.

        Called by MainWindow on startup so that the operator P&ID dynamos
        that reference PID blocks from the strategy continue to work.
        Falls back to the plugin's default_strategy if no last strategy
        is remembered.
        """
        from azeo_control_trainer.core.strategy.serialization.strategy_io import (
            get_last_strategy, get_strategy_dir, load_strategy,
        )
        path = get_last_strategy()

        # Fallback to plugin default strategy
        if path is None and self._plugin:
            default_name = getattr(self._plugin, "default_strategy", "")
            if default_name:
                default_path = (
                    get_strategy_dir(self._plugin.strategy_subdir)
                    / default_name
                )
                if default_path.exists():
                    path = default_path
                    log.info("Using plugin default strategy: %s", path)

        if path is None:
            return False

        try:
            graph, comments = load_strategy(path)
        except Exception as e:
            log.warning("Failed to auto-load strategy: %s", e)
            return False

        label = self._tab_label_for_path(str(path))
        canvas = self._create_canvas(label=label, file_path=str(path))
        canvas.scene.load_graph(graph, comments=comments)
        self._connect_all_block_signals(canvas.scene)
        self._props.set_block(None)
        canvas.dirty = False
        self._fit_canvas(canvas)
        log.info("Auto-loaded last strategy from %s", path)
        return True

    def refresh_project_tree(self) -> None:
        """Re-read the project area and repopulate the tree.

        Public because switching areas (File ▸ Open Project) happens from the
        window, which should not be reaching into the tab's private widgets.
        """
        self._project_tree.refresh()

    def auto_load_project(self) -> bool:
        """Load every module in the plugin's _project.json area into its own tab.

        Implements the Azeo per-loop convention: each control module (one per
        loop) + each SFC/equipment module is opened as a separate tab. The
        subsequent auto_go_online() brings them all online together. Returns
        True if at least one module loaded.
        """
        import json
        from pathlib import Path
        from azeo_control_trainer.core.strategy.serialization.strategy_io import (
            get_strategy_dir, load_strategy, set_last_strategy,
        )
        if not self._plugin:
            return False
        base = get_strategy_dir(self._plugin.strategy_subdir)
        proj = base / "_project.json"
        if not proj.exists():
            return False
        try:
            cfg = json.loads(proj.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("Failed to read _project.json: %s", e)
            return False

        # Equipment modules are deliberately excluded. An EM is a *descriptor*
        # — parameters, states and the modules it commands — not a function
        # block diagram. Loading one through `load_strategy` produces an empty
        # graph, which then opens as a blank canvas, fails validation with
        # "Strategy has no blocks", and offers itself for download. It belongs
        # in the project tree and its own properties dialog, both of which
        # already handle it. `TagDatabase.from_area` skips them for the same
        # reason.
        rels: list[str] = []
        for area in cfg.get("areas", []):
            for key in ("strategies", "sfc_modules"):
                rels.extend(area.get(key, []))
            skipped = area.get("equipment_modules", []) or []
            if skipped:
                log.info("Area '%s': %d equipment module(s) are descriptors, "
                         "not strategies — not opened as canvases: %s",
                         area.get("name", "?"), len(skipped),
                         ", ".join(str(x) for x in skipped))
        loaded = 0
        from PySide6.QtCore import QSignalBlocker
        # Native tab layout and seat-settings writes dominated bulk startup.
        # Keep the final module active/remembered, without laying out each
        # intermediate selection or rewriting the same settings file per tab.
        bar = self._canvas_tabs.tabBar()
        bar_hidden = bar.isHidden()
        updates_enabled = self._canvas_tabs.updatesEnabled()
        self._canvas_tabs.setUpdatesEnabled(False)
        bar.hide()
        try:
            with QSignalBlocker(self._canvas_tabs):
                for rel in rels:
                    path = base / Path(rel.replace("\\", "/"))
                    if not path.exists():
                        log.warning("Project module not found: %s", path)
                        continue
                    try:
                        graph, comments = load_strategy(path, remember=False)
                    except Exception as e:
                        log.warning("Failed to load module %s: %s", path, e)
                        continue
                    label = self._tab_label_for_path(str(path))
                    canvas = self._create_canvas(label=label, file_path=str(path),
                                                 defer_visuals=True, activate=False)
                    canvas.scene.load_graph(graph, comments=comments, defer_items=True)
                    loaded += 1
                if loaded:
                    self._canvas_tabs.setCurrentIndex(self._canvas_tabs.indexOf(canvas))
                    try:
                        set_last_strategy(canvas.file_path)
                    except OSError as error:
                        # A failed preference write must not discard graphs
                        # that were successfully read and opened for editing.
                        log.warning("Project opened, but last module could not be remembered: %s", error)
        finally:
            bar.setVisible(not bar_hidden)
            self._canvas_tabs.setUpdatesEnabled(updates_enabled)
        if loaded:
            self._on_canvas_tab_changed(self._canvas_tabs.currentIndex())
            self._props.set_block(None)
            log.info("Auto-loaded %d project modules from %s", loaded, base)
        return loaded > 0

    def auto_go_online(self):
        """Compile and go online silently (no message boxes).

        Brings all open canvas tabs online (multi-module support).
        """
        if self._store is None:
            return False

        self._ensure_runtimes_list()

        from azeo_control_trainer.core.strategy.engine.compiler import (
            compile_strategy, CompileError,
        )
        from azeo_control_trainer.core.strategy.engine.runtime import StrategyRuntime
        from azeo_control_trainer.core.strategy.engine.bridge import DataBridge

        # Ensure simulation is running
        main = self.window()
        if hasattr(main, 'start_simulation'):
            main.start_simulation()

        success = False
        for i in range(self._canvas_tabs.count()):
            canvas = self._canvas_tabs.widget(i)
            if not isinstance(canvas, StrategyCanvas):
                continue
            if not canvas.scene.graph.blocks:
                continue
            # Skip canvases already online (avoid duplicate runtimes in the
            # controller if auto_go_online is invoked more than once).
            existing = getattr(canvas, "runtime", None)
            if existing is not None and getattr(existing, "is_online", False):
                if canvas.download_snapshot is None:
                    self._capture_download_baseline(canvas)
                success = True
                continue

            try:
                compiled = compile_strategy(canvas.scene.graph)
            except CompileError as e:
                log.warning("Auto-compile failed for tab %d: %s", i, e)
                continue

            previous_runtime = canvas.runtime
            canvas.bridge = DataBridge(self._store)
            canvas.runtime = StrategyRuntime()
            canvas.runtime.load(compiled, canvas.bridge)
            self._carry_debug_breakpoints(previous_runtime, canvas.runtime)

            if canvas.runtime.go_online():
                import time as _time
                canvas.scene.set_structure_locked(True)
                self._store.add_strategy_runtime(canvas.runtime, plugin=self._plugin)
                canvas.scene.set_show_wire_values(True)
                for wire_item in canvas.scene._wire_items.values():
                    wire_item.set_show_value(True)
                    wire_item.update_live_value()
                canvas.scene.set_live_mode(True)
                canvas.scene.apply_exec_order(compiled.exec_order)
                canvas.downloaded_at = _time.time()
                canvas.modified_since_download = False
                self._capture_download_baseline(canvas)
                tab_name = self._canvas_tabs.tabText(i)
                self._canvas_tabs.setTabText(i, f"\u25cf {tab_name}")
                log.info("Auto-started module '%s' ONLINE", tab_name)
                success = True

        if success:
            self._toolbar._btn_show_values.setChecked(True)
            self._sync_legacy_refs()

        return success

    def save_dirty_modules(self):
        """Save the requested module edits without stopping their shared host."""
        for label, canvas in self.get_unsaved_canvases():
            if not canvas.dirty:
                continue
            if not getattr(canvas, "file_path", None):
                log.info("Module '%s' has no file path; not auto-saving", label)
                continue
            self._save(canvas)

    def cleanup(self, save_dirty: bool = False):
        """Release designer resources when the designer is closed.

        Deliberately does NOT take modules offline or clear the store's
        runtimes: closing the designer (a popped-out dialog, or the standalone
        Control Designer window opened from the running app) must not drop the
        plant's regulatory control while the engine and operator graphics keep
        running. At real application shutdown the process exits and the
        runtimes go with it.

        Nor does it save by default. Silently rewriting every open module on
        close overwrote untouched source files; the window asks the user first
        and passes ``save_dirty=True`` only when they choose to save.
        """
        if save_dirty:
            self.save_dirty_modules()

        # Stop monitoring panel timer
        if hasattr(self, '_monitoring_panel') and self._monitoring_panel is not None:
            self._monitoring_panel.stop()
        if hasattr(self, "_engineering_live_timer"):
            self._engineering_live_timer.stop()

        # Close the faceplates this tab opened. Only ours: a host's manager
        # serves the whole application and keeps running.
        if self.owns_faceplate_manager():
            self._faceplates.cleanup()

        if self._tagdb_dialog is not None:
            self._tagdb_dialog.close()
            self._tagdb_dialog = None
        for dialog in list(self._command_dialogs.values()):
            try:
                dialog.close()
            except RuntimeError:
                pass
        self._command_dialogs.clear()

        # The scan timer stops with the window that owns it, but the modules
        # stay online — closing the editor must not shed control (see the
        # docstring above). A host executive is left alone.
        if (self._executive is not None
                and getattr(self.window(), "_executive", None) is None):
            self._executive.stop()

    def has_unsaved_changes(self) -> list[str]:
        """Labels of open modules with unsaved edits."""
        return [label for label, canvas in self.get_unsaved_canvases()
                if canvas.dirty]

    # ------------------------------------------------------------ auto-save helpers
    def get_unsaved_canvases(self) -> list[tuple[str, 'StrategyCanvas']]:
        """Return list of (label, canvas) pairs for all open canvases with blocks.

        Returns canvases that contain at least one block and are candidates
        for auto-save.
        """
        result = []
        for i in range(self._canvas_tabs.count()):
            canvas = self._canvas_tabs.widget(i)
            if not isinstance(canvas, StrategyCanvas):
                continue
            if not canvas.scene.graph.blocks:
                continue
            label = self._canvas_tabs.tabText(i)
            # Strip online indicator if present
            if label.startswith("\u25cf "):
                label = label[2:]
            result.append((label, canvas))
        return result

    def auto_save_canvases(self, directory: 'Path') -> None:
        """Auto-save all open canvases with blocks to the given directory.

        Each canvas is saved as ``<label>.json`` in the directory.
        Uses the same serialization format as the normal strategy save.
        """
        from pathlib import Path
        import json

        directory = Path(directory)
        canvases = self.get_unsaved_canvases()
        if not canvases:
            return

        directory.mkdir(parents=True, exist_ok=True)

        for label, canvas in canvases:
            try:
                data = canvas.scene.graph.to_dict()
                comments = canvas.scene.get_comments_data()
                if comments:
                    data["comments"] = comments
                # Include the original file_path for recovery context
                if canvas.file_path:
                    data["_autosave_original_path"] = canvas.file_path

                safe_label = label.replace(" ", "_").replace("/", "_")
                save_path = directory / f"{safe_label}.json"
                with open(save_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, default=str)
                log.debug("Auto-saved canvas '%s' to %s", label, save_path)
            except Exception:
                log.debug("Failed to auto-save canvas '%s'", label,
                          exc_info=True)

    def check_auto_save_recovery(self, directory: 'Path') -> None:
        """Check for auto-saved strategies and offer to restore them.

        On startup, if auto-saved strategy files exist in the directory,
        prompt the user and load them as new canvas tabs.
        """
        from pathlib import Path
        import json

        directory = Path(directory)
        if not directory.exists():
            return

        autosave_files = sorted(directory.glob("*.json"))
        if not autosave_files:
            return

        # A missing operator is not a decision to discard recovery data.
        # Returning before the No branch preserves every autosave for the next
        # interactive launch instead of silently deleting it in CI.
        if is_headless():
            log.info(
                "Strategy recovery prompt skipped headlessly; preserving %d "
                "autosave file(s)", len(autosave_files))
            return

        reply = QMessageBox.question(
            self, "Strategy Recovery",
            f"Found {len(autosave_files)} auto-saved strategy canvas(es).\n\n"
            "Would you like to restore them?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No)

        if reply != QMessageBox.Yes:
            # Clean up auto-save files since user declined
            for f in autosave_files:
                try:
                    f.unlink()
                except OSError:
                    pass
            return

        from azeo_control_trainer.core.strategy.serialization.strategy_io import load_strategy

        restored = 0
        for auto_file in autosave_files:
            try:
                graph, comments = load_strategy(auto_file)

                # Determine label: prefer the graph name, fall back to stem
                label = graph.name if graph.name != "Untitled" else auto_file.stem

                # Check if this canvas is already open (by original path)
                with open(auto_file, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                original_path = raw.get("_autosave_original_path")

                if original_path and self._find_tab_by_path(original_path) >= 0:
                    # Already open, skip
                    continue

                canvas = self._create_canvas(
                    label=label,
                    file_path=original_path)
                canvas.scene.load_graph(graph, comments=comments)
                self._connect_all_block_signals(canvas.scene)
                restored += 1
                log.info("Restored auto-saved strategy '%s' from %s",
                         label, auto_file)
            except Exception:
                log.warning("Failed to restore auto-save %s", auto_file,
                            exc_info=True)

        # Clean up auto-save files after restoration
        for f in autosave_files:
            try:
                f.unlink()
            except OSError:
                pass

        if restored > 0:
            log.info("Restored %d strategy canvas(es) from auto-save", restored)

    # ------------------------------------------------------------ backward compat
    # These properties provide access to the active canvas's scene/view
    # for any external code that still references self._scene / self._view.
    @property
    def _scene(self) -> StrategyScene | None:
        return self._active_scene()

    @property
    def _view(self) -> StrategyView | None:
        return self._active_view()
