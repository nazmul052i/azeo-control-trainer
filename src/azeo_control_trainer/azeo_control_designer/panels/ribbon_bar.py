"""Commercial-grade Ribbon Bar — Process Simulation Strategy Designer.

A tabbed ribbon toolbar inspired by Honeywell Experion PKS and Microsoft Office,
with:
  - Quick Access Toolbar (QAT) above the ribbon
  - Tabbed pages: Home | Module | View | Controller | Tools
  - Large + small button layout with icon support
  - Labeled ribbon groups with separators
  - Collapsible ribbon (double-click tab header)
  - Status badge and scan time indicators
  - Silver industrial theme styling
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.menu_style import StudioMenu

from azeo_control_trainer.core.presentation.brand import UI

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QWidget,
    QFileDialog, QScrollArea, QSizePolicy, QVBoxLayout, QTabBar, QStackedWidget,
    QToolButton,
)

# ══════════════════════════════════════════════════════════════════════
# Theme Constants — derived from ISA-101 Silver Theme
# ══════════════════════════════════════════════════════════════════════

from azeo_control_trainer.core.presentation.ribbon import (  # noqa: F401
    _C, _icon, _QSS_QAT, _QSS_TAB_BAR, _QSS_RIBBON_PAGE, _QSS_LARGE_BTN,
    _QSS_SMALL_BTN, _QSS_ACCENT_BTN, _QSS_DEACTIVATE_BTN, _QSS_MENU, _NO_CONTROLLER,
    RibbonButton, SmallRibbonButton, RibbonSeparator, RibbonGroup, RibbonPage,
)


class RibbonBar(QWidget):
    """Commercial-grade tabbed ribbon bar for the strategy designer.

    Drop-in replacement for StrategyToolbar — emits the same signals.
    """

    # ── Signals (matching StrategyToolbar API) ──
    compileRequested = Signal()
    goOnlineRequested = Signal()
    goOfflineRequested = Signal()
    saveRequested = Signal()
    loadRequested = Signal(str)
    newRequested = Signal()
    zoomFitRequested = Signal()
    zoomInRequested = Signal()
    zoomOutRequested = Signal()
    presetRequested = Signal(str)
    addCommentRequested = Signal()
    showValuesToggled = Signal(bool)
    showExecOrderToggled = Signal(bool)
    searchRequested = Signal(str)
    compareRequested = Signal()
    compareParametersRequested = Signal()
    versionHistoryRequested = Signal()
    placeTemplateRequested = Signal(str)
    manageTemplatesRequested = Signal()
    controllerStatusRequested = Signal()
    alignRequested = Signal(str)
    distributeRequested = Signal(str)
    autoArrangeRequested = Signal()

    # ── Additional signals for new ribbon features ──
    undoRequested = Signal()
    redoRequested = Signal()
    downloadRequested = Signal()
    hideUnusedPinsRequested = Signal()
    showAllPinsRequested = Signal()
    cutRequested = Signal()
    copyRequested = Signal()
    pasteRequested = Signal()
    connectRequested = Signal()
    disconnectRequested = Signal()
    ioConfigRequested = Signal()
    controllerPropertiesRequested = Signal()
    diagnosticsRequested = Signal()
    uploadRequested = Signal()
    checkpointSaveRequested = Signal()
    checkpointRestoreRequested = Signal()
    simulatorRequested = Signal()
    datalogConfigRequested = Signal()
    monitoringRequested = Signal()
    faceplateRequested = Signal()
    debuggerRequested = Signal()
    debugPauseResumeRequested = Signal()
    debugStepRequested = Signal()
    debugRunScanRequested = Signal()
    deploymentImpactRequested = Signal()
    bulkEngineeringRequested = Signal()
    moduleClassesRequested = Signal()
    redundancySimulatorRequested = Signal()

    # ── Edit-menu parity (Azeo Edit menu carries the full standard set) ──
    # Cut/Copy/Paste already exist above; these complete the set so the
    # designer window's Edit menu and the ribbon share one code path.
    saveAsRequested = Signal()
    selectAllRequested = Signal()
    deleteRequested = Signal()

    # ── Module-tab parity (Azeo Module menu) ──
    modulePropertiesRequested = Signal()
    moduleParametersRequested = Signal()
    namedSetsRequested = Signal()
    crossReferenceRequested = Signal()
    assignIORequested = Signal()
    execOrderEditRequested = Signal()
    printRequested = Signal()
    printPreviewRequested = Signal()
    moduleReportRequested = Signal()

    # ── View-tab parity ──
    watchWindowRequested = Signal()
    tagDatabaseRequested = Signal()

    def __init__(self, plugin=None, parent=None):
        super().__init__(parent)
        self._plugin = plugin
        self._collapsed = False
        self._ribbon_height = 88
        self._build_ui()

    # ──────────────────────────────────────────────────────────────
    # Build
    # ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Quick Access Toolbar ──
        self._qat = self._build_qat()
        root.addWidget(self._qat)

        # ── Tab bar (ribbon tabs) ──
        self._tab_bar = QTabBar()
        self._tab_bar.setObjectName("RibbonTabBar")
        self._tab_bar.setStyleSheet(_QSS_TAB_BAR)
        self._tab_bar.setExpanding(False)
        self._tab_bar.setDrawBase(False)
        # Give the tab bar the row's remaining width.  At its size hint Qt's
        # built-in scroll buttons consume enough space to manufacture their
        # own overflow, which left two orphaned chevrons beside Tools even on
        # a maximized window.
        self._tab_bar.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._tab_bar.tabBarDoubleClicked.connect(self._toggle_collapse)
        # Clicking a tab while minimized brings the ribbon back — without
        # this the only way out was the same double-click that got you in,
        # which nobody discovers twice.
        self._tab_bar.tabBarClicked.connect(self._restore_if_collapsed)
        tab_row = QHBoxLayout()
        tab_row.setContentsMargins(0, 0, 6, 0)
        tab_row.setSpacing(0)
        tab_row.addWidget(self._tab_bar, 1)
        self._btn_collapse = QPushButton("⌃")
        self._btn_collapse.setFixedSize(22, 20)
        self._btn_collapse.setToolTip(
            "Minimize the ribbon (or double-click a tab)")
        self._btn_collapse.setStyleSheet(
            "QPushButton { border: none; background: transparent; "
            f"color: {UI.text_muted}; font-size: 10pt; }} "
            f"QPushButton:hover {{ background: {UI.hover}; }}")
        self._btn_collapse.clicked.connect(lambda: self._toggle_collapse(-1))
        tab_row.addWidget(self._btn_collapse)
        root.addLayout(tab_row)

        # ── Stacked pages ──
        self._pages = QStackedWidget()
        self._pages.setFixedHeight(self._ribbon_height)
        # Ribbon pages may be wider than a small desktop; the window may not.
        self._ribbon_scroll = QScrollArea()
        self._ribbon_scroll.setWidgetResizable(True)
        self._ribbon_scroll.setFrameShape(QFrame.NoFrame)
        self._ribbon_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._ribbon_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._ribbon_scroll.setWidget(self._pages)
        self._ribbon_scroll.setFixedHeight(self._ribbon_height + 16)
        root.addWidget(self._ribbon_scroll)

        # Bottom border line
        border = QFrame()
        border.setFixedHeight(1)
        border.setStyleSheet(f"background: {_C['ribbon_border']};")
        root.addWidget(border)

        self._tab_bar.currentChanged.connect(self._pages.setCurrentIndex)

        # ── Build all ribbon pages ──
        self._build_home_page()
        self._build_diagram_page()
        self._build_module_page()
        self._build_view_page()
        self._build_controller_page()
        self._build_tools_page()

        # Start on Home tab
        self._tab_bar.setCurrentIndex(0)

    # ── Quick Access Toolbar ──
    def _build_qat(self) -> QWidget:
        qat = QWidget()
        qat.setObjectName("QATBar")
        qat.setStyleSheet(_QSS_QAT)
        qat.setFixedHeight(28)

        lay = QHBoxLayout(qat)
        lay.setContentsMargins(8, 1, 8, 1)
        lay.setSpacing(3)

        # App icon / title
        from azeo_control_trainer.core.presentation.app_icon import get_app_icon

        app_mark = QLabel()
        app_mark.setObjectName("ControlDesignerMark")
        app_mark.setPixmap(get_app_icon(
            application_id="control_designer").pixmap(16, 16))
        app_mark.setFixedSize(18, 20)
        app_mark.setToolTip("Azeo Control Designer")
        lay.addWidget(app_mark)

        title_lbl = QLabel("AZEO CONTROL DESIGNER")
        title_lbl.setObjectName("ControlDesignerTitle")
        title_lbl.setStyleSheet(
            f"color: {UI.blue}; font-size: 9pt; font-weight: 700; "
            "letter-spacing: .7px; background: transparent; border: none; "
            "padding-right: 8px;")
        lay.addWidget(title_lbl)

        sep = QFrame()
        sep.setFixedWidth(1)
        sep.setFixedHeight(14)
        sep.setStyleSheet(f"background: {_C['group_sep']};")
        lay.addWidget(sep)

        # QAT buttons
        self._qat_save = self._make_qat_btn("save", "Save (Ctrl+S)")
        self._qat_save.clicked.connect(self.saveRequested.emit)
        lay.addWidget(self._qat_save)

        self._qat_undo = self._make_qat_btn("undo", "Undo (Ctrl+Z)")
        self._qat_undo.clicked.connect(self.undoRequested.emit)
        lay.addWidget(self._qat_undo)

        self._qat_redo = self._make_qat_btn("redo", "Redo (Ctrl+Y)")
        self._qat_redo.clicked.connect(self.redoRequested.emit)
        lay.addWidget(self._qat_redo)

        sep2 = QFrame()
        sep2.setFixedWidth(1)
        sep2.setFixedHeight(16)
        sep2.setStyleSheet(f"background: {_C['group_sep']};")
        lay.addWidget(sep2)

        self._qat_compile = self._make_qat_btn("compile", "Compile (F7)")
        self._qat_compile.clicked.connect(self.compileRequested.emit)
        lay.addWidget(self._qat_compile)

        self._qat_download = self._make_qat_btn("download", "Download (F5)")
        self._qat_download.clicked.connect(self.downloadRequested.emit)
        lay.addWidget(self._qat_download)

        lay.addStretch(1)

        # ── Status badge (right side of QAT) ──
        self._status = QLabel("  INACTIVE  ")
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setFixedHeight(18)
        self._set_status_style(False)
        lay.addWidget(self._status)

        self._scan_time = QLabel("")
        self._scan_time.setStyleSheet(
            f"color: {UI.text_muted}; font-size: 9pt; border: none; "
            "background: transparent; padding: 0 6px;")
        lay.addWidget(self._scan_time)

        return qat

    def _make_qat_btn(self, icon_name: str, tip: str) -> QToolButton:
        btn = QToolButton()
        btn.setToolTip(tip)
        btn.setIcon(_icon(icon_name, 16))
        btn.setIconSize(QSize(16, 16))
        btn.setFixedSize(26, 24)
        btn.setAutoRaise(True)
        return btn

    # ──────────────────────────────────────────────────────────────
    # Page: HOME
    # ──────────────────────────────────────────────────────────────

    def _build_home_page(self):
        page = RibbonPage()

        # ── File group ──
        g_file = RibbonGroup("File")
        self._btn_new = RibbonButton("New", "new", "New strategy (Ctrl+N)")
        self._btn_new.clicked.connect(self.newRequested.emit)
        g_file.addWidget(self._btn_new)

        self._btn_open = RibbonButton("Open", "open", "Open strategy (Ctrl+O)")
        self._btn_open.clicked.connect(self._on_load)
        g_file.addWidget(self._btn_open)

        self._btn_save = RibbonButton("Save", "save", "Save strategy (Ctrl+S)")
        self._btn_save.clicked.connect(self.saveRequested.emit)
        g_file.addWidget(self._btn_save)

        btn_save_as = SmallRibbonButton(
            "Save As", "save_as", "Save the module to a new file (Ctrl+Shift+S)")
        btn_save_as.clicked.connect(self.saveAsRequested.emit)
        btn_presets = SmallRibbonButton("Presets", "presets", "Load preset strategy")
        btn_presets.clicked.connect(self._on_presets)
        g_file.addSmallButtonColumn(btn_save_as, btn_presets)

        page.addGroup(g_file)

        # ── Edit group ──
        # Previously labelled "Clipboard" while holding only Undo/Redo, with
        # no cut/copy/paste anywhere in the ribbon.
        g_edit = RibbonGroup("Edit")
        btn_undo = RibbonButton("Undo", "undo", "Undo (Ctrl+Z)")
        btn_undo.clicked.connect(self.undoRequested.emit)
        g_edit.addWidget(btn_undo)
        btn_redo = RibbonButton("Redo", "redo", "Redo (Ctrl+Y)")
        btn_redo.clicked.connect(self.redoRequested.emit)
        g_edit.addWidget(btn_redo)
        btn_sel_all = SmallRibbonButton(
            "All", "select_all", "Select every block (Ctrl+A)")
        btn_sel_all.clicked.connect(self.selectAllRequested.emit)
        btn_del = SmallRibbonButton(
            "Del", "delete", "Delete the selection (Del)")
        btn_del.clicked.connect(self.deleteRequested.emit)
        g_edit.addSmallButtonColumn(btn_sel_all, btn_del)
        page.addGroup(g_edit)

        # ── Clipboard group ──
        g_clip = RibbonGroup("Clipboard")
        btn_cut = RibbonButton("Cut", "cut", "Cut selection (Ctrl+X)")
        btn_cut.clicked.connect(self.cutRequested.emit)
        g_clip.addWidget(btn_cut)
        btn_copy = RibbonButton("Copy", "copy", "Copy selection (Ctrl+C)")
        btn_copy.clicked.connect(self.copyRequested.emit)
        g_clip.addWidget(btn_copy)
        btn_paste = RibbonButton("Paste", "paste", "Paste (Ctrl+V)")
        btn_paste.clicked.connect(self.pasteRequested.emit)
        g_clip.addWidget(btn_paste)
        page.addGroup(g_clip)

        # ── Build group ──
        g_build = RibbonGroup("Build")
        self._btn_compile = RibbonButton("Compile", "compile", "Compile strategy (F7)")
        self._btn_compile.clicked.connect(self.compileRequested.emit)
        g_build.addWidget(self._btn_compile)

        self._btn_download = RibbonButton("Download", "download", "Download to controller (F5)")
        self._btn_download.setStyleSheet(_QSS_ACCENT_BTN)
        self._btn_download.setFixedSize(70, 54)
        self._btn_download.clicked.connect(self.downloadRequested.emit)
        g_build.addWidget(self._btn_download)

        self._btn_deactivate = RibbonButton("Stop", "deactivate", "Deactivate all modules (Shift+F5)")
        self._btn_deactivate.setStyleSheet(_QSS_DEACTIVATE_BTN)
        self._btn_deactivate.setFixedSize(70, 54)
        self._btn_deactivate.setEnabled(False)
        self._btn_deactivate.clicked.connect(self.goOfflineRequested.emit)
        g_build.addWidget(self._btn_deactivate)

        btn_ctrlstatus = SmallRibbonButton("Status", "status", "Controller status")
        btn_ctrlstatus.clicked.connect(self.controllerStatusRequested.emit)
        g_build.addSmallButtonColumn(btn_ctrlstatus)

        page.addGroup(g_build)

        # ── Annotations group ──
        # Holds annotations only. "Show Values" and "Exec Order" are display
        # toggles, not annotations; they used to sit here as well while also
        # (correctly) living on View ▸ Display, so the group title described
        # only a third of its contents. They now exist once, on View ▸ Display,
        # and ``_btn_show_values`` / ``_btn_exec_order`` alias those buttons.
        g_annot = RibbonGroup("Annotations")
        self._btn_comment = RibbonButton("Comment", "comment", "Add comment annotation")
        self._btn_comment.clicked.connect(self.addCommentRequested.emit)
        g_annot.addWidget(self._btn_comment)
        page.addGroup(g_annot)

        # ── Layout group ──
        g_layout = RibbonGroup("Layout")
        btn_align = RibbonButton("Align", "align", "Align selected blocks")
        btn_align.clicked.connect(self._on_align_menu)
        g_layout.addWidget(btn_align)

        btn_auto = RibbonButton("Arrange", "auto", "Auto-arrange by execution order")
        btn_auto.clicked.connect(self.autoArrangeRequested.emit)
        g_layout.addWidget(btn_auto)

        btn_fit = SmallRibbonButton("Fit All", "zoom_fit", "Zoom to fit all blocks (Ctrl+0)")
        btn_fit.clicked.connect(self.zoomFitRequested.emit)
        g_layout.addSmallButtonColumn(btn_fit)

        page.addGroup(g_layout)

        page.addStretch()
        self._add_page("Home", page)

    # ──────────────────────────────────────────────────────────────
    # Page: MODULE
    # ──────────────────────────────────────────────────────────────

    def _build_diagram_page(self):
        """Azeo Control Designer's Diagram tab, with only the groups this
        format honestly supports — a button promising a shape the file
        cannot hold is worse than no button (the Font-group lesson)."""
        page = RibbonPage()

        g_order = RibbonGroup("Execution Order")
        btn_calc = RibbonButton(
            "Auto\nCalculate", "compile",
            "Compile the diagram — execution order is recalculated")
        btn_calc.clicked.connect(self.compileRequested.emit)
        g_order.addWidget(btn_calc)
        btn_order = SmallRibbonButton(
            "Show Order", "exec_order",
            "Show each block's execution order number")
        btn_order.setCheckable(True)
        btn_order.toggled.connect(self.showExecOrderToggled.emit)
        g_order.addSmallButtonColumn(btn_order)
        page.addGroup(g_order)

        g_shapes = RibbonGroup("Shapes")
        btn_text = RibbonButton("Text\nBox", "comment",
                                "Add a text annotation to the diagram")
        btn_text.clicked.connect(self.addCommentRequested.emit)
        g_shapes.addWidget(btn_text)
        page.addGroup(g_shapes)

        g_layout = RibbonGroup("Layout")
        btn_align = RibbonButton("Align", "align", "Align selected blocks")
        btn_align.clicked.connect(self._on_align_menu)
        g_layout.addWidget(btn_align)
        btn_arrange = RibbonButton("Arrange", "auto",
                                   "Auto-arrange by execution order")
        btn_arrange.clicked.connect(self.autoArrangeRequested.emit)
        g_layout.addWidget(btn_arrange)
        btn_hide = SmallRibbonButton(
            "Hide Pins", "hide",
            "Hide unwired pins on the selected blocks")
        btn_hide.clicked.connect(self.hideUnusedPinsRequested.emit)
        btn_show = SmallRibbonButton(
            "Show Pins", "show",
            "Show every pin on all blocks in this module")
        btn_show.clicked.connect(self.showAllPinsRequested.emit)
        g_layout.addSmallButtonColumn(btn_hide, btn_show)
        page.addGroup(g_layout)

        # Sparse pages splay their groups across the full ribbon width
        # without this — the trailing stretch is what packs them left,
        # the way every other page reads.
        page.addStretch()
        self._add_page("Diagram", page)

    def _build_module_page(self):
        page = RibbonPage()

        # ── Deploy group ──
        g_deploy = RibbonGroup("Deploy")
        self._btn_module_compile = RibbonButton(
            "Compile", "compile", "Compile strategy (F7)")
        self._btn_module_compile.clicked.connect(self.compileRequested.emit)
        g_deploy.addWidget(self._btn_module_compile)

        self._btn_module_download = RibbonButton(
            "Download", "download", "Download to controller (F5)")
        self._btn_module_download.setStyleSheet(_QSS_ACCENT_BTN)
        self._btn_module_download.setFixedSize(70, 54)
        self._btn_module_download.clicked.connect(self.downloadRequested.emit)
        g_deploy.addWidget(self._btn_module_download)

        self._btn_module_offline = RibbonButton(
            "Go Offline", "deactivate", "Go offline (Shift+F5)")
        self._btn_module_offline.setStyleSheet(_QSS_DEACTIVATE_BTN)
        self._btn_module_offline.setFixedSize(70, 54)
        self._btn_module_offline.setEnabled(False)
        self._btn_module_offline.clicked.connect(self.goOfflineRequested.emit)
        g_deploy.addWidget(self._btn_module_offline)
        page.addGroup(g_deploy)

        # ── Transfer group ──
        g_transfer = RibbonGroup("Transfer")
        btn_upload = RibbonButton("Upload", "upload", "Upload from controller to project")
        btn_upload.clicked.connect(self.uploadRequested.emit)
        g_transfer.addWidget(btn_upload)

        btn_compare_p = SmallRibbonButton("Cmp Params", "compare", "Compare parameters (Ctrl+K)")
        btn_compare_p.clicked.connect(self.compareParametersRequested.emit)
        btn_impact = SmallRibbonButton(
            "Deploy Impact", "compare",
            "Compare current modules with the last successful download")
        btn_impact.clicked.connect(self.deploymentImpactRequested.emit)
        g_transfer.addSmallButtonColumn(btn_compare_p, btn_impact)
        page.addGroup(g_transfer)

        # ── Checkpoint group ──
        g_ckpt = RibbonGroup("Checkpoint")
        btn_save_ckpt = RibbonButton("Save", "checkpoint", "Save checkpoint (Ctrl+Shift+C)")
        btn_save_ckpt.clicked.connect(self.checkpointSaveRequested.emit)
        g_ckpt.addWidget(btn_save_ckpt)

        btn_restore_ckpt = RibbonButton("Restore", "restore", "Restore checkpoint")
        btn_restore_ckpt.clicked.connect(self.checkpointRestoreRequested.emit)
        g_ckpt.addWidget(btn_restore_ckpt)
        page.addGroup(g_ckpt)

        # ── Simulator group ──
        g_sim = RibbonGroup("Simulation")
        btn_sim = RibbonButton("Sim", "simulator", "Controller simulator (Ctrl+Shift+M)")
        btn_sim.clicked.connect(self.simulatorRequested.emit)
        g_sim.addWidget(btn_sim)

        btn_faceplate = SmallRibbonButton("Faceplate", "faceplate", "Open faceplate for selected block")
        btn_faceplate.clicked.connect(self.faceplateRequested.emit)
        g_sim.addSmallButtonColumn(btn_faceplate)
        page.addGroup(g_sim)

        # ── Online FBD debugger ──
        # These controls stop at compiled block boundaries while the module
        # remains online.  Step/Scan are deliberately unavailable until the
        # module is paused, so the ribbon never offers an ambiguous command.
        g_debug = RibbonGroup("Debugger")
        self._btn_debugger = RibbonButton(
            "Debugger", "debugger",
            "Open the online FBD block-boundary debugger")
        self._btn_debugger.setEnabled(False)
        self._btn_debugger.clicked.connect(self.debuggerRequested.emit)
        g_debug.addWidget(self._btn_debugger)

        self._btn_debug_pause = SmallRibbonButton(
            "Pause", "pause", "Pause the active module at a safe boundary")
        self._btn_debug_pause.setEnabled(False)
        self._btn_debug_pause.clicked.connect(
            self.debugPauseResumeRequested.emit)
        self._btn_debug_step = SmallRibbonButton(
            "Step Block", "step_block",
            "Execute exactly one compiled block while paused")
        self._btn_debug_step.setEnabled(False)
        self._btn_debug_step.clicked.connect(self.debugStepRequested.emit)
        self._btn_debug_scan = SmallRibbonButton(
            "Run Scan", "run_scan",
            "Complete the remaining scan, or one new full scan")
        self._btn_debug_scan.setEnabled(False)
        self._btn_debug_scan.clicked.connect(self.debugRunScanRequested.emit)
        g_debug.addSmallButtonColumn(
            self._btn_debug_pause,
            self._btn_debug_step,
            self._btn_debug_scan,
        )
        page.addGroup(g_debug)

        # ── Properties group ──
        # Only commands backed by an authoring model belong in this menu.
        # Execution order is compiler-derived and has no persisted manual-order
        # model yet; I/O assignment belongs to Explorer's channel configuration.
        g_props = RibbonGroup("Properties")
        self._module_menu = QMenu(self)
        self._module_menu.setStyleSheet(_QSS_MENU)
        for label, icon, signal, tip in [
            ("Module Properties...", "module_props",
             self.modulePropertiesRequested,
             "Scan rate, assigned node, description"),
            ("Module Parameters...", "params", self.moduleParametersRequested,
             "Module-level parameter list"),
            ("Named Sets...", "named_sets", self.namedSetsRequested,
             "Named set (enumeration) definitions"),
            ("Cross Reference...", "xref", self.crossReferenceRequested,
             "Where a tag or block is used"),
        ]:
            act = self._module_menu.addAction(_icon(icon, 16), label)
            act.setToolTip(tip)
            act.triggered.connect(signal.emit)
        btn_mprops = RibbonButton(
            "Module\nProps", "module_props",
            "Module properties, parameters, named sets and cross reference")
        btn_mprops.setFixedSize(70, 54)
        btn_mprops.setMenu(self._module_menu)
        btn_mprops.setPopupMode(QToolButton.InstantPopup)
        g_props.addWidget(btn_mprops)
        page.addGroup(g_props)

        # ── Print group ──
        g_print = RibbonGroup("Print")
        btn_print = RibbonButton("Print", "print", "Print the diagram (Ctrl+P)")
        btn_print.clicked.connect(self.printRequested.emit)
        g_print.addWidget(btn_print)
        btn_preview = SmallRibbonButton(
            "Preview", "print", "Print preview of the diagram")
        btn_preview.clicked.connect(self.printPreviewRequested.emit)
        btn_report = SmallRibbonButton(
            "Module Report", "print",
            "Configuration, live state, alarms, execution and validation report")
        btn_report.clicked.connect(self.moduleReportRequested.emit)
        g_print.addSmallButtonColumn(btn_preview, btn_report)
        page.addGroup(g_print)

        page.addStretch()
        self._add_page("Module", page)

    # ──────────────────────────────────────────────────────────────
    # Page: VIEW
    # ──────────────────────────────────────────────────────────────

    def _build_view_page(self):
        page = RibbonPage()

        # ── Zoom group ──
        g_zoom = RibbonGroup("Zoom")
        btn_zin = RibbonButton("Zoom In", "zoom_in", "Zoom in (Ctrl+=)")
        btn_zin.clicked.connect(self.zoomInRequested.emit)
        g_zoom.addWidget(btn_zin)

        btn_zout = RibbonButton("Zoom Out", "zoom_out", "Zoom out (Ctrl+-)")
        btn_zout.clicked.connect(self.zoomOutRequested.emit)
        g_zoom.addWidget(btn_zout)

        btn_zfit = RibbonButton("Fit All", "zoom_fit", "Zoom to fit all blocks (Ctrl+0)")
        btn_zfit.clicked.connect(self.zoomFitRequested.emit)
        g_zoom.addWidget(btn_zfit)

        self._zoom_label = QLabel("100%")
        self._zoom_label.setFixedWidth(42)
        self._zoom_label.setAlignment(Qt.AlignCenter)
        self._zoom_label.setStyleSheet(
            f"color: {_C['btn_text']}; font-size: 9pt; font-weight: bold; "
            "border: none; background: transparent;")
        g_zoom.addWidget(self._zoom_label)
        page.addGroup(g_zoom)

        # ── Display group ──
        g_display = RibbonGroup("Display")
        btn_vals = RibbonButton("Values", "values", "Show live values on wires")
        btn_vals.setCheckable(True)
        btn_vals.toggled.connect(self.showValuesToggled.emit)
        self._view_btn_values = btn_vals
        # Single home for the display toggles (see _build_home_page). The
        # ``_btn_*`` names are the public handles used to reflect runtime state.
        self._btn_show_values = btn_vals
        g_display.addWidget(btn_vals)

        btn_exec = RibbonButton("Exec Ord", "exec_order", "Show execution order on blocks")
        btn_exec.setCheckable(True)
        btn_exec.setChecked(True)
        btn_exec.toggled.connect(self.showExecOrderToggled.emit)
        self._view_btn_exec = btn_exec
        self._btn_exec_order = btn_exec
        g_display.addWidget(btn_exec)

        # Pin visibility for the whole diagram. A block shows every terminal by
        # default, which makes the large Azeo blocks very tall (AVTR is 30+
        # rows); Azeo instead shows only the pins the engineer exposes.
        btn_hide_pins = SmallRibbonButton(
            "Hide Unused Pins", "hide",
            "Hide every unconnected pin on all blocks in this module")
        btn_hide_pins.clicked.connect(self.hideUnusedPinsRequested.emit)
        btn_show_pins = SmallRibbonButton(
            "Show All Pins", "show",
            "Show every pin on all blocks in this module")
        btn_show_pins.clicked.connect(self.showAllPinsRequested.emit)
        g_display.addSmallButtonColumn(btn_hide_pins, btn_show_pins)

        btn_mon = SmallRibbonButton("Monitor", "datalog", "Monitoring tab (Ctrl+M)")
        btn_mon.clicked.connect(self.monitoringRequested.emit)
        # The watch window was only reachable from a block's right-click ▸ Add
        # to Watch, so closing it stranded the panel. It is a first-class
        # debug window in Azeo — give it a View entry point.
        btn_watch = SmallRibbonButton(
            "Watch", "watch",
            "Open the Watch Window (Ctrl+Shift+W)")
        btn_watch.clicked.connect(self.watchWindowRequested.emit)
        # The tag database is derived from the open modules and is the
        # namespace a watch window, a cross-reference and an HMI binding all
        # pick from — it needs its own way in.
        btn_tagdb = SmallRibbonButton(
            "Tag DB", "xref",
            "Browse the tag database (Ctrl+T)")
        btn_tagdb.clicked.connect(self.tagDatabaseRequested.emit)
        g_display.addSmallButtonColumn(btn_mon, btn_watch)
        g_display.addSmallButtonColumn(btn_tagdb)
        page.addGroup(g_display)

        # ── Layout group ──
        g_layout = RibbonGroup("Layout")
        btn_align = RibbonButton("Align", "align", "Align selected blocks")
        btn_align.clicked.connect(self._on_align_menu)
        g_layout.addWidget(btn_align)

        btn_auto = RibbonButton("Arrange", "auto", "Auto-arrange by execution order")
        btn_auto.clicked.connect(self.autoArrangeRequested.emit)
        g_layout.addWidget(btn_auto)
        page.addGroup(g_layout)

        # ── Comment group ──
        g_annot = RibbonGroup("Annotate")
        btn_cmt = RibbonButton("Comment", "comment", "Add comment annotation")
        btn_cmt.clicked.connect(self.addCommentRequested.emit)
        g_annot.addWidget(btn_cmt)
        page.addGroup(g_annot)

        page.addStretch()
        self._add_page("View", page)

    # ──────────────────────────────────────────────────────────────
    # Page: CONTROLLER
    # ──────────────────────────────────────────────────────────────

    def _build_controller_page(self):
        page = RibbonPage()

        # ── Configuration group ──
        # Connection lifecycle and physical I/O assignment belong to Explorer;
        # disabled surrogates do not belong on the Control Designer ribbon.

        g_cfg = RibbonGroup("Configuration")
        btn_props = RibbonButton(
            "Props", "properties",
            "Configure the controller node name, PK model and capacity")
        btn_props.clicked.connect(self.controllerPropertiesRequested.emit)
        g_cfg.addWidget(btn_props)
        page.addGroup(g_cfg)

        # ── Monitoring group ──
        g_mon = RibbonGroup("Monitoring")
        btn_status = RibbonButton("Status", "status", "Controller status")
        btn_status.clicked.connect(self.controllerStatusRequested.emit)
        g_mon.addWidget(btn_status)

        btn_diag = RibbonButton("Diag", "diagnostics", "Controller diagnostics")
        btn_diag.clicked.connect(self.diagnosticsRequested.emit)
        g_mon.addWidget(btn_diag)
        page.addGroup(g_mon)

        g_resilience = RibbonGroup("Resilience")
        btn_redundancy = RibbonButton(
            "Redundancy", "simulator",
            "Simulate controller failover and field communication failures")
        btn_redundancy.clicked.connect(self.redundancySimulatorRequested.emit)
        g_resilience.addWidget(btn_redundancy)
        page.addGroup(g_resilience)

        page.addStretch()
        self._add_page("Controller", page)

    # ──────────────────────────────────────────────────────────────
    # Page: TOOLS
    # ──────────────────────────────────────────────────────────────

    def _build_tools_page(self):
        page = RibbonPage()

        # ── Compare group ──
        g_cmp = RibbonGroup("Compare")
        btn_cmp = RibbonButton("Compare", "compare", "Compare two strategy files")
        btn_cmp.clicked.connect(self.compareRequested.emit)
        g_cmp.addWidget(btn_cmp)

        btn_hist = RibbonButton("History", "history", "View version history")
        btn_hist.clicked.connect(self.versionHistoryRequested.emit)
        g_cmp.addWidget(btn_hist)
        page.addGroup(g_cmp)

        # ── Templates group ──
        g_tmpl = RibbonGroup("Templates")
        btn_tmpl = RibbonButton("Tmpl", "templates", "Place or manage templates")
        btn_tmpl.clicked.connect(self._on_templates)
        g_tmpl.addWidget(btn_tmpl)
        btn_bulk = RibbonButton(
            "Bulk", "templates",
            "Generate or edit modules through a validated preview")
        btn_bulk.clicked.connect(self.bulkEngineeringRequested.emit)
        g_tmpl.addWidget(btn_bulk)
        btn_classes = RibbonButton(
            "Classes", "templates",
            "Manage revisioned Control Module Classes and linked instances")
        btn_classes.clicked.connect(self.moduleClassesRequested.emit)
        g_tmpl.addWidget(btn_classes)
        page.addGroup(g_tmpl)

        # ── Data group ──
        g_data = RibbonGroup("Data")
        btn_dl = RibbonButton("Data Log", "datalog", "Data log configuration")
        btn_dl.clicked.connect(self.datalogConfigRequested.emit)
        g_data.addWidget(btn_dl)
        page.addGroup(g_data)

        # ── Search group ──
        g_search = RibbonGroup("Search")
        search_container = QWidget()
        search_lay = QVBoxLayout(search_container)
        search_lay.setContentsMargins(0, 2, 0, 0)
        search_lay.setSpacing(1)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Find block... (Ctrl+F)")
        self._search_edit.setFixedHeight(24)
        self._search_edit.setFixedWidth(220)
        self._search_edit.setStyleSheet(
            f"QLineEdit {{ background: #FFFFFF; color: {_C['btn_text']}; "
            f"border: 1px solid {_C['group_sep']}; border-radius: 3px; "
            "padding: 2px 8px; font-size: 9pt; }"
            f"QLineEdit:focus {{ border-color: {_C['accent']}; "
            f"border-width: 1px; }}")
        self._search_edit.textChanged.connect(self.searchRequested.emit)
        self._search_edit.returnPressed.connect(
            lambda: self.searchRequested.emit(self._search_edit.text()))
        search_lay.addWidget(self._search_edit)

        self._search_count = QLabel("")
        self._search_count.setFixedHeight(14)
        self._search_count.setStyleSheet(
            f"color: {UI.text_muted}; font-size: 9pt; border: none; "
            "background: transparent; padding: 0 2px;")
        search_lay.addWidget(self._search_count)

        g_search.addWidget(search_container)
        page.addGroup(g_search)

        page.addStretch()
        self._add_page("Tools", page)

    # ──────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────

    def _add_page(self, title: str, page: RibbonPage):
        self._tab_bar.addTab(title)
        self._pages.addWidget(page)

    def _toggle_collapse(self, index: int):
        """Minimize/restore the ribbon — chevron, or double-click a tab."""
        self._collapsed = not self._collapsed
        if self._collapsed:
            self._pages.setFixedHeight(0)
            self._pages.hide()
            self._ribbon_scroll.hide()
        else:
            self._pages.setFixedHeight(self._ribbon_height)
            self._pages.show()
            self._ribbon_scroll.show()
        if hasattr(self, "_btn_collapse"):
            self._btn_collapse.setText(
                "⌄" if self._collapsed else "⌃")
            self._btn_collapse.setToolTip(
                "Restore the ribbon" if self._collapsed
                else "Minimize the ribbon (or double-click a tab)")

    def _restore_if_collapsed(self, index: int):
        if self._collapsed:
            self._toggle_collapse(index)

    def _set_status_style(self, online: bool):
        if online:
            self._status.setText("  ACTIVE  ")
            self._status.setStyleSheet(
                "QLabel { color: #fff; font-weight: bold; font-size: 9pt; "
                f"background: qlineargradient(y1:0, y2:1, "
                f"stop:0 #4CAF50, stop:1 {_C['online_bg']}); "
                f"border: 1px solid {_C['online_border']}; border-radius: 3px; "
                "padding: 2px 10px; }")
        else:
            self._status.setText("  INACTIVE  ")
            self._status.setStyleSheet(
                f"QLabel {{ color: {_C['offline_text']}; font-weight: bold; font-size: 9pt; "
                f"background: qlineargradient(y1:0, y2:1, "
                f"stop:0 #E0E2EB, stop:1 {_C['offline_bg']}); "
                f"border: 1px solid {_C['offline_border']}; border-radius: 3px; "
                "padding: 2px 10px; }")

    # ──────────────────────────────────────────────────────────────
    # Public API (StrategyToolbar compatibility)
    # ──────────────────────────────────────────────────────────────

    def set_online_state(self, online: bool):
        self._btn_download.setEnabled(not online)
        self._btn_deactivate.setEnabled(online)
        self._btn_compile.setEnabled(not online)
        self._btn_new.setEnabled(not online)
        self._btn_open.setEnabled(not online)
        # Home and Module are two presentations of the same commands. Keeping
        # only the Home copies in sync left Download enabled on one ribbon tab
        # and disabled on the other after going online.
        self._btn_module_compile.setEnabled(not online)
        self._btn_module_download.setEnabled(not online)
        self._btn_module_offline.setEnabled(online)
        self._set_status_style(online)
        if not online:
            self._scan_time.clear()
        self.set_debug_state(online=online, paused=False)

    def set_debug_state(self, *, online: bool, paused: bool,
                        scan_active: bool = False):
        """Reflect the active module's debugger state in ribbon controls."""
        self._btn_debugger.setEnabled(online)
        self._btn_debug_pause.setEnabled(online)
        self._btn_debug_pause.setText("Resume" if paused else "Pause")
        self._btn_debug_pause.setIcon(
            _icon("run_scan" if paused else "pause", 16))
        self._btn_debug_pause.setToolTip(
            "Resume the active module"
            if paused else "Pause the active module at a safe boundary")
        self._btn_debug_step.setEnabled(online and paused)
        self._btn_debug_scan.setEnabled(online and paused)
        if online and paused:
            suffix = " · PARTIAL" if scan_active else ""
            self._status.setText(f"  PAUSED{suffix}  ")
            self._status.setStyleSheet(
                "QLabel { color: #fff; font-weight: bold; font-size: 9pt; "
                "background: #D17A00; border: 1px solid #945500; "
                "border-radius: 3px; padding: 2px 10px; }")
        else:
            self._set_status_style(online)

    def update_scan_time(self, ms: float):
        self._scan_time.setText(f"Scan: {ms:.1f} ms")

    def update_zoom(self, factor: float):
        self._zoom_label.setText(f"{factor * 100:.0f}%")

    def focus_search(self):
        """Focus the search box (Ctrl+F)."""
        for i in range(self._tab_bar.count()):
            if self._tab_bar.tabText(i) == "Tools":
                self._tab_bar.setCurrentIndex(i)
                break
        self._search_edit.setFocus()
        self._search_edit.selectAll()

    def update_search_count(self, text: str):
        self._search_count.setText(text)

    # ──────────────────────────────────────────────────────────────
    # Dropdown menus (same logic as StrategyToolbar)
    # ──────────────────────────────────────────────────────────────

    def _on_load(self):
        from azeo_control_trainer.core.strategy.serialization import strategy_io
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Strategy", str(strategy_io.STRATEGY_DIR),
            "Strategy Files (*.json);;All Files (*)")
        if path:
            self.loadRequested.emit(path)

    def _on_presets(self):
        if self._plugin and self._plugin.preset_builders:
            builders = self._plugin.preset_builders
        else:
            from azeo_control_trainer.core.strategy.presets.king_strategies import SCHEME_BUILDERS
            builders = SCHEME_BUILDERS

        menu = StudioMenu(self)
        menu.setStyleSheet(_QSS_MENU)

        if not builders:
            act = menu.addAction("(no presets available)")
            act.setEnabled(False)
        else:
            template_keys = {"SINGLE", "CASCADE", "RATIO", "OVERRIDE", "SPLIT"}
            process_entries = []
            generic_entries = []
            for key, entry in builders.items():
                if key.upper() in template_keys:
                    generic_entries.append((key, entry))
                else:
                    process_entries.append((key, entry))

            for key, entry in process_entries:
                label, _ = entry
                act = menu.addAction(label)
                act.triggered.connect(
                    lambda checked, k=key: self.presetRequested.emit(k))

            if generic_entries:
                menu.addSeparator()
                header = menu.addAction("-- Control Templates --")
                header.setEnabled(False)
                for key, entry in generic_entries:
                    label, _ = entry
                    act = menu.addAction(label)
                    act.triggered.connect(
                        lambda checked, k=key: self.presetRequested.emit(k))

        btn = self.sender()
        if btn:
            menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _on_templates(self):
        from azeo_control_trainer.core.strategy.serialization.strategy_io import list_templates
        menu = StudioMenu(self)
        menu.setStyleSheet(_QSS_MENU)

        templates = list_templates()
        if templates:
            header = menu.addAction("-- Place Template --")
            header.setEnabled(False)
            for tp in templates:
                import json
                try:
                    with open(tp, "r", encoding="utf-8") as f:
                        tdata = json.load(f)
                    name = tdata.get("name", tp.stem)
                except Exception:
                    name = tp.stem
                act = menu.addAction(name)
                act.triggered.connect(
                    lambda checked, p=str(tp): self.placeTemplateRequested.emit(p))
        else:
            act = menu.addAction("(no templates saved)")
            act.setEnabled(False)

        menu.addSeparator()
        act_manage = menu.addAction("Manage Templates...")
        act_manage.triggered.connect(self.manageTemplatesRequested.emit)

        btn = self.sender()
        if btn:
            menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _on_align_menu(self):
        menu = StudioMenu(self)
        menu.setStyleSheet(_QSS_MENU)

        for label, direction in [
            ("Align Left", "left"),
            ("Align Right", "right"),
            ("Align Top", "top"),
            ("Align Bottom", "bottom"),
            ("Center Horizontally", "center_h"),
            ("Center Vertically", "center_v"),
        ]:
            act = menu.addAction(label)
            act.triggered.connect(
                lambda checked, d=direction: self.alignRequested.emit(d))

        menu.addSeparator()
        act_dh = menu.addAction("Distribute Horizontally")
        act_dh.triggered.connect(
            lambda: self.distributeRequested.emit("horizontal"))
        act_dv = menu.addAction("Distribute Vertically")
        act_dv.triggered.connect(
            lambda: self.distributeRequested.emit("vertical"))

        btn = self.sender()
        if btn:
            menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))
