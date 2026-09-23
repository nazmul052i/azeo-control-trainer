"""Main window.

Layout: a tab per P&ID in the centre, dockable panels around it, a menu bar and
toolbar for engine control, and a status bar that shows both the simulator's
health and the host machine's, because an overrunning engine and a saturated CPU
look identical from the process side.

Refresh policy: the UI polls a snapshot on a timer rather than receiving signals
from the engine thread. Polling at five hertz is cheaper than marshalling
thousands of cross-thread signals a second, and a display that is 200 ms stale is
indistinguishable from a real DCS console.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Dict, Optional

import psutil
from PySide6.QtCore import QEvent, QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QIcon, QKeySequence
from PySide6.QtWidgets import (QApplication, QDockWidget, QFileDialog,
                               QInputDialog, QLabel, QMainWindow,
                               QMessageBox, QTextBrowser,
                               QVBoxLayout, QWidget)

from ..core.engine import SimulationEngine
from ..core.tags import TagDatabase, TagKind
from ..product import PRODUCT_TITLE
from . import theme
from .faceplate import FaceplateDialog
from .live_dialog import retain_faceplate
from .panels import (CommsPanel, EnginePanel, FieldOpsPanel, LoopsPanel, LogPanel,
                     MalfunctionPanel)
from .help_browser import HelpBrowser
from .icon import APP_ICON
from .trend_studio import TrendStudio
from .variables import VariablesPanel

log = logging.getLogger(__name__)

HELP_DIR = Path(__file__).resolve().parent.parent / "help"


class MainWindow(QMainWindow):
    def __init__(self, db: TagDatabase, flowsheet, engine: SimulationEngine,
                 server, historian, snapshot_dir: Path) -> None:
        super().__init__()
        self.db = db
        self.historian = historian
        self.flowsheet = flowsheet
        self.engine = engine
        self.server = server
        self.snapshot_dir = Path(snapshot_dir)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.alarms = getattr(engine, "alarms", None)
        self.journal = getattr(self.alarms, "journal", None)
        self._process = psutil.Process()
        self._nav_history: list = []
        self._nav_pos = -1
        self._navigating = False
        self._float_windows: list = []
        from .settings_dialog import DEFAULTS
        self._prefs = dict(DEFAULTS)
        self._open_faceplates: Dict[str, FaceplateDialog] = {}

        self.setWindowTitle(PRODUCT_TITLE)
        self.setWindowIcon(QIcon(str(APP_ICON)))
        self.resize(1600, 980)
        self.setStyleSheet(theme.STYLESHEET)

        self._build_central()
        from .device_faceplate import build_device_registry
        self._device_registry = build_device_registry(flowsheet)
        self._build_docks()
        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_statusbar()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(200)
        psutil.cpu_percent(interval=None)      # prime the sampler
        self._restore_settings()
        log.info("Main window ready")

    # -------------------------------------------------------------- construction
    def _build_central(self) -> None:
        """The process displays are the centre; everything else is a window.

        No tabs: trending lives in non-modal trend windows (F4) and the
        variables table in its own non-modal window (F2), the way a real
        console keeps the graphics in front and the tools beside them.
        """
        self.variables_panel = VariablesPanel(self.db)
        self.variables_panel.tag_activated.connect(self.open_faceplate)
        self.variables_panel.context_provider = self._tag_context
        self._active_trend: TrendStudio | None = None
        self.variables_panel.pen_requested.connect(self.add_to_trend)

        controller = getattr(self.engine, "controller", None)
        if controller is not None:
            from .hmi import OperatorDisplay
            self.operator_display = OperatorDisplay(
                self.db, controller, self.open_loop_faceplate,
                self.open_faceplate,
                context_provider=self._tag_context, alarms=self.alarms)
            self.operator_display.on_scene_changed = self._on_scene_changed
            self._record_nav(("scene", "Overview"))
            central = self.operator_display
            # the variables table becomes its own non-modal window
            # distinctive part first, so the taskbar can tell windows apart
            self.variables_panel.setWindowTitle("Variables  ·  AzeoPlant")
            self.variables_panel.setWindowIcon(QIcon(str(APP_ICON)))
            self.variables_panel.resize(1000, 700)
        else:
            # without a DCS there are no graphics; the table is the centre
            self.operator_display = None
            central = self.variables_panel

        from PySide6.QtWidgets import QVBoxLayout
        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.nav_band = (self._build_nav_band()
                         if self.operator_display is not None else None)
        if self.nav_band is not None:
            lay.addWidget(self.nav_band)
        if self.alarms is not None:
            from .alarm_panel import AlarmBanner
            self.alarm_banner = AlarmBanner(self.alarms, self.open_faceplate,
                                            self._show_alarms)
            lay.addWidget(self.alarm_banner)
        else:
            self.alarm_banner = None
        lay.addWidget(central, 1)
        self.setCentralWidget(holder)

    def _build_nav_band(self) -> QWidget:
        """The commercial navigation band: back/forward/home, the plant
        breadcrumb, a display picker and the loop-mode chip."""
        from PySide6.QtGui import QActionGroup
        from PySide6.QtWidgets import QHBoxLayout, QMenu, QToolButton
        from .icons import make_icon

        band = QWidget()
        band.setObjectName("navBand")
        band.setAttribute(Qt.WA_StyledBackground, True)
        band.setFixedHeight(36)
        lay = QHBoxLayout(band)
        lay.setContentsMargins(6, 3, 10, 3)
        lay.setSpacing(2)

        def btn(icon, tip, cb, text=""):
            b = QToolButton()
            b.setIcon(make_icon(icon))
            b.setToolTip(tip)
            if text:
                b.setText(text)
                b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            b.clicked.connect(lambda _=False: cb())
            lay.addWidget(b)
            return b

        btn("back", "Display back (Alt+Left)", self._nav_back)
        btn("forward", "Display forward (Alt+Right)", self._nav_fwd)
        btn("home", "Plant overview",
            lambda: self.operator_display.show_scene("Overview"))
        self.crumb = QLabel("Plant  ▸  Overview")
        self.crumb.setObjectName("navCrumb")
        lay.addWidget(self.crumb)

        pick = QToolButton()
        pick.setIcon(make_icon("displays"))
        pick.setText("Displays")
        pick.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        pick.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(pick)
        group = QActionGroup(menu)
        group.setExclusive(True)
        self._scene_acts = {}
        for name in self.operator_display.scenes:
            a = menu.addAction(name)
            a.setCheckable(True)
            group.addAction(a)
            a.triggered.connect(
                lambda _c=False, n=name:
                self.operator_display.show_scene(n))
            self._scene_acts[name] = a
        if "Overview" in self._scene_acts:
            self._scene_acts["Overview"].setChecked(True)
        pick.setMenu(menu)
        lay.addWidget(pick)
        lay.addStretch(1)

        self.mode_chip = QLabel("")
        self.mode_chip.setObjectName("modeChip")
        lay.addWidget(self.mode_chip)
        lay.addSpacing(8)
        btn("search", "Find any tag or module (Ctrl+K)",
            self._show_quick_nav)
        self._update_mode_chip()
        return band

    def _on_scene_changed(self, name: str) -> None:
        self._record_nav(("scene", name))
        if getattr(self, "crumb", None) is not None:
            self.crumb.setText(f"Plant  ▸  {name}")
            a = self._scene_acts.get(name)
            if a is not None:
                a.setChecked(True)

    def _update_mode_chip(self) -> None:
        chip = getattr(self, "mode_chip", None)
        if chip is None:
            return
        controller = getattr(self.engine, "controller", None)
        closed = bool(controller is not None
                      and getattr(controller, "enabled", False))
        if closed and chip.text() != "CLOSED LOOP":
            chip.setText("CLOSED LOOP")
            chip.setStyleSheet("background: #DDEBDD; color: #2F6B3A;"
                               " border: 1px solid #9CC3A2;")
        elif not closed and chip.text() != "OPEN LOOP":
            chip.setText("OPEN LOOP")
            chip.setStyleSheet("background: #F5E3C8; color: #7A5200;"
                               " border: 1px solid #D9B36A;")

    def _dock(self, title: str, widget: QWidget, area, visible: bool = True) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setWidget(widget)
        dock.setObjectName(title.replace(" ", "_"))
        self.addDockWidget(area, dock)
        dock.setVisible(visible)
        return dock

    def _build_docks(self) -> None:
        self.mf_panel = MalfunctionPanel(self.flowsheet)
        self.field_panel = FieldOpsPanel(self.flowsheet)
        self.comms_panel = CommsPanel(self.server, self.db)
        self.engine_panel = EnginePanel(self.engine, self.flowsheet)
        self.log_panel = LogPanel()
        self.loops_panel = LoopsPanel(getattr(self.engine, "controller", None),
                                      self.open_loop_faceplate)

        self.docks = {
            # Hidden by default: the section displays carry every loop
            # now, and the panel returns from the View menu when wanted.
            "Loops": self._dock("Loops", self.loops_panel,
                                Qt.RightDockWidgetArea, False),
        }
        if self.alarms is not None:
            from .alarm_panel import AlarmSummaryPanel
            self.alarm_panel = AlarmSummaryPanel(self.alarms, self.journal,
                                                 self.open_faceplate)
            self.docks["Alarms"] = self._dock(
                "Alarms", self.alarm_panel, Qt.BottomDockWidgetArea, False)
        else:
            self.alarm_panel = None
        self.docks.update({
            "Malfunctions": self._dock("Malfunctions", self.mf_panel,
                                       Qt.BottomDockWidgetArea, False),
            "Field operations": self._dock("Field operations", self.field_panel,
                                           Qt.RightDockWidgetArea, False),
            "OPC UA": self._dock("OPC UA", self.comms_panel, Qt.RightDockWidgetArea,
                                 False),
            "Engine": self._dock("Engine", self.engine_panel, Qt.RightDockWidgetArea,
                                 False),
            "Log": self._dock("Log", self.log_panel, Qt.BottomDockWidgetArea, False),
        })
        self.tabifyDockWidget(self.docks["Malfunctions"], self.docks["Log"])
        if "Alarms" in self.docks:
            self.tabifyDockWidget(self.docks["Malfunctions"],
                                  self.docks["Alarms"])
        for name in ("OPC UA", "Engine"):
            self.tabifyDockWidget(self.docks["Field operations"], self.docks[name])

    def _build_actions(self) -> None:
        def act(text, slot, shortcut=None, checkable=False, tip="") -> QAction:
            a = QAction(text, self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.setCheckable(checkable)
            a.setStatusTip(tip or text)
            return a

        self.act_run = act("&Run", self._run, "F5", tip="Start the simulation")
        self.act_freeze = act("&Freeze", self._freeze, "F6",
                              tip="Hold the simulation at the current state")
        self.act_step = act("&Single step", self.engine.step_once, "F7",
                            tip="Advance exactly one integration step")
        self.act_save = act("&Save snapshot...", self._save_snapshot, "Ctrl+S")
        self.act_load = act("&Load snapshot...", self._load_snapshot, "Ctrl+O")
        self.act_settings = act("Se&ttings...", self._show_settings)
        self.act_quit = act("E&xit", self.close, "Ctrl+Q")

        self.act_closed = act("&Closed loop control", self._toggle_loop_mode,
                              "F9", checkable=True,
                              tip="Checked: the software DCS holds the plant."
                                  " Unchecked: open loop, outputs held,"
                                  " operate by hand or from an external DCS")
        ctl = getattr(self.engine, "controller", None)
        self.act_closed.setChecked(bool(ctl is not None
                                        and getattr(ctl, "enabled", True)))
        self.act_closed.setEnabled(ctl is not None)
        self.act_clear_mf = act("&Clear all malfunctions",
                                self.mf_panel._clear_all, "Ctrl+Shift+C")
        self.act_clear_ovr = act("Clear all local &overrides", self._clear_overrides)
        self.act_forces = act("Active &forces...", self._show_forces,
                              "Ctrl+F", tip="Every forced signal, who "
                              "forced it and why, with release")
        self.act_alarms = act("&Alarm summary...", self._show_alarms, "F3",
                              tip="Active alarms, the shelved list and "
                              "the event journal")
        self.act_alarms.setEnabled(self.alarms is not None)
        self.act_audible = act("A&udible annunciation", lambda _c: None,
                               checkable=True,
                               tip="Beep when a new CRITICAL or HIGH "
                               "alarm annunciates")
        self.act_audible.setChecked(True)
        self.act_quick = act("&Go to...", self._show_quick_nav, "Ctrl+K",
                             tip="Find any tag or module and jump to it")
        self.act_back = act("Display &back", self._nav_back, "Alt+Left")
        self.act_fwd = act("Display &forward", self._nav_fwd, "Alt+Right")
        self.act_new_display = act("&New display window",
                                   self._new_display_window,
                                   tip="A second operator display, for "
                                       "another monitor")
        self.act_new_display.setEnabled(self.operator_display is not None)
        self.act_variables = act("&Variables", self._show_variables, "F2",
                                 tip="Every tag by unit: value, units, "
                                     "quality and node id, in its own "
                                     "window")
        self.act_trends = act("&Trends", self._show_trends, "F4",
                              tip="The historian trend window; add pens "
                                  "from any tag's right-click menu")
        self.act_new_trend = act("New &trend window",
                                 self._new_trend_window,
                                 tip="An additional independent trend window")
        self.act_comp_map = act("Compressor &map", self._show_comp_map, "F8",
                                tip="C1 head-flow map: speed curves, surge "
                                    "line, stonewall and the live operating "
                                    "point")
        self.act_ph_curve = act("&pH response", self._show_ph_curve,
                                tip="The titration curve with the live "
                                    "operating point and the local process "
                                    "gain")
        self.act_col_views = act("Col&umn views", self._show_col_views,
                                 tip="Live temperature profile and parallel "
                                     "coordinates for T1 and T2")
        self.act_col_solver = act("Column s&olver", self._show_col_solver,
                                  tip="Steady-state design tool on the "
                                      "columns' own separation law")
        self.act_fuel_q = act("Fuel &quality", self._show_fuel_q,
                              tip="Heating value vs specific gravity: the "
                                  "inference line, the components off it, "
                                  "and the live header gas")
        self.act_tuning = act("&Tuning lab", self._show_tuning,
                              tip="Step test a loop, identify the "
                                  "process model, get SIMC and "
                                  "Ziegler-Nichols settings")
        self.act_tuning.setEnabled(
            getattr(self.engine, "controller", None) is not None)
        self.act_builder = act("Display &builder", self._show_builder,
                               tip="Draw a display: place dynamos and PVMs, "
                                   "draw pipes, import SVG or PNG graphics, "
                                   "save the page")
        self.act_user_disp = act("Open &user display...",
                                 self._open_user_display,
                                 tip="Open a page saved from the display "
                                     "builder, live")
        self.act_all_auto = act("All loops to &AUTO", self._all_auto)
        self.act_all_man = act("All loops to &MANUAL", self._all_manual)
        self.act_sis_reset = act("Give SIS field &reset", self._sis_reset,
                                 tip="Pulse XS-9002, the field reset "
                                 "pushbutton; latches clear only if their "
                                 "causes are gone")

        self.speed_actions = QActionGroup(self)
        self.speed_actions.setExclusive(True)
        self._speed_items = []
        for label, factor in [("0.5x", 0.5), ("1x real time", 1.0), ("2x", 2.0),
                              ("5x", 5.0), ("10x", 10.0)]:
            a = QAction(label, self)
            a.setCheckable(True)
            a.setChecked(abs(factor - 1.0) < 1e-9)
            a.triggered.connect(lambda _c, f=factor: self._set_speed(f))
            self.speed_actions.addAction(a)
            self._speed_items.append(a)

        self.act_help = act("&Contents", lambda: self._show_help("index"), "F1")
        self.act_about = act("&About", self._about)

        from .icons import make_icon
        self.act_run.setIcon(make_icon("run"))
        self.act_freeze.setIcon(make_icon("freeze"))
        self.act_step.setIcon(make_icon("step"))
        self.act_clear_mf.setIcon(make_icon("clear"))
        self.act_variables.setIcon(make_icon("table"))
        self.act_trends.setIcon(make_icon("chart"))
        self.act_builder.setIcon(make_icon("builder"))
        self.act_quick.setIcon(make_icon("search"))
        self.act_forces.setIcon(make_icon("forces"))
        self.act_settings.setIcon(make_icon("settings"))
        self.act_closed.setIcon(make_icon("loop"))
        self.act_save.setIcon(make_icon("save"))
        self.act_load.setIcon(make_icon("open"))

    def _build_menus(self) -> None:
        m = self.menuBar()

        f = m.addMenu("&File")
        f.addAction(self.act_save)
        f.addAction(self.act_load)
        f.addSeparator()
        f.addAction(self.act_settings)
        f.addSeparator()
        f.addAction(self.act_quit)

        s = m.addMenu("&Simulation")
        s.addAction(self.act_run)
        s.addAction(self.act_freeze)
        s.addAction(self.act_step)
        s.addSeparator()
        speed = s.addMenu("&Speed factor")
        for a in self._speed_items:
            speed.addAction(a)
        s.addSeparator()
        s.addAction(self.act_closed)
        s.addAction(self.act_all_auto)
        s.addAction(self.act_all_man)
        s.addAction(self.act_sis_reset)
        s.addSeparator()
        s.addAction(self.act_forces)
        s.addAction(self.act_clear_mf)
        s.addAction(self.act_clear_ovr)
        s.addSeparator()
        s.addAction(self.act_alarms)
        s.addAction(self.act_audible)

        v = m.addMenu("&View")
        v.addAction(self.act_quick)
        v.addAction(self.act_back)
        v.addAction(self.act_fwd)
        v.addSeparator()
        v.addAction(self.act_variables)
        v.addAction(self.act_trends)
        v.addAction(self.act_new_trend)
        v.addAction(self.act_comp_map)
        v.addAction(self.act_ph_curve)
        v.addAction(self.act_col_views)
        v.addAction(self.act_col_solver)
        v.addAction(self.act_fuel_q)
        v.addAction(self.act_tuning)
        v.addAction(self.act_new_display)
        v.addSeparator()
        v.addAction(self.act_builder)
        v.addAction(self.act_user_disp)
        v.addSeparator()
        v.addAction(self.act_alarms)
        panels = v.addMenu("&Panels")
        for name, dock in self.docks.items():
            a = dock.toggleViewAction()
            a.setText(name)
            panels.addAction(a)

        h = m.addMenu("&Help")
        h.addAction(self.act_help)
        for title, page in [("&Getting started", "getting_started"),
                            ("&Operating the HMI", "operating"),
                            ("Control system and &ESD", "control"),
                            ("&Connecting a DCS", "connecting"),
                            ("&Process description", "process"),
                            ("&Malfunctions and exercises", "exercises"),
                            ("&Troubleshooting", "troubleshooting")]:
            a = QAction(title, self)
            a.triggered.connect(lambda _c, p=page: self._show_help(p))
            h.addAction(a)
        h.addSeparator()
        h.addAction(self.act_about)

    def _build_toolbar(self) -> None:
        tb = self.addToolBar("Simulation")
        tb.setObjectName("SimulationToolBar")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        tb.addAction(self.act_run)
        tb.addAction(self.act_freeze)
        tb.addAction(self.act_step)
        tb.addSeparator()
        tb.addAction(self.act_clear_mf)
        if self.alarms is not None:
            from .icons import make_icon
            self.act_alarms.setIcon(make_icon("alarm"))
            tb.addSeparator()
            tb.addAction(self.act_alarms)
        tb.addSeparator()
        for a in (self.act_variables, self.act_trends, self.act_builder):
            tb.addAction(a)
            w = tb.widgetForAction(a)
            if w is not None:
                w.setToolButtonStyle(Qt.ToolButtonIconOnly)

    def _build_statusbar(self) -> None:
        bar = self.statusBar()
        self.status_labels: Dict[str, QLabel] = {}
        for key, width in [("mode", 150), ("sis", 120), ("alarms", 170),
                           ("engine", 150), ("speed", 90), ("scan", 130),
                           ("sim_time", 140), ("opc", 190), ("errors", 130),
                           ("cpu", 210)]:
            label = QLabel("-")
            label.setMinimumWidth(width)
            label.setFont(theme.font(8, mono=True))
            self.status_labels[key] = label
            bar.addPermanentWidget(label)
        bar.showMessage("Ready")

    # ------------------------------------------------------------------ actions
    def _run(self) -> None:
        if self.engine.stats.frozen_by_error:
            answer = QMessageBox.question(
                self, "Resume after model failure",
                "The engine froze after repeated model errors:\n\n"
                f"{self.engine.stats.last_error}\n\n"
                "Resume anyway? If the underlying fault is still present it will "
                "freeze again.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        self.engine.resume()

    def _freeze(self) -> None:
        self.engine.freeze()

    def _set_speed(self, factor: float) -> None:
        self.engine.speed_factor = factor

    def _toggle_loop_mode(self, checked: bool) -> None:
        controller = getattr(self.engine, "controller", None)
        if controller is None:
            return
        if checked:
            controller.close_loop()
            self._log_operator("Loop closed: internal DCS took the plant")
            self.statusBar().showMessage(
                "CLOSED LOOP: setpoints adopted as-found, all loops in "
                "service", 8000)
        else:
            controller.open_loop()
            self._log_operator("Loop opened: DCS passive, outputs held")
            self.statusBar().showMessage(
                "OPEN LOOP: DCS and SIS passive, outputs held - operate "
                "valves by hand or from an external DCS", 8000)

    def _log_operator(self, message: str, tag: str = "",
                      value: str = "") -> None:
        if self.journal is not None:
            self.journal.log("OPERATOR", message, source="operator",
                             tag=tag, value=value)

    # ----------------------------------------------- context and forces
    def _tag_context(self, ref: str):
        """Right-click entries for a tag or module reference."""
        from PySide6.QtWidgets import QApplication
        bus = getattr(self.engine, "bus", None)
        controller = getattr(self.engine, "controller", None)
        tag = ref if ref in self.db else ""
        if not tag and controller is not None:
            loop = controller.loops.get(ref)
            if loop is not None:
                tag = loop.pv_tag
        forced = bool(bus and any(r.tag == tag for r in bus.active_forces()))
        entries = [("Open faceplate", lambda: self.open_faceplate(ref), True)]
        if tag:
            entries.append(("Add to trend",
                            lambda: self.add_to_trend(tag), True))
            entries.append(("Show in Variables",
                            lambda: self._reveal_tag(tag), True))
            entries.append(("Copy tag name",
                            lambda: QApplication.clipboard().setText(tag),
                            True))
            entries.append(None)
            entries.append(("Force value...",
                            lambda: self._force_tag(tag), bus is not None))
            entries.append(("Release force",
                            lambda: self._release_force(tag), forced))
        return entries

    def _reveal_tag(self, tag: str) -> None:
        self.variables_panel.filter.setText(tag)
        self._show_variables()

    def _force_tag(self, tag: str) -> None:
        bus = getattr(self.engine, "bus", None)
        if bus is None or tag not in self.db:
            return
        t = self.db[tag]
        current = t.value if t.kind.analogue else int(bool(t.value))
        value, ok = QInputDialog.getText(
            self, f"Force {tag}",
            f"{t.desc}\nCurrent: {current}"
            + (f" {t.eu}" if t.kind.analogue else
               f"  ({t.state0}=0 / {t.state1}=1)")
            + "\n\nForce to:", text=str(current))
        if not ok or not value.strip():
            return
        reason, ok = QInputDialog.getText(
            self, f"Force {tag}", "Reason (required, goes to the audit):")
        if not ok or not reason.strip():
            self.statusBar().showMessage("Force cancelled: a reason is "
                                         "required", 4000)
            return
        try:
            v = (float(value) if t.kind.analogue
                 else value.strip().lower() in ("1", "true", "on",
                                                t.state1.lower()))
            bus.force(tag, v, source="operator", reason=reason.strip())
        except (ValueError, KeyError) as exc:
            QMessageBox.warning(self, "Force", str(exc))
            return
        self._log_operator(f"Forced: {reason.strip()}", tag=tag,
                           value=str(value))
        self.statusBar().showMessage(f"FORCED {tag} = {value}", 6000)

    def _release_force(self, tag: str) -> None:
        bus = getattr(self.engine, "bus", None)
        if bus is not None:
            bus.release(tag)
            self._log_operator("Force released", tag=tag)
            self.statusBar().showMessage(f"Released force on {tag}", 4000)

    def _show_alarms(self) -> None:
        dock = self.docks.get("Alarms")
        if dock is None:
            return
        dock.setVisible(True)
        dock.raise_()
        if self.alarm_panel is not None:
            self.alarm_panel.refresh()

    def _show_forces(self) -> None:
        bus = getattr(self.engine, "bus", None)
        if bus is None:
            QMessageBox.information(self, "Forces",
                                    "No virtual I/O bus in this session.")
            return
        from .force_dialog import ForceDialog
        dlg = getattr(self, "_force_dlg", None)
        if dlg is None or not dlg.isVisible():
            self._force_dlg = ForceDialog(bus, self)
        self._force_dlg.show()
        self._force_dlg.raise_()

    def _all_auto(self) -> None:
        c = getattr(self.engine, "controller", None)
        if c is not None and c.enabled:
            c.all_auto()
            self.statusBar().showMessage("All loops to AUTO / CAS", 4000)

    def _all_manual(self) -> None:
        c = getattr(self.engine, "controller", None)
        if c is not None:
            c.all_manual()
            self.statusBar().showMessage("All loops to MANUAL, outputs "
                                         "held", 4000)

    def _sis_reset(self) -> None:
        unit = self.flowsheet.unit("U900")
        if unit is None:
            return
        unit.initiators["XS-9002"] = True
        QTimer.singleShot(1500, lambda: unit.initiators.__setitem__(
            "XS-9002", False))
        self._log_operator("SIS field reset pushbutton pulsed",
                           tag="XS-9002")
        self.statusBar().showMessage(
            "SIS field reset given; latches clear only where the cause is "
            "gone", 6000)

    def _clear_overrides(self) -> None:
        count = 0
        with self.db.lock:
            for tag in self.db.all():
                if tag.override:
                    tag.override = False
                    count += 1
        log.info("Cleared %d local overrides", count)
        self.statusBar().showMessage(f"Cleared {count} local overrides", 4000)

    def _save_snapshot(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save snapshot", str(self.snapshot_dir / "snapshot.json"),
            "Snapshot files (*.json)")
        if not path:
            return
        try:
            self.engine.save_snapshot(path)
            self.statusBar().showMessage(f"Snapshot saved to {path}", 5000)
        except Exception as exc:
            log.exception("Snapshot save failed")
            QMessageBox.critical(self, "Snapshot failed",
                                 f"Could not save the snapshot.\n\n{exc}")

    def _load_snapshot(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load snapshot", str(self.snapshot_dir), "Snapshot files (*.json)")
        if not path:
            return
        try:
            r = self.engine.load_snapshot(path)
            self._log_operator(f"Snapshot restored: {Path(path).name}")
            note = (f"Snapshot {Path(path).name} restored: {r['tags']} tags, "
                    f"{r['units']} units, {r['bus']} tear streams")
            if r["skipped"] or r["missing"]:
                note += (f"  ({len(r['skipped'])} bus values skipped, "
                         f"{len(r['missing'])} units without state)")
            self.statusBar().showMessage(note, 8000)
        except Exception as exc:
            log.exception("Snapshot load failed")
            QMessageBox.critical(
                self, "Snapshot failed",
                # The engine rolls back on failure, so this claim is now true.
                f"Could not load the snapshot. The plant was put back as it was.\n\n{exc}")

    def open_loop_faceplate(self, module: str) -> None:
        controller = getattr(self.engine, "controller", None)
        if controller is None:
            return
        loop = controller.loops.get(module) or controller.loop_for_tag(module)
        if loop is None:
            return
        from .loop_faceplate import LoopFaceplate
        existing = self._open_faceplates.get(loop.module)
        if existing is not None and existing.isVisible():
            existing.raise_(); existing.activateWindow(); return
        unit = self.db[loop.pv_tag].unit if loop.pv_tag in self.db else ""
        dlg = LoopFaceplate(loop, controller, unit_name=unit, parent=self)
        retain_faceplate(self, loop.module, dlg)
        dlg.show()

    def open_device_faceplate(self, tag_name: str) -> None:
        ad = self._device_registry.get(tag_name)
        if ad is None:
            return
        from .device_faceplate import DeviceFaceplate
        existing = self._open_faceplates.get(ad.tag)
        if existing is not None and existing.isVisible():
            existing.raise_(); existing.activateWindow(); return
        dlg = DeviceFaceplate(ad, parent=self)
        retain_faceplate(self, ad.tag, dlg)
        dlg.show()

    def open_ai_faceplate(self, tag_name: str) -> None:
        from .ai_faceplate import AiFaceplate, _monitor_for
        existing = self._open_faceplates.get(tag_name)
        if existing is not None and existing.isVisible():
            existing.raise_(); existing.activateWindow(); return
        dlg = AiFaceplate(_monitor_for(self, tag_name), parent=self)
        retain_faceplate(self, tag_name, dlg)
        dlg.show()

    def open_faceplate(self, tag_name: str) -> None:
        # A tag owned by a control module opens the loop faceplate, one owned
        # by a motor or valve package opens the device faceplate, and bare
        # tags keep the plain one.
        controller = getattr(self.engine, "controller", None)
        if controller is not None:
            loop = controller.loop_for_tag(tag_name)
            if loop is not None:
                self.open_loop_faceplate(loop.module)
                return
        if tag_name in self._device_registry:
            self.open_device_faceplate(tag_name)
            return
        if tag_name in self.db and self.db[tag_name].kind is TagKind.AI:
            self.open_ai_faceplate(tag_name)
            return
        if tag_name not in self.db:
            log.warning("Faceplate requested for unknown tag %s", tag_name)
            return
        existing = self._open_faceplates.get(tag_name)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return
        dialog = FaceplateDialog(self.db, tag_name, self)
        retain_faceplate(self, tag_name, dialog)
        dialog.show()

    # -------------------------------------------- navigation and workspace
    def _record_nav(self, entry) -> None:
        if self._navigating:
            return
        if self._nav_history and self._nav_history[self._nav_pos] == entry:
            return
        del self._nav_history[self._nav_pos + 1:]
        self._nav_history.append(entry)
        if len(self._nav_history) > 100:
            del self._nav_history[0]
        self._nav_pos = len(self._nav_history) - 1

    def _nav_back(self) -> None:
        if self._nav_pos > 0:
            self._nav_pos -= 1
            self._apply_nav(self._nav_history[self._nav_pos])

    def _nav_fwd(self) -> None:
        if self._nav_pos < len(self._nav_history) - 1:
            self._nav_pos += 1
            self._apply_nav(self._nav_history[self._nav_pos])

    def _apply_nav(self, entry) -> None:
        self._navigating = True
        try:
            kind, value = entry
            if kind == "scene" and self.operator_display is not None:
                self.operator_display.show_scene(value)
        finally:
            self._navigating = False

    def _show_variables(self) -> None:
        """The variables table, a non-modal window like the trends."""
        if self.variables_panel.isWindow():
            self.variables_panel.show()
            self.variables_panel.raise_()
            self.variables_panel.activateWindow()

    def _show_comp_map(self) -> None:
        """The C1 head-flow map, a non-modal window like the trends."""
        from .comp_map import CompressorMap
        from .float_window import FloatingShell
        self._float_singleton("_comp_map", lambda: FloatingShell(
            "Compressor map", CompressorMap(self.db),
            "C1 head against suction flow, live", size=(740, 570)))

    def _show_ph_curve(self) -> None:
        """The titration-curve window, a non-modal window like the map."""
        from .float_window import FloatingShell
        from .ph_curve import PhCurve
        self._float_singleton("_ph_curve", lambda: FloatingShell(
            "pH response", PhCurve(self.db),
            "titration curve with the live operating point",
            size=(660, 510)))

    def _float_singleton(self, attr: str, maker) -> None:
        w = getattr(self, attr, None)
        if w is None or w not in self._float_windows:
            w = maker()
            w.setWindowIcon(QIcon(str(APP_ICON)))
            w.setAttribute(Qt.WA_DeleteOnClose, True)
            w.destroyed.connect(
                lambda *_a, ww=w: ww in self._float_windows
                and self._float_windows.remove(ww))
            self._float_windows.append(w)
            setattr(self, attr, w)
        w.show()
        w.raise_()
        w.activateWindow()

    def _show_col_views(self) -> None:
        from .column_views import ColumnViews
        from .float_window import FloatingShell
        self._float_singleton("_col_views", lambda: FloatingShell(
            "Column views", ColumnViews(self.db),
            "temperature profile and parallel coordinates",
            size=(920, 640)))

    def _show_col_solver(self) -> None:
        from .column_solver import ColumnSolver
        from .float_window import FloatingShell
        self._float_singleton("_col_solver", lambda: FloatingShell(
            "Column solver", ColumnSolver(),
            "steady state on the columns' own separation law",
            size=(860, 620)))

    def _show_fuel_q(self) -> None:
        from .float_window import FloatingShell
        from .fuel_quality import FuelQuality
        self._float_singleton("_fuel_q", lambda: FloatingShell(
            "Fuel quality", FuelQuality(self.db),
            "heating value against specific gravity", size=(660, 510)))

    def _show_tuning(self) -> None:
        controller = getattr(self.engine, "controller", None)
        if controller is None:
            return
        from .float_window import FloatingShell
        from .tuning_lab import TuningLab
        self._float_singleton("_tuning_lab", lambda: FloatingShell(
            "Tuning lab", TuningLab(self.engine, controller,
                                    self.journal),
            "step test · identify · recommend · apply",
            size=(880, 680)))

    def _show_builder(self) -> None:
        from .display_builder import DisplayBuilder
        controller = getattr(self.engine, "controller", None)
        fresh = getattr(self, "_disp_builder", None) is None \
            or self._disp_builder not in self._float_windows
        self._float_singleton("_disp_builder", lambda: DisplayBuilder(
            self.db, controller, self.alarms))
        if fresh:
            # a leftover autosave means the last session did not save
            self._disp_builder.offer_recovery()

    def _open_user_display(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        from .display_builder import DISPLAY_DIR, UserDisplay
        DISPLAY_DIR.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getOpenFileName(
            self, "Open user display", str(DISPLAY_DIR), "Displays (*.json)")
        if not path:
            return
        controller = getattr(self.engine, "controller", None)
        w = UserDisplay(path, self.db, controller, self.alarms,
                        open_ref=self._open_ref,
                        context_provider=self._tag_context)
        w.setWindowIcon(QIcon(str(APP_ICON)))
        w.setAttribute(Qt.WA_DeleteOnClose, True)
        w.destroyed.connect(
            lambda *_a, ww=w: ww in self._float_windows
            and self._float_windows.remove(ww))
        self._float_windows.append(w)
        w.show()
        w.raise_()
        w.activateWindow()

    def _show_quick_nav(self) -> None:
        from .quick_nav import QuickNav
        controller = getattr(self.engine, "controller", None)
        QuickNav(self.db, controller, self._open_ref,
                 self.add_to_trend, self).exec()

    def _open_ref(self, ref: str) -> None:
        controller = getattr(self.engine, "controller", None)
        if controller is not None and ref in controller.loops:
            self.open_loop_faceplate(ref)
        else:
            self.open_faceplate(ref)

    def _new_display_window(self) -> None:
        controller = getattr(self.engine, "controller", None)
        if controller is None:
            return
        from .hmi import OperatorDisplay
        w = OperatorDisplay(self.db, controller, self.open_loop_faceplate,
                            self.open_faceplate,
                            context_provider=self._tag_context,
                            alarms=self.alarms, standalone=True)
        w.setWindowTitle(
            f"Display {len(self._float_windows) + 2}  ·  AzeoPlant")
        w.setWindowIcon(QIcon(str(APP_ICON)))
        w.setAttribute(Qt.WA_DeleteOnClose, True)
        w.destroyed.connect(
            lambda *_a, ww=w: ww in self._float_windows
            and self._float_windows.remove(ww))
        w.resize(1100, 760)
        self._float_windows.append(w)
        w.show()

    def _new_trend_window(self) -> "TrendStudio":
        w = TrendStudio(self.db, self.historian,
                        getattr(self.engine, "controller", None))
        w.setWindowTitle("Trends  ·  AzeoPlant")
        w.setWindowIcon(QIcon(str(APP_ICON)))
        w.setAttribute(Qt.WA_DeleteOnClose, True)
        w.destroyed.connect(lambda *_a, ww=w: self._trend_gone(ww))
        w.installEventFilter(self)
        w.resize(900, 560)
        self._float_windows.append(w)
        self._active_trend = w
        w.show()
        return w

    def _trend_gone(self, w) -> None:
        if w in self._float_windows:
            self._float_windows.remove(w)
        if self._active_trend is w:
            self._active_trend = None

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        # "Add to trend" targets the trend window the user last touched
        if (event.type() == QEvent.WindowActivate
                and isinstance(obj, TrendStudio)):
            self._active_trend = obj
        return super().eventFilter(obj, event)

    def _ensure_trend(self) -> "TrendStudio":
        w = self._active_trend
        if w is None or w not in self._float_windows:
            w = self._new_trend_window()
        return w

    def _show_trends(self) -> None:
        w = self._ensure_trend()
        w.show()
        w.raise_()
        w.activateWindow()

    def add_to_trend(self, tag: str) -> None:
        """Route a pen to the historian window, the real-DCS way: open a
        trend window if none is up, land the pen in the last one used."""
        w = self._ensure_trend()
        w.add_pen(tag)
        w.show()
        w.raise_()
        w.activateWindow()

    def _show_settings(self) -> None:
        from .settings_dialog import SettingsDialog
        dlg = SettingsDialog(dict(self._prefs), self)
        if dlg.exec():
            self._apply_prefs(dlg.prefs())
            self._save_settings()

    def _apply_prefs(self, prefs: dict) -> None:
        self._prefs = dict(prefs)
        self._timer.setInterval(int(prefs["ui_refresh_ms"]))
        if self.alarm_banner is not None:
            self.alarm_banner.max_slots = int(prefs["banner_slots"])
        if self.alarms is not None:
            self.alarms.horn_priorities = set(
                str(prefs["horn_scope"]).split(","))

    def _save_settings(self) -> None:
        s = QSettings("AzeoPlant", "AzeoPlant Simulator")
        for key, value in self._prefs.items():
            s.setValue(key, value)
        s.setValue("geometry", self.saveGeometry())
        s.setValue("state", self.saveState())
        s.setValue("speed", float(self.engine.speed_factor))
        s.setValue("audible", self.act_audible.isChecked())
        if self.variables_panel.isWindow():
            s.setValue("vars_geometry", self.variables_panel.saveGeometry())
            s.setValue("vars_visible", self.variables_panel.isVisible())

    def _restore_settings(self) -> None:
        from .settings_dialog import DEFAULTS
        s = QSettings("AzeoPlant", "AzeoPlant Simulator")
        try:
            self._apply_prefs({
                "ui_refresh_ms": int(s.value("ui_refresh_ms",
                                             DEFAULTS["ui_refresh_ms"])),
                "banner_slots": int(s.value("banner_slots",
                                            DEFAULTS["banner_slots"])),
                "horn_scope": str(s.value("horn_scope",
                                          DEFAULTS["horn_scope"])),
                "confirm_exit": s.value("confirm_exit",
                                        DEFAULTS["confirm_exit"],
                                        type=bool),
            })
            geometry = s.value("geometry")
            if geometry is not None:
                self.restoreGeometry(geometry)
            state = s.value("state")
            if state is not None:
                self.restoreState(state)
            speed = float(s.value("speed", 1.0))
            self.engine.speed_factor = speed
            for a, factor in zip(self._speed_items,
                                 [0.5, 1.0, 2.0, 5.0, 10.0]):
                a.setChecked(abs(factor - speed) < 1e-9)
            self.act_audible.setChecked(
                s.value("audible", True, type=bool))
            if self.variables_panel.isWindow():
                vg = s.value("vars_geometry")
                if vg is not None:
                    self.variables_panel.restoreGeometry(vg)
                if s.value("vars_visible", False, type=bool):
                    self._show_variables()
        except Exception:
            # a stale or foreign settings blob must never break startup
            log.exception("Could not restore the saved workspace")

    def _show_help(self, page: str = "index") -> None:
        """Open the help browser, or bring it forward, at the given page."""
        viewer = getattr(self, "_help", None)
        if viewer is None or not viewer.isVisible():
            try:
                viewer = HelpBrowser(HELP_DIR, self)
            except Exception:
                log.exception("Could not open the help browser")
                QMessageBox.critical(self, "Help",
                                     "The help browser could not be opened. "
                                     "See the log for details.")
                return
            viewer.setWindowIcon(QIcon(str(APP_ICON)))
            self._help = viewer
        viewer.show_page(page)
        viewer.show()
        viewer.raise_()
        viewer.activateWindow()

    def _about(self) -> None:
        import platform

        import PySide6

        from ..version import build_info
        counts = self.db.counts()
        QMessageBox.about(
            self, "About AzeoPlant",
            "<h3>AzeoPlant Open Loop Process Simulator</h3>"
            f"<p><b>Version</b> {build_info()}<br>"
            f"Python {platform.python_version()} · "
            f"PySide6 {PySide6.__version__}</p>"
            "<p>A dynamic process simulator that publishes its signals over "
            "OPC UA for a DCS to control. It contains no controllers: every "
            "regulatory loop lives in the DCS.</p>"
            f"<p><b>Tags</b><br>AI {counts['AI']} &nbsp; AO {counts['AO']} &nbsp; "
            f"DI {counts['DI']} &nbsp; DO {counts['DO']}</p>"
            f"<p><b>Units modelled</b><br>{', '.join(u.code + ' ' + u.name for u in self.flowsheet.units)}</p>"
            f"<p><b>OPC UA endpoint</b><br>{self.server.stats.endpoint}<br>"
            "Node identifiers are the tag name at namespace 2.</p>")

    # ------------------------------------------------------------------ refresh
    def _refresh(self) -> None:
        try:
            snap = self.db.snapshot()
            if self.variables_panel.isVisible():
                self.variables_panel.refresh(snap)
            if self.operator_display is not None:
                self.operator_display.refresh(snap)
            for w in list(self._float_windows):
                if w.isVisible():
                    w.refresh(snap)
            if self.alarm_banner is not None:
                self.alarm_banner.refresh()
                if (self.act_audible.isChecked()
                        and self.alarms.take_annunciations()):
                    from PySide6.QtWidgets import QApplication
                    QApplication.beep()
            if (self.alarm_panel is not None
                    and self.docks["Alarms"].isVisible()):
                self.alarm_panel.refresh(snap)
            for dock_name, panel in [("Loops", self.loops_panel),
                                     ("Malfunctions", self.mf_panel),
                                     ("Field operations", self.field_panel),
                                     ("OPC UA", self.comms_panel),
                                     ("Engine", self.engine_panel),
                                     ("Log", self.log_panel)]:
                if self.docks[dock_name].isVisible():
                    panel.refresh(snap)
            self._refresh_status()
        except Exception:
            # A failure here would fire five times a second; log it once loudly
            # and keep the window alive rather than spamming or dying.
            log.exception("UI refresh failed; suspending refresh timer")
            self._timer.stop()
            QMessageBox.critical(
                self, "Display error",
                "The display refresh failed and has been suspended. The "
                "simulation and OPC UA server are still running. See the log "
                "for details.")

    def _refresh_status(self) -> None:
        st = self.engine.stats

        controller = getattr(self.engine, "controller", None)
        bus = getattr(self.engine, "bus", None)
        if controller is None:
            self.status_labels["mode"].setText("NO DCS")
            self.status_labels["mode"].setStyleSheet(
                f"color: {theme.MUTED_TEXT.name()};")
        elif getattr(controller, "enabled", True):
            self.status_labels["mode"].setText("CLOSED LOOP")
            self.status_labels["mode"].setStyleSheet(
                f"color: {theme.RUNNING.name()}; font-weight: bold;")
        else:
            holder = getattr(bus, "holder", None) if bus is not None else None
            text = ("OPEN LOOP · " + holder) if holder else "OPEN LOOP"
            self.status_labels["mode"].setText(text)
            self.status_labels["mode"].setStyleSheet(
                f"color: {theme.ALARM_HIGH.name()}; font-weight: bold;")
        self._update_mode_chip()
        if self.act_closed.isChecked() != bool(
                controller is not None and getattr(controller, "enabled",
                                                   False)):
            self.act_closed.blockSignals(True)
            self.act_closed.setChecked(bool(
                controller is not None and controller.enabled))
            self.act_closed.blockSignals(False)

        esd = getattr(controller, "esd", None) if controller else None
        if esd is not None and any(esd.latched.values()):
            fo = esd.first_out or next(c for c, v in esd.latched.items()
                                       if v)
            self.status_labels["sis"].setText(f"ESD {fo}")
            self.status_labels["sis"].setStyleSheet(
                f"color: {theme.ALARM_CRITICAL.name()}; font-weight: bold;")
        else:
            self.status_labels["sis"].setText("SIS OK" if esd else "-")
            self.status_labels["sis"].setStyleSheet(
                f"color: {theme.MUTED_TEXT.name()};")

        if self.alarms is not None:
            c = self.alarms.counts()
            unack = self.alarms.unacked_count()
            self.status_labels["alarms"].setText(
                f"ALM {c['CRITICAL']}C {c['HIGH']}H {c['ADVISORY']}A"
                f" · {unack} unack")
            colour = (theme.ALARM_CRITICAL if c["CRITICAL"] else
                      theme.ALARM_HIGH if c["HIGH"] or unack else
                      theme.MUTED_TEXT)
            self.status_labels["alarms"].setStyleSheet(
                f"color: {colour.name()};"
                + (" font-weight: bold;" if unack else ""))
        else:
            self.status_labels["alarms"].setText("-")
        running = st.running and not st.frozen_by_error
        state = "FROZEN (ERROR)" if st.frozen_by_error else (
            "RUNNING" if running else "FROZEN")
        colour = theme.ALARM_CRITICAL if st.frozen_by_error else (
            theme.RUNNING if running else theme.ALARM_HIGH)
        self.status_labels["engine"].setText(f"Engine: {state}")
        self.status_labels["engine"].setStyleSheet(
            f"color: {colour.name()}; font-weight: bold;")

        self.status_labels["speed"].setText(f"{st.speed_factor:g}x")
        self.status_labels["scan"].setText(
            f"Scan {st.scan_time_ms:5.1f} ms")
        hours, rem = divmod(int(st.sim_time), 3600)
        self.status_labels["sim_time"].setText(
            f"Sim {hours:02d}:{rem // 60:02d}:{rem % 60:02d}")

        if self.server is not None:
            sstat = self.server.stats
            fails = getattr(sstat, "publish_errors", 0)
            opc_text = (f"OPC UA {'up' if sstat.running else 'down'}"
                        f" · {sstat.node_count} nodes")
            if fails:
                opc_text += f" · {fails} failed"
            self.status_labels["opc"].setText(opc_text)
            opc_ok = sstat.running and not fails
            self.status_labels["opc"].setStyleSheet(
                f"color: {(theme.RUNNING if opc_ok else theme.ALARM_CRITICAL).name()};")
        else:
            self.status_labels["opc"].setText("OPC UA off")
            self.status_labels["opc"].setStyleSheet(
                f"color: {theme.MUTED_TEXT.name()};")

        err_text = f"Errors {st.errors} · overruns {st.overruns}"
        self.status_labels["errors"].setText(err_text)
        self.status_labels["errors"].setStyleSheet(
            f"color: {theme.ALARM_HIGH.name()};" if (st.errors or st.overruns) else "")

        cpu = psutil.cpu_percent(interval=None)
        vm = psutil.virtual_memory()
        rss = self._process.memory_info().rss / (1024 ** 2)
        self.status_labels["cpu"].setText(
            f"CPU {cpu:4.1f}% · RAM {vm.percent:4.1f}% · this {rss:5.0f} MB")
        self.status_labels["cpu"].setStyleSheet(
            f"color: {theme.ALARM_HIGH.name()};" if cpu > 85 or vm.percent > 90 else "")

    # -------------------------------------------------------------------- close
    def closeEvent(self, event) -> None:
        if self._prefs.get("confirm_exit", True):
            answer = QMessageBox.question(
                self, "Exit", "Stop the simulator and close?\n\n"
                "Any unsaved snapshot will be lost and connected OPC UA "
                "clients will lose their session.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                event.ignore()
                return
        log.info("Shutting down")
        self._save_settings()
        self._timer.stop()
        for w in list(self._float_windows):
            w.close()
        if self.variables_panel.isWindow():
            self.variables_panel.close()
        try:
            self.historian.stop()
        except Exception:
            log.exception("Historian did not stop cleanly")
        try:
            self.server.stop()
        except Exception:
            log.exception("OPC UA server did not stop cleanly")
        if self.journal is not None:
            try:
                self.journal.stop()
            except Exception:
                log.exception("Event journal did not stop cleanly")
        try:
            self.engine.stop()
        except Exception:
            log.exception("Engine did not stop cleanly")
        super().closeEvent(event)
