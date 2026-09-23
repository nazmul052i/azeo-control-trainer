"""The Assembler window itself — browser | canvas | configuration pane.

Display assembly: bind by path, pick a PVM class, choose a standard,
arrange, and author restricted TypeScript interaction scripts.

The three load-bearing behaviours from the wireframe:

- **The control browser is the only place a block path originates.**
  A PVM is placed by dragging a block, never by typing a tag.
- **Binding resolves on drag-enter, not release** — the ghost names
  the real tag before the mouse is let go.
- **The chooser is the exception.** One registered class for a type
  means silent placement; the choice is remembered per display.

Publish and revert go through `publishing.DisplayStore` — saving is
never publishing, and the publish dialog shows the diff in display
terms with the binding-health gate applied.
"""
from __future__ import annotations

from azeo_control_trainer.core.hmi.compatibility import (
    PVM_SCOPE_PREFIXES, USER_INSTANCE_ID_SEED, is_display_root,
)

from azeo_control_trainer.core.presentation.menu_style import retain_menu

import copy
import json
import logging
import uuid
from contextlib import nullcontext
from datetime import datetime

from azeo_control_trainer.core.presentation.authoring_controls import AuthoringComboBox
from azeo_control_trainer.core.presentation.authoring_dialog import (
    add_authoring_dialog_header,
    apply_compact_authoring_dialog,
    style_dialog_buttons,
)
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QGraphicsItem,
    QGraphicsRectItem, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from azeo_control_trainer.core.hmi.binding import BindingEngine, LiveGraphSource
from azeo_control_trainer.core.hmi.pvms.base import Pvm, primary_path, registry
from azeo_control_trainer.core.hmi.pvms.faceplate_actions import (
    resolve_associated_dcc,
    resolve_module_faceplate,
)
from azeo_control_trainer.core.hmi.pvms.elements import default_symbol_ports
from azeo_control_trainer.core.hmi.pvms.publishing import (
    DisplayLocked, DisplayStore, PvmDisplay, PublishRefused,
    display_diff,
)
from azeo_control_trainer.core.hmi.pvms.render import PvmFaceplateWidget
from azeo_control_trainer.core.hmi.pvms.roles import Tier, may_edit_displays
from azeo_control_trainer.core.hmi.theme.roles import Role
from azeo_control_trainer.core.hmi.theme.tokens import DEFAULT_THEME, THEMES
from .browser import ControlBrowser
from .canvas import _Canvas
from azeo_control_trainer.core.hmi.pvms.rendering.chrome import (
    DYNAMO_ROLES, PVM_H, PVM_W, MODE_EDIT, MODE_TEST, MODE_VIEW,
    ROLE_SIZES, STUDIO_THEME, WF,
)
from azeo_control_trainer.core.hmi.pvms.rendering.items import (
    PvmItem, PipeItem, StaticItem, item_document_data, item_group_id,
)
from .panes import _AlarmBanner, _ConfigPane, _Snippet, _WatchArea
from azeo_control_trainer.core.hmi.pvms.rendering.renderer import DisplayRenderer, pvm_from_dict
from .preview import PreviewSource
from .rulers import CanvasFrame
from azeo_control_trainer.core.hmi.pvms.variables import VariableSet
def _still_bound(widget) -> bool:
    """Whether a faceplate still holds its binding names.

    NOT `isVisible()`: a faceplate built headless is never shown and
    would read as closed while still holding every name. The honest
    question is whether its bindings are outstanding, which is
    exactly the condition that makes a second bind fail. Qt may also
    have deleted the C++ object under a live Python wrapper, so this
    has to survive a RuntimeError.
    """
    try:
        return bool(getattr(widget, "bound", None))
    except RuntimeError:
        return False

class PvmStudio(QWidget):
    """The Assembler window: browser | canvas | configuration pane."""

    geometryReadoutChanged = Signal(str)
    uiError = Signal(str)
    modeChanged = Signal(str)
    recoveryChanged = Signal()
    documentChanged = Signal()

    def __init__(self, graphs_provider, display_root,
                 display_name: str = "Overview",
                 tier: Tier = Tier.ASSEMBLER,
                 theme: str = STUDIO_THEME, hosted: bool = False,
                 parent=None, store=None):
        super().__init__(parent)
        self.tier = tier
        #: The library template this canvas edits, or empty for a display.
        self.template_session = ""
        self.hosted = hosted
        #: §8.6 mode machine. VIEW is the default: bindings live, no
        #: selection handles, no tools. The lock is acquired on entering
        #: EDIT — locking at open meant a browsing engineer blocked an
        #: editing one.
        self.mode = MODE_VIEW
        self.grid_visible = True
        self.snap_enabled = True
        self.smart_guides_enabled = True
        self.repeat_placement = False
        self._moving_selection = False
        self.rulers_visible = True
        #: The engineering camera starts fitted to the authored page and
        #: continues fitting while splitters/window chrome resize its viewport.
        #: A deliberate zoom or pan turns this off until Fit is invoked.
        self.auto_fit_enabled = True
        self._fitting_viewport = False
        self.zoom_percent = 100
        self._cursor_scene_pos = QPointF()
        #: Display-edit undo (§8.7): snapshots of the document. The
        #: stack must NEVER contain a process write — Ctrl+Z must not be
        #: able to move a setpoint — and here it structurally cannot:
        #: only to_dict() snapshots go in.
        self._undo_stack: list = []
        self._redo_stack: list = []
        self._gesture_open = False
        self._gesture_before = None
        self._gesture_redo_before = None
        self._gesture_unsaved_before = None
        self.interaction_mode = "view"
        self.isolated_layer: str | None = None
        #: True only while WE hold the store's single-writer lock. The
        #: lock FILE existing is not the same question — it may be
        #: someone else's, and inferring edit rights from it handed
        #: two engineers the same display.
        self._holds_lock = False
        #: Bumped by `touch_geometry()`; keys the crossover cache.
        self.crossover_generation = 0
        #: Conversion functions, kept across ticks and reloaded only
        #: when the file changes (`_apply_animations`).
        self._functions = None
        #: Library standards, for `Standard.` / `GL.` references.
        self._standards = None
        #: This display's own variables — `Dsp.<name>` reaches them.
        #: One value, shared by every property that references it,
        #: which is what makes a PVM reusable without scripting.
        self.variables = VariableSet(scope="Display")
        # Azeo's DL store lasts for the graphics session, not one event.
        self.script_store: dict = {}
        self.script_scopes: dict = {}
        self.theme = theme
        self.palette_roles = THEMES.get(theme, THEMES[DEFAULT_THEME])
        self.graphs_provider = graphs_provider
        #: Set by the tabbed Studio host. A data element names a display;
        #: the host owns tabs and navigation, so the canvas calls out.
        self.display_opener = None
        from azeo_control_trainer.core.procedures.hmi import ProcedureDesignSource
        from pathlib import Path
        from azeo_control_trainer.core.datastore.memory_tags import MemoryTagStore
        root = Path(display_root)
        memory = MemoryTagStore(root.parent.parent) if is_display_root(root) else None
        self.preview_source = PreviewSource(ProcedureDesignSource(LiveGraphSource(graphs_provider, memory=memory)))
        self.engine = BindingEngine(self.preview_source)
        # A template session supplies a store that writes the template
        # instead of a display draft (template_session.py).
        self.store = store if store is not None else DisplayStore(display_root)
        # Ruler guides are workstation authoring state, not operator ink.
        # Their sidecar is intentionally outside draft.json/revisions.
        from .authoring_state import load_guides
        self.authoring_guides = load_guides(display_root, display_name)
        #: The ONE thing that turns a display into pictures. The
        #: operator's view (`viewer.PvmDisplayView`) builds through the
        #: same object, which is why this canvas is WYSIWYG.
        self.renderer = DisplayRenderer(
            self.engine, self.palette_roles, config_root=display_root,
            action_handler=self._open_display_link,
            write_handler=self._write_user_entry,
            interaction_handler=self._execute_item_action,
            alarm_provider=lambda: self.engine._source.alarm_state.records())
        # User-imported SVG symbols survive across sessions.
        try:
            from pathlib import Path as _P

            from azeo_control_trainer.core.hmi.pvms.symbols import load_user_symbols
            load_user_symbols(_P(display_root) / "_library" / "svg")
        except Exception:                           # noqa: BLE001
            logging.getLogger(__name__).exception(
                "Could not load the project's imported SVG symbols")
        self.display = PvmDisplay(name=display_name)
        #: block_type -> chosen role, remembered PER DISPLAY (sheet 02D).
        self.role_choices: dict[str, str] = {}
        #: A drop may resolve to a native registered PVM or a visually
        #: authored class.  Keep that preference separate from role_choices
        #: so the established native role contract stays byte-compatible.
        self.visual_choices: dict[str, tuple[str, object]] = {}
        self.unsaved = False
        #: Open faceplates, keyed by (faceplate class, bound path) —
        #: the same space the ENGINE keys binding names by. Keyed by
        #: PVM id instead, two placements of one module would each
        #: try to bind `PIDFaceplate:FIC-101/PID1:pv_value`; a plain
        #: list let even a single PVM do it twice.
        self._faceplates: dict = {}
        self._user_faceplates: list = []
        self._tuning_trends: list = []
        self._process_history_views: list = []
        # Graphics Designer previews the same live graphs as the station.  Its
        # faceplate History icon therefore opens the shared Process History
        # View, not the short-lived tuning popup that merely looked similar.
        from azeo_control_trainer.core.hmi.history import ContinuousHistorian
        self.historian = ContinuousHistorian(
            None, resolver=self.preview_source)
        self.historian.configure_from(
            None, list(self.graphs_provider().values()))

        # No lock here — §8.6: acquire on entering EDIT, not at open.

        self.setWindowTitle(f"PVM Display Studio — {display_name}  "
                            f"[{tier.name}]")
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 0, 6, 4)
        root.setSpacing(4)

        bar = QHBoxLayout()
        self.status_label = QLabel(f"{tier.name} ●")
        bar.addWidget(self.status_label)
        bar.addStretch(1)
        for text, slot in (("Save", self.save_draft),
                           ("Publish…", self.open_publish),
                           ("History…", self.open_history)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            button.setEnabled(may_edit_displays(tier)
                              or text == "History…")
            bar.addWidget(button)
        if hosted:
            # The window's toolbar drives the lifecycle; one owner.
            self.status_label.hide()
            for i in range(bar.count()):
                widget = bar.itemAt(i).widget()
                if widget is not None:
                    widget.hide()
        root.addLayout(bar)

        split = QSplitter(Qt.Horizontal)
        if not hosted:
            self.browser = ControlBrowser(graphs_provider)
            split.addWidget(self.browser)
        else:
            self.browser = None     # the window's explorer owns it
        self.canvas = _Canvas(self)
        from .selection import SelectionController
        self.selection = SelectionController(self.canvas.scene(), self)
        self._selection_overlay = QGraphicsRectItem()
        self._selection_overlay.setAcceptedMouseButtons(Qt.NoButton)
        self._selection_overlay.setPen(QPen(QColor(WF["lapis"]), 1.2,
                                           Qt.DashLine))
        self._selection_overlay.setBrush(Qt.NoBrush)
        self._selection_overlay.setZValue(100000)
        self._selection_overlay.setVisible(False)
        self.canvas.scene().addItem(self._selection_overlay)
        self.canvas_frame = CanvasFrame(self.canvas)
        self.canvas_frame.set_rulers_visible(False)
        split.addWidget(self.canvas_frame)
        self.pane = _ConfigPane(self)
        # A 260 px property sheet forced long engineering names, paths and
        # units into the same cramped line. Keep the inspector stable, but
        # give its two-column forms the width of a real authoring sidebar.
        self.pane.setMinimumWidth(320)
        self.pane.setMaximumWidth(360)
        split.addWidget(self.pane)
        # Only the drawing viewport consumes width gained from the host. With
        # equal/default splitter weights, folding the project tree expanded
        # Graphics Configuration from 278 px to 412 px and gave a third of
        # the recovered canvas space to a form that did not need it.
        canvas_index = split.indexOf(self.canvas_frame)
        pane_index = split.indexOf(self.pane)
        split.setStretchFactor(canvas_index, 1)
        split.setStretchFactor(pane_index, 0)
        split.setCollapsible(pane_index, False)
        split.setSizes([220, 608, 316] if not hosted else [708, 340])
        self.workspace_splitter = split
        root.addWidget(split, 1)

        # The alarm banner strip along the canvas bottom (sheet 01).
        self.alarm_banner = _AlarmBanner()
        root.addWidget(self.alarm_banner)

        self.selection.changed.connect(
            self._on_selection)

        # Debounced crash recovery is not a Save: it lives in a separate
        # hidden checkpoint and is cleared only by an explicit draft save.
        self._recovery_timer = QTimer(self)
        self._recovery_timer.setSingleShot(True)
        self._recovery_timer.setInterval(2000)
        self._recovery_timer.timeout.connect(self._write_recovery)
        # Continuous editing must not postpone the checkpoint indefinitely.
        self._recovery_deadline_timer = QTimer(self)
        self._recovery_deadline_timer.setSingleShot(True)
        self._recovery_deadline_timer.setInterval(30_000)
        self._recovery_deadline_timer.timeout.connect(self._write_recovery)
        self.recovery_error = ""
        self.recovery_saved_at = ""
        self._closing = False
        existing = self.store.load_draft(display_name)
        self._draft_fingerprint = self.store.draft_fingerprint(display_name)
        recovery = self.store.load_recovery(display_name)
        restored_recovery = False
        if recovery is not None:
            from azeo_control_trainer.core.presentation.headless import is_headless
            if not is_headless():
                answer = QMessageBox.question(
                    self, "Recover unsaved display",
                    f"Graphics Designer found newer unsaved work for "
                    f"{display_name}. Restore it?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes,
                )
                if answer == QMessageBox.Yes:
                    existing = recovery
                    restored_recovery = True
                else:
                    self.store.clear_recovery(display_name)
        if existing is not None:
            for is_pvm, data in self.renderer.ordered_content(existing):
                if is_pvm:
                    self._restore(data)
                else:
                    self._restore_item(data)
            self.reroute_pipes()
            self.display.description = existing.description
            self.display.background = existing.background
            self.display.width = existing.width
            self.display.height = existing.height
            self.display.safe_margin = existing.safe_margin
            self.display.fit = existing.fit
            self.display.view_type = existing.view_type
            self.display.level = existing.level
            self.display.parent = existing.parent
            self.display.show_tag = existing.show_tag
            self.display.events = dict(existing.events)
            self.variables = VariableSet.from_list(existing.variables)
            self.display.work_in_progress = existing.work_in_progress
            self.display.wip_reason = existing.wip_reason
            self.display.schema_version = existing.schema_version
            self.display.stacking_order = list(existing.stacking_order)
            self.unsaved = restored_recovery
            self._sync_status()

        self.apply_display_frame()
        self.pane.show_pvm(None)        # start on the DISPLAY page
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(250)

    # ------------------------------------------------------------ binding
    def classes_for(self, block_type: str) -> dict:
        """{(role, variant): cls} for every placeable dynamo look —
        variants (the tank trend) are peers in the chooser."""
        out = {}
        for (bt, role, variant), cls in registry.all_classes().items():
            if bt == block_type and role in DYNAMO_ROLES:
                out[(role, variant)] = cls
        return out

    def authored_classes_for(self, block_type: str) -> dict:
        """Authored PVM classes whose typed drop target accepts the block.

        A drawing that merely contains a Control Tag-looking string is not a
        candidate.  The class must have a valid configuration document with
        one explicit primary drop target, otherwise the chooser would invite
        an engineer to create an unresolved object.
        """
        matches = {}
        library = self.user_library()
        library.reload()
        for name in library.names(definition_kind="pvm"):
            config = self._config_for_name(name)
            if config is None or config.issues():
                continue
            target = config.drop_target_for(block_type)
            if target is not None:
                matches[name] = (target, config)
        return matches

    @staticmethod
    def _authored_drop_choices(config, target, path: str) -> dict:
        """Populate conventional relative inputs in the same transaction.

        The primary target is authoritative.  The remaining names are the
        current paired-faceplate blueprint contract; filling them here makes
        the generated Loop-style class immediately live while custom classes
        remain free to expose only their own target.
        """
        module = path.rsplit("/", 1)[0] if "/" in path else path
        candidates = {
            "ModuleName": path,
            "ModulePath": module,
            "PVPath": f"{path}/PV",
            "SPPath": f"{path}/SP",
            "OUTPath": f"{path}/OUT",
        }
        public = {prop.name for prop in config.public_properties()}
        choices = {name: value for name, value in candidates.items()
                   if name in public}
        choices[target.name] = path
        return choices

    def _place_authored_for_block(self, name: str, path: str,
                                  block_type: str, x: float,
                                  y: float) -> int:
        match = self.authored_classes_for(block_type).get(name)
        if match is None:
            return 0
        target, config = match
        self.checkpoint()
        return self.place_user_pvm(
            name, x, y,
            choices=self._authored_drop_choices(config, target, path))

    def make_ghost(self, payload: dict):
        """Sheet 02 frame 2.1 — resolved before release."""
        ghost = QGraphicsRectItem(0, 0, PVM_W, PVM_H)
        ghost.setPen(QPen(QColor(self.palette_roles[Role.ACTION]),
                          1.2, Qt.DashLine))
        ghost.setOpacity(0.75)
        ghost.setZValue(10)
        from PySide6.QtWidgets import QGraphicsSimpleTextItem
        text = QGraphicsSimpleTextItem(
            f"{payload['path']}\n{payload['block_type']} · resolving",
            ghost)
        text.setPos(6, 6)
        return ghost

    def make_palette_ghost(self, payload: dict):
        """Build a truthful, non-interactive drag preview for a stencil."""
        kind = str(payload.get("type", ""))
        width, height = 112.0, 58.0
        title = str(payload.get("title", "Component"))
        if kind == "pvm":
            key = (str(payload.get("block_type", "")),
                   str(payload.get("role", "")),
                   str(payload.get("variant", "")))
            pvm_class = registry.all_classes().get(key)
            role = key[1]
            size = getattr(pvm_class, "DEFAULT_SIZE", None) \
                if pvm_class is not None else None
            width, height = size or ROLE_SIZES.get(role, (PVM_W, PVM_H))
            title = key[0] or "PVM"
        elif kind == "symbol":
            symbol = str(payload.get("symbol", ""))
            from azeo_control_trainer.core.hmi.pvms.symbols import aspect

            width, height = 110.0, 110.0 * aspect(symbol)
            data = {"kind": "symbol", "x": 0.0, "y": 0.0,
                    "w": width, "h": height, "symbol": symbol,
                    "ports": default_symbol_ports(symbol)}
            ghost = StaticItem(data, self.palette_roles)
            ghost.setOpacity(0.58)
            ghost.setAcceptedMouseButtons(Qt.NoButton)
            ghost.setFlag(QGraphicsItem.ItemIsMovable, False)
            ghost.setFlag(QGraphicsItem.ItemIsSelectable, False)
            ghost.setFlag(QGraphicsItem.ItemSendsGeometryChanges, False)
            ghost._authoring_preview = True
            ghost.setZValue(10)
            return ghost
        elif kind == "stream_connector":
            from azeo_control_trainer.core.hmi.pvms.elements import stream_connector_properties

            direction = str(payload.get("direction", "incoming"))
            width, height = 150.0, 30.0
            data = {"kind": "stream_connector", "x": 0.0, "y": 0.0,
                    "w": width, "h": height,
                    "text": str(payload.get("label", title))}
            data.update(stream_connector_properties(direction))
            ghost = StaticItem(data, self.palette_roles)
            ghost.setOpacity(0.58)
            ghost.setAcceptedMouseButtons(Qt.NoButton)
            ghost.setFlag(QGraphicsItem.ItemIsMovable, False)
            ghost.setFlag(QGraphicsItem.ItemIsSelectable, False)
            ghost.setFlag(QGraphicsItem.ItemSendsGeometryChanges, False)
            ghost._authoring_preview = True
            ghost.setZValue(10)
            return ghost
        elif kind == "special_symbol":
            from azeo_control_trainer.core.hmi.pvms.faceplate_icons import SPECIAL_SYMBOL_SIZES

            width, height = SPECIAL_SYMBOL_SIZES.get(
                str(payload.get("icon", "")), (32, 32))
            title = "Special symbol"
        elif kind == "user_pvm":
            entry = self.user_library().entries.get(
                str(payload.get("name", "")), {})
            width = float(entry.get("w", 120.0))
            height = float(entry.get("h", 60.0))
            title = str(payload.get("name", "User PVM"))
        ghost = QGraphicsRectItem(0, 0, float(width), float(height))
        ghost.setPen(QPen(QColor(self.palette_roles[Role.ACTION]),
                          1.2, Qt.DashLine))
        ghost.setBrush(QBrush(QColor(
            self.palette_roles[Role.SURFACE_PANEL])))
        ghost.setOpacity(0.72)
        ghost.setZValue(10)
        from PySide6.QtWidgets import QGraphicsSimpleTextItem
        label = QGraphicsSimpleTextItem(title, ghost)
        label.setBrush(QColor(self.palette_roles[Role.TEXT]))
        label.setPos(7, 6)
        return ghost

    def drop_palette_item(self, payload: dict, position: QPointF):
        """Place a dragged stencil centred exactly under the pointer."""
        if self.mode != MODE_EDIT:
            self.enter_edit()
        kind = str(payload.get("type", ""))
        placed = None
        if kind == "pvm":
            block_type = str(payload.get("block_type", ""))
            role = str(payload.get("role", ""))
            variant = str(payload.get("variant", ""))
            pvm_class = registry.all_classes().get(
                (block_type, role, variant))
            size = getattr(pvm_class, "DEFAULT_SIZE", None) \
                if pvm_class is not None else None
            width, height = size or ROLE_SIZES.get(
                role, (PVM_W, PVM_H))
            pvm = self.place_block(
                str(payload.get("path", "")), block_type, position.x() - width / 2,
                position.y() - height / 2, role=(role, variant))
            placed = next((item for item in self._items()
                           if pvm is not None and item.pvm.id == pvm.id),
                          None)
        elif kind == "symbol":
            symbol = str(payload.get("symbol", ""))
            from azeo_control_trainer.core.hmi.pvms.symbols import aspect

            width = 110.0
            height = width * aspect(symbol)
            placed = self.add_static(
                "symbol", x=position.x() - width / 2,
                y=position.y() - height / 2, w=width, h=height,
                symbol=symbol)
        elif kind == "stream_connector":
            direction = str(payload.get("direction", "incoming"))
            width, height = 150.0, 30.0
            placed = self.add_stream_connector(
                direction, position.x() - width / 2,
                position.y() - height / 2,
                text=str(payload.get("label", "") or ""),
                w=width, h=height)
        elif kind == "special_symbol":
            icon = str(payload.get("icon", "module_detail"))
            from azeo_control_trainer.core.hmi.pvms.faceplate_icons import SPECIAL_SYMBOL_SIZES

            width, height = SPECIAL_SYMBOL_SIZES.get(icon, (32, 32))
            placed = self.add_static(
                "special_symbol", x=position.x() - width / 2,
                y=position.y() - height / 2, w=width, h=height)
            placed.data["icon"] = icon
            placed.update()
        elif kind == "user_pvm":
            name = str(payload.get("name", ""))
            entry = self.user_library().entries.get(name, {})
            width = float(entry.get("w", 120.0))
            height = float(entry.get("h", 60.0))
            before = set(self.canvas.scene().items())
            self.place_user_pvm(
                name, position.x() - width / 2,
                position.y() - height / 2)
            added = [item for item in self.canvas.scene().items()
                     if item not in before and isinstance(
                         item, (StaticItem, PvmItem))]
            placed = added[0] if len(added) == 1 else None
            self.canvas.scene().clearSelection()
            for item in added:
                item.setSelected(True)
        if placed is not None:
            self.canvas.scene().clearSelection()
            placed.setSelected(True)
            if isinstance(placed, PvmItem):
                self.pane.show_pvm(placed)
            else:
                self.pane.show_item(placed)
        return placed

    def place_block(self, path: str, block_type: str,
                    x: float, y: float, role: str | None = None):
        """Drop-to-bind. Zero dialogs in the common case; the chooser
        only when more than one class is registered for the type."""
        if self.mode != MODE_EDIT:
            self.enter_edit()               # DisplayLocked raises up
        classes = self.classes_for(block_type)
        authored = self.authored_classes_for(block_type) \
            if role is None else {}
        if not classes and not authored:
            QMessageBox.information(
                self, "No PVM class",
                f"No dynamo PVM class is registered for {block_type} — "
                "an Author registers classes; there is no blank widget "
                "to start from.") if self.isVisible() else None
            return None
        if authored:
            remembered = self.visual_choices.get(block_type)
            available = ({("native", key) for key in classes}
                         | {("authored", name) for name in authored})
            selected = remembered if remembered in available else None
            if selected is None:
                if len(available) == 1:
                    selected = next(iter(available))
                else:
                    selected = self._choose_block_visual(
                        block_type, classes, authored)
            if selected is None:
                return None
            kind, key = selected
            if kind == "authored":
                return self._place_authored_for_block(
                    str(key), path, block_type, x, y)
            role = key
        if isinstance(role, str):
            role = (role, "") if (role, "") in classes else next(
                (key for key in classes if key[0] == role), None)
        if role is None:
            role = self.role_choices.get(block_type)
        if role is None:
            role = next(iter(classes)) if len(classes) == 1 \
                else self._choose_role(block_type, classes)
            if role is None:
                return None
        pvm_cls = classes[role]
        self.checkpoint()
        from azeo_control_trainer.core.hmi.pvms.symbols import pvm_symbol
        if pvm_cls.DEFAULT_SIZE is not None:
            w, h = pvm_cls.DEFAULT_SIZE
        elif pvm_symbol(block_type) is not None:
            w, h = 84.0, 88.0           # symbol + tag + state text
        else:
            w, h = ROLE_SIZES.get(role[0], (PVM_W, PVM_H))
        pvm = pvm_cls().place(f"pvm_{uuid.uuid4().hex[:4]}",
                              standard="hphmi.controller",
                              x=x, y=y, w=w, h=h,
                              **{param: path if param == "path" else ""
                                 for param in pvm_cls.PARAMS})
        self._add_item(pvm)
        self.mark_unsaved()
        return pvm

    def _choose_block_visual(self, block_type: str, classes: dict,
                             authored: dict) -> tuple | None:
        """Choose between registered and visually authored classes."""
        from ..component_icons import block_icon, pvm_icon

        dialog = QDialog(self)
        dialog.setWindowTitle("Choose a PVM class")
        lay = QVBoxLayout(dialog)
        add_authoring_dialog_header(
            dialog,
            lay,
            "Choose a PVM class",
            f"Choose a compatible native or authored visual for {block_type}.",
        )
        listing = QListWidget()
        for (role, variant), cls in sorted(
                classes.items(), key=lambda entry: entry[0]):
            label = role.replace("dynamo_", "").title()
            if variant:
                label += f" · {variant}"
            item = QListWidgetItem(
                f"Native · {block_type} / {label} — {cls.display_name}")
            item.setData(Qt.UserRole, ("native", (role, variant)))
            item.setIcon(pvm_icon(cls))
            listing.addItem(item)
        for name in sorted(authored):
            item = QListWidgetItem(f"Authored · {name}")
            item.setData(Qt.UserRole, ("authored", name))
            item.setIcon(block_icon(block_type))
            listing.addItem(item)
        listing.setCurrentRow(0)
        lay.addWidget(listing)
        from PySide6.QtWidgets import QCheckBox
        remember = QCheckBox(
            f"Remember this choice for {block_type} on this display.")
        remember.setChecked(True)
        lay.addWidget(remember)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                   | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        style_dialog_buttons(buttons, QDialogButtonBox.Ok)
        lay.addWidget(buttons)
        self._chooser = dialog
        from azeo_control_trainer.core.presentation.headless import is_headless
        accepted = True if is_headless() else \
            dialog.exec() == QDialog.Accepted
        if not accepted or listing.currentItem() is None:
            return None
        selected = listing.currentItem().data(Qt.UserRole)
        if remember.isChecked():
            self.visual_choices[block_type] = selected
        return selected

    def duplicate_selected(self) -> int:
        """Ctrl+D — copies offset one grid step, one undo entry."""
        if self.mode != MODE_EDIT:
            return 0
        return self.paste_payload(self.copy_selection_payload(), dx=16, dy=16)

    def _choose_role(self, block_type: str, classes: dict) -> str | None:
        from ..component_icons import pvm_icon

        dialog = QDialog(self)
        dialog.setWindowTitle("Choose a PVM class")
        lay = QVBoxLayout(dialog)
        add_authoring_dialog_header(
            dialog,
            lay,
            "Choose a PVM class",
            f"{len(classes)} compatible classes are registered for {block_type}.",
        )
        listing = QListWidget()
        for (role, variant), cls in classes.items():
            label = role.replace("dynamo_", "").title()
            if variant:
                label += f" · {variant}"
            item = QListWidgetItem(
                f"{block_type} / {label} — {cls.display_name}")
            item.setData(Qt.UserRole, (role, variant))
            item.setIcon(pvm_icon(cls))
            listing.addItem(item)
        listing.setCurrentRow(0)
        lay.addWidget(listing)
        from PySide6.QtWidgets import QCheckBox
        remember = QCheckBox(
            f"Remember this choice for {block_type} on this display.")
        remember.setChecked(True)
        lay.addWidget(remember)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                   | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        style_dialog_buttons(buttons, QDialogButtonBox.Ok)
        lay.addWidget(buttons)
        self._chooser = dialog
        from azeo_control_trainer.core.presentation.headless import is_headless
        accepted = True if is_headless() else \
            dialog.exec() == QDialog.Accepted
        if not accepted:
            return None
        role = listing.currentItem().data(Qt.UserRole)
        if remember.isChecked():
            self.role_choices[block_type] = role
        return role

    @staticmethod
    def _class_bindings(pvm_cls) -> tuple:
        """A class's binding specs, or none when this build registers
        no class for the placement (see `_add_item`)."""
        return tuple(getattr(pvm_cls, "bindings", ()) or ())

    def _primary_template(self, pvm_cls) -> tuple[str, str]:
        specs = self._class_bindings(pvm_cls)
        value_spec = next((s for s in specs
                           if not s.prop and not s.expr), None)
        mode_spec = next((s for s in specs if s.key == "mode"), None)
        return (value_spec.path if value_spec else "",
                mode_spec.path if mode_spec else "")

    def _pvm_config(self, pvm_cls, revision=""):
        return self.renderer.pvm_config(pvm_cls, revision)

    def _pvm_config_named(self, name: str):
        return self.renderer.pvm_config_named(name)

    def _add_item(self, pvm: Pvm) -> PvmItem:
        """One placement onto the editing canvas.

        The BUILDING is `DisplayRenderer`'s, shared with the operator
        view; everything this adds is editing — the movable and
        selectable flags, and the document sync. That is the whole
        WYSIWYG guarantee: the two surfaces cannot disagree about how a
        PVM looks because only one of them knows how to make one.
        """
        item = self.renderer.build_pvm(pvm)
        item.setFlag(QGraphicsItem.ItemIsMovable,
                     self.mode == MODE_EDIT)
        item.setFlag(QGraphicsItem.ItemIsSelectable,
                     self.mode == MODE_EDIT)
        self.canvas.scene().addItem(item)
        self._sync_document()
        return item

    def refresh_pvm_configuration(self, class_name: str) -> int:
        """Rebuild open instances after an Author saves their class.

        The display document is deliberately untouched: geometry, bindings
        and the named profile choice already belong to each placement. Only
        the class-owned rendering contract has changed. Rebuilding through
        the shared renderer also keeps Studio and operator rendering on the
        same path.
        """
        self.renderer.forget_config(class_name)
        replacements = []
        for item in list(self._items()):
            pvm_cls = registry.get(
                item.pvm.block_type, item.pvm.role, item.pvm.variant) \
                or registry.get(item.pvm.block_type, item.pvm.role) \
                or registry.get(item.pvm.block_type, "dynamo_compact")
            if pvm_cls is None or pvm_cls.__name__ != class_name:
                continue
            selected = item.isSelected()
            self.renderer.unbind(item)
            self.canvas.scene().removeItem(item)
            replacement = self.renderer.build_pvm(item.pvm)
            replacement.setFlag(QGraphicsItem.ItemIsMovable,
                                self.mode == MODE_EDIT)
            replacement.setFlag(QGraphicsItem.ItemIsSelectable,
                                self.mode == MODE_EDIT)
            self.canvas.scene().addItem(replacement)
            replacement.setSelected(selected)
            replacements.append(replacement)
        if replacements:
            self.reroute_pipes()
            self.canvas.viewport().update()
        return len(replacements)

    def relink_selected(self, block_type: str, role: str,
                        variant: str = "") -> int:
        """Relink PVM (working-pvms p.22): re-point every selected
        instance at a different class. Geometry, params, label and
        CHOICES all carry over by name; choices the target class's
        configuration does not declare stay in the record, inert —
        relink back and they take effect again. One undo step."""
        target = registry.get(block_type, role, variant)
        if target is None:
            return 0
        selected = [i for i in self._items() if i.isSelected()]
        if not selected:
            return 0
        self.enter_edit()
        self.checkpoint()
        count = 0
        for item in selected:
            new_pvm = Pvm(**{**item.pvm.__dict__,
                             "block_type": block_type, "role": role,
                             "variant": variant})
            for binding in (item.binding, item.mode_binding,
                            *item.rows.values()):
                if binding is not None:
                    self.engine.unbind(binding)
            self.canvas.scene().removeItem(item)
            self._add_item(new_pvm)
            count += 1
        self.mark_unsaved()
        self.reroute_pipes()
        return count

    def path_resolves(self, path: str, block_type: str = "") -> bool:
        """The p.30 verification: does MODULE/BLOCK exist in the
        loaded configuration? Blue solid when it does, red when it
        does not — a typo, or the module is not in the database."""
        if not path or "/" not in path:
            return False
        module, block = path.split("/", 1)
        try:
            graph = self.engine._source._graphs().get(module)
        except Exception:                           # noqa: BLE001
            graph = None
        if graph is None:
            return False
        return any(b.instance_name == block and (not block_type or b.block_type == block_type)
                   for b in graph.blocks.values())

    def set_pvm_path(self, item: PvmItem, path: str, parameter: str = "path") -> PvmItem:
        """The Control Tag changed (typed and verified, or browsed) —
        an engineering act, so it REBINDS, same as a Selection flip."""
        cls = registry.get(item.pvm.block_type, item.pvm.role, item.pvm.variant)
        if cls is None or parameter not in cls.PARAMS:
            raise ValueError("Unknown PVM tag parameter")
        expected = getattr(cls, "PARAM_TYPES", {}).get(parameter, "")
        if expected and path and not self.path_resolves(path, expected):
            raise ValueError(f"{parameter} must reference a {expected} block")
        self.enter_edit()
        self.checkpoint()
        params = dict(item.pvm.params)
        params[parameter] = path
        new_pvm = Pvm(**{**item.pvm.__dict__, "params": params})
        for binding in (item.binding, item.mode_binding,
                        *item.rows.values()):
            if binding is not None:
                self.engine.unbind(binding)
        self.canvas.scene().removeItem(item)
        replacement = self._add_item(new_pvm)
        self.mark_unsaved()
        self.reroute_pipes()
        return replacement

    def set_pvm_choice(self, item: PvmItem, name: str,
                       option: str) -> PvmItem:
        """A Selection choice changed — an engineering act, so it
        REBINDS (the binding rule: the poll hot path never composes a
        string). The placement records only the named option; values
        resolve through the class's configuration."""
        self.checkpoint()
        pvm_cls = registry.get(item.pvm.block_type, item.pvm.role,
                               item.pvm.variant)
        config = self._pvm_config(pvm_cls, item.pvm.class_revision) if pvm_cls else None
        choices = dict(item.pvm.choices)
        default = ""
        if config is not None:
            prop = config.property(name)
            default = prop.default if prop else ""
        if option == default:
            choices.pop(name, None)     # absent-when-default
        else:
            choices[name] = option
        new_pvm = Pvm(**{**item.pvm.__dict__, "choices": choices})
        for binding in (item.binding, item.mode_binding,
                        *item.rows.values()):
            if binding is not None:
                self.engine.unbind(binding)
        self.canvas.scene().removeItem(item)
        replacement = self._add_item(new_pvm)
        self.mark_unsaved()
        self.reroute_pipes()
        return replacement

    def _restore(self, pvm_dict: dict) -> PvmItem:
        # Through the shared reader: the operator view loads the same
        # stored format, and two readings of one format is two chances
        # to read it differently.
        return self._add_item(pvm_from_dict(pvm_dict))

    def set_pvm_rotation(self, item: PvmItem, rot: float) -> PvmItem:
        """Quarter-turn a placement. Geometry, so the placement owns
        it (I7); the item rebuilds so anchors, pipes and the painter
        lookup all follow."""
        self.checkpoint()
        new_pvm = Pvm(**{**item.pvm.__dict__, "rot": rot % 360})
        for binding in (item.binding, item.mode_binding,
                        *item.rows.values()):
            if binding is not None:
                self.engine.unbind(binding)
        self.canvas.scene().removeItem(item)
        replacement = self._add_item(new_pvm)
        self.mark_unsaved()
        self.reroute_pipes()
        return replacement

    def rotate_selected(self, delta: float = 90.0) -> list:
        """Rotate every selected PVM and drawing item by delta —
        the ribbon's Rotate 90° and the context menu both land
        here."""
        rotated = []
        for item in [i for i in self._items() if i.isSelected()]:
            rotated.append(self.set_pvm_rotation(
                item, ((item.pvm.rot or 0) + delta) % 360))
        statics = [i for i in self._static_items() if i.isSelected()]
        if statics:
            self.checkpoint()
            for item in statics:
                item.data["rot"] = \
                    (float(item.data.get("rot", 0)) + delta) % 360
                item.apply_rotation()
                rotated.append(item)
            self.reroute_pipes()
            self.mark_unsaved()
        for item in rotated:
            item.setSelected(True)
        return rotated

    def _items(self) -> list:
        return [i for i in self._document_items()
                if isinstance(i, PvmItem)]

    def _static_items(self) -> list:
        return [i for i in self._document_items()
                if isinstance(i, StaticItem)]

    def _document_items(self, order=Qt.DescendingOrder):
        """Cursor previews share the renderer, never the saved document."""
        items = self.canvas.scene().items(order)
        placement = getattr(self, "_place_ghost", None)
        dragged = self.canvas._ghost
        if placement is None and dragged is None:
            return items
        return [item for item in items if item is not placement and item is not dragged]

    def _pipe_items(self) -> list:
        return [i for i in self.canvas.scene().items()
                if isinstance(i, PipeItem)]

    def _groupable_items(self) -> list:
        """All authored objects that may share one logical group."""
        pvms, statics, pipes = self._scene_buckets()
        return pvms + statics + pipes

    def _scene_buckets(self) -> tuple[list, list, list]:
        """(pvms, statics, pipes) from ONE walk of the scene.

        Every mouse-move of a drag lands in `reroute_pipes`, and
        resolving each pipe's endpoints by re-walking the scene made
        that O(pipes x items): 62 ms per move event at 120 objects,
        which is a 16 fps drag before anything is repainted.
        """
        pvms, statics, pipes = [], [], []
        for item in self._document_items():
            if isinstance(item, PvmItem):
                pvms.append(item)
            elif isinstance(item, PipeItem):
                pipes.append(item)
            elif isinstance(item, StaticItem):
                statics.append(item)
        return pvms, statics, pipes

    def _drawing_data(self) -> list:
        return [item.data for item in self._document_items(Qt.AscendingOrder)
                if isinstance(item, (StaticItem, PipeItem))]

    def _restore_item(self, item_dict: dict):
        """Rebuild one drawing item from the document — no checkpoint,
        no mode change: restoring is not editing."""
        item = self.renderer.build_drawing(item_dict)
        self.canvas.scene().addItem(item)
        editable = self.mode == MODE_EDIT
        item.setFlag(QGraphicsItem.ItemIsMovable,
                     editable and not isinstance(item, PipeItem))
        item.setFlag(QGraphicsItem.ItemIsSelectable, editable)
        return item

    def rebind_static(self, item: StaticItem) -> StaticItem:
        """Apply edited Data/Display Link configuration immediately."""
        self.renderer.configure_drawing(item)
        item.update()
        self._sync_status()
        return item

    def _open_display_link(self, target: str) -> bool:
        """Delegate navigation without teaching a canvas about tabs."""
        if self.mode == MODE_EDIT or self.display_opener is None:
            return False
        return self.display_opener(target) is not None

    def _execute_item_action(self, action: dict, item=None) -> bool:
        """Execute the manual's built-in interaction actions in VIEW/TEST."""
        from azeo_control_trainer.core.hmi.pvms.elements import (
            ADD_TO_WATCH, OPEN_DETAIL, OPEN_DISPLAY, OPEN_FACEPLATE,
            OPEN_USER_FACEPLATE, SCRIPT, SHOW_TOOLTIP, WRITE_VALUE,
        )

        if self.mode == MODE_EDIT:
            return False
        kind, target = action.get("kind"), str(action.get("target", ""))
        if kind == SCRIPT:
            return self.run_script(str(action.get("source", "")), item).ok
        if kind == OPEN_DISPLAY:
            return self._open_display_link(target)
        if kind in {OPEN_USER_FACEPLATE, "open_user_detail"}:
            return self.open_user_faceplate(target, item) is not None
        if kind == WRITE_VALUE:
            return bool(self._write_user_entry(
                target, action.get("value")).success)
        if kind == SHOW_TOOLTIP and item is not None:
            item.setToolTip(target)
            return True
        if kind == ADD_TO_WATCH and item is not None \
                and getattr(item, "binding", None) is not None:
            if not hasattr(self, "watch_area"):
                self.watch_area = _WatchArea(self)
            self.watch_area.add_row(
                item.data.get("title") or target or item.data.get("id", ""),
                item.binding)
            return True
        pvm = next((one for one in self._items()
                    if one.pvm.id == target), None)
        if pvm is not None and kind in (OPEN_FACEPLATE, OPEN_DETAIL):
            self.open_faceplate(pvm.pvm)
            return True
        return False

    def open_user_faceplate(self, name: str, source_item=None):
        """Open a Studio-authored faceplate with its PVM instance inputs."""
        if not name:
            return None
        from azeo_control_trainer.core.hmi.pvms.user_faceplate import UserFaceplateView
        choices = dict(item_document_data(source_item).get("pvm_choices", {}))
        try:
            view = UserFaceplateView(
                name, self.store.root, self.graphs_provider,
                source=self.engine._source, choices=choices,
                theme=self.theme, parent=self,
                action_handler=lambda action, _view, item:
                self._execute_item_action(action, item),
                write_handler=self._write_user_entry,
                write_checker=self.engine.can_write)
        except (KeyError, OSError, ValueError):
            return None
        self._user_faceplates.append(view)
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not is_headless():
            view.show()
        return view

    def run_script(self, source: str, item=None, *, allow_writes=None):
        """Run an authored event against this display's live/test context."""
        from azeo_control_trainer.core.hmi.pvms.scripting import GraphicsScriptRuntime, ScriptContext

        for name, value in self.variable_values().items():
            self.script_scopes.setdefault(f"Dsp.{name}", value)
        standards = self._standards_store()
        for entry in getattr(standards, "entries", ()):
            self.script_scopes.setdefault(
                f"GL.{entry.get('name', '')}", entry.get("value"))
        context = ScriptContext(
            source=self.engine._source,
            alarm_state=self.engine._source.alarm_state,
            view=self.canvas,
            item=item,
            can_operate=(self.mode == MODE_VIEW if allow_writes is None
                         else bool(allow_writes)),
            open_display=self._open_display_link,
            session_store=self.script_store,
            scope_values=self.script_scopes,
        )
        answer = GraphicsScriptRuntime().run(source, context)
        if not answer.ok:
            import logging
            logging.getLogger("azeo.pvms.scripting").warning(
                "%s script failed: %s", self.display.name, answer.error)
        return answer

    def open_script_assistant(self) -> None:
        """Edit the selected element's click script, or display-open script."""
        from ..script_editor import edit_script

        selected = [item for item in self.canvas.scene().selectedItems()
                    if isinstance(item, StaticItem)]
        if selected:
            item = selected[0]
            actions = list(item.data.get("actions", ()))
            script = next((row for row in actions
                           if row.get("kind") == "script"), None)
            edited = edit_script(
                str((script or {}).get("source", "")),
                runner=lambda text: self.run_script(
                    text, item, allow_writes=False), parent=self)
            if edited is None:
                return
            self.checkpoint()
            if script is None:
                actions.append({"event": "click", "kind": "script",
                                "source": edited})
            else:
                script["source"] = edited
            item.data["actions"] = actions
            self.rebind_static(item)
        else:
            events = dict(self.display.events)
            actions = list(events.get("open", ()))
            script = next((row for row in actions
                           if row.get("kind") == "script"), None)
            edited = edit_script(
                str((script or {}).get("source", "")),
                runner=lambda text: self.run_script(
                    text, allow_writes=False), parent=self)
            if edited is None:
                return
            self.checkpoint()
            if script is None:
                actions.append({"event": "open", "kind": "script",
                                "source": edited})
            else:
                script["source"] = edited
            events["open"] = actions
            self.display.events = events
        self.mark_unsaved()

    def _write_user_entry(self, path: str, value):
        """Keep TEST a sandbox; popup controls remain live in VIEW/EDIT.

        EDIT changes canvas interaction, not the ownership of a separate
        faceplate window. Blocking that popup made its Limit and Tuning
        editors look functional while silently refusing every write.
        """
        if self.mode == MODE_TEST:
            from azeo_control_trainer.core.hmi.binding import WriteResult
            return WriteResult(False, "writes are disabled while testing")
        return self.engine.write(path, value)

    # -------------------------------------------------------------- pipes
    @staticmethod
    def _index_endpoints(pvms, statics) -> dict:
        """id -> item, for resolving a pipe's ends in one lookup."""
        index = {item.pvm.id: item for item in pvms}
        index.update({item.data.get("id", ""): item
                      for item in statics})
        return index

    def _endpoint(self, item_id: str):
        pvms, statics, _pipes = self._scene_buckets()
        return self._index_endpoints(pvms, statics).get(item_id)

    def _endpoint_id(self, item) -> str:
        return item.pvm.id if isinstance(item, PvmItem) \
            else item_document_data(item).get("id", "")

    @staticmethod
    def _pipe_end_exists(pipe, end: str, index: dict) -> bool:
        """Whether one connector end resolves to an object or loose point."""
        ident = str(pipe.data.get(end, "") or "")
        if ident:
            return ident in index
        point = pipe.data.get(f"{end}_point")
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            return False
        try:
            import math
            return all(math.isfinite(float(value)) for value in point)
        except (TypeError, ValueError):
            return False

    def _refresh_crossovers(self, statics=None, pipes=None) -> None:
        if statics is None or pipes is None:
            _pvms, statics, pipes = self._scene_buckets()
        if any(i.data.get("crossover") for i in statics + pipes):
            self.canvas.scene().update()

    def touch_geometry(self) -> None:
        """Invalidate the crossover segment cache. Anything that moves,
        resizes, adds or removes drawn geometry must call this — a
        stale cache would leave a moved line's crossover marks behind
        where the line used to be."""
        self.crossover_generation = \
            getattr(self, "crossover_generation", 0) + 1

    def reroute_pipes(self, only_for=None, *, only_ids=None, preview=False) -> None:
        """Re-lay the pipes. `only_for` restricts the work to the ones
        touching that item, which is what a drag needs: moving one box
        used to re-route every pipe on the display, on every mouse
        event."""
        if self._moving_selection:
            return
        _pvms, statics, pipes = self._scene_buckets()
        self.touch_geometry()
        from azeo_control_trainer.core.hmi.pvms.rendering.pipe_scene import route_scene_pipes
        route_scene_pipes(self.canvas.scene(), only_for=only_for, only_ids=only_ids, preview=preview)
        self._refresh_crossovers(statics, pipes)

    def _connected_pipe_ends(self, item) -> list[tuple]:
        """Return ``(pipe, this_end, other_end, other_item)`` records.

        A connected *run* is a property of the graph, not of whichever
        connector happens to be selected. Keeping this lookup here lets a
        valve, pump, vessel, or ordinary drawn symbol expose the same command.
        """
        ident = self._endpoint_id(item)
        pvms, statics, pipes = self._scene_buckets()
        index = self._index_endpoints(pvms, statics)
        result = []
        for pipe in pipes:
            for end, other_end in (("a", "b"), ("b", "a")):
                if str(pipe.data.get(end, "")) != ident:
                    continue
                other = index.get(str(pipe.data.get(other_end, "")))
                if other is not None and other is not item:
                    result.append((pipe, end, other_end, other))
        return result

    def _selected_run_item(self):
        """Resolve an inline item from either the item or its two pipes."""
        snapshot = self.selection.snapshot()
        if isinstance(snapshot.primary, (PvmItem, StaticItem)):
            return snapshot.primary
        direct = next((one for one in snapshot.items
                       if isinstance(one, (PvmItem, StaticItem))), None)
        if direct is not None:
            return direct
        selected_pipes = [one for one in snapshot.items
                          if isinstance(one, PipeItem)]
        if len(selected_pipes) < 2:
            return None
        counts = {}
        for pipe in selected_pipes:
            for end in ("a", "b"):
                ident = str(pipe.data.get(end, "") or "")
                if ident:
                    counts[ident] = counts.get(ident, 0) + 1
        common = next((ident for ident, count in counts.items()
                       if count >= 2), "")
        return self._endpoint(common) if common else None

    def connected_run_axis(self, item=None) -> str | None:
        """Axis that has connected equipment on both sides of ``item``."""
        from .selection import visual_scene_rect

        if item is None:
            item = self._selected_run_item()
        if item is None:
            return None
        records = self._connected_pipe_ends(item)
        if len(records) < 2:
            return None
        centre = visual_scene_rect(item).center()
        centres = [visual_scene_rect(record[3]).center()
                   for record in records]
        left = any(point.x() < centre.x() - 1.0 for point in centres)
        right = any(point.x() > centre.x() + 1.0 for point in centres)
        above = any(point.y() < centre.y() - 1.0 for point in centres)
        below = any(point.y() > centre.y() + 1.0 for point in centres)
        if left and right:
            return "horizontal"
        if above and below:
            return "vertical"
        return None

    def can_straighten_connected_run(self, item=None) -> bool:
        return self.connected_run_axis(item) is not None

    def selected_pipe_items(self) -> tuple:
        """Selected process connectors, in the engineer's selection order."""
        return tuple(one for one in self.selection.snapshot().items
                     if isinstance(one, PipeItem))

    def can_align_pipe_endpoints(self, pipes=None) -> bool:
        """Whether at least one selected connector has a shared axis."""
        from azeo_control_trainer.core.hmi.pvms.rendering.pipe_scene import port_anchor

        candidates = tuple(pipes) if pipes is not None \
            else self.selected_pipe_items()
        pvms, statics, _all_pipes = self._scene_buckets()
        index = self._index_endpoints(pvms, statics)
        for pipe in candidates:
            if not isinstance(pipe, PipeItem):
                continue
            a = index.get(str(pipe.data.get("a", "")))
            b = index.get(str(pipe.data.get("b", "")))
            if a is None or b is None:
                continue
            a_side = str(pipe.data.get("a_side", "e"))
            b_side = str(pipe.data.get("b_side", "w"))
            a_normal = port_anchor(a, a_side).normal
            b_normal = port_anchor(b, b_side).normal
            if ((a_normal in ("e", "w") and b_normal in ("e", "w"))
                    or (a_normal in ("n", "s")
                        and b_normal in ("n", "s"))):
                return True
        return False

    def align_pipe_endpoints(self, pipes=None, *, anchor_item=None) -> int:
        """Move endpoint equipment until selected pipe anchors share an axis.

        This is the AzeoPlantSimulator editor's ``Align endpoints (straight
        line)`` behavior, adapted to the canonical PVM display model.  The
        first endpoint (or ``anchor_item``) is the key object; only the other
        object's cross-axis coordinate changes.  A chain propagates the
        established axis from one selected pipe to the next.  Manual bends
        are cleared because leaving them behind would make a correctly
        aligned endpoint pair still look crooked.
        """
        from .selection import is_locked
        from azeo_control_trainer.core.hmi.pvms.rendering.pipe_scene import port_anchor

        candidates = list(pipes) if pipes is not None \
            else list(self.selected_pipe_items())
        candidates = [one for one in candidates
                      if isinstance(one, PipeItem)
                      and not bool(one.data.get("locked", False))]
        if not candidates:
            return 0
        pvms, statics, _all_pipes = self._scene_buckets()
        index = self._index_endpoints(pvms, statics)
        anchor_id = self._endpoint_id(anchor_item) \
            if anchor_item is not None else ""
        anchored = {anchor_id} if anchor_id else set()
        touched = 0
        state = self._bulk_begin()
        snap_before = self.snap_enabled
        self.snap_enabled = False
        try:
            for pipe in candidates:
                a_id = str(pipe.data.get("a", ""))
                b_id = str(pipe.data.get("b", ""))
                a, b = index.get(a_id), index.get(b_id)
                if a is None or b is None:
                    continue
                a_side = str(pipe.data.get("a_side", "e"))
                b_side = str(pipe.data.get("b_side", "w"))
                a_port = port_anchor(a, a_side)
                b_port = port_anchor(b, b_side)
                horizontal = (a_port.normal in ("e", "w")
                              and b_port.normal in ("e", "w"))
                vertical = (a_port.normal in ("n", "s")
                            and b_port.normal in ("n", "s"))
                if not horizontal and not vertical:
                    continue

                # Preserve the explicit key object. Otherwise propagate the
                # axis already established by an earlier pipe in this chain.
                if b_id in anchored and a_id not in anchored:
                    fixed, moving = b, a
                    fixed_port, moving_port = b_port, a_port
                    fixed_id, moving_id = b_id, a_id
                else:
                    fixed, moving = a, b
                    fixed_port, moving_port = a_port, b_port
                    fixed_id, moving_id = a_id, b_id
                if anchor_id == b_id:
                    fixed, moving = b, a
                    fixed_port, moving_port = b_port, a_port
                    fixed_id, moving_id = b_id, a_id
                elif anchor_id == a_id:
                    fixed, moving = a, b
                    fixed_port, moving_port = a_port, b_port
                    fixed_id, moving_id = a_id, b_id

                if is_locked(moving):
                    if is_locked(fixed) or fixed_id in anchored:
                        continue
                    fixed, moving = moving, fixed
                    fixed_port, moving_port = moving_port, fixed_port
                    fixed_id, moving_id = moving_id, fixed_id
                if horizontal:
                    delta = QPointF(0.0,
                                    fixed_port.point.y
                                    - moving_port.point.y)
                else:
                    delta = QPointF(fixed_port.point.x
                                    - moving_port.point.x, 0.0)
                if abs(delta.x()) > 1e-9 or abs(delta.y()) > 1e-9:
                    moving.setPos(moving.pos() + delta)
                pipe.data.pop("route_mode", None)
                pipe.data.pop("route_points", None)
                pipe.data["auto"] = False
                anchored.update((fixed_id, moving_id))
                touched += 1
        finally:
            self.snap_enabled = snap_before
        changed = self._bulk_end(state)
        self.canvas.update_rulers()
        return touched if changed else 0

    @staticmethod
    def _run_outline_anchor(item, side: str, coordinate: float,
                            *, horizontal: bool) -> str:
        """Find a perimeter point on ``coordinate``, retaining real ink.

        Symbols expose an exact-axis resolver; the generic fallback uses a
        near-zero hit radius so an unrelated named port cannot steal the
        alignment. Either result remains a normalized, resize-safe token.
        """
        resolver = getattr(item, "connection_anchor_on_axis", None)
        if callable(resolver):
            return str(resolver(
                side, coordinate, horizontal=horizontal) or side)
        cardinal = item.anchor(side)
        probe = QPointF(cardinal.x(), coordinate) if horizontal \
            else QPointF(coordinate, cardinal.y())
        token = item.connection_anchor_at(probe, pixels=0.01,
                                          allow_inside=True)
        return str(token or side)

    def straighten_connected_run(self, item=None,
                                 axis: str = "auto") -> int:
        """Put pipes through an inline object on one continuous centerline.

        The selected inline object is the key object.  Its neighbours move
        only across the run axis until their authored SVG ports line up with
        it, matching AzeoPlantSimulator's straight-line endpoint command.
        Along-axis spacing is untouched, so process layout intent is retained.
        """
        from .selection import is_locked, visual_scene_rect

        if item is None:
            item = self._selected_run_item()
        if item is None:
            return 0
        inferred = self.connected_run_axis(item)
        chosen_axis = inferred if axis == "auto" else axis
        if chosen_axis not in {"horizontal", "vertical"}:
            return 0

        horizontal = chosen_axis == "horizontal"
        centre = visual_scene_rect(item).center()
        records = self._connected_pipe_ends(item)

        changes = []
        for pipe, this_end, other_end, other in records:
            other_centre = visual_scene_rect(other).center()
            if horizontal:
                delta = other_centre.x() - centre.x()
                if abs(delta) <= 1.0:
                    continue
                this_side = "e" if delta > 0 else "w"
                other_side = "w" if delta > 0 else "e"
            else:
                delta = other_centre.y() - centre.y()
                if abs(delta) <= 1.0:
                    continue
                this_side = "s" if delta > 0 else "n"
                other_side = "n" if delta > 0 else "s"
            changes.append((pipe, this_end, this_side,
                            other_end, other_side))

        if len(changes) < 2:
            return 0
        state = self._bulk_begin()
        snap_before = self.snap_enabled
        self.snap_enabled = False
        try:
            for pipe, this_end, this_side, other_end, other_side in changes:
                pipe.data[f"{this_end}_side"] = this_side
                pipe.data[f"{other_end}_side"] = other_side
                pipe.data["auto"] = False
                # Straighten is an explicit request to replace hand-drawn
                # bends; retaining waypoints would make the command inert.
                pipe.data.pop("route_mode", None)
                pipe.data.pop("route_points", None)
                other_id = str(pipe.data.get(other_end, ""))
                other = self._endpoint(other_id)
                if other is None or is_locked(other):
                    continue
                fixed = item.anchor(this_side)
                moving = other.anchor(other_side)
                offset = QPointF(0.0, fixed.y() - moving.y()) \
                    if horizontal else \
                    QPointF(fixed.x() - moving.x(), 0.0)
                if abs(offset.x()) > 1e-9 or abs(offset.y()) > 1e-9:
                    other.setPos(other.pos() + offset)
        finally:
            self.snap_enabled = snap_before
        changed = self._bulk_end(state)
        self.canvas.update_rulers()
        return len(changes) if changed else 0

    def renumber_pipe_anchors(self, item, removed: int) -> None:
        """Follow a removed connection point through the pipes on
        `item`: later indices shift down one, and the pipe that was on
        the point itself falls back to the east cardinal port rather than
        dangling on an index that now names somebody else."""
        item_id = self._endpoint_id(item)
        _pvms, _statics, pipes = self._scene_buckets()
        for pipe in pipes:
            for end in ("a", "b"):
                if pipe.data.get(end) != item_id:
                    continue
                side_key = f"{end}_side"
                current = str(pipe.data.get(side_key, ""))
                if not current.startswith("c"):
                    continue
                try:
                    number = int(current[1:])
                except ValueError:
                    continue
                if number == removed:
                    pipe.data[side_key] = "e"
                    pipe.data["auto"] = False
                elif number > removed:
                    pipe.data[side_key] = f"c{number - 1}"

    def drop_connection_point(self, item, side: str) -> bool:
        """The gesture behind 'Remove connection point': one undo
        step, the renumbering, and a reroute so the pipes move now."""
        if not side.startswith("c") or side not in item.anchor_sides():
            return False                    # nothing to remove
        self.checkpoint()
        if not item.remove_connection_point(side):
            return False
        self.reroute_pipes()
        self.mark_unsaved()
        return True

    def add_pipe(self, item_a, side_a: str, item_b,
                 side_b: str, *, point_a=None, point_b=None) -> PipeItem:
        """Create one routed connector with attached or loose endpoints.

        A loose endpoint is intentional authoring state, represented by a
        scene point.  This lets the Line tool and endpoint handles behave like
        modern diagrammers: pull into open space now, finish the connection
        later, without manufacturing an unrelated static stroke.
        """
        if self.mode != MODE_EDIT:
            self.enter_edit()
        self.checkpoint()
        # A process continuation has flow semantics independent of which end
        # the engineer happened to drag first. Incoming is always the source;
        # outgoing is always the destination. The small arrow then makes the
        # process direction immediately legible on the completed P&ID.
        def stream_direction(item):
            data = getattr(item, "data", {}) if item is not None else {}
            return str(data.get("stream_direction", "")) \
                if isinstance(data, dict) else ""

        if stream_direction(item_b) == "incoming" \
                or stream_direction(item_a) == "outgoing":
            item_a, item_b = item_b, item_a
            side_a, side_b = side_b, side_a
            point_a, point_b = point_b, point_a
        from azeo_control_trainer.core.hmi.pvms.rendering.pipe_scene import junction_side
        side_a = junction_side(item_a, side_a, item_b.anchor(side_b) if item_b is not None else point_b)
        side_b = junction_side(item_b, side_b, item_a.anchor(side_a) if item_a is not None else point_a)
        # Endpoint ports are authored geometry, including the four cardinal
        # handles. Obstacle routing remains automatic through `route_mode`,
        # but moving equipment must not replace the engineer's selected port.
        data = {"kind": "pipe", "auto": False,
                "crossover": "jump",
                "corner_radius": 7.0,
                "a": self._endpoint_id(item_a) if item_a is not None else "",
                "a_side": side_a,
                "b": self._endpoint_id(item_b) if item_b is not None else "",
                "b_side": side_b}
        if stream_direction(item_a) == "incoming" \
                or stream_direction(item_b) == "outgoing":
            data.update({"arrow_end": "filled_arrow",
                         "arrow_size": "small"})
        for end, item, point in (("a", item_a, point_a),
                                 ("b", item_b, point_b)):
            if item is not None:
                continue
            if point is None:
                raise ValueError(f"pipe endpoint {end} needs an item or point")
            data[f"{end}_point"] = [float(point.x()), float(point.y())]
        pipe = PipeItem(data, self.palette_roles)
        self.canvas.scene().addItem(pipe)
        from azeo_control_trainer.core.hmi.pvms.rendering.pipe_scene import route_pipe
        route_pipe(self.canvas.scene(), pipe, item_a, item_b)
        self.mark_unsaved()
        return pipe

    # -------------------------------------------------- click-to-place
    # ------------------------------------------------ freehand + eraser
    def arm_pencil(self) -> None:
        """Freehand: press, draw, release. The stroke is simplified on
        release (Ramer–Douglas–Peucker), because hundreds of raw
        samples are slow to draw and impossible to edit afterwards."""
        self.enter_edit()
        self.cancel_gestures()
        self._pencil_armed = True
        self.set_interaction_mode("draw")
        self._pencil_points = []
        self._pencil_preview = None
        self.canvas.setCursor(Qt.CrossCursor)

    def _pencil_press(self, scene_pos) -> bool:
        if not getattr(self, "_pencil_armed", False):
            return False
        self._pencil_points = [(scene_pos.x(), scene_pos.y())]
        return True

    def _pencil_drag(self, scene_pos) -> bool:
        if not getattr(self, "_pencil_armed", False) \
                or not self._pencil_points:
            return False
        previous = self._pencil_points[-1]
        import math as _math
        # One sample per 1.25 scene px, as the builder does — closer
        # samples carry no shape, only cost.
        if _math.hypot(scene_pos.x() - previous[0],
                       scene_pos.y() - previous[1]) < 1.25:
            return True
        self._pencil_points.append((scene_pos.x(), scene_pos.y()))
        from PySide6.QtWidgets import QGraphicsPathItem
        from azeo_control_trainer.core.hmi.pvms.shapes import smoothed_path
        if self._pencil_preview is None:
            self._pencil_preview = QGraphicsPathItem()
            self._pencil_preview.setPen(
                QPen(QColor(WF["lapis"]), 1.6))
            self._pencil_preview.setZValue(20)
            self.canvas.scene().addItem(self._pencil_preview)
        self._pencil_preview.setPath(
            smoothed_path(self._pencil_points))
        return True

    def _pencil_release(self, scene_pos):
        if not getattr(self, "_pencil_armed", False):
            return None
        self._pencil_drag(scene_pos)
        points = list(self._pencil_points)
        if self._pencil_preview is not None:
            self.canvas.scene().removeItem(self._pencil_preview)
            self._pencil_preview = None
        self._pencil_points = []
        self._pencil_armed = self.repeat_placement
        self._finish_drawing_tool()
        if len(points) < 2:
            return None
        from azeo_control_trainer.core.hmi.pvms.shapes import simplify_points
        points = simplify_points(points, 2.0)
        min_x = min(p[0] for p in points)
        min_y = min(p[1] for p in points)
        local = [[p[0] - min_x, p[1] - min_y] for p in points]
        # No checkpoint here: `add_static` takes one, and a second
        # snapshot made ONE stroke cost two Ctrl+Z — the first press
        # removing it and the second doing nothing visible.
        item = self.add_static("freehand", min_x, min_y,
                               w=max(p[0] for p in local) or 1,
                               h=max(p[1] for p in local) or 1)
        item.data["points"] = local
        item._refit_points()
        self.mark_unsaved()
        return item

    def arm_eraser(self) -> None:
        """Erase by dragging over objects. One undo step for the whole
        drag, however many objects it takes — the builder's rule, and
        the only one that makes an eraser usable."""
        self.enter_edit()
        self.cancel_gestures()
        self._eraser_armed = True
        self.set_interaction_mode("draw")
        self._eraser_checkpointed = False
        self.canvas.setCursor(Qt.ForbiddenCursor)

    def _eraser_at(self, scene_pos) -> bool:
        if not getattr(self, "_eraser_armed", False):
            return False
        hit = [i for i in self._static_items() + self._pipe_items()
               if not i.data.get("locked")
               and i.sceneBoundingRect().contains(scene_pos)]
        if not hit:
            return True
        if not self._eraser_checkpointed:
            self.checkpoint()
            self._eraser_checkpointed = True
        for item in hit:
            if isinstance(item, StaticItem):
                self._drop_pipes_touching(item.data.get("id"))
            self.canvas.scene().removeItem(item)
        self.mark_unsaved()
        return True

    def _drop_pipes_touching(self, item_id: str) -> None:
        """A pipe whose endpoint is gone is a dangling line — remove
        it with its endpoint rather than leave it pointing nowhere."""
        if not item_id:
            return
        for pipe in self._pipe_items():
            if item_id in (pipe.data.get("a"), pipe.data.get("b")):
                self.canvas.scene().removeItem(pipe)

    def disarm_eraser(self) -> None:
        self._eraser_armed = False
        self._eraser_checkpointed = False
        self.canvas.unsetCursor()

    def set_pan_mode(self, on: bool) -> None:
        """Compatibility entry point for the one interaction-mode machine."""
        if on:
            self.cancel_gestures()
        self.set_interaction_mode("pan" if on else "select")

    def set_interaction_mode(self, mode: str) -> None:
        """Set Select/Pan/Draw without competing QGraphicsView state."""
        from PySide6.QtWidgets import QGraphicsView
        mode = str(mode).casefold()
        if self.mode != MODE_EDIT:
            mode = "view"
        if mode not in ("select", "pan", "draw", "view"):
            mode = "select"
        self.interaction_mode = mode
        self.pan_active = mode == "pan"
        self._pan_mode = self.pan_active
        drag = QGraphicsView.RubberBandDrag if mode == "select" \
            else QGraphicsView.ScrollHandDrag if mode == "pan" \
            else QGraphicsView.NoDrag
        self.canvas.setDragMode(drag)
        if mode in ("draw", "pan"):
            self.canvas.setFocus()
        frame = getattr(self, "canvas_frame", None)
        if frame is not None:
            frame.update_tools()

    def _finish_drawing_tool(self) -> None:
        self.set_interaction_mode("draw" if self.repeat_placement else "select")
        self.canvas.setCursor(Qt.CrossCursor if self.repeat_placement else Qt.ArrowCursor)

    def cancel_gestures(self) -> None:
        """Escape: every armed tool disarms, every preview goes, and
        the editor is back to Select. One place, so a new tool cannot
        forget to be cancellable."""
        self.canvas.pointer_drag.reset()
        self._style_brush_armed = False
        self.cancel_place()
        self._line_armed = False
        self._line_start = None
        self._line_start_target = None
        self._line_end_target = None
        if getattr(self, "_line_preview", None) is not None:
            self.canvas.scene().removeItem(self._line_preview)
            self._line_preview = None
        self._pencil_armed = False
        self._pencil_points = []
        if getattr(self, "_pencil_preview", None) is not None:
            self.canvas.scene().removeItem(self._pencil_preview)
            self._pencil_preview = None
        self._drop_shape_preview()
        self._shape_armed = False
        self._shape_start = None
        self._drop_polyline_preview()
        self._polyline_armed = False
        self._polyline_points = []
        self._connect_preview(None)
        self.connect_armed = False
        self._connect_pending = None
        self.disarm_eraser()
        self.cancel_gesture()
        self.set_interaction_mode("select")
        self.canvas.unsetCursor()

    def nudge_selected(self, dx: float, dy: float) -> int:
        """Arrow keys move the selection 1 px, Shift+Arrow 10 px —
        one undo step per press, so a nudge can be taken back one
        press at a time."""
        items = [i for i in self._static_items() + self._items()
                 if i.isSelected()
                 and not (isinstance(i, StaticItem)
                          and i.data.get("locked"))]
        if not items:
            return 0
        self.checkpoint()
        for item in items:
            item.setPos(item.pos().x() + dx, item.pos().y() + dy)
        self.reroute_pipes()
        self.mark_unsaved()
        return len(items)

    def resize_selected(self, dw: float, dh: float) -> int:
        """Resize selected drawing elements about their centres.

        Azeo assigns Shift+Arrow to this operation. Point-edited strokes
        are excluded because their geometry is their vertices, not a box;
        aspect-locked symbols change the other dimension proportionally.
        """
        items = [item for item in self._static_items()
                 if item.isSelected() and not item.data.get("locked")
                 and not item.is_point_kind()]
        if not items:
            return 0
        self.checkpoint()
        for item in items:
            rect = item.rect()
            old_w, old_h = rect.width(), rect.height()
            min_w, min_h = item.minimum_size()
            new_w = max(min_w, old_w + 2.0 * dw)
            new_h = max(min_h, old_h + 2.0 * dh)
            if item.aspect_locked() and old_w > 0 and old_h > 0:
                ratio = old_w / old_h
                if dw:
                    new_h = max(min_h, new_w / ratio)
                elif dh:
                    new_w = max(min_w, new_h * ratio)
            centre = item.mapToScene(rect.center())
            item.prepareGeometryChange()
            item.data.update({"x": centre.x() - new_w / 2.0,
                              "y": centre.y() - new_h / 2.0,
                              "w": new_w, "h": new_h})
            item.setRect(0, 0, new_w, new_h)
            item.setPos(item.data["x"], item.data["y"])
            item.apply_rotation()
            item.update()
        self.reroute_pipes()
        self.mark_unsaved()
        return len(items)

    def set_selected_crossover(self, mode: str) -> int:
        """Apply continuous, Break or Jump to every selected open stroke.

        Crossings belong to the stroke drawn on top: selecting several lines
        is therefore a useful bulk operation, while closed shapes are ignored
        instead of being given a property their painter cannot honor.
        """
        from azeo_control_trainer.core.hmi.pvms.rendering.crossing import CROSSOVER_KINDS
        mode = mode if mode in ("gap", "jump") else ""
        items = [item for item in self._static_items() + self._pipe_items()
                 if item.isSelected()
                 and item.data.get("kind") in CROSSOVER_KINDS]
        if not items:
            return 0
        self.checkpoint()
        for item in items:
            if mode:
                item.data["crossover"] = mode
            else:
                item.data.pop("crossover", None)
            item.update()
        self.mark_unsaved()
        self.canvas.scene().update()
        if len(items) == 1:
            self.pane.show_item(items[0])
        return len(items)

    def arm_line(self) -> None:
        """The paper's line gesture: press sets the start, drag shows
        the line, release places it — SHIFT snaps to 45-degree
        increments (p.27's own tip)."""
        self.enter_edit()
        self.cancel_gestures()
        self._line_start = None
        self._line_start_target = None
        self._line_end_target = None
        self._line_armed = True
        self.set_interaction_mode("draw")
        self._line_preview = None
        self.canvas.setCursor(Qt.CrossCursor)

    def _line_press(self, scene_pos) -> bool:
        if not getattr(self, "_line_armed", False):
            return False
        # The Line tool doubles as the process connector when the gesture
        # begins on connectable artwork.  The target need not be selected;
        # the exact outline token is captured at the press coordinate.
        self._line_start_target = self._connect_target_at(scene_pos)
        self._line_start = self._line_start_target[0].anchor(
            self._line_start_target[1]) \
            if self._line_start_target is not None else QPointF(scene_pos)
        return True

    def _line_drag(self, scene_pos, shift: bool = False):
        if not getattr(self, "_line_armed", False) \
                or self._line_start is None:
            return None
        import math as _math
        start = self._line_start
        raw_scene_pos = QPointF(scene_pos)
        start_target = getattr(self, "_line_start_target", None)
        self._line_end_target = self._connect_target_at(
            raw_scene_pos,
            exclude=start_target[0] if start_target is not None else None)
        if self._line_end_target is not None:
            # Preserve exact equipment ink while showing its alignment axes.
            scene_pos = self._line_end_target[0].anchor(
                self._line_end_target[1])
            self.canvas.snap_route_point(
                scene_pos, reference_points=(start, scene_pos))
        elif not shift:
            scene_pos = self.canvas.snap_route_point(
                raw_scene_pos, reference_points=(start,))
        else:
            # Shift owns the gesture while angle-constraining. Never leave a
            # guide from the preceding unconstrained mouse-move on screen.
            self.canvas.clear_smart_guides()
        dx = scene_pos.x() - start.x()
        dy = scene_pos.y() - start.y()
        angle = _math.degrees(_math.atan2(dy, dx))
        if shift:
            angle = round(angle / 45.0) * 45.0
        length = _math.hypot(dx, dy)
        end = QPointF(
            start.x() + length * _math.cos(_math.radians(angle)),
            start.y() + length * _math.sin(_math.radians(angle)))
        if self._line_end_target is not None:
            end = self._line_end_target[0].anchor(self._line_end_target[1])
            dx, dy = end.x() - start.x(), end.y() - start.y()
            angle = _math.degrees(_math.atan2(dy, dx))
            length = _math.hypot(dx, dy)
        self._set_connect_target(self._line_end_target)
        from PySide6.QtWidgets import QGraphicsPathItem
        if self._line_preview is None:
            self._line_preview = QGraphicsPathItem()
            self._line_preview.setZValue(20)
            self.canvas.scene().addItem(self._line_preview)
        path = None
        if start_target is not None or self._line_end_target is not None:
            from azeo_control_trainer.core.hmi.pvms.rendering.pipe_scene import port_anchor, scene_bounds, scene_obstacles
            from azeo_control_trainer.core.hmi.pvms.rendering.routing import OrthogonalRouter, Point, PortAnchor, RouteRequest
            normal_a, normal_b = ("e", "w") if abs(dx) >= abs(dy) \
                and dx >= 0 else ("w", "e") if abs(dx) >= abs(dy) \
                else ("s", "n") if dy >= 0 else ("n", "s")
            anchor_a = port_anchor(*start_target) \
                if start_target is not None \
                else PortAnchor(Point(start.x(), start.y()), normal_a)
            anchor_b = port_anchor(*self._line_end_target) \
                if self._line_end_target is not None \
                else PortAnchor(Point(end.x(), end.y()), normal_b)
            excluded = tuple(target[0] for target in (
                start_target, self._line_end_target) if target is not None)
            routed = OrthogonalRouter().route(RouteRequest(
                anchor_a, anchor_b,
                obstacles=scene_obstacles(self.canvas.scene(), excluded),
                bounds=scene_bounds(self.canvas.scene()), max_nodes=20_000))
            from azeo_control_trainer.core.hmi.pvms.rendering.items import rounded_polyline_path
            path = rounded_polyline_path(
                [QPointF(point.x, point.y) for point in routed.points], 7.0)
        if path is None:
            from PySide6.QtGui import QPainterPath
            path = QPainterPath(start)
            path.lineTo(end)
        connected = start_target is not None \
            and self._line_end_target is not None
        pen = QPen(QColor(WF["cmd_teal"] if connected else WF["lapis"]),
                   2.2 if connected else 1.4,
                   Qt.SolidLine if connected else Qt.DashLine)
        pen.setCosmetic(True)
        self._line_preview.setPen(pen)
        self._line_preview.setPath(path)
        return end, angle, length

    def _line_release(self, scene_pos, shift: bool = False):
        result = self._line_drag(scene_pos, shift)
        if self._line_preview is not None:
            self.canvas.scene().removeItem(self._line_preview)
            self._line_preview = None
        self._line_armed = self.repeat_placement
        self._finish_drawing_tool()
        if result is None or self._line_start is None:
            return None
        end, angle, length = result
        start = self._line_start
        start_target = getattr(self, "_line_start_target", None)
        end_target = getattr(self, "_line_end_target", None)
        self._line_start = None
        self._line_start_target = None
        self._line_end_target = None
        self._set_connect_target(None)
        self.canvas.clear_smart_guides()
        if length < 6:
            return None
        if start_target is not None or end_target is not None:
            # A gesture touching process artwork is a routed connector.  If
            # one end is still loose it remains a selectable pipe endpoint,
            # ready to be glued later; it is never downgraded to a static
            # line that only looks connected.
            dx, dy = end.x() - start.x(), end.y() - start.y()
            side_a, side_b = ("e", "w") if abs(dx) >= abs(dy) \
                and dx >= 0 else ("w", "e") if abs(dx) >= abs(dy) \
                else ("s", "n") if dy >= 0 else ("n", "s")
            return self.add_pipe(
                start_target[0] if start_target is not None else None,
                start_target[1] if start_target is not None else side_a,
                end_target[0] if end_target is not None else None,
                end_target[1] if end_target is not None else side_b,
                point_a=None if start_target is not None else start,
                point_b=None if end_target is not None else end)
        # `add_static` checkpoints; a second one here made drawing a
        # line cost two undo presses (see `_pencil_release`).
        item = self.add_static("line", 0, 0, w=length, h=12)
        # Position so the rect's mid-line runs start-to-end after
        # rotation about the rect centre.
        cx = (start.x() + end.x()) / 2
        cy = (start.y() + end.y()) / 2
        item.data["x"], item.data["y"] = cx - length / 2, cy - 6
        item.setPos(item.data["x"], item.data["y"])
        item.data["rot"] = angle
        item.apply_rotation()
        self.mark_unsaved()
        return item

    # ------------------------------------------- direct shape + polyline
    def arm_shape(self, kind: str, *, w: float = 120.0,
                  h: float = 80.0) -> None:
        """Press-drag-release shape drawing with a live geometry preview.

        A click without a drag retains the convenient default-size placement.
        Shift constrains the result to a square; Alt draws about the starting
        point as a centre.  Those modifiers are conventional across CAD and
        illustration tools and make the Studio faster without changing the
        persisted drawing vocabulary.
        """
        self.enter_edit()
        self.cancel_gestures()
        self._shape_armed = True
        self.set_interaction_mode("draw")
        self._shape_kind = kind
        self._shape_default = (float(w), float(h))
        self._shape_start = None
        self._shape_preview = None
        self.canvas.setCursor(Qt.CrossCursor)

    def _shape_press(self, scene_pos) -> bool:
        if not getattr(self, "_shape_armed", False):
            return False
        self._shape_start = QPointF(scene_pos)
        return True

    @staticmethod
    def _draw_rect(start, end, *, constrain=False,
                   from_center=False) -> tuple[float, float, float, float]:
        dx, dy = end.x() - start.x(), end.y() - start.y()
        if constrain:
            extent = max(abs(dx), abs(dy))
            dx = extent if dx >= 0 else -extent
            dy = extent if dy >= 0 else -extent
        if from_center:
            left, right = start.x() - abs(dx), start.x() + abs(dx)
            top, bottom = start.y() - abs(dy), start.y() + abs(dy)
        else:
            left, right = sorted((start.x(), start.x() + dx))
            top, bottom = sorted((start.y(), start.y() + dy))
        return left, top, right - left, bottom - top

    def _shape_drag(self, scene_pos, modifiers=Qt.NoModifier):
        if not getattr(self, "_shape_armed", False) \
                or self._shape_start is None:
            return None
        constrain = bool(modifiers & Qt.ShiftModifier) \
            or self._shape_kind == "square"
        geometry = self._draw_rect(
            self._shape_start, scene_pos, constrain=constrain,
            from_center=bool(modifiers & Qt.AltModifier))
        left, top, width, height = geometry
        if self._shape_preview is None:
            self._shape_preview = QGraphicsRectItem()
            self._shape_preview.setPen(
                QPen(QColor(WF["lapis"]), 1.4, Qt.DashLine))
            fill = QColor(WF["lapis_lt"])
            fill.setAlpha(55)
            self._shape_preview.setBrush(QBrush(fill))
            self._shape_preview.setZValue(20)
            self.canvas.scene().addItem(self._shape_preview)
        self._shape_preview.setRect(left, top, width, height)
        return geometry

    def _drop_shape_preview(self) -> None:
        preview = getattr(self, "_shape_preview", None)
        if preview is not None and preview.scene() is not None:
            preview.scene().removeItem(preview)
        self._shape_preview = None

    def _shape_release(self, scene_pos, modifiers=Qt.NoModifier):
        geometry = self._shape_drag(scene_pos, modifiers)
        start = self._shape_start
        kind = getattr(self, "_shape_kind", "rect")
        default = getattr(self, "_shape_default", (120.0, 80.0))
        self._drop_shape_preview()
        self._shape_armed = self.repeat_placement
        self._shape_start = None
        self._finish_drawing_tool()
        if start is None:
            return None
        if geometry is None or geometry[2] < 6.0 or geometry[3] < 6.0:
            width, height = default
            if kind == "square":
                width = height = max(width, height)
            geometry = (start.x() - width / 2.0,
                        start.y() - height / 2.0, width, height)
        left, top, width, height = geometry
        return self.add_static(
            kind, left, top, w=width, h=height,
            text="Text" if kind == "text" else "")

    def arm_polyline(self) -> None:
        """Click vertices, move for preview, double-click/Enter to finish."""
        self.enter_edit()
        self.cancel_gestures()
        self._polyline_armed = True
        self.set_interaction_mode("draw")
        self._polyline_points = []
        self._polyline_preview = None
        self.canvas.setCursor(Qt.CrossCursor)

    def _polyline_path(self, cursor=None):
        from PySide6.QtGui import QPainterPath
        points = list(getattr(self, "_polyline_points", ()))
        if cursor is not None and points:
            points.append(QPointF(cursor))
        path = QPainterPath()
        if points:
            path.moveTo(points[0])
            for point in points[1:]:
                path.lineTo(point)
        return path

    def _polyline_press(self, scene_pos) -> bool:
        if not getattr(self, "_polyline_armed", False):
            return False
        self._polyline_points.append(QPointF(scene_pos))
        self._polyline_move(scene_pos)
        return True

    def _polyline_move(self, scene_pos) -> bool:
        if not getattr(self, "_polyline_armed", False) \
                or not self._polyline_points:
            return False
        from PySide6.QtWidgets import QGraphicsPathItem
        if self._polyline_preview is None:
            self._polyline_preview = QGraphicsPathItem()
            self._polyline_preview.setPen(
                QPen(QColor(WF["lapis"]), 1.4, Qt.DashLine))
            self._polyline_preview.setZValue(20)
            self.canvas.scene().addItem(self._polyline_preview)
        self._polyline_preview.setPath(self._polyline_path(scene_pos))
        return True

    def _drop_polyline_preview(self) -> None:
        preview = getattr(self, "_polyline_preview", None)
        if preview is not None and preview.scene() is not None:
            preview.scene().removeItem(preview)
        self._polyline_preview = None

    def finish_polyline(self):
        points = list(getattr(self, "_polyline_points", ()))
        # The second press of a double-click can repeat the final vertex.
        if len(points) >= 2 \
                and (points[-1] - points[-2]).manhattanLength() < 1.0:
            points.pop()
        self._drop_polyline_preview()
        self._polyline_armed = self.repeat_placement
        self._polyline_points = []
        self._finish_drawing_tool()
        if len(points) < 2:
            return None
        min_x = min(point.x() for point in points)
        min_y = min(point.y() for point in points)
        local = [[point.x() - min_x, point.y() - min_y]
                 for point in points]
        item = self.add_static(
            "polyline", min_x, min_y,
            w=max(point[0] for point in local) or 1.0,
            h=max(point[1] for point in local) or 1.0)
        item.data["points"] = local
        item._refit_points()
        self.mark_unsaved()
        return item

    def arm_place(self, kind: str, symbol: str = "",
                  w: float = 110, h: float = 90,
                  text: str = "", *, properties: dict | None = None) -> None:
        """Palette click arms placement: a half-opacity ghost follows
        the cursor, a click drops it there, Esc cancels — the item
        lands where the mouse is, not in the middle of the screen."""
        if self.mode != MODE_EDIT:
            self.enter_edit()
        self.cancel_gestures()
        if symbol:
            from azeo_control_trainer.core.hmi.pvms.symbols import aspect
            h = w * aspect(symbol)
        data = {"kind": kind, "x": 0, "y": 0, "w": w, "h": h,
                "text": text or ("Text" if kind == "text" else "")}
        if properties:
            data.update(copy.deepcopy(properties))
        if kind == "datalink":
            data.update({"datalink_type": "numeric", "path": "",
                         "decimals": 1})
        elif kind == "display_link":
            data.update({"target": "", "text": "Display Link"})
        elif kind == "user_entry":
            data.update({"entry": {"kind": "button", "label": "Button",
                                    "path": ""}})
        elif kind == "chart":
            data.update({"pens": [{"label": "PV", "path": ""}],
                         "lo": 0.0, "hi": 100.0})
        elif kind in ("multi_point", "radar_plot"):
            data["parameters"] = [
                {"label": f"P{index + 1}", "path": "", "lo": 0.0,
                 "hi": 100.0} for index in range(3)]
        elif kind == "alarm_list":
            data.update({"priority_min": 0, "path_prefix": ""})
        elif kind == "tab":
            data.update({"tabs": [{"title": "Tab 1", "text": ""}],
                         "active_tab": 0})
        elif kind == "date_time":
            data.update({"timezone": "local",
                         "format": "%Y-%m-%d  %H:%M:%S"})
        elif kind == "table":
            data.update({
                "columns": [
                    {"key": "parameter", "title": "Param", "width": 2},
                    {"key": "value", "title": "Value", "width": 2},
                    {"key": "units", "title": "Units", "width": 1},
                ],
                "rows": [], "row_height": 20, "header_height": 20,
            })
        elif kind == "icon_button":
            data.update({"icon": "module_detail", "button": False})
        elif kind == "special_symbol":
            data.update({"icon": "module_detail"})
        if symbol:
            data["symbol"] = symbol
            # Equipment is process-connectable from its first placement.
            # Named normalized ports keep authored pipes attached through a
            # later resize or symbol replacement.
            data["ports"] = default_symbol_ports(symbol)
        ghost = StaticItem(data, self.palette_roles)
        ghost.setOpacity(0.55)
        ghost.setFlag(QGraphicsItem.ItemIsMovable, False)
        ghost.setFlag(QGraphicsItem.ItemIsSelectable, False)
        # Cursor movement must not mark the real document unsaved or turn an
        # unplaced stencil into an obstacle for existing process pipes.
        ghost.setFlag(QGraphicsItem.ItemSendsGeometryChanges, False)
        ghost._authoring_preview = True
        ghost.setAcceptedMouseButtons(Qt.NoButton)
        ghost.hide()
        self.canvas.scene().addItem(ghost)
        self._place_ghost = ghost
        self.set_interaction_mode("draw")
        self.canvas.setCursor(Qt.CrossCursor)
        self.canvas.setMouseTracking(True)
        self.canvas.setFocus()

    def cancel_place(self) -> None:
        ghost = getattr(self, "_place_ghost", None)
        if ghost is not None:
            self.canvas.scene().removeItem(ghost)
            self._place_ghost = None
            self.canvas.unsetCursor()
        self._placement_payload = None

    def arm_palette_item(self, payload: dict) -> None:
        """Use the drag/drop placement path for click-to-place stencils too."""
        self.enter_edit()
        self.cancel_gestures()
        self._placement_payload = copy.deepcopy(payload)
        ghost = self.make_palette_ghost(payload)
        ghost.setAcceptedMouseButtons(Qt.NoButton)
        ghost.hide()
        self._place_ghost = ghost
        self.canvas.scene().addItem(ghost)
        self.set_interaction_mode("draw")
        self.canvas.setCursor(Qt.CrossCursor)
        self.canvas.setFocus()

    def _place_move(self, scene_pos) -> bool:
        ghost = getattr(self, "_place_ghost", None)
        if ghost is None:
            return False
        ghost.setPos(scene_pos.x() - ghost.rect().width() / 2,
                     scene_pos.y() - ghost.rect().height() / 2)
        ghost.show()
        return True

    def _place_click(self, scene_pos) -> bool:
        ghost = getattr(self, "_place_ghost", None)
        if ghost is None:
            return False
        payload = getattr(self, "_placement_payload", None)
        if payload is not None:
            self.drop_palette_item(payload, scene_pos)
        else:
            data = copy.deepcopy(ghost.data)
            data.pop("id", None)
            self.checkpoint()
            data["x"] = scene_pos.x() - data["w"] / 2
            data["y"] = scene_pos.y() - data["h"] / 2
            item = self.renderer.build_drawing(data)
            self.canvas.scene().addItem(item)
            self.selection.replace((item,))
            self.mark_unsaved()
        if not self.repeat_placement:
            self.cancel_place()
        self._finish_drawing_tool()
        return True

    # ------------------------------- drag-from-anchor (the JS gesture)
    def start_anchor_drag(self, item, side: str) -> None:
        """Press on a connection point starts the pipe — no tool to
        arm first. The preview follows until release."""
        self.connect_armed = True
        self.set_interaction_mode("draw")
        self._connect_pending = (item, side)
        self.canvas.setCursor(Qt.CrossCursor)

    def _set_connect_target(self, target) -> None:
        """Highlight exactly one magnetic destination during a drag."""
        previous = getattr(self, "_connect_target", None)
        if previous == target:
            return
        if previous is not None:
            previous[0]._connect_hot_anchor = None
            previous[0].update()
        self._connect_target = target
        if target is not None:
            target[0]._connect_hot_anchor = target[1]
            target[0].update()
        self.update_geometry_readout()

    def _connect_target_at(self, scene_pos, exclude=None,
                           pixels: float = 18.0):
        """Resolve a forgiving, screen-sized magnetic target.

        Requiring the pointer to intersect a four-pixel dot made connection
        creation fail most often at Fit zoom. We inspect only the small scene
        rectangle beneath the pointer, prefer an engineered port when one is
        close, then project to the exact continuous perimeter. The resulting
        normalized edge coordinate remains attached through move and resize.
        """
        scale = abs(self.canvas.transform().m11())
        radius = pixels / max(scale, 0.1)
        area = QRectF(scene_pos.x() - radius, scene_pos.y() - radius,
                      radius * 2.0, radius * 2.0)
        candidates = []
        for candidate in self.canvas.scene().items(area):
            if candidate is exclude \
                    or not isinstance(candidate, (PvmItem, StaticItem)) \
                    or not candidate.isVisible():
                continue
            side = candidate.connection_anchor_at(
                scene_pos, pixels=pixels, allow_inside=True)
            if side is None:
                continue
            point = candidate.anchor(side)
            distance = (point.x() - scene_pos.x()) ** 2 \
                + (point.y() - scene_pos.y()) ** 2
            inside = candidate.shape().contains(
                candidate.mapFromScene(scene_pos))
            # An SVG symbol is not its rectangular host. A pointer in the
            # empty corner of a pump's item must not silently glue a pipe to
            # some remote part of the silhouette. Cards and primitive shapes
            # retain their forgiving whole-interior target.
            exact_outline = str(side).startswith("outline:")
            if distance <= radius ** 2 or (inside and not exact_outline):
                candidates.append((distance, -candidate.zValue(),
                                   candidate, side))
        if not candidates:
            return None
        _distance, _z, candidate, side = min(
            candidates, key=lambda row: (row[0], row[1]))
        return candidate, side

    def drag_anchor_to(self, scene_pos) -> None:
        self._connect_preview(scene_pos)

    def finish_anchor_drag(self, scene_pos, exclude=None) -> None:
        """Release: on another connectable item -> pipe; else cancel."""
        target = self._connect_target_at(scene_pos, exclude=exclude)
        if target is not None:
            self._finish_connect(target[0], scene_pos, side=target[1])
        else:
            self._connect_preview(None)
            self._connect_pending = None
            self.connect_armed = False
            self.set_interaction_mode("select")
            self.canvas.unsetCursor()

    def arm_connect(self) -> None:
        """One press on each endpoint draws a pipe, then the tool
        disarms — a mode that stayed on would turn the next selection
        click into an accidental pipe."""
        if self.mode != MODE_EDIT:
            self.enter_edit()
        self._connect_pending = None
        self.canvas.setCursor(Qt.CrossCursor)
        self.canvas.setMouseTracking(True)
        self.connect_armed = True

    def _connect_preview(self, scene_pos=None) -> None:
        """The dashed preview from the first anchor to the cursor —
        without it, an armed half-connection is invisible state."""
        from PySide6.QtWidgets import QGraphicsPathItem
        pending = getattr(self, "_connect_pending", None)
        preview = getattr(self, "_connect_line", None)
        if pending is None or scene_pos is None:
            if preview is not None:
                self.canvas.scene().removeItem(preview)
                self._connect_line = None
            self._set_connect_target(None)
            self.canvas.clear_smart_guides()
            return
        if preview is None:
            preview = QGraphicsPathItem()
            pen = QPen(QColor(WF["lapis"]), 1.6, Qt.DashLine)
            pen.setCosmetic(True)
            preview.setPen(pen)
            preview.setZValue(20)
            self.canvas.scene().addItem(preview)
            self._connect_line = preview
        from azeo_control_trainer.core.hmi.pvms.rendering.pipe_scene import junction_side, port_anchor, scene_bounds, scene_obstacles
        from azeo_control_trainer.core.hmi.pvms.rendering.routing import OrthogonalRouter, Point, PortAnchor, RouteRequest
        start = pending[0].anchor(pending[1])
        raw_scene_pos = QPointF(scene_pos)
        target = self._connect_target_at(raw_scene_pos, exclude=pending[0])
        if target is not None:
            target = (target[0], junction_side(target[0], target[1], start))
        pending = (pending[0], junction_side(pending[0], pending[1],
                   target[0].anchor(target[1]) if target is not None else raw_scene_pos))
        self._connect_pending = pending
        self._set_connect_target(target)
        if target is not None:
            end = target[0].anchor(target[1])
            self.canvas.snap_route_point(
                end, reference_points=(start, end))
        else:
            end = self.canvas.snap_route_point(
                raw_scene_pos, reference_points=(start,))
        dx, dy = end.x() - start.x(), end.y() - start.y()
        end_normal = "w" if abs(dx) >= abs(dy) and dx >= 0 else \
            "e" if abs(dx) >= abs(dy) else \
            "n" if dy >= 0 else "s"
        end_anchor = port_anchor(target[0], target[1]) \
            if target is not None else \
            PortAnchor(Point(end.x(), end.y()), end_normal)
        result = OrthogonalRouter().route(RouteRequest(
            port_anchor(pending[0], pending[1]),
            end_anchor,
            obstacles=scene_obstacles(
                self.canvas.scene(), (pending[0],) + (
                    (target[0],) if target is not None else ())),
            bounds=scene_bounds(self.canvas.scene()), max_nodes=20_000))
        pen = QPen(QColor(WF["cmd_teal"] if target is not None
                            else WF["lapis"]),
                   2.2 if target is not None else 1.6,
                   Qt.SolidLine if target is not None else Qt.DashLine)
        pen.setCosmetic(True)
        preview.setPen(pen)
        from azeo_control_trainer.core.hmi.pvms.rendering.items import rounded_polyline_path
        points = [QPointF(point.x, point.y) for point in result.points]
        preview.setPath(rounded_polyline_path(points, 7.0))

    def _finish_connect(self, item, scene_pos, side=None) -> None:
        pending = self._connect_pending
        side = side or item.nearest_side(scene_pos)
        self._connect_preview(None)
        self._connect_pending = None
        # Canvas-level release completes a successful drag before Qt sends a
        # release to the source item. Clear its gesture latch here or the next
        # ordinary move is misread as a second connector drag.
        pending[0]._anchor_dragging = False
        self.connect_armed = self.repeat_placement
        self._finish_drawing_tool()
        self.add_pipe(pending[0], pending[1], item, side)

    def _connect_click(self, item, scene_pos) -> bool:
        """A press while the connect tool is armed. True = consumed.
        Works click-click AND press-drag-release: the second endpoint
        may be a second click or the release of the same drag."""
        if not getattr(self, "connect_armed", False):
            return False
        if item is None or not hasattr(item, "nearest_side"):
            target = self._connect_target_at(scene_pos)
            if target is None:
                self._connect_preview(None)
                self._connect_pending = None
                self.connect_armed = False
                self.set_interaction_mode("select")
                self.canvas.unsetCursor()
                return True
            item = target[0]
        pending = getattr(self, "_connect_pending", None)
        if pending is None:
            self._connect_pending = (item, item.nearest_side(scene_pos))
            return True
        if item is pending[0]:
            return True                 # same item: keep waiting
        self._finish_connect(item, scene_pos)
        return True

    def _connect_move(self, scene_pos) -> bool:
        if getattr(self, "_connect_pending", None) is None:
            return False
        self._connect_preview(scene_pos)
        return True

    def _connect_release(self, item, scene_pos) -> bool:
        """Drag gesture: released over another connectable item ends
        the pipe there; released elsewhere keeps waiting for a click."""
        pending = getattr(self, "_connect_pending", None)
        if pending is None or not getattr(self, "connect_armed", False):
            return False
        target = self._connect_target_at(scene_pos, exclude=pending[0])
        if target is None and isinstance(item, (PvmItem, StaticItem)) \
                and item is not pending[0]:
            # Dropping anywhere on a symbol is a deliberate target gesture;
            # resolve it to the nearest *visible outline* even when the
            # pointer is not within the magnetic edge band. This preserves
            # click/drag ergonomics without ever storing the host rectangle.
            side = item.connection_anchor_at(
                scene_pos, pixels=18.0, allow_inside=True)
            if side is not None:
                target = (item, side)
        if target is not None:
            self._finish_connect(target[0], scene_pos, side=target[1])
            return True
        return False

    # ------------------------------------------------ group / align / rot
    @staticmethod
    def _group_of(item) -> str:
        return item_group_id(item)

    @staticmethod
    def _set_group(item, group: str) -> None:
        if isinstance(item, PvmItem):
            item.pvm = Pvm(**{**item.pvm.__dict__, "group": group})
        elif isinstance(item, (StaticItem, PipeItem)):
            if group:
                item.data["group"] = group
            else:
                item.data.pop("group", None)

    def _bulk_begin(self):
        before = json.loads(json.dumps(self._document()))
        redo = list(self._redo_stack)
        self.checkpoint()
        return before, redo

    def _bulk_end(self, state) -> bool:
        before, redo = state
        if self._document() == before:
            if self._undo_stack and self._undo_stack[-1] == before:
                self._undo_stack.pop()
            self._redo_stack[:] = redo
            return False
        self.reroute_pipes()
        self.mark_unsaved()
        return True

    def _arrangement_units(self, items, rects):
        """Treat a selected group as one shape without introducing a
        second graphics-container model.

        Selection expands a group into its members so the inspector can
        still address each object. Arrange commands must do the opposite:
        moving every member to the same edge would collapse the group.
        """
        units = []
        grouped = {}
        for item in items:
            group = self._group_of(item)
            key = ("group", group) if group else ("item", id(item))
            if key not in grouped:
                grouped[key] = []
                units.append(grouped[key])
            grouped[key].append(item)

        result = []
        for members in units:
            bounds = None
            for member in members:
                rect = rects[member]
                bounds = bounds.united(rect) \
                    if bounds is not None else QRectF(rect)
            result.append((tuple(members),
                           bounds if bounds is not None else QRectF()))
        return result

    def group_selected(self) -> str | None:
        """Group mixed PVM/drawing selections without nesting documents.

        The group is authoring metadata rather than a graphics container, so
        every member keeps its binding, identity, and runtime representation.
        Connecting pipes participate in the logical group while remaining
        non-movable derived geometry; moving grouped equipment reroutes them.
        """
        items = list(self.selection.editable_items(include_pipes=True))
        if len(items) < 2:
            return None
        existing = {self._group_of(item) for item in items}
        existing.discard("")
        if len(existing) == 1:
            group = next(iter(existing))
            members = {item for item in self._groupable_items()
                       if self._group_of(item) == group}
            if members == set(items):
                return group
        state = self._bulk_begin()
        group_id = f"grp_{uuid.uuid4().hex[:8]}"
        for item in items:
            self._set_group(item, group_id)
        self._bulk_end(state)
        return group_id

    def ungroup_selected(self) -> None:
        represented = {self._group_of(item) for item in self.selection.snapshot().items}
        represented.discard("")
        if not represented:
            return
        state = self._bulk_begin()
        for item in self._groupable_items():
            if self._group_of(item) in represented:
                self._set_group(item, "")
        self._bulk_end(state)

    def align_selection(self, edge: str, reference: str = "primary") -> int:
        """Align visible artwork, using the focused item as the key object.

        Qt bounding rectangles include authoring handles and damage margins;
        those are deliberately excluded. The primary item is the last item
        selected, matching professional drawing tools and making the result
        predictable for mixed-size process equipment.
        """
        from .selection import visual_scene_rect

        items = list(self.selection.editable_items())
        if len(items) < 2:
            return 0
        snapshot = self.selection.snapshot()
        rects = {item: visual_scene_rect(item) for item in items}
        units = self._arrangement_units(items, rects)
        if len(units) < 2:
            return 0
        primary_unit = next(
            (unit for unit in units if snapshot.primary in unit[0]), None)
        if reference == "primary" and primary_unit is not None:
            target = primary_unit[1]
        else:
            target = None
            for _members, rect in units:
                target = target.united(rect) \
                    if target is not None else QRectF(rect)
            target = target if target is not None else QRectF()
        state = self._bulk_begin()
        for members, rect in units:
            dx = dy = 0.0
            if edge == "left":
                dx = target.left() - rect.left()
            elif edge == "right":
                dx = target.right() - rect.right()
            elif edge == "top":
                dy = target.top() - rect.top()
            elif edge == "bottom":
                dy = target.bottom() - rect.bottom()
            elif edge == "hcenter":
                dx = target.center().x() - rect.center().x()
            elif edge == "vcenter":
                dy = target.center().y() - rect.center().y()
            if abs(dx) > 1e-9 or abs(dy) > 1e-9:
                for item in members:
                    item.setPos(item.pos() + QPointF(dx, dy))
        changed = self._bulk_end(state)
        self.canvas.update_rulers()
        return len(items) if changed else 0

    def align_selected(self, edge: str) -> int:
        """Align to the last-selected key object and return the item count."""
        return self.align_selection(edge, reference="primary")

    def distribute_selected(self, axis: str) -> int:
        """Even the GAPS between three or more elements. Returns how
        many moved.

        **Gaps, not origins.** Spacing centres evenly leaves ragged
        space whenever the elements differ in size, and the eye reads
        the space between things rather than where each one starts —
        this is the same rule the DynaLive editor's distribute follows.

        The outermost two do not move: they define the span being
        divided, and shifting them would make the command depend on
        which element happened to be selected first.
        """
        from .selection import visual_scene_rect

        items = list(self.selection.editable_items())
        rects = {item: visual_scene_rect(item) for item in items}
        units = self._arrangement_units(items, rects)
        if len(units) < 3:
            return 0
        horizontal = axis in ("h", "hspace", "horizontal")

        def start(unit):
            rect = unit[1]
            return rect.left() if horizontal else rect.top()

        def size(unit):
            r = unit[1]
            return r.width() if horizontal else r.height()

        units.sort(key=start)
        span = (start(units[-1]) + size(units[-1])) - start(units[0])
        gap = (span - sum(size(unit) for unit in units)) / (len(units) - 1)
        state = self._bulk_begin()
        cursor = start(units[0]) + size(units[0]) + gap
        for members, bounds in units[1:-1]:
            delta = cursor - (bounds.left() if horizontal else bounds.top())
            if horizontal:
                offset = QPointF(delta, 0)
            else:
                offset = QPointF(0, delta)
            for item in members:
                item.setPos(item.pos() + offset)
            cursor += (bounds.width() if horizontal else bounds.height()) + gap
        changed = self._bulk_end(state)
        self.canvas.update_rulers()
        return sum(len(members) for members, _bounds in units[1:-1]) \
            if changed else 0

    def equalize_selected(self, dimension: str = "both") -> int:
        """Match width/height to the focused (key) object."""
        items = list(self.selection.editable_items())
        if len(items) < 2:
            return 0
        key = self.selection.snapshot().primary
        if key not in items:
            key = items[-1]
        target = key.rect().size()
        state = self._bulk_begin()
        for item in items:
            if item is key:
                continue
            width = target.width() if dimension in ("width", "both") \
                else item.rect().width()
            height = target.height() if dimension in ("height", "both") \
                else item.rect().height()
            destination = QPointF(item.pos().x() + width,
                                  item.pos().y() + height)
            if isinstance(item, PvmItem):
                item._resize_to("se", destination, bypass_snap=True)
            else:
                item._resize_to("se", destination, True, True)
        return len(items) - 1 if self._bulk_end(state) else 0

    def align_selected_to_page(self, anchor: str) -> int:
        """Align the selection to the authored safe area or page centre."""
        from .selection import visual_scene_rect

        page = self.canvas.page_rect()
        items = list(self.selection.editable_items())
        if page.isEmpty() or not items:
            return 0
        margin = max(0.0, float(getattr(self.display, "safe_margin", 24)))
        safe = page.adjusted(margin, margin, -margin, -margin)
        moves = []
        for item in items:
            bounds = visual_scene_rect(item)
            dx = dy = 0.0
            if anchor == "left":
                dx = safe.left() - bounds.left()
            elif anchor == "right":
                dx = safe.right() - bounds.right()
            elif anchor == "top":
                dy = safe.top() - bounds.top()
            elif anchor == "bottom":
                dy = safe.bottom() - bounds.bottom()
            elif anchor in ("hcenter", "center"):
                dx = safe.center().x() - bounds.center().x()
            if anchor in ("vcenter", "center"):
                dy = safe.center().y() - bounds.center().y()
            elif anchor not in ("left", "right", "top", "bottom",
                                 "hcenter"):
                return 0
            if abs(dx) > 1e-9 or abs(dy) > 1e-9:
                moves.append((item, dx, dy))
        if not moves:
            return 0
        state = self._bulk_begin()
        for item, dx, dy in moves:
            item.setPos(item.pos() + QPointF(dx, dy))
        changed = self._bulk_end(state)
        self.canvas.update_rulers()
        return len(moves) if changed else 0

    def selection_property(self, key: str):
        from .selection import aggregate
        return aggregate(self.selection.snapshot().items, key)

    def apply_selection_property(self, key: str, value) -> tuple[int, int]:
        """Atomically apply one property shared by a multi-selection."""
        from dataclasses import replace
        from .layers import set_item_layer
        from .selection import COMMON_PROPERTIES

        if key not in COMMON_PROPERTIES:
            return 0, self.selection.snapshot().count
        items = list(self.selection.snapshot().items)
        if not items:
            return 0, 0
        primary = self.selection.snapshot().primary
        state = self._bulk_begin()
        changed = skipped = 0
        for item in items:
            item_data = getattr(item, "data", None)
            if key not in ("visible", "locked") and (
                    getattr(getattr(item, "pvm", None), "locked", False)
                    or (isinstance(item_data, dict)
                        and item_data.get("locked", False))):
                skipped += 1
                continue
            pvm = getattr(item, "pvm", None)
            if key == "layer":
                set_item_layer(item, str(value))
            elif key in ("x", "y"):
                x = float(value) if key == "x" else item.pos().x()
                y = float(value) if key == "y" else item.pos().y()
                item.setPos(x, y)
            elif key in ("w", "h"):
                width = float(value) if key == "w" else item.rect().width()
                height = float(value) if key == "h" else item.rect().height()
                destination = item.pos() + QPointF(width, height)
                if isinstance(item, PvmItem):
                    item._resize_to("se", destination, bypass_snap=True)
                elif isinstance(item, StaticItem):
                    item._resize_to("se", destination, True, True)
                else:
                    skipped += 1
                    continue
            elif key == "rot":
                if pvm is not None:
                    item.pvm = replace(pvm, rot=float(value) % 360)
                    item._apply_pvm_rotation()
                elif isinstance(item, StaticItem):
                    item.data["rot"] = float(value) % 360
                    item.apply_rotation()
                else:
                    skipped += 1
                    continue
            elif key == "visible":
                if pvm is not None:
                    item.pvm = replace(pvm, visible=bool(value))
                    item.pvm_visible = bool(value)
                else:
                    item.data["visible"] = bool(value)
            elif key == "locked":
                if pvm is not None:
                    item.pvm = replace(pvm, locked=bool(value))
                    item.pvm_locked = bool(value)
                else:
                    item.data["locked"] = bool(value)
            elif key == "opacity":
                item.setOpacity(float(value))
                if pvm is None:
                    item.data["opacity"] = float(value)
                else:
                    # Opacity is not part of the governed PVM appearance
                    # contract; transparent PVMs can hide process state.
                    item.setOpacity(1.0)
                    skipped += 1
                    continue
            changed += 1
        self._apply_mode()
        self.selection.replace(items, primary=primary)
        return (changed if self._bulk_end(state) else 0), skipped

    def transform_selection(self, *, dx: float = 0.0, dy: float = 0.0,
                            rotation: float = 0.0) -> int:
        items = list(self.selection.editable_items())
        if not items or (not dx and not dy and not rotation):
            return 0
        state = self._bulk_begin()
        for item in items:
            item.setPos(item.pos() + QPointF(dx, dy))
            if rotation:
                if isinstance(item, PvmItem):
                    item.pvm = Pvm(**{**item.pvm.__dict__,
                                      "rot": (item.pvm.rot + rotation) % 360})
                    item._apply_pvm_rotation()
                else:
                    item.data["rot"] = (float(item.data.get("rot", 0))
                                        + rotation) % 360
                    item.apply_rotation()
        return len(items) if self._bulk_end(state) else 0

    def contain_selected_in_page(self) -> int:
        """Move selected elements wholly inside the publishable safe area."""
        from .selection import visual_scene_rect

        page = self.canvas.page_rect()
        items = list(self.selection.editable_items())
        if page.isEmpty() or not items:
            return 0
        margin = max(0.0, float(getattr(self.display, "safe_margin", 24)))
        safe = page.adjusted(margin, margin, -margin, -margin)
        moved = []
        for item in items:
            # Authoring adornments deliberately widen some items' Qt bounding
            # rectangles.  They are selection furniture, not published ink;
            # using them here moved an already page-aligned item a second time.
            bounds = visual_scene_rect(item)
            if bounds.left() < safe.left():
                dx = safe.left() - bounds.left()
            elif bounds.right() > safe.right():
                dx = safe.right() - bounds.right()
            else:
                dx = 0.0
            if bounds.top() < safe.top():
                dy = safe.top() - bounds.top()
            elif bounds.bottom() > safe.bottom():
                dy = safe.bottom() - bounds.bottom()
            else:
                dy = 0.0
            if abs(dx) > 1e-9 or abs(dy) > 1e-9:
                moved.append((item, dx, dy))
        if not moved:
            return 0
        state = self._bulk_begin()
        for item, dx, dy in moved:
            item.setPos(item.pos() + QPointF(dx, dy))
        return len(moved) if self._bulk_end(state) else 0

    def set_background(self, colour: str) -> None:
        self.checkpoint()
        self.display.background = colour
        self.canvas.viewport().update()
        self.mark_unsaved()

    # ---------------------------------------------------- drawing style
    _STYLE_KEYS = (
        "fill", "line", "width", "style", "cap", "join", "corner_radius",
        "fill_role", "line_role", "text_role",
        "font_family", "font_size", "font_bold", "font_italic",
        "font_underline", "text_color", "text_align", "text_valign",
        "arrow", "arrow_start", "arrow_end", "arrow_shape", "arrow_size",
        "crossover",
    )

    @staticmethod
    def _styleable(item) -> bool:
        # Copy/paste drawing style must not rewrite a linked class subtree or
        # bypass the native PVM's two governed appearance fields.
        return isinstance(item, (StaticItem, PipeItem)) \
            and not item.data.get("user_pvm")

    def copy_drawing_style(self, source=None) -> bool:
        source = source if source is not None else self.selection.snapshot().primary
        if not self._styleable(source):
            self.uiError.emit("Select a drawing object or pipe to copy its style.")
            return False
        self._style_clipboard = {key: copy.deepcopy(source.data[key])
                                 for key in self._STYLE_KEYS if key in source.data}
        return True

    def paste_drawing_style(self, targets=None) -> int:
        from .selection import is_locked
        style = getattr(self, "_style_clipboard", None)
        if style is None:
            return 0
        self.enter_edit()
        targets = self.selection.snapshot().items if targets is None else targets
        targets = [item for item in targets
                   if self._styleable(item) and not is_locked(item)]
        if not targets:
            return 0
        state = self._bulk_begin()
        for item in targets:
            item.prepareGeometryChange()
            for key in self._STYLE_KEYS:
                item.data.pop(key, None)
            item.data.update(copy.deepcopy(style))
            item.update()
        return len(targets) if self._bulk_end(state) else 0

    def arm_style_brush(self) -> None:
        if not self.copy_drawing_style():
            return
        self.enter_edit()
        self.cancel_gestures()
        self._style_brush_armed = True
        self.set_interaction_mode("draw")
        self.canvas.setCursor(Qt.PointingHandCursor)
        self.canvas.setFocus()

    def _style_brush_click(self, item) -> bool:
        from .selection import is_locked

        if not getattr(self, "_style_brush_armed", False):
            return False
        while item is not None and not self._styleable(item):
            item = item.parentItem()
        if self._styleable(item) and not is_locked(item):
            self.paste_drawing_style((item,))
        else:
            return True
        if not self.repeat_placement:
            self._style_brush_armed = False
            self._finish_drawing_tool()
        return True

    def _add_element_actions(self, menu, item) -> None:
        """Azeo's element operations, on the element's own menu.

        Everything here already existed as a command — `align_selected`,
        `group_selected`, the `arrange.*` dispatch ids — and was
        reachable only from the ribbon. A ribbon is where you go when
        you know what a thing is called; a shortcut menu is where you
        go when you have the thing under the pointer.
        """
        data = getattr(item, "data", None)
        is_dict = isinstance(data, dict)

        # Visibility and Lock. Both are element STATE and both make the
        # element unreachable on the canvas afterwards, which is why
        # the Selection pane exists to get back to them.
        visible = bool(data.get("visible", True)) if is_dict else True
        locked = bool(data.get("locked", False)) if is_dict else False
        hide = menu.addAction("Hide" if visible else "Show")
        hide.triggered.connect(
            lambda: self._set_element_flag(item, "visible", not visible))
        lock = menu.addAction("Unlock" if locked else "Lock")
        lock.triggered.connect(
            lambda: self._set_element_flag(item, "locked", not locked))

        menu.addSeparator()
        order = menu.addMenu("Order")
        for label, action_id in (("Bring Forward", "arrange.front"),
                                 ("Send Backward", "arrange.back")):
            entry = order.addAction(label)
            entry.triggered.connect(
                lambda _c=False, a=action_id: self._menu_dispatch(a))

        align = menu.addMenu("Align")
        chosen = len(self.selection.editable_items())
        groupable = len(self.selection.editable_items(include_pipes=True))
        straightenable = self.can_straighten_connected_run(item)
        align.setEnabled(chosen >= 2 or straightenable)
        run = align.addAction("Straighten Connected Run")
        run.setEnabled(straightenable)
        run.setToolTip(
            "Align the pipes on opposite sides of this equipment to one "
            "continuous centerline")
        run.triggered.connect(
            lambda _c=False, it=item: self.straighten_connected_run(it))
        align.addSeparator()
        for label, edge in (("Left", "left"), ("Centre", "hcenter"),
                            ("Right", "right"), ("Top", "top"),
                            ("Middle", "vcenter"), ("Bottom", "bottom")):
            entry = align.addAction(label)
            entry.setEnabled(chosen >= 2)
            entry.triggered.connect(
                lambda _c=False, e=edge: self.align_selected(e))
        # Distribute needs THREE; offering it for two would be a
        # command that silently does nothing.
        space = menu.addMenu("Distribute")
        space.setEnabled(chosen >= 3)
        for label, axis in (("Horizontally", "h"), ("Vertically", "v")):
            entry = space.addAction(label)
            entry.triggered.connect(
                lambda _c=False, a=axis: self.distribute_selected(a))
        size = menu.addMenu("Make Same Size")
        size.setEnabled(chosen >= 2)
        for label, dimension in (("Width", "width"), ("Height", "height"),
                                 ("Width and Height", "both")):
            size.addAction(label).triggered.connect(
                lambda _c=False, d=dimension: self.equalize_selected(d))
        page = menu.addMenu("Align to Page")
        page.setEnabled(not self.canvas.page_rect().isEmpty())
        for label, anchor in (("Left Safe Edge", "left"),
                              ("Right Safe Edge", "right"),
                              ("Top Safe Edge", "top"),
                              ("Bottom Safe Edge", "bottom"),
                              ("Horizontal Centre", "hcenter"),
                              ("Vertical Centre", "vcenter"),
                              ("Page Centre", "center")):
            page.addAction(label).triggered.connect(
                lambda _c=False, a=anchor:
                self.align_selected_to_page(a))
        page.addSeparator()
        page.addAction("Move Inside Safe Area").triggered.connect(
            self.contain_selected_in_page)

        menu.addSeparator()
        for label, action_id, needs in (("Cut", "edit.cut", 1),
                                        ("Copy", "edit.copy", 1),
                                        ("Duplicate", "edit.duplicate", 1),
                                        ("Group", "edit.group", 2),
                                        ("Ungroup", "edit.ungroup", 1),
                                        ("Delete", "edit.delete", 1)):
            entry = menu.addAction(label)
            count = groupable if action_id in ("edit.group",
                                                "edit.ungroup") else chosen
            entry.setEnabled(count >= needs)
            entry.triggered.connect(
                lambda _c=False, a=action_id: self._menu_dispatch(a))

    def _set_element_flag(self, item, field: str, value) -> None:
        """Hide/show or lock/unlock one element, and record it."""
        from PySide6.QtWidgets import QGraphicsItem

        data = getattr(item, "data", None)
        if not isinstance(data, dict):
            return
        self.checkpoint()
        data[field] = bool(value)
        if field == "visible":
            item.setVisible(bool(value))
        else:
            item.setFlag(QGraphicsItem.ItemIsMovable, not value)
        self.mark_unsaved()
        item.update()

    def _menu_dispatch(self, action_id: str) -> None:
        """Run a ribbon command from the shortcut menu.

        Goes through the hosting window's `dispatch` when there is one,
        so a command has ONE implementation however it is reached.
        """
        window = self.window()
        if hasattr(window, "dispatch"):
            window.dispatch(action_id)

    def drawing_context_menu(self, item, global_pos) -> None:
        """Menu D — pipe or shape: drawing style and delete."""
        from PySide6.QtWidgets import QColorDialog

        from azeo_control_trainer.core.presentation.menu_style             import studio_menu
        from azeo_control_trainer.core.presentation.headless import is_headless

        kind = item.data.get("kind", "item")
        menu = studio_menu(
            f"DRAWING  {item.data.get('symbol') or kind}",
            {"pipe": "Connection", "symbol": "Equipment symbol",
             "text": "Label",
             "stream_connector": "Off-page process stream"}.get(
                 kind, "Shape"))

        def pick(key):
            if is_headless():
                item.data[key] = "#FF0000"
                item.update()
                self.mark_unsaved()
                return
            colour = QColorDialog.getColor(parent=self)
            if colour.isValid():
                self.checkpoint()
                item.data[key] = colour.name()
                item.update()
                self.mark_unsaved()

        from PySide6.QtWidgets import QMenu as _QMenu

        def set_data(key, value):
            self.checkpoint()
            item.data[key] = value
            if key == "rot":
                item.apply_rotation()
            item.update()
            self.reroute_pipes()
            self.mark_unsaved()

        format_item = menu.addAction("Format...")
        format_font = format_item.font()
        format_font.setBold(True)
        format_item.setFont(format_font)
        format_item.setToolTip(
            "Open this object's properties; double-click does the same")
        format_item.triggered.connect(
            lambda _checked=False, it=item:
            self.edit_drawing_format(it))
        menu.addSeparator()

        # Keep the top level short.  The former menu appended the complete
        # element command set and then appended Clipboard/Arrange/Object a
        # second time, producing duplicate Lock, Copy, Duplicate, Group,
        # rotation, order and Delete entries.  Commercial drawing tools put
        # detailed choices behind stable task-oriented submenus.
        style_menu = _QMenu("Style", menu)
        if item.data.get("kind") in ("symbol", "stream_connector",
                                     "rect", "square",
                                     "polygon", "hexagon",
                                     "ellipse"):
            fill = style_menu.addAction("Fill colour…")
            fill.triggered.connect(lambda: pick("fill"))
        line = style_menu.addAction("Line colour…")
        line.triggered.connect(lambda: pick("line"))

        width_menu = _QMenu("Line thickness", style_menu)
        for width in (1, 2, 3, 4, 6):
            act = width_menu.addAction(f"{width} px")
            act.triggered.connect(
                lambda checked=False, w=width: set_data("width", w))
        style_menu.addMenu(width_menu)
        line_style_menu = _QMenu("Line type", style_menu)
        for style in ("solid", "dash", "dot", "dashdot"):
            act = line_style_menu.addAction(style.title())
            act.triggered.connect(
                lambda checked=False, s=style: set_data("style", s))
        style_menu.addMenu(line_style_menu)
        from azeo_control_trainer.core.hmi.pvms.rendering.crossing import CROSSOVER_KINDS
        if item.data.get("kind") in CROSSOVER_KINDS:
            # The paper's Crossover Effect (pp.27–28): where this
            # line crosses another, gap or jump.
            crossover_menu = _QMenu("Crossover effect", style_menu)
            current = item.data.get("crossover", "")
            for value, label in (("", "None (continuous)"),
                                 ("gap", "Break at crossings"),
                                 ("jump", "Jump over crossings")):
                act = crossover_menu.addAction(
                    label + ("   ✓" if current == value else ""))
                act.triggered.connect(
                    lambda checked=False, v=value:
                    (set_data("crossover", v),
                     item.scene().update()))
            style_menu.addMenu(crossover_menu)
        route_menu = None
        if item.data.get("kind") == "pipe":
            route_menu = _QMenu("Routing", menu)
            junction = route_menu.addAction("Insert branch junction here")
            junction.setToolTip("Split this run into two attached pipes; connect a branch to the new dot")
            junction_point = self.canvas.mapToScene(self.canvas.viewport().mapFromGlobal(global_pos))
            junction.triggered.connect(lambda _checked=False, pipe=item, point=junction_point:
                                       self.insert_pipe_junction(pipe, point))
            align_endpoints = route_menu.addAction(
                "Align endpoints (straight line)")
            align_endpoints.setToolTip(
                "Move the connected equipment across this pipe's axis so "
                "both authored symbol ports line up exactly")
            align_endpoints.setEnabled(
                self.can_align_pipe_endpoints((item,)))
            align_endpoints.triggered.connect(
                lambda _checked=False, pipe=item:
                self.align_pipe_endpoints((pipe,)))
            route_menu.addSeparator()
            route_status = route_menu.addAction(
                f"Status: {getattr(item, 'route_status', 'unrouted')}"
                + (f" — {item.route_message}" if item.route_message else ""))
            route_status.setEnabled(False)
            obstacles = route_menu.addAction("Show blocking objects")
            obstacles.setEnabled(bool(item.route_collisions))
            obstacles.triggered.connect(
                lambda _checked=False, pipe=item: self.show_route_obstructions(pipe))
            reroute = route_menu.addAction("Reroute now")
            reroute.triggered.connect(self.reroute_pipes)
            automatic = route_menu.addAction("Automatic obstacle routing")
            automatic.setCheckable(True)
            automatic.setChecked(item.data.get("route_mode", "auto") != "manual")

            def reset_route():
                self.checkpoint()
                item.data.pop("route_mode", None)
                item.data.pop("route_points", None)
                self.reroute_pipes()
                self.mark_unsaved()
            automatic.triggered.connect(reset_route)
            manual = route_menu.addAction("Convert to manual route")

            def make_manual():
                self.checkpoint()
                item.data["route_mode"] = "manual"
                item.data["route_points"] = [
                    [point.x(), point.y()] for point in item._points[1:-1]]
                self.reroute_pipes()
                self.mark_unsaved()
            manual.triggered.connect(make_manual)
            repair = route_menu.addAction("Repair manual waypoints")
            repair.setEnabled(item.data.get("route_mode") == "manual")

            def repair_route():
                from azeo_control_trainer.core.hmi.pvms.rendering.pipe_scene import scene_bounds
                from azeo_control_trainer.core.hmi.pvms.rendering.routing import repair_manual_waypoints
                self.checkpoint()
                repaired = repair_manual_waypoints(
                    item.data.get("route_points", ()),
                    grid=float(getattr(self, "grid_size", 8) or 8),
                    bounds=scene_bounds(self.canvas.scene()))
                item.data["route_points"] = [point.to_list()
                                             for point in repaired]
                self.reroute_pipes()
                self.mark_unsaved()
            repair.triggered.connect(repair_route)
            clearance = route_menu.addAction(
                f"Clearance… ({float(item.data.get('clearance', 12)):g} px)")

            def set_clearance():
                from PySide6.QtWidgets import QInputDialog
                value, accepted = QInputDialog.getDouble(
                    self, "Pipe clearance", "Obstacle clearance (px)",
                    float(item.data.get("clearance", 12)), 0, 256, 1)
                if accepted:
                    self.checkpoint()
                    if abs(value - 12.0) < 1e-9:
                        item.data.pop("clearance", None)
                    else:
                        item.data["clearance"] = value
                    self.reroute_pipes()
                    self.mark_unsaved()
            clearance.triggered.connect(set_clearance)
            route_menu.addSeparator()
            hint = route_menu.addAction(
                "Double-click a segment to add; drag square bends to edit")
            hint.setEnabled(False)
            arrow_menu = _QMenu("Arrows", style_menu)
            for label, value in (("None", ""), ("End", "end"),
                                 ("Start", "start"), ("Both", "both")):
                act = arrow_menu.addAction(label)
                act.triggered.connect(
                    lambda checked=False, v=value:
                    set_data("arrow", v))
            arrow_menu.addSeparator()
            for label, value in (("Filled head", "filled"),
                                 ("Open head", "open")):
                act = arrow_menu.addAction(label)
                act.triggered.connect(
                    lambda checked=False, v=value:
                    set_data("arrow_shape", v))
            style_menu.addMenu(arrow_menu)
        style_menu.addSeparator()
        copy_style = style_menu.addAction("Copy style")
        copy_style.triggered.connect(lambda: self.copy_drawing_style(item))
        paste_style = style_menu.addAction("Paste style")
        paste_style.setEnabled(
            getattr(self, "_style_clipboard", None) is not None)
        paste_style.triggered.connect(lambda: self.paste_drawing_style())
        reset_style = style_menu.addAction("Reset style")

        def reset():
            self.checkpoint()
            item.data.pop("fill", None)
            item.data.pop("line", None)
            item.data.pop("width", None)
            item.data.pop("style", None)
            item.update()
            self.mark_unsaved()
        reset_style.triggered.connect(reset)
        menu.addMenu(style_menu)
        if route_menu is not None:
            menu.addMenu(route_menu)

        # ── PRIMARY EDIT COMMANDS ────────────────────────────
        # These are intentionally the only repeated-everywhere commands kept
        # at the top level.  Everything specialized lives in one submenu.
        menu.addSection("Clipboard")
        window = self.window()
        for label, method in (("Cut\tCtrl+X", "_copy_selected"),
                              ("Copy\tCtrl+C", "_copy_selected")):
            act = menu.addAction(label)
            if hasattr(window, method):
                is_cut = label.startswith("Cut")
                act.triggered.connect(
                    lambda checked=False, cut=is_cut:
                    (getattr(window, "_copy_selected")(),
                     self.delete_selected() if cut else None))
        dup = menu.addAction("Duplicate\tCtrl+D")
        dup.triggered.connect(self.duplicate_drawing_selected)

        # ── ARRANGE ───────────────────────────────────────────
        arrange_menu = _QMenu("Arrange", menu)
        order_menu = _QMenu("Order", arrange_menu)
        for label, delta in (("Bring to front\tCtrl+Shift+]", 1000),
                             ("Bring forward\tCtrl+]", 1),
                             ("Send backward\tCtrl+[", -1),
                             ("Send to back\tCtrl+Shift+[", -1000)):
            act = order_menu.addAction(label)
            act.triggered.connect(
                lambda checked=False, d=delta: self.z_shift(d))
        arrange_menu.addMenu(order_menu)

        chosen = len(self.selection.editable_items())
        groupable = len(self.selection.editable_items(include_pipes=True))
        align_menu = _QMenu("Align", arrange_menu)
        align_menu.setEnabled(chosen >= 2)
        for label, edge in (("Left", "left"), ("Centre", "hcenter"),
                            ("Right", "right"), ("Top", "top"),
                            ("Middle", "vcenter"),
                            ("Bottom", "bottom")):
            action = align_menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, e=edge: self.align_selected(e))
        arrange_menu.addMenu(align_menu)

        distribute_menu = _QMenu("Distribute", arrange_menu)
        distribute_menu.setEnabled(chosen >= 3)
        for label, axis in (("Horizontally", "h"), ("Vertically", "v")):
            distribute_menu.addAction(label).triggered.connect(
                lambda _checked=False, a=axis:
                self.distribute_selected(a))
        arrange_menu.addMenu(distribute_menu)

        same_size_menu = _QMenu("Make Same Size", arrange_menu)
        same_size_menu.setEnabled(chosen >= 2)
        for label, dimension in (("Width", "width"),
                                 ("Height", "height"),
                                 ("Width and Height", "both")):
            same_size_menu.addAction(label).triggered.connect(
                lambda _checked=False, d=dimension:
                self.equalize_selected(d))
        arrange_menu.addMenu(same_size_menu)

        page_menu = _QMenu("Align to Page", arrange_menu)
        page_menu.setEnabled(not self.canvas.page_rect().isEmpty())
        for label, anchor_name in (
                ("Left Safe Edge", "left"),
                ("Right Safe Edge", "right"),
                ("Top Safe Edge", "top"),
                ("Bottom Safe Edge", "bottom"),
                ("Horizontal Centre", "hcenter"),
                ("Vertical Centre", "vcenter"),
                ("Page Centre", "center")):
            page_menu.addAction(label).triggered.connect(
                lambda _checked=False, a=anchor_name:
                self.align_selected_to_page(a))
        page_menu.addSeparator()
        page_menu.addAction("Move Inside Safe Area").triggered.connect(
            self.contain_selected_in_page)
        arrange_menu.addMenu(page_menu)
        arrange_menu.addSeparator()
        group_act = arrange_menu.addAction("Group\tCtrl+G")
        group_act.setEnabled(groupable >= 2)
        group_act.triggered.connect(self.group_selected)
        ungroup_act = arrange_menu.addAction("Ungroup\tCtrl+Shift+G")
        ungroup_act.setEnabled(any(
            self._group_of(selected)
            for selected in self.selection.editable_items(
                include_pipes=True)))
        ungroup_act.triggered.connect(self.ungroup_selected)
        menu.addMenu(arrange_menu)

        # ── TRANSFORM ─────────────────────────────────────────
        if item.data.get("kind") != "pipe":
            transform_menu = _QMenu("Transform", menu)
            if item.data.get("kind") == "symbol":
                native = transform_menu.addAction("Reset size")

                def reset_size():
                    from azeo_control_trainer.core.hmi.pvms.symbols import aspect
                    self.checkpoint()
                    item.data["w"] = 110.0
                    item.data["h"] = 110.0 * aspect(
                        item.data.get("symbol", ""))
                    item.prepareGeometryChange()
                    item.setRect(0, 0, item.data["w"], item.data["h"])
                    self.reroute_pipes()
                    self.mark_unsaved()
                native.triggered.connect(reset_size)
                transform_menu.addSeparator()
            rot_l = transform_menu.addAction("Rotate left 90°")
            rot_l.triggered.connect(lambda: self.rotate_selected(-90.0))
            rot_r = transform_menu.addAction("Rotate right 90°")
            rot_r.triggered.connect(lambda: self.rotate_selected(90.0))
            rot_0 = transform_menu.addAction("Reset rotation")
            rot_0.setEnabled(bool(item.data.get("rot")))
            rot_0.triggered.connect(
                lambda: (set_data("rot", 0.0), item.apply_rotation()))
            transform_menu.addSeparator()
            mirror_h = transform_menu.addAction("Mirror horizontal")
            mirror_h.triggered.connect(
                lambda: set_data("mx", not item.data.get("mx", False)))
            mirror_v = transform_menu.addAction("Mirror vertical")
            mirror_v.triggered.connect(
                lambda: set_data("my", not item.data.get("my", False)))
            reset_mirror = transform_menu.addAction("Reset mirroring")
            reset_mirror.setEnabled(bool(
                item.data.get("mx") or item.data.get("my")))
            reset_mirror.triggered.connect(
                lambda: (set_data("mx", False), set_data("my", False)))
            menu.addMenu(transform_menu)

        # ── CONNECTIONS ─────────────────────────────────────────
        if item.data.get("kind") != "pipe":
            connection_menu = _QMenu("Connections", menu)
            straight = connection_menu.addAction(
                "Straighten Connected Run")
            straight.setEnabled(self.can_straighten_connected_run(item))
            straight.triggered.connect(
                lambda _checked=False, it=item:
                self.straighten_connected_run(it))
            connection_menu.addAction(
                "Reroute connected lines").triggered.connect(
                    self.reroute_pipes)
            if item.data.get("kind") != "stream_connector":
                connection_menu.addSeparator()
                scene_click = self.canvas.mapToScene(
                    self.canvas.mapFromGlobal(global_pos))
                add_cp = connection_menu.addAction(
                    "Add connection point here")
                add_cp.triggered.connect(
                    lambda checked=False, s=scene_click:
                    (self.checkpoint(), item.add_connection_point(s),
                     self.mark_unsaved()))
                edit_cp = connection_menu.addAction(
                    "Edit connection points")
                edit_cp.setCheckable(True)
                edit_cp.setChecked(getattr(item, "_cp_edit", False))
                edit_cp.toggled.connect(
                    lambda on: (setattr(item, "_cp_edit", on), item.update()))
                hit = item.anchor_hit(item.mapFromScene(scene_click))
                if hit is not None and hit.startswith("c"):
                    connection_menu.addAction(
                        "Remove connection point").triggered.connect(
                            lambda checked=False, s=hit:
                            self.drop_connection_point(item, s))
            menu.addMenu(connection_menu)

        # ── REUSABLE COMPONENT ─────────────────────────────
        component_menu = _QMenu("Reusable Component", menu)
        convert = component_menu.addAction("Convert to PVM class…")
        convert.triggered.connect(
            lambda: self._convert_selection_dialog("pvm"))
        convert_fp = component_menu.addAction(
            "Convert to Faceplate class…")
        convert_fp.triggered.connect(
            lambda: self._convert_selection_dialog("faceplate"))
        if item.data.get("kind") == "symbol":
            component_menu.addAction(
                "Convert to live vessel (bar + trend)…").triggered.connect(
                    lambda _checked=False, it=item:
                    self._convert_to_vessel_pvm(it))
        if item.data.get("user_pvm"):
            pvm_name = item.data["user_pvm"]
            group = item.data.get("group", "")
            linked = item.data.get("pvm_link", "linked") == "linked"
            component_menu.addSeparator()
            component_menu.addSection(f"PVM  {pvm_name}")
            link_action = component_menu.addAction(
                "Unlink instance" if linked else "Relink instance")
            link_action.triggered.connect(
                lambda _=False, g=group, current=linked:
                self.set_user_pvm_link(
                    g, "unlinked" if current else "linked"))
            unlink_all = component_menu.addAction(
                "Unlink instance and nested PVMs")
            unlink_all.setEnabled(linked)
            unlink_all.triggered.connect(
                lambda _=False, g=group:
                self.set_user_pvm_link(g, "unlinked", True))
            configure = component_menu.addAction("Configure instance…")
            configure.triggered.connect(
                lambda _=False, it=item:
                self._user_pvm_choices_dialog(it))
            bind_shape = component_menu.addAction("Shape binding…")
            bind_shape.triggered.connect(
                lambda _=False, it=item:
                self._shape_binding_dialog(it))
            open_cfg = component_menu.addAction(
                "Configuration designer…")
            open_cfg.triggered.connect(
                lambda _=False, n=pvm_name:
                getattr(self.window(), "open_pvm_config",
                        lambda *_a: None)(n))
            if self.user_library().entries.get(
                    pvm_name, {}).get("definition_kind", "pvm") == "pvm":
                pair = component_menu.addAction(
                    "Pair with Faceplate class…")
                pair.triggered.connect(
                    lambda _=False, it=item:
                    self._pair_faceplate_dialog(it))
        menu.addMenu(component_menu)

        # ── OBJECT STATE ─────────────────────────────────────────
        visible = bool(item.data.get("visible", True))
        locked = bool(item.data.get("locked", False))
        menu.addSeparator()
        visibility = menu.addAction("Hide" if visible else "Show")
        visibility.triggered.connect(
            lambda: self._set_element_flag(item, "visible", not visible))
        lock = menu.addAction("Unlock" if locked else "Lock\tCtrl+L")
        lock.triggered.connect(
            lambda: self._set_element_flag(item, "locked", not locked))
        menu.addSeparator()
        delete = menu.addAction("Delete\tDel")
        delete.triggered.connect(self.delete_selected)
        retain_menu(self, menu, "_context_menu")
        if not is_headless():
            menu.exec_transient(global_pos)

    def edit_drawing_format(self, item):
        """Select one drawing object and enter its direct Format editor."""
        if item is None or item.scene() is not self.canvas.scene():
            return None
        if self.mode != MODE_EDIT:
            self.enter_edit()
        self.selection.replace((item,), primary=item)
        window = self.window()
        ribbon = getattr(window, "ribbon_tabs", None)
        ribbon_names = tuple(getattr(window, "_RIBBON", ()))
        if ribbon is not None and "Format" in ribbon_names:
            ribbon.setCurrentIndex(ribbon_names.index("Format"))
        return self.pane.begin_item_format(item)

    def duplicate_drawing_selected(self) -> int:
        """Ctrl+D on drawing items: copies offset one grid step.

        A connector between two copied shapes is copied too, re-pointed
        at the COPIES — duplicating a connected pair and getting two
        loose shapes means rebuilding by hand what was already drawn.
        A connector with only one end in the selection is not copied:
        it would have to attach to the original, which is a line the
        engineer did not draw.
        """
        return self.duplicate_selected()

    def _copy_pipes(self, pipe_data: list, remap: dict,
                    dx: float = 0.0, dy: float = 0.0,
                    group_remap: dict | None = None) -> int:
        """Re-create every pipe whose BOTH endpoints were copied,
        pointing it at the copies. `remap` is old id -> new id."""
        count = 0
        for original in pipe_data:
            a, b = original.get("a"), original.get("b")
            if a not in remap or b not in remap:
                continue
            data = copy.deepcopy(original)
            data["id"] = f"itm_{uuid.uuid4().hex[:4]}"
            data["a"], data["b"] = remap[a], remap[b]
            data["x"] = data.get("x", 0) + dx
            data["y"] = data.get("y", 0) + dy
            if data.get("route_points"):
                data["route_points"] = [[x + dx, y + dy]
                                        for x, y in data["route_points"]]
            group = data.get("group")
            if group and group_remap is not None:
                data["group"] = group_remap.setdefault(
                    group, f"grp_{uuid.uuid4().hex[:8]}")
            self._restore_item(data)
            count += 1
        return count

    def z_shift(self, delta: int) -> int:
        """Bring forward / send backward — ±1 steps, ±1000 to the ends."""
        selected = self.selection.editable_items(include_pipes=True)
        if not selected or not delta:
            return 0
        state = self._bulk_begin()
        for item in selected:
            z = item.zValue() + delta
            item.setZValue(z)
            if isinstance(item, PvmItem):
                item.pvm = Pvm(**{**item.pvm.__dict__, "z": z})
            else:
                item.data["z"] = z
        return len(selected) if self._bulk_end(state) else 0

    # ---------------------------------------------------- static drawing
    def _build_user_pvm_choices_dialog(self, item):
        """Build the real public instance editor without running it.

        Documentation capture and headless regression tests need to inspect
        the exact editor that engineers use.  Separating construction from
        modal execution also makes the public/internal boundary explicit:
        internal class properties never enter ``properties`` and therefore
        cannot leak into placement configuration.
        """
        name = item.data.get("user_pvm", "")
        config = self._config_for_name(name)
        properties = [p for p in (config.public_properties()
                                  if config else [])]
        if not properties:
            return None
        from PySide6.QtWidgets import QCheckBox as _QCheck
        from PySide6.QtWidgets import QComboBox as _QCB
        from PySide6.QtWidgets import QDialogButtonBox as _QBB
        from PySide6.QtWidgets import QFormLayout as _QFL
        from PySide6.QtWidgets import QLineEdit as _QLE
        dialog = QDialog(self)
        dialog.setWindowTitle(f"{name} — configure instance")
        apply_compact_authoring_dialog(dialog)
        form = _QFL(dialog)
        choices = dict(item.data.get("pvm_choices", {}))
        editors = {}
        for prop in properties:
            if not config.is_present(prop.name, choices):
                continue
            current = choices.get(prop.name, prop.default)
            if prop.ptype == "Selection" and prop.options:
                editor = _QCB()
                editor.addItems([o.name for o in prop.options])
                editor.setCurrentText(str(current))
            elif prop.ptype == "Boolean":
                editor = _QCheck()
                editor.setChecked(str(current).strip().lower() in (
                    "true", "1", "yes", "on"))
            else:
                editor = _QLE(str(current))
                if prop.ptype in ("Control Tag", "Function Block Reference",
                                  "Parameter Reference"):
                    editor.setPlaceholderText("MODULE/BLOCK[/PARAMETER]")
            editors[prop.name] = (prop.ptype, editor)
            label = prop.title or prop.name
            form.addRow(label + (" *" if prop.required else ""), editor)
        buttons = _QBB(_QBB.Ok | _QBB.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        style_dialog_buttons(buttons, _QBB.Ok)
        form.addRow(buttons)
        return dialog, editors, choices, properties

    def _user_pvm_choices_dialog(self, item) -> None:
        """Edit public choices and re-instantiate the resolved class group."""
        from azeo_control_trainer.core.presentation.headless import is_headless
        if is_headless():
            return
        built = self._build_user_pvm_choices_dialog(item)
        if built is None:
            name = item.data.get("user_pvm", "")
            QMessageBox.information(
                self, name,
                "This class has no configuration document yet — "
                "author one in the Configuration designer (same "
                "menu), then its typed properties appear here.")
            return
        dialog, editors, choices, properties = built
        if dialog.exec() == QDialog.Accepted:
            for prop_name, (ptype, editor) in editors.items():
                if ptype == "Selection":
                    value = editor.currentText()
                elif ptype == "Boolean":
                    value = "true" if editor.isChecked() else "false"
                else:
                    value = editor.text()
                choices[prop_name] = value
            missing = [
                prop.title or prop.name for prop in properties
                if prop.required and not str(
                    choices.get(prop.name, prop.default)).strip()]
            if missing:
                QMessageBox.warning(
                    self, "Required class parameters",
                    "Enter a value for: " + ", ".join(missing))
                return
            self.set_user_pvm_choices(
                item.data.get("group", ""), choices)

    def _shape_binding_dialog(self, item) -> None:
        """Author one shape's binding: pick the property, type the
        Pvm.… reference — written to the class, every instance
        follows."""
        from azeo_control_trainer.core.presentation.headless import is_headless
        if is_headless():
            return
        from PySide6.QtWidgets import QComboBox as _QCB
        from PySide6.QtWidgets import QDialogButtonBox as _QBB
        from PySide6.QtWidgets import QFormLayout as _QFL
        from PySide6.QtWidgets import QLineEdit as _QLE
        dialog = QDialog(self)
        dialog.setWindowTitle(
            f"{item.data.get('user_pvm')} — shape binding")
        apply_compact_authoring_dialog(dialog)
        form = _QFL(dialog)
        prop_combo = _QCB()
        from azeo_control_trainer.core.hmi.pvms.user_library import UserPvmLibrary
        prop_combo.addItems(list(UserPvmLibrary.SHAPE_BINDABLE))
        form.addRow("Shape property", prop_combo)
        reference = _QLE()
        reference.setPlaceholderText(
            "Pvm.Orientation.BodyRot   ·   empty clears")
        form.addRow("Reference", reference)
        from PySide6.QtWidgets import QLabel as _QL
        note = _QL("Bind appearance, text, geometry or visibility to a "
                   "typed PVM configuration property. Standard.<name> "
                   "may be used through the class document; present=false "
                   "omits the shape online.")
        note.setWordWrap(True)
        form.addRow(note)
        buttons = _QBB(_QBB.Ok | _QBB.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        style_dialog_buttons(buttons, _QBB.Ok)
        form.addRow(buttons)
        if dialog.exec() == QDialog.Accepted:
            self.bind_user_pvm_shape(item,
                                     prop_combo.currentText(),
                                     reference.text().strip())

    def _convert_to_vessel_pvm(self, item: StaticItem) -> None:
        """Replace a symbol with the registered live vessel PVM in place."""
        from azeo_control_trainer.core.presentation.headless import is_headless
        if is_headless():
            return
        from ..param_browser import ParameterBrowserDialog

        chosen = ParameterBrowserDialog.browse(
            self.graphs_provider, "", parent=self, selection="block")
        if chosen is None:
            return
        path, block_type = chosen
        block_type = str(block_type).upper()
        if block_type not in ("AI", "PID"):
            QMessageBox.warning(
                self, "Live vessel",
                "A live vessel requires an AI measurement or PID loop.")
            return

        self.checkpoint()
        data = item.data
        pvm_data = {
            "id": str(data.get("id")),
            "class": f"{block_type}/dynamo_inline",
            "variant": "vessel",
            "params": {"path": path},
            "choices": {"equipment_symbol": str(
                data.get("symbol", "vessel")),
                "show_hp_anatomy": False,
                "show_readout": False},
            "x": item.pos().x(), "y": item.pos().y(),
            "w": item.rect().width(), "h": item.rect().height(),
            "standard": "hphmi.controller",
        }
        scene = self.canvas.scene()
        self.renderer.unbind(item)
        scene.removeItem(item)
        pvm_item = self.renderer.build_pvm(pvm_from_dict(pvm_data))
        scene.addItem(pvm_item)
        pvm_item.setSelected(True)
        self.reroute_pipes(pvm_item)
        self.mark_unsaved()

    def _convert_selection_dialog(self, definition_kind: str = "pvm") -> None:
        """Name a reusable PVM or faceplate and refresh the palette."""
        from azeo_control_trainer.core.presentation.headless import is_headless
        faceplate = definition_kind == "faceplate"
        prefix = "UserFaceplate" if faceplate else "UserPVM"
        name = f"{prefix}{len(self.user_library().names()) + 1}"
        if not is_headless():
            from PySide6.QtWidgets import QInputDialog
            name, ok = QInputDialog.getText(
                self,
                f"Convert to {'Faceplate' if faceplate else 'PVM'} class",
                "Class name:",
                text=name)
            if not ok or not name:
                return
        if self.convert_selection_to_component(
                name.strip(), definition_kind):
            window = self.window()
            if hasattr(window, "refresh_user_pvms"):
                window.refresh_user_pvms()

    def _pair_faceplate_dialog(self, item) -> None:
        """Choose the reusable popup a compact user PVM calls."""
        choices = self.user_library().names(definition_kind="faceplate")
        if not choices:
            return
        from azeo_control_trainer.core.presentation.headless import is_headless
        selected = choices[0]
        if not is_headless():
            from PySide6.QtWidgets import QInputDialog
            selected, ok = QInputDialog.getItem(
                self, "Pair PVM with Faceplate", "Faceplate class:",
                choices, 0, False)
            if not ok:
                return
        pvm_name = str(item.data.get("user_pvm", ""))
        if self.user_library().pair_faceplate(pvm_name, selected):
            self.set_user_pvm_choices(
                item.data.get("group", ""),
                dict(item.data.get("pvm_choices", {})))

    # -------------------------------- user PVMs (Convert to PVM)
    def user_library(self):
        from azeo_control_trainer.core.hmi.pvms.user_library import UserPvmLibrary
        if not hasattr(self, "_user_library"):
            self._user_library = UserPvmLibrary(self.store.root)
        return self._user_library

    def _config_for_name(self, name: str):
        """A configuration document by class NAME — user PVM classes
        have no Python class, only their pvmcfg document."""
        # User classes are authored live; read fresh.
        self.renderer.forget_config(name)
        return self._pvm_config_named(name)

    def _standards_lookup(self, standard_name: str):
        try:
            from pathlib import Path as _P

            from azeo_control_trainer.core.hmi.pvms.standards import StandardsStore
            entry = StandardsStore(
                _P(self.store.root).parent).get(standard_name)
            return entry["value"] if entry else None
        except Exception:                           # noqa: BLE001
            return None

    @staticmethod
    def _uuid_text(value: object) -> str:
        try:
            return str(uuid.UUID(str(value)))
        except (TypeError, ValueError, AttributeError):
            return ""

    def _user_instance_state(self, group: str) -> dict | None:
        """One expanded instance's durable identity and placement state."""
        members = [item for item in self._static_items()
                   if item.data.get("group") == group]
        if not members:
            return None
        first = next((item for item in members
                      if item.data.get("instance_definition")), members[0])
        data = first.data
        name = str(data.get("instance_definition")
                   or data.get("user_pvm", ""))
        if not name:
            return None
        from azeo_control_trainer.core.hmi.pvms.class_revisions import revision_root
        from azeo_control_trainer.core.hmi.pvms.user_library import UserPvmLibrary
        library = UserPvmLibrary(revision_root(self.store.root, data.get("class_revision", "")))
        library.reload()
        entry = library.entries.get(name) or {}
        stored_shapes = [row for row in entry.get("items", ())
                         if row.get("kind") not in ("pipe", "nested_pvm")]
        definition_id, current_definition_revision = \
            library.definition_metadata(name)
        placed_definition_id = self._uuid_text(data.get("definition_id"))
        try:
            placed_definition_revision = max(
                0, int(data.get("definition_revision", 0) or 0))
        except (TypeError, ValueError):
            placed_definition_revision = 0
        instance_id = self._uuid_text(data.get("instance_id")) \
            or str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                USER_INSTANCE_ID_SEED + f"{self.display.name}:{group}"))

        origin = None
        for member in members:
            source_x = member.data.get("source_x")
            source_y = member.data.get("source_y")
            if source_x is None or source_y is None:
                try:
                    stored = stored_shapes[int(member.data.get(
                        "pvm_index", -1))]
                    source_x = float(stored.get("x", 0))
                    source_y = float(stored.get("y", 0))
                except (IndexError, TypeError, ValueError):
                    continue
            origin = (float(member.data.get("x", 0)) - float(source_x),
                      float(member.data.get("y", 0)) - float(source_y))
            break
        if origin is None:
            origin = (min(float(item.data.get("x", 0)) for item in members),
                      min(float(item.data.get("y", 0)) for item in members))
        original_origin = (
            float(data.get("instance_origin_x", origin[0])),
            float(data.get("instance_origin_y", origin[1])),
        )
        source_by_index = {}
        for member in members:
            try:
                index = int(member.data.get("pvm_index", -1))
            except (TypeError, ValueError):
                continue
            source_id = self._uuid_text(
                member.data.get("source_element_id"))
            if source_id:
                source_by_index[index] = source_id
        raw_overrides = dict(data.get("instance_overrides")
                             or data.get("pvm_overrides", {}))
        overrides = library.normalize_overrides(
            name, raw_overrides, source_by_index=source_by_index,
            entry=entry)
        return {
            "class_revision": data.get("class_revision", ""),
            "group": group,
            "members": members,
            "name": name,
            "link": str(data.get("instance_link")
                        or data.get("pvm_link", "linked")),
            "choices": dict(data.get("instance_choices")
                            or data.get("pvm_choices", {})),
            "overrides": overrides,
            "source_by_index": source_by_index,
            "instance_id": instance_id,
            "definition_id": definition_id,
            "definition_revision": current_definition_revision,
            "placed_definition_id": placed_definition_id,
            "placed_definition_revision": placed_definition_revision,
            "origin": origin,
            "original_origin": original_origin,
            "stored_shapes": stored_shapes,
            "detached_nested": copy.deepcopy(
                data.get("instance_detached_nested", {})),
        }

    def _source_id_for_member(self, item, state: dict) -> str:
        source_id = self._uuid_text(item.data.get("source_element_id"))
        if source_id:
            return source_id
        try:
            index = int(item.data.get("pvm_index", -1))
            stored = state["stored_shapes"][index]
        except (IndexError, TypeError, ValueError):
            try:
                index = max(0, int(item.data.get("pvm_index", 0) or 0))
            except (TypeError, ValueError):
                index = 0
            stored = {"id": item.data.get("id", "")}
        library = self.user_library()
        return self._uuid_text(stored.get("source_element_id")) \
            or library._legacy_source_id(state["name"], stored, index)

    def _instantiate_user_pvm(self, state: dict, *, choices: dict,
                              link: str, overrides: dict,
                              unlink_nested: bool = False) -> list:
        from azeo_control_trainer.core.hmi.pvms.class_revisions import revision_root, standards_root
        from azeo_control_trainer.core.hmi.pvms.user_library import UserPvmLibrary
        from azeo_control_trainer.core.hmi.pvms.standards import StandardsStore
        revision = state.get("class_revision", "")
        root = revision_root(self.store.root, revision)
        standards = StandardsStore(standards_root(root))
        items = UserPvmLibrary(root).instantiate(
            state["name"], state["origin"][0], state["origin"][1],
            config=self.renderer.pvm_config_named(state["name"], revision),
            choices=choices, standards=lambda name: (standards.get(name) or {}).get("value"),
            link=link, overrides=overrides,
            unlink_nested=(unlink_nested or bool(state["detached_nested"])),
            instance_id=state["instance_id"],
            instance_origin=state["original_origin"],
            group_id=state["group"],
            detached_nested=state["detached_nested"])
        for data in items:
            if revision:
                data["class_revision"] = revision
            self._restore_item(data)
        return items

    def _rebuild_user_instance(self, group: str, *, choices=None,
                               link: str | None = None, overrides=None,
                               unlink_nested: bool = False,
                               checkpoint: bool = True,
                               mark: bool = True) -> int:
        """Rebuild a linked snapshot without sacrificing field wiring.

        External pipes attach to the class member's stable source UUID, not
        its transient scene id.  A removed class member intentionally leaves
        that endpoint unresolved: verification can then name the fault and
        the engineer's route is never silently deleted.
        """
        state = self._user_instance_state(group)
        if state is None:
            return 0
        if self.mode != MODE_EDIT:
            self.enter_edit()
        next_choices = dict(state["choices"] if choices is None else choices)
        next_link = state["link"] if link is None else str(link)
        next_overrides = dict(
            state["overrides"] if overrides is None else overrides)
        if next_link not in ("linked", "unlinked"):
            return 0
        if checkpoint:
            self.checkpoint()

        members = state["members"]
        member_by_id = {item.data.get("id", ""): item for item in members}
        member_ids = set(member_by_id)
        internal_pipes = []
        attachments = []
        for pipe in self._pipe_items():
            data = pipe.data
            legacy_internal = data.get("a") in member_ids \
                and data.get("b") in member_ids
            stable_internal = bool(data.get("instance_internal")) \
                and data.get("instance_id") == state["instance_id"]
            if legacy_internal or stable_internal:
                internal_pipes.append(pipe)
                continue
            for end in ("a", "b"):
                endpoint = data.get(end)
                source_id = ""
                member = member_by_id.get(endpoint)
                if member is not None:
                    source_id = self._source_id_for_member(member, state)
                elif data.get(f"{end}_instance_id") \
                        == state["instance_id"]:
                    source_id = self._uuid_text(
                        data.get(f"{end}_source_element_id"))
                if source_id:
                    attachments.append((pipe, end, source_id))

        for member in members:
            self.renderer.unbind(member)
            self.canvas.scene().removeItem(member)
        for pipe in internal_pipes:
            self.canvas.scene().removeItem(pipe)

        created = self._instantiate_user_pvm(
            state, choices=next_choices, link=next_link,
            overrides=next_overrides, unlink_nested=unlink_nested)
        source_to_id = {
            data.get("source_element_id"): data.get("id")
            for data in created if data.get("kind") != "pipe"
        }
        for pipe, end, source_id in attachments:
            pipe.data[f"{end}_instance_id"] = state["instance_id"]
            pipe.data[f"{end}_source_element_id"] = source_id
            replacement = source_to_id.get(source_id)
            if replacement:
                pipe.data[end] = replacement
        self.reroute_pipes()
        if mark:
            self.mark_unsaved()
        return len(created)

    def refresh_user_pvm_class(self, class_name: str) -> int:
        """Refresh every linked authored instance in this editable draft.

        Class Save never acquires another engineer's lock and never touches a
        published revision.  Open displays that are not already editable are
        therefore skipped; their linked snapshots update when deliberately
        opened for engineering and republished.
        """
        from azeo_control_trainer.core.configuration.workspace import draft_root
        if self.mode != MODE_EDIT or not self._holds_lock or draft_root(self.store.root):
            return 0
        groups = []
        seen = set()
        for item in self._static_items():
            data = item.data
            if data.get("class_revision"):
                continue
            name = str(data.get("instance_definition")
                       or data.get("user_pvm", ""))
            group = str(data.get("group", ""))
            link = str(data.get("instance_link")
                       or data.get("pvm_link", "linked"))
            if name == class_name and group and group not in seen \
                    and link == "linked":
                seen.add(group)
                groups.append(group)
        if not groups:
            return 0
        self.checkpoint()
        refreshed = 0
        for group in groups:
            if self._rebuild_user_instance(
                    group, checkpoint=False, mark=False):
                refreshed += 1
        if refreshed:
            self.display.work_in_progress = True
            self.display.wip_reason = (
                f"Linked {class_name} class changed; review and publish "
                "this affected display.")
            self.mark_unsaved()
        return refreshed

    def refresh_stale_user_pvm_instances(self) -> int:
        """Bring linked snapshots current when their draft enters EDIT.

        VIEW is intentionally inert: merely opening an operator/read-only
        revision must never rewrite it.  The edit lock and one undo checkpoint
        make this an ordinary, reviewable draft change.
        """
        from azeo_control_trainer.core.configuration.workspace import draft_root
        if self.mode != MODE_EDIT or not self._holds_lock or draft_root(self.store.root):
            return 0
        groups = []
        classes = set()
        seen = set()
        for item in self._static_items():
            group = str(item.data.get("group", ""))
            if not group or group in seen:
                continue
            seen.add(group)
            state = self._user_instance_state(group)
            if state is None or state["link"] != "linked" or state.get("class_revision"):
                continue
            stale_id = bool(state["placed_definition_id"]) \
                and state["placed_definition_id"] != state["definition_id"]
            stale_revision = state["placed_definition_revision"] \
                < state["definition_revision"]
            if stale_id or stale_revision:
                groups.append(group)
                classes.add(state["name"])
        if not groups:
            return 0
        self.checkpoint()
        refreshed = sum(bool(self._rebuild_user_instance(
            group, checkpoint=False, mark=False)) for group in groups)
        if refreshed:
            self.display.work_in_progress = True
            names = ", ".join(sorted(classes))
            self.display.wip_reason = (
                f"Linked {names} class changed while this display was "
                "closed; review and publish this affected display.")
            self.mark_unsaved()
        return refreshed

    def set_user_pvm_choices(self, group: str,
                             choices: dict) -> int:
        """Reconfigure one placed instance: re-instantiate its group
        with the new choices resolved through the class document —
        the same engineering-act rebuild a Selection flip does."""
        return self._rebuild_user_instance(group, choices=choices)

    def bind_user_pvm_shape(self, item, key: str,
                            reference: str) -> bool:
        """Author a shape binding through a placed member: written to
        the CLASS (every instance follows), this instance refreshed."""
        name = item.data.get("user_pvm", "")
        index = item.data.get("pvm_index")
        if not name or index is None:
            return False
        if not self.user_library().set_shape_binding(
                name, int(index), key, reference):
            return False
        self.set_user_pvm_choices(
            item.data.get("group", ""),
            dict(item.data.get("pvm_choices", {})))
        return True

    def convert_selection_to_pvm(self, name: str) -> bool:
        """The Azeo creation flow: the selected drawing items
        become a named class in the user library, and the selection
        is replaced by an instance of it — same pixels, now one
        object."""
        return self.convert_selection_to_component(name, "pvm")

    def convert_selection_to_component(self, name: str,
                                       definition_kind: str = "pvm") -> bool:
        """Convert selected elements into one reusable class definition."""
        if definition_kind not in ("pvm", "faceplate"):
            return False
        selected = [i for i in self._static_items() if i.isSelected()]
        selected_pipes = [i for i in self._pipe_items()
                          if i.isSelected()]
        if not name or not selected:
            return False
        self.enter_edit()
        self.checkpoint()
        data = [dict(i.data) for i in selected] \
            + [dict(i.data) for i in selected_pipes]
        entry = self.user_library().add(
            name, data,
            folder=("My Faceplates" if definition_kind == "faceplate"
                    else "My PVMs"),
            definition_kind=definition_kind)
        if entry is None:
            return False
        origin_x = min(i.data.get("x", 0) for i in selected)
        origin_y = min(i.data.get("y", 0) for i in selected)
        for item in (*selected, *selected_pipes):
            self.canvas.scene().removeItem(item)
        self.place_user_pvm(name, origin_x, origin_y)
        self.mark_unsaved()
        return True

    def place_user_pvm(self, name: str, x: float, y: float,
                       choices: dict | None = None, *, link="linked",
                       overrides=None, unlink_nested=False,
                       instance_id: str = "",
                       instance_origin: tuple[float, float] | None = None,
                       group_id: str = "") -> int:
        """One placement of a user PVM class: fresh grouped items,
        shape bindings resolved through the class's configuration."""
        if self.mode != MODE_EDIT:
            self.enter_edit()
        items = self.user_library().instantiate(
            name, x, y, config=self._config_for_name(name),
            choices=choices, standards=self._standards_lookup,
            link=link, overrides=overrides,
            unlink_nested=unlink_nested, instance_id=instance_id,
            instance_origin=instance_origin, group_id=group_id)
        for data in items:
            self._restore_item(data)
        if items:
            self.reroute_pipes()
            self.mark_unsaved()         # syncs the document itself
        return len(items)

    def insert_faceplate_section(self, key: str, x: float = 0.0,
                                 y: float = 0.0, *, declared=None):
        """Insert one faceplate section as ONE undo step.

        Returns ``(count, missing)`` — how many items were added and the
        typed class properties the section reads that this class does not
        declare yet. The section is still inserted when something is
        missing: an unresolved binding is visible on the canvas, and the
        author is usually laying out before configuring. What must not
        happen is inserting it silently.
        """
        from uuid import uuid4

        from azeo_control_trainer.core.hmi.pvms import faceplate_sections

        if key not in faceplate_sections.SECTIONS:
            return 0, ()
        if self.mode != MODE_EDIT:
            self.enter_edit()
        # A fresh prefix per insert: two Value rows on one faceplate must
        # not share `fp_pv`, or the second would overwrite the first's
        # identity on save.
        prefix = f"fs{uuid4().hex[:12]}_"
        items = faceplate_sections.items_at(key, x, y, prefix=prefix)
        if not items:
            return 0, ()
        # Rule 1: one gesture is one undo step. Checkpoint once for the
        # whole section, never per item.
        self.checkpoint()
        for data in items:
            self._restore_item(data)
        self.reroute_pipes()
        self.mark_unsaved()
        if declared is None:
            declared = self._declared_class_properties()
        return (len(items),
                faceplate_sections.missing_properties(key, declared))

    def insert_live_section(self, key: str, x: float = 0.0,
                            y: float = 0.0, *, control_tag: str = "",
                            block: str = "SEQ1"):
        """Place a hosted faceplate section — a condition table or a
        sequencer body — as ONE undo step.

        For a sequencer the whole binding map is generated from one
        control tag: thirty-three paths that would otherwise be entered
        by hand, with no way to loop in a graphics script either.
        """
        from uuid import uuid4

        from azeo_control_trainer.core.hmi.pvms import faceplate_sections

        spec = faceplate_sections.LIVE_SECTIONS.get(key)
        if spec is None:
            return 0, ()
        if self.mode != MODE_EDIT:
            self.enter_edit()
        width, height = spec["size"]
        paths: dict = {}
        if key == "state_list":
            paths = faceplate_sections.sequence_paths(
                control_tag or "Pvm.ControlTag", block)
        elif control_tag:
            paths = {name: f"{control_tag}/{name}"
                     for name in spec.get("keys", ())}
        data = {"kind": "faceplate_section",
                "id": f"fs{uuid4().hex[:12]}_{key}",
                "section": key, "x": float(x), "y": float(y),
                "w": width, "h": height, "paths": paths}
        self.checkpoint()
        self._restore_item(data)
        self.mark_unsaved()
        return 1, tuple(sorted(paths))

    def _declared_class_properties(self) -> tuple[str, ...]:
        """Typed property names of the class this tab is editing, if any."""
        name = ""
        display = str(getattr(self.display, "name", "") or "")
        if display.startswith(PvmStudio.PVM_EDIT_PREFIX):
            name = display[len(PvmStudio.PVM_EDIT_PREFIX):]
        if not name:
            return ()
        config = self._config_for_name(name)
        if config is None:
            return ()
        return tuple(
            prop.name for group in getattr(config, "groups", ())
            for prop in getattr(group, "properties", ()))

    def set_user_pvm_link(self, group: str, link: str,
                          unlink_nested: bool = False) -> int:
        if link not in ("linked", "unlinked"):
            return 0
        return self._rebuild_user_instance(
            group, link=link, unlink_nested=unlink_nested)

    def override_user_pvm(self, group: str, index: int,
                          prop: str, value) -> bool:
        state = self._user_instance_state(group)
        if state is None or state["link"] != "linked":
            return False
        source_id = state["source_by_index"].get(int(index), "")
        if not source_id:
            return False
        overrides = dict(state["overrides"])
        overrides[self.user_library().override_key(source_id, prop)] = value
        return bool(self._rebuild_user_instance(
            group, overrides=overrides))

    def remove_user_pvm_override(self, group: str, index: int,
                                 prop: str) -> bool:
        state = self._user_instance_state(group)
        if state is None or state["link"] != "linked":
            return False
        source_id = state["source_by_index"].get(int(index), "")
        if not source_id:
            return False
        overrides = dict(state["overrides"])
        canonical = self.user_library().override_key(source_id, prop)
        removed = overrides.pop(canonical, None)
        # A snapshot created by an older Studio may still carry the numeric
        # key. Removing the override explicitly cleans both representations.
        legacy = overrides.pop(f"{int(index)}.{prop}", None)
        if removed is None and legacy is None:
            return False
        return bool(self._rebuild_user_instance(
            group, overrides=overrides))

    def import_svg_file(self, path, name: str | None = None, *,
                        category: str = "IMPORTED", title: str = "",
                        replace: bool = False):
        """Import an SVG as a first-class equipment symbol.

        The file is cleaned and themed by `pvms.svg_import` before it is
        written, so an imported symbol renders, tints, anchors on its ink
        and follows the display theme exactly like the vendored catalog —
        one pipeline, not a parallel one. The engineer's original is never
        modified: what is written is the prepared copy.

        Returns a `SvgImportOutcome`; `import_svg` is the plain-name
        wrapper its existing callers use.
        """
        from pathlib import Path as _Path

        from azeo_control_trainer.core.hmi.pvms import svg_import
        from azeo_control_trainer.core.hmi.pvms.symbols import (
            USER_SYMBOLS, VENDORED_NAMES, register_user_symbol,
            save_user_symbol_index)

        source = _Path(path)
        if not source.exists():
            return svg_import.SvgImportOutcome(error=f"{source.name} does not exist")
        try:
            raw = source.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            return svg_import.SvgImportOutcome(error=f"could not read the file — {error}")
        try:
            prepared = svg_import.prepare(raw)
        except svg_import.SvgImportError as error:
            return svg_import.SvgImportOutcome(error=str(error))

        requested = (name or source.stem).strip()
        # Only the vendored names are reserved. Re-importing over one of
        # the engineer's own symbols is how corrected artwork is updated.
        resolved = svg_import.safe_symbol_name(
            requested, taken=VENDORED_NAMES)
        target_dir = _Path(self.store.root) / "_library" / "svg"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{resolved}.svg"
        replaced = resolved in USER_SYMBOLS or target.exists()
        if replaced and not replace:
            return svg_import.SvgImportOutcome(
                name=resolved, path=target,
                error=(f"{resolved}.svg already exists; confirm replacement "
                       "before importing it again"))
        try:
            svg_import.atomic_write_svg(target, prepared.svg)
        except OSError as error:
            return svg_import.SvgImportOutcome(error=f"could not write it — {error}")
        if not register_user_symbol(resolved, target, title=title,
                                    category=category):
            return svg_import.SvgImportOutcome(
                error=f"{resolved} could not be registered")
        save_user_symbol_index(target_dir)
        return svg_import.SvgImportOutcome(name=resolved, report=prepared,
                                renamed=resolved != requested,
                                replaced=replaced, path=target)

    def import_svg(self, path, name: str | None = None) -> str | None:
        """The imported symbol's name, or None when it was refused."""
        return self.import_svg_file(path, name).name or None

    def add_static(self, kind: str, x: float = 0, y: float = 0,
                   w: float = 120, h: float = 60, text: str = "",
                   symbol: str = "", *,
                   properties: dict | None = None) -> StaticItem:
        if self.mode != MODE_EDIT:
            self.enter_edit()
        self.checkpoint()
        if symbol:
            # The item's rect matches the glyph's own aspect, so the
            # selection box hugs what is actually drawn.
            from azeo_control_trainer.core.hmi.pvms.symbols import aspect
            h = w * aspect(symbol)
        data = {"kind": kind, "x": x, "y": y, "w": w, "h": h,
                "text": text}
        if properties:
            data.update(copy.deepcopy(properties))
        if kind == "user_entry":
            data["entry"] = {"kind": "button", "label": "Button",
                             "path": ""}
        if symbol:
            data["symbol"] = symbol
            data["ports"] = default_symbol_ports(symbol)
        item = self.renderer.build_drawing(data)
        self.canvas.scene().addItem(item)
        self.mark_unsaved()
        return item

    def add_stream_connector(self, direction: str, x: float = 0,
                             y: float = 0, *, text: str = "",
                             w: float = 150, h: float = 30) -> StaticItem:
        """Place one named incoming/outgoing off-page process stream."""
        from azeo_control_trainer.core.hmi.pvms.elements import (
            STREAM_INCOMING, stream_connector_properties,
        )

        properties = stream_connector_properties(direction)
        label = text.strip() or (
            "INCOMING STREAM" if direction == STREAM_INCOMING
            else "OUTGOING STREAM")
        return self.add_static(
            "stream_connector", x=x, y=y, w=w, h=h, text=label,
            properties=properties)

    def delete_selected(self) -> int:
        selection = list(self.canvas.scene().selectedItems())
        if not selection:
            return 0        # no phantom undo entry for a no-op Del
        self.checkpoint()
        removed = 0
        for item in selection:
            if isinstance(item, (PvmItem, StaticItem)):
                self.renderer.unbind(item)
            if isinstance(item, (PvmItem, StaticItem, PipeItem)):
                self.canvas.scene().removeItem(item)
                removed += 1
        # A pipe whose endpoint is gone is gone too.
        pvms, statics, pipes = self._scene_buckets()
        index = self._index_endpoints(pvms, statics)
        for pipe in pipes:
            if not self._pipe_end_exists(pipe, "a", index) \
                    or not self._pipe_end_exists(pipe, "b", index):
                self.canvas.scene().removeItem(pipe)
                removed += 1
        if removed:
            self.mark_unsaved()
        return removed

    # ------------------------------------------------- §8.6 mode machine
    def enter_edit(self) -> None:
        """VIEW/TEST → EDIT. Acquires the single-writer lock NOW —
        raises DisplayLocked, naming the holder, if someone else has it."""
        if self.mode == MODE_EDIT and self.store.owns_lock(self.display.name):
            return
        already_owned = self.store.owns_lock(self.display.name)
        self.store.acquire_lock(self.display.name)
        fingerprint = self.store.draft_fingerprint(self.display.name)
        if fingerprint != self._draft_fingerprint:
            if self.unsaved:
                if not already_owned:
                    self.store.release_lock(self.display.name)
                raise DisplayLocked(
                    "The saved display changed. Copy your unsaved work to a "
                    "new display before reopening the current draft.")
            draft = self.store.load_draft(self.display.name)
            if draft is not None:
                self._load_document(draft.to_dict())
                self.unsaved = False
                self._undo_stack.clear()
                self._redo_stack.clear()
            self._draft_fingerprint = fingerprint
        self._holds_lock = True
        self.preview_source.enabled = False
        self.engine.poll()
        self.mode = MODE_EDIT
        self._apply_mode()
        self.refresh_stale_user_pvm_instances()

    def leave_edit(self) -> None:
        """EDIT → VIEW, releasing the lock."""
        if self.mode == MODE_EDIT:
            self._write_recovery()
            self.store.release_lock(self.display.name)
            self._holds_lock = False
        self.preview_source.enabled = False
        self.engine.poll()
        self.mode = MODE_VIEW
        self._apply_mode()

    def enter_test(self) -> None:
        """→ TEST: renders exactly as an operator sees it — same
        renderer, no grid, no handles — while bindings stay live.

        `writes_blocked` is True for the duration. User Entries and
        writable faceplate bindings both route through
        `_write_user_entry`, so the sandbox is enforced at the one
        checked exit rather than separately by every control.
        """
        self.cancel_gestures()
        self.mode = MODE_TEST
        self.preview_source.enabled = True
        self.engine.poll()
        self._apply_mode()

    @property
    def test_mode(self) -> bool:
        """True in TEST. A property, not a field kept in step by hand —
        the field version was assigned in `_apply_mode` and read
        nowhere, which is exactly how it would have gone stale the
        first time someone set `mode` directly."""
        return self.mode == MODE_TEST

    @property
    def writes_blocked(self) -> bool:
        """Whether an operator command may reach the process from here.
        TEST is a sandbox; VIEW and EDIT are not."""
        return self.mode == MODE_TEST

    def exit_test(self) -> None:
        """TEST → EDIT when WE still hold the lock, else VIEW.

        Asking whether the lock FILE exists answered a different
        question: someone else's lock let us back into EDIT without
        ever acquiring it, and `enter_edit` then early-returns on the
        mode, so the single-writer guarantee never re-engaged.
        """
        self.preview_source.enabled = False
        self.engine.poll()
        self.mode = MODE_EDIT if self._holds_lock else MODE_VIEW
        self._apply_mode()

    def _apply_mode(self) -> None:
        # Hidden objects are ghosted in EDIT and absent in VIEW/TEST.  Their
        # segments therefore enter/leave crossover detection with the mode.
        self.touch_geometry()
        editable = self.mode == MODE_EDIT
        if not editable:
            self.cancel_gestures()
        drawing = getattr(self, "_place_ghost", None) is not None or any(
            getattr(self, name, False) for name in (
                "_line_armed", "_shape_armed", "_polyline_armed",
                "_pencil_armed", "_eraser_armed", "connect_armed",
                "_style_brush_armed"))
        self.set_interaction_mode(
            ("draw" if drawing else "select") if editable else "view")
        self.apply_authoring_visibility()
        for item in (self._items() + self._static_items()
                     + self._pipe_items()):
            document_data = getattr(item, "data", None)
            document_locked = bool(document_data.get("locked", False)) \
                if isinstance(document_data, dict) else False
            item.setFlag(QGraphicsItem.ItemIsMovable,
                         editable and not isinstance(item, PipeItem)
                         and not getattr(item, "pvm_locked", False)
                         and not document_locked)
            item.setFlag(QGraphicsItem.ItemIsSelectable, editable)
            if isinstance(item, StaticItem) \
                    and item.data.get("kind") in (
                        "display_link", "user_entry"):
                item.setCursor(Qt.ArrowCursor if editable
                               else Qt.PointingHandCursor)
        self._sync_status()
        self._selection_overlay.setVisible(
            editable and self.selection.snapshot().count > 1)
        self.canvas_frame.set_rulers_visible(editable and self.rulers_visible)
        # The dotted page frame is Edit-only authoring chrome. Repaint now;
        # otherwise it can linger until an unrelated hover or selection.
        self.canvas.viewport().update()
        self.modeChanged.emit(self.mode)

    def set_test_mode(self, on: bool) -> None:
        """Compatibility wrapper over the §8.6 machine."""
        if on:
            self.enter_test()
        else:
            self.exit_test()

    # ------------------------------------------------------ §8.7 undo
    def _document(self) -> dict:
        self._sync_document()
        return self.display.to_dict()

    def checkpoint(self) -> None:
        """Snapshot before a structural change — one undo entry each."""
        self._undo_stack.append(json.loads(json.dumps(self._document())))
        del self._undo_stack[:-30]
        self._redo_stack.clear()
        self._gesture_open = False
        self._gesture_before = None
        self._gesture_redo_before = None
        self._gesture_unsaved_before = None

    def gesture_checkpoint(self) -> None:
        """One checkpoint per press-drag-release, however many move
        events the drag produces — a drag is ONE undo step."""
        if self.mode == MODE_EDIT and not self._gesture_open:
            redo_before = list(self._redo_stack)
            unsaved_before = self.unsaved
            self.checkpoint()
            self._gesture_open = True
            self._gesture_before = self._undo_stack[-1]
            self._gesture_redo_before = redo_before
            self._gesture_unsaved_before = unsaved_before

    begin_gesture = gesture_checkpoint

    def end_gesture(self) -> None:
        """Close a press/drag/release transaction, dropping click no-ops."""
        if not self._gesture_open:
            return
        before = self._gesture_before
        redo_before = self._gesture_redo_before
        unsaved_before = self._gesture_unsaved_before
        self._gesture_open = False
        self._gesture_before = None
        self._gesture_redo_before = None
        self._gesture_unsaved_before = None
        if before is not None and before == self._document() \
                and self._undo_stack and self._undo_stack[-1] == before:
            self._undo_stack.pop()
            self._redo_stack[:] = redo_before or ()
            if unsaved_before is not None:
                self.unsaved = bool(unsaved_before)
                if not self.unsaved:
                    self._recovery_timer.stop()
                    self.store.clear_recovery(self.display.name)
                self._sync_status()

    def cancel_gesture(self) -> None:
        """Escape/focus loss rolls the whole in-flight drag back."""
        if not self._gesture_open:
            return
        before = self._gesture_before
        redo_before = self._gesture_redo_before
        unsaved_before = self._gesture_unsaved_before
        self._gesture_open = False
        self._gesture_before = None
        self._gesture_redo_before = None
        self._gesture_unsaved_before = None
        if before is None:
            return
        changed = before != self._document()
        if self._undo_stack and self._undo_stack[-1] == before:
            self._undo_stack.pop()
        self._redo_stack[:] = redo_before or ()
        if changed:
            self._load_document(before)
        if unsaved_before is not None:
            self.unsaved = bool(unsaved_before)
            if not self.unsaved:
                self._recovery_timer.stop()
                self.store.clear_recovery(self.display.name)
            self._sync_status()

    def undo(self) -> bool:
        if not self._undo_stack:
            return False
        self._redo_stack.append(json.loads(json.dumps(self._document())))
        self._load_document(self._undo_stack.pop())
        return True

    def redo(self) -> bool:
        if not self._redo_stack:
            return False
        self._undo_stack.append(json.loads(json.dumps(self._document())))
        self._load_document(self._redo_stack.pop())
        return True

    def _load_document(self, document: dict) -> None:
        self.documentChanged.emit()
        pvms, statics, pipes = self._scene_buckets()
        for item in pvms:
            self.renderer.unbind(item)
            self.canvas.scene().removeItem(item)
        # Pipes are NOT StaticItems. Clearing only pvms and statics
        # left every pipe on the scene while the document put its own
        # back, so each undo/redo doubled them — and `_drawing_data`
        # carried the duplicates into the saved draft.
        for item in statics:
            self.renderer.unbind(item)
            self.canvas.scene().removeItem(item)
        for item in pipes:
            self.canvas.scene().removeItem(item)
        self.renderer.unregistered.clear()
        from azeo_control_trainer.core.hmi.pvms.publishing import PvmDisplay as _GD
        self.display = _GD.from_dict(document)
        self.display.name = self.display.name or "Overview"
        # Undo/revert must restore the variable model before mark_unsaved
        # serializes it, or the old runtime values overwrite the restored ones.
        self.variables = VariableSet.from_list(self.display.variables)
        self.script_scopes = {key: value for key, value in self.script_scopes.items()
                              if not key.startswith("Dsp.")}
        self.canvas.scene().display = self.display
        for is_pvm, data in self.renderer.ordered_content(self.display):
            if is_pvm:
                self._restore(data)
            else:
                self._restore_item(data)
        self.reroute_pipes()
        self.apply_display_frame()
        self._apply_mode()
        self.mark_unsaved()

    def set_zoom(self, percent: int) -> None:
        percent = max(10, min(800, int(percent)))
        self.disable_auto_fit()
        self.canvas.resetTransform()
        self.canvas.scale(percent / 100.0, percent / 100.0)
        self.zoom_percent = percent
        self.canvas.update_rulers()

    @staticmethod
    def _authored_bounds(item) -> QRectF:
        """Visible engineering geometry, excluding resize/rotation chrome."""
        if hasattr(item, "rect"):
            return QRectF(item.mapRectToScene(item.rect()))
        return QRectF(item.sceneBoundingRect())

    def selected_bounds(self) -> QRectF:
        """Union of selected authored geometry in display coordinates."""
        bounds = QRectF()
        for item in self.canvas.scene().selectedItems():
            item_bounds = self._authored_bounds(item)
            if item_bounds.isEmpty() and item_bounds.isNull():
                continue
            bounds = item_bounds if bounds.isNull() \
                else bounds.united(item_bounds)
        return bounds

    @staticmethod
    def _engineering_number(value: float) -> str:
        rounded = round(float(value), 2)
        if abs(rounded - round(rounded)) < 0.005:
            return str(int(round(rounded)))
        return f"{rounded:.2f}".rstrip("0").rstrip(".")

    def geometry_readout(self) -> str:
        """Compact CAD status text for the cursor or current selection."""
        target = getattr(self, "_connect_target", None)
        if target is not None:
            item, side = target
            label = getattr(getattr(item, "pvm", None), "label", "") or self._endpoint_id(item)
            port = "Perimeter" if str(side).startswith(("outline:", "edge:")) else str(side).replace("_", " ").title()
            return f"CONNECT  {str(label)[:32]}  /  {port}"
        selected = self.canvas.scene().selectedItems()
        bounds = self.selected_bounds()
        number = self._engineering_number
        if selected and not bounds.isNull():
            prefix = "SEL" if len(selected) == 1 \
                else f"{len(selected)} SELECTED"
            return (f"{prefix}  X {number(bounds.left())}  "
                    f"Y {number(bounds.top())}  "
                    f"W {number(bounds.width())}  "
                    f"H {number(bounds.height())}")
        return (f"CURSOR  X {number(self._cursor_scene_pos.x())}  "
                f"Y {number(self._cursor_scene_pos.y())}")

    def update_geometry_readout(self, scene_pos: QPointF | None = None) \
            -> str:
        if scene_pos is not None:
            self._cursor_scene_pos = QPointF(scene_pos)
        text = self.geometry_readout()
        self.geometryReadoutChanged.emit(text)
        return text

    def fit_selection(self) -> bool:
        """Zoom tightly to selected ink while preserving the CAD camera.

        ``fitInView`` can exceed a sane engineering zoom for a tiny symbol.
        Computing the scale explicitly keeps the same 10â€“800% contract as
        the wheel and Zoom box, and leaves an invariant 36 px screen apron.
        """
        bounds = self.selected_bounds()
        viewport = self.canvas.viewport().size()
        if bounds.isNull() or viewport.width() < 32 or viewport.height() < 32:
            return False
        width = max(bounds.width(), 1.0)
        height = max(bounds.height(), 1.0)
        available_w = max(1.0, viewport.width() - 72.0)
        available_h = max(1.0, viewport.height() - 72.0)
        scale = max(0.10, min(8.0, available_w / width,
                             available_h / height))
        self.disable_auto_fit()
        self.canvas.resetTransform()
        self.canvas.scale(scale, scale)
        self.canvas.centerOn(bounds.center())
        self.zoom_percent = int(round(scale * 100))
        self.canvas.update_rulers()
        self.update_geometry_readout()
        return True

    def _persist_authoring_guides(self) -> None:
        from .authoring_state import save_guides
        try:
            save_guides(self.store.root, self.display.name,
                        self.authoring_guides)
        except OSError:
            # Guides are convenience state. An unwritable sidecar must not
            # interrupt editing or masquerade as a display Save failure.
            pass

    def add_authoring_guide(self, axis: str, coordinate: float, *,
                            bypass_snap: bool = False) -> bool:
        """Add a ruler guide without dirtying the publishable document."""
        if axis not in ("x", "y") or self.mode != MODE_EDIT:
            return False
        value = float(coordinate)
        if self.snap_enabled and not bypass_snap:
            value = round(value / 8.0) * 8.0
        if any(kind == axis and abs(existing - value) < 0.01
               for kind, existing in self.authoring_guides):
            return False
        self.authoring_guides.append((axis, value))
        self._persist_authoring_guides()
        self.canvas.viewport().update()

    def apply_authoring_visibility(self) -> None:
        """Apply authored visibility plus an EDIT-only viewport isolate."""
        from .layers import item_layer

        isolate = self.isolated_layer if self.mode == MODE_EDIT else None
        for item in self._items() + self._static_items() + self._pipe_items():
            if isinstance(item, PvmItem):
                authored = bool(item.pvm.visible)
                item.pvm_visible = authored
            else:
                authored = bool(item.data.get("visible", True))
            item.setVisible(authored and (
                isolate is None or item_layer(item) == isolate))

    def isolate_layer(self, layer: str | None) -> None:
        """Temporarily filter the authoring viewport; never edit the display."""
        layer = str(layer or "").strip() or None
        self.isolated_layer = None if layer == self.isolated_layer else layer
        self.apply_authoring_visibility()
        self.canvas.viewport().update()
        self.canvas.update_rulers()
        return True

    def remove_authoring_guide(self, axis: str, coordinate: float,
                               tolerance: float = 1.0) -> bool:
        candidates = [(abs(value - coordinate), index)
                      for index, (kind, value)
                      in enumerate(self.authoring_guides)
                      if kind == axis]
        if not candidates or min(candidates)[0] > max(0.0, tolerance):
            return False
        _distance, index = min(candidates)
        self.authoring_guides.pop(index)
        self._persist_authoring_guides()
        self.canvas.viewport().update()
        self.canvas.update_rulers()
        return True

    def clear_authoring_guides(self, axis: str = "") -> int:
        before = len(self.authoring_guides)
        self.authoring_guides[:] = [
            guide for guide in self.authoring_guides
            if axis and guide[0] != axis
        ]
        removed = before - len(self.authoring_guides)
        if removed:
            self._persist_authoring_guides()
            self.canvas.viewport().update()
            self.canvas.update_rulers()
        return removed

    def disable_auto_fit(self) -> None:
        """Keep a deliberate CAD camera adjustment stable across resizes."""
        self.auto_fit_enabled = False

    def set_rulers_visible(self, visible: bool) -> None:
        """Show engineering rulers only where they cannot be published."""
        self.rulers_visible = bool(visible)
        self.canvas_frame.set_rulers_visible(
            self.rulers_visible and self.mode == MODE_EDIT)

    def refit_to_viewport(self) -> None:
        """Refit the page after the view has its real, post-layout size."""
        if not self.auto_fit_enabled or self._fitting_viewport:
            return
        framed = self.display.width > 0 and self.display.height > 0
        rect = self.canvas.page_rect() if framed \
            else self.canvas.scene().itemsBoundingRect()
        viewport = self.canvas.viewport().size()
        if rect.isEmpty() or viewport.width() < 32 or viewport.height() < 32:
            return
        self._fitting_viewport = True
        try:
            # A small scene-space apron keeps the dotted page boundary and
            # resize handles clear of the viewport frame without recreating
            # the enormous dead area that prompted this camera repair.
            apron = max(rect.width(), rect.height()) * 0.0125
            if not framed:
                # Auto documents start with an empty scene rectangle. Qt
                # clamps centerOn to that old rectangle even after fitInView
                # finds the new content, leaving the drawing off-screen.
                self.canvas.setSceneRect(
                    rect.adjusted(-apron, -apron, apron, apron))
            self.canvas.fitInView(
                rect.adjusted(-apron, -apron, apron, apron),
                Qt.KeepAspectRatio)
            self.canvas.centerOn(rect.center())
            self.zoom_percent = int(round(
                abs(self.canvas.transform().m11()) * 100))
            self.canvas.update_rulers()
        finally:
            self._fitting_viewport = False

    # ------------------------------------------------------- validation
    def validate(self) -> list[str]:
        from azeo_control_trainer.core.hmi.pvms.visual_quality import drain
        return drain(self.iter_validation())

    def iter_validation(self):
        """Every PVM path that does not resolve, by name — the same
        check that gates publish, runnable any time.

        A placement whose PVM class this build does not register is
        reported here too: it draws unbound rather than crashing the
        window, and an unbound card that nothing explains is exactly
        the screen lying about itself.
        """
        problems = []
        for item in self._items():
            yield None
            cls = registry.get(item.pvm.block_type, item.pvm.role, item.pvm.variant)
            for param, expected in getattr(cls, "PARAM_TYPES", {}).items():
                path = str(item.pvm.params.get(param, ""))
                if not self.path_resolves(path, expected):
                    problems.append(f"{item.pvm.id}: {param} must reference a {expected} block")
            for value in item.pvm.params.values():
                if not self._resolve(str(value)):
                    problems.append(str(value))
        from azeo_control_trainer.core.hmi.binding.result import UNRESOLVED
        from azeo_control_trainer.core.hmi.pvms.elements import (
            CLICK, DATA_ELEMENT_KINDS, SCRIPT, Action, UserEntry, actions_of,
            element_paths, validate_data_element, validate_datalink,
        )
        from azeo_control_trainer.core.hmi.pvms.scripting import GraphicsScriptRuntime
        script_runtime = GraphicsScriptRuntime()
        for item in self._static_items():
            yield None
            kind = item.data.get("kind")
            if kind == "datalink":
                path = str(item.data.get("path", "") or "").strip()
                problem = validate_datalink(
                    item.data.get("datalink_type", "numeric"), path)
                if problem:
                    problems.append(f"Data Link: {problem}")
                elif self.engine._source.read(path) is UNRESOLVED:
                    problems.append(f"unresolved Data Link: {path}")
            elif kind == "display_link":
                target = str(item.data.get("target", "") or "").strip()
                if not target:
                    problems.append("Display Link: no target display")
            elif kind == "user_entry":
                entry = UserEntry.from_dict(item.data.get("entry", {}))
                problem = entry.validate()
                if problem:
                    problems.append(f"User Entry: {problem}")
                elif not entry.path:
                    if not any(action.active and action.event == CLICK
                               for action in actions_of(item.data)):
                        problems.append(
                            "User Entry: no write target or action is configured")
                else:
                    writable = self.engine.can_write(entry.path)
                    if not writable.success:
                        problems.append(
                            f"User Entry: {entry.path}: {writable.error}")
            elif kind in DATA_ELEMENT_KINDS:
                problem = validate_data_element(item.data)
                if problem:
                    problems.append(
                        f"{kind.replace('_', ' ').title()}: {problem}")
                for path in element_paths(item.data):
                    if self.engine._source.read(path) is UNRESOLVED:
                        problems.append(f"unresolved {kind}: {path}")
            for action in actions_of(item.data):
                if action.kind != SCRIPT:
                    continue
                answer = script_runtime.validate(action.source)
                if not answer.ok:
                    name = item.data.get("title") or item.data.get("id", "item")
                    problems.append(f"Script {name}: {answer.error}")
        for event, rows in self.display.events.items():
            yield None
            for action in (Action.from_dict(row) for row in rows):
                if action.kind != SCRIPT:
                    continue
                answer = script_runtime.validate(action.source)
                if not answer.ok:
                    problems.append(f"Display {event} script: {answer.error}")
        # One reusable-class instance is several scene items. Validate its
        # public contract once per group so Publish names the missing input
        # rather than emitting the same finding for every line and label.
        checked_groups = set()
        for item in self._static_items():
            yield None
            data = item.data
            class_name = str(data.get("user_pvm", ""))
            group = str(data.get("group", ""))
            if not class_name or not group or group in checked_groups:
                continue
            checked_groups.add(group)
            config = self.renderer.pvm_config_named(class_name, data.get("class_revision", ""))
            if config is None:
                continue
            class_issues = config.issues()
            if class_issues:
                problems.append(
                    f"{class_name} class interface: {class_issues[0]}")
                continue
            choices = dict(data.get("pvm_choices", {}))
            for prop in config.public_properties():
                value = choices.get(prop.name, prop.default)
                if prop.required and not str(value).strip():
                    problems.append(
                        f"{class_name}: required {prop.title or prop.name}")
                if prop.ptype == "Procedure Reference":
                    from azeo_control_trainer.core.procedures.hmi import reference
                    try:
                        reference(value)
                    except ValueError as error:
                        problems.append(f"{class_name}: {error}")
        page = self.canvas.page_rect()
        if not page.isEmpty():
            for item in self._items() + self._static_items():
                yield None
                document_data = getattr(item, "data", None)
                document_visible = document_data.get("visible", True) \
                    if isinstance(document_data, dict) else True
                if not bool(getattr(item, "pvm_visible", True)) \
                        or not bool(document_visible):
                    continue
                bounds = item.mapRectToScene(item.rect())
                if not page.contains(bounds):
                    pvm = getattr(item, "pvm", None)
                    data = document_data if isinstance(
                        document_data, dict) else {}
                    name = next(iter(pvm.params.values()), pvm.id) \
                        if pvm is not None else data.get("id", "item")
                    problems.append(f"outside display page: {name}")
        for item in self._static_items():
            yield None
            data = item.data
            if data.get("kind") == "text" \
                    and float(data.get("font_size", 9.0) or 9.0) < 7.0:
                problems.append(
                    f"text below 7 pt minimum: {data.get('id', 'text')}")
        endpoints = self._index_endpoints(self._items(), self._static_items())
        for pipe in self._pipe_items():
            yield None
            a = endpoints.get(pipe.data.get("a"))
            b = endpoints.get(pipe.data.get("b"))
            if a is None or b is None:
                problems.append(
                    f"unconnected pipe: {pipe.data.get('id', 'pipe')}")
                continue
            from azeo_control_trainer.core.hmi.pvms.rendering.pipe_scene import valid_port_name
            for end, endpoint in (("a", a), ("b", b)):
                side = str(pipe.data.get(f"{end}_side", ""))
                if not pipe.data.get("auto", True) \
                        and not valid_port_name(endpoint, side):
                    problems.append(
                        f"pipe {pipe.data.get('id', 'pipe')}: missing port "
                        f"{side or '(empty)'} on {pipe.data.get(end, '')}")
            if pipe.data.get("route_mode") == "manual":
                from azeo_control_trainer.core.hmi.pvms.rendering.pipe_scene import scene_bounds, scene_obstacles
                from azeo_control_trainer.core.hmi.pvms.rendering.routing import validate_manual_waypoints
                issues = validate_manual_waypoints(
                    pipe.data.get("route_points", ()),
                    bounds=scene_bounds(self.canvas.scene()),
                    obstacles=scene_obstacles(
                        self.canvas.scene(), (a, b)))
                problems.extend(
                    f"pipe {pipe.data.get('id', 'pipe')} waypoint "
                    f"{issue.index + 1}: {issue.message}"
                    for issue in issues if issue.severity == "error")
            if pipe.route_status == "blocked":
                detail = pipe.route_message or "no clear orthogonal path"
                problems.append(
                    f"blocked pipe {pipe.data.get('id', 'pipe')}: {detail}")
        problems.extend(f"no PVM class registered for {name}"
                        for name in self.renderer.unregistered)
        return sorted(set(problems))


    def verification_findings(self):
        """Return the one structured preflight used by Verify and Publish."""
        from .verification import findings_for_studio
        return findings_for_studio(self)

    def health(self) -> dict:
        """SUBS / GOOD / BAD / FORCED for the status bar."""
        good = bad = forced = 0
        bound_items = list(self._items()) + [
            item for item in self._static_items()
            if item.data.get("kind") in ("datalink", "user_entry")]
        for item in bound_items:
            result = item.binding.result if item.binding else None
            if result is None:
                continue
            if result.quality.name == "BAD":
                bad += 1
            else:
                good += 1
            forced += 1 if result.forced else 0
        for item in self._static_items():
            for binding in item.bindings.values():
                result = binding.result
                if result.quality.name == "BAD":
                    bad += 1
                else:
                    good += 1
                forced += 1 if result.forced else 0
        return {"subs": self.engine.monitored_count, "good": good,
                "bad": bad, "forced": forced}

    # -------------------------------------------------------------- live
    #: Properties the live loop may drive. Everything an author can
    #: attach a descriptor to, which since the property model landed is
    #: the general case rather than four special ones.
    ANIMATABLE = ("fill", "line", "fill_pct", "rot", "visible", "text",
                  "width", "radius", "start", "span", "style", "opacity",
                  "text_color", "font_family", "font_size", "font_bold",
                  "font_italic", "font_underline", "icon", "tooltip",
                  "enabled")
    #: Those that must end up numeric whatever the source said.
    NUMERIC_PROPERTIES = ("fill_pct", "rot", "width", "radius",
                          "start", "span", "opacity", "font_size")
    BOOLEAN_PROPERTIES = ("visible", "font_bold", "font_italic",
                          "font_underline", "enabled")

    def property_resolver(self):
        """The resolver for this display's context.

        Rebuilt per tick rather than cached because standards,
        functions and variables all change under an author's hands and
        a resolver holding yesterday's variables is the animation
        equivalent of a stale cache.
        """
        from azeo_control_trainer.core.hmi.pvms.functions import FunctionStore
        from azeo_control_trainer.core.hmi.pvms.properties import PropertyResolver
        if self._functions is None:
            self._functions = FunctionStore(self.store.root)
        # Reload only when the file actually changed: this runs four
        # times a second, and re-reading and re-parsing the JSON on
        # every tick is wasted I/O for a document that changes when
        # an Author edits it.
        self._functions.refresh()
        return PropertyResolver(
            standards=self._standards_store(),
            functions=self._functions,
            variables=self.variable_values(),
            read=self.engine._source.read)

    def _standards_store(self):
        if self._standards is None:
            try:
                from pathlib import Path as _P

                from azeo_control_trainer.core.hmi.pvms.standards import StandardsStore
                self._standards = StandardsStore(
                    _P(self.store.root).parent)
            except Exception:                       # noqa: BLE001
                self._standards = None
        return self._standards

    def variable_values(self) -> dict:
        """This display's variables, resolved. `Dsp.<name>` reaches
        these; a PVM's own variables are layered on top per item."""
        values = {}
        if self.variables.items:
            from azeo_control_trainer.core.hmi.pvms.properties import PropertyResolver
            plain = PropertyResolver(standards=self._standards_store(),
                                     functions=self._functions,
                                     read=self.engine._source.read)
            values.update(self.variables.resolve(plain))
        values.update({key.removeprefix("Dsp."): value
                       for key, value in self.script_scopes.items()
                       if key.startswith("Dsp.")})
        return values

    def _apply_animations(self) -> None:
        """Drive every described property from its descriptor.

        The diamond-menu model: a property may be animated from a
        parameter, blink on a condition, compute from an expression, or
        reference a standard, a variable or a PVM configuration
        property. Whichever it is, **Bad quality REMOVES the override**
        rather than inventing a value — a shape that keeps its last
        animated colour after its source dies is a screen lying about
        the plant.
        """
        from azeo_control_trainer.core.hmi.pvms.properties import described_properties
        described = [(i, described_properties(i.data))
                     for i in self._static_items()]
        described = [(i, d) for i, d in described if d]
        if not described:
            return
        resolver = self.property_resolver()
        for item, specs in described:
            dirty = False
            for prop, spec in specs.items():
                if prop not in self.ANIMATABLE:
                    continue
                answer = resolver.resolve(spec, None)
                value = answer.value if answer.usable else None
                if value is None:
                    if item.data.pop(prop + "_animated", None) \
                            is not None:
                        base = item.data.get(prop + "_static")
                        if base is None:
                            item.data.pop(prop, None)
                        else:
                            item.data[prop] = base
                        dirty = True
                    continue
                if prop in self.NUMERIC_PROPERTIES:
                    try:
                        value = float(value)
                    except (TypeError, ValueError):
                        continue
                elif prop in self.BOOLEAN_PROPERTIES:
                    value = value if isinstance(value, bool) else \
                        str(value).strip().lower() in (
                            "1", "true", "yes", "on", "show")
                if item.data.get(prop) != value:
                    if not item.data.get(prop + "_animated") \
                            and prop in item.data \
                            and prop + "_static" not in item.data:
                        item.data[prop + "_static"] = item.data[prop]
                    item.data[prop] = value
                    item.data[prop + "_animated"] = True
                    dirty = True
                    if prop == "rot" \
                            and hasattr(item, "apply_rotation"):
                        item.apply_rotation()
                        # An animated turn moves real geometry, so the
                        # crossover cache has to know.
                        self.touch_geometry()
                    elif prop == "tooltip":
                        item.setToolTip(str(value))
            if dirty:
                item.update()

    # ---------------------------------------------------- complexity
    def complexity(self):
        """This display's complexity measures and index.

        The manual's own arithmetic, so the number is comparable with
        one a real Graphics Designer would print for the same display.
        """
        from azeo_control_trainer.core.hmi.pvms.complexity import measure_display
        self._sync_document()
        return measure_display(self.display, renderer=self.renderer)

    def _tick(self) -> None:
        with getattr(self.engine._source, "snapshot", nullcontext)():
            self._refresh_live()

    def _refresh_live(self):
        pvms, statics, _pipes = self._scene_buckets()
        watch = getattr(self, "watch_area", None)
        bindings = [binding for item in (*pvms, *statics)
                    for binding in self.renderer.item_bindings(item)]
        if watch is not None:
            bindings.extend(binding for _label, binding in watch.rows)
        self.engine.poll(bindings)
        self._apply_animations()
        for item in pvms:
            revision = self.renderer.binding_revision(item)
            changed = revision != getattr(item, "_painted_binding_revision", None)
            item._painted_binding_revision = revision
            item.sample_history()
            # Trend painters advance with time, not only with change.
            if changed or item.pvm.variant in ("tank", "vessel"):
                item.update()
        for item in statics:
            revision = self.renderer.binding_revision(item)
            changed = revision != getattr(item, "_painted_binding_revision", None)
            item._painted_binding_revision = revision
            if item.data.get("kind") == "user_entry":
                item.refresh_write_permission()
                if changed:
                    item.update()
            elif item.data.get("kind") == "datalink" and changed:
                item.update()
            if item.sample_compound():
                item.update()
            elif item.data.get("kind") in (
                    "date_time", "alarm_list", "table"):
                item.update()
        self.alarm_banner.set_alarms(self.alarm_summary())
        if watch is not None and watch.rows:
            watch.refresh()

    def alarm_summary(self) -> list:
        """(priority, tag, condition, since) per abnormal PVM — CRIT
        first, Bad quality riding as WARN.

        `since` is when the condition FIRST appeared, remembered per
        (placement, condition) for as long as it stands. Stamping the
        current time each call made every alarm look like it had just
        arrived, which is the one thing the column exists to say —
        an alarm standing for ten minutes read as ten seconds old.
        """
        import time as _time
        entries = []
        now = _time.strftime("%H:%M:%S")
        seen = {}
        onset = getattr(self, "_alarm_onset", {})
        for item in self._items():
            result = item.binding.result if item.binding else None
            if result is None:
                continue
            tag = "/".join(str(v) for v in item.pvm.params.values())
            tag = tag.split("/")[-1] if "/" in tag else tag
            if result.alarm_active:
                priority = "CRIT" if result.alarm_priority >= 15 \
                    else "WARN"
                condition = result.alarm_condition or "ALARM"
            elif result.quality.name == "BAD":
                priority, condition = "WARN", "BAD QUALITY"
            else:
                continue
            key = (item.pvm.id, condition)
            since = onset.get(key, now)
            seen[key] = since
            entries.append((0 if priority == "CRIT" else 1,
                            priority, tag, condition, since))
        # An alarm that cleared forgets its onset, so the same
        # condition returning is a NEW alarm with a new time.
        self._alarm_onset = seen
        entries.sort()
        return [(p, t, c, s) for _, p, t, c, s in entries]

    # ------------------------------------------------------------ snippet
    def show_snippet(self, item: "PvmItem", screen_pos) -> None:
        if getattr(self, "connect_armed", False):
            return                      # anchors are the feedback then
        if not hasattr(self, "_snippet"):
            self._snippet = _Snippet()
        self._snippet.show_for(item, screen_pos)

    def hide_snippet(self) -> None:
        snippet = getattr(self, "_snippet", None)
        if snippet is not None:
            snippet.hide()

    def add_to_watch(self, item: "PvmItem") -> None:
        """Watch Area drop: no configuration, no engineering change."""
        if not hasattr(self, "watch_area"):
            self.watch_area = _WatchArea(self)
        tag = "/".join(str(v) for v in item.pvm.params.values())
        label = item.pvm.label or (tag.split("/")[-1]
                                   if "/" in tag else tag)
        binding = item.binding
        if binding is None:
            return
        self.watch_area.add_row(label, binding)
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not is_headless():
            self.watch_area.show()
            self.watch_area.raise_()

    def rename_pvm(self, item: "PvmItem", label: str) -> None:
        """Set the friendly name — identity metadata, one undo step."""
        self.checkpoint()
        from azeo_control_trainer.core.hmi.pvms.base import Pvm as _Pvm
        item.pvm = _Pvm(**{**item.pvm.__dict__, "label": label})
        item.update()
        self.mark_unsaved()

    # -------------------------------------------------- §8.4 context menus
    # ------------------------------- builder-parity canvas operations
    def copy_selection_payload(self) -> dict:
        """Return a typed, detached clipboard with internal topology only."""
        _pvms, _statics, pipes = self._scene_buckets()
        selected = set(self.selection.snapshot().items)
        endpoints = [item for item in self._document_items(Qt.AscendingOrder)
                     if item in selected and isinstance(item, (PvmItem, StaticItem))]
        endpoint_ids = {self._endpoint_id(item) for item in endpoints}
        records = []
        for item in endpoints:
            if isinstance(item, PvmItem):
                records.append({"type": "pvm",
                                "data": copy.deepcopy(item.pvm.to_dict())})
            else:
                records.append({"type": "item",
                                "data": copy.deepcopy(item.data)})
        for pipe in pipes:
            data = pipe.data
            if data.get("a") in endpoint_ids and data.get("b") in endpoint_ids:
                records.append({"type": "pipe", "data": copy.deepcopy(data)})
        return {"schema": 1, "records": records}

    @staticmethod
    def _clipboard_records(payload) -> list[dict]:
        """Accept the schema plus historical raw lists used by old drafts/tests."""
        if isinstance(payload, dict) and payload.get("schema") == 1:
            return copy.deepcopy(list(payload.get("records", ())))
        records = []
        for data in payload if isinstance(payload, list) else ():
            kind = "pipe" if data.get("kind") == "pipe" \
                else "pvm" if "class" in data else "item"
            records.append({"type": kind, "data": copy.deepcopy(data)})
        return records

    def paste_payload(self, payload, *, dx: float = 24, dy: float = 24,
                      anchor=None, as_gesture: bool = False) -> int:
        records = self._clipboard_records(payload)
        endpoints = [r for r in records if r.get("type") in ("pvm", "item")]
        wires = [r for r in records if r.get("type") == "pipe"]
        if not endpoints:
            return 0
        if self.mode != MODE_EDIT:
            self.enter_edit()
        if anchor is not None:
            xs = [float(r["data"].get("x", 0)) for r in endpoints]
            ys = [float(r["data"].get("y", 0)) for r in endpoints]
            dx = anchor.x() - (min(xs) + max(xs)) / 2
            dy = anchor.y() - (min(ys) + max(ys)) / 2
        if as_gesture:
            self.gesture_checkpoint()
        else:
            self.checkpoint()
        remap, group_remap, pasted_items = {}, {}, []
        for record in endpoints:
            source = record["data"]
            data = copy.deepcopy(source)
            prefix = "pvm" if record["type"] == "pvm" else "itm"
            data["id"] = f"{prefix}_{uuid.uuid4().hex[:8]}"
            data["x"] = float(source.get("x", 0)) + dx
            data["y"] = float(source.get("y", 0)) + dy
            group = data.get("group")
            if group:
                data["group"] = group_remap.setdefault(
                    group, f"grp_{uuid.uuid4().hex[:8]}")
            remap[source.get("id")] = data["id"]
            pasted_items.append(self._restore(data) if record["type"] == "pvm"
                                else self._restore_item(data))
        prior_pipes = set(self._pipe_items())
        copied = self._copy_pipes([r["data"] for r in wires], remap, dx, dy,
                                  group_remap)
        copied_pipes = [pipe for pipe in self._pipe_items() if pipe not in prior_pipes]
        self.selection.replace([item for item in pasted_items if item is not None]
                               + copied_pipes)
        self.reroute_pipes()
        self.mark_unsaved()
        return len(pasted_items) + copied

    def insert_at(self, kind: str, scene_pos) -> "StaticItem":
        """INSERT AT CURSOR — the item lands centred on the click."""
        sizes = {"line": (140, 12), "polyline": (140, 80),
                 "text": (110, 20), "arc": (90, 60),
                 "ellipse": (90, 70)}
        w, h = sizes.get(kind, (120, 80))
        return self.add_static(kind, x=scene_pos.x() - w / 2,
                               y=scene_pos.y() - h / 2, w=w, h=h,
                               text="Text" if kind == "text" else "")

    def paste_at(self, scene_pos) -> int:
        """CLIPBOARD 'Paste here' — the copied set lands at the click,
        keeping its internal arrangement.

        Connectors are re-pointed at the pasted copies. Regenerating
        the pipe's own id while leaving `a`/`b` naming the originals
        drew a second line over the objects that were copied FROM,
        which reads as a paste that landed in the wrong place.
        """
        window = self.window()
        clipboard = getattr(window, "_pvm_clipboard", None)
        if not clipboard:
            return 0
        return self.paste_payload(clipboard, anchor=scene_pos)

    def select_tool(self) -> None:
        """V — back to plain selection: every armed tool stands down."""
        self.cancel_gestures()
        self.set_pan(False)

    def set_pan(self, on: bool) -> None:
        """H — the pan hand."""
        if on:
            self.disable_auto_fit()
        self.set_interaction_mode("pan" if on else "select")

    def open_display_properties(self):
        """Open the one property editor for every display hierarchy type."""
        if self.mode != MODE_EDIT:
            self.enter_edit()
        from .display_properties import DisplayPropertiesDialog

        dialog = DisplayPropertiesDialog(self, self)
        # Keep a reference for modeless/headless inspection and automation.
        self._display_properties_dialog = dialog
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not is_headless():
            dialog.exec()
        return dialog

    def fit_drawing(self) -> None:
        """F — restore the live-fit camera for the page or Auto drawing."""
        self.auto_fit_enabled = True
        self.refit_to_viewport()

    def apply_display_frame(self) -> None:
        """Apply the Canvas frame without changing any object geometry."""
        if self.display.width > 0 and self.display.height > 0:
            self.canvas.setSceneRect(
                0.0, 0.0, float(self.display.width),
                float(self.display.height))
        else:
            self.canvas.scene().setSceneRect(
                self.canvas.scene().itemsBoundingRect())
        if self.display.view_type == "scale_to_frame":
            self.auto_fit_enabled = True
            self.canvas.schedule_auto_fit()
        self.canvas.viewport().update()

    def canvas_context_menu(self, global_pos, scene_pos=None) -> None:
        """Menu A — the builder's own anatomy: HISTORY, INSERT AT
        CURSOR, CLIPBOARD, CANVAS — every entry a real operation."""
        from azeo_control_trainer.core.presentation.menu_style \
            import studio_menu
        menu = studio_menu(f"DISPLAY  {self.display.name}",
                           f"L{self.display.level} display")
        menu.addSection("History")
        undo = menu.addAction("Undo\tCtrl+Z")
        undo.setEnabled(bool(self._undo_stack))
        undo.triggered.connect(self.undo)
        redo = menu.addAction("Redo\tCtrl+Y")
        redo.setEnabled(bool(self._redo_stack))
        redo.triggered.connect(self.redo)

        menu.addSection("Insert at cursor")
        anchor = scene_pos or self.canvas.mapToScene(
            self.canvas.viewport().rect().center())
        for label, key, kind in (("Rectangle", "R", "rect"),
                                 ("Ellipse", "E", "ellipse"),
                                 ("Line", "L", "line"),
                                 ("Polyline", "P", "polyline"),
                                 ("Arc", "A", "arc"),
                                 ("Text", "T", "text")):
            act = menu.addAction(f"{label}\t{key}")
            act.triggered.connect(
                lambda checked=False, k=kind, s=anchor:
                self.insert_at(k, s))

        menu.addSection("Clipboard")
        paste = menu.addAction("Paste here\tCtrl+V")
        paste.setEnabled(bool(getattr(self.window(), "_pvm_clipboard",
                                      None)))
        paste.triggered.connect(
            lambda checked=False, s=anchor: self.paste_at(s))

        menu.addSection("Canvas")
        properties = menu.addAction("Display properties…")
        properties.triggered.connect(self.open_display_properties)
        menu.addSeparator()
        select_tool = menu.addAction("Select tool\tV")
        select_tool.triggered.connect(self.select_tool)
        pan = menu.addAction("Pan tool\tH")
        pan.setCheckable(True)
        pan.setChecked(getattr(self, "pan_active", False))
        pan.toggled.connect(self.set_pan)
        routes = menu.addAction("Clean up connector routes")
        routes.triggered.connect(self.reroute_pipes)
        zoom_selection = menu.addAction("Zoom to selection\tShift+F")
        zoom_selection.setEnabled(bool(
            self.canvas.scene().selectedItems()))
        zoom_selection.triggered.connect(self.fit_selection)
        fit = menu.addAction("Fit drawing\tF")
        fit.triggered.connect(self.fit_drawing)
        act = menu.addAction("Select all\tCtrl+A")
        act.triggered.connect(
            lambda: [i.setSelected(True) for i in self._items()
                     + self._static_items() + self._pipe_items()])
        menu.addSeparator()
        grid = menu.addAction("Show grid\tCtrl+;")
        grid.setCheckable(True)
        grid.setChecked(self.grid_visible)
        grid.toggled.connect(self._set_grid)
        snap = menu.addAction("Snap to grid\tCtrl+'")
        snap.setCheckable(True)
        snap.setChecked(self.snap_enabled)
        snap.toggled.connect(lambda on: setattr(self, "snap_enabled",
                                                on))
        menu.addSeparator()
        selected_groupable = list(
            self.selection.editable_items(include_pipes=True))
        group_action = menu.addAction("Group selected objects\tCtrl+G")
        group_action.setEnabled(len(selected_groupable) >= 2)
        group_action.triggered.connect(self.group_selected)
        ungroup_action = menu.addAction("Ungroup")
        ungroup_action.setEnabled(
            any(self._group_of(i) for i in selected_groupable))
        ungroup_action.triggered.connect(self.ungroup_selected)
        from PySide6.QtWidgets import QMenu as _QMenu
        align_menu = _QMenu("Align", menu)
        selected_pipes = self.selected_pipe_items()
        align_menu.setEnabled(
            len(self.selection.editable_items()) >= 2
            or self.can_align_pipe_endpoints(selected_pipes))
        endpoints = align_menu.addAction(
            "Pipe endpoints (straight line)")
        endpoints.setEnabled(
            self.can_align_pipe_endpoints(selected_pipes))
        endpoints.triggered.connect(self.align_pipe_endpoints)
        align_menu.addSeparator()
        for label, edge in (("Left", "left"), ("Right", "right"),
                            ("Top", "top"), ("Bottom", "bottom"),
                            ("Center horizontal", "hcenter"),
                            ("Center vertical", "vcenter")):
            act = align_menu.addAction(label)
            act.triggered.connect(
                lambda checked=False, e=edge: self.align_selected(e))
        menu.addMenu(align_menu)
        menu.addSeparator()
        background = menu.addAction("Background colour…")

        def pick_background():
            from azeo_control_trainer.core.presentation.headless import is_headless as _hl
            if _hl():
                self.set_background("#F0F0E8")
                return
            from PySide6.QtWidgets import QColorDialog
            colour = QColorDialog.getColor(parent=self)
            if colour.isValid():
                self.set_background(colour.name())
        background.triggered.connect(pick_background)
        menu.addSeparator()
        validate = menu.addAction("Validate bindings\tF8")
        validate.triggered.connect(self.validate)
        retain_menu(self, menu, "_context_menu")
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not is_headless():
            menu.exec_transient(global_pos)

    def pvm_context_menu(self, item: PvmItem, global_pos) -> None:
        """Menu B — one PVM. Open faceplate is bold: the default."""
        from azeo_control_trainer.core.presentation.menu_style             import studio_menu
        tag = "/".join(str(v) for v in item.pvm.params.values())
        menu = studio_menu(f"{item.pvm.block_type}  {tag}",
                           item.pvm.label or "PVM")
        open_fp = menu.addAction("Open faceplate\tEnter")
        font = open_fp.font()
        font.setBold(True)
        open_fp.setFont(font)
        open_fp.triggered.connect(
            lambda: self.open_faceplate(item.pvm))
        watch = menu.addAction("Add to Watch Area")
        watch.triggered.connect(
            lambda: self.add_to_watch(item))
        menu.addSeparator()
        if self.mode == MODE_EDIT:
            pvm_cls = registry.get(
                item.pvm.block_type, item.pvm.role, item.pvm.variant) \
                or registry.get(item.pvm.block_type, item.pvm.role) \
                or registry.get(item.pvm.block_type, "dynamo_compact")
            if pvm_cls is not None:
                typography = menu.addAction(
                    "Edit PVM class typography…")
                typography.setToolTip(
                    "Edit named profiles shared by every linked instance")

                def edit_typography():
                    opener = getattr(self.window(), "open_pvm_config", None)
                    if callable(opener):
                        opener(pvm_cls.__name__)

                typography.triggered.connect(edit_typography)
                menu.addSeparator()
            straight = menu.addAction("Straighten Connected Run")
            straight.setEnabled(self.can_straighten_connected_run(item))
            straight.setToolTip(
                "Make the inlet and outlet pipes share one centerline")
            straight.triggered.connect(
                lambda _checked=False, it=item:
                self.straighten_connected_run(it))
            menu.addSeparator()
            # Relink PVM (working-pvms p.22): re-point instances at
            # a different class; everything transferable transfers,
            # choices the new class does not know stay in the record
            # so relinking back restores them.
            from PySide6.QtWidgets import QMenu as _QMenu
            relink_menu = _QMenu("Relink PVM", menu)
            current_key = (item.pvm.block_type, item.pvm.role,
                           item.pvm.variant)
            for (bt, role, variant), cls in sorted(
                    registry.all_classes().items()):
                if role not in DYNAMO_ROLES \
                        or (bt, role, variant) == current_key:
                    continue
                label = cls.display_name or cls.__name__
                text = f"{label}   ·   {bt}/{role}" \
                    + (f" · {variant}" if variant else "")
                relink_menu.addAction(text).triggered.connect(
                    lambda _=False, b=bt, r=role, v=variant,
                    it=item: (it.setSelected(True),
                              self.relink_selected(b, r, v)))
            menu.addMenu(relink_menu)
            rotate = menu.addAction("Rotate 90°")
            rotate.triggered.connect(
                lambda: (item.setSelected(True),
                         self.rotate_selected(90)))
            rename = menu.addAction("Friendly name…")

            def do_rename():
                from azeo_control_trainer.core.presentation.headless import is_headless
                if is_headless():
                    self.rename_pvm(item, "Renamed")
                    return
                from PySide6.QtWidgets import QInputDialog as _QID
                text, ok = _QID.getText(self, "Friendly name",
                                        "Name:", text=item.pvm.label)
                if ok:
                    self.rename_pvm(item, text.strip())
            rename.triggered.connect(do_rename)
            duplicate = menu.addAction("Duplicate\tCtrl+D")
            duplicate.triggered.connect(self.duplicate_selected)
            delete = menu.addAction("Delete\tDel")
            delete.triggered.connect(self.delete_selected)
            menu.addSeparator()
        copy_path = menu.addAction("Copy path")
        copy_path.triggered.connect(
            lambda: __import__("PySide6.QtWidgets",
                               fromlist=["QApplication"])
            .QApplication.clipboard().setText(
                "/".join(str(v) for v in item.pvm.params.values())))
        retain_menu(self, menu, "_context_menu")
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not is_headless():
            menu.exec_transient(global_pos)

    def _set_grid(self, on: bool) -> None:
        self.grid_visible = bool(on)
        self.canvas.viewport().update()

    # ---------------------------------------------- §8.5 keyboard map
    def keyPressEvent(self, event) -> None:         # noqa: N802
        key, mods = event.key(), event.modifiers()
        ctrl = mods & Qt.ControlModifier
        shift = mods & Qt.ShiftModifier
        arrows = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0),
                  Qt.Key_Up: (0, -1), Qt.Key_Down: (0, 1)}
        if self.mode == MODE_EDIT and key in arrows and not ctrl:
            dx, dy = arrows[key]
            if shift:
                # Azeo: Shift+Up/Right grows in both directions;
                # Shift+Down/Left shrinks in both directions.
                self.resize_selected(dx, -dy)
            else:
                self.nudge_selected(dx, dy)
        elif key == Qt.Key_Delete and self.mode == MODE_EDIT:
            self.delete_selected()
        elif ctrl and key == Qt.Key_D:
            self.duplicate_selected()
        elif ctrl and key == Qt.Key_Z:
            self.undo()
        elif ctrl and key == Qt.Key_Y:
            self.redo()
        elif ctrl and (mods & Qt.ShiftModifier) and key == Qt.Key_G:
            self.ungroup_selected()
        elif ctrl and key == Qt.Key_G:
            self.group_selected()
        elif ctrl and key == Qt.Key_A:
            for item in self._items() + self._static_items():
                item.setSelected(True)
        elif not ctrl and self.mode == MODE_EDIT \
                and key in (Qt.Key_R, Qt.Key_E, Qt.Key_L,
                            Qt.Key_P, Qt.Key_A, Qt.Key_T):
            from PySide6.QtGui import QCursor
            kind = {Qt.Key_R: "rect", Qt.Key_E: "ellipse",
                    Qt.Key_L: "line", Qt.Key_P: "polyline",
                    Qt.Key_A: "arc", Qt.Key_T: "text"}[key]
            view_pos = self.canvas.mapFromGlobal(QCursor.pos())
            if self.canvas.viewport().rect().contains(view_pos):
                self.insert_at(kind,
                               self.canvas.mapToScene(view_pos))
        elif not ctrl and key == Qt.Key_V:
            self.select_tool()
        elif not ctrl and key == Qt.Key_H:
            self.set_pan(not getattr(self, "pan_active", False))
        elif not ctrl and shift and key == Qt.Key_F:
            self.fit_selection()
        elif not ctrl and key == Qt.Key_F:
            self.fit_drawing()
        elif key in (Qt.Key_Return, Qt.Key_Enter) \
                and getattr(self, "_polyline_armed", False):
            self.finish_polyline()
        elif key == Qt.Key_Escape:
            if any((getattr(self, "_place_ghost", None) is not None,
                    getattr(self, "connect_armed", False),
                    getattr(self, "_line_armed", False),
                    getattr(self, "_pencil_armed", False),
                    getattr(self, "_eraser_armed", False),
                    getattr(self, "_shape_armed", False),
                    getattr(self, "_polyline_armed", False))):
                self.cancel_gestures()
            elif self.mode == MODE_TEST:
                self.exit_test()
            else:
                self.canvas.scene().clearSelection()
        else:
            super().keyPressEvent(event)

    def open_faceplate(self, pvm: Pvm) -> None:
        """Open this PVM's faceplate, or raise the one already open.

        **One faceplate per PVM.** Binding names are unique per
        (class, path, key), so a second widget for the same PVM
        re-bound names the first still holds and `bind_all` raised
        `BindingError` — straight out of `mouseDoubleClickEvent`,
        which took the whole application down. Raising the existing
        window is also what the operator means by clicking a dynamo
        they already have open.

        A faceplate that has been CLOSED released its bindings in
        `closeEvent`, so its slot is free and a fresh one is built.

        Two PVMs bound to the SAME module share one faceplate, which
        is both what the operator means and what the binding engine
        requires — the names are per path, not per placement.
        """
        pvm_cls = registry.get(pvm.block_type, "faceplate", pvm.variant) or registry.get(pvm.block_type, "faceplate")
        if pvm_cls is None:
            return
        key = (pvm_cls.__name__,
               tuple(sorted((str(k), str(v))
                            for k, v in (pvm.params or {}).items())))
        existing = self._faceplates.get(key)
        if existing is not None and _still_bound(existing):
            existing.raise_()
            existing.activateWindow()
            return
        widget = PvmFaceplateWidget(pvm_cls, pvm.params, self.engine,
                                    theme=self.theme, parent=self)
        widget.set_write_handler(self._write_user_entry,
                                 self.engine.can_write)
        widget.setWindowFlag(Qt.Window, True)
        widget.action_requested.connect(
            lambda action, g=pvm: self.faceplate_action(
                action, g, origin_kind="faceplate"))
        available = widget.serviceable_actions()
        if resolve_module_faceplate(pvm, self.engine) is not None:
            available.add("faceplate")
        if resolve_associated_dcc(pvm, self.engine) is not None:
            available.add("dcc")
        from azeo_control_trainer.core.hmi.pvms.contextual import tuning_paths
        if tuning_paths(pvm_cls, pvm.params):
            available.add("history")
        # The source registry is the same alarm state the compact list reads,
        # so ACK can be offered without fabricating a second alarm service.
        if self._alarm_registry() is not None:
            available.add("ack")
        # The originating display is already the best primary-control target
        # Graphics Designer can answer honestly; the action raises that display
        # from behind the contextual faceplate.
        available.add("primary")
        if self._control_designer_host() is not None:
            available.add("studio")
        widget.set_available_actions(available)
        self._faceplates[key] = widget
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not is_headless():
            widget.show()

    def faceplate_action(self, action: str, pvm: Pvm, *,
                         origin_kind: str = "faceplate") -> None:
        """Service every Loop_fp action this host can answer honestly."""
        if action == "faceplate":
            target = pvm if origin_kind == "detail" \
                else resolve_module_faceplate(pvm, self.engine)
            if target is not None:
                self.open_faceplate(target)
        elif action == "dcc":
            target = resolve_associated_dcc(pvm, self.engine)
            if target is not None:
                self.open_faceplate(target)
        elif action == "detail":
            self.open_detail(pvm)
        elif action == "primary":
            host = self.window()
            host.show()
            host.raise_()
            host.activateWindow()
            self.canvas.setFocus()
        elif action == "studio":
            owner = self._control_designer_host()
            if owner is not None:
                owner.open_module(self._module_for(pvm))
        elif action == "history":
            self.open_process_history(self._module_for(pvm))
        elif action == "ack":
            self.acknowledge_faceplate(pvm)

    @staticmethod
    def _module_for(pvm: Pvm) -> str:
        path = primary_path(pvm.params or {})
        return path.partition("/")[0]

    def _alarm_registry(self):
        source = getattr(self.engine, "_source", None)
        return getattr(source, "alarm_state", None)

    def _control_designer_host(self):
        host = self.window()
        owner = host.parentWidget() if hasattr(host, "parentWidget") else None
        return owner if callable(getattr(owner, "open_module", None)) else None

    def acknowledge_faceplate(self, pvm: Pvm) -> tuple[str, ...]:
        registry = self._alarm_registry()
        path = primary_path(pvm.params or {})
        module, separator, remainder = path.partition("/")
        block = remainder.split("/", 1)[0] if separator else ""
        if registry is None or not module or not block:
            return ()
        records = registry.records(blocks=(f"{module}/{block}",))
        changed = registry.acknowledge(record.key for record in records)
        for widget in self._faceplates.values():
            widget.refresh()
        return changed

    def open_detail(self, pvm: Pvm) -> None:
        """Open the registered block detail without duplicating bindings."""
        detail_cls = registry.get(pvm.block_type, "detail")
        if detail_cls is None:
            return
        key = (detail_cls.__name__,
               tuple(sorted((str(k), str(v))
                            for k, v in (pvm.params or {}).items())))
        existing = self._faceplates.get(key)
        if existing is not None and _still_bound(existing):
            existing.raise_()
            existing.activateWindow()
            return
        widget = PvmFaceplateWidget(detail_cls, pvm.params, self.engine,
                                    theme=self.theme, parent=self)
        widget.set_write_handler(self._write_user_entry,
                                 self.engine.can_write)
        widget.setWindowFlag(Qt.Window, True)
        widget.action_requested.connect(
            lambda action, g=pvm: self.faceplate_action(
                action, g, origin_kind="detail"))
        self._faceplates[key] = widget
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not is_headless():
            widget.show()

    def open_tuning_trend(self, pvm: Pvm):
        """Open a live trend for every conventional value the class owns."""
        pvm_cls = registry.get(pvm.block_type, "faceplate", pvm.variant) or registry.get(pvm.block_type, "faceplate")
        if pvm_cls is None:
            return None
        from azeo_control_trainer.core.hmi.pvms.contextual import TuningTrendWidget, tuning_paths
        paths = tuning_paths(pvm_cls, pvm.params)
        if not paths:
            return None
        widget = TuningTrendWidget(
            self.engine, paths, theme=self.theme, parent=self)
        widget.setWindowFlag(Qt.Window, True)
        self._tuning_trends.append(widget)
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not is_headless():
            widget.show()
        return widget

    def open_process_history(self, module: str = ""):
        """Open the shared Process History View for a faceplate module."""
        from azeo_control_trainer.core.hmi.history.view import ProcessHistoryView

        self.historian.configure_from(
            None, list(self.graphs_provider().values()))
        self.historian.collect(force=True)
        view = ProcessHistoryView(self.historian, module, self)
        view.setAttribute(Qt.WA_DeleteOnClose, True)
        self._process_history_views.append(view)

        def release(_object=None, *, target=view):
            try:
                self._process_history_views.remove(target)
            except ValueError:
                pass

        view.destroyed.connect(release)
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not is_headless():
            view.show()
            view.raise_()
        return view

    def _on_selection(self) -> None:
        self.update_geometry_readout()
        snapshot = self.selection.snapshot()
        self._selection_overlay.setVisible(
            self.mode == MODE_EDIT and snapshot.count > 1)
        if snapshot.count > 1:
            self._selection_overlay.setRect(snapshot.bounds)
        selected = snapshot.items
        if len(selected) == 1 and isinstance(selected[0], PvmItem):
            self.pane.show_pvm(selected[0])
            return
        if len(selected) == 1 and isinstance(selected[0], (StaticItem, PipeItem)):
            # Azeo's Graphics Configuration pane: select a shape,
            # its properties appear on the right (paper p.27).
            self.pane.show_item(selected[0])
        else:
            self.pane.show_pvm(None)

    # --------------------------------------------------------- lifecycle
    def _sync_document(self) -> None:
        """Copy the scene back onto the display record — one walk."""
        pvms, drawings, order = [], [], []
        for item in self._document_items(Qt.AscendingOrder):
            if isinstance(item, PvmItem):
                pvms.append(item.pvm)
                order.append(item.pvm.id)
            elif isinstance(item, (StaticItem, PipeItem)):
                drawings.append(item.data)
                order.append(item.data["id"])
        self.display.pvms = pvms
        self.display.items = drawings
        self.display.stacking_order = order
        self.display.variables = self.variables.to_list()

    def mark_unsaved(self, *, sync_document=True) -> None:
        if self._moving_selection:
            return
        self.documentChanged.emit()
        self.unsaved = True
        # Pointer movement changes retained item geometry, not membership.
        # Release, recovery and Save capture it through the same document walk.
        if sync_document:
            self._sync_document()
        # Every mutation funnels through here, which makes it the one
        # place that cannot forget to invalidate the crossover cache.
        self.touch_geometry()
        self._sync_status()
        if hasattr(self, "_recovery_timer"):
            self._recovery_timer.start()
            if not self._recovery_deadline_timer.isActive():
                self._recovery_deadline_timer.start()
            self.recoveryChanged.emit()

    def insert_pipe_junction(self, pipe, scene_pos):
        from .pipe_editing import insert_junction
        return insert_junction(self, pipe, scene_pos)

    def show_route_obstructions(self, pipe) -> int:
        """Locate the reason for a blocked route without changing its geometry."""
        identities = set(pipe.route_collisions)
        items = [item for item in self._items() + self._static_items()
                 if str(getattr(getattr(item, "pvm", None), "id", "")
                        or item_document_data(item).get("id", "")) in identities]
        if items:
            self.selection.replace(items, primary=items[0])
            bounds = items[0].sceneBoundingRect()
            for item in items[1:]:
                bounds = bounds.united(item.sceneBoundingRect())
            self.canvas.ensureVisible(bounds.adjusted(-24, -24, 24, 24))
        else:
            self.uiError.emit("No blocking object remains. Use Reroute now to recheck the pipe.")
        return len(items)

    def recovery_status(self) -> str:
        if self.recovery_error:
            return "Recovery unavailable"
        if self.recovery_saved_at:
            return f"Recovery {self.recovery_saved_at}"
        return "Recovery pending" if self.unsaved else "Saved draft"

    def _write_recovery(self) -> bool:
        """Persist the current unsaved document without promoting it."""
        self._recovery_timer.stop()
        self._recovery_deadline_timer.stop()
        if not self.unsaved:
            return True
        try:
            if not self.store.owns_lock(self.display.name):
                raise OSError("The editing lock is no longer held. Reopen the display before saving.")
            if self.store.draft_fingerprint(self.display.name) != self._draft_fingerprint:
                raise OSError("The saved display changed. Reopen it before saving.")
            self._sync_document()
            self.store.save_recovery(self.display)
        except Exception as error:  # noqa: BLE001 - timer callback must not escape into Qt
            message = str(error) or type(error).__name__
            if message != self.recovery_error:
                logging.getLogger(__name__).exception("Display recovery failed")
                self.uiError.emit(f"Recovery unavailable: {message}")
            self.recovery_error = message
            if not self._closing:
                self._recovery_deadline_timer.start()
            self.recoveryChanged.emit()
            return False
        self.recovery_error = ""
        self.recovery_saved_at = datetime.now().astimezone().strftime("%H:%M:%S")
        self.recoveryChanged.emit()
        return True

    def _sync_status(self) -> None:
        self.status_label.setText(
            f"{self.tier.name} ●" + ("  UNSAVED" if self.unsaved else ""))

    #: A display with this prefix is a user PVM class's layout, open
    #: for editing (the class_edit pattern): Save rebuilds the class.
    PVM_EDIT_PREFIX = "_pvm_"

    def edited_user_class_name(self) -> str:
        """Class represented by this document, or empty for a display.

        Keeping the test here gives the canvas and inspector one definition
        of class-master mode.  The prefix is an editor routing detail and
        must never leak into the reusable-class model or published displays.
        """
        name = str(self.display.name)
        return name[len(self.PVM_EDIT_PREFIX):] \
            if name.startswith(self.PVM_EDIT_PREFIX) else ""

    def save_draft(self) -> bool:
        """Durably save the current document.

        A class-master capture may legitimately fail (for example an empty
        master).  Treat that as a failed Save: the dirty flag and recovery
        checkpoint remain, so closing cannot silently discard the author's
        only copy.
        """
        try:
            if not self.store.owns_lock(self.display.name):
                self.uiError.emit("Open this display for editing before saving.")
                return False
            if self.store.draft_fingerprint(self.display.name) != self._draft_fingerprint:
                self.uiError.emit("The saved display changed. Reopen it before saving.")
                return False
            self._sync_document()
            class_name = self.edited_user_class_name()
            # A class master is not an operator Display. Persisting its
            # temporary `_pvm_` document put it under Displays and made the
            # Publish command capable of deploying authoring chrome. The
            # class library is its sole durable document.
            if class_name:
                if not self._capture_pvm_class():
                    self.unsaved = True
                    self._write_recovery()
                    self._sync_status()
                    return False
            else:
                self.store.save_draft(self.display)
                self._draft_fingerprint = self.store.draft_fingerprint(self.display.name)
        except Exception as error:  # noqa: BLE001 - Save is also a Qt action
            logging.getLogger(__name__).exception("Display save failed")
            self.uiError.emit(f"Save failed: {error}")
            self._sync_status()
            return False
        self.unsaved = False
        self._recovery_timer.stop()
        self._recovery_deadline_timer.stop()
        self.recovery_error = ""
        self.recovery_saved_at = ""
        try:
            self.store.clear_recovery(self.display.name)
        except OSError as error:
            # The draft is durable already; a cleanup failure cannot undo Save.
            self.recovery_error = f"Draft saved; recovery cleanup failed: {error}"
        self.recoveryChanged.emit()
        self._sync_status()
        return True

    def _capture_pvm_class(self) -> bool:
        """Rebuild the user PVM class from this canvas. Shape
        bindings (`Pvm.X` / `Standard.Y` references) survive by
        shape id — what the layout editor does not understand it
        must not lose. Every placed instance follows on its next
        repaint, free, because rendering goes through the one live
        library."""
        name = self.display.name[len(self.PVM_EDIT_PREFIX):]
        data = self._drawing_data()
        if not name or not data:
            return False
        library = self.user_library()
        library.reload()        # bindings may be newer than our cache
        old = library.entries.get(name) or {}
        old_shapes = {d.get("id"): d for d in old.get("items", [])}
        old_sources = {d.get("source_element_id"): d
                       for d in old.get("items", [])
                       if d.get("source_element_id")}
        old_order = {
            (d.get("source_element_id") or d.get("id")): index
            for index, d in enumerate(old.get("items", ()))
        }
        carried = []
        for d in data:
            d = dict(d)
            previous = old_shapes.get(d.get("id")) \
                or old_sources.get(d.get("source_element_id"))
            if previous:
                for key in library.BINDABLE:
                    value = previous.get(key)
                    if isinstance(value, str) and (
                            value.startswith(PVM_SCOPE_PREFIXES)
                            or value.startswith("Standard.")):
                        d[key] = value
            carried.append(d)
        # QGraphicsScene returns equal-z items in paint-stack order, not
        # class-document order.  Re-saving a master must not reshuffle member
        # indices (legacy overrides still address them); stable source UUIDs
        # let known members retain their order while new members append.
        indexed = list(enumerate(carried))
        indexed.sort(key=lambda pair: old_order.get(
            pair[1].get("source_element_id") or pair[1].get("id"),
            len(old_order) + pair[0]))
        carried = [item for _index, item in indexed]
        master_size = (float(self.display.width),
                       float(self.display.height))
        if library.add(name, carried, master_size=master_size) is None:
            return False
        window = self.window()
        if hasattr(window, "refresh_user_pvms"):
            window.refresh_user_pvms()
        if hasattr(window, "refresh_user_pvm_instances"):
            window.refresh_user_pvm_instances(name, exclude=self)
        return True

    def _resolve(self, path: str) -> bool:
        from azeo_control_trainer.core.hmi.binding.result import UNRESOLVED
        return self.engine._source.read(f"{path}/OUT") \
            is not UNRESOLVED or \
            self.engine._source.read(f"{path}/PV") is not UNRESOLVED \
            or self.engine._source.read(f"{path}/STATE") \
            is not UNRESOLVED

    def open_publish(self) -> None:
        self._sync_document()
        findings = self.verification_findings()
        history = self.store.history(self.display.name)
        previous = self.store.revision_document(
            self.display.name, history[-1]["rev"]) \
            if history else {"pvms": []}
        diff = display_diff(previous, self.display.to_dict())
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Publish display — {self.display.name}")
        lay = QVBoxLayout(dialog)
        add_authoring_dialog_header(
            dialog,
            lay,
            "Publish display",
            f"Review validation and release {self.display.name} to a governed revision.",
        )
        env = AuthoringComboBox()
        env.addItems(["TEST", "PROD"])
        form = QFormLayout()
        form.addRow("To", env)
        lay.addLayout(form)
        lay.addWidget(QLabel("Changes since last revision:"))
        listing = QListWidget()
        for line in (diff or ["(no changes)"]):
            listing.addItem(line)
        lay.addWidget(listing)
        compare = QPushButton("Visual comparison…")
        compare.clicked.connect(lambda: self.open_revision_review(dialog))
        lay.addWidget(compare)
        lay.addWidget(QLabel("Verification:"))
        verification = QListWidget()
        verification.setObjectName("publish_verification")
        if not findings:
            verification.addItem("PASS  No verification findings")
        for finding in findings:
            row = QListWidgetItem(
                f"{finding.severity.upper()}  {finding.message}")
            if finding.severity == "error":
                row.setForeground(QColor("#B42318"))
            elif finding.severity == "warning":
                row.setForeground(QColor("#B54708"))
            verification.addItem(row)
        lay.addWidget(verification)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                   | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Publish")
        blockers = [one for one in findings if one.blocks_publish]
        buttons.button(QDialogButtonBox.Ok).setEnabled(not blockers)
        if blockers:
            buttons.button(QDialogButtonBox.Ok).setToolTip(
                f"Resolve {len(blockers)} verification error(s) first")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        style_dialog_buttons(buttons, QDialogButtonBox.Ok)
        lay.addWidget(buttons)
        self._publish_dialog = dialog
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not is_headless() and dialog.exec() != QDialog.Accepted:
            return
        try:
            self.publish(env.currentText())
        except PublishRefused as error:
            if not is_headless():
                QMessageBox.warning(self, "Publish refused", str(error))
            else:
                raise

    def publish(self, env: str = "TEST",
                workstations: list | None = None) -> dict:
        if not self.store.owns_lock(self.display.name):
            raise PublishRefused("Open this display for editing before publishing.")
        if self.store.draft_fingerprint(self.display.name) != self._draft_fingerprint:
            raise PublishRefused("The saved display changed. Reopen it before publishing.")
        self._sync_document()
        # Never accept caller-supplied findings here.  A public override made
        # ``publish(findings=())`` a release-gate bypass; direct, dialog, and
        # automated publishes must all execute the current complete preflight.
        findings = self.verification_findings()
        from azeo_control_trainer.config.logging_config import audit_event
        try:
            entry = self.store.publish(self.display, env=env,
                                       workstations=workstations,
                                       resolve=self._resolve,
                                       findings=findings)
        except Exception as error:
            audit_event(
                "graphics_designer", "display.publish", outcome="refused",
                display=self.display.name, environment=env,
                reason=type(error).__name__)
            raise
        audit_event(
            "graphics_designer", "display.publish",
            display=self.display.name, environment=env,
            revision=entry.get("rev", ""))
        self.save_draft()
        return entry

    def open_revision_review(self, parent=None):
        from ..revision_review import RevisionReview
        from azeo_control_trainer.core.presentation.headless import is_headless
        previous = getattr(self, "_revision_review", None)
        if previous is not None:
            previous.close()
            previous.deleteLater()
        self._revision_review = RevisionReview(self)
        if parent is not None:
            self._revision_review.setParent(parent, Qt.Dialog)
            self._revision_review.review_parent = parent
            self._revision_review.publish_button.setText("Back to review")
        if not is_headless():
            self._revision_review.show()
        return self._revision_review

    def open_history(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(f"{self.display.name} — history")
        lay = QVBoxLayout(dialog)
        add_authoring_dialog_header(
            dialog,
            lay,
            "Revision history",
            f"Review, compare or restore published revisions of {self.display.name}.",
        )
        table = QTableWidget(0, 5)
        table.setHorizontalHeaderLabels(
            ["Rev", "Env", "Published", "By", ""])
        entries = self.store.history(self.display.name)
        table.setRowCount(len(entries))
        for row, entry in enumerate(reversed(entries)):
            for column, key in enumerate(("rev", "env", "at", "by")):
                table.setItem(row, column,
                              QTableWidgetItem(str(entry.get(key, ""))))
            button = QPushButton("Revert")
            button.clicked.connect(
                lambda checked=False, r=entry["rev"]:
                self._revert(r, dialog))
            table.setCellWidget(row, 4, button)
        lay.addWidget(table)
        compare = QPushButton("Compare revisions…")
        compare.clicked.connect(lambda: self.open_revision_review(dialog))
        lay.addWidget(compare)
        self._history_dialog = dialog
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not is_headless():
            dialog.exec()

    def _revert(self, rev: int, dialog=None) -> None:
        if not self.store.owns_lock(self.display.name):
            self.uiError.emit("Open this display for editing before reverting.")
            return
        self.store.revert(self.display.name, rev)
        self._draft_fingerprint = self.store.draft_fingerprint(self.display.name)
        draft = self.store.load_draft(self.display.name)
        if draft is not None:
            # Through the one document loader: reverting used to drop
            # the PVMs only, leaving the superseded revision's shapes
            # and pipes on the canvas and restoring none of the
            # reverted ones.
            self._load_document(draft.to_dict())
        self.unsaved = False
        self._sync_status()
        if dialog is not None:
            dialog.accept()

    def closeEvent(self, event) -> None:            # noqa: N802
        self._closing = True
        self.documentChanged.emit()
        self._timer.stop()
        self._recovery_timer.stop()
        self._write_recovery()
        for widget in list(self._faceplates.values()):
            widget.close()
        self._faceplates.clear()
        for widget in self._tuning_trends:
            widget.close()
        self._tuning_trends.clear()
        for widget in list(self._process_history_views):
            widget.close()
        self._process_history_views.clear()
        for item in self._items() + self._static_items():
            self.renderer.unbind(item)
        self.store.release_lock(self.display.name)
        self._holds_lock = False
        super().closeEvent(event)


# Existing extensions may still import the former class name. Keep that name
# as an alias while all first-party code and documentation use PvmStudio.
globals()["Pvm" + "Studio"] = PvmStudio
