"""The operator's view of a published PVM display.

This is the "what you get" half of WYSIWYG, and it exists mainly so
that the half can be *checked*. Before it, `DisplayStore.published_document`
had no reader anywhere in the tree: the studio was the only thing that
could draw a PVM display, so "the editor shows what the operator sees"
was a claim with nothing on the other side of it.

It builds through `DisplayRenderer` — the same object the studio's
canvas builds through — so the two cannot drift. It removes every
authoring affordance:

- no grid, ever (the studio hides it outside EDIT for the same reason)
- items are neither movable nor selectable, so no handles or anchors
  paint and no hover chrome appears — enforced in `_read_only`, which
  had to be written after this line had been true on paper and false
  in the code for long enough that an operator could drag a PVM on a
  published display
- no tools, no undo, no lock

Runtime Ctrl-click tag selection is a view overlay for the historian. It never
sets the scene items' authoring selection or movement flags.

`tests/_smoke_operator.py` renders the same display through the studio
in TEST mode and through this, and compares the two images pixel for
pixel. That test is the WYSIWYG guarantee; this module is what makes it
possible to write.
"""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from functools import wraps
import logging
from numbers import Real

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QCursor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QGraphicsScene, QGraphicsView, QMenu, QToolTip

from azeo_control_trainer.core.hmi.binding import BindingEngine, LiveGraphSource
from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore, PvmDisplay
from azeo_control_trainer.core.hmi.theme.roles import Role
from azeo_control_trainer.core.hmi.theme.tokens import DEFAULT_THEME, THEMES
from .chrome import STUDIO_THEME
from .renderer import DisplayRenderer, pvm_from_dict

#: Match the studio's own poll period, so a value is no fresher in one
#: window than the other.
REFRESH_MS = 250


def _guard_view_event(callback):
    @wraps(callback)
    def guarded(self, event):
        try:
            return callback(self, event)
        except Exception:  # noqa: BLE001 - Qt must never inherit a pending Python error
            logging.getLogger(__name__).exception("Operator display interaction failed")
            self._pvm_press_item = self._pvm_press_position = self._pan_position = None
            self._chart_selecting = self._pvm_press_dragged = False
            self.clear_chart_selection()
            self.viewport().unsetCursor()
            self._chart_tip("Display interaction failed. See the application log for details.")
            event.accept()
    return guarded


@dataclass(frozen=True)
class ChartCandidate:
    """One numeric value the operator can send to Process History View.

    The live :class:`Binding` travels with the path so the station can use
    the value's real engineering units and range when the historian did not
    already collect that point.  A painted string is never reverse-parsed
    into a tag; the action follows the same binding that produced it.
    """

    label: str
    path: str
    binding: object


def chart_candidates(item) -> tuple[ChartCandidate, ...]:
    """Return direct numeric bindings visibly represented by ``item``.

    PVM classes can bind diagnostic/configuration values that they do not
    paint on their compact display.  Offering those in a value context menu
    would make the menu claim the pointer was over data that is not there, so
    the operator surface is intentionally limited to the primary value and
    the standard PV/SP/OUT/readback rows.  Numeric Data Links carry exactly
    one displayed binding and therefore resolve without a chooser.
    """
    from .items import PvmItem, StaticItem

    labelled = []
    if isinstance(item, PvmItem):
        for label in ("PV", "SP", "OUT", "READBACK"):
            binding = item.rows.get(label)
            if binding is not None:
                labelled.append((label, binding))
        if item.binding is not None:
            terminal = str(item.binding.path).rstrip("/").rsplit("/", 1)[-1]
            labelled.append((terminal or "Value", item.binding))
    elif isinstance(item, StaticItem) \
            and item.data.get("kind") == "datalink" \
            and item.data.get("datalink_type", "numeric") in (
                "numeric", "scaled_numeric"):
        labelled.append((str(item.data.get("label", "") or "Value"),
                         item.binding))

    answer, seen = [], set()
    for label, binding in labelled:
        path = str(getattr(binding, "path", "") or "").strip()
        result = getattr(binding, "result", None)
        value = getattr(result, "value", None)
        if not isinstance(value, Real) or isinstance(value, bool) \
                or not path or path in seen:
            continue
        seen.add(path)
        answer.append(ChartCandidate(str(label), path, binding))
    return tuple(answer)


class PvmDisplayView(QGraphicsView):
    """One published display, live and read-only."""

    def __init__(self, document: dict, graphs_provider,
                 theme: str = STUDIO_THEME, config_root=None,
                 live: bool = True, parent=None, open_display=None,
                 source=None, layout=None, action_handler=None,
                 write_handler=None, write_checker=None,
                 script_scopes=None, pvm_activation_handler=None,
                 chart_handler=None, chart_batch_handler=None):
        super().__init__(parent)
        self.display = PvmDisplay.from_dict(document)
        from ..symbols import register_document_symbols
        symbol_aliases = register_document_symbols(self.display.symbol_assets)
        self._symbol_aliases = symbol_aliases
        self.palette_roles = THEMES.get(theme, THEMES[DEFAULT_THEME])
        self.engine = BindingEngine(source or LiveGraphSource(graphs_provider))
        self.engine.configuration_root = config_root
        self.layout_model = layout
        self.config_root = config_root
        self._functions = self._standards = None
        self._action_handler = action_handler
        # PVM activation is a host policy, not a renderer policy. The
        # operator station opts in to one-click contextual faceplates while
        # plain viewers and Graphics Designer keep their existing interaction
        # model. Keeping the callback here avoids replacing Qt event methods
        # on individual view instances, which made the old double-click path
        # both difficult to test and easy to lose during view replacement.
        self._pvm_activation_handler = pvm_activation_handler
        # Only the operator host supplies this.  Graphics Designer uses its own
        # authoring context menus and must never acquire runtime chart verbs.
        self._chart_handler = chart_handler
        self._chart_batch_handler = chart_batch_handler
        self._chart_selection = {}
        self._chart_selecting = False
        self._last_chart_context_menu = None
        self._manual_view = False
        self._pan_position = None
        self._active = True
        self._disposed = False
        self._pvm_press_item = None
        self._pvm_press_position = None
        self._pvm_press_dragged = False
        self.script_scopes = script_scopes if script_scopes is not None else {}
        self.renderer = DisplayRenderer(
            self.engine, self.palette_roles, config_root=config_root,
            action_handler=open_display,
            write_handler=write_handler,
            write_checker=write_checker,
            interaction_handler=(
                (lambda action, item: action_handler(action, self, item))
                if action_handler is not None else None),
            alarm_provider=lambda: self.engine._source.alarm_state.records(),
            symbol_aliases=symbol_aliases)
        self.setScene(QGraphicsScene(self))
        # PvmItem reads display-level furniture (L1 alarm box versus L2+
        # alarm icon, ShowTag) from its scene.  Store only the document here,
        # not the view itself: a view -> scene -> view Python cycle can outlive
        # Qt's C++ owner during teardown.  Omitting the document made every
        # operator view silently render as L2.
        self.scene().display = self.display
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setWindowTitle(self.display.name)
        # Binding construction reads immediately. Without one inventory
        # snapshot a large display enumerated all controller modules once
        # for every parameter before its first paint.
        with getattr(self.engine._source, "snapshot", nullcontext)():
            self._populate()
        self._apply_display_view()
        self._dispatch_display_event("open")
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        if live:
            self._timer.start(REFRESH_MS)

    # ---------------------------------------------------------- build
    def apply_theme(self, theme: str) -> bool:
        """Repaint retained items without replaying scripts or replacing bindings."""
        from .items import PvmItem, PipeItem, StaticItem
        palette = THEMES.get(theme)
        if palette is None:
            return False
        self.palette_roles = palette
        self.renderer.palette = palette
        for item in self.scene().items():
            if isinstance(item, PvmItem):
                item.set_palette(palette)
                item.update()
            elif isinstance(item, (PipeItem, StaticItem)):
                item._palette = palette
                item.update()
        self.viewport().update()
        return True

    def _populate(self) -> None:
        scene = self.scene()
        self._refresh_items = []
        for is_pvm, data in self.renderer.ordered_content(self.display):
            item = (self.renderer.build_pvm(pvm_from_dict(data)) if is_pvm
                    else self.renderer.build_drawing(data))
            scene.addItem(self._read_only(item))
            self._refresh_items.append(item)
        self.reroute_pipes()

    def _apply_display_view(self) -> None:
        """Honor the authored Canvas frame in the operator renderer."""
        if self.display.width > 0 and self.display.height > 0:
            rect = QRectF(0.0, 0.0, float(self.display.width),
                          float(self.display.height))
        else:
            rect = self.scene().itemsBoundingRect()
        self.setSceneRect(rect)
        if self.display.view_type == "scale_to_frame" and not rect.isEmpty():
            self.fitInView(rect, Qt.KeepAspectRatio)
        elif self.display.view_type == "actual_size":
            self.resetTransform()

    def resizeEvent(self, event) -> None:          # noqa: N802
        super().resizeEvent(event)
        if not self._manual_view:
            self._apply_display_view()

    def fit_display(self) -> None:
        self._manual_view = False
        self._apply_display_view()

    def zoom_by(self, factor: float) -> None:
        self._manual_view = True
        target = min(8.0, max(0.1, self.transform().m11() * factor))
        self.scale(target / self.transform().m11(), target / self.transform().m11())

    @_guard_view_event
    def wheelEvent(self, event) -> None:  # noqa: N802
        if event.modifiers() & Qt.ControlModifier:
            self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
            self.zoom_by(1.15 if event.angleDelta().y() > 0 else 1 / 1.15)
            event.accept()
            return
        super().wheelEvent(event)

    def set_active(self, active: bool) -> None:
        """Retain navigation context while hidden views stop sampling."""
        if self._disposed or self._active == active:
            return
        self._active = active
        self._pvm_press_item = self._pvm_press_position = None
        self._chart_selecting = False
        self.clear_chart_selection()
        self._release_chart_context_menu()
        if active:
            self.refresh()
            self._dispatch_display_event("open")
            self._timer.start(REFRESH_MS)
        else:
            self._timer.stop()
            self._dispatch_display_event("close")

    @staticmethod
    def _pvm_item(item):
        """Walk drawing children back to their owning PVM, if any."""
        from .items import PvmItem

        while item is not None and not isinstance(item, PvmItem):
            item = item.parentItem()
        return item

    def _runtime_item_at(self, position, *, chart=False):
        """Resolve an operating target beneath noninteractive decoration."""
        from azeo_control_trainer.core.hmi.pvms.elements import TAB, actions_of
        from .items import PvmItem, StaticItem, item_document_data

        # Qt's first geometric hit can be the unfilled card surrounding a
        # visible PVM. Looking only at itemAt made every plant card inert.
        # Authored controls remain barriers, including disabled controls: a
        # click on one must never activate unrelated equipment behind it.
        seen = set()
        scene_point = self.mapToScene(position)
        hits = (self.items(position.x(), position.y(), 1, 1, Qt.IntersectsItemBoundingRect)
                if chart else self.items(position))
        for candidate in hits:
            local = candidate.mapFromScene(scene_point)
            if chart and not candidate.contains(local):
                # The printed HP tag is outside the authoring shape. Expand
                # runtime hits only to its painted band, not resize handles
                # or the empty padding of every item's bounding rectangle.
                from azeo_control_trainer.core.hmi.pvms.hp.tag import display_tag
                if not isinstance(candidate, PvmItem) \
                        or not candidate.pvm.choices.get("show_hp_anatomy", True) \
                        or not display_tag(candidate.pvm, candidate.show_tag) \
                        or not candidate.hp_tag_rect().contains(local):
                    continue
            item = candidate
            while item is not None and item not in seen:
                seen.add(item)
                if isinstance(item, PvmItem):
                    return item
                data = item_document_data(item)
                if data.get("kind") in ("display_link", "user_entry", TAB) or actions_of(data):
                    return None
                if chart and isinstance(item, StaticItem) and chart_candidates(item):
                    return item
                item = item.parentItem()
        return None

    @_guard_view_event
    def mousePressEvent(self, event) -> None:      # noqa: N802
        """Remember the left-button origin so a release is a real click."""
        if event.button() == Qt.MiddleButton:
            self._pan_position = event.position()
            self._manual_view = True
            self.viewport().setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        self._pvm_press_item = None
        self._pvm_press_position = None
        self._pvm_press_dragged = False
        self._chart_selecting = False
        if event.button() == Qt.LeftButton and (
                self._chart_handler is not None or self._chart_batch_handler is not None):
            if event.modifiers() & Qt.ControlModifier:
                self._chart_selecting = True
                self._pvm_press_item = self._runtime_item_at(event.position().toPoint(), chart=True)
                self._pvm_press_position = event.position()
                # Ctrl is a historian gesture, including over authored write
                # controls. It must not activate a faceplate or send a write.
                event.accept()
                return
            self.clear_chart_selection()
        if event.button() == Qt.MouseButton.LeftButton \
                and self._pvm_activation_handler is not None:
            self._pvm_press_item = self._runtime_item_at(event.position().toPoint())
            self._pvm_press_position = event.position()
        super().mousePressEvent(event)
        if self._pvm_press_item is not None:
            # Read-only scene items ignore Qt's default selection press. The
            # operator view owns this gesture and must keep its release.
            event.accept()

    @_guard_view_event
    def mouseMoveEvent(self, event) -> None:       # noqa: N802
        """Cancel activation once the pointer crosses Qt's drag threshold."""
        if self._pan_position is not None and event.buttons() & Qt.MiddleButton:
            delta = event.position() - self._pan_position
            self._pan_position = event.position()
            for bar, amount in ((self.horizontalScrollBar(), delta.x()),
                                (self.verticalScrollBar(), delta.y())):
                bar.setValue(bar.value() - round(amount))
            event.accept()
            return
        if self._pvm_press_item is not None \
                and event.buttons() & Qt.MouseButton.LeftButton \
                and self._pvm_press_position is not None:
            distance = (
                event.position() - self._pvm_press_position).manhattanLength()
            if distance > QApplication.startDragDistance():
                self._pvm_press_dragged = True
        super().mouseMoveEvent(event)

    @_guard_view_event
    def mouseReleaseEvent(self, event) -> None:    # noqa: N802
        """Activate one operator PVM on a normal left-click release.

        Activation is deliberately release-based, matching native buttons
        and avoiding a contextual popup while a press gesture is still in
        progress. Right-click continues through Qt unchanged so item context
        menus (including Add to Historian) remain reachable.
        """
        if event.button() == Qt.MiddleButton:
            self._pan_position = None
            self.viewport().unsetCursor()
            event.accept()
            return
        pvm = None
        pressed = self._pvm_press_item
        press_position = self._pvm_press_position
        dragged = self._pvm_press_dragged
        self._pvm_press_item = None
        self._pvm_press_position = None
        self._pvm_press_dragged = False
        selecting = self._chart_selecting
        self._chart_selecting = False
        if selecting and event.button() == Qt.LeftButton:
            released = self._runtime_item_at(event.position().toPoint(), chart=True)
            if pressed is not None and released is pressed and not dragged \
                    and (event.position() - press_position).manhattanLength() <= QApplication.startDragDistance():
                self.toggle_chart_selection(pressed)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton \
                and self._pvm_activation_handler is not None \
                and pressed is not None and press_position is not None:
            released = self._runtime_item_at(event.position().toPoint())
            distance = (event.position() - press_position).manhattanLength()
            if released is pressed and not dragged \
                    and distance <= QApplication.startDragDistance():
                pvm = pressed.pvm
        super().mouseReleaseEvent(event)
        if pvm is not None:
            self._pvm_activation_handler(pvm, self.engine)
            event.accept()

    @_guard_view_event
    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.modifiers() & Qt.ControlModifier and (
                self._chart_handler is not None or self._chart_batch_handler is not None):
            self.mousePressEvent(event)
            return
        super().mouseDoubleClickEvent(event)

    @_guard_view_event
    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape and self._chart_selection:
            self.clear_chart_selection()
            event.accept()
            return
        super().keyPressEvent(event)

    def clear_chart_selection(self) -> None:
        if not self._chart_selection:
            return
        self._chart_selection.clear()
        self.viewport().update()

    def selected_chart_candidates(self) -> tuple[ChartCandidate, ...]:
        return tuple({candidate.path: candidate
                      for candidate in self._chart_selection.values()}.values())

    def toggle_chart_selection(self, item) -> None:
        candidates = chart_candidates(item)
        if item in self._chart_selection:
            del self._chart_selection[item]
        elif candidates:
            from azeo_control_trainer.core.hmi.history import MAX_CHART_PENS
            if len(self.selected_chart_candidates()) >= MAX_CHART_PENS:
                self._chart_tip(f"Select up to {MAX_CHART_PENS} tags per historian chart.")
                return
            # A PVM represents its PV (or primary value); its context menu
            # keeps SP/OUT individually available without adding hidden pens.
            self._chart_selection[item] = candidates[0]
        self.viewport().update()

    def drawForeground(self, painter, rect) -> None:  # noqa: N802
        super().drawForeground(painter, rect)
        if not self._chart_selection:
            return
        # View-only ink never changes item flags or document bounds. A full
        # viewport update on deselection also erases the outline outside items.
        painter.save()
        pen = QPen(QColor(self.palette_roles[Role.FOCUS]), 2)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        margin = 4 / max(0.1, self.transform().m11())
        for item in self._chart_selection:
            painter.drawRoundedRect(item.sceneBoundingRect().adjusted(
                -margin, -margin, margin, margin), margin, margin)
        painter.restore()

    def _chart_tip(self, message) -> None:
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not is_headless():
            QToolTip.showText(QCursor.pos(), message, self)

    def _send_to_historian(self, candidates) -> None:
        try:
            if self._chart_batch_handler is not None:
                self._chart_batch_handler([(one.path, one.binding) for one in candidates])
            else:
                for one in candidates:
                    self._chart_handler(one.path, one.binding)
            self.clear_chart_selection()
        except Exception:  # noqa: BLE001 - a host failure must not escape Qt
            logging.getLogger(__name__).exception("Could not open selected historian tags")
            self._chart_tip("Could not open the historian. See the application log for details.")

    def chart_context_menu(self, item) -> QMenu | None:
        """Build the value menu without displaying it.

        Keeping construction separate from ``contextMenuEvent`` makes the
        complete operator path testable offscreen: a test can trigger the
        real QAction and prove the bound path reaches the station service,
        while no modal native menu is left waiting in CI.
        """
        candidates = chart_candidates(item)
        if not candidates or (self._chart_handler is None and self._chart_batch_handler is None):
            return None
        # The view retains only the currently visible popup. Making the menu
        # a child of the long-lived display caused every prior right-click to
        # survive until the operator changed displays.
        menu = QMenu()
        menu.setObjectName("operator_historian_menu")
        from azeo_control_trainer.core.hmi.theme.widgets import apply_widget_theme, operator_service
        service = operator_service(self)
        if service is not None:
            apply_widget_theme(menu, service.current)
        else:
            from azeo_control_trainer.core.presentation.authoring_style import AUTHORING_CHROME_QSS
            menu.setStyleSheet(AUTHORING_CHROME_QSS)
        menu._chart_actions = []
        from azeo_control_trainer.core.presentation.studio_icons import studio_icon

        def add(target, text, points):
            action = QAction(studio_icon("history", 16), text, target)
            target.addAction(action)
            menu._chart_actions.append(action)
            action.setToolTip("\n".join(one.path for one in points))
            action.triggered.connect(lambda _checked=False, chosen=points: self._send_to_historian(chosen))
            return action

        selected = self.selected_chart_candidates() if item in self._chart_selection else ()
        if not selected:
            self.clear_chart_selection()
            selected = candidates[:1]
        label = "Add to Historian"
        if len(selected) > 1:
            label += f" ({len(selected)} tags)"
        add(menu, label, selected).setObjectName("add_to_historian")
        if len(candidates) > 1:
            target = QMenu("Other values", menu)
            menu.addMenu(target)
            menu._chart_values_menu = target
            for candidate in candidates:
                add(target, f"{candidate.label}  —  {candidate.path}", (candidate,))
            target.addSeparator()
            add(target, "All displayed values", candidates)
        if self._chart_selection:
            action = QAction("Clear tag selection", menu)
            menu.addAction(action)
            menu._chart_actions.append(action)
            action.triggered.connect(self.clear_chart_selection)
        menu.setToolTipsVisible(True)
        return menu

    def _release_chart_context_menu(self, menu=None, *, close=True) -> None:
        """Dispose the transient popup without retaining hidden QMenus."""
        target = menu or self._last_chart_context_menu
        if target is None:
            return
        if target is self._last_chart_context_menu:
            self._last_chart_context_menu = None
        if close:
            target.close()
        target.deleteLater()

    @_guard_view_event
    def contextMenuEvent(self, event) -> None:     # noqa: N802
        """Offer a real historian action only over a numeric live value."""
        self._release_chart_context_menu()
        item = self._runtime_item_at(event.pos(), chart=True)
        menu = self.chart_context_menu(item) if item is not None else None
        self._last_chart_context_menu = menu
        if menu is None:
            super().contextMenuEvent(event)
            return
        event.accept()
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not is_headless():
            menu.aboutToHide.connect(
                lambda current=menu:
                self._release_chart_context_menu(current, close=False))
            menu.popup(event.globalPos())

    @staticmethod
    def _read_only(item):
        """Strip the authoring flags off one item.

        **This module's docstring claimed it and nothing did it.**
        `PvmItem.__init__` sets `ItemIsMovable | ItemIsSelectable`
        because the studio needs them, the renderer is shared with the
        studio by design, and the viewer never cleared them — so an
        operator could select a PVM on a published display, see the
        studio's selection handles, and DRAG it.

        The renderer stays shared (one painter, one truth); what the
        operator's view removes is the affordances, which is exactly
        what it says it does.
        """
        from PySide6.QtWidgets import QGraphicsItem
        from .items import StaticItem

        item.setFlag(QGraphicsItem.ItemIsMovable, False)
        # Qt's bounded pixmap cache also retains unchanged operator buttons and
        # tables. Repainting all workflow text on every scan exceeded a frame
        # budget even when neither the page nor its observations had changed.
        # Binding, animation, permission, paging and theme changes call update().
        if isinstance(item, StaticItem) and item.data.get("kind") in {
                "rect", "ellipse", "text", "symbol", "polygon", "polyline", "user_entry", "table"} \
                and not item.data.get("anim"):
            item.setCacheMode(QGraphicsItem.DeviceCoordinateCache)
        item.setFlag(QGraphicsItem.ItemIsSelectable, False)
        item.setAcceptHoverEvents(False)
        if isinstance(item, StaticItem) and item.data.get("kind") == "table" and item.data.get("rows_path"):
            item.setAcceptHoverEvents(True)
        data = getattr(item, "data", {})
        if isinstance(data, dict):
            from azeo_control_trainer.core.hmi.pvms.elements import TAB, has_interaction_region
        if isinstance(data, dict) and (
                data.get("kind") in ("display_link", "user_entry", TAB)
                or (data.get("kind") == "table" and data.get("rows_path"))
                or has_interaction_region(data)):
            from PySide6.QtCore import Qt
            item.setCursor(Qt.PointingHandCursor)
        return item

    def reroute_pipes(self) -> None:
        from .pipe_scene import route_scene_pipes
        route_scene_pipes(self.scene())

    def set_show_tag(self, mode: str) -> bool:
        """Apply the operator's workstation-local display-tag preference."""
        from azeo_control_trainer.core.hmi.pvms.hp.tag import SHOW_TAG_MODES
        if mode not in SHOW_TAG_MODES:
            return False
        self.display.show_tag = mode
        self.scene().display = self.display
        from .items import PvmItem
        for item in self.scene().items():
            if isinstance(item, PvmItem):
                item.update()
        self.viewport().update()
        return True

    # ----------------------------------------------------------- live
    def refresh(self) -> None:
        with getattr(self.engine._source, "snapshot", nullcontext)():
            self._refresh()

    def _refresh(self) -> None:
        from .items import PvmItem, StaticItem
        # A retained display may share its engine with detached faceplates.
        # Poll just the subscriptions this scene owns, as its disposal does.
        self.engine.poll(binding for item in self._refresh_items
                         for binding in self.renderer.item_bindings(item))
        self._apply_animations()
        for item in self._refresh_items:
            revision = self.renderer.binding_revision(item)
            changed = revision != getattr(item, "_painted_binding_revision", None)
            item._painted_binding_revision = revision
            if isinstance(item, PvmItem):
                item.sample_history()
                # Trend painters advance with time, not only with
                # change — the same rule the studio's tick follows.
                if changed or item.pvm.variant in ("tank", "vessel"):
                    item.update()
            elif isinstance(item, StaticItem) \
                    and item.data.get("kind") in ("datalink", "user_entry"):
                if item.data.get("kind") == "user_entry":
                    item.refresh_write_permission()
                if changed:
                    item.update()
            elif isinstance(item, StaticItem) and item.sample_compound():
                item.update()
            elif isinstance(item, StaticItem) \
                    and (item.data.get("kind") in ("date_time", "alarm_list")
                         or (item.data.get("kind") in (
                             "table", "faceplate_section") and changed)):
                item.update()

    def _property_resolver(self, revision=""):
        from azeo_control_trainer.core.hmi.pvms.functions import FunctionStore
        from ..standards import StandardsStore
        from azeo_control_trainer.core.hmi.pvms.properties import PropertyResolver
        from azeo_control_trainer.core.hmi.pvms.variables import VariableSet

        if self.config_root is not None and self._functions is None:
            self._functions = FunctionStore(self.config_root)
            try:
                from ..class_revisions import standards_root
                self._standards = StandardsStore(standards_root(self.config_root))
            except Exception:  # noqa: BLE001
                self._standards = None
        if self._functions is not None:
            self._functions.refresh()
        functions, standards = self._functions, self._standards
        if revision:
            from ..class_revisions import revision_root, standards_root
            if not hasattr(self, "_pinned_property_stores"):
                self._pinned_property_stores = {}
            if revision not in self._pinned_property_stores:
                root = revision_root(self.config_root, revision)
                self._pinned_property_stores[revision] = (FunctionStore(root), StandardsStore(standards_root(root)))
            functions, standards = self._pinned_property_stores[revision]
        basic = PropertyResolver(
            standards=standards, functions=functions,
            read=self.engine._source.read,
            blink_standard_ms=getattr(
                self.layout_model, "blink_standard_ms", 500),
            blink_alternate_ms=getattr(
                self.layout_model, "blink_alternate_ms", 125))
        variables = VariableSet.from_list(self.display.variables).resolve(basic)
        variables.update({key.removeprefix("Dsp."): value
                          for key, value in self.script_scopes.items()
                          if key.startswith("Dsp.")})
        if self.layout_model is not None:
            layout_vars = VariableSet.from_list(
                self.layout_model.variables, scope="Layout").resolve(basic)
            layout_vars.update({key.removeprefix("Lyt."): value
                                for key, value in self.script_scopes.items()
                                if key.startswith("Lyt.")})
            variables.update(layout_vars)
        basic.variables = variables
        return basic

    def _apply_animations(self) -> None:
        """Resolve diamond properties with the assigned layout's timing."""
        from .items import StaticItem
        from azeo_control_trainer.core.hmi.pvms.properties import described_properties

        candidates = [(item, described_properties(item.data))
                      for item in self._refresh_items if isinstance(item, StaticItem)]
        candidates = [(item, properties) for item, properties in candidates if properties]
        if not candidates:
            return
        resolver = self._property_resolver()
        resolvers = {"": resolver}
        numeric = {"fill_pct", "rot", "width", "radius", "start", "span",
                   "opacity", "font_size"}
        animatable = {"fill", "line", "fill_pct", "rot", "visible", "text",
                      "width", "radius", "start", "span", "style",
                      "opacity", "text_color", "font_family", "font_size",
                      "font_bold", "font_italic", "font_underline", "icon",
                      "tooltip", "enabled"}
        boolean = {"visible", "font_bold", "font_italic", "font_underline",
                   "enabled"}
        for item, properties in candidates:
            revision = item.data.get("class_revision", "")
            if revision not in resolvers:
                resolvers[revision] = self._property_resolver(revision)
            resolver = resolvers[revision]
            dirty = False
            for prop, spec in properties.items():
                if prop not in animatable:
                    continue
                base_key = f"_{prop}_static"
                if base_key not in item.data:
                    item.data[base_key] = item.data.get(prop)
                answer = resolver.resolve(spec, item.data.get(base_key))
                value = answer.value if answer.usable \
                    else item.data.get(base_key)
                if prop in numeric and value is not None:
                    try:
                        value = float(value)
                    except (TypeError, ValueError):
                        continue
                elif prop in boolean and value is not None:
                    value = value if isinstance(value, bool) else \
                        str(value).strip().lower() in (
                            "1", "true", "yes", "on", "show")
                if item.data.get(prop) != value:
                    item.data[prop] = value
                    dirty = True
                    if prop == "rot" and hasattr(item, "apply_rotation"):
                        item.apply_rotation()
                    elif prop == "tooltip":
                        item.setToolTip(str(value))
            if dirty:
                item.update()

    def _dispatch_display_event(self, name: str) -> None:
        if self._action_handler is None:
            return
        for action in self.display.events.get(name, ()):
            self._action_handler(dict(action), self)

    def drawBackground(self, painter: QPainter, rect) -> None:  # noqa: N802
        # The display's own background, else the theme's. No grid:
        # there is no authoring here to need one.
        painter.fillRect(rect, QColor(
            self.display.background
            or self.palette_roles[Role.SURFACE_BG]))

    def closeEvent(self, event) -> None:            # noqa: N802
        if self._disposed:
            super().closeEvent(event)
            return
        self.set_active(False)
        self._disposed = True
        self._timer.stop()
        self._release_chart_context_menu()
        from .items import PvmItem, StaticItem
        for item in self.scene().items():
            if isinstance(item, (PvmItem, StaticItem)):
                self.renderer.unbind(item)
        from ..symbols import release_document_symbols
        release_document_symbols(self._symbol_aliases)
        self._symbol_aliases = {}
        super().closeEvent(event)


def open_published(root, name: str, graphs_provider,
                   workstation: str = "", theme: str = STUDIO_THEME,
                   live: bool = True, parent=None, open_display=None):
    """The operator's entry point: the revision published to this
    workstation, or None when `name` has never been published.

    `workstation` is the store's own targeting: a station takes the
    revision aimed at it, else the widest current one.
    """
    store = DisplayStore(root)
    document = store.published_document(name, workstation=workstation)
    if document is None:
        return None
    return PvmDisplayView(document, graphs_provider, theme=theme,
                          config_root=root, live=live,
                          open_display=open_display, parent=parent)
