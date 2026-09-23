"""Strategy designer view — handles zoom, pan, drag-drop, and wiring.

Also paints two canvas overlays (drawn in viewport space, so they never move
or scale with the diagram):

* a **legend** — solid = signal flow, dashed amber = feedback / next scan,
  plus the block status-dot colours — toggleable with Ctrl+L;
* a **connection card** — when exactly one wire is selected, its direction
  (``SRC.OUT → DST.IN``), routing style and numeric route handles.
"""
from __future__ import annotations

from functools import wraps
import logging

from PySide6.QtCore import Qt, QPointF, QRectF, QTimer, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QPainter, QPen,
    QWheelEvent, QMouseEvent, QKeyEvent,
    QDragEnterEvent, QDragMoveEvent, QDropEvent,
)
from PySide6.QtWidgets import QGraphicsView

from azeo_control_trainer.core.presentation.headless import is_headless

from .strategy_scene import StrategyScene
from ..items.terminal_item import TerminalItem
from ..items.block_item import BlockItem
from ..items.wire_item import WireItem
from ..panels.block_palette import (
    MIME_TYPE as BLOCK_MIME_TYPE,
    TEMPLATE_MIME_TYPE,
)


# ── Legend content ────────────────────────────────────────────────────────
# Line samples must match the wire conventions in items/wire_item.py.
_LEGEND_LINES = [
    ("#2868A8", False, "Signal flow (analog)"),
    ("#C89820", False, "Signal flow (discrete)"),
    ("#f39c12", True,  "Feedback / next scan (BKCAL)"),
]
# Dot samples must match _STATUS_COLORS in items/block_item.py.
_LEGEND_DOTS = [
    ("#2D8E3C", "Good"),
    ("#E8C822", "Uncertain"),
    ("#E8272C", "Bad"),
    ("#9AA5B4", "Out of service"),
]

_OVERLAY_BG = QColor(255, 255, 255, 232)
_OVERLAY_BORDER = QColor("#B6BFCC")
_OVERLAY_TITLE = QColor("#48586E")
_OVERLAY_TEXT = QColor("#2E3946")
_OVERLAY_MARGIN = 12
_OVERLAY_PAD = 8
_MAX_AUTO_FIT_ZOOM = 1.4


def _guard_view_event(method):
    @wraps(method)
    def guarded(self, event, *args, **kwargs):
        try:
            return method(self, event, *args, **kwargs)
        except Exception as error:  # Qt must not receive a Python event exception.
            logging.getLogger(__name__).exception("Control Designer interaction failed")
            self._pan = False
            self.unsetCursor()
            try:
                self._scene.finish_wiring(None)
            except Exception:
                logging.getLogger(__name__).exception("Could not cancel wiring")
            self.uiError.emit(str(error))
            window = self.window()
            if hasattr(window, "statusBar"):
                window.statusBar().showMessage(f"Interaction failed: {error}", 8000)
            event.accept()
            return None
    return guarded


class StrategyView(QGraphicsView):
    """QGraphicsView for the strategy designer with zoom/pan, drag-drop, and wiring."""

    zoomChanged = Signal(float)  # zoom factor
    searchActivated = Signal()   # Ctrl+F pressed
    legendToggled = Signal(bool)  # legend visibility changed
    templateDropped = Signal(str, QPointF)
    uiError = Signal(str)

    # Internal clipboard (shared across all views in the same process)
    _clipboard: dict | None = None

    def _find_terminal_at(self, scene_pos: QPointF):
        """Find a TerminalItem at scene_pos, walking through all overlapping items."""
        items = self._scene.items(scene_pos, Qt.IntersectsItemShape,
                                  Qt.DescendingOrder, self.transform())
        for item in items:
            if isinstance(item, TerminalItem):
                return item
        return None

    def __init__(self, scene: StrategyScene, parent=None):
        super().__init__(scene, parent)
        self._scene = scene
        self._zoom = 1.0
        self._pan = False
        self._pan_start = QPointF()
        # Loading often happens before a tab receives its final viewport size.
        # fitInView() at that moment records the wrong scale/scroll position,
        # which is why some modules opened pressed against one side.  Defer one
        # initial frame until the view is both visible and laid out.
        self._initial_frame_pending = False
        self._initial_frame_scheduled = False

        # Enable drag-drop
        self.setAcceptDrops(True)

        # Rendering
        self.setRenderHints(
            QPainter.Antialiasing | QPainter.SmoothPixmapTransform |
            QPainter.TextAntialiasing
        )
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)

        # Style — silver theme
        self.setStyleSheet("""
            QGraphicsView {
                border: 1px solid #bdbdbd;
                background: #eceff1;
            }
        """)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        # Canvas overlays (viewport-space, never scale with the diagram)
        self._legend_visible = True
        self._connection_card = True
        self._quick_insert_dialog = None
        scene.selectionChanged.connect(self._on_selection_changed)

    # ---------------------------------------------------------------- Overlays
    @property
    def legend_visible(self) -> bool:
        return self._legend_visible

    def set_legend_visible(self, visible: bool):
        """Show/hide the canvas legend."""
        visible = bool(visible)
        if visible == self._legend_visible:
            return
        self._legend_visible = visible
        self.viewport().update()
        self.legendToggled.emit(visible)

    def toggle_legend(self) -> bool:
        """Flip the legend and return its new visibility."""
        self.set_legend_visible(not self._legend_visible)
        return self._legend_visible

    def selected_wires(self) -> list[WireItem]:
        """Currently selected connections."""
        return [it for it in self._scene.selectedItems()
                if isinstance(it, WireItem)]

    def _on_selection_changed(self):
        # The connection card lives in the viewport, not the scene, so it has
        # to be repainted explicitly when the selection changes.
        self.viewport().update()

    def drawForeground(self, painter: QPainter, rect):
        super().drawForeground(painter, rect)
        if not (self._legend_visible or self._connection_card):
            return
        painter.save()
        painter.resetTransform()
        painter.setRenderHint(QPainter.Antialiasing, True)
        if self._legend_visible:
            self._draw_legend(painter)
        if self._connection_card:
            self._draw_connection_card(painter)
        painter.restore()

    def _overlay_font(self, bold: bool = False) -> QFont:
        font = QFont("Segoe UI", 8)
        font.setBold(bold)
        return font

    def _draw_panel(self, painter: QPainter, rect: QRectF, title: str):
        painter.setPen(QPen(_OVERLAY_BORDER, 1))
        painter.setBrush(QBrush(_OVERLAY_BG))
        painter.drawRoundedRect(rect, 4, 4)
        painter.setFont(self._overlay_font(bold=True))
        painter.setPen(QPen(_OVERLAY_TITLE, 1))
        painter.drawText(
            QRectF(rect.left() + _OVERLAY_PAD, rect.top() + 4,
                   rect.width() - 2 * _OVERLAY_PAD, 14),
            Qt.AlignLeft | Qt.AlignVCenter, title)

    def _draw_legend(self, painter: QPainter):
        """Bottom-left legend: wire conventions + status-dot colours."""
        painter.setFont(self._overlay_font())
        fm = painter.fontMetrics()
        row_h = max(fm.height() + 2, 15)
        sample_w = 26.0
        gap = 8.0

        labels = [t for _c, _d, t in _LEGEND_LINES] + [t for _c, t in _LEGEND_DOTS]
        text_w = max((fm.horizontalAdvance(t) for t in labels), default=60)
        width = _OVERLAY_PAD * 2 + sample_w + gap + text_w
        rows = len(_LEGEND_LINES) + len(_LEGEND_DOTS)
        height = 20 + rows * row_h + 6 + _OVERLAY_PAD

        vp = self.viewport().rect()
        rect = QRectF(_OVERLAY_MARGIN,
                      vp.height() - height - _OVERLAY_MARGIN,
                      width, height)
        if rect.top() < 0:                       # tiny viewport — skip
            return
        self._draw_panel(painter, rect, "LEGEND")

        y = rect.top() + 20
        x_sample = rect.left() + _OVERLAY_PAD
        x_text = x_sample + sample_w + gap

        painter.setFont(self._overlay_font())
        for color, dashed, text in _LEGEND_LINES:
            pen = QPen(QColor(color), 2, Qt.SolidLine, Qt.SquareCap)
            if dashed:
                pen.setDashPattern([3, 2])
            painter.setPen(pen)
            painter.drawLine(QPointF(x_sample, y + row_h / 2),
                             QPointF(x_sample + sample_w, y + row_h / 2))
            painter.setPen(QPen(_OVERLAY_TEXT, 1))
            painter.drawText(QRectF(x_text, y, text_w, row_h),
                             Qt.AlignLeft | Qt.AlignVCenter, text)
            y += row_h

        y += 3
        for color, text in _LEGEND_DOTS:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(color)))
            painter.drawEllipse(QPointF(x_sample + sample_w / 2, y + row_h / 2),
                                4, 4)
            painter.setPen(QPen(_OVERLAY_TEXT, 1))
            painter.drawText(QRectF(x_text, y, text_w, row_h),
                             Qt.AlignLeft | Qt.AlignVCenter, text)
            y += row_h

    def _draw_connection_card(self, painter: QPainter):
        """Top-right properties card for the selected connection."""
        wires = self.selected_wires()
        if len(wires) != 1:
            return
        props = wires[0].properties()

        lines = [
            props["label"],
            "Feedback / next scan" if props["feedback"] else "Signal flow",
            f"Routing: orthogonal ({props['route_style']})",
        ]
        if props["handles"]:
            lines += [f"handle {i + 1}:  {h['axis']} = {h['value']:.0f}"
                      for i, h in enumerate(props["handles"])]
        else:
            lines.append("no route handles")

        painter.setFont(self._overlay_font())
        fm = painter.fontMetrics()
        row_h = max(fm.height() + 2, 15)
        text_w = max(fm.horizontalAdvance(t) for t in lines)
        width = max(text_w + 2 * _OVERLAY_PAD, 160.0)
        height = 20 + len(lines) * row_h + _OVERLAY_PAD

        vp = self.viewport().rect()
        rect = QRectF(vp.width() - width - _OVERLAY_MARGIN, _OVERLAY_MARGIN,
                      width, height)
        if rect.left() < 0:                      # tiny viewport — skip
            return
        self._draw_panel(painter, rect, "CONNECTION")

        y = rect.top() + 20
        painter.setFont(self._overlay_font(bold=True))
        painter.setPen(QPen(_OVERLAY_TEXT, 1))
        painter.drawText(QRectF(rect.left() + _OVERLAY_PAD, y, text_w, row_h),
                         Qt.AlignLeft | Qt.AlignVCenter, lines[0])
        y += row_h
        painter.setFont(self._overlay_font())
        for text in lines[1:]:
            painter.drawText(QRectF(rect.left() + _OVERLAY_PAD, y, text_w, row_h),
                             Qt.AlignLeft | Qt.AlignVCenter, text)
            y += row_h

    # ---------------------------------------------------------------- Zoom
    @_guard_view_event
    def wheelEvent(self, event: QWheelEvent):
        self._initial_frame_pending = False
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        new_zoom = self._zoom * factor
        if 0.1 <= new_zoom <= 5.0:
            self._zoom = new_zoom
            self.scale(factor, factor)
            self.zoomChanged.emit(self._zoom)

    def zoom_to_fit(self):
        """Frame all logic centrally without making a tiny module enormous."""
        items_rect = self._scene.itemsBoundingRect()
        if items_rect.isNull():
            return
        target = items_rect.adjusted(-50, -50, 50, 50)
        self.resetTransform()
        self.fitInView(target, Qt.KeepAspectRatio)
        fitted = self.transform().m11()
        if fitted > _MAX_AUTO_FIT_ZOOM:
            self.resetTransform()
            self.scale(_MAX_AUTO_FIT_ZOOM, _MAX_AUTO_FIT_ZOOM)
        self.centerOn(items_rect.center())
        self._zoom = self.transform().m11()
        self.zoomChanged.emit(self._zoom)

    def request_initial_frame(self):
        """Frame this module once its tab has a real, visible viewport."""
        self._initial_frame_pending = True
        self._schedule_initial_frame()

    def ensure_initial_frame(self):
        """Complete a previously requested first frame, if still pending."""
        self._schedule_initial_frame()

    def _schedule_initial_frame(self):
        if (not self._initial_frame_pending
                or self._initial_frame_scheduled
                or not self.isVisible()):
            return
        self._initial_frame_scheduled = True
        QTimer.singleShot(0, self._apply_initial_frame)

    def _apply_initial_frame(self):
        self._initial_frame_scheduled = False
        if not self._initial_frame_pending or not self.isVisible():
            return
        viewport = self.viewport()
        if viewport.width() < 80 or viewport.height() < 80:
            return
        if self._scene.itemsBoundingRect().isNull():
            self.resetTransform()
            self._zoom = 1.0
            # (0, 0) is the conventional origin for a new control module and
            # therefore more useful than centring an otherwise empty sheet.
            self.centerOn(QPointF(0, 0))
            self.zoomChanged.emit(self._zoom)
        else:
            self.zoom_to_fit()
        self._initial_frame_pending = False

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_initial_frame()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Only the pending first frame follows a resize.  Once the engineer
        # zooms or pans, subsequent window/panel changes preserve that view.
        self._schedule_initial_frame()

    def set_zoom(self, factor: float):
        """Set absolute zoom level."""
        self._initial_frame_pending = False
        scale_factor = factor / self._zoom
        self._zoom = factor
        self.scale(scale_factor, scale_factor)
        self.zoomChanged.emit(self._zoom)

    # ---------------------------------------------------------------- Drag-drop from palette
    @_guard_view_event
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasFormat(BLOCK_MIME_TYPE) \
                or event.mimeData().hasFormat(TEMPLATE_MIME_TYPE):
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            super().dragEnterEvent(event)

    @_guard_view_event
    def dragMoveEvent(self, event: QDragMoveEvent):
        if event.mimeData().hasFormat(BLOCK_MIME_TYPE) \
                or event.mimeData().hasFormat(TEMPLATE_MIME_TYPE):
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            super().dragMoveEvent(event)

    @_guard_view_event
    def dropEvent(self, event: QDropEvent):
        if event.mimeData().hasFormat(TEMPLATE_MIME_TYPE):
            template_path = bytes(
                event.mimeData().data(TEMPLATE_MIME_TYPE)).decode()
            scene_pos = self.mapToScene(event.position().toPoint())
            self.templateDropped.emit(template_path, scene_pos)
            event.setDropAction(Qt.CopyAction)
            event.accept()
        elif event.mimeData().hasFormat(BLOCK_MIME_TYPE):
            block_type = bytes(event.mimeData().data(BLOCK_MIME_TYPE)).decode()
            scene_pos = self.mapToScene(event.position().toPoint())
            self._scene.add_block_by_type(block_type, scene_pos)
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            super().dropEvent(event)

    # ---------------------------------------------------------------- Pan
    @_guard_view_event
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MiddleButton:
            self._initial_frame_pending = False
            self._pan = True
            self._pan_start = event.position()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return

        # Check if clicking on a terminal (start wiring)
        scene_pos = self.mapToScene(event.position().toPoint())
        terminal = self._find_terminal_at(scene_pos)

        if terminal is not None and event.button() == Qt.LeftButton:
            self._scene.start_wiring(terminal)
            event.accept()
            return

        super().mousePressEvent(event)

    @_guard_view_event
    def mouseMoveEvent(self, event: QMouseEvent):
        if self._pan:
            delta = event.position() - self._pan_start
            self._pan_start = event.position()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - int(delta.x()))
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - int(delta.y()))
            event.accept()
            return

        if self._scene._wiring:
            scene_pos = self.mapToScene(event.position().toPoint())
            self._scene.update_wiring(scene_pos)
            event.accept()
            return

        super().mouseMoveEvent(event)

    @_guard_view_event
    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MiddleButton and self._pan:
            self._pan = False
            self.setCursor(Qt.ArrowCursor)
            event.accept()
            return

        if self._scene._wiring and event.button() == Qt.LeftButton:
            scene_pos = self.mapToScene(event.position().toPoint())
            source = self._scene.wiring_source
            target = (self._find_terminal_at(scene_pos)
                      or self._scene.wiring_snap_target)
            self._scene.finish_wiring(target)
            if target is None and source is not None and not is_headless():
                from ..dialogs.quick_insert import QuickInsertDialog

                dialog = QuickInsertDialog(source.terminal, parent=self)
                dialog.candidateChosen.connect(
                    lambda candidate, src=source, where=QPointF(scene_pos):
                    self._scene.quick_insert_from_terminal(src, candidate, where)
                )
                dialog.finished.connect(
                    lambda _result, current=dialog:
                    setattr(self, "_quick_insert_dialog", None)
                    if self._quick_insert_dialog is current else None
                )
                self._quick_insert_dialog = dialog
                dialog.show()
            event.accept()
            return

        super().mouseReleaseEvent(event)

    def scrollContentsBy(self, dx: int, dy: int):
        # The overlays live in viewport space; scrolling blits the viewport,
        # so they must be repainted or they smear.
        super().scrollContentsBy(dx, dy)
        if self._legend_visible or self._connection_card:
            self.viewport().update()

    @_guard_view_event
    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Delete:
            self._scene.delete_selected()
            event.accept()
            return

        # Legend toggle: Ctrl+L
        if event.key() == Qt.Key_L and event.modifiers() == Qt.ControlModifier:
            self.toggle_legend()
            event.accept()
            return

        # Auto Route the selected connections: Ctrl+R (purely visual — legal
        # while the module is online).
        if event.key() == Qt.Key_R and event.modifiers() == Qt.ControlModifier:
            for wire_item in self.selected_wires():
                wire_item.auto_route()
            self.viewport().update()
            event.accept()
            return

        if event.key() == Qt.Key_Escape:
            if self._scene._wiring:
                self._scene.finish_wiring(None)
            else:
                self._scene.clearSelection()
            event.accept()
            return

        # F1 — open block documentation for the (single) selected block
        if event.key() == Qt.Key_F1:
            sel = [it for it in self._scene.selectedItems()
                   if isinstance(it, BlockItem)]
            if len(sel) == 1:
                from ..dialogs.block_documentation_dialog import (
                    open_block_documentation,
                )
                open_block_documentation(sel[0].block, parent=self)
                event.accept()
                return

        if event.key() == Qt.Key_F and event.modifiers() == Qt.ControlModifier:
            # Ctrl+F now opens block search; zoom-to-fit via toolbar
            self.searchActivated.emit()
            event.accept()
            return

        # Copy: Ctrl+C
        if event.key() == Qt.Key_C and event.modifiers() == Qt.ControlModifier:
            clipboard = self._scene.copy_selected()
            if clipboard:
                StrategyView._clipboard = clipboard
            event.accept()
            return

        # Cut: Ctrl+X. Use the exact mixed-selection routes as the ribbon so
        # comments and explicitly selected wires behave consistently.
        if event.key() == Qt.Key_X and event.modifiers() == Qt.ControlModifier:
            clipboard = self._scene.copy_selected()
            if clipboard:
                StrategyView._clipboard = clipboard
                self._scene.delete_selected("Cut selection")
            event.accept()
            return

        # Paste: Ctrl+V
        if event.key() == Qt.Key_V and event.modifiers() == Qt.ControlModifier:
            if StrategyView._clipboard:
                # Paste at the current mouse position mapped to scene coords,
                # or fall back to offset from original position.
                cursor_pos = self.mapFromGlobal(self.cursor().pos())
                if self.rect().contains(cursor_pos):
                    target = self.mapToScene(cursor_pos)
                    self._scene.paste_clipboard(StrategyView._clipboard,
                                                target_pos=target)
                else:
                    self._scene.paste_clipboard(StrategyView._clipboard)
            event.accept()
            return

        # Duplicate: Ctrl+D (copy + paste in one step)
        if event.key() == Qt.Key_D and event.modifiers() == Qt.ControlModifier:
            clipboard = self._scene.copy_selected()
            if clipboard:
                self._scene.paste_clipboard(clipboard)
            event.accept()
            return

        # Undo: Ctrl+Z
        if event.key() == Qt.Key_Z and event.modifiers() == Qt.ControlModifier:
            self._scene.undo_stack.undo()
            event.accept()
            return

        # Redo: Ctrl+Shift+Z or Ctrl+Y
        if (event.key() == Qt.Key_Z
                and event.modifiers() == (Qt.ControlModifier | Qt.ShiftModifier)):
            self._scene.undo_stack.redo()
            event.accept()
            return
        if event.key() == Qt.Key_Y and event.modifiers() == Qt.ControlModifier:
            self._scene.undo_stack.redo()
            event.accept()
            return

        super().keyPressEvent(event)
