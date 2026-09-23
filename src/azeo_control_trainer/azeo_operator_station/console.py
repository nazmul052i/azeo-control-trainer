"""The Azeo Operator Station operator console.

A **published** PVM display, Azeo's chrome around it, and a PVM
faceplate when you click something. Distinct from
This is the one shipping station over published ``PvmDisplay`` revisions;
the former DynaLive stack is archived outside the import path.

    ┌──────────────────────────────────────────────────────┐
    │ ⌕ ⚠ ⚒ ⟳ ▤ ▽ ∿ ⚙ ⛭    26-Aug-26 21:32  CON-01\\op  ⏻ │  menu
    │ ◀ ▶ ⌂  Overview                                   ☰ │  navigation
    ├──────────────────────────────────────────────────────┤
    │                  the published display                │  PvmDisplayView
    ├──────────────────────────────────────────────────────┤
    │ ▲ FIC-101   ◆ LIC-201                    ACK  SUMMARY │  alarm banner
    └──────────────────────────────────────────────────────┘

**Nothing here reads a file.** The display comes from
`PvmDeployment.document()`, which goes through the publish rules — so
what an operator sees is a *revision somebody released to this
workstation*, never whatever happens to be on disk. That is the whole
point of the exercise, and it is the difference between "deploy" meaning
something and meaning nothing.

Three consequences worth stating, because each is a behaviour an
engineer has to see to believe:

- **Publishing changes nothing on screen.** It lights the Refresh
  bubble. The operator takes the update when they are not in the middle
  of something.
- **A display nobody has opened here fetches newest silently.** Nobody
  is looking at it, so there is nothing to interrupt.
- **An unpublished display does not appear at all**, however finished it
  looks in the studio. `PvmDeployment.displays()` reads releases, not
  the filesystem.

**One renderer.** The view is `PvmDisplayView`, which builds through
`DisplayRenderer` — the same object the PVM studio's canvas builds
through. The WYSIWYG guarantee in `tests/_smoke_operator.py` covers this
console for free, because it is the same painter.
"""
from __future__ import annotations

import logging
import weakref
from collections import OrderedDict
from contextlib import nullcontext
from dataclasses import replace

from PySide6.QtCore import QPoint, QRect, QTimer, Qt
from PySide6.QtGui import QCursor, QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSplitter, QToolButton, QVBoxLayout, QWidget

from azeo_control_trainer.core.hmi.binding import LiveGraphSource, RuntimeAlarmRegistry
from .shell.chrome import (
    AlarmBanner, ConsoleSettings, ConsoleStatus, StatusBar,
)
from .shell.chrome_azeo import NavigationBar
from .shell.navigation import NavigationStack
from azeo_control_trainer.core.hmi.model.alarms import blink_on
from azeo_control_trainer.core.hmi.theme.roles import Role
from azeo_control_trainer.core.hmi.theme.palette import RolePalette
from azeo_control_trainer.core.hmi.theme.fonts import apply_application_font
from azeo_control_trainer.core.hmi.theme.tokens import DEFAULT_THEME, THEMES
from azeo_control_trainer.core.hmi.theme.service import ThemeService, THEME_LABELS
from azeo_control_trainer.core.hmi.theme.widgets import (
    WidgetThemeBinding, apply_widget_theme, bind_operator_theme,
)
from azeo_control_trainer.core.hmi.pvms.base import primary_path, registry
from azeo_control_trainer.core.hmi.pvms.faceplate_actions import (
    resolve_associated_dcc,
    resolve_module_faceplate,
)
from azeo_control_trainer.core.hmi.pvms.layout import LayoutStore, NEW_WINDOW, open_target
from azeo_control_trainer.core.hmi.pvms.render import PvmFaceplateWidget
from azeo_control_trainer.core.hmi.pvms.rendering.viewer import PvmDisplayView
from .alarm_rollup import DisplayAlarmRollup
from .dialogs import (
    AlarmBannerHelpDialog, AlarmFilter, AlarmFilterDialog, AlarmListDialog,
    DisplayTagSettingsDialog, LocalLogonDialog, StationErrorsDialog,
    StationSearchDialog, UtilitiesDialog, station_search_records,
)
from .layout_surface import StationLayoutSurface

log = logging.getLogger("azeo.operator_station")

#: How often the header clock and the bubbles refresh.
CHROME_MS = 1000

#: Compatibility hook for builds that intentionally omit a station service.
#: This build services every standard button, so nothing is dropped.
UNAVAILABLE = ()


class LiveStation(QWidget):
    """One Azeo Operator Station console over a published PVM display store."""

    def __init__(self, deployment, graphs_provider, *,
                 settings: ConsoleSettings | None = None,
                 config_root=None, control_designer_opener=None,
                 operator_change_recorder=None, simulation_service=None,
                 training_root=None, history_path=None, parent=None):
        super().__init__(parent)
        from azeo_control_trainer.core.presentation.app_icon import get_app_icon
        self.setWindowIcon(get_app_icon(application_id="operator_station"))
        # A station can be launched as a child tool without passing through
        # app.main(); apply the packaged face before building its chrome.
        apply_application_font()
        self.deployment = deployment
        self.graphs_provider = graphs_provider
        self.alarm_state = RuntimeAlarmRegistry()
        from azeo_control_trainer.core.procedures.hmi import ProcedureSource
        from azeo_control_trainer.core.datastore.memory_tags import MemoryTagStore
        memory = MemoryTagStore(simulation_service.project_dir) if simulation_service else None
        self.live_source = ProcedureSource(
            LiveGraphSource(graphs_provider, self.alarm_state, memory=memory), self.procedure_session)
        self.config_root = config_root
        from azeo_control_trainer.core.hmi.binding import BindingEngine
        self._context_engine = BindingEngine(self.live_source)
        self._context_engine.configuration_root = config_root or getattr(
            getattr(deployment, "store", None), "root", None)
        from azeo_control_trainer.config.distribution import component_available
        self.control_designer_opener = (
            control_designer_opener if component_available("control_designer") else None)
        self.operator_change_recorder = operator_change_recorder
        self.simulation_service = simulation_service
        from .freshness import ScanFreshness
        self._freshness = ScanFreshness()
        self._view_cache = OrderedDict()
        self._published_displays = ()
        self._pending_display_count = 0
        self._historian_signature = None
        self.training_session = None
        self.training_error = ""
        if simulation_service is not None:
            from azeo_control_trainer.core.simulation.training import TrainingSession
            self.training_session = TrainingSession(
                simulation_service, self.live_source, self.alarm_state,
                training_root or simulation_service.project_dir / "simulation" / "training")
        self.settings = settings or ConsoleSettings(azeo_chrome=True)
        self._default_theme = self.settings.theme if self.settings.theme in THEMES else "silver"
        self.settings = replace(self.settings, theme=self._default_theme)
        self._theme_startup_message = ""
        self._changing_theme = False
        self._preferences = None
        environment_root = config_root or getattr(
            getattr(deployment, "store", None), "root", None)
        if environment_root is not None:
            import hashlib
            from pathlib import Path
            from PySide6.QtCore import QSettings
            namespace = hashlib.sha256(
                f"{Path(environment_root).resolve()}|{self.settings.console_id}".encode()).hexdigest()[:20]
            try:
                self._preferences = QSettings("Azeo", "OperatorLiveWorkspace")
                self._preferences.beginGroup(namespace)
                self.settings = replace(self.settings, **{
                    key: self._preferences.value(key, getattr(self.settings, key), type=bool)
                    for key in ("comfortable", "dock_context")})
                saved = self._preferences.value("theme", self._default_theme)
                if isinstance(saved, str) and saved in THEMES:
                    self.settings = replace(self.settings, theme=saved)
                else:
                    self._theme_startup_message = "Saved HMI theme is unavailable; using the station default."
                if self._preferences.status() != QSettings.NoError:
                    self._theme_startup_message = "HMI preferences could not be read; using the station default."
                    self.settings = replace(self.settings, theme=self._default_theme)
            except (TypeError, ValueError, OSError, RuntimeError):
                self._theme_startup_message = "HMI preferences could not be read; using the station default."
                self.settings = replace(self.settings, theme=self._default_theme)
                self._preferences = None
        self.themes = ThemeService(self.settings.theme, self, initial_theme=self._default_theme)
        log.info("Live Station opened: console=%s config_root=%s",
                 self.settings.console_id, config_root or "<deployment>")
        self.palette_roles = THEMES.get(self.settings.theme,
                                        THEMES[DEFAULT_THEME])
        environment_root = config_root or getattr(
            getattr(deployment, "store", None), "root", None)
        self.layout_store = LayoutStore(environment_root) \
            if environment_root is not None else None
        self.assignment = self.layout_store.assignment(
            self.settings.console_id) if self.layout_store else None
        self.layout_model = self.layout_store.layout(
            self.assignment.layout) \
            if self.layout_store and self.assignment \
            and self.assignment.layout else None
        self.active_display_set = self.layout_store.display_set(
            self.assignment.active_display_set) \
            if self.layout_store and self.assignment \
            and self.assignment.active_display_set else None
        apply_widget_theme(self, self.settings.theme, surface=Role.SURFACE_BG, basic=True)

        from .workspace import ContextWorkspace, OperatorCommandBar, chrome_palette
        self.menu = OperatorCommandBar(self.settings, self)
        self.menu.set_training_available(self.training_session is not None)
        self.menu.drop_buttons(UNAVAILABLE)
        self.nav = NavigationBar(self.settings)
        self.nav.palette_ = chrome_palette(self.settings.theme)
        self.banner = AlarmBanner(self.settings)
        #: The bottom strip, under the banner — the Azeo figure has
        #: both, and the existing `StatusBar` is reused rather than a
        #: second one written. Its **middle stays empty** in normal
        #: operation on purpose (`docs/console/07`): a bar with something in
        #: every slot has nowhere an exception could appear that the
        #: eye would notice.
        self.status = StatusBar(self.settings)
        self.view: PvmDisplayView | None = None
        self.placeholder = QLabel(
            "No display has been published to %s.\n\n"
            "Publish one from the PVM Display Studio — a display with a "
            "draft but no release is not deployed, however finished it "
            "looks in the editor." % self.settings.console_id)
        self.placeholder.setWordWrap(True)
        self.placeholder.setStyleSheet(
            "color: %s; padding: 40px;" % self.palette_roles[Role.TEXT_DIM])

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.layout_surfaces: list[StationLayoutSurface] = []
        if self.layout_model is not None:
            for index, screen in enumerate(self.layout_model.screens):
                surface = StationLayoutSurface(
                    self.layout_model, self.palette_roles,
                    screen_index=index)
                surface.navigate.connect(self._frame_navigation)
                self.layout_surfaces.append(surface)
                if index:
                    surface.setWindowTitle(
                        f"{self.settings.console_id} - {screen.name}")
                    surface.resize(screen.width, screen.height)
                    ox, oy = screen.origin
                    x = int(ox * screen.width if abs(ox) <= 8 else ox)
                    y = int(oy * screen.height if abs(oy) <= 8 else oy)
                    surface.move(x, y)
        self.layout_surface = self.layout_surfaces[0] \
            if self.layout_surfaces else None
        if sum(len(surface.hosts) for surface in self.layout_surfaces) == 1:
            # One hierarchy selector is enough on a single-frame seat. Keep
            # authored frame navigation on multi-frame/multi-screen layouts.
            for host in self.layout_surface.hosts.values():
                host.navigation_enabled = False
        self.views: dict[str, PvmDisplayView] = {}
        if self.layout_surface is not None:
            self.body.addWidget(self.layout_surface)
            self.placeholder.hide()
        else:
            self.body.addWidget(self.placeholder)

        from azeo_control_trainer.core.presentation.headless import is_headless
        if not is_headless():
            for surface in self.layout_surfaces[1:]:
                surface.setWindowFlags(surface.windowFlags() | Qt.Window)
                surface.show()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.menu)
        layout.addWidget(self.nav)
        self.process_area = QWidget()
        self.process_area.setMinimumWidth(240)
        self.process_area.setLayout(self.body)
        self.workspace = ContextWorkspace(self)
        self.workspace_split = QSplitter(Qt.Horizontal)
        self.workspace_split.addWidget(self.process_area)
        self.workspace_split.addWidget(self.workspace)
        self.workspace_split.setStretchFactor(0, 3)
        self.workspace_split.setStretchFactor(1, 2)
        self.workspace_split.setChildrenCollapsible(False)
        self.workspace_split.setSizes([900, 550])
        if self._preferences is not None:
            try:
                sizes = self._preferences.value("split", [])
                if len(sizes) == 2 and all(int(size) > 0 for size in sizes):
                    self.workspace_split.setSizes([int(size) for size in sizes])
            except (ValueError, TypeError, OSError, RuntimeError):
                pass
        layout.addWidget(self.workspace_split, 1)
        self.theme_notice = QLabel(self._theme_startup_message)
        self.theme_notice.setWordWrap(True)
        self.theme_notice.setTextFormat(Qt.PlainText)
        self.theme_notice.setVisible(bool(self._theme_startup_message))
        layout.addWidget(self.theme_notice)
        self.training_strip = QLabel()
        self.training_strip.setWordWrap(True)
        self.training_strip.hide()
        layout.addWidget(self.training_strip)
        self.command_feedback = QLabel()
        self.command_feedback.setWordWrap(True)
        self.command_feedback.setTextFormat(Qt.PlainText)
        self.feedback_area = QWidget()
        feedback_row = QHBoxLayout(self.feedback_area)
        feedback_row.setContentsMargins(8, 3, 8, 3)
        feedback_row.addWidget(self.command_feedback, 1)
        dismiss = QToolButton()
        dismiss.setText("Dismiss")
        dismiss.clicked.connect(self.feedback_area.hide)
        feedback_row.addWidget(dismiss)
        self.feedback_area.hide()
        layout.addWidget(self.feedback_area)
        self._feedback_timer = QTimer(self)
        self._feedback_timer.setSingleShot(True)
        self._feedback_timer.timeout.connect(self.feedback_area.hide)
        layout.addWidget(self.banner)
        layout.addWidget(self.status)

        self.history = NavigationStack()
        self.faceplates: list = []
        self.user_faceplates: list = []
        self.tuning_trends: list = []
        self.operator_dialogs: list = []
        self.process_history_views: list = []
        self.errors: tuple = ()
        self.alarm_filter = AlarmFilter()
        self.full_desktop = False
        self.script_store: dict = {}
        self.script_scopes: dict = {}
        self.tag_display_mode = ""
        self.context_path = ""
        self._display_menu = None
        self._display_branch_menu = None
        self._display_recent_menu = None
        self._tools_menu = None
        self._recent_children: dict[str, str] = {}
        self._coordinating = False
        self.alarm_rollup = DisplayAlarmRollup(
            deployment, self.live_source, self.palette_roles,
            config_root=self.config_root)
        from azeo_control_trainer.core.hmi.history import ContinuousHistorian
        try:
            self.historian = ContinuousHistorian(None, resolver=self.live_source,
                                               archive_path=history_path, clock_context=self._history_clock)
        except Exception:  # noqa: BLE001 - a disk problem must not prevent operating the station
            log.exception("Historian archive unavailable; retaining live samples in memory")
            self.historian = ContinuousHistorian(None, resolver=self.live_source, clock_context=self._history_clock)
        self._history_alarm_cursor = 0
        if self.training_session is not None:
            self.historian.training_archive = self.training_session.archive
            self.training_session.history_event_sink = self._history_training_event
        if self.simulation_service is not None:
            self.simulation_service.history_event_sink = self._history_workbench_event
        self._configure_historian()

        #: Themes are a RUNTIME choice here, as they are in Azeo —
        #: the console subscribes rather than being registered with
        #: the service, so a faceplate opened after a switch is no
        #: different from one that was open across it.
        self.themes.changed.connect(self._theme_changed)

        self.menu.activated.connect(self.menu_action)
        self.nav.navigate.connect(self.navigate)
        self.nav.select.connect(self.open_display_menu)
        self.nav.open_display.connect(self.show_display)
        self.banner.acknowledge.connect(self.acknowledge_all)
        self.banner.open_summary.connect(self.show_alarm_list)
        self.banner.help_requested.connect(self.open_banner_help)
        self._install_navigation_shortcuts()
        from PySide6.QtGui import QKeySequence, QShortcut
        self._help_shortcut = QShortcut(QKeySequence("F1"), self)
        self._help_shortcut.activated.connect(self.open_suite_help)

        if self.layout_surface is not None:
            self.reset_layout()
        else:
            first = self.deployment.displays()
            if first:
                self.show_display(first[0])
        self.sync_chrome()
        self._sync_training_strip()

        #: The menu bar's clock and its bubbles. Without this the
        #: header read 00:00:00 forever — the same dead-clock defect the
        #: archived station once had, on the screen an operator
        #: dates log entries from. One tick feeds both.
        self._tick = QTimer(self)
        self._tick.setInterval(CHROME_MS)
        self._tick.timeout.connect(self._timer_tick)
        self._tick.start()
        self.tick()

    def _sync_training_strip(self):
        session = self.training_session
        active = session is not None and session.active
        self.training_strip.setVisible(active)
        if active:
            caps = session.workbench.process_capabilities()
            state = "Paused" if caps.get("paused") else "Running"
            self.training_strip.setText(
                f"Training · {session.exercise.name} · {state} · {session.elapsed():.0f} s")
            self.training_strip.setToolTip("Open Training to review objectives and session controls")

    def tick(self) -> None:
        """Advance the header clock and re-read the deployment.

        The clock is wall time here because this console has no
        controller of its own to ask — and it says so by carrying the
        workstation's own value rather than pretending to a server
        time it was never handed.
        """
        import time

        now = time.time()
        # Alarm membership, navigation and the update badge ask the same
        # release questions. Re-reading their JSON files held the GUI thread
        # two or three times per tick without discovering anything new.
        with getattr(self.deployment, "catalog_snapshot", nullcontext)():
            self._refresh_station(now)

    def _refresh_station(self, now: float) -> None:
        self.menu.server_time = now
        with self.live_source.snapshot():
            self.alarm_rollup.poll()
            self._configure_historian()
            self.historian.collect()
            self._history_alarm_events()
        if self.training_session is not None:
            try:
                self.training_session.tick()
                self.training_error = ""
            except Exception as error:  # noqa: BLE001 - keep the operator console usable
                self.training_error = str(error)
                log.exception("Training recording failed")
        self.banner.set_banner(
            self.alarm_rollup.banner(self.settings.banner_lines),
            blink_on(now))
        self.sync_status(now)
        self.sync_chrome()
        self._sync_training_strip()

    def _timer_tick(self) -> None:
        import time
        scope = getattr(self.deployment, "background_catalog_snapshot", None)
        if scope is None:
            self.tick()
            return
        with scope():
            self._refresh_station(time.time())

    def sync_status(self, now: float) -> None:
        """The bottom strip.

        **The revision is the interesting field.** "A console runs a
        revision, not a file" is the whole claim of this console, and
        this is where it is visible — an engineer can read what the
        seat is actually showing without opening anything.
        """
        current = self.history.current or ""
        from .freshness import SourceState
        from .shell.chrome import Exception_
        state = SourceState()
        exceptions = ()
        service = self.simulation_service
        if service is not None:
            capabilities = service.process_capabilities()
            if capabilities.get("available"):
                exceptions = (Exception_.SIMULATION,)
            executive = service.executive
            paused = executive is not None and not executive.is_running
            state = self._freshness.sample(service._runtimes(), now, paused=paused)
        self.status.setToolTip(state.detail)
        self.status.set_status(ConsoleStatus(
            server_time=now, last_update=state.last_update,
            scan_ms=state.scan_ms, source_state=state.state,
            exceptions=exceptions,
            display_id=current,
            revision=self.deployment._held(current) or 0
            if current else 0))

    def _configure_historian(self) -> None:
        graphs = tuple(self.graphs_provider().values())
        signature = tuple((graph.name, id(graph), tuple(
            (block.id, id(block), block.instance_name)
            for block in graph.blocks.values())) for graph in graphs)
        if signature != self._historian_signature:
            self.historian.configure_from(None, graphs)
            self._historian_signature = signature

    def _history_clock(self):
        if self.simulation_service is None:
            return {}
        caps = self.simulation_service.process_capabilities()
        return {"sim_time": caps.get("sim_time"), "paused": caps.get("paused")}

    def _history_training_event(self, action, target, detail, sim_time):
        self.historian.add_event("training", action, target, detail, sim_time=sim_time)

    def _history_workbench_event(self, action, target, detail, sim_time):
        self.historian.add_event("clock" if action in {"process_pause", "process_resume", "process_step", "process_speed", "snapshot_restore"}
                                 else "mode" if action == "mode" else "operator",
                                 action, target, detail, sim_time=sim_time)

    def _history_alarm_events(self):
        for row in self.alarm_state.events:
            if row["sequence"] > self._history_alarm_cursor:
                self.historian.add_event("alarm", row["action"], row.get("key", row.get("module", "")),
                                         row, t=row["time"] - self.historian.origin,
                                         sim_time=self.historian._last_context.get("sim_time") if self.historian._last_context else None)
                self._history_alarm_cursor = row["sequence"]

    def _park_view(self, view, frame_name="") -> None:
        if frame_name:
            surface = self._surface_for_frame(frame_name)
            if surface is not None:
                surface.take_frame_widget(frame_name)
        else:
            self.body.removeWidget(view)
        view.set_active(False)
        view.hide()
        # Removing a widget from a layout retains its Qt owner. Reparenting
        # every parked view forced native/style work on every return trip.
        if view.parentWidget() is None:
            view.setParent(self)
        key = view._cache_key
        previous = self._view_cache.pop(key, None)
        if previous is not None and previous is not view:
            previous.close()
            previous.deleteLater()
        self._view_cache[key] = view
        while len(self._view_cache) > 6:
            _, expired = self._view_cache.popitem(last=False)
            expired.close()
            expired.deleteLater()

    def _clear_view_cache(self, displays=None) -> None:
        for key, view in tuple(self._view_cache.items()):
            if displays is None or key[1] in displays:
                self._view_cache.pop(key)
                view.close()
                view.deleteLater()

    def acknowledge_all(self) -> tuple:
        """Acknowledge every standing alarm in the shared registry."""
        if not self.settings.write_authority:
            return ()
        changed = self.alarm_state.acknowledge()
        from azeo_control_trainer.config.logging_config import audit_event
        audit_event("operator_station", "alarms.acknowledge_all",
                    count=len(changed), actor=self.settings.user)
        self._after_alarm_acknowledgement()
        return changed

    def acknowledge_container(self, prefix: str) -> tuple:
        """Acknowledge one module/container through station policy."""
        if not self.settings.write_authority:
            return ()
        root = str(prefix or "").rstrip("/")
        keys = tuple(
            row.key for row in self.alarm_state.records()
            if row.key == root or row.key.startswith(root + "/"))
        changed = self.alarm_state.acknowledge(keys)
        from azeo_control_trainer.config.logging_config import audit_event
        audit_event("operator_station", "alarms.acknowledge_container",
                    prefix=root, count=len(changed), actor=self.settings.user)
        self._after_alarm_acknowledgement()
        return changed

    def _after_alarm_acknowledgement(self) -> None:
        """Refresh every retained alarm surface after one checked ACK."""
        for view in self.views.values():
            view.refresh()
        if self.view is not None and self.view not in self.views.values():
            self.view.refresh()
        self.alarm_rollup.poll()

    def show_banner_help(self) -> tuple:
        """What the banner's own controls do.

        The manual puts `?` on the banner rather than the menu bar —
        "Click ? on the Alarm Banner to see descriptions for the Alarm
        Banner buttons and controls" — so help about these controls
        sits beside them.
        """
        return (("ACK", "Acknowledge the alarms on the banner"),
                ("SUMMARY", "Open the full alarm list"),
                ("SILENCE", "Hold alarms quiet. It LATCHES and says so, "
                            "and a new alarm clears it."),
                ("?", "This list"))

    def open_banner_help(self):
        """Present the help requested by the banner's visible ``?`` button."""
        return self._present(AlarmBannerHelpDialog(
            self.show_banner_help(), self))

    # ------------------------------------------------------- displays
    def show_display(self, display_id: str, record: bool = True,
                     from_frame: str = "", target_frame: str = "",
                     coordinate: bool = True) -> bool:
        """Put a PUBLISHED revision on screen.

        False when nothing is published to this station under that
        name — which is not an error, it is a display that has not been
        released here.
        """
        if self.active_display_set is not None \
                and display_id not in self.active_display_set.displays():
            log.info("%s is not in active display set %s", display_id,
                     self.active_display_set.name)
            return False
        revision = self.deployment.open(display_id)
        if revision is None:
            log.info("%s is not published to %s", display_id,
                     self.settings.console_id)
            return False
        frame_name = ""
        if self.layout_surface is not None:
            frame_name = target_frame or open_target(
                self.layout_model, self.active_display_set, display_id,
                from_frame=from_frame)
            if frame_name != NEW_WINDOW \
                    and self._surface_for_frame(frame_name) is None:
                candidates = self._all_frame_names()
                frame_name = candidates[0] if candidates else NEW_WINDOW

        def callback(target, source=frame_name):
            return self.show_display(target, from_frame=source)

        key = (frame_name, display_id, revision, self.settings.theme)
        previous = self.views.get(frame_name) if self.layout_surface else self.view
        if previous is not None and getattr(previous, "_cache_key", None) == key:
            if record:
                self.history.go(display_id)
            self.view = previous
            self._remember_branch(display_id)
            if coordinate and frame_name and frame_name != NEW_WINDOW:
                self._coordinate_displays(display_id, frame_name, from_frame)
            self.sync_chrome(refresh_deployment=False)
            return True
        view = self._view_cache.pop(key, None) if frame_name != NEW_WINDOW else None
        if view is None:
            document = self.deployment.document(display_id)
            if document is None:
                return False
            view = PvmDisplayView(
                document, self.graphs_provider, theme=self.settings.theme,
                config_root=(self.deployment.configuration_root(display_id)
                             if callable(getattr(self.deployment, "configuration_root", None))
                             else self.config_root), open_display=callback,
                source=self.live_source, layout=self.layout_model,
                action_handler=self.display_event_action,
                write_handler=self._write, write_checker=self._can_write,
                script_scopes=self.script_scopes,
                pvm_activation_handler=self.open_faceplate,
                chart_handler=self.add_tag_to_chart,
                chart_batch_handler=self.add_tags_to_historian)
            view._cache_key = key
        if self.tag_display_mode:
            view.set_show_tag(self.tag_display_mode)
        if self.layout_surface is not None and frame_name != NEW_WINDOW:
            if previous is not None:
                self._park_view(previous, frame_name)
            surface = self._surface_for_frame(frame_name)
            if surface is None:
                return False
            surface.set_frame_widget(frame_name, view)
            self.views[frame_name] = view
        elif self.layout_surface is not None:
            key = f"popup:{display_id}:{len(self.views)}"
            self.views[key] = view
            view.setWindowTitle(display_id)
            from azeo_control_trainer.core.presentation.headless import is_headless
            if not is_headless():
                view.setWindowFlags(view.windowFlags() | Qt.Window)
                view.show()
        elif self.view is not None:
            self._park_view(self.view)
        else:
            self.placeholder.hide()
            self.body.removeWidget(self.placeholder)
        if self.layout_surface is None:
            self.body.addWidget(view)
            view.show()
        view.set_active(True)
        self.view = view
        self._remember_branch(display_id)
        if record:
            self.history.go(display_id)
        if coordinate and frame_name and frame_name != NEW_WINDOW:
            self._coordinate_displays(display_id, frame_name, from_frame)
        self.sync_chrome(refresh_deployment=False)
        return True

    def _all_frame_names(self) -> tuple[str, ...]:
        return tuple(name for surface in self.layout_surfaces
                     for name in surface.frame_names())

    def _surface_for_frame(self, frame_name: str):
        return next((surface for surface in self.layout_surfaces
                     if frame_name in surface.frame_names()), None)

    def _frame_navigation(self, display_id: str, frame_name: str) -> None:
        self.show_display(display_id, from_frame=frame_name)

    def _remember_branch(self, display_id: str) -> None:
        if self.active_display_set is None:
            return
        trail = self.active_display_set.breadcrumb(display_id)
        for parent, child in zip(trail, trail[1:]):
            self._recent_children[parent] = child

    def _coordinate_displays(self, display_id: str, frame_name: str,
                             from_frame: str = "") -> None:
        if self._coordinating or self.layout_model is None:
            return
        source = self.layout_model.frame(from_frame) if from_frame else None
        if source is not None and source.prevent_external_coordination:
            return
        opened = self.layout_model.frame(frame_name)
        if opened is None or not opened.coordinates:
            return
        targets = self.layout_model.coordination_targets(
            self.active_display_set, display_id, self._recent_children)
        self._coordinating = True
        try:
            for target_frame, target_display in targets.items():
                # Coordination follows an explicit call-up into companion
                # frames. It must never replace the frame the operator just
                # navigated: on a single-frame workstation a remembered L4
                # descendant otherwise immediately painted over an L1 call-up
                # while history and chrome continued to say L1.
                if target_frame == frame_name:
                    continue
                current = self.views.get(target_frame)
                if current is not None \
                        and current.display.name == target_display:
                    continue
                existing_frame = next((name for name, candidate in
                                       self.views.items()
                                       if not name.startswith("popup:")
                                       and candidate.display.name
                                       == target_display), "")
                target = self.layout_model.frame(target_frame)
                relocation = getattr(target, "relocation", "none")
                if existing_frame and existing_frame != target_frame \
                        and relocation == "none":
                    continue
                displaced = current.display.name if current is not None else ""
                if self.show_display(
                        target_display, record=False,
                        target_frame=target_frame, coordinate=False) \
                        and relocation == "swap" and existing_frame \
                        and displaced:
                    self.show_display(
                        displaced, record=False, target_frame=existing_frame,
                        coordinate=False)
        finally:
            self._coordinating = False

    def display_event_action(self, action: dict, view=None, item=None) -> bool:
        """Run a built-in or TypeScript display interaction action."""
        kind = str(action.get("kind", ""))
        target = str(action.get("target", ""))
        if kind == "procedure_help":
            from .procedure_actions import block_help
            return block_help(self, target)
        if kind == "procedure_command":
            from .procedure_actions import dispatch
            return dispatch(self, action, view, item)
        if kind == "script":
            from azeo_control_trainer.core.hmi.pvms.scripting import GraphicsScriptRuntime, ScriptContext
            resolver = view._property_resolver() if view is not None else None
            for name, value in (getattr(resolver, "variables", None)
                                or {}).items():
                self.script_scopes.setdefault(f"Dsp.{name}", value)
            standards = getattr(resolver, "standards", None)
            for entry in getattr(standards, "entries", ()):
                self.script_scopes.setdefault(
                    f"GL.{entry.get('name', '')}", entry.get("value"))
            context = ScriptContext(
                source=self.live_source, alarm_state=self.alarm_state,
                deployment=self.deployment, view=view, item=item,
                user=self.settings.user,
                can_operate=self.settings.write_authority,
                can_write=self._can_write,
                write_value=self._write,
                acknowledge_container=self.acknowledge_container,
                acknowledge_all=self.acknowledge_all,
                open_display=self.show_display,
                open_faceplate=lambda path: self._open_script_pvm(
                    path, "faceplate"),
                open_detail=lambda path: self._open_script_pvm(
                    path, "detail"),
                open_context=lambda path: self._open_script_pvm(
                    path, "faceplate"),
                session_store=self.script_store,
                scope_values=self.script_scopes)
            answer = GraphicsScriptRuntime().run(
                str(action.get("source", "")), context)
            if not answer.ok:
                self.errors = tuple(sorted(set(self.errors) | {
                    (getattr(getattr(view, "display", None), "name", ""),
                     "Script", answer.error)}))
                self.sync_chrome()
            return answer.ok
        if kind == "open_display" and target:
            return self.show_display(target)
        if kind in {"open_user_faceplate", "open_user_detail"} and target:
            return self.open_user_faceplate(target, item, view) is not None
        if kind == "write_value" and target and view is not None:
            return bool(self._write(target, action.get("value")).success)
        return False

    def open_user_faceplate(self, name: str, source_item=None,
                            source_view=None):
        """Open the faceplate definition paired with a compact user PVM."""
        source_view = source_view or self.current_display_view()
        root = getattr(source_view, "config_root", None) or self.config_root or getattr(
            getattr(self.deployment, "store", None), "root", None)
        if not name or root is None:
            return None
        from azeo_control_trainer.core.hmi.pvms.user_faceplate import UserFaceplateView
        from azeo_control_trainer.core.hmi.pvms.rendering.items import item_document_data
        from azeo_control_trainer.core.hmi.pvms.class_revisions import item_revision, revision_root
        try:
            root = revision_root(root, item_revision(source_item))
        except ValueError:
            log.exception("Cannot open faceplate: its pinned class files are unavailable")
            return None
        choices = dict(item_document_data(source_item).get("pvm_choices", {}))
        for existing in self.user_faceplates:
            if not existing._disposed and existing.class_name == name \
                    and existing.instance_choices == choices \
                    and existing.config_root == root:
                self._present_faceplate(existing)
                return existing
        source = getattr(getattr(source_view, "engine", None), "_source",
                         self.live_source)
        try:
            popup = UserFaceplateView(
                name, root, self.graphs_provider, source=source,
                choices=choices, theme=self.settings.theme, parent=self,
                action_handler=self.display_event_action,
                write_handler=self._write, write_checker=self._can_write)
        except (KeyError, OSError, ValueError):
            return None
        self._close_unpinned_faceplates()
        self._retain_window(self.user_faceplates, popup)
        self._present_faceplate(popup)
        return popup

    def _open_script_pvm(self, path: str, kind: str) -> bool:
        """Resolve a scripting tag to a visible PVM and open its context."""
        wanted = str(path or "").rstrip("/")
        candidates = (*self.views.values(), self.view)
        for view in candidates:
            if view is None or view.scene() is None:
                continue
            for item in view.scene().items():
                pvm = getattr(item, "pvm", None)
                if pvm is None:
                    continue
                tag = primary_path(pvm.params or {}).rstrip("/")
                if wanted and tag != wanted:
                    continue
                if kind == "detail":
                    self.open_detail(pvm, engine=view.engine)
                else:
                    self.open_faceplate(pvm, engine=view.engine)
                return True
        return False

    def _can_write(self, path: str):
        from azeo_control_trainer.core.hmi.binding import WriteResult
        if not self.settings.write_authority:
            return WriteResult(False, "station is logged on View Only")
        return self.live_source.can_write(path)

    def current_display_view(self):
        """Return the operator's active view in single- or multi-frame layouts."""
        if self.view is not None:
            return self.view
        current = self.history.current
        if current:
            matched = next((view for view in reversed(tuple(self.views.values()))
                            if view.display.name == current), None)
            if matched is not None:
                return matched
        return next(reversed(tuple(self.views.values())), None)

    def current_binding_engine(self):
        """Bind contextual tools even while no display frame is active."""
        view = self.current_display_view()
        return view.engine if view is not None else self._context_engine

    def _write(self, path: str, value):
        before = self.live_source.read(path)
        units = getattr(before, "units", "") or ""
        requested = f"{path}: {before.value} → {value} {units}".rstrip()
        self.command_feedback.setText("Sending · " + requested)
        self.feedback_area.show()
        self._feedback_timer.stop()
        allowed = self._can_write(path)
        result = self.live_source.write(path, value) if allowed.success else allowed
        self.command_feedback.setText(
            ("Accepted · " if result.success else f"Rejected · {result.error} · ") + requested)
        self.command_feedback.setAccessibleName(self.command_feedback.text())
        if result.success:
            self._feedback_timer.start(6000)
            try:
                self.historian.add_event("operator", "Command accepted", path,
                                         {"before": before.value, "requested": value, "actor": self.settings.user},
                                         sim_time=self._history_clock().get("sim_time"))
            except Exception:  # noqa: BLE001 - observation cannot reverse a completed process command
                log.exception("Could not record accepted operator command in history: %s", path)
        if bool(getattr(result, "success", result is not False)) \
                and callable(self.operator_change_recorder):
            try:
                self.operator_change_recorder(path, value)
            except Exception:                              # noqa: BLE001
                # The process write already succeeded. An audit-disk problem
                # must be logged, but cannot turn that success into a second
                # write or a false operator failure.
                log.exception("Could not journal operator write to %s", path)
        return result

    def open_faceplate(self, pvm, engine=None) -> None:
        """Open one PVM's faceplate, or raise the one already open."""
        pvm_cls = registry.get(pvm.block_type, "faceplate", pvm.variant) or registry.get(pvm.block_type, "faceplate")
        if pvm_cls is None:
            log.info("%s has no faceplate class", pvm.block_type)
            return
        self.context_path = primary_path(pvm.params or {})
        engine = engine or self.current_binding_engine()
        key = (pvm_cls.__name__,
               tuple(sorted((str(k), str(v))
                            for k, v in (pvm.params or {}).items())),
               pvm.class_revision, str(getattr(engine, "configuration_root", "")))
        for existing_key, widget in self.faceplates:
            if existing_key == key and getattr(widget, "bound", None):
                self._present_faceplate(widget)
                return
        # Contextual replacement: one unpinned faceplate gives way to
        # the next; pinned ones stay exactly because the operator pinned
        # them. This happens before binding the replacement so duplicate
        # binding names are released first.
        self._close_unpinned_faceplates()
        engine = engine or self.current_binding_engine()
        if engine is None:
            return
        try:
            from azeo_control_trainer.core.hmi.pvms.rendering.renderer import DisplayRenderer
            root = getattr(engine, "configuration_root", None) or self.config_root
            config = DisplayRenderer(engine, {}, root).pvm_config(pvm_cls, pvm.class_revision)
        except ValueError:
            log.exception("Faceplate class configuration is unavailable")
            return
        widget = PvmFaceplateWidget(pvm_cls, pvm.params, engine,
                                    theme=self.settings.theme, config=config, choices=pvm.choices,
                                    parent=self, window_flags=Qt.Tool | Qt.FramelessWindowHint)
        widget._context_kind = "faceplate"
        widget.context_title.pin.setEnabled(True)
        widget.setAttribute(Qt.WA_DeleteOnClose, True)
        widget.set_write_handler(self._write, self._can_write)
        widget.setWindowTitle("%s — %s" % (
            pvm_cls.display_name or pvm.block_type,
            " · ".join(str(v) for v in pvm.params.values())))
        widget.action_requested.connect(
            lambda action, g=pvm, source=engine:
            self.faceplate_action(action, g, source,
                                  origin_kind="faceplate"))
        available = widget.serviceable_actions()
        if resolve_module_faceplate(pvm, engine) is not None:
            available.add("faceplate")
        if resolve_associated_dcc(pvm, engine) is not None:
            available.add("dcc")
        from azeo_control_trainer.core.hmi.pvms.contextual import tuning_paths
        if tuning_paths(pvm_cls, pvm.params):
            available.add("history")
        path = primary_path(pvm.params or {})
        module, _, block = path.partition("/")
        if self.alarm_rollup.primary_control(
                module, block, self.active_display_set):
            available.add("primary")
        available.add("ack")
        if callable(self.control_designer_opener):
            available.add("studio")
        widget.set_available_actions(available)
        self._retain_faceplate(key, widget)
        self._present_faceplate(widget)

    def _retain_faceplate(self, key, widget):
        # A closed detail must leave both the binding engine and theme fanout.
        widget.setAttribute(Qt.WA_DeleteOnClose, True)
        self.faceplates.append((key, widget))
        target_ref = weakref.ref(widget)
        owner_ref = weakref.ref(self)

        def forget_faceplate(_object=None):
            owner, target = owner_ref(), target_ref()
            if owner is not None:
                owner.faceplates[:] = [(item_key, item) for item_key, item in owner.faceplates
                                       if item is not target]

        # A destruction callback must not keep its own native window alive
        # through a Python closure while Qt is tearing that window down.
        widget.destroyed.connect(forget_faceplate)

    def _close_unpinned_faceplates(self):
        retained = []
        for key, widget in self.faceplates:
            if getattr(widget, "_context_kind", "faceplate") == "faceplate" and not widget.pinned:
                widget.close()
            else:
                retained.append((key, widget))
        self.faceplates = retained
        for widget in list(self.user_faceplates):
            if not widget.pinned:
                widget.close()
                self.user_faceplates.remove(widget)

    def _present_faceplate(self, widget):
        # A faceplate is an independent operating window. The tools docking
        # preference must never shrink the process display just to open one.
        if widget.parentWidget() is not self or not widget.isWindow():
            widget.setParent(self, Qt.Tool | Qt.FramelessWindowHint)
        if not getattr(widget, "_operator_positioned", False):
            available = self.screen().availableGeometry().adjusted(8, 8, -8, -8)
            if isinstance(widget, PvmFaceplateWidget):
                widget.fit_to_desktop(available.size())
                widget.adjustSize()
            else:
                widget.resize(widget.size().boundedTo(available.size()))
            position = QCursor.pos() + QPoint(18, 18)
            windows = [other for _key, other in self.faceplates] + self.user_faceplates
            pinned = [other for other in windows
                      if other is not widget and other.pinned and other.isVisible()]
            candidates = [position]
            for other in pinned:
                rect = other.frameGeometry()
                candidates.extend((rect.topRight() + QPoint(12, 0),
                                   rect.topLeft() - QPoint(widget.width() + 12, 0),
                                   rect.bottomLeft() + QPoint(0, 12),
                                   rect.topLeft() - QPoint(0, widget.height() + 12)))
            for candidate in candidates:
                candidate.setX(max(available.left(), min(candidate.x(), available.right() - widget.width() + 1)))
                candidate.setY(max(available.top(), min(candidate.y(), available.bottom() - widget.height() + 1)))

            def overlap(candidate):
                rect = QRect(candidate, widget.size())
                intersections = [rect.intersected(other.frameGeometry()) for other in pinned]
                return (sum(part.width() * part.height() for part in intersections if not part.isEmpty()),
                        (candidate - position).manhattanLength())

            # Clamping a popup to the right edge must not put it directly on
            # top of a pinned window. Prefer free space beside existing ones.
            widget.move(min(candidates, key=overlap))
            widget._operator_positioned = True
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not is_headless():
            widget.show()
            widget.raise_()
            widget.activateWindow()

    def open_equipment_alarms(self, path):
        dialog = self.open_alarm_investigation()
        dialog.summary.set_filter(replace(self.alarm_filter, path_prefix=path.partition("/")[0]))
        return dialog

    def faceplate_action(self, action: str, pvm, engine=None, *,
                         origin_kind: str = "faceplate") -> None:
        """Service an action exposed by a faceplate.

        The faceplate emits an intent and this decides what it means,
        so the console keeps owning its own windows. Only the actions
        the console can really perform are enabled on the row — the
        rest stay greyed with a reason rather than swallowing clicks,
        which is how every one of these behaved before: drawn live and
        connected to nothing.
        """
        if action == "faceplate":
            target = pvm if origin_kind == "detail" \
                else resolve_module_faceplate(pvm, engine)
            if target is not None:
                self.open_faceplate(target, engine=engine)
        elif action == "dcc":
            target = resolve_associated_dcc(pvm, engine)
            if target is not None:
                self.open_faceplate(target, engine=engine)
        elif action == "detail":
            self.open_detail(pvm, engine=engine)
        elif action == "primary":
            path = primary_path(pvm.params or {})
            module, _, block = path.partition("/")
            target = self.alarm_rollup.primary_control(
                module, block, self.active_display_set)
            if target:
                self.show_display(target)
        elif action == "history":
            path = primary_path(pvm.params or {})
            self.open_process_history(path.partition("/")[0])
        elif action == "ack":
            # The footer bell is narrower than the banner ACK: it acts only
            # on rows represented by this module faceplate.
            self.acknowledge_faceplate(pvm)
        elif action == "studio" and callable(self.control_designer_opener):
            path = primary_path(pvm.params or {})
            self.control_designer_opener(path.partition("/")[0])

    def acknowledge_faceplate(self, pvm) -> tuple[str, ...]:
        """Acknowledge only the alarm rows represented by this faceplate."""
        if not self.settings.write_authority:
            return ()
        path = primary_path(pvm.params or {})
        module, separator, remainder = path.partition("/")
        block = remainder.split("/", 1)[0] if separator else ""
        if not module or not block:
            return ()
        rows = self.alarm_state.records(blocks=(f"{module}/{block}",))
        changed = self.alarm_state.acknowledge(row.key for row in rows)
        from azeo_control_trainer.config.logging_config import audit_event
        audit_event("operator_station", "alarms.acknowledge_faceplate",
                    module=module, block=block, count=len(changed),
                    actor=self.settings.user)
        for _key, widget in self.faceplates:
            widget.refresh()
        self._after_alarm_acknowledgement()
        return changed

    def open_tuning_trend(self, pvm, engine=None):
        """Open the unsaved one-second PV/SP/OUT popup."""
        pvm_cls = registry.get(pvm.block_type, "faceplate", pvm.variant) or registry.get(pvm.block_type, "faceplate")
        engine = engine or self.current_binding_engine()
        if pvm_cls is None or engine is None:
            return None
        from azeo_control_trainer.core.hmi.pvms.contextual import TuningTrendWidget, tuning_paths
        paths = tuning_paths(pvm_cls, pvm.params)
        if len(paths) < 2:
            return None
        widget = TuningTrendWidget(
            engine, paths, theme=self.settings.theme)
        self._retain_window(self.tuning_trends, widget)
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not is_headless():
            widget.show()
        return widget

    def open_detail(self, pvm, engine=None) -> None:
        """Open the detail display for one PVM's block.

        Same shape as `open_faceplate`, and deliberately not folded
        into it: Azeo's detail display is a separate window from the
        faceplate and an operator opens it *while* the faceplate is up.
        """
        detail_cls = registry.get(pvm.block_type, "detail")
        if detail_cls is None:
            log.info("%s has no detail display class", pvm.block_type)
            return
        engine = engine or self.current_binding_engine()
        key = (detail_cls.__name__,
               tuple(sorted((str(k), str(v))
                            for k, v in (pvm.params or {}).items())),
               pvm.class_revision, str(getattr(engine, "configuration_root", "")))
        for existing_key, widget in self.faceplates:
            if existing_key == key and getattr(widget, "bound", None):
                widget.raise_()
                widget.activateWindow()
                return
        engine = engine or self.current_binding_engine()
        if engine is None:
            return
        try:
            from azeo_control_trainer.core.hmi.pvms.rendering.renderer import DisplayRenderer
            root = getattr(engine, "configuration_root", None) or self.config_root
            config = DisplayRenderer(engine, {}, root).pvm_config(detail_cls, pvm.class_revision)
        except ValueError:
            log.exception("Detail class configuration is unavailable")
            return
        widget = PvmFaceplateWidget(detail_cls, pvm.params, engine,
                                    theme=self.settings.theme, config=config, choices=pvm.choices,
                                    parent=self, window_flags=Qt.Tool | Qt.FramelessWindowHint)
        widget._context_kind = "detail"
        widget.context_title.pin.setEnabled(bool(
            getattr(self.layout_model, "contextual_pinning", False)))
        widget.set_write_handler(self._write, self._can_write)
        widget.setWindowTitle("%s — %s" % (
            detail_cls.display_name or pvm.block_type,
            " · ".join(str(v) for v in pvm.params.values())))
        widget.action_requested.connect(
            lambda action, g=pvm, source=engine:
            self.faceplate_action(action, g, source,
                                  origin_kind="detail"))
        self._retain_faceplate(key, widget)
        self._present_faceplate(widget)

    # ----------------------------------------------------- navigation
    def _install_navigation_shortcuts(self) -> None:
        """Keyboard routes for operators who should not have to aim at tabs."""
        routes = (
            ("Alt+Left", lambda: self.navigate("back")),
            ("Alt+Right", lambda: self.navigate("forward")),
            ("Alt+Home", lambda: self.navigate("home")),
            ("Alt+Up", lambda: self.navigate("up")),
            ("Ctrl+L", self.open_display_menu),
            ("Ctrl+K", self.open_search),
            ("F5", self.refresh_configuration),
            ("F11", self.toggle_window_mode),
            ("Ctrl+0", lambda: self.view.fit_display() if self.view else None),
            ("Ctrl+=", lambda: self.view.zoom_by(1.15) if self.view else None),
            ("Ctrl+-", lambda: self.view.zoom_by(1 / 1.15) if self.view else None),
            ("Ctrl+Shift+A", self.show_alarm_list),
            ("Ctrl+Shift+H", self.open_process_history),
        )
        self._navigation_shortcuts = []
        for sequence, callback in routes:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.WindowShortcut)
            shortcut.activated.connect(callback)
            self._navigation_shortcuts.append(shortcut)

    def navigate(self, action: str) -> None:
        home = (self.active_display_set.roots[0].display
                if self.active_display_set is not None
                and self.active_display_set.roots
                else (self.choose_display() or (None,))[0])
        current = self.history.current or ""
        parent = self.active_display_set.parent_of(current) \
            if self.active_display_set is not None and current else None
        target = {"back": self.history.back,
                  "forward": self.history.forward,
                  "home": lambda: home,
                  "up": lambda: parent.display if parent is not None else None,
                  }.get(action)
        if target is None:
            return
        display_id = target()
        if display_id:
            self.show_display(display_id, record=(action == "home"))
        self.sync_chrome()

    def choose_display(self) -> tuple:
        """What this station may open — published displays only."""
        published = self.deployment.displays()
        if self.active_display_set is None:
            return published
        allowed = set(self.active_display_set.displays())
        return tuple(name for name in published if name in allowed)

    def display_menu_entries(self) -> tuple[tuple[int, tuple[str, ...]], ...]:
        """Published destinations grouped by the L1-L4 operating levels."""
        groups: dict[int, list[str]] = {}
        for name in self.choose_display():
            level = self.active_display_set.level_of(name) \
                if self.active_display_set is not None else 0
            groups.setdefault(int(level or 0), []).append(name)
        return tuple((level, tuple(names))
                     for level, names in sorted(groups.items()))

    def navigation_summary(self) -> dict:
        """Current branch, useful recent destinations and the full inventory.

        Kept Qt-free so the menu is a view of navigation state rather than a
        second navigation model hidden inside a popup.
        """
        current = self.history.current or ""
        allowed = set(self.choose_display())
        branch = tuple(self.active_display_set.breadcrumb(current)) \
            if self.active_display_set is not None and current else (
                (current,) if current else ())
        recent = []
        for name in reversed(self.history.history()):
            if name == current or name not in allowed or name in recent:
                continue
            recent.append(name)
            if len(recent) == 8:
                break
        return {"branch": branch, "recent": tuple(recent),
                "levels": self.display_menu_entries()}

    def open_display_menu(self, at=None):
        """Open the selector that the painted field and down-arrow promise."""
        entries = self.display_menu_entries()
        from azeo_control_trainer.core.presentation.headless import is_headless
        if is_headless():
            return entries
        from PySide6.QtWidgets import QMenu

        for attribute in ("_display_menu", "_tools_menu"):
            previous = getattr(self, attribute)
            setattr(self, attribute, None)
            if previous is not None:
                previous.close()
                previous.deleteLater()
        self._display_branch_menu = None
        self._display_recent_menu = None
        menu = QMenu(self)
        bind_operator_theme(menu)
        summary = self.navigation_summary()
        labels = {
            0: "Other Displays",
            1: "L1 · High-Level Overview",
            2: "L2 · Primary Operation",
            3: "L3 · Secondary Operation",
            4: "L4 · Support and Diagnostics",
        }
        current = self.history.current or ""
        if summary["branch"]:
            branch_menu = menu.addMenu("Current hierarchy")
            for name in summary["branch"]:
                level = self.active_display_set.level_of(name) \
                    if self.active_display_set is not None else 0
                action = branch_menu.addAction(
                    f"L{level}  {name}" if level else name)
                action.setCheckable(True)
                action.setChecked(name == current)
                action.triggered.connect(
                    lambda _checked=False, target=name:
                    self.show_display(target))
            self._display_branch_menu = branch_menu
        if summary["recent"]:
            recent_menu = menu.addMenu("Recently viewed")
            for name in summary["recent"]:
                action = recent_menu.addAction(name)
                action.triggered.connect(
                    lambda _checked=False, target=name:
                    self.show_display(target))
            self._display_recent_menu = recent_menu
        if summary["branch"] or summary["recent"]:
            menu.addSeparator()
        for level, names in entries:
            section = menu.addMenu(labels.get(level, f"Level {level}"))
            for name in names:
                action = section.addAction(name)
                action.setCheckable(True)
                action.setChecked(name == current)
                action.triggered.connect(
                    lambda _checked=False, target=name:
                    self.show_display(target))
        menu.addSeparator()
        search = menu.addAction("Search displays and control tags…")
        search.setShortcut(QKeySequence("Ctrl+K"))
        search.triggered.connect(self.open_search)
        self._display_menu = menu
        point = at or self.nav.mapToGlobal(
            self.nav.selector_rect().bottomLeft())
        # Let the chrome input handler unwind before the popup takes focus.
        # This keeps mouse and keyboard activation on the same stable path.
        QTimer.singleShot(0, lambda m=menu, p=point: m.popup(p))
        return menu

    def open_search(self):
        """Open the searchable display/control-tag selector."""
        dialog = StationSearchDialog(
            station_search_records(self.deployment, self.choose_display()),
            self.show_display, self)
        return self._present(dialog)

    # ----------------------------------------------------- tools menu
    def tools_menu(self) -> tuple:
        """The runtime menu behind the ⚒ button.

        Azeo's is Print, Reset Layout, Layout Scale, Display Sets and
        Themes. Each carries whether this workstation's assignment makes
        it meaningful; absent services stay disabled with a reason.
        """
        has_layout = self.layout_surface is not None
        set_count = len(self.assignment.display_sets) \
            if self.assignment is not None else 0
        return (
            ("print", "Print", False,
             "No print service is attached to this console"),
            ("reset_layout", "Reset Layout", has_layout,
             "No layout is assigned to this workstation"),
            ("layout_scale", "Layout Scale", has_layout,
             "No layout is assigned to this workstation"),
            ("display_sets", "Display Sets", set_count > 1,
             "Only one display set is assigned to this workstation"),
            ("themes", "Themes", True, ""),
            ("comfortable", "Comfortable toolbar", True, ""),
            ("dock_context", "Dock operating tools", True, ""),
            ("fit", "Fit process display (Ctrl+0)", self.view is not None, "No display open"),
            ("loop_diagnostics", "Loop diagnosis…", True, ""),
            ("alarm_investigation", "Alarm investigation…", True, ""),
            *((("procedures", "Procedures…", True, ""),)
              if self.simulation_service is not None else ()),
            *((("training", "Training sessions…", True, ""),)
              if self.training_session is not None else ()),
        )

    def open_tools_menu(self, at=None):
        """Show the runtime menu, with Themes ▸ Choose Theme live."""
        from azeo_control_trainer.core.presentation.headless import is_headless

        if is_headless():
            return self.tools_menu()
        from PySide6.QtWidgets import QMenu

        for attribute in ("_tools_menu", "_display_menu"):
            previous = getattr(self, attribute)
            setattr(self, attribute, None)
            if previous is not None:
                previous.close()
                previous.deleteLater()
        menu = QMenu(self)
        bind_operator_theme(menu)
        submenus = []
        for key, label, usable, reason in self.tools_menu():
            if key in {"comfortable", "dock_context"}:
                entry = menu.addAction(label)
                entry.setCheckable(True)
                entry.setChecked(getattr(self.settings, key))
                entry.triggered.connect(lambda checked, setting=key: self.set_workspace_option(setting, checked))
                continue
            if key == "fit":
                entry = menu.addAction(label)
                entry.setEnabled(usable)
                entry.triggered.connect(lambda: self.view.fit_display() if self.view else None)
                continue
            training_tools = {"training": self.open_training,
                              "procedures": self.open_procedures,
                              "loop_diagnostics": self.open_loop_diagnostics,
                              "alarm_investigation": self.open_alarm_investigation}
            if key in training_tools:
                entry = menu.addAction(label)
                entry.triggered.connect(lambda _=False, fn=training_tools[key]: fn())
                continue
            if key == "reset_layout":
                entry = menu.addAction(label)
                entry.setEnabled(usable)
                if not usable:
                    entry.setToolTip(reason)
                else:
                    entry.triggered.connect(self.reset_layout)
                continue
            if key == "layout_scale":
                scale_menu = menu.addMenu(label)
                scale_menu.setEnabled(usable)
                if not usable:
                    scale_menu.setToolTip(reason)
                for percent in (75, 90, 100, 110, 125, 150):
                    entry = scale_menu.addAction(f"{percent}%")
                    entry.setCheckable(True)
                    entry.setChecked(self.layout_surface is not None
                                     and self.layout_surface.scale_percent
                                     == percent)
                    entry.triggered.connect(
                        lambda _c=False, p=percent: self.set_layout_scale(p))
                submenus.append(scale_menu)
                continue
            if key == "display_sets":
                sets_menu = menu.addMenu(label)
                sets_menu.setEnabled(usable)
                if not usable:
                    sets_menu.setToolTip(reason)
                for name in (self.assignment.display_sets
                             if self.assignment else ()):
                    entry = sets_menu.addAction(name)
                    entry.setCheckable(True)
                    entry.setChecked(self.active_display_set is not None
                                     and self.active_display_set.name == name)
                    entry.triggered.connect(
                        lambda _c=False, n=name:
                        self.choose_active_display_set(n))
                submenus.append(sets_menu)
                continue
            if key != "themes":
                entry = menu.addAction(label)
                entry.setEnabled(usable)
                if not usable:
                    entry.setToolTip(reason)
                continue
            themes = menu.addMenu("Themes")
            chooser = themes.addMenu("Choose Theme")
            for name, is_current, is_initial in self.theme_choices():
                text = THEME_LABELS.get(name, name) + (" (Station default)" if is_initial else "")
                entry = chooser.addAction(text)
                entry.setData(name)
                entry.setCheckable(True)
                entry.setChecked(is_current)
                entry.triggered.connect(
                    lambda _c=False, n=name: self.choose_theme(n))
            restore = themes.addAction("Restore station default")
            restore.triggered.connect(self.restore_default_theme)
            # Held so the submenu is not garbage-collected out from
            # under the menu that owns it — QMenu.addMenu(title) returns
            # a menu Python drops on the next line otherwise.
            submenus.extend((themes, chooser))
        from azeo_control_trainer.core.presentation.product_help import add_help_action
        menu.addSeparator()
        add_help_action(menu, self)
        self._tools_submenus = tuple(submenus)
        self._tools_menu = menu
        point = at or self.menu.mapToGlobal(self.menu.rect().center())
        # This is deliberately modeless. A nested exec() entered from the
        # custom chrome's mouse handler made the originating click close the
        # menu on some Windows/Qt combinations and froze other station input
        # while the menu was active.
        QTimer.singleShot(0, lambda m=menu, p=point: m.popup(p))
        return menu

    def procedure_session(self):
        if self.simulation_service is None:
            return None
        if getattr(self, "_procedure_session", None) is None:
            from .procedure_supervisor import ProcedureSupervisor
            self._procedure_session = ProcedureSupervisor(self)
        return self._procedure_session

    def open_procedures(self):
        if self.simulation_service is None:
            return None
        try:
            from .procedures import ProceduresDialog
            # One supervisor per station, including when operating tools float.
            dialog = next((tool for tool in self.operator_dialogs
                           if isinstance(tool, ProceduresDialog) and not tool._closed), None)
            if dialog is not None:
                if self.workspace.get("procedures") is dialog:
                    return self.workspace.present("procedures", dialog, "Procedures")
                return self._present(dialog)
            return self._open_workspace_tool("procedures", "Procedures", lambda: ProceduresDialog(self))
        except Exception as error:  # A corrupt/read-only audit store must not escape a Qt action.
            log.exception("Cannot open procedure workspace")
            dialog = StationErrorsDialog((("Procedures", "Cannot open workspace", str(error)),), self)
            dialog.setWindowTitle("Procedure workspace unavailable")
            return self._present(dialog)

    def open_training(self):
        if self.training_session is None:
            return None
        from .training import TrainingDialog
        return self._open_workspace_tool("training", "Training", lambda: TrainingDialog(self))

    def open_loop_diagnostics(self, path=""):
        from .training import LoopDiagnosticsDialog
        dialog = self._open_workspace_tool("diagnosis", "Diagnosis", lambda: LoopDiagnosticsDialog(self, path))
        if path:
            dialog.loop.setCurrentText(path)
        return dialog

    def open_alarm_investigation(self):
        from .training import AlarmInvestigationDialog
        return self._open_workspace_tool("alarms", "Alarms", lambda: AlarmInvestigationDialog(self))

    def _open_workspace_tool(self, key, title, factory):
        if not self.settings.dock_context:
            return self._present(factory())
        dialog = self.workspace.get(key)
        if dialog is None:
            dialog = factory()
            self._retain_window(self.operator_dialogs, dialog)
        return self.workspace.present(key, dialog, title)

    def set_workspace_option(self, key, value):
        if key not in {"comfortable", "dock_context"}:
            return
        self.settings = replace(self.settings, **{key: bool(value)})
        if key == "dock_context" and not value:
            while self.workspace.entries:
                self.workspace.tabs.setCurrentIndex(0)
                self.workspace.detach_current()
        self.menu.settings = self.settings
        self.menu.sync()
        if self._preferences is not None:
            self._preferences.setValue(key, bool(value))

    def reset_layout(self) -> bool:
        """Restore assigned frame occupants and the display-set home."""
        if self.layout_surface is None:
            return False
        self._clear_view_cache()
        for surface in self.layout_surfaces:
            surface.clear_views()
        self.views.clear()
        self.view = None
        self.history = NavigationStack()
        opened = False
        for frame in self.layout_model.frames():
            if self._surface_for_frame(frame.name) is None \
                    or not frame.initial_display:
                continue
            opened = self.show_display(
                frame.initial_display, record=False,
                target_frame=frame.name, coordinate=False) or opened
        home = None
        if self.active_display_set is not None \
                and self.active_display_set.roots:
            home = self.active_display_set.roots[0].display
        else:
            choices = self.deployment.displays()
            home = choices[0] if choices else None
        if home:
            opened = self.show_display(home, record=True) or opened
        self.sync_chrome()
        return opened

    def set_layout_scale(self, percent: int) -> bool:
        if self.layout_surface is None:
            return False
        for surface in self.layout_surfaces:
            surface.set_scale(percent)
        return True

    def choose_active_display_set(self, name: str) -> bool:
        """Select an assigned set, then reset as Azeo requires."""
        if self.layout_store is None or self.assignment is None \
                or name not in self.assignment.display_sets:
            return False
        if not self.layout_store.choose_display_set(
                self.settings.console_id, name):
            return False
        self.assignment = self.layout_store.assignment(
            self.settings.console_id)
        self.active_display_set = self.layout_store.display_set(name)
        return self.reset_layout()

    # ---------------------------------------------------------- theme
    def theme_choices(self) -> tuple:
        """The Themes ▸ Choose Theme list, as Azeo presents it.

        `(name, is_current, is_initial)` per row. The default is
        MARKED rather than hidden — Azeo labels it *Initial*, which
        is what lets an operator get back to the theme the site was
        commissioned with after trying the others.
        """
        return tuple((name, name == self.settings.theme,
                      self.themes.is_initial(name))
                     for name in self.themes.names())

    def choose_theme(self, name: str) -> bool:
        """Switch the running session's theme. False if it did not move.

        The whole console follows at once — chrome, display and every
        open faceplate — because a theme that reached only some of the
        surfaces would put two palettes on screen, which is worse than
        offering no themes at all.
        """
        if self._changing_theme or name not in THEMES or name == self.settings.theme:
            return False
        if not self.apply_theme(name):
            return False
        self._save_theme_preference(name)
        return True

    def _save_theme_preference(self, name):
        from PySide6.QtCore import QSettings
        if self._preferences is None:
            self._theme_message("HMI theme applies to this temporary session only.")
            return False
        try:
            if name is None:
                self._preferences.remove("theme")
            else:
                self._preferences.setValue("theme", name)
            self._preferences.sync()
            if self._preferences.status() != QSettings.NoError:
                raise OSError("Local preference store is unavailable")
        except (OSError, RuntimeError):
            log.exception("Could not save HMI theme preference")
            self._theme_message("HMI theme is active for this session but could not be saved.")
            return False
        self._theme_message("")
        return True

    def _theme_message(self, message):
        self.theme_notice.setText(message)
        self.theme_notice.setVisible(bool(message))

    def restore_default_theme(self, _checked=False):
        if self.settings.theme != self._default_theme and not self.apply_theme(self._default_theme):
            return False
        return self._save_theme_preference(None)

    def _theme_changed(self, name):
        if not self._changing_theme and self.settings.theme != name:
            self.apply_theme(name)

    def apply_theme(self, name: str) -> bool:
        """Apply presentation in place and refuse partial visual success."""
        if self._changing_theme or name not in THEMES:
            return False
        if self.settings.theme == name:
            return False
        previous = self.settings.theme
        self._changing_theme = True
        try:
            from PySide6.QtGui import QColor
            if any(role not in THEMES[name] or not QColor(THEMES[name][role]).isValid() for role in Role):
                raise ValueError("Incomplete HMI theme")
            self._apply_theme_surfaces(name)
        except Exception:
            log.exception("Could not apply HMI theme %s", name)
            try:
                self._apply_theme_surfaces(previous, force=True)
            except Exception:
                log.exception("Could not restore all HMI surfaces after theme failure")
            self.themes.set_theme(previous)
            self._theme_message("HMI theme could not be applied completely. The previous theme was restored where possible; retry or report the display error.")
            return False
        else:
            self.themes.set_theme(name)
            self._theme_message("")
            return True
        finally:
            self._changing_theme = False

    def _apply_theme_surfaces(self, name, *, force=False):
        self.settings = replace(self.settings, theme=name)
        self.palette_roles = THEMES[name]
        apply_widget_theme(self, name, surface=Role.SURFACE_BG, basic=True)
        self.placeholder.setStyleSheet(
            "color: %s; padding: 40px;"
            % self.palette_roles[Role.TEXT_DIM])
        for strip in (self.menu, self.nav, self.banner, self.status):
            strip.settings = self.settings
            # The settings name alone is not the palette.  Custom-painted
            # chrome caches its resolved role colours, so leaving this object
            # behind produced a half-switched station: process graphics took
            # the new theme while the command/navigation bars stayed old.
            strip.palette_ = RolePalette.for_theme(name)
            strip.update()
        for surface in self.layout_surfaces:
            surface.apply_palette(self.palette_roles)
        retained = set((*self.views.values(), *self._view_cache.values(), self.view))
        for view in retained:
            if view is not None:
                view.apply_theme(name)
                if hasattr(view, "_cache_key"):
                    view._cache_key = (*view._cache_key[:3], name)
        self._view_cache = OrderedDict((view._cache_key, view) for view in self._view_cache.values())
        for _key, widget in self.faceplates:
            applier = getattr(widget, "apply_theme", None)
            if applier is not None:
                applier(name)
        for widget in self.user_faceplates:
            widget.apply_theme(name)
        for binding in self.findChildren(WidgetThemeBinding):
            binding.apply_theme(name, force=force)
        self.update()

    # -------------------------------------------------- menu actions
    def open_suite_help(self):
        from azeo_control_trainer.core.presentation.product_help import open_product_help
        return open_product_help(self)

    def menu_action(self, key: str) -> None:
        handler = {"refresh": self.refresh_configuration,
                   "errors": self.show_errors,
                   "search": self.open_search,
                   "tools": lambda: self.open_tools_menu(
                       self.menu.mapToGlobal(
                           self.menu.button_rects()["tools"].bottomLeft())),
                   "alarm_list": self.show_alarm_list,
                   "alarm_filter": self.show_alarm_filter,
                   "history": self.open_process_history,
                   "training": self.open_training,
                   "utilities": self.open_utilities,
                   "mode": self.toggle_window_mode,
                   "logon": self.open_logon,
                   "tag_settings": self.open_tag_settings,
                   "exit": self.close}.get(key)
        if handler is not None:
            handler()

    def refresh_configuration(self) -> dict:
        """Take the waiting updates — the operator's half of the model.

        Rebuilds only the display on screen, and only if it moved: a
        refresh that redrew everything would be the interruption the
        whole design exists to avoid.
        """
        invalidate = getattr(self.deployment, "invalidate_background_catalog", None)
        if invalidate is not None:
            invalidate()
        moved = self.deployment.refresh()
        self._clear_view_cache(moved)
        self.alarm_rollup.refresh_documents(force=True)
        current = self.history.current
        if self.layout_surface is not None:
            visible = [(frame, view.display.name)
                       for frame, view in self.views.items()
                       if not frame.startswith("popup:")]
            for frame, display_id in visible:
                if display_id in moved:
                    self.show_display(display_id, record=False,
                                      target_frame=frame)
        elif current in moved:
            self.show_display(current, record=False)
        self._clear_view_cache(moved)
        self.sync_chrome()
        return moved

    def show_errors(self) -> tuple:
        """Open diagnostics for scripts and unregistered visible PVM classes."""
        views = self.views.values() if self.layout_surface is not None \
            else ((self.view,) if self.view is not None else ())
        unresolved = {
            (view.display.name, "Unregistered PVM", name)
            for view in views for name in view.renderer.unregistered}
        script_errors = {row for row in self.errors if isinstance(row, tuple)}
        self.errors = tuple(sorted(unresolved | script_errors))
        self.sync_chrome()
        self._present(StationErrorsDialog(self.errors, self))
        return self.errors

    def show_alarm_list(self) -> tuple:
        """Open the filtered, actionable Alarm List."""
        records = tuple(record for record in self.alarm_rollup.records()
                        if self.alarm_filter.matches(record))
        if self.settings.dock_context:
            dialog = self.open_alarm_investigation()
            dialog.summary.set_filter(self.alarm_filter)
        else:
            self._present(AlarmListDialog(
                self.alarm_state, self.alarm_filter, self.open_alarm_source, self))
        return tuple(record.key for record in records)

    def open_alarm_source(self, record) -> bool:
        target = self.alarm_rollup.primary_control(
            record.module, record.block, self.active_display_set)
        return bool(target and self.show_display(target))

    def set_alarm_filter(self, alarm_filter: AlarmFilter) -> AlarmFilter:
        self.alarm_filter = alarm_filter
        return alarm_filter

    def show_alarm_filter(self):
        dialog = AlarmFilterDialog(self.alarm_filter, self)
        from azeo_control_trainer.core.presentation.headless import is_headless
        if is_headless():
            self._retain_window(self.operator_dialogs, dialog)
            return dialog
        if dialog.exec():
            self.set_alarm_filter(dialog.alarm_filter())
            self.show_alarm_list()
        return dialog

    def open_process_history(self, module: str = "", paths=None):
        """Open Process History View for a module or explicit point set."""
        from azeo_control_trainer.core.hmi.history.view import ProcessHistoryView
        self._configure_historian()
        with self.live_source.snapshot():
            self.historian.collect()
        selected = [str(path) for path in (paths or ())
                    if str(path or "").strip()]
        view = self._current_history_view()
        if view is None:
            view = ProcessHistoryView(self.historian, "" if selected else module, self)
            self._retain_window(self.process_history_views, view)
        elif module and not selected:
            view.show_module(module)
        if selected:
            known = [path for path in selected if path in self.historian.TAGS]
            if known:
                view.set_pens(known)
            elif module:
                # An old caller may pass a field alias not configured by this
                # workstation. A truthful module chart is better than a blank
                # view whose requested pen never existed.
                view.show_module(module)
        return self._present(view, retain=False)

    def add_tag_to_chart(self, path: str, binding=None):
        """Compatibility entry point for a single displayed numeric binding."""
        return self.add_tags_to_historian([(path, binding)])

    def _current_history_view(self):
        # Delete-on-close is deferred by Qt. Do not reopen a closed window
        # during that interval with its timers already stopped.
        return next((view for view in reversed(self.process_history_views)
                     if not view._closed), None)

    def add_tags_to_historian(self, bindings):
        """Append an entire gesture in one refresh, in a detached chart."""
        from azeo_control_trainer.core.hmi.history import MAX_CHART_PENS
        from azeo_control_trainer.core.hmi.history.view import ProcessHistoryView

        bindings = list(dict(bindings).items())
        if len(bindings) > MAX_CHART_PENS:
            self.command_feedback.setText(
                f"Historian: select up to {MAX_CHART_PENS} tags for one chart.")
            self.feedback_area.show()
            self._feedback_timer.stop()
            return None
        self._configure_historian()
        selected = [path for path, binding in bindings
                    if self._register_chart_point(path, binding)]
        if not selected:
            return None
        with self.live_source.snapshot():
            self.historian.collect()

        view = self._current_history_view()
        if view is not None:
            view._sync_point_identities()
        combined = list(dict.fromkeys((view._pens if view is not None else []) + selected))
        # Preserve the existing chart when it is full. The complete selection
        # goes into another window instead of silently dropping excess tags.
        if view is None or len(combined) > MAX_CHART_PENS:
            view = ProcessHistoryView(self.historian, parent=self)
            self._retain_window(self.process_history_views, view)
            combined = selected
        reveal = any(not view._visible.get(path, True) for path in selected)
        for path in selected:
            view._visible[path] = True
        if view._pens != combined or reveal:
            view.set_pens(combined)
        return self._present(view, retain=False)

    def _register_chart_point(self, path: str, binding) -> bool:
        """Use the same binding and engineering metadata that painted the tag."""
        path = str(path or "").strip()
        result = getattr(binding, "result", None)
        if not path or binding is None \
                or str(getattr(binding, "path", "")) != path:
            return False
        from numbers import Real
        value = getattr(result, "value", None)
        if not isinstance(value, Real) or isinstance(value, bool):
            return False

        from azeo_control_trainer.core.configuration.identities import context_for_path
        context = context_for_path(self.graphs_provider().values(), path)
        existing = self.historian.TAGS.get(path)
        if existing is None or (context and existing.point_id != context["point_id"]):
            lo, hi = 0.0, 100.0
            eu_range = getattr(result, "eu_range", None)
            if isinstance(eu_range, (tuple, list)) and len(eu_range) >= 2:
                try:
                    lo, hi = float(eu_range[0]), float(eu_range[1])
                except (TypeError, ValueError):
                    pass
            # A bare field tag (for example FT-101.PV) has no module scope.
            # Calling the whole tag a module would leave a false module name
            # in Process History View after a Data Link is charted.
            module = path.partition("/")[0] if "/" in path else ""
            terminal = path.rstrip("/").rsplit("/", 1)[-1]
            self.historian.add_point(
                path, label=terminal or path,
                unit=str(getattr(result, "units", "") or ""),
                lo=lo, hi=hi, module=module, **context)
        return True

    def utilities(self) -> tuple:
        return (
            ("history", "Process History View", "Trend configured process points",
             self.open_process_history),
            ("alarms", "Alarm List", "Review and acknowledge station alarms",
             self.show_alarm_list),
            ("search", "Station Search", "Find a display or control tag",
             self.open_search),
            ("errors", "Display Errors", "Review open-display diagnostics",
             self.show_errors),
        )

    def open_utilities(self):
        return self._present(UtilitiesDialog(self.utilities(), self))

    def set_window_mode(self, full_desktop: bool) -> bool:
        """Present the station as a DCS desktop or a maintainable window.

        A dedicated operator seat owns its screen, so normal operation has no
        operating-system title bar or taskbar consuming process-display space.
        F11 still returns to a conventional window for engineering support.
        """
        self.full_desktop = bool(full_desktop)
        if self.full_desktop:
            self.menu.selected.add("mode")
        else:
            self.menu.selected.discard("mode")
        self.menu.update()
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not is_headless():
            if self.full_desktop:
                self.showFullScreen()
            else:
                self.showNormal()
        return self.full_desktop

    def toggle_window_mode(self) -> bool:
        """Toggle ordinary window and full-desktop station modes."""
        return self.set_window_mode(not self.full_desktop)

    def set_local_user(self, user: str, can_operate: bool = True) -> tuple:
        """Switch optional local training identity and write authority."""
        self.settings = replace(
            self.settings, user=str(user or "operator"),
            write_authority=bool(can_operate))
        for strip in (self.menu, self.nav, self.banner, self.status):
            strip.settings = self.settings
            strip.update()
        self.menu.sync()
        from azeo_control_trainer.core.hmi.pvms.rendering.items import StaticItem
        for view in set((*self.views.values(), self.view)):
            if view is None:
                continue
            for item in view.scene().items():
                if isinstance(item, StaticItem) \
                        and item.data.get("kind") == "user_entry":
                    item.refresh_write_permission()
            view.refresh()
        return self.settings.user, self.settings.write_authority

    def open_logon(self):
        dialog = LocalLogonDialog(
            self.settings.user, self.settings.write_authority, self)
        from azeo_control_trainer.core.presentation.headless import is_headless
        if is_headless():
            self._retain_window(self.operator_dialogs, dialog)
            return dialog
        if dialog.exec():
            self.set_local_user(*dialog.identity())
        return dialog

    def _retain_window(self, collection: list, widget):
        """Own a modeless tool until the operator closes it, then forget it.

        Parent ownership alone keeps a closed Qt window alive until the whole
        station exits. Repeated Search, Errors, Alarm List and History calls
        therefore retained every table, timer and signal connection even
        though none remained visible.
        """
        bind_operator_theme(widget)
        if widget not in collection:
            collection.append(widget)
        widget.setAttribute(Qt.WA_DeleteOnClose, True)

        target_ref = weakref.ref(widget)

        def release(_object=None, *, items=collection):
            target = target_ref()
            try:
                items.remove(target)
            except ValueError:
                pass

        widget.destroyed.connect(release)
        return widget

    def _present(self, dialog, *, retain: bool = True):
        if dialog in self.process_history_views and not getattr(dialog, "_operator_positioned", False):
            # Historian windows keep the process graphic at its operating
            # size, regardless of the preference for other docked tools.
            available = self.screen().availableGeometry().adjusted(16, 40, -16, -16)
            dialog.resize(dialog.size().boundedTo(available.size()))
            dialog.move(available.center() - dialog.rect().center())
            dialog._operator_positioned = True
        if retain:
            self._retain_window(self.operator_dialogs, dialog)
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not is_headless():
            if dialog.isMinimized():
                dialog.showNormal()
            else:
                dialog.show()
            dialog.raise_()
            dialog.activateWindow()
        return dialog

    def set_tag_display_mode(self, mode: str) -> bool:
        """Apply a workstation-local ShowTag choice to current/future views."""
        from azeo_control_trainer.core.hmi.pvms.hp.tag import SHOW_TAG_MODES
        if mode not in SHOW_TAG_MODES:
            return False
        self.tag_display_mode = mode
        for view in set((*self.views.values(), self.view)):
            if view is not None:
                view.set_show_tag(mode)
        return True

    def open_tag_settings(self):
        current = self.tag_display_mode or (
            self.view.display.show_tag if self.view is not None else "module")
        dialog = DisplayTagSettingsDialog(current, self)
        from azeo_control_trainer.core.presentation.headless import is_headless
        if is_headless():
            self._retain_window(self.operator_dialogs, dialog)
            return dialog
        if dialog.exec():
            self.set_tag_display_mode(dialog.selected_mode())
        return dialog

    def toggle_tag_settings(self):
        """Compatibility route retained for callers of the former dead toggle."""
        return self.open_tag_settings()

    # --------------------------------------------------------- chrome
    def sync_chrome(self, *, refresh_deployment: bool = True) -> None:
        """Bubbles, navigation state and the display name, in one place."""
        # Release discovery belongs to the station tick/explicit retrieval.
        # Reopening a retained view must not read every history file before
        # the operator can see the next process display.
        if refresh_deployment:
            self._pending_display_count = len(self.deployment.pending())
            self._published_displays = self.deployment.displays()
        pending_count = self._pending_display_count
        error_count = len(self.errors)
        # Preserve the original boolean contract for the common one-item
        # case; larger queues gain a useful count without changing callers
        # that quite reasonably ask whether the indicator is exactly True.
        self.menu.bubbles["refresh"] = (
            pending_count if pending_count > 1 else bool(pending_count))
        self.menu.bubbles["errors"] = (
            error_count if error_count > 1 else bool(error_count))
        self.menu.update()
        self.menu.sync()
        published = self._published_displays
        current = self.history.current or ""
        parent = self.active_display_set.parent_of(current) \
            if self.active_display_set is not None and current else None
        self.nav.can_go["back"] = self.history.can_go_back
        self.nav.can_go["forward"] = self.history.can_go_forward
        self.nav.can_go["home"] = bool(published)
        self.nav.can_go["up"] = parent is not None
        breadcrumb = self.active_display_set.breadcrumb(current) \
            if self.active_display_set is not None and current else (
                [current] if current else [])
        level = self.active_display_set.level_of(current) \
            if self.active_display_set is not None and current else 0
        self.nav.set_context(current, breadcrumb, level)
        self.nav.update()
        rollups = self.alarm_rollup.summary(self.active_display_set)
        for surface in self.layout_surfaces:
            surface.set_navigation(
                self.active_display_set, self.history.current or "", rollups)

    def closeEvent(self, event):  # noqa: N802
        if self._shutdown() is False:
            event.ignore()
            return
        super().closeEvent(event)

    def close(self) -> bool:                             # noqa: A003
        if self._shutdown() is False:
            return False
        return super().close()

    def _shutdown(self):
        if getattr(self, "_closing", False):
            return
        session = getattr(self, "_procedure_session", None)
        if session is not None and not session.close():
            # Keep event processing alive while the PA worker finishes disk
            # publication. A blocking join here froze the entire Qt thread.
            retry = getattr(self, "_procedure_close_retry", None)
            if retry is None:
                retry = self._procedure_close_retry = QTimer(self)
                retry.setSingleShot(True)
                retry.timeout.connect(self.close)
            if not retry.isActive():
                retry.start(100)
            return False
        # Procedure workers must publish their final evidence before the
        # training session and historian are closed underneath them.
        for tool in list(self.operator_dialogs):
            finish = getattr(tool, "shutdown", None)
            if callable(finish) and finish() is False:
                return False
        self._closing = True
        self._tick.stop()
        close_deployment = getattr(self.deployment, "close", None)
        if close_deployment is not None:
            close_deployment()
        if self._preferences is not None and self.workspace.isVisible():
            try:
                self._preferences.setValue("split", self.workspace_split.sizes())
            except (OSError, RuntimeError):
                log.exception("Could not save operator workspace size")
        self._feedback_timer.stop()
        if self.training_session is not None:
            try:
                self.training_session.finish("Operator Station closed")
            except Exception:  # noqa: BLE001
                log.exception("Could not finish training session during station close")
        self._tick.stop()
        self._clear_view_cache()
        for _key, widget in list(self.faceplates):
            widget.close()
        self.faceplates.clear()
        for widget in list(self.tuning_trends):
            widget.close()
        self.tuning_trends.clear()
        for widget in list(self.process_history_views):
            widget.close()
        self.process_history_views.clear()
        for widget in list(self.operator_dialogs):
            widget.close()
        self.operator_dialogs.clear()
        for widget in list(self.user_faceplates):
            widget.close()
        self.user_faceplates.clear()
        while self.workspace.tabs.count():
            self.workspace.close_tab(0)
        for window in list(self.workspace._floating):
            window.close()
        closed = set()
        for view in (*self.views.values(), self.view):
            if view is not None and id(view) not in closed:
                closed.add(id(view))
                view.close()
        self.views.clear()
        for surface in self.layout_surfaces[1:]:
            surface.close()
        if self.training_session is not None:
            self.training_session.history_event_sink = None
        if self.simulation_service is not None:
            self.simulation_service.history_event_sink = None
        self.historian.close()
