"""The canvas view: drops, tools, and the ghost that resolves early.

Binding resolves on drag-ENTER, not release — the ghost names the
real tag before the mouse is let go, so a mis-drag is obvious
without undo.
"""
from __future__ import annotations

from functools import wraps
import json
import logging
import weakref
from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsScene, QGraphicsView

from azeo_control_trainer.core.hmi.pvms.rendering.chrome import (
    MIME_BLOCK, MIME_PALETTE, MIME_PARAMETER, MODE_EDIT, WF,
)
from azeo_control_trainer.core.hmi.pvms.rendering.items import PvmItem
from azeo_control_trainer.core.hmi.pvms.publishing import DisplayLocked
from azeo_control_trainer.core.hmi.theme.roles import Role

if TYPE_CHECKING:                       # pragma: no cover
    # The window owns the view, so importing it for real would close
    # the loop; the annotation is a string either way.
    from .assembler import PvmStudio


log = logging.getLogger("azeo.graphics_designer.canvas")


def _guard_canvas_event(callback):
    """Keep one failed Qt gesture from escaping into Explorer's event loop."""
    @wraps(callback)
    def guarded(self, event):
        try:
            return callback(self, event)
        except Exception as error:  # noqa: BLE001 - Qt callback boundary
            self._recover_event_failure(callback.__name__, error, event)
            return None
    return guarded


class _Canvas(QGraphicsView):
    def __init__(self, studio: "PvmStudio"):
        super().__init__()
        self.setObjectName("graphics_canvas")
        # The view frame is application chrome, not authored geometry. A
        # second native border around the finite page made its safe-area
        # guide read like selected content.
        self.setFrameShape(QGraphicsView.NoFrame)
        self._pasteboard_brush = QBrush(QColor("#EEF1F5"))
        # The Studio owns this view and the view owns its scene. A strong
        # Python back-link here creates studio -> canvas -> scene -> studio,
        # which can outlive Qt's native owner during modeless teardown.
        self.studio = weakref.proxy(studio)
        scene = QGraphicsScene(self)
        scene.studio = weakref.proxy(studio)
        # Rendering state belongs to the document, not to a view back-link.
        scene.display = studio.display
        self.setScene(scene)
        self.setAcceptDrops(True)
        # Cursor coordinates are an always-on CAD affordance, not a drawing
        # tool mode. QGraphicsView otherwise reports moves only while a
        # button is held, leaving the engineering readout stale.
        self.setMouseTracking(True)
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self._ghost: QGraphicsRectItem | None = None
        self._smart_guides: list[tuple[str, float]] = []
        self._ruler_guide_preview: tuple[str, float] | None = None
        self._middle_panning = False
        self._space_pan = False
        self._pan_button = Qt.MiddleButton
        self._suppress_context_menu = False
        self._middle_pan_position = QPoint()
        from .pointer_drag import PointerDrag
        self.pointer_drag = PointerDrag(self)
        self._auto_fit_timer = QTimer(self)
        self._auto_fit_timer.setSingleShot(True)
        self._auto_fit_timer.timeout.connect(self._auto_fit_after_layout)

    def _recover_event_failure(self, operation: str, error: Exception,
                               event) -> None:
        """Log, cancel transient state, and leave the editor interactive."""
        log.exception("Canvas event %s failed", operation)
        self._middle_panning = False
        try:
            self._drop_ghost()
            self.clear_smart_guides()
            self.studio.cancel_gestures()
        except Exception:  # noqa: BLE001 - recovery must not mask first failure
            log.exception("Canvas recovery after %s also failed", operation)
        message = f"Canvas command failed: {error}"
        try:
            self.studio.uiError.emit(message)
        except (AttributeError, RuntimeError, ReferenceError):
            # The owning tab may already be closing; the exception is still
            # preserved in the application and error journals above.
            pass
        try:
            event.accept()
        except (AttributeError, RuntimeError):
            pass

    def page_rect(self) -> QRectF:
        """The authored page, or an empty rectangle for an Auto canvas."""
        display = self.studio.display
        if display.width > 0 and display.height > 0:
            return QRectF(0.0, 0.0, float(display.width),
                          float(display.height))
        return QRectF()

    def page_boundary_visible(self) -> bool:
        """Whether authoring should paint the non-published page guide."""
        return self.studio.mode == MODE_EDIT and not self.page_rect().isEmpty()

    @_guard_canvas_event
    def wheelEvent(self, event) -> None:            # noqa: N802
        """Ctrl+wheel zooms about the pointer; an unmodified wheel scrolls.

        This is the manual's canvas shortcut. Clamping protects both ends:
        a near-zero transform makes a display appear empty, while an
        unbounded transform can allocate enormous paint regions.
        """
        # CAD convention: the wheel is a camera control. Ctrl+wheel remains
        # accepted for Visio/browser muscle memory; Shift+wheel pans across
        # wide process drawings instead of changing their scale.
        if event.modifiers() & Qt.ShiftModifier \
                and not event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y() or event.pixelDelta().y()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta)
            self.studio.disable_auto_fit()
            event.accept()
            return
        current = self.transform().m11()
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if not delta:
            event.accept()
            return
        factor = 1.15 ** (delta / 120.0)
        target = max(0.10, min(8.0, current * factor))
        if abs(target - current) > 1e-9:
            self.scale(target / current, target / current)
            self.studio.zoom_percent = int(round(target * 100))
            self.studio.disable_auto_fit()
            self.update_rulers()
        event.accept()

    def resizeEvent(self, event) -> None:           # noqa: N802
        super().resizeEvent(event)
        # Fitting during construction uses a pre-layout viewport (often only
        # a few pixels wide). Refit after every splitter/layout change so
        # folding a pane immediately gives that space to the authored page.
        self.schedule_auto_fit()
        self.update_rulers()

    def schedule_auto_fit(self) -> None:
        if getattr(self.studio, "auto_fit_enabled", False):
            self._auto_fit_timer.start(0)

    def _auto_fit_after_layout(self) -> None:
        try:
            self.studio.refit_to_viewport()
        except RuntimeError:
            # A queued final resize can arrive while Qt tears down a tab.
            pass

    def update_rulers(self) -> None:
        frame = getattr(self.studio, "canvas_frame", None)
        if frame is not None:
            frame.update_rulers()

    def drawBackground(self, painter: QPainter, rect) -> None:  # noqa: N802
        # The display's own background, else the THEME's — not a
        # wireframe grey. What is behind the objects is part of what
        # the operator sees, so it has to retheme with them.
        background = getattr(self.studio.display, "background", "")
        display_colour = QColor(
            background or self.studio.palette_roles[Role.SURFACE_BG])
        page_rect = self.page_rect()
        framed_authoring_page = self.studio.mode == MODE_EDIT \
            and not page_rect.isEmpty()
        if framed_authoring_page:
            # This pasteboard exists only in the authoring view. The authored
            # page is repainted with the exact operator background below, so
            # Studio polish cannot change a published HMI pixel.
            painter.fillRect(rect, self._pasteboard_brush)
            shadow_colour = QColor("#526777")
            shadow_colour.setAlpha(46)
            painter.fillRect(page_rect.translated(6.0, 7.0), shadow_colour)
            painter.fillRect(page_rect, display_colour)
        else:
            painter.fillRect(rect, display_colour)
        # Reusable classes are transparent compositions rather than operator
        # pages.  The checkerboard is editor chrome inside the authoritative
        # class bounds; it makes an accidental opaque background immediately
        # visible and gives a small PVM/faceplate a clear working surface.
        if self.studio.mode == MODE_EDIT \
                and self.studio.edited_user_class_name():
            page = page_rect.intersected(rect)
            tile = 12
            painter.save()
            painter.setClipRect(page)
            top = int(page.top()) - int(page.top()) % tile
            left = int(page.left()) - int(page.left()) % tile
            y = top
            row = top // tile
            while y < page.bottom():
                x = left
                column = left // tile
                while x < page.right():
                    color = "#FFFFFF" if (row + column) % 2 == 0 \
                        else "#E7E9EC"
                    painter.fillRect(QRectF(x, y, tile, tile),
                                     QColor(color))
                    x += tile
                    column += 1
                y += tile
                row += 1
            painter.restore()
        # The grid is authoring chrome. VIEW and TEST are the operator's
        # view of the display, and TEST says so in as many words, so
        # neither may draw it — a grid is the one thing on the canvas
        # that certainly is not on the operator's screen.
        if self.studio.mode != MODE_EDIT \
                or not getattr(self.studio, "grid_visible", True):
            return
        colour = QColor("#8B99A7")
        colour.setAlpha(90)
        pen = QPen(colour, 1.0)
        pen.setCosmetic(True)
        painter.setPen(pen)
        step = 16
        # Keep dots apart at reduced zoom. A dense grid competes with pipes
        # and equipment; visual spacing does not change snapping precision.
        while step * abs(self.transform().m11()) < 12:
            step *= 2
        grid_rect = page_rect.intersected(rect) \
            if framed_authoring_page else rect
        left = int(grid_rect.left()) - int(grid_rect.left()) % step
        top = int(grid_rect.top()) - int(grid_rect.top()) % step
        points = []
        y = top
        while y < grid_rect.bottom():
            x = left
            while x < grid_rect.right():
                points.append(QPointF(x, y))
                x += step
            y += step
        painter.drawPoints(points)

    def drawForeground(self, painter: QPainter, rect) -> None:  # noqa: N802
        """Mark the publishable page without adding an object to it.

        A scene item would be selectable, serializable by an incautious scene
        walk, and visible to the shared operator renderer. View foreground is
        authoring chrome, so the guide cannot leak into a saved display.
        """
        super().drawForeground(painter, rect)
        if self.page_boundary_visible():
            page = self.page_rect().adjusted(0.5, 0.5, -0.5, -0.5)
            pen = QPen(QColor(WF["lapis"]), 1.0, Qt.DotLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(page)
            margin = max(0, int(getattr(
                self.studio.display, "safe_margin", 24)))
            if margin and page.width() > margin * 2 \
                    and page.height() > margin * 2:
                safe = page.adjusted(margin, margin, -margin, -margin)
                safe_pen = QPen(QColor(WF["lapis_lt"]), 1.0,
                                Qt.DashLine)
                safe_pen.setCosmetic(True)
                painter.setPen(safe_pen)
                painter.drawRect(safe)
        authoring_guides = list(getattr(
            self.studio, "authoring_guides", ()))
        if authoring_guides and self.studio.mode == MODE_EDIT:
            guide_pen = QPen(QColor(WF["sel_br"]), 1.0, Qt.DotLine)
            guide_pen.setCosmetic(True)
            painter.setPen(guide_pen)
            for orientation, coordinate in authoring_guides:
                if orientation == "x":
                    painter.drawLine(coordinate, rect.top(),
                                     coordinate, rect.bottom())
                else:
                    painter.drawLine(rect.left(), coordinate,
                                     rect.right(), coordinate)
        if self._smart_guides and self.studio.mode == MODE_EDIT:
            guide_pen = QPen(QColor(WF["lapis_lt"]), 1.0, Qt.DashLine)
            guide_pen.setCosmetic(True)
            painter.setPen(guide_pen)
            for orientation, coordinate in self._smart_guides:
                if orientation == "x":
                    painter.drawLine(coordinate, rect.top(),
                                     coordinate, rect.bottom())
                else:
                    painter.drawLine(rect.left(), coordinate,
                                     rect.right(), coordinate)
        if self._ruler_guide_preview is not None \
                and self.studio.mode == MODE_EDIT:
            orientation, coordinate = self._ruler_guide_preview
            preview_pen = QPen(QColor(WF["lapis_lt"]), 1.5, Qt.DashLine)
            preview_pen.setCosmetic(True)
            painter.setPen(preview_pen)
            if orientation == "x":
                painter.drawLine(coordinate, rect.top(),
                                 coordinate, rect.bottom())
            else:
                painter.drawLine(rect.left(), coordinate,
                                 rect.right(), coordinate)

    def snap_item_position(self, item, value) -> QPointF:
        """Grid plus alignment-guide snapping for a single-object drag.

        Multi-selection deliberately retains relative spacing. Alt bypasses
        all snapping for precision placement, matching resize behavior.
        """
        from PySide6.QtWidgets import QApplication

        point = QPointF(value)
        if getattr(self.studio, "_moving_selection", False):
            return point
        if self.studio.mode != MODE_EDIT or not item.isSelected() \
                or not getattr(self.studio, "_gesture_open", False) \
                or QApplication.keyboardModifiers() & Qt.AltModifier:
            return point
        if getattr(self.studio, "snap_enabled", False):
            grid = 8.0
            point = QPointF(round(point.x() / grid) * grid,
                            round(point.y() / grid) * grid)
        self._smart_guides = []
        if not getattr(self.studio, "smart_guides_enabled", True) \
                or len(self.scene().selectedItems()) != 1:
            self.viewport().update()
            self.update_rulers()
            return point

        rect = item.rect()
        x_targets: list[float] = []
        y_targets: list[float] = []
        page = self.page_rect()
        if not page.isEmpty():
            margin = max(0.0, float(getattr(
                self.studio.display, "safe_margin", 24)))
            safe = page.adjusted(margin, margin, -margin, -margin)
            x_targets.extend((safe.left(), safe.center().x(), safe.right()))
            y_targets.extend((safe.top(), safe.center().y(), safe.bottom()))
        for orientation, coordinate in getattr(
                self.studio, "authoring_guides", ()):
            if orientation == "x":
                x_targets.append(float(coordinate))
            elif orientation == "y":
                y_targets.append(float(coordinate))
        for other in self.scene().items():
            if other is item or other is self._ghost or not other.isVisible():
                continue
            document_data = getattr(other, "data", None)
            if (isinstance(document_data, dict)
                    and document_data.get("kind") == "pipe") \
                    or not hasattr(other, "rect"):
                continue
            # Align visible geometry, not boundingRect's authoring handles.
            # The latter extends past the object for resize knobs and would
            # make a 40 px rectangle advertise a 58 px alignment edge.
            bounds = other.mapRectToScene(other.rect())
            x_targets.extend((bounds.left(), bounds.center().x(),
                              bounds.right()))
            y_targets.extend((bounds.top(), bounds.center().y(),
                              bounds.bottom()))

        tolerance = 6.0 / max(abs(self.transform().m11()), 0.1)

        def nearest(origin: float, offsets: tuple, targets: list):
            matches = ((abs(origin + offset - target), target - offset,
                        target)
                       for offset in offsets for target in targets)
            best = min(matches, default=None)
            return best if best is not None and best[0] <= tolerance else None

        x_match = nearest(point.x(), (0.0, rect.width() / 2,
                                      rect.width()), x_targets)
        y_match = nearest(point.y(), (0.0, rect.height() / 2,
                                      rect.height()), y_targets)
        if x_match:
            point.setX(x_match[1])
            self._smart_guides.append(("x", x_match[2]))
        if y_match:
            point.setY(y_match[1])
            self._smart_guides.append(("y", y_match[2]))
        self.viewport().update()
        self.update_rulers()
        return point

    def clear_smart_guides(self) -> None:
        if self._smart_guides:
            self._smart_guides.clear()
            self.viewport().update()
            self.update_rulers()

    def snap_route_point(self, value, *, source=None,
                         reference_points=()) -> QPointF:
        """Snap a loose pipe end or manual bend and paint CAD-style guides.

        Object movement already had smart guides, but pipe editing happens
        inside :class:`PipeItem` and therefore never passed through
        :meth:`snap_item_position`.  Keep route guidance view-only: the
        resulting coordinate may be persisted by the caller, while the guide
        lines themselves disappear at the end of the gesture.
        """
        from PySide6.QtWidgets import QApplication

        point = QPointF(value)
        pointer = QPointF(value)
        if self.studio.mode != MODE_EDIT \
                or QApplication.keyboardModifiers() & Qt.AltModifier:
            self.clear_smart_guides()
            return point
        if getattr(self.studio, "snap_enabled", False):
            grid = 8.0
            point = QPointF(round(point.x() / grid) * grid,
                            round(point.y() / grid) * grid)
        self._smart_guides = []
        if not getattr(self.studio, "smart_guides_enabled", True):
            self.viewport().update()
            self.update_rulers()
            return point

        x_targets: list[float] = []
        y_targets: list[float] = []
        preferred_x: list[float] = []
        preferred_y: list[float] = []

        def add_target(target, *, preferred: bool = False) -> None:
            try:
                x, y = float(target.x()), float(target.y())
            except (AttributeError, TypeError, ValueError):
                return
            x_targets.append(x)
            y_targets.append(y)
            if preferred:
                preferred_x.append(x)
                preferred_y.append(y)

        for target in reference_points:
            # The two ends and neighboring bends define the run being edited;
            # prefer those axes over a nearby unused nozzle on another item.
            add_target(target, preferred=True)

        page = self.page_rect()
        if not page.isEmpty():
            margin = max(0.0, float(getattr(
                self.studio.display, "safe_margin", 24)))
            safe = page.adjusted(margin, margin, -margin, -margin)
            x_targets.extend((safe.left(), safe.center().x(), safe.right()))
            y_targets.extend((safe.top(), safe.center().y(), safe.bottom()))
        for orientation, coordinate in getattr(
                self.studio, "authoring_guides", ()):
            if orientation == "x":
                x_targets.append(float(coordinate))
            elif orientation == "y":
                y_targets.append(float(coordinate))

        for other in self.scene().items():
            if other is source or other is self._ghost or not other.isVisible():
                continue
            document_data = getattr(other, "data", None)
            if isinstance(document_data, dict) \
                    and document_data.get("kind") == "pipe":
                # Existing route vertices are first-class drafting geometry:
                # a new run should line up with an established header or bend.
                for vertex in getattr(other, "_points", ()):
                    add_target(vertex)
                continue
            if not hasattr(other, "rect"):
                continue
            bounds = other.mapRectToScene(other.rect())
            x_targets.extend((bounds.left(), bounds.center().x(),
                              bounds.right()))
            y_targets.extend((bounds.top(), bounds.center().y(),
                              bounds.bottom()))
            # Prefer real nozzles/connection points over the host rectangle.
            # SVG equipment often has its outlet away from the visual centre.
            anchors = getattr(other, "anchor_sides", None)
            resolve = getattr(other, "anchor", None)
            if callable(anchors) and callable(resolve):
                for side in anchors():
                    try:
                        add_target(resolve(side))
                    except (KeyError, TypeError, ValueError):
                        continue

        tolerance = 6.0 / max(abs(self.transform().m11()), 0.1)

        def nearest(origin: float, targets: list[float]):
            best = min(((abs(origin - target), target)
                        for target in targets), default=None)
            return best if best is not None and best[0] <= tolerance else None

        # Smart geometry wins over the coarse grid. Measuring from the
        # pointer prevents an 8-unit grid step from moving a point just beyond
        # the six-pixel acquisition band of an otherwise obvious alignment.
        x_match = nearest(pointer.x(), preferred_x) \
            or nearest(pointer.x(), x_targets)
        y_match = nearest(pointer.y(), preferred_y) \
            or nearest(pointer.y(), y_targets)
        if x_match:
            point.setX(x_match[1])
            self._smart_guides.append(("x", x_match[1]))
        if y_match:
            point.setY(y_match[1])
            self._smart_guides.append(("y", y_match[1]))
        self.viewport().update()
        self.update_rulers()
        return point

    def preview_ruler_guide(self, axis: str,
                            coordinate: float | None) -> None:
        """Paint a non-persistent guide while a ruler owns the pointer."""
        self._ruler_guide_preview = None if coordinate is None \
            else (axis, float(coordinate))
        self.viewport().update()
        self.update_rulers()

    @_guard_canvas_event
    def contextMenuEvent(self, event) -> None:      # noqa: N802
        if self._suppress_context_menu:
            self._suppress_context_menu = False
            event.accept()
            return
        if self.itemAt(event.pos()) is None:
            self.studio.canvas_context_menu(
                event.globalPos(), self.mapToScene(event.pos()))
        else:
            super().contextMenuEvent(event)

    # Binding resolves on drag-ENTER: the ghost shows the real tag.
    @_guard_canvas_event
    def dragEnterEvent(self, event) -> None:        # noqa: N802
        if self.studio.mode != MODE_EDIT:
            # §8.6: placement is an EDIT-mode gesture. Auto-enter if
            # the lock is free; refuse the drag otherwise.
            try:
                self.studio.enter_edit()
            except DisplayLocked:
                return
        if any(event.mimeData().hasFormat(kind)
               for kind in (MIME_PALETTE, MIME_BLOCK, MIME_PARAMETER)):
            # A new stencil drag replaces click-to-place; leaving both ghosts
            # armed made the next canvas click place the previous stencil.
            self.studio.cancel_gestures()
            self._drop_ghost()
        if event.mimeData().hasFormat(MIME_PALETTE):
            payload = json.loads(
                bytes(event.mimeData().data(MIME_PALETTE)).decode())
            self._ghost = self.studio.make_palette_ghost(payload)
            if self._ghost is not None:
                self.scene().addItem(self._ghost)
            event.acceptProposedAction()
        elif event.mimeData().hasFormat(MIME_BLOCK):
            payload = json.loads(
                bytes(event.mimeData().data(MIME_BLOCK)).decode())
            self._ghost = self.studio.make_ghost(payload)
            if self._ghost is not None:
                self.scene().addItem(self._ghost)
            event.acceptProposedAction()
        elif event.mimeData().hasFormat(MIME_PARAMETER):
            # A parameter has no PVM-class chooser: its one honest visual
            # representation is a bound Data Link preview.
            self._ghost = QGraphicsRectItem(0, 0, 128, 28)
            self._ghost.setPen(QPen(QColor(WF["lapis"]), 1, Qt.DashLine))
            self._ghost.setBrush(QColor(WF["pane"]))
            self.scene().addItem(self._ghost)
            event.acceptProposedAction()

    @_guard_canvas_event
    def dragMoveEvent(self, event) -> None:         # noqa: N802
        if self._ghost is not None:
            self._ghost.setPos(self.mapToScene(event.position()
                                               .toPoint()))
        event.acceptProposedAction()

    @_guard_canvas_event
    def dragLeaveEvent(self, event) -> None:        # noqa: N802
        self._drop_ghost()

    @_guard_canvas_event
    def mousePressEvent(self, event) -> None:       # noqa: N802
        self._suppress_context_menu = False
        if event.button() == Qt.MiddleButton \
                or self._space_pan and event.button() == Qt.LeftButton:
            self._middle_panning = True
            self._pan_button = event.button()
            self._pan_cursor = self.cursor()
            self._middle_pan_position = event.position().toPoint()
            self.setCursor(Qt.ClosedHandCursor)
            self.studio.disable_auto_fit()
            event.accept()
            return
        if event.button() != Qt.LeftButton:
            if event.button() == Qt.RightButton \
                    and self.studio.interaction_mode == "draw":
                self.studio.cancel_gestures()
                self._suppress_context_menu = True
                event.accept()
                return
            super().mousePressEvent(event)
            return
        scene_pos = self.mapToScene(event.position().toPoint())
        self.studio.update_geometry_readout(scene_pos)
        item = self._pointer_item_at(event.position().toPoint())
        if self.studio._style_brush_click(item):
            event.accept()
            return
        if self.studio._eraser_at(scene_pos):
            event.accept()
            return
        if self.studio._pencil_press(scene_pos):
            event.accept()
            return
        if self.studio._line_press(scene_pos):
            event.accept()
            return
        if self.studio._shape_press(scene_pos):
            event.accept()
            return
        if self.studio._polyline_press(scene_pos):
            event.accept()
            return
        if self.studio._place_click(scene_pos):
            event.accept()
            return
        if self.studio._connect_click(item, scene_pos):
            event.accept()
            return
        if self.studio.interaction_mode == "select" and event.modifiers() & (
                Qt.ControlModifier | Qt.AltModifier) == (Qt.ControlModifier | Qt.AltModifier):
            self.cycle_overlap(event.position().toPoint())
            event.accept()
            return
        if self.pointer_drag.press(item, scene_pos, event.modifiers()):
            self.setFocus(Qt.MouseFocusReason)
            event.accept()
            return
        super().mousePressEvent(event)

    def _pointer_item_at(self, position):
        # The selection frame is above the equipment but accepts no clicks.
        # itemAt() still returns it, hiding the assembly beneath from tools.
        return next((item for item in self.items(position)
                     if item.acceptedMouseButtons() & Qt.LeftButton), None)

    def cycle_overlap(self, position):
        """Ctrl+Alt-click reaches covered objects without disturbing their order."""
        from .selection import is_locked
        document_items = set(self.studio._document_items())
        candidates = [item for item in self.items(position)
                      if item in document_items and item.isVisible() and not is_locked(item)
                      and self.studio._endpoint_id(item)]
        if not candidates:
            return None
        primary = self.studio.selection.snapshot().primary
        index = (candidates.index(primary) + 1) % len(candidates) if primary in candidates else 0
        self.studio.selection.replace((candidates[index],), primary=candidates[index])
        return candidates[index]

    @_guard_canvas_event
    def mouseMoveEvent(self, event) -> None:        # noqa: N802
        if self._middle_panning and event.buttons() & self._pan_button:
            position = event.position().toPoint()
            delta = position - self._middle_pan_position
            self._middle_pan_position = position
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - delta.y())
            self.update_rulers()
            event.accept()
            return
        scene_pos = self.mapToScene(event.position().toPoint())
        self.studio.update_geometry_readout(scene_pos)
        shift = bool(event.modifiers() & Qt.ShiftModifier)
        if event.buttons() & Qt.LeftButton \
                and self.studio._eraser_at(scene_pos):
            event.accept()
            return
        if self.studio._pencil_drag(scene_pos):
            event.accept()
            return
        if self.studio._line_drag(scene_pos, shift) is not None:
            event.accept()
            return
        if self.studio._shape_drag(scene_pos, event.modifiers()) is not None:
            event.accept()
            return
        if self.studio._polyline_move(scene_pos):
            event.accept()
            return
        if self.studio._place_move(scene_pos):
            event.accept()
            return
        if self.studio._connect_move(scene_pos):
            event.accept()
            return
        if self.pointer_drag.move(scene_pos, event.modifiers()):
            self.studio.update_geometry_readout(scene_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)
        # QGraphicsItem movement happens inside the base implementation;
        # refresh once more so W/H/X/Y describe the new selection geometry.
        self.studio.update_geometry_readout(scene_pos)

    @_guard_canvas_event
    def mouseReleaseEvent(self, event) -> None:     # noqa: N802
        if event.button() == self._pan_button and self._middle_panning:
            self._middle_panning = False
            cursor = self._space_cursor if self._pan_button == Qt.LeftButton \
                else self._pan_cursor
            self.setCursor(Qt.OpenHandCursor if self._space_pan else cursor)
            event.accept()
            return
        if event.button() != Qt.LeftButton:
            super().mouseReleaseEvent(event)
            return
        if self.pointer_drag.active:
            if self.pointer_drag.started:
                self.pointer_drag.move(self.mapToScene(event.position().toPoint()),
                                       event.modifiers())
            self.pointer_drag.release()
            event.accept()
            return
        scene_pos = self.mapToScene(event.position().toPoint())
        self.studio.update_geometry_readout(scene_pos)
        if getattr(self.studio, "_eraser_armed", False):
            # The eraser stays armed for the next stroke, but each
            # drag is its own undo step.
            self.studio._eraser_checkpointed = False
            event.accept()
            self.studio.end_gesture()
            return
        if getattr(self.studio, "_pencil_armed", False):
            self.studio._pencil_release(scene_pos)
            event.accept()
            self.studio.end_gesture()
            return
        if getattr(self.studio, "_line_armed", False) \
                and self.studio._line_start is not None:
            shift = bool(event.modifiers() & Qt.ShiftModifier)
            self.studio._line_release(scene_pos, shift)
            event.accept()
            self.studio.end_gesture()
            return
        if getattr(self.studio, "_shape_armed", False) \
                and self.studio._shape_start is not None:
            self.studio._shape_release(scene_pos, event.modifiers())
            event.accept()
            self.studio.end_gesture()
            return
        if getattr(self.studio, "_polyline_armed", False):
            event.accept()
            self.studio.end_gesture()
            return
        item = self.itemAt(event.position().toPoint())
        if self.studio._connect_release(item, scene_pos):
            event.accept()
            self.studio.end_gesture()
            return
        super().mouseReleaseEvent(event)
        self.studio.end_gesture()
        self.clear_smart_guides()

    @_guard_canvas_event
    def focusOutEvent(self, event) -> None:         # noqa: N802
        self.pointer_drag.reset()
        if self._space_pan:
            self.setCursor(self._space_cursor)
        elif self._middle_panning:
            self.setCursor(self._pan_cursor)
        self._space_pan = False
        self._middle_panning = False
        self.studio.cancel_gesture()
        super().focusOutEvent(event)

    @_guard_canvas_event
    def dropEvent(self, event) -> None:             # noqa: N802
        position = self.mapToScene(event.position().toPoint())
        if event.mimeData().hasFormat(MIME_PALETTE):
            payload = json.loads(
                bytes(event.mimeData().data(MIME_PALETTE)).decode())
            self._drop_ghost()
            self.studio.drop_palette_item(payload, position)
        elif event.mimeData().hasFormat(MIME_BLOCK):
            payload = json.loads(
                bytes(event.mimeData().data(MIME_BLOCK)).decode())
            self._drop_ghost()
            self.studio.place_block(payload["path"], payload["block_type"],
                                    position.x(), position.y())
        elif event.mimeData().hasFormat(MIME_PARAMETER):
            payload = json.loads(
                bytes(event.mimeData().data(MIME_PARAMETER)).decode())
            self._drop_ghost()
            data_type = str(payload.get("data_type", "")).upper()
            link_type = "boolean" if data_type in ("BOOL", "BOOLEAN") \
                else "string" if data_type == "STRING" else "numeric"
            item = self.studio.add_static(
                "datalink", x=position.x(), y=position.y(), w=128, h=28)
            item.data.update({
                "path": str(payload.get("path", "")),
                "datalink_type": link_type,
            })
            self.studio.rebind_static(item)
            self.studio.pane.show_item(item)
        else:
            return
        event.acceptProposedAction()

    def _drop_ghost(self) -> None:
        if self._ghost is not None:
            self.scene().removeItem(self._ghost)
            self._ghost = None

    @_guard_canvas_event
    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MiddleButton:
            # AutoCAD's wheel double-click is the fastest route back from a
            # deep inspection to the whole authored display.
            self.studio.fit_drawing()
            event.accept()
            return
        if getattr(self.studio, "_polyline_armed", False):
            self.studio.finish_polyline()
            event.accept()
            return
        # PointerDrag owns the selection press, so Qt has no item mouse
        # grabber to receive the second click in EDIT. Resolve the PVM here
        # in every mode, excluding our selection overlay, and dispatch once.
        item = self._pointer_item_at(event.position().toPoint())
        if event.button() == Qt.LeftButton and isinstance(item, PvmItem):
            self.pointer_drag.reset()
            self.studio.open_faceplate(item.pvm)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    @_guard_canvas_event
    def keyPressEvent(self, event) -> None:  # noqa: N802
        """Finish a vertex chain without stealing Enter from property fields."""
        if event.key() == Qt.Key_Escape:
            self.studio.cancel_gestures()
            event.accept()
            return
        if event.key() == Qt.Key_Space:
            if not event.isAutoRepeat() and not self._space_pan:
                self._space_cursor = self.cursor()
                self._space_pan = True
                self.setCursor(Qt.OpenHandCursor)
            event.accept()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) \
                and getattr(self.studio, "_polyline_armed", False):
            self.studio.finish_polyline()
            event.accept()
            return
        super().keyPressEvent(event)

    @_guard_canvas_event
    def keyReleaseEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Space:
            if not event.isAutoRepeat():
                self._space_pan = False
                self.setCursor(getattr(self, "_space_cursor", Qt.ArrowCursor))
            event.accept()
            return
        super().keyReleaseEvent(event)
