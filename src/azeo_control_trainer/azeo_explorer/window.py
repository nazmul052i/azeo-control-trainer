"""The Explorer window — two panes, Windows-NT anatomy, Azeo verbs.

Left tree shows containers; the right contents pane shows the selected
container's children as a details list. The context menu is the
workflow (every operation is also a right-click), status
lives on the rows (● on scan, ▲ loaded but not downloaded — computed
honestly from the executive, never asserted), and the toolbar launches
the applications: Control Designer, Graphics Designer, Operator Station,
the configured Plant Simulator, Diagnostics, and Tag Database. The Plant
Simulator entry owns the existing Local Virtual I/O lifecycle; it never
starts a second standalone process. Double-click always opens an object's
natural editor.

Phase 1 of the engineering-station work: the anatomy and every verb
that maps to something that already exists. The commission lifecycle,
I/O channel properties and the staged download dialog are the next
phases (see docs/new_hmi_studio/IMPLEMENTATION_NOTES.md checkpoint).
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.authoring_style import AUTHORING_CHROME_QSS
from azeo_control_trainer.core.presentation.brand import UI

import getpass
import logging
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont, QKeySequence, \
    QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QFormLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QSplitter,
    QStatusBar, QToolBar, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget,
)

from azeo_control_trainer.core.presentation.headless import is_headless
from azeo_control_trainer.core.presentation.configuration_chrome import ConfigurationComboBox as QComboBox
from azeo_control_trainer.core.presentation.engineering_dialog import polish_dialog

log = logging.getLogger("azeo.explorer")

#: Row-status vocabulary — computed, never asserted.
ON_SCAN = "● on scan"
NOT_DOWNLOADED = "▲ not downloaded"
CHANGED = "●▲ changed since download"
OTHER_STATION = "—"

#: The studio's palette (rev-3 wireframe), reused so the shell and the
#: editors read as one product.
_C = {"page": UI.page, "pane": UI.pane, "chrome": UI.chrome,
      "navy": UI.blue, "lapis": UI.blue, "sel": UI.selection,
      "hover": UI.hover, "bd": UI.border, "tx": UI.text,
      "tx2": UI.text_secondary, "sel_tx": UI.blue}

_QSS = AUTHORING_CHROME_QSS

#: A decommissioned controller reappears under its
#: default naming convention.
_DECOM_NAME = "CTLR-005034"


class _VirtualIoLifecycleWorker(QThread):
    """Run a provider lifecycle call without freezing the engineering shell.

    The worker knows only the provider-neutral ``start``, ``stop`` and
    ``health`` surface.  Simulator construction remains behind the configured
    field-I/O factory and never leaks into Explorer.
    """

    completed = Signal(str, bool, str)
    progress = Signal(str)

    def __init__(self, driver, operation: str, parent=None):
        super().__init__(parent)
        self.driver = driver
        self.operation = operation

    def _health(self) -> dict:
        health = getattr(self.driver, "health", None)
        if callable(health):
            answer = health()
            if isinstance(answer, dict):
                return dict(answer)
        return {
            "running": bool(getattr(self.driver, "running", False)),
            "last_error": str(
                getattr(self.driver, "last_error", "") or ""),
        }

    def _failure(self, fallback: str) -> str:
        health = self._health()
        message = str(health.get("last_error") or "").strip()
        if message:
            return message
        status = getattr(self.driver, "status", None)
        if callable(status):
            message = str(status() or "").strip()
        return message or fallback

    def run(self) -> None:
        try:
            if self.operation == "start":
                self.progress.emit("starting")
                started = getattr(self.driver, "start", None)
                if not callable(started) or started() is False:
                    self.completed.emit(
                        self.operation, False,
                        self._failure("provider did not start"))
                    return
                if not self._health().get("running"):
                    self.completed.emit(
                        self.operation, False,
                        self._failure("provider returned without running"))
                    return
            elif self.operation in {"stop", "restart"}:
                self.progress.emit("stopping")
                stopped = getattr(self.driver, "stop", None)
                if not callable(stopped):
                    self.completed.emit(
                        self.operation, False, "provider cannot be stopped")
                    return
                stopped()
                if self._health().get("running"):
                    # A timed-out scan worker still owns the provider. Starting
                    # over it would create a second plant and a second claimant.
                    self.completed.emit(
                        self.operation, False,
                        self._failure("provider did not finish stopping"))
                    return
                if self.operation == "restart":
                    self.progress.emit("starting")
                    started = getattr(self.driver, "start", None)
                    if not callable(started) or started() is False:
                        self.completed.emit(
                            self.operation, False,
                            self._failure("provider did not restart"))
                        return
                    if not self._health().get("running"):
                        self.completed.emit(
                            self.operation, False,
                            self._failure(
                                "provider returned without running"))
                        return
            else:                                  # pragma: no cover - guard
                raise ValueError(
                    f"unknown Virtual I/O lifecycle operation {self.operation}")
        except Exception as error:                  # noqa: BLE001
            self.completed.emit(self.operation, False, str(error))
            return
        self.completed.emit(self.operation, True, "")


def _valid_node_name(name: str) -> bool:
    """Node naming rules: ≤16 chars, at least one alpha, and only
    alphanumerics plus $, - or _."""
    if not name or len(name) > 16:
        return False
    if not any(ch.isalpha() for ch in name):
        return False
    return all(ch.isalnum() or ch in "$-_" for ch in name)


class _SystemTree(QTreeWidget):
    """The left tree, with the course's one drag gesture: commission
    by dragging the decommissioned node onto the Control Network."""

    def __init__(self, explorer):
        super().__init__()
        self.explorer = explorer
        self.setHeaderHidden(True)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)

    def _payload_at(self, item):
        return item.data(0, Qt.UserRole) if item else None

    def startDrag(self, actions) -> None:           # noqa: N802
        payload = self._payload_at(self.currentItem())
        if payload and payload[0] in (
                "decommissioned_node", "project_decommissioned_controller"):
            super().startDrag(actions)

    def dragEnterEvent(self, event) -> None:        # noqa: N802
        event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:         # noqa: N802
        event.acceptProposedAction()

    def dropEvent(self, event) -> None:             # noqa: N802
        source = self._payload_at(self.currentItem())
        target = self._payload_at(self.itemAt(event.position()
                                              .toPoint()))
        if source and source[0] in (
                "decommissioned_node", "project_decommissioned_controller") \
                and target and target[0] in ("network", "placeholder",
                                             "physical"):
            event.acceptProposedAction()
            if source[0] == "project_decommissioned_controller":
                self.explorer.commission_project_controller(
                    str(source[1].get("node_id") or ""))
            else:
                self.explorer.commission()
        else:
            event.ignore()


def _runtime_graph_name(runtime) -> str:
    graph = getattr(runtime, "graph", None) \
        or getattr(getattr(runtime, "_compiled", None), "graph", None)
    return getattr(graph, "name", "")


class _ContentsList(QTreeWidget):
    """The details pane, and a drag source: a block row dragged onto
    the Graphics Designer canvas drops-to-bind with the studio's own
    MIME payload — 'get the tag from the Explorer', literally."""

    def startDrag(self, actions) -> None:           # noqa: N802
        item = self.currentItem()
        payload = item.data(0, Qt.UserRole) if item else None
        if not payload or payload[0] != "mod_block":
            return
        import json

        from PySide6.QtCore import QMimeData
        from PySide6.QtGui import QDrag

        from azeo_control_trainer.core.hmi.pvms.rendering.chrome import MIME_BLOCK
        path, block_type = payload[1]
        mime = QMimeData()
        mime.setData(MIME_BLOCK, json.dumps(
            {"path": path, "block_type": block_type}).encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)


class ExplorerWindow(QMainWindow):
    """The engineering-station shell: one system, navigable."""

    def __init__(self, store, area: Path, designer, parent=None):
        super().__init__(parent)
        from azeo_control_trainer.core.presentation.app_icon import get_app_icon
        self.setWindowIcon(get_app_icon(application_id="explorer"))
        self.store = store
        self.area = Path(area)
        self.designer_window = designer
        log.info("Explorer opened: area=%s", self.area.resolve())
        self._shortcuts: list[QShortcut] = []
        self._virtual_io_worker: _VirtualIoLifecycleWorker | None = None
        self._virtual_io_transition = ""
        self._virtual_io_operation_error = ""
        # Opening the Operator Station is an end-to-end operation in the
        # training product: a published picture over stopped I/O and offline
        # modules looks valid but can never acquire a live value.  Retain the
        # user's request while the provider starts asynchronously, then put
        # the modules on scan before constructing the console.
        self._pending_operator_station = False
        self._virtual_io_actions: dict[str, object] = {}
        self._virtual_io_launcher_actions: list[object] = []
        self._virtual_io_simulator_dialog = None
        self._simulation_workbench_dialog = None
        self._pa_designer_window = None
        self._project_administrator_dialog = None
        self.setWindowTitle(f"Azeo Explorer — {self.area.name}")
        self.resize(1100, 640)
        from azeo_control_trainer.core.presentation.menu_style import MENU_QSS
        # MENU_QSS rides along so the menubar's dropdowns wear the
        # same commercial styling the context menus do.
        self.setStyleSheet(_QSS + MENU_QSS)

        #: Commission lifecycle state. Older projects without
        #: physical identities retain their commissioned-at-start behaviour;
        #: identity-bearing projects restore the last persisted state.
        nodes, _assignments = self._project_network()
        controller = getattr(self.store, "controller", None)
        controller_name = getattr(controller, "name", "")
        primary_record = next(
            (node for node in nodes
             if str(node.get("name") or "") == controller_name), None)
        primary_commissioning = (
            primary_record.get("commissioning") or {}
            if primary_record else {})
        self._primary_node_id = str(
            primary_record.get("node_id") or "") if primary_record else ""
        self.commissioned = (
            str(primary_commissioning.get("state") or "commissioned").lower()
            != "decommissioned")
        self.io_sensed = self.commissioned
        self.node_description = str(
            primary_record.get("description") or "") \
            if primary_record else ""
        #: Module fingerprints as of the last download — what the
        #: blue triangle diffs against (a difference
        #: between the database and the node raises it; a download
        #: clears it).
        self.downloaded_fp: dict = {}
        #: Remote controller nodes (monitor/operate over OPC UA).
        #: Runtime-only, like the keylock.
        self.remote_nodes: list = []
        #: A workstation controls ONLY the areas assigned
        #: to its Alarms & Events subsystem. The trainer boots with
        #: its area assigned (the workshop's own end state); removing
        #: it gates the Operator Station — the one real consequence
        #: that makes the assignment honest.
        self.assigned_areas: set = {self.area.name}
        self._identify_ticks = 0
        self._identify_timer = QTimer(self)
        self._identify_timer.timeout.connect(self._identify_tick)

        self._build_menus()
        self._build_toolbar()

        self.split = QSplitter()
        split = self.split
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search the system  (Ctrl+F)")
        self.search.setStyleSheet(
            f"QLineEdit {{ border: none; border-bottom: 1px solid "
            f"{_C['bd']}; padding: 5px 8px; font-size: 9pt;"
            f"background: {_C['pane']}; }}")
        self.search.textChanged.connect(self._filter_tree)
        left_lay.addWidget(self.search)
        self.tree = _SystemTree(self)
        self.tree.setIconSize(QSize(18, 18))
        self.tree.setMinimumWidth(260)
        self.tree.itemClicked.connect(self._show_contents)
        self.tree.itemDoubleClicked.connect(self._activate)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        left_lay.addWidget(self.tree, 1)
        split.addWidget(left)

        self.contents = _ContentsList()
        self.contents.setDragEnabled(True)
        self.contents.setRootIsDecorated(False)
        self.contents.setIconSize(QSize(16, 16))
        self.contents.setHeaderLabels(
            ["Name", "Type", "Status", "Description"])
        self.contents.setColumnWidth(0, 200)
        self.contents.setColumnWidth(1, 150)
        self.contents.setColumnWidth(2, 130)
        self.contents.itemDoubleClicked.connect(self._activate)
        self.contents.itemClicked.connect(self._sync_tree_selection)
        self.contents.setContextMenuPolicy(Qt.CustomContextMenu)
        self.contents.customContextMenuRequested.connect(
            self._contents_menu)
        split.addWidget(self.contents)
        split.setSizes([300, 800])
        self._shortcuts.append(QShortcut(
            QKeySequence.Find, self,
            activated=lambda: (self.search.setFocus(),
                               self.search.selectAll())))

        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)
        self.setCentralWidget(holder)

        # Status bar with FIELDS, not one string — user | area |
        # controller identity | DST gauge, the gauge colouring itself.
        status = QStatusBar()
        self.setStatusBar(status)

        def field(text=""):
            label = QLabel(text)
            label.setStyleSheet(f"color: {UI.text_secondary}; font-size: 9pt;"
                                "padding: 0 10px; border: none;")
            return label

        self.user_field = field(f"User: {getpass.getuser()}")
        status.addWidget(self.user_field)
        self.area_field = field(f"Area: {self.area.name}")
        status.addWidget(self.area_field)
        self.node_field = field()
        status.addWidget(self.node_field, 1)
        self.dst_field = field()
        status.addPermanentWidget(self.dst_field)
        self._sync_status()

        self._shortcuts.extend((
            QShortcut(QKeySequence(Qt.Key_F5), self,
                      activated=self.refresh),
            QShortcut(QKeySequence(Qt.Key_F1), self,
                      activated=self.show_help),
            QShortcut(QKeySequence(Qt.Key_Backspace), self,
                      activated=self.navigate_up),
        ))
        self._restore_settings()
        self.refresh()
        # Never open onto an empty pane: start on the system root.
        root_item = self.tree.topLevelItem(0)
        if root_item is not None:
            self.tree.setCurrentItem(root_item)
            self._show_contents(root_item)

    # ---------------------------------------------------------- chrome
    def _build_menus(self) -> None:
        """The menubar's dropdowns are studio menus — same styling,
        same auto-attached vector icons as every context menu."""
        from azeo_control_trainer.core.presentation.menu_style import studio_menu

        bar = self.menuBar()

        def dropdown(title: str):
            menu = studio_menu()
            menu.setTitle(title)
            bar.addMenu(menu)
            return menu

        file_menu = dropdown("&File")
        file_menu.addAction("Project Administrator…",
                            self.open_project_administrator)
        file_menu.addSeparator()
        file_menu.addAction("New Area…",
                            lambda: self.new_area())
        file_menu.addSeparator()
        file_menu.addAction("Exit", self.close)

        view_menu = dropdown("&View")
        view_menu.addAction("Refresh\tF5", self.refresh)
        view_menu.addSeparator()
        view_menu.addAction("Expand all", self.tree_expand)
        view_menu.addAction("Collapse all", self.tree_collapse)

        object_menu = dropdown("&Object")
        object_menu.addAction("Open", self._open_selected)
        object_menu.addAction("Total Download…",
                              lambda: self._total_download())

        apps = dropdown("&Applications")
        from azeo_control_trainer.core.presentation.installed_apps import action_available
        for label, handler in (("Control Designer", self.open_control_designer),
                               ("Graphics Designer", self.open_graphics_designer),
                               ("PA Designer", self.open_pa_designer),
                               ("Operator Station", self.open_operator_station)):
            if action_available(handler):
                apps.addAction(label, handler)
        if self._project_virtual_io():
            simulator = apps.addAction(
                "Start Plant Simulator", self.open_or_start_virtual_io)
            self._virtual_io_launcher_actions.append(simulator)
            apps.addAction("Simulation Workbench…",
                           self.open_simulation_workbench)
            apps.addAction("Virtual I/O Simulator…",
                           self.open_virtual_io_simulator)
        apps.addSeparator()
        apps.addAction("Diagnose…", self.open_diagnostics)
        apps.addAction("Tag Database\tCtrl+T",
                       self.open_tag_database)

        tools = dropdown("&Tools")
        tools.addAction("Controller Status…",
                        self.open_controller_status)
        tools.addAction("Discover Controllers…",
                        self.open_controller_discovery)
        tools.addAction("EIOC OPC UA Client…",
                        self.open_eioc_config)
        virtual_io_menu = studio_menu(parent=tools)
        virtual_io_menu.setTitle("Virtual I/O")
        tools.addMenu(virtual_io_menu)
        virtual_io_menu.addAction(
            "Open Signal Simulator…", self.open_virtual_io_simulator)
        virtual_io_menu.addAction(
            "Open Simulation Workbench…", self.open_simulation_workbench)
        virtual_io_menu.addSeparator()
        self._virtual_io_actions = self._add_virtual_io_lifecycle_actions(
            virtual_io_menu)
        virtual_io_menu.aboutToShow.connect(
            lambda: self._sync_virtual_io_actions(
                self._virtual_io_actions))
        tools.addAction("PVM Configuration Designer…",
                        lambda: self.open_pvm_config())
        tools.addSeparator()
        tools.addAction("Auto-sense I/O", self.auto_sense)

        help_menu = dropdown("&Help")
        from azeo_control_trainer.core.presentation.product_help import add_help_action
        add_help_action(help_menu, self)
        help_menu.addAction("Azeo Explorer Help\tF1", self.show_help)
        help_menu.addSeparator()
        help_menu.addAction("About Azeo Explorer…", self._about)

    def tree_expand(self) -> None:
        self.tree.expandAll()

    def tree_collapse(self) -> None:
        self.tree.collapseAll()

    def _build_toolbar(self) -> None:
        """Vector icons from the ribbon's own vocabulary — the same
        set the menus wear — with tooltips. Emoji is not an icon."""
        from azeo_control_trainer.core.presentation.studio_icons import draw_icon as _draw_icon

        toolbar = QToolBar("Applications")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        toolbar.setIconSize(QSize(18, 18))
        application_actions = [
                ("exec_edit", "Control Designer",
                 "Open the module editor", self.open_control_designer),
                ("faceplate", "Graphics Designer",
                 "Open the PVM display studio",
                 self.open_graphics_designer),
                ("procedure", "PA Designer",
                 "Build, map and validate project procedures",
                 self.open_pa_designer),
                ("simulator", "Operator Station",
                 "Open the Azeo Operator Station console — published PVM "
                 "displays only",
                 self.open_operator_station),
                ("named_sets", "Project Administrator",
                 "Create, copy, back up, restore, and select projects",
                 self.open_project_administrator),
        ]
        if self._project_virtual_io():
            application_actions.append(
                ("connect", "Start Plant Simulator",
                 "Start the configured embedded plant through Local "
                 "Virtual I/O", self.open_or_start_virtual_io))
            application_actions.append(
                ("simulator", "Simulation Workbench",
                 "System-level controller and process simulation checkout",
                 self.open_simulation_workbench))
        application_actions.extend((
                ("diagnostics", "Diagnostics",
                 "Controller diagnostics", self.open_diagnostics),
                ("named_sets", "Tag Database",
                 "Browse the derived tag database",
                 self.open_tag_database),
                (None, None, None, None),
                ("download", "Total Download",
                 "Download the entire configuration (staged)",
                 lambda: self._total_download()),
                ("undo", "Refresh",
                 "Refresh and recompute every status mark (F5)",
                 self.refresh),
                ("open", "Up",
                 "Up one level (Backspace)", self.navigate_up),
        ))
        for icon_name, label, tip, handler in application_actions:
            from azeo_control_trainer.core.presentation.installed_apps import action_available
            if handler and not action_available(handler):
                continue
            if icon_name is None:
                toolbar.addSeparator()
                continue
            action = toolbar.addAction(
                _draw_icon(icon_name, 18, _C["navy"]), label,
                handler)
            action.setToolTip(tip)
            if handler == self.open_or_start_virtual_io:
                self._virtual_io_launcher_actions.append(action)
        self.addToolBar(toolbar)
        self._sync_virtual_io_actions()

    def _sync_status(self) -> None:
        controller = getattr(self.store, "controller", None)
        if controller is None:
            return
        name = getattr(controller, "name", "")
        model = getattr(getattr(controller, "model", None), "name",
                        "")
        locked = getattr(controller, "keylock", False)
        self.node_field.setText(
            f"{name} · {model}"
            + ("  ·  🔒 keylock LOCKED" if locked else "")
            + ("" if self.commissioned else "  ·  DECOMMISSIONED"))
        try:
            usage = controller.dst_usage(self.store)
            limit = controller.dst_limit
            over = usage > limit
            self.dst_field.setText(f"DSTs {usage} / {limit}")
            self.dst_field.setStyleSheet(
                "font-size: 9pt; padding: 0 10px; border: none;"
                + ("color: #B42318; font-weight: 600;"
                   if over or locked else f"color: {UI.text_secondary};"))
        except Exception:                           # noqa: BLE001
            pass

    # ------------------------------------------------------------ tree
    def _node(self, parent, text, payload=None, bold=False):
        icon = self._row_icon(*payload) if payload else None
        if icon is not None:
            prefix, separator, label = text.partition(" ")
            if separator and prefix not in ("●", "▲", "●▲") and not any(
                    character.isalnum() for character in prefix):
                text = label
        item = QTreeWidgetItem(parent, [text])
        if icon is not None:
            item.setIcon(0, icon)
        if payload is not None:
            item.setData(0, Qt.UserRole, payload)
        if bold:
            # A new item's unresolved QFont has pointSize == -1 on Windows.
            # Resolve it through the owning view before storing emphasis.
            font = QFont(self.tree.font())
            font.setBold(True)
            item.setFont(0, font)
        return item

    def _online_names(self) -> set:
        try:
            executive = self.designer_window.designer \
                .controller_executive()
            return {_runtime_graph_name(rt)
                    for rt in executive.online_runtimes()}
        except Exception:                           # noqa: BLE001
            return set()

    def _areas_on_disk(self) -> list:
        root = self.area.parent
        out = []
        try:
            for candidate in sorted(root.iterdir()):
                if candidate.is_dir() \
                        and (candidate / "_project.json").exists():
                    out.append(candidate)
        except OSError:
            pass
        if self.area not in out:
            out.insert(0, self.area)
        return out

    def _area_modules(self, area: Path) -> list:
        """(name, path, kind) from the area's folders, disk-honest."""
        out = []
        for folder, kind in (("control", "Control Module"),
                             ("sequence", "Sequence Module"),
                             ("equipment", "Equipment Module")):
            directory = area / folder
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.json")):
                out.append((path.stem, path, kind))
        return out

    @staticmethod
    def _area_units(area: Path) -> list:
        """Return the units declared by one project directory.

        Projects in the repository use both supported document shapes:
        units may live at the project root or inside configured area
        records. Explorer normalises that storage detail so the engineering
        hierarchy is always Area -> Unit -> Module.
        """
        import json

        try:
            project = json.loads(
                (area / "_project.json").read_text(encoding="utf-8"))
        except Exception:                           # noqa: BLE001
            return []
        units = list(project.get("units", []) or [])
        for entry in project.get("areas", ()) or ():
            if isinstance(entry, dict):
                units.extend(entry.get("units", []) or [])
        return units

    @staticmethod
    def _unit_module_names(unit) -> list[str]:
        """Normalise a unit's module membership without inventing links."""
        if not isinstance(unit, dict):
            return []
        modules = unit.get("modules", ()) or ()
        if isinstance(modules, str):
            modules = modules.replace(",", " ").split()
        if not isinstance(modules, (list, tuple)):
            return []
        return [str(name).strip() for name in modules
                if str(name).strip()]

    # ------------------------------------ navigation + view state
    @staticmethod
    def _text_key(item) -> str:
        """A stable identity for a row across refreshes: its text
        path with the dynamic marks stripped."""
        parts = []
        while item is not None:
            text = item.text(0)
            for mark in "●▲⛔⚠💡":
                text = text.replace(mark, "")
            parts.append(" ".join(text.split()))
            item = item.parent()
        return " / ".join(reversed(parts))

    def _capture_view(self) -> dict:
        expanded, selected = set(), None

        def walk(item):
            if item.isExpanded():
                expanded.add(self._text_key(item))
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))
        if self.tree.currentItem() is not None:
            selected = self._text_key(self.tree.currentItem())
        return {"expanded": expanded, "selected": selected,
                "scroll": self.tree.verticalScrollBar().value()}

    def _restore_view(self, view: dict) -> None:
        selected_item = None

        def walk(item):
            nonlocal selected_item
            key = self._text_key(item)
            item.setExpanded(key in view["expanded"])
            if key == view["selected"]:
                selected_item = item
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))
        if selected_item is not None:
            self.tree.setCurrentItem(selected_item)
            self._show_contents(selected_item)
        self.tree.verticalScrollBar().setValue(view["scroll"])
        self._filter_tree(self.search.text())

    def _filter_tree(self, text: str) -> None:
        needle = text.strip().lower()

        def walk(item) -> bool:
            text = item.text(0)
            payload = self._payload_of(item)
            if payload and payload[0] == "lib_procedure_block":
                text += f" PA {item.toolTip(0)} {item.parent().text(0)}"
            keep = needle in text.lower() if needle else True
            child_kept = False
            for i in range(item.childCount()):
                child_kept |= walk(item.child(i))
            item.setHidden(bool(needle) and not (keep or child_kept))
            if needle and child_kept:
                item.setExpanded(True)
            return keep or child_kept

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))

    def _tree_item_for_key(self, key: str):
        found = None

        def walk(item):
            nonlocal found
            if found is None and self._text_key(item) == key:
                found = item
                return
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))
        return found

    def _sync_tree_selection(self, row, _col=0) -> None:
        """Clicking a contents row highlights its node on the left —
        the two panes never disagree about where you are."""
        key = row.data(0, Qt.UserRole + 1)
        if not key:
            return
        item = self._tree_item_for_key(key)
        if item is not None:
            self.tree.setCurrentItem(item)

    def navigate_up(self) -> None:
        """Backspace / the Up button: one level toward the root."""
        current = self.tree.currentItem()
        parent = current.parent() if current is not None else None
        if parent is not None:
            self.tree.setCurrentItem(parent)
            self._show_contents(parent)

    def _restore_settings(self) -> None:
        from PySide6.QtCore import QSettings
        settings = QSettings()
        geometry = settings.value("explorer/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        sizes = settings.value("explorer/splitter")
        if sizes:
            try:
                self.split.setSizes([int(s) for s in sizes])
            except (TypeError, ValueError):
                pass
        widths = settings.value("explorer/columns")
        if widths:
            try:
                for i, width in enumerate(widths):
                    self.contents.setColumnWidth(i, int(width))
            except (TypeError, ValueError):
                pass

    def closeEvent(self, event) -> None:            # noqa: N802
        worker = self._virtual_io_worker
        if worker is not None and worker.isRunning() \
                and not worker.wait(1000):
            # Destroying a live QThread is a native crash. Keep the Explorer
            # open and let the provider operation finish honestly.
            event.ignore()
            self.statusBar().showMessage(
                "Virtual I/O operation is still finishing…", 3000)
            return
        editor = self._pa_designer_window
        if editor is not None and not editor.close():
            event.ignore()
            return
        from PySide6.QtCore import QSettings
        settings = QSettings()
        settings.setValue("explorer/geometry", self.saveGeometry())
        settings.setValue("explorer/splitter",
                          [str(s) for s in self.split.sizes()])
        settings.setValue(
            "explorer/columns",
            [str(self.contents.columnWidth(i)) for i in range(4)])
        super().closeEvent(event)

    def _module_status(self, name: str, online: set,
                       fingerprints: dict) -> str:
        """The node states: on scan and matching, on scan but
        changed since download (the blue triangle over a running
        module), or not downloaded at all."""
        if name not in online:
            return NOT_DOWNLOADED
        current = fingerprints.get(name)
        recorded = self.downloaded_fp.get(name)
        if recorded is None and current is not None:
            # Downloaded outside the Explorer (Control Designer's own
            # button) — adopt what runtime holds as the baseline.
            self.downloaded_fp[name] = current
            recorded = current
        if current is not None and recorded != current:
            return CHANGED
        return ON_SCAN

    def _open_fingerprints(self) -> dict:
        from .download import graph_fingerprint
        out = {}
        try:
            for graph in self.designer_window.designer.open_graphs():
                out[graph.name] = graph_fingerprint(graph)
        except Exception:                           # noqa: BLE001
            pass
        return out

    def refresh(self) -> None:
        view = self._capture_view()
        self.tree.clear()
        node = self._node
        online = self._online_names()
        fingerprints = self._open_fingerprints()
        system = node(self.tree, "🏭 AZEO_SYSTEM", bold=True,
                      payload=("system", None))

        library = node(system, "📚 Library", payload=("library", None))
        blocks = node(library, "▤ Function Blocks",
                      payload=("lib_blocks", None))
        try:
            from azeo_control_trainer.core.presentation.function_block_icons import (
                make_block_qicon,
            )
            from azeo_control_trainer.core.strategy.model.block_registry import BlockRegistry
            for category, classes in sorted(
                    BlockRegistry().by_category().items(),
                    key=lambda pair: pair[0].name):
                cat_item = node(blocks, category.name.title(),
                                payload=("lib_category", category))
                for cls in sorted(classes,
                                  key=lambda c: c.block_type):
                    leaf = node(cat_item, cls.block_type,
                                payload=("lib_block", cls.block_type))
                    # The designer's own vector icon — one icon
                    # vocabulary across palette and Explorer.
                    leaf.setIcon(0, make_block_qicon(
                        cls.block_type, category, 18))
        except Exception:                           # noqa: BLE001
            pass
        from azeo_control_trainer.core.procedures.library import block_library

        procedures = node(library, "Procedure Blocks (PA)",
                          payload=("lib_procedure_blocks", None))
        categories = {}
        for block in block_library():
            if block.category not in categories:
                categories[block.category] = node(
                    procedures, block.category,
                    payload=("lib_procedure_category", block.category))
            leaf = node(categories[block.category], block.label,
                        payload=("lib_procedure_block", block.block_id))
            leaf.setToolTip(0, f"{block.description}\n{block.block_id} · {block.version}")
        pvms = node(library, "◈ PVM Classes",
                    payload=("lib_pvms", None))
        try:
            from azeo_control_trainer.core.hmi.pvms import registry as pvm_registry
            for key, cls in sorted(pvm_registry.all_classes().items()):
                label = cls.__name__
                if key[2]:
                    label += f" · {key[2]}"
                leaf = node(pvms, label, payload=("lib_pvm", key))
                leaf.setIcon(0, self._pvm_icon(key, cls))
        except Exception:                           # noqa: BLE001
            pass

        strategies = node(system, "🗂 Control Strategies",
                          payload=("strategies", None))
        for area in self._areas_on_disk():
            live = area == self.area
            label = f"🗀 {area.name.upper()}"
            if live:
                label += "  (this station)"
            area_item = node(strategies, label,
                             payload=("area", area), bold=live)

            modules = self._area_modules(area)
            units = self._area_units(area)

            def add_module(parent, module) -> None:
                name, path, kind = module
                marker = ""
                # An equipment module is a descriptor, not a strategy
                # — it never downloads, so it never wears a download
                # marker (a status it can't clear would be a lie).
                if live and kind != "Equipment Module":
                    status = self._module_status(name, online,
                                                 fingerprints)
                    marker = {ON_SCAN: "  ●", CHANGED: "  ●▲",
                              NOT_DOWNLOADED: "  ▲"}.get(status, "")
                node(parent, f"⛭ {name}{marker}",
                     payload=("module", (name, path, kind, live)))

            if units:
                by_name = {module[0]: module for module in modules}
                assigned: set[Path] = set()
                group_labels = {
                    "Control Module": "Control Modules",
                    "Equipment Module": "Equipment Modules",
                    "Sequence Module": "Sequence Modules",
                }
                for unit in units:
                    unit_name = (str(unit.get("name") or "Unnamed Unit")
                                 if isinstance(unit, dict) else str(unit))
                    unit_item = node(area_item, unit_name,
                                     payload=("unit", unit))
                    members = []
                    for module_name in self._unit_module_names(unit):
                        module = by_name.get(module_name)
                        if module is None or module[1] in assigned:
                            continue
                        members.append(module)
                        assigned.add(module[1])
                    for module_kind in (
                            "Control Module", "Equipment Module",
                            "Sequence Module"):
                        grouped = [module for module in members
                                   if module[2] == module_kind]
                        if not grouped:
                            continue
                        group = node(
                            unit_item,
                            f"{group_labels[module_kind]} ({len(grouped)})",
                            payload=("module_group", module_kind))
                        for module in grouped:
                            add_module(group, module)
                    unit_item.setExpanded(live)

                unassigned = [module for module in modules
                              if module[1] not in assigned]
                if unassigned:
                    # Never hide an engineering object because unit metadata
                    # is stale. The explicit bucket turns the configuration
                    # defect into something an engineer can see and repair.
                    unassigned_item = node(
                        area_item,
                        f"Unassigned Modules ({len(unassigned)})",
                        payload=("unassigned_modules", area))
                    for module_kind in (
                            "Control Module", "Equipment Module",
                            "Sequence Module"):
                        grouped = [module for module in unassigned
                                   if module[2] == module_kind]
                        if not grouped:
                            continue
                        group = node(
                            unassigned_item,
                            f"{group_labels[module_kind]} ({len(grouped)})",
                            payload=("module_group", module_kind))
                        for module in grouped:
                            add_module(group, module)
            else:
                # Older projects without unit declarations retain their
                # disk-honest flat view until the engineer assigns units.
                for module in modules:
                    add_module(area_item, module)
            area_item.setExpanded(live)

        physical = node(system, "🖧 Physical Network",
                        payload=("physical", None))
        network = node(physical, "🕸 Control Network",
                       payload=("network", None))
        project_controllers, project_assignments = self._project_network()

        def modules_for(node_id: str) -> list[tuple[str, Path, str]]:
            assigned = []
            for module_name, module_path, module_kind in self._area_modules(
                    self.area):
                try:
                    relative = str(module_path.relative_to(self.area))
                except ValueError:
                    relative = str(module_path)
                key = relative.replace("\\", "/").lower()
                if project_assignments.get(key) == node_id:
                    assigned.append((module_name, module_path, module_kind))
            return assigned

        controller = getattr(self.store, "controller", None)
        primary_name = getattr(controller, "name", "")
        primary_record = next(
            (record for record in project_controllers
             if str(record.get("name") or "") == primary_name), None)
        lamp = "● " if self._identify_ticks % 2 == 1 else ""
        if controller is not None and self.commissioned:
            name = getattr(controller, "name", "PK-CTLR-1")
            model = getattr(getattr(controller, "model", None),
                            "name", "") or str(
                getattr(controller, "model", ""))
            ctl_item = node(network, f"{lamp}{name}  ·  {model}",
                            payload=("controller", None), bold=True)
            assigned = node(ctl_item, "Assigned Modules",
                            payload=("assigned_modules", None))
            primary_modules = (modules_for(str(primary_record.get("node_id")))
                               if primary_record and project_assignments
                               else [(module_name, Path(), "")
                                     for module_name in sorted(online)])
            for module_name, _module_path, _module_kind in primary_modules:
                mark = "  ●" if module_name in online else "  ▲"
                node(assigned, f"⛭ {module_name}{mark}",
                     payload=("assigned_module", module_name))
            eioc = getattr(self.store, "eioc", None)
            io_label = ("Native I/O" if eioc is not None else "Assigned I/O")
            if not self.io_sensed:
                io_label += "  (not sensed)"
            io_item = node(
                ctl_item, io_label,
                payload=(("native_io" if eioc is not None
                          else "assigned_io"), None))
            if self.io_sensed:
                cards = self._io_cards()
                for card in cards:
                    card_item = node(
                        io_item,
                        f"🂠 {card.label} — {card.channel_type}s",
                        payload=("io_card", card.label))
                    for channel in card.channels:
                        marks = ""
                        if not channel.enabled:
                            marks += "  ⛔"
                        elif not channel.referenced:
                            marks += "  ⚠"
                        node(card_item,
                             f"⚡ {channel.label}  {channel.device_tag}"
                             f"{marks}",
                             payload=("channel", channel))
                if not cards and eioc is None:
                    # No channel-shaped transport attached — fall back
                    # to the tag database's flat DST list.
                    tagdb = getattr(self.store, "tagdb", None)
                    try:
                        for tag, blocks_for in sorted(
                                tagdb.field_tags().items()
                                if tagdb else ()):
                            kinds = ", ".join(sorted(
                                str(b) for b in blocks_for)) \
                                if isinstance(blocks_for,
                                              (list, tuple, set)) \
                                else str(blocks_for)
                            node(io_item, f"⚡ {tag}",
                                 payload=("field_tag", (tag, kinds)))
                    except Exception:               # noqa: BLE001
                        pass
                elif not cards:
                    hint = node(io_item, "(no native I/O configured)")
                    hint.setForeground(0, Qt.gray)
            ctl_item.setExpanded(True)
        elif controller is not None:
            # A placeholder remains in the Control
            # Network after decommissioning — the configuration
            # survives the hardware.
            name = getattr(controller, "name", "PK-CTLR-1")
            node(network, f"⬚ {name}  (placeholder)",
                 payload=("placeholder", None))

        decommissioned_project_controllers: list[dict] = []
        for record in project_controllers:
            name = str(record.get("name") or "Controller")
            if name == primary_name:
                continue
            node_id = str(record.get("node_id") or "")
            model = str(record.get("model") or "PK100").upper()
            commissioning = record.get("commissioning") or {}
            state = str(commissioning.get("state") or "configured").lower()
            if state not in ("configured", "commissioned", "decommissioned"):
                state = "configured"
            if state == "decommissioned":
                decommissioned_project_controllers.append(record)
            kind = ("project_controller" if state == "commissioned"
                    else "configured_controller")
            suffix = ("commissioned" if state == "commissioned"
                      else "placeholder" if state == "decommissioned"
                      else "configured")
            extra = node(
                network, f"🎛 {name}  ·  {model}  ·  {suffix}",
                payload=(kind, record), bold=True)
            assigned = node(
                extra, "Assigned Modules",
                payload=("configured_assigned_modules", node_id))
            module_records = modules_for(node_id)
            for module_name, module_path, module_kind in module_records:
                node(
                    assigned, f"⛭ {module_name}  ·  configured",
                    payload=("configured_module",
                             (module_name, module_path, module_kind)))
            if not module_records:
                hint = node(assigned, "(no modules assigned)")
                hint.setForeground(0, Qt.gray)
            native = node(
                extra, "Native I/O",
                payload=("configured_native_io", node_id))
            hint = node(native, "(no native I/O configured)")
            hint.setForeground(0, Qt.gray)
            extra.setExpanded(True)

        eioc = getattr(self.store, "eioc", None)
        if eioc is not None:
            eioc_item = node(
                network, f"🌐 {eioc.name}  ·  EIOC",
                payload=("eioc", None), bold=True)
            node(eioc_item, f"Endpoint  ·  {eioc.endpoint}",
                 payload=("eioc_endpoint", eioc.endpoint))
            signals_item = node(
                eioc_item,
                f"OPC UA Signals  ({eioc.signal_count} of "
                f"{eioc.signal_limit})",
                payload=("eioc_signals", None))
            groups: dict[str, dict[str, list[tuple[str, dict]]]] = {}
            for tag, spec in eioc.signals.items():
                unit = str(spec.get("plant_unit") or "Ungrouped")
                kind = str(spec.get("kind") or "Signal").upper()
                groups.setdefault(unit, {}).setdefault(kind, []).append(
                    (tag, spec))
            for unit, kinds in sorted(groups.items()):
                unit_count = sum(len(entries) for entries in kinds.values())
                unit_item = node(
                    signals_item, f"{unit}  ({unit_count})",
                    payload=("eioc_unit", unit))
                for kind, entries in sorted(kinds.items()):
                    kind_item = node(
                        unit_item, f"{kind}  ({len(entries)})",
                        payload=("eioc_signal_group", (unit, kind)))
                    for tag, spec in sorted(entries):
                        direction = str(spec.get("direction") or "read")
                        description = str(spec.get("description") or "")
                        detail = f"{kind} · {direction}"
                        if description:
                            detail += f" · {description}"
                        node(kind_item, f"⚡ {tag}",
                             payload=("field_tag", (tag, detail)))
            eioc_item.setExpanded(True)

        # Local Virtual I/O is a first-class controller-side provider.  It is
        # neither native card I/O nor an Ethernet I/O Card, and drawing it as
        # either makes an in-process provider look as though a network hop
        # exists.  The project document remains visible even when its factory
        # cannot start, so an engineer can diagnose a broken provider rather
        # than watching the node silently disappear.
        virtual_io = self._project_virtual_io()
        if virtual_io:
            driver = self._virtual_io_driver()
            health = self._virtual_io_health(driver, virtual_io)
            name = str(virtual_io.get("name") or "Local Virtual I/O")
            state = str(health.get("state") or "stopped")
            vio_item = node(
                network, f"🔌 {name}  ·  Local Virtual I/O  ·  {state}",
                payload=("virtual_io", virtual_io), bold=True)
            provider = virtual_io.get("provider") or {}
            factory = str(provider.get("factory") or "not configured")
            node(vio_item, f"Provider  ·  {factory}",
                 payload=("virtual_io_provider", factory))
            source = str(virtual_io.get("source") or "not configured")
            node(vio_item, f"Output holder  ·  {source}",
                 payload=("virtual_io_source", source))
            signals = self._virtual_io_signal_specs(virtual_io)
            signal_root = node(
                vio_item, f"Virtual I/O Signals  ({len(signals)})",
                payload=("virtual_io_signals", virtual_io))
            groups: dict[str, dict[str, list[tuple[str, dict]]]] = {}
            for tag, raw in signals.items():
                spec = raw if isinstance(raw, dict) else {}
                unit = str(spec.get("plant_unit") or "Ungrouped")
                kind = str(spec.get("kind") or "Signal").upper()
                groups.setdefault(unit, {}).setdefault(kind, []).append(
                    (str(tag), spec))
            for unit, kinds in sorted(groups.items()):
                unit_count = sum(len(entries) for entries in kinds.values())
                unit_item = node(
                    signal_root, f"{unit}  ({unit_count})",
                    payload=("virtual_io_unit", unit))
                for kind, entries in sorted(kinds.items()):
                    kind_item = node(
                        unit_item, f"{kind}  ({len(entries)})",
                        payload=("virtual_io_signal_group", (unit, kind)))
                    for tag, spec in sorted(entries):
                        direction = str(spec.get("direction") or "")
                        description = str(spec.get("description") or "")
                        detail = f"{kind} · {direction} · local"
                        if description:
                            detail += f" · {description}"
                        node(kind_item, f"⚡ {tag}",
                             payload=("field_tag", (tag, detail)))
            vio_item.setExpanded(True)

        for remote in self.remote_nodes:
            suffix = f"@ {remote.host}"
            remote_item = node(
                network, f"🌐 {remote.name}  {suffix}  (remote)",
                payload=("remote_node", remote.name))
            if remote.connected:
                assigned_remote = node(
                    remote_item,
                    f"Assigned Modules  ({len(remote.modules)})",
                    payload=("remote_modules", remote.name))
                for module_name in remote.modules:
                    node(assigned_remote, f"⛭ {module_name}  ●",
                         payload=("remote_module",
                                  (remote.name, module_name)))
                remote_item.setExpanded(True)
            else:
                node(remote_item,
                     f"⚠ unreachable — {remote.error or 'no reply'}",
                     payload=("remote_error", remote.name))
        decom = node(physical, "🗃 Decommissioned Nodes",
                     payload=("decommissioned", None))
        if self.commissioned and not decommissioned_project_controllers:
            hint = node(decom,
                        "(nodes appear here when decommissioned)")
            hint.setForeground(0, Qt.gray)
        if controller is not None and not self.commissioned:
            # And the node itself reappears under the default naming
            # convention.
            if primary_record and primary_record.get("hardware_id"):
                commissioning = primary_record.get("commissioning") or {}
                identity = str(primary_record.get("serial")
                               or primary_record.get("hardware_id"))
                address = str(commissioning.get("address") or "offline")
                label = f"{primary_name}  ·  {identity}  ·  {address}"
            else:
                label = _DECOM_NAME
            node(decom, f"{lamp}{label}",
                 payload=("decommissioned_node", label))
            decom.setExpanded(True)
        for record in decommissioned_project_controllers:
            commissioning = record.get("commissioning") or {}
            name = str(record.get("name") or "Controller")
            identity = str(record.get("serial")
                           or record.get("hardware_id") or "unidentified")
            address = str(commissioning.get("address") or "offline")
            node(decom, f"🎛 {name}  ·  {identity}  ·  {address}",
                 payload=("project_decommissioned_controller", record))
            decom.setExpanded(True)
        physical.setExpanded(True)
        network.setExpanded(True)

        assigned = self.area.name in self.assigned_areas
        alarms = node(
            system,
            "🔔 Alarms and Events  "
            + ("(assigned to this workstation)" if assigned
               else "(NOT assigned — operation gated)"),
            payload=("alarms", None))
        for unit in self._project_units():
            unit_name = unit.get("name", "?") \
                if isinstance(unit, dict) else str(unit)
            node(alarms, f"⛨ {unit_name}", payload=("unit", unit))

        historian = node(system, "🗄 Continuous Historian",
                         payload=("historian", None))
        tagdb = getattr(self.store, "tagdb", None)
        if tagdb is not None:
            try:
                for tag in sorted(tagdb.field_tags()):
                    node(historian, f"📉 {tag}",
                         payload=("hist_tag", tag))
            except Exception:                       # noqa: BLE001
                pass

        system.setExpanded(True)
        strategies.setExpanded(True)
        # A refresh must not lose your place: expansion, selection
        # and scroll survive the rebuild.
        if view["expanded"] or view["selected"]:
            self._restore_view(view)
        self._sync_status()

    # -------------------------------------------------------- contents
    def _row_icon(self, kind: str, data):
        from azeo_control_trainer.core.presentation.studio_icons import draw_icon as _draw_icon
        names = {"system": "node", "library": "library",
                 "lib_blocks": "lib_block", "lib_pvms": "pvm_class",
                 "lib_procedure_blocks": "procedure", "lib_procedure_category": "folder",
                 "strategies": "folder", "network": "network", "physical": "network",
                 "control_network": "network", "alarms": "alarm",
                 "historian": "trend", "decommissioned": "disconnect",
                 "module": "module", "assigned_module": "module",
                 "controller": "controller",
                 "configured_controller": "controller",
                 "project_controller": "controller",
                 "project_decommissioned_controller": "disconnect",
                 "configured_module": "module",
                 "placeholder": "controller", "decommissioned_node": "disconnect",
                 "remote_node": "node", "remote_module": "module", "area": "area",
                 "eioc": "connect", "eioc_endpoint": "connect",
                 "eioc_signals": "tagdb", "eioc_unit": "folder",
                 "eioc_signal_group": "folder",
                 "io_card": "io_config", "channel": "assign_io",
                 "field_tag": "assign_io", "hist_tag": "trend",
                 "unit": "equipment", "lib_category": "folder",
                 "module_group": "folder", "unassigned_modules": "unassigned",
                 "std_folder": "folder", "virtual_io": "provider",
                 "virtual_io_provider": "provider",
                 "virtual_io_source": "status",
                 "virtual_io_signals": "tagdb",
                 "virtual_io_unit": "folder",
                 "virtual_io_signal_group": "folder"}
        if kind == "lib_procedure_block":
            from azeo_control_trainer.core.presentation.configuration_chrome import icon
            from azeo_control_trainer.core.procedures.library import block_for_token
            return icon(block_for_token(data).icon)
        if kind == "lib_block":
            try:
                from azeo_control_trainer.core.presentation.function_block_icons import (
                    make_block_qicon,
                )
                return make_block_qicon(data, None, 16)
            except Exception:                       # noqa: BLE001
                pass
        if kind == "lib_pvm":
            try:
                from azeo_control_trainer.core.hmi.pvms import registry as pvm_registry
                return self._pvm_icon(data, pvm_registry.get(*data))
            except Exception:                       # noqa: BLE001
                pass
        return _draw_icon(names.get(kind, "folder"), 16, _C["lapis"])

    def _show_contents(self, item, _col=0) -> None:
        self.contents.setSortingEnabled(False)
        self.contents.clear()
        for i in range(item.childCount()):
            child = item.child(i)
            payload = child.data(0, Qt.UserRole)
            name = child.text(0)
            for junk in ("🗀 ", "⛭ ", "⚡ ", "📉 ", "⛨ ", "🎛 ",
                         "  ●", "  ▲", "  (this station)"):
                name = name.replace(junk, "")
            kind, data = payload if payload else ("", None)
            type_text, status, description = "", "", ""
            if kind == "module":
                module_name, _path, module_kind, live = data
                type_text = module_kind
                if module_kind == "Equipment Module":
                    description = "descriptor — never downloads"
                elif live:
                    status = self._module_status(
                        module_name, self._online_names(),
                        self._open_fingerprints())
                else:
                    status = OTHER_STATION
                    description = "engineering only — not this station"
            elif kind == "area":
                type_text = "Plant Area"
                status = "(this station)" if data == self.area else ""
            elif kind == "field_tag":
                type_text = "Device Signal Tag"
                description = data[1]
            elif kind == "channel":
                type_text = data.channel_type
                status = "Enabled" if data.enabled else "Disabled"
                if data.enabled and not data.referenced:
                    status = "⚠ unreferenced"
                description = (f"{data.device_tag} · addr "
                               f"{data.address}"
                               + (f" · {data.description}"
                                  if data.description else ""))
            elif kind == "io_card":
                type_text = "I/O Card"
            elif kind == "unit":
                type_text = "Unit Module"
                if isinstance(data, dict):
                    consolidate = data.get("consolidate", {})
                    collapsed = [p.title() for p in
                                 ("WARNING", "ADVISORY", "LOG")
                                 if consolidate.get(p, True)]
                    description = (data.get("description", "")
                                   + (" · consolidates: "
                                      + ", ".join(collapsed)
                                      if collapsed else "")).strip(
                        " ·")
            elif kind == "module_group":
                type_text = "Module Type Folder"
                description = f"{data}s assigned to this unit"
            elif kind == "unassigned_modules":
                type_text = "Configuration Review"
                status = "Assignment required"
                description = "modules not assigned to a declared unit"
            elif kind == "controller":
                type_text = "Controller Node"
            elif kind == "configured_controller":
                type_text = "Configured Controller Node"
                commissioning = data.get("commissioning") or {}
                state = str(commissioning.get("state") or "configured")
                status = ("Decommissioned placeholder"
                          if state == "decommissioned"
                          else "Awaiting hardware")
                description = str(data.get("description") or "")
            elif kind == "project_controller":
                type_text = "Commissioned Controller Node"
                status = "Commissioned"
                commissioning = data.get("commissioning") or {}
                description = str(
                    commissioning.get("address")
                    or data.get("description") or "")
            elif kind == "project_decommissioned_controller":
                type_text = "Physical Controller"
                status = "Decommissioned"
                commissioning = data.get("commissioning") or {}
                description = str(
                    data.get("serial") or data.get("hardware_id") or "")
                if commissioning.get("address"):
                    description += f" · {commissioning['address']}"
            elif kind == "configured_module":
                type_text = data[2] or "Control Module"
                status = "Configured"
            elif kind == "eioc":
                type_text = "Ethernet I/O Card"
                eioc = getattr(self.store, "eioc", None)
                if eioc is not None:
                    status = ("Over capacity" if eioc.over_capacity
                              else "Configured")
                    description = eioc.identity_line()
            elif kind == "eioc_endpoint":
                type_text = "OPC UA Server"
                description = str(data)
            elif kind == "eioc_signals":
                type_text = "External Signal Database"
            elif kind == "eioc_unit":
                type_text = "Plant Unit"
            elif kind == "eioc_signal_group":
                type_text = "Signal Group"
            elif kind == "virtual_io":
                type_text = "Local Virtual I/O Provider"
                health = self._virtual_io_health(
                    self._virtual_io_driver(), data)
                status = str(health.get("state") or "stopped").title()
                description = str(health.get("last_error") or
                                  health.get("source") or "")
            elif kind == "virtual_io_provider":
                type_text = "In-process Provider Factory"
                description = str(data)
            elif kind == "virtual_io_source":
                type_text = "Output Arbitration Holder"
                description = str(data)
            elif kind == "virtual_io_signals":
                type_text = "Virtual Signal Database"
            elif kind == "virtual_io_unit":
                type_text = "Plant Unit"
            elif kind == "virtual_io_signal_group":
                type_text = "Signal Group"
            elif kind == "hist_tag":
                type_text = "Historized Parameter"
                description = "derived from the loaded modules"
            elif kind == "lib_block":
                type_text = "Function Block"
            elif kind == "lib_procedure_block":
                from azeo_control_trainer.core.procedures.library import block_for_token
                block = block_for_token(data)
                type_text, status, description = "Procedure Block", block.version, block.description
            elif kind in ("lib_procedure_blocks", "lib_procedure_category"):
                type_text = "Procedure Block Library"
            elif kind == "lib_pvm":
                type_text = "PVM Class"
            row = QTreeWidgetItem(
                self.contents, [name, type_text, status, description])
            row.setData(0, Qt.UserRole, payload)
            row.setData(0, Qt.UserRole + 1, self._text_key(child))
            icon = self._row_icon(kind, data)
            if icon is not None:
                row.setIcon(0, icon)
            if status == ON_SCAN:
                row.setForeground(2, Qt.darkGreen)
            elif status in (NOT_DOWNLOADED, CHANGED):
                row.setForeground(2, Qt.blue)
        payload = self._payload_of(item)
        if self.contents.topLevelItemCount() == 0 and payload \
                and payload[0] == "module" and payload[1][3]:
            # A live module's contents ARE its blocks — the tag
            # source Graphics Designer drags from (the Explorer owns
            # the plant tree; the studio keeps none).
            module_name = payload[1][0]
            try:
                graph = next(
                    (g for g in self.designer_window.designer
                     .open_graphs() if g.name == module_name), None)
            except Exception:                       # noqa: BLE001
                graph = None
            for block in (graph.blocks.values() if graph else ()):
                row = QTreeWidgetItem(self.contents, [
                    block.instance_name, block.block_type, "",
                    getattr(block, "display_name", "")])
                row.setData(0, Qt.UserRole, (
                    "mod_block",
                    (f"{module_name}/{block.instance_name}",
                     block.block_type)))
                icon = self._row_icon("lib_block", block.block_type)
                if icon is not None:
                    row.setIcon(0, icon)
        if self.contents.topLevelItemCount() == 0:
            kind = payload[0] if payload else ""
            hint = {"decommissioned":
                    "nodes appear here when decommissioned",
                    "assigned_io": "run Auto-sense to discover the "
                    "channels"}.get(kind, "this container is empty")
            row = QTreeWidgetItem(self.contents, [f"({hint})"])
            row.setForeground(0, Qt.gray)
        else:
            self.contents.setSortingEnabled(True)
            self.contents.sortByColumn(0, Qt.AscendingOrder)

    # ------------------------------------------------------- open verbs
    def _payload_of(self, item):
        return item.data(0, Qt.UserRole) if item else None

    def _activate(self, item, _col=0) -> None:
        payload = self._payload_of(item)
        if not payload:
            return
        kind, data = payload
        if kind == "module":
            self.open_module(data[0], data[1], live=data[3])
        elif kind in ("assigned_module",):
            for name, path, _k in self._area_modules(self.area):
                if name == data:
                    self.open_module(name, path, live=True)
                    return
        elif kind == "controller":
            self.open_controller_status()
        elif kind in ("configured_controller", "project_controller",
                      "project_decommissioned_controller"):
            self.open_control_designer()
        elif kind == "configured_module":
            self.open_module(data[0], data[1], live=True)
        elif kind == "eioc":
            self.open_eioc_config()
        elif kind == "virtual_io":
            self.open_virtual_io_simulator()
        elif kind == "channel":
            self.channel_properties(data)
        elif kind == "lib_procedure_block":
            self.open_procedure_block(data)
        else:
            # A container double-clicked in the right pane drills
            # into it — the Explorer convention; Backspace goes up.
            key = item.data(0, Qt.UserRole + 1)                 if item.treeWidget() is self.contents                 else self._text_key(item)
            target = self._tree_item_for_key(key)
            if target is not None and target.childCount() > 0:
                self.tree.setCurrentItem(target)
                target.setExpanded(True)
                self._show_contents(target)
            elif kind == "area":
                self.open_control_designer()

    from azeo_control_trainer.core.presentation.installed_apps import requires_component

    @requires_component("control_designer")
    def open_module(self, name: str, path: Path,
                    live: bool = True) -> None:
        """Double-click a module: Control Designer on that module."""
        if not live:
            if not is_headless():
                QMessageBox.information(
                    self, "Not this station",
                    f"{name} belongs to another area. Engineering is "
                    "performed at that area's own station — launch "
                    f"the trainer on '{Path(path).parents[1].name}' "
                    "to edit it.")
            return
        tab = self.designer_window.designer
        index = tab._find_tab_by_path(str(path))
        if index >= 0:
            tab._canvas_tabs.setCurrentIndex(index)
        self.open_control_designer()

    def _open_selected(self) -> None:
        item = self.contents.currentItem() or self.tree.currentItem()
        if item is not None:
            self._activate(item)

    # ----------------------------------------------------- applications
    def _launch_with_shell_retained(self, opener):
        """Run a modeless application command without losing Explorer.

        Explorer is the engineering-station shell, not a splash screen. A
        launched application may cover it while it owns focus, but Explorer
        must remain a live top-level window behind that application.
        """
        shell_was_visible = self.isVisible()
        try:
            return opener()
        finally:
            if shell_was_visible and not self.isVisible():
                # Do not raise or activate Explorer: the newly launched
                # application should retain foreground focus.
                self.show()

    @requires_component("control_designer")
    def open_control_designer(self) -> None:
        def launch():
            self.designer_window.show()
            self.designer_window.raise_()
            self.designer_window.activateWindow()

        self._launch_with_shell_retained(launch)

    @requires_component("graphics_designer")
    def open_graphics_designer(self) -> None:
        """The Azeo Graphics Designer (the type-driven PVM studio)."""
        self._launch_with_shell_retained(
            self.designer_window._pvm_studio)

    @requires_component("pa_designer")
    def open_pa_designer(self):
        """Retain one project editor, including its unsaved procedure draft."""
        try:
            from azeo_control_trainer.azeo_pa_designer import create_window

            if self._pa_designer_window is None:
                self._pa_designer_window = create_window(self.area, self)
                self._pa_designer_window.setAttribute(Qt.WA_DeleteOnClose)
                self._pa_designer_window.destroyed.connect(
                    lambda: setattr(self, "_pa_designer_window", None))
            editor = self._pa_designer_window
            self._launch_with_shell_retained(editor.show)
            editor.raise_()
            editor.activateWindow()
            return editor
        except Exception as error:
            self.statusBar().showMessage(f"PA Designer could not open: {error}")
            if not is_headless():
                QMessageBox.warning(self, "PA Designer", str(error))
            return None

    def open_procedure_block(self, kind=None, *, insert=False):
        editor = self.open_pa_designer()
        if editor is None:
            return None
        if kind is None:
            editor.focus_block_library()
        elif not editor.focus_library_block(kind):
            return None
        if insert:
            editor.add_selected_block()
        return editor

    def show_procedure_block_help(self, kind):
        from azeo_control_trainer.core.presentation.procedure_help import ProcedureHelpCenter
        from azeo_control_trainer.core.procedures.library import block_for_token

        dialog = getattr(self, "_procedure_block_help", None)
        if dialog is None:
            dialog = ProcedureHelpCenter(self)
            self._procedure_block_help = dialog
        dialog.show_topic(block_for_token(kind).help_key)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        return dialog

    @requires_component("graphics_designer")
    def open_pvm_config(self, pvm_class: str = "") -> None:
        """Azeo capability: the PVM Configuration Designer opens from
        Graphics Designer — so open the studio and ask it."""
        def launch():
            self.designer_window._pvm_studio()
            studio = getattr(self.designer_window,
                             "_pvm_studio_window", None)
            if studio is not None:
                return studio.open_pvm_config(pvm_class)
            return None

        self._launch_with_shell_retained(launch)

    @requires_component("operator_station")
    def open_operator_station(self) -> bool:
        """Gated by the A&E assignment: a workstation
        controls only the areas assigned to its subsystem."""
        if self.area.name not in self.assigned_areas:
            if not is_headless():
                QMessageBox.warning(
                    self, "Area not assigned",
                    f"{self.area.name} is not assigned to this "
                    "workstation's Alarms and Events subsystem — "
                    "operation is gated. Assign it from the Alarms "
                    "and Events node.")
            return False
        config = self._project_virtual_io()
        driver = self._virtual_io_driver() if config else None
        health = self._virtual_io_health(driver, config) if config else {}
        if config and not health.get("running"):
            # An explicit Operator Station command is also an explicit
            # request for the configured training process.  It remains
            # manual at ordinary Explorer startup; only this user action (or
            # Start Plant Simulator) attaches it.
            self._pending_operator_station = True
            worker = self._virtual_io_worker
            if worker is not None and worker.isRunning():
                self.statusBar().showMessage(
                    "Preparing Operator Station: waiting for Virtual I/Oâ€¦")
                return True
            if not self.start_virtual_io():
                self._pending_operator_station = False
                return False
            self.statusBar().showMessage(
                "Preparing Operator Station: starting Virtual I/Oâ€¦")
            return True

        return self._open_operator_station_live()

    def _open_operator_station_live(self) -> bool:
        """Download loaded modules, then open the console over live graphs."""
        designer = self.designer_window.designer
        self.statusBar().showMessage(
            "Preparing Operator Station: downloading control modulesâ€¦")
        QApplication.processEvents()
        if not designer.auto_go_online():
            log.warning(
                "Operator Station opened without an online control module")
            self.statusBar().showMessage(
                "Operator Station has no online control modules.", 6000)
        # **The Azeo Operator Station console, not the DynaLive one.**
        #
        # Explorer's Graphics Designer button opens the PVM studio, so
        # the console beside it has to be the one that shows what that
        # studio publishes. It used to open the DynaLive station, which
        # renders `Display` files from a different stack — an engineer
        # drew a PVM display, opened the Operator Station and saw
        # something unrelated, with nothing on screen saying why.
        #
        # **The Explorer offers ONE console, and it is this one.**
        # The DynaLive station is not deleted — it is a working
        # console over a different document format — but it is
        # reached from Control Designer's View menu (Ctrl+Shift+O),
        # not from here. An engineering station that launches two
        # consoles over two document formats makes the operator's
        # first question "which one?", and the Explorer's own
        # Graphics Designer publishes to exactly one of them.
        self._launch_with_shell_retained(
            self.designer_window._live_station)
        self.statusBar().showMessage(
            "Operator Station opened with live control modules.", 5000)
        return True

    def open_diagnostics(self) -> None:
        self._launch_with_shell_retained(
            self.designer_window._controller_diagnostics)

    def open_controller_status(self) -> None:
        self.designer_window._controller_status()

    def open_eioc_config(self) -> None:
        """Open the external-device configurator from its EIOC node."""
        from .opcua_browser import (
            OpcUaBrowserDialog,
        )

        dialog = OpcUaBrowserDialog(
            self.store, project_path=self.area / "_project.json", parent=self)
        self._eioc_dialog = dialog
        dialog.accepted.connect(self._reload_eioc_config)
        dialog.show()

    def _reload_eioc_config(self) -> None:
        """Refresh EIOC identity after Save; transport changes need restart."""
        from ..app import _eioc_config
        from azeo_control_trainer.connectivity.fieldio.eioc import EthernetIoCard

        self.store.eioc = EthernetIoCard.from_config(_eioc_config(self.area))
        self.refresh()

    def open_tag_database(self) -> None:
        self._launch_with_shell_retained(
            self.designer_window.designer.show_tag_database)

    # ------------------------------------------------------ pvm icons
    _pvm_icons: dict = {}

    def _pvm_icon(self, key, cls):
        """Use the shared engineering glyph, including on high-DPI displays."""
        from azeo_control_trainer.core.presentation.function_block_icons import make_block_qicon

        return make_block_qicon(cls.block_type, size=18)

    # ---------------------------------------------- I/O channels (M2)
    def _field_driver(self):
        return getattr(self.designer_window, "plant_driver", None)

    def _io_cards(self) -> list:
        from .io_model import build_io_cards
        return build_io_cards(self.store, self._field_driver())

    def _io_master(self):
        from .io_model import _master_of
        return _master_of(self._field_driver())

    def set_channel(self, tag: str, enabled: bool | None = None,
                    device_tag: str | None = None) -> bool:
        """Apply channel configuration to the live field driver —
        the workshop's Enable + Device Tag edits, honestly wired."""
        master = self._io_master()
        if master is None:
            return False
        if enabled is not None:
            master.set_channel_enabled(tag, enabled)
        if device_tag is not None:
            master.set_device_tag(tag, device_tag)
        self.refresh()
        return True

    def channel_properties(self, channel) -> None:
        """The p. 88 Properties box: Description, Channel Type,
        Device Tag, Enabled — type is the card's, tag and enable are
        the engineer's."""
        if is_headless():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(
            f"{channel.card} {channel.label} Properties")
        form = QFormLayout(dialog)
        form.addRow("Description",
                    QLabel(channel.description or "—"))
        form.addRow("Channel Type", QLabel(channel.channel_type))
        form.addRow("Address", QLabel(str(channel.address)))
        tag_field = QLineEdit(channel.device_tag)
        form.addRow("Device Tag", tag_field)
        enabled_box = QCheckBox("Enabled")
        enabled_box.setChecked(channel.enabled)
        form.addRow("", enabled_box)
        if not channel.referenced:
            warn = QLabel("⚠ No control module references this "
                          "Device Tag — the channel feeds a point "
                          "nothing consumes.")
            warn.setWordWrap(True)
            form.addRow(warn)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                   | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.Accepted:
            self.set_channel(channel.tag,
                             enabled=enabled_box.isChecked(),
                             device_tag=tag_field.text().strip())

    # ------------------------------- A&E and Historian (phase 5)
    def _project_network(self) -> tuple[list[dict], dict[str, str]]:
        """Return persisted controller nodes and normalized assignments.

        Explorer used to draw only the in-process controller even though the
        Control Designer project tree already authored multiple nodes. Reading
        the same document here keeps both engineering views on one network.
        """
        import json

        try:
            project = json.loads(
                (self.area / "_project.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return [], {}
        nodes = [dict(raw) for raw in project.get("nodes", ())
                 if str(raw.get("type") or "controller").lower()
                 == "controller"]
        assignments = {
            str(path).replace("\\", "/").lower(): str(node_id)
            for path, node_id in (project.get("assignments") or {}).items()
        }
        return nodes, assignments

    def _project_virtual_io(self) -> dict:
        """Return the area's Local Virtual I/O declaration, if present."""
        _path, project = self._project_document()
        if not project:
            return {}
        for area in project.get("areas", ()):
            config = area.get("virtual_io")
            if isinstance(config, dict):
                return dict(config)
        return {}

    def _virtual_io_signal_specs(self, config: dict | None = None) -> dict:
        """Expose the driver's catalog-backed routes without a copied map."""
        config = config or {}
        try:
            from azeo_control_trainer.connectivity.fieldio.local_virtual_io import load_signal_routes

            routes = load_signal_routes(config, self.area)
        except Exception as error:                 # noqa: BLE001
            log.warning("Explorer cannot load Virtual I/O routes: %s", error)
            return {}
        return {
            tag: {
                "direction": route.direction,
                "kind": route.kind,
                "data_type": route.data_type,
                "unit": route.unit,
                "description": route.description,
                "plant_unit": route.plant_unit,
                "range": route.eu_range,
            }
            for tag, route in routes.items()
        }

    def _virtual_io_health(self, driver, config: dict | None = None) -> dict:
        """Normalise live/failed/not-started provider state for Explorer."""
        answer: dict = {}
        health = getattr(driver, "health", None)
        if callable(health):
            try:
                raw = health()
                if isinstance(raw, dict):
                    answer = dict(raw)
            except Exception as error:             # noqa: BLE001
                answer = {"running": False, "last_error": str(error)}
        config = config or {}
        answer.setdefault(
            "name", str(config.get("name") or "Local Virtual I/O"))
        answer.setdefault(
            "type", str(config.get("type") or "local_virtual_io"))
        answer.setdefault(
            "running", bool(getattr(driver, "running", False)))
        answer.setdefault("source", str(config.get("source") or ""))
        answer.setdefault(
            "signals", len(self._virtual_io_signal_specs(config)))
        answer.setdefault(
            "last_error", str(getattr(driver, "last_error", "") or ""))
        answer.setdefault(
            "can_start", bool(driver and callable(
                getattr(driver, "start", None))))
        if self._virtual_io_operation_error:
            answer["last_error"] = self._virtual_io_operation_error
        if self._virtual_io_transition:
            state = self._virtual_io_transition
        elif answer.get("last_error"):
            state = "faulted"
        elif answer.get("running"):
            state = "running"
        else:
            state = "stopped"
        answer["state"] = state
        return answer

    def _virtual_io_driver(self):
        """Return the configured provider without knowing its implementation."""
        driver = getattr(self.store, "field_io_driver", None)
        if driver is not None:
            return driver
        return self._field_driver()

    @staticmethod
    def _virtual_io_operation_allowed(operation: str, health: dict,
                                      driver) -> bool:
        if driver is None:
            return False
        running = bool(health.get("running"))
        can_start = bool(health.get("can_start"))
        state = str(health.get("state") or "")
        if state in {"starting", "stopping"}:
            return False
        if operation == "start":
            return can_start and not running
        if operation == "stop":
            return running and callable(getattr(driver, "stop", None))
        if operation == "restart":
            return can_start and (running or state == "faulted")
        return False

    def _add_virtual_io_lifecycle_actions(self, menu) -> dict[str, object]:
        """Add the same real lifecycle verbs to Tools and context menus."""
        start = menu.addAction("Start Simulator")
        start.triggered.connect(self.start_virtual_io)
        stop = menu.addAction("Stop Simulator")
        stop.triggered.connect(self.stop_virtual_io)
        restart = menu.addAction("Restart Simulator")
        restart.triggered.connect(self.restart_virtual_io)
        menu.addSeparator()
        status = menu.addAction("Provider Status…")
        status.triggered.connect(self.open_virtual_io_status)
        actions = {
            "start": start, "stop": stop, "restart": restart,
            "status": status,
        }
        self._sync_virtual_io_actions(actions)
        return actions

    def _sync_virtual_io_actions(self, actions=None) -> dict:
        actions = actions or self._virtual_io_actions
        config = self._project_virtual_io()
        # A legacy bundled PlantDriver may also sit on the designer host. The
        # Virtual I/O menu must never operate it when this project declares no
        # Virtual I/O provider.
        driver = self._virtual_io_driver() if config else None
        health = self._virtual_io_health(driver, config)
        for operation in ("start", "stop", "restart"):
            action = actions.get(operation)
            if action is not None:
                action.setEnabled(self._virtual_io_operation_allowed(
                    operation, health, driver))
        status = actions.get("status")
        if status is not None:
            status.setEnabled(bool(config))
        self._sync_virtual_io_launcher_actions(health, driver, config)
        return health

    def _sync_virtual_io_launcher_actions(
            self, health: dict, driver, config: dict) -> None:
        """Keep application launchers truthful as provider state changes."""
        visible = bool(config)
        state = str(health.get("state") or "stopped")
        running = bool(health.get("running"))
        provider_name = str(config.get("name") or "Local Virtual I/O")
        if running:
            label = "Plant Simulator Status…"
            tip = f"Open {provider_name} status (running)"
            enabled = True
        elif state in {"starting", "stopping"}:
            label = f"Plant Simulator {state.title()}…"
            tip = f"{provider_name} lifecycle operation is in progress"
            enabled = False
        else:
            label = ("Retry Plant Simulator" if state == "faulted"
                     else "Start Plant Simulator")
            tip = f"Start {provider_name} through Local Virtual I/O"
            enabled = self._virtual_io_operation_allowed(
                "start", health, driver)
        for action in self._virtual_io_launcher_actions:
            action.setVisible(visible)
            action.setText(label)
            action.setToolTip(tip)
            action.setEnabled(enabled)

    def _confirm_virtual_io_stop(self, operation: str) -> bool:
        if is_headless() or operation == "start":
            return True
        verb = "restart" if operation == "restart" else "stop"
        return QMessageBox.question(
            self, f"{verb.title()} Virtual I/O",
            f"{verb.title()} the local simulator? Controller inputs become "
            "stale while the provider is stopped, and field output demands "
            "will be rejected.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) == QMessageBox.Yes

    def _begin_virtual_io_operation(self, operation: str) -> bool:
        worker = self._virtual_io_worker
        if worker is not None and worker.isRunning():
            self.statusBar().showMessage(
                "A Virtual I/O operation is already in progress.", 3000)
            return False
        config = self._project_virtual_io()
        driver = self._virtual_io_driver() if config else None
        health = self._virtual_io_health(driver, config)
        if not self._virtual_io_operation_allowed(
                operation, health, driver):
            reason = str(health.get("last_error") or "").strip()
            if not health.get("can_start") and operation in {
                    "start", "restart"}:
                reason = reason or (
                    "The provider configuration is unavailable; correct it "
                    "and reopen the project.")
            self.statusBar().showMessage(
                reason or f"Virtual I/O cannot {operation} in its current "
                "state.", 5000)
            return False
        if not self._confirm_virtual_io_stop(operation):
            return False

        self._virtual_io_operation_error = ""
        self._virtual_io_transition = (
            "starting" if operation == "start" else "stopping")
        name = str(config.get("name") or "Local Virtual I/O")
        self.statusBar().showMessage(
            f"{name}: {self._virtual_io_transition.title()}…")
        self.refresh()
        self._sync_virtual_io_actions()

        worker = _VirtualIoLifecycleWorker(driver, operation, self)
        self._virtual_io_worker = worker
        worker.progress.connect(self._virtual_io_progress)
        worker.completed.connect(self._virtual_io_completed)
        worker.finished.connect(
            lambda w=worker: self._virtual_io_worker_finished(w))
        worker.start()
        return True

    def _virtual_io_progress(self, state: str) -> None:
        if state not in {"starting", "stopping"}:
            return
        self._virtual_io_transition = state
        name = str(self._project_virtual_io().get("name")
                   or "Local Virtual I/O")
        self.statusBar().showMessage(f"{name}: {state.title()}…")
        self.refresh()
        self._sync_virtual_io_actions()

    def _virtual_io_completed(self, operation: str, success: bool,
                              message: str) -> None:
        self._virtual_io_transition = ""
        self._virtual_io_operation_error = "" if success else (
            message or f"Virtual I/O {operation} failed")
        config = self._project_virtual_io()
        name = str(config.get("name") or "Local Virtual I/O")
        outcome = "success" if success else "failure"
        from ..config.logging_config import audit_event
        audit_event(
            "explorer", f"virtual_io.{operation}", outcome=outcome,
            provider=name, source=str(config.get("source") or ""))
        if success:
            text = f"{name}: {operation.title()} completed."
            log.info(text)
        else:
            text = f"{name}: {operation.title()} failed — {message}"
            log.error(text)
        self.statusBar().showMessage(text, 6000)
        self.refresh()
        self._sync_virtual_io_actions()
        if operation == "start" and self._pending_operator_station:
            self._pending_operator_station = False
            if success:
                # Complete outside the worker's signal stack.  The download
                # constructs 151 runtimes and may process events itself; doing
                # that while QThread is still delivering `completed` made the
                # lifecycle state needlessly re-entrant.
                QTimer.singleShot(0, self._open_operator_station_live)
            elif not is_headless():
                QMessageBox.warning(
                    self, "Operator Station not started",
                    "The configured Virtual I/O provider could not start, "
                    "so the Operator Station was not opened with misleading "
                    "non-live values.\n\n" + (message or "Unknown error"))

    def _virtual_io_worker_finished(
            self, worker: _VirtualIoLifecycleWorker) -> None:
        if self._virtual_io_worker is worker:
            self._virtual_io_worker = None
        worker.deleteLater()

    def start_virtual_io(self) -> bool:
        return self._begin_virtual_io_operation("start")

    def open_or_start_virtual_io(self) -> bool:
        """Start a stopped provider, or show status for the running one."""
        config = self._project_virtual_io()
        driver = self._virtual_io_driver() if config else None
        health = self._virtual_io_health(driver, config)
        if health.get("running"):
            self.open_virtual_io_status()
            return True
        return self.start_virtual_io()

    def open_virtual_io_simulator(self):
        """Open the provider-neutral AI/DI commissioning workbench."""
        driver = self._virtual_io_driver()
        if driver is None:
            if not is_headless():
                QMessageBox.information(
                    self, "Virtual I/O Simulator",
                    "This project does not declare a Local Virtual I/O provider.")
            return None
        capabilities = getattr(driver, "simulation_capabilities", None)
        if is_headless():
            return capabilities() if callable(capabilities) else {
                "available": False,
                "reason": "This provider does not expose signal simulation.",
            }
        existing = self._virtual_io_simulator_dialog
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return existing
        from .virtual_io_simulator import VirtualIoSimulatorDialog

        dialog = VirtualIoSimulatorDialog(driver, self.area, self)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.destroyed.connect(
            lambda: setattr(self, "_virtual_io_simulator_dialog", None))
        self._virtual_io_simulator_dialog = dialog
        self._launch_with_shell_retained(dialog.show)
        return dialog

    @requires_component("simulation_workbench")
    def open_simulation_workbench(self):
        """Open the provider-neutral system simulation workspace."""
        driver = self._virtual_io_driver()
        if driver is None:
            if not is_headless():
                QMessageBox.information(
                    self, "Simulation Workbench",
                    "This project does not declare a Local Virtual I/O provider.")
            return None
        from azeo_control_trainer.core.simulation.workbench import SimulationWorkbenchService
        from azeo_control_trainer.config.paths import data_dir
        executive_getter = getattr(
            getattr(self.designer_window, "designer", None),
            "controller_executive", None)
        executive = executive_getter() if callable(executive_getter) else None
        if is_headless():
            journal_path = (data_dir() / "simulation" / self.area.name /
                            "operator_changes.sqlite")
            return SimulationWorkbenchService(
                self.store, driver, self.area,
                executive=executive,
                journal_path=journal_path).diagnostics()
        existing = self._simulation_workbench_dialog
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return existing
        from azeo_control_trainer.azeo_simulation_workbench import (
            SimulationWorkbenchWindow,
        )

        dialog = SimulationWorkbenchWindow(
            self.store, driver, self.area, self, executive=executive)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.destroyed.connect(
            lambda: setattr(self, "_simulation_workbench_dialog", None))
        self._simulation_workbench_dialog = dialog
        self._launch_with_shell_retained(dialog.show)
        return dialog

    def open_project_administrator(self):
        """Open the repository's transactional project lifecycle tool."""
        from .project_admin import ProjectAdministrator

        manager = ProjectAdministrator()
        if is_headless():
            return manager.inventory()
        existing = self._project_administrator_dialog
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return existing
        from .project_administrator import ProjectAdministratorDialog

        dialog = ProjectAdministratorDialog(manager, self.area, self)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.destroyed.connect(
            lambda: setattr(self, "_project_administrator_dialog", None))
        self._project_administrator_dialog = dialog
        self._launch_with_shell_retained(dialog.show)
        return dialog

    def stop_virtual_io(self) -> bool:
        return self._begin_virtual_io_operation("stop")

    def restart_virtual_io(self) -> bool:
        return self._begin_virtual_io_operation("restart")

    def open_virtual_io_status(self):
        """Show provider-neutral status without importing simulator code."""
        config = self._project_virtual_io()
        driver = getattr(self.store, "field_io_driver", None)
        health = self._virtual_io_health(driver, config)
        if is_headless():
            return health
        dialog = QDialog(self)
        dialog.setWindowTitle(
            f"{config.get('name') or 'Local Virtual I/O'} — Status")
        form = QFormLayout(dialog)
        form.addRow("State", QLabel(str(
            health.get("state") or "stopped").title()))
        form.addRow("Type", QLabel(str(
            health.get("type") or config.get("type") or "local_virtual_io")))
        form.addRow("Source / holder", QLabel(str(
            health.get("source") or config.get("source") or "—")))
        form.addRow("Signals", QLabel(str(
            health.get("signals") or
            len(self._virtual_io_signal_specs(config)))))
        form.addRow("Inputs published", QLabel(str(
            health.get("reads_published", 0))))
        form.addRow("Output demands enqueued", QLabel(str(
            health.get("writes_enqueued", 0))))
        form.addRow("Output readbacks", QLabel(str(
            health.get("output_readbacks_published", 0))))
        provider = config.get("provider") or {}
        form.addRow("Provider factory", QLabel(str(
            provider.get("factory") or "—")))
        error = str(health.get("last_error") or "")
        if error:
            warning = QLabel(error)
            warning.setWordWrap(True)
            warning.setStyleSheet("color: #B42318; font-weight: 600;")
            form.addRow("Last error", warning)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.clicked.connect(dialog.accept)
        form.addRow(buttons)
        polish_dialog(dialog, title="Virtual I/O Status", mark="status")
        dialog.setMinimumWidth(480)
        dialog.exec()
        return health

    def _project_document(self) -> tuple[Path, dict] | tuple[None, None]:
        """Read the project document used by node lifecycle operations."""
        import json

        path = self.area / "_project.json"
        try:
            return path, json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, None

    @staticmethod
    def _write_project_document(path: Path, project: dict) -> bool:
        import json

        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            temporary.write_text(
                json.dumps(project, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8")
            # A controller association is identity-bearing configuration.
            # Replacing a complete sibling file avoids leaving half a JSON
            # document behind if the process stops during the write.
            temporary.replace(path)
        except OSError:
            temporary.unlink(missing_ok=True)
            return False
        return True

    def _project_units(self) -> list:
        """The area's units, wherever the file keeps them (top level
        or inside the area entry — both shapes ship)."""
        return self._area_units(self.area)

    def set_area_assigned(self, assigned: bool) -> None:
        """Workshop step 4 (p.66): assign the area to this
        workstation's Alarms & Events subsystem — or remove it, and
        watch operation gate."""
        if assigned:
            self.assigned_areas.add(self.area.name)
        else:
            self.assigned_areas.discard(self.area.name)
        self.refresh()

    def save_unit(self, name: str, description: str | None = None,
                  consolidate: dict | None = None) -> bool:
        """Write a unit's description and per-priority consolidation
        back to _project.json — the dialog owns this one section of
        the file, nothing else. CRITICAL never consolidates: hiding
        what tripped is the one thing the operator cannot afford."""
        import json
        path = self.area / "_project.json"
        try:
            project = json.loads(path.read_text(encoding="utf-8"))
        except Exception:                           # noqa: BLE001
            return False

        def units_lists(doc):
            yield doc.get("units", []) or []
            for entry in doc.get("areas", ()) or ():
                yield entry.get("units", []) or []

        found = False
        for units in units_lists(project):
            for unit in units:
                if isinstance(unit, dict) \
                        and unit.get("name") == name:
                    if description is not None:
                        unit["description"] = description
                    if consolidate is not None:
                        merged = dict(unit.get("consolidate", {}))
                        merged.update(consolidate)
                        merged["CRITICAL"] = False
                        unit["consolidate"] = merged
                    found = True
        if not found:
            return False
        path.write_text(json.dumps(project, indent=2,
                                   ensure_ascii=False) + "\n",
                        encoding="utf-8", newline="\n")
        self.refresh()
        return True

    def unit_properties(self, unit: dict) -> None:
        """The unit's Properties page: description, its modules, and
        the per-priority consolidation policy (7017 Module 6)."""
        if is_headless() or not isinstance(unit, dict):
            return
        name = unit.get("name", "?")
        dialog = QDialog(self)
        dialog.setWindowTitle(f"{name} — Unit Properties")
        form = QFormLayout(dialog)
        form.addRow("Unit", QLabel(name))
        desc_field = QLineEdit(unit.get("description", ""))
        desc_field.setMinimumWidth(300)
        form.addRow("Description", desc_field)
        modules_label = QLabel(
            ", ".join(unit.get("modules", [])) or "—")
        modules_label.setWordWrap(True)
        form.addRow("Modules", modules_label)
        note = QLabel("Per priority: consolidate the banner entry "
                      "under the unit name, or keep the module name.")
        note.setWordWrap(True)
        form.addRow(note)
        boxes = {}
        consolidate = dict(unit.get("consolidate", {}))
        for priority in ("CRITICAL", "WARNING", "ADVISORY", "LOG"):
            box = QCheckBox("Consolidate under the unit name")
            box.setChecked(bool(consolidate.get(
                priority, priority != "CRITICAL")))
            if priority == "CRITICAL":
                box.setChecked(False)
                box.setEnabled(False)
                box.setToolTip(
                    "Never consolidate a critical alarm — hiding "
                    "what tripped is the one thing the operator "
                    "cannot afford.")
            form.addRow(priority.title(), box)
            boxes[priority] = box
        buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                   | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.Accepted:
            self.save_unit(
                name, description=desc_field.text(),
                consolidate={p: b.isChecked()
                             for p, b in boxes.items()})

    def export_historian_csv(self, path=None):
        """The derived dataset as CSV — real export, no fake toggles:
        the historian configures itself from the loaded modules, so
        its Properties page documents rather than pretends."""
        tagdb = getattr(self.store, "tagdb", None)
        if tagdb is None:
            return None
        if path is None:
            if is_headless():
                return None
            from PySide6.QtWidgets import QFileDialog
            path, _f = QFileDialog.getSaveFileName(
                self, "Export historian dataset",
                str(self.area / "historian_dataset.csv"),
                "CSV (*.csv)")
            if not path:
                return None
        path = Path(path)
        lines = ["tag,touched_by"]
        for tag, blocks_for in sorted(tagdb.field_tags().items()):
            touched = ";".join(sorted(str(b) for b in blocks_for)) \
                if isinstance(blocks_for, (list, tuple, set)) \
                else str(blocks_for)
            lines.append(f"{tag},{touched}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8",
                        newline="\n")
        return path

    def historian_properties(self) -> None:
        if is_headless():
            return
        tagdb = getattr(self.store, "tagdb", None)
        count = len(tagdb.field_tags()) if tagdb else 0
        QMessageBox.information(
            self, "Continuous Historian",
            f"Dataset: {count} parameter(s), derived from the loaded "
            "modules — nothing here is hand-maintained, so it cannot "
            "drift from the configuration.\n\nRight-click a "
            "parameter to open its trend; Export writes the dataset "
            "as CSV.")

    # ------------------------------------------ remote nodes (2.5)
    def _remote(self, name: str):
        return next((r for r in self.remote_nodes
                     if r.name == name), None)

    def add_remote_node(self, name: str | None = None,
                        endpoint: str | None = None):
        """Control Network ▸ Add Remote Node: name it, point it at a
        PK endpoint, browse it. An unreachable node still lands in
        the tree with its error spelled out."""
        from .remote_node import RemoteNode
        if name is None and not is_headless():
            dialog = QDialog(self)
            dialog.setWindowTitle("Add Remote Node")
            form = QFormLayout(dialog)
            name_field = QLineEdit("PK-CTLR-2")
            form.addRow("Name", name_field)
            endpoint_field = QLineEdit(
                "opc.tcp://192.168.0.10:43300/azeo/pk")
            endpoint_field.setMinimumWidth(280)
            form.addRow("Endpoint", endpoint_field)
            note = QLabel("Monitor and operate only — engineering is "
                          "performed at that node's own station.")
            note.setWordWrap(True)
            form.addRow(note)
            buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                       | QDialogButtonBox.Cancel)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            form.addRow(buttons)
            polish_dialog(dialog, title="Commission Controller", mark="connect")
            dialog.setMinimumWidth(520)
            if dialog.exec() != QDialog.Accepted:
                return None
            name = name_field.text().strip()
            endpoint = endpoint_field.text().strip()
        if not name or not endpoint \
                or not _valid_node_name(name) \
                or self._remote(name) is not None:
            return None
        remote = RemoteNode(name=name, endpoint=endpoint)
        remote.browse()
        self.remote_nodes.append(remote)
        self.refresh()
        return remote

    def rebrowse_remote(self, name: str) -> bool:
        remote = self._remote(name)
        if remote is None:
            return False
        ok = remote.browse()
        self.refresh()
        return ok

    def remove_remote_node(self, name: str) -> bool:
        before = len(self.remote_nodes)
        self.remote_nodes = [r for r in self.remote_nodes
                             if r.name != name]
        removed = len(self.remote_nodes) < before
        if removed:
            self.refresh()
        return removed

    def remote_properties(self, name: str) -> None:
        remote = self._remote(name)
        if remote is None or is_headless():
            return
        state = (f"connected — {len(remote.modules)} module(s)"
                 if remote.connected
                 else f"unreachable — {remote.error}")
        QMessageBox.information(
            self, f"{remote.name} — Remote Node",
            f"Endpoint:  {remote.endpoint}\n"
            f"State:  {state}\n\n"
            "Contract: monitor and operate over OPC UA. Modules are "
            "browsed live with quality; writable field points accept "
            "supervisory writes. Engineering is performed at this "
            "node's own station.")

    # -------------------------- physical-controller discovery / lifecycle
    def _controller_record(self, node_id: str) -> dict | None:
        nodes, _assignments = self._project_network()
        return next((node for node in nodes
                     if str(node.get("node_id") or "") == node_id), None)

    def open_controller_discovery(self) -> None:
        """Find controllers that answer on the engineering network."""
        from .controller_discovery_dialog import ControllerDiscoveryDialog

        nodes, _assignments = self._project_network()
        hardware_ids = {
            str(node.get("hardware_id")) for node in nodes
            if node.get("hardware_id")
        }
        dialog = ControllerDiscoveryDialog(
            add_controller=self.add_discovered_controller,
            existing_hardware_ids=hardware_ids,
            parent=self,
        )
        self._controller_discovery_dialog = dialog
        dialog.show()
        dialog.discover()

    def add_discovered_controller(self, advertisement,
                                  commission: bool = False) -> bool:
        """Persist a discovered identity, optionally commissioning it.

        Matching an unbound placeholder by name is intentional: engineering
        may create ``APS-CTRL-2`` before its hardware arrives. A conflicting
        hardware identity is refused rather than silently replacing it.
        """
        from datetime import datetime, timezone
        import re

        path, project = self._project_document()
        if path is None or project is None:
            return False
        nodes = project.setdefault("nodes", [])
        hardware_id = str(advertisement.hardware_id).strip()
        name = str(advertisement.name).strip()
        if not hardware_id or not _valid_node_name(name):
            return False

        record = next((node for node in nodes
                       if str(node.get("hardware_id") or "") == hardware_id),
                      None)
        same_name = next((node for node in nodes
                          if str(node.get("type") or "controller").lower()
                          == "controller"
                          and str(node.get("name") or "") == name), None)
        if record is None and same_name is not None:
            bound_id = str(same_name.get("hardware_id") or "")
            if bound_id and bound_id != hardware_id:
                return False
            record = same_name
        if record is None:
            base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") \
                or "controller"
            known_ids = {str(node.get("node_id") or "") for node in nodes}
            node_id = base
            suffix = 2
            while node_id in known_ids:
                node_id = f"{base}_{suffix}"
                suffix += 1
            record = {"node_id": node_id, "type": "controller"}
            nodes.append(record)

        # Once hardware is associated, the project placeholder owns its
        # configured name. Rediscovery may report an old device name; accepting
        # that here would silently rename the engineering configuration.
        configured_name = str(record.get("name") or name)
        record.update({
            "name": configured_name,
            "type": "controller",
            "model": str(advertisement.model or "PK100").upper(),
            "hardware_id": hardware_id,
            "serial": str(advertisement.serial or hardware_id),
            "description": str(advertisement.description or
                               record.get("description") or ""),
        })
        record["commissioning"] = {
            "state": "commissioned" if commission else "decommissioned",
            "address": str(advertisement.address or ""),
            "opcua_endpoint": str(advertisement.opcua_endpoint or ""),
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        }
        if not self._write_project_document(path, project):
            return False
        self.refresh()
        return True

    def _set_project_controller_state(
            self, node_id: str, state: str, *, configured_name: str = "",
            description: str | None = None) -> bool:
        path, project = self._project_document()
        if path is None or project is None:
            return False
        record = next((node for node in project.get("nodes", ())
                       if str(node.get("node_id") or "") == node_id
                       and str(node.get("type") or "controller").lower()
                       == "controller"), None)
        if record is None or not record.get("hardware_id"):
            return False
        commissioning = record.setdefault("commissioning", {})
        commissioning["state"] = state
        if configured_name:
            record["name"] = configured_name
        if description is not None:
            record["description"] = description
        if not self._write_project_document(path, project):
            return False
        if node_id == self._primary_node_id:
            responder = getattr(self.store, "controller_discovery", None)
            advertisement = getattr(responder, "advertisement", None)
            if advertisement is not None:
                from dataclasses import replace

                responder.advertisement = replace(
                    advertisement,
                    name=str(record.get("name") or advertisement.name),
                    state=state,
                    description=str(record.get("description")
                                    or advertisement.description),
                )
        from ..config.logging_config import audit_event
        audit_event("explorer", "controller.commissioning_state",
                    node_id=node_id, state=state,
                    configured_name=record.get("name", ""))
        self.refresh()
        return True

    def commission_project_controller(self, node_id: str) -> bool:
        """Associate a previously discovered controller with its placeholder."""
        return self._set_project_controller_state(node_id, "commissioned")

    def decommission_project_controller(self, node_id: str) -> bool:
        """Retain configuration but return hardware to Decommissioned Nodes."""
        if not is_headless():
            record = self._controller_record(node_id) or {}
            proceed = QMessageBox.warning(
                self, "Production System Warning",
                f"Decommission {record.get('name', node_id)}?\n\n"
                "Its configuration remains as a Control Network placeholder; "
                "the physical identity moves to Decommissioned Nodes.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No) == QMessageBox.Yes
            if not proceed:
                return False
        return self._set_project_controller_state(node_id, "decommissioned")

    # ------------------------------------- commission lifecycle (M2)
    def decommission(self) -> bool:
        """Course pp.80–82: keylock refuses; otherwise warn, take the
        modules off scan, leave a placeholder, and the node reappears
        under Decommissioned Nodes with the default name."""
        controller = getattr(self.store, "controller", None)
        if controller is None or not self.commissioned:
            return False
        if getattr(controller, "keylock", False):
            if not is_headless():
                QMessageBox.warning(
                    self, "Keylock",
                    "The carrier keylock is LOCKED — decommission is "
                    "refused. Operation continues untouched; turn the "
                    "key in Controller Diagnostics.")
            return False
        if not is_headless():
            proceed = QMessageBox.warning(
                self, "Production System Warning",
                f"Decommission {getattr(controller, 'name', '?')}?\n\n"
                "Every module goes off scan and every input reports "
                "Bad until the node is commissioned again. A "
                "placeholder keeps the configuration.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No) == QMessageBox.Yes
            if not proceed:
                return False
        executive = self.designer_window.designer \
            .controller_executive()
        for runtime in list(executive.online_runtimes()):
            try:
                runtime.go_offline()
            except Exception:                       # noqa: BLE001
                pass
        self.commissioned = False
        self.io_sensed = False
        if not self._primary_node_id or not self._set_project_controller_state(
                self._primary_node_id, "decommissioned"):
            self.refresh()
        return True

    def commission(self, name: str | None = None,
                   description: str | None = None,
                   auto_sense: bool | None = None) -> bool:
        """Course pp.72–76: name it under the naming rules, describe
        it, then Auto-sense the I/O. Headless callers pass arguments;
        interactive ones get the Properties dialog."""
        controller = getattr(self.store, "controller", None)
        if controller is None or self.commissioned:
            return False
        current = getattr(controller, "name", "PK-CTLR-1")
        if name is None and not is_headless():
            dialog = QDialog(self)
            dialog.setWindowTitle("Commission Controller")
            form = QFormLayout(dialog)
            name_field = QLineEdit(current)
            form.addRow("Name", name_field)
            desc_field = QLineEdit(self.node_description)
            form.addRow("Description", desc_field)
            area_field = QComboBox()
            area_field.addItems([a.name for a
                                 in self._areas_on_disk()])
            area_field.setCurrentText(self.area.name)
            form.addRow("Alarms and Events area", area_field)
            redundant = QCheckBox("Node is redundant")
            redundant.setEnabled(False)
            redundant.setToolTip("Greyed out for a simplex "
                                 "controller.")
            form.addRow("", redundant)
            note = QLabel("Name: 16 characters or fewer, at least "
                          "one letter; only $, - or _ besides "
                          "letters and digits.")
            note.setWordWrap(True)
            form.addRow(note)
            buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                       | QDialogButtonBox.Cancel)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            form.addRow(buttons)
            if dialog.exec() != QDialog.Accepted:
                return False
            name = name_field.text().strip()
            description = desc_field.text().strip()
        name = current if name is None else name
        if not _valid_node_name(name):
            if not is_headless():
                QMessageBox.warning(
                    self, "Invalid name",
                    f"'{name}' breaks the node naming rules: 16 "
                    "characters or fewer, at least one letter, only "
                    "$, - or _ besides letters and digits.")
            return False
        controller.name = name
        if description is not None:
            self.node_description = description
        if auto_sense is None and not is_headless():
            auto_sense = QMessageBox.question(
                self, "Auto-sense I/O",
                "Scan the I/O sub-system and identify the attached "
                "channels now?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes) == QMessageBox.Yes
        self.commissioned = True
        self.io_sensed = bool(auto_sense)
        if not self._primary_node_id or not self._set_project_controller_state(
                self._primary_node_id, "commissioned",
                configured_name=name, description=description):
            self.refresh()
        return True

    def auto_sense(self) -> int:
        """Sense controller-native I/O without claiming EIOC signals."""
        self.io_sensed = True
        self.refresh()
        try:
            controller = getattr(self.store, "controller", None)
            return controller.dst_usage(self.store) if controller else 0
        except Exception:                           # noqa: BLE001
            return 0

    def identify(self) -> None:
        """Flash the lights. The LED blinks on the node's
        row for a few seconds — one object, two views."""
        self._identify_ticks = 7
        self._identify_timer.start(450)

    def _identify_tick(self) -> None:
        self._identify_ticks -= 1
        if self._identify_ticks <= 0:
            self._identify_ticks = 0
            self._identify_timer.stop()
        self.refresh()

    # -------------------------------------------------------- download
    def _total_download(self, verify: bool | None = None) -> dict:
        """Course pp.100–102: the confirm dialog with a REAL verify
        checkbox, then the staged checklist — bold while running,
        checked when done, stippled when skipped."""
        from .download import DownloadDialog, run_total_download
        if verify is None:
            verify = True
            if not is_headless():
                confirm = QDialog(self)
                confirm.setWindowTitle("Confirm Total Download")
                lay = QVBoxLayout(confirm)
                text = QLabel(
                    "Download the entire configuration for "
                    f"{getattr(getattr(self.store, 'controller', None), 'name', 'PK-CTLR-1')}?\n\n"
                    "It may not be desirable to perform a total "
                    "download on a running process.")
                text.setWordWrap(True)
                lay.addWidget(text)
                verify_box = QCheckBox("Verify the configuration")
                verify_box.setChecked(True)
                lay.addWidget(verify_box)
                buttons = QDialogButtonBox(QDialogButtonBox.Yes
                                           | QDialogButtonBox.No)
                buttons.accepted.connect(confirm.accept)
                buttons.rejected.connect(confirm.reject)
                lay.addWidget(buttons)
                if confirm.exec() != QDialog.Accepted:
                    return {"ok": False, "notes": ["cancelled"],
                            "states": []}
                verify = verify_box.isChecked()
        dialog = None
        if not is_headless():
            dialog = DownloadDialog(self)
            dialog.show()
        result = run_total_download(self, verify=verify,
                                    dialog=dialog)
        self._last_download = result
        return result

    # ----------------------------------------------------- context menus
    def _menu_for(self, payload):
        from azeo_control_trainer.core.presentation.menu_style import studio_menu
        kind, data = payload
        if kind == "module":
            name, path, module_kind, live = data
            menu = studio_menu(f"MODULE  {name}", module_kind)
            open_action = menu.addAction("Open")
            font = open_action.font()
            font.setBold(True)
            open_action.setFont(font)
            open_action.triggered.connect(
                lambda: self.open_module(name, path, live=live))
            if live:
                menu.addAction("Download").triggered.connect(
                    lambda: self._download_module(name, path))
            menu.addSeparator()
            menu.addAction("Copy name").triggered.connect(
                lambda: self._clipboard(name))
            return menu
        if kind == "configured_controller":
            name = str(data.get("name") or "Controller")
            model = str(data.get("model") or "PK100").upper()
            commissioning = data.get("commissioning") or {}
            state = str(commissioning.get("state") or "configured")
            menu = studio_menu(
                f"CONTROLLER  {name}",
                f"{model} · " + (
                    "decommissioned hardware; configuration retained"
                    if state == "decommissioned"
                    else "configured without assigned hardware"))
            if state == "decommissioned" and data.get("hardware_id"):
                open_action = menu.addAction("Commission…")
                open_action.triggered.connect(
                    lambda: self.commission_project_controller(
                        str(data.get("node_id") or "")))
            else:
                open_action = menu.addAction("Manage in Control Designer")
                open_action.triggered.connect(self.open_control_designer)
            font = open_action.font()
            font.setBold(True)
            open_action.setFont(font)
            menu.addSeparator()
            menu.addAction("Copy name").triggered.connect(
                lambda: self._clipboard(name))
            menu.addAction("Refresh\tF5").triggered.connect(self.refresh)
            return menu
        if kind == "project_controller":
            name = str(data.get("name") or "Controller")
            model = str(data.get("model") or "PK100").upper()
            node_id = str(data.get("node_id") or "")
            menu = studio_menu(
                f"CONTROLLER  {name}", f"{model} · commissioned")
            manage = menu.addAction("Manage in Control Designer")
            font = manage.font()
            font.setBold(True)
            manage.setFont(font)
            manage.triggered.connect(self.open_control_designer)
            menu.addSeparator()
            menu.addAction("Decommission…").triggered.connect(
                lambda: self.decommission_project_controller(node_id))
            menu.addSeparator()
            menu.addAction("Copy hardware ID").triggered.connect(
                lambda: self._clipboard(str(data.get("hardware_id") or "")))
            return menu
        if kind == "project_decommissioned_controller":
            name = str(data.get("name") or "Controller")
            node_id = str(data.get("node_id") or "")
            menu = studio_menu(
                f"PHYSICAL CONTROLLER  {name}", "Decommissioned")
            commission = menu.addAction("Commission…")
            font = commission.font()
            font.setBold(True)
            commission.setFont(font)
            commission.triggered.connect(
                lambda: self.commission_project_controller(node_id))
            menu.addSeparator()
            menu.addAction("Copy hardware ID").triggered.connect(
                lambda: self._clipboard(str(data.get("hardware_id") or "")))
            commissioning = data.get("commissioning") or {}
            if commissioning.get("address"):
                menu.addAction("Copy address").triggered.connect(
                    lambda: self._clipboard(
                        str(commissioning.get("address") or "")))
            return menu
        if kind == "virtual_io":
            name = str(data.get("name") or "Local Virtual I/O")
            driver = self._virtual_io_driver()
            health = self._virtual_io_health(driver, data)
            subtitle = str(health.get("state") or "stopped")
            menu = studio_menu(f"VIRTUAL I/O  {name}", subtitle)
            simulator = menu.addAction("Open Signal Simulator…")
            font = simulator.font()
            font.setBold(True)
            simulator.setFont(font)
            simulator.triggered.connect(self.open_virtual_io_simulator)
            menu.addSeparator()
            self._add_virtual_io_lifecycle_actions(menu)
            menu.addSeparator()
            provider = data.get("provider") or {}
            menu.addAction("Copy provider factory").triggered.connect(
                lambda: self._clipboard(str(provider.get("factory") or "")))
            menu.addAction("Copy output holder").triggered.connect(
                lambda: self._clipboard(str(data.get("source") or "")))
            menu.addAction("Refresh\tF5").triggered.connect(self.refresh)
            return menu
        if kind == "eioc":
            eioc = getattr(self.store, "eioc", None)
            name = getattr(eioc, "name", "EIOC-1")
            menu = studio_menu(f"EIOC  {name}", "External OPC UA client")
            configure = menu.addAction("Configure OPC UA Client…")
            font = configure.font()
            font.setBold(True)
            configure.setFont(font)
            configure.triggered.connect(self.open_eioc_config)
            menu.addSeparator()
            menu.addAction("Copy endpoint").triggered.connect(
                lambda: self._clipboard(getattr(eioc, "endpoint", "")))
            menu.addAction("Refresh\tF5").triggered.connect(self.refresh)
            return menu
        if kind == "controller":
            controller = getattr(self.store, "controller", None)
            name = getattr(controller, "name", "PK-CTLR-1")
            menu = studio_menu(f"CONTROLLER  {name}",
                               "Commissioned node")
            download = menu.addAction("Download ▸ Total Download…")
            font = download.font()
            font.setBold(True)
            download.setFont(font)
            download.triggered.connect(lambda: self._total_download())
            menu.addAction("Diagnose…").triggered.connect(
                self.open_diagnostics)
            menu.addAction("Controller Status…").triggered.connect(
                self.open_controller_status)
            menu.addSeparator()
            menu.addAction("Identify (flash lights)") \
                .triggered.connect(self.identify)
            menu.addAction("Auto-sense I/O") \
                .triggered.connect(self.auto_sense)
            menu.addSeparator()
            menu.addAction("Decommission…").triggered.connect(
                self.decommission)
            return menu
        if kind == "decommissioned_node":
            menu = studio_menu(f"NODE  {data}", "Decommissioned")
            commission = menu.addAction("Commission…")
            font = commission.font()
            font.setBold(True)
            commission.setFont(font)
            commission.triggered.connect(lambda: self.commission())
            menu.addAction("Identify (flash lights)") \
                .triggered.connect(self.identify)
            return menu
        if kind == "placeholder":
            menu = studio_menu("PLACEHOLDER", "Awaiting its node")
            menu.addAction("Commission from decommissioned node…") \
                .triggered.connect(lambda: self.commission())
            return menu
        if kind == "network":
            menu = studio_menu("CONTROL NETWORK", "Physical network")
            discover = menu.addAction("Discover Controllers…")
            font = discover.font()
            font.setBold(True)
            discover.setFont(font)
            discover.triggered.connect(self.open_controller_discovery)
            menu.addSeparator()
            menu.addAction("Download Control Network…") \
                .triggered.connect(lambda: self._total_download())
            menu.addSeparator()
            add_remote = menu.addAction("Add Remote Node…")
            add_remote.triggered.connect(
                lambda: self.add_remote_node())
            return menu
        if kind == "remote_node":
            remote = self._remote(data)
            menu = studio_menu(f"REMOTE NODE  {data}",
                               "Monitor and operate over OPC UA")
            rebrowse = menu.addAction("Rebrowse")
            font = rebrowse.font()
            font.setBold(True)
            rebrowse.setFont(font)
            rebrowse.triggered.connect(
                lambda: self.rebrowse_remote(data))
            menu.addAction("Properties…").triggered.connect(
                lambda: self.remote_properties(data))
            menu.addSeparator()
            menu.addAction("Copy endpoint").triggered.connect(
                lambda: self._clipboard(remote.endpoint
                                        if remote else ""))
            menu.addAction("Remove").triggered.connect(
                lambda: self.remove_remote_node(data))
            return menu
        if kind == "remote_module":
            node_name, module_name = data
            menu = studio_menu(f"REMOTE MODULE  {module_name}",
                               f"On {node_name}")
            menu.addAction("Copy path").triggered.connect(
                lambda: self._clipboard(module_name))
            note = menu.addAction(
                "Engineering at that node's own station")
            note.setEnabled(False)
            return menu
        if kind == "area":
            menu = studio_menu(f"AREA  {Path(data).name}",
                               "Plant area")
            menu.addAction("Open in Control Designer") \
                .triggered.connect(self.open_control_designer)
            return menu
        if kind == "field_tag":
            menu = studio_menu(f"DST  {data[0]}",
                               "Device Signal Tag")
            menu.addAction("Copy tag").triggered.connect(
                lambda: self._clipboard(data[0]))
            return menu
        if kind == "system":
            menu = studio_menu("AZEO_SYSTEM", "This station")
            administrator = menu.addAction("Project Administrator…")
            font = administrator.font()
            font.setBold(True)
            administrator.setFont(font)
            administrator.triggered.connect(self.open_project_administrator)
            menu.addSeparator()
            menu.addAction("Refresh\tF5").triggered.connect(
                self.refresh)
            menu.addAction("Total Download…").triggered.connect(
                lambda: self._total_download())
            menu.addSeparator()
            menu.addAction("About Azeo Explorer…").triggered.connect(
                self._about)
            return menu
        if kind == "strategies":
            menu = studio_menu("CONTROL STRATEGIES", "Plant areas")
            new_area = menu.addAction("New Area…")
            font = new_area.font()
            font.setBold(True)
            new_area.setFont(font)
            new_area.triggered.connect(lambda: self.new_area())
            menu.addSeparator()
            menu.addAction("Refresh\tF5").triggered.connect(
                self.refresh)
            return menu
        if kind == "lib_block":
            menu = studio_menu(f"FUNCTION BLOCK  {data}",
                               "Library block")
            insert = menu.addAction("Insert into open module")
            font = insert.font()
            font.setBold(True)
            insert.setFont(font)
            insert.triggered.connect(
                lambda: (self.open_control_designer(),
                         self.designer_window._insert_block(data)))
            menu.addSeparator()
            menu.addAction("Copy block type").triggered.connect(
                lambda: self._clipboard(data))
            return menu
        if kind == "lib_procedure_block":
            from azeo_control_trainer.core.procedures.library import block_for_token
            block = block_for_token(data)
            menu = studio_menu(f"PROCEDURE BLOCK  {block.label}",
                               f"{block.block_id} · {block.version}")
            open_action = menu.addAction("Open in PA Designer")
            menu.setDefaultAction(open_action)
            open_action.triggered.connect(
                lambda: self.open_procedure_block(data))
            menu.addAction("Insert into current procedure").triggered.connect(
                lambda: self.open_procedure_block(data, insert=True))
            menu.addSeparator()
            menu.addAction("Block Help").triggered.connect(
                lambda: self.show_procedure_block_help(data))
            menu.addAction("Copy block identity").triggered.connect(
                lambda: self._clipboard(block.block_id))
            return menu
        if kind in ("lib_procedure_blocks", "lib_procedure_category"):
            menu = studio_menu("PROCEDURE BLOCKS", "PA Designer library")
            menu.addAction("Open PA Designer").triggered.connect(
                lambda: self.open_procedure_block())
            return menu
        if kind == "lib_pvm":
            from azeo_control_trainer.core.hmi.pvms import registry as pvm_registry
            cls = pvm_registry.get(*data)
            name = cls.__name__ if cls else str(data)
            menu = studio_menu(f"PVM CLASS  {name}",
                               f"{data[0]} / {data[1]}")
            config = menu.addAction("Configuration…")
            font = config.font()
            font.setBold(True)
            config.setFont(font)
            config.triggered.connect(
                lambda: self.open_pvm_config(name))
            menu.addSeparator()
            menu.addAction("Copy class name").triggered.connect(
                lambda: self._clipboard(name))
            return menu
        if kind in ("library", "lib_blocks", "lib_category"):
            menu = studio_menu("LIBRARY", "Function blocks, procedure blocks and PVMs")
            menu.addAction("Open Control Designer").triggered.connect(
                self.open_control_designer)
            menu.addAction("Open Graphics Designer").triggered.connect(
                self.open_graphics_designer)
            menu.addAction("Open PA Designer").triggered.connect(
                lambda: self.open_procedure_block())
            return menu
        if kind == "lib_pvms":
            menu = studio_menu("PVM CLASSES", "The display library")
            menu.addAction("Open Graphics Designer").triggered.connect(
                self.open_graphics_designer)
            menu.addAction("PVM Configuration Designer…") \
                .triggered.connect(lambda: self.open_pvm_config())
            return menu
        if kind == "physical":
            menu = studio_menu("PHYSICAL NETWORK", "Nodes and I/O")
            menu.addAction("Download Control Network…") \
                .triggered.connect(lambda: self._total_download())
            return menu
        if kind == "decommissioned":
            menu = studio_menu("DECOMMISSIONED NODES",
                               "Non-active network members")
            discover = menu.addAction("Discover Controllers…")
            font = discover.font()
            font.setBold(True)
            discover.setFont(font)
            discover.triggered.connect(self.open_controller_discovery)
            menu.addSeparator()
            refresh = menu.addAction("Refresh\tF5")
            refresh.triggered.connect(self.refresh)
            return menu
        if kind == "assigned_modules":
            menu = studio_menu("ASSIGNED MODULES", "On scan now")
            menu.addAction("Total Download…").triggered.connect(
                lambda: self._total_download())
            return menu
        if kind in ("assigned_io", "native_io"):
            title = "NATIVE I/O" if kind == "native_io" else "ASSIGNED I/O"
            menu = studio_menu(title, "Cards and channels")
            sense = menu.addAction("Auto-sense I/O")
            font = sense.font()
            font.setBold(True)
            sense.setFont(font)
            sense.triggered.connect(self.auto_sense)
            return menu
        if kind == "io_card":
            menu = studio_menu(f"I/O CARD  {data}", "Channels")
            menu.addAction("Enable all channels").triggered.connect(
                lambda: self._card_enable(data, True))
            menu.addAction("Disable all channels").triggered.connect(
                lambda: self._card_enable(data, False))
            return menu
        if kind == "assigned_module":
            menu = studio_menu(f"MODULE  {data}", "On scan")
            open_action = menu.addAction("Open")
            font = open_action.font()
            font.setBold(True)
            open_action.setFont(font)
            open_action.triggered.connect(
                lambda: self._activate_assigned(data))
            return menu
        if kind == "alarms":
            assigned = self.area.name in self.assigned_areas
            menu = studio_menu("ALARMS AND EVENTS",
                               "Workstation subsystem")
            toggle = menu.addAction(
                "Remove this area from this workstation"
                if assigned
                else "Assign this area to this workstation")
            font = toggle.font()
            font.setBold(True)
            toggle.setFont(font)
            toggle.triggered.connect(
                lambda: self.set_area_assigned(not assigned))
            menu.addSeparator()
            menu.addAction("Refresh\tF5").triggered.connect(
                self.refresh)
            return menu
        if kind == "unit":
            unit_name = data.get("name", "?") \
                if isinstance(data, dict) else str(data)
            menu = studio_menu(f"UNIT  {unit_name}",
                               "Module hierarchy and alarm consolidation")
            properties = menu.addAction("Properties…")
            font = properties.font()
            font.setBold(True)
            properties.setFont(font)
            properties.triggered.connect(
                lambda: self.unit_properties(data))
            menu.addSeparator()
            menu.addAction("Copy unit name").triggered.connect(
                lambda: self._clipboard(unit_name))
            return menu
        if kind == "historian":
            menu = studio_menu("CONTINUOUS HISTORIAN",
                               "Derived from the loaded modules")
            properties = menu.addAction("Properties…")
            font = properties.font()
            font.setBold(True)
            properties.setFont(font)
            properties.triggered.connect(self.historian_properties)
            menu.addAction("Export dataset (CSV)…") \
                .triggered.connect(
                    lambda: self.export_historian_csv())
            menu.addSeparator()
            menu.addAction("Refresh\tF5").triggered.connect(
                self.refresh)
            return menu
        if kind == "hist_tag":
            menu = studio_menu(f"HISTORY  {data}",
                               "Historized parameter")
            trend = menu.addAction("Open trend…")
            font = trend.font()
            font.setBold(True)
            trend.setFont(font)
            trend.triggered.connect(
                lambda: self.designer_window
                ._open_historian_popup(data))
            menu.addSeparator()
            menu.addAction("Copy tag").triggered.connect(
                lambda: self._clipboard(data))
            return menu
        if kind == "channel":
            menu = studio_menu(f"CHANNEL  {data.card} {data.label}",
                               data.channel_type)
            properties = menu.addAction("Properties…")
            font = properties.font()
            font.setBold(True)
            properties.setFont(font)
            properties.triggered.connect(
                lambda: self.channel_properties(data))
            toggle = menu.addAction(
                "Disable" if data.enabled else "Enable")
            toggle.triggered.connect(
                lambda: self.set_channel(data.tag,
                                         enabled=not data.enabled))
            menu.addSeparator()
            menu.addAction("Copy device tag").triggered.connect(
                lambda: self._clipboard(data.device_tag))
            return menu
        return None

    def _generic_menu(self, tree: bool):
        """Empty space still answers — commercial shells never shrug."""
        from azeo_control_trainer.core.presentation.menu_style import studio_menu
        menu = studio_menu("AZEO EXPLORER", "System view")
        menu.addAction("Refresh\tF5").triggered.connect(self.refresh)
        if tree:
            menu.addSeparator()
            menu.addAction("Expand all").triggered.connect(
                self.tree.expandAll)
            menu.addAction("Collapse all").triggered.connect(
                self.tree.collapseAll)
        return menu

    def _tree_menu(self, pos) -> None:
        payload = self._payload_of(self.tree.itemAt(pos))
        menu = (self._menu_for(payload) if payload else None) \
            or self._generic_menu(tree=True)
        self._context_menu = menu
        if not is_headless():
            menu.exec_transient(self.tree.mapToGlobal(pos))

    def _contents_menu(self, pos) -> None:
        payload = self._payload_of(self.contents.itemAt(pos))
        menu = (self._menu_for(payload) if payload else None) \
            or self._generic_menu(tree=False)
        self._context_menu = menu
        if not is_headless():
            menu.exec_transient(self.contents.mapToGlobal(pos))

    def _card_enable(self, card_label: str, enabled: bool) -> None:
        for card in self._io_cards():
            if card.label == card_label:
                for channel in card.channels:
                    master = self._io_master()
                    if master is not None:
                        master.set_channel_enabled(channel.tag,
                                                   enabled)
        self.refresh()

    def _activate_assigned(self, module_name: str) -> None:
        for name, path, _kind in self._area_modules(self.area):
            if name == module_name:
                self.open_module(name, path, live=True)
                return

    def new_area(self, name: str | None = None) -> Path | None:
        """Workshop p.67: Control Strategies ▸ New Area. Creates the
        area folder with a minimal _project.json beside the others."""
        import json
        if name is None:
            name = "PLANT_AREA_A"
            if not is_headless():
                from PySide6.QtWidgets import QInputDialog
                name, ok = QInputDialog.getText(
                    self, "New Area", "Area name:", text=name)
                if not ok or not name:
                    return None
        name = name.strip()
        if not name or any(ch in name for ch in "\\/:*?\"<>|"):
            return None
        target = self.area.parent / name
        if target.exists():
            if not is_headless():
                QMessageBox.information(
                    self, "New Area",
                    f"An area named {name} already exists.")
            return None
        target.mkdir(parents=True)
        (target / "control").mkdir()
        project = {"areas": [{"name": name, "strategies": [],
                              "sfc_modules": [],
                              "equipment_modules": []}]}
        (target / "_project.json").write_text(
            json.dumps(project, indent=2) + "\n", encoding="utf-8",
            newline="\n")
        self.refresh()
        return target

    def _download_module(self, name: str, path: Path) -> None:
        tab = self.designer_window.designer
        index = tab._find_tab_by_path(str(path))
        if index >= 0:
            tab.go_online_single(index)
        self.refresh()

    def _clipboard(self, text: str) -> None:
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(text)

    def show_help(self) -> "QDialog":
        """The detailed Explorer help — the shared topic, written
        once in the Control Designer help book (same pattern as the SFC
        editor's F1)."""
        item = self.contents.currentItem() if self.contents.hasFocus() else self.tree.currentItem()
        payload = self._payload_of(item)
        if payload and payload[0] == "lib_procedure_block":
            return self.show_procedure_block_help(payload[1])
        from .help_content import EXPLORER_HELP
        from azeo_control_trainer.core.presentation.help_style import styled_help_html
        from azeo_control_trainer.core.presentation.product_information import (
            product_information_html,
        )
        from PySide6.QtWidgets import QTextBrowser

        dialog = QDialog(self)
        dialog.setWindowTitle("Azeo Explorer Help")
        dialog.resize(720, 560)
        lay = QVBoxLayout(dialog)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setHtml(styled_help_html(
            EXPLORER_HELP + product_information_html("explorer")))
        lay.addWidget(browser)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        lay.addWidget(buttons)
        self._help_dialog = dialog
        if not is_headless():
            dialog.show()
        return dialog

    def _about(self):
        """Show the release and support identity for this Explorer session."""
        from azeo_control_trainer.core.presentation.product_information import (
            ProductAboutDialog,
        )

        dialog = getattr(self, "_about_dialog", None)
        if dialog is None:
            controller = getattr(self.store, "controller", None)
            dialog = ProductAboutDialog(
                "explorer",
                self,
                context_rows=(
                    ("Active project / area", self.area.name),
                    ("Project location", str(self.area.resolve())),
                    ("Controller", getattr(controller, "name", "Not configured")),
                ),
            )
            self._about_dialog = dialog
        dialog.show()
        if not is_headless():
            dialog.raise_()
            dialog.activateWindow()
        return dialog
