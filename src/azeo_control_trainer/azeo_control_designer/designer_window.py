"""Standalone Strategy Designer Window — Azeo Control Designer style.

A non-modal QMainWindow that wraps the StrategyDesignerTab, allowing the
strategy designer to run as an independent window alongside the operator
HMI.  Launched from MainWindow via View menu or Ctrl+Shift+S.
"""
from __future__ import annotations

from azeo_control_trainer.core.hmi.compatibility import display_root

from azeo_control_trainer.core.presentation.menu_style import MENU_QSS

from azeo_control_trainer.core.presentation.authoring_style import AUTHORING_CHROME_QSS

import getpass
import logging
import weakref

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMainWindow

from azeo_control_trainer.core.presentation.headless import is_headless as _is_headless
from .designer_tab import StrategyDesignerTab

log = logging.getLogger("strategy.designer_window")




class StrategyDesignerWindow(QMainWindow):
    """Standalone non-modal strategy designer window."""

    def __init__(self, store=None, provider=None, plugin=None,
                 main_window=None, parent=None):
        super().__init__(parent)
        self._store = store
        self._plugin = plugin
        self._main_window = main_window
        self._live_station_window = None
        self._pvm_studio_window = None

        self._setup_window()

        # Create the designer tab as the central widget. It must exist before
        # the menus are built: every menu action routes through a ribbon signal
        # so the menu bar and the ribbon share one code path.
        self._designer = StrategyDesignerTab(
            store=store, provider=provider, plugin=plugin, parent=self)
        self.setCentralWidget(self._designer)

        self._build_menus()
        self._connect_ribbon_fallbacks()

        # Tick timer for live updates (1 Hz)
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._on_tick)
        self._tick_timer.start(1000)

    def _setup_window(self):
        """Configure window properties."""
        plugin_name = self._plugin.display_name if self._plugin else "Simulator"
        self.setWindowTitle(f"Azeo Control Designer — {plugin_name}")
        # Set per-window as well as per-application: a window opened before
        # the application icon is applied, or in an embedding host that sets
        # its own, would otherwise show the wrong one.
        from azeo_control_trainer.core.presentation.app_icon import get_app_icon
        from azeo_control_trainer.core.presentation.wheel_guard import install_wheel_guard

        # Scrolling must never edit a value — app-wide, once.
        install_wheel_guard()
        self.setWindowIcon(get_app_icon(application_id="control_designer"))
        available = self.screen().availableGeometry()
        self.setMinimumSize(min(860, available.width() - 40),
                            min(500, available.height() - 60))
        self.resize(min(1400, available.width() - 40),
                    min(850, available.height() - 60))

        # Use the same engineering chrome as Explorer and Graphics Designer.
        self.setStyleSheet(AUTHORING_CHROME_QSS + MENU_QSS)

        # Status bar
        self.statusBar().showMessage("Control Designer — Ready")

    # ──────────────────────────────────────────────────────────────
    # Ribbon plumbing
    # ──────────────────────────────────────────────────────────────

    @property
    def _ribbon(self):
        """The designer's ribbon bar — the single owner of the command signals."""
        return self._designer._toolbar

    def _has_receivers(self, signal_name: str) -> bool:
        """True when something is already listening to ``signal_name``."""
        try:
            return self._ribbon.receivers(f"2{signal_name}()") > 0
        except Exception:      # pragma: no cover — Qt introspection quirk
            return False

    def _connect_ribbon_fallbacks(self):
        """Handle ribbon commands the designer tab does not implement yet.

        ``StrategyDesignerTab`` owns the handlers for most ribbon signals. A
        few Azeo-parity commands have no handler there yet; rather than let
        their menu items and buttons be silent no-ops, the window provides the
        implementation — but only when nothing else has claimed the signal, so
        moving a handler into the designer tab automatically takes precedence.
        """
        for name, slot in (
            ("saveAsRequested", self._save_strategy_as),
            ("selectAllRequested", self._select_all),
            ("deleteRequested", self._delete_selection),
            ("watchWindowRequested", self._open_watch_window),
        ):
            if self._has_receivers(name):
                continue
            getattr(self._ribbon, name).connect(slot)

    def _build_menus(self):
        """Build one icon-led menu surface over the ribbon commands."""
        from azeo_control_trainer.core.presentation.menu_style import studio_menu

        mb = self.menuBar()

        def dropdown(title: str):
            menu = studio_menu(parent=self)
            menu.setTitle(title)
            mb.addMenu(menu)
            return menu

        # ── File menu ──
        file_menu = dropdown("&File")
        file_menu.addAction("&New Strategy", self._new_strategy,
                            QKeySequence("Ctrl+N"))
        file_menu.addAction("&Open...", self._open_strategy,
                            QKeySequence("Ctrl+O"))
        file_menu.addAction("&Save", self._save_strategy,
                            QKeySequence("Ctrl+S"))
        file_menu.addAction("Save &As...", self._ribbon.saveAsRequested.emit,
                            QKeySequence("Ctrl+Shift+S"))
        file_menu.addSeparator()
        # Closing a module and closing the application are different actions.
        # Only "Close Window" existed, so the menu offered no way to put a
        # module down — the tab's X button was the sole route.
        file_menu.addAction("&Close Module", self._close_module,
                            QKeySequence("Ctrl+F4"))
        file_menu.addAction("Close A&ll Modules", self._close_all_modules)
        file_menu.addSeparator()
        file_menu.addAction("Open &Project...", self._open_project)
        file_menu.addAction("C&lose Project", self._close_project)
        file_menu.addSeparator()
        file_menu.addAction("&Print...", self._ribbon.printRequested.emit,
                            QKeySequence("Ctrl+P"))
        file_menu.addAction("Print Pre&view...",
                            self._ribbon.printPreviewRequested.emit)
        file_menu.addAction("Module &Report...",
                            self._ribbon.moduleReportRequested.emit)
        file_menu.addSeparator()
        file_menu.addAction("Close &Window", self.close,
                            QKeySequence("Ctrl+W"))

        # ── Edit menu ──
        # Azeo's Edit menu carries the full standard set; ours held only
        # Undo / Redo / Find. Every entry emits the ribbon's signal so the menu
        # and the ribbon buttons run exactly the same handler.
        edit_menu = dropdown("&Edit")
        edit_menu.addAction("&Undo", self._ribbon.undoRequested.emit,
                            QKeySequence("Ctrl+Z"))
        edit_menu.addAction("&Redo", self._ribbon.redoRequested.emit,
                            QKeySequence("Ctrl+Y"))
        edit_menu.addSeparator()
        edit_menu.addAction("Cu&t", self._ribbon.cutRequested.emit,
                            QKeySequence("Ctrl+X"))
        edit_menu.addAction("&Copy", self._ribbon.copyRequested.emit,
                            QKeySequence("Ctrl+C"))
        edit_menu.addAction("&Paste", self._ribbon.pasteRequested.emit,
                            QKeySequence("Ctrl+V"))
        edit_menu.addAction("&Delete", self._ribbon.deleteRequested.emit,
                            QKeySequence("Del"))
        edit_menu.addSeparator()
        edit_menu.addAction("Select &All", self._ribbon.selectAllRequested.emit,
                            QKeySequence("Ctrl+A"))
        edit_menu.addSeparator()
        edit_menu.addAction("&Find Block...", self._find_block,
                            QKeySequence("Ctrl+F"))

        # ── Insert menu ──
        # The palette is drag-only and the canvas right-click menu skips whole
        # categories, so some registered block types had no menu path at all.
        # This menu exposes every category.
        insert_menu = dropdown("&Insert")
        self._fb_menu = studio_menu(parent=self)
        self._fb_menu.setTitle("&Function Block")
        self._fb_menu.aboutToShow.connect(self._populate_function_block_menu)
        insert_menu.addMenu(self._fb_menu)
        insert_menu.addSeparator()
        insert_menu.addAction("&Comment", self._add_comment)

        # ── Module menu ──
        module_menu = dropdown("&Module")
        module_menu.addAction("&Compile", self._compile,
                              QKeySequence("F7"))
        # Download and Go On Line are separate operations, as in Azeo:
        # Download installs the configuration, Go On Line only attaches the
        # live view to modules that are already running.
        module_menu.addAction("&Download", self._download,
                              QKeySequence("F5"))
        module_menu.addAction("Go On &Line", self._go_online)
        module_menu.addAction("Go O&ff Line", self._go_offline,
                              QKeySequence("Shift+F5"))
        module_menu.addSeparator()
        module_menu.addAction("Module &Properties...",
                              self._ribbon.modulePropertiesRequested.emit)
        module_menu.addAction("Module Para&meters...",
                              self._ribbon.moduleParametersRequested.emit)
        module_menu.addAction("&Named Sets...",
                              self._ribbon.namedSetsRequested.emit)
        module_menu.addAction("Cross &Reference...",
                              self._ribbon.crossReferenceRequested.emit)
        # Execution order is compiler-derived; no manual-order document exists
        # yet. I/O assignment is performed in Explorer. Do not advertise
        # controls that cannot change either model.
        module_menu.addSeparator()
        module_menu.addAction("&Upload (Controller -> Project)...",
                              self._upload_to_project)
        module_menu.addAction("&Compare Parameters...",
                              self._ribbon.compareParametersRequested.emit,
                              QKeySequence("Ctrl+K"))
        module_menu.addSeparator()
        module_menu.addAction("Controller &Status...",
                              self._controller_status)
        module_menu.addAction("Controller &Diagnostics...",
                              self._controller_diagnostics)
        module_menu.addSeparator()
        module_menu.addAction("Save Check&point...",
                              self._save_checkpoint, QKeySequence("Ctrl+Shift+C"))
        module_menu.addAction("&Restore Checkpoint...",
                              self._restore_checkpoint)
        module_menu.addSeparator()
        module_menu.addAction("Controller Si&mulator...",
                              self._controller_simulator,
                              QKeySequence("Ctrl+Shift+M"))

        # ── View menu ──
        view_menu = dropdown("&View")
        view_menu.addAction("Zoom to &Fit",
                            self._zoom_fit, QKeySequence("Ctrl+0"))
        view_menu.addAction("Zoom &In", self._zoom_in,
                            QKeySequence("Ctrl+="))
        view_menu.addAction("Zoom &Out", self._zoom_out,
                            QKeySequence("Ctrl+-"))
        view_menu.addSeparator()

        act_exec = QAction("Show &Execution Order", self)
        act_exec.setCheckable(True)
        act_exec.toggled.connect(self._toggle_exec_order)
        view_menu.addAction(act_exec)

        act_values = QAction("Show &Wire Values", self)
        act_values.setCheckable(True)
        act_values.toggled.connect(self._toggle_wire_values)
        view_menu.addAction(act_values)

        view_menu.addSeparator()
        view_menu.addAction("Auto &Arrange", self._auto_arrange)
        view_menu.addSeparator()
        view_menu.addAction("Hide &Unused Pins", self._hide_unused_pins)
        view_menu.addAction("Show A&ll Pins", self._show_all_pins)
        view_menu.addSeparator()
        # ONE graphics designer and ONE console. DynaLive was a second
        # of each — its own document format, renderer and faceplate
        # family — and it is archived; what is left is the PVM half,
        # so the entries take the plain names rather than keeping
        # qualifiers that distinguished them from something gone.
        view_menu.addAction("&Graphics Designer...", self._pvm_studio,
                            QKeySequence("Ctrl+Shift+G"))
        view_menu.addAction("&Operator Station...", self._live_station,
                            QKeySequence("Ctrl+Shift+O"))
        view_menu.addAction("&Tag Database...", self._tag_database,
                            QKeySequence("Ctrl+T"))
        view_menu.addAction("&Monitoring Tab", self._toggle_monitoring,
                            QKeySequence("Ctrl+M"))
        # The watch window used to be reachable only from a block's right-click
        # menu, so closing it stranded the panel.
        view_menu.addAction("&Watch Window", self._ribbon.watchWindowRequested.emit,
                            QKeySequence("Ctrl+Shift+W"))

        # ── Tools menu ──
        tools_menu = dropdown("&Tools")
        tools_menu.addAction("&Compare Strategies...",
                             self._compare_strategies)
        tools_menu.addAction("&Version History...",
                             self._version_history)
        tools_menu.addSeparator()
        tools_menu.addAction("&Template Manager...",
                             self._manage_templates)
        tools_menu.addAction("Control Module &Classes...",
                             self._ribbon.moduleClassesRequested.emit)
        tools_menu.addSeparator()
        tools_menu.addAction("&Data Log Configuration...",
                             self._datalog_config)

        # ── Help menu ──
        help_menu = dropdown("&Help")
        from azeo_control_trainer.core.presentation.product_help import add_help_action
        add_help_action(help_menu, self)
        help_menu.addAction("Control Designer &Help...", self._show_help,
                            QKeySequence("F1"))
        help_menu.addAction("&Block Reference...", self._show_block_reference)
        help_menu.addAction("&Keyboard Shortcuts...", self._show_shortcuts)
        help_menu.addSeparator()
        help_menu.addAction("SFC &Programming Guide...", self._show_sfc_guide)
        help_menu.addAction("&Equipment Modules...", self._show_em_help)
        help_menu.addAction("PID && &BKCAL Wiring...", self._show_bkcal_help)
        help_menu.addAction("&Design Patterns...", self._show_design_patterns)
        help_menu.addSeparator()
        help_menu.addAction("&Knowledge Builder...", self._knowledge_builder,
                            QKeySequence("Shift+F1"))
        help_menu.addSeparator()
        help_menu.addAction("&About Control Designer...", self._show_about)

    # ── Tick ──
    def _on_tick(self):
        """1 Hz update for live data."""
        if self.isVisible():
            self._designer.on_tick()

    # ── File actions ──
    def _new_strategy(self):
        self._designer._new_strategy()

    def _open_strategy(self):
        from PySide6.QtWidgets import QFileDialog
        from azeo_control_trainer.core.strategy.serialization import strategy_io
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Strategy", str(strategy_io.STRATEGY_DIR),
            "Strategy JSON (*.json)")
        if path:
            self._designer._load(path)

    def _close_module(self):
        """Close the module in the active tab (Ctrl+F4)."""
        tabs = self._designer._canvas_tabs
        idx = tabs.currentIndex()
        if idx >= 0:
            self._designer._close_canvas_tab(idx)

    def _close_all_modules(self):
        """Close every open module tab, newest first."""
        tabs = self._designer._canvas_tabs
        for idx in range(tabs.count() - 1, -1, -1):
            self._designer._close_canvas_tab(idx)

    def _open_project(self):
        """Switch to another strategy area and open its modules.

        Rebinds ``strategy_io.STRATEGY_DIR`` through the module — never a
        from-imported copy, which would leave the project tree pointing at the
        old area — then reloads the tree and the area's modules.
        """
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog
        from azeo_control_trainer.core.strategy.serialization import strategy_io

        start = str(Path(strategy_io.STRATEGY_DIR).parent)
        chosen = QFileDialog.getExistingDirectory(
            self, "Open Project Area", start)
        if not chosen:
            return

        area = Path(chosen)
        self._close_all_modules()
        strategy_io.STRATEGY_DIR = area
        plugin = getattr(self._designer, "_plugin", None)
        if plugin is not None:
            plugin.strategy_subdir = str(area.resolve())
            plugin.display_name = area.name
        self.setWindowTitle(
            f"Azeo Control Designer — {area.name} — Azeo Control Trainer")
        self._designer.refresh_project_tree()
        if not self._designer.auto_load_project():
            log.info("Area '%s' has no _project.json", area.name)

    def _close_project(self):
        """Close every module and leave the area empty, but still selected."""
        self._close_all_modules()
        self._designer.refresh_project_tree()

    def _save_strategy(self):
        self._designer._save()

    def _save_strategy_as(self):
        """Fallback for ``saveAsRequested`` (see _connect_ribbon_fallbacks)."""
        self._designer._save_as()

    # ── Edit actions ──
    def _select_all(self):
        """Fallback for ``selectAllRequested`` using the tab's one command."""
        self._designer._select_all()

    def _delete_selection(self):
        """Fallback for ``deleteRequested`` using the tab's one command."""
        self._designer._delete_selection()

    def _open_watch_window(self):
        """Fallback for ``watchWindowRequested``: raise the watch panel."""
        from .widgets.watch_panel import open_watch_panel
        open_watch_panel(self)

    def _find_block(self):
        self._designer._toolbar.focus_search()

    # ── Insert actions ──
    def _add_comment(self):
        self._designer._add_comment()

    def _insert_position(self):
        """Scene coordinates at the centre of the active view."""
        view = self._designer._active_view()
        if view is None:
            return None
        return view.mapToScene(view.viewport().rect().center())

    def _populate_function_block_menu(self):
        """Fill the Insert ▸ Function Block menu (lazily — 140+ block types).

        Every registered category is listed, including APC and SFC which the
        canvas right-click menu omits.
        """
        menu = self._fb_menu
        if menu.actions():
            return
        from azeo_control_trainer.core.strategy.model.block_base import BlockCategory
        from azeo_control_trainer.core.strategy.model.block_registry import registry
        from azeo_control_trainer.core.presentation.function_block_icons import (
            make_block_qicon,
        )
        by_cat = registry.by_category()
        for cat in BlockCategory:
            blocks = by_cat.get(cat, [])
            if not blocks:
                continue
            sub = menu.addMenu(cat.value)
            for cls in sorted(blocks, key=lambda c: c.display_name):
                act = sub.addAction(
                    make_block_qicon(cls.block_type, category=cat, size=16),
                    f"{cls.display_name} ({cls.block_type})",
                )
                act.triggered.connect(
                    lambda checked=False, bt=cls.block_type:
                        self._insert_block(bt))

    def _insert_block(self, block_type: str):
        scene = self._designer._active_scene()
        if scene is None:
            return
        scene.add_block_by_type(block_type, self._insert_position())

    # ── Module actions ──
    def _compile(self):
        self._designer._compile()

    def _download(self):
        """Compile + install the module into the runtime (Azeo Download)."""
        self._ribbon.downloadRequested.emit()

    def _go_online(self):
        """Attach the live view to already-running modules (Azeo Go On Line)."""
        self._ribbon.goOnlineRequested.emit()

    def _go_offline(self):
        self._ribbon.goOfflineRequested.emit()

    def _controller_status(self):
        self._designer._show_controller_status()

    def _controller_diagnostics(self):
        self._designer._show_diagnostics()

    def _compare_parameters(self):
        self._designer._show_compare_parameters()

    def _upload_to_project(self):
        self._designer._show_upload_dialog()

    def _save_checkpoint(self):
        self._designer._show_checkpoint_dialog("save")

    def _restore_checkpoint(self):
        self._designer._show_checkpoint_dialog("restore")

    def _controller_simulator(self):
        self._designer._show_controller_simulator()

    # ── View actions ──
    def _zoom_fit(self):
        view = self._designer._active_view()
        if view:
            view.zoom_to_fit()

    def _zoom_in(self):
        view = self._designer._active_view()
        if view:
            view.set_zoom(view._zoom * 1.2)

    def _zoom_out(self):
        view = self._designer._active_view()
        if view:
            view.set_zoom(view._zoom / 1.2)

    def _toggle_exec_order(self, checked):
        self._designer._toggle_exec_order(checked)

    def _toggle_wire_values(self, checked):
        self._designer._toggle_show_values(checked)

    def _auto_arrange(self):
        self._designer._auto_arrange()

    def _hide_unused_pins(self):
        self._ribbon.hideUnusedPinsRequested.emit()

    def _show_all_pins(self):
        self._ribbon.showAllPinsRequested.emit()

    def _toggle_monitoring(self):
        self._designer._show_monitoring_tab()

    def _tag_database(self):
        """Browse the addressable namespace derived from the open modules."""
        self._designer.show_tag_database()

    from azeo_control_trainer.core.presentation.installed_apps import requires_component

    @requires_component("graphics_designer")
    def _pvm_studio(self):
        """Azeo Graphics Designer — the type-driven PVM display studio.

        Sheet-01 anatomy: display tabs over the store, Control/Library
        explorer, insert tools, Test mode, live health in the status
        bar. Drop a block from the control browser, get a bound,
        correctly ranged PVM — zero dialogs unless the type has more
        than one class. Displays live under the area's `displays/pvm/`
        with the sheet-05 lifecycle: draft, gated publish, history,
        one-action revert, single-writer lock.
        """
        existing = getattr(self, "_pvm_studio_window", None)
        if existing is not None:
            # The application command means "show Graphics Designer", not
            # "construct another copy". A hidden/minimized existing window
            # was previously left behind Control Designer, making every later
            # click look inert while also paying the full project load again.
            if existing.isMinimized():
                existing.showNormal()
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return existing

        from azeo_control_trainer.azeo_graphics_designer import (
            GraphicsDesignerWindow,
        )
        from azeo_control_trainer.core.strategy.serialization import (
            strategy_io,
        )

        owner_ref = weakref.ref(self)

        def graphs():
            from shiboken6 import isValid
            owner = owner_ref()
            if owner is None or not isValid(owner):
                return {}
            return {graph.name: graph
                    for graph in owner._designer.open_graphs()}

        root = display_root(strategy_io.STRATEGY_DIR)
        window = GraphicsDesignerWindow(
            graphs,
            root,
            area_name=strategy_io.STRATEGY_DIR.name,
        )
        # A native owner ties the editor's taskbar/focus and minimize behavior
        # to the hidden Control Designer host. Retain the independent window in
        # Python instead, and release the closed session before reactivation.
        window.setAttribute(Qt.WA_DeleteOnClose)
        self._pvm_studio_window = window
        window_ref = weakref.ref(window)

        def forget_window(*_args):
            owner = owner_ref()
            if owner is not None and owner._pvm_studio_window is window_ref():
                owner._pvm_studio_window = None

        window.closed.connect(forget_window)
        window.destroyed.connect(forget_window)
        self.destroyed.connect(window.close)
        window.show()
        window.raise_()
        window.activateWindow()
        return window

    @requires_component("control_designer")
    def open_module(self, module: str) -> bool:
        """Raise Control Designer on the requested already-loaded module."""
        found = False
        tabs = self._designer._canvas_tabs
        for index in range(tabs.count()):
            canvas = tabs.widget(index)
            graph = getattr(getattr(canvas, "scene", None), "graph", None)
            if graph is not None and graph.name == module:
                tabs.setCurrentIndex(index)
                found = True
                break
        self.show()
        self.raise_()
        self.activateWindow()
        return found

    @requires_component("operator_station")
    def _live_station(self, *, dedicated: bool = False):
        """Open the Azeo Operator Station console over the area's PUBLISHED PVMs.

        Nothing here reads a display file. If the area has PVM displays
        with drafts but no releases, the console opens empty and says
        so — which is the lesson: publishing is what deploys, and an
        engineer who has not published has not deployed.
        """
        from pathlib import Path

        from azeo_control_trainer.core.hmi.pvms.publishing import (
            DisplayStore,
        )
        from azeo_control_trainer.azeo_operator_station import (
            ConsoleSettings,
            PvmDeployment,
            OperatorStationWindow,
        )
        from azeo_control_trainer.core.hmi.pvms.rendering.chrome import (
            STUDIO_THEME,
        )
        from azeo_control_trainer.core.strategy.serialization import strategy_io

        existing = getattr(self, "_live_station_window", None)
        if existing is not None and getattr(existing, "_closing", False):
            existing.deleteLater()
            self._live_station_window = None
            existing = None
        if existing is not None:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return existing

        area = Path(strategy_io.STRATEGY_DIR)
        store = DisplayStore(display_root(area))
        console_id = "CON-01"
        deployment = PvmDeployment(store, workstation=console_id)
        def graphs():
            # A station must follow downloads and offline transitions after
            # opening. A captured dictionary kept retired controller graphs
            # readable and writable for the entire operator session.
            return {runtime.compiled.graph.name: runtime.compiled.graph
                    for runtime in self._store.get_strategy_runtimes()
                    if runtime.is_online and runtime.compiled is not None}
        from azeo_control_trainer.core.simulation.workbench import OperatorChangeJournal
        from azeo_control_trainer.config.paths import data_dir
        change_journal = OperatorChangeJournal(
            data_dir() / "simulation" / area.name / "operator_changes.sqlite")

        def record_operator_change(path, value):
            if not change_journal.recording:
                return
            driver = getattr(self._store, "field_io_driver", None)
            describe = getattr(driver, "process_simulation_capabilities", None)
            process = describe() if callable(describe) else {}
            change_journal.append(
                sim_time=float(process.get("sim_time") or 0.0),
                actor=getpass.getuser(), action="tag_write",
                target=str(path), value=value)

        from azeo_control_trainer.core.simulation.workbench import SimulationWorkbenchService
        import hashlib
        history_identity = hashlib.sha256(f"{area.resolve()}|{console_id}".encode()).hexdigest()[:20]
        simulation_service = SimulationWorkbenchService(
            self._store, getattr(self._store, "field_io_driver", None), area,
            actor=getpass.getuser(), executive=self._designer.controller_executive(),
            journal_path=change_journal.path)
        window = OperatorStationWindow(
            deployment, graphs,
            settings=ConsoleSettings(console_id=console_id,
                                     user=getpass.getuser(),
                                     span=area.name.upper(),
                                     # The SAME theme the PVM studio
                                     # authors against. WYSIWYG is a
                                     # comparison: a station on a
                                     # different ground means the
                                     # engineer never saw what the
                                     # operator gets.
                                     theme=STUDIO_THEME,
                                     azeo_chrome=True),
            config_root=display_root(area),
            control_designer_opener=self.open_module,
            operator_change_recorder=record_operator_change,
            simulation_service=simulation_service,
            training_root=data_dir() / "simulation" / area.name / "training",
            history_path=data_dir() / "history" / history_identity / "history.sqlite")
        window.setWindowTitle(
            "Azeo Operator Station — %s (published)" % area.name)
        window.resize(1500, 880)
        window.setAttribute(Qt.WA_DeleteOnClose, True)
        window.destroyed.connect(
            lambda *_: setattr(self, "_live_station_window", None)
            if self._live_station_window is window else None)
        self._live_station_window = window
        if dedicated:
            # A console launched as the workstation's primary application is
            # not an engineering child window.  Give the process display the
            # complete screen; F11 remains the explicit maintenance escape.
            window.set_window_mode(True)
        else:
            window.show()
        return window

    # ── Tools actions ──
    def _compare_strategies(self):
        self._designer._compare_strategies()

    def _version_history(self):
        self._designer._version_history()

    def _manage_templates(self):
        self._designer._manage_templates()

    def _datalog_config(self):
        self._designer._show_datalog_config()

    # ── Help actions ──
    # Topic indices: 0=Getting Started, 1=Block Reference, 2=PID & BKCAL,
    # 3=SFC Programming, 4=Equipment Modules, 5=Keyboard Shortcuts,
    # 6=Design Patterns, 7=Compilation & Runtime, 8=Strategy Files,
    # 9=About Control Designer
    def _show_help(self):
        self._open_help_dialog(0)

    def _show_block_reference(self):
        self._open_help_dialog(1)

    def _show_shortcuts(self):
        self._open_help_dialog(5)

    def _show_sfc_guide(self):
        self._open_help_dialog(3)

    def _show_em_help(self):
        self._open_help_dialog(4)

    def _show_bkcal_help(self):
        self._open_help_dialog(2)

    def _show_design_patterns(self):
        self._open_help_dialog(6)

    def _show_about(self):
        self._open_help_dialog(9)

    def _knowledge_builder(self):
        self._designer._show_knowledge_builder()

    def _open_help_dialog(self, topic_index: int = 0):
        from .dialogs.help_dialog import ControlDesignerHelpDialog
        dlg = ControlDesignerHelpDialog(self)
        dlg._topic_list.setCurrentRow(topic_index)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.show()

    # ── Window actions ──
    def _show_main_window(self):
        """Open or raise the workstation's one operator station.

        Explorer owns workstation assignment and therefore gets first refusal.
        A standalone Control Designer has no Explorer, so it opens the same PVM
        station directly. The old implementation only checked a legacy host
        pointer and silently did nothing in the normal application launch.
        """
        explorer = getattr(self, "explorer", None)
        opener = getattr(explorer, "open_operator_station", None)
        if callable(opener):
            if not opener():
                return None
            return self._live_station_window
        if self._main_window:
            self._main_window.raise_()
            self._main_window.activateWindow()
            return self._main_window
        return self._live_station()

    # ── Pass-through for designer_tab ──
    def start_simulation(self):
        """Delegate to the main window's start_simulation."""
        if self._main_window and hasattr(self._main_window, 'start_simulation'):
            self._main_window.start_simulation()

    @property
    def _faceplate_mgr(self):
        """Delegate faceplate manager to main window."""
        if self._main_window:
            return getattr(self._main_window, '_faceplate_mgr', None)
        return None

    def _open_historian_popup(self, tag=None, module: str = "", tags=None):
        """Open Process History View and preserve every explicitly chosen pen."""
        paths = [str(path) for path in (tags or ([tag] if tag else []))
                 if str(path or "").strip()]
        legacy = getattr(self._main_window, '_open_historian_popup', None)
        if callable(legacy):
            # Hosts predating multi-pen launch accept only ``tag``. Inspecting
            # the signature avoids treating a TypeError raised *inside* the
            # host as evidence that it has the old API.
            import inspect
            parameters = inspect.signature(legacy).parameters
            if "tags" in parameters or any(
                    item.kind is inspect.Parameter.VAR_KEYWORD
                    for item in parameters.values()):
                return legacy(tags=paths, module=module)
            return legacy(tag=paths[0] if paths else tag)

        station = self._show_main_window()
        history = getattr(station, "open_process_history", None)
        if callable(history):
            return history(module, paths=paths)

        message = "Process History View is unavailable on this workstation."
        log.error(message)
        if not _is_headless():
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Process History View", message)
        return None

    def _open_faceplate(self, tag):
        """Delegate faceplate to main window."""
        if self._main_window and hasattr(self._main_window, '_open_faceplate'):
            self._main_window._open_faceplate(tag)

    # ── Public API ──
    @property
    def designer(self) -> StrategyDesignerTab:
        """Access the underlying designer tab."""
        return self._designer

    def auto_load_and_go_online(self):
        """Auto-load strategies and go online (called on startup).

        If the plugin opts into project autoloading (``autoload_project``) and a
        ``_project.json`` is present, every module in the area (one per loop, per
        the Azeo control-module convention) is loaded and brought online.
        Otherwise the single last/default strategy is loaded.
        """
        loaded = False
        if getattr(self._plugin, "autoload_project", False):
            loaded = self._designer.auto_load_project()
        if not loaded:
            loaded = self._designer.auto_load_last_strategy()
        if loaded:
            QTimer.singleShot(500, self._designer.auto_go_online)

    # ── Lifecycle ──
    def closeEvent(self, event):
        """Ask about unsaved modules, then release designer resources.

        Modules are NOT taken offline here — closing the editor must not shed
        the plant's control while the engine keeps running. Saving is the
        user's choice: silently rewriting every open module on close used to
        overwrite untouched source files.
        """
        unsaved = []
        try:
            unsaved = self._designer.has_unsaved_changes()
        except Exception:
            pass

        save = False
        # Never block a headless run on a modal dialog: under the offscreen
        # platform (smoke tests, screenshot capture) there is nobody to answer,
        # so close without saving and say so in the log.
        if unsaved and _is_headless():
            log.warning("Closing with %d unsaved module(s) — headless, not "
                        "prompting and not saving: %s", len(unsaved), unsaved)
            unsaved = []

        if unsaved:
            from PySide6.QtWidgets import QMessageBox
            names = "\n".join(f"  • {n}" for n in unsaved[:12])
            more = f"\n  … and {len(unsaved) - 12} more" if len(unsaved) > 12 else ""
            choice = QMessageBox.question(
                self, "Unsaved changes",
                f"{len(unsaved)} module(s) have unsaved changes:\n\n{names}{more}\n\n"
                "Save them before closing?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save,
            )
            if choice == QMessageBox.Cancel:
                event.ignore()
                return
            save = choice == QMessageBox.Save

        # Explorer and the independent products still use this controller
        # host. Keep the usual unsaved decision above, but do not close their
        # windows or stop shared timers just because this editor is closing.
        peers = (getattr(self, "explorer", None), self._pvm_studio_window,
                 self._live_station_window)
        from shiboken6 import isValid
        if any(peer is not None and isValid(peer) and peer.isVisible() for peer in peers):
            if save:
                self._designer.save_dirty_modules()
            log.info("Control Designer editor hidden; another product retains the session")
            event.accept()
            return

        self._tick_timer.stop()
        if self._live_station_window is not None:
            self._live_station_window.close()
            self._live_station_window = None
        if self._pvm_studio_window is not None:
            self._pvm_studio_window.close()
            self._pvm_studio_window = None
        self._designer.cleanup(save_dirty=save)
        log.info("Control Designer window closed (saved=%s, unsaved=%d)",
                 save, len(unsaved))
        event.accept()
